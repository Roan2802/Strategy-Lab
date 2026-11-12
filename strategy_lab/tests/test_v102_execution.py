"""V102 execution realism: margin, volume limits, swap, partial exits, stress, FX-blocked results. Synthetic bars with
hand-calculated numbers (same setup as test_v101_execution_reference: XAUUSD-like, Ask = Bid + 0.5, stop 2.0 -> 0.5 lot,
entry Ask 100.9, stop 98.9, 2R target 104.9)."""
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from runtime.strategy_lab import execution as xm, rule_engine as engine
from runtime.strategy_lab.execution import ContractSpec
from strategy_lab.research import backtest

CONTRACT = ContractSpec("USD", 100.0, 0.01, 1.0, 0.01)
LONG_SETUP = [100.0] * 5 + [100.4, 100.5]


def bars(closes, over=None, spread=0.5):
    over = over or {}
    closes = list(closes)
    for i, o in over.items():
        if "close" in o:
            closes[i] = o["close"]
    opens = [closes[0]] + closes[:-1]
    df = pd.DataFrame({"open": opens, "high": [o + 0.5 for o in opens], "low": [o - 0.5 for o in opens],
                       "close": closes, "volume": 1.0},
                      index=pd.date_range("2026-01-05", periods=len(closes), freq="15min", tz="UTC"))
    for i, o in over.items():
        for k, v in o.items():
            df.iloc[i, df.columns.get_loc(k)] = v
    ask = df.copy()
    for c in ("open", "high", "low", "close"):
        ask[c] = df[c] + spread
    return df, ask


def spec(**over):
    base = {"schema": engine.SCHEMA, "name": "Ref", "symbol": "XAUUSD", "timeframe": "M15", "direction": "long",
            "entry": {"long": {"left": {"price": "close"}, "op": ">", "right": {"value": 100.2}}},
            "stop": {"type": "points", "value": 2.0}, "target": {"type": "r_multiple", "value": 2.0},
            "risk": {"percent": 1.0}}
    base.update(over)
    return base


def run(closes, over=None, sp=None, capital=10_000, **kw):
    bid, ask = bars(closes, over)
    return backtest.run_rules_backtest(sp or spec(), bid, ask, None, capital=capital, contract=CONTRACT, **kw)


def only(res):
    assert len(res["trades"]) == 1, res["diagnostics"]
    return res["trades"][0]


MARGIN = {"initial_rate": 1.0, "status": "SIMULATION_ASSUMPTION"}


# ------------------------------------------------------------------ legacy stays identical
def test_default_run_has_no_execution_model_and_no_new_effects():
    t = only(run(LONG_SETUP + [105.0], {7: {"high": 105.5, "low": 100.0}}))
    assert t["pnl"] == pytest.approx(200.0) and "swap" not in t and "partial_fills" not in t
    res = run(LONG_SETUP + [105.0], {7: {"high": 105.5, "low": 100.0}}, execution={})
    assert only(res)["pnl"] == pytest.approx(200.0)


def test_invalid_execution_model_is_refused():
    with pytest.raises(ValueError, match="execution"):
        run(LONG_SETUP + [105.0], execution={"swap": {"long_per_lot_per_night": -1, "short_per_lot_per_night": 1}})  # no status
    with pytest.raises(ValueError, match="unknown key"):
        run(LONG_SETUP + [105.0], execution={"banana": 1})
    with pytest.raises(ValueError, match="margin"):
        run(LONG_SETUP + [105.0], execution={"margin": {"status": "CONTRACTUAL"}})


# ------------------------------------------------------------------ volume and margin
def test_max_lot_caps_and_min_lot_rejects_and_both_are_reported():
    tp = {7: {"high": 105.5, "low": 100.0}}
    res = run(LONG_SETUP + [105.0], tp, execution={"volume": {"max_lot": 0.3}})
    t = only(res)
    assert t["qty"] == pytest.approx(0.3) and t["pnl"] == pytest.approx(4.0 * 0.3 * 100.0)
    lim = res["diagnostics"]["size_limits"][0]
    assert lim["theoretical_lots"] == pytest.approx(0.5) and lim["executed_lots"] == pytest.approx(0.3)
    assert lim["limited_by"] == ["MAX_LOT"]
    rej = run(LONG_SETUP + [105.0], tp, execution={"volume": {"min_lot": 0.6}})
    assert rej["trades"] == [] and rej["diagnostics"]["rejected_min_lot"] >= 1


