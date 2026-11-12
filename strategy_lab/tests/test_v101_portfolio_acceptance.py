"""Portfolio acceptance (v101): shared capital, margin, risk budgets, limits, gaps/jumps, FX gating, correlations.

All market data is synthetic and proves engine mechanics only; it is not evidence about any strategy.
"""
from datetime import timedelta

from fastapi.testclient import TestClient
import pytest

from runtime.strategy_lab.execution import ContractSpec
from strategy_lab import forward_rules, server
from strategy_lab.daily_data import DailyPartitionStore
from strategy_lab.data_manager import DataError
from strategy_lab.forward_engine import ForwardEngine
from strategy_lab.forward_registry import ForwardRegistry
from strategy_lab.portfolio import correlation_pairs, forward_correlations, fx_check
from strategy_lab.tests.test_forward_rules import (
    CONTRACT, DAY, ENTRY_ROWS, FROZEN, flat_day, ingest, observed, path_day, setup, spec)

DAY2 = DAY + timedelta(days=1)
HOLD = [(101, 101.2, 100.9, 101.0)] * 3
ENTRY_HOLD = ENTRY_ROWS + HOLD
WIN = ENTRY_ROWS + [(101, 101.2, 100.9, 101.0), (101.0, 103.6, 100.9, 103.5)] + [(100, 100.1, 99.9, 100)] * 3
LOSS = ENTRY_ROWS + [(101, 101.2, 100.9, 101.0), (101.0, 101.1, 99.5, 100.0)] + [(100, 100.1, 99.9, 100)] * 3
SECOND_SIGNAL = [(101, 101.1, 100.9, 101)] * 7          # LOSS + this = stopped out, then a fresh entry signal
FLAT = [(100, 100.1, 99.9, 100)] * 8
SHORT = spec(direction="short", entry={"short": {"left": {"price": "close"}, "op": "<",
                                                  "right": {"value": 101.5}}})


def feed(store, day, rows_by_symbol):
    for symbol, rows in rows_by_symbol.items():
        ingest(store, symbol, day, path_day(day, rows))


def events(engine, portfolio, *kinds):
    return [e for e in engine.report(portfolio)["events"] if not kinds or e["kind"] in kinds]


def test_two_strategies_open_simultaneously_on_shared_capital(tmp_path):
    store, engine, portfolio, _ = setup(tmp_path / "a.sqlite", [spec(symbol="XAUUSD"), spec(symbol="EURUSD")],
                                        capital=1000, risk_limit=0.05, per_member=500)
    feed(store, DAY, {"XAUUSD": ENTRY_HOLD, "EURUSD": ENTRY_HOLD})
    engine.advance(portfolio, as_of=observed(DAY))
    fills = events(engine, portfolio, "ORDER_FILLED")
    assert len(fills) == 2 and fills[0]["candle_at"] == fills[1]["candle_at"]
    assert not events(engine, portfolio, "ORDER_REJECTED")
    report = engine.report(portfolio)
    assert sum(m["allocation"] for m in report["members"]) <= report["portfolio"]["starting_capital"]
    perf = server.summarize_forward(report, model_current=True, data=store)["portfolio"]
    assert perf["open_margin"] == pytest.approx(sum(f["detail"]["margin"] for f in fills))
    assert perf["open_stop_risk"] == pytest.approx(sum(f["detail"]["risk"] for f in fills))
    assert perf["open_margin"] < report["days"][-1]["equity"]
    assert perf["unallocated_capital"] == pytest.approx(0)


def test_insufficient_margin_rejects_the_order_and_leaves_capital_untouched(tmp_path):
    store, engine, portfolio, _ = setup(tmp_path / "a.sqlite", [spec()], capital=1000, risk_limit=0.05,
                                        leverage=1)
    feed(store, DAY, {"XAUUSD": ENTRY_HOLD})
    engine.advance(portfolio, as_of=observed(DAY))
    rejected, = events(engine, portfolio, "ORDER_REJECTED")
    assert rejected["detail"]["reason"] == "SHARED_MARGIN" and rejected["detail"]["margin"] > 1000
    report = engine.report(portfolio)
    assert not events(engine, portfolio, "ORDER_FILLED") and report["members"][0]["state"]["position"] is None
    assert report["portfolio"]["balance"] == report["days"][-1]["equity"] == 1000


