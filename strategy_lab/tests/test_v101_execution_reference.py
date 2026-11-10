"""V101 execution-realism reference tests: known orders on hand-built Bid/Ask bars, every expected number is an
independent hand calculation (written as arithmetic, not read back from the engine).

Setup: XAUUSD-like contract (size 100, tick 0.01, lot step 0.01, USD), Ask = Bid + 0.5 on every price, risk 1% of
10_000 = 100 USD, stop 2.0 points -> 0.5 lot. Signal bar 5 closes 100.4 (> 100.2) so the market entry is the
Ask open of bar 6: 100.4 + 0.5 = 100.9; stop 98.9, target (2R) 104.9.
"""
import numpy as np
import pandas as pd
import pytest

from runtime.strategy_lab import rule_engine as engine
from runtime.strategy_lab.execution import ContractSpec, apply_ledger, conservative_ohlc_exit, position_pnl
from strategy_lab.research import backtest

CONTRACT = ContractSpec("USD", 100.0, 0.01, 1.0, 0.01)
E = 100.9  # Ask open of bar 6 (see module docstring)


def bars(closes, over=None, spread=0.5):
    """Standard bar i: open = previous close, high = open + 0.5, low = open - 0.5 (true range exactly 1.0), then
    per-bar overrides {i: {open, high, low, close}}. Ask = Bid + spread."""
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


LONG_SETUP = [100.0] * 5 + [100.4, 100.5]  # bars 0-6; bar 7+ appended per scenario


def run(closes, over=None, sp=None, capital=10_000, **kw):
    bid, ask = bars(closes, over)
    return backtest.run_rules_backtest(sp or spec(), bid, ask, None, capital=capital, contract=CONTRACT, **kw)


def only(res):
    assert len(res["trades"]) == 1, res["diagnostics"]
    return res["trades"][0]


# ------------------------------------------------------------------ fills: market, SL, TP, gap, ambiguity
def test_long_market_entry_on_ask_tp_exit_on_bid():
    t = only(run(LONG_SETUP + [105.0], {7: {"high": 105.5, "low": 100.0}}))
    assert t["entry_time"] == "2026-01-05T01:30:00+00:00"          # bar 6: next bar open after the bar-5 signal
    assert t["entry_price"] == pytest.approx(100.4 + 0.5)          # Ask open
    assert t["qty"] == pytest.approx(100.0 / (2.0 * 100.0))        # 1% of 10_000 / (stop 2.0 x contract 100) = 0.5
    assert t["exit_reason"] == "TAKE_PROFIT" and t["exit_price"] == pytest.approx(104.9)  # entry + 2 x 2.0, on Bid high
    assert t["pnl"] == pytest.approx((104.9 - 100.9) * 0.5 * 100.0)  # +200.0
    assert t["r_multiple"] == pytest.approx(2.0) and t["costs"] == 0.0


def test_stop_gap_fills_at_the_open_not_at_the_stop():
    t = only(run(LONG_SETUP + [98.0], {7: {"open": 98.0, "high": 98.5, "low": 97.5}}))
    assert t["exit_reason"] == "STOP_LOSS" and t["exit_price"] == pytest.approx(98.0)  # min(open 98.0, stop 98.9)
    assert t["pnl"] == pytest.approx((98.0 - 100.9) * 0.5 * 100.0)                     # -145.0 (worse than -1R)
    assert t["r_multiple"] == pytest.approx(-1.45)


def test_sl_and_tp_in_the_same_bar_resolves_conservatively_to_stop():
    res = run(LONG_SETUP + [100.5], {7: {"high": 105.5, "low": 98.0, "close": 100.5}})
    t = only(res)
    assert t["exit_reason"] == "STOP_LOSS" and t["exit_price"] == pytest.approx(98.9)  # min(open 100.5, stop 98.9)
    assert t["pnl"] == pytest.approx((98.9 - 100.9) * 0.5 * 100.0)                     # -100.0 = exactly -1R
    assert res["diagnostics"]["ambiguous_bars"] == 1


