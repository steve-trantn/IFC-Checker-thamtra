"""Giao diện desktop (tkinter) cho Thẩm Tra Mô Hình BIM."""

from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, CREDIT, __version__
from .engine import CheckOptions, CheckResult, run_checks
from .model_loader import ModelInfo, load_model
from .report import default_report_name, export_excel, export_html, export_json
from .rules import (CONDITION_HELP, CONDITIONS, SEVERITIES, TARGET_HELP, TARGETS, Rule, RuleSet,
                    default_ruleset, load_rules, save_rules)
from .settings import Settings
from .textnorm import split_list, value_to_str

SEV_VN = {"ERROR": "Lỗi", "WARNING": "Cảnh báo", "INFO": "Thông tin"}
MAX_ROWS = 5000
PART_MODES = [
    ("auto", "Tự động (khuyến nghị): cha chỉ là nhóm -> kiểm tra các con; cha có thuộc tính -> chỉ kiểm tra cha"),
    ("parents", "Chỉ kiểm tra đối tượng cha / độc lập"),
    ("all", "Kiểm tra tất cả (cả cha và con)"),
]


def _open_file(path):
    try:
        os.startfile(path)  # type: ignore[attr-defined]
    except Exception:
        pass


class Cancelled(Exception):
    pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {__version__} - Kiểm tra thông tin phi hình học IFC theo BEP")
        self.geometry("1360x820")
        self.minsize(1000, 620)
        try:
            self.state("zoomed")
        except tk.TclError:
            pass
        self.settings = Settings.load()
        self.models: dict[str, ModelInfo] = {}
        self.ruleset: RuleSet = default_ruleset()
        self.rules_dirty = False
        self.result: CheckResult | None = None
        self.rec_index = {}
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.cancel_flag = threading.Event()

        self._style()
        self._build()
        if self.settings.last_rules and os.path.exists(self.settings.last_rules):
            try:
                self.ruleset = load_rules(self.settings.last_rules)
            except Exception:
                self.ruleset = default_ruleset()
        self.refresh_rules()
        self.after(100, self._poll)
        self.after(300, self._bring_to_front)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _bring_to_front(self):
        self.lift()
        self.attributes("-topmost", True)
        self.after(400, lambda: self.attributes("-topmost", False))
        self.focus_force()

    # ================================================================ UI
    def _style(self):
        st = ttk.Style(self)
        if "vista" in st.theme_names():
            st.theme_use("vista")
        self.option_add("*Font", ("Segoe UI", 10))
        st.configure("Treeview", rowheight=24, font=("Segoe UI", 9))
        st.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        st.configure("Big.TLabel", font=("Segoe UI", 20, "bold"))
        st.configure("Cap.TLabel", foreground="#64748b", font=("Segoe UI", 9))
        st.configure("Title.TLabel", font=("Segoe UI", 11, "bold"))
        st.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

    def _build(self):
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill="x")
        ttk.Button(bar, text="📂 Thêm file IFC", command=self.add_files).pack(side="left", padx=2)
        ttk.Button(bar, text="📁 Thêm thư mục", command=self.add_folder).pack(side="left", padx=2)
        ttk.Button(bar, text="📋 Mở bộ quy tắc", command=self.open_rules).pack(side="left", padx=2)
        ttk.Button(bar, text="▶ CHẠY KIỂM TRA", style="Accent.TButton", command=self.run_check).pack(side="left", padx=8)
        ttk.Button(bar, text="📊 Xuất báo cáo", command=self.export_report).pack(side="left", padx=2)
        ttk.Button(bar, text="⚙ Cài đặt", command=self.open_settings).pack(side="right", padx=2)
        ttk.Button(bar, text="? Hướng dẫn", command=self.show_help).pack(side="right", padx=2)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self._tab_files()
        self._tab_rules()
        self._tab_results()
        self._tab_stats()
        self._tab_ai()

        sb = ttk.Frame(self, padding=(8, 2))
        sb.pack(fill="x")
        self.status = tk.StringVar(value="Sẵn sàng. Thêm file IFC và chọn bộ quy tắc BEP để bắt đầu.")
        ttk.Label(sb, textvariable=self.status).pack(side="left")
        ttk.Label(sb, text=CREDIT, style="Cap.TLabel").pack(side="right", padx=(10, 0))
        self.btn_cancel = ttk.Button(sb, text="Huỷ", command=self.cancel_flag.set, state="disabled")
        self.btn_cancel.pack(side="right")
        self.pb = ttk.Progressbar(sb, length=260, mode="determinate")
        self.pb.pack(side="right", padx=6)

    def _tree(self, parent, cols, widths, height=10, anchors=None):
        fr = ttk.Frame(parent)
        tv = ttk.Treeview(fr, columns=[c for c, _ in cols], show="headings", height=height)
        for (c, label), w in zip(cols, widths):
            tv.heading(c, text=label, command=lambda c=c, tv=tv: self._sort(tv, c, False))
            tv.column(c, width=w, anchor=(anchors or {}).get(c, "w"), stretch=True)
        ys = ttk.Scrollbar(fr, orient="vertical", command=tv.yview)
        xs = ttk.Scrollbar(fr, orient="horizontal", command=tv.xview)
        tv.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        tv.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        fr.rowconfigure(0, weight=1)
        fr.columnconfigure(0, weight=1)
        tv.tag_configure("ERROR", foreground="#b42318")
        tv.tag_configure("WARNING", foreground="#a15c07")
        tv.tag_configure("bad", background="#fde8e8")
        tv.tag_configure("mid", background="#fff4d6")
        tv.tag_configure("good", background="#e6f4ea")
        tv.tag_configure("off", foreground="#9aa4b2")
        return fr, tv

    def _sort(self, tv, col, reverse):
        items = [(tv.set(k, col), k) for k in tv.get_children("")]

        def key(x):
            v = x[0].replace("%", "").replace(",", "")
            try:
                return (0, float(v))
            except ValueError:
                return (1, x[0].lower())
        items.sort(key=key, reverse=reverse)
        for i, (_, k) in enumerate(items):
            tv.move(k, "", i)
        tv.heading(col, command=lambda: self._sort(tv, col, not reverse))

    # ---------------------------------------------------------------- Tab 1
    def _tab_files(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="  1. Hồ sơ IFC  ")
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 6))
        ttk.Button(top, text="Thêm file", command=self.add_files).pack(side="left", padx=2)
        ttk.Button(top, text="Thêm thư mục", command=self.add_folder).pack(side="left", padx=2)
        ttk.Button(top, text="Xoá file chọn", command=self.remove_files).pack(side="left", padx=2)
        ttk.Button(top, text="Xoá tất cả", command=self.clear_files).pack(side="left", padx=2)
        self.files_info = tk.StringVar(value="Chưa có file")
        ttk.Label(top, textvariable=self.files_info, style="Cap.TLabel").pack(side="right")
        cols = [("file", "File"), ("schema", "Schema"), ("app", "Phần mềm xuất"), ("project", "Dự án"),
                ("elements", "Đối tượng"), ("parts", "Phần tử con"), ("proxy", "Proxy"), ("status", "Trạng thái")]
        fr, self.tv_files = self._tree(tab, cols, [360, 110, 220, 160, 80, 90, 70, 240],
                                       anchors={"elements": "e", "parts": "e", "proxy": "e"})
        fr.pack(fill="both", expand=True)
        ttk.Label(tab, style="Cap.TLabel", text=(
            "Mẹo: 'Phần tử con' là đối tượng thuộc 1 cụm (assembly). Chế độ Tự động: nếu cha chỉ là nhóm thì kiểm tra "
            "từng con, nếu cha mang thông tin thì con là khối hình học tách nhỏ và được bỏ qua (đổi trong Cài đặt).")).pack(anchor="w", pady=4)

    # ---------------------------------------------------------------- Tab 2
    def _tab_rules(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="  2. Quy tắc BEP  ")
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 4))
        for txt, cmd in (("Mở...", self.open_rules), ("Lưu", self.save_rules), ("Lưu thành...", self.save_rules_as),
                         ("Tải lại", self.reload_rules), ("Bộ mẫu mặc định", self.reset_rules)):
            ttk.Button(top, text=txt, command=cmd).pack(side="left", padx=2)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=6)
        for txt, cmd in (("+ Thêm", self.add_rule), ("Sửa", self.edit_rule), ("Nhân bản", self.dup_rule),
                         ("Xoá", self.del_rule), ("Bật/Tắt", self.toggle_rule)):
            ttk.Button(top, text=txt, command=cmd).pack(side="left", padx=2)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(top, text="📑 Nhập Phụ lục LOI từ BEP", command=self.import_loi).pack(side="left", padx=2)
        ttk.Button(top, text="🤖 AI: tạo quy tắc từ BEP", command=self.ai_generate_rules).pack(side="left", padx=2)

        info = ttk.Frame(tab)
        info.pack(fill="x", pady=2)
        self.rules_info = tk.StringVar()
        ttk.Label(info, textvariable=self.rules_info).pack(side="left")
        ttk.Button(info, text="Bỏ IDS", command=self.clear_ids).pack(side="right", padx=2)
        ttk.Button(info, text="Chọn file IDS...", command=self.choose_ids).pack(side="right", padx=2)
        self.ids_info = tk.StringVar()
        ttk.Label(info, textvariable=self.ids_info, style="Cap.TLabel").pack(side="right", padx=6)

        cols = [("on", "Bật"), ("id", "Mã QT"), ("cls", "Lớp IFC áp dụng"), ("target", "Đối tượng KT"),
                ("pset", "Property Set"), ("name", "Thuộc tính"), ("cond", "Điều kiện"), ("value", "Giá trị"),
                ("sev", "Mức độ"), ("desc", "Mô tả yêu cầu"), ("ref", "Tham chiếu")]
        fr, self.tv_rules = self._tree(tab, cols, [40, 70, 200, 95, 150, 160, 85, 170, 75, 330, 120],
                                       anchors={"on": "center"})
        fr.pack(fill="both", expand=True)
        self.tv_rules.bind("<Double-1>", lambda e: self.edit_rule())
        self.tv_rules.bind("<Delete>", lambda e: self.del_rule())
        ttk.Label(tab, style="Cap.TLabel", text=(
            "Bộ quy tắc lưu dạng Excel (sheet Rules + ClassMapping + Hướng dẫn) để dễ chỉnh sửa hàng loạt. "
            "Sheet ClassMapping: từ khoá trong tên -> lớp IFC đúng, dùng để phát hiện xuất sai chủng loại.")
        ).pack(anchor="w", pady=4)

    # ---------------------------------------------------------------- Tab 3
    def _tab_results(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="  3. Kết quả  ")
        cards = ttk.Frame(tab)
        cards.pack(fill="x", pady=(0, 6))
        self.card_vars = {}
        for key, cap in (("checked", "Đối tượng kiểm tra"), ("compliance", "Tỷ lệ đạt"), ("errors", "Lỗi bắt buộc"),
                         ("warnings", "Cảnh báo"), ("proxies", "IfcBuildingElementProxy"), ("files", "File")):
            f = ttk.Frame(cards, padding=(14, 4), relief="groove")
            f.pack(side="left", padx=4, fill="y")
            v = tk.StringVar(value="-")
            self.card_vars[key] = v
            lbl = ttk.Label(f, textvariable=v, style="Big.TLabel")
            lbl.pack(anchor="w")
            if key == "compliance":
                self.lbl_compliance = lbl
            ttk.Label(f, text=cap, style="Cap.TLabel").pack(anchor="w")

        pw = ttk.PanedWindow(tab, orient="vertical")
        pw.pack(fill="both", expand=True)
        up = ttk.Frame(pw)
        ttk.Label(up, text="Theo quy tắc (bấm 1 dòng để lọc lỗi)", style="Title.TLabel").pack(anchor="w")
        cols = [("file", "File"), ("id", "Mã QT"), ("desc", "Yêu cầu"), ("sev", "Mức độ"), ("app", "Áp dụng"),
                ("pass", "Đạt"), ("fail", "Không đạt"), ("rate", "Tỷ lệ đạt")]
        fr, self.tv_stats = self._tree(up, cols, [260, 80, 440, 80, 80, 70, 85, 85], height=7,
                                       anchors={"app": "e", "pass": "e", "fail": "e", "rate": "e"})
        fr.pack(fill="both", expand=True)
        self.tv_stats.bind("<<TreeviewSelect>>", self._on_stat_select)
        pw.add(up, weight=1)

        down = ttk.Frame(pw)
        flt = ttk.Frame(down)
        flt.pack(fill="x", pady=(6, 2))
        ttk.Label(flt, text="Chi tiết lỗi", style="Title.TLabel").pack(side="left", padx=(0, 10))
        self.f_file = tk.StringVar(value="(Tất cả)")
        self.f_sev = tk.StringVar(value="(Tất cả)")
        self.f_rule = tk.StringVar(value="(Tất cả)")
        self.f_cls = tk.StringVar(value="(Tất cả)")
        self.f_text = tk.StringVar()
        self.cb_file = self._combo(flt, "File", self.f_file, 26)
        self._combo(flt, "Mức độ", self.f_sev, 10, ["(Tất cả)", "Lỗi", "Cảnh báo", "Thông tin"])
        self.cb_rule = self._combo(flt, "Mã QT", self.f_rule, 10)
        self.cb_cls = self._combo(flt, "Lớp", self.f_cls, 22)
        ttk.Label(flt, text="Tìm").pack(side="left", padx=(8, 2))
        e = ttk.Entry(flt, textvariable=self.f_text, width=24)
        e.pack(side="left")
        e.bind("<Return>", lambda ev: self.refresh_issues())
        ttk.Button(flt, text="Lọc", command=self.refresh_issues).pack(side="left", padx=4)
        ttk.Button(flt, text="Bỏ lọc", command=self._clear_filters).pack(side="left")
        self.issue_info = tk.StringVar()
        ttk.Label(flt, textvariable=self.issue_info, style="Cap.TLabel").pack(side="right")
        cols = [("file", "File"), ("sev", "Mức độ"), ("rule", "Mã QT"), ("cls", "Lớp IFC"), ("name", "Tên đối tượng"),
                ("msg", "Nội dung"), ("actual", "Giá trị hiện tại"), ("guid", "GlobalId"), ("step", "#")]
        fr, self.tv_issues = self._tree(down, cols, [200, 70, 70, 170, 230, 380, 140, 190, 60], height=12)
        fr.pack(fill="both", expand=True)
        self.tv_issues.bind("<Double-1>", self._on_issue_open)
        self.tv_issues.bind("<Button-3>", self._issue_menu)
        pw.add(down, weight=2)

    def _combo(self, parent, label, var, width, values=None):
        ttk.Label(parent, text=label).pack(side="left", padx=(8, 2))
        cb = ttk.Combobox(parent, textvariable=var, width=width, state="readonly", values=values or ["(Tất cả)"])
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_issues())
        return cb

    # ---------------------------------------------------------------- Tab 4
    def _tab_stats(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="  4. Thống kê  ")
        pw = ttk.PanedWindow(tab, orient="horizontal")
        pw.pack(fill="both", expand=True)
        left = ttk.Frame(pw)
        ttk.Label(left, text="Số lượng theo lớp IFC", style="Title.TLabel").pack(anchor="w")
        cols = [("file", "File"), ("cls", "Lớp IFC"), ("pt", "PredefinedType/Loại"), ("n", "Số ĐT"), ("p", "Phần tử con")]
        fr, self.tv_cls = self._tree(left, cols, [160, 190, 170, 60, 80], anchors={"n": "e", "p": "e"})
        fr.pack(fill="both", expand=True)
        pw.add(left, weight=1)
        right = ttk.Frame(pw)
        hdr = ttk.Frame(right)
        hdr.pack(fill="x")
        ttk.Label(hdr, text="Mức độ điền thuộc tính (Property Set)", style="Title.TLabel").pack(side="left")
        self.inv_filter = tk.StringVar()
        e = ttk.Entry(hdr, textvariable=self.inv_filter, width=26)
        e.pack(side="right")
        e.bind("<KeyRelease>", lambda ev: self.refresh_inventory())
        ttk.Label(hdr, text="Lọc:").pack(side="right")
        cols = [("file", "File"), ("cls", "Lớp IFC"), ("pset", "Property Set"), ("prop", "Thuộc tính"),
                ("fill", "Đã điền/Tổng"), ("rate", "Tỷ lệ"), ("sample", "Giá trị mẫu")]
        fr, self.tv_inv = self._tree(right, cols, [140, 160, 170, 180, 90, 60, 260], anchors={"fill": "e", "rate": "e"})
        fr.pack(fill="both", expand=True)
        pw.add(right, weight=2)
        ttk.Label(tab, style="Cap.TLabel", text=(
            "Bảng thuộc tính giúp đối chiếu nhanh với BEP: thuộc tính nào có trong mô hình, điền được bao nhiêu %, "
            "giá trị mẫu ra sao - dùng để lập/hiệu chỉnh bộ quy tắc.")).pack(anchor="w", pady=4)

    # ---------------------------------------------------------------- Tab 5
    def _tab_ai(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="  5. Trợ lý AI  ")
        top = ttk.Frame(tab)
        top.pack(fill="x")
        self.ai_info = tk.StringVar()
        ttk.Label(top, textvariable=self.ai_info).pack(side="left")
        ttk.Button(top, text="Cài đặt AI", command=self.open_settings).pack(side="right")
        acts = ttk.Frame(tab)
        acts.pack(fill="x", pady=6)
        ttk.Button(acts, text="🤖 Đề xuất lớp IFC đúng (Proxy & đối tượng nghi sai)",
                   command=self.ai_suggest).pack(side="left", padx=2)
        ttk.Button(acts, text="📝 Viết nhận xét thẩm tra", command=self.ai_summarize).pack(side="left", padx=2)
        ttk.Button(acts, text="📄 Tạo quy tắc từ file BEP", command=self.ai_generate_rules).pack(side="left", padx=2)

        pw = ttk.PanedWindow(tab, orient="vertical")
        pw.pack(fill="both", expand=True)
        up = ttk.Frame(pw)
        ttk.Label(up, text="Đề xuất lớp IFC", style="Title.TLabel").pack(anchor="w")
        cols = [("file", "File"), ("cur", "Lớp hiện tại"), ("label", "Tên / Loại"), ("n", "Số ĐT"),
                ("sug", "Lớp đề xuất"), ("pt", "PredefinedType"), ("conf", "Tin cậy"), ("why", "Lý do")]
        fr, self.tv_ai = self._tree(up, cols, [170, 170, 260, 60, 190, 120, 80, 380], height=7, anchors={"n": "e"})
        fr.pack(fill="both", expand=True)
        pw.add(up, weight=1)
        down = ttk.Frame(pw)
        ttk.Label(down, text="Nhận xét / Trả lời của AI", style="Title.TLabel").pack(anchor="w", pady=(6, 0))
        self.ai_text = tk.Text(down, wrap="word", height=10, font=("Segoe UI", 10), padx=8, pady=6)
        self.ai_text.pack(fill="both", expand=True)
        ask = ttk.Frame(down)
        ask.pack(fill="x", pady=4)
        ttk.Label(ask, text="Hỏi AI về kết quả:").pack(side="left")
        self.ai_q = tk.StringVar()
        e = ttk.Entry(ask, textvariable=self.ai_q)
        e.pack(side="left", fill="x", expand=True, padx=4)
        e.bind("<Return>", lambda ev: self.ai_ask())
        ttk.Button(ask, text="Gửi", command=self.ai_ask).pack(side="left")
        ttk.Button(ask, text="Đưa nhận xét vào báo cáo", command=self._ai_text_to_report).pack(side="left", padx=4)
        pw.add(down, weight=2)
        self._update_ai_info()

    # ================================================================ worker
    def run_bg(self, label, fn, on_done):
        if self.busy:
            messagebox.showinfo(APP_NAME, "Đang xử lý tác vụ khác, vui lòng đợi.")
            return
        self.busy = True
        self.cancel_flag.clear()
        self.btn_cancel.configure(state="normal")
        self.status.set(label)
        self.pb.configure(mode="indeterminate")
        self.pb.start(12)

        def progress(done, total, msg=""):
            self.q.put(("progress", done, total, msg))

        def cancel():
            return self.cancel_flag.is_set()

        def worker():
            try:
                r = fn(progress, cancel)
                self.q.put(("done", on_done, r))
            except Exception as ex:
                traceback.print_exc()
                self.q.put(("error", ex))
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "progress":
                    _, done, total, msg = item
                    if total:
                        self.pb.stop()
                        self.pb.configure(mode="determinate", maximum=max(total, 1), value=done)
                    if msg:
                        self.status.set(msg)
                elif kind == "call":
                    item[1](item[2])
                elif kind in ("done", "error"):
                    self.busy = False
                    self.pb.stop()
                    self.pb.configure(mode="determinate", value=0)
                    self.btn_cancel.configure(state="disabled")
                    if kind == "done":
                        try:
                            item[1](item[2])
                        except Exception as ex:
                            traceback.print_exc()
                            messagebox.showerror(APP_NAME, f"Lỗi: {ex}")
                    else:
                        self.status.set("Có lỗi xảy ra")
                        messagebox.showerror(APP_NAME, str(item[1]))
        except queue.Empty:
            pass
        self.after(100, self._poll)

    # ================================================================ files
    def add_files(self):
        paths = filedialog.askopenfilenames(title="Chọn file IFC", initialdir=self.settings.last_dir or None,
                                            filetypes=[("IFC", "*.ifc *.IFC"), ("Tất cả", "*.*")])
        if paths:
            self.settings.last_dir = os.path.dirname(paths[0])
            self._load_paths(list(paths))

    def add_folder(self):
        d = filedialog.askdirectory(title="Chọn thư mục hồ sơ", initialdir=self.settings.last_dir or None)
        if not d:
            return
        paths = []
        for root, _, files in os.walk(d):
            paths += [os.path.join(root, f) for f in files if f.lower().endswith(".ifc")]
        if not paths:
            messagebox.showinfo(APP_NAME, "Không tìm thấy file .ifc trong thư mục")
            return
        self.settings.last_dir = d
        self._load_paths(sorted(paths))

    def _load_paths(self, paths):
        paths = [p for p in paths if p not in self.models]
        if not paths:
            return
        for p in paths:
            self.tv_files.insert("", "end", iid=p, values=(os.path.basename(p), "", "", "", "", "", "", "Đang chờ..."))

        def job(progress, cancel):
            out = []
            for i, p in enumerate(paths):
                if cancel():
                    break
                progress(i, len(paths), f"Đọc file {i + 1}/{len(paths)}: {os.path.basename(p)}")
                m = load_model(p, progress=None, cancel=cancel)
                out.append(m)
                self.q.put(("call", self._file_loaded, m))  # cập nhật từng file
            return out
        self.run_bg("Đang đọc file IFC...", job, lambda ms: self._after_files())

    def _file_loaded(self, m: ModelInfo):
        self.models[m.path] = m
        for r in m.records:
            self.rec_index[(m.file_name, r.step_id)] = r
        status = m.error or f"OK ({m.load_seconds:.1f}s)"
        proxies = sum(1 for r in m.records if r.kind == "element" and r.ifc_class == "IfcBuildingElementProxy")
        if self.tv_files.exists(m.path):
            self.tv_files.item(m.path, values=(m.file_name, m.schema, m.application, m.project_name,
                                               len(m.elements), m.part_count, proxies, status),
                               tags=("ERROR",) if m.error else ())

    def _after_files(self):
        for iid in self.tv_files.get_children():
            if iid not in self.models:
                self.tv_files.delete(iid)
        n = len(self.models)
        total = sum(len(m.elements) for m in self.models.values())
        self.files_info.set(f"{n} file · {total:,} đối tượng")
        self.status.set(f"Đã đọc {n} file IFC.")

    def remove_files(self):
        for iid in self.tv_files.selection():
            m = self.models.pop(iid, None)
            self.tv_files.delete(iid)
            if m:
                for r in m.records:
                    self.rec_index.pop((m.file_name, r.step_id), None)
        self._after_files()

    def clear_files(self):
        self.models.clear()
        self.rec_index.clear()
        self.tv_files.delete(*self.tv_files.get_children())
        self._after_files()

    # ================================================================ rules
    def refresh_rules(self):
        tv = self.tv_rules
        tv.delete(*tv.get_children())
        for i, r in enumerate(self.ruleset.rules):
            tv.insert("", "end", iid=str(i), values=(
                "✔" if r.enabled else "", r.id, r.ifc_class, r.target, r.pset, r.name, r.condition, r.value,
                SEV_VN.get(r.severity, r.severity), r.description, r.reference),
                tags=(() if r.enabled else ("off",)))
        on = sum(1 for r in self.ruleset.rules if r.enabled)
        dirty = " (chưa lưu)" if self.rules_dirty else ""
        self.rules_info.set(f"Nguồn: {self.ruleset.source}{dirty} · {len(self.ruleset.rules)} quy tắc, {on} đang bật · "
                            f"{len(self.ruleset.mappings)} từ khoá phân loại")
        self.ids_info.set(f"IDS: {os.path.basename(self.ruleset.ids_path)}" if self.ruleset.ids_path else "IDS: (không)")

    def _confirm_discard(self):
        return not self.rules_dirty or messagebox.askyesno(APP_NAME, "Bộ quy tắc hiện tại chưa lưu. Bỏ thay đổi?")

    def open_rules(self):
        if not self._confirm_discard():
            return
        p = filedialog.askopenfilename(title="Mở bộ quy tắc", filetypes=[
            ("Bộ quy tắc", "*.xlsx *.xlsm *.json *.ids"), ("Excel", "*.xlsx"), ("JSON", "*.json"),
            ("IDS buildingSMART", "*.ids")])
        if not p:
            return
        try:
            rs = load_rules(p)
        except Exception as ex:
            messagebox.showerror(APP_NAME, f"Không đọc được bộ quy tắc:\n{ex}")
            return
        if p.lower().endswith(".ids"):
            self.ruleset.ids_path = p  # giữ quy tắc hiện tại, chạy kèm IDS
            if messagebox.askyesno(APP_NAME, "Đã chọn file IDS.\nCó tắt các quy tắc Excel hiện tại để chỉ kiểm tra theo IDS không?"):
                for r in self.ruleset.rules:
                    r.enabled = False
        else:
            self.ruleset = rs
            self.settings.last_rules = p
            self.rules_dirty = False
        self._show_rule_warnings()
        self.refresh_rules()
        self.nb.select(1)

    def _show_rule_warnings(self):
        w = self.ruleset.validate()
        if w:
            messagebox.showwarning(APP_NAME, "Cảnh báo bộ quy tắc:\n- " + "\n- ".join(w[:25]))

    def reload_rules(self):
        src = self.ruleset.source
        if not os.path.exists(src):
            messagebox.showinfo(APP_NAME, "Bộ quy tắc chưa được lưu ra file.")
            return
        if not self._confirm_discard():
            return
        ids = self.ruleset.ids_path
        self.ruleset = load_rules(src)
        self.ruleset.ids_path = self.ruleset.ids_path or ids
        self.rules_dirty = False
        self._show_rule_warnings()
        self.refresh_rules()

    def save_rules(self):
        src = self.ruleset.source
        if not src or not os.path.splitext(src)[1].lower() in (".xlsx", ".json"):
            return self.save_rules_as()
        self._save_rules_to(src)

    def save_rules_as(self):
        p = filedialog.asksaveasfilename(title="Lưu bộ quy tắc", defaultextension=".xlsx",
                                         initialfile="BEP_Rules.xlsx",
                                         filetypes=[("Excel", "*.xlsx"), ("JSON", "*.json")])
        if p:
            self._save_rules_to(p)

    def _save_rules_to(self, p):
        try:
            save_rules(self.ruleset, p)
        except PermissionError:
            messagebox.showerror(APP_NAME, "Không ghi được file - có thể file đang mở trong Excel.")
            return
        self.ruleset.source = p
        self.settings.last_rules = p
        self.rules_dirty = False
        self.refresh_rules()
        if messagebox.askyesno(APP_NAME, f"Đã lưu {p}\nMở file để chỉnh sửa trong Excel?"):
            _open_file(p)

    def reset_rules(self):
        if not self._confirm_discard():
            return
        self.ruleset = default_ruleset()
        self.rules_dirty = True
        self.refresh_rules()

    def choose_ids(self):
        p = filedialog.askopenfilename(title="Chọn file IDS", filetypes=[("IDS", "*.ids"), ("XML", "*.xml")])
        if p:
            from .ids_runner import ids_available
            if not ids_available():
                messagebox.showerror(APP_NAME, "Chưa cài ifctester. Chạy: pip install ifctester")
                return
            self.ruleset.ids_path = p
            self.refresh_rules()

    def clear_ids(self):
        self.ruleset.ids_path = ""
        self.refresh_rules()

    def _sel_rule_idx(self):
        sel = self.tv_rules.selection()
        return [int(s) for s in sel]

    def add_rule(self):
        n = len(self.ruleset.rules) + 1
        r = Rule(id=f"R{n:03d}")
        if RuleDialog(self, r, "Thêm quy tắc").ok:
            self.ruleset.rules.append(r.normalized())
            self.rules_dirty = True
            self.refresh_rules()

    def edit_rule(self):
        idx = self._sel_rule_idx()
        if not idx:
            return
        r = self.ruleset.rules[idx[0]]
        if RuleDialog(self, r, f"Sửa quy tắc {r.id}").ok:
            r.normalized()
            self.rules_dirty = True
            self.refresh_rules()
            self.tv_rules.selection_set(str(idx[0]))

    def dup_rule(self):
        import copy
        for i in self._sel_rule_idx():
            r = copy.deepcopy(self.ruleset.rules[i])
            r.id = r.id + "-copy"
            self.ruleset.rules.append(r)
        self.rules_dirty = True
        self.refresh_rules()

    def del_rule(self):
        idx = self._sel_rule_idx()
        if idx and messagebox.askyesno(APP_NAME, f"Xoá {len(idx)} quy tắc?"):
            for i in sorted(idx, reverse=True):
                del self.ruleset.rules[i]
            self.rules_dirty = True
            self.refresh_rules()

    def toggle_rule(self):
        idx = self._sel_rule_idx()
        for i in idx:
            self.ruleset.rules[i].enabled = not self.ruleset.rules[i].enabled
        self.rules_dirty = True
        self.refresh_rules()
        for i in idx:
            self.tv_rules.selection_add(str(i))

    def import_loi(self):
        from .bep_import import build_rules, components, guess_mapping, parse_loi
        p = filedialog.askopenfilename(title="Chọn BEP có bảng yêu cầu thông tin thuộc tính (LOI)", filetypes=[
            ("BEP", "*.docx *.xlsx"), ("Word", "*.docx"), ("Excel", "*.xlsx")])
        if not p:
            return
        try:
            items = parse_loi(p)
        except Exception as ex:
            messagebox.showerror(APP_NAME, f"Không đọc được file:\n{ex}")
            return
        if not items:
            messagebox.showinfo(APP_NAME, "Không tìm thấy bảng LOI (cần bảng có cột 'Nhóm | Thông tin | Kiểu').\n"
                                          "Có thể dùng chức năng AI để đọc BEP dạng khác.")
            return
        models = list(self.models.values())
        names = [m.file_name for m in models]
        rows = []
        for c in components(items):
            cls, ff = guess_mapping(c, names, models, self.ruleset.mappings)
            rows.append([c, sum(1 for i in items if i.component == c), cls, ff])
        dlg = LoiMapDialog(self, rows, names)
        if not dlg.ok:
            return
        mapping = {r[0]: (r[2], r[3]) for r in dlg.rows}
        rules = build_rules(items, mapping, source=os.path.basename(p))
        ans = messagebox.askyesnocancel(APP_NAME, (
            f"Tạo {len(rules)} quy tắc từ {len(rows)} hạng mục ({sum(r.enabled for r in rules)} quy tắc đang bật).\n\n"
            "YES = Thêm vào bộ quy tắc hiện tại\nNO = Thay thế (giữ lại các quy tắc chung GEN-*)\nCANCEL = Huỷ"))
        if ans is None:
            return
        if ans:
            existing = {r.id for r in self.ruleset.rules}
            for r in rules:
                while r.id in existing:
                    r.id += "b"
                existing.add(r.id)
            self.ruleset.rules.extend(rules)
        else:
            self.ruleset.rules = [r for r in self.ruleset.rules if r.id.startswith("GEN")] + rules
            self.ruleset.source = f"(nhập từ {os.path.basename(p)})"
        self.rules_dirty = True
        self.refresh_rules()
        self.status.set(f"Đã nhập {len(rules)} quy tắc LOI từ BEP - rà soát rồi bấm Lưu.")

    # ================================================================ check
    def run_check(self):
        models = [m for m in self.models.values()]
        if not models:
            messagebox.showinfo(APP_NAME, "Chưa có file IFC. Hãy thêm file trước.")
            return
        if not any(r.enabled for r in self.ruleset.rules) and not self.ruleset.ids_path \
                and not self.settings.check_classification:
            messagebox.showinfo(APP_NAME, "Không có quy tắc nào đang bật.")
            return
        warns = self.ruleset.validate()
        if warns and not messagebox.askyesno(APP_NAME, "Bộ quy tắc có cảnh báo:\n- " + "\n- ".join(warns[:15]) +
                                             "\n\nVẫn chạy kiểm tra?"):
            return
        opts = CheckOptions(part_mode=self.settings.part_mode,
                            check_classification=self.settings.check_classification,
                            empty_tokens=self.settings.empty_tokens)
        rs = self.ruleset

        def job(progress, cancel):
            return run_checks(models, rs, opts, progress, cancel)
        self.run_bg("Đang kiểm tra...", job, self._show_result)

    def _show_result(self, res: CheckResult):
        old = self.result
        if old is not None:  # giữ kết quả AI của lần trước nếu có
            res.ai_suggestions = old.ai_suggestions
        self.result = res
        t = res.totals()
        self.card_vars["checked"].set(f"{t['checked']:,}")
        self.card_vars["compliance"].set(f"{t['compliance']:.1f}%")
        self.card_vars["errors"].set(f"{t['errors']:,}")
        self.card_vars["warnings"].set(f"{t['warnings']:,}")
        self.card_vars["proxies"].set(f"{t['proxies']:,}")
        self.card_vars["files"].set(str(t["files"]))
        c = t["compliance"]
        self.lbl_compliance.configure(foreground="#b42318" if c < 50 else ("#a15c07" if c < 90 else "#1e7b34"))

        tv = self.tv_stats
        tv.delete(*tv.get_children())
        for i, s in enumerate(res.rule_stats):
            if not s.applicable:
                continue
            tag = "good" if s.rate >= 90 else ("mid" if s.rate >= 50 else "bad")
            tv.insert("", "end", iid=f"s{i}", values=(s.file, s.rule_id, s.description, SEV_VN.get(s.severity, ""),
                                                      s.applicable, s.passed, s.failed, f"{s.rate:.1f}%"), tags=(tag,))
        files = sorted({i.file for i in res.issues})
        self.cb_file.configure(values=["(Tất cả)"] + files)
        self.cb_rule.configure(values=["(Tất cả)"] + sorted({i.rule_id for i in res.issues}))
        self.cb_cls.configure(values=["(Tất cả)"] + sorted({i.ifc_class for i in res.issues if i.ifc_class}))
        self._clear_filters()
        self.refresh_stats()
        self.nb.select(2)
        not_applied = [s.rule_id for s in res.rule_stats if not s.applicable]
        msg = f"Kiểm tra xong: {t['checked']:,} đối tượng, đạt {c:.1f}%, {t['errors']:,} lỗi, {t['warnings']:,} cảnh báo."
        if not_applied:
            msg += f" ({len(set(not_applied))} quy tắc không có đối tượng áp dụng)"
        self.status.set(msg)

    def _clear_filters(self):
        for v in (self.f_file, self.f_sev, self.f_rule, self.f_cls):
            v.set("(Tất cả)")
        self.f_text.set("")
        self.refresh_issues()

    def _on_stat_select(self, _e=None):
        sel = self.tv_stats.selection()
        if not sel:
            return
        vals = self.tv_stats.item(sel[0], "values")
        self.f_file.set(vals[0])
        self.f_rule.set(vals[1])
        self.refresh_issues()

    def refresh_issues(self):
        tv = self.tv_issues
        tv.delete(*tv.get_children())
        if not self.result:
            return
        rev = {v: k for k, v in SEV_VN.items()}
        f_file, f_rule, f_cls = self.f_file.get(), self.f_rule.get(), self.f_cls.get()
        f_sev = rev.get(self.f_sev.get())
        q = self.f_text.get().strip().lower()
        n = 0
        shown = 0
        for idx, i in enumerate(self.result.issues):
            if f_file != "(Tất cả)" and i.file != f_file:
                continue
            if f_rule != "(Tất cả)" and i.rule_id != f_rule:
                continue
            if f_cls != "(Tất cả)" and i.ifc_class != f_cls:
                continue
            if f_sev and i.severity != f_sev:
                continue
            if q and q not in f"{i.element_name} {i.guid} {i.message} {i.actual} {i.ifc_class} {i.rule_id}".lower():
                continue
            n += 1
            if shown < MAX_ROWS:
                tv.insert("", "end", iid=str(idx), values=(i.file, SEV_VN.get(i.severity, i.severity), i.rule_id,
                                                           i.ifc_class, i.element_name, i.message, i.actual, i.guid,
                                                           i.step_id), tags=(i.severity,))
                shown += 1
        extra = f" (hiển thị {MAX_ROWS:,} dòng đầu - xem đủ trong báo cáo Excel)" if n > MAX_ROWS else ""
        self.issue_info.set(f"{n:,} lỗi/cảnh báo{extra}")

    def _issue_from_iid(self, iid):
        return self.result.issues[int(iid)] if self.result else None

    def _on_issue_open(self, _e=None):
        sel = self.tv_issues.selection()
        if not sel:
            return
        i = self._issue_from_iid(sel[0])
        rec = self.rec_index.get((i.file, i.step_id))
        if rec is None:
            return
        issues = [x for x in self.result.issues if x.file == i.file and x.step_id == i.step_id]
        ElementWindow(self, rec, issues)

    def _issue_menu(self, e):
        iid = self.tv_issues.identify_row(e.y)
        if not iid:
            return
        self.tv_issues.selection_set(iid)
        i = self._issue_from_iid(iid)
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="Xem chi tiết đối tượng", command=self._on_issue_open)
        m.add_command(label="Copy GlobalId", command=lambda: self._copy(i.guid))
        m.add_command(label="Copy các GlobalId đang lọc (để tìm trong phần mềm BIM)",
                      command=lambda: self._copy("\n".join(
                          sorted({self._issue_from_iid(k).guid for k in self.tv_issues.get_children()}))))
        m.tk_popup(e.x_root, e.y_root)

    def _copy(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.set("Đã copy vào clipboard")

    # ================================================================ stats
    def refresh_stats(self):
        tv = self.tv_cls
        tv.delete(*tv.get_children())
        if not self.result:
            return
        for row in self.result.class_counts:
            tv.insert("", "end", values=row, tags=(("WARNING",) if row[1] == "IfcBuildingElementProxy" else ()))
        self.refresh_inventory()

    def refresh_inventory(self):
        tv = self.tv_inv
        tv.delete(*tv.get_children())
        if not self.result:
            return
        q = self.inv_filter.get().strip().lower()
        n = 0
        for f, cls, pset, prop, present, filled, total, samples in self.result.inventory:
            if q and q not in f"{f} {cls} {pset} {prop}".lower():
                continue
            rate = 100.0 * filled / total if total else 0
            tag = "good" if rate >= 90 else ("mid" if rate >= 50 else "bad")
            tv.insert("", "end", values=(f, cls, pset, prop, f"{filled}/{total}", f"{rate:.0f}%", samples), tags=(tag,))
            n += 1
            if n >= MAX_ROWS:
                break

    # ================================================================ export
    def export_report(self):
        if not self.result:
            messagebox.showinfo(APP_NAME, "Chưa có kết quả. Hãy chạy kiểm tra trước.")
            return
        p = filedialog.asksaveasfilename(title="Xuất báo cáo", initialfile=default_report_name() + ".xlsx",
                                         defaultextension=".xlsx", filetypes=[
                                             ("Excel (chi tiết)", "*.xlsx"), ("HTML (gửi nhanh)", "*.html"),
                                             ("JSON (dữ liệu)", "*.json")])
        if not p:
            return
        res = self.result
        ext = os.path.splitext(p)[1].lower()

        def job(progress, cancel):
            if ext == ".html":
                export_html(res, p)
            elif ext == ".json":
                export_json(res, p)
            else:
                export_excel(res, p)
                export_html(res, os.path.splitext(p)[0] + ".html")
            return p

        def done(path):
            extra = "\n(Kèm bản HTML cùng tên)" if ext == ".xlsx" else ""
            self.status.set(f"Đã xuất {path}")
            if messagebox.askyesno(APP_NAME, f"Đã xuất báo cáo:\n{path}{extra}\n\nMở báo cáo?"):
                _open_file(path)
        self.run_bg("Đang xuất báo cáo...", job, done)

    # ================================================================ AI
    def _update_ai_info(self):
        from .ai import sdk_available
        if not sdk_available():
            self.ai_info.set("⚠ Chưa cài thư viện 'anthropic' (pip install anthropic). Các chức năng khác vẫn dùng bình thường.")
        elif not self.settings.has_ai_key():
            self.ai_info.set("AI chưa được cấu hình - nhập API key Claude trong Cài đặt (không bắt buộc; phần mềm vẫn "
                             "kiểm tra đầy đủ khi không có AI).")
        else:
            self.ai_info.set(f"AI: {self.settings.ai_model} · mức suy luận {self.settings.ai_effort}. "
                             "Chỉ gửi dữ liệu tóm tắt (tên lớp, tên loại, thuộc tính, thống kê) - không gửi file IFC.")

    def _ai_client(self):
        from .ai import AIClient, sdk_available
        if not sdk_available():
            messagebox.showerror(APP_NAME, "Chưa cài thư viện 'anthropic'.\nChạy: pip install anthropic")
            return None
        if not self.settings.has_ai_key():
            if messagebox.askyesno(APP_NAME, "Chưa có API key Claude. Mở Cài đặt để nhập?"):
                self.open_settings()
            if not self.settings.has_ai_key():
                return None
        if not self.settings.ai_consent:
            ok = messagebox.askyesno(APP_NAME, (
                "Chức năng AI sẽ gửi DỮ LIỆU TÓM TẮT lên dịch vụ Claude (Anthropic):\n"
                "- tên lớp IFC, tên đối tượng/loại, tên và giá trị mẫu của thuộc tính\n"
                "- thống kê kết quả kiểm tra\n- nội dung file BEP (khi tạo quy tắc)\n\n"
                "Không gửi file IFC hay hình học. Hãy đảm bảo việc này phù hợp quy định bảo mật của dự án.\n\n"
                "Đồng ý tiếp tục?"))
            if not ok:
                return None
            self.settings.ai_consent = True
            self.settings.save()
        try:
            return AIClient(self.settings.api_key, self.settings.ai_model, self.settings.ai_effort)
        except Exception as ex:
            messagebox.showerror(APP_NAME, str(ex))
            return None

    def ai_suggest(self):
        if not self.models:
            messagebox.showinfo(APP_NAME, "Chưa có file IFC.")
            return
        from .ai import build_class_groups, flagged_from_result
        flagged = flagged_from_result(self.result) if self.result else set()
        groups = build_class_groups(self.models.values(), True, flagged)
        if not groups:
            messagebox.showinfo(APP_NAME, "Không có đối tượng Proxy hoặc nghi sai chủng loại để AI phân tích.")
            return
        n = min(len(groups), 300)
        if not messagebox.askyesno(APP_NAME, f"Sẽ gửi {n} nhóm đối tượng (gộp theo tên loại) cho AI phân tích.\nTiếp tục?"):
            return
        client = self._ai_client()
        if not client:
            return
        models = list(self.models.values())

        def job(progress, cancel):
            return client.suggest_classes(models, max_groups=300, progress=progress, flagged_ids=flagged)

        def done(items):
            if self.result is not None:
                self.result.ai_suggestions = items
            self._ai_items = items
            tv = self.tv_ai
            tv.delete(*tv.get_children())
            for s in items:
                tv.insert("", "end", values=(s["file"], s["current_class"], s["label"], s["count"], s["suggested_class"],
                                             s["predefined_type"], s["confidence"], s["reason"]),
                          tags=(("WARNING",) if s.get("changed") else ()))
            changed = sum(1 for s in items if s.get("changed"))
            self.status.set(f"AI đề xuất xong: {changed}/{len(items)} nhóm nên đổi lớp IFC. "
                            f"Token: {client.usage['input']:,} vào / {client.usage['output']:,} ra")
            self.nb.select(4)
        self.run_bg("AI đang phân tích chủng loại đối tượng...", job, done)

    def ai_summarize(self):
        if not self.result:
            messagebox.showinfo(APP_NAME, "Hãy chạy kiểm tra trước.")
            return
        client = self._ai_client()
        if not client:
            return
        res = self.result

        def job(progress, cancel):
            return client.summarize(res)

        def done(text):
            self.ai_text.delete("1.0", "end")
            self.ai_text.insert("1.0", text)
            res.ai_summary = text
            self.status.set("AI đã viết nhận xét (đã đưa vào báo cáo; có thể sửa trực tiếp rồi bấm 'Đưa nhận xét vào báo cáo').")
            self.nb.select(4)
        self.run_bg("AI đang viết nhận xét thẩm tra...", job, done)

    def _ai_text_to_report(self):
        if not self.result:
            messagebox.showinfo(APP_NAME, "Chưa có kết quả kiểm tra.")
            return
        self.result.ai_summary = self.ai_text.get("1.0", "end").strip()
        self.status.set("Đã cập nhật nhận xét vào báo cáo.")

    def ai_ask(self):
        q = self.ai_q.get().strip()
        if not q:
            return
        client = self._ai_client()
        if not client:
            return
        res = self.result

        def job(progress, cancel):
            return client.ask(res, q)

        def done(text):
            self.ai_text.insert("end", f"\n\n❓ {q}\n{text}\n")
            self.ai_text.see("end")
            self.ai_q.set("")
            self.status.set("AI đã trả lời.")
        self.run_bg("AI đang trả lời...", job, done)

    def ai_generate_rules(self):
        p = filedialog.askopenfilename(title="Chọn tài liệu BEP / EIR / bảng LOIN", filetypes=[
            ("Tài liệu", "*.pdf *.docx *.txt *.md *.xlsx"), ("PDF", "*.pdf"), ("Word", "*.docx"),
            ("Excel", "*.xlsx"), ("Text", "*.txt *.md")])
        if not p:
            return
        extra = AskText(self, "Yêu cầu thêm cho AI (tuỳ chọn)",
                        "Ví dụ: chỉ lấy yêu cầu bộ môn Thoát nước; Pset dự án tên 'Tham số định danh'...").value
        if extra is None:
            return
        client = self._ai_client()
        if not client:
            return

        def job(progress, cancel):
            return client.generate_rules(bep_path=p, extra=extra)

        def done(rules):
            if not rules:
                messagebox.showinfo(APP_NAME, "AI không tìm thấy yêu cầu thông tin nào có thể kiểm tra tự động.")
                return
            ans = messagebox.askyesnocancel(APP_NAME, (
                f"AI đề xuất {len(rules)} quy tắc từ {os.path.basename(p)}.\n\n"
                "YES = Thêm vào bộ quy tắc hiện tại\nNO = Thay thế bộ quy tắc hiện tại\nCANCEL = Huỷ\n\n"
                "Lưu ý: hãy rà soát lại từng quy tắc trước khi dùng cho thẩm tra chính thức."))
            if ans is None:
                return
            existing = {r.id for r in self.ruleset.rules} if ans else set()
            for r in rules:
                r.reference = r.reference or os.path.basename(p)
                while r.id in existing:
                    r.id += "-AI"
                existing.add(r.id)
            if ans:
                self.ruleset.rules.extend(rules)
            else:
                self.ruleset.rules = rules
                self.ruleset.source = "(AI tạo từ " + os.path.basename(p) + ")"
            self.rules_dirty = True
            self.refresh_rules()
            self.nb.select(1)
            self.status.set(f"Đã thêm {len(rules)} quy tắc do AI đề xuất - hãy rà soát và Lưu.")
        self.run_bg("AI đang đọc BEP và lập quy tắc (có thể mất vài phút)...", job, done)

    # ================================================================ misc
    def open_settings(self):
        SettingsDialog(self)
        self._update_ai_info()

    def show_help(self):
        HelpWindow(self)

    def _on_close(self):
        if self.rules_dirty and not messagebox.askyesno(APP_NAME, "Bộ quy tắc chưa lưu. Vẫn thoát?"):
            return
        try:
            self.settings.save()
        except Exception:
            pass
        self.destroy()


# ==================================================================== dialogs
class _Dialog(tk.Toplevel):
    def __init__(self, master, title):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.resizable(True, False)
        self.ok = False
        self.body = ttk.Frame(self, padding=12)
        self.body.pack(fill="both", expand=True)

    def finish(self, w=None, h=None):
        btns = ttk.Frame(self, padding=(12, 0, 12, 12))
        btns.pack(fill="x")
        ttk.Button(btns, text="Huỷ", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="OK", command=self._ok, style="Accent.TButton").pack(side="right")
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        m = self.master
        x = m.winfo_rootx() + (m.winfo_width() - self.winfo_width()) // 2
        y = m.winfo_rooty() + (m.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()
        self.wait_window()

    def _ok(self):
        if self.apply() is not False:
            self.ok = True
            self.destroy()

    def apply(self):
        return True


class RuleDialog(_Dialog):
    def __init__(self, master, rule: Rule, title):
        super().__init__(master, title)
        self.rule = rule
        b = self.body
        self.vars = {}
        rows = [
            ("id", "Mã QT", "entry", None),
            ("ifc_class", "Lớp IFC áp dụng", "entry", "vd: IfcWall;IfcSlab  ·  * = mọi đối tượng  ·  !IfcX để loại trừ"),
            ("predefined_type", "PredefinedType", "entry", "(tuỳ chọn) vd: SHEAR;PARTITIONING"),
            ("name_filter", "Lọc theo tên (regex)", "entry", "(tuỳ chọn) chỉ áp dụng khi tên khớp, vd: tuong|wall"),
            ("file_filter", "Lọc theo tên file (regex)", "entry", "(tuỳ chọn) chỉ áp dụng cho file khớp, vd: THOATNUOC(MUA|THAI)"),
            ("target", "Đối tượng KT", "combo", TARGETS),
            ("pset", "Property Set", "entry", "Tên Pset (không phân biệt hoa/thường, dấu) · * = mọi Pset · Pset_*Common"),
            ("name", "Tên thuộc tính", "entry", None),
            ("condition", "Điều kiện", "combo", CONDITIONS),
            ("value", "Giá trị yêu cầu", "entry", None),
            ("severity", "Mức độ", "combo", SEVERITIES),
            ("description", "Mô tả yêu cầu", "entry", None),
            ("reference", "Tham chiếu BEP", "entry", "vd: BEP mục 5.3 / Phụ lục LOIN"),
        ]
        for r_, (key, label, kind, extra) in enumerate(rows):
            ttk.Label(b, text=label).grid(row=r_ * 2, column=0, sticky="w", pady=(4, 0), padx=(0, 8))
            v = tk.StringVar(value=str(getattr(rule, key) or ""))
            self.vars[key] = v
            if kind == "combo":
                w = ttk.Combobox(b, textvariable=v, values=extra, state="readonly", width=24)
                w.grid(row=r_ * 2, column=1, sticky="w", pady=(4, 0))
                w.bind("<<ComboboxSelected>>", lambda e: self._help())
            else:
                ttk.Entry(b, textvariable=v, width=70).grid(row=r_ * 2, column=1, sticky="ew", pady=(4, 0))
                if extra:
                    ttk.Label(b, text=extra, style="Cap.TLabel").grid(row=r_ * 2 + 1, column=1, sticky="w")
        self.en = tk.BooleanVar(value=rule.enabled)
        ttk.Checkbutton(b, text="Áp dụng quy tắc này", variable=self.en).grid(row=100, column=1, sticky="w", pady=6)
        self.help = tk.StringVar()
        ttk.Label(b, textvariable=self.help, foreground="#1f4e78", wraplength=620, justify="left").grid(
            row=101, column=0, columnspan=2, sticky="w", pady=(4, 0))
        b.columnconfigure(1, weight=1)
        self._help()
        self.finish()

    def _help(self):
        t = self.vars["target"].get()
        c = self.vars["condition"].get()
        self.help.set(f"{t}: {TARGET_HELP.get(t, '')}\n{c}: {CONDITION_HELP.get(c, '')}")

    def apply(self):
        import re
        vals = {k: v.get().strip() for k, v in self.vars.items()}
        if not vals["id"]:
            messagebox.showwarning(APP_NAME, "Cần nhập Mã QT", parent=self)
            return False
        if vals["target"] in ("PROPERTY", "ATTRIBUTE") and not vals["name"]:
            messagebox.showwarning(APP_NAME, "Cần nhập Tên thuộc tính", parent=self)
            return False
        for pat in ([vals["value"]] if vals["condition"] == "REGEX" else []) + [vals["name_filter"], vals["file_filter"]]:
            if pat:
                try:
                    re.compile(pat)
                except re.error as ex:
                    messagebox.showwarning(APP_NAME, f"Regex lỗi: {pat}\n{ex}", parent=self)
                    return False
        for k, v in vals.items():
            setattr(self.rule, k, v)
        self.rule.enabled = self.en.get()
        return True


class LoiMapDialog(_Dialog):
    """Gán hạng mục trong bảng LOI của BEP với lớp IFC / tên file."""

    def __init__(self, master, rows, file_names):
        super().__init__(master, "Gán hạng mục LOI với mô hình")
        self.resizable(True, True)
        self.rows = rows
        b = self.body
        ttk.Label(b, wraplength=820, justify="left", text=(
            "Mỗi hạng mục trong BEP cần biết áp dụng cho đối tượng nào. Nhập 'Lọc tên file' (regex trên tên file IFC, "
            "theo quy tắc đặt tên của BEP) và/hoặc 'Lớp IFC'. Hạng mục để trống cả hai (lớp '*', không lọc file) sẽ "
            "được tạo nhưng TẮT. Phần mềm đã tự đoán từ các file đang mở - hãy kiểm tra lại.")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        for j, h in enumerate(("Hạng mục (BEP)", "Số thuộc tính", "Lớp IFC", "Lọc tên file (regex)")):
            ttk.Label(b, text=h, style="Title.TLabel").grid(row=1, column=j, sticky="w", padx=4)
        self.vars = []
        for i, (comp, n, cls, ff) in enumerate(rows, start=2):
            ttk.Label(b, text=comp).grid(row=i, column=0, sticky="w", padx=4, pady=1)
            ttk.Label(b, text=str(n)).grid(row=i, column=1, padx=4)
            vc, vf = tk.StringVar(value=cls), tk.StringVar(value=ff)
            ttk.Entry(b, textvariable=vc, width=34).grid(row=i, column=2, padx=4, pady=1, sticky="ew")
            ttk.Entry(b, textvariable=vf, width=30).grid(row=i, column=3, padx=4, pady=1, sticky="ew")
            self.vars.append((vc, vf))
        if file_names:
            ttk.Label(b, style="Cap.TLabel", wraplength=820, justify="left",
                      text="File đang mở: " + ", ".join(file_names)).grid(
                row=len(rows) + 3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        b.columnconfigure(2, weight=1)
        b.columnconfigure(3, weight=1)
        self.finish()

    def apply(self):
        import re
        for (vc, vf), row in zip(self.vars, self.rows):
            ff = vf.get().strip()
            if ff:
                try:
                    re.compile(ff)
                except re.error as ex:
                    messagebox.showwarning(APP_NAME, f"Regex lỗi ở '{row[0]}': {ex}", parent=self)
                    return False
            row[2], row[3] = vc.get().strip() or "*", ff
        return True


class SettingsDialog(_Dialog):
    def __init__(self, master: App):
        super().__init__(master, "Cài đặt")
        from .ai import EFFORTS, MODELS
        s = master.settings
        self.s = s
        b = self.body
        lf = ttk.LabelFrame(b, text="Kiểm tra", padding=8)
        lf.pack(fill="x")
        self.cls = tk.BooleanVar(value=s.check_classification)
        ttk.Label(lf, text="Cụm đối tượng (cha - con):").pack(anchor="w")
        self.mode = tk.StringVar(value=s.part_mode)
        for val, txt in PART_MODES:
            ttk.Radiobutton(lf, text=txt, value=val, variable=self.mode).pack(anchor="w", padx=12)
        ttk.Checkbutton(lf, text="Kiểm tra tên đối tượng ↔ lớp IFC theo bảng từ khoá (phát hiện sai chủng loại)",
                        variable=self.cls).pack(anchor="w")
        ttk.Label(lf, text="Giá trị được coi là 'trống' (phân cách ;):").pack(anchor="w", pady=(6, 0))
        self.empty = tk.StringVar(value="; ".join(s.empty_tokens))
        ttk.Entry(lf, textvariable=self.empty, width=80).pack(fill="x")

        af = ttk.LabelFrame(b, text="Trợ lý AI (Claude) - không bắt buộc", padding=8)
        af.pack(fill="x", pady=(10, 0))
        ttk.Label(af, text="API key (để trống nếu đã đặt biến môi trường ANTHROPIC_API_KEY):").grid(
            row=0, column=0, columnspan=3, sticky="w")
        self.key = tk.StringVar(value=s.api_key)
        self.key_entry = ttk.Entry(af, textvariable=self.key, show="•", width=60)
        self.key_entry.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.show = tk.BooleanVar()
        ttk.Checkbutton(af, text="Hiện", variable=self.show,
                        command=lambda: self.key_entry.configure(show="" if self.show.get() else "•")).grid(row=1, column=2)
        ttk.Label(af, text="Model:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.model = tk.StringVar(value=s.ai_model)
        ttk.Combobox(af, textvariable=self.model, values=MODELS, width=24).grid(row=2, column=1, sticky="w", pady=(6, 0))
        ttk.Label(af, text="Mức suy luận:").grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.effort = tk.StringVar(value=s.ai_effort)
        ttk.Combobox(af, textvariable=self.effort, values=EFFORTS, state="readonly", width=12).grid(
            row=3, column=1, sticky="w", pady=(4, 0))
        ttk.Label(af, style="Cap.TLabel", wraplength=560, justify="left", text=(
            "claude-opus-5-5: chất lượng cao nhất (mặc định) · claude-sonnet-5-5: nhanh, rẻ hơn · "
            "claude-haiku-5-5: rẻ nhất cho mô hình lớn. API key được lưu trong %APPDATA%\\ThamTraBIM\\settings.json "
            "trên máy này.")).grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Button(af, text="Kiểm tra kết nối AI", command=self._test).grid(row=5, column=0, sticky="w", pady=(8, 0))
        self.test_res = tk.StringVar()
        ttk.Label(af, textvariable=self.test_res, wraplength=420).grid(row=5, column=1, columnspan=2, sticky="w", pady=(8, 0))
        af.columnconfigure(1, weight=1)
        self.finish()

    def _collect(self):
        self.s.part_mode = self.mode.get()
        self.s.check_classification = self.cls.get()
        self.s.empty_tokens = split_list(self.empty.get()) + ([""] if "" not in split_list(self.empty.get()) else [])
        self.s.api_key = self.key.get().strip()
        self.s.ai_model = self.model.get().strip() or "claude-opus-5-5"
        self.s.ai_effort = self.effort.get()

    def _test(self):
        from .ai import AIClient
        self._collect()
        self.test_res.set("Đang kiểm tra...")
        self.update()
        try:
            r = AIClient(self.s.api_key, self.s.ai_model, self.s.ai_effort).test()
            self.test_res.set("✔ " + r[:200])
        except Exception as ex:
            self.test_res.set("✘ " + str(ex))

    def apply(self):
        self._collect()
        self.s.save()
        return True


class AskText(_Dialog):
    def __init__(self, master, title, hint):
        super().__init__(master, title)
        self.value = None
        ttk.Label(self.body, text=hint, style="Cap.TLabel", wraplength=520).pack(anchor="w")
        self.txt = tk.Text(self.body, width=70, height=5, font=("Segoe UI", 10))
        self.txt.pack(fill="both", expand=True, pady=4)
        self.finish()

    def apply(self):
        self.value = self.txt.get("1.0", "end").strip()
        return True


class ElementWindow(tk.Toplevel):
    def __init__(self, master, rec, issues):
        super().__init__(master)
        self.title(f"{rec.ifc_class} - {rec.display_name()}")
        self.geometry("820x640")
        txt = tk.Text(self, wrap="none", font=("Consolas", 10), padx=8, pady=6)
        ys = ttk.Scrollbar(self, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        txt.pack(fill="both", expand=True)
        txt.tag_configure("h", font=("Consolas", 11, "bold"), foreground="#1f4e78")
        txt.tag_configure("err", foreground="#b42318")
        txt.tag_configure("warn", foreground="#a15c07")
        txt.tag_configure("empty", foreground="#9aa4b2")

        def line(s="", tag=None):
            txt.insert("end", s + "\n", tag)
        line("THÔNG TIN CHUNG", "h")
        for k, v in (("File", rec.file), ("Lớp IFC", rec.ifc_class), ("PredefinedType", rec.predefined_type),
                     ("GlobalId", rec.guid), ("#STEP", rec.step_id), ("Name", rec.name), ("ObjectType", rec.object_type),
                     ("Description", rec.description), ("Tag", rec.tag), ("Loại (Type)", f"{rec.type_name} [{rec.type_class}]"),
                     ("Không gian chứa", f"{rec.container_name} [{rec.container_class}]"),
                     ("Đối tượng cha", rec.parent_guid), ("Vật liệu", "; ".join(rec.materials)),
                     ("Phân loại", "; ".join(rec.classifications))):
            line(f"  {k:<16}: {v}")
        if issues:
            line()
            line(f"LỖI / CẢNH BÁO ({len(issues)})", "h")
            for i in issues:
                line(f"  [{i.rule_id}] {i.message}" + (f"  (giá trị: {i.actual})" if i.actual else ""),
                     "err" if i.severity == "ERROR" else "warn")
        for pset, props in rec.psets.items():
            line()
            line(f"{pset.strip()}", "h")
            for k, v in props.items():
                s = value_to_str(v)
                line(f"  {k.strip():<34} = {s if s else '(trống)'}", None if s else "empty")
        txt.configure(state="disabled")


class HelpWindow(tk.Toplevel):
    TEXT = """QUY TRÌNH THẨM TRA

1. Hồ sơ IFC: thêm các file IFC cần thẩm tra (nhiều file/nhiều bộ môn cùng lúc).

2. Quy tắc BEP: chuyển yêu cầu thông tin trong BEP/EIR (LOIN) thành bộ quy tắc.
   - Bấm 'Bộ mẫu mặc định' -> 'Lưu thành...' để có file Excel mẫu, chỉnh trong Excel rồi 'Tải lại'.
   - Hoặc dùng AI đọc file BEP (PDF/Word/Excel) để đề xuất quy tắc, sau đó rà soát.
   - Có thể chạy kèm file IDS (chuẩn buildingSMART) nếu chủ đầu tư cung cấp.

   Các loại kiểm tra:
   PROPERTY       Thuộc tính trong Property Set (Pset_WallCommon.FireRating, Pset dự án...)
   ATTRIBUTE      Thuộc tính gốc: Name, Description, ObjectType, Tag, PredefinedType, LongName
   CLASS          Chủng loại IFC (vd: không dùng IfcBuildingElementProxy)
   CONTAINER      Thuộc cấu trúc không gian (Site/Building/Storey/Road...)
   MATERIAL       Có gán vật liệu / vật liệu đúng danh mục
   CLASSIFICATION Mã phân loại (Uniclass, OmniClass, mã dự án)

   Điều kiện: EXISTS, EQUALS, NOT_EQUALS, IN, NOT_IN, REGEX, RANGE (min..max), NUMBER
   Tên Pset/thuộc tính so khớp KHÔNG phân biệt hoa/thường, dấu tiếng Việt và khoảng trắng thừa.

3. Chạy kiểm tra -> xem tab Kết quả: tỷ lệ đạt theo file, theo quy tắc, chi tiết từng đối tượng.
   Nhấp đúp 1 dòng lỗi để xem toàn bộ thuộc tính đối tượng. Chuột phải để copy GlobalId
   (dán vào ô tìm kiếm của Navisworks/Revit/BIMcollab để định vị).

4. Phát hiện sai chủng loại: so sánh tên đối tượng với bảng từ khoá (sheet ClassMapping),
   vd tên chứa 'tường' nhưng xuất là IfcBeam/IfcBuildingElementProxy. AI có thể đề xuất lớp đúng.

5. Xuất báo cáo Excel (kèm HTML) - có thể đính kèm vào báo cáo thẩm tra.

LƯU Ý
- 'Đối tượng đạt' = không vi phạm quy tắc mức ERROR.
- Phần tử con (hình học tách nhỏ thuộc 1 cụm) mặc định không kiểm tra - đổi trong Cài đặt.
- AI chỉ là công cụ hỗ trợ; kết luận thẩm tra do chuyên gia quyết định.

DÒNG LỆNH (chạy hàng loạt)
  python main.py check "D:\\HoSo\\*.ifc" --rules BEP_Rules.xlsx --out BaoCao.xlsx --html BaoCao.html
"""

    def __init__(self, master):
        super().__init__(master)
        self.title("Hướng dẫn")
        self.geometry("860x640")
        t = tk.Text(self, wrap="word", font=("Segoe UI", 10), padx=12, pady=10)
        t.pack(fill="both", expand=True)
        t.insert("1.0", APP_NAME + " " + __version__ + chr(10) + CREDIT + chr(10) * 2 + self.TEXT)
        t.configure(state="disabled")


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
