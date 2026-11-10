"""Forward engine audit (v101): prospective-only data, carry-over, exactly-once, IDs, ledger integrity.

Fixture data is synthetic and proves mechanics only; it is not evidence about any strategy.
"""
from contextlib import closing
from datetime import timedelta
import sqlite3

from fastapi.testclient import TestClient
import pytest

from strategy_lab import forward_rules, server
from strategy_lab.daily_data import DailyPartitionStore
from strategy_lab.data_manager import DataError
from strategy_lab.forward_engine import EVIDENCE_CLASSES, ForwardEngine, MODEL_SHA256
from strategy_lab.forward_registry import ForwardRegistry
from strategy_lab.tests.test_forward_rules import (
    DAY, ENTRY_ROWS, FROZEN, flat_day, ingest, observed, path_day, setup, spec)

DAY2, DAY3 = DAY + timedelta(days=1), DAY + timedelta(days=2)
HOLD = [(101, 101.2, 100.9, 101.0)] * 3
WIN = ENTRY_ROWS + [(101, 101.2, 100.9, 101.0), (101.0, 103.6, 100.9, 103.5)] + [(100, 100.1, 99.9, 100)] * 3
LOSS = ENTRY_ROWS + [(101, 101.2, 100.9, 101.0), (101.0, 101.1, 99.5, 100.0)] + [(100, 100.1, 99.9, 100)] * 3


def kinds(report, *names):
    return [e for e in report["events"] if e["kind"] in names]


def run_days(path, days=(WIN, LOSS)):
    store, engine, portfolio, ids = setup(path, [spec()])
    for offset, rows in enumerate(days):
        day = DAY + timedelta(days=offset)
        ingest(store, "XAUUSD", day, path_day(day, rows))
        engine.advance(portfolio, as_of=observed(day))
    return store, engine, portfolio, ids


def test_evidence_classes_are_declared_and_realtime_paper_is_not_implemented(tmp_path, monkeypatch):
    store, engine, portfolio, _ = run_days(tmp_path / "a.sqlite", (WIN,))
    report = engine.report(portfolio)
    assert report["mode"] == "DAILY_DELAYED_FORWARD" and report["evidence_classes"] == EVIDENCE_CLASSES
    assert {name for name, row in EVIDENCE_CLASSES.items() if not row["implemented"]} == {"REALTIME_PAPER"}
    assert {name for name, row in EVIDENCE_CLASSES.items() if row["prospective"]} == {
        "DAILY_DELAYED_FORWARD", "REALTIME_PAPER"}
    monkeypatch.setattr(server, "DEFAULT_DAILY_STORE", tmp_path / "a.sqlite")
    body = TestClient(server.app).get(f"/api/forward/portfolios/{portfolio}").json()
    assert body["evidence_classes"]["REALTIME_PAPER"]["implemented"] is False
    assert "NOT IMPLEMENTED" in body["evidence_classes"]["REALTIME_PAPER"]["note"]


def test_quotes_published_before_registration_are_never_counted_as_prospective(tmp_path):
    path = tmp_path / "a.sqlite"
    old = DAY - timedelta(days=3)
    # Two complete historical days that would trade (one winner, one loser), public before registration.
    for offset, rows in ((0, WIN), (1, LOSS)):
        day = old + timedelta(days=offset)
        ingest(DailyPartitionStore(path), "XAUUSD", day, path_day(day, rows))
    store, engine, portfolio, _ = setup(path, [spec()])
    ingest(store, "XAUUSD", DAY, path_day(DAY, LOSS))
    assert engine.advance(portfolio, as_of=observed(DAY))["committed_days"] == [DAY.isoformat()]   # first day = join day
    report = engine.report(portfolio)
    assert [d["day"] for d in report["days"]] == [DAY.isoformat()]
    assert {e["day"] for e in report["events"]} == {DAY.isoformat()}
    assert all(e["candle_at"] >= FROZEN.isoformat(timespec="microseconds") for e in report["events"])
    assert len(kinds(report, "POSITION_CLOSED")) == 1                 # DAY's own trade only, none from history


