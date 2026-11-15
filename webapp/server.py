"""Máy chủ web Thẩm Tra Mô Hình BIM.

Chạy:  python -m uvicorn webapp.server:app --host 0.0.0.0 --port 8000   (hoặc run_web.bat)
"""

from __future__ import annotations

import os
import time
from collections import Counter

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ifc_checker import APP_NAME, AUTHOR, AUTHOR_PHONE, __version__
from ifc_checker.clash import (MODE_HELP, MODE_VN, MODES, default_matrix, default_tests, fcl_available,
                               load_matrix, run_clash, save_matrix, tests_from_records, tests_to_records)
from ifc_checker.engine import CheckOptions, CheckResult, run_checks
from ifc_checker.model_loader import load_model
from ifc_checker.report import export_excel, export_html
from ifc_checker.rules import (CONDITIONS, SEVERITIES, TARGETS, Rule, default_ruleset, load_rules, save_rules)
from ifc_checker.textnorm import value_to_str
from ifc_checker.viewer_data import build_scene, scene_key

from .workspace import WorkspaceStore, safe_name

ROOT = os.path.dirname(os.path.abspath(__file__))
VIEWER = os.path.join(os.path.dirname(ROOT), "ifc_checker", "viewer")
app = FastAPI(title=APP_NAME, version=__version__)
app.mount("/static", StaticFiles(directory=os.path.join(ROOT, "static")), name="static")
app.mount("/viewer", StaticFiles(directory=VIEWER), name="viewer")
store = WorkspaceStore()
SEV_VN = {"ERROR": "Lỗi", "WARNING": "Cảnh báo", "INFO": "Thông tin"}


@app.middleware("http")
async def session_cookie(request: Request, call_next):
    sid = request.cookies.get("sid")
    ws = store.get(sid)
    request.state.sid = ws.sid
    resp = await call_next(request)
    if sid != ws.sid:
        resp.set_cookie("sid", ws.sid, httponly=True, samesite="lax", max_age=7 * 24 * 3600)
    return resp


def ws_of(request: Request):
    return store.get(request.state.sid)


@app.get("/")
def index():
    return FileResponse(os.path.join(ROOT, "static", "index.html"))


# ====================================================================== trạng thái
@app.get("/api/state")
def state(request: Request):
    ws = ws_of(request)
    res = ws.result
    return {
        "app": {"name": APP_NAME, "version": __version__, "author": AUTHOR, "phone": AUTHOR_PHONE},
        "models": [{"file": m.file_name, "schema": m.schema, "app": m.application, "project": m.project_name,
                    "elements": len(m.elements), "parts": m.part_count, "error": m.error, "warnings": m.warnings,
                    "proxies": sum(1 for r in m.elements if r.ifc_class == "IfcBuildingElementProxy"),
                    "seconds": round(m.load_seconds, 1)} for m in ws.model_list()],
        "rules": {"source": ws.ruleset.source, "count": len(ws.ruleset.rules),
                  "enabled": sum(r.enabled for r in ws.ruleset.rules), "ids": os.path.basename(ws.ruleset.ids_path)},
        "result": (res.totals() | {"started": res.started}) if res else None,
        "clash": {"tests": len(ws.clash_tests), "enabled": sum(t.enabled for t in ws.clash_tests),
                  "count": len(ws.clash_run.clashes) if ws.clash_run else None, "fcl": fcl_available()},
        "scene": {"key": ws.scene[2] if ws.scene else None, "dirty": ws.scene_dirty},
        "settings": {"part_mode": ws.part_mode, "classify": ws.check_classification, "ai_model": ws.ai_model,
                     "ai_key": bool(ws.api_key or os.environ.get("ANTHROPIC_API_KEY"))},
        "busy": ws.busy(),
        "options": {"targets": TARGETS, "conditions": CONDITIONS, "severities": SEVERITIES,
                    "clash_modes": [{"k": k, "vn": MODE_VN[k], "help": MODE_HELP[k]} for k in MODES]},
    }


