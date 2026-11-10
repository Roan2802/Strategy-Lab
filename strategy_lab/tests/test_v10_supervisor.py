"""Scenario tests for the data supervisor. Everything here is FIXTURE data with a mocked HTTP transport and a
virtual clock; no provider request is ever sent and no real data directory is touched."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import lzma
import sqlite3
import struct
from zoneinfo import ZoneInfo

import pytest

from strategy_lab import data_supervisor, trickle
from strategy_lab.daily_data import DailyPartitionStore
from strategy_lab.daily_sync import ChainProvider, MarketCalendar, SymbolPlan, SyncBusy, SyncConfig, run_sync
from strategy_lab.dukascopy_datafeed import DatafeedProvider, FeedClient, RateLimiter, Response, feed_url
from strategy_lab.data_supervisor import Supervisor

START = 10 * 3600
OPEN = MarketCalendar(ZoneInfo("UTC"), {d: [["10:00", "10:05"]] for d in range(7)}, {})
WEEKDAYS = MarketCalendar(ZoneInfo("UTC"), {d: ([["10:00", "10:05"]] if d < 5 else []) for d in range(7)}, {})
MON = datetime(2026, 11, 9, 8, tzinfo=timezone.utc)  # Monday


def bi5(base=4_085_000, count=5):
    rows = [(START + 60 * i, base + i, base + i + 5, base + i - 3, base + i + 9, 1.5) for i in range(count)]
    return lzma.compress(b"".join(struct.pack(">5if", *r) for r in rows), format=lzma.FORMAT_ALONE)


class Feed:
    def __init__(self):
        self.files, self.fail, self.urls = {}, None, []

    def publish(self, day, bid=None, ask=None, symbol="XAUUSD"):
        self.files[feed_url(symbol, day, "BID")] = bid if bid is not None else bi5()
        self.files[feed_url(symbol, day, "ASK")] = ask if ask is not None else bi5(4_085_200)

    def publish_range(self, first, last):
        day = first
        while day <= last:
            self.publish(day)
            day += timedelta(days=1)

    def __call__(self, url):
        self.urls.append(url)
        if self.fail:
            return Response(self.fail, {}, b"")
        return Response(200, {}, self.files[url]) if url in self.files else Response(404, {}, b"")


class Env:
    def __init__(self, tmp, *, first=date(2026, 11, 5), calendar=OPEN, update_at=0, max_days=31, factor=1000, now=MON):
        self.now = now
        self.source = tmp / "source"
        self.source.mkdir()
        for side, close in (("Bid", 4000.5), ("Ask", 4000.7)):
            rows = [f"2026.11.04 12:{i:02d}:00,{close},{close + 1},{close - 1},{close},1" for i in range(5)]
            (self.source / f"XAUUSD_1 Min_{side}_2026.01.01_2026.11.04.csv").write_text(
                "Time (EET),Open,High,Low,Close,Volume\n" + "\n".join(rows) + "\n", encoding="utf-8")
        self.store = DailyPartitionStore(tmp / "daily.sqlite")
        self.feed = Feed()
        clock = [1000.0]
        client = FeedClient(self.feed, RateLimiter(clock=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s)),
                            lambda: self.now)
        provider = DatafeedProvider(client=client, today=lambda: self.now.date(), clock=lambda: self.now,
                                    factors={"XAUUSD": factor})
        self.plan = SymbolPlan("XAUUSD", first, update_at, calendar)
        self.config = SyncConfig([self.plan], ChainProvider([("datafeed", provider)]), max_days, [], 3, None, self.source)
        self.scheduler = {"installed": True, "enabled": True, "state": "Ready", "lastResult": 0, "startWhenAvailable": True,
                          "lastRun": self.now.isoformat(), "nextRun": (self.now + timedelta(minutes=30)).isoformat(),
                          "missed": 0, "triggers": ["time", "logon"]}
        self.worker = {"alive": True, "state": "FILLING", "heartbeat_age_s": 5, "stuck": False}
        self.tmp = tmp
        self.supervisor = self.new_supervisor()

    def new_supervisor(self):
        return Supervisor(self.tmp / "cfg.json", self.tmp / "daily.sqlite", self.tmp / "supervisor.sqlite",
                          clock=lambda: self.now, scheduler_probe=lambda: self.scheduler,
                          worker_probe=lambda now: self.worker, config=self.config)

    def sync(self):
        self.config.provider.methods[0][1].client.files.clear()  # every real run is a fresh process/config load
        return run_sync(self.store, self.config, manual=True, clock=lambda: self.now)

    def status(self):
        return self.supervisor.status(self.now)

    def row(self):
        return self.status()["symbols"][0]

    def partitions(self):
        with closing(sqlite3.connect(self.store.path)) as db:
            return db.execute("SELECT day, sha256 FROM daily_partitions ORDER BY day, observed_at").fetchall()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


@pytest.fixture(autouse=True)
def isolate_default(tmp_path, monkeypatch):
    monkeypatch.setattr(data_supervisor, "_DEFAULT", None)
    monkeypatch.setattr(data_supervisor, "SUPERVISOR_DB", tmp_path / "default_supervisor.sqlite")


def originals(env):
    return {p.name: digest(p) for p in env.source.glob("*.csv")}


def test_normal_daily_sync_reaches_up_to_date_and_never_requests_today(env):
    before = originals(env)
    env.feed.publish_range(date(2026, 11, 5), date(2026, 11, 8))
    result = env.sync()
    assert [r["status"] for r in result["intake"]] == ["UP TO DATE"] * 4 and result["status"] == "COMPLETE"
    assert not any("/2026/10/09/" in url for url in env.feed.urls)
    row = env.row()
    assert row["status"] == "UP_TO_DATE" and row["lag_days"] == 0 and row["expected_day"] == "2026-11-08"
    assert env.status()["overall"] == "UP_TO_DATE"
    assert row["quality"] == {"bid_ask": True, "duplicates": 0, "negative_spread": 0, "scale_ok": True,
                              "incomplete_days": 0, "gaps": 0, "revisions": 0, "utc": True}
    assert originals(env) == before


def test_no_new_data_sends_no_request_and_changes_nothing(env):
    env.feed.publish_range(date(2026, 11, 5), date(2026, 11, 8))
    env.sync()
    sent, parts, sha = len(env.feed.urls), env.partitions(), env.row()["snapshot_sha"]
    env.now += timedelta(hours=3)
    assert env.sync()["intake"] == [] and len(env.feed.urls) == sent
    assert env.partitions() == parts and env.row()["snapshot_sha"] == sha
    assert env.supervisor.needs_fetch(env.now)["fetch"] is False


def test_market_closed_is_not_a_delay(tmp_path):
    env = Env(tmp_path, first=date(2026, 11, 6), calendar=WEEKDAYS)  # Fri; Sat+Sun closed; now Monday
    env.feed.publish(date(2026, 11, 6))
    result = env.sync()
    assert [r["status"] for r in result["intake"]] == ["UP TO DATE", "MARKET CLOSED", "MARKET CLOSED"]
    assert len(env.feed.urls) == 2  # only Friday's two files
    row = env.row()
    assert row["status"] == "MARKET_CLOSED" and row["market_closed"] and row["lag_days"] == 0
    assert row["expected_day"] == "2026-11-06" and env.status()["overall"] == "UP_TO_DATE"


def test_market_closed_before_any_sync_is_still_not_pending(tmp_path):
    env = Env(tmp_path, first=date(2026, 11, 7), calendar=WEEKDAYS)  # first day is a Saturday
    row = env.row()
    assert row["lag_days"] == 0 and row["status"] == "MARKET_CLOSED" and row["expected_day"] is None


def test_day_before_update_time_is_not_yet_expected(tmp_path):
    env = Env(tmp_path, update_at=6 * 60 + 30)  # 06:30 UTC, now 05:00
    env.now = datetime(2026, 11, 9, 5, tzinfo=timezone.utc)
    env.feed.publish_range(date(2026, 11, 5), date(2026, 11, 7))
    env.sync()
    row = env.row()
    assert row["expected_day"] == "2026-11-07" and row["lag_days"] == 0 and row["status"] == "UP_TO_DATE"
    env.now = datetime(2026, 11, 9, 7, tzinfo=timezone.utc)
    assert env.row()["expected_day"] == "2026-11-08" and env.row()["lag_days"] == 1


def test_temporary_503_backs_off_without_hammering_then_recovers(env):
    env.feed.fail = 503
    env.sync()
    sent = len(env.feed.urls)
    assert sent == 3  # one file, one try + two short same-run retries, then stop
    row = env.row()
    assert row["status"] == "DELAYED" and row["backoff_until"] and "serverfout" in row["reason_nl"]
    assert row["backoff"]["failures"] == 1
    assert env.status()["overall"] == "ERROR"  # the only instrument is behind
    assert env.supervisor.needs_fetch(env.now)["fetch"] is False
    env.now += timedelta(minutes=1)
    env.sync()
    assert len(env.feed.urls) == sent  # still inside the 2 minute backoff: no request at all
    env.now += timedelta(minutes=5)
    env.feed.fail = None
    env.feed.publish_range(date(2026, 11, 5), date(2026, 11, 8))
    assert env.sync()["status"] == "COMPLETE"
    row = env.row()
    assert row["status"] == "UP_TO_DATE" and row["backoff_until"] is None and row["lag_days"] == 0


def test_long_outage_escalates_backoff_to_the_ceiling_and_stays_visible(env):
    env.feed.fail = 503
    waits = []
    for _ in range(7):
        env.sync()
        row = env.row()
        waits.append(datetime.fromisoformat(row["backoff_until"]) - env.now)
        env.now = datetime.fromisoformat(row["backoff_until"]) + timedelta(seconds=1)
    assert row["backoff"]["failures"] >= 6
    assert waits == sorted(waits) and max(waits) <= timedelta(hours=1) and waits[-1] == timedelta(hours=1)
    assert env.status()["overall"] == "ERROR" and row["lag_days"] == 4


def test_429_retry_after_is_honoured_and_never_retried_early(env):
    env.feed.files.clear()
    env.feed.fail = 429
    env.sync()
    sent = len(env.feed.urls)
    assert env.row()["status"] == "DELAYED"
    env.now += timedelta(minutes=10)
    env.sync()
    assert len(env.feed.urls) == sent
    assert datetime.fromisoformat(env.row()["backoff_until"]) > env.now


def test_waf_block_is_reported_blocked_and_not_bypassed(env):
    env.feed.fail = 403
    env.sync()
    sent = len(env.feed.urls)
    row = env.row()
    assert row["status"] == "BLOCKED" and "blokkeert" in row["reason_nl"] and sent == 1
    env.now += timedelta(hours=1)
    env.sync()
    assert len(env.feed.urls) == sent  # 6 hour cool-down


def test_sleep_resume_gap_is_detected_and_catch_up_respects_backoff(env):
    assert env.supervisor.tick(1800) is False
    env.now += timedelta(hours=9)  # laptop slept
    assert env.supervisor.tick(1800) is True
    assert env.supervisor.events(1)[0]["kind"] == "RESUME"
    env.feed.publish_range(date(2026, 11, 5), date(2026, 11, 8))
    assert env.supervisor.needs_fetch(env.now)["fetch"] is True
    env.sync()
    assert env.row()["lag_days"] == 0


def test_trickle_wall_clock_jump_wakes_immediately_and_logs_resume(tmp_path, monkeypatch):
    ticks = iter([0, 0, 0, 7200])  # deadline, remaining, started, after-wait: machine slept ~2 h
    monkeypatch.setattr(data_supervisor, "_DEFAULT", Supervisor(tmp_path / "c.json", tmp_path / "s.sqlite",
                                                                tmp_path / "sup.sqlite", config=object()))
    worker = trickle.Trickle(tmp_path / "c.json", tmp_path / "s.sqlite", wall=lambda: next(ticks))
    worker._wake = type("W", (), {"wait": lambda self, timeout: False, "clear": lambda self: None, "set": lambda self: None})()
    assert worker._pause(900) is True
    assert worker.status["resumed_at"]
    assert data_supervisor.default().events(1)[0]["kind"] == "RESUME"


def test_trickle_pause_returns_normally_when_the_clock_is_steady(tmp_path):
    ticks = iter([0, 0, 0, 30, 30, 30, 30, 900, 900])
    worker = trickle.Trickle(tmp_path / "c.json", tmp_path / "s.sqlite", wall=lambda: next(ticks))
    worker._wake = type("W", (), {"wait": lambda self, timeout: False, "clear": lambda self: None, "set": lambda self: None})()
    assert worker._pause(900) is False


def test_scheduler_stopped_or_missing_is_detected(env):
    env.feed.fail = 503
    env.sync()
    env.worker = {"alive": False, "state": "OFF", "heartbeat_age_s": None, "stuck": False}
    env.scheduler = {"installed": True, "enabled": False, "state": "Disabled", "lastResult": 0}
    data = env.status()
    codes = {issue["code"] for issue in data["issues"]}
    assert {"SCHEDULER_DISABLED", "WORKER_DOWN"} <= codes and data["scheduler"]["enabled"] is False
    assert data["overall"] == "ERROR"
    env.scheduler = {"installed": False}
    assert "SCHEDULER_MISSING" in {i["code"] for i in env.status()["issues"]}


def test_scheduler_stale_and_failed_last_result_are_flagged(env):
    env.scheduler = {**env.scheduler, "lastRun": (env.now - timedelta(hours=6)).isoformat()}
    assert "SCHEDULER_STALE" in {i["code"] for i in env.status()["issues"]}
    env.scheduler = {**env.scheduler, "lastRun": env.now.isoformat(), "lastResult": 2147942402}
    data = env.status()
    assert "SCHEDULER_FAILED" in {i["code"] for i in data["issues"]}
    env.scheduler = {**env.scheduler, "lastResult": 1}  # 1 = finished with days still waiting, not a failure
    assert "SCHEDULER_FAILED" not in {i["code"] for i in env.status()["issues"]}
    assert data["scheduler"]["catch_up"] is True


def test_worker_with_old_heartbeat_is_flagged_stuck(env):
    env.worker = {"alive": True, "state": "BACKOFF", "heartbeat_age_s": 99999, "stuck": True}
    assert "WORKER_STUCK" in {i["code"] for i in env.status()["issues"]}


def test_stuck_updater_is_detected_by_dead_run_row_and_by_lock_age(env):
    started = (env.now - timedelta(hours=3)).isoformat()
    with closing(sqlite3.connect(env.store.path)) as db, db:
        db.execute("INSERT INTO daily_sync_runs(started_at, mode, status) VALUES (?, 'SCHEDULED', 'RUNNING')", (started,))
    assert env.status()["updater"]["stuck"] is True  # RUNNING but no process holds the lock
    assert "UPDATER_STUCK" in {i["code"] for i in env.status()["issues"]}


def test_single_downloader_lock_blocks_a_second_sync(env):
    lock = sqlite3.connect(env.store.path.with_suffix(".sync-lock.sqlite3"))
    lock.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(SyncBusy):
            env.sync()
        job = env.supervisor.check_sync(wait=True)
        assert env.supervisor.job_status()["state"] == "BUSY" and job["state"] in {"RUNNING", "BUSY"}
        assert env.status()["updater"]["lock_held"] is True and env.feed.urls == []
    finally:
        lock.close()


def test_app_restart_keeps_state_and_never_redownloads(env):
    env.feed.publish_range(date(2026, 11, 5), date(2026, 11, 6))
    env.feed.fail = None
    env.sync()
    env.supervisor.put("last_check_at", env.now.isoformat())
    sha, sent = env.row()["snapshot_sha"], len(env.feed.urls)
    restarted = env.new_supervisor()
    env.store = DailyPartitionStore(env.store.path)
    assert restarted.get("last_check_at") == env.now.isoformat()
    assert restarted.status(env.now)["symbols"][0]["snapshot_sha"] == sha
    env.feed.fail = 503
    env.sync()
    assert env.row()["backoff_until"]
    env.supervisor = env.new_supervisor()  # restart again: backoff must survive
    assert env.row()["backoff_until"]
    sent = len(env.feed.urls)
    env.sync()
    assert len(env.feed.urls) == sent


def test_missed_days_catch_up_in_one_run(env):
    env.feed.publish_range(date(2026, 11, 5), date(2026, 11, 8))
    env.now = datetime(2026, 11, 9, 8, tzinfo=timezone.utc)
    assert env.row()["lag_days"] == 4 and env.row()["status"] == "CATCHING_UP"
    assert env.sync()["status"] == "COMPLETE"
    assert env.row()["lag_days"] == 0


def test_catch_up_budget_spreads_missed_days_over_several_runs(tmp_path):
    env = Env(tmp_path, max_days=2)
    env.feed.publish_range(date(2026, 11, 5), date(2026, 11, 8))
    env.sync()
    assert env.row()["lag_days"] == 2 and env.row()["status"] == "CATCHING_UP"
    assert env.supervisor.needs_fetch(env.now)["fetch"] is True
    env.sync()
    assert env.row()["lag_days"] == 0


def test_same_day_twice_creates_no_duplicate_and_keeps_snapshot(env):
    env.feed.publish(date(2026, 11, 5))
    env.sync()
    again = env.sync()  # settled day: not requested again
    assert all(r["day"] != "2026-11-05" for r in again["intake"])
    day = date(2026, 11, 5)
    bid, ask = _frames(env, day)
    sha = env.supervisor.dataset_snapshot("XAUUSD", now=env.now)["snapshot_sha"]
    report = env.store.ingest("XAUUSD", day, bid, ask, expected_minutes=OPEN.expected(day), observed_at=env.now)
    assert report["added"] is False
    assert len([p for p in env.partitions() if p[0] == "2026-11-05"]) == 1
    assert env.supervisor.dataset_snapshot("XAUUSD", now=env.now)["snapshot_sha"] == sha


def _frames(env, day, bump=0.0):
    from strategy_lab.dukascopy_datafeed import decode_bi5
    bid, ask = decode_bi5(bi5(), day, 1000), decode_bi5(bi5(4_085_200), day, 1000)
    return bid + bump, ask + bump


def test_provider_revision_changes_snapshot_and_is_counted(env):
    env.feed.publish(date(2026, 11, 5))
    env.sync()
    first = env.supervisor.dataset_snapshot("XAUUSD", now=env.now)
    day = date(2026, 11, 5)
    bid, ask = _frames(env, day, bump=0.5)
    later = env.now + timedelta(hours=1)
    report = env.store.ingest("XAUUSD", day, bid, ask, expected_minutes=OPEN.expected(day), observed_at=later)
    assert report["revision_detected"] is True
    env.now = later
    second = env.supervisor.dataset_snapshot("XAUUSD", now=later)
    assert second["snapshot_sha"] != first["snapshot_sha"] and second["version"].startswith("XAUUSD@2026-11-05#")
    assert env.row()["quality"]["revisions"] == 1
    # a research result pinned to the earlier time still resolves to the earlier snapshot
    assert env.supervisor.dataset_snapshot("XAUUSD", now=later - timedelta(minutes=30))["snapshot_sha"] == first["snapshot_sha"]


def test_incomplete_bid_ask_is_rejected_and_reported(env):
    env.feed.publish(date(2026, 11, 5), ask=bi5(4_085_200, count=4))
    env.sync()
    assert env.partitions() == []
    row = env.row()
    assert row["status"] != "UP_TO_DATE" and row["lag_days"] == 4 and row["quality"]["incomplete_days"] == 1


def test_wrong_point_factor_is_rejected_before_any_partition(tmp_path):
    env = Env(tmp_path, factor=100)  # decoded price 40850 vs last close 4000.5
    env.feed.publish(date(2026, 11, 5))
    env.sync()
    assert env.partitions() == []
    row = env.row()
    assert row["status"] == "ERROR" and "schaal" in row["reason_nl"]


def test_negative_spread_in_storage_is_reported_as_a_quality_error(env):
    day = date(2026, 11, 5)
    bid, ask = _frames(env, day)
    env.store.ingest("XAUUSD", day, bid, ask, expected_minutes=OPEN.expected(day), observed_at=env.now)
    with closing(sqlite3.connect(env.store.path)) as db, db:  # simulate corruption below the ingest guard
        payload, = db.execute("SELECT payload FROM daily_partitions").fetchone()
        rows = json.loads(payload)
        for r in rows:
            r[1], r[6] = r[6] + 1, r[1]  # swap bid/ask open so ask < bid
        bad = json.dumps(rows, separators=(",", ":")).encode("ascii")
        db.execute("UPDATE daily_partitions SET payload=?, sha256=?", (bad, hashlib.sha256(bad).hexdigest()))
    row = env.row()
    assert row["quality"]["negative_spread"] > 0 and row["status"] == "ERROR" and "negatieve spread" in row["reason_nl"]


def test_snapshot_is_deterministic_and_includes_source_identity(env):
    a = env.supervisor.dataset_snapshot("XAUUSD", now=env.now)
    assert a == env.supervisor.dataset_snapshot("XAUUSD", now=env.now)
    assert set(a["source"]) == {"bid", "ask"} and a["partition_days"] == 0
    env.feed.publish(date(2026, 11, 5))
    env.sync()
    b = env.supervisor.dataset_snapshot("XAUUSD", now=env.now)
    assert b["snapshot_sha"] != a["snapshot_sha"] and b["partition_days"] >= 1
    assert data_supervisor.dataset_snapshot  # public hook exists for research code


def test_check_sync_runs_in_the_background_and_reports_a_job(env):
    env.feed.publish_range(date(2026, 11, 5), date(2026, 11, 8))
    job = env.supervisor.check_sync(wait=True)
    done = env.supervisor.job_status()
    assert done["state"] == "DONE" and done["result"]["imported"] == 4 and job["id"] == done["id"]
    assert env.status()["job"]["state"] == "DONE" and env.status()["last_check"]


def test_overall_partial_when_some_instruments_current(tmp_path):
    env = Env(tmp_path)
    second = SymbolPlan("EURUSD", date(2026, 11, 8), 0, OPEN)
    env.config = SyncConfig([env.plan, second], env.config.provider, 31, [], 3, None, env.source)
    env.supervisor = env.new_supervisor()
    with closing(sqlite3.connect(env.store.path)) as db, db:
        db.execute("INSERT INTO intake_attempts(symbol, day, observed_at, status, detail) VALUES "
                   "('EURUSD', '2026-11-08', ?, 'MARKET CLOSED', '{}')", (env.now.isoformat(),))
    data = env.status()
    assert data["overall"] == "PARTIAL" and {s["symbol"]: s["status"] for s in data["symbols"]} == {
        "XAUUSD": "CATCHING_UP", "EURUSD": "UP_TO_DATE"}


def test_api_contract_shape(env):
    data = env.status()
    assert {"overall", "overall_nl", "last_check", "last_success", "scheduler", "worker", "symbols"} <= set(data)
    assert {"installed", "enabled", "last_run", "last_result", "next_run", "catch_up"} <= set(data["scheduler"])
    assert {"alive", "state", "heartbeat_age_s"} <= set(data["worker"])
    assert {"symbol", "last_candle", "expected_day", "lag_days", "market_closed", "status", "reason_nl", "next_attempt",
            "backoff_until", "quality", "snapshot_sha"} <= set(data["symbols"][0])
    assert {"bid_ask", "duplicates", "negative_spread", "scale_ok", "incomplete_days"} <= set(data["symbols"][0]["quality"])


def test_endpoints_expose_status_and_start_a_job(env, monkeypatch):
    from fastapi.testclient import TestClient
    from strategy_lab import server
    monkeypatch.setattr(data_supervisor, "_DEFAULT", env.supervisor)
    client = TestClient(server.app)
    data = client.get("/api/data/supervisor").json()
    assert data["overall"] in {"UP_TO_DATE", "PARTIAL", "ERROR"} and data["symbols"][0]["symbol"] == "XAUUSD"
    assert client.post("/api/data/supervisor/check-sync", content="{}").status_code == 415
    env.feed.publish_range(date(2026, 11, 5), date(2026, 11, 8))
    monkeypatch.setattr(data_supervisor, "run_sync", lambda *a, **k: run_sync(env.store, env.config, manual=True, clock=lambda: env.now))
    reply = client.post("/api/data/supervisor/check-sync", json={})
    assert reply.status_code == 200 and reply.json()["job"]["state"] in {"RUNNING", "DONE"}
