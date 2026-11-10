from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import copy
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient
import numpy as np
import pandas as pd
import pytest

from runtime.strategy_lab import rule_engine
from runtime.strategy_lab.execution import ContractSpec
from strategy_lab import forward_rules, research_api, server
from strategy_lab.daily_data import DailyPartitionStore
from strategy_lab.data_manager import DataError
from strategy_lab.forward_engine import ForwardEngine, LAB_SOURCE, MODEL
from strategy_lab.forward_registry import ForwardRegistry
from strategy_lab.research import backtest

DAY = date(2026, 10, 19)
FROZEN = datetime(2026, 10, 19, 11, tzinfo=timezone.utc)
LAB = Path(__file__).resolve().parents[2] / "runtime" / "strategies" / LAB_SOURCE
CONTRACT = {"account_currency": "USD", "contract_size": 1, "tick_size": 0.01, "quote_to_account": 1,
            "volume_step": 0.01, "slippage_points": 1, "commission_per_lot_side": 0.1}


def observed(day):
    return datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)


def ingest(store, symbol, day, bid, spread=0.2, *, observed_at=None):
    ask = bid.copy()
    ask.iloc[:, :4] = ask.iloc[:, :4].add(spread, axis=0)
    return store.ingest(symbol, day, bid, ask, expected_minutes=bid.index, observed_at=observed_at or observed(day))


def flat_day(day, price=100.0, minutes=1440, start="00:00"):
    index = pd.date_range(f"{day}T{start}Z", periods=minutes, freq="min")
    return pd.DataFrame({"open": price, "high": price + 0.1, "low": price - 0.1, "close": price, "volume": 1.0},
                        index=index)


def path_day(day, rows, start="12:00"):
    index = pd.date_range(f"{day}T{start}Z", periods=len(rows), freq="min")
    return pd.DataFrame([(*row, 1.0) for row in rows], columns=["open", "high", "low", "close", "volume"],
                        index=index, dtype=float)


def spec(timeframe="M5", symbol="XAUUSD", **over):
    body = {"schema": rule_engine.SCHEMA, "name": "Fixture", "symbol": symbol, "timeframe": timeframe,
            "direction": "long", "entry": {"long": {"left": {"price": "close"}, "op": ">", "right": {"value": 100.5}}},
            "stop": {"type": "points", "value": 1.0}, "target": {"type": "r_multiple", "value": 2.0},
            "risk": {"percent": 1.0}}
    body.update(over)
    return body


def make_registration(store, strategy, *, params=None, capital=1000, trade_risk=0.01, limit=0.05, leverage=100,
                      cap=None, frozen_at=FROZEN):
    warm = forward_rules.warmup_from_fetch(forward_rules.store_fetch(store, strategy["symbol"]), strategy, params,
                                           as_of=frozen_at, buffer_cap=cap)
    config = forward_rules.build_configuration(
        strategy, params, contract=CONTRACT, starting_capital=capital, trade_risk=trade_risk,
        portfolio_risk_limit=limit, leverage=leverage, warmup=warm, data_version="fixture")
    return forward_rules.freeze(ForwardRegistry(store), config, frozen_at=frozen_at)


def setup(path, strategies, *, capital=1000, risk_limit=0.05, joined_at=FROZEN, per_member=None, **kw):
    store = DailyPartitionStore(path)
    for item in strategies:
        ingest(store, item["symbol"], DAY - timedelta(days=1), flat_day(DAY - timedelta(days=1)))
    engine = ForwardEngine(ForwardRegistry(store))
    portfolio = engine.create("Rules", currency="USD", starting_capital=capital, risk_limit=risk_limit)
    ids = []
    for item in strategies:
        registration = make_registration(store, item, capital=per_member or capital, limit=risk_limit, **kw)
        engine.enroll(portfolio, registration["id"], joined_at=joined_at)
        ids.append(registration["id"])
    return store, engine, portfolio, ids


def kinds(engine, portfolio):
    return [(e["kind"], e["candle_at"][11:16]) for e in engine.report(portfolio)["events"]]


def closed(engine, portfolio):
    return [e["detail"] for e in engine.report(portfolio)["events"] if e["kind"] == "POSITION_CLOSED"]


ENTRY_ROWS = [(100, 100.1, 99.9, 100)] * 4 + [(100, 101.1, 99.9, 101)]          # bucket 12:00, closes 101


