"""Đóng gói hình học + trạng thái kiểm tra thành dữ liệu cho trình xem 3D (three.js).

Định dạng:
  data.bin  = [positions float32 (n*3)] [indices uint32 (m*3)]
  meta.json = {origin, files, classes, objects:[...], clashes:[...], issues:{obj: [msg]}}
Mỗi object: [file_idx, step_id, guid, class_idx, name, status, v0, vn, t0, tn, bbox(6)]
status: 2 = lỗi, 1 = cảnh báo, 0 = đạt, -1 = không kiểm tra
"""

from __future__ import annotations

import hashlib
import json

import numpy as np

from .engine import select_records
from .geometry import common_origin, compute_many, descendants
from .model_loader import ModelInfo

SEV_CODE = {"ERROR": 2, "WARNING": 1, "INFO": 0}


def build_scene(models: list[ModelInfo], result=None, clash_run=None, progress=None, cancel=None):
    """Trả về (bytes, meta dict). Tự dựng hình cho các model chưa có mesh."""
    models = [m for m in models if not m.error or m.records]
    compute_many(models, progress, cancel)     # song song, mỗi file 1 tiến trình con
    origin = common_origin(models)

    # trạng thái theo (file, step_id)
    status: dict[tuple, int] = {}
    msgs: dict[tuple, list] = {}
    checked: set[tuple] = set()
    if result is not None:
        for i in result.issues:
            k = (i.file, i.step_id)
            status[k] = max(status.get(k, -1), SEV_CODE.get(i.severity, 0))
            lst = msgs.setdefault(k, [])
            if len(lst) < 8:
                lst.append(f"[{i.rule_id}] {i.message}")

    files, classes, objects = [], [], []
    class_idx: dict[str, int] = {}
    obj_index: dict[tuple, int] = {}
    issues_out: dict[str, list] = {}
    pos_parts, idx_parts = [], []
    vbase = tbase = 0
    for fi, m in enumerate(models):
        files.append(m.file_name)
        meshes = getattr(m, "meshes", None) or {}
        recs = {r.step_id: r for r in m.records}
        by_guid = {r.guid: r for r in m.records}
        if result is not None:
            sel, _ = select_records(m, "auto")
            for r in sel:
                checked.add((m.file_name, r.step_id))
        # đối tượng cha không có hình học: truyền trạng thái/lỗi xuống các con
        inherited: dict[int, tuple] = {}
        if result is not None:
            for parent, kids in descendants(m).items():
                k = (m.file_name, parent)
                if k in status and parent not in meshes:
                    for kid in kids:
                        inherited[kid] = k
        for sid, mesh in meshes.items():
            rec = recs.get(sid)
            cls = rec.ifc_class if rec else ""
            if cls not in class_idx:
                class_idx[cls] = len(classes)
                classes.append(cls)
            key = (m.file_name, sid)
            src = key if key in status else inherited.get(sid, key)
            if src in status:
                st = status[src]
            elif result is not None and (key in checked or src in checked):
                st = 0
            else:
                st = -1
            name = ""
            if rec:
                name = rec.display_name()
                if name.startswith("#"):
                    parent = by_guid.get(rec.parent_guid)
                    if parent:
                        name = f"{parent.display_name()} › #{sid}"
            v = mesh.relative_to(origin)
            f = (mesh.faces.astype(np.int64) + vbase).astype(np.uint32)
            bb = np.vstack([v.min(axis=0), v.max(axis=0)]).reshape(-1)
            oi = len(objects)
            par = by_guid.get(rec.parent_guid) if rec else None
            objects.append([fi, int(sid), mesh.guid, class_idx[cls], name, st, vbase, len(v), tbase, len(f),
                            [round(float(x), 3) for x in bb], par.step_id if par is not None else -1])
            obj_index[key] = oi
            if src in msgs:
                issues_out[str(oi)] = msgs[src]
            pos_parts.append(v.reshape(-1))
            idx_parts.append(f.reshape(-1))
            vbase += len(v)
            tbase += len(f)

    clashes = []
    if clash_run is not None:
        for c in clash_run.clashes:
            a = obj_index.get((c.file_a, c.step_a))
            b = obj_index.get((c.file_b, c.step_b))
            p = np.array([c.x, c.y, c.z]) - origin
            clashes.append({"id": c.id, "a": a, "b": b, "p": [round(float(x), 3) for x in p], "d": c.depth,
                            "t": c.test, "m": getattr(c, "detail", ""), "k": getattr(c, "kind", "HARD")})

    pos = np.concatenate(pos_parts) if pos_parts else np.zeros(0, np.float32)
    idx = np.concatenate(idx_parts) if idx_parts else np.zeros(0, np.uint32)
    data = pos.astype("<f4").tobytes() + idx.astype("<u4").tobytes()
    meta = {
        "version": 1,
        "origin": [float(x) for x in origin],
        "files": files,
        "classes": classes,
        "objects": objects,
        "clashes": clashes,
        "issues": issues_out,
        "nverts": int(vbase),
        "ntris": int(tbase),
        "posBytes": int(pos.size * 4),
    }
    return data, meta


def scene_key(data: bytes, meta: dict) -> str:
    h = hashlib.sha1()
    h.update(data[:1_000_000])
    h.update(str(len(data)).encode())
    h.update(json.dumps(meta, sort_keys=True)[:2_000_000].encode())
    return h.hexdigest()[:16]


def find_object(meta: dict, file_name: str, step_id: int) -> int | None:
    try:
        fi = meta["files"].index(file_name)
    except ValueError:
        return None
    for i, o in enumerate(meta["objects"]):
        if o[0] == fi and o[1] == step_id:
            return i
    return None


def objects_for(meta: dict, file_name: str, step_id: int, model: ModelInfo | None = None) -> list[int]:
    """Đối tượng 3D tương ứng 1 đối tượng IFC (nếu không có hình học thì lấy các con)."""
    i = find_object(meta, file_name, step_id)
    if i is not None:
        return [i]
    if model is None:
        return []
    kids = descendants(model).get(step_id, [])
    fi = meta["files"].index(file_name) if file_name in meta["files"] else -1
    want = set(kids)
    return [j for j, o in enumerate(meta["objects"]) if o[0] == fi and o[1] in want]
