"""Chuẩn hoá chuỗi để so khớp tên Pset/thuộc tính/giá trị.

Thực tế các file IFC xuất từ nhiều phần mềm thường có tên thuộc tính
có dấu cách thừa ở cuối, viết hoa/thường lẫn lộn, có/không dấu tiếng Việt.
"""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")
_NUM = re.compile(r"[-+]?\d+(?:[.,]\d+)*")

DEFAULT_EMPTY_TOKENS = ["", "-", "--", "n/a", "na", "null", "none", "?", "tbd", "chua co", "khong co"]


def strip_accents(s: str) -> str:
    """Bỏ dấu tiếng Việt nhưng giữ nguyên hoa/thường (an toàn cho regex)."""
    s = s.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")


def norm(s) -> str:
    """Chuẩn hoá để so sánh: bỏ dấu, chữ thường, gộp khoảng trắng, cắt hai đầu."""
    if s is None:
        return ""
    s = strip_accents(str(s)).casefold()
    return _WS.sub(" ", s).strip()


def value_to_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, (list, tuple)):
        return "; ".join(value_to_str(x) for x in v)
    return str(v).strip()


def is_empty(v, empty_tokens=None) -> bool:
    if v is None:
        return True
    if isinstance(v, (bool, int, float)):
        return False
    tokens = DEFAULT_EMPTY_TOKENS if empty_tokens is None else empty_tokens
    return norm(value_to_str(v)) in {norm(t) for t in tokens}


def parse_number(v):
    """Lấy số đầu tiên trong giá trị ('136.35m3 ' -> 136.35, '1,5' -> 1.5)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM.search(str(v))
    if not m:
        return None
    t = m.group(0)
    if "," in t and "." in t:
        # 1.234,5 (kiểu VN/EU) hoặc 1,234.5 (kiểu US)
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        parts = t.split(",")
        t = t.replace(",", ".") if len(parts) == 2 else t.replace(",", "")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def split_list(s: str, allow_comma: bool = False) -> list[str]:
    """Tách danh sách 'a; b | c' thành ['a','b','c'] (bỏ phần tử rỗng).

    Danh sách giá trị chỉ tách bằng ; | xuống dòng (để '1,5' không bị tách);
    danh sách tên lớp IFC cho phép thêm dấu phẩy.
    """
    if not s:
        return []
    pat = r"[;|,\n]" if allow_comma else r"[;|\n]"
    return [x.strip() for x in re.split(pat, str(s)) if x.strip()]
