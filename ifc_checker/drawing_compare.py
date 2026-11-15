"""Đối chiếu mô hình IFC với bản vẽ 2D (DXF / DWG).

Quy trình (tự động hoá cách đối chiếu thủ công hồ sơ hạ tầng):
  1. Đọc bản vẽ: ký hiệu (block INSERT / CIRCLE / POINT) theo layer, chữ (TEXT / MTEXT / thuộc tính block), đường nét.
  2. Mỗi ký hiệu được gán nhãn gần nhất -> đọc tên (vd T13.5), cao độ mặt, cao độ đáy.
  3. Đối tượng mô hình (vd hố ga): vị trí mặt bằng, tên, cao độ mặt / đáy (thuộc tính, hoặc từ hình học).
  4. Ghép theo vị trí: cặp gần nhất HAI CHIỀU trong bán kính cho trước.
  5. Thống kê độ lệch vị trí, cao độ; danh sách chỉ có trên bản vẽ / chỉ có trong mô hình / khác tên.
Toạ độ bản vẽ và mô hình phải cùng hệ (VN-2000, m) - có tham số tỷ lệ và dịch chuyển nếu khác.
DWG cần công cụ chuyển đổi cài sẵn: ODA File Converter (miễn phí) hoặc LibreDWG dwg2dxf (nguồn mở).
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field

import numpy as np

from .textnorm import norm, parse_number, split_list, strip_accents

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- chuyển DWG -> DXF
def find_dwg_converter() -> tuple[str, str] | None:
    """Trả về (loại, đường dẫn) của công cụ chuyển DWG nếu máy có cài."""
    env = os.environ.get("DWG2DXF")
    if env and os.path.exists(env):
        return ("oda" if "oda" in os.path.basename(env).lower() else "libredwg", env)
    for pat in [r"C:\Program Files\ODA\*\ODAFileConverter.exe", r"C:\Program Files (x86)\ODA\*\ODAFileConverter.exe"]:
        hits = sorted(glob.glob(pat))
        if hits:
            return ("oda", hits[-1])
    for name in ("ODAFileConverter", "dwg2dxf"):
        p = shutil.which(name)
        if p:
            return ("oda" if name.startswith("ODA") else "libredwg", p)
    hits = glob.glob(os.path.join(ROOT, "tools", "**", "dwg2dxf.exe"), recursive=True)
    if hits:
        return ("libredwg", hits[0])
    return None


def dwg_to_dxf(path: str) -> str:
    conv = find_dwg_converter()
    if conv is None:
        raise RuntimeError("Máy chủ chưa có công cụ chuyển DWG -> DXF. Hãy xuất DXF từ AutoCAD/ZWCAD (Save As -> DXF), "
                           "hoặc cài ODA File Converter (miễn phí) / LibreDWG (nguồn mở).")
    kind, exe = conv
    out_dir = tempfile.mkdtemp(prefix="tt_dxf_")
    if kind == "oda":
        in_dir = tempfile.mkdtemp(prefix="tt_dwg_")
        shutil.copy2(path, os.path.join(in_dir, os.path.basename(path)))
        subprocess.run([exe, in_dir, out_dir, "ACAD2018", "DXF", "0", "1", "*.DWG"], check=False, timeout=900)
        hits = glob.glob(os.path.join(out_dir, "*.dxf"))
        if not hits:
            raise RuntimeError("ODA File Converter không tạo được DXF")
        return hits[0]
    out = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + ".dxf")
    subprocess.run([exe, "-y", "-o", out, path], check=False, timeout=900,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not os.path.exists(out):
        raise RuntimeError("LibreDWG không chuyển được file DWG này")
    return out


# ---------------------------------------------------------------- đọc bản vẽ
@dataclass
class Drawing:
    path: str
    layers: dict = field(default_factory=dict)       # layer -> Counter loại đối tượng
    points: list = field(default_factory=list)       # (x, y, layer, kind, block)
    texts: list = field(default_factory=list)        # (x, y, layer, text)
    segments: dict = field(default_factory=dict)     # layer -> list[(x1,y1,x2,y2)]
    extents: list = field(default_factory=list)      # [xmin, ymin, xmax, ymax]
    units: str = ""


def read_drawing(path: str, max_segments: int = 400_000) -> Drawing:
    import ezdxf
    from ezdxf import recover

    src = path
    if path.lower().endswith(".dwg"):
        src = dwg_to_dxf(path)
    try:
        doc, _aud = recover.readfile(src)
    except Exception:
        doc = ezdxf.readfile(src)
    msp = doc.modelspace()
    d = Drawing(path=path)
    try:
        d.units = str(doc.header.get("$INSUNITS", ""))
    except Exception:
        pass
    layers: dict[str, Counter] = {}
    nseg = 0

    def seg(layer, a, b):
        nonlocal nseg
        if nseg >= max_segments:
            return
        d.segments.setdefault(layer, []).append((float(a[0]), float(a[1]), float(b[0]), float(b[1])))
        nseg += 1

    for e in msp:
        t = e.dxftype()
        layer = e.dxf.get("layer", "0")
        layers.setdefault(layer, Counter())[t] += 1
        try:
            if t == "INSERT":
                p = e.dxf.insert
                d.points.append((p.x, p.y, layer, "INSERT", e.dxf.name))
                for a in getattr(e, "attribs", []):
                    ap = a.dxf.insert
                    if a.dxf.text.strip():
                        d.texts.append((ap.x, ap.y, a.dxf.get("layer", layer), a.dxf.text.strip()))
            elif t == "CIRCLE":
                c = e.dxf.center
                d.points.append((c.x, c.y, layer, "CIRCLE", ""))
            elif t == "POINT":
                c = e.dxf.location
                d.points.append((c.x, c.y, layer, "POINT", ""))
            elif t == "TEXT":
                p = e.dxf.align_point if e.dxf.get("halign", 0) or e.dxf.get("valign", 0) else e.dxf.insert
                if e.dxf.text.strip():
                    d.texts.append((p.x, p.y, layer, e.dxf.text.strip()))
            elif t == "MTEXT":
                txt = e.plain_text().strip()
                p = e.dxf.insert
                if txt:
                    d.texts.append((p.x, p.y, layer, txt))
            elif t == "LINE":
                seg(layer, e.dxf.start, e.dxf.end)
            elif t in ("LWPOLYLINE", "POLYLINE"):
                pts = [(p[0], p[1]) for p in (e.get_points("xy") if t == "LWPOLYLINE" else e.points())]
                closed = bool(e.closed) if hasattr(e, "closed") else False
                for i in range(len(pts) - 1):
                    seg(layer, pts[i], pts[i + 1])
                if closed and len(pts) > 2:
                    seg(layer, pts[-1], pts[0])
            elif t == "ARC":
                c, r = e.dxf.center, e.dxf.radius
                a0, a1 = np.radians(e.dxf.start_angle), np.radians(e.dxf.end_angle)
                if a1 < a0:
                    a1 += 2 * np.pi
                n = max(4, int((a1 - a0) / 0.2))
                angs = np.linspace(a0, a1, n + 1)
                pts = [(c.x + r * np.cos(a), c.y + r * np.sin(a)) for a in angs]
                for i in range(len(pts) - 1):
                    seg(layer, pts[i], pts[i + 1])
        except Exception:
            continue
    d.layers = {k: dict(v) for k, v in layers.items()}
    xs = [p[0] for p in d.points] + [t[0] for t in d.texts]
    ys = [p[1] for p in d.points] + [t[1] for t in d.texts]
    for s in d.segments.values():
        for x1, y1, x2, y2 in s[:: max(1, len(s) // 2000)]:
            xs += [x1, x2]
            ys += [y1, y2]
    if xs:
        d.extents = [float(np.percentile(xs, 1)), float(np.percentile(ys, 1)),
                     float(np.percentile(xs, 99)), float(np.percentile(ys, 99))]
    return d


# ---------------------------------------------------------------- cấu hình
@dataclass
class CompareConfig:
    name: str = "Hố ga"
    point_layers: str = ""        # regex layer ký hiệu (trống = tự đoán)
    point_kinds: str = "INSERT;CIRCLE"
    block_filter: str = ""        # regex tên block (tuỳ chọn)
    label_layers: str = ""        # regex layer nhãn (trống = mọi layer)
    name_regex: str = r"\b[A-Z]{1,4}\d+(?:[.\-_]\d+)+\b"
    label_radius: float = 20.0    # m - bán kính tìm nhãn quanh ký hiệu
    match_radius: float = 16.0    # m - bán kính ghép ký hiệu bản vẽ <-> đối tượng mô hình
    elev_tol: float = 0.10        # m - lệch cao độ cho phép
    model_file: str = ""          # regex tên file IFC
    model_class: str = "IfcDistributionChamberElement"
    model_name: str = ""          # từ khoá tên (giống ma trận va chạm)
    top_prop: str = "cao độ mặt; cao do mat; cao độ nắp; top elevation"
    bottom_prop: str = "cao độ đáy; cao do day; bottom elevation"
    scale: float = 1.0            # nhân toạ độ bản vẽ (vd 0.001 nếu bản vẽ theo mm)
    dx: float = 0.0               # dịch toạ độ bản vẽ (sau khi nhân tỷ lệ)
    dy: float = 0.0
    overlay_layers: str = ""      # regex layer đường nét hiện lên 3D


_TOP = re.compile(r"\b(?:cdm|cd\s*mat|cao\s*do\s*mat|mat\s*ga|cd\s*nap|tn|cdtn|ctn|mat)\b[^0-9\-]{0,12}(-?\d+(?:[.,]\d+)?)")
_BOT = re.compile(r"\b(?:cdd|cd\s*day|cao\s*do\s*day|day\s*cong|day\s*ga|day|dc|cdday)\b[^0-9\-]{0,12}(-?\d+(?:[.,]\d+)?)")
_NUM = re.compile(r"(?<![\w.])-?\d{1,4}[.,]\d{1,3}(?![\w.])")


def parse_label(text: str, name_rx: re.Pattern) -> dict:
    raw = text.replace("\n", " ")
    n = norm(raw)
    name = ""
    m = name_rx.search(strip_accents(raw).upper())
    if m:
        name = m.group(0)
    top = bot = None
    mt, mb = _TOP.search(n), _BOT.search(n)
    if mt:
        top = parse_number(mt.group(1))
    if mb:
        bot = parse_number(mb.group(1))
    if top is None or bot is None:
        clean = name_rx.sub(" ", strip_accents(raw).upper())
        nums = [parse_number(x) for x in _NUM.findall(clean)]
        nums = [x for x in nums if x is not None and -100 < x < 5000]
        if top is None and bot is None and len(nums) >= 2:
            top, bot = max(nums), min(nums)
        elif top is None and nums:
            cand = [x for x in nums if bot is None or x > bot]
            top = max(cand) if cand else None
        elif bot is None and nums:
            cand = [x for x in nums if top is None or x < top]
            bot = min(cand) if cand else None
    return {"name": name, "top": top, "bottom": bot}


def suggest_config(dw: Drawing, models=None) -> CompareConfig:
    cfg = CompareConfig()
    kw = re.compile(r"(ga|hoga|ho_ga|ho ga|hg|manhole|mh)", re.I)
    cands = Counter()
    for x, y, layer, kind, block in dw.points:
        if kind in ("INSERT", "CIRCLE") and (kw.search(strip_accents(layer)) or kw.search(strip_accents(block or ""))):
            cands[layer] += 1
    if cands:
        cfg.point_layers = "|".join("^" + re.escape(lay) + "$" for lay, _ in cands.most_common(3))
    tl = Counter(t[2] for t in dw.texts if re.search(r"\d+[.,]\d+", t[3]))
    if tl:
        cfg.label_layers = "|".join("^" + re.escape(lay) + "$" for lay, _ in tl.most_common(4))
    if dw.extents and models:
        from .geometry import common_origin, compute_many
        compute_many(models)
        o = common_origin(models)
        if not np.any(o):      # chưa có hình học mô hình -> không đoán tỷ lệ
            return cfg
        cx = (dw.extents[0] + dw.extents[2]) / 2
        if abs(cx - o[0]) > 1e5 and abs(cx / 1000 - o[0]) < 5e4:   # bản vẽ theo mm
            cfg.scale = 0.001
    return cfg


# ---------------------------------------------------------------- đối tượng mô hình
def _prop(rec, keywords: str):
    keys = [norm(k) for k in split_list(keywords)]
    for props in rec.psets.values():
        for k, v in props.items():
            nk = norm(k)
            if any(nk == x or nk.startswith(x) for x in keys):
                n = parse_number(v)
                if n is not None:
                    return n
    return None


def model_points(models, cfg: CompareConfig) -> list[dict]:
    from .clash import _group, _label
    from .geometry import compute_many

    compute_many(models)
    out = []
    by_guid_cache = {}
    for m, rec, mesh in _group(models, cfg.model_file, cfg.model_class or "*", cfg.model_name):
        bg = by_guid_cache.setdefault(m.path, {r.guid: r for r in m.records})
        name = ""
        for props in rec.psets.values():
            for k, v in props.items():
                if norm(k) in ("name", "ten doi tuong") and str(v or "").strip():
                    name = str(v).strip()
                    break
            if name:
                break
        top = _prop(rec, cfg.top_prop)
        bot = _prop(rec, cfg.bottom_prop)
        out.append({
            "file": m.file_name, "step": rec.step_id, "guid": rec.guid, "label": _label(rec, bg), "name": name or rec.name,
            "x": float((mesh.bbox[0][0] + mesh.bbox[1][0]) / 2), "y": float((mesh.bbox[0][1] + mesh.bbox[1][1]) / 2),
            "top": top if top is not None else float(mesh.bbox[1][2]),
            "top_src": "thuộc tính" if top is not None else "hình học",
            "bottom": bot if bot is not None else float(mesh.bbox[0][2]),
            "bottom_src": "thuộc tính" if bot is not None else "hình học",
        })
    return out


def drawing_points(dw: Drawing, cfg: CompareConfig) -> list[dict]:
    lay_rx = re.compile(cfg.point_layers, re.I) if cfg.point_layers else None
    blk_rx = re.compile(cfg.block_filter, re.I) if cfg.block_filter else None
    lab_rx = re.compile(cfg.label_layers, re.I) if cfg.label_layers else None
    name_rx = re.compile(cfg.name_regex) if cfg.name_regex else re.compile(r"$^")
    kinds = {k.strip().upper() for k in split_list(cfg.point_kinds, allow_comma=True)} or {"INSERT", "CIRCLE"}
    pts = [p for p in dw.points if p[3] in kinds and (lay_rx is None or lay_rx.search(p[2]))
           and (blk_rx is None or blk_rx.search(p[4] or ""))]
    # bỏ ký hiệu trùng vị trí (vd block + đường tròn của cùng 1 hố ga)
    seen, uniq = set(), []
    for p in pts:
        k = (round(p[0] * cfg.scale + cfg.dx, 1), round(p[1] * cfg.scale + cfg.dy, 1))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    texts = [t for t in dw.texts if lab_rx is None or lab_rx.search(t[2])]
    T = np.array([[t[0] * cfg.scale + cfg.dx, t[1] * cfg.scale + cfg.dy] for t in texts]) if texts else np.zeros((0, 2))
    P = np.array([[p[0] * cfg.scale + cfg.dx, p[1] * cfg.scale + cfg.dy] for p in uniq]) if uniq else np.zeros((0, 2))
    has_name = np.array([bool(name_rx.search(strip_accents(t[3]).upper())) for t in texts], dtype=bool)
    has_num = np.array([bool(re.search(r"\d[.,]\d", t[3])) for t in texts], dtype=bool)
    anchors = np.where(has_name | has_num)[0]
    # Ghép nhãn <-> ký hiệu 1-1. Bản vẽ thường đặt nhãn theo 1 hướng cố định (vd lệch lên-phải) -> học độ lệch
    # phổ biến rồi so khoảng cách tới VỊ TRÍ DỰ ĐOÁN của nhãn; gán tham lam toàn cục theo chi phí tăng dần.
    owner = {}
    if len(anchors) and len(P):
        TA = T[anchors]
        cand = []          # (chi phí, điểm, nhãn, khoảng cách thật)
        first = []
        for s0 in range(0, len(P), 1024):
            D = np.hypot(P[s0:s0 + 1024, None, 0] - TA[None, :, 0], P[s0:s0 + 1024, None, 1] - TA[None, :, 1])
            j = D.argmin(axis=1)
            for r_, jj in enumerate(j):
                if D[r_, jj] <= cfg.label_radius:
                    first.append(TA[jj] - P[s0 + r_])
        off = np.zeros(2)
        if len(first) >= 5:
            F = np.array(first)
            med = np.median(F, axis=0)
            spread = np.median(np.hypot(F[:, 0] - med[0], F[:, 1] - med[1]))
            if np.hypot(*med) > 0.5 and spread < 0.6 * np.hypot(*med):
                off = med                      # hướng đặt nhãn nhất quán
        Q = P + off
        for s0 in range(0, len(P), 1024):
            Dr = np.hypot(P[s0:s0 + 1024, None, 0] - TA[None, :, 0], P[s0:s0 + 1024, None, 1] - TA[None, :, 1])
            Dq = np.hypot(Q[s0:s0 + 1024, None, 0] - TA[None, :, 0], Q[s0:s0 + 1024, None, 1] - TA[None, :, 1])
            rr, cc = np.nonzero(Dr <= cfg.label_radius)
            for r_, c_ in zip(rr.tolist(), cc.tolist()):
                t_i = int(anchors[c_])
                cost = float(Dq[r_, c_]) - (0.5 if has_name[t_i] else 0.0)
                cand.append((cost, s0 + r_, t_i, float(Dr[r_, c_])))
        cand.sort()
        used_p, used_t = set(), set()
        for cost, p_i, t_i, dd in cand:
            if p_i in used_p or t_i in used_t:
                continue
            used_p.add(p_i)
            used_t.add(t_i)
            owner[p_i] = [(dd, t_i)]
    out = []
    for k, (x, y, layer, kind, block) in enumerate(uniq):
        X, Y = float(P[k, 0]), float(P[k, 1])
        rec = {"x": X, "y": Y, "layer": layer, "kind": kind, "block": block, "name": "", "top": None, "bottom": None,
               "label": "", "label_dist": None}
        mine = sorted(owner.get(k, []))
        if mine:
            named = [m for m in mine if has_name[m[1]]]
            dist, main = (named or mine)[0]
            mx, my = T[main]
            # nhãn nhiều dòng (nhiều TEXT): gộp các dòng sát nhãn chính, không lấy dòng thuộc ký hiệu khác
            others = {t for kk, lst in owner.items() if kk != k for _, t in lst}
            near = np.where(np.hypot(T[:, 0] - mx, T[:, 1] - my) <= max(3.0, cfg.label_radius * 0.2))[0]
            group = [int(i) for i in near if int(i) != main and int(i) not in others]
            txt = " | ".join(texts[i][3] for i in [main] + group)
            lab = parse_label(txt, name_rx)
            rec.update(name=lab["name"], top=lab["top"], bottom=lab["bottom"], label=txt[:160], label_dist=round(dist, 2))
        out.append(rec)
    return out


# ---------------------------------------------------------------- ghép & thống kê
def compare(dw: Drawing, models, cfg: CompareConfig) -> dict:
    dp = drawing_points(dw, cfg)
    mp = model_points(models, cfg)
    A = np.array([[p["x"], p["y"]] for p in dp]) if dp else np.zeros((0, 2))
    B = np.array([[p["x"], p["y"]] for p in mp]) if mp else np.zeros((0, 2))
    pairs = []
    if len(A) and len(B):
        nn_ab = np.empty(len(A), dtype=int)
        dab = np.empty(len(A))
        for s in range(0, len(A), 1024):
            D = np.hypot(A[s:s + 1024, None, 0] - B[None, :, 0], A[s:s + 1024, None, 1] - B[None, :, 1])
            nn_ab[s:s + 1024] = D.argmin(axis=1)
            dab[s:s + 1024] = D.min(axis=1)
        nn_ba = np.empty(len(B), dtype=int)
        for s in range(0, len(B), 1024):
            D = np.hypot(B[s:s + 1024, None, 0] - A[None, :, 0], B[s:s + 1024, None, 1] - A[None, :, 1])
            nn_ba[s:s + 1024] = D.argmin(axis=1)
        for i in range(len(A)):
            j = nn_ab[i]
            if nn_ba[j] == i and dab[i] <= cfg.match_radius:
                pairs.append((i, j, float(dab[i])))
    used_a = {i for i, _, _ in pairs}
    used_b = {j for _, j, _ in pairs}
    rows = []
    tol = cfg.elev_tol
    for i, j, dist in pairs:
        a, b = dp[i], mp[j]
        dt = None if a["top"] is None else round(b["top"] - a["top"], 3)
        db = None if a["bottom"] is None else round(b["bottom"] - a["bottom"], 3)
        same_name = (not a["name"]) or norm(a["name"]) == norm(b["name"])
        bad = (dt is not None and abs(dt) > tol) or (db is not None and abs(db) > tol)
        status = "Lệch cao độ" if bad else ("Khác tên" if not same_name else "Khớp")
        rows.append({"status": status, "name_model": b["name"], "name_drawing": a["name"], "x": round(b["x"], 3),
                     "y": round(b["y"], 3), "dxy": round(dist, 3), "top_model": round(b["top"], 3),
                     "top_drawing": a["top"], "d_top": dt, "bottom_model": round(b["bottom"], 3),
                     "bottom_drawing": a["bottom"], "d_bottom": db, "label_dist": a["label_dist"],
                     "file": b["file"], "step": b["step"], "label": a["label"], "top_src": b["top_src"],
                     "bottom_src": b["bottom_src"], "same_name": same_name})
    for i, a in enumerate(dp):
        if i not in used_a:
            rows.append({"status": "Chỉ có trên bản vẽ", "name_model": "", "name_drawing": a["name"], "x": round(a["x"], 3),
                         "y": round(a["y"], 3), "dxy": None, "top_model": None, "top_drawing": a["top"], "d_top": None,
                         "bottom_model": None, "bottom_drawing": a["bottom"], "d_bottom": None,
                         "label_dist": a["label_dist"], "file": "", "step": None, "label": a["label"]})
    for j, b in enumerate(mp):
        if j not in used_b:
            rows.append({"status": "Chỉ có trong mô hình", "name_model": b["name"], "name_drawing": "", "x": round(b["x"], 3),
                         "y": round(b["y"], 3), "dxy": None, "top_model": round(b["top"], 3), "top_drawing": None,
                         "d_top": None, "bottom_model": round(b["bottom"], 3), "bottom_drawing": None, "d_bottom": None,
                         "label_dist": None, "file": b["file"], "step": b["step"], "label": ""})
    dts = np.array([abs(r["d_top"]) for r in rows if r.get("d_top") is not None])
    dbs = np.array([abs(r["d_bottom"]) for r in rows if r.get("d_bottom") is not None])
    dxy = np.array([r["dxy"] for r in rows if r.get("dxy") is not None])

    def over(arr, t):
        return int((arr > t).sum()) if len(arr) else 0
    summary = {
        "name": cfg.name, "drawing_points": len(dp), "model_points": len(mp), "matched": len(pairs),
        "only_drawing": len(dp) - len(pairs), "only_model": len(mp) - len(pairs),
        "match_rate_model": round(100 * len(pairs) / len(mp), 1) if mp else 0,
        "labels_found": sum(1 for p in dp if p["label"]),
        "name_mismatch": sum(1 for r in rows if r["status"] != "Chỉ có trên bản vẽ" and r.get("same_name") is False),
        "top": {"n": int(len(dts)), "median": round(float(np.median(dts)), 3) if len(dts) else None,
                ">0.1": over(dts, 0.1), ">0.3": over(dts, 0.3), ">0.5": over(dts, 0.5), ">1.0": over(dts, 1.0)},
        "bottom": {"n": int(len(dbs)), "median": round(float(np.median(dbs)), 3) if len(dbs) else None,
                   ">0.1": over(dbs, 0.1), ">0.3": over(dbs, 0.3), ">0.5": over(dbs, 0.5), ">1.0": over(dbs, 1.0)},
        "dxy": {"median": round(float(np.median(dxy)), 3) if len(dxy) else None,
                "max": round(float(dxy.max()), 3) if len(dxy) else None},
        "elev_tol": cfg.elev_tol, "match_radius": cfg.match_radius,
    }
    order = {"Lệch cao độ": 0, "Khác tên": 1, "Chỉ có trên bản vẽ": 2, "Chỉ có trong mô hình": 3, "Khớp": 4}
    rows.sort(key=lambda r: (order.get(r["status"], 9), -(abs(r["d_top"]) if r.get("d_top") is not None else 0)))
    return {"summary": summary, "rows": rows, "config": asdict(cfg)}


def overlay_segments(dw: Drawing, cfg: CompareConfig, max_segments: int = 200_000) -> np.ndarray:
    """Đoạn thẳng (x1,y1,x2,y2) của các layer được chọn, đã áp tỷ lệ / dịch chuyển."""
    rx = re.compile(cfg.overlay_layers, re.I) if cfg.overlay_layers else None
    segs = []
    for layer, ss in dw.segments.items():
        if rx is None or rx.search(layer):
            segs.extend(ss)
        if len(segs) >= max_segments:
            break
    if not segs:
        return np.zeros((0, 4))
    a = np.asarray(segs[:max_segments], dtype=np.float64)
    a[:, [0, 2]] = a[:, [0, 2]] * cfg.scale + cfg.dx
    a[:, [1, 3]] = a[:, [1, 3]] * cfg.scale + cfg.dy
    return a


def export_excel(res: dict, path: str) -> None:
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tổng hợp"
    s = res["summary"]
    lines = [("ĐỐI CHIẾU MÔ HÌNH IFC - BẢN VẼ 2D", ""), ("Đối tượng", s["name"]),
             ("Ký hiệu trên bản vẽ", s["drawing_points"]), ("Đối tượng trong mô hình", s["model_points"]),
             ("Ghép được theo vị trí", f"{s['matched']} (bán kính {s['match_radius']} m)"),
             ("Chỉ có trên bản vẽ", s["only_drawing"]), ("Chỉ có trong mô hình", s["only_model"]),
             ("Khác tên", s["name_mismatch"]), ("Lệch vị trí trung vị / lớn nhất (m)", f"{s['dxy']['median']} / {s['dxy']['max']}")]
    for k in ("top", "bottom"):
        t = s[k]
        lines.append((f"Cao độ {'mặt' if k == 'top' else 'đáy'}: so được / trung vị |Δ|",
                      f"{t['n']} / {t['median']} m · >0,1 m: {t['>0.1']} · >0,3 m: {t['>0.3']} · >0,5 m: {t['>0.5']} · >1,0 m: {t['>1.0']}"))
    for a, b in lines:
        ws.append([a, b])
    ws["A1"].font = Font(bold=True, size=14)
    ws.column_dimensions["A"].width = 42
    ws.column_dimensions["B"].width = 80
    wd = wb.create_sheet("Chi tiết")
    hdr = ["Trạng thái", "Tên (IFC)", "Tên (BV)", "X", "Y", "Lệch vị trí (m)", "Mặt IFC", "Mặt BV", "Δ mặt",
           "Đáy IFC", "Đáy BV", "Δ đáy", "K/c nhãn (m)", "File", "#", "Nhãn bản vẽ"]
    wd.append(hdr)
    for c in wd[1]:
        c.fill, c.font = PatternFill("solid", fgColor="1F4E78"), Font(bold=True, color="FFFFFF")
    for r in res["rows"]:
        wd.append([r["status"], r["name_model"], r["name_drawing"], r["x"], r["y"], r["dxy"], r["top_model"],
                   r["top_drawing"], r["d_top"], r["bottom_model"], r["bottom_drawing"], r["d_bottom"],
                   r["label_dist"], r["file"], r["step"], r["label"]])
    for i, w in enumerate([18, 14, 14, 12, 12, 10, 10, 10, 9, 10, 10, 9, 10, 34, 8, 60], start=1):
        wd.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    wd.freeze_panes = "B2"
    wd.auto_filter.ref = wd.dimensions
    wb.save(path)
