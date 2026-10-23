"""Background jobs with progress, polled or streamed by the API."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from typing import Callable


class Job:
    def __init__(self, kind: str, label: str = ""):
        self.id = uuid.uuid4().hex[:8]
        self.kind, self.label = kind, label
        self.state = "running"          # running | done | error | cancelled
        self.done = 0
        self.total = 0
        self.message = ""
        self.error = None
        self.result = None
        self.started = time.time()
        self.finished = None
        self._cancel = threading.Event()

    def progress(self, done, total, message=""):
        self.done, self.total, self.message = done, total, message

    def cancelled(self):
        return self._cancel.is_set()

    def to_dict(self):
        elapsed = (self.finished or time.time()) - self.started
        eta = (elapsed / self.done) * (self.total - self.done) if self.done and self.total and self.state == "running" else None
        return {"id": self.id, "kind": self.kind, "label": self.label, "state": self.state, "done": self.done,
                "total": self.total, "message": self.message, "error": self.error, "result": self.result,
                "elapsed": elapsed, "eta": eta}


class Jobs:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, kind: str, fn: Callable[[Job], object], label: str = "") -> Job:
        job = Job(kind, label)
        with self._lock:
            self._jobs[job.id] = job

        def run():
            try:
                job.result = fn(job)
                job.state = "cancelled" if job.cancelled() else "done"
            except Exception as e:                          # shown to the user, never swallowed
                job.state, job.error = "error", f"{type(e).__name__}: {e}"
                traceback.print_exc()
            job.finished = time.time()

        threading.Thread(target=run, daemon=True, name=f"job-{kind}").start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str):
        if (j := self._jobs.get(job_id)):
            j._cancel.set()

    def active(self):
        return [j.to_dict() for j in self._jobs.values() if j.state == "running"]

    def recent(self, n=10):
        return [j.to_dict() for j in sorted(self._jobs.values(), key=lambda j: -j.started)[:n]]