def test_freeze_validates_spec_news_filter_and_warmup_provenance(tmp_path):
    store = DailyPartitionStore(tmp_path / "f.sqlite")
    ingest(store, "XAUUSD", DAY - timedelta(days=1), flat_day(DAY - timedelta(days=1)))
    good = spec()
    registration = make_registration(store, good)
    config = registration["configuration"]
    assert set(registration["sources"]) == {forward_rules.ENGINE_SOURCE, forward_rules.RULES_SOURCE}
    assert config["warmup"]["count"] == config["warmup"]["required_bars"] == rule_engine.lookback_bars(good)
    assert config["spec_sha256"] == rule_engine.spec_sha256(good) and config["news_filter"] == {"enabled": False}

    warm = config["warmup"]
    build = lambda **over: forward_rules.build_configuration(
        over.pop("spec", good), None, contract=CONTRACT, starting_capital=1000, trade_risk=0.01,
        portfolio_risk_limit=0.05, leverage=100, warmup=warm, data_version="x")
    with pytest.raises(DataError, match="Invalid rule spec"):
        build(spec={**good, "surprise": 1})
    registry = ForwardRegistry(store)
    with pytest.raises(DataError, match="news filter"):
        forward_rules.freeze(registry, {**config, "news_filter": {"enabled": True}}, frozen_at=FROZEN)
    # Tampered warm-up bars, bars not closed at the freeze time, or a snapshot taken after the freeze.
    tampered = copy.deepcopy(config)
    tampered["warmup"]["bars"][-1][4] += 1
    with pytest.raises(DataError, match="checksum"):
        forward_rules.freeze(registry, tampered, frozen_at=FROZEN)
    with pytest.raises(DataError, match="after the freeze"):
        forward_rules.freeze(registry, config, frozen_at=FROZEN - timedelta(hours=1))
    # Partitions published after the freeze never enter the warm-up.
    frame, parts = store.read("XAUUSD", DAY - timedelta(days=1), DAY - timedelta(days=1), as_of=FROZEN)
    late = [{**parts[0], "observed_at": (FROZEN + timedelta(minutes=1)).isoformat()}]
    with pytest.raises(DataError, match="observed after the freeze"):
        forward_rules.build_warmup(frame, late, good, None, as_of=FROZEN)
    leaked = copy.deepcopy(config)
    leaked["warmup"]["partitions"][0]["observed_at"] = (FROZEN + timedelta(minutes=1)).isoformat()
    with pytest.raises(DataError, match="observed after the freeze"):
        forward_rules.freeze(registry, leaked, frozen_at=FROZEN)
    # Frozen code files must be the loaded ones.
    with pytest.raises(DataError, match="differs from the loaded"):
        forward_rules.validate_registration({"configuration": config, "frozen_at": registration["frozen_at"],
                                             "sources": {**registration["sources"], forward_rules.ENGINE_SOURCE: b"x"}})


def test_registry_is_immutable_and_a_changed_spec_is_a_new_registration(tmp_path):
    store = DailyPartitionStore(tmp_path / "f.sqlite")
    ingest(store, "XAUUSD", DAY - timedelta(days=1), flat_day(DAY - timedelta(days=1)))
    first = make_registration(store, spec())
    other = make_registration(store, spec(stop={"type": "points", "value": 2.0}))
    assert first["id"] != other["id"]
    assert first["configuration"]["spec_sha256"] != other["configuration"]["spec_sha256"]
    with closing(sqlite3.connect(tmp_path / "f.sqlite")) as db, db:
        raw = db.execute("SELECT configuration FROM frozen_strategies WHERE id=?", (first["id"],)).fetchone()[0]
        db.execute("UPDATE frozen_strategies SET configuration=? WHERE id=?",
                   (raw.replace(b'"value":1.0', b'"value":9.0'), first["id"]))
    with pytest.raises(DataError, match="checksum"):
        ForwardRegistry(store).get(first["id"])