def test_a_strategy_cannot_borrow_unallocated_capital_beyond_its_own_budget(tmp_path):
    # 10000 shared capital, but this member is allocated only 500 and needs ~506 of margin at 1:1.
    store, engine, portfolio, _ = setup(tmp_path / "a.sqlite", [spec()], capital=10000, risk_limit=0.05,
                                        per_member=500, leverage=1)
    feed(store, DAY, {"XAUUSD": ENTRY_HOLD})
    engine.advance(portfolio, as_of=observed(DAY))
    rejected, = events(engine, portfolio, "ORDER_REJECTED")
    assert rejected["detail"]["reason"] == "SHARED_MARGIN" and 500 < rejected["detail"]["margin"] < 10000
    perf = server.summarize_forward(engine.report(portfolio), model_current=True, data=store)["portfolio"]
    assert perf["unallocated_capital"] == pytest.approx(9500)
    store2, engine2, portfolio2, ids2 = setup(tmp_path / "b.sqlite", [spec()], capital=1000, per_member=500)
    base = ForwardRegistry(store2).get(ids2[0])["configuration"]
    big = forward_rules.freeze(ForwardRegistry(store2), {**base, "starting_capital": 600}, frozen_at=FROZEN)
    with pytest.raises(DataError, match="exceed shared starting capital"):       # allocations never exceed capital
        engine2.enroll(portfolio2, big["id"], joined_at=FROZEN)


def test_same_market_opposite_signals_hold_two_gross_virtual_positions(tmp_path):
    long_spec, short_spec = spec(), {**SHORT, "name": "ShortFixture"}
    store, engine, portfolio, _ = setup(tmp_path / "a.sqlite", [long_spec, short_spec], capital=1000,
                                        risk_limit=0.05, per_member=500)
    feed(store, DAY, {"XAUUSD": ENTRY_HOLD})
    engine.advance(portfolio, as_of=observed(DAY))
    fills = events(engine, portfolio, "ORDER_FILLED")
    assert sorted(f["detail"]["side"] for f in fills) == ["long", "short"]
    assert sorted(f["detail"]["quote"] for f in fills) == ["ASK", "BID"]       # each side pays the spread
    perf = server.summarize_forward(engine.report(portfolio), model_current=True, data=store)["portfolio"]
    assert perf["open_margin"] == pytest.approx(sum(f["detail"]["margin"] for f in fills))   # no netting
    feed(store, DAY2, {"XAUUSD": [(101.0, 103.6, 100.9, 103.5)] * 2})
    engine.advance(portfolio, as_of=observed(DAY2))
    closed = [e["detail"] for e in events(engine, portfolio, "POSITION_CLOSED")]
    assert {c["exit_reason"] for c in closed} == {"TAKE_PROFIT", "STOP_LOSS"}
    report = engine.report(portfolio)
    assert report["portfolio"]["balance"] == pytest.approx(1000 + sum(c["net_pnl"] for c in closed))
    assert report["days"][-1]["equity"] == pytest.approx(report["portfolio"]["balance"])


