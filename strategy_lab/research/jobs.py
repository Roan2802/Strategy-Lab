"""In-process background job runner for long research tasks (thread based, results kept in memory)."""
from __future__ import annotations

import queue
import threading
import traceback
from datetime import datetime, timezone
from uuid import uuid4

MAX_JOBS = 50
FINISHED = {"done", "error", "cancelled"}


class JobCancelled(Exception):
    pass


class JobContext:
    def __init__(self, job):
        self._job = job

    def progress(self, fraction, message=""):
        self._job["progress"] = min(1.0, max(0.0, float(fraction)))
        if message:
            self._job["message"] = str(message)

    @property
    def cancelled(self) -> bool:
        return self._job["_cancel"].is_set()

    def check(self):
        if self.cancelled:
            raise JobCancelled()


def _now():
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    def __init__(self, max_concurrent=2):
        self._jobs = {}
        self._order = []
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._max = max(1, int(max_concurrent))
        self._workers = []

    def _ensure_workers(self):
        with self._lock:
            while len(self._workers) < self._max:
                worker = threading.Thread(target=self._work, daemon=True,
                                          name=f"research-job-{len(self._workers)}")
                self._workers.append(worker)
                worker.start()

    def submit(self, kind, title, fn, *args, timeout_s=None, **kwargs) -> str:
        job = {"id": uuid4().hex[:12], "kind": kind, "title": title, "status": "queued", "progress": 0.0,
               "message": "", "started_at": None, "finished_at": None, "result": None, "error": None,
               "_cancel": threading.Event(), "_timeout": timeout_s, "_timed_out": False,
               "_call": (fn, args, kwargs)}
        with self._lock:
            self._jobs[job["id"]] = job
            self._order.append(job["id"])
            self._trim()
        self._ensure_workers()
        self._queue.put(job["id"])
        return job["id"]

    def _trim(self):
        while len(self._order) > MAX_JOBS:
            victim = next((i for i in self._order if self._jobs[i]["status"] in FINISHED), None)
            if victim is None:
                return
            self._order.remove(victim)
            del self._jobs[victim]

    def _work(self):
        while True:
            job = self._jobs.get(self._queue.get())
            if job is None or job["status"] != "queued":
                continue
            job["status"], job["started_at"] = "running", _now()
            timer = None
            if job["_timeout"]:
                def expire(job=job):
                    job["_timed_out"] = True
                    job["_cancel"].set()
                timer = threading.Timer(float(job["_timeout"]), expire)
                timer.daemon = True
                timer.start()
            fn, args, kwargs = job["_call"]
            result = None
            try:
                result = fn(JobContext(job), *args, **kwargs)
                outcome = ("error", "TIMEOUT") if job["_timed_out"] else ("done", None)
            except JobCancelled:
                outcome = ("error", "TIMEOUT") if job["_timed_out"] else ("cancelled", None)
            except Exception as exc:
                outcome = ("error", f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}")
            finally:
                if timer:
                    timer.cancel()
            if outcome[0] == "done":
                job["result"], job["progress"] = result, 1.0
            job["error"] = outcome[1]
            job["finished_at"] = _now()
            job["_call"] = None
            job["status"] = outcome[0]
            with self._lock:
                self._trim()

    @staticmethod
    def _view(job, with_result):
        view = {k: v for k, v in job.items() if not k.startswith("_")}
        if not with_result or view["status"] != "done":
            view.pop("result", None)
        return view

    def get(self, job_id):
        job = self._jobs.get(job_id)
        return self._view(job, True) if job else None

    def list(self, limit=20) -> list[dict]:
        with self._lock:
            ids = list(reversed(self._order))[:max(1, int(limit))]
            return [self._view(self._jobs[i], False) for i in ids]

    def cancel(self, job_id) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job["status"] in FINISHED:
            return False
        job["_cancel"].set()
        if job["status"] == "queued":
            job["status"], job["finished_at"], job["_call"] = "cancelled", _now(), None
        return True
