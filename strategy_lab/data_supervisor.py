"""Data supervisor: one honest answer to "is the Dukascopy data current, and if not, why not?".

It only READS the daily partition store, the backoff table, the scheduler and the in-app worker, plus the tail of
the original CSVs (never written). It sends no provider request on its own; ``check_sync`` hands that to the existing
``run_sync`` (single lock, backoff and rate limits unchanged). Status is persisted in its own SQLite file so a
scheduled run with the app closed leaves evidence too.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .daily_data import DailyPartitionStore, provider_symbol
from .daily_sync import (DEFAULT_SYNC_CONFIG, SyncBusy, SyncConfig, SymbolPlan, _last_source_row, load_sync_config,
                         resolve_symbol, run_sync)
from .data_manager import DEFAULT_DAILY_STORE, DISPLAY_ALIASES, DataError
from .dukascopy_datafeed import PRICE_TOLERANCE, FetchState

SUPERVISOR_DB = DEFAULT_DAILY_STORE.with_name("data_supervisor.sqlite3")
TASK_NAME = "Dukascopy Strategy Lab Daily Sync"
SCHEDULER_INTERVAL_S = 30 * 60
STUCK_RUN_S = 2 * 3600            # same as the task's ExecutionTimeLimit
from .dukascopy_datafeed import GLOBAL
FAILING = {"DELAYED", "ERROR", "BLOCKED"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots (
    symbol TEXT NOT NULL, sha TEXT NOT NULL, version TEXT NOT NULL, created_at TEXT NOT NULL, detail TEXT NOT NULL,
    PRIMARY KEY (symbol, sha)
);
"""
_OPEN_DAYS: dict[tuple, bool] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds") if value else None


def _age(then: str | None, now: datetime) -> int | None:
    return max(0, int((now - datetime.fromisoformat(then)).total_seconds())) if then else None


def day_is_open(plan: SymbolPlan, day: date) -> bool:
    """True when the market calendar expects at least one minute on this UTC day."""
    calendar = plan.calendar
    key = (plan.symbol, str(calendar.timezone), repr(sorted(calendar.weekly.items())),
           repr(sorted(calendar.overrides.items())), day)
    if key not in _OPEN_DAYS:
        if len(_OPEN_DAYS) > 20000:
            _OPEN_DAYS.clear()
        _OPEN_DAYS[key] = len(calendar.expected(day)) > 0
    return _OPEN_DAYS[key]


def expected_day(plan: SymbolPlan, now: datetime) -> date | None:
    """Latest complete UTC day that should exist: before today, a session day, and (for yesterday) past update time."""
    now = now.astimezone(timezone.utc)
    day = now.date() - timedelta(days=1)
    if not plan.due(now):
        day -= timedelta(days=1)  # yesterday is only expected once the daily update time has passed
    for _ in range(14):
        if day < plan.first_day:
            return None
        if day_is_open(plan, day):
            return day
        day -= timedelta(days=1)
    return None


def reason_nl(status: str, detail: str | None) -> str:
    text = (detail or "").lower()
    if status == "BLOCKED" or "blocked" in text:
        return "Dukascopy blokkeert verzoeken; wachten op afkoeling, niet omzeild"
    if "429" in text or "pause" in text:
        return "Dukascopy vraagt om een pauze (HTTP 429); wachten op Retry-After"
    if "unreachable" in text or "urlerror" in text or "timeout" in text or "time-out" in text:
        return "Dukascopy niet bereikbaar (netwerk of server); automatisch opnieuw na wachttijd"
    if "http 5" in text:
        return "Dukascopy meldt een serverfout; automatisch opnieuw na wachttijd"
    if "calendar" in text or "outside configured session" in text:
        return "Sessiekalender klopt niet met de aanbieder; controle nodig"
    if "wrong point factor" in text or "price scale" in text:
        return "Prijsschaal wijkt af van de laatste koers; dag geweigerd"
    if "waiting for both" in text or "404" in text or "empty" in text:
        return "Dag nog niet gepubliceerd door Dukascopy; later opnieuw"
    if status == "INCOMPLETE":
        return "Bid/Ask-dag onvolledig; niet geimporteerd"
    if detail:
        return "Import mislukt: " + detail[:140]
    return "Achterstand wordt ingehaald"


