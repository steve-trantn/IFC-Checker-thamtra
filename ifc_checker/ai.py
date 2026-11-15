"""Tích hợp AI (Claude) - tuỳ chọn.

Chỉ gửi THÔNG TIN PHI HÌNH HỌC dạng tóm tắt (tên lớp, tên loại, tên thuộc tính,
thống kê lỗi). Không gửi file IFC hay hình học.

Chức năng:
  1. suggest_classes  - đề xuất lớp IFC đúng cho các nhóm đối tượng (đặc biệt là Proxy)
  2. generate_rules   - đọc BEP (PDF/DOCX/TXT) và đề xuất bộ quy tắc kiểm tra
  3. summarize        - viết nhận xét thẩm tra từ kết quả kiểm tra
  4. ask              - hỏi đáp tự do về kết quả
"""

from __future__ import annotations

import base64
import json
import os
import re
import zipfile
from collections import Counter

from .rules import CONDITIONS, SEVERITIES, TARGETS, Rule

MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
EFFORTS = ["low", "medium", "high"]
DEFAULT_MODEL = "claude-opus-5-5"
# Model hỗ trợ cơ chế server-side fallback khi bị bộ lọc an toàn từ chối nhầm
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1", "claude-opus-5"}


class AIError(Exception):
    pass


def sdk_available() -> bool:
    try:
        import anthropic  # noqa: F401
        return True
    except Exception:
        return False