def test_daily_loss_limit_blocks_new_entries_for_the_rest_of_the_day_only(tmp_path):
    rows = LOSS + SECOND_SIGNAL
    control = setup(tmp_path / "c.sqlite", [spec()])
    feed(control[0], DAY, {"XAUUSD": rows})
    control[1].advance(control[2], as_of=observed(DAY))
    assert len(events(control[1], control[2], "ORDER_FILLED")) == 2                # no limit: it re-enters

    store, engine, portfolio, _ = setup(tmp_path / "a.sqlite", [spec()])
    engine.set_limits(portfolio, max_daily_loss=0.005)
    feed(store, DAY, {"XAUUSD": rows})
    engine.advance(portfolio, as_of=observed(DAY))
    assert len(events(engine, portfolio, "ORDER_FILLED")) == 1
    rejected, = events(engine, portfolio, "ORDER_REJECTED")
    assert rejected["detail"]["reason"] == "PORTFOLIO_HALT" and rejected["detail"]["halt"] == "DAILY_LOSS_LIMIT"
    report = engine.report(portfolio)
    assert [f["kind"] for f in report["flags"] if f["kind"] == "DAILY_LOSS_HALT"] == ["DAILY_LOSS_HALT"]
    feed(store, DAY2, {"XAUUSD": ENTRY_HOLD})                                       # a new UTC day resets the guard
    engine.advance(portfolio, as_of=observed(DAY2))
    assert len(events(engine, portfolio, "ORDER_FILLED")) == 2
    perf = server.summarize_forward(engine.report(portfolio), model_current=True, data=store)
    assert sum(m["risk_rejections"] for m in perf["members"].values()) == 1
    with pytest.raises(DataError, match="cannot change"):
        engine.set_limits(portfolio, max_daily_loss=0.5)


def test_max_portfolio_drawdown_halts_entries_until_equity_recovers(tmp_path):
    store, engine, portfolio, _ = setup(tmp_path / "a.sqlite", [spec()])
    engine.set_limits(portfolio, max_drawdown=0.005)
    feed(store, DAY, {"XAUUSD": LOSS})
    feed(store, DAY2, {"XAUUSD": ENTRY_HOLD})
    engine.advance(portfolio, as_of=observed(DAY2), max_days=2)
    assert len(events(engine, portfolio, "ORDER_FILLED")) == 1                       # only the day-1 trade
    rejected, = events(engine, portfolio, "ORDER_REJECTED")
    assert rejected["day"] == DAY2.isoformat() and rejected["detail"]["reason"] == "PORTFOLIO_HALT"
    assert rejected["detail"]["halt"] == "MAX_DRAWDOWN"
    assert engine.report(portfolio)["members"][0]["state"]["position"] is None


def test_per_symbol_risk_cap_limits_stacking_on_one_market(tmp_path):
    members = [spec(), {**spec(), "name": "FixtureTwo"}]
    open_ = setup(tmp_path / "a.sqlite", members, capital=1000, risk_limit=0.05, per_member=500)
    feed(open_[0], DAY, {"XAUUSD": ENTRY_HOLD})
    open_[1].advance(open_[2], as_of=observed(DAY))
    assert len(events(open_[1], open_[2], "ORDER_FILLED")) == 2                    # no cap: both stack

    store, engine, portfolio, _ = setup(tmp_path / "b.sqlite", members, capital=1000, risk_limit=0.05,
                                        per_member=500)
    engine.set_limits(portfolio, symbol_risk=0.007)
    feed(store, DAY, {"XAUUSD": ENTRY_HOLD})
    engine.advance(portfolio, as_of=observed(DAY))
    assert len(events(engine, portfolio, "ORDER_FILLED")) == 1
    rejected, = events(engine, portfolio, "ORDER_REJECTED")
    assert rejected["detail"]["reason"] == "SYMBOL_RISK_LIMIT"


def test_restart_with_an_open_position_keeps_its_margin_and_risk_in_the_shared_budget(tmp_path):
    path = tmp_path / "a.sqlite"
    store, engine, portfolio, _ = setup(path, [spec(symbol="XAUUSD"), spec(symbol="EURUSD")],
                                        capital=1000, risk_limit=0.008, per_member=500)
    feed(store, DAY, {"XAUUSD": ENTRY_HOLD, "EURUSD": FLAT})
    engine.advance(portfolio, as_of=observed(DAY))
    assert len(events(engine, portfolio, "ORDER_FILLED")) == 1
    engine = ForwardEngine(ForwardRegistry(DailyPartitionStore(path)))               # restart with the position open
    feed(store, DAY2, {"XAUUSD": HOLD, "EURUSD": ENTRY_HOLD})
    engine.advance(portfolio, as_of=observed(DAY2))
    rejected, = events(engine, portfolio, "ORDER_REJECTED")
    assert rejected["detail"]["reason"] == "RISK_LIMIT" and rejected["day"] == DAY2.isoformat()
    assert len(events(engine, portfolio, "ORDER_FILLED")) == 1


