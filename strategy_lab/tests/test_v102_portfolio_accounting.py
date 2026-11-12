"""Portfolio accounting (v102): FX per candle, exposure groups, position cap, priority, stress scenarios.

Synthetic fixtures; mechanics only, not evidence about any strategy.
"""
from datetime import timedelta
from types import SimpleNamespace

import pytest

from strategy_lab.data_manager import DataError
from strategy_lab.portfolio import portfolio_accounting, stress_test
from strategy_lab.research.fx import FxRateUnavailable
from strategy_lab.tests.test_forward_rules import DAY, ingest, observed, path_day, setup, spec
from strategy_lab.tests.test_v101_portfolio_acceptance import ENTRY_HOLD, feed, events

DAY2 = DAY + timedelta(days=1)


def fixed_fx(rate):
    return SimpleNamespace(rate=lambda *a, **k: SimpleNamespace(rate=rate, source="TEST", data_version="t"))


def two_open(tmp_path, symbols=("XAUUSD", "EURUSD"), **limits):
    members = [spec(symbol=s) for s in symbols]
    store, engine, portfolio, _ = setup(tmp_path / "a.sqlite", members, capital=1000, risk_limit=0.05, per_member=500)
    if limits:
        engine.set_limits(portfolio, **limits)
    feed(store, DAY, {s: ENTRY_HOLD for s in symbols})
    engine.advance(portfolio, as_of=observed(DAY))
    return store, engine, portfolio


# ----------------------------------------------------------------------------- FX

def test_fx_converts_per_candle_and_a_missing_rate_pauses_with_no_fills(tmp_path):
    class Flaky:
        def __init__(self):
            self.down = True

        def rate(self, quote, account, ts, side="mid"):
            if self.down:
                raise FxRateUnavailable("EURUSD: no bar")
            return SimpleNamespace(rate=1.25, source="TEST", data_version="t")

    store, engine, portfolio, _ = setup(tmp_path / "a.sqlite", [spec(symbol="DE40")], capital=1000)
    engine._fx = fx = Flaky()
    feed(store, DAY, {"DE40": ENTRY_HOLD})
    out = engine.advance(portfolio, as_of=observed(DAY))
    assert out["status"] == "PAUSED" and out["reason"] == "FX_RATE_UNAVAILABLE" and out["committed_days"] == []
    report = engine.report(portfolio)
    assert report["events"] == [] and report["days"] == [] and report["portfolio"]["last_day"] is None
    fx.down = False
    assert engine.advance(portfolio, as_of=observed(DAY))["committed_days"] == [DAY.isoformat()]
    fill, = events(engine, portfolio, "ORDER_FILLED")
    assert fill["detail"]["fx_rate"] == 1.25 and fill["detail"]["fx_quote"] == "EUR"
    assert fill["detail"]["fx_source"] == "TEST"
    # sizing is risk-based in account currency: a stronger EUR means fewer lots, never the frozen 1.0
    store2, engine2, portfolio2, _ = setup(tmp_path / "b.sqlite", [spec(symbol="DE40")], capital=1000)
    engine2._fx = fixed_fx(2.5)
    feed(store2, DAY, {"DE40": ENTRY_HOLD})
    engine2.advance(portfolio2, as_of=observed(DAY))
    fill2, = events(engine2, portfolio2, "ORDER_FILLED")
    assert fill2["detail"]["quantity"] < fill["detail"]["quantity"]
    assert engine2.report(portfolio2)["members"][0]["state"]["fx_rate"] == 2.5


def test_usd_quoted_markets_need_no_fx_service(tmp_path):
    store, engine, portfolio, _ = setup(tmp_path / "a.sqlite", [spec()], capital=1000)
    engine._fx = SimpleNamespace(rate=lambda *a, **k: (_ for _ in ()).throw(AssertionError("FX must not be used")))
    feed(store, DAY, {"XAUUSD": ENTRY_HOLD})
    assert engine.advance(portfolio, as_of=observed(DAY))["committed_days"] == [DAY.isoformat()]