class AIClient:
    def __init__(self, api_key: str = "", model: str = DEFAULT_MODEL, effort: str = "medium"):
        if not sdk_available():
            raise AIError("Chưa cài thư viện 'anthropic'. Chạy: pip install anthropic")
        import anthropic

        self._anthropic = anthropic
        kwargs = {"max_retries": 3, "timeout": 600}
        if api_key.strip():
            kwargs["api_key"] = api_key.strip()
        try:
            self.client = anthropic.Anthropic(**kwargs)
        except Exception as ex:
            raise AIError(f"Không khởi tạo được AI: {ex}. Hãy nhập API key trong Cài đặt "
                          "hoặc đặt biến môi trường ANTHROPIC_API_KEY.") from ex
        self.model = model or DEFAULT_MODEL
        self.effort = effort if effort in EFFORTS else "medium"
        self.usage = Counter()

    # ------------------------------------------------------------ core
    def _call(self, system: str, content, schema: dict | None = None, max_tokens: int = 32000) -> str:
        a = self._anthropic
        output_config: dict = {"effort": self.effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        params = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config=output_config,
        )
        if self.model in _FALLBACK_MODELS:
            params["betas"] = ["server-side-fallback-2026-07-01"]
            params["fallbacks"] = "default"
        try:
            with self.client.beta.messages.stream(**params) as stream:
                msg = stream.get_final_message()
        except a.AuthenticationError:
            raise AIError("API key không hợp lệ. Kiểm tra lại trong Cài đặt.")
        except a.PermissionDeniedError:
            raise AIError("API key không có quyền dùng model này.")
        except a.NotFoundError:
            raise AIError(f"Không tìm thấy model '{self.model}'.")
        except a.RateLimitError:
            raise AIError("Vượt giới hạn tần suất gọi API. Thử lại sau ít phút.")
        except a.BadRequestError as ex:
            raise AIError(f"Yêu cầu không hợp lệ: {ex.message}")
        except a.APIStatusError as ex:
            raise AIError(f"Lỗi máy chủ AI ({ex.status_code}): {ex.message}")
        except a.APIConnectionError:
            raise AIError("Không kết nối được tới máy chủ AI. Kiểm tra Internet/proxy.")
        u = msg.usage
        self.usage["input"] += getattr(u, "input_tokens", 0) or 0
        self.usage["output"] += getattr(u, "output_tokens", 0) or 0
        if msg.stop_reason == "refusal":
            raise AIError("AI từ chối xử lý yêu cầu này.")
        if msg.stop_reason == "max_tokens":
            raise AIError("Kết quả AI quá dài, bị cắt. Hãy giảm số lượng dữ liệu gửi đi.")
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return text.strip()

    def _call_json(self, system, content, schema, max_tokens=32000) -> dict:
        text = self._call(system, content, schema, max_tokens)
        try:
            return json.loads(text)
        except json.JSONDecodeError as ex:
            raise AIError(f"AI trả về dữ liệu không đúng định dạng: {ex}")

    def test(self) -> str:
        return self._call("Trả lời ngắn gọn bằng tiếng Việt.", "Xin chào, hãy xác nhận kết nối trong 1 câu.",
                          max_tokens=2000)

    # ------------------------------------------------------------ 1. phân loại
    def suggest_classes(self, models, max_groups: int = 300, batch: int = 50, progress=None,
                        only_suspicious: bool = True, flagged_ids: set | None = None) -> list[dict]:
        groups = build_class_groups(models, only_suspicious, flagged_ids)[:max_groups]
        if not groups:
            return []
        schema = {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "suggested_class": {"type": "string"},
                    "predefined_type": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["Cao", "Trung bình", "Thấp"]},
                    "reason": {"type": "string"},
                },
                "required": ["key", "suggested_class", "predefined_type", "confidence", "reason"],
                "additionalProperties": False,
            }}},
            "required": ["items"],
            "additionalProperties": False,
        }
        system = (
            "Bạn là chuyên gia BIM/openBIM am hiểu chuẩn IFC (ISO 16739) gồm IFC2X3, IFC4 và IFC4X3 "
            "(hạ tầng: đường, cầu, thoát nước, đường sắt). Nhiệm vụ: với mỗi nhóm đối tượng, dựa vào tên, "
            "loại (type), ObjectType và tên thuộc tính, đề xuất LỚP IFC phù hợp nhất (là lớp con của IfcElement "
            "có tồn tại trong đúng schema đã cho) và PredefinedType (để trống nếu không chắc). "
            "Nếu lớp hiện tại đã đúng thì giữ nguyên. Tên có thể là tiếng Việt, có thể bị mất dấu do lỗi xuất. "
            "Lý do viết tiếng Việt, ngắn gọn (dưới 25 từ). Trả về đủ mọi key."
        )
        out = []
        for i in range(0, len(groups), batch):
            chunk = groups[i:i + batch]
            payload = [{k: g[k] for k in ("key", "schema", "current_class", "name", "type_name", "object_type",
                                          "predefined_type", "properties", "count")} for g in chunk]
            if progress:
                progress(i, len(groups), f"AI đang phân tích nhóm {i + 1}-{i + len(chunk)}/{len(groups)}")
            data = self._call_json(system, "Danh sách nhóm đối tượng (JSON):\n" +
                                   json.dumps(payload, ensure_ascii=False), schema, max_tokens=32000)
            by_key = {g["key"]: g for g in chunk}
            for it in data.get("items", []):
                g = by_key.get(it.get("key"))
                if not g:
                    continue
                cls = it.get("suggested_class", "").strip()
                valid = class_exists(g["schema"], cls)
                out.append({
                    "file": g["file"], "current_class": g["current_class"], "label": g["label"],
                    "count": g["count"], "suggested_class": cls + ("" if valid else " (?)"),
                    "predefined_type": it.get("predefined_type", ""), "confidence": it.get("confidence", ""),
                    "reason": it.get("reason", "") + ("" if valid else " [Lớp không có trong schema]"),
                    "changed": cls.lower() != g["current_class"].lower(),
                })
        if progress:
            progress(len(groups), len(groups), "AI phân loại xong")
        return out

    # ------------------------------------------------------------ 2. sinh quy tắc từ BEP
    def generate_rules(self, bep_path: str = "", bep_text: str = "", extra: str = "") -> list[Rule]:
        rule_props = {
            "id": {"type": "string"},
            "ifc_class": {"type": "string"},
            "predefined_type": {"type": "string"},
            "name_filter": {"type": "string"},
            "file_filter": {"type": "string"},
            "target": {"type": "string", "enum": TARGETS},
            "pset": {"type": "string"},
            "name": {"type": "string"},
            "condition": {"type": "string", "enum": CONDITIONS},
            "value": {"type": "string"},
            "severity": {"type": "string", "enum": SEVERITIES},
            "description": {"type": "string"},
            "reference": {"type": "string"},
        }
        schema = {
            "type": "object",
            "properties": {"rules": {"type": "array", "items": {
                "type": "object", "properties": rule_props,
                "required": list(rule_props), "additionalProperties": False}}},
            "required": ["rules"], "additionalProperties": False,
        }
        system = (
            "Bạn là chuyên gia thẩm tra hồ sơ BIM. Đọc tài liệu BEP/EIR/yêu cầu thông tin (LOIN/LOD) và "
            "chuyển các yêu cầu THÔNG TIN PHI HÌNH HỌC có thể kiểm tra tự động trên file IFC thành danh sách quy tắc.\n"
            "Định nghĩa trường:\n"
            "- id: mã ngắn duy nhất, vd ARC-01, STR-02, MEP-03, HT-01.\n"
            "- ifc_class: lớp IFC áp dụng, nhiều lớp phân cách ';' (gồm lớp con), '*' = mọi đối tượng.\n"
            "- predefined_type, name_filter: để trống nếu không cần (name_filter là regex trên tên, không dấu).\n"
            "- file_filter: regex trên tên file IFC khi yêu cầu chỉ áp dụng cho 1 hạng mục/mô hình (vd THOATNUOC), "
            "dựa theo quy tắc đặt tên file trong BEP; để trống nếu không cần.\n"
            "- target: PROPERTY (thuộc tính trong Pset, cần pset+name) | ATTRIBUTE (Name, Description, ObjectType, "
            "Tag, PredefinedType, LongName, TypeName) | CLASS (lớp IFC; vd NOT_IN IfcBuildingElementProxy) | "
            "CONTAINER (thuộc không gian, value có thể là IfcBuildingStorey) | MATERIAL | CLASSIFICATION.\n"
            "- pset: tên Property Set đúng như BEP quy định (vd Pset_WallCommon hoặc Pset dự án); '*' nếu BEP "
            "không chỉ rõ Pset.\n"
            "- condition: EXISTS | EQUALS | NOT_EQUALS | IN | NOT_IN | REGEX | RANGE | NUMBER. "
            "value: danh sách phân cách ';', RANGE dạng 'min..max'.\n"
            "- severity: ERROR cho yêu cầu bắt buộc, WARNING cho khuyến nghị.\n"
            "- description: mô tả yêu cầu bằng tiếng Việt; reference: mục/trang trong BEP.\n"
            "Chỉ tạo quy tắc có căn cứ trong tài liệu; không bịa thêm. Ưu tiên tên Pset/thuộc tính chuẩn IFC "
            "khi BEP mô tả chung chung."
        )
        content = []
        if bep_path:
            content.extend(document_blocks(bep_path))
        if bep_text.strip():
            content.append({"type": "text", "text": "Nội dung BEP/yêu cầu:\n" + bep_text})
        if not content:
            raise AIError("Chưa có nội dung BEP.")
        content.append({"type": "text", "text": "Hãy lập bộ quy tắc kiểm tra từ tài liệu trên." +
                        (f"\nYêu cầu thêm: {extra}" if extra.strip() else "")})
        data = self._call_json(system, content, schema, max_tokens=64000)
        rules = []
        for d in data.get("rules", []):
            r = Rule(**{k: d.get(k, "") for k in rule_props})
            r.enabled = True
            rules.append(r.normalized())
        return rules

    # ------------------------------------------------------------ 3. nhận xét
    def summarize(self, res, extra: str = "") -> str:
        ctx = result_context(res)
        system = (
            "Bạn là chuyên gia thẩm tra hồ sơ BIM tại Việt Nam. Viết NHẬN XÉT THẨM TRA bằng tiếng Việt, văn phong "
            "hành chính - kỹ thuật, dạng văn bản thuần (không dùng markdown, không dùng ký hiệu #, *). Cấu trúc: "
            "1. Đánh giá chung; 2. Các tồn tại chính (nêu số liệu cụ thể, mã quy tắc); 3. Kiến nghị chỉnh sửa cho "
            "đơn vị lập mô hình (cụ thể theo phần mềm/thiết lập xuất IFC nếu suy ra được); 4. Kết luận (đạt / "
            "chưa đạt yêu cầu, cần bổ sung). Chỉ dựa trên số liệu được cung cấp. Dài 250-500 từ."
        )
        return self._call(system, "Kết quả kiểm tra tự động (JSON):\n" + json.dumps(ctx, ensure_ascii=False) +
                          (f"\n\nGhi chú thêm của người thẩm tra: {extra}" if extra.strip() else ""),
                          max_tokens=16000)

    def ask(self, res, question: str) -> str:
        ctx = result_context(res) if res is not None else {}
        system = ("Bạn là trợ lý chuyên gia BIM/IFC hỗ trợ thẩm tra hồ sơ. Trả lời tiếng Việt, ngắn gọn, chính xác, "
                  "văn bản thuần. Dựa vào số liệu kiểm tra nếu có; nếu câu hỏi cần thông tin không có, nói rõ.")
        return self._call(system, "Số liệu kiểm tra (JSON):\n" + json.dumps(ctx, ensure_ascii=False) +
                          "\n\nCâu hỏi: " + question, max_tokens=16000)


