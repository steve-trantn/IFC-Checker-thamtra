"""Máy chủ cục bộ (chỉ 127.0.0.1) cho trình xem 3D của bản desktop.

Phần mềm desktop đặt dữ liệu cảnh + lệnh "phóng tới"; trang xem 3D trong trình duyệt
hỏi trạng thái mỗi 0,6 giây và gửi lại đối tượng người dùng nháy chọn.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer")
_TYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8"}


class ViewerServer:
    def __init__(self):
        self.key = ""
        self.data = b""
        self.meta_bytes = b"{}"
        self.meta: dict = {}
        self.seq = 0
        self.ids: list[int] = []
        self.clash = ""
        self.picks: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # im lặng
                pass

            def _send(self, code, body: bytes, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?", 1)[0].lstrip("/") or "standalone.html"
                if path == "state":
                    with srv._lock:
                        body = json.dumps({"key": srv.key, "seq": srv.seq, "ids": srv.ids, "clash": srv.clash})
                    return self._send(200, body.encode())
                if path == "meta.json":
                    return self._send(200, srv.meta_bytes)
                if path == "data.bin":
                    return self._send(200, srv.data, "application/octet-stream")
                if path in ("standalone.html", "viewer.js", "three.min.js"):
                    with open(os.path.join(ASSETS, path), "rb") as f:
                        return self._send(200, f.read(), _TYPES[os.path.splitext(path)[1]])
                self._send(404, b"{}")

            def do_POST(self):
                if self.path.startswith("/pick"):
                    n = int(self.headers.get("Content-Length") or 0)
                    try:
                        srv.picks.put(json.loads(self.rfile.read(n) or b"{}"))
                    except Exception:
                        pass
                    return self._send(200, b"{}")
                self._send(404, b"{}")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/standalone.html"

    def set_scene(self, data: bytes, meta: dict, key: str):
        with self._lock:
            self.data = data
            self.meta = meta
            self.meta_bytes = json.dumps(meta, ensure_ascii=False).encode("utf-8")
            self.key = key
            self.seq += 1
            self.ids, self.clash = [], ""

    def focus(self, ids: list[int]):
        with self._lock:
            self.ids, self.clash = list(ids), ""
            self.seq += 1

    def focus_clash(self, clash_id: str):
        with self._lock:
            self.ids, self.clash = [], clash_id
            self.seq += 1

    def open_browser(self):
        webbrowser.open(self.url)

    def shutdown(self):
        try:
            self.httpd.shutdown()
        except Exception:
            pass