def test_short_entry_on_bid_exit_on_ask_and_spread_tightens_the_stop():
    sp = spec(direction="short", entry={"short": {"left": {"price": "close"}, "op": "<", "right": {"value": 99.8}}})
    setup = [100.0] * 5 + [99.6, 99.5]
    tp = only(run(setup + [95.0], {7: {"low": 94.5, "high": 99.9}}, sp))
    assert tp["entry_price"] == pytest.approx(99.6)                       # Bid open of bar 6: shorts sell on the Bid
    assert tp["exit_reason"] == "TAKE_PROFIT" and tp["exit_price"] == pytest.approx(95.6)  # 99.6 - 2 x 2.0, Ask low 95.0 <= 95.6
    assert tp["pnl"] == pytest.approx((99.6 - 95.6) * 0.5 * 100.0)        # +200.0
    # Bid high 101.2 never reaches the 101.6 stop, but the Ask high 101.7 does: the spread costs the short a stop-out
    sl = only(run(setup + [100.0], {7: {"open": 100.0, "high": 101.2, "low": 99.8, "close": 100.0}}, sp))
    assert sl["exit_reason"] == "STOP_LOSS" and sl["exit_price"] == pytest.approx(101.6)  # max(Ask open 100.5, 101.6)
    assert sl["pnl"] == pytest.approx((99.6 - 101.6) * 0.5 * 100.0)       # -100.0


# ------------------------------------------------------------------ slippage, commission, lot rounding, min lot
def test_slippage_is_adverse_on_market_entry_and_stop_but_not_on_take_profit():
    # 10 points x tick 0.01 = 0.10 per slipped fill
    sl = only(run(LONG_SETUP + [100.0], {7: {"high": 100.9, "low": 98.5, "close": 100.0}}, slippage_points=10))
    assert sl["entry_price"] == pytest.approx(100.9 + 0.1)                 # 101.0
    assert sl["exit_price"] == pytest.approx(99.0 - 0.1)                   # stop 101.0 - 2.0 = 99.0, slipped to 98.9
    assert sl["pnl"] == pytest.approx((98.9 - 101.0) * 0.5 * 100.0)        # -105.0
    assert sl["costs"] == pytest.approx(2 * 0.1 * 0.5 * 100.0)             # two slipped fills = 10.0 (informational)
    tp = only(run(LONG_SETUP + [105.0], {7: {"high": 105.5, "low": 100.0}}, slippage_points=10))
    assert tp["exit_price"] == pytest.approx(101.0 + 4.0)                  # TP is a limit-type fill: no slippage
    assert tp["pnl"] == pytest.approx((105.0 - 101.0) * 0.5 * 100.0)


def test_commission_is_per_lot_per_side():
    t = only(run(LONG_SETUP + [105.0], {7: {"high": 105.5, "low": 100.0}}, commission_per_lot=3.5))
    assert t["costs"] == pytest.approx(2 * 0.5 * 3.5)                      # 3.5 round turn for 0.5 lot
    assert t["pnl"] == pytest.approx(200.0 - 3.5)


def test_lot_is_rounded_down_to_the_step_and_risk_never_exceeds_the_budget():
    t = only(run(LONG_SETUP + [105.0], {7: {"high": 105.5, "low": 100.0}}, capital=10_750))
    assert t["qty"] == pytest.approx(0.53)                                 # 107.5 / 200 = 0.5375 -> floor 0.53
    assert t["qty"] * 2.0 * 100.0 <= 107.5
    assert t["pnl"] == pytest.approx(4.0 * 0.53 * 100.0)                   # 212.0


def test_below_minimum_lot_is_rejected_not_rounded_up():
    res = run(LONG_SETUP + [105.0], {7: {"high": 105.5}}, capital=50)      # 0.50 / 200 = 0.0025 lot < 0.01
    assert res["trades"] == [] and res["diagnostics"]["rejected_min_lot"] >= 1