def test_market_entry_at_next_bucket_open_on_ask_and_exit_on_bid(tmp_path):
    store, engine, portfolio, _ = setup(tmp_path / "f.sqlite", [spec()])
    rows = ENTRY_ROWS + [(101, 101.2, 100.9, 101.0), (101.0, 103.6, 100.9, 103.5)] + [(100, 100.1, 99.9, 100)] * 3
    ingest(store, "XAUUSD", DAY, path_day(DAY, rows))
    assert engine.advance(portfolio, as_of=observed(DAY))["committed_days"] == [DAY.isoformat()]
    events = engine.report(portfolio)["events"]
    assert [(e["kind"], e["candle_at"][11:16]) for e in events] == [
        ("ORDER_SUBMITTED", "12:05"), ("ORDER_FILLED", "12:05"), ("POSITION_CLOSED", "12:06")]
    submitted, filled, exit_ = (e["detail"] for e in events)
    assert submitted["signal_at"].startswith(f"{DAY}T12:05") and submitted["type"] == "MARKET"
    assert filled["entry"] == pytest.approx(101.0 + 0.2 + 0.01)       # Ask open + one point of slippage
    assert filled["quote"] == "ASK" and filled["fill_assumption"] == "NEXT_OPEN"
    assert filled["quantity"] == pytest.approx(10) and filled["stop"] == pytest.approx(100.21)
    assert exit_["exit_reason"] == "TAKE_PROFIT" and exit_["quote"] == "BID" and not exit_["ambiguous"]
    assert exit_["exit"] == pytest.approx(103.21)                      # target; take profit carries no slippage
    assert exit_["net_pnl"] == pytest.approx((103.21 - 101.21) * 10 - 2)
    assert exit_["contract"]["tick_size"] == 0.01 and exit_["spread_open"] == pytest.approx(0.2)
    assert engine.report(portfolio)["portfolio"]["balance"] == pytest.approx(1000 + exit_["net_pnl"])


def test_stop_loss_wins_when_both_levels_touch_in_one_candle(tmp_path):
    store, engine, portfolio, _ = setup(tmp_path / "f.sqlite", [spec()])
    rows = ENTRY_ROWS + [(101, 101.2, 100.9, 101.0), (101.0, 104.0, 100.0, 101.0)] + [(100, 100.1, 99.9, 100)] * 3
    ingest(store, "XAUUSD", DAY, path_day(DAY, rows))
    engine.advance(portfolio, as_of=observed(DAY))
    exit_, = closed(engine, portfolio)
    assert exit_["exit_reason"] == "STOP_LOSS" and exit_["ambiguous"]
    assert exit_["exit"] == pytest.approx(100.21 - 0.01)               # stop minus slippage, never the target
    assert exit_["net_pnl"] == pytest.approx((100.20 - 101.21) * 10 - 2)


def test_only_candles_after_registration_and_complete_buckets_are_used(tmp_path):
    rows = [(101, 101.1, 100.9, 101)] * 60                              # every bar closes above the threshold
    for joined, first_signal in ((FROZEN.replace(hour=12, minute=30), "12:35"),
                                 (FROZEN.replace(hour=12, minute=32), "12:40")):
        store, engine, portfolio, _ = setup(tmp_path / f"{first_signal[-2:]}.sqlite", [spec()], joined_at=joined)
        ingest(store, "XAUUSD", DAY, path_day(DAY, rows))
        engine.advance(portfolio, as_of=observed(DAY))
        report = engine.report(portfolio)
        assert min(e["candle_at"] for e in report["events"]) >= joined.isoformat(timespec="microseconds")
        assert [e["candle_at"][11:16] for e in report["events"] if e["kind"] == "ORDER_SUBMITTED"][0] == first_signal
        assert report["members"][0]["state"]["last_candle_at"].startswith(f"{DAY}T12:59")


def random_walk(day, minutes, seed, start="12:00"):
    rng = np.random.default_rng(seed)
    index = pd.date_range(f"{day}T{start}Z", periods=minutes, freq="min")
    close = 2000 + np.cumsum(rng.normal(0, 0.3, minutes))
    open_ = np.concatenate(([2000.0 + seed], close[:-1]))
    high = np.maximum(open_, close) + rng.random(minutes) * 0.15
    low = np.minimum(open_, close) - rng.random(minutes) * 0.15
    bid = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": rng.random(minutes) + 1},
                       index=index)
    return bid, 0.2 + rng.random(minutes) * 0.2


