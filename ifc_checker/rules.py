"""Bộ quy tắc kiểm tra (chuyển hoá từ yêu cầu thông tin trong BEP/EIR).

Lưu trữ dạng Excel (dễ cho người làm BIM chỉnh sửa) hoặc JSON.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields

from .textnorm import norm

TARGETS = ["PROPERTY", "ATTRIBUTE", "CLASS", "CONTAINER", "MATERIAL", "CLASSIFICATION"]
CONDITIONS = ["EXISTS", "EQUALS", "NOT_EQUALS", "IN", "NOT_IN", "REGEX", "RANGE", "NUMBER"]
SEVERITIES = ["ERROR", "WARNING", "INFO"]

TARGET_HELP = {
    "PROPERTY": "Thuộc tính trong Property Set / Quantity Set (cần Pset + Tên)",
    "ATTRIBUTE": "Thuộc tính gốc IFC: Name, Description, ObjectType, Tag, PredefinedType, LongName, TypeName",
    "CLASS": "Chủng loại IFC của đối tượng (vd NOT_IN IfcBuildingElementProxy)",
    "CONTAINER": "Đối tượng thuộc cấu trúc không gian (Storey/Site/Road...)",
    "MATERIAL": "Vật liệu được gán cho đối tượng",
    "CLASSIFICATION": "Mã phân loại (Uniclass/OmniClass/mã dự án...)",
}
CONDITION_HELP = {
    "EXISTS": "Có và không để trống",
    "EQUALS": "Bằng giá trị (không phân biệt hoa/thường, dấu)",
    "NOT_EQUALS": "Khác giá trị",
    "IN": "Thuộc danh sách (phân cách bằng ;)",
    "NOT_IN": "Không thuộc danh sách",
    "REGEX": "Khớp biểu thức chính quy, vd ^[A-Z]{2}-\\d{2}$",
    "RANGE": "Số trong khoảng min..max (vd 0..100, 0.., ..50)",
    "NUMBER": "Là giá trị số",
}

# Tiêu đề cột Excel (tiếng Việt) <-> tên trường
COLUMNS = [
    ("id", "Mã QT"),
    ("enabled", "Áp dụng"),
    ("ifc_class", "Lớp IFC áp dụng"),
    ("predefined_type", "PredefinedType"),
    ("name_filter", "Lọc theo tên (regex)"),
    ("file_filter", "Lọc theo tên file (regex)"),
    ("target", "Đối tượng KT"),
    ("pset", "Property Set"),
    ("name", "Tên thuộc tính"),
    ("condition", "Điều kiện"),
    ("value", "Giá trị yêu cầu"),
    ("severity", "Mức độ"),
    ("description", "Mô tả yêu cầu"),
    ("reference", "Tham chiếu BEP"),
]
MAP_COLUMNS = [
    ("keyword", "Từ khoá trong tên (regex, không dấu)"),
    ("expected", "Lớp IFC đúng (phân cách ;)"),
    ("note", "Ghi chú"),
]


@dataclass
class Rule:
    id: str = ""
    enabled: bool = True
    ifc_class: str = "*"
    predefined_type: str = ""
    name_filter: str = ""
    target: str = "PROPERTY"
    pset: str = ""
    name: str = ""
    condition: str = "EXISTS"
    value: str = ""
    severity: str = "ERROR"
    description: str = ""
    reference: str = ""
    file_filter: str = ""   # regex trên tên file IFC, vd THOATNUOC(MUA|THAI)

    def normalized(self) -> "Rule":
        self.id = str(self.id or "").strip()
        self.ifc_class = str(self.ifc_class or "*").strip() or "*"
        self.target = str(self.target or "PROPERTY").strip().upper()
        self.condition = str(self.condition or "EXISTS").strip().upper().replace(" ", "_")
        self.severity = str(self.severity or "ERROR").strip().upper()
        if self.severity in ("LOI", "LỖI"):
            self.severity = "ERROR"
        elif self.severity in ("CANH BAO", "CẢNH BÁO"):
            self.severity = "WARNING"
        if self.severity not in SEVERITIES:
            self.severity = "ERROR"
        for f_ in ("predefined_type", "name_filter", "file_filter", "pset", "name", "value", "description", "reference"):
            v = getattr(self, f_)
            setattr(self, f_, "" if v is None else str(v).strip())
        if isinstance(self.enabled, str):
            # Excel: 'x' = bật, để trống = tắt
            self.enabled = norm(self.enabled) not in ("", "0", "false", "no", "khong", "off", "n")
        elif self.enabled is None:
            self.enabled = False
        else:
            self.enabled = bool(self.enabled)
        return self

    def summary(self) -> str:
        if self.description:
            return self.description
        what = {
            "PROPERTY": f"{self.pset or '*'}.{self.name}",
            "ATTRIBUTE": self.name,
            "CLASS": "Lớp IFC",
            "CONTAINER": "Cấu trúc không gian",
            "MATERIAL": "Vật liệu",
            "CLASSIFICATION": "Mã phân loại",
        }.get(self.target, self.name)
        val = f" {self.value}" if self.value else ""
        return f"[{self.ifc_class}] {what} {self.condition}{val}"


@dataclass
class ClassMapping:
    keyword: str = ""
    expected: str = ""
    note: str = ""


@dataclass
class RuleSet:
    rules: list[Rule] = field(default_factory=list)
    mappings: list[ClassMapping] = field(default_factory=list)
    source: str = ""
    ids_path: str = ""  # file IDS (buildingSMART) chạy kèm, nếu có

    def validate(self) -> list[str]:
        """Trả về danh sách cảnh báo cấu hình."""
        import re

        msgs = []
        seen = set()
        for r in self.rules:
            if not r.id:
                msgs.append("Có quy tắc chưa có Mã QT")
            elif r.id in seen:
                msgs.append(f"Trùng Mã QT: {r.id}")
            seen.add(r.id)
            if r.target not in TARGETS:
                msgs.append(f"{r.id}: Đối tượng KT không hợp lệ '{r.target}'")
            if r.condition not in CONDITIONS:
                msgs.append(f"{r.id}: Điều kiện không hợp lệ '{r.condition}'")
            if r.target in ("PROPERTY", "ATTRIBUTE") and not r.name:
                msgs.append(f"{r.id}: thiếu Tên thuộc tính")
            if r.condition in ("EQUALS", "NOT_EQUALS", "IN", "NOT_IN", "REGEX", "RANGE") and not r.value \
                    and r.target != "CONTAINER":
                msgs.append(f"{r.id}: điều kiện {r.condition} cần 'Giá trị yêu cầu'")
            for pat in ([r.value] if r.condition == "REGEX" else []) + [x for x in (r.name_filter, r.file_filter) if x]:
                try:
                    re.compile(pat)
                except re.error as ex:
                    msgs.append(f"{r.id}: regex lỗi '{pat}': {ex}")
        return msgs


# ---------------------------------------------------------------- Mặc định

def default_mappings() -> list[ClassMapping]:
    """Từ khoá (tiếng Việt không dấu + tiếng Anh) -> lớp IFC đúng.
    Thứ tự quan trọng: từ khoá cụ thể đặt trước từ khoá chung."""
    m = [
        (r"\b(cot thep|thep chu|thep dai|rebar)\b", "IfcReinforcingBar;IfcReinforcingElement", "Cốt thép"),
        (r"\b(cua thu|cua xa|ho thu|gieng thu|inlet|outlet)\b", "IfcDistributionChamberElement;IfcFlowTerminal;IfcDistributionFlowElement", "Cửa thu/xả thoát nước"),
        (r"\b(ho ga|ga thu|gieng tham|manhole|chamber)\b", "IfcDistributionChamberElement", "Hố ga"),
        (r"\b(cong hop|cong tron|ong cong|culvert)\b", "IfcPipeSegment;IfcFlowSegment;IfcCivilElement", "Cống"),
        (r"\b(ong gio|duct)\b", "IfcDuctSegment;IfcFlowSegment", "Ống gió"),
        (r"\b(ong|pipe)\b", "IfcPipeSegment;IfcFlowSegment;IfcPipeFitting;IfcFlowFitting", "Ống"),
        (r"\b(bo via|kerb|curb)\b", "IfcKerb;IfcCivilElement", "Bó vỉa"),
        (r"\b(mat duong|ao duong|lop mat|cap phoi|be tong nhua|btn|pavement|course)\b", "IfcCourse;IfcPavement;IfcCivilElement", "Kết cấu áo đường"),
        (r"\b(san nen|dap nen|dao dap|earthwork)\b", "IfcEarthworksFill;IfcEarthworksCut;IfcGeographicElement", "Đất đắp/đào"),
        (r"\b(bien bao|sign)\b", "IfcSign", "Biển báo"),
        (r"\b(vach son|marking)\b", "IfcSurfaceFeature;IfcAnnotation", "Vạch sơn"),
        (r"\b(lan can|ho lan|tay vin|railing|guardrail)\b", "IfcRailing", "Lan can"),
        (r"\b(cau thang|stair)\b", "IfcStair;IfcStairFlight", "Cầu thang"),
        (r"\b(ram doc|ramp)\b", "IfcRamp;IfcRampFlight", "Ram dốc"),
        (r"\b(coc|pile)\b", "IfcPile;IfcDeepFoundation", "Cọc"),
        (r"\b(mong|dai mong|footing|foundation)\b", "IfcFooting", "Móng"),
        (r"\b(cua so|window)\b", "IfcWindow", "Cửa sổ"),
        (r"\b(cua di|cua|door)\b", "IfcDoor", "Cửa đi"),
        (r"\b(tuong chan|ke chan|retaining)\b", "IfcWall", "Tường chắn"),
        (r"\b(tuong|wall)\b", "IfcWall;IfcCurtainWall", "Tường"),
        (r"\b(cot|column)\b", "IfcColumn", "Cột"),
        (r"\b(dam|beam|girder)\b", "IfcBeam", "Dầm"),
        (r"\b(san|slab|floor)\b", "IfcSlab", "Sàn"),
        (r"\b(mai|roof)\b", "IfcRoof;IfcSlab", "Mái"),
        (r"\b(tran|ceiling)\b", "IfcCovering", "Trần"),
        (r"\b(den|light|lamp)\b", "IfcLightFixture;IfcFlowTerminal", "Đèn"),
        (r"\b(may bom|pump)\b", "IfcPump;IfcFlowMovingDevice", "Bơm"),
        (r"\b(van|valve)\b", "IfcValve;IfcFlowController", "Van"),
        (r"\b(cay xanh|tree)\b", "IfcGeographicElement", "Cây xanh"),
    ]
    return [ClassMapping(k, e, n) for k, e, n in m]


def default_rules() -> list[Rule]:
    R = Rule
    return [
        R("GEN-01", True, "*", "", "", "CLASS", "", "", "NOT_IN", "IfcBuildingElementProxy", "WARNING",
          "Hạn chế dùng IfcBuildingElementProxy - phải xuất đúng chủng loại IFC", "BEP mục Xuất IFC"),
        R("GEN-02", True, "*", "", "", "ATTRIBUTE", "", "Name", "EXISTS", "", "ERROR",
          "Đối tượng phải có Tên (Name)", "BEP - LOIN"),
        R("GEN-03", True, "*", "", "", "CONTAINER", "", "", "EXISTS", "", "ERROR",
          "Đối tượng phải thuộc cấu trúc không gian (Site/Building/Storey/Road...)", "BEP - Cấu trúc mô hình"),
        R("GEN-04", False, "*", "", "", "CLASSIFICATION", "", "", "EXISTS", "", "WARNING",
          "Đối tượng nên có mã phân loại (Uniclass/OmniClass/mã dự án)", "BEP - Phân loại"),
        R("ARC-01", True, "IfcWall", "", "", "PROPERTY", "Pset_WallCommon", "IsExternal", "EXISTS", "", "ERROR",
          "Tường phải khai báo trong/ngoài nhà", "BEP - LOIN Kiến trúc"),
        R("ARC-02", True, "IfcWall", "", "", "PROPERTY", "Pset_WallCommon", "LoadBearing", "EXISTS", "", "ERROR",
          "Tường phải khai báo chịu lực hay không", "BEP - LOIN Kết cấu"),
        R("ARC-03", True, "IfcWall;IfcDoor", "", "", "PROPERTY", "*", "FireRating", "EXISTS", "", "WARNING",
          "Tường/cửa cần khai báo giới hạn chịu lửa", "QCVN 06"),
        R("ARC-04", True, "IfcSpace", "", "", "ATTRIBUTE", "", "LongName", "EXISTS", "", "WARNING",
          "Phòng phải có tên đầy đủ (LongName)", "BEP - Không gian"),
        R("STR-01", True, "IfcColumn;IfcBeam;IfcSlab;IfcFooting", "", "", "MATERIAL", "", "", "EXISTS", "", "ERROR",
          "Cấu kiện kết cấu phải được gán vật liệu", "BEP - LOIN Kết cấu"),
        R("STR-02", True, "IfcSlab", "", "", "PROPERTY", "Pset_SlabCommon", "LoadBearing", "EXISTS", "", "WARNING",
          "Sàn phải khai báo chịu lực", "BEP - LOIN Kết cấu"),
        # Ví dụ cho mô hình hạ tầng dùng Pset tự định nghĩa tiếng Việt
        R("HT-01", True, "IfcBuildingElementProxy;IfcCivilElement;IfcDistributionElement", "", "", "PROPERTY",
          "Tham số định danh", "Phân loại đối tượng", "REGEX", r"^[A-Z]{2}-\d{2}\.\d{2}\.\d{2}$", "ERROR",
          "Mã phân loại đối tượng đúng định dạng XX-00.00.00", "BEP - Bảng mã phân loại"),
        R("HT-02", True, "IfcBuildingElementProxy;IfcCivilElement;IfcDistributionElement", "", "", "PROPERTY",
          "Tham số định danh", "Tên tuyến", "EXISTS", "", "ERROR",
          "Phải có tên tuyến", "BEP - LOIN Hạ tầng"),
        R("HT-03", True, "IfcBuildingElementProxy;IfcCivilElement;IfcDistributionElement", "", "", "PROPERTY",
          "Vật liệu", "Mác bê tông", "IN", "M150;M200;M250;M300;M350;M400", "ERROR",
          "Mác bê tông thuộc danh mục cho phép", "BEP - Vật liệu"),
        R("HT-04", True, "IfcBuildingElementProxy;IfcCivilElement;IfcDistributionElement", "", "", "PROPERTY",
          "Vật liệu", "Khối lượng bê tông (m3)", "RANGE", "0..", "WARNING",
          "Khối lượng bê tông phải là số dương", "BEP - Khối lượng"),
    ]


def default_ruleset() -> RuleSet:
    return RuleSet(rules=default_rules(), mappings=default_mappings(), source="(mặc định)")


# ---------------------------------------------------------------- Đọc / ghi

def _header_key(h: str, columns) -> str | None:
    n = norm(h)
    for key, label in columns:
        if n in (norm(key), norm(label)):
            return key
    aliases = {
        "rule id": "id", "ma": "id", "id": "id", "class": "ifc_class", "ifc class": "ifc_class",
        "entity": "ifc_class", "lop ifc": "ifc_class", "target": "target", "property set": "pset",
        "pset": "pset", "property": "name", "thuoc tinh": "name", "condition": "condition",
        "value": "value", "gia tri": "value", "severity": "severity", "muc do": "severity",
        "description": "description", "mo ta": "description", "reference": "reference",
        "enabled": "enabled", "name filter": "name_filter", "predefined type": "predefined_type",
        "keyword": "keyword", "expected": "expected", "note": "note",
    }
    return aliases.get(n)


def load_rules(path: str) -> RuleSet:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        names = {f_.name for f_ in fields(Rule)}
        rules = [Rule(**{k: v for k, v in r.items() if k in names}).normalized() for r in data.get("rules", [])]
        maps = [ClassMapping(**m) for m in data.get("mappings", [])]
        rs = RuleSet(rules, maps or default_mappings(), source=path, ids_path=data.get("ids_path", ""))
        return rs
    if ext in (".xlsx", ".xlsm"):
        return _load_excel(path)
    if ext == ".ids":
        rs = RuleSet(rules=[], mappings=default_mappings(), source=path, ids_path=path)
        return rs
    raise ValueError(f"Không hỗ trợ định dạng {ext}. Dùng .xlsx, .json hoặc .ids")


def _read_sheet(ws, columns):
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    # tìm dòng tiêu đề trong 5 dòng đầu
    hdr_idx, keys = 0, []
    for i, row in enumerate(rows[:5]):
        ks = [_header_key(str(c), columns) if c is not None else None for c in row]
        if sum(1 for k in ks if k) >= 2:
            hdr_idx, keys = i, ks
            break
    out = []
    for row in rows[hdr_idx + 1:]:
        if row is None or all(c is None or str(c).strip() == "" for c in row):
            continue
        d = {}
        for k, c in zip(keys, row):
            if k:
                d[k] = c
        out.append(d)
    return out


def _load_excel(path: str) -> RuleSet:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheet_rules = None
    sheet_map = None
    for ws in wb.worksheets:
        n = norm(ws.title)
        if n in ("rules", "quy tac", "quy tac bep") and sheet_rules is None:
            sheet_rules = ws
        elif n in ("classmapping", "class mapping", "phan loai", "tu khoa phan loai"):
            sheet_map = ws
    if sheet_rules is None:
        sheet_rules = wb.worksheets[0]
    rules = []
    for d in _read_sheet(sheet_rules, COLUMNS):
        r = Rule(**{k: v for k, v in d.items() if k in {f_.name for f_ in fields(Rule)}})
        r.normalized()
        if not r.id and not r.name and r.target not in ("CLASS", "CONTAINER", "MATERIAL", "CLASSIFICATION"):
            continue
        if not r.id:
            r.id = f"R{len(rules) + 1:03d}"
        rules.append(r)
    maps = []
    if sheet_map is not None:
        for d in _read_sheet(sheet_map, MAP_COLUMNS):
            if d.get("keyword") and d.get("expected"):
                maps.append(ClassMapping(str(d["keyword"]).strip(), str(d["expected"]).strip(),
                                         str(d.get("note") or "").strip()))
    wb.close()
    return RuleSet(rules, maps if sheet_map is not None else default_mappings(), source=path)


def save_rules(rs: RuleSet, path: str) -> None:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"rules": [asdict(r) for r in rs.rules],
                       "mappings": [asdict(m) for m in rs.mappings],
                       "ids_path": rs.ids_path}, f, ensure_ascii=False, indent=2)
        return
    _save_excel(rs, path)


def _save_excel(rs: RuleSet, path: str) -> None:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Rules"
    hdr_fill = PatternFill("solid", fgColor="1F4E78")
    hdr_font = Font(bold=True, color="FFFFFF")
    ws.append([label for _, label in COLUMNS])
    for r in rs.rules:
        ws.append([("x" if r.enabled else "") if k == "enabled" else getattr(r, k) for k, _ in COLUMNS])
    widths = [9, 8, 30, 15, 20, 24, 15, 24, 26, 13, 30, 10, 50, 22]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    for c in ws[1]:
        c.fill, c.font = hdr_fill, hdr_font
        c.alignment = Alignment(wrap_text=True, vertical="center")
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions
    n = max(len(rs.rules) + 200, 500)
    for col, opts in (("G", TARGETS), ("J", CONDITIONS), ("L", SEVERITIES)):
        dv = DataValidation(type="list", formula1='"' + ",".join(opts) + '"', allow_blank=True)
        dv.add(f"{col}2:{col}{n}")
        ws.add_data_validation(dv)

    wm = wb.create_sheet("ClassMapping")
    wm.append([label for _, label in MAP_COLUMNS])
    for m in rs.mappings:
        wm.append([m.keyword, m.expected, m.note])
    for c in wm[1]:
        c.fill, c.font = hdr_fill, hdr_font
    wm.column_dimensions["A"].width = 60
    wm.column_dimensions["B"].width = 60
    wm.column_dimensions["C"].width = 25

    wh = wb.create_sheet("Hướng dẫn")
    wh.column_dimensions["A"].width = 22
    wh.column_dimensions["B"].width = 100
    guide = [
        ("HƯỚNG DẪN LẬP BỘ QUY TẮC TỪ BEP", ""),
        ("", ""),
        ("Mã QT", "Mã duy nhất của quy tắc, vd ARC-01"),
        ("Áp dụng", "Đánh 'x' để bật quy tắc, để trống để tắt"),
        ("Lớp IFC áp dụng", "Danh sách lớp IFC, phân cách ; (gồm cả lớp con). '*' = mọi đối tượng. "
                            "Thêm '!' để loại trừ, vd: *;!IfcOpeningElement"),
        ("PredefinedType", "(Tuỳ chọn) chỉ áp dụng cho PredefinedType này, vd SHEAR;PARTITIONING"),
        ("Lọc theo tên (regex)", "(Tuỳ chọn) chỉ áp dụng khi Name/ObjectType/Type khớp regex (không phân biệt dấu)"),
        ("Lọc theo tên file (regex)", "(Tuỳ chọn) chỉ áp dụng cho file IFC có tên khớp, vd THOATNUOC(MUA|THAI). "
                                      "Hữu ích khi tên file theo quy tắc đặt tên của BEP (mỗi file = 1 hạng mục)"),
        ("Đối tượng KT", "; ".join(f"{k}: {v}" for k, v in TARGET_HELP.items())),
        ("Property Set", "Tên Pset/Qto (không phân biệt hoa thường, dấu, khoảng trắng thừa). '*' = tìm trong mọi Pset. "
                         "Cho phép ký tự đại diện: Pset_*Common"),
        ("Tên thuộc tính", "Tên thuộc tính trong Pset, hoặc tên thuộc tính gốc khi Đối tượng KT = ATTRIBUTE"),
        ("Điều kiện", "; ".join(f"{k}: {v}" for k, v in CONDITION_HELP.items())),
        ("Giá trị yêu cầu", "Giá trị dùng cho điều kiện. Danh sách phân cách bằng ;"),
        ("Mức độ", "ERROR (bắt buộc) / WARNING (khuyến nghị) / INFO (thông tin)"),
        ("Sheet ClassMapping", "Từ khoá trong tên đối tượng -> lớp IFC đúng. Dùng để phát hiện xuất sai chủng loại "
                               "(vd tên có 'tường' nhưng lớp là IfcBeam). Từ khoá viết không dấu, chữ thường, "
                               "dạng regex; dòng trên được ưu tiên."),
        ("File IDS", "Có thể chạy kèm file .ids (chuẩn buildingSMART Information Delivery Specification)."),
    ]
    for a, b in guide:
        wh.append([a, b])
    wh["A1"].font = Font(bold=True, size=14)
    for row in wh.iter_rows(min_row=3):
        row[0].font = Font(bold=True)
        row[1].alignment = Alignment(wrap_text=True)
    wb.save(path)