# ----------------------------------------------------------------------------- limits

def test_group_risk_cap_limits_correlated_stacking_across_symbols(tmp_path):
    _, control, cportfolio = two_open(tmp_path / "c", ("EURUSD", "GBPUSD")) if (tmp_path / "c").mkdir() is None else None
    assert len(events(control, cportfolio, "ORDER_FILLED")) == 2                    # no cap: both fx positions open
    (tmp_path / "g").mkdir()
    _, engine, portfolio = two_open(tmp_path / "g", ("EURUSD", "GBPUSD"), group_risk=0.007)
    assert len(events(engine, portfolio, "ORDER_FILLED")) == 1
    rejected, = events(engine, portfolio, "ORDER_REJECTED")
    assert rejected["detail"]["reason"] == "GROUP_RISK_LIMIT" and rejected["detail"]["priority_rank"] == 2
    (tmp_path / "m").mkdir()
    _, mixed, mportfolio = two_open(tmp_path / "m", ("XAUUSD", "EURUSD"), group_risk=0.007)
    assert len(events(mixed, mportfolio, "ORDER_FILLED")) == 2                      # different asset classes do not share a cap


def test_max_positions_blocks_extra_entries_and_logs_the_hypothetical_order(tmp_path):
    _, engine, portfolio = two_open(tmp_path, ("XAUUSD", "EURUSD"), max_positions=1)
    assert len(events(engine, portfolio, "ORDER_FILLED")) == 1
    rejected, = events(engine, portfolio, "ORDER_REJECTED")
    assert rejected["detail"]["reason"] == "PORTFOLIO_HALT" and rejected["detail"]["halt"] == "MAX_POSITIONS"
    assert rejected["detail"]["priority_rank"] == 2                                  # the later member lost the tie
    with pytest.raises(DataError):
        engine.set_limits(portfolio, max_positions=0)


def test_priority_is_enrolment_order_and_rejections_are_counted(tmp_path):
    _, engine, portfolio = two_open(tmp_path, ("XAUUSD", "EURUSD"), max_positions=1)
    report = engine.report(portfolio)
    first, second = report["members"]
    filled, = events(engine, portfolio, "ORDER_FILLED")
    assert filled["registration_id"] == first["registration_id"]
    accounting = portfolio_accounting(report)
    assert accounting["rejected_orders"]["by_reason"] == {"PORTFOLIO_HALT": 1}
    recent, = accounting["rejected_orders"]["recent"]
    assert recent["hypothetical"] is True and recent["registration_id"] == second["registration_id"]
    assert "enrolment order" in accounting["priority_rule"]


# ----------------------------------------------------------------------------- accounting

def test_accounting_reports_shared_capital_risk_margin_and_exposure(tmp_path):
    _, engine, portfolio = two_open(tmp_path, ("XAUUSD", "EURUSD"), max_daily_loss=0.05)
    report = engine.report(portfolio)
    acc = portfolio_accounting(report)
    fills = events(engine, portfolio, "ORDER_FILLED")
    assert acc["capital"]["allocated"] == 1000 and acc["capital"]["unallocated"] == 0
    assert acc["risk"]["open_stop_risk"] == pytest.approx(sum(f["detail"]["risk"] for f in fills))
    assert acc["margin"]["open_margin"] == pytest.approx(sum(f["detail"]["margin"] for f in fills))
    assert acc["margin"]["free_margin"] == pytest.approx(
        min(report["portfolio"]["balance"], acc["capital"]["equity"]) - acc["margin"]["open_margin"])
    assert set(acc["risk"]["by_group"]) == {"metal", "fx"} and acc["risk"]["group_basis"].startswith("ASSUMED")
    assert acc["risk"]["max_open_risk"] == pytest.approx(
        0.05 * min(acc["capital"]["balance"], acc["capital"]["equity"]))
    assert acc["allocations"][0]["margin_model"]["status"] == "ASSUMED"            # labelled, not broker-verified
    assert acc["exposure"]["gross_notional"] > 0 and "NONE" in acc["exposure"]["netting"]
    assert acc["daily_loss"]["limit_fraction"] == 0.05 and acc["drawdown"]["peak_equity"] >= 1000


