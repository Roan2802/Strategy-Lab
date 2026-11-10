"""v10.1 data acceptance: corrupted partitions, calendar drift, calendar provenance, never-green-when-lagging.
FIXTURE data, mocked transport, virtual clock: nothing here touches the provider or the real store."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
import sqlite3
from pathlib import Path

import pytest

from strategy_lab.daily_data import DailyPartitionStore
from strategy_lab.daily_sync import CALENDAR_SOURCES, SymbolPlan, load_sync_config
from strategy_lab.data_manager import DataError
from test_v10_supervisor import Env, OPEN, WEEKDAYS, env, isolate_default  # noqa: F401  (fixtures)

LOCAL_CONFIG = Path(__file__).resolve().parents[1] / "daily_sync.local.json"


def _corrupt(env, day):
    with closing(sqlite3.connect(env.store.path)) as db, db:
        payload, = db.execute("SELECT payload FROM daily_partitions WHERE day=?", (day,)).fetchone()
        db.execute("UPDATE daily_partitions SET payload=? WHERE day=?", (payload.replace(b"4085", b"4086", 1), day))


def test_corrupt_partition_is_detected_quarantined_and_downloaded_again(env):
    env.feed.publish_range(date(2026, 11, 5), date(2026, 11, 8))
    env.sync()
    good = env.partitions()
    _corrupt(env, "2026-11-06")
    row = env.row()  # status must report it, not crash on the checksum
    assert row["status"] == "ERROR" and "corrupte partitie" in row["reason_nl"] and env.status()["overall"] != "UP_TO_DATE"
    with pytest.raises(DataError, match="Checksum mismatch"):
        env.store.read("XAUUSD", date(2026, 11, 5), date(2026, 11, 8), as_of=env.now)
    env.now += timedelta(minutes=1)
    done = env.supervisor.repair(env.now)
    assert [(q["day"], q["reason"]) for q in done["quarantined"]] == [("2026-11-06", "checksum mismatch")]
    frame, manifests = env.store.read("XAUUSD", date(2026, 11, 5), date(2026, 11, 8), as_of=env.now)
    assert [m["day"] for m in manifests] == ["2026-11-05", "2026-11-07", "2026-11-08"]  # bad day is never used
    row = env.row()
    assert row["lag_days"] == 1 and row["first_missing_day"] == "2026-11-06" and row["status"] != "UP_TO_DATE"
    env.now += timedelta(minutes=1)
    env.sync()  # same published bytes again: the repaired day is stored as a fresh revision with the original checksum
    assert env.row()["status"] == "UP_TO_DATE" and env.row()["lag_days"] == 0
    new = env.partitions()
    assert len(new) == len(good) + 1 and {sha for day, sha in new if day == "2026-11-06"} == {sha for day, sha in good if day == "2026-11-06"}
    assert len(env.store.quarantined()) == 1
    env.store.read("XAUUSD", date(2026, 11, 5), date(2026, 11, 8), as_of=env.now)


def test_partition_that_no_longer_matches_the_calendar_is_quarantined(env):
    env.feed.publish_range(date(2026, 11, 5), date(2026, 11, 6))
    env.sync()
    narrower = type(OPEN)(OPEN.timezone, {d: [["10:00", "10:03"]] for d in range(7)}, {})
    env.plan = SymbolPlan("XAUUSD", env.plan.first_day, 0, narrower)
    env.config = type(env.config)([env.plan], env.config.provider, 31, [], 3, None, env.config.source_directory)
    env.supervisor = env.new_supervisor()
    done = env.supervisor.repair(env.now)["quarantined"]
    assert len(done) == 2 and all("2 extra" in item["reason"] for item in done)
    assert env.row()["lag_days"] == 4  # two quarantined days plus two never downloaded
    assert env.supervisor.repair(env.now)["quarantined"] == []  # idempotent


def test_repair_skips_while_a_sync_holds_the_lock(env):
    env.feed.publish(date(2026, 11, 5))
    env.sync()
    _corrupt(env, "2026-11-05")
    lock = sqlite3.connect(env.store.path.with_suffix(".sync-lock.sqlite3"), timeout=0)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        assert env.supervisor.repair(env.now) == {"skipped": "sync running", "quarantined": []}
    finally:
        lock.close()
    assert len(env.supervisor.repair(env.now)["quarantined"]) == 1


def test_market_holiday_override_is_closed_not_pending(env):
    cal = type(OPEN)(OPEN.timezone, {d: [["10:00", "10:05"]] for d in range(7)}, {"2026-11-06": []})
    env.plan = SymbolPlan("XAUUSD", date(2026, 11, 5), 0, cal, "DERIVED_FROM_DATA")
    env.config = type(env.config)([env.plan], env.config.provider, 31, [], 3, None, env.config.source_directory)
    env.supervisor = env.new_supervisor()
    env.feed.publish(date(2026, 11, 5)); env.feed.publish(date(2026, 11, 7)); env.feed.publish(date(2026, 11, 8))
    result = env.sync()
    assert [r["status"] for r in result["intake"]] == ["UP TO DATE", "MARKET CLOSED", "UP TO DATE", "UP TO DATE"]
    assert env.row()["calendar_source"] == "DERIVED_FROM_DATA" and env.row()["lag_days"] == 0


def test_overall_is_never_green_while_any_day_is_missing(env):
    env.feed.publish(date(2026, 11, 5))
    env.sync()  # 11-06..11-08 still missing, worker and scheduler healthy
    status = env.status()
    assert env.row()["lag_days"] == 3 and status["overall"] in {"ERROR", "PARTIAL"} and status["overall"] != "UP_TO_DATE"
    assert not status["issues"]


def test_real_config_labels_every_calendar_with_its_provenance():
    config = load_sync_config(LOCAL_CONFIG)
    assert {plan.calendar_source for plan in config.plans} <= set(CALENDAR_SOURCES)
    assert all(plan.calendar_source != "ASSUMED" for plan in config.plans)
    raw = json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
    de40 = next(p for p in config.plans if p.symbol in {"DE40", "DEUIDXEUR"})
    assert de40.calendar_source == "DERIVED_FROM_DATA" and raw["symbols"]["DE40"]["timezone"] == "Europe/Berlin"


def test_unknown_calendar_source_is_rejected(tmp_path):
    raw = json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
    raw["symbols"]["DE40"]["calendar_source"] = "GUESS"
    path = tmp_path / "c.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DataError, match="calendar_source"):
        load_sync_config(path)