def rich_spec(order):
    return spec("M1", entry={"long": {"left": {"indicator": "rsi", "period": 5}, "op": "<",
                                      "right": {"value": {"param": "low_level"}}},
                             "short": {"left": {"indicator": "rsi", "period": 5}, "op": ">",
                                       "right": {"value": 75}}},
                direction="both", stop={"type": "atr", "period": 5, "mult": 1.5},
                target={"type": "r_multiple", "value": 1.5}, break_even={"activate_r": 0.8},
                trailing={"activate_r": 1.0, "atr_mult": 1.0, "period": 5}, time_exit_bars=12, cooldown_bars=2,
                params=[{"name": "low_level", "type": "float", "min": 10, "max": 40, "default": 25}],
                order=order)


def run_forward_vs_backtest(tmp_path, order, days=3, minutes=240, timeframe="M1"):
    strategy = {**rich_spec(order), "timeframe": timeframe}
    store = DailyPartitionStore(tmp_path / "f.sqlite")
    warm_bid, warm_spread = random_walk(DAY - timedelta(days=1), 1440, 99, start="00:00")
    ingest(store, "XAUUSD", DAY - timedelta(days=1), warm_bid, pd.Series(warm_spread, index=warm_bid.index))
    engine = ForwardEngine(ForwardRegistry(store))
    portfolio = engine.create("Equivalence", currency="USD", starting_capital=10000, risk_limit=0.5)
    registration = make_registration(store, strategy, params={"low_level": 25}, capital=10000, limit=0.5,
                                     leverage=1000, cap=5000)
    engine.enroll(portfolio, registration["id"], joined_at=FROZEN)
    bids, asks = [warm_bid], [warm_spread]
    for offset in range(days):
        day = DAY + timedelta(days=offset)
        bid, spread = random_walk(day, minutes, offset)
        ingest(store, "XAUUSD", day, bid, pd.Series(spread, index=bid.index))
        bids.append(bid), asks.append(spread)
        engine.advance(portfolio, as_of=observed(day))
    bid = pd.concat(bids)
    ask = bid.copy()
    ask.iloc[:, :4] += np.concatenate(asks)[:, None]
    warm = registration["configuration"]["warmup"]
    first = pd.Timestamp(warm["bars"][0][0], unit="ms", tz="UTC")
    contract = ContractSpec(**CONTRACT)
    if timeframe != "M1":
        bars = lambda frame: frame.loc[first:].resample(
            "5min", label="left", closed="left", origin="start_day").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        bid, ask, first = bars(bid), bars(ask), None
    result = backtest.run_rules_backtest(strategy, bid.loc[first:], ask.loc[first:], {"low_level": 25},
                                         capital=10000, contract=contract,
                                         trade_from=pd.Timestamp(f"{DAY}T12:00Z"))
    trades = [t for t in result["trades"] if t["exit_reason"] != "END_OF_DATA"]
    return engine, portfolio, trades


@pytest.mark.parametrize("order", [{"type": "market"},
                                   {"type": "stop", "offset_atr": 0.3, "atr_period": 5, "expiry_bars": 3},
                                   {"type": "limit", "offset_atr": 0.4, "atr_period": 5, "expiry_bars": 4}])
def test_forward_m1_trades_equal_the_research_backtest(tmp_path, order):
    engine, portfolio, expected = run_forward_vs_backtest(tmp_path, order)
    actual = closed(engine, portfolio)
    assert len(expected) >= 8 and len(actual) == len(expected)
    for ours, theirs in zip(actual, expected):
        assert pd.Timestamp(ours["entry_at"]) == pd.Timestamp(theirs["entry_time"])
        assert pd.Timestamp(ours["exit_at"]) == pd.Timestamp(theirs["exit_time"])
        assert ours["side"] == theirs["side"] and ours["exit_reason"] == theirs["exit_reason"]
        assert ours["entry"] == pytest.approx(theirs["entry_price"]) and ours["exit"] == pytest.approx(theirs["exit_price"])
        assert ours["quantity"] == pytest.approx(theirs["qty"]) and ours["net_pnl"] == pytest.approx(theirs["pnl"])
    assert {t["exit_reason"] for t in expected} >= {"STOP_LOSS", "TAKE_PROFIT"}
    balance = engine.report(portfolio)["portfolio"]["balance"]
    assert balance == pytest.approx(10000 + sum(t["pnl"] for t in expected))