@app.get("/api/jobs/{jid}")
def job(jid: str, request: Request):
    j = ws_of(request).jobs.get(jid)
    if not j:
        raise HTTPException(404, "Không có tác vụ")
    return j.to_dict()


def _need_idle(ws):
    if ws.busy():
        raise HTTPException(409, "Đang xử lý tác vụ khác, vui lòng đợi")


# ====================================================================== mô hình
@app.post("/api/models")
async def upload(request: Request, files: list[UploadFile] = File(...)):
    ws = ws_of(request)
    _need_idle(ws)
    paths = []
    for f in files:
        name = safe_name(f.filename)
        if not name.lower().endswith(".ifc"):
            continue
        p = os.path.join(ws.tmpdir, name)
        with open(p, "wb") as out:
            while chunk := await f.read(1 << 20):
                out.write(chunk)
        paths.append(p)
    if not paths:
        raise HTTPException(400, "Không có file .ifc")

    def work(progress):
        for i, p in enumerate(paths):
            progress(i, len(paths), f"Đang đọc {os.path.basename(p)} ({i + 1}/{len(paths)})")
            m = load_model(p)
            ws.models[m.file_name] = m
        ws.scene_dirty = True
        return len(paths)
    return ws.start_job("Đọc file IFC", work).to_dict()


@app.delete("/api/models/{name}")
def remove_model(name: str, request: Request):
    ws = ws_of(request)
    _need_idle(ws)
    ws.models.pop(name, None)
    ws.scene_dirty = True
    return {"ok": True}


@app.get("/api/health")
def health(request: Request):
    """Sức khoẻ mô hình: thống kê lớp IFC, Proxy, cảnh báo file, mức độ điền thuộc tính."""
    from ifc_checker.engine import class_counts, property_inventory, select_records
    ws = ws_of(request)
    classes, inventory, storeys = [], [], Counter()
    for m in ws.ok_models():
        classes += [list(x) for x in class_counts(m)]
        recs = select_records(m, ws.part_mode)[0]
        inventory += [list(x) for x in property_inventory(m, recs)]
        for r in recs:
            if r.kind == "element":
                storeys[(m.file_name, r.container_name or "(không thuộc tầng)")] += 1
    return {"classes": classes, "inventory": inventory[:20000],
            "storeys": [[f, s, n] for (f, s), n in storeys.most_common()]}


@app.get("/api/select")
def select(request: Request, file: str = "", ifc_class: str = "", storey: str = ""):
    """Danh sách đối tượng theo file / lớp / tầng (để tô sáng hoặc cô lập trong 3D)."""
    from ifc_checker.engine import select_records
    ws = ws_of(request)
    refs = []
    for m in ws.ok_models():
        if file and m.file_name != file:
            continue
        for r in select_records(m, ws.part_mode)[0]:
            if r.kind != "element":
                continue
            if ifc_class and r.ifc_class != ifc_class:
                continue
            if storey and (r.container_name or "(không thuộc tầng)") != storey:
                continue
            refs.append([m.file_name, r.step_id])
    return {"refs": refs, "count": len(refs)}