def test_open_position_carries_over_days_and_restarts_without_duplicate_fills(tmp_path):
    path = tmp_path / "a.sqlite"
    store, engine, portfolio, ids = setup(path, [spec()])
    ingest(store, "XAUUSD", DAY, path_day(DAY, ENTRY_ROWS + HOLD))
    engine.advance(portfolio, as_of=observed(DAY))
    day1 = engine.report(portfolio)
    position = day1["members"][0]["state"]["position"]
    assert position and len(kinds(day1, "ORDER_FILLED")) == 1 and not kinds(day1, "POSITION_CLOSED")
    assert day1["portfolio"]["balance"] == 1000 and day1["days"][0]["equity"] != 1000   # marked to market

    engine = ForwardEngine(ForwardRegistry(DailyPartitionStore(path)))                # restart
    assert engine.report(portfolio) == day1
    assert engine.advance(portfolio, as_of=observed(DAY))["committed_days"] == []
    ingest(store, "XAUUSD", DAY2, path_day(DAY2, [(101.0, 103.6, 100.9, 103.5)] * 2))
    engine = ForwardEngine(ForwardRegistry(DailyPartitionStore(path)))                # restart again
    engine.advance(portfolio, as_of=observed(DAY2))
    day2 = engine.report(portfolio)
    assert len(kinds(day2, "ORDER_FILLED")) == 1
    closed, = kinds(day2, "POSITION_CLOSED")
    assert closed["day"] == DAY2.isoformat() and closed["detail"]["exit_reason"] == "TAKE_PROFIT"
    assert closed["detail"]["position_id"] == kinds(day2, "ORDER_FILLED")[0]["detail"]["position_id"]
    assert day2["members"][0]["state"]["position"] is None
    assert day2["portfolio"]["balance"] == pytest.approx(1000 + closed["detail"]["net_pnl"])
    # earlier committed rows are byte-identical
    assert day2["events"][:len(day1["events"])] == day1["events"] and day2["days"][0] == day1["days"][0]


def test_pending_order_carries_over_a_restart(tmp_path):
    from strategy_lab.tests.test_forward_engine import publish, setup as lab_setup
    path = tmp_path / "a.sqlite"
    store, engine, portfolio, _ = lab_setup(path, symbols=("XAUUSD",))
    publish(store, "XAUUSD", DAY, [(100, 100.2, 99.8, 100), (101, 101.2, 100.8, 101)])
    engine.advance(portfolio, as_of=observed(DAY))
    pending = engine.report(portfolio)["members"][0]["state"]["pending"]
    assert pending and pending["type"] == "MARKET"
    engine = ForwardEngine(ForwardRegistry(DailyPartitionStore(path)))
    assert engine.report(portfolio)["members"][0]["state"]["pending"] == pending
    publish(store, "XAUUSD", DAY2, [(102, 102.4, 101.8, 102)])
    engine.advance(portfolio, as_of=observed(DAY2))
    report = engine.report(portfolio)
    assert report["members"][0]["state"]["pending"] is None
    assert len(kinds(report, "ORDER_FILLED")) == 1 and kinds(report, "ORDER_FILLED")[0]["day"] == DAY2.isoformat()


def test_exactly_once_processing_across_instances_and_duplicate_inserts(tmp_path):
    path = tmp_path / "a.sqlite"
    store, engine, portfolio, _ = run_days(path)
    before = engine.report(portfolio)
    other = ForwardEngine(ForwardRegistry(DailyPartitionStore(path)))
    for instance in (engine, other, engine):
        assert instance.advance(portfolio, as_of=observed(DAY2))["committed_days"] == []
    assert engine.report(portfolio) == before
    days = [d["day"] for d in before["days"]]
    assert days == sorted(set(days)) == [DAY.isoformat(), DAY2.isoformat()]
    with closing(sqlite3.connect(path)) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO forward_days VALUES (?, ?, 'x', '{}', 1, 1)", (portfolio, DAY.isoformat()))
    opened = [e["detail"]["position_id"] for e in kinds(before, "ORDER_FILLED")]
    assert len(opened) == len(set(opened)) == 2


def test_order_and_position_ids_are_deterministic_unique_and_linked(tmp_path):
    one = run_days(tmp_path / "a.sqlite")
    first = one[1].report(one[2])
    second = run_days(tmp_path / "c.sqlite")
    second_report = second[1].report(second[2])
    ids = lambda r: [(e["kind"], e["detail"].get("order_id"), e["detail"].get("position_id")) for e in r["events"]]
    assert ids(first) == ids(second_report)                    # reproducible in another database
    for event in first["events"]:
        if event["kind"] in {"ORDER_SUBMITTED", "ORDER_FILLED", "POSITION_CLOSED"}:
            assert len(event["detail"]["order_id"]) == 24
    submitted = {e["detail"]["order_id"] for e in kinds(first, "ORDER_SUBMITTED")}
    filled = kinds(first, "ORDER_FILLED")
    closed = kinds(first, "POSITION_CLOSED")
    assert len(submitted) == 2 and {e["detail"]["order_id"] for e in filled} == submitted
    assert [e["detail"]["position_id"] for e in filled] == [e["detail"]["position_id"] for e in closed]
    assert len({e["detail"]["position_id"] for e in filled}) == 2


def test_trade_ledger_integrity_and_consistent_equity(tmp_path):
    store, engine, portfolio, _ = run_days(tmp_path / "a.sqlite")
    report = engine.report(portfolio)
    closed = [e["detail"] for e in kinds(report, "POSITION_CLOSED")]
    assert len(closed) == 2 and {c["exit_reason"] for c in closed} == {"TAKE_PROFIT", "STOP_LOSS"}
    assert report["portfolio"]["balance"] == pytest.approx(1000 + sum(c["net_pnl"] for c in closed))
    assert report["members"][0]["state"]["realized_pnl"] == pytest.approx(sum(c["net_pnl"] for c in closed))
    assert report["days"][-1]["equity"] == pytest.approx(report["portfolio"]["balance"])   # flat at day end
    fills = {e["detail"]["position_id"]: e for e in kinds(report, "ORDER_FILLED")}
    for exit_ in kinds(report, "POSITION_CLOSED"):
        assert fills[exit_["detail"]["position_id"]]["id"] < exit_["id"]                 # fill precedes exit
    for event in report["events"]:
        assert event["detail"]["partition_sha256"] == report["days"][-1]["publications"]["XAUUSD"]["sha256"] \
            or event["day"] == DAY.isoformat()
        assert event["detail"]["quote_observed_at"] <= report["days"][-1]["processed_at"]
    assert engine.verify_ledger(portfolio)["status"] == "OK"