# ---------------------------------------------------------------- helpers

_REVIT_ID = re.compile(r":\d{3,}$")


def _label(rec) -> str:
    base = rec.type_name or rec.name or rec.object_type or ""
    return _REVIT_ID.sub("", base).strip()


def build_class_groups(models, only_suspicious=True, flagged_ids=None) -> list[dict]:
    """Gom đối tượng theo (file, lớp, tên loại) để giảm dữ liệu gửi AI."""
    from .engine import select_records

    groups: dict[tuple, dict] = {}
    for m in models:
        for r in select_records(m, "auto")[0]:
            if r.kind != "element":
                continue
            suspicious = r.ifc_class in ("IfcBuildingElementProxy", "IfcCivilElement", "IfcElementAssembly") \
                or (flagged_ids and (m.file_name, r.step_id) in flagged_ids)
            if only_suspicious and not suspicious:
                continue
            label = _label(r)
            key = (m.file_name, r.ifc_class, label)
            g = groups.get(key)
            if g is None:
                props = []
                for pset, pp in r.psets.items():
                    for k, v in pp.items():
                        if len(props) >= 10:
                            break
                        sv = str(v).strip()[:40] if v not in (None, "") else ""
                        props.append(f"{pset.strip()}.{k.strip()}={sv}")
                g = groups[key] = {
                    "key": f"G{len(groups) + 1}", "file": m.file_name, "schema": m.schema,
                    "current_class": r.ifc_class, "label": label, "name": _REVIT_ID.sub("", r.name)[:80],
                    "type_name": r.type_name[:80], "object_type": r.object_type[:80],
                    "predefined_type": r.predefined_type[:40], "properties": props, "count": 0,
                }
            g["count"] += 1
    return sorted(groups.values(), key=lambda g: -g["count"])


