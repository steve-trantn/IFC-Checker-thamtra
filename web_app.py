"""Thẩm Tra Mô Hình BIM - giao diện web (Streamlit).

Chạy:  streamlit run web_app.py     (hoặc run_web.bat)
Dùng chung bộ máy kiểm tra với bản desktop (ifc_checker/).
"""

from __future__ import annotations

import os
import time
import sys
import tempfile
from dataclasses import asdict, fields

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ifc_checker import APP_NAME, AUTHOR, AUTHOR_PHONE, __version__  # noqa: E402
from ifc_checker.engine import CheckOptions, run_checks  # noqa: E402
from ifc_checker.model_loader import load_model  # noqa: E402
from ifc_checker.report import default_report_name, export_excel, export_html  # noqa: E402
from ifc_checker.rules import (CONDITIONS, SEVERITIES, TARGETS, Rule, RuleSet,  # noqa: E402
                               default_ruleset, load_rules, save_rules)
from ifc_checker.textnorm import value_to_str  # noqa: E402

SEV_VN = {"ERROR": "Lỗi", "WARNING": "Cảnh báo", "INFO": "Thông tin"}
RULE_FIELDS = [f.name for f in fields(Rule)]

st.set_page_config(page_title=APP_NAME, page_icon="🏗️", layout="wide")

ss = st.session_state
ss.setdefault("models", {})        # key -> ModelInfo
ss.setdefault("ruleset", default_ruleset())
ss.setdefault("result", None)
ss.setdefault("tmpdir", tempfile.mkdtemp(prefix="thamtra_"))
ss.setdefault("ai_text", "")


def _save_upload(up) -> str:
    p = os.path.join(ss.tmpdir, up.name)
    with open(p, "wb") as f:
        f.write(up.getbuffer())
    return p