class Supervisor:
    def __init__(self, config_path: Path = DEFAULT_SYNC_CONFIG, store_path: Path = DEFAULT_DAILY_STORE,
                 db_path: Path = SUPERVISOR_DB, *, clock=None, scheduler_probe=None, worker_probe=None,
                 config: SyncConfig | None = None):
        self.config_path, self.store_path, self.db_path = Path(config_path), Path(store_path), Path(db_path)
        self.clock = clock or _utcnow
        self.scheduler_probe = scheduler_probe or probe_windows_task
        self.worker_probe = worker_probe or probe_worker
        self._config = config
        self._job: dict | None = None
        self._job_lock = threading.Lock()
        with closing(self._connect()) as db, db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    # ---- persisted small facts -------------------------------------------------------------------------------
    def put(self, key: str, value) -> None:
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?, ?)",
                       (key, json.dumps(value), _iso(self.clock())))

    def get(self, key: str, default=None):
        with closing(self._connect()) as db:
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def event(self, kind: str, detail: dict) -> None:
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO events(at, kind, detail) VALUES (?, ?, ?)",
                       (_iso(self.clock()), kind, json.dumps(detail)))

    def events(self, limit: int = 20) -> list[dict]:
        with closing(self._connect()) as db:
            return [{"at": r["at"], "kind": r["kind"], "detail": json.loads(r["detail"])}
                    for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]

    def config(self) -> SyncConfig:
        return self._config or load_sync_config(self.config_path)

    def store(self) -> DailyPartitionStore:
        return DailyPartitionStore(self.store_path)

    # ---- clock jumps -----------------------------------------------------------------------------------------
    def tick(self, expected_interval_s: int = SCHEDULER_INTERVAL_S) -> bool:
        """Heartbeat for any periodic caller. True when more wall time passed than the caller's own interval
        allows (sleep/hibernate/app was closed): that is a catch-up moment."""
        now = self.clock()
        previous = self.get("last_tick_at")
        self.put("last_tick_at", _iso(now))
        if previous and (now - datetime.fromisoformat(previous)).total_seconds() > 3 * expected_interval_s + 60:
            self.event("RESUME", {"previous_tick": previous, "gap_s": int((now - datetime.fromisoformat(previous)).total_seconds())})
            return True
        return False

    # ---- updater / lock --------------------------------------------------------------------------------------
    def updater(self, store: DailyPartitionStore, now: datetime) -> dict:
        runs = store.sync_history(5)
        running = next((r for r in runs if r["status"] == "RUNNING"), None)
        lock = store.path.with_suffix(".sync-lock.sqlite3")
        locked = False
        if lock.exists():
            with closing(sqlite3.connect(lock, timeout=0)) as probe:
                try:
                    probe.execute("BEGIN EXCLUSIVE")
                    probe.rollback()
                except sqlite3.OperationalError:
                    locked = True
        age = _age(running["started_at"], now) if running else None
        # A RUNNING row without a held lock is a dead process (killed/crashed), a held lock for >2 h is stuck.
        stuck = bool(running and ((not locked) or (age or 0) > STUCK_RUN_S))
        last = next((r for r in runs if r["status"] != "RUNNING"), None)
        return {"running": bool(running and locked), "lock_held": locked, "run_started_at": running["started_at"] if running else None,
                "run_age_s": age, "stuck": stuck, "last_run_status": last["status"] if last else None,
                "last_run_finished_at": last["finished_at"] if last else None}

    # ---- per symbol --------------------------------------------------------------------------------------------
    def _symbol(self, plan: SymbolPlan, store: DailyPartitionStore, holds: list[dict], now: datetime) -> dict:
        source = provider_symbol(plan.symbol)
        expected = expected_day(plan, now)
        yesterday = now.date() - timedelta(days=1)
        closed = not day_is_open(plan, yesterday) if yesterday >= plan.first_day else False
        settled = store.settled_days(plan.symbol, plan.first_day)
        pending = []
        if expected:
            cursor = plan.first_day
            while cursor <= expected:
                if cursor not in settled and day_is_open(plan, cursor):
                    pending.append(cursor)
                cursor += timedelta(days=1)
        first = pending[0] if pending else None
        summary = store.intake_summary(plan.symbol, plan.first_day, first)
        attempt = summary["pending"] or {}
        mark = store.watermark(plan.symbol, as_of=now)
        last_candle = mark["last_candle_at"] if mark else None
        if last_candle is None:
            row = _last_source_row(self.config().source_directory, resolve_symbol(plan.symbol))
            last_candle = row[0].isoformat() if row else None
        mine = [h for h in holds if h["symbol"] == source and first and h["day"] == first.isoformat()]
        hold = (mine or [h for h in holds if h["symbol"] == GLOBAL] or [None])[0] if pending else None
        quality = self.quality(plan, store, now, pending_days=len(pending))
        if not pending:
            status = "MARKET_CLOSED" if closed else "UP_TO_DATE"
            reason = ("Markt gesloten; geen nieuwe dag verwacht" if closed else "Actueel tot de verwachte dag")
            next_attempt = _iso(plan.next_update(now))
        else:
            detail = (hold or {}).get("reason") or attempt.get("detail")
            failed = str(detail or "").startswith("ERROR") or (attempt.get("status") == "ERROR"
                                                              and "no request sent" not in str(attempt.get("detail")))
            if hold and hold["blocked"]:
                status = "BLOCKED"
            elif failed:
                status = "ERROR"
                if attempt.get("status") == "ERROR" and "no request sent" not in str(attempt.get("detail")):
                    detail = attempt.get("detail")
            elif hold:
                status = "DELAYED"
            else:
                status = "CATCHING_UP"
            reason = reason_nl("BLOCKED" if status == "BLOCKED" else attempt.get("status", ""), str(detail or ""))
            if status == "CATCHING_UP" and not attempt:
                reason = "Achterstand wordt ingehaald (een dag per stap)"
            next_attempt = hold["next_attempt_at"] if hold else _iso(now if plan.due(now) else plan.next_update(now))
        if quality["problems"] and status not in FAILING:
            status, reason = "ERROR", "Datakwaliteit: " + ", ".join(quality["problems"])
        last_check = max((a for a in (attempt.get("observed_at"), (summary["latest"] or {}).get("observed_at")) if a),
                         default=None)
        return {"symbol": plan.symbol, "last_candle": last_candle, "expected_day": expected.isoformat() if expected else None,
                "lag_days": len(pending), "first_missing_day": first.isoformat() if first else None,
                "market_closed": closed, "calendar_source": plan.calendar_source, "status": status, "reason_nl": reason, "last_check": last_check,
                "last_success": (summary["success"] or {}).get("observed_at"), "next_attempt": next_attempt,
                "backoff_until": hold["next_attempt_at"] if hold else None,
                "backoff": ({"failures": hold["failures"], "blocked": hold["blocked"], "reason": hold["reason"]} if hold else None),
                "quality": {k: quality[k] for k in ("bid_ask", "duplicates", "negative_spread", "scale_ok",
                                                    "incomplete_days", "gaps", "revisions", "utc")},
                "snapshot_sha": self.dataset_snapshot(plan.symbol, store=store, now=now)["snapshot_sha"]}

    # ---- quality -------------------------------------------------------------------------------------------------
    def quality(self, plan: SymbolPlan, store: DailyPartitionStore, now: datetime, *, pending_days: int = 0) -> dict:
        source = provider_symbol(plan.symbol)
        with closing(store._connect()) as db:
            signature = list(db.execute("SELECT COUNT(*), COALESCE(MAX(observed_at), ''), COALESCE(MAX(rowid), 0), COALESCE(MAX(sha256), '') "
                                        "FROM daily_partitions WHERE symbol=?", (source,)).fetchone())
            signature.append(db.execute("SELECT COALESCE(MAX(id), 0) FROM intake_attempts WHERE symbol=?",
                                        (source,)).fetchone()[0])
            incomplete = db.execute(
                "SELECT COUNT(*) FROM (SELECT day, status, MAX(id) FROM intake_attempts WHERE symbol=? GROUP BY day) "
                "WHERE status IN ('INCOMPLETE')", (source,)).fetchone()[0]
            revisions = db.execute("SELECT COUNT(*) FROM (SELECT day FROM daily_partitions WHERE symbol=? "
                                   "GROUP BY day HAVING COUNT(DISTINCT sha256) > 1)", (source,)).fetchone()[0]
        key = f"quality:{source}"
        cached = self.get(key)
        corrupt = self._corrupt(store, source, signature, now)
        if cached and cached["signature"] == signature and not corrupt:
            result = cached["result"]
        elif corrupt:
            result = {"bid_ask": None, "duplicates": 0, "negative_spread": 0, "scale_ok": None, "utc": None, "days_checked": 0}
        else:
            result = self._measure(plan, store, now, source)
            self.put(key, {"signature": signature, "result": result})
        result = {**result, "incomplete_days": incomplete, "revisions": revisions, "gaps": pending_days,
                  "corrupt_days": corrupt, "quarantined_days": len(store.quarantined(source))}
        result["problems"] = [name for name, bad in (("corrupte partitie", bool(corrupt)),
            ("bid/ask ontbreekt", result["bid_ask"] is False), ("dubbele minuten", result["duplicates"] > 0),
            ("negatieve spread", result["negative_spread"] > 0), ("prijsschaal", result["scale_ok"] is False),
            ("niet-UTC", result["utc"] is False)) if bad]
        return result

    def _corrupt(self, store: DailyPartitionStore, source: str, signature: list, now: datetime) -> list[str]:
        """Days whose newest live revision fails its checksum. Rechecked at most every 10 minutes per data change."""
        key = f"verified:{source}"
        cached = self.get(key)
        if cached and cached["signature"] == signature[:4] and (_age(cached["at"], now) or 0) < 600:
            return cached["corrupt"]
        corrupt = [c["day"] for c in store.corrupt_days(source)]
        self.put(key, {"signature": signature[:4], "at": _iso(now), "corrupt": corrupt})
        return corrupt

    def repair(self, now: datetime | None = None) -> dict:
        """Quarantine newest revisions that fail their checksum or no longer match the configured calendar, so the
        next sync downloads those days again. Needs the sync lock; skipped (not an error) while a sync runs."""
        now = (now or self.clock()).astimezone(timezone.utc)
        store, config = self.store(), self.config()
        lock_path = store.path.with_suffix(".sync-lock.sqlite3")
        with closing(sqlite3.connect(lock_path, timeout=0)) as lock:
            try:
                lock.execute("BEGIN EXCLUSIVE")
            except sqlite3.OperationalError:
                return {"skipped": "sync running", "quarantined": []}
            done = []
            plans = {provider_symbol(plan.symbol): plan for plan in config.plans}
            for record in store.live_partitions():
                plan = plans.get(record["symbol"])
                reason = None
                if hashlib.sha256(record["payload"]).hexdigest() != record["sha256"]:
                    reason = "checksum mismatch"
                elif plan is not None:
                    memo_key = f"conform:{record['symbol']}"
                    memo = self.get(memo_key) or {}
                    calendar_id = repr((str(plan.calendar.timezone), sorted(plan.calendar.weekly.items()),
                                        sorted(plan.calendar.overrides.items())))
                    if memo.get("calendar") != calendar_id:
                        memo = {"calendar": calendar_id, "ok": {}}
                    if memo["ok"].get(record["day"]) != record["sha256"]:
                        stamps = {int(row[0]) for row in json.loads(record["payload"])}
                        expected = {int(t.timestamp()) for t in plan.calendar.expected(date.fromisoformat(record["day"]))}
                        if stamps != expected:
                            reason = (f"bars differ from the configured calendar ({len(stamps - expected)} extra, "
                                      f"{len(expected - stamps)} missing)")
                        else:
                            memo["ok"][record["day"]] = record["sha256"]
                            self.put(memo_key, memo)
                if reason:
                    store.quarantine(record["symbol"], record["day"], record["observed_at"], reason, at=now)
                    done.append({"symbol": record["symbol"], "day": record["day"], "reason": reason})
            if done:
                self.event("PARTITIONS_QUARANTINED", {"days": done})
            return {"skipped": None, "quarantined": done}

    def _measure(self, plan: SymbolPlan, store: DailyPartitionStore, now: datetime, source: str) -> dict:
        with closing(store._connect()) as db:
            bounds = db.execute("SELECT MIN(day), MAX(day) FROM daily_partitions WHERE symbol=?", (source,)).fetchone()
        if not bounds[0]:
            return {"bid_ask": None, "duplicates": 0, "negative_spread": 0, "scale_ok": None, "utc": None, "days_checked": 0}
        frame, manifests = store.read(plan.symbol, date.fromisoformat(bounds[0]), date.fromisoformat(bounds[1]), as_of=now)
        values = frame.to_numpy(dtype="float64")
        both = bool(len(frame)) and not frame.isna().any().any() and not (frame.filter(like="_open") <= 0).any().any()
        negative = int(((frame.ask_open < frame.bid_open) | (frame.ask_close < frame.bid_close)).sum())
        reference = _last_source_row(self.config().source_directory, resolve_symbol(plan.symbol))
        scale_ok = None
        if len(frame):
            anchor = reference[1] if reference else float(frame.bid_close.median())
            scale_ok = bool(float(frame.bid_close.min()) >= anchor * (1 - PRICE_TOLERANCE)
                            and float(frame.bid_close.max()) <= anchor * (1 + PRICE_TOLERANCE))
        return {"bid_ask": both if len(frame) else None, "duplicates": int(frame.index.duplicated().sum()),
                "negative_spread": negative, "scale_ok": scale_ok,
                "utc": bool(str(frame.index.tz) == "UTC") if len(frame) else None, "days_checked": len(manifests)}

    # ---- snapshot ------------------------------------------------------------------------------------------------
    def dataset_snapshot(self, symbol: str, *, store: DailyPartitionStore | None = None, now: datetime | None = None) -> dict:
        """Deterministic identity of the data a research result was computed on: original CSV identity plus the
        newest revision hash of every daily partition. Same data -> same sha; any new/revised day changes it."""
        store = store or self.store()
        now = now or self.clock()
        source = provider_symbol(symbol)
        cutoff = now.astimezone(timezone.utc).isoformat(timespec="microseconds")
        with closing(store._connect()) as db:
            parts = [(r["day"], r["sha256"]) for r in db.execute(
                "SELECT day, sha256 FROM (SELECT day, sha256, ROW_NUMBER() OVER (PARTITION BY day ORDER BY observed_at DESC, "
                "rowid DESC) AS rank FROM daily_partitions WHERE symbol=? AND observed_at<=?) WHERE rank=1 ORDER BY day",
                (source, cutoff))]
            rows = db.execute("SELECT COALESCE(SUM(rows), 0) FROM (SELECT rows, ROW_NUMBER() OVER (PARTITION BY day ORDER BY "
                              "observed_at DESC, rowid DESC) AS rank FROM daily_partitions WHERE symbol=? AND observed_at<=?) "
                              "WHERE rank=1", (source, cutoff)).fetchone()[0]
        origin = self._source_identity(resolve_symbol(symbol))
        body = {"symbol": source, "source": origin, "partitions": parts}
        sha = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        last_day = parts[-1][0] if parts else (origin or {}).get("last_day")
        snapshot = {"symbol": source, "snapshot_sha": sha, "version": f"{source}@{last_day or 'source'}#{sha[:10]}",
                    "partition_days": len(parts), "partition_rows": int(rows), "source": origin, "last_partition_day": parts[-1][0] if parts else None,
                    "created_at": _iso(now)}
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO snapshots VALUES (?, ?, ?, ?, ?)",
                       (source, sha, snapshot["version"], snapshot["created_at"], json.dumps(snapshot)))
        return snapshot

    def _source_identity(self, source_symbol: str) -> dict | None:
        directory = self.config().source_directory
        if not directory.is_dir():
            return None
        identity = {}
        for side in ("Bid", "Ask"):
            matches = [p for p in directory.glob("*.csv") if p.name.upper().startswith(f"{source_symbol.upper()}_1 MIN_{side.upper()}_")]
            if not matches:
                continue
            path = max(matches, key=lambda p: p.stat().st_size)
            stat = path.stat()
            identity[side.lower()] = {"file": path.name, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                                      "sha256": self._manifest_sha(source_symbol, side.lower(), stat)}
        return identity or None

    def _manifest_sha(self, source_symbol: str, side: str, stat) -> str | None:
        cache = self.store_path.parent
        for name in {source_symbol, DISPLAY_ALIASES.get(source_symbol, source_symbol)}:
            path = cache / f"{name}_M1_bidask_manifest.json"
            if path.is_file():
                try:
                    entry = json.loads(path.read_text(encoding="utf-8")).get(f"{side}_source") or {}
                except (OSError, ValueError):
                    continue
                if entry.get("bytes") == stat.st_size and entry.get("mtime_ns") == stat.st_mtime_ns:
                    return entry.get("sha256")
        return None

    # ---- whole picture -------------------------------------------------------------------------------------------
    def status(self, now: datetime | None = None) -> dict:
        now = (now or self.clock()).astimezone(timezone.utc)
        store = self.store()
        config = self.config()
        holds = FetchState(store.path).active_holds(now)
        symbols = [self._symbol(plan, store, holds, now) for plan in config.plans]
        scheduler = self._scheduler(store, now)
        worker = self.worker_probe(now)
        updater = self.updater(store, now)
        lagging = [s for s in symbols if s["status"] in FAILING | {"CATCHING_UP"}]
        current = [s for s in symbols if s["status"] in {"UP_TO_DATE", "MARKET_CLOSED"}]
        issues = []
        if not scheduler["installed"]:
            issues.append({"code": "SCHEDULER_MISSING", "nl": "Geplande taak niet geinstalleerd"})
        elif not scheduler["enabled"]:
            issues.append({"code": "SCHEDULER_DISABLED", "nl": "Geplande taak staat uitgeschakeld"})
        elif scheduler["stale"]:
            issues.append({"code": "SCHEDULER_STALE", "nl": "Geplande taak draait niet meer volgens schema"})
        elif scheduler["last_result_class"] == "FAILED":
            issues.append({"code": "SCHEDULER_FAILED", "nl": "Laatste geplande run is mislukt: " + scheduler["last_result_nl"]})
        if not worker["alive"]:
            issues.append({"code": "WORKER_DOWN", "nl": "Achtergrondworker in de app draait niet"})
        elif worker["stuck"]:
            issues.append({"code": "WORKER_STUCK", "nl": "Achtergrondworker reageert niet (hartslag te oud)"})
        if updater["stuck"]:
            issues.append({"code": "UPDATER_STUCK", "nl": "Een synchronisatie lijkt vast te zitten of is afgebroken"})
        nobody = lagging and not worker["alive"] and not (scheduler["installed"] and scheduler["enabled"])
        if not lagging:
            overall, text = "UP_TO_DATE", "Alle data is actueel tot de verwachte dag."
        elif not current or nobody or updater["stuck"]:
            overall, text = "ERROR", f"Geen enkel instrument is actueel of er werkt niets meer aan de achterstand ({len(lagging)} achter)."
        else:
            overall, text = "PARTIAL", f"{len(current)} van {len(symbols)} instrumenten actueel, {len(lagging)} lopen achter."
        last_success = max((s["last_success"] for s in symbols if s["last_success"]), default=None)
        last_check = max([c for c in (self.get("last_check_at"), updater["last_run_finished_at"], worker.get("checked_at"),
                                      *(s["last_check"] for s in symbols)) if c], default=None)
        result = {"overall": overall, "overall_nl": text, "computed_at": _iso(now), "last_check": last_check,
                  "last_success": last_success, "scheduler": scheduler, "worker": worker, "updater": updater,
                  "issues": issues, "symbols": symbols, "events": self.events(5), "job": self.job_status()}
        self.put("last_status", {k: result[k] for k in ("overall", "overall_nl", "computed_at", "issues")})
        return result

    def _scheduler(self, store: DailyPartitionStore, now: datetime) -> dict:
        info = self.scheduler_probe() or {}
        installed = bool(info.get("installed"))
        code = info.get("lastResult")
        klass, nl = classify_task_result(code)
        last_run = info.get("lastRun")
        age = _age(last_run, now)
        stale = bool(installed and info.get("enabled") and age is not None and age > 3 * SCHEDULER_INTERVAL_S + 600)
        return {"installed": installed, "enabled": bool(info.get("enabled")), "state": info.get("state"),
                "last_run": last_run, "last_run_age_s": age, "last_result": code, "last_result_class": klass,
                "last_result_nl": nl, "next_run": info.get("nextRun"), "catch_up": bool(info.get("startWhenAvailable")),
                "wake_to_run": bool(info.get("wake")), "missed_runs": info.get("missed"), "triggers": info.get("triggers"),
                "stale": stale, "runs_when_app_closed": installed, "error": info.get("error")}

    # ---- check + sync job ------------------------------------------------------------------------------------------
    def check_sync(self, *, wait: bool = False) -> dict:
        """Run a status check and, in the background, one sync that respects every stored backoff."""
        self.put("last_check_at", _iso(self.clock()))
        with self._job_lock:
            if self._job and self._job["state"] == "RUNNING":
                return {**self._job, "already_running": True}
            self._job = {"id": f"job-{int(self.clock().timestamp())}", "state": "RUNNING", "started_at": _iso(self.clock()),
                         "finished_at": None, "result": None}
            job = self._job
        thread = threading.Thread(target=self._run_job, args=(job,), name="data-supervisor-sync", daemon=True)
        thread.start()
        if wait:
            thread.join()
        return dict(job)

    def _run_job(self, job: dict) -> None:
        try:
            result = run_sync(self.store(), self.config(), manual=True, clock=self.clock)
            statuses = [i.get("status") for i in result["intake"]]
            held = sum(1 for i in result["intake"] if "no request sent" in str(i.get("detail", "")))
            job.update(state="DONE", result={"status": result["status"], "days": len(statuses), "imported":
                       statuses.count("UP TO DATE"), "held_by_backoff": held, "failed": [i for i in result["intake"]
                       if i.get("status") not in ("UP TO DATE", "MARKET CLOSED")][:5]})
        except SyncBusy:
            job.update(state="BUSY", result={"detail": "Er loopt al een synchronisatie"})
        except Exception as exc:
            job.update(state="ERROR", result={"detail": f"{type(exc).__name__}: {exc}"})
        job["finished_at"] = _iso(self.clock())
        self.put("last_job", job)

    def job_status(self) -> dict | None:
        return dict(self._job) if self._job else self.get("last_job")

    def needs_fetch(self, now: datetime | None = None) -> dict:
        """Gate for scheduled runs: fetch only when a complete session day is missing, its update time has passed
        and no backoff covers it."""
        now = (now or self.clock()).astimezone(timezone.utc)
        data = self.status(now)
        wanted = [s["symbol"] for s in data["symbols"] if s["lag_days"] and s["status"] in {"CATCHING_UP", "ERROR"}
                  and not s["backoff_until"]]
        inbox = self.config().inbox
        waiting = bool(inbox and inbox.directory.is_dir() and any(inbox.directory.glob("*.csv")))
        return {"fetch": bool(wanted) or waiting, "symbols": wanted, "inbox_files": waiting, "overall": data["overall"]}


