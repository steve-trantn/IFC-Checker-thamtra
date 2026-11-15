"""Tính lưới tam giác (mesh) cho các đối tượng IFC - dùng chung cho Xem 3D và Kiểm tra xung đột.

Kết quả được lưu đệm trên ModelInfo (model.meshes) để chỉ tính 1 lần.
Toạ độ giữ nguyên hệ toạ độ thế giới (float64) - VN-2000 có giá trị rất lớn,
nên khi hiển thị/tính va chạm sẽ trừ đi 1 điểm gốc chung để tránh mất chính xác.
"""

from __future__ import annotations

import multiprocessing
from dataclasses import dataclass

import numpy as np

from .model_loader import EXCLUDED_ELEMENT_CLASSES, ModelInfo


@dataclass
class Mesh:
    step_id: int
    guid: str
    verts: np.ndarray   # (n, 3) float64, toạ độ thế giới
    faces: np.ndarray   # (m, 3) int32
    bbox: np.ndarray    # (2, 3) min/max


def compute_meshes(model: ModelInfo, progress=None, cancel=None) -> dict[int, Mesh]:
    """Trả về {step_id: Mesh}. Lưu đệm trên model."""
    cached = getattr(model, "meshes", None)
    if cached is not None:
        return cached
    import ifcopenshell.geom as G

    out: dict[int, Mesh] = {}
    if model.ifc is None:
        model.meshes = out
        return out
    f = model.ifc
    # Sửa tạm trong bộ nhớ (không ghi ra file): bỏ IfcStyledItem bị đặt nhầm vào Items của biểu diễn hình học
    try:
        for r in f.by_type("IfcShapeRepresentation"):
            items = r.Items or ()
            good = tuple(i for i in items if not i.is_a("IfcStyledItem"))
            if len(good) != len(items):
                r.Items = good
    except Exception:
        pass
    s = G.settings()
    s.set("use-world-coords", True)
    s.set("weld-vertices", True)
    try:
        elements = [e for e in f.by_type("IfcElement")
                    if e.Representation is not None and not any(e.is_a(x) for x in EXCLUDED_ELEMENT_CLASSES)]
    except Exception:
        elements = []
    if not elements:
        model.meshes = out
        return out
    it = G.iterator(s, f, max(1, multiprocessing.cpu_count() - 1), include=elements)
    total = len(elements)
    n = 0
    if it.initialize():
        while True:
            if cancel and cancel():
                break
            sh = it.get()
            g = sh.geometry
            v = np.asarray(g.verts, dtype=np.float64).reshape(-1, 3)
            fc = np.asarray(g.faces, dtype=np.int32).reshape(-1, 3)
            if len(fc) and len(v):
                out[sh.id] = Mesh(sh.id, sh.guid, v, fc, np.vstack([v.min(axis=0), v.max(axis=0)]))
            n += 1
            if progress and n % 200 == 0:
                progress(n, total, f"Dựng hình 3D {model.file_name}: {n}/{total}")
            if not it.next():
                break
    model.meshes = out
    return out


def common_origin(models: list[ModelInfo]) -> np.ndarray:
    """Điểm gốc chung (tâm bao của tất cả mesh) để trừ đi trước khi đổi sang float32."""
    mins, maxs = [], []
    for m in models:
        for mesh in (getattr(m, "meshes", None) or {}).values():
            mins.append(mesh.bbox[0])
            maxs.append(mesh.bbox[1])
    if not mins:
        return np.zeros(3)
    lo = np.min(np.array(mins), axis=0)
    hi = np.max(np.array(maxs), axis=0)
    return np.round((lo + hi) / 2.0, 0)


def descendants(model: ModelInfo) -> dict[int, list[int]]:
    """step_id cha -> các step_id con (đệ quy), dùng để tô màu đối tượng cha không có hình học."""
    by_guid = {r.guid: r for r in model.records}
    kids: dict[int, list[int]] = {}
    for r in model.records:
        if r.parent_guid and r.parent_guid in by_guid:
            kids.setdefault(by_guid[r.parent_guid].step_id, []).append(r.step_id)
    out: dict[int, list[int]] = {}

    def walk(i, acc, depth=0):
        for k in kids.get(i, []):
            acc.append(k)
            if depth < 6:
                walk(k, acc, depth + 1)
        return acc
    for i in kids:
        out[i] = walk(i, [])
    return out
