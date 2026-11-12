"""V9 consistency regressions (synthetic fixtures; real-data twins live in test_v9_consistency_real.py).

Properties: no look-ahead, causal aggregation, Bid/Ask fill rules, contract maths, backtest/forward parity,
restart idempotence, and the forward model's refusal of features it cannot reproduce.
"""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import copy
from types import SimpleNamespace
import random
import shutil
import sqlite3
import sys
import threading

import numpy as np
import pandas as pd
import pytest

EVIDENCE = Path(__file__).resolve().parents[2] / "evidence" / "v9" / "consistency"
sys.path.insert(0, str(EVIDENCE))

import check1_lookahead as c1  # noqa: E402  (spec sets + prefix test shared with the evidence script)
from runtime.strategy_lab import execution, rule_engine  # noqa: E402
from runtime.strategy_lab.execution import ContractSpec  # noqa: E402
from strategy_lab import forward_rules  # noqa: E402
from strategy_lab.daily_data import DailyPartitionStore, provider_symbol  # noqa: E402
from strategy_lab.data_manager import DataError, DataManager  # noqa: E402
from strategy_lab.forward_engine import ForwardEngine  # noqa: E402
from strategy_lab.forward_performance import summarize_forward  # noqa: E402
from strategy_lab.forward_registry import ForwardRegistry, data_symbol  # noqa: E402
from strategy_lab.research import backtest  # noqa: E402
from test_forward_rules import (CONTRACT, DAY, FROZEN, closed, flat_day, ingest, make_registration, observed,  # noqa: E402
                                path_day, random_walk, rich_spec, run_forward_vs_backtest, setup, spec)
from test_rule_engine import synthetic_arrays  # noqa: E402

TF_MS = {"M1": 60_000, "M5": 300_000, "M15": 900_000, "H1": 3_600_000, "H4": 14_400_000, "D1": 86_400_000}


# ----------------------------------------------------------------------------- 1. no look-ahead

@pytest.mark.parametrize("timeframe", list(TF_MS))
def test_rule_signals_equal_on_every_truncated_prefix(timeframe):
    n = 1500 if timeframe == "M1" else 700
    arrays = synthetic_arrays(n=n, seed=11, start="2026-03-02T00:00:00", step_ms=TF_MS[timeframe])
    rng = random.Random(5)
    c1.SAMPLE_SIGNALS, c1.SAMPLE_RANDOM = 4, 8
    tested = signals = 0
    for name, strategy in c1.spec_set(timeframe).items():
        if rule_engine.validate_spec(strategy):
            continue
        outcome = c1.prefix_test(strategy, arrays, rng)
        assert outcome["pass"], (timeframe, name, outcome["mismatches"][:3])
        tested += outcome["bars_tested"]
        signals += outcome["long_signals"] + outcome["short_signals"]
    assert tested > 100 and signals > 0


def test_signal_only_depends_on_closed_bars_when_the_last_bar_is_rewritten():
    """Changing any bar after i must never change the decision at i."""
    arrays = synthetic_arrays(n=500, seed=2, step_ms=TF_MS["M15"])
    strategy = c1.spec_set("M15")["ema_rsi_pullback"]
    base = rule_engine.evaluate(strategy, arrays)
    for i in (120, 260, 399):
        mutated = {k: v.copy() for k, v in arrays.items()}
        mutated["close"][i + 1:] *= 1.5
        mutated["high"][i + 1:] *= 1.5
        mutated["low"][i + 1:] *= 1.5
        out = rule_engine.evaluate(strategy, mutated)
        for side in ("long", "short"):
            assert out["states"][side][i] == base["states"][side][i]
        assert np.array_equal(out["exits"]["stop_atr"][: i + 1], base["exits"]["stop_atr"][: i + 1], equal_nan=True)


# ----------------------------------------------------------------------------- 2. causal aggregation

