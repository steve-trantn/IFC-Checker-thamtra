"""Trợ lý AI "Hỏi mô hình": Claude dùng công cụ truy vấn trực tiếp dữ liệu IFC đã mở rồi trả lời bằng tiếng Việt.

Mỗi công cụ trả về số lượng + thống kê + danh sách mẫu; danh sách đối tượng đầy đủ được giữ lại phía máy chủ
dưới dạng "thẻ kết quả" để giao diện hiển thị nút Danh sách / Highlight 3D / Cô lập.
Chỉ gửi cho AI dữ liệu tóm tắt (tên lớp, tên, thuộc tính, thống kê), không gửi file IFC hay hình học.
"""

from __future__ import annotations

import json
from collections import Counter

from ifc_checker.engine import select_records
from ifc_checker.textnorm import is_empty, norm, value_to_str

SYSTEM = (
    "Bạn là trợ lý thẩm tra mô hình BIM, trả lời bằng tiếng Việt, ngắn gọn, chính xác. "
    "LUÔN dùng công cụ để truy vấn/đếm trên mô hình trước khi trả lời câu hỏi về số lượng, thuộc tính, lỗi "
    "kiểm tra hay xung đột - không đoán. Khi tìm theo tên, nhớ rằng tên có thể bị mất dấu do lỗi xuất IFC "
    "(công cụ đã tự xử lý). Trình bày: câu trả lời chính trước (có số liệu), sau đó chia theo loại nếu hữu ích, "
    "cuối cùng là lưu ý ngắn nếu dữ liệu có vấn đề. Không dùng tiêu đề markdown; được dùng gạch đầu dòng. "
    "Giao diện tự hiển thị thẻ kết quả kèm nút Danh sách / Highlight 3D / Cô lập cho mỗi lần tìm kiếm, "
    "nên không cần liệt kê toàn bộ GUID."
)

_STR = {"type": "string"}
TOOLS = [
    {"name": "model_overview",
     "description": "Tổng quan các file IFC đang mở: schema, phần mềm xuất, số đối tượng theo lớp IFC, danh sách "
                    "tầng/không gian chứa, các Property Set phổ biến. Gọi đầu tiên khi chưa rõ mô hình có gì.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "find_elements",
     "description": "Tìm và đếm đối tượng theo điều kiện (tất cả điều kiện đều tuỳ chọn, kết hợp AND). "
                    "Trả về số lượng, thống kê theo lớp/loại/tầng/file và danh sách mẫu.",
     "input_schema": {"type": "object", "properties": {
         "ifc_class": {"type": "string", "description": "Lớp IFC, vd IfcDoor, IfcPile (gồm lớp con). Nhiều lớp cách nhau ;"},
         "name_contains": {"type": "string", "description": "Từ khoá trong tên đối tượng / tên loại / tên đối tượng cha"},
         "storey_contains": {"type": "string", "description": "Từ khoá trong tên tầng / không gian chứa"},
         "file_contains": {"type": "string", "description": "Từ khoá trong tên file IFC"},
         "property_name": {"type": "string", "description": "Tên thuộc tính cần xét (trong mọi Pset)"},
         "property_state": {"type": "string", "enum": ["any", "missing", "empty", "filled", "equals", "contains"],
                            "description": "Điều kiện với property_name: missing=không có, empty=có nhưng trống, "
                                           "filled=đã điền, equals/contains=so với property_value"},
         "property_value": {"type": "string"},
         "material_state": {"type": "string", "enum": ["any", "missing", "present"]},
     }, "additionalProperties": False}},
    {"name": "element_details",
     "description": "Toàn bộ thông tin 1 đối tượng: thuộc tính gốc, Pset, vật liệu, lỗi kiểm tra.",
     "input_schema": {"type": "object", "properties": {"file": _STR, "step_id": {"type": "integer"}},
                      "required": ["file", "step_id"], "additionalProperties": False}},
    {"name": "check_summary",
     "description": "Tóm tắt kết quả kiểm tra theo bộ quy tắc BEP (nếu đã chạy): tỷ lệ đạt, số lỗi, các quy tắc "
                    "không đạt nhiều nhất.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "find_issues",
     "description": "Tìm các lỗi/cảnh báo kiểm tra theo mã quy tắc, mức độ, lớp IFC hoặc nội dung.",
     "input_schema": {"type": "object", "properties": {
         "rule_id": _STR, "severity": {"type": "string", "enum": ["ERROR", "WARNING", "INFO", "ANY"]},
         "ifc_class": _STR, "text_contains": _STR}, "additionalProperties": False}},
    {"name": "find_clashes",
     "description": "Tìm xung đột (nếu đã chạy kiểm tra xung đột) theo tên phép thử, loại kiểm tra hoặc nội dung.",
     "input_schema": {"type": "object", "properties": {
         "test_contains": _STR, "kind": {"type": "string", "enum": ["HARD", "CLEARANCE", "FLUSH", "COVER", "ANY"]},
         "text_contains": _STR}, "additionalProperties": False}},
]


