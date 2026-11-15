"""Không gian làm việc của mỗi phiên: mô hình đã mở, bộ quy tắc, kết quả, xung đột, cảnh 3D, tác vụ nền."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field

from ifc_checker.clash import ClashRun, ClashTest, default_matrix
from ifc_checker.engine import CheckResult
from ifc_checker.model_loader import ModelInfo
from ifc_checker.rules import RuleSet, default_ruleset


@dataclass
class Job:
    id: str
    label: str
    status: str = "running"      # running | done | error
    done: int = 0
    total: int = 0
    message: str = ""
    error: str = ""
    result: object = None
    started: float = field(default_factory=time.time)

    def to_dict(self):
        return {"id": self.id, "label": self.label, "status": self.status, "done": self.done, "total": self.total,
                "message": self.message, "error": self.error, "seconds": round(time.time() - self.started, 1)}


class Workspace:
    def __init__(self, sid: str):
        self.sid = sid
        self.models: dict[str, ModelInfo] = {}
        self.ruleset: RuleSet = default_ruleset()
        self.result: CheckResult | None = None
        self.clash_tests: list[ClashTest] = default_matrix()
        self.clash_run: ClashRun | None = None
        self.scene = None              # (data bytes, meta dict, key)
        self.scene_dirty = True
        self.part_mode = "auto"
        self.check_classification = True
        self.api_key = ""
        self.ai_model = "claude-opus-5-5"
        self.ai_summary = ""
        self.jobs: dict[str, Job] = {}
        import hashlib
        tag = hashlib.sha1(sid.encode("utf-8")).hexdigest()[:10]   # sid có thể là email -> không dùng làm tên thư mục
        self.tmpdir = tempfile.mkdtemp(prefix=f"thamtra_{tag}_")
        self.lock = threading.Lock()   # một tác vụ nặng tại một thời điểm
        self.last_used = time.time()

    # ------------------------------------------------------------------ tác vụ nền
    def start_job(self, label: str, fn, on_done=None) -> Job:
        job = Job(id=uuid.uuid4().hex[:10], label=label)
        self.jobs[job.id] = job

        def progress(done, total, msg=""):
            job.done, job.total = done, total
            if msg:
                job.message = msg

        def runner():
            with self.lock:
                try:
                    job.message = label
                    job.result = fn(progress)
                    if on_done:
                        on_done(job.result)
                    job.status = "done"
                    job.message = job.message or "Xong"
                except Exception as ex:  # báo lỗi cho giao diện
                    traceback.print_exc()
                    job.status, job.error = "error", str(ex)
        threading.Thread(target=runner, daemon=True).start()
        return job

    def busy(self) -> bool:
        return any(j.status == "running" for j in self.jobs.values())

    def model_list(self) -> list[ModelInfo]:
        return list(self.models.values())

    def ok_models(self) -> list[ModelInfo]:
        return [m for m in self.models.values() if not m.error]

    def record(self, file_name: str, step_id: int):
        m = self.models.get(file_name)
        if not m:
            return None, None
        for r in m.records:
            if r.step_id == step_id:
                return m, r
        return m, None

    def cleanup(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class WorkspaceStore:
    def __init__(self, max_sessions: int = 8, idle_hours: float = 12):
        self.items: dict[str, Workspace] = {}
        self.max_sessions = max_sessions
        self.idle = idle_hours * 3600
        self._lock = threading.Lock()

    def get(self, sid: str | None) -> Workspace:
        with self._lock:
            now = time.time()
            for k in [k for k, w in self.items.items() if now - w.last_used > self.idle and not w.busy()]:
                self.items.pop(k).cleanup()
            if sid and sid in self.items:
                ws = self.items[sid]
            else:
                if len(self.items) >= self.max_sessions:
                    oldest = min(self.items.values(), key=lambda w: w.last_used)
                    self.items.pop(oldest.sid).cleanup()
                sid = sid or uuid.uuid4().hex
                ws = self.items[sid] = Workspace(sid)
            ws.last_used = now
            return ws


def safe_name(name: str) -> str:
    base = os.path.basename(name or "file.ifc")
    return "".join(ch for ch in base if ch not in '<>:"/\\|?*') or "file.ifc"