def test_forward_m5_entries_equal_the_backtest_and_exits_are_resolved_on_m1(tmp_path):
    engine, portfolio, expected = run_forward_vs_backtest(tmp_path, {"type": "market"}, days=4, minutes=600,
                                                          timeframe="M5")
    actual = closed(engine, portfolio)
    assert len(expected) >= 20 and len(actual) == len(expected)
    for ours, theirs in zip(actual, expected):
        assert pd.Timestamp(ours["entry_at"]) == pd.Timestamp(theirs["entry_time"])
        assert ours["entry"] == pytest.approx(theirs["entry_price"])
        assert ours["side"] == theirs["side"] and ours["exit_reason"] == theirs["exit_reason"]
        # Sizes follow realized equity, which differs once an earlier exit price differs.
        assert ours["quantity"] == pytest.approx(theirs["qty"], rel=0.05)
        # Deliberate difference: the backtest stamps the M5 bar, the forward model the M1 candle of the touch.
        assert pd.Timestamp(ours["exit_at"]).floor("5min") == pd.Timestamp(theirs["exit_time"])


def test_restart_each_day_and_rerun_give_identical_events(tmp_path):
    base = tmp_path / "continuous"
    base.mkdir()
    continuous, c_portfolio, _ = run_forward_vs_backtest(base, {"type": "market"}, days=3, minutes=120)
    # Same data, but a fresh engine object (a process restart) before every day and a repeated advance.
    path = tmp_path / "restart.sqlite"
    strategy = rich_spec({"type": "market"})
    store = DailyPartitionStore(path)
    warm_bid, warm_spread = random_walk(DAY - timedelta(days=1), 1440, 99, start="00:00")
    ingest(store, "XAUUSD", DAY - timedelta(days=1), warm_bid, pd.Series(warm_spread, index=warm_bid.index))
    engine = ForwardEngine(ForwardRegistry(store))
    portfolio = engine.create("Equivalence", currency="USD", starting_capital=10000, risk_limit=0.5)
    registration = make_registration(store, strategy, params={"low_level": 25}, capital=10000, limit=0.5,
                                     leverage=1000, cap=5000)
    engine.enroll(portfolio, registration["id"], joined_at=FROZEN)
    for offset in range(3):
        day = DAY + timedelta(days=offset)
        bid, spread = random_walk(day, 120, offset)
        ingest(store, "XAUUSD", day, bid, pd.Series(spread, index=bid.index))
        engine = ForwardEngine(ForwardRegistry(DailyPartitionStore(path)))
        assert engine.advance(portfolio, as_of=observed(day))["committed_days"] == [day.isoformat()]
        before = engine.report(portfolio)
        assert engine.advance(portfolio, as_of=observed(day))["committed_days"] == []
        assert engine.report(portfolio) == before

    def strip(report):
        return [(e["kind"], e["day"], e["candle_at"], e["detail"]) for e in report["events"]]
    assert len(strip(engine.report(portfolio))) > 10
    assert strip(engine.report(portfolio)) == strip(continuous.report(c_portfolio))
    assert engine.report(portfolio)["members"][0]["state"] == continuous.report(c_portfolio)["members"][0]["state"]


