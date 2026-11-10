from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import sqlite3
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from strategy_lab.daily_data import DailyPartitionStore
from strategy_lab.daily_sync import (
    Inbox, MarketCalendar, SymbolPlan, _decode_times, load_sync_config, process_inbox, run_sync,
)
from strategy_lab.data_manager import DataManager

CALENDAR = MarketCalendar(ZoneInfo("UTC"), {day: [["10:00", "10:05"]] for day in range(7)}, {})
PLAN = SymbolPlan("XAUUSD", date(2026, 1, 1), 0, CALENDAR)
NOW1 = datetime(2026, 2, 1, tzinfo=timezone.utc)
NOW2 = datetime(2026, 2, 2, tzinfo=timezone.utc)


def minutes(days, count=5):
    return [pd.Timestamp(f"2026-01-{day:02d}T10:00Z") + pd.Timedelta(minutes=i) for day in days for i in range(count)]


def write_export(directory, side, stamps, price=100.0, header="Time (EET)", symbol="XAUUSD",
                 first="2026.01.01", last="2026.01.31", tail="", spread=0.0, mutate=None):
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    for stamp in stamps:
        value = price + (spread if side == "Ask" else 0)
        if mutate:
            value = mutate(stamp, value)
        shown = stamp.tz_convert("Europe/Helsinki") if "EET" in header else stamp
        rows.append(f"{shown:%Y.%m.%d %H:%M:%S},{value},{value + 1},{value - 1},{value},5")
    path = directory / f"{symbol}_1 Min_{side}_{first}_{last}{tail}.csv"
    path.write_text(f"{header},Open,High,Low,Close,Volume\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def export_pair(inbox, stamps, price=100.0, **kw):
    write_export(inbox, "Bid", stamps, price, **kw)
    write_export(inbox, "Ask", stamps, price, spread=0.5, **kw)


def partitions(store):
    with closing(sqlite3.connect(store.path)) as db:
        return db.execute("SELECT day, sha256 FROM daily_partitions ORDER BY day, observed_at").fetchall()


@pytest.fixture
def env(tmp_path):
    inbox = Inbox(tmp_path / "inbox", tmp_path / "source")
    return inbox, DailyPartitionStore(tmp_path / "daily.sqlite")


def test_multiday_export_splits_into_full_days_and_last_day_is_incomplete(env):
    inbox, store = env
    export_pair(inbox.directory, minutes([5, 6, 7]))
    reports, events = process_inbox(store, inbox, [PLAN], clock=lambda: NOW1)
    assert [r["status"] for r in reports] == ["UP TO DATE", "UP TO DATE", "INCOMPLETE"]
    assert [d for d, _ in partitions(store)] == ["2026-01-05", "2026-01-06"]
    assert [e["status"] for e in events] == ["PROCESSED"]
    frame, _ = store.read("XAUUSD", date(2026, 1, 5), date(2026, 1, 6), as_of=NOW1)
    assert len(frame) == 10 and frame.index[0] == pd.Timestamp("2026-01-05T10:00Z")
    assert (frame.ask_open - frame.bid_open == 0.5).all()


def test_files_move_only_after_success_and_stay_on_failure(env):
    inbox, store = env
    export_pair(inbox.directory, minutes([5, 6]))
    process_inbox(store, inbox, [PLAN], clock=lambda: NOW1)
    assert len(list((inbox.directory / "processed").glob("*/*.csv"))) == 2
    assert not list(inbox.directory.glob("*.csv"))
    stamps = minutes([8, 9, 10])
    write_export(inbox.directory, "Bid", stamps, first="2026.01.08", last="2026.01.10")
    write_export(inbox.directory, "Ask", stamps, first="2026.01.08", last="2026.01.10", mutate=lambda s, v: v - 1)
    reports, events = process_inbox(store, inbox, [PLAN], clock=lambda: NOW2)
    assert events[0]["status"] == "KEPT" and len(list(inbox.directory.glob("*.csv"))) == 2
    assert any(r["status"] == "ERROR" for r in reports)


def test_rerun_is_idempotent_and_overlap_revision_is_recorded_not_overwritten(env):
    inbox, store = env
    export_pair(inbox.directory, minutes([5, 6, 7]))
    process_inbox(store, inbox, [PLAN], clock=lambda: NOW1)
    before = partitions(store)
    export_pair(inbox.directory, minutes([5, 6, 7]))
    reports, _ = process_inbox(store, inbox, [PLAN], clock=lambda: NOW2)
    assert partitions(store) == before
    assert [r.get("added") for r in reports[:2]] == [False, False]
    revised = lambda stamp, value: value + 5 if stamp.day == 6 else value
    export_pair(inbox.directory, minutes([6, 7, 8]), first="2026.01.06", mutate=revised)
    later = NOW2 + timedelta(days=1)
    reports, _ = process_inbox(store, inbox, [PLAN], clock=lambda: later)
    assert reports[0]["revision_detected"] and reports[1]["status"] == "UP TO DATE"
    assert reports[2]["status"] == "INCOMPLETE"
    assert [d for d, _ in partitions(store)] == ["2026-01-05", "2026-01-06", "2026-01-06", "2026-01-07"]
    first, _ = store.read("XAUUSD", date(2026, 1, 6), date(2026, 1, 6), as_of=NOW2, first_publication=True)
    latest, _ = store.read("XAUUSD", date(2026, 1, 6), date(2026, 1, 6), as_of=later + timedelta(days=1))
    assert first.bid_open.iloc[0] == 100 and latest.bid_open.iloc[0] == 105


def test_unpaired_file_is_error_and_nothing_is_ingested(env):
    inbox, store = env
    write_export(inbox.directory, "Bid", minutes([5, 6]))
    reports, events = process_inbox(store, inbox, [PLAN], clock=lambda: NOW1)
    assert reports == [] and events[0]["status"] == "ERROR" and "Unpaired" in events[0]["detail"]
    assert partitions(store) == [] and len(list(inbox.directory.glob("*.csv"))) == 1


def test_negative_spread_day_is_rejected_with_reason(env):
    inbox, store = env
    write_export(inbox.directory, "Bid", minutes([5, 6, 7]))
    write_export(inbox.directory, "Ask", minutes([5, 6, 7]), mutate=lambda s, v: v - 1 if s.day == 6 else v)
    reports, events = process_inbox(store, inbox, [PLAN], clock=lambda: NOW1)
    assert reports[1]["status"] == "ERROR" and "Negative spread" in reports[1]["detail"]
    assert events[0]["status"] == "KEPT"


def test_duplicate_timestamps_are_rejected(env):
    inbox, store = env
    stamps = minutes([5, 6, 7])
    export_pair(inbox.directory, stamps[:3] + stamps[2:])
    reports, _ = process_inbox(store, inbox, [PLAN], clock=lambda: NOW1)
    assert reports[0]["status"] == "ERROR" and "duplicate" in reports[0]["detail"]


def test_days_in_original_history_are_not_reingested(env, tmp_path):
    inbox, store = env
    source = tmp_path / "source"
    source.mkdir()
    (source / "XAUUSD_1 Min_Bid_2004.01.01_2026.01.06.csv").write_text(
        "Time (EET),Open,High,Low,Close,Volume\n2026.01.05 12:00:00,1,2,1,1,1\n2026.01.06 12:04:00,1,2,1,1,1\n",
        encoding="utf-8")
    export_pair(inbox.directory, minutes([4, 5, 6, 7, 8]))
    reports, _ = process_inbox(store, inbox, [PLAN], clock=lambda: NOW1)
    assert [r["day"] for r in reports] == ["2026-01-07", "2026-01-08"]
    assert [d for d, _ in partitions(store)] == ["2026-01-07"]


def test_eet_conversion_handles_dst_and_matches_data_manager(tmp_path):
    winter = _decode_times(pd.Series(["2026.01.01 12:00:00"]), "Europe/Helsinki")
    summer = _decode_times(pd.Series(["2026.07.01 12:00:00"]), "Europe/Helsinki")
    assert winter[0] == pd.Timestamp("2026-01-01T10:00Z") and summer[0] == pd.Timestamp("2026-07-01T09:00Z")
    fall_back = ["2026.10.25 03:50:00", "2026.10.25 03:10:00", "2026.10.25 04:00:00", "2026.07.01 12:00:00"]
    ours = _decode_times(pd.Series(fall_back), "Europe/Helsinki")
    assert list(ours[:3]) == [pd.Timestamp("2026-10-25T00:50Z"), pd.Timestamp("2026-10-25T01:10Z"),
                              pd.Timestamp("2026-10-25T02:00Z")]
    with pytest.raises(Exception, match="nonexistent"):
        _decode_times(pd.Series(["2026.03.29 03:30:00"]), "Europe/Helsinki")
    path = tmp_path / "XAUUSD_1 Min_Bid_x.csv"
    path.write_text("Time (EET),Open,High,Low,Close,Volume\n" + "\n".join(f"{t},1,2,1,1,1" for t in fall_back) + "\n")
    expected, _ = DataManager._read_side(path, "bid")
    assert list(expected.index) == sorted(ours)


def test_utc_header_variant_and_tick_volume_suffix(env):
    inbox, store = env
    export_pair(inbox.directory, minutes([5, 6, 7]), header="Gmt time", tail="_TickVolume_1")
    reports, events = process_inbox(store, inbox, [PLAN], clock=lambda: NOW1)
    assert events[0]["status"] == "PROCESSED"
    assert [r["day"] for r in reports] == ["2026-01-05", "2026-01-06", "2026-01-07"]


def test_config_inbox_mode_runs_end_to_end(tmp_path):
    config = tmp_path / "daily_sync.json"
    week = {str(day): [["10:00", "10:05"]] for day in range(7)}
    config.write_text(__import__("json").dumps({
        "inbox_directory": "inbox", "source_directory": "source",
        "symbols": {"DE40": {"first_day": "2026-01-01", "update_at": "00:00", "timezone": "UTC", "weekly": week}}}))
    loaded = load_sync_config(config)
    assert loaded.inbox and loaded.plans[0].symbol == "DE40"
    export_pair(tmp_path / "inbox", minutes([5, 6, 7]), symbol="DEUIDXEUR")
    store = DailyPartitionStore(tmp_path / "daily.sqlite")
    result = run_sync(store, loaded, manual=True, clock=lambda: NOW1)
    assert result["status"] == "RETRY"
    assert [e["status"] for e in result["inbox"]] == ["PROCESSED"]
    assert [d for d, _ in partitions(store)] == ["2026-01-05", "2026-01-06"]
    assert store.sync_history()[0]["detail"]["inbox"][0]["status"] == "PROCESSED"
