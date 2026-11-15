"""Xuất báo cáo kết quả thẩm tra: Excel (chi tiết) và HTML (gửi/đọc nhanh)."""

from __future__ import annotations

import html
import json
from datetime import datetime

from . import APP_NAME, AUTHOR, AUTHOR_PHONE, CREDIT, __version__
from .engine import CheckResult

SEV_VN = {"ERROR": "Lỗi", "WARNING": "Cảnh báo", "INFO": "Thông tin"}


def export_excel(res: CheckResult, path: str) -> None:
    import openpyxl
    from openpyxl.chart import BarChart, Reference
    from openpyxl.formatting.rule import CellIsRule, DataBarRule
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    hdr_fill = PatternFill("solid", fgColor="1F4E78")
    hdr_font = Font(bold=True, color="FFFFFF")
    red = PatternFill("solid", fgColor="F8CBAD")
    yellow = PatternFill("solid", fgColor="FFE699")
    green = PatternFill("solid", fgColor="C6EFCE")

    def table(ws, headers, rows, widths, start_row=1):
        for j, h in enumerate(headers, start=1):
            c = ws.cell(row=start_row, column=j, value=h)
            c.fill, c.font = hdr_fill, hdr_font
            c.alignment = Alignment(wrap_text=True, vertical="center")
        for i, row in enumerate(rows, start=start_row + 1):
            for j, v in enumerate(row, start=1):
                ws.cell(row=i, column=j, value=v)
        for j, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(j)].width = w
        last = start_row + max(len(rows), 1)
        ws.auto_filter.ref = f"A{start_row}:{get_column_letter(len(headers))}{last}"
        ws.freeze_panes = ws.cell(row=start_row + 1, column=1)
        return last

    wb = openpyxl.Workbook()

    # ---- Tổng quan
    ws = wb.active
    ws.title = "Tổng quan"
    t = res.totals()
    ws["A1"] = "BÁO CÁO THẨM TRA THÔNG TIN MÔ HÌNH IFC"
    ws["A1"].font = Font(bold=True, size=16, color="1F4E78")
    meta = [
        ("Thời điểm kiểm tra", res.started),
        ("Bộ quy tắc", res.ruleset_source),
        ("Số file", t["files"]),
        ("Số đối tượng kiểm tra", t["checked"]),
        ("Đối tượng đạt (không có lỗi bắt buộc)", t["passed"]),
        ("Tỷ lệ đạt", f"{t['compliance']:.1f}%"),
        ("Tổng số lỗi (ERROR)", t["errors"]),
        ("Tổng số cảnh báo (WARNING)", t["warnings"]),
        ("Số đối tượng IfcBuildingElementProxy", t["proxies"]),
        ("Phần mềm", f"{APP_NAME} {__version__}"),
        ("Người phát triển", f"{AUTHOR} - ĐT: {AUTHOR_PHONE}"),
    ]
    for i, (k, v) in enumerate(meta, start=3):
        ws.cell(row=i, column=1, value=k).font = Font(bold=True)
        ws.cell(row=i, column=2, value=v)
    r0 = 3 + len(meta) + 1
    headers = ["File", "Schema", "Dự án", "Phần mềm xuất", "Tổng đối tượng", "Đã kiểm tra",
               "Phần tử con bỏ qua", "Proxy", "Đạt", "Tỷ lệ đạt (%)", "Lỗi", "Cảnh báo", "Ghi chú"]
    rows = [(f.file, f.schema, f.project, f.application, f.total_elements, f.checked, f.parts_skipped,
             f.proxies, f.passed_elements, round(f.compliance, 1), f.errors, f.warnings, f.error)
            for f in res.files]
    last = table(ws, headers, rows, [42, 13, 22, 26, 10, 10, 10, 8, 8, 10, 8, 9, 30], start_row=r0)
    ws.freeze_panes = None
    ws.column_dimensions["A"].width = 42
    if rows:
        rng = f"J{r0 + 1}:J{last}"
        ws.conditional_formatting.add(rng, CellIsRule(operator="lessThan", formula=["50"], fill=red))
        ws.conditional_formatting.add(rng, CellIsRule(operator="between", formula=["50", "89.99"], fill=yellow))
        ws.conditional_formatting.add(rng, CellIsRule(operator="greaterThanOrEqual", formula=["90"], fill=green))
    if res.ai_summary:
        rr = last + 2
        ws.cell(row=rr, column=1, value="NHẬN XÉT (AI hỗ trợ - cần chuyên gia xác nhận)").font = Font(bold=True)
        c = ws.cell(row=rr + 1, column=1, value=res.ai_summary)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=rr + 1, start_column=1, end_row=rr + 1, end_column=10)
        ws.row_dimensions[rr + 1].height = min(409, 15 * (res.ai_summary.count("\n") + 4 + len(res.ai_summary) // 150))

    # ---- Theo quy tắc
    ws = wb.create_sheet("Theo quy tắc")
    headers = ["File", "Mã QT", "Yêu cầu", "Mức độ", "Số áp dụng", "Đạt", "Không đạt", "Tỷ lệ đạt (%)"]
    rows = [(s.file, s.rule_id, s.description, SEV_VN.get(s.severity, s.severity), s.applicable, s.passed,
             s.failed, round(s.rate, 1) if s.applicable else None) for s in res.rule_stats]
    last = table(ws, headers, rows, [40, 10, 60, 10, 11, 9, 11, 12])
    if rows:
        ws.conditional_formatting.add(f"H2:H{last}", DataBarRule(start_type="num", start_value=0, end_type="num",
                                                                 end_value=100, color="63BE7B"))
        ws.conditional_formatting.add(f"G2:G{last}", CellIsRule(operator="greaterThan", formula=["0"], fill=red))

    # ---- Chi tiết lỗi
    ws = wb.create_sheet("Chi tiết lỗi")
    headers = ["File", "Mức độ", "Mã QT", "Nhóm", "Lớp IFC", "Tên đối tượng", "GlobalId", "#STEP",
               "Nội dung", "Pset", "Thuộc tính", "Giá trị hiện tại", "Yêu cầu", "Tham chiếu BEP"]
    rows = [(i.file, SEV_VN.get(i.severity, i.severity), i.rule_id, i.category, i.ifc_class, i.element_name,
             i.guid, i.step_id, i.message, i.pset, i.prop, i.actual, i.expected, i.reference) for i in res.issues]
    if len(rows) > 1_000_000:
        rows = rows[:1_000_000]
    last = table(ws, headers, rows, [30, 10, 10, 12, 24, 34, 24, 8, 55, 22, 24, 22, 26, 22])
    if rows:
        ws.conditional_formatting.add(f"B2:B{last}", CellIsRule(operator="equal", formula=['"Lỗi"'], fill=red))
        ws.conditional_formatting.add(f"B2:B{last}", CellIsRule(operator="equal", formula=['"Cảnh báo"'], fill=yellow))

    # ---- Thống kê đối tượng
    ws = wb.create_sheet("Thống kê đối tượng")
    headers = ["File", "Lớp IFC", "PredefinedType/Loại", "Số đối tượng", "Số phần tử con"]
    rows = list(res.class_counts)
    table(ws, headers, rows, [40, 30, 40, 13, 14])
    # biểu đồ số đối tượng theo lớp (tổng hợp)
    agg = {}
    for _, cls, _, n, _p in res.class_counts:
        agg[cls] = agg.get(cls, 0) + n
    if agg:
        wc = wb.create_sheet("Biểu đồ lớp IFC")
        wc.append(["Lớp IFC", "Số đối tượng"])
        for cls, n in sorted(agg.items(), key=lambda x: -x[1])[:25]:
            wc.append([cls, n])
        wc.column_dimensions["A"].width = 32
        ch = BarChart()
        ch.type = "bar"
        ch.title = "Số đối tượng theo lớp IFC"
        ch.y_axis.title = "Số lượng"
        data = Reference(wc, min_col=2, min_row=1, max_row=wc.max_row)
        cats = Reference(wc, min_col=1, min_row=2, max_row=wc.max_row)
        ch.add_data(data, titles_from_data=True)
        ch.set_categories(cats)
        ch.height, ch.width = 12, 22
        ch.legend = None
        wc.add_chart(ch, "D2")

    # ---- Thuộc tính
    ws = wb.create_sheet("Thống kê thuộc tính")
    headers = ["File", "Lớp IFC", "Property Set", "Thuộc tính", "Số ĐT có thuộc tính", "Số ĐT đã điền",
               "Tổng ĐT của lớp", "Tỷ lệ điền (%)", "Giá trị mẫu"]
    rows = [(a, b, c, d, e, f, g, round(100.0 * f / g, 1) if g else None, h) for a, b, c, d, e, f, g, h in res.inventory]
    last = table(ws, headers, rows, [36, 26, 28, 30, 12, 12, 12, 12, 60])
    if rows:
        ws.conditional_formatting.add(f"H2:H{last}", DataBarRule(start_type="num", start_value=0, end_type="num",
                                                                 end_value=100, color="5B9BD5"))

    # ---- AI phân loại
    if res.ai_suggestions:
        ws = wb.create_sheet("AI đề xuất lớp IFC")
        headers = ["File", "Lớp hiện tại", "Tên / Loại", "Số đối tượng", "Lớp IFC đề xuất", "PredefinedType",
                   "Độ tin cậy", "Lý do"]
        rows = [(s.get("file", ""), s.get("current_class", ""), s.get("label", ""), s.get("count", 0),
                 s.get("suggested_class", ""), s.get("predefined_type", ""), s.get("confidence", ""),
                 s.get("reason", "")) for s in res.ai_suggestions]
        table(ws, headers, rows, [34, 26, 46, 10, 28, 18, 10, 60])

    wb.save(path)


def export_json(res: CheckResult, path: str) -> None:
    from dataclasses import asdict

    data = {
        "started": res.started, "ruleset": res.ruleset_source, "totals": res.totals(),
        "files": [asdict(f) | {"compliance": f.compliance} for f in res.files],
        "rule_stats": [asdict(s) | {"failed": s.failed} for s in res.rule_stats],
        "issues": [asdict(i) for i in res.issues],
        "ai_suggestions": res.ai_suggestions, "ai_summary": res.ai_summary,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def export_html(res: CheckResult, path: str, max_issues: int = 20000) -> None:
    e = html.escape
    t = res.totals()

    def color(p):
        return "#c0392b" if p < 50 else ("#b7791f" if p < 90 else "#2f855a")

    files_rows = "".join(
        f"<tr><td>{e(f.file)}</td><td>{e(f.schema)}</td><td class=n>{f.checked}</td><td class=n>{f.parts_skipped}</td>"
        f"<td class=n>{f.proxies}</td><td class=n style='color:{color(f.compliance)};font-weight:600'>"
        f"{f.compliance:.1f}%</td><td class=n>{f.errors}</td><td class=n>{f.warnings}</td><td>{e(f.error)}</td></tr>"
        for f in res.files)
    rule_rows = "".join(
        f"<tr><td>{e(s.file)}</td><td>{e(s.rule_id)}</td><td>{e(s.description)}</td><td>{SEV_VN.get(s.severity, '')}</td>"
        f"<td class=n>{s.applicable}</td><td class=n>{s.failed}</td>"
        f"<td><div class=bar><span style='width:{s.rate:.0f}%;background:{color(s.rate)}'></span></div>"
        f"<small>{s.rate:.0f}%</small></td></tr>"
        for s in res.rule_stats if s.applicable)
    issues = res.issues[:max_issues]
    issue_rows = "".join(
        f"<tr class='{i.severity}'><td>{e(i.file)}</td><td>{SEV_VN.get(i.severity, '')}</td><td>{e(i.rule_id)}</td>"
        f"<td>{e(i.ifc_class)}</td><td>{e(i.element_name)}</td><td><code>{e(i.guid)}</code></td>"
        f"<td>{e(i.message)}</td><td>{e(i.actual)}</td></tr>"
        for i in issues)
    more = (f"<p class=muted>Hiển thị {max_issues} / {len(res.issues)} lỗi. Xem đầy đủ trong file Excel.</p>"
            if len(res.issues) > max_issues else "")
    ai = (f"<section><h2>Nhận xét (AI hỗ trợ — cần chuyên gia xác nhận)</h2><div class=ai>{e(res.ai_summary)}</div></section>"
          if res.ai_summary else "")
    doc = f"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Báo cáo thẩm tra IFC</title>
<style>
:root{{--bg:#f7f8fa;--fg:#1a202c;--card:#fff;--muted:#64748b;--line:#e2e8f0;--accent:#1f4e78}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111418;--fg:#e2e8f0;--card:#1a1f26;--muted:#94a3b8;--line:#2d3748;--accent:#7fb3e0}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 "Segoe UI",system-ui,sans-serif}}
main{{max-width:1300px;margin:auto;padding:24px 16px}}
h1{{color:var(--accent);margin:0 0 4px}} h2{{margin:28px 0 10px;font-size:18px}}
.muted{{color:var(--muted)}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:16px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}
.card b{{display:block;font-size:26px}} .card span{{color:var(--muted);font-size:12px}}
.tbl{{overflow:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;max-height:640px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{position:sticky;top:0;background:var(--accent);color:#fff;font-weight:600}}
td.n{{text-align:right;font-variant-numeric:tabular-nums}}
tr.ERROR td:nth-child(2){{color:#c0392b;font-weight:600}} tr.WARNING td:nth-child(2){{color:#b7791f;font-weight:600}}
.bar{{display:inline-block;width:90px;height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin-right:6px}}
.bar span{{display:block;height:100%}}
input{{padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);width:min(420px,100%)}}
.ai{{white-space:pre-wrap;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}
code{{font-size:11px}}
</style></head><body><main>
<h1>Báo cáo thẩm tra thông tin mô hình IFC</h1>
<div class=muted>Thời điểm: {e(res.started)} · Bộ quy tắc: {e(res.ruleset_source)} · {APP_NAME} {__version__}</div>
<div class=cards>
<div class=card><b>{t['files']}</b><span>File IFC</span></div>
<div class=card><b>{t['checked']:,}</b><span>Đối tượng kiểm tra</span></div>
<div class=card><b style="color:{color(t['compliance'])}">{t['compliance']:.1f}%</b><span>Tỷ lệ đạt</span></div>
<div class=card><b style="color:#c0392b">{t['errors']:,}</b><span>Lỗi bắt buộc</span></div>
<div class=card><b style="color:#b7791f">{t['warnings']:,}</b><span>Cảnh báo</span></div>
<div class=card><b>{t['proxies']:,}</b><span>IfcBuildingElementProxy</span></div>
</div>
{ai}
<section><h2>Theo file</h2><div class=tbl><table><tr><th>File</th><th>Schema</th><th>Kiểm tra</th><th>Phần tử con bỏ qua</th>
<th>Proxy</th><th>Tỷ lệ đạt</th><th>Lỗi</th><th>Cảnh báo</th><th>Ghi chú</th></tr>{files_rows}</table></div></section>
<section><h2>Theo quy tắc</h2><div class=tbl><table><tr><th>File</th><th>Mã</th><th>Yêu cầu</th><th>Mức độ</th>
<th>Áp dụng</th><th>Không đạt</th><th>Tỷ lệ đạt</th></tr>{rule_rows}</table></div></section>
<section><h2>Chi tiết lỗi</h2><p><input id=q placeholder="Lọc nhanh: mã QT, lớp IFC, tên, GUID..." oninput="f(this.value)"></p>
{more}<div class=tbl><table id=iss><tr><th>File</th><th>Mức độ</th><th>Mã QT</th><th>Lớp IFC</th><th>Tên</th><th>GlobalId</th>
<th>Nội dung</th><th>Giá trị</th></tr>{issue_rows}</table></div></section>
<footer class=muted style="margin-top:32px;text-align:center">{e(APP_NAME)} {__version__} · {e(CREDIT)}</footer>
</main><script>
function f(q){{q=q.toLowerCase();for(const r of document.querySelectorAll('#iss tr:not(:first-child)'))
r.style.display=r.textContent.toLowerCase().includes(q)?'':'none'}}
</script></body></html>"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)


def default_report_name() -> str:
    return "BaoCao_ThamTra_IFC_" + datetime.now().strftime("%Y%m%d_%H%M")