def test_failed_day_commit_rolls_back_rules_state_and_events(tmp_path):
    store, engine, portfolio, _ = setup(tmp_path / "f.sqlite", [spec()])
    ingest(store, "XAUUSD", DAY, path_day(DAY, ENTRY_ROWS + [(101, 101.2, 100.9, 101.0)]))
    original = engine.report(portfolio)
    with closing(sqlite3.connect(tmp_path / "f.sqlite")) as db, db:
        db.execute("""CREATE TRIGGER fixture_abort BEFORE INSERT ON forward_days
                      BEGIN SELECT RAISE(ABORT, 'fixture abort'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="fixture abort"):
        engine.advance(portfolio, as_of=observed(DAY))
    assert engine.report(portfolio) == original
    with closing(sqlite3.connect(tmp_path / "f.sqlite")) as db, db:
        db.execute("DROP TRIGGER fixture_abort")
    engine.advance(portfolio, as_of=observed(DAY))
    assert [kind for kind, _ in kinds(engine, portfolio)] == ["ORDER_SUBMITTED", "ORDER_FILLED"]


def test_two_members_share_the_portfolio_risk_budget(tmp_path):
    members = [spec(symbol="XAUUSD"), spec(symbol="EURUSD")]
    store, engine, portfolio, _ = setup(tmp_path / "f.sqlite", members, risk_limit=0.008, per_member=500)
    rows = ENTRY_ROWS + [(101, 101.2, 100.9, 101.0)] * 4
    for item in members:
        ingest(store, item["symbol"], DAY, path_day(DAY, rows))
    engine.advance(portfolio, as_of=observed(DAY))
    report = engine.report(portfolio)
    filled = [e for e in report["events"] if e["kind"] == "ORDER_FILLED"]
    rejected = [e for e in report["events"] if e["kind"] == "ORDER_REJECTED"]
    assert len(filled) == 1 and len(rejected) == 1
    assert rejected[0]["detail"]["reason"] == "RISK_LIMIT" and rejected[0]["candle_at"] == filled[0]["candle_at"]
    assert rejected[0]["registration_id"] != filled[0]["registration_id"]
    performance = server.summarize_forward(report, model_current=True, data=store)
    assert sum(m["risk_rejections"] for m in performance["members"].values()) == 1
    assert performance["portfolio"]["open_stop_risk"] == pytest.approx(filled[0]["detail"]["risk"])


def test_labbreakout_member_still_runs_next_to_a_rules_member(tmp_path):
    store = DailyPartitionStore(tmp_path / "f.sqlite")
    ingest(store, "EURUSD", DAY - timedelta(days=1), flat_day(DAY - timedelta(days=1)))
    engine = ForwardEngine(ForwardRegistry(store))
    portfolio = engine.create("Mixed", currency="USD", starting_capital=1000, risk_limit=0.05)
    lab = {"strategy": "LabBreakout", "symbol": "XAUUSD", "timeframe": "M1", "execution_model": MODEL,
           "data_version": "fixture", "starting_capital": 500, "trade_risk": 0.01, "portfolio_risk_limit": 0.05,
           "news_filter": {"enabled": False}, "indicators": {}, "contract": CONTRACT,
           "parameters": {"order_mode": 0, "entry_interval": 1, "position_size": 1, "ema_period": 2,
                          "max_bar_range": 25, "stop_distance": 1, "target_distance": 3, "stop_offset": 0.1,
                          "pending_expiry_bars": 2}}
    lab_id = engine.registry.freeze(lab, {LAB_SOURCE: LAB.read_bytes()}, frozen_at=FROZEN)["id"]
    rules_id = make_registration(store, spec(symbol="EURUSD"), capital=500, limit=0.05)["id"]
    for identifier in (lab_id, rules_id):
        engine.enroll(portfolio, identifier, joined_at=FROZEN)
    rising = [(100, 100.2, 99.8, 100), (101, 101.2, 100.8, 101)]
    ingest(store, "XAUUSD", DAY, path_day(DAY, rising))
    ingest(store, "EURUSD", DAY, path_day(DAY, ENTRY_ROWS + [(101, 101.2, 100.9, 101.0)]))
    engine.advance(portfolio, as_of=observed(DAY))
    by_member = {}
    for event in engine.report(portfolio)["events"]:
        by_member.setdefault(event["registration_id"], []).append(event["kind"])
    assert by_member[lab_id] == ["ORDER_SUBMITTED"]
    assert by_member[rules_id] == ["ORDER_SUBMITTED", "ORDER_FILLED"]


def test_cooldown_and_daily_trade_limit_come_from_the_spec(tmp_path):
    always = {"left": {"price": "close"}, "op": ">", "right": {"value": 0}}
    strategy = spec("M1", entry={"long": always}, target={"type": "r_multiple", "value": 0.5},
                    max_trades_per_day=2, cooldown_bars=1)
    store, engine, portfolio, _ = setup(tmp_path / "f.sqlite", [strategy])
    ingest(store, "XAUUSD", DAY, path_day(DAY, [(100, 100.9, 99.9, 100.2)] * 14))
    engine.advance(portfolio, as_of=observed(DAY))
    fills = [e for e in engine.report(portfolio)["events"] if e["kind"] == "ORDER_FILLED"]
    limited = [e for e in engine.report(portfolio)["events"]
               if e["kind"] == "SIGNAL_REJECTED" and e["detail"]["reason"] == "MAX_TRADES_PER_DAY"]
    reasons = {e["detail"]["reason"] for e in engine.report(portfolio)["events"] if e["kind"] == "SIGNAL_REJECTED"}
    assert len(fills) == 2 and limited and reasons == {"COOLDOWN", "MAX_TRADES_PER_DAY"}


def test_buffer_is_capped_and_survives_in_member_state(tmp_path):
    store, engine, portfolio, _ = setup(tmp_path / "f.sqlite", [spec("M1")], cap=170)
    ingest(store, "XAUUSD", DAY, path_day(DAY, [(100, 100.1, 99.9, 100)] * 50))
    engine.advance(portfolio, as_of=observed(DAY))
    state = engine.report(portfolio)["members"][0]["state"]
    assert len(state["buffer"]) == 170 and state["buffer"][-1][0] == int(pd.Timestamp(f"{DAY}T12:48Z").timestamp() * 1000)


def api_client(tmp_path, monkeypatch, strategy):
    path = tmp_path / "api.sqlite"
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "Fixture.json").write_text(__import__("json").dumps({**strategy, "meta": {"origin": "test"}}))

    def candles(symbol, timeframe, start, end):
        assert timeframe == "M1"
        index = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="min", inclusive="left")
        frame = pd.DataFrame({f"{side}_{field}": 100.0 + (0.2 if side == "ask" else 0) for side in ("bid", "ask")
                              for field in ("open", "high", "low", "close", "volume")}, index=index)
        return frame, {"bid_source": {"sha256": "b"}, "ask_source": {"sha256": "a"}}

    monkeypatch.setattr(research_api, "SPEC_ROOT", specs)
    monkeypatch.setattr(research_api, "DEFAULT_DAILY_STORE", path)
    monkeypatch.setattr(research_api, "_data_manager", SimpleNamespace(candles=candles))
    monkeypatch.setattr(server, "DEFAULT_DAILY_STORE", path)
    return TestClient(server.app)


def test_rule_registration_api_previews_freezes_and_enrolls(tmp_path, monkeypatch):
    client = api_client(tmp_path, monkeypatch, spec("M15"))
    preview = client.get("/api/forward/rule-registrations/preview", params={"spec_name": "Fixture"})
    assert preview.status_code == 200, preview.text
    shown = preview.json()["configuration"]
    assert shown["execution_model"] == forward_rules.MODEL and shown["warmup"]["bars_omitted"]
    assert shown["warmup"]["count"] == rule_engine.lookback_bars(spec("M15"))
    assert ForwardRegistry(DailyPartitionStore(research_api.DEFAULT_DAILY_STORE)).data.path.exists()
    body = {"spec_name": "Fixture", "contract": CONTRACT, "starting_capital": 500, "trade_risk": 0.01,
            "portfolio_risk_limit": 0.05, "leverage": 50}
    assert client.post("/api/forward/rule-registrations", json={**body, "spec": {**spec(), "surprise": 1}}).status_code == 422
    assert client.post("/api/forward/rule-registrations", json={**body, "spec_name": "Missing"}).status_code == 404
    assert client.post("/api/forward/rule-registrations", json={**body, "contract": {"account_currency": "USD"}}).status_code == 422
    created = client.post("/api/forward/rule-registrations", json=body)
    assert created.status_code == 201, created.text
    registration = ForwardRegistry(DailyPartitionStore(research_api.DEFAULT_DAILY_STORE)).get(created.json()["registration_id"])
    assert registration["configuration"]["parameters"] == {} and registration["configuration"]["leverage"] == 50
    assert datetime.fromisoformat(registration["configuration"]["warmup"]["as_of"]) <= datetime.fromisoformat(registration["frozen_at"])
    portfolio = client.post("/api/forward/portfolios", json={"name": "API", "currency": "USD", "starting_capital": 1000,
                                                              "risk_limit": 0.05}).json()["id"]
    route = f"/api/forward/portfolios/{portfolio}"
    assert client.post(route + "/members", json={"registration_id": created.json()["registration_id"]}).status_code == 201
    report = client.get(route)
    assert report.status_code == 200, report.text
    member = report.json()["performance"]["members"][created.json()["registration_id"]]
    assert member["status"] == "AWAITING_FIRST_CANDLE"
    reference = client.post(route + f"/members/{created.json()['registration_id']}/historical-reference",
                            json={"start_day": "2026-01-01", "end_day": "2026-01-02"})
    assert reference.status_code == 422
