"""Thẩm Tra Mô Hình BIM - điểm khởi chạy.

  python main.py                 -> mở giao diện desktop
  python main.py check ...       -> chạy dòng lệnh (xem: python main.py check -h)
  python main.py template X.xlsx -> tạo file quy tắc mẫu
  streamlit run web_app.py       -> giao diện web
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _log_crash(exc_text: str) -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "ThamTraBIM")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "error.log")
    with open(p, "a", encoding="utf-8") as f:
        f.write(exc_text + "\n")
    return p


def run():
    if len(sys.argv) > 1 and sys.argv[1] in ("check", "template", "-h", "--help"):
        from ifc_checker.cli import main as cli_main
        sys.exit(cli_main(sys.argv[1:]))
    try:
        from ifc_checker.gui import main as gui_main
        gui_main()
    except Exception:
        txt = traceback.format_exc()
        p = _log_crash(txt)
        try:  # báo lỗi bằng hộp thoại (khi chạy bằng pythonw không có cửa sổ console)
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror("Thẩm Tra Mô Hình BIM",
                                 f"Không khởi động được chương trình:\n\n{txt[-1500:]}\n\nNhật ký lỗi: {p}")
        except Exception:
            print(txt, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run()
