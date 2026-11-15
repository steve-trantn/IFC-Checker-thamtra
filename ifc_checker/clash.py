"""Kiểm tra xung đột (va chạm) giữa các mô hình IFC theo ma trận va chạm.

Mỗi phép thử (ClashTest) so sánh nhóm A với nhóm B. Nhóm được xác định bằng
regex tên file + lớp IFC + từ khoá tên (tên đối tượng, tên đối tượng cha, thuộc tính Name).

4 loại kiểm tra:
  HARD       va chạm cứng: 2 khối giao nhau, độ sâu xuyên > giá trị (m)
  CLEARANCE  khoảng cách tối thiểu: 2 đối tượng gần nhau hơn giá trị (m) (gồm cả giao nhau)
  FLUSH      cao độ khớp mặt: đỉnh đối tượng A (vd mặt hố ga) lệch so với bề mặt B
             (vd mặt đường/vỉa hè) tại cùng vị trí quá ± giá trị (m). A nằm trong B là ĐÚNG thiết kế.
  COVER      chiều sâu chôn tối thiểu: từ bề mặt B xuống đỉnh A nhỏ hơn giá trị (m)

HARD/CLEARANCE dùng thư viện FCL trên lưới tam giác; FLUSH/COVER chiếu đứng xuống bề mặt B (numpy).
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field, fields

import numpy as np

from .geometry import Mesh, common_origin, compute_many
from .model_loader import ElementRecord, ModelInfo
from .textnorm import norm, split_list, strip_accents

MODES = ["HARD", "CLEARANCE", "FLUSH", "COVER"]
MODE_VN = {
    "HARD": "Va chạm cứng",
    "CLEARANCE": "Khoảng cách tối thiểu",
    "FLUSH": "Cao độ khớp mặt",
    "COVER": "Chiều sâu chôn tối thiểu",
}
MODE_HELP = {
    "HARD": "Hai khối giao nhau với độ sâu xuyên lớn hơn giá trị (m)",
    "CLEARANCE": "Khoảng cách giữa 2 đối tượng nhỏ hơn giá trị (m), kể cả giao nhau",
    "FLUSH": "Đỉnh A (vd mặt hố ga) lệch cao độ so với bề mặt B (vd mặt đường) quá ± giá trị (m). "
             "A nằm trong khối B là đúng thiết kế, không tính là va chạm",
    "COVER": "Khoảng cách đứng từ bề mặt B xuống đỉnh A (chiều sâu chôn) nhỏ hơn giá trị (m)",
}


@dataclass
class ClashTest:
    name: str = ""
    enabled: bool = True
    a_file: str = ""      # regex tên file nhóm A ('' = mọi file)
    a_class: str = "*"    # lớp IFC nhóm A
    b_file: str = ""
    b_class: str = "*"
    tolerance: float = 0.01   # giá trị (m) - ý nghĩa theo loại kiểm tra
    max_results: int = 5000
    mode: str = "HARD"
    a_name: str = ""      # từ khoá tên nhóm A (phân cách ;), không phân biệt dấu
    b_name: str = ""
    code: str = ""        # mã ô ma trận, vd C1-B1
    a_group: str = ""     # mã nhóm (hiển thị), vd C1
    b_group: str = ""
    note: str = ""        # căn cứ / định nghĩa va chạm

    def is_self(self) -> bool:
        return (self.a_file, self.a_class, self.a_name) == (self.b_file, self.b_class, self.b_name)


@dataclass
class ClashGroup:
    code: str
    name: str
    file: str = ""
    classes: str = "*"
    keywords: str = ""
    note: str = ""


@dataclass
class Clash:
    id: str
    test: str
    file_a: str
    step_a: int
    guid_a: str
    class_a: str
    name_a: str
    file_b: str
    step_b: int
    guid_b: str
    class_b: str
    name_b: str
    x: float
    y: float
    z: float
    depth: float          # giá trị đo: độ sâu xuyên / khoảng cách / độ lệch cao độ / chiều sâu chôn (m)
    status: str = "Mới"
    note: str = ""
    kind: str = "HARD"
    detail: str = ""      # mô tả dễ đọc, vd "Mặt ga thấp hơn mặt đường 0,035 m"


@dataclass
class ClashRun:
    tests: list[ClashTest] = field(default_factory=list)
    clashes: list[Clash] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def fcl_available() -> bool:
    try:
        import fcl  # noqa: F401  (chỉ kiểm tra có cài)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- so khớp tên

def _drop_non_ascii(s: str) -> str:
    """Mô phỏng lỗi xuất IFC hay gặp: bỏ mọi ký tự ngoài ASCII ('Mặt đường' -> 'Mt ng')."""
    return re.sub(r"\s+", " ", "".join(ch for ch in s if ord(ch) < 128)).strip()


def _keyword_forms(keywords: str) -> list[str]:
    forms = []
    for k in split_list(keywords or "", allow_comma=False):
        for f in (norm(k), norm(_drop_non_ascii(k))):
            if len(f.replace(" ", "")) >= 3 and f not in forms:
                forms.append(f)
    return forms


def _name_texts(rec: ElementRecord, by_guid: dict) -> list[str]:
    texts = [rec.name, rec.object_type, rec.type_name]
    parent = by_guid.get(rec.parent_guid)
    depth = 0
    while parent is not None and depth < 4:
        texts += [parent.name, parent.object_type]
        parent = by_guid.get(parent.parent_guid)
        depth += 1
    for props in rec.psets.values():
        for k, v in props.items():
            if norm(k) in ("name", "ten doi tuong", "ten", "ten loai", "ten nhom") and v not in (None, ""):
                texts.append(str(v))
    return [norm(t) for t in texts if t]


def _name_match(forms: list[str], texts: list[str]) -> bool:
    return any(f in t for f in forms for t in texts)


# ---------------------------------------------------------------- nhóm

def _stem_token(file_name: str) -> str:
    return re.escape(os.path.splitext(file_name)[0])


def _short(file_name: str) -> str:
    stem = os.path.splitext(file_name)[0]
    parts = [p for p in re.split(r"[-_ ]", stem) if p]
    return parts[-1] if parts else stem


def default_tests(models: list[ModelInfo], tolerance: float = 0.01) -> list[ClashTest]:
    """Mỗi cặp file là 1 phép thử va chạm cứng (A < B)."""
    names = [m.file_name for m in models if not m.error]
    tests = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            tests.append(ClashTest(name=f"{_short(a)} × {_short(b)}", a_file=_stem_token(a),
                                   b_file=_stem_token(b), tolerance=tolerance))
    return tests


def _group(models, file_re: str, classes: str, keywords: str = ""):
    rx = re.compile(strip_accents(file_re), re.IGNORECASE) if file_re else None
    cls = [c for c in split_list(classes or "*", allow_comma=True) if c != "*"]
    forms = _keyword_forms(keywords)
    out = []
    for m in models:
        if rx and not rx.search(strip_accents(m.file_name)):
            continue
        meshes = getattr(m, "meshes", None) or {}
        if not meshes:
            continue
        by_id = {r.step_id: r for r in m.records}
        by_guid = {r.guid: r for r in m.records}
        for sid, mesh in meshes.items():
            rec = by_id.get(sid)
            if rec is None:
                continue
            if cls and not any(rec.is_a(c) for c in cls):
                continue
            if forms and not _name_match(forms, _name_texts(rec, by_guid)):
                continue
            out.append((m, rec, mesh))
    return out


def _label(rec: ElementRecord, by_guid: dict) -> str:
    if rec.display_name() and not rec.display_name().startswith("#"):
        return rec.display_name()
    for props in rec.psets.values():
        for k, v in props.items():
            if norm(k) in ("name", "ten doi tuong") and str(v or "").strip():
                return f"{str(v).strip()} (#{rec.step_id})"
    parent = by_guid.get(rec.parent_guid)
    if parent is not None:
        return f"{parent.display_name()} (con #{rec.step_id})"
    return rec.display_name()


# ---------------------------------------------------------------- hình học phụ trợ

def _surface_z(px: np.ndarray, py: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """Cao độ lớn nhất của các tam giác 'tris' (t,3,3) tại các điểm (px,py). NaN nếu không có."""
    out = np.full(len(px), np.nan)
    if len(tris) == 0 or len(px) == 0:
        return out
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    v0 = c[:, :2] - a[:, :2]
    v1 = b[:, :2] - a[:, :2]
    d00 = (v0 * v0).sum(1)
    d01 = (v0 * v1).sum(1)
    d11 = (v1 * v1).sum(1)
    den = d00 * d11 - d01 * d01
    ok = np.abs(den) > 1e-12          # bỏ tam giác thẳng đứng
    a, b, c, v0, v1, d00, d01, d11, den = (x[ok] for x in (a, b, c, v0, v1, d00, d01, d11, den))
    if len(a) == 0:
        return out
    tmin = np.minimum(np.minimum(a[:, :2], b[:, :2]), c[:, :2])
    tmax = np.maximum(np.maximum(a[:, :2], b[:, :2]), c[:, :2])
    for s in range(0, len(px), 256):
        X, Y = px[s:s + 256, None], py[s:s + 256, None]
        inbox = (X >= tmin[:, 0] - 1e-6) & (X <= tmax[:, 0] + 1e-6) & (Y >= tmin[:, 1] - 1e-6) & (Y <= tmax[:, 1] + 1e-6)
        if not inbox.any():
            continue
        v2x, v2y = X - a[:, 0], Y - a[:, 1]
        d20 = v2x * v0[:, 0] + v2y * v0[:, 1]
        d21 = v2x * v1[:, 0] + v2y * v1[:, 1]
        u = (d11 * d20 - d01 * d21) / den
        v = (d00 * d21 - d01 * d20) / den
        inside = inbox & (u >= -1e-7) & (v >= -1e-7) & (u + v <= 1 + 1e-7)
        z = a[:, 2] + u * (c[:, 2] - a[:, 2]) + v * (b[:, 2] - a[:, 2])
        z = np.where(inside, z, -np.inf)
        zmax = z.max(axis=1)
        zmax[np.isneginf(zmax)] = np.nan
        out[s:s + 256] = zmax
    return out


def _bbox_pairs(ba: np.ndarray, bb: np.ndarray, margin: float = 0.0, xy_only: bool = False):
    """Các cặp (i, j) có hộp bao giao nhau (sau khi nới 'margin')."""
    pairs = []
    if len(ba) == 0 or len(bb) == 0:
        return pairs
    dims = 2 if xy_only else 3
    lo_b, hi_b = bb[:, 0, :dims], bb[:, 1, :dims]
    for s in range(0, len(ba), 512):
        lo_a = ba[s:s + 512, 0, :dims] - margin
        hi_a = ba[s:s + 512, 1, :dims] + margin
        ov = np.all((lo_a[:, None, :] <= hi_b[None]) & (hi_a[:, None, :] >= lo_b[None]), axis=2)
        ii, jj = np.nonzero(ov)
        pairs.extend(zip((ii + s).tolist(), jj.tolist()))
    return pairs


def _same_component(ma, ra, mb, rb) -> bool:
    if ma.path != mb.path:
        return False
    return ra.step_id == rb.step_id or ra.guid == rb.parent_guid or rb.guid == ra.parent_guid


def _fmt(x: float) -> str:
    return f"{x:.3f}".replace(".", ",")


# ---------------------------------------------------------------- chạy

def run_clash(models: list[ModelInfo], tests: list[ClashTest], progress=None, cancel=None) -> ClashRun:
    import fcl

    run = ClashRun(tests=tests)
    active = [t for t in tests if t.enabled]
    if not active:
        run.messages.append("Không có phép thử nào được bật")
        return run

    # 1. Dựng hình cho các file liên quan
    need = set()
    for t in active:
        for fr in (t.a_file, t.b_file):
            try:
                rx = re.compile(strip_accents(fr), re.IGNORECASE) if fr else None
            except re.error:
                continue
            for m in models:
                if not m.error and (rx is None or rx.search(strip_accents(m.file_name))):
                    need.add(m.path)
    todo = [m for m in models if m.path in need]
    compute_many(todo, progress, cancel)
    if cancel and cancel():
        return run
    origin = common_origin(todo)
    guid_maps = {m.path: {r.guid: r for r in m.records} for m in todo}
    fcl_cache: dict[tuple, tuple] = {}

    def fcl_obj(m: ModelInfo, mesh: Mesh):
        key = (m.path, mesh.step_id)
        hit = fcl_cache.get(key)
        if hit is None:
            g = fcl.BVHModel()
            v = (mesh.verts - origin).astype(np.float64)
            g.beginModel(len(v), len(mesh.faces))
            g.addSubModel(v, mesh.faces.astype(np.int32))
            g.endModel()
            hit = fcl_cache[key] = (fcl.CollisionObject(g, fcl.Transform()), g)
        return hit

    counter = [0]

    def add(t, ma, ra, mb, rb, value, pos_world, detail):
        counter[0] += 1
        run.clashes.append(Clash(
            id=f"C{counter[0]:05d}", test=t.name,
            file_a=ma.file_name, step_a=ra.step_id, guid_a=ra.guid, class_a=ra.ifc_class,
            name_a=_label(ra, guid_maps[ma.path]),
            file_b=mb.file_name, step_b=rb.step_id, guid_b=rb.guid, class_b=rb.ifc_class,
            name_b=_label(rb, guid_maps[mb.path]),
            x=round(float(pos_world[0]), 3), y=round(float(pos_world[1]), 3), z=round(float(pos_world[2]), 3),
            depth=round(float(value), 4), kind=t.mode, detail=detail, note=t.note))

    for ti, t in enumerate(active):
        if cancel and cancel():
            break
        mode = (t.mode or "HARD").upper()
        if progress:
            progress(ti, len(active), f"Xung đột: {t.name} ({ti + 1}/{len(active)})")
        try:
            ga = _group(todo, t.a_file, t.a_class, t.a_name)
            gb = ga if t.is_self() else _group(todo, t.b_file, t.b_class, t.b_name)
        except re.error as ex:
            run.messages.append(f"{t.name}: regex lỗi {ex}")
            continue
        if not ga or not gb:
            run.messages.append(f"{t.name}: nhóm A có {len(ga)}, nhóm B có {len(gb)} đối tượng hình học → bỏ qua")
            continue
        before = len(run.clashes)
        rows = []
        if mode == "HARD":
            rows = _run_hard(fcl, fcl_obj, t, ga, gb)
            for d, pos, (ma, ra), (mb, rb) in rows[:t.max_results]:
                add(t, ma, ra, mb, rb, d, pos + origin, f"Giao nhau, độ sâu xuyên {_fmt(d)} m")
        elif mode == "CLEARANCE":
            rows = _run_clearance(fcl, fcl_obj, t, ga, gb, origin)
            for d, pos, (ma, ra), (mb, rb) in rows[:t.max_results]:
                txt = "Giao nhau" if d <= 0 else f"Khoảng cách {_fmt(d)} m"
                add(t, ma, ra, mb, rb, max(d, 0.0), pos + origin, f"{txt} < yêu cầu {_fmt(t.tolerance)} m")
        elif mode in ("FLUSH", "COVER"):
            rows = _run_vertical(t, ga, gb, mode)
            for val, pos, (ma, ra), (mb, rb), txt in rows[:t.max_results]:
                add(t, ma, ra, mb, rb, val, pos, txt)
        else:
            run.messages.append(f"{t.name}: loại kiểm tra không hỗ trợ '{t.mode}'")
            continue
        n = len(run.clashes) - before
        extra = f" (chỉ giữ {t.max_results})" if len(rows) > t.max_results else ""
        run.messages.append(f"{t.name} [{MODE_VN.get(mode, mode)} {_fmt(t.tolerance)} m]: "
                            f"{len(ga)} × {len(gb)} đối tượng → {n} va chạm{extra}")
    if progress:
        progress(len(active), len(active), f"Xong kiểm tra xung đột: {len(run.clashes)} va chạm")
    return run


def _run_hard(fcl, fcl_obj, t, ga, gb):
    lookup, objs_a, objs_b = {}, [], []
    for m, rec, mesh in ga:
        o, g = fcl_obj(m, mesh)
        lookup[id(g)] = (m, rec)
        objs_a.append(o)
    if not t.is_self():
        for m, rec, mesh in gb:
            o, g = fcl_obj(m, mesh)
            lookup[id(g)] = (m, rec)
            objs_b.append(o)
    mgr_a = fcl.DynamicAABBTreeCollisionManager()
    mgr_a.registerObjects(objs_a)
    mgr_a.setup()
    req = fcl.CollisionRequest(num_max_contacts=max(1000, t.max_results * 20), enable_contact=True)
    cdata = fcl.CollisionData(request=req)
    if t.is_self():
        mgr_a.collide(cdata, fcl.defaultCollisionCallback)
    else:
        mgr_b = fcl.DynamicAABBTreeCollisionManager()
        mgr_b.registerObjects(objs_b)
        mgr_b.setup()
        mgr_a.collide(mgr_b, cdata, fcl.defaultCollisionCallback)
    best: dict[tuple, tuple] = {}
    for c in cdata.result.contacts:
        ka, kb = lookup.get(id(c.o1)), lookup.get(id(c.o2))
        if ka is None or kb is None:
            continue
        (ma, ra), (mb, rb) = ka, kb
        if _same_component(ma, ra, mb, rb):
            continue
        key = tuple(sorted([(ma.path, ra.step_id), (mb.path, rb.step_id)]))
        d = float(c.penetration_depth)
        if key not in best or d > best[key][0]:
            best[key] = (d, np.asarray(c.pos, dtype=np.float64), (ma, ra), (mb, rb))
    return sorted((v for v in best.values() if v[0] >= t.tolerance), key=lambda v: -v[0])


def _run_clearance(fcl, fcl_obj, t, ga, gb, origin):
    ba = np.array([mesh.bbox for _, _, mesh in ga])
    bb = np.array([mesh.bbox for _, _, mesh in gb])
    out = {}
    req = fcl.DistanceRequest(enable_nearest_points=True)
    for i, j in _bbox_pairs(ba, bb, margin=t.tolerance):
        (ma, ra, mesha), (mb, rb, meshb) = ga[i], gb[j]
        if _same_component(ma, ra, mb, rb):
            continue
        key = tuple(sorted([(ma.path, ra.step_id), (mb.path, rb.step_id)]))
        if key in out:
            continue
        oa, _ = fcl_obj(ma, mesha)
        ob, _ = fcl_obj(mb, meshb)
        res = fcl.DistanceResult()
        d = float(fcl.distance(oa, ob, req, res))
        if d < t.tolerance:
            try:
                p = (np.asarray(res.nearest_points[0]) + np.asarray(res.nearest_points[1])) / 2.0
                if not np.all(np.isfinite(p)) or (d <= 0 and not np.any(p)):
                    raise ValueError
            except Exception:
                lo = np.maximum(mesha.bbox[0], meshb.bbox[0])
                hi = np.minimum(mesha.bbox[1], meshb.bbox[1])
                p = (lo + hi) / 2.0 - origin
            out[key] = (d, np.asarray(p, dtype=np.float64), (ma, ra), (mb, rb))
    return sorted(out.values(), key=lambda v: v[0])


def _run_vertical(t, ga, gb, mode):
    """FLUSH: so cao độ đỉnh A với bề mặt B. COVER: chiều sâu từ bề mặt B xuống đỉnh A."""
    ba = np.array([mesh.bbox for _, _, mesh in ga])
    bb = np.array([mesh.bbox for _, _, mesh in gb])
    cand: dict[int, list[int]] = {}
    for i, j in _bbox_pairs(ba, bb, margin=0.0, xy_only=True):
        cand.setdefault(i, []).append(j)
    rows = []
    for i, js in cand.items():
        ma, ra, mesha = ga[i]
        v = mesha.verts
        if mode == "FLUSH":
            ztop = float(v[:, 2].max())
            top = v[v[:, 2] >= ztop - 0.01]
            px = np.array([top[:, 0].mean()])
            py = np.array([top[:, 1].mean()])
            pz = np.array([ztop])
        else:
            pts = v if len(v) <= 600 else v[np.argsort(-v[:, 2])[:600]]   # ưu tiên các đỉnh cao nhất
            px, py, pz = pts[:, 0], pts[:, 1], pts[:, 2]
        best = None
        for j in js:
            mb, rb, meshb = gb[j]
            if _same_component(ma, ra, mb, rb):
                continue
            tri = meshb.verts[meshb.faces]
            lo = np.array([px.min(), py.min()])
            hi = np.array([px.max(), py.max()])
            tmin = tri[:, :, :2].min(axis=1)
            tmax = tri[:, :, :2].max(axis=1)
            sel = np.all(tmax >= lo, axis=1) & np.all(tmin <= hi, axis=1)
            if not sel.any():
                continue
            zs = _surface_z(px, py, tri[sel])
            if np.all(np.isnan(zs)):
                continue
            if mode == "FLUSH":
                dz = float(pz[0] - zs[0])
                if best is None or abs(dz) < abs(best[0]):   # bề mặt gần nhất với mặt ga
                    best = (dz, np.array([px[0], py[0], pz[0]]), (mb, rb))
            else:
                cover = zs - pz
                k = int(np.nanargmin(cover))
                c = float(cover[k])
                if best is None or c < best[0]:
                    best = (c, np.array([px[k], py[k], pz[k]]), (mb, rb))
        if best is None:
            continue
        val, pos, (mb, rb) = best
        if mode == "FLUSH":
            if abs(val) > t.tolerance:
                side = "cao hơn" if val > 0 else "thấp hơn"
                rows.append((abs(val), pos, (ma, ra), (mb, rb),
                             f"Đỉnh A {side} bề mặt B {_fmt(abs(val))} m (cho phép ± {_fmt(t.tolerance)} m)"))
        else:
            if val < t.tolerance:
                txt = (f"Đỉnh A nhô lên trên bề mặt B {_fmt(-val)} m" if val < 0
                       else f"Chiều sâu chôn {_fmt(val)} m < yêu cầu {_fmt(t.tolerance)} m")
                rows.append((val, pos, (ma, ra), (mb, rb), txt))
    rows.sort(key=lambda r: -r[0] if mode == "FLUSH" else r[0])
    return rows


# ---------------------------------------------------------------- ma trận va chạm mẫu

def default_matrix_groups() -> list[ClashGroup]:
    """Nhóm đối tượng theo ma trận va chạm thường dùng cho công trình hạ tầng kỹ thuật (đường, thoát nước,
    chiếu sáng, cây xanh). Nhận diện theo từ khoá tên (cả dạng có dấu và dạng bị mất ký tự khi xuất IFC)."""
    G = ClashGroup
    return [
        G("A1", "Đất quy hoạch / san nền", "", "*", "đất quy hoạch; san nền"),
        G("B1", "Mặt đường", "", "*", "mặt đường; phần xe chạy"),
        G("B2", "Rãnh đan", "", "*", "rãnh đan"),
        G("B3", "Vỉa hè", "", "*", "vỉa hè; hè phố"),
        G("B4", "Biển báo", "", "*", "biển báo; cột biển"),
        G("B5", "Cây xanh / hố trồng cây", "", "*", "cây xanh; cây cảnh quan; hố trồng cây; bồn cây"),
        G("B6", "Cột đèn chiếu sáng", "", "*", "cột đèn; đèn chiếu sáng"),
        G("C1", "Hố ga thoát nước mưa", "", "IfcDistributionChamberElement;IfcBuildingElementProxy",
          "TNM.hố ga; hố ga thoát nước mưa; ga thu nước mưa"),
        G("C2", "Đường ống thoát nước mưa", "", "IfcPipeSegment;IfcFlowSegment;IfcBuildingElementProxy",
          "TNM.đường ống; ống thoát nước mưa"),
        G("C3", "Cống hộp / cống ngang", "", "*", "cống hộp; cống ngang"),
        G("D1", "Hố ga thoát nước thải", "", "IfcDistributionChamberElement;IfcBuildingElementProxy",
          "TNT.hố ga; hố ga thoát nước thải"),
        G("D2", "Đường ống thoát nước thải", "", "IfcPipeSegment;IfcFlowSegment;IfcBuildingElementProxy",
          "TNT.đường ống; ống thoát nước thải"),
    ]


# (mã A, mã B, loại, giá trị m, định nghĩa / căn cứ)
_MATRIX = [
    ("C1", "B1", "FLUSH", 0.02, "Mặt hố ga TNM phải trùng cao độ mặt đường hoàn thiện. Hố ga nằm trong kết cấu "
                                "đường là đúng thiết kế; chỉ tính va chạm khi mặt ga lệch quá ±20 mm."),
    ("C1", "B3", "FLUSH", 0.02, "Mặt hố ga TNM nằm trên vỉa hè phải trùng cao độ mặt hè, lệch không quá ±20 mm."),
    ("D1", "B1", "FLUSH", 0.02, "Mặt hố ga TNT phải trùng cao độ mặt đường hoàn thiện, lệch không quá ±20 mm."),
    ("D1", "B3", "FLUSH", 0.02, "Mặt hố ga TNT nằm trên vỉa hè phải trùng cao độ mặt hè, lệch không quá ±20 mm."),
    ("C2", "B1", "COVER", 0.70, "Đỉnh ống TNM dưới lòng đường phải sâu ≥ 0,7 m so với mặt đường "
                                "(giá trị mẫu - đối chiếu TCVN 7957 và hồ sơ thiết kế)."),
    ("D2", "B1", "COVER", 0.70, "Đỉnh ống TNT dưới lòng đường phải sâu ≥ 0,7 m so với mặt đường (giá trị mẫu)."),
    ("C2", "B3", "COVER", 0.50, "Đỉnh ống TNM dưới vỉa hè phải sâu ≥ 0,5 m (giá trị mẫu)."),
    ("D2", "B3", "COVER", 0.50, "Đỉnh ống TNT dưới vỉa hè phải sâu ≥ 0,5 m (giá trị mẫu)."),
    ("C3", "B1", "COVER", 0.50, "Đỉnh cống ngang dưới đường phải có lớp đắp ≥ 0,5 m (giá trị mẫu)."),
    ("C2", "D2", "CLEARANCE", 0.20, "Ống TNM và ống TNT giao cắt / đi song song phải cách nhau tối thiểu 0,2 m "
                                    "(giá trị mẫu - đối chiếu QCVN 07:2023/BXD)."),
    ("C3", "D2", "CLEARANCE", 0.20, "Cống ngang và ống TNT phải cách nhau tối thiểu 0,2 m (giá trị mẫu)."),
    ("C2", "D1", "HARD", 0.0, "Ống TNM không được xuyên qua hố ga TNT."),
    ("D2", "C1", "HARD", 0.0, "Ống TNT không được xuyên qua hố ga TNM."),
    ("C1", "D1", "HARD", 0.0, "Hố ga TNM và hố ga TNT không được chồng lấn."),
    ("B6", "C2", "CLEARANCE", 0.50, "Móng/cột đèn cách ống TNM tối thiểu 0,5 m (giá trị mẫu)."),
    ("B6", "D2", "CLEARANCE", 0.50, "Móng/cột đèn cách ống TNT tối thiểu 0,5 m (giá trị mẫu)."),
    ("B5", "C2", "CLEARANCE", 1.00, "Cây xanh / hố trồng cây cách ống TNM tối thiểu 1,0 m (giá trị mẫu - QCVN 07)."),
    ("B5", "D2", "CLEARANCE", 1.00, "Cây xanh / hố trồng cây cách ống TNT tối thiểu 1,0 m (giá trị mẫu)."),
    ("B4", "B1", "HARD", 0.01, "Biển báo không được đặt trong phần xe chạy."),
    ("B6", "B1", "HARD", 0.01, "Cột đèn không được đặt trong phần xe chạy."),
    ("B5", "B1", "HARD", 0.01, "Cây xanh / hố trồng cây không được nằm trong phần xe chạy."),
    ("B4", "B6", "CLEARANCE", 0.50, "Biển báo không trùng vị trí cột đèn, cách tối thiểu 0,5 m."),
    ("B4", "C1", "HARD", 0.0, "Cột biển báo không được đặt trên hố ga TNM."),
    ("B6", "C1", "HARD", 0.0, "Cột đèn không được đặt trên hố ga TNM."),
]

# Các cặp KHÔNG kiểm tra vì tiếp xúc là đúng thiết kế (ghi vào template để người dùng biết)
MATRIX_EXCLUDED = [
    ("C1", "C2", "Hố ga và ống cùng hệ TNM: ống nối vào ga là đúng thiết kế"),
    ("D1", "D2", "Hố ga và ống cùng hệ TNT: ống nối vào ga là đúng thiết kế"),
    ("B1", "B2", "Mặt đường tiếp giáp rãnh đan"),
    ("B1", "B3", "Mặt đường tiếp giáp vỉa hè (qua bó vỉa)"),
    ("A1", "*", "Bề mặt san nền/địa hình là bề mặt tham chiếu, không kiểm tra va chạm khối"),
]


def default_matrix(groups: list[ClashGroup] | None = None) -> list[ClashTest]:
    groups = groups or default_matrix_groups()
    g = {x.code: x for x in groups}
    tests = []
    for a, b, mode, val, note in _MATRIX:
        ga, gb = g[a], g[b]
        tests.append(ClashTest(
            name=f"{a}×{b} {ga.name} – {gb.name}", enabled=True, mode=mode, tolerance=val,
            a_file=ga.file, a_class=ga.classes, a_name=ga.keywords,
            b_file=gb.file, b_class=gb.classes, b_name=gb.keywords,
            code=f"{a}-{b}", a_group=a, b_group=b, note=note))
    return tests


# ---------------------------------------------------------------- lưu / đọc

def tests_from_records(rows: list[dict]) -> list[ClashTest]:
    names = {f.name for f in fields(ClashTest)}
    out = []
    for d in rows:
        d = {k: v for k, v in d.items() if k in names}
        if not str(d.get("name", "")).strip() and not d.get("a_file") and not d.get("b_file") \
                and not d.get("a_name") and not d.get("b_name"):
            continue
        t = ClashTest(**{k: ("" if v is None or (isinstance(v, float) and np.isnan(v)) else v) for k, v in d.items()})
        t.name = str(t.name or "").strip() or f"Phép thử {len(out) + 1}"
        t.enabled = bool(t.enabled) and str(t.enabled).lower() not in ("false", "0", "", "nan")
        for k in ("a_file", "b_file", "a_name", "b_name", "code", "a_group", "b_group", "note"):
            setattr(t, k, str(getattr(t, k) or "").strip())
        t.a_class, t.b_class = str(t.a_class or "*").strip() or "*", str(t.b_class or "*").strip() or "*"
        t.mode = str(t.mode or "HARD").strip().upper()
        if t.mode not in MODES:
            t.mode = "HARD"
        try:
            t.tolerance = float(str(t.tolerance).replace(",", "."))
        except (TypeError, ValueError):
            t.tolerance = 0.01
        try:
            t.max_results = int(float(t.max_results))
        except (TypeError, ValueError):
            t.max_results = 5000
        out.append(t)
    return out


def tests_to_records(tests: list[ClashTest]) -> list[dict]:
    return [asdict(t) for t in tests]


_XLS_COLS = [("code", "Mã ô"), ("enabled", "Bật"), ("name", "Tên phép thử"), ("mode", "Loại kiểm tra"),
             ("tolerance", "Giá trị (m)"), ("a_group", "Nhóm A"), ("a_file", "A - lọc tên file (regex)"),
             ("a_class", "A - lớp IFC"), ("a_name", "A - từ khoá tên"), ("b_group", "Nhóm B"),
             ("b_file", "B - lọc tên file (regex)"), ("b_class", "B - lớp IFC"), ("b_name", "B - từ khoá tên"),
             ("max_results", "Tối đa"), ("note", "Định nghĩa va chạm / căn cứ")]


def save_matrix(tests: list[ClashTest], path: str, groups: list[ClashGroup] | None = None) -> None:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    groups = groups if groups is not None else default_matrix_groups()
    hdr_fill, hdr_font = PatternFill("solid", fgColor="1F4E78"), Font(bold=True, color="FFFFFF")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Phep_thu"
    ws.append([lbl for _, lbl in _XLS_COLS])
    for t in tests:
        ws.append([("x" if t.enabled else "") if k == "enabled" else getattr(t, k) for k, _ in _XLS_COLS])
    for i, w in enumerate([8, 6, 44, 14, 10, 8, 20, 26, 30, 8, 20, 26, 30, 8, 80], start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    for c in ws[1]:
        c.fill, c.font = hdr_fill, hdr_font
        c.alignment = Alignment(wrap_text=True)
    for row in ws.iter_rows(min_row=2, min_col=15, max_col=15):
        row[0].alignment = Alignment(wrap_text=True, vertical="top")
    dv = DataValidation(type="list", formula1='"' + ",".join(MODES) + '"', allow_blank=False)
    dv.add(f"D2:D{max(len(tests) + 100, 200)}")
    ws.add_data_validation(dv)
    ws.freeze_panes = "C2"

    wg = wb.create_sheet("Nhom")
    wg.append(["Mã nhóm", "Tên nhóm", "Lọc tên file (regex)", "Lớp IFC", "Từ khoá tên (;)", "Ghi chú"])
    for g in groups:
        wg.append([g.code, g.name, g.file, g.classes, g.keywords, g.note])
    for i, w in enumerate([9, 30, 22, 50, 50, 40], start=1):
        wg.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    for c in wg[1]:
        c.fill, c.font = hdr_fill, hdr_font

    wx = wb.create_sheet("Khong_kiem_tra")
    wx.append(["Nhóm A", "Nhóm B", "Lý do không kiểm tra (tiếp xúc đúng thiết kế)"])
    for a, b, why in MATRIX_EXCLUDED:
        wx.append([a, b, why])
    wx.column_dimensions["C"].width = 80
    for c in wx[1]:
        c.fill, c.font = hdr_fill, hdr_font

    wh = wb.create_sheet("Huong_dan")
    wh.column_dimensions["A"].width = 22
    wh.column_dimensions["B"].width = 110
    rows = [("MA TRẬN VA CHẠM", ""), ("", "")] + [(f"{k} - {MODE_VN[k]}", MODE_HELP[k]) for k in MODES] + [
        ("", ""),
        ("Nhóm đối tượng", "Đối tượng thuộc nhóm khi thoả đồng thời: tên file khớp regex (để trống = mọi file), lớp IFC "
                           "khớp (gồm lớp con, * = mọi lớp) và tên chứa 1 trong các từ khoá. Tên được xét gồm: tên "
                           "đối tượng, tên đối tượng cha, thuộc tính Name/Tên đối tượng. Từ khoá không phân biệt "
                           "hoa/thường, có dấu/không dấu, và tự khớp cả dạng bị mất ký tự tiếng Việt khi xuất IFC "
                           "(vd 'Mặt đường' khớp 'Mt ng')."),
        ("Giá trị mẫu", "Các giá trị khoảng cách / chiều sâu là giá trị MẪU để bắt đầu - cần đối chiếu QCVN 07:2023/BXD, "
                        "TCVN 7957, chỉ dẫn kỹ thuật và hồ sơ thiết kế của dự án trước khi dùng cho thẩm tra chính thức."),
    ]
    for a, b in rows:
        wh.append([a, b])
    wh["A1"].font = Font(bold=True, size=14)
    for r in wh.iter_rows(min_row=3):
        r[0].font = Font(bold=True)
        r[1].alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)


def load_matrix(path: str) -> list[ClashTest]:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb["Phep_thu"] if "Phep_thu" in wb.sheetnames else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    groups = {}
    if "Nhom" in wb.sheetnames:
        for r in list(wb["Nhom"].iter_rows(values_only=True))[1:]:
            if r and r[0]:
                groups[str(r[0]).strip()] = ClashGroup(str(r[0]).strip(), str(r[1] or ""), str(r[2] or ""),
                                                       str(r[3] or "*"), str(r[4] or ""), str(r[5] or ""))
    wb.close()
    if not rows:
        return []
    label_to_key = {norm(lbl): k for k, lbl in _XLS_COLS}
    keys = [label_to_key.get(norm(str(h or ""))) for h in rows[0]]
    recs = []
    for r in rows[1:]:
        if not r or all(c in (None, "") for c in r):
            continue
        d = {k: v for k, v in zip(keys, r) if k}
        d["enabled"] = norm(str(d.get("enabled") or "")) not in ("", "0", "false", "no", "khong")
        # ô lọc để trống mà có mã nhóm -> lấy theo sheet Nhom
        for side in ("a", "b"):
            g = groups.get(str(d.get(f"{side}_group") or "").strip())
            if g:
                if not d.get(f"{side}_file"):
                    d[f"{side}_file"] = g.file
                if not d.get(f"{side}_class"):
                    d[f"{side}_class"] = g.classes
                if not d.get(f"{side}_name"):
                    d[f"{side}_name"] = g.keywords
        recs.append(d)
    return tests_from_records(recs)
