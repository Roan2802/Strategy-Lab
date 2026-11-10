"""V101: instrument data provenance, money gate, and DE40 regression (EUR and USD account currency)."""
import math

import numpy as np
import pandas as pd
import pytest

from runtime.strategy_lab.execution import ContractSpec
from strategy_lab.research import backtest, instruments


class FakeDM:
    def __init__(self):
        idx = pd.date_range("2020-01-01", "2023-12-31", freq="1h", tz="UTC")
        idx = idx[idx.dayofweek < 5]
        self.frames = {}
        rng = np.random.default_rng(5)
        for sym, p0, sp in (("EURUSD", 1.1, 0.0001), ("USDJPY", 120.0, 0.01), ("DE40", 15000.0, 1.0),
                            ("XAUUSD", 1800.0, 0.3)):
            close = p0 * np.exp(np.cumsum(rng.normal(0, 0.0005, len(idx))))
            f = pd.DataFrame(index=idx)
            for side, off in (("bid", 0.0), ("ask", sp)):
                for c in ("open", "high", "low", "close"):
                    f[f"{side}_{c}"] = np.round(close + off, 3 if p0 > 50 else 5)
                f[f"{side}_volume"] = 1.0
            self.frames[sym] = f

    def candles(self, symbol, timeframe, start=None, end=None):
        f = self.frames[symbol]
        if start:
            f = f[f.index >= pd.Timestamp(start, tz="UTC")]
        if end:
            f = f[f.index < pd.Timestamp(end, tz="UTC")]
        return f, {"symbol": symbol}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(instruments, "CACHE", tmp_path / "stats.json")
    monkeypatch.setattr(instruments, "BROKER_OVERRIDES", tmp_path / "broker.json")
    return FakeDM(), tmp_path


# ------------------------------------------------------------------ provenance + money gate
WANTED = ("contract_size", "tick_size", "tick_value", "pip_value", "currency", "lot_step", "min_lot", "max_lot", "margin",
          "trading_hours", "commission_per_lot_side", "typical_spread", "slippage", "swap")


def test_every_field_has_status_and_source_and_nothing_is_broker_verified_by_default(env):
    dm, _ = env
    for sym in ("DE40", "XAUUSD", "EURUSD", "USDJPY"):
        fields = instruments.spec_for(sym, dm)["fields"]
        for name in WANTED:
            f = fields[name]
            assert f["status"] in ("VERIFIED_FROM_DATA", "ASSUMED", "UNKNOWN", "BROKER_VERIFIED"), (sym, name)
            assert f["source"], (sym, name)
            assert f["status"] != "BROKER_VERIFIED"
    f = instruments.spec_for("DE40", dm)["fields"]
    assert f["typical_spread"]["status"] == "VERIFIED_FROM_DATA" and f["typical_spread"]["source"] == "dukascopy_data"
    assert f["swap"]["status"] == f["margin"]["status"] == f["commission_per_lot_side"]["status"] == "UNKNOWN"
    assert f["contract_size"]["status"] == "ASSUMED"
    assert f["tick_value"]["status"] == "ASSUMED"  # derived from an assumed contract size
    assert instruments.spec_for("EURUSD", dm)["fields"]["pip_value"]["value"]["pip_size"] == 0.0001
    assert instruments.spec_for("USDJPY", dm)["fields"]["pip_value"]["value"]["pip_size"] == 0.01
    assert instruments.spec_for("DE40", dm)["fields"]["pip_value"]["status"] == "UNKNOWN"


def test_money_gate_blocks_until_every_required_field_is_verified(env):
    dm, tmp = env
    s = instruments.spec_for("DE40", dm)
    gate = s["money_gate"]
    assert s["money_status"] == "PROVISIONAL" and gate["money_approval_allowed"] is False
    assert {"contract_size", "lot_step", "min_lot", "commission_per_lot_side", "margin", "swap",
            "slippage"} <= set(gate["blocking_fields"])
    assert "typical_spread" not in gate["blocking_fields"] and "account_conversion" not in gate["blocking_fields"]
    agg = instruments.money_approval(["DE40", "XAUUSD"], dm)
    assert agg["money_approval_allowed"] is False and set(agg["blocking"]) == {"DE40", "XAUUSD"}


