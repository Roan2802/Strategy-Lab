"""Background gap filler: while the app runs, fetch ONE missing symbol-day at a time from the
configured Dukascopy provider chain, round-robin over the configured symbols.

It shares the daily worker's lock, validation, partition store and backoff state, so it never
races the scheduled task, never re-downloads a settled day and never sends a request while a
symbol is in backoff.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .daily_data import DailyPartitionStore
from .daily_sync import (DEFAULT_SYNC_CONFIG, _advance_forward, load_sync_config, reference_close,
                         synchronize)
from .data_manager import DEFAULT_DAILY_STORE, DataError

BUSY_INTERVAL_S = 20       # while gaps remain and the last request succeeded
IDLE_INTERVAL_S = 900      # everything up to date, waiting or in backoff
DONE = {"UP TO DATE", "MARKET CLOSED"}
WAKE_SLICE_S = 30          # sleep in slices so a wall-clock jump (laptop sleep) is noticed
RESUME_JUMP_S = 120        # a slice that took this much longer than planned means the machine slept


class Trickle:
    def __init__(self, config_path: Path = DEFAULT_SYNC_CONFIG, store_path: Path = DEFAULT_DAILY_STORE, *,
                 clock=None, wall=None):
        self.config_path = Path(config_path)
        self.store_path = Path(store_path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.wall = wall or time.time
        self.thread: threading.Thread | None = None
        self._next = 0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.status = {"state": "STARTING", "last": None, "filled": 0, "requests": 0, "last_error": None,
                       "next_check_s": BUSY_INTERVAL_S,
                       "heartbeat_at": None, "resumed_at": None}

    def step(self) -> dict:
        """Fetch at most one missing day for the next symbol that has one. Returns a report."""
        try:
            config = load_sync_config(self.config_path)
        except (FileNotFoundError, DataError) as exc:
            return {"state": "NOT_CONFIGURED", "detail": str(exc)}
        if not config.fetch or not config.plans:
            return {"state": "NOT_CONFIGURED", "detail": "Geen datafeed-provider ingesteld"}
        store = DailyPartitionStore(self.store_path)
        lock_path = store.path.with_suffix(".sync-lock.sqlite3")
        with closing(sqlite3.connect(lock_path, timeout=0)) as lock:
            try:
                lock.execute("BEGIN EXCLUSIVE")
            except sqlite3.OperationalError:
                return {"state": "BUSY", "detail": "De dagelijkse taak draait; volgende keer verder"}
            if hasattr(config.provider, "bind_reference"):
                config.provider.bind_reference(
                    lambda symbol: reference_close(store, config.source_directory, symbol, self.clock()))
            plans = config.plans
            held = []
            for offset in range(len(plans)):
                plan = plans[(self._next + offset) % len(plans)]
                reports = synchronize(store, config.provider, plan.symbol, plan.calendar,
                                      first_day=plan.first_day, now=self.clock(), max_days=1, clock=self.clock)
                if not reports:
                    continue  # this symbol has no gap left
                report = reports[-1]
                fetched = "no request sent" not in str(report.get("detail", ""))
                if not fetched:
                    held.append(report)  # in backoff: try the next symbol instead
                    continue
                self._next = (self._next + offset + 1) % len(plans)
                if report["status"] in DONE:
                    forward = _advance_forward(store, config, self.clock())
                    return {"state": "FILLING", "report": report, "fetched": fetched, "forward": forward}
                return {"state": "WAITING", "report": report, "fetched": fetched}
            if held:
                return {"state": "BACKOFF", "report": held[0], "fetched": False}
            return {"state": "UP TO DATE"}

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                result = self.step()
                state = result["state"]
                report = result.get("report") or {}
                if result.get("fetched"):
                    self.status["requests"] += 1
                if report.get("status") in DONE and report.get("status") == "UP TO DATE":
                    self.status["filled"] += 1
                if report.get("status") not in (None, *DONE):
                    self.status["last_error"] = f"{report.get('symbol')} {report.get('day')}: {report.get('detail')}"
                interval = BUSY_INTERVAL_S if state == "FILLING" else IDLE_INTERVAL_S
                if state == "WAITING":
                    interval = BUSY_INTERVAL_S * 3  # a day failed; give the provider a short pause
                self.status.update(state=state, last=report or result.get("detail"), next_check_s=interval,
                                   checked_at=self.clock().isoformat(timespec="seconds"))
            except Exception as exc:  # never let the filler take the server down
                interval = IDLE_INTERVAL_S
                self.status.update(state="ERROR", last_error=f"{type(exc).__name__}: {exc}", next_check_s=interval)
            self.status["heartbeat_at"] = self.clock().isoformat(timespec="seconds")
            self._pause(interval)

    def _pause(self, interval: float) -> bool:
        """Wait on the wall clock. Returns True when woken by a clock jump (sleep/resume) so the next step
        runs at once; the step itself still honours every stored backoff."""
        deadline = self.wall() + interval
        while not self._stop.is_set():
            remaining = deadline - self.wall()
            if remaining <= 0:
                return False
            started = self.wall()
            if self._wake.wait(min(remaining, WAKE_SLICE_S)):
                self._wake.clear()
                return False
            self.status["heartbeat_at"] = self.clock().isoformat(timespec="seconds")
            elapsed = self.wall() - started
            if elapsed > min(remaining, WAKE_SLICE_S) + RESUME_JUMP_S:
                moment = self.clock().isoformat(timespec="seconds")
                self.status["resumed_at"] = moment
                try:
                    from . import data_supervisor
                    data_supervisor.record_event("RESUME", {"at": moment, "gap_s": round(elapsed)})
                except Exception:
                    pass
                return True
        return False

    def nudge(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()


_instance: Trickle | None = None


def start() -> Trickle:
    global _instance
    if _instance is None:
        _instance = Trickle()
        _instance.thread = threading.Thread(target=_instance.run_forever, name="dukascopy-trickle", daemon=True)
        _instance.thread.start()
    return _instance


def current() -> Trickle | None:
    return _instance
