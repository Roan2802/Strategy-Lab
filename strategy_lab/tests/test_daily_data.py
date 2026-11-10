from datetime import date, datetime, timezone
import json
import sqlite3
from contextlib import closing
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from strategy_lab import server
from strategy_lab.daily_data import DailyPartitionStore, provider_symbol
from strategy_lab.data_manager import DataError, DataManager
from strategy_lab.daily_sync import JForexExport, MarketCalendar, SyncBusy, SymbolPlan, feed_status, load_sync_config, run_sync, synchronize
from strategy_lab.forward_registry import ForwardRegistry


DAY = date(2026, 10, 16)
FIRST = datetime(2026, 10, 17, 1, tzinfo=timezone.utc)
SECOND = datetime(2026, 10, 18, 1, tzinfo=timezone.utc)
MINUTES = pd.date_range("2026-10-16T12:00Z", periods=3, freq="min")


def quotes(price=100, index=MINUTES):
    return pd.DataFrame({
        "open": price, "high": price + 1, "low": price - 1,
        "close": price, "volume": 1,
    }, index=index)


def test_retries_and_revisions_preserve_first_observed_data(tmp_path):
    path = tmp_path / "daily.sqlite"
    store = DailyPartitionStore(path)
    first = store.ingest("US30", DAY, quotes(), quotes(101), expected_minutes=MINUTES, observed_at=FIRST)
    duplicate = DailyPartitionStore(path).ingest(
        "USA30IDXUSD", DAY, quotes(), quotes(101), expected_minutes=MINUTES, observed_at=SECOND,
    )
    changed = store.ingest(
        "US30", DAY, quotes(102), quotes(103), expected_minutes=MINUTES, observed_at=SECOND,
    )
    assert first["added"] and not duplicate["added"] and changed["revision_detected"]
    assert first["sha256"] == duplicate["sha256"] != changed["sha256"]
    original, evidence = store.read("US30", DAY, DAY, as_of=FIRST)
    latest, _ = store.read("US30", DAY, DAY, as_of=SECOND)
    assert original.index.equals(MINUTES)
    assert original.bid_open.tolist() == [100] * 3
    assert latest.bid_open.tolist() == [102] * 3
    assert evidence[0]["sha256"] == first["sha256"]
    assert provider_symbol("NASDAQ") == "USATECHIDXUSD"


def test_partial_day_invalid_prices_and_calendar_closure(tmp_path):
    store = DailyPartitionStore(tmp_path / "daily.sqlite")
    args = dict(expected_minutes=MINUTES, observed_at=FIRST)
    assert store.ingest("XAUUSD", DAY, quotes(), quotes(101).iloc[1:], **args)["status"] == "INCOMPLETE"
    assert store.ingest("XAUUSD", DAY, quotes(), quotes(99), **args)["status"] == "ERROR"
    assert store.ingest("XAUUSD", DAY, quotes(), quotes(101), expected_minutes=MINUTES,
                        observed_at=MINUTES[-1].to_pydatetime())["status"] == "INCOMPLETE"
    empty = MINUTES[:0]
    assert store.ingest("XAUUSD", DAY, quotes(index=empty), quotes(index=empty),
                        expected_minutes=empty, observed_at=FIRST)["status"] == "MARKET CLOSED"
    assert store.read("XAUUSD", DAY, DAY, as_of=FIRST)[0].empty


