from datetime import datetime, timezone

from strategy_lab.interpretation import interpret_run


def _ms(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000


def _trade(day, pnl, fee=1.0):
    return {"opened_at": _ms(*day), "closed_at": _ms(*day), "PNL": pnl, "fee": fee, "type": "long"}


MANIFEST = {"symbol": "XAUUSD", "timeframe": "M15", "start_date": "2024-01-01", "end_date": "2025-12-31",
            "starting_balance": 10000.0, "action": "backtest"}


def _sections(out):
    return {s["key"]: s for s in out["sections"]}


def test_profit_case_has_all_sections_and_computed_facts():
    trades = [_trade((2024, 1 + i % 12, 5), 20.0 if i % 4 else -10.0) for i in range(40)]
    out = interpret_run(MANIFEST, {"trades": trades, "metrics": {}}, {})
    sec = _sections(out)
    assert out["evidence_class"] == "HISTORICAL_BACKTEST"
    assert set(sec) == {"performance", "risk", "weak_periods", "evidence", "costs", "failed_checks", "missing"}
    assert sec["performance"]["status"] == "good"
    assert sec["performance"]["facts"]["net_pnl"] == 30 * 20.0 - 10 * 10.0
    assert sec["costs"]["facts"]["pnl_if_costs_doubled"] == 500.0 - 40.0
    assert sec["evidence"]["status"] == "warn"
    assert "geen bewijs van toekomstige prestaties" in out["warning_nl"] + out["evidence_label_nl"]


def test_losing_case_fails_checks_and_drawdown_from_trades():
    trades = [_trade((2024, 3, 1 + i), -50.0) for i in range(10)]
    out = interpret_run(MANIFEST, {"trades": trades}, {})
    sec = _sections(out)
    assert sec["performance"]["status"] == "bad"
    assert sec["risk"]["facts"]["max_drawdown_abs"] == 500.0
    assert sec["risk"]["facts"]["longest_losing_streak"] == 10
    failed = sec["failed_checks"]["facts"]["failed"]
    assert any("Negatief" in f for f in failed) and any("trades" in f for f in failed)


def test_few_trades_is_bad_evidence_and_short_period_noted():
    manifest = {**MANIFEST, "start_date": "2024-01-01", "end_date": "2024-01-10"}
    out = interpret_run(manifest, {"trades": [_trade((2024, 1, 5), 10.0)]}, {})
    sec = _sections(out)
    assert sec["evidence"]["status"] == "bad"
    assert "te kort" in sec["weak_periods"]["text_nl"]


def test_missing_fees_reported_and_missing_steps_listed():
    trades = [{"closed_at": _ms(2024, 2, 1), "PNL": 5.0}] * 3
    out = interpret_run(MANIFEST, {"trades": trades}, {"optimization_done": False, "validation_done": False,
                                                       "forward_registered": False})
    sec = _sections(out)
    assert "niet afzonderlijk vastgelegd" in sec["costs"]["text_nl"]
    assert sec["costs"]["facts"]["pnl_if_costs_doubled"] is None
    assert sec["missing"]["facts"]["missing"] == ["Optimalisatie", "Statistische validatie (walk-forward, Monte Carlo)", "Forwardtest"]


def test_optimize_run_uses_holdout_metrics_as_oos():
    best = {"trial": 3, "status": "COMPLETED",
            "testing_metrics": {"total": 12, "net_profit": 50.0, "win_rate": 0.5, "gross_profit": 150.0,
                                "gross_loss": -100.0, "fee": 4.0, "starting_balance": 10000.0,
                                "max_drawdown": -1.5, "losing_streak": 3, "largest_losing_trade": -30.0},
            "training_metrics": {"total": 50}}
    result = {"action": "optimize", "best_trials": [best], "all_trials": [best] * 20,
              "optimization": {"holdout_percent": 20.0}}
    out = interpret_run({**MANIFEST, "action": "optimize"}, result, {})
    sec = _sections(out)
    assert out["evidence_class"] == "HISTORICAL_OOS"
    assert sec["performance"]["facts"]["profit_factor"] == 1.5
    assert sec["risk"]["facts"]["max_drawdown_pct"] == 1.5
    assert "20 geteste" in sec["evidence"]["text_nl"]