@app.get("/api/element")
def element(request: Request, file: str, step: int):
    ws = ws_of(request)
    m, r = ws.record(file, step)
    if r is None:
        raise HTTPException(404, "Không tìm thấy đối tượng")
    by_guid = {x.guid: x for x in m.records}
    parent = by_guid.get(r.parent_guid)
    issues = [{"rule": i.rule_id, "sev": i.severity, "msg": i.message, "actual": i.actual}
              for i in (ws.result.issues if ws.result else []) if i.file == file and i.step_id == step]
    clashes = [{"id": c.id, "test": c.test, "detail": c.detail} for c in (ws.clash_run.clashes if ws.clash_run else [])
               if (c.file_a == file and c.step_a == step) or (c.file_b == file and c.step_b == step)][:50]
    return {
        "file": file, "step": step, "guid": r.guid, "class": r.ifc_class,
        "identity": [["Tên (Name)", r.name], ["Lớp IFC", r.ifc_class], ["PredefinedType", r.predefined_type],
                     ["GlobalId", r.guid], ["#STEP", str(r.step_id)], ["ObjectType", r.object_type],
                     ["Loại (Type)", r.type_name], ["Mô tả", r.description], ["Tag", r.tag],
                     ["Tầng / không gian", r.container_name], ["Đối tượng cha", parent.name if parent else ""],
                     ["Vật liệu", "; ".join(r.materials)], ["Phân loại", "; ".join(r.classifications)],
                     ["File", file]],
        "psets": [{"name": p.strip(), "props": [[k.strip(), value_to_str(v)] for k, v in props.items()]}
                  for p, props in r.psets.items()],
        "issues": issues, "clashes": clashes,
    }


# ====================================================================== cảnh 3D
@app.post("/api/scene")
def scene_build(request: Request):
    ws = ws_of(request)
    if ws.scene is not None and not ws.scene_dirty:
        return {"status": "done", "key": ws.scene[2]}
    _need_idle(ws)
    models, res, run = ws.ok_models(), ws.result, ws.clash_run

    def work(progress):
        data, meta = build_scene(models, res, run, progress)
        ws.scene = (data, meta, scene_key(data, meta))
        ws.scene_dirty = False
        return ws.scene[2]
    return ws.start_job("Dựng mô hình 3D", work).to_dict()


@app.get("/api/scene/meta")
def scene_meta(request: Request):
    ws = ws_of(request)
    if ws.scene is None:
        raise HTTPException(404, "Chưa dựng cảnh 3D")
    return JSONResponse(ws.scene[1])


@app.get("/api/scene/data")
def scene_data(request: Request):
    ws = ws_of(request)
    if ws.scene is None:
        raise HTTPException(404, "Chưa dựng cảnh 3D")
    return Response(ws.scene[0], media_type="application/octet-stream")


# ====================================================================== quy tắc
def _rules_json(ws):
    from dataclasses import asdict
    return {"source": ws.ruleset.source, "ids": os.path.basename(ws.ruleset.ids_path),
            "rules": [asdict(r) for r in ws.ruleset.rules], "warnings": ws.ruleset.validate()}


@app.get("/api/rules")
def rules_get(request: Request):
    return _rules_json(ws_of(request))


@app.put("/api/rules")
async def rules_put(request: Request):
    ws = ws_of(request)
    body = await request.json()
    names = set(Rule.__dataclass_fields__)
    rules = []
    for d in body.get("rules", []):
        r = Rule(**{k: v for k, v in d.items() if k in names})
        r.enabled = bool(d.get("enabled", True))
        if r.id or r.name:
            rules.append(r.normalized())
    ws.ruleset.rules = rules
    return _rules_json(ws)


@app.post("/api/rules/default")
def rules_default(request: Request):
    ws = ws_of(request)
    ws.ruleset = default_ruleset()
    return _rules_json(ws)


@app.post("/api/rules/import")
async def rules_import(request: Request, file: UploadFile = File(...)):
    ws = ws_of(request)
    p = os.path.join(ws.tmpdir, safe_name(file.filename))
    with open(p, "wb") as out:
        out.write(await file.read())
    try:
        rs = load_rules(p)
    except Exception as ex:
        raise HTTPException(400, f"Không đọc được: {ex}")
    if p.lower().endswith(".ids"):
        ws.ruleset.ids_path = p
    else:
        rs.source = os.path.basename(p)
        rs.ids_path = ws.ruleset.ids_path
        ws.ruleset = rs
    return _rules_json(ws)


@app.delete("/api/rules/ids")
def rules_ids_clear(request: Request):
    ws = ws_of(request)
    ws.ruleset.ids_path = ""
    return _rules_json(ws)