def synthetic_m1(days=6, seed=4):
    """Weekday M1 quotes with the Dukascopy-style 22:00-23:00 UTC break and a Friday/Sunday closure."""
    rng = np.random.default_rng(seed)
    index = pd.date_range("2026-10-05T00:00Z", periods=days * 1440, freq="min")
    keep = ~((index.hour == 22) | ((index.dayofweek == 5)) | ((index.dayofweek == 4) & (index.hour >= 21)))
    index = index[keep]
    close = 2000 + np.cumsum(rng.normal(0, 0.3, len(index)))
    open_ = np.r_[close[0], close[:-1]]
    frame = pd.DataFrame({"bid_open": open_, "bid_high": np.maximum(open_, close) + 0.1,
                          "bid_low": np.minimum(open_, close) - 0.1, "bid_close": close, "bid_volume": 1.0}, index=index)
    for field in ("open", "high", "low", "close"):
        frame[f"ask_{field}"] = frame[f"bid_{field}"] + 0.3
    frame["ask_volume"] = 1.0
    return frame


@pytest.mark.parametrize("timeframe,freq", [("M5", "5min"), ("M15", "15min"), ("H1", "1h"), ("H4", "4h"), ("D1", "1D")])
def test_aggregation_labels_are_open_times_and_closed_bars_never_change(timeframe, freq):
    m1 = synthetic_m1()
    full = DataManager._aggregate(m1, freq)
    minutes = TF_MS[timeframe] // 60_000
    epoch = ((full.index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta("1ms")).to_numpy("int64")
    assert (epoch % TF_MS[timeframe] == 0).all()
    rng = random.Random(1)
    for _ in range(12):
        cut = m1.index[rng.randrange(len(m1) // 4, len(m1))]
        part = DataManager._aggregate(m1[m1.index < cut], freq)
        closed_bars = part[part.index + pd.Timedelta(minutes=minutes) <= cut]
        assert np.allclose(closed_bars[["bid_open", "bid_high", "bid_low", "bid_close"]].to_numpy(),
                           full.loc[closed_bars.index, ["bid_open", "bid_high", "bid_low", "bid_close"]].to_numpy())
        rows = forward_rules.aggregate_bid(m1[m1.index < cut], timeframe, until=cut.to_pydatetime())
        assert [r[0] for r in rows] == list(((closed_bars.index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta("1ms")))
        assert np.allclose([r[1:5] for r in rows], closed_bars[["bid_open", "bid_high", "bid_low", "bid_close"]].to_numpy())
        assert np.allclose([r[5] for r in rows], closed_bars["bid_volume"].to_numpy())


def test_a_partial_trailing_bucket_is_never_a_closed_forward_bar():
    m1 = synthetic_m1()
    cut = pd.Timestamp("2026-10-06T10:32Z")
    rows = forward_rules.aggregate_bid(m1[m1.index < cut], "H1", until=cut.to_pydatetime())
    assert rows[-1][0] == int(pd.Timestamp("2026-10-06T09:00Z").timestamp() * 1000)


# ----------------------------------------------------------------------------- 3. Bid/Ask execution

SHORT_ENTRY = {"short": {"left": {"price": "close"}, "op": "<", "right": {"value": 99.5}}}
LONG_ENTRY = {"long": {"left": {"price": "close"}, "op": ">", "right": {"value": 100.5}}}
SLIP = CONTRACT["slippage_points"] * CONTRACT["tick_size"]       # 0.01
FEE = CONTRACT["commission_per_lot_side"]
SPREAD = 0.2


def both(tmp_path, strategy, rows):
    """Run one handcrafted M1 path through the forward engine and the research backtest."""
    store, engine, portfolio, _ = setup(tmp_path / "f.sqlite", [strategy])
    bid_day = path_day(DAY, rows)
    ingest(store, "XAUUSD", DAY, bid_day, spread=SPREAD)
    engine.advance(portfolio, as_of=observed(DAY))
    report = engine.report(portfolio)
    warm = flat_day(DAY - timedelta(days=1))
    bid = pd.concat([warm, bid_day])
    ask = bid.copy()
    ask.iloc[:, :4] += SPREAD
    result = backtest.run_rules_backtest(strategy, bid, ask, capital=1000, contract=ContractSpec(**CONTRACT),
                                         trade_from=pd.Timestamp(f"{DAY}T12:00Z"))
    return report, [t for t in result["trades"] if t["exit_reason"] != "END_OF_DATA"], result


def short_spec(**over):
    return spec("M1", direction="short", entry=SHORT_ENTRY, **over)


QUIET = [(100, 100.1, 99.9, 100)] * 3
SHORT_SIGNAL = [(100, 100.1, 98.9, 99.0)]
SHORT_FILL = [(99.0, 99.1, 98.9, 99.0)]


def assert_same_trade(report, backtest_trades, **expect):
    trades = closed_events(report)
    assert len(trades) == len(backtest_trades) == 1
    ours, theirs = trades[0], backtest_trades[0]
    assert ours["entry"] == pytest.approx(theirs["entry_price"]) and ours["exit"] == pytest.approx(theirs["exit_price"])
    assert ours["net_pnl"] == pytest.approx(theirs["pnl"]) and ours["exit_reason"] == theirs["exit_reason"]
    for key, value in expect.items():
        if isinstance(value, float):
            assert ours[key] == pytest.approx(value), key
        else:
            assert ours[key] == value, key
    return ours


def closed_events(report):
    return [e["detail"] for e in report["events"] if e["kind"] == "POSITION_CLOSED"]


def test_short_enters_on_bid_exits_on_ask_and_takes_profit_without_slippage(tmp_path):
    rows = QUIET + SHORT_SIGNAL + SHORT_FILL + [(99.0, 99.5, 96.5, 97.0)] + QUIET
    report, trades, _ = both(tmp_path, short_spec(), rows)
    deal = assert_same_trade(report, trades, side="short", exit_reason="TAKE_PROFIT", quote="ASK", ambiguous=False)
    assert deal["entry"] == pytest.approx(99.0 - SLIP)                     # Bid open minus one point
    assert deal["exit"] == pytest.approx(99.0 - SLIP - 2.0)               # target 2R, no slippage, hit on Ask low (96.3)
    quantity = deal["quantity"]
    assert quantity == pytest.approx(10.0)                                 # 1% of 1000 / stop distance 1.0
    assert deal["commission"] == pytest.approx(2 * quantity * FEE)
    assert deal["net_pnl"] == pytest.approx(2.0 * quantity - 2 * quantity * FEE)


def test_short_stop_loss_is_tested_on_ask_with_slippage_and_wins_over_target(tmp_path):
    both_levels = (99.0, 99.9, 96.5, 97.0)                                 # Ask high 100.1 >= stop and Ask low 96.3 <= target
    rows = QUIET + SHORT_SIGNAL + SHORT_FILL + [both_levels] + QUIET
    report, trades, _ = both(tmp_path, short_spec(), rows)
    deal = assert_same_trade(report, trades, side="short", exit_reason="STOP_LOSS", ambiguous=True)
    assert deal["exit"] == pytest.approx(max(99.0 + SPREAD, deal["stop"]) + SLIP)
    assert deal["net_pnl"] < 0


def test_gap_through_the_stop_fills_at_the_gapped_open_not_at_the_stop(tmp_path):
    rows = QUIET + SHORT_SIGNAL + SHORT_FILL + [(101.0, 101.2, 100.9, 101.0)] + QUIET
    report, trades, _ = both(tmp_path, short_spec(), rows)
    deal = assert_same_trade(report, trades, exit_reason="STOP_LOSS")
    assert deal["exit"] == pytest.approx(101.0 + SPREAD + SLIP)            # Ask open of the gap candle + slippage
    assert deal["exit"] > deal["stop"] + 1.0


def test_long_exit_uses_bid_and_a_gap_up_through_the_target_fills_at_the_open(tmp_path):
    rows = QUIET + [(100, 101.1, 99.9, 101)] + [(101, 101.2, 100.9, 101.0)] + [(104.0, 104.5, 103.9, 104.2)] + QUIET
    report, trades, _ = both(tmp_path, spec("M1", direction="long", entry=LONG_ENTRY), rows)
    deal = assert_same_trade(report, trades, side="long", exit_reason="TAKE_PROFIT", quote="BID")
    assert deal["entry"] == pytest.approx(101.0 + SPREAD + SLIP)
    assert deal["exit"] == pytest.approx(104.0)                            # Bid open of the gap candle, above the target


def test_pending_stop_gap_fill_touch_fill_and_expiry_match_the_backtest(tmp_path):
    order = {"type": "stop", "offset_atr": 0.5, "atr_period": 5, "expiry_bars": 2}
    strategy = spec("M1", direction="long", entry=LONG_ENTRY, order=order)
    for name in ("touch", "gap", "expire"):
        (tmp_path / name).mkdir()
    # touch inside the first candle after the signal: fill at the order price plus slippage
    rows = QUIET + [(100, 101.1, 99.9, 101)] + [(101.0, 101.6, 100.9, 101.2)] + QUIET
    report, trades, _ = both(tmp_path / "touch", strategy, rows)
    submitted = next(e["detail"] for e in report["events"] if e["kind"] == "ORDER_SUBMITTED")
    filled = next(e["detail"] for e in report["events"] if e["kind"] == "ORDER_FILLED")
    assert filled["fill_assumption"] == "QUOTE_SIDE_STOP_OR_WORSE_OPEN" and filled["quote"] == "ASK"
    assert filled["entry"] == pytest.approx(submitted["planned_price"] + SLIP)
    assert trades and trades[0]["entry_price"] == pytest.approx(filled["entry"])
    # gap above the order price: the open (Ask) is the fill, never the better planned price
    rows = QUIET + [(100, 101.1, 99.9, 101)] + [(103.0, 103.4, 102.9, 103.2)] + QUIET
    report, trades, _ = both(tmp_path / "gap", strategy, rows)
    filled = next(e["detail"] for e in report["events"] if e["kind"] == "ORDER_FILLED")
    assert filled["entry"] == pytest.approx(103.0 + SPREAD + SLIP) and trades[0]["entry_price"] == pytest.approx(filled["entry"])
    # never touched inside expiry_bars=2: cancelled on the third candle (before that candle can fill), nothing filled
    rows = QUIET + [(100, 101.1, 99.9, 101)] + [(100.6, 100.8, 100.5, 100.6)] * 2 + [(100.9, 105, 100.7, 101)] + QUIET
    report, trades, _ = both(tmp_path / "expire", strategy, rows)
    kinds = [(e["kind"], e["candle_at"][11:16]) for e in report["events"]]
    assert ("ORDER_CANCELLED", "12:06") in kinds
    assert not [k for k, at in kinds if k == "ORDER_FILLED" and at <= "12:06"] and not trades


def test_limit_fill_is_the_better_of_open_and_limit_with_no_slippage(tmp_path):
    order = {"type": "limit", "offset_atr": 0.4, "atr_period": 5, "expiry_bars": 3}
    strategy = spec("M1", direction="long", entry=LONG_ENTRY, order=order)
    rows = QUIET + [(100, 101.1, 99.9, 101)] + [(101.0, 101.0, 99.0, 100.5)] + QUIET
    report, trades, _ = both(tmp_path, strategy, rows)
    submitted = next(e["detail"] for e in report["events"] if e["kind"] == "ORDER_SUBMITTED")
    filled = next(e["detail"] for e in report["events"] if e["kind"] == "ORDER_FILLED")
    assert filled["fill_assumption"] == "QUOTE_SIDE_LIMIT_OR_BETTER_OPEN"
    assert filled["entry"] == pytest.approx(min(101.0 + SPREAD, submitted["planned_price"]))
    assert trades[0]["entry_price"] == pytest.approx(filled["entry"])


def test_time_exit_pays_slippage_and_takes_the_next_bar_open(tmp_path):
    strategy = spec("M1", direction="long", entry=LONG_ENTRY, time_exit_bars=2, target=None)
    rows = QUIET + [(100, 101.1, 99.9, 101)] + [(101.0, 101.2, 100.3, 100.4)] * 4 + QUIET
    report, trades, _ = both(tmp_path, strategy, rows)
    deal = assert_same_trade(report, trades, exit_reason="TIME_EXIT", quote="BID")
    assert deal["exit"] == pytest.approx(101.0 - SLIP)
    assert deal["fill_assumption"] == "TIME_EXIT_NEXT_BAR_OPEN"


def test_contract_value_maths_and_slippage_points():
    contract = ContractSpec("USD", 100.0, 0.01, 1.0, 0.01, 3.5, 3.0)
    pnl = execution.position_pnl(side="long", quantity_lots=0.5, entry=2000.0, exit=2001.5, contract=contract)
    assert pnl["gross_pnl"] == pytest.approx(1.5 * 0.5 * 100)
    assert pnl["commission"] == pytest.approx(2 * 0.5 * 3.5) and pnl["net_pnl"] == pytest.approx(75 - 3.5)
    assert pnl["tick_value"] == pytest.approx(0.01 * 100 * 1.0)
    jpy_like = ContractSpec("USD", 100000.0, 0.001, 1 / 150.0, 0.01)
    short = execution.position_pnl(side="short", quantity_lots=1, entry=150.0, exit=149.0, contract=jpy_like)
    assert short["gross_pnl"] == pytest.approx(1.0 * 100000 / 150.0)
    assert execution.fill_market("long", {"open": 10.0}, {"open": 10.2}, 3, 0.01) == pytest.approx(10.23)
    assert execution.fill_market("short", {"open": 10.0}, {"open": 10.2}, 3, 0.01) == pytest.approx(9.97)
    with pytest.raises(ValueError):
        ContractSpec("USD", 0, 0.01, 1, 0.01).validate()
    with pytest.raises(ValueError):
        execution.position_pnl(side="flat", quantity_lots=1, entry=1, exit=1, contract=contract)


# ----------------------------------------------------------------------------- 4. backtest/forward parity

def test_forward_parity_survives_closed_days_and_a_sliding_default_buffer(tmp_path):
    """Weekend-style closed day in the middle, default 2N buffer cap, backtest started far earlier."""
    strategy = {**rich_spec({"type": "market"}), "timeframe": "M5"}
    store = DailyPartitionStore(tmp_path / "f.sqlite")
    warm_bid, warm_spread = random_walk(DAY - timedelta(days=1), 1440, 99, start="00:00")
    ingest(store, "XAUUSD", DAY - timedelta(days=1), warm_bid, pd.Series(warm_spread, index=warm_bid.index))
    engine = ForwardEngine(ForwardRegistry(store))
    portfolio = engine.create("Parity", currency="USD", starting_capital=10000, risk_limit=0.5)
    registration = make_registration(store, strategy, params={"low_level": 25}, capital=10000, limit=0.5, leverage=1000)
    assert registration["configuration"]["warmup"]["buffer_cap"] == 2 * registration["configuration"]["warmup"]["required_bars"]
    engine.enroll(portfolio, registration["id"], joined_at=FROZEN)
    bids, spreads = [warm_bid], [warm_spread]
    for offset in range(5):
        day = DAY + timedelta(days=offset)
        if offset == 2:                                                     # closed market day
            empty = pd.DataFrame({c: pd.Series(dtype="float64") for c in ("open", "high", "low", "close", "volume")},
                                 index=pd.DatetimeIndex([], tz="UTC"))
            assert store.ingest("XAUUSD", day, empty, empty.copy(), expected_minutes=empty.index,
                                observed_at=observed(day))["status"] == "MARKET CLOSED"
        else:
            bid, spread = random_walk(day, 600, offset)
            ingest(store, "XAUUSD", day, bid, pd.Series(spread, index=bid.index))
            bids.append(bid), spreads.append(spread)
        assert engine.advance(portfolio, as_of=observed(day))["committed_days"] == [day.isoformat()]
    bid = pd.concat(bids)
    ask = bid.copy()
    ask.iloc[:, :4] += np.concatenate(spreads)[:, None]
    bars = lambda frame: frame.resample("5min", label="left", closed="left", origin="start_day").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    result = backtest.run_rules_backtest(strategy, bars(bid), bars(ask), {"low_level": 25}, capital=10000,
                                         contract=ContractSpec(**CONTRACT), trade_from=pd.Timestamp(f"{DAY}T12:00Z"))
    expected = [t for t in result["trades"] if t["exit_reason"] != "END_OF_DATA"]
    actual = closed(engine, portfolio)
    assert len(expected) >= 10 and len(actual) == len(expected)
    for ours, theirs in zip(actual, expected):
        assert pd.Timestamp(ours["entry_at"]) == pd.Timestamp(theirs["entry_time"])
        assert ours["side"] == theirs["side"] and ours["entry"] == pytest.approx(theirs["entry_price"])
        assert pd.Timestamp(ours["exit_at"]).floor("5min") == pd.Timestamp(theirs["exit_time"]) or \
            ours["exit_reason"] != theirs["exit_reason"]


def test_scale_in_specs_are_refused_by_the_forward_model(tmp_path):
    """The backtest adds to winners; the forward run never does, so the strategy must not be frozen at all."""
    store = DailyPartitionStore(tmp_path / "f.sqlite")
    ingest(store, "XAUUSD", DAY - timedelta(days=1), flat_day(DAY - timedelta(days=1)))
    scaled = spec("M1", scale_in={"max_adds": 2, "add_every_r": 0.5, "add_size_fraction": 0.5})
    assert rule_engine.validate_spec(scaled) == []
    with pytest.raises(DataError, match="scale_in"):
        make_registration(store, scaled)
    plain = make_registration(store, spec("M1"))
    tampered = copy.deepcopy(plain["configuration"])
    tampered["spec"]["scale_in"] = {"max_adds": 1, "add_every_r": 1.0, "add_size_fraction": 0.5}
    tampered["spec_sha256"] = rule_engine.spec_sha256(tampered["spec"])
    with pytest.raises(DataError, match="scale_in"):
        forward_rules.validate_registration({"configuration": tampered, "sources": plain["sources"],
                                             "frozen_at": plain["frozen_at"]})


@pytest.mark.parametrize("symbol", ["DE40", "US500", "US30", "USTEC"])
def test_every_display_symbol_survives_freeze_and_advance(tmp_path, symbol):
    """provider_symbol() rejects its own DE40/US500 output; the frozen config keeps the provider name."""
    store, engine, portfolio, ids = setup(tmp_path / "f.sqlite", [spec("M1", symbol=symbol)])
    engine._fx = SimpleNamespace(rate=lambda *a, **k: SimpleNamespace(rate=1.1, source="TEST", data_version="t"))
    frozen = ForwardRegistry(store).get(ids[0])["configuration"]["symbol"]
    assert provider_symbol(data_symbol(frozen)) == frozen
    rows = ([(100, 100.1, 99.9, 100)] * 3 + [(100, 101.1, 99.9, 101), (101, 101.2, 100.9, 101.0),
                                              (101.0, 101.3, 100.8, 101.1), (101.1, 104.0, 101.0, 103.5)] + [(100, 100.1, 99.9, 100)] * 3)
    ingest(store, symbol, DAY, path_day(DAY, rows))
    out = engine.advance(portfolio, as_of=observed(DAY))
    assert out["committed_days"] == [DAY.isoformat()]
    assert len(closed(engine, portfolio)) == 1
    # the performance view re-reads the committed partition by the provider name
    summary = summarize_forward(engine.report(portfolio), model_current=True, data=store)
    assert summary["portfolio"]["excursion_covered"] == 1 and summary["portfolio"]["excursion_unverified"] == 0


# ----------------------------------------------------------------------------- 5. restart idempotence

def signature(engine, portfolio):
    report = engine.report(portfolio)
    return ([(e["kind"], e["day"], e["candle_at"], e["registration_id"], e["detail"]) for e in report["events"]],
            [m["state"] for m in report["members"]], report["portfolio"],
            [(d["day"], d["balance"], d["equity"]) for d in report["days"]])


def prepared(tmp_path, days=4, minutes=200):
    path = tmp_path / "base.sqlite"
    strategy = rich_spec({"type": "stop", "offset_atr": 0.3, "atr_period": 5, "expiry_bars": 3})
    store = DailyPartitionStore(path)
    warm_bid, warm_spread = random_walk(DAY - timedelta(days=1), 1440, 99, start="00:00")
    ingest(store, "XAUUSD", DAY - timedelta(days=1), warm_bid, pd.Series(warm_spread, index=warm_bid.index))
    engine = ForwardEngine(ForwardRegistry(store))
    portfolio = engine.create("Idem", currency="USD", starting_capital=10000, risk_limit=0.5)
    registration = make_registration(store, strategy, params={"low_level": 25}, capital=10000, limit=0.5, leverage=1000, cap=5000)
    engine.enroll(portfolio, registration["id"], joined_at=FROZEN)
    for offset in range(days):
        day = DAY + timedelta(days=offset)
        bid, spread = random_walk(day, minutes, offset)
        ingest(store, "XAUUSD", day, bid, pd.Series(spread, index=bid.index))
    return path, portfolio, [DAY + timedelta(days=i) for i in range(days)]


def reopen(path):
    return ForwardEngine(ForwardRegistry(DailyPartitionStore(path)))


def clone(path, name):
    target = path.parent / f"{name}.sqlite"
    shutil.copy(path, target)
    return target


def test_restart_double_processing_batch_crash_and_threads_all_equal_the_continuous_run(tmp_path):
    base, portfolio, days = prepared(tmp_path)
    continuous = clone(base, "continuous")
    engine = reopen(continuous)
    for day in days:
        engine.advance(portfolio, as_of=observed(day))
    reference = signature(engine, portfolio)
    trade_ids = [(e[4]["entry_at"], e[4]["side"]) for e in reference[0] if e[0] == "POSITION_CLOSED"]
    assert len(trade_ids) >= 3 and len(trade_ids) == len(set(trade_ids))

    # a fresh engine before every day plus an immediate second advance of the same day
    path = clone(base, "restart")
    for day in days:
        assert reopen(path).advance(portfolio, as_of=observed(day))["committed_days"] == [day.isoformat()]
        assert reopen(path).advance(portfolio, as_of=observed(day))["committed_days"] == []
    assert signature(reopen(path), portfolio) == reference

    # every day in one call
    path = clone(base, "batch")
    assert len(reopen(path).advance(portfolio, as_of=observed(days[-1]), max_days=31)["committed_days"]) == len(days)
    assert signature(reopen(path), portfolio)[:2] == reference[:2]

    # crash while committing the third day, then resume
    path = clone(base, "crash")
    engine = reopen(path)
    for day in days[:2]:
        engine.advance(portfolio, as_of=observed(day))
    before = signature(engine, portfolio)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TRIGGER crash BEFORE INSERT ON forward_days BEGIN SELECT RAISE(ABORT, 'crash'); END")
    with pytest.raises(sqlite3.IntegrityError):
        engine.advance(portfolio, as_of=observed(days[2]))
    assert signature(reopen(path), portfolio) == before
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TRIGGER crash")
    engine = reopen(path)
    for day in days[2:]:
        engine.advance(portfolio, as_of=observed(day))
    assert signature(engine, portfolio) == reference

    # three concurrent advancers: every day committed exactly once
    path = clone(base, "threads")
    committed, errors = [], []

    def work():
        try:
            committed.append(reopen(path).advance(portfolio, as_of=observed(days[-1]), max_days=31)["committed_days"])
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))
    threads = [threading.Thread(target=work) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    flat = [d for c in committed for d in c]
    assert not errors and sorted(flat) == [d.isoformat() for d in days]
    assert signature(reopen(path), portfolio)[:2] == reference[:2]


def test_a_later_revision_of_a_processed_day_never_changes_the_forward_ledger(tmp_path):
    base, portfolio, days = prepared(tmp_path, days=3)
    plain = clone(base, "plain")
    engine = reopen(plain)
    engine.advance(portfolio, as_of=observed(days[-1]) + timedelta(days=1), max_days=31)
    reference = signature(engine, portfolio)
    path = clone(base, "revised")
    store = DailyPartitionStore(path)
    symbol = ForwardRegistry(store).get(next(iter(engine.report(portfolio)["members"]))["registration_id"])["configuration"]["symbol"]
    frame, _ = store.read(symbol, days[1], days[1], as_of=observed(days[-1]))
    bid = frame[[f"bid_{c}" for c in ("open", "high", "low", "close", "volume")]].rename(columns=lambda c: c[4:]) + 1.0
    ask = frame[[f"ask_{c}" for c in ("open", "high", "low", "close", "volume")]].rename(columns=lambda c: c[4:]) + 1.0
    revision = store.ingest("XAUUSD", days[1], bid, ask, expected_minutes=bid.index,
                            observed_at=observed(days[1]) + timedelta(hours=3))
    assert revision["revision_detected"]
    engine = reopen(path)
    engine.advance(portfolio, as_of=observed(days[-1]) + timedelta(days=1), max_days=31)
    assert signature(engine, portfolio)[:2] == reference[:2]