def test_data_snapshot_sha_is_recorded_and_later_revisions_do_not_rewrite_history(tmp_path):
    store, engine, portfolio, _ = run_days(tmp_path / "a.sqlite", (WIN,))
    report = engine.report(portfolio)
    publication = report["days"][0]["publications"]["XAUUSD"]
    _, manifests = store.read("XAUUSD", DAY, DAY, as_of=observed(DAY), first_publication=True)
    assert publication["sha256"] == manifests[0]["sha256"] and len(publication["sha256"]) == 64
    assert {e["detail"]["partition_sha256"] for e in report["events"]} == {publication["sha256"]}
    ingest(store, "XAUUSD", DAY, path_day(DAY, LOSS), observed_at=observed(DAY2))        # provider revision
    assert engine.report(portfolio) == report and engine.verify_ledger(portfolio)["status"] == "OK"


def test_ledger_is_append_only_and_tampering_is_detected(tmp_path, monkeypatch):
    path = tmp_path / "a.sqlite"
    store, engine, portfolio, _ = run_days(path)
    seal = engine.verify_ledger(portfolio)
    assert seal["status"] == "OK" and seal["sealed_days"] == 2
    with closing(sqlite3.connect(path)) as db:
        for sql in ("UPDATE forward_events SET kind='X'", "DELETE FROM forward_events",
                    "UPDATE forward_days SET equity=1", "DELETE FROM forward_ledger"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(sql)
    with closing(sqlite3.connect(path)) as db, db:                                      # a determined editor
        db.execute("DROP TRIGGER forward_events_no_update")
        db.execute("UPDATE forward_events SET detail=replace(detail, '\"net_pnl\"', '\"net_pnl2\"') "
                   "WHERE kind='POSITION_CLOSED' AND day=?", (DAY.isoformat(),))
    broken = engine.verify_ledger(portfolio)
    assert broken["status"] == "BROKEN" and broken["first_bad_day"] == DAY.isoformat()
    monkeypatch.setattr(server, "DEFAULT_DAILY_STORE", path)
    assert TestClient(server.app).get(f"/api/forward/portfolios/{portfolio}/ledger").json()["status"] == "BROKEN"


def test_crash_in_the_middle_of_a_day_rolls_back_and_the_retry_equals_a_clean_run(tmp_path, monkeypatch):
    clean_store, clean, clean_id, _ = run_days(tmp_path / "clean.sqlite")
    path = tmp_path / "crash.sqlite"
    store, engine, portfolio, _ = setup(path, [spec()])
    ingest(store, "XAUUSD", DAY, path_day(DAY, WIN))
    engine.advance(portfolio, as_of=observed(DAY))
    committed = engine.report(portfolio)
    ingest(store, "XAUUSD", DAY2, path_day(DAY2, LOSS))
    real, calls = forward_rules.step, {"n": 0}

    def exploding(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 6:
            raise RuntimeError("simulated crash mid-day")
        return real(*args, **kwargs)

    monkeypatch.setattr(forward_rules, "step", exploding)
    with pytest.raises(RuntimeError, match="simulated crash"):
        engine.advance(portfolio, as_of=observed(DAY2))
    assert calls["n"] == 6
    assert engine.report(portfolio) == committed                    # no partial events, flags, state or seal
    assert engine.verify_ledger(portfolio)["status"] == "OK"
    monkeypatch.setattr(forward_rules, "step", real)
    assert engine.advance(portfolio, as_of=observed(DAY2))["committed_days"] == [DAY2.isoformat()]
    strip = lambda r: [(e["kind"], e["day"], e["candle_at"], e["detail"]) for e in r["events"]]
    assert strip(engine.report(portfolio)) == strip(clean.report(clean_id))
    assert engine.verify_ledger(portfolio)["status"] == "OK" and engine.verify_ledger(portfolio)["sealed_days"] == 2


def test_a_frozen_engine_version_mismatch_refuses_to_continue(tmp_path):
    path = tmp_path / "a.sqlite"
    store, engine, portfolio, _ = setup(path, [spec()])
    ingest(store, "XAUUSD", DAY, path_day(DAY, WIN))
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE forward_portfolios SET model_sha256='old'")
    with pytest.raises(DataError, match="engine version"):
        engine.advance(portfolio, as_of=observed(DAY))
    assert MODEL_SHA256 != "old"