def test_new_data_after_a_gap_fills_a_stop_at_the_gap_open_and_flags_the_gap(tmp_path):
    store, engine, portfolio, _ = setup(tmp_path / "a.sqlite", [spec()])
    feed(store, DAY, {"XAUUSD": ENTRY_HOLD})
    feed(store, DAY2, {"XAUUSD": [(98.0, 98.5, 97.5, 98.0)] * 2})
    engine.advance(portfolio, as_of=observed(DAY2), max_days=2)
    closed, = events(engine, portfolio, "POSITION_CLOSED")
    assert closed["detail"]["exit_reason"] == "STOP_LOSS" and closed["detail"]["exit"] == pytest.approx(97.99)
    assert closed["detail"]["exit"] < closed["detail"]["stop"]                       # worse than the stop, not at it
    gaps = [f for f in engine.report(portfolio)["flags"] if f["kind"] == "DATA_GAP"]
    assert len(gaps) == 1 and gaps[0]["day"] == DAY2.isoformat() and gaps[0]["detail"]["gap_minutes"] > 1000


def test_a_missing_day_waits_instead_of_guessing(tmp_path):
    store, engine, portfolio, _ = setup(tmp_path / "a.sqlite", [spec()])
    feed(store, DAY, {"XAUUSD": ENTRY_HOLD})
    feed(store, DAY + timedelta(days=2), {"XAUUSD": HOLD})
    result = engine.advance(portfolio, as_of=observed(DAY + timedelta(days=2)), max_days=5)
    assert result["status"] == "WAITING_FOR_DATA" and result["day"] == DAY2.isoformat()
    assert result["committed_days"] == [DAY.isoformat()]
    assert engine.report(portfolio)["portfolio"]["last_day"] == DAY.isoformat()


def test_a_large_price_jump_is_flagged_and_blocks_entries_on_that_candle(tmp_path):
    def run(name, open_price):
        store, engine, portfolio, _ = setup(tmp_path / name, [spec()])
        feed(store, DAY, {"XAUUSD": ENTRY_ROWS})
        ingest(store, "XAUUSD", DAY2, path_day(DAY2, [(open_price, open_price + 0.1, open_price - 0.1,
                                                       open_price)] * 3, start="12:05"))
        engine.advance(portfolio, as_of=observed(DAY2), max_days=2)
        return engine, portfolio

    engine, portfolio = run("calm.sqlite", 101.0)
    assert len(events(engine, portfolio, "ORDER_FILLED")) == 1
    assert not [f for f in engine.report(portfolio)["flags"] if f["kind"] == "PRICE_JUMP"]
    engine, portfolio = run("jump.sqlite", 130.0)
    jump, = [f for f in engine.report(portfolio)["flags"] if f["kind"] == "PRICE_JUMP"]
    assert jump["detail"]["move"] > 0.2 and jump["day"] == DAY2.isoformat()
    rejected, = events(engine, portfolio, "ORDER_REJECTED")
    assert rejected["detail"]["reason"] == "PORTFOLIO_HALT" and rejected["detail"]["halt"] == "PRICE_JUMP"
    assert not events(engine, portfolio, "ORDER_FILLED")


def _fx_portfolio(tmp_path, symbol, contract_over, currency="USD"):
    store = DailyPartitionStore(tmp_path / "fx.sqlite")
    ingest(store, symbol, DAY - timedelta(days=1), flat_day(DAY - timedelta(days=1)))
    engine = ForwardEngine(ForwardRegistry(store))
    portfolio = engine.create("FX", currency=currency, starting_capital=1000, risk_limit=0.05)
    strategy = spec(symbol=symbol)
    contract = {**CONTRACT, **contract_over}
    warm = forward_rules.warmup_from_fetch(forward_rules.store_fetch(store, symbol), strategy, None, as_of=FROZEN)
    config = forward_rules.build_configuration(strategy, None, contract=contract, starting_capital=1000,
                                               trade_risk=0.01, portfolio_risk_limit=0.05, warmup=warm,
                                               data_version="fixture")
    registration = forward_rules.freeze(ForwardRegistry(store), config, frozen_at=FROZEN)
    return engine, portfolio, registration


