"""Chạy kiểm tra hàng loạt từ dòng lệnh.

Ví dụ:
  python main.py check "D:\\HoSo\\*.ifc" --rules BEP_Rules.xlsx --out BaoCao.xlsx --html BaoCao.html
  python main.py template BEP_Rules.xlsx
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

from .engine import CheckOptions, run_checks
from .model_loader import load_model
from .report import export_excel, export_html, export_json
from .rules import RuleSet, default_ruleset, load_rules, save_rules


def _print(*a):
    try:
        print(*a, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(*(str(x).encode(enc, "replace").decode(enc) for x in a), flush=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="thamtra-bim", description="Thẩm tra thông tin phi hình học IFC theo BEP")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="Kiểm tra file IFC")
    c.add_argument("ifc", nargs="+", help="File IFC hoặc mẫu *.ifc, hoặc thư mục")
    c.add_argument("--rules", help="Bộ quy tắc .xlsx/.json (mặc định: bộ mẫu)")
    c.add_argument("--ids", help="File IDS chạy kèm")
    c.add_argument("--out", help="Báo cáo Excel (.xlsx)")
    c.add_argument("--html", help="Báo cáo HTML")
    c.add_argument("--json", help="Kết quả JSON")
    c.add_argument("--parts", choices=["auto", "parents", "all"], default="auto",
                   help="Cụm đối tượng: auto (mặc định) | parents (chỉ cha) | all (tất cả)")
    c.add_argument("--no-classify", action="store_true", help="Tắt kiểm tra tên <-> lớp IFC")
    c.add_argument("--fail-under", type=float, default=None,
                   help="Trả mã lỗi 1 nếu tỷ lệ đạt dưới ngưỡng (%%) - dùng cho tự động hoá")

    t = sub.add_parser("template", help="Tạo file Excel quy tắc mẫu")
    t.add_argument("path")

    a = p.parse_args(argv)
    if a.cmd == "template":
        save_rules(default_ruleset(), a.path)
        _print(f"Đã tạo {a.path}")
        return 0

    files = []
    for pat in a.ifc:
        if os.path.isdir(pat):
            files += sorted(glob.glob(os.path.join(pat, "**", "*.ifc"), recursive=True))
        else:
            files += sorted(glob.glob(pat)) or [pat]
    if not files:
        _print("Không tìm thấy file IFC")
        return 2
    rs: RuleSet = load_rules(a.rules) if a.rules else default_ruleset()
    if a.ids:
        rs.ids_path = a.ids
    for w in rs.validate():
        _print("! " + w)

    models = []
    for f in files:
        _print(f"Đọc {f} ...")
        m = load_model(f)
        if m.error:
            _print(f"  ! {m.error}")
        else:
            _print(f"  {m.schema}, {len(m.elements)} đối tượng, {m.load_seconds:.1f}s")
        models.append(m)

    res = run_checks(models, rs, CheckOptions(part_mode=a.parts, check_classification=not a.no_classify))
    t = res.totals()
    _print("")
    for f in res.files:
        _print(f"{f.file}: kiểm tra {f.checked}, đạt {f.compliance:.1f}%, lỗi {f.errors}, cảnh báo {f.warnings}"
               + (f"  [{f.error}]" if f.error else ""))
    _print(f"TỔNG: {t['checked']} đối tượng, đạt {t['compliance']:.1f}%, {t['errors']} lỗi, {t['warnings']} cảnh báo")
    if a.out:
        export_excel(res, a.out)
        _print(f"Đã xuất {a.out}")
    if a.html:
        export_html(res, a.html)
        _print(f"Đã xuất {a.html}")
    if a.json:
        export_json(res, a.json)
        _print(f"Đã xuất {a.json}")
    if a.fail_under is not None and t["compliance"] < a.fail_under:
        return 1
    return 0