def test_interrupted_transaction_never_publishes_a_partition(tmp_path):
    path = tmp_path / "daily.sqlite"
    store = DailyPartitionStore(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""CREATE TRIGGER interrupt_attempt BEFORE INSERT ON intake_attempts
                      BEGIN SELECT RAISE(ABORT, 'simulated interruption'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="simulated interruption"):
        store.ingest("XAUUSD", DAY, quotes(), quotes(101), expected_minutes=MINUTES, observed_at=FIRST)
    assert DailyPartitionStore(path).read("XAUUSD", DAY, DAY, as_of=FIRST)[0].empty


def test_existing_store_and_new_partition_are_queried_together(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    originals = {}
    for side, price in (("Bid", 100), ("Ask", 101)):
        csv = source / f"XAUUSD_1 Min_{side}_legacy.csv"
        csv.write_text(f"Time (EET),Open,High,Low,Close,Volume\n2026.10.16 14:59:00,{price},{price+1},{price-1},{price},1\n")
        originals[csv] = csv.read_bytes()
    path = tmp_path / "daily.sqlite"
    store = DailyPartitionStore(path)
    store.ingest("XAUUSD", DAY, quotes(), quotes(101), expected_minutes=MINUTES, observed_at=FIRST)
    manager = DataManager(source, tmp_path / "cache", daily_store_path=path)
    candles, manifest = manager.candles("XAUUSD", "M1", "2026-10-16T11:59Z", "2026-10-16T12:03Z")
    assert candles.index.tolist() == [MINUTES[0] - pd.Timedelta(minutes=1), *MINUTES]
    assert manifest["daily_partitions"][0]["rows"] == 3
    assert all(csv.read_bytes() == content for csv, content in originals.items())
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE daily_partitions SET payload = ?", (b"[]",))
    with pytest.raises(DataError, match="Checksum mismatch"):
        manager.candles("XAUUSD", "M1")


def test_headless_worker_retries_missing_side_and_catches_up_in_order(tmp_path):
    store = DailyPartitionStore(tmp_path / "daily.sqlite")
    provider = JForexExport(tmp_path)
    weekly = {i: [["12:00", "12:03"]] if i < 5 else [] for i in range(7)}
    calendar = MarketCalendar(ZoneInfo("UTC"), weekly, {})
    now = datetime(2026, 10, 20, 5, tzinfo=timezone.utc)

    def export(day, side, price):
        index = pd.date_range(f"{day}T12:00Z", periods=3, freq="min")
        frame = quotes(price, index)
        frame.index = index.strftime("%Y-%m-%dT%H:%M:%SZ")
        frame.to_csv(tmp_path / f"XAUUSD_{day}_{side}.csv", index_label="time_utc")

    def run():
        return synchronize(store, provider, "XAUUSD", calendar, first_day=DAY, now=now,
                           max_days=4, clock=lambda: now)

    export(DAY, "BID", 100)
    assert [row["status"] for row in run()] == ["DELAYED"]
    export(DAY, "ASK", 101)
    monday = date(2026, 10, 19)
    export(monday, "BID", 100)
    export(monday, "ASK", 101)
    assert [row["status"] for row in run()] == [
        "UP TO DATE", "MARKET CLOSED", "MARKET CLOSED", "UP TO DATE",
    ]
    assert run() == []
    assert len(store.read("XAUUSD", DAY, monday, as_of=now)[0]) == 6


def test_calendar_handles_repeated_hour_and_explicit_holiday():
    weekly = {i: [["02:00", "03:00"]] if i == 6 else [] for i in range(7)}
    calendar = MarketCalendar(ZoneInfo("Europe/Amsterdam"), weekly, {"2026-11-01": []})
    minutes = calendar.expected(date(2026, 10, 25))
    assert len(minutes) == 120 and minutes.is_unique
    assert len(calendar.expected(date(2026, 3, 29))) == 0
    assert calendar.expected(date(2026, 11, 1)).empty


def test_frozen_registration_uses_only_post_registration_first_publications(tmp_path):
    path = tmp_path / "daily.sqlite"
    store = DailyPartitionStore(path)
    registry = ForwardRegistry(store)
    config = {
        "strategy": "LabBreakout", "parameters": {"period": 20}, "indicators": {}, "symbol": "XAUUSD",
        "timeframe": "M1", "data_version": "fixture", "execution_model": "Bid/Ask OHLC",
        "contract": {"point": 0.01}, "starting_capital": 10000, "trade_risk": 0.01,
        "portfolio_risk_limit": 0.02, "news_filter": {"enabled": False},
    }
    frozen_at = MINUTES[1].to_pydatetime() + pd.Timedelta(seconds=30)
    snapshot = registry.freeze(config, {"LabBreakout/__init__.py": b"pass\n"}, frozen_at=frozen_at)
    config["parameters"]["period"] = 40
    store.ingest("XAUUSD", DAY, quotes(), quotes(101), expected_minutes=MINUTES, observed_at=FIRST)
    store.ingest("XAUUSD", DAY, quotes(102), quotes(103), expected_minutes=MINUTES, observed_at=SECOND)
    reopened = ForwardRegistry(DailyPartitionStore(path))
    record = reopened.get(snapshot["id"])
    assert record == snapshot
    assert record["configuration"]["parameters"] == {"period": 20}
    before_publication, _ = reopened.eligible_data(snapshot["id"], as_of=frozen_at)
    assert before_publication.empty
    eligible, manifests = reopened.eligible_data(snapshot["id"], as_of=SECOND)
    assert eligible.index.tolist() == [MINUTES[-1]]
    assert eligible.bid_open.tolist() == [100]
    assert datetime.fromisoformat(manifests[0]["observed_at"]) == FIRST


def configured_sync(tmp_path):
    path = tmp_path / "daily.json"
    path.write_text(json.dumps({
        "export_directory": ".",
        "max_days_per_symbol": 1,
        "symbols": {"XAUUSD": {"first_day": DAY.isoformat(), "timezone": "UTC", "update_at": "02:00",
                               "weekly": {str(day): [["12:00", "12:03"]] for day in range(7)}}},
    }), encoding="utf-8")
    for side, price in (("BID", 100), ("ASK", 101)):
        frame = quotes(price)
        frame.index = MINUTES.strftime("%Y-%m-%dT%H:%M:%SZ")
        frame.to_csv(tmp_path / f"XAUUSD_{DAY}_{side}.csv", index_label="time_utc")
    return path


def test_manual_and_scheduled_runs_share_lock_and_recover_interruption(tmp_path):
    store = DailyPartitionStore(tmp_path / "daily.sqlite")
    config = load_sync_config(configured_sync(tmp_path))
    store.start_sync(now=FIRST, manual=False)
    with closing(sqlite3.connect(store.path.with_suffix(".sync-lock.sqlite3"))) as lock:
        lock.execute("BEGIN EXCLUSIVE")
        with pytest.raises(SyncBusy):
            run_sync(store, config, manual=True, clock=lambda: FIRST)
    assert store.sync_history()[0]["status"] == "RUNNING"

    # At 01:00 UTC the configured schedule has not started. A manual run can proceed.
    assert not run_sync(store, config, clock=lambda: FIRST)["intake"]
    assert store.sync_history()[1]["status"] == "INTERRUPTED"
    assert run_sync(store, config, manual=True, clock=lambda: FIRST)["intake"][0]["added"]
    status = feed_status(store, config, now=FIRST)
    row = status["symbols"][0]
    assert row["status"] == "UP TO DATE"
    assert row["last_candle_at"] == MINUTES[-1].isoformat()
    assert row["last_added_rows"] == 3
    assert row["pending_days"] == 0
    assert row["next_update_at"] == "2026-10-17T02:00:00+00:00"
    assert [item["status"] for item in status["runs"]] == ["COMPLETE", "COMPLETE", "INTERRUPTED"]


def test_schedule_uses_one_wall_clock_threshold_across_dst():
    plan = SymbolPlan("XAUUSD", DAY, 150, MarketCalendar(
        ZoneInfo("Europe/Amsterdam"), {day: [] for day in range(7)}, {},
    ))
    assert plan.next_update(datetime(2026, 3, 29, 0, tzinfo=timezone.utc)).isoformat() == "2026-03-29T01:00:00+00:00"
    second_fold = datetime(2026, 10, 25, 1, 15, tzinfo=timezone.utc)
    assert plan.due(second_fold)
    assert plan.next_update(second_fold).isoformat() == "2026-10-26T01:30:00+00:00"


def test_monitor_and_manual_sync_use_the_same_store(tmp_path, monkeypatch):
    config_path = tmp_path / "missing.json"
    monkeypatch.setattr(server, "DEFAULT_SYNC_CONFIG", config_path)
    monkeypatch.setattr(server, "DEFAULT_DAILY_STORE", tmp_path / "daily.sqlite")
    client = TestClient(server.app, base_url="http://127.0.0.1:8767")
    assert not client.get("/api/data/monitor").json()["configured"]
    assert client.post("/api/data/sync", json={}).status_code == 409
    monkeypatch.setattr(server, "DEFAULT_SYNC_CONFIG", configured_sync(tmp_path))
    assert client.post("/api/data/sync", json={}, headers={"Origin": "https://unrelated.example"}).status_code == 403
    response = client.post("/api/data/sync", json={}, headers={"Origin": "http://127.0.0.1:8767"})
    assert response.status_code == 200, response.text
    assert response.json()["intake"][0]["added"]
    monitor = client.get("/api/data/monitor").json()
    assert monitor["symbols"][0]["last_candle_at"] == MINUTES[-1].isoformat()
    assert monitor["runs"][0]["mode"] == "MANUAL"