class ModelQuery:
    """Thực thi các công cụ trên Workspace. Thu thập 'thẻ' (danh sách đối tượng) cho giao diện."""

    def __init__(self, ws):
        self.ws = ws
        self.cards: list[dict] = []

    # ------------------------------------------------------------------ tiện ích
    def _records(self):
        for m in self.ws.ok_models():
            by_guid = {r.guid: r for r in m.records}
            for r in select_records(m, self.ws.part_mode)[0]:
                if r.kind == "element":
                    yield m, r, by_guid

    @staticmethod
    def _names(r, by_guid):
        t = [r.name, r.object_type, r.type_name]
        p = by_guid.get(r.parent_guid)
        if p is not None:
            t += [p.name, p.object_type]
        for props in r.psets.values():
            for k, v in props.items():
                if norm(k) in ("name", "ten doi tuong", "ten") and v:
                    t.append(str(v))
        return " | ".join(norm(x) for x in t if x)

    @staticmethod
    def _kw_match(kw: str, text: str) -> bool:
        k = norm(kw)
        drop = norm("".join(ch for ch in kw if ord(ch) < 128))
        return (k and k in text) or (len(drop.replace(" ", "")) >= 3 and drop in text)

    def _card(self, title: str, refs: list, kind: str = "elements") -> str:
        cid = f"K{len(self.cards) + 1}"
        self.cards.append({"id": cid, "title": title, "count": len(refs), "refs": refs[:20000], "kind": kind})
        return cid

    # ------------------------------------------------------------------ công cụ
    def model_overview(self):
        files = []
        cls = Counter()
        storeys = Counter()
        psets = Counter()
        for m in self.ws.ok_models():
            files.append({"file": m.file_name, "schema": m.schema, "app": m.application,
                          "elements": len(m.elements), "warnings": m.warnings})
        for m, r, _ in self._records():
            cls[r.ifc_class] += 1
            storeys[r.container_name or "(không thuộc tầng)"] += 1
            for p in r.psets:
                psets[p.strip()] += 1
        return {"files": files, "classes": cls.most_common(40), "storeys": storeys.most_common(30),
                "common_psets": psets.most_common(25), "total_checked_objects": sum(cls.values())}

    def find_elements(self, ifc_class="", name_contains="", storey_contains="", file_contains="",
                      property_name="", property_state="any", property_value="", material_state="any"):
        classes = [c.strip() for c in (ifc_class or "").replace(",", ";").split(";") if c.strip()]
        pn = norm(property_name)
        hits = []
        for m, r, bg in self._records():
            if file_contains and norm(file_contains) not in norm(m.file_name):
                continue
            if classes and not any(r.is_a(c) for c in classes):
                continue
            if name_contains and not self._kw_match(name_contains, self._names(r, bg)):
                continue
            if storey_contains and not self._kw_match(storey_contains, norm(r.container_name)):
                continue
            if material_state == "missing" and r.materials:
                continue
            if material_state == "present" and not r.materials:
                continue
            if pn and property_state and property_state != "any":
                vals = [v for props in r.psets.values() for k, v in props.items() if norm(k) == pn]
                st = property_state
                if st == "missing" and vals:
                    continue
                if st == "empty" and not (vals and all(is_empty(v) for v in vals)):
                    continue
                if st == "filled" and not any(not is_empty(v) for v in vals):
                    continue
                if st == "equals" and not any(norm(value_to_str(v)) == norm(property_value) for v in vals):
                    continue
                if st == "contains" and not any(norm(property_value) in norm(value_to_str(v)) for v in vals):
                    continue
            elif pn:
                if not any(norm(k) == pn for props in r.psets.values() for k in props):
                    continue
            hits.append((m, r, bg))
        title = "Đối tượng: " + ", ".join(x for x in [ifc_class, name_contains, storey_contains, file_contains,
                                                      f"{property_name} {property_state} {property_value}".strip()
                                                      if pn else "", "thiếu vật liệu" if material_state == "missing"
                                                      else ""] if x)
        cid = self._card(title or "Đối tượng", [[m.file_name, r.step_id] for m, r, _ in hits])

        def label(m, r, bg):
            if r.name:
                return r.name
            p = bg.get(r.parent_guid)
            return (p.name if p else "") + f" #{r.step_id}"
        return {
            "count": len(hits), "card": cid,
            "by_class": Counter(r.ifc_class for _, r, _ in hits).most_common(15),
            "by_type": Counter((r.type_name or r.object_type or "(không loại)") for _, r, _ in hits).most_common(15),
            "by_storey": Counter((r.container_name or "(không thuộc tầng)") for _, r, _ in hits).most_common(15),
            "by_file": Counter(m.file_name for m, _, _ in hits).most_common(15),
            "sample": [{"file": m.file_name, "step_id": r.step_id, "class": r.ifc_class, "name": label(m, r, bg)[:60]}
                       for m, r, bg in hits[:15]],
        }

    def element_details(self, file, step_id):
        m, r = self.ws.record(file, int(step_id))
        if r is None:
            return {"error": "Không tìm thấy đối tượng"}
        issues = [f"[{i.rule_id}] {i.message}" for i in (self.ws.result.issues if self.ws.result else [])
                  if i.file == file and i.step_id == r.step_id]
        self._card(f"{r.ifc_class} #{r.step_id}", [[file, r.step_id]])
        return {"class": r.ifc_class, "name": r.name, "type": r.type_name, "predefined": r.predefined_type,
                "storey": r.container_name, "materials": r.materials, "classifications": r.classifications,
                "psets": {p.strip(): {k.strip(): value_to_str(v)[:80] for k, v in props.items()}
                          for p, props in list(r.psets.items())[:12]},
                "issues": issues[:20]}

    def check_summary(self):
        res = self.ws.result
        if res is None:
            return {"error": "Chưa chạy kiểm tra theo bộ quy tắc BEP"}
        worst = sorted((s for s in res.rule_stats if s.applicable), key=lambda s: -s.failed)[:15]
        return {"totals": res.totals(),
                "files": [{"file": f.file, "checked": f.checked, "compliance": round(f.compliance, 1),
                           "errors": f.errors, "warnings": f.warnings, "note": f.error} for f in res.files],
                "worst_rules": [{"rule": s.rule_id, "desc": s.description, "failed": s.failed,
                                 "applicable": s.applicable} for s in worst]}

    def find_issues(self, rule_id="", severity="ANY", ifc_class="", text_contains=""):
        res = self.ws.result
        if res is None:
            return {"error": "Chưa chạy kiểm tra theo bộ quy tắc BEP"}
        out = [i for i in res.issues
               if (not rule_id or i.rule_id.lower() == rule_id.lower())
               and (severity in ("", "ANY") or i.severity == severity)
               and (not ifc_class or i.ifc_class.lower() == ifc_class.lower())
               and (not text_contains or norm(text_contains) in norm(i.message))]
        refs = list(dict.fromkeys((i.file, i.step_id) for i in out))
        cid = self._card("Lỗi kiểm tra: " + " ".join(x for x in [rule_id, ifc_class, text_contains] if x),
                         [list(x) for x in refs])
        return {"issue_count": len(out), "object_count": len(refs), "card": cid,
                "by_rule": Counter(i.rule_id for i in out).most_common(15),
                "by_class": Counter(i.ifc_class for i in out).most_common(10),
                "by_message": Counter(i.message[:90] for i in out).most_common(10)}

    def find_clashes(self, test_contains="", kind="ANY", text_contains=""):
        run = self.ws.clash_run
        if run is None:
            return {"error": "Chưa chạy kiểm tra xung đột"}
        out = [c for c in run.clashes
               if (not test_contains or norm(test_contains) in norm(c.test))
               and (kind in ("", "ANY") or c.kind == kind)
               and (not text_contains or norm(text_contains) in norm(c.detail))]
        refs = list(dict.fromkeys([(c.file_a, c.step_a) for c in out] + [(c.file_b, c.step_b) for c in out]))
        cid = self._card("Xung đột: " + " ".join(x for x in [test_contains, kind if kind != "ANY" else ""] if x),
                         [list(x) for x in refs], kind="clashes")
        self.cards[-1]["clashes"] = [c.id for c in out[:5000]]
        return {"count": len(out), "card": cid, "by_test": Counter(c.test for c in out).most_common(15),
                "sample": [{"id": c.id, "test": c.test, "detail": c.detail, "a": c.name_a, "b": c.name_b}
                           for c in out[:10]]}

    def run(self, name: str, args: dict):
        fn = getattr(self, name, None)
        if fn is None or name.startswith("_"):
            return {"error": f"Không có công cụ {name}"}
        try:
            return fn(**(args or {}))
        except TypeError as ex:
            return {"error": f"Tham số không hợp lệ: {ex}"}