def _rules_df(rs: RuleSet) -> pd.DataFrame:
    return pd.DataFrame([asdict(r) for r in rs.rules], columns=RULE_FIELDS)


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown(f"### 🏗️ {APP_NAME}")
    st.caption(f"Phiên bản {__version__}  \nPhát triển bởi **{AUTHOR}**  \nĐT: {AUTHOR_PHONE}")
    st.divider()
    st.markdown("**Bộ quy tắc BEP**")
    up_rules = st.file_uploader("Mở bộ quy tắc (.xlsx / .json)", type=["xlsx", "json"], key="up_rules")
    if up_rules is not None and ss.get("rules_name") != (up_rules.name, up_rules.size):
        try:
            rs = load_rules(_save_upload(up_rules))
            rs.source = up_rules.name
            rs.ids_path = ss.ruleset.ids_path
            ss.ruleset = rs
            ss.rules_name = (up_rules.name, up_rules.size)
            st.success(f"Đã nạp {len(rs.rules)} quy tắc")
        except Exception as ex:
            st.error(f"Không đọc được: {ex}")
    up_ids = st.file_uploader("File IDS chạy kèm (tuỳ chọn)", type=["ids", "xml"], key="up_ids")
    ss.ruleset.ids_path = _save_upload(up_ids) if up_ids is not None else ""
    if st.button("Dùng bộ quy tắc mẫu mặc định"):
        ss.ruleset = default_ruleset()
        ss.pop("rules_name", None)
    st.caption(f"Nguồn: {ss.ruleset.source} · {sum(r.enabled for r in ss.ruleset.rules)}/{len(ss.ruleset.rules)} quy tắc bật")

    st.divider()
    st.markdown("**Tuỳ chọn**")
    part_mode = st.radio("Cụm đối tượng (cha - con)", ["auto", "parents", "all"],
                         format_func={"auto": "Tự động (khuyến nghị)", "parents": "Chỉ đối tượng cha",
                                      "all": "Tất cả"}.get,
                         help="Tự động: cha chỉ là nhóm -> kiểm tra các con; cha có thuộc tính -> chỉ kiểm tra cha.")
    classify = st.checkbox("Kiểm tra tên ↔ lớp IFC", value=True)

    st.divider()
    st.markdown("**Trợ lý AI (tuỳ chọn)**")
    api_key = st.text_input("API key Claude", type="password",
                            help="Chỉ lưu trong phiên làm việc này, không ghi ra đĩa. "
                                 "Để trống nếu máy chủ đã đặt ANTHROPIC_API_KEY.")
    ai_model = st.selectbox("Model", ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"])

st.title(f"🏗️ {APP_NAME}")
st.caption("Kiểm tra tự động thông tin phi hình học, yêu cầu BEP/LOIN và chủng loại IFC cho hồ sơ mô hình BIM.")

tab_files, tab_rules, tab_res, tab_clash, tab_stats, tab_ai = st.tabs(
    ["1. Hồ sơ IFC", "2. Quy tắc BEP", "3. Kết quả", "4. Xung đột & 3D", "5. Thống kê", "6. Trợ lý AI"])


def _viewer_panel(where: str, result, issue_row=None, issue_model=None, clash_id: str = ""):
    """Khung xem 3D dùng chung cho tab Kết quả và tab Xung đột."""
    from ifc_checker.viewer_data import objects_for
    from ifc_checker.web_viewer import ensure_scene, viewer

    all_models = [m for m in ss.models.values() if not m.error]
    names = [m.file_name for m in all_models]
    default = names if len(names) <= 4 else ([issue_model.file_name] if issue_model else names[:1])
    pick_files = st.multiselect("File hiển thị trong 3D", names, default=default, key=f"files3d_{where}",
                                help="Mô hình lớn: chọn ít file để hiển thị nhanh hơn.")
    models = [m for m in all_models if m.file_name in pick_files]
    if not models:
        st.info("Chọn ít nhất 1 file để hiển thị.")
        return
    sc = ensure_scene(f"scene_{where}", models, result, ss.get("clash_run"))
    focus = []
    if issue_row is not None and issue_model is not None:
        focus = objects_for(sc["meta"], issue_row["File"], int(issue_row["#"]), issue_model)
        if not focus:
            st.caption("Đối tượng đang chọn không có hình học (hoặc file chưa được chọn hiển thị).")
    picked = viewer(sc, focus_ids=focus, focus_clash=clash_id, height=620, key=f"viewer_{where}")
    st.caption(f"{len(sc['meta']['objects']):,} đối tượng 3D · {sc['size_mb']:.1f} MB")
    if picked:
        errs = [i for i in (result.issues if result else []) if i.file == picked.get("file")
                and i.step_id == picked.get("step")]
        st.markdown(f"**Đã chọn trong 3D:** `{picked.get('guid')}` · {picked.get('file')} · #{picked.get('step')}" +
                    ("".join(f"\n- [{i.rule_id}] {i.message}" for i in errs[:10]) if errs else "  \n_Không có lỗi_"))

# ------------------------------------------------------------------ 1. files
with tab_files:
    ups = st.file_uploader("Tải lên file IFC (chọn được nhiều file)", type=["ifc"], accept_multiple_files=True)
    current = set()
    if ups:
        for up in ups:
            key = (up.name, up.size)
            current.add(key)
            if key not in ss.models:
                with st.spinner(f"Đang đọc {up.name} ..."):
                    ss.models[key] = load_model(_save_upload(up))
    for k in [k for k in ss.models if k not in current and not ss.get("_keep_models")]:  # file đã gỡ khỏi ô tải lên
        del ss.models[k]
    if ss.models:
        rows = []
        for m in ss.models.values():
            rows.append({"File": m.file_name, "Schema": m.schema, "Phần mềm xuất": m.application,
                         "Dự án": m.project_name, "Đối tượng": len(m.elements), "Phần tử con": m.part_count,
                         "Proxy": sum(1 for r in m.elements if r.ifc_class == "IfcBuildingElementProxy"),
                         "Trạng thái": m.error or ("⚠ " + "; ".join(m.warnings) if m.warnings
                                                   else f"OK ({m.load_seconds:.1f}s)")})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.info("Chưa có file IFC. Kéo thả file vào ô phía trên.")

    run = st.button("▶ CHẠY KIỂM TRA", type="primary", disabled=not ss.models)
    if run:
        with st.spinner("Đang kiểm tra..."):
            ss.result = run_checks(list(ss.models.values()), ss.ruleset,
                                   CheckOptions(part_mode=part_mode, check_classification=classify))
        t = ss.result.totals()
        st.success(f"Xong: {t['checked']:,} đối tượng, đạt {t['compliance']:.1f}%, "
                   f"{t['errors']:,} lỗi, {t['warnings']:,} cảnh báo. Xem tab 3. Kết quả.")

# ------------------------------------------------------------------ 2. rules
with tab_rules:
    st.caption("Sửa trực tiếp trong bảng (thêm dòng ở cuối bảng). Bấm 'Áp dụng thay đổi' rồi chạy lại kiểm tra.")
    edited = st.data_editor(
        _rules_df(ss.ruleset), num_rows="dynamic", width="stretch", hide_index=True,
        column_config={
            "enabled": st.column_config.CheckboxColumn("Bật"),
            "id": "Mã QT", "ifc_class": "Lớp IFC áp dụng", "predefined_type": "PredefinedType",
            "name_filter": "Lọc tên (regex)", "file_filter": "Lọc tên file (regex)",
            "target": st.column_config.SelectboxColumn("Đối tượng KT", options=TARGETS),
            "pset": "Property Set", "name": "Thuộc tính",
            "condition": st.column_config.SelectboxColumn("Điều kiện", options=CONDITIONS),
            "value": "Giá trị", "severity": st.column_config.SelectboxColumn("Mức độ", options=SEVERITIES),
            "description": "Mô tả yêu cầu", "reference": "Tham chiếu BEP",
        }, key="rules_editor")
    c1, c2, c3 = st.columns([1, 1, 3])
    if c1.button("Áp dụng thay đổi"):
        rules = []
        for d in edited.fillna("").to_dict("records"):
            if not str(d.get("id", "")).strip() and not str(d.get("name", "")).strip():
                continue
            d["enabled"] = bool(d.get("enabled"))
            rules.append(Rule(**{k: d.get(k, "") for k in RULE_FIELDS}).normalized())
        ss.ruleset.rules = rules
        for w in ss.ruleset.validate():
            st.warning(w)
        st.success(f"Đã cập nhật {len(rules)} quy tắc")
    buf_path = os.path.join(ss.tmpdir, "BEP_Rules.xlsx")
    save_rules(ss.ruleset, buf_path)
    with open(buf_path, "rb") as f:
        c2.download_button("⬇ Tải bộ quy tắc (.xlsx)", f.read(), "BEP_Rules.xlsx")

    with st.expander("📑 Nhập Phụ lục LOI (yêu cầu thông tin thuộc tính) từ BEP - không cần AI"):
        from ifc_checker.bep_import import build_rules, components, guess_mapping, parse_loi
        bep_loi = st.file_uploader("File BEP (.docx / .xlsx) có bảng 'Nhóm | Thông tin | Kiểu'",
                                   type=["docx", "xlsx"], key="bep_loi")
        if bep_loi is not None:
            items = parse_loi(_save_upload(bep_loi))
            if not items:
                st.warning("Không tìm thấy bảng LOI trong file.")
            else:
                models = list(ss.models.values())
                names = [m.file_name for m in models]
                map_rows = []
                for c in components(items):
                    cls, ff = guess_mapping(c, names, models, ss.ruleset.mappings)
                    map_rows.append({"Hạng mục": c, "Số thuộc tính": sum(1 for i in items if i.component == c),
                                     "Lớp IFC": cls, "Lọc tên file (regex)": ff})
                st.caption(f"Tìm thấy {len(items)} thuộc tính. Kiểm tra cột Lớp IFC / Lọc tên file "
                           "(hạng mục để '*' và trống sẽ bị tắt). File đang mở: " + (", ".join(names) or "(chưa có)"))
                mdf = st.data_editor(pd.DataFrame(map_rows), hide_index=True, width="stretch",
                                     disabled=["Hạng mục", "Số thuộc tính"], key="loi_map")
                mode = st.radio("Cách nhập", ["Thay thế (giữ quy tắc GEN-*)", "Thêm vào bộ hiện tại"], horizontal=True)
                if st.button("Tạo quy tắc từ BEP"):
                    mapping = {r["Hạng mục"]: (str(r["Lớp IFC"] or "*"), str(r["Lọc tên file (regex)"] or ""))
                               for r in mdf.fillna("").to_dict("records")}
                    rules = build_rules(items, mapping, source=bep_loi.name)
                    if mode.startswith("Thay"):
                        ss.ruleset.rules = [r for r in ss.ruleset.rules if r.id.startswith("GEN")] + rules
                        ss.ruleset.source = f"(nhập từ {bep_loi.name})"
                    else:
                        ss.ruleset.rules.extend(rules)
                    st.success(f"Đã tạo {len(rules)} quy tắc ({sum(r.enabled for r in rules)} đang bật).")
                    st.rerun()

# ------------------------------------------------------------------ 3. results
res = ss.result
with tab_res:
    if res is None:
        st.info("Chưa có kết quả. Tải file IFC và bấm CHẠY KIỂM TRA ở tab 1.")
    else:
        t = res.totals()
        cols = st.columns(6)
        cols[0].metric("Đối tượng kiểm tra", f"{t['checked']:,}")
        cols[1].metric("Tỷ lệ đạt", f"{t['compliance']:.1f}%")
        cols[2].metric("Lỗi bắt buộc", f"{t['errors']:,}")
        cols[3].metric("Cảnh báo", f"{t['warnings']:,}")
        cols[4].metric("IfcBuildingElementProxy", f"{t['proxies']:,}")
        cols[5].metric("File", t["files"])

        # tải báo cáo
        name = default_report_name()
        xp = os.path.join(ss.tmpdir, name + ".xlsx")
        hp = os.path.join(ss.tmpdir, name + ".html")
        res.ai_summary = ss.ai_text or res.ai_summary
        export_excel(res, xp)
        export_html(res, hp)
        d1, d2, _ = st.columns([1, 1, 3])
        with open(xp, "rb") as f:
            d1.download_button("⬇ Báo cáo Excel", f.read(), name + ".xlsx", type="primary")
        with open(hp, "rb") as f:
            d2.download_button("⬇ Báo cáo HTML", f.read(), name + ".html")

        st.subheader("Theo file")
        st.dataframe(pd.DataFrame([{
            "File": f.file, "Schema": f.schema, "Kiểm tra": f.checked, "Phần tử con bỏ qua": f.parts_skipped,
            "Proxy": f.proxies, "Tỷ lệ đạt (%)": round(f.compliance, 1), "Lỗi": f.errors, "Cảnh báo": f.warnings,
            "Ghi chú": f.error} for f in res.files]), hide_index=True, width="stretch")

        st.subheader("Theo quy tắc")
        st.dataframe(pd.DataFrame([{
            "File": s.file, "Mã QT": s.rule_id, "Yêu cầu": s.description, "Mức độ": SEV_VN.get(s.severity, ""),
            "Áp dụng": s.applicable, "Không đạt": s.failed, "Tỷ lệ đạt": round(s.rate, 1)}
            for s in res.rule_stats if s.applicable]), hide_index=True, width="stretch",
            column_config={"Tỷ lệ đạt": st.column_config.ProgressColumn("Tỷ lệ đạt", min_value=0, max_value=100,
                                                                         format="%.0f%%")})

        st.subheader("Chi tiết lỗi")
        df = pd.DataFrame([{
            "File": i.file, "Mức độ": SEV_VN.get(i.severity, i.severity), "Mã QT": i.rule_id, "Lớp IFC": i.ifc_class,
            "Tên đối tượng": i.element_name, "Nội dung": i.message, "Giá trị": i.actual, "GlobalId": i.guid,
            "#": i.step_id} for i in res.issues])
        if df.empty:
            st.success("Không có lỗi nào 🎉")
        else:
            f1, f2, f3, f4 = st.columns(4)
            sel_file = f1.multiselect("File", sorted(df["File"].unique()))
            sel_sev = f2.multiselect("Mức độ", sorted(df["Mức độ"].unique()))
            sel_rule = f3.multiselect("Mã QT", sorted(df["Mã QT"].unique()))
            q = f4.text_input("Tìm (tên, GUID, nội dung...)")
            view = df
            if sel_file:
                view = view[view["File"].isin(sel_file)]
            if sel_sev:
                view = view[view["Mức độ"].isin(sel_sev)]
            if sel_rule:
                view = view[view["Mã QT"].isin(sel_rule)]
            if q:
                mask = view.astype(str).apply(lambda c: c.str.contains(q, case=False, regex=False)).any(axis=1)
                view = view[mask]
            show3d = st.toggle("🧊 Hiện mô hình 3D bên cạnh (bấm 1 dòng lỗi để phóng tới đối tượng)",
                               key="show3d_res")
            left, right = st.columns([1.1, 1]) if show3d else (st.container(), None)
            with left:
                st.caption(f"{len(view):,} dòng · chọn 1 dòng để xem toàn bộ thông tin đối tượng")
                ev = st.dataframe(view, hide_index=True, width="stretch", height=600 if show3d else 420,
                                  on_select="rerun", selection_mode="single-row", key="issues_df")
            rows = ev.selection.rows if ev and ev.selection else []
            rec = sel_model = r = None
            if rows:
                r = view.iloc[rows[0]]
                for m in ss.models.values():
                    if m.file_name == r["File"]:
                        sel_model = m
                        rec = next((x for x in m.records if x.step_id == int(r["#"])), None)
            if right is not None:
                with right:
                    _viewer_panel("res", res, r, sel_model)
            if rows:
                if rec:
                    with st.expander(f"🔎 {rec.ifc_class} - {rec.display_name()}", expanded=True):
                        a, b = st.columns(2)
                        a.markdown(
                            f"**GlobalId:** `{rec.guid}`  \n**PredefinedType:** {rec.predefined_type}  \n"
                            f"**Loại:** {rec.type_name} [{rec.type_class}]  \n"
                            f"**Không gian chứa:** {rec.container_name} [{rec.container_class}]  \n"
                            f"**Vật liệu:** {'; '.join(rec.materials) or '-'}  \n"
                            f"**Phân loại:** {'; '.join(rec.classifications) or '-'}")
                        errs = [i for i in res.issues if i.file == r["File"] and i.step_id == rec.step_id]
                        b.markdown("**Lỗi của đối tượng:**  \n" + "  \n".join(f"- [{i.rule_id}] {i.message}" for i in errs))
                        prows = [{"Property Set": ps.strip(), "Thuộc tính": k.strip(), "Giá trị": value_to_str(v)}
                                 for ps, pp in rec.psets.items() for k, v in pp.items()]
                        if prows:
                            st.dataframe(pd.DataFrame(prows), hide_index=True, width="stretch")

# ------------------------------------------------------------------ 4. clash & 3D
with tab_clash:
    from ifc_checker.bcf import export_bcf
    from ifc_checker.clash import (MODE_HELP, MODE_VN, MODES, default_matrix, default_tests,
                                   fcl_available, load_matrix, run_clash, save_matrix, tests_from_records,
                                   tests_to_records)
    from ifc_checker.engine import CheckResult

    models_ok = [m for m in ss.models.values() if not m.error]
    if not fcl_available():
        st.error("Chưa cài thư viện kiểm tra va chạm. Chạy: pip install python-fcl")
    elif not models_ok:
        st.info("Tải file IFC ở tab 1 trước.")
    else:
        with st.expander("ℹ️ Cách định nghĩa va chạm (4 loại kiểm tra)", expanded=False):
            st.markdown("\n".join(f"- **{k} – {MODE_VN[k]}**: {MODE_HELP[k]}" for k in MODES) +
                        "\n\nNhóm A/B nhận diện theo **từ khoá tên** (tên đối tượng, tên đối tượng cha, thuộc tính Name; "
                        "tự khớp cả dạng bị mất dấu khi xuất IFC, vd 'Mặt đường' ↔ 'Mt ng'), **lớp IFC** và **lọc tên "
                        "file**. Ma trận mẫu theo nhóm A1…D2 của BEP hạ tầng; các giá trị khoảng cách/chiều sâu là "
                        "giá trị mẫu - cần đối chiếu QCVN 07:2023, TCVN 7957 và hồ sơ thiết kế.")
        if "clash_tests" not in ss:
            ss.clash_tests = default_matrix()
        b1, b2, b3, b4 = st.columns([1.3, 1.3, 1.6, 1.2])
        if b1.button("📋 Ma trận va chạm mẫu (hạ tầng)"):
            ss.clash_tests = default_matrix()
            ss.pop("clash_editor", None)
            st.rerun()
        if b2.button("Theo từng cặp file"):
            ss.clash_tests = default_tests(models_ok)
            ss.pop("clash_editor", None)
            st.rerun()
        up_mx = b3.file_uploader("Mở ma trận (.xlsx)", type=["xlsx"], key="up_matrix", label_visibility="collapsed")
        if up_mx is not None and ss.get("matrix_name") != (up_mx.name, up_mx.size):
            try:
                ss.clash_tests = load_matrix(_save_upload(up_mx))
                ss.matrix_name = (up_mx.name, up_mx.size)
                ss.pop("clash_editor", None)
                st.rerun()
            except Exception as ex:
                st.error(f"Không đọc được ma trận: {ex}")
        mxp = os.path.join(ss.tmpdir, "Ma_tran_va_cham.xlsx")
        save_matrix(ss.clash_tests, mxp)
        with open(mxp, "rb") as f:
            b4.download_button("⬇ Tải ma trận (.xlsx)", f.read(), "Ma_tran_va_cham.xlsx")
        cols = ["code", "enabled", "name", "mode", "tolerance", "a_name", "b_name", "a_file", "a_class", "b_file",
                "b_class", "max_results", "note", "a_group", "b_group"]
        tdf = st.data_editor(
            pd.DataFrame(tests_to_records(ss.clash_tests), columns=cols),
            num_rows="dynamic", hide_index=True, width="stretch", key="clash_editor",
            column_order=cols[:13],
            column_config={
                "code": "Mã ô", "name": "Tên phép thử", "enabled": st.column_config.CheckboxColumn("Bật"),
                "mode": st.column_config.SelectboxColumn("Loại kiểm tra", options=MODES, required=True,
                                                         help="HARD va chạm cứng · CLEARANCE khoảng cách tối thiểu · "
                                                              "FLUSH cao độ khớp mặt · COVER chiều sâu chôn tối thiểu"),
                "tolerance": st.column_config.NumberColumn("Giá trị (m)", min_value=0.0, step=0.01, format="%.3f"),
                "a_name": "A - từ khoá tên", "b_name": "B - từ khoá tên",
                "a_file": "A - lọc tên file", "a_class": "A - lớp IFC",
                "b_file": "B - lọc tên file", "b_class": "B - lớp IFC",
                "max_results": st.column_config.NumberColumn("Tối đa", min_value=1, step=100),
                "note": st.column_config.TextColumn("Định nghĩa va chạm / căn cứ", width="large"),
            })
        if st.button("▶ Chạy kiểm tra xung đột", type="primary"):
            tests = tests_from_records(tdf.to_dict("records"))
            ss.clash_tests = tests
            bar = st.progress(0.0, text="Đang chuẩn bị...")

            def _prog(d, t, msg):
                bar.progress(min(1.0, d / max(t, 1)), text=msg)
            ss.clash_run = run_clash(models_ok, tests, progress=_prog)
            bar.empty()
            if ss.result is not None:
                ss.result.clashes = ss.clash_run.clashes
                ss.result.clash_messages = ss.clash_run.messages
        run = ss.get("clash_run")
        if run is not None:
            cl = run.clashes
            m1, m2, m3 = st.columns(3)
            m1.metric("Tổng xung đột", f"{len(cl):,}")
            m2.metric("Phép thử có xung đột", len({c.test for c in cl}))
            m3.metric("Phép thử đã chạy", sum(1 for t in run.tests if t.enabled))
            with st.expander("Nhật ký chạy (số va chạm theo từng phép thử)"):
                st.text("\n".join(run.messages))
            if cl:
                cdf = pd.DataFrame([{"Mã": c.id, "Phép thử": c.test, "Loại": MODE_VN.get(c.kind, c.kind),
                                     "Mô tả": c.detail, "Giá trị đo (m)": c.depth, "Lớp A": c.class_a,
                                     "Tên A": c.name_a, "Lớp B": c.class_b, "Tên B": c.name_b,
                                     "X": c.x, "Y": c.y, "Z": c.z, "File A": c.file_a, "File B": c.file_b}
                                    for c in cl])
                fc1, fc2 = st.columns([2, 1])
                sel_t = fc1.multiselect("Lọc phép thử", sorted(cdf["Phép thử"].unique()))
                sel_k = fc2.multiselect("Lọc loại kiểm tra", sorted(cdf["Loại"].unique()))
                cview = cdf
                if sel_t:
                    cview = cview[cview["Phép thử"].isin(sel_t)]
                if sel_k:
                    cview = cview[cview["Loại"].isin(sel_k)]
                show3d_c = st.toggle("🧊 Hiện mô hình 3D bên cạnh", value=True, key="show3d_clash")
                lc, rc = st.columns([1.1, 1]) if show3d_c else (st.container(), None)
                with lc:
                    st.caption(f"{len(cview):,} xung đột · bấm 1 dòng để phóng tới vị trí")
                    cev = st.dataframe(cview, hide_index=True, width="stretch", height=620,
                                       on_select="rerun", selection_mode="single-row", key="clash_df")
                crow = cev.selection.rows if cev and cev.selection else []
                cid = cview.iloc[crow[0]]["Mã"] if crow else ""
                if rc is not None:
                    with rc:
                        _viewer_panel("clash", ss.result, clash_id=cid)
                d1, d2, _ = st.columns([1, 1, 3])
                sel_cl = [c for c in cl if c.id in set(cview["Mã"])]
                bp = os.path.join(ss.tmpdir, "XungDot.bcfzip")
                export_bcf(sel_cl, bp)
                with open(bp, "rb") as f:
                    d1.download_button(f"⬇ BCF ({len(sel_cl):,} xung đột)", f.read(), "XungDot.bcfzip",
                                       help="Mở trong Navisworks / BIMcollab / Solibri / Revizto")
                xp = os.path.join(ss.tmpdir, "XungDot.xlsx")
                export_excel(CheckResult(started=time.strftime("%Y-%m-%d %H:%M:%S"), ruleset_source="(chỉ xung đột)",
                                         clashes=sel_cl, clash_messages=run.messages), xp)
                with open(xp, "rb") as f:
                    d2.download_button("⬇ Excel xung đột", f.read(), "XungDot.xlsx")
        else:
            st.markdown("**Xem mô hình 3D** (chưa chạy xung đột)")
            if st.toggle("Hiện mô hình 3D", key="show3d_only"):
                _viewer_panel("only", ss.result)

# ------------------------------------------------------------------ 5. stats
with tab_stats:
    if res is None:
        st.info("Chưa có kết quả.")
    else:
        a, b = st.columns([2, 3])
        cdf = pd.DataFrame(res.class_counts, columns=["File", "Lớp IFC", "PredefinedType/Loại", "Số ĐT", "Phần tử con"])
        a.subheader("Số lượng theo lớp IFC")
        a.bar_chart(cdf.groupby("Lớp IFC")["Số ĐT"].sum().sort_values(ascending=False).head(20), horizontal=True)
        a.dataframe(cdf, hide_index=True, width="stretch")
        b.subheader("Mức độ điền thuộc tính")
        idf = pd.DataFrame(res.inventory, columns=["File", "Lớp IFC", "Property Set", "Thuộc tính", "Có", "Đã điền",
                                                   "Tổng ĐT", "Giá trị mẫu"])
        if not idf.empty:
            idf["Tỷ lệ điền"] = (100 * idf["Đã điền"] / idf["Tổng ĐT"].where(idf["Tổng ĐT"] > 0)).round(1)
        qq = b.text_input("Lọc thuộc tính")
        if qq and not idf.empty:
            idf = idf[idf.astype(str).apply(lambda c: c.str.contains(qq, case=False, regex=False)).any(axis=1)]
        b.dataframe(idf, hide_index=True, width="stretch", height=560,
                    column_config={"Tỷ lệ điền": st.column_config.ProgressColumn(
                        "Tỷ lệ điền", min_value=0, max_value=100, format="%.0f%%")})

# ------------------------------------------------------------------ 6. AI
with tab_ai:
    st.caption("Chỉ gửi dữ liệu tóm tắt (tên lớp, tên loại, thuộc tính, thống kê) lên Claude - không gửi file IFC. "
               "Hãy đảm bảo phù hợp quy định bảo mật của dự án.")

    def _client():
        from ifc_checker.ai import AIClient, AIError
        if not (api_key or os.environ.get("ANTHROPIC_API_KEY")):
            st.warning("Nhập API key Claude ở thanh bên trái.")
            return None
        try:
            return AIClient(api_key, ai_model, "medium")
        except AIError as ex:
            st.error(str(ex))
            return None

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Đề xuất lớp IFC đúng** (Proxy & đối tượng nghi sai chủng loại)")
        if st.button("🤖 Phân tích", disabled=not ss.models):
            cl = _client()
            if cl:
                from ifc_checker.ai import AIError, flagged_from_result
                try:
                    with st.spinner("AI đang phân tích..."):
                        items = cl.suggest_classes(list(ss.models.values()),
                                                   flagged_ids=flagged_from_result(res) if res else set())
                    if res:
                        res.ai_suggestions = items
                    ss.ai_items = items
                except AIError as ex:
                    st.error(str(ex))
        if ss.get("ai_items"):
            st.dataframe(pd.DataFrame(ss.ai_items).drop(columns=["changed"], errors="ignore"),
                         hide_index=True, width="stretch")
    with c2:
        st.markdown("**Tạo quy tắc từ tài liệu BEP**")
        bep = st.file_uploader("BEP / EIR / bảng LOIN", type=["pdf", "docx", "xlsx", "txt", "md"], key="bep")
        extra = st.text_input("Yêu cầu thêm (tuỳ chọn)", placeholder="vd: chỉ bộ môn Thoát nước")
        if st.button("📄 Tạo quy tắc", disabled=bep is None):
            cl = _client()
            if cl:
                from ifc_checker.ai import AIError
                try:
                    with st.spinner("AI đang đọc BEP (có thể mất vài phút)..."):
                        rules = cl.generate_rules(bep_path=_save_upload(bep), extra=extra)
                    ss.ruleset.rules.extend(rules)
                    st.success(f"Đã thêm {len(rules)} quy tắc vào tab 2 - hãy rà soát trước khi dùng.")
                except AIError as ex:
                    st.error(str(ex))

    st.divider()
    st.markdown("**Nhận xét thẩm tra**")
    if st.button("📝 Viết nhận xét", disabled=res is None):
        cl = _client()
        if cl:
            from ifc_checker.ai import AIError
            try:
                with st.spinner("AI đang viết..."):
                    ss.ai_text = cl.summarize(res)
            except AIError as ex:
                st.error(str(ex))
    ss.ai_text = st.text_area("Nội dung (sửa được, sẽ đưa vào báo cáo)", ss.ai_text, height=260)

st.divider()
st.caption(f"{APP_NAME} {__version__} · Phát triển bởi {AUTHOR} · ĐT: {AUTHOR_PHONE}")