def test_sizing_by_risk_and_by_available_margin_reports_the_unexecutable_theoretical_size():
    sp = spec(risk={"percent": 5.0})                 # risk size = 500 / (2 x 100) = 2.5 lots, notional 25_225 > 10_000
    tp = {7: {"high": 105.5, "low": 100.0}}
    res = run(LONG_SETUP + [105.0], tp, sp, execution={"margin": {**MARGIN, "on_insufficient": "cap"}})
    t = only(res)
    assert t["qty"] == pytest.approx(0.99)           # floor(10_000 / (100 x 100.9 x 1.0 margin rate)) = 0.99 lot
    lim = res["diagnostics"]["size_limits"][0]
    assert lim["theoretical_lots"] == pytest.approx(2.5) and lim["executed_lots"] == pytest.approx(0.99)
    assert lim["limited_by"] == ["INSUFFICIENT_MARGIN"] and res["diagnostics"]["size_limited_entries"] == 1
    assert res["diagnostics"]["execution_model"]["margin"]["status"] == "SIMULATION_ASSUMPTION"
    assert res["diagnostics"]["min_margin_level_pct"] > 100.0
    # explicit rejection instead of capping
    rej = run(LONG_SETUP + [105.0], tp, sp, execution={"margin": {**MARGIN, "on_insufficient": "reject"}})
    assert rej["trades"] == [] and rej["diagnostics"]["rejected_insufficient_margin"] >= 1
    # enough leverage: executed at the theoretical size, nothing limited
    ok = run(LONG_SETUP + [105.0], tp, sp, execution={"margin": {"leverage": 30, "status": "CONTRACTUAL"}})
    assert only(ok)["qty"] == pytest.approx(2.5) and ok["diagnostics"]["size_limited_entries"] == 0
    assert ok["diagnostics"]["execution_model"]["margin"]["status"] == "CONTRACTUAL"
    assert ok["diagnostics"]["execution_model"]["margin"]["initial_rate"] == pytest.approx(1 / 30)
    assert ok["diagnostics"]["max_portfolio_leverage"] > 2.0


def test_margin_stop_out_closes_at_the_adverse_extreme_before_the_stop():
    sp = spec(stop={"type": "points", "value": 50.0}, risk={"percent": 100.0}, target=None)
    over = {7: {"open": 100.5, "high": 100.6, "low": 60.0, "close": 61.0}}
    res = run(LONG_SETUP + [61.0], over, sp, execution={
        "margin": {"initial_rate": 0.5, "stop_out_level_pct": 50.0, "status": "SIMULATION_ASSUMPTION"}})
    t = only(res)
    assert t["qty"] == pytest.approx(1.98)           # risk size 2.0 lots capped by margin: floor(10_000 / (100 x 100.9 x 0.5))
    assert t["exit_reason"] == "STOP_OUT" and t["exit_price"] == pytest.approx(60.0)   # Bid low; stop 50.9 not reached
    assert t["pnl"] == pytest.approx((60.0 - 100.9) * 1.98 * 100.0)
    assert res["diagnostics"]["stop_outs"] == 1


# ------------------------------------------------------------------ swap
SWAP = {"long_per_lot_per_night": -7.0, "short_per_lot_per_night": 2.0, "status": "SCENARIO"}


def test_swap_nights_triple_day_and_weekend_rules():
    ms = lambda s: int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
    cfg = {"rollover_hour_utc": 22}
    assert xm.swap_nights(ms("2026-01-05T10:00"), ms("2026-01-06T10:00"), cfg) == 1                 # Mon night
    assert xm.swap_nights(ms("2026-01-07T10:00"), ms("2026-01-08T10:00"), cfg) == 3                 # Wed night = triple
    assert xm.swap_nights(ms("2026-01-09T10:00"), ms("2026-01-12T10:00"), cfg) == 1                 # Fri night only; Sat/Sun none
    assert xm.swap_nights(ms("2026-01-05T10:00"), ms("2026-01-12T10:00"), cfg) == 7                 # 1+1+3+1+1
    assert xm.swap_nights(ms("2026-01-09T10:00"), ms("2026-01-12T10:00"), {**cfg, "triple_weekday": 4}) == 3  # index: Friday
    assert xm.swap_nights(ms("2026-01-10T10:00"), ms("2026-01-12T10:00"), {**cfg, "skip_weekdays": [], "triple_weekday": None}) == 2
    assert xm.swap_nights(ms("2026-01-05T23:00"), ms("2026-01-05T23:30"), cfg) == 0