def chat(ws, history: list[dict], question: str) -> dict:
    """history: [{role: user|assistant, text}]. Trả về {answer, cards, usage}."""
    from ifc_checker.ai import AIClient, AIError

    client = AIClient(ws.api_key, ws.ai_model, "medium")
    a = client._anthropic
    q = ModelQuery(ws)
    messages = [{"role": h["role"], "content": h["text"]} for h in history[-10:] if h.get("text")]
    messages.append({"role": "user", "content": question})
    params = dict(model=client.model, max_tokens=16000, system=SYSTEM, tools=TOOLS,
                  output_config={"effort": "medium"})
    if client.model in ("claude-opus-5-5", "claude-sonnet-5-5"):
        params["betas"] = ["server-side-fallback-2026-07-01"]
        params["fallbacks"] = "default"
    answer = ""
    for _ in range(10):
        try:
            msg = client.client.beta.messages.create(messages=messages, **params)
        except a.AuthenticationError:
            raise AIError("API key không hợp lệ.")
        except a.RateLimitError:
            raise AIError("Vượt giới hạn tần suất gọi AI, thử lại sau ít phút.")
        except a.APIStatusError as ex:
            raise AIError(f"Lỗi AI ({ex.status_code}): {ex.message}")
        except a.APIConnectionError:
            raise AIError("Không kết nối được máy chủ AI.")
        client.usage["input"] += msg.usage.input_tokens or 0
        client.usage["output"] += msg.usage.output_tokens or 0
        if msg.stop_reason == "refusal":
            raise AIError("AI từ chối xử lý câu hỏi này.")
        messages.append({"role": "assistant", "content": msg.content})
        uses = [b for b in msg.content if getattr(b, "type", "") == "tool_use"]
        if not uses:
            answer = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
            break
        results = []
        for u in uses:
            out = q.run(u.name, u.input if isinstance(u.input, dict) else {})
            results.append({"type": "tool_result", "tool_use_id": u.id,
                            "content": json.dumps(out, ensure_ascii=False, default=str)[:60000],
                            **({"is_error": True} if isinstance(out, dict) and out.get("error") else {})})
        messages.append({"role": "user", "content": results})
    else:
        answer = answer or "Câu hỏi cần quá nhiều bước truy vấn, hãy hỏi cụ thể hơn."
    cards = [c for c in q.cards if c["count"] > 0][-4:]
    return {"answer": answer, "cards": cards, "usage": dict(client.usage)}