def class_exists(schema: str, cls: str) -> bool:
    if not cls:
        return False
    try:
        import ifcopenshell.ifcopenshell_wrapper as w

        s = schema.upper()
        name = "IFC4X3_ADD2" if s.startswith("IFC4X3") else ("IFC2X3" if s.startswith("IFC2X3") else "IFC4")
        w.schema_by_name(name).declaration_by_name(cls)
        return True
    except Exception:
        return False


def result_context(res, max_rules: int = 40) -> dict:
    t = res.totals()
    rules = sorted((s for s in res.rule_stats if s.applicable), key=lambda s: -s.failed)[:max_rules]
    msgs = Counter((i.rule_id, i.message[:120]) for i in res.issues)
    proxy_names = Counter()
    for i in res.issues:
        if i.ifc_class == "IfcBuildingElementProxy" and i.rule_id in ("GEN-01", "CLS"):
            proxy_names[i.element_name[:50]] += 1
    return {
        "tong_hop": t,
        "file": [{"ten": f.file, "schema": f.schema, "phan_mem": f.application, "kiem_tra": f.checked,
                  "phan_tu_con_bo_qua": f.parts_skipped, "proxy": f.proxies, "ty_le_dat": round(f.compliance, 1),
                  "loi": f.errors, "canh_bao": f.warnings, "loi_doc_file": f.error} for f in res.files],
        "quy_tac": [{"file": s.file, "ma": s.rule_id, "yeu_cau": s.description, "muc_do": s.severity,
                     "ap_dung": s.applicable, "khong_dat": s.failed} for s in rules],
        "loi_pho_bien": [{"ma": k[0], "noi_dung": k[1], "so_luong": n} for k, n in msgs.most_common(25)],
        "doi_tuong_proxy_tieu_bieu": [{"ten": k, "so_luong": n} for k, n in proxy_names.most_common(15)],
        "ai_de_xuat_lop": [s for s in res.ai_suggestions if s.get("changed")][:20],
    }