# ----------------------------------------------------------------------------- stress

def scenario(result, name):
    return next(s for s in result["scenarios"] if s["name"] == name)


def test_stress_scenarios_on_open_positions(tmp_path):
    _, engine, portfolio = two_open(tmp_path, ("EURUSD", "GBPUSD"), max_daily_loss=0.009)
    report = engine.report(portfolio)
    risks = [p["risk"] for p in portfolio_accounting(report)["open_positions"]]
    result = stress_test(report)
    assert result["basis"] == "ASSUMED_SCENARIOS_NOT_FORECASTS" and result["open_positions"] == 2
    names = {s["name"] for s in result["scenarios"]}
    assert names == {"CORRELATED_SAME_DIRECTION", "CORRELATIONS_GO_TO_ONE", "GAP_THROUGH_STOP", "VOLATILITY_SHOCK",
                     "POOR_LIQUIDITY", "HIGHER_SPREADS", "MANY_SIMULTANEOUS_POSITIONS", "INSUFFICIENT_MARGIN",
                     "DAILY_LOSS_LIMIT"}
    same = scenario(result, "CORRELATED_SAME_DIRECTION")
    assert same["group"] == "fx" and same["loss"] == pytest.approx(sum(risks))      # both fx positions stop together
    assert scenario(result, "VOLATILITY_SHOCK")["loss"] == pytest.approx(2 * sum(risks))
    assert scenario(result, "GAP_THROUGH_STOP")["loss"] > sum(risks)
    assert scenario(result, "POOR_LIQUIDITY")["loss"] >= sum(risks)
    assert scenario(result, "HIGHER_SPREADS")["loss"] > sum(risks)                   # spread cost recorded at the fill
    daily = scenario(result, "DAILY_LOSS_LIMIT")
    assert "DAILY_LOSS_LIMIT" in daily["breaches"] and daily["headroom"] < 0
    shock = scenario(result, "VOLATILITY_SHOCK")
    assert shock["equity_after"] == pytest.approx(result["equity"] - shock["loss"])


def test_stress_margin_call_and_capacity_cap(tmp_path):
    store, engine, portfolio, _ = setup(tmp_path / "a.sqlite", [spec(), {**spec(), "name": "Two"}], capital=1000,
                                        risk_limit=0.05, per_member=500, leverage=1.5)
    feed(store, DAY, {"XAUUSD": ENTRY_HOLD})
    engine.advance(portfolio, as_of=observed(DAY))
    result = stress_test(engine.report(portfolio), vol_multiple=40.0)
    shock = scenario(result, "VOLATILITY_SHOCK")
    assert "PORTFOLIO_RISK_BUDGET_EXCEEDED" in shock["breaches"]
    many = scenario(result, "MANY_SIMULTANEOUS_POSITIONS")
    assert many["members"] == 2 and many["loss"] <= 0.05 * result["equity"] + 1e-9
    open_margin = scenario(result, "INSUFFICIENT_MARGIN")["open_margin"]
    assert open_margin > 0
    hard = stress_test(engine.report(portfolio), stop_out_level=1e6)
    assert "MARGIN_CALL_ASSUMED" in scenario(hard, "INSUFFICIENT_MARGIN")["breaches"]


def test_stress_without_positions_is_explicit_instead_of_a_fake_zero(tmp_path):
    store, engine, portfolio, _ = setup(tmp_path / "a.sqlite", [spec()], capital=1000)
    result = stress_test(engine.report(portfolio))
    assert result["open_positions"] == 0
    assert scenario(result, "CORRELATED_SAME_DIRECTION")["note"] == "no open positions"
    assert scenario(result, "MANY_SIMULTANEOUS_POSITIONS")["loss"] > 0               # capacity view still informative
    assert all(s["breaches"] == [] for s in result["scenarios"] if s["name"] != "MANY_SIMULTANEOUS_POSITIONS")
