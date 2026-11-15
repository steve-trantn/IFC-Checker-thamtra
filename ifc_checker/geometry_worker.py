"""Tiến trình con dựng hình 3D cho 1 file IFC.

Chạy:  python -m ifc_checker.geometry_worker <file.ifc> <out.npz> [geometry_library]
In tiến độ ra stdout dạng "P <done> <total>", ghi kết quả vào file .npz.
Lý do chạy riêng: bộ nhớ của nhân hình học (C++) được hệ điều hành thu hồi hoàn toàn khi tiến trình kết thúc,
và nhiều file có thể dựng song song.
"""

from __future__ import annotations

import sys

import numpy as np


def run(path: str, out_npz: str, library: str) -> int:
    import ifcopenshell

    from .geometry import _iterate, _sanitize
    from .model_loader import EXCLUDED_ELEMENT_CLASSES

    f = ifcopenshell.open(path)
    _sanitize(f)
    elements = [e for e in f.by_type("IfcElement")
                if e.Representation is not None and not any(e.is_a(x) for x in EXCLUDED_ELEMENT_CLASSES)]
    out, offset = {}, [None]

    def progress(done, total, _msg=""):
        print(f"P {done} {total}", flush=True)
    if elements:
        try:
            _iterate(f, elements, library, out, offset, progress, None, "", len(elements))
        except Exception:
            out.clear()
        if library != "opencascade":
            missing = [e for e in elements if e.id() not in out]
            if missing and len(missing) <= max(50, len(elements) // 2):
                try:
                    _iterate(f, missing, "opencascade", out, offset, progress, None, "", len(elements),
                             len(elements) - len(missing))
                except Exception:
                    pass
    meshes = list(out.values())
    vcount = np.array([len(m.local) for m in meshes], dtype=np.int64)
    fcount = np.array([len(m.faces) for m in meshes], dtype=np.int64)
    np.savez(
        out_npz,
        ids=np.array([m.step_id for m in meshes], dtype=np.int64),
        guids=np.array([m.guid for m in meshes], dtype="U22"),
        offset=offset[0] if offset[0] is not None else np.zeros(3),
        verts=np.concatenate([m.local for m in meshes]) if meshes else np.zeros((0, 3), np.float32),
        faces=np.concatenate([m.faces for m in meshes]) if meshes else np.zeros((0, 3), np.int32),
        vcount=vcount, fcount=fcount,
        bboxes=np.array([m.bbox for m in meshes]) if meshes else np.zeros((0, 2, 3)),
    )
    print(f"P {len(elements)} {len(elements)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "hybrid-cgal-simple-opencascade"))