def classify_task_result(code) -> tuple[str, str]:
    if code is None:
        return "UNKNOWN", "onbekend"
    code = int(code) & 0xFFFFFFFF
    table = {0: ("OK", "geslaagd"), 1: ("RETRY", "afgerond met openstaande dagen (herhaalt zichzelf)"),
             267009: ("RUNNING", "draait nu"), 267011: ("NEVER", "nog niet gedraaid"),
             267014: ("FAILED", "taak is beeindigd (tijdslimiet of handmatig gestopt)"),
             2147942402: ("FAILED", "bestand niet gevonden (Python of script ontbreekt)")}
    return table.get(code, ("FAILED", f"foutcode 0x{code:X}"))


_PROBE_CACHE: dict = {"at": 0.0, "value": None}


def probe_windows_task(name: str = TASK_NAME, *, max_age_s: float = 120.0) -> dict:
    """State of the Windows scheduled task (cached; one PowerShell call)."""
    if time.monotonic() - _PROBE_CACHE["at"] < max_age_s and _PROBE_CACHE["value"] is not None:
        return _PROBE_CACHE["value"]
    if sys.platform != "win32":
        return {"installed": False, "error": "geen Windows"}
    script = (
        f"$t=Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue; if(-not $t){{'{{}}';exit}};"
        "$i=$t|Get-ScheduledTaskInfo;"
        "function iso($d){ if($d -and $d.Year -gt 2000){$d.ToUniversalTime().ToString('o')}else{$null} };"
        "[pscustomobject]@{installed=$true;state=[string]$t.State;enabled=([string]$t.State -ne 'Disabled');"
        "startWhenAvailable=[bool]$t.Settings.StartWhenAvailable;wake=[bool]$t.Settings.WakeToRun;"
        "lastRun=(iso $i.LastRunTime);nextRun=(iso $i.NextRunTime);lastResult=[int64]$i.LastTaskResult;"
        "missed=[int]$i.NumberOfMissedRuns;triggers=@($t.Triggers|ForEach-Object{$_.CimClass.CimClassName})}"
        "|ConvertTo-Json -Compress")
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                              text=True, timeout=25, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        value = json.loads(done.stdout.strip() or "{}")
        value.setdefault("installed", False)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        value = {"installed": False, "error": f"{type(exc).__name__}: {exc}"}
    _PROBE_CACHE.update(at=time.monotonic(), value=value)
    return value