@app.get("/api/rules/export")
def rules_export(request: Request):
    ws = ws_of(request)
    p = os.path.join(ws.tmpdir, "BEP_Rules.xlsx")
    save_rules(ws.ruleset, p)
    return FileResponse(p, filename="BEP_Rules.xlsx")


@app.post("/api/rules/loi/parse")
async def loi_parse(request: Request, file: UploadFile = File(...)):
    from ifc_checker.bep_import import components, guess_mapping, parse_loi
    ws = ws_of(request)
    p = os.path.join(ws.tmpdir, safe_name(file.filename))
    with open(p, "wb") as out:
        out.write(await file.read())
    items = parse_loi(p)
    if not items:
        raise HTTPException(400, "Không tìm thấy bảng LOI (cần bảng có cột 'Nhóm | Thông tin | Kiểu')")
    ws._loi = (items, os.path.basename(p))
    models = ws.ok_models()
    rows = []
    for c in components(items):
        cls, ff = guess_mapping(c, [m.file_name for m in models], models, ws.ruleset.mappings)
        rows.append({"component": c, "count": sum(1 for i in items if i.component == c), "ifc_class": cls,
                     "file_filter": ff})
    return {"items": len(items), "components": rows, "files": [m.file_name for m in models]}


@app.post("/api/rules/loi/apply")
async def loi_apply(request: Request):
    from ifc_checker.bep_import import build_rules
    ws = ws_of(request)
    body = await request.json()
    items, src = getattr(ws, "_loi", (None, ""))
    if not items:
        raise HTTPException(400, "Hãy tải file BEP trước")
    mapping = {r["component"]: (r.get("ifc_class") or "*", r.get("file_filter") or "") for r in body["components"]}
    rules = build_rules(items, mapping, source=src)
    if body.get("mode") == "replace":
        ws.ruleset.rules = [r for r in ws.ruleset.rules if r.id.startswith("GEN")] + rules
        ws.ruleset.source = f"(nhập từ {src})"
    else:
        have = {r.id for r in ws.ruleset.rules}
        for r in rules:
            while r.id in have:
                r.id += "b"
            have.add(r.id)
        ws.ruleset.rules += rules
    return _rules_json(ws)


# ====================================================================== kiểm tra
@app.post("/api/check")
def check(request: Request):
    ws = ws_of(request)
    _need_idle(ws)
    if not ws.ok_models():
        raise HTTPException(400, "Chưa có file IFC")
    models, rs = ws.ok_models(), ws.ruleset
    opts = CheckOptions(part_mode=ws.part_mode, check_classification=ws.check_classification)

    def work(progress):
        res = run_checks(models, rs, opts, progress)
        if ws.clash_run is not None:
            res.clashes, res.clash_messages = ws.clash_run.clashes, ws.clash_run.messages
        res.ai_summary = ws.ai_summary
        ws.result = res
        ws.scene_dirty = True
        return res.totals()
    return ws.start_job("Kiểm tra theo bộ quy tắc", work).to_dict()


@app.get("/api/results")
def results(request: Request, file: str = "", rule: str = "", sev: str = "", cls: str = "", q: str = "",
            offset: int = 0, limit: int = 500):
    ws = ws_of(request)
    res = ws.result
    if res is None:
        return {"totals": None}
    ql = q.lower().strip()
    rows = [i for i in res.issues
            if (not file or i.file == file) and (not rule or i.rule_id == rule) and (not sev or i.severity == sev)
            and (not cls or i.ifc_class == cls)
            and (not ql or ql in f"{i.element_name} {i.guid} {i.message} {i.actual} {i.ifc_class}".lower())]
    return {
        "totals": res.totals(), "started": res.started, "source": res.ruleset_source,
        "files": [{"file": f.file, "checked": f.checked, "skipped": f.parts_skipped, "proxies": f.proxies,
                   "compliance": round(f.compliance, 1), "errors": f.errors, "warnings": f.warnings,
                   "note": f.error} for f in res.files],
        "rules": [{"file": s.file, "rule": s.rule_id, "desc": s.description, "sev": s.severity,
                   "applicable": s.applicable, "failed": s.failed, "rate": round(s.rate, 1)}
                  for s in res.rule_stats if s.applicable],
        "filters": {"files": sorted({i.file for i in res.issues}), "rules": sorted({i.rule_id for i in res.issues}),
                    "classes": sorted({i.ifc_class for i in res.issues if i.ifc_class})},
        "count": len(rows),
        "rows": [{"file": i.file, "step": i.step_id, "guid": i.guid, "sev": i.severity, "rule": i.rule_id,
                  "cls": i.ifc_class, "name": i.element_name, "msg": i.message, "actual": i.actual,
                  "pset": i.pset, "prop": i.prop}
                 for i in rows[offset:offset + limit]],
        "refs": list({(i.file, i.step_id): [i.file, i.step_id] for i in rows}.values())[:50000],
    }


