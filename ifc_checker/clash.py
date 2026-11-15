"""Kiểm tra xung đột (va chạm) giữa các mô hình IFC.

Mỗi phép thử (ClashTest) so sánh nhóm A với nhóm B; nhóm được xác định bằng
regex tên file + lớp IFC (giống quy tắc BEP). Va chạm được tính trên lưới tam giác
bằng thư viện FCL (cây AABB động + kiểm tra giao tam giác), độ sâu xuyên lấy theo
điểm tiếp xúc sâu nhất của từng cặp đối tượng.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field, fields

import numpy as np

from .geometry import Mesh, common_origin, compute_meshes
from .model_loader import ElementRecord, ModelInfo
from .textnorm import split_list, strip_accents


@dataclass
class ClashTest:
    name: str = ""
    enabled: bool = True
    a_file: str = ""      # regex tên file nhóm A ('' = mọi file)
    a_class: str = "*"    # lớp IFC nhóm A
    b_file: str = ""
    b_class: str = "*"
    tolerance: float = 0.01   # m - bỏ qua va chạm có độ sâu xuyên nhỏ hơn
    max_results: int = 5000

    def is_self(self) -> bool:
        return (self.a_file, self.a_class) == (self.b_file, self.b_class)


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
    depth: float
    status: str = "Mới"
    note: str = ""


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


def _stem_token(file_name: str) -> str:
    stem = os.path.splitext(file_name)[0]
    return re.escape(stem)


def default_tests(models: list[ModelInfo], tolerance: float = 0.01) -> list[ClashTest]:
    """Mỗi cặp file là 1 phép thử (A < B)."""
    names = [m.file_name for m in models if not m.error]
    tests = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            tests.append(ClashTest(name=f"{_short(a)} × {_short(b)}", a_file=_stem_token(a),
                                   b_file=_stem_token(b), tolerance=tolerance))
    return tests


def _short(file_name: str) -> str:
    stem = os.path.splitext(file_name)[0]
    parts = [p for p in re.split(r"[-_ ]", stem) if p]
    return parts[-1] if parts else stem


def _group(models: list[ModelInfo], file_re: str, classes: str) -> list[tuple[ModelInfo, ElementRecord, Mesh]]:
    rx = re.compile(strip_accents(file_re), re.IGNORECASE) if file_re else None
    cls = [c for c in split_list(classes or "*", allow_comma=True) if c != "*"]
    out = []
    for m in models:
        if rx and not rx.search(strip_accents(m.file_name)):
            continue
        meshes = getattr(m, "meshes", None) or {}
        if not meshes:
            continue
        by_id = {r.step_id: r for r in m.records}
        for sid, mesh in meshes.items():
            rec = by_id.get(sid)
            if rec is None:
                continue
            if cls and not any(rec.is_a(c) for c in cls):
                continue
            out.append((m, rec, mesh))
    return out


def _label(model: ModelInfo, rec: ElementRecord, by_guid: dict) -> str:
    if rec.display_name() and not rec.display_name().startswith("#"):
        return rec.display_name()
    parent = by_guid.get(rec.parent_guid)
    if parent is not None:
        return f"{parent.display_name()} (con #{rec.step_id})"
    return rec.display_name()


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
            rx = re.compile(strip_accents(fr), re.IGNORECASE) if fr else None
            for m in models:
                if not m.error and (rx is None or rx.search(strip_accents(m.file_name))):
                    need.add(m.path)
    todo = [m for m in models if m.path in need]
    for i, m in enumerate(todo):
        if cancel and cancel():
            return run
        if progress:
            progress(i, len(todo), f"Dựng hình 3D {m.file_name} ({i + 1}/{len(todo)})...")
        compute_meshes(m, cancel=cancel)
    origin = common_origin(todo)
    guid_maps = {m.path: {r.guid: r for r in m.records} for m in todo}

    # 2. Bộ đệm đối tượng FCL theo (file, step_id)
    fcl_cache: dict[tuple, object] = {}

    def fcl_obj(m: ModelInfo, mesh: Mesh):
        """Trả về (CollisionObject, BVHModel). Kết quả va chạm của FCL tham chiếu tới BVHModel."""
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

    n_clash = 0
    for ti, t in enumerate(active):
        if cancel and cancel():
            break
        if progress:
            progress(ti, len(active), f"Xung đột: {t.name} ({ti + 1}/{len(active)})")
        try:
            ga = _group(todo, t.a_file, t.a_class)
            gb = ga if t.is_self() else _group(todo, t.b_file, t.b_class)
        except re.error as ex:
            run.messages.append(f"{t.name}: regex lỗi {ex}")
            continue
        if not ga or not gb:
            run.messages.append(f"{t.name}: nhóm A ({len(ga)}) hoặc nhóm B ({len(gb)}) không có đối tượng hình học")
            continue
        lookup = {}
        objs_a = []
        for m, rec, mesh in ga:
            o, g = fcl_obj(m, mesh)
            lookup[id(g)] = (m, rec)
            objs_a.append(o)
        objs_b = []
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
            if ma.path == mb.path and ra.step_id == rb.step_id:
                continue
            # bỏ va chạm giữa cha - con / các con cùng cha trong cùng file (cùng 1 cấu kiện)
            if ma.path == mb.path and (ra.guid == rb.parent_guid or rb.guid == ra.parent_guid):
                continue
            key = tuple(sorted([(ma.path, ra.step_id), (mb.path, rb.step_id)]))
            d = float(c.penetration_depth)
            if key not in best or d > best[key][0]:
                best[key] = (d, np.asarray(c.pos, dtype=np.float64), (ma, ra), (mb, rb))
        rows = sorted((v for v in best.values() if v[0] >= t.tolerance), key=lambda v: -v[0])
        if len(rows) > t.max_results:
            run.messages.append(f"{t.name}: {len(rows)} va chạm, chỉ giữ {t.max_results} va chạm sâu nhất")
            rows = rows[:t.max_results]
        for d, pos, (ma, ra), (mb, rb) in rows:
            n_clash += 1
            p = pos + origin
            run.clashes.append(Clash(
                id=f"C{n_clash:05d}", test=t.name,
                file_a=ma.file_name, step_a=ra.step_id, guid_a=ra.guid, class_a=ra.ifc_class,
                name_a=_label(ma, ra, guid_maps[ma.path]),
                file_b=mb.file_name, step_b=rb.step_id, guid_b=rb.guid, class_b=rb.ifc_class,
                name_b=_label(mb, rb, guid_maps[mb.path]),
                x=round(float(p[0]), 3), y=round(float(p[1]), 3), z=round(float(p[2]), 3), depth=round(d, 4)))
        run.messages.append(f"{t.name}: {len(ga)} × {len(gb)} đối tượng → {len(rows)} va chạm")
    if progress:
        progress(len(active), len(active), f"Xong kiểm tra xung đột: {len(run.clashes)} va chạm")
    return run


# ---------------------------------------------------------------- lưu / đọc phép thử

TEST_COLUMNS = [("name", "Tên phép thử"), ("enabled", "Bật"), ("a_file", "Nhóm A - lọc tên file"),
                ("a_class", "Nhóm A - lớp IFC"), ("b_file", "Nhóm B - lọc tên file"),
                ("b_class", "Nhóm B - lớp IFC"), ("tolerance", "Dung sai (m)"), ("max_results", "Tối đa")]


def tests_from_records(rows: list[dict]) -> list[ClashTest]:
    names = {f.name for f in fields(ClashTest)}
    out = []
    for d in rows:
        d = {k: v for k, v in d.items() if k in names}
        if not str(d.get("name", "")).strip() and not d.get("a_file") and not d.get("b_file"):
            continue
        t = ClashTest(**d)
        t.name = str(t.name or "").strip() or f"Phép thử {len(out) + 1}"
        t.enabled = bool(t.enabled) and str(t.enabled).lower() not in ("false", "0", "")
        t.a_file, t.b_file = str(t.a_file or "").strip(), str(t.b_file or "").strip()
        t.a_class, t.b_class = str(t.a_class or "*").strip() or "*", str(t.b_class or "*").strip() or "*"
        try:
            t.tolerance = float(t.tolerance)
        except (TypeError, ValueError):
            t.tolerance = 0.01
        try:
            t.max_results = int(t.max_results)
        except (TypeError, ValueError):
            t.max_results = 5000
        out.append(t)
    return out


def tests_to_records(tests: list[ClashTest]) -> list[dict]:
    return [asdict(t) for t in tests]