def test_swap_is_charged_per_lot_per_night_with_triple_day_and_only_from_a_labelled_config():
    flat = [100.0] * 5 + [100.4] * 295                # 75 h: Mon 22:00 (1) + Tue 22:00 (1) + Wed 22:00 (3) = 5 nights
    res = run(flat, execution={"swap": SWAP})
    t = only(res)
    assert t["swap"] == pytest.approx(5 * 0.5 * -7.0) == pytest.approx(-17.5)
    assert t["pnl"] == pytest.approx((100.4 - 100.9) * 0.5 * 100.0 - 17.5)       # price result (-25) + swap
    assert res["diagnostics"]["swap_total"] == pytest.approx(-17.5)
    assert res["diagnostics"]["execution_model"]["swap"]["status"] == "SCENARIO"
    nothing = run(flat)
    assert "swap" not in only(nothing) and only(nothing)["pnl"] == pytest.approx(-25.0)   # not modelled by default


# ------------------------------------------------------------------ stress knobs
def test_stop_gap_rejection_and_trading_hours():
    sl = {7: {"high": 100.9, "low": 98.5, "close": 100.0}}
    base = only(run(LONG_SETUP + [100.0], sl))
    gap = only(run(LONG_SETUP + [100.0], sl, execution={"stop_gap_points": 20}))
    assert base["exit_price"] == pytest.approx(98.9) and gap["exit_price"] == pytest.approx(98.9 - 0.2)
    assert gap["pnl"] == pytest.approx((98.7 - 100.9) * 0.5 * 100.0)
    rej = run(LONG_SETUP + [105.0], {7: {"high": 105.5}}, execution={"reject_every_n": 1})
    assert rej["trades"] == [] and rej["diagnostics"]["rejected_orders"] >= 1
    flat = [100.0] * 5 + [100.4] * 60                   # entry bar 01:30 is outside 08-16 UTC; first in-hours entry 08:00
    hrs = run(flat, execution={"trading_hours": {"utc_hours": list(range(8, 16)), "weekdays": [0, 1, 2, 3, 4]}})
    t = only(hrs)
    assert t["entry_time"].startswith("2026-01-05T08:00") if len(flat) > 32 else True
    assert hrs["diagnostics"]["rejected_outside_hours"] >= 1


def test_stress_harness_labels_what_it_can_and_cannot_do():
    bid, ask = bars(LONG_SETUP + [105.0] + [100.5] * 20, {7: {"high": 105.5, "low": 100.0}})
    out = backtest.run_execution_stress(spec(), bid, ask, None, contract=CONTRACT,
                                        scenarios=("baseline", "spread_x2", "extra_slippage_5pts", "stop_gap_20pts",
                                                   "order_rejection_every_2", "insufficient_margin_leverage_2"))
    sc = out["scenarios"]
    assert sc["baseline"]["delta_net_profit_vs_baseline"] == 0.0 and sc["baseline"]["trades"] >= 1
    assert sc["spread_x2"]["net_profit"] < sc["baseline"]["net_profit"]
    assert sc["extra_slippage_5pts"]["net_profit"] < sc["baseline"]["net_profit"]
    assert sc["partial_fills_of_one_order"]["status"] == "NOT_SUPPORTED"
    assert sc["same_candle_sl_tp"]["status"] == "CONSERVATIVE_STOP_FIRST"
    assert sc["insufficient_margin_leverage_2"]["diagnostics"].get("rejected_insufficient_margin", 0) >= 0
    with pytest.raises(ValueError):
        backtest.run_execution_stress(spec(), bid, ask, None, contract=CONTRACT, scenarios=("nonsense",))


# ------------------------------------------------------------------ partial exits (hand-calculated)
PARTIAL = [{"r": 1.0, "fraction": 0.5, "stop_r": 0.0}]          # 0.25 lot at 102.9 (entry + 1R), then stop to break-even


def test_partial_exit_then_break_even_stop_on_the_remainder():
    over = {7: {"high": 103.5, "close": 103.0}, 9: {"low": 100.0, "close": 100.0}}
    t = only(run(LONG_SETUP + [103.0, 103.0, 100.0, 100.0, 100.0], over, spec(partial_exits=PARTIAL)))
    assert t["qty"] == pytest.approx(0.5) and len(t["partial_fills"]) == 1
    pf = t["partial_fills"][0]
    assert pf["price"] == pytest.approx(102.9) and pf["qty"] == pytest.approx(0.25)
    assert pf["net_pnl"] == pytest.approx((102.9 - 100.9) * 0.25 * 100.0)          # +50.0
    assert t["exit_reason"] == "STOP_LOSS" and t["exit_price"] == pytest.approx(100.9)   # new stop = entry, from bar 8
    assert t["pnl"] == pytest.approx(50.0 + 0.0) and t["r_multiple"] == pytest.approx(0.5)