def test_missing_or_wrong_fx_conversion_blocks_enrollment_and_is_flagged_otherwise(tmp_path, monkeypatch):
    engine, portfolio, registration = _fx_portfolio(tmp_path, "USDJPY", {"quote_to_account": 1})
    engine.enroll(portfolio, registration["id"], joined_at=FROZEN)
    fx = engine.report(portfolio)["members"][0]["fx"]
    assert fx["status"] == "DYNAMIC_FX" and fx["pair"] == "USDJPY"                   # the frozen 1.0 is never used
    eur = tmp_path / "eur"
    eur.mkdir()
    engine, portfolio, registration = _fx_portfolio(eur, "XAUUSD", {"account_currency": "EUR"}, currency="EUR")
    with pytest.raises(DataError, match="no causal conversion"):
        engine.enroll(portfolio, registration["id"], joined_at=FROZEN)
    none = tmp_path / "none"
    none.mkdir()
    monkeypatch.setattr("strategy_lab.forward_engine.load_fx", lambda: None)
    engine, portfolio, registration = _fx_portfolio(none, "DE40", {})
    with pytest.raises(DataError, match="FX service"):
        engine.enroll(portfolio, registration["id"], joined_at=FROZEN)


def test_fx_check_covers_every_status():
    usd = ContractSpec(**CONTRACT)
    assert fx_check("XAUUSD", usd)["status"] == "OK"
    assert fx_check("DE40", usd, fx_available=True)["status"] == "DYNAMIC_FX"        # EUR index: converted per candle
    blocked = fx_check("DE40", usd, fx_available=False)
    assert blocked["status"] == "BLOCKED" and blocked["reason_code"] == "FX_RATE_UNAVAILABLE"
    assert fx_check("NOT_A_SYMBOL", usd)["status"] == "UNKNOWN_INSTRUMENT"
    assert fx_check("XAUUSD", ContractSpec(**{**CONTRACT, "account_currency": "GBP"}))["status"] == "BLOCKED"