# ====================================================================== xung đột
@app.get("/api/clash/tests")
def clash_tests(request: Request):
    return {"tests": tests_to_records(ws_of(request).clash_tests)}


@app.put("/api/clash/tests")
async def clash_tests_put(request: Request):
    ws = ws_of(request)
    ws.clash_tests = tests_from_records((await request.json()).get("tests", []))
    return {"tests": tests_to_records(ws.clash_tests)}


@app.post("/api/clash/preset/{kind}")
def clash_preset(kind: str, request: Request):
    ws = ws_of(request)
    ws.clash_tests = default_matrix() if kind == "matrix" else default_tests(ws.ok_models())
    return {"tests": tests_to_records(ws.clash_tests)}


@app.post("/api/clash/import")
async def clash_import(request: Request, file: UploadFile = File(...)):
    ws = ws_of(request)
    p = os.path.join(ws.tmpdir, safe_name(file.filename))
    with open(p, "wb") as out:
        out.write(await file.read())
    try:
        ws.clash_tests = load_matrix(p)
    except Exception as ex:
        raise HTTPException(400, f"Không đọc được ma trận: {ex}")
    return {"tests": tests_to_records(ws.clash_tests)}


@app.get("/api/clash/export")
def clash_export(request: Request):
    ws = ws_of(request)
    p = os.path.join(ws.tmpdir, "Ma_tran_va_cham.xlsx")
    save_matrix(ws.clash_tests, p)
    return FileResponse(p, filename="Ma_tran_va_cham.xlsx")


@app.post("/api/clash/run")
def clash_run(request: Request):
    ws = ws_of(request)
    _need_idle(ws)
    if not fcl_available():
        raise HTTPException(400, "Máy chủ chưa cài python-fcl")
    models, tests = ws.ok_models(), list(ws.clash_tests)

    def work(progress):
        run = run_clash(models, tests, progress)
        ws.clash_run = run
        if ws.result is not None:
            ws.result.clashes, ws.result.clash_messages = run.clashes, run.messages
        ws.scene_dirty = True
        return len(run.clashes)
    return ws.start_job("Kiểm tra xung đột", work).to_dict()


@app.get("/api/clash/results")
def clash_results(request: Request, test: str = "", kind: str = "", offset: int = 0, limit: int = 1000):
    ws = ws_of(request)
    run = ws.clash_run
    if run is None:
        return {"count": None}
    rows = [c for c in run.clashes if (not test or c.test == test) and (not kind or c.kind == kind)]
    return {"count": len(rows), "total": len(run.clashes), "messages": run.messages,
            "tests": [[t, n] for t, n in Counter(c.test for c in run.clashes).most_common()],
            "rows": [{"id": c.id, "test": c.test, "kind": c.kind, "kind_vn": MODE_VN.get(c.kind, c.kind),
                      "detail": c.detail, "value": c.depth, "a": c.name_a, "ca": c.class_a, "fa": c.file_a,
                      "sa": c.step_a, "b": c.name_b, "cb": c.class_b, "fb": c.file_b, "sb": c.step_b,
                      "x": c.x, "y": c.y, "z": c.z, "note": c.note} for c in rows[offset:offset + limit]]}