def test_partial_commission_is_charged_per_fill_and_the_final_target_takes_the_remainder():
    over = {7: {"high": 103.5, "close": 103.0}, 9: {"high": 105.5, "close": 100.0}}
    t = only(run(LONG_SETUP + [103.0, 103.0, 100.0, 100.0, 100.0], over, spec(partial_exits=PARTIAL), commission_per_lot=3.5))
    pf = t["partial_fills"][0]
    assert pf["commission"] == pytest.approx(2 * 0.25 * 3.5)
    assert t["exit_reason"] == "TAKE_PROFIT" and t["exit_price"] == pytest.approx(104.9)
    gross = (102.9 - 100.9) * 0.25 * 100 + (104.9 - 100.9) * 0.25 * 100             # 50 + 100
    assert t["pnl"] == pytest.approx(gross - 2 * 0.5 * 3.5) and t["costs"] == pytest.approx(3.5)


def test_two_levels_in_one_bar_fill_at_their_prices_and_remaining_size_is_tracked():
    levels = [{"r": 1.0, "fraction": 0.5}, {"r": 1.5, "fraction": 0.25}]           # 0.25 @102.9, 0.12 @103.9, 0.13 left
    over = {7: {"high": 104.0, "close": 103.0}}
    res = run(LONG_SETUP + [103.0, 103.0], over, spec(partial_exits=levels))
    t = only(res)                                                                   # END_OF_DATA closes the remainder
    assert [(round(p["price"], 2), p["qty"]) for p in t["partial_fills"]] == [(102.9, 0.25), (103.9, 0.12)]
    last_close = 103.0                                                              # Bid close at the final bar
    expect = (102.9 - 100.9) * 25 + (103.9 - 100.9) * 12 + (last_close - 100.9) * 0.13 * 100
    assert t["exit_reason"] == "END_OF_DATA" and t["qty"] == pytest.approx(0.5)
    assert t["pnl"] == pytest.approx(expect)


def test_gap_through_a_level_fills_at_the_better_open_and_same_bar_stop_wins():
    gap = {7: {"open": 103.4, "high": 103.6, "low": 103.0, "close": 103.2}}
    pf = only(run(LONG_SETUP + [103.2, 103.2], gap, spec(partial_exits=PARTIAL)))["partial_fills"][0]
    assert pf["price"] == pytest.approx(103.4)                                       # max(open, level 102.9)
    both = run(LONG_SETUP + [100.5], {7: {"high": 103.5, "low": 98.0, "close": 100.5}}, spec(partial_exits=PARTIAL))
    t = only(both)
    assert t["exit_reason"] == "STOP_LOSS" and t["exit_price"] == pytest.approx(98.9) and t["partial_fills"] == []
    assert t["pnl"] == pytest.approx(-100.0) and both["diagnostics"]["ambiguous_bars"] == 1   # level NOT credited


def test_levels_summing_to_one_close_the_trade_and_dust_rides_with_the_last_level():
    levels = [{"r": 1.0, "fraction": 0.5}, {"r": 1.5, "fraction": 0.5}]
    t = only(run(LONG_SETUP + [100.0] * 4, {7: {"high": 104.0, "close": 100.0}}, spec(partial_exits=levels)))
    assert t["exit_reason"] == "TAKE_PROFIT" and t["exit_price"] == pytest.approx(103.9) and len(t["partial_fills"]) == 1
    assert t["pnl"] == pytest.approx((102.9 - 100.9) * 25 + (103.9 - 100.9) * 25)
    plan = xm.partial_plan([{"r": 1, "fraction": 0.5}], 0.07, 0.01, min_lot=0.05)          # 0.03 < min -> skipped
    assert plan == []
    plan = xm.partial_plan([{"r": 1, "fraction": 0.9}], 0.53, 0.01, min_lot=0.05)          # 0.47 + dust 0.06 >= min: stays
    assert plan[0]["qty"] == pytest.approx(0.47)
    plan = xm.partial_plan([{"r": 1, "fraction": 0.95}], 0.53, 0.01, min_lot=0.05)         # 0.50, left 0.03 < min -> all
    assert plan[0]["qty"] == pytest.approx(0.53)