def document_blocks(path: str) -> list[dict]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        size = os.path.getsize(path)
        if size > 30 * 1024 * 1024:
            raise AIError("File PDF lớn hơn 30MB. Hãy tách phần yêu cầu thông tin (LOIN) ra file nhỏ hơn.")
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("ascii")
        return [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data},
                 "title": os.path.basename(path)}]
    if ext == ".docx":
        return [{"type": "text", "text": f"Tài liệu {os.path.basename(path)}:\n" + docx_text(path)}]
    if ext in (".xlsx", ".xlsm"):
        return [{"type": "text", "text": f"Bảng {os.path.basename(path)}:\n" + xlsx_text(path)}]
    with open(path, encoding="utf-8", errors="replace") as f:
        return [{"type": "text", "text": f"Tài liệu {os.path.basename(path)}:\n" + f.read()}]


def docx_text(path: str) -> str:
    """Lấy văn bản (gồm bảng) từ .docx mà không cần python-docx."""
    import xml.etree.ElementTree as ET

    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    lines = []
    body = root.find(f"{ns}body")
    for el in body if body is not None else []:
        if el.tag == f"{ns}p":
            t = "".join(x.text or "" for x in el.iter(f"{ns}t"))
            if t.strip():
                lines.append(t)
        elif el.tag == f"{ns}tbl":
            for tr in el.iter(f"{ns}tr"):
                cells = ["".join(x.text or "" for x in tc.iter(f"{ns}t")).strip() for tc in tr.iter(f"{ns}tc")]
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def xlsx_text(path: str, max_rows: int = 3000) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f"## Sheet {ws.title}")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= max_rows:
                break
            if row and any(c is not None for c in row):
                out.append(" | ".join("" if c is None else str(c) for c in row))
    wb.close()
    return "\n".join(out)


def flagged_from_result(res) -> set:
    """(file, step_id) các đối tượng bị cảnh báo sai chủng loại."""
    s = set()
    for i in res.issues:
        if i.rule_id in ("CLS", "GEN-01") or i.category == "CLASS":
            s.add((i.file, i.step_id))
    return s


__all__ = ["AIClient", "AIError", "MODELS", "EFFORTS", "DEFAULT_MODEL", "sdk_available"]