# ------------------------------------------------------------------ pending orders, break-even, trailing
def test_pending_stop_gap_fill_ignores_tp_in_fill_bar_and_expires():
    sp = spec(order={"type": "stop", "offset_atr": 0, "expiry_bars": 3})
    # P = signal-bar Bid close 100.4. Bar 6 opens below it (Ask open 100.3) and trades through it (Ask high 105.5)
    over = {6: {"open": 99.8, "high": 105.0, "low": 99.5, "close": 104.0}, 7: {"high": 104.6, "low": 103.5}}
    t = only(run([100.0] * 5 + [100.4, 104.0, 104.0], over, sp))
    assert t["entry_price"] == pytest.approx(100.4)                        # max(Ask open 100.3, P 100.4): fills AT the stop price
    assert t["entry_time"] == "2026-01-05T01:30:00+00:00"
    # TP = 100.4 + 4.0 = 104.4 was reached in the fill bar (Bid high 105.0) but ignored; taken in bar 7
    assert t["exit_time"] == "2026-01-05T01:45:00+00:00" and t["exit_price"] == pytest.approx(104.4)
    assert t["pnl"] == pytest.approx((104.4 - 100.4) * 0.5 * 100.0)
    flat = {i: {"open": 99.0, "high": 99.3, "low": 98.8, "close": 99.0} for i in (6, 7, 8)}
    res = run([100.0] * 5 + [100.4] + [99.0] * 4, flat, sp)                # never touched within expiry_bars=3
    assert res["trades"] == [] and res["diagnostics"]["unfilled_orders"] == 1


def test_break_even_locks_the_entry_price_from_the_next_bar():
    sp = spec(break_even={"activate_r": 1.0})
    # bar 7 Bid high 103.0 -> 1.05 R favourable -> stop moves 98.9 -> 100.9 for bar 8; bar 8 low 100.5 takes it out
    t = only(run(LONG_SETUP + [102.5, 102.5], {7: {"high": 103.0, "low": 100.0}, 8: {"low": 100.5}}, sp))
    assert t["exit_reason"] == "STOP_LOSS" and t["exit_price"] == pytest.approx(100.9)   # min(open 102.5, new stop 100.9)
    assert t["pnl"] == pytest.approx(0.0, abs=1e-9)                        # no spread/commission offset is added to BE


def test_trailing_stop_follows_best_price_minus_atr_from_the_next_bar():
    sp = spec(trailing={"activate_r": 1.0, "atr_mult": 1.0, "period": 3})
    closes = [100.0] * 5 + [100.4, 100.5, 100.9, 101.3, 101.7, 102.1, 102.5, 102.9, 103.3, 103.3]  # TR == 1.0 -> ATR == 1.0
    # bar 12 Bid high 103.0 = 1.05 R: trail = 103.0 - 1.0 = 102.0 (bar 13); bar 13 high 103.4 -> 102.4 (bar 14)
    t = only(run(closes, {14: {"low": 102.0}}, sp))
    assert t["exit_price"] == pytest.approx(102.4) and t["exit_reason"] == "STOP_LOSS"  # min(open 103.3, trail 102.4)
    assert t["pnl"] == pytest.approx((102.4 - 100.9) * 0.5 * 100.0)        # +75.0


# ------------------------------------------------------------------ documented gaps (these tests pin what is NOT modelled)
def test_partial_exits_are_unsupported_by_the_rule_spec():
    errs = engine.validate_spec(spec(partial_exit={"at_r": 1.0, "fraction": 0.5}))
    assert errs and any("unknown key" in e for e in errs)


def test_overnight_swap_is_not_modelled_a_multi_day_hold_pays_nothing():
    n = 300  # 75 hours: crosses three UTC midnights
    t = only(run([100.0] * 5 + [100.4] * (n - 5)))
    assert t["entry_time"][:10] != t["exit_time"][:10] and t["bars_held"] > 288
    assert t["exit_reason"] == "END_OF_DATA" and t["costs"] == 0.0 and "swap" not in t
    assert t["pnl"] == pytest.approx((100.4 - 100.9) * 0.5 * 100.0)        # pure price move, nothing for holding