def test_broker_overrides_need_source_and_status(env):
    import json
    dm, tmp = env
    ov = {"status": "BROKER_VERIFIED", "source": "broker spec sheet 2026-11-01", "value": 1.0}
    (tmp / "broker.json").write_text(json.dumps({"DE40": {
        "contract_size": ov, "lot_step": {**ov, "value": 0.1},
        "min_lot": {"status": "BROKER_VERIFIED", "value": 0.1},            # no source: ignored
        "margin": {"status": "ASSUMED", "source": "x", "value": 0.05}}}), encoding="utf-8")
    f = instruments.spec_for("DE40", dm)["fields"]
    assert f["contract_size"]["status"] == "BROKER_VERIFIED" and f["contract_size"]["source"].startswith("broker spec")
    assert f["lot_step"]["value"] == 0.1
    assert f["min_lot"]["status"] == "ASSUMED" and f["margin"]["status"] == "UNKNOWN"
    assert f["tick_value"]["status"] == "VERIFIED_FROM_DATA"  # tick measured, contract size broker-verified
    # all required fields verified -> gate opens
    full = {n: {**ov, "value": 1.0} for n in ("contract_size", "lot_step", "min_lot", "commission_per_lot_side", "margin",
                                               "swap", "slippage")}
    (tmp / "broker.json").write_text(json.dumps({"DE40": full}), encoding="utf-8")
    s = instruments.spec_for("DE40", dm)
    assert s["money_gate"]["money_approval_allowed"] is True and s["money_status"] == "BROKER_VERIFIED"


# ------------------------------------------------------------------ DE40: conversion
def test_de40_conversion_usd_account_is_eurusd_mid_at_open(env):
    dm, _ = env
    ser = instruments.conversion_series(dm, "DE40", "H1", None, None)
    f = dm.frames["EURUSD"]
    assert np.allclose(ser.to_numpy(), ((f.bid_open + f.ask_open) / 2).to_numpy())


def test_de40_eur_account_needs_no_conversion_but_usd_instruments_do(env):
    dm, _ = env
    assert instruments.conversion_info("DE40", "EUR")["needed"] is False
    assert len(instruments.conversion_series(dm, "DE40", "H1", None, None, account="EUR")) == 0
    eur_per_usd = instruments.conversion_series(dm, "XAUUSD", "H1", None, None, account="EUR")
    f = dm.frames["EURUSD"]
    assert np.allclose(eur_per_usd.to_numpy(), (1.0 / ((f.bid_open + f.ask_open) / 2)).to_numpy())
    # cross: JPY -> EUR = (USD per JPY) / (USD per EUR)
    cross = instruments.conversion_series(dm, "USDJPY", "H1", None, None, account="EUR")
    j, e = dm.frames["USDJPY"], dm.frames["EURUSD"]
    expect = (1.0 / ((j.bid_open + j.ask_open) / 2)) / ((e.bid_open + e.ask_open) / 2)
    assert np.allclose(cross.to_numpy(), expect.to_numpy())


def test_historical_fx_rate_is_asof_never_current_or_future():
    idx = pd.to_datetime(["2022-01-03 10:00", "2022-06-01 10:00", "2023-01-02 10:00"], utc=True)
    ser = pd.Series([1.13, 1.05, 1.07], index=idx)
    assert instruments.rate_at(ser, "2022-01-03 10:00") == 1.13           # at the bar open
    assert instruments.rate_at(ser, "2022-03-15 12:00") == 1.13           # held until the next bar, not the later 1.05
    assert instruments.rate_at(ser, "2022-06-01 10:00") == 1.05
    assert instruments.rate_at(ser, "2030-01-01") == 1.07                 # last known, never invented
    with pytest.raises(ValueError, match="no conversion rate"):
        instruments.rate_at(ser, "2021-12-31")                            # before first rate: refuse, no back-fill
    trades = [{"exit_time": "2022-03-15T12:00:00+00:00", "pnl": 100.0, "costs": 2.0},
              {"exit_time": "2022-07-01T00:00:00+00:00", "pnl": -50.0, "costs": 1.0}]
    out = instruments.convert_trades(trades, ser)
    assert out[0]["pnl"] == pytest.approx(113.0) and out[0]["costs"] == pytest.approx(2.26)
    assert out[1]["pnl"] == pytest.approx(-52.5) and out[1]["fx_rate"] == 1.05
    assert trades[0]["pnl"] == 100.0  # input untouched
    same = instruments.convert_trades(trades, pd.Series(dtype=float))
    assert same[0]["pnl"] == 100.0  # EUR trade on a EUR account: no conversion