def probe_worker(now: datetime) -> dict:
    """Health of the in-app trickle thread: alive, state, and heartbeat age against its own next-check interval."""
    from . import trickle
    instance = trickle.current()
    if instance is None:
        return {"alive": False, "state": "OFF", "heartbeat_age_s": None, "stuck": False}
    status = dict(instance.status)
    alive = bool(instance.thread.is_alive()) if instance.thread else True
    age = _age(status.get("heartbeat_at"), now)
    limit = 2 * int(status.get("next_check_s") or 900) + 600  # a step can take minutes (1 request/s, 2 files/day)
    return {"alive": alive, "state": status.get("state"), "heartbeat_age_s": age,
            "stuck": bool(alive and age is not None and age > limit), "requests": status.get("requests"),
            "filled": status.get("filled"), "last_error": status.get("last_error"), "checked_at": status.get("checked_at"),
            "resumed_at": status.get("resumed_at"), "next_check_s": status.get("next_check_s")}


_DEFAULT: Supervisor | None = None


def default() -> Supervisor:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Supervisor()
    return _DEFAULT


def dataset_snapshot(symbol: str) -> dict:
    """Public hook for research code: ``snapshot_sha``/``version`` identify the exact data in use."""
    return default().dataset_snapshot(symbol)


def record_event(kind: str, detail: dict) -> None:
    default().event(kind, detail)
