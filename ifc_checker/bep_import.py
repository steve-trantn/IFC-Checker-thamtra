"""Nhập bảng yêu cầu thông tin thuộc tính (Phụ lục LOI) từ BEP thành bộ quy tắc - không cần AI.

Hỗ trợ dạng bảng phổ biến trong BEP theo QĐ 347/348/BXD:

    Nhóm               | Thông tin           | Kiểu   | Ghi chú
    HỆ THỐNG THOÁT NƯỚC                                   <- nhóm (chữ in hoa)
    Hố ga thoát nước                                      <- hạng mục / cấu kiện
    Tham số định danh  | Tên mạng lưới       | Ký tự  |
                       | Phân loại đối tượng | Ký tự  | Omni class
    Vật liệu           | Khối lượng bê tông  | Số     |
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .rules import ClassMapping, Rule
from .textnorm import norm, strip_accents

_NUMBER_KINDS = {"so", "number", "numeric", "so thuc", "so nguyen", "real", "integer", "double"}
_STOP = ("danh muc", "ma tran", "mau bieu", "quy trinh", "phu luc")


@dataclass
class LoiItem:
    group: str
    component: str
    pset: str
    prop: str
    is_number: bool
    note: str = ""


def _lines(path: str) -> list[str]:
    from .ai import docx_text, xlsx_text

    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return docx_text(path).splitlines()
    if ext in (".xlsx", ".xlsm"):
        return xlsx_text(path).splitlines()
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def _is_header(cells: list[str]) -> bool:
    n = [norm(c) for c in cells]
    return len(n) >= 3 and n[1] in ("thong tin", "thuoc tinh", "ten thuoc tinh", "parameter", "property") \
        and n[2] in ("kieu", "kieu du lieu", "loai", "type", "data type")


def parse_loi(path: str) -> list[LoiItem]:
    lines = _lines(path)
    items: list[LoiItem] = []
    inside = False
    group = component = pset = ""
    for raw in lines:
        cells = [c.strip() for c in raw.split("|")]
        if _is_header(cells):
            inside = True
            group = component = pset = ""
            continue
        if not inside:
            continue
        nonempty = [c for c in cells if c]
        if not nonempty:
            continue
        first = norm(nonempty[0])
        if len(nonempty) == 1 and first.startswith(_STOP):
            inside = False  # hết bảng LOI
            continue
        if len(nonempty) == 1 and (len(cells) < 2 or cells[0]):
            text = nonempty[0]
            if text == text.upper() and any(ch.isalpha() for ch in text):
                group, component = text, ""
            else:
                component = text
            pset = ""
            continue
        if len(cells) < 3:
            continue
        if cells[0]:
            pset = cells[0]
        prop, kind = cells[1], cells[2]
        if not prop:
            continue
        items.append(LoiItem(group=group, component=component or pset or group, pset=pset, prop=prop,
                             is_number=norm(kind) in _NUMBER_KINDS, note=cells[3] if len(cells) > 3 else ""))
    return items


def components(items: list[LoiItem]) -> list[str]:
    seen = []
    for it in items:
        if it.component not in seen:
            seen.append(it.component)
    return seen


def _compact(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", strip_accents(s).upper())


def guess_mapping(component: str, file_names: list[str], models=None,
                  mappings: list[ClassMapping] | None = None) -> tuple[str, str]:
    """Đoán (lớp IFC, regex tên file) cho 1 hạng mục dựa vào tên file và bảng từ khoá."""
    words = [_compact(w) for w in strip_accents(component).split() if _compact(w)]
    files_c = [_compact(os.path.splitext(f)[0]) for f in file_names]
    file_filter = ""
    cands = ["".join(words)] + ["".join(words[i:i + 2]) for i in range(len(words) - 1)]
    for c in cands:
        if len(c) >= 6 and any(c in f for f in files_c):
            file_filter = c
            break
    ifc_class = "*"
    if mappings:
        text = norm(component)
        for m in mappings:
            try:
                if re.search(strip_accents(m.keyword), text, re.IGNORECASE):
                    expected = [x.strip() for x in re.split(r"[;,|]", m.expected) if x.strip()]
                    # chỉ dùng lớp gợi ý nếu lớp đó thực sự có trong các file liên quan
                    if models:
                        present = set()
                        for mdl in models:
                            if file_filter and file_filter not in _compact(mdl.file_name):
                                continue
                            present |= {r.ifc_class for r in mdl.records}
                        hit = [c for c in expected if c in present]
                        if hit:
                            ifc_class = ";".join(hit)
                    break
            except re.error:
                continue
    return ifc_class, file_filter


def build_rules(items: list[LoiItem], mapping: dict[str, tuple[str, str]], source: str = "BEP",
                prefix: str = "LOI", severity: str = "ERROR") -> list[Rule]:
    """mapping: {hạng mục: (lớp IFC, regex tên file)}. Hạng mục chưa gán (lớp '*' và không lọc file) sẽ tắt."""
    rules = []
    seen = set()
    for it in items:
        cls, ff = mapping.get(it.component, ("*", ""))
        key = (it.component, norm(it.pset), norm(it.prop))
        if key in seen:
            continue
        seen.add(key)
        mapped = bool(ff) or (cls and cls != "*")
        rules.append(Rule(
            id=f"{prefix}-{len(rules) + 1:03d}",
            enabled=mapped,
            ifc_class=cls or "*",
            target="PROPERTY",
            pset=it.pset or "*",
            name=it.prop,
            condition="NUMBER" if it.is_number else "EXISTS",
            severity=severity,
            description=f"{it.component}: {it.prop} ({'kiểu số' if it.is_number else 'ký tự'})",
            reference=f"{source} - LOI {it.group.title() if it.group else ''}".strip(),
            file_filter=ff,
        ).normalized())
    return rules