def test_correlation_api_aligns_daily_returns_and_refuses_short_series(tmp_path, monkeypatch):
    path = tmp_path / "a.sqlite"
    pattern = [1, 0, 0, 1, 1, 0, 1, 0, 0, 1, 0, 1]
    members = [spec(symbol="XAUUSD"), spec(symbol="EURUSD"), spec(symbol="GBPUSD")]
    store, engine, portfolio, ids = setup(path, members, capital=3000, risk_limit=0.05, per_member=1000)
    for offset, outcome in enumerate(pattern):
        day = DAY + timedelta(days=offset)
        feed(store, day, {"XAUUSD": WIN if outcome else LOSS, "EURUSD": WIN if outcome else LOSS,
                          "GBPUSD": LOSS if outcome else WIN})
        engine.advance(portfolio, as_of=observed(day))
    assert len(engine.report(portfolio)["days"]) == 12
    monkeypatch.setattr(server, "DEFAULT_DAILY_STORE", path)
    client = TestClient(server.app)
    route = f"/api/forward/portfolios/{portfolio}/correlations"
    body = client.get(route, params={"min_obs": 10}).json()
    forward = body["classes"]["DAILY_DELAYED_FORWARD"]
    assert forward["status"] == "OK" and len(forward["pairs"]) == 3
    by_pair = {frozenset((p["first"], p["second"])): p for p in forward["pairs"]}
    assert all(p["n_obs"] == 12 for p in forward["pairs"])
    same = by_pair[frozenset((ids[0], ids[1]))]
    opposite = by_pair[frozenset((ids[0], ids[2]))]
    assert same["correlation"] == pytest.approx(1.0) and opposite["correlation"] == pytest.approx(-1.0)
    assert same["high_correlation"] and len(body["correlation_risk_pairs"]) == 3
    assert body["classes"]["HISTORICAL_BACKTEST"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert body["classes"]["HISTORICAL_OOS"]["pairs"] == []
    assert body["classes"]["REALTIME_PAPER"]["status"] == "NOT_IMPLEMENTED"
    short = client.get(route, params={"min_obs": 30}).json()["classes"]["DAILY_DELAYED_FORWARD"]
    assert short["status"] == "INSUFFICIENT_EVIDENCE"
    assert all(p["correlation"] is None and p["status"] == "INSUFFICIENT_EVIDENCE" and p["n_obs"] == 12
               for p in short["pairs"])
    assert client.get(route, params={"min_obs": 2}).status_code == 422


def test_correlation_pairs_never_report_a_number_for_flat_or_misaligned_series():
    flat = {f"2026-01-{d:02d}": 0.0 for d in range(1, 11)}
    moving = {f"2026-01-{d:02d}": 0.001 * d for d in range(1, 11)}
    late = {f"2026-01-{d:02d}": 0.001 * d for d in range(8, 20)}
    pairs = {(p["first"], p["second"]): p for p in correlation_pairs(
        {"flat": flat, "moving": moving, "late": late}, min_obs=5)}
    assert pairs[("flat", "moving")]["status"] == "INSUFFICIENT_EVIDENCE" and "zero variance" in pairs[("flat", "moving")]["reason"]
    assert pairs[("late", "moving")]["n_obs"] == 3 and pairs[("late", "moving")]["correlation"] is None


def test_historical_oos_and_prospective_correlations_stay_in_separate_classes(tmp_path):
    store = DailyPartitionStore(tmp_path / "a.sqlite")
    engine = ForwardEngine(ForwardRegistry(store))
    portfolio = engine.create("Classes", currency="USD", starting_capital=1000, risk_limit=0.05)
    hist = {f"2025-01-{d:02d}": ((-1) ** d) * 0.001 * d for d in range(1, 29)}
    oos = {f"2025-06-{d:02d}": 0.001 * d for d in range(1, 11)}
    for symbol in ("XAUUSD", "EURUSD"):
        ingest(store, symbol, DAY - timedelta(days=1), flat_day(DAY - timedelta(days=1)))
        strategy = spec(symbol=symbol)
        warm = forward_rules.warmup_from_fetch(forward_rules.store_fetch(store, symbol), strategy, None, as_of=FROZEN)
        config = forward_rules.build_configuration(strategy, None, contract=CONTRACT, starting_capital=500,
                                                   trade_risk=0.01, portfolio_risk_limit=0.05, warmup=warm,
                                                   data_version="fixture")
        config["pre_registration_returns"] = {"HISTORICAL_BACKTEST": hist, "HISTORICAL_OOS": oos}
        engine.enroll(portfolio, forward_rules.freeze(ForwardRegistry(store), config, frozen_at=FROZEN)["id"],
                      joined_at=FROZEN)
    classes = forward_correlations(engine.report(portfolio), min_obs=20)["classes"]
    backtest, sealed, prospective = (classes[k] for k in ("HISTORICAL_BACKTEST", "HISTORICAL_OOS",
                                                          "DAILY_DELAYED_FORWARD"))
    assert backtest["status"] == "OK" and backtest["pairs"][0]["n_obs"] == 28
    assert backtest["pairs"][0]["correlation"] == pytest.approx(1.0)
    assert sealed["status"] == "INSUFFICIENT_EVIDENCE" and sealed["pairs"][0]["n_obs"] == 10
    assert prospective["status"] == "INSUFFICIENT_EVIDENCE" and prospective["pairs"][0]["n_obs"] == 0
    assert forward_correlations(engine.report(portfolio), min_obs=5)["classes"]["HISTORICAL_OOS"]["status"] == "OK"
