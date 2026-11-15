"""Lưu cấu hình người dùng tại %APPDATA%\\ThamTraBIM\\settings.json."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields

from .textnorm import DEFAULT_EMPTY_TOKENS


def config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "ThamTraBIM")
    os.makedirs(d, exist_ok=True)
    return d


@dataclass
class Settings:
    api_key: str = ""                 # để trống -> dùng biến môi trường ANTHROPIC_API_KEY
    ai_model: str = "claude-opus-5-5"
    ai_effort: str = "medium"
    ai_consent: bool = False          # người dùng đã đồng ý gửi dữ liệu tóm tắt lên AI
    part_mode: str = "auto"           # auto | parents | all
    check_classification: bool = True
    empty_tokens: list[str] = field(default_factory=lambda: list(DEFAULT_EMPTY_TOKENS))
    last_rules: str = ""
    last_dir: str = ""

    @classmethod
    def load(cls) -> "Settings":
        p = os.path.join(config_dir(), "settings.json")
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            names = {f_.name for f_ in fields(cls)}
            return cls(**{k: v for k, v in data.items() if k in names})
        except Exception:
            return cls()

    def save(self) -> None:
        p = os.path.join(config_dir(), "settings.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    def has_ai_key(self) -> bool:
        return bool(self.api_key.strip() or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
