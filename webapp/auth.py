"""Đăng nhập bằng tài khoản Google (OAuth 2.0 / OpenID Connect) + danh sách được phép.

Cấu hình bằng biến môi trường hoặc file config.env ở thư mục gốc (không đưa lên git):
  GOOGLE_CLIENT_ID       Client ID tạo trong Google Cloud Console (loại "Web application")
  GOOGLE_CLIENT_SECRET   Client secret tương ứng
  AUTH_ALLOWED_DOMAINS   Tên miền email được phép, phân cách dấu phẩy (vd: congty.com.vn)
  AUTH_ALLOWED_EMAILS    Email cụ thể được phép, phân cách dấu phẩy (vd: ai@gmail.com)
  AUTH_ADMIN_EMAILS      (tuỳ chọn) email quản trị
  SESSION_SECRET         Chuỗi bí mật để ký cookie phiên (tự sinh nếu bỏ trống - mất phiên khi khởi động lại)
  PUBLIC_URL             Địa chỉ công khai, vd https://thamtra.congty.vn (để tạo redirect URI đúng sau proxy)
Không đặt GOOGLE_CLIENT_ID -> tắt đăng nhập (dùng nội bộ trên 1 máy).
Nếu bật đăng nhập mà không khai báo danh sách được phép -> mọi tài khoản Google đều vào được (không khuyến nghị).
"""

from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime
from urllib.parse import urlencode

import requests
from itsdangerous import BadSignature, URLSafeTimedSerializer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
COOKIE = "tt_user"
SESSION_DAYS = 7


def load_config_file(path: str | None = None) -> None:
    """Đọc config.env (KEY=VALUE) vào biến môi trường, không ghi đè biến đã có."""
    path = path or os.path.join(ROOT, "config.env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _csv(name: str) -> set[str]:
    return {x.strip().lower() for x in os.environ.get(name, "").split(",") if x.strip()}


class Auth:
    def __init__(self):
        load_config_file()
        self.client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
        self.client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
        self.domains = _csv("AUTH_ALLOWED_DOMAINS")
        self.emails = _csv("AUTH_ALLOWED_EMAILS")
        self.admins = _csv("AUTH_ADMIN_EMAILS")
        self.public_url = os.environ.get("PUBLIC_URL", "").rstrip("/")
        secret = os.environ.get("SESSION_SECRET", "").strip() or secrets.token_urlsafe(32)
        self.signer = URLSafeTimedSerializer(secret, salt="thamtra-auth")
        self.log_path = os.path.join(ROOT, "logs", "access.log")

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def describe(self) -> dict:
        return {"enabled": self.enabled, "domains": sorted(self.domains), "emails": len(self.emails),
                "open_to_all": self.enabled and not (self.domains or self.emails)}

    # ------------------------------------------------------------------ phân quyền
    def allowed(self, email: str) -> bool:
        email = (email or "").lower()
        if not (self.domains or self.emails):
            return True
        if email in self.emails or email in self.admins:
            return True
        return email.rsplit("@", 1)[-1] in self.domains

    # ------------------------------------------------------------------ cookie phiên
    def make_cookie(self, user: dict) -> str:
        return self.signer.dumps({k: user.get(k, "") for k in ("email", "name", "picture")})

    def read_cookie(self, value: str | None) -> dict | None:
        if not value:
            return None
        try:
            u = self.signer.loads(value, max_age=SESSION_DAYS * 86400)
        except BadSignature:
            return None
        return u if self.allowed(u.get("email", "")) else None   # bị gỡ khỏi danh sách -> mất quyền ngay

    # ------------------------------------------------------------------ luồng OAuth
    def redirect_uri(self, base_url: str) -> str:
        return (self.public_url or base_url.rstrip("/")) + "/auth/callback"

    def login_url(self, base_url: str, state: str) -> str:
        q = {"client_id": self.client_id, "response_type": "code", "scope": "openid email profile",
             "redirect_uri": self.redirect_uri(base_url), "state": state, "prompt": "select_account"}
        if len(self.domains) == 1 and not self.emails:
            q["hd"] = next(iter(self.domains))      # gợi ý chọn tài khoản công ty
        return AUTH_URL + "?" + urlencode(q)

    def exchange(self, base_url: str, code: str) -> dict:
        r = requests.post(TOKEN_URL, data={
            "code": code, "client_id": self.client_id, "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri(base_url), "grant_type": "authorization_code"}, timeout=20)
        if r.status_code != 200:
            raise ValueError(f"Google từ chối mã đăng nhập ({r.status_code})")
        token = r.json().get("access_token")
        u = requests.get(USERINFO_URL, headers={"Authorization": f"Bearer {token}"}, timeout=20)
        if u.status_code != 200:
            raise ValueError("Không lấy được thông tin tài khoản Google")
        info = u.json()
        if not info.get("email") or not info.get("email_verified", False):
            raise ValueError("Email Google chưa được xác minh")
        return {"email": info["email"].lower(), "name": info.get("name", ""), "picture": info.get("picture", "")}

    # ------------------------------------------------------------------ nhật ký truy cập
    def log(self, event: str, email: str, ip: str = "") -> None:
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "event": event,
                                    "email": email, "ip": ip}, ensure_ascii=False) + "\n")
        except Exception:
            pass


def new_state() -> str:
    return secrets.token_urlsafe(24) + "." + str(int(time.time()))