# ------------------------------------------------------------------ DE40: sizing, risk %, exposure, portfolio
def test_de40_lot_size_and_risk_pct_usd_vs_eur_account():
    # 1% of 10_000, stop 50 index points, contract size 1 (1 unit per index point), lot step 0.01
    usd = instruments.size_position(10_000, 1.0, 50.0, 1.0, rate=1.10, lot_step=0.01)
    assert usd["lots"] == pytest.approx(1.81)                       # 100 / (50 * 1.10) = 1.818 -> floor to 1.81
    assert usd["risk_amount"] == pytest.approx(1.81 * 50 * 1.10)    # 99.55 USD <= 100
    assert usd["risk_amount"] <= 100.0 and usd["risk_pct_of_equity"] == pytest.approx(0.9955)
    eur = instruments.size_position(10_000, 1.0, 50.0, 1.0, rate=1.0, lot_step=0.01)
    assert eur["lots"] == pytest.approx(2.0) and eur["risk_amount"] == pytest.approx(100.0)
    # rate at entry matters: a stronger EUR means fewer lots for the same USD risk
    assert instruments.size_position(10_000, 1.0, 50.0, 1.0, 1.20, 0.01)["lots"] < usd["lots"]
    assert instruments.size_position(10_000, 1.0, 50000.0, 1.0, 1.1, 0.01)["rejected"] == "BELOW_MIN_LOT"
    with pytest.raises(ValueError):
        instruments.size_position(10_000, 1.0, 50.0, 1.0, 0.0, 0.01)


def test_de40_exposure_and_portfolio_risk_in_account_currency():
    assert instruments.exposure(2.0, 18_000.0, 1.0, 1.0) == 36_000.0                  # EUR account
    assert instruments.exposure(2.0, 18_000.0, 1.0, 1.10) == pytest.approx(39_600.0)  # USD account
    de40 = {"lots": 1.81, "stop_distance": 50.0, "contract_size": 1.0, "rate": 1.10, "price": 18_000.0}
    gold = {"lots": 0.10, "stop_distance": 5.0, "contract_size": 100.0, "rate": 1.0, "price": 2000.0}
    pr = instruments.portfolio_open_risk([de40, gold], 10_000)
    assert pr["per_position_risk"] == [pytest.approx(99.55), pytest.approx(50.0)]
    assert pr["open_risk"] == pytest.approx(149.55) and pr["open_risk_pct"] == pytest.approx(1.4955)
    assert pr["gross_exposure"] == pytest.approx(1.81 * 18_000 * 1.10 + 0.10 * 2000 * 100)
    # each position uses ITS OWN entry-time rate: change DE40's rate only
    pr2 = instruments.portfolio_open_risk([{**de40, "rate": 1.30}, gold], 10_000)
    assert pr2["per_position_risk"][1] == pytest.approx(50.0) and pr2["per_position_risk"][0] > pr["per_position_risk"][0]


# ------------------------------------------------------------------ DE40: P/L through the backtester
def _de40_frames(n=900, seed=11):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-05", periods=n, freq="15min", tz="UTC")
    close = 15000 + np.cumsum(rng.normal(0.5, 6.0, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(1, 8, n)
    low = np.minimum(open_, close) - rng.uniform(1, 8, n)
    bid = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1.0}, index=idx)
    ask = bid.copy()
    for c in ("open", "high", "low", "close"):
        ask[c] = bid[c] + 1.0
    return bid, ask


def _equity_at_entry(trades, t, capital=10_000.0):
    return capital + sum(x["pnl"] for x in trades if x["exit_time"] < t["entry_time"])