def test_no_margin_or_max_lot_check_huge_leverage_is_accepted():
    sp = spec(stop={"type": "points", "value": 0.01})
    res = run(LONG_SETUP + [100.5], None, sp)
    t = res["trades"][0]
    assert t["qty"] == pytest.approx(100.0, abs=0.02)                      # ~100 lots (float floor at the step)
    assert t["qty"] * 100.0 * t["entry_price"] / 10_000 > 90               # ~1.0M notional on 10k equity: no margin rejection


# ------------------------------------------------------------------ execution.py primitives + Jesse vs Bid/Ask
def test_position_pnl_hand_values():
    c = ContractSpec("USD", 100.0, 0.01, 1.0, 0.01, commission_per_lot_side=3.5)
    r = position_pnl(side="long", quantity_lots=0.5, entry=100.9, exit=104.9, contract=c)
    assert r["gross_pnl"] == pytest.approx(200.0) and r["commission"] == pytest.approx(3.5)
    assert r["net_pnl"] == pytest.approx(196.5) and r["tick_value"] == pytest.approx(1.0)   # 0.01 x 100 x 1.0
    s = position_pnl(side="short", quantity_lots=2.0, entry=104.0, exit=103.0, contract=ContractSpec("EUR", 1.0, 0.1, 1.1, 0.01))
    assert s["gross_pnl"] == pytest.approx(1.0 * 2.0 * 1.0 * 1.1)          # index CFD with EUR->USD 1.1


def test_conservative_exit_primitive_sides():
    bid = {"open": 100.0, "high": 106.0, "low": 98.0}
    ask = {"open": 100.5, "high": 106.5, "low": 98.5}
    long_ = conservative_ohlc_exit(side="long", bid=bid, ask=ask, stop=99.0, target=105.0)
    assert long_ == {"reason": "STOP_LOSS", "price": 99.0, "ambiguous": True}
    short = conservative_ohlc_exit(side="short", bid=bid, ask=ask, stop=107.0, target=99.0)
    assert short == {"reason": "TAKE_PROFIT", "price": 99.0, "ambiguous": False}   # Ask low 98.5 <= 99, Ask high 106.5 < 107


def test_jesse_bid_only_fills_vs_bid_ask_reprice_differs_by_exactly_the_spread_cost():
    """Jesse fills both sides on the single Bid series (+ a fee fraction); the Bid/Ask ledger buys on the Ask and
    sells on the Bid. For the same trade the difference is spread x qty x contract size, nothing else."""
    t0, t1 = 1_767_575_400_000, 1_767_579_000_000
    jesse = {"id": "j1", "type": "long", "qty": 0.5, "opened_at": t0, "closed_at": t1, "PNL": 180.0,
             "entry_price": 100.4, "exit_price": 104.0}
    quotes = {t0: {"bid_open": 100.4, "ask_open": 100.9}, t1: {"bid_open": 104.0, "ask_open": 104.5}}
    out = apply_ledger([jesse], quotes, CONTRACT)[0]
    assert out["entry_price"] == pytest.approx(100.9) and out["exit_price"] == pytest.approx(104.0)
    assert out["gross_pnl"] == pytest.approx((104.0 - 100.9) * 0.5 * 100.0)           # 155.0
    bid_only = (104.0 - 100.4) * 0.5 * 100.0                                          # 180.0 on one price series
    assert bid_only - out["gross_pnl"] == pytest.approx(0.5 * 0.5 * 100.0)           # 25.0 = spread 0.5 x qty x size
    assert out["spread_trigger_replayed"] is False and out["status"] == "JESSE_RESEARCH_PROVISIONAL"
    assert out["jesse_pnl_provisional"] == 180.0
    bad = apply_ledger([{**jesse, "qty": 0.2598574}], quotes, CONTRACT)[0]
    assert bad["volume_step_valid"] is False and bad["warning"]
    assert apply_ledger([jesse], {t0: quotes[t0]}, CONTRACT)[0]["status"] == "MISSING_DUKASCOPY_QUOTE"