@app.get("/api/clash/bcf")
def clash_bcf(request: Request, test: str = "", kind: str = ""):
    from ifc_checker.bcf import export_bcf
    ws = ws_of(request)
    if ws.clash_run is None:
        raise HTTPException(400, "Chưa có kết quả xung đột")
    rows = [c for c in ws.clash_run.clashes if (not test or c.test == test) and (not kind or c.kind == kind)]
    p = os.path.join(ws.tmpdir, "XungDot.bcfzip")
    export_bcf(rows, p)
    return FileResponse(p, filename="XungDot.bcfzip")


# ====================================================================== báo cáo
def _report_result(ws) -> CheckResult:
    if ws.result is not None:
        ws.result.ai_summary = ws.ai_summary
        return ws.result
    r = CheckResult(started=time.strftime("%Y-%m-%d %H:%M:%S"), ruleset_source="(chưa kiểm tra quy tắc)")
    if ws.clash_run:
        r.clashes, r.clash_messages = ws.clash_run.clashes, ws.clash_run.messages
    r.ai_summary = ws.ai_summary
    return r


@app.get("/api/report.xlsx")
def report_xlsx(request: Request):
    ws = ws_of(request)
    p = os.path.join(ws.tmpdir, "BaoCao_ThamTra.xlsx")
    export_excel(_report_result(ws), p)
    return FileResponse(p, filename=f"BaoCao_ThamTra_{time.strftime('%Y%m%d_%H%M')}.xlsx")


@app.get("/api/report.html")
def report_html(request: Request):
    ws = ws_of(request)
    p = os.path.join(ws.tmpdir, "BaoCao_ThamTra.html")
    export_html(_report_result(ws), p)
    return FileResponse(p, filename=f"BaoCao_ThamTra_{time.strftime('%Y%m%d_%H%M')}.html")


# ====================================================================== cài đặt & AI
@app.put("/api/settings")
async def settings(request: Request):
    ws = ws_of(request)
    b = await request.json()
    if "part_mode" in b and b["part_mode"] in ("auto", "parents", "all"):
        ws.part_mode = b["part_mode"]
    if "classify" in b:
        ws.check_classification = bool(b["classify"])
    if "ai_model" in b and b["ai_model"]:
        ws.ai_model = str(b["ai_model"])
    if "api_key" in b:
        ws.api_key = str(b["api_key"] or "").strip()   # chỉ giữ trong bộ nhớ phiên, không ghi ra đĩa
    return state(request)


@app.post("/api/ai/chat")
async def ai_chat(request: Request):
    from ifc_checker.ai import AIError
    from .assistant import chat
    ws = ws_of(request)
    b = await request.json()
    if not ws.models:
        raise HTTPException(400, "Hãy mở file IFC trước")
    try:
        import anyio
        return await anyio.to_thread.run_sync(lambda: chat(ws, b.get("history", []), b.get("question", "")))
    except AIError as ex:
        raise HTTPException(400, str(ex))


@app.post("/api/ai/summary")
async def ai_summary(request: Request):
    from ifc_checker.ai import AIClient, AIError
    ws = ws_of(request)
    if ws.result is None:
        raise HTTPException(400, "Hãy chạy kiểm tra trước")
    try:
        import anyio
        text = await anyio.to_thread.run_sync(lambda: AIClient(ws.api_key, ws.ai_model, "medium").summarize(ws.result))
    except AIError as ex:
        raise HTTPException(400, str(ex))
    ws.ai_summary = ws.result.ai_summary = text
    return {"text": text}


@app.put("/api/ai/summary")
async def ai_summary_put(request: Request):
    ws = ws_of(request)
    ws.ai_summary = (await request.json()).get("text", "")
    if ws.result:
        ws.result.ai_summary = ws.ai_summary
    return {"ok": True}