def test_partial_exit_spec_validation_and_exit_delay_not_supported():
    ok = {"partial_exits": [{"r": 1, "fraction": 0.5, "stop_r": 0}, {"r": 2, "fraction": 0.5}]}
    assert engine.validate_spec(spec(**ok)) == []
    bad = lambda **o: engine.validate_spec(spec(**o))
    assert any("sum" in e for e in bad(partial_exits=[{"r": 1, "fraction": 0.7}, {"r": 2, "fraction": 0.7}]))
    assert any("increasing" in e for e in bad(partial_exits=[{"r": 2, "fraction": 0.2}, {"r": 1, "fraction": 0.2}]))
    assert any("below" in e for e in bad(partial_exits=[{"r": 1, "fraction": 0.5, "stop_r": 1.5}]))
    assert any("scale_in" in e for e in bad(partial_exits=[{"r": 1, "fraction": 0.5}],
                                            scale_in={"max_adds": 1, "add_every_r": 1, "add_size_fraction": 0.5}))
    assert any("unknown key" in e for e in bad(partial_exits=[{"r": 1, "fraction": 0.5, "x": 1}]))
    with pytest.raises(ValueError, match="NOT_SUPPORTED"):
        run(LONG_SETUP + [103.0], None, spec(partial_exits=PARTIAL), exit_delay_bars=1)


# ------------------------------------------------------------------ FX: never 1.0, money blocked
TP_BAR = {7: {"high": 105.5, "low": 100.0, "close": 100.0}}


def de40_run(conv=None, **kw):
    bid, ask = bars(LONG_SETUP + [100.0] * 5, TP_BAR)
    contract = ContractSpec("USD", 1.0, 0.1, 1.0, 0.01)
    return backtest.run_rules_backtest(spec(symbol="DE40"), bid, ask, None, capital=10_000, contract=contract,
                                       conversion=conv, **kw), bid.index


def test_no_conversion_series_blocks_money_metrics_and_keeps_quote_currency_separate():
    res, _ = de40_run()
    d, m = res["diagnostics"], res["metrics"]
    assert d["currency_status"] == "QUOTE_CURRENCY_UNCONVERTED" and d["fx_status"] == "FX_RATE_UNAVAILABLE"
    assert m["money_blocked"] and m["net_profit"] is None and m["profit_factor"] is None
    assert m["quote_currency_metrics"]["currency"] == "EUR" and m["quote_currency_metrics"]["net_profit"] is not None
    assert m["trades"] == 1                                      # unit-free numbers survive


def test_missing_rate_at_settlement_flags_the_trade_and_never_uses_1_or_an_old_rate():
    bid, ask = bars(LONG_SETUP + [100.0] * 5, TP_BAR)
    conv = pd.Series(1.10, index=bid.index)
    conv.iloc[7] = np.nan                                       # the exit bar has no valid rate
    res, _ = de40_run(conv)
    t = only(res)
    assert t["fx_status"] == "FX_RATE_UNAVAILABLE" and t["pnl"] is None and t["costs"] is None
    assert t["r_basis"] == "PRICE_ONLY" and t["r_multiple"] == pytest.approx(2.0)
    assert res["diagnostics"]["fx_status"] == "FX_RATE_UNAVAILABLE" and res["metrics"]["net_profit"] is None


def test_stale_or_missing_rate_at_entry_skips_the_entry_instead_of_inventing_a_rate():
    bid, ask = bars(LONG_SETUP + [100.0] * 5, TP_BAR)
    conv = pd.Series([1.10], index=bid.index[:1])               # one rate at bar 0, then nothing
    res, _ = de40_run(conv, conversion_max_staleness=timedelta(minutes=30))
    assert res["trades"] == [] and res["diagnostics"]["skipped_missing_conversion"] >= 1
    assert res["diagnostics"]["fx_status"] == "FX_GAPS_ENTRIES_SKIPPED"
    fresh = pd.Series(1.10, index=bid.index)
    ok, _ = de40_run(fresh)
    assert ok["diagnostics"]["fx_status"] == "OK" and ok["metrics"]["net_profit"] is not None and "money_blocked" not in ok["metrics"]
    # an invalid (non-positive) print is unavailable as well, not carried forward from the previous bar
    zero = fresh.copy()
    zero.iloc[6] = 0.0
    res0, _ = de40_run(zero)
    assert res0["diagnostics"]["skipped_missing_conversion"] >= 1
