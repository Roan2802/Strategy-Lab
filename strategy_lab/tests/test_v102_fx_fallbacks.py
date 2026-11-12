"""V10.2b: no silent 1.0 / frozen-rate FX fallback for non-account quote currencies."""
from datetime import datetime, timezone

import pandas as pd
import pytest

from runtime.strategy_lab.execution import ContractSpec
from strategy_lab import forward_performance, forward_rules, portfolio
from strategy_lab.data_manager import DataError
from strategy_lab.research import fx, instruments

RAW = {"account_currency": "USD", "contract_size": 1.0, "tick_size": 0.01, "quote_to_account": 1.0,
       "volume_step": 0.01, "commission_per_lot_side": 0.0, "slippage_points": 0.0}


class _Svc:
    def rate(self, quote, account, ts, side="mid"):
        return fx.Conversion(quote, account, pd.Timestamp(ts), 1.08, side, "test", "v", 0.0)


# 1. instruments.contract_for / ledger validation
def test_contract_for_raises_without_ts_for_eur_quote():
    with pytest.raises(fx.FxRateUnavailable, match="FX_RATE_UNAVAILABLE"):
        instruments.contract_for("DE40", None)


def test_contract_for_same_currency_is_one_and_ts_gives_real_rate():
    assert instruments.contract_for("XAUUSD", None).quote_to_account == 1.0
    spec = instruments.contract_for("DE40", None, ts="2024-03-13T10:00:00Z", fx_service=_Svc())
    assert spec.quote_to_account == 1.08


def test_contract_for_series_mode_is_explicit_opt_in():
    assert instruments.contract_for("DE40", None, conversion_series=True).quote_to_account == 1.0


def test_ledger_contract_rejects_one_for_eur_quote():
    spec = ContractSpec("USD", 1.0, 0.01, 1.0, 0.01)
    with pytest.raises(ValueError, match="FX_RATE_UNAVAILABLE"):
        instruments.validate_ledger_contract("DE40", spec)
    assert instruments.validate_ledger_contract("XAUUSD", spec)["status"] == "OK"
    fixed = instruments.validate_ledger_contract("DE40", ContractSpec("USD", 1.0, 0.01, 1.1, 0.01))
    assert fixed["status"] == "FIXED_USER_RATE"
    with pytest.raises(ValueError):
        instruments.validate_ledger_contract("XAUUSD", ContractSpec("EUR", 1.0, 0.01, 1.0, 0.01))


# 2. portfolio._open_positions
def _report(symbol, fx_rate):
    state = {"position": {"side": "long", "quantity": 2.0, "entry": 100.0, "stop": 90.0, "margin": 5.0, "risk": 20.0,
                          "entry_at": "t"}, "mark": 101.0}
    if fx_rate is not None:
        state["fx_rate"] = fx_rate
    contract = {**RAW, "quote_to_account": 1.0}
    return {"events": [], "members": [{"registration_id": "r", "state": state,
                                       "configuration": {"symbol": symbol, "contract": contract}}]}


def test_open_positions_blocks_money_for_eur_quote_without_fx_rate():
    row = portfolio._open_positions(_report("DE40", None))[0]
    assert row["status"] == "FX_RATE_UNAVAILABLE"
    assert row["multiplier"] is None and row["notional"] is None and row["spread_cost"] is None
    assert row["risk"] == 20.0 and row["margin"] == 5.0   # already account-currency at fill time


def test_open_positions_uses_real_rate_and_same_currency_one():
    eur = portfolio._open_positions(_report("DE40", 1.1))[0]
    assert eur["status"] == "OK" and eur["multiplier"] == pytest.approx(2.2)
    usd = portfolio._open_positions(_report("XAUUSD", None))[0]
    assert usd["status"] == "OK" and usd["multiplier"] == 2.0


def test_stress_test_marks_fx_dependent_scenarios_unavailable():
    base = _report("DE40", None)
    base["members"][0].update(allocation=1000.0)
    base["members"][0]["configuration"]["trade_risk"] = 0.01
    report = {**base, "portfolio": {"starting_capital": 1000.0, "balance": 1000.0, "risk_limit": 0.5},
              "days": [], "limits": {}}
    names = {s["name"]: s for s in portfolio.stress_test(report)["scenarios"]}
    assert names["GAP_THROUGH_STOP"]["status"] == "FX_RATE_UNAVAILABLE" and names["GAP_THROUGH_STOP"]["loss"] is None
    assert names["VOLATILITY_SHOCK"]["loss"] == 40.0


# 3. forward_performance MAE/MFE
def _excursion(symbol, close_extra):
    stamp = datetime(2024, 3, 13, 10, tzinfo=timezone.utc)
    index = pd.DatetimeIndex([stamp.replace(minute=m) for m in range(0, 5)])
    frame = pd.DataFrame({"bid_high": [102.0] * 5, "bid_low": [98.0] * 5, "ask_high": [102.0] * 5,
                          "ask_low": [98.0] * 5}, index=index)
    pub = {"sha256": "x", "observed_at": "o"}
    fill = {"fill_assumption": "NEXT_OPEN", "partition_sha256": "x", "quote_observed_at": "o"}
    close = {"entry_at": "2024-03-13T10:00:00+00:00", "exit_at": "2024-03-13T10:04:00+00:00", "side": "long",
             "entry": 100.0, "quantity": 2.0, "contract": {**RAW, "quote_to_account": 1.0},
             "partition_sha256": "x", "quote_observed_at": "o", **close_extra}
    days = {"2024-03-13": {"publications": {symbol: pub}}}
    return forward_performance._trade_excursion(fill, close, symbol, days, lambda s, d: frame)


def test_mae_mfe_uses_closing_fx_rate_for_eur_quote():
    out = _excursion("DE40", {"fx_rate": 1.1})
    assert out["status"] == "AVAILABLE"
    assert out["mae"] == pytest.approx(2.0 * 2.0 * 1.1) and out["mfe"] == pytest.approx(2.0 * 2.0 * 1.1)


def test_mae_mfe_without_fx_rate_is_unavailable_not_frozen_one():
    assert _excursion("DE40", {})["status"] == "FX_RATE_UNAVAILABLE"


def test_mae_mfe_same_currency_uses_contract_rate():
    out = _excursion("XAUUSD", {})
    assert out["status"] == "AVAILABLE" and out["mae"] == pytest.approx(4.0)


# 4. forward_rules._contract
def test_forward_contract_rejects_unverifiable_quote_currency():
    with pytest.raises(DataError, match="FX_RATE_UNAVAILABLE"):
        forward_rules._contract(RAW, "NOT_A_SYMBOL")
    assert forward_rules._contract(RAW, "XAUUSD").quote_to_account == 1.0   # same currency


def test_forward_contract_eur_quote_is_a_dynamic_fx_placeholder_not_a_rate():
    assert portfolio.fx_check("DE40", forward_rules._contract(RAW, "DE40"), fx_available=True)["status"] == "DYNAMIC_FX"
    assert portfolio.fx_check("DE40", forward_rules._contract(RAW, "DE40"), fx_available=False)["status"] == "BLOCKED"
