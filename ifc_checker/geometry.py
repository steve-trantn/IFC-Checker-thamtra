"""Tính lưới tam giác (mesh) cho các đối tượng IFC - dùng chung cho Xem 3D và Kiểm tra xung đột.

Hiệu năng:
  - Mặc định dùng nhân hình học "hybrid-cgal-simple-opencascade" của IfcOpenShell (mã nguồn mở):
    nhanh hơn OpenCASCADE thuần 8-60 lần với mô hình dạng lưới tam giác (Civil 3D, Revit...),
    kết quả hình học như nhau. Đối tượng nào không dựng được sẽ tự thử lại bằng OpenCASCADE.
    Đổi bằng biến môi trường GEOM_LIBRARY (opencascade | cgal-simple | manifold | hybrid-cgal-simple-opencascade).
  - Toạ độ lưu float32 tương đối so với 1 điểm gốc của mô hình (VN-2000 có giá trị rất lớn) -> giảm 1/2 RAM.
Kết quả được lưu đệm trên ModelInfo (model.meshes) để chỉ tính 1 lần.
"""

from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from .model_loader import EXCLUDED_ELEMENT_CLASSES, ModelInfo

DEFAULT_LIBRARY = "hybrid-cgal-simple-opencascade"


@dataclass
class Mesh:
    step_id: int
    guid: str
    local: np.ndarray    # (n, 3) float32, toạ độ tương đối so với offset
    offset: np.ndarray   # (3,) float64
    faces: np.ndarray    # (m, 3) int32
    bbox: np.ndarray     # (2, 3) float64, toạ độ thế giới
    colors: np.ndarray | None = None   # (n, 3) uint8 - màu IFC (IfcSurfaceStyle) theo đỉnh

    @property
    def verts(self) -> np.ndarray:
        """Toạ độ thế giới (float64) - tạo tạm khi cần."""
        return self.local.astype(np.float64) + self.offset

    def relative_to(self, origin: np.ndarray) -> np.ndarray:
        """Toạ độ float32 tương đối so với 'origin' (dùng cho trình xem 3D)."""
        return (self.local + (self.offset - origin).astype(np.float32)).astype(np.float32)


def _sanitize(f) -> None:
    """Sửa tạm trong bộ nhớ: bỏ IfcStyledItem bị đặt nhầm vào Items của biểu diễn hình học (lỗi xuất IFC)."""
    try:
        for r in f.by_type("IfcShapeRepresentation"):
            items = r.Items or ()
            good = tuple(i for i in items if not i.is_a("IfcStyledItem"))
            if len(good) != len(items):
                r.Items = good
    except Exception:
        pass


DEFAULT_RGB = np.array([178, 182, 190], dtype=np.uint8)


def _vertex_colors(g, nverts: int, faces: np.ndarray) -> np.ndarray:
    """Màu từng đỉnh theo vật liệu hiển thị của từng mặt (IfcSurfaceStyleShading/Rendering -> SurfaceColour).
    Đối tượng không có kiểu bề mặt dùng màu mặc định theo lớp của IfcOpenShell."""
    vc = np.empty((nverts, 3), dtype=np.uint8)
    vc[:] = DEFAULT_RGB
    try:
        mats = g.materials
        if not len(mats):
            return vc
        pal = []
        for m in mats:
            d = m.diffuse
            rgb = [d.r(), d.g(), d.b()]
            if any(x != x for x in rgb):          # NaN -> chưa khai báo màu
                pal.append(DEFAULT_RGB / 255.0)
            else:
                pal.append([min(1.0, max(0.0, x)) for x in rgb])
        pal = (np.asarray(pal) * 255 + 0.5).astype(np.uint8)
        mids = np.asarray(g.material_ids, dtype=np.int64)
        if len(mids) == len(faces) and mids.min(initial=0) >= 0 and mids.max(initial=0) < len(pal):
            vc[faces.reshape(-1)] = np.repeat(pal[mids], 3, axis=0)
        else:
            vc[:] = pal[0]
    except Exception:
        pass
    return vc


def _iterate(f, elements, library, out, offset_holder, progress, cancel, label, total, done0=0):
    import ifcopenshell.geom as G

    s = G.settings()
    s.set("use-world-coords", True)
    s.set("weld-vertices", True)
    try:
        s.set("no-normals", True)      # không cần pháp tuyến: trình xem tự tính
    except Exception:
        pass
    threads = int(os.environ.get("GEOM_THREADS") or max(1, multiprocessing.cpu_count() - 1))
    it = G.iterator(s, f, threads, include=elements, geometry_library=library)
    n = done0
    if not it.initialize():
        return n
    while True:
        if cancel and cancel():
            break
        sh = it.get()
        g = sh.geometry
        v = np.asarray(g.verts, dtype=np.float64).reshape(-1, 3)
        fc = np.asarray(g.faces, dtype=np.int32).reshape(-1, 3)
        if len(fc) and len(v):
            if offset_holder[0] is None:
                offset_holder[0] = np.round(v.mean(axis=0), 0)
            off = offset_holder[0]
            out[sh.id] = Mesh(sh.id, sh.guid, (v - off).astype(np.float32), off, fc,
                              np.vstack([v.min(axis=0), v.max(axis=0)]), _vertex_colors(g, len(v), fc))
        n += 1
        if progress and n % 200 == 0:
            progress(n, total, f"{label}: {n}/{total}")
        if not it.next():
            break
    return n


