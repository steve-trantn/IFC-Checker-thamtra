"""Lưu / mở dự án: file IFC + bộ quy tắc + ma trận va chạm + kết quả + cài đặt.

Cấu trúc trên đĩa (DATA_DIR, mặc định <thư mục phần mềm>/data):
  projects/<chủ sở hữu>/<mã dự án>/project.json
                                  /ifc/*.ifc
                                  /extra/  (IDS, bản vẽ DXF...)
File .ttproj tải về = zip của thư mục trên. Chỉ dùng JSON (không pickle) nên an toàn khi tải lên lại.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
import zipfile
from dataclasses import asdict, fields

from ifc_checker.clash import Clash, ClashRun, tests_from_records, tests_to_records
from ifc_checker.engine import CheckResult, FileSummary, Issue, RuleStat
from ifc_checker.rules import ClassMapping, Rule, RuleSet

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORMAT = 1


def data_dir() -> str:
    d = os.environ.get("DATA_DIR") or os.path.join(ROOT, "data")
    os.makedirs(d, exist_ok=True)
    return d


def owner_dir(owner: str) -> str:
    tag = re.sub(r"[^a-z0-9]+", "_", (owner or "local").lower())[:40] + "_" + \
        hashlib.sha1((owner or "local").encode()).hexdigest()[:8]
    d = os.path.join(data_dir(), "projects", tag)
    os.makedirs(d, exist_ok=True)
    return d


def _safe_id(pid: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{12}", pid or ""):
        raise ValueError("Mã dự án không hợp lệ")
    return pid


def _mk(cls, d: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in names})


# ---------------------------------------------------------------- tuần tự hoá
def result_to_json(res: CheckResult | None):
    if res is None:
        return None
    return {
        "started": res.started, "ruleset_source": res.ruleset_source,
        "files": [asdict(f) for f in res.files], "issues": [asdict(i) for i in res.issues],
        "rule_stats": [asdict(s) for s in res.rule_stats],
        "class_counts": [list(x) for x in res.class_counts], "inventory": [list(x) for x in res.inventory],
        "ai_suggestions": res.ai_suggestions, "ai_summary": res.ai_summary,
    }


def result_from_json(d) -> CheckResult | None:
    if not d:
        return None
    return CheckResult(
        started=d.get("started", ""), ruleset_source=d.get("ruleset_source", ""),
        files=[_mk(FileSummary, x) for x in d.get("files", [])],
        issues=[_mk(Issue, x) for x in d.get("issues", [])],
        rule_stats=[_mk(RuleStat, x) for x in d.get("rule_stats", [])],
        class_counts=[tuple(x) for x in d.get("class_counts", [])],
        inventory=[tuple(x) for x in d.get("inventory", [])],
        ai_suggestions=d.get("ai_suggestions", []), ai_summary=d.get("ai_summary", ""),
    )


def clash_to_json(run: ClashRun | None):
    if run is None:
        return None
    return {"tests": tests_to_records(run.tests), "clashes": [asdict(c) for c in run.clashes],
            "messages": run.messages}


def clash_from_json(d) -> ClashRun | None:
    if not d:
        return None
    return ClashRun(tests=tests_from_records(d.get("tests", [])),
                    clashes=[_mk(Clash, x) for x in d.get("clashes", [])], messages=d.get("messages", []))


# ---------------------------------------------------------------- danh sách / lưu / mở / xoá
def list_projects(owner: str) -> list[dict]:
    out = []
    base = owner_dir(owner)
    for pid in os.listdir(base):
        f = os.path.join(base, pid, "project.json")
        if not os.path.exists(f):
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            size = sum(os.path.getsize(os.path.join(r, x)) for r, _, fs in os.walk(os.path.join(base, pid)) for x in fs)
            out.append({"id": pid, "name": d.get("name", pid), "updated": d.get("updated", ""),
                        "files": [m["file"] for m in d.get("models", [])], "size_mb": round(size / 2**20, 1),
                        "has_result": bool(d.get("result")), "has_clash": bool(d.get("clash_run"))})
        except Exception:
            continue
    return sorted(out, key=lambda x: x["updated"], reverse=True)


def save_project(ws, owner: str, name: str, pid: str | None = None, progress=None) -> dict:
    pid = _safe_id(pid) if pid else uuid.uuid4().hex[:12]
    pdir = os.path.join(owner_dir(owner), pid)
    os.makedirs(os.path.join(pdir, "ifc"), exist_ok=True)
    os.makedirs(os.path.join(pdir, "extra"), exist_ok=True)
    keep = set()
    models = ws.model_list()
    for i, m in enumerate(models):
        if progress:
            progress(i, len(models) + 1, f"Lưu {m.file_name}...")
        dst = os.path.join(pdir, "ifc", m.file_name)
        keep.add(m.file_name)
        if os.path.abspath(m.path) != os.path.abspath(dst) and os.path.exists(m.path):
            shutil.copy2(m.path, dst)
            m.path = dst                    # lần lưu sau không chép lại
    for f in os.listdir(os.path.join(pdir, "ifc")):        # bỏ file đã đóng khỏi dự án
        if f not in keep:
            os.remove(os.path.join(pdir, "ifc", f))
    ids_name = ""
    if ws.ruleset.ids_path and os.path.exists(ws.ruleset.ids_path):
        ids_name = os.path.basename(ws.ruleset.ids_path)
        dst = os.path.join(pdir, "extra", ids_name)
        if os.path.abspath(ws.ruleset.ids_path) != os.path.abspath(dst):
            shutil.copy2(ws.ruleset.ids_path, dst)
    extras = {}
    for key, path in getattr(ws, "extra_files", {}).items():   # vd bản vẽ DXF dùng để đối chiếu
        if path and os.path.exists(path):
            dst = os.path.join(pdir, "extra", os.path.basename(path))
            if os.path.abspath(path) != os.path.abspath(dst):
                shutil.copy2(path, dst)
            extras[key] = os.path.basename(path)
    prev = {}
    pj = os.path.join(pdir, "project.json")
    if os.path.exists(pj):
        with open(pj, encoding="utf-8") as fh:
            prev = json.load(fh)
    d = {
        "format": FORMAT, "id": pid, "name": name or prev.get("name") or "Dự án",
        "created": prev.get("created") or time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"), "owner": owner,
        "models": [{"file": m.file_name, "schema": m.schema} for m in models],
        "ruleset": {"source": ws.ruleset.source, "rules": [asdict(r) for r in ws.ruleset.rules],
                    "mappings": [asdict(x) for x in ws.ruleset.mappings], "ids": ids_name},
        "clash_tests": tests_to_records(ws.clash_tests),
        "settings": {"part_mode": ws.part_mode, "classify": ws.check_classification, "ai_model": ws.ai_model},
        "ai_summary": ws.ai_summary,
        "result": result_to_json(ws.result),
        "clash_run": clash_to_json(ws.clash_run),
        "drawing": getattr(ws, "drawing_state", None),
        "extras": extras,
    }
    tmp = pj + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)
    os.replace(tmp, pj)
    ws.project = {"id": pid, "name": d["name"], "owner": owner}
    if progress:
        progress(1, 1, "Đã lưu dự án")
    return {"id": pid, "name": d["name"], "updated": d["updated"]}


def open_project(ws, owner: str, pid: str, progress=None) -> dict:
    from ifc_checker.model_loader import load_model, release_ifc

    pdir = os.path.join(owner_dir(owner), _safe_id(pid))
    with open(os.path.join(pdir, "project.json"), encoding="utf-8") as fh:
        d = json.load(fh)
    ws.models = {}
    files = d.get("models", [])
    for i, mm in enumerate(files):
        path = os.path.join(pdir, "ifc", os.path.basename(mm["file"]))
        if progress:
            progress(i, len(files), f"Mở {mm['file']} ({i + 1}/{len(files)})")
        if os.path.exists(path):
            m = load_model(path)
            if os.environ.get("KEEP_IFC", "0") != "1":
                release_ifc(m)
            ws.models[m.file_name] = m
    rs = d.get("ruleset") or {}
    ws.ruleset = RuleSet(rules=[_mk(Rule, r) for r in rs.get("rules", [])],
                         mappings=[_mk(ClassMapping, x) for x in rs.get("mappings", [])],
                         source=rs.get("source", ""),
                         ids_path=os.path.join(pdir, "extra", rs["ids"]) if rs.get("ids") else "")
    ws.clash_tests = tests_from_records(d.get("clash_tests", [])) or ws.clash_tests
    st = d.get("settings") or {}
    ws.part_mode = st.get("part_mode", ws.part_mode)
    ws.check_classification = st.get("classify", ws.check_classification)
    ws.ai_model = st.get("ai_model", ws.ai_model)
    ws.ai_summary = d.get("ai_summary", "")
    ws.result = result_from_json(d.get("result"))
    ws.clash_run = clash_from_json(d.get("clash_run"))
    if ws.result is not None and ws.clash_run is not None:
        ws.result.clashes, ws.result.clash_messages = ws.clash_run.clashes, ws.clash_run.messages
    ws.drawing_state = d.get("drawing")
    ws.extra_files = {k: os.path.join(pdir, "extra", v) for k, v in (d.get("extras") or {}).items()}
    ws.scene, ws.scene_dirty = None, True
    ws.project = {"id": d.get("id", pid), "name": d.get("name", ""), "owner": owner}
    return {"id": pid, "name": d.get("name", ""), "models": len(ws.models)}


def project_ifc_dir(owner: str, pid: str) -> str:
    d = os.path.join(owner_dir(owner), _safe_id(pid), "ifc")
    os.makedirs(d, exist_ok=True)
    return d


def reset_workspace(ws) -> None:
    """Không gian làm việc trống cho dự án mới (giữ cài đặt AI / chế độ kiểm tra)."""
    from ifc_checker.clash import default_matrix
    from ifc_checker.rules import default_ruleset
    ws.models = {}
    ws.ruleset = default_ruleset()
    ws.result = None
    ws.clash_tests = default_matrix()
    ws.clash_run = None
    ws.scene, ws.scene_dirty = None, True
    ws.ai_summary = ""
    ws.extra_files = {}
    ws.drawing_state = None
    ws.drawing = None
    ws.project = None


def delete_project(owner: str, pid: str) -> None:
    shutil.rmtree(os.path.join(owner_dir(owner), _safe_id(pid)), ignore_errors=True)


def export_zip(owner: str, pid: str, out_path: str) -> str:
    pdir = os.path.join(owner_dir(owner), _safe_id(pid))
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for r, _, fs in os.walk(pdir):
            for f in fs:
                if f.endswith(".tmp"):
                    continue
                full = os.path.join(r, f)
                z.write(full, os.path.relpath(full, pdir).replace(os.sep, "/"))
    return out_path


def import_zip(owner: str, zip_path: str) -> str:
    """Nhập file .ttproj: chỉ nhận project.json, ifc/*.ifc, extra/* (chặn đường dẫn nguy hiểm)."""
    pid = uuid.uuid4().hex[:12]
    pdir = os.path.join(owner_dir(owner), pid)
    os.makedirs(os.path.join(pdir, "ifc"))
    os.makedirs(os.path.join(pdir, "extra"))
    ok = False
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            n = info.filename.replace("\\", "/")
            if info.is_dir() or n.startswith("/") or ".." in n.split("/"):
                continue
            parts = n.split("/")
            if n == "project.json":
                dst = os.path.join(pdir, "project.json")
                ok = True
            elif len(parts) == 2 and parts[0] == "ifc" and parts[1].lower().endswith(".ifc"):
                dst = os.path.join(pdir, "ifc", os.path.basename(parts[1]))
            elif len(parts) == 2 and parts[0] == "extra":
                dst = os.path.join(pdir, "extra", os.path.basename(parts[1]))
            else:
                continue
            with z.open(info) as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)
    if not ok:
        shutil.rmtree(pdir, ignore_errors=True)
        raise ValueError("File không phải dự án Thẩm Tra (thiếu project.json)")
    with open(os.path.join(pdir, "project.json"), encoding="utf-8") as fh:
        d = json.load(fh)
    d["id"] = pid
    d["owner"] = owner
    with open(os.path.join(pdir, "project.json"), "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)
    return pid