def _hand_lots(trades, t, stop_points, rate, risk_pct=1.0, step=0.01, contract_size=1.0):
    """Independent hand calculation: floor(equity * risk% / (stop * contract size * rate) / step) * step."""
    raw = _equity_at_entry(trades, t) * risk_pct / 100.0 / (stop_points * contract_size * rate)
    return math.floor(raw / step + 1e-9) * step


SPEC = {"schema": "strategy_lab.rules/1", "name": "De40Probe", "symbol": "DE40", "timeframe": "M15",
        "direction": "long",
        "entry": {"long": {"all": [{"left": {"indicator": "ema", "period": 8}, "op": "crosses_above",
                                    "right": {"indicator": "ema", "period": 21}}]}},
        "order": {"type": "market"}, "stop": {"type": "points", "value": 40.0},
        "target": {"type": "r_multiple", "value": 2.0}, "risk": {"percent": 1.0}}


def test_de40_backtest_usd_account_uses_trade_time_rate_for_pnl_and_sizing():
    bid, ask = _de40_frames()
    conv = pd.Series(1.05 + 0.0002 * np.arange(len(bid)), index=bid.index)  # EURUSD drifting every bar
    contract = ContractSpec("USD", 1.0, 0.1, 1.0, 0.01)
    res = backtest.run_rules_backtest(SPEC, bid, ask, None, capital=10_000, contract=contract, conversion=conv)
    assert res["diagnostics"]["currency_status"] == "CONVERTED_CAUSAL"
    assert len(res["trades"]) >= 5
    for t in res["trades"]:
        q_exit = instruments.rate_at(conv, t["exit_time"])       # rate at the exit bar open, not today's
        q_entry = instruments.rate_at(conv, t["entry_time"])
        assert t["qty"] == pytest.approx(_hand_lots(res["trades"], t, 40.0, q_entry))
        gross = (t["exit_price"] - t["entry_price"]) * t["qty"] * 1.0 * q_exit
        assert t["pnl"] == pytest.approx(gross, abs=1e-6)         # zero commission: pnl == hand-calculated gross
        assert t["qty"] * 40.0 * q_entry <= _equity_at_entry(res["trades"], t) * 0.01 + 1e-9  # <= 1% risk at entry rate
    # it is NOT the same as using the last (current) rate for everything
    flat = backtest.run_rules_backtest(SPEC, bid, ask, None, capital=10_000, contract=contract,
                                       conversion=pd.Series(float(conv.iloc[-1]), index=bid.index))
    assert flat["metrics"]["net_profit"] != pytest.approx(res["metrics"]["net_profit"])


def test_de40_backtest_eur_account_has_no_conversion_and_is_labelled():
    bid, ask = _de40_frames()
    eur = backtest.run_rules_backtest(SPEC, bid, ask, None, capital=10_000, contract=ContractSpec("EUR", 1.0, 0.1, 1.0, 0.01))
    assert eur["diagnostics"]["currency_status"] == "ACCOUNT_CURRENCY_QUOTE"
    for t in eur["trades"]:
        assert t["pnl"] == pytest.approx((t["exit_price"] - t["entry_price"]) * t["qty"], abs=1e-6)
        assert t["qty"] == pytest.approx(_hand_lots(eur["trades"], t, 40.0, 1.0))
        assert t["qty"] * 40.0 <= _equity_at_entry(eur["trades"], t) * 0.01 + 1e-9
    # same signals as the USD account at a constant rate of 1.0 (sanity: only currency differs)
    usd1 = backtest.run_rules_backtest(SPEC, bid, ask, None, capital=10_000, contract=ContractSpec("USD", 1.0, 0.1, 1.0, 0.01),
                                       conversion=pd.Series(1.0, index=bid.index))
    assert [t["entry_time"] for t in eur["trades"]] == [t["entry_time"] for t in usd1["trades"]]
    assert eur["metrics"]["net_profit"] == pytest.approx(usd1["metrics"]["net_profit"])


def test_unconverted_eur_quote_on_usd_account_is_flagged_provisional():
    bid, ask = _de40_frames()
    res = backtest.run_rules_backtest(SPEC, bid, ask, None, capital=10_000, contract=ContractSpec("USD", 1.0, 0.1, 1.0, 0.01))
    assert res["diagnostics"]["currency_status"] == "QUOTE_CURRENCY_UNCONVERTED"