def _use_subprocess() -> bool:
    return os.environ.get("GEOM_SUBPROCESS", "1") != "0" and not getattr(sys, "frozen", False)


def _compute_subprocess(model: ModelInfo, progress=None, cancel=None, threads: int = 0) -> dict[int, Mesh]:
    """Dựng hình trong tiến trình con (giải phóng hết RAM nhân hình học khi xong)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fd, npz = tempfile.mkstemp(suffix=".npz", prefix="tt_geom_")
    os.close(fd)
    try:
        cmd = [sys.executable, "-m", "ifc_checker.geometry_worker", model.path, npz,
               os.environ.get("GEOM_LIBRARY", DEFAULT_LIBRARY)]
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        env = dict(os.environ, GEOM_THREADS=str(threads)) if threads else None
        proc = subprocess.Popen(cmd, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                creationflags=flags, env=env)
        for line in proc.stdout:
            if cancel and cancel():
                proc.kill()
                raise RuntimeError("Đã huỷ")
            parts = line.split()
            if len(parts) == 3 and parts[0] == "P" and progress:
                progress(int(parts[1]), int(parts[2]), f"Dựng hình 3D {model.file_name}: {parts[1]}/{parts[2]}")
        if proc.wait() != 0:
            raise RuntimeError("Tiến trình dựng hình lỗi")
        with np.load(npz) as d:   # mỗi lần d["x"] là 1 lần giải nén -> nạp 1 lần duy nhất
            verts, faces, off = d["verts"], d["faces"], d["offset"]
            ids, guids, vcount, fcount, bboxes = (d["ids"].tolist(), d["guids"].tolist(), d["vcount"].tolist(),
                                                  d["fcount"].tolist(), d["bboxes"])
            cols = d["colors"] if "colors" in d.files else None
        out: dict[int, Mesh] = {}
        vs = fs = 0
        for sid, g, vc, fc, bb in zip(ids, guids, vcount, fcount, bboxes):
            out[sid] = Mesh(sid, g, verts[vs:vs + vc], off, faces[fs:fs + fc], bb,
                            cols[vs:vs + vc] if cols is not None else None)
            vs += vc
            fs += fc
        return out
    finally:
        try:
            os.remove(npz)
        except OSError:
            pass


def compute_many(models: list[ModelInfo], progress=None, cancel=None) -> None:
    """Dựng hình cho nhiều file song song (mỗi file 1 tiến trình con)."""
    todo = [m for m in models if getattr(m, "meshes", None) is None and not (m.error and not m.records)]
    if not todo:
        return
    state = {m.path: (0, 1) for m in todo}

    def prog_for(m):
        def p(done, total, msg=""):
            state[m.path] = (done, max(total, 1))
            if progress:
                d = sum(x[0] for x in state.values())
                t = sum(x[1] for x in state.values())
                progress(d, t, msg)
        return p
    workers = 1 if not _use_subprocess() else max(1, min(3, len(todo), (os.cpu_count() or 4) // 4))
    if workers == 1:
        for m in todo:
            compute_meshes(m, prog_for(m), cancel)
        return
    threads = max(2, (os.cpu_count() or 4) // workers)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda m: compute_meshes(m, prog_for(m), cancel, threads), todo))


def compute_meshes(model: ModelInfo, progress=None, cancel=None, threads: int = 0) -> dict[int, Mesh]:
    """Trả về {step_id: Mesh}. Lưu đệm trên model."""
    cached = getattr(model, "meshes", None)
    if cached is not None:
        return cached
    out: dict[int, Mesh] = {}
    if model.error and not model.records:
        model.meshes = out
        return out
    if _use_subprocess() and os.path.exists(model.path):
        try:
            model.meshes = _compute_subprocess(model, progress, cancel, threads)
            return model.meshes
        except Exception:
            if cancel and cancel():
                raise
            # lỗi tiến trình con -> dựng ngay trong tiến trình chính
    from .model_loader import reopen_ifc
    f = reopen_ifc(model)
    _sanitize(f)
    try:
        elements = [e for e in f.by_type("IfcElement")
                    if e.Representation is not None and not any(e.is_a(x) for x in EXCLUDED_ELEMENT_CLASSES)]
    except Exception:
        elements = []
    if not elements:
        model.meshes = out
        return out
    library = os.environ.get("GEOM_LIBRARY", DEFAULT_LIBRARY)
    label = f"Dựng hình 3D {model.file_name}"
    offset = [None]
    try:
        _iterate(f, elements, library, out, offset, progress, cancel, label, len(elements))
    except Exception:
        out.clear()
    # đối tượng nhân nhanh không dựng được -> thử lại bằng OpenCASCADE (chậm nhưng đầy đủ nhất)
    if library != "opencascade" and not (cancel and cancel()):
        missing = [e for e in elements if e.id() not in out]
        if missing and len(missing) <= max(50, len(elements) // 2):
            try:
                _iterate(f, missing, "opencascade", out, offset, progress, cancel, label + " (bổ sung)",
                         len(elements), len(elements) - len(missing))
            except Exception:
                pass
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
