import json
from pathlib import Path

from fastapi.testclient import TestClient

from strategy_lab import server


def _save_run(root: Path, run_id: str, *, strategy: str, symbol: str,
              balance: float, trades: list[dict], currency: str = "USD",
              signals_recomputed: bool = False, policy: str = "OHLC_MODEL"):
    run_dir = root / run_id
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({
        "action": "backtest", "source_root": r"C:\AI\Dukascopy_data",
        "strategy": strategy, "symbol": symbol, "timeframe": "M1",
        "starting_balance": balance,
    }), encoding="utf-8")
    (run_dir / "bidask_execution.json").write_text(json.dumps({
        "status": "DUKASCOPY_BIDASK_REPLAY",
        "contract_spec_status": "USER_SUPPLIED_NOT_BROKER_VERIFIED",
        "contract": {"account_currency": currency, "contract_size": 100,
                     "tick_size": 0.01, "quote_to_account": 1,
                     "volume_step": 0.01},
        "independent_model": {
            "strategy_signals_recomputed": signals_recomputed,
            "execution_policy": policy, "quote_quality": "COMPLETE_QUOTES",
            "trades": trades,
        },
    }), encoding="utf-8")


def test_portfolio_api_combines_only_compatible_saved_bidask_runs(tmp_path, monkeypatch):
    first = "11111111-1111-4111-8111-111111111111"
    second = "22222222-2222-4222-8222-222222222222"
    _save_run(tmp_path, first, strategy="Alpha", symbol="XAUUSD", balance=1000,
              trades=[{"entry_timestamp_ms": 10, "exit_timestamp_ms": 100, "net_pnl": -10},
                      {"entry_timestamp_ms": 20, "exit_timestamp_ms": 200, "net_pnl": 20}])
    _save_run(tmp_path, second, strategy="Beta", symbol="EURUSD", balance=2000,
              trades=[{"entry_timestamp_ms": 50, "exit_timestamp_ms": 100, "net_pnl": -5}])
    monkeypatch.setattr(server, "RESULT_ROOT", tmp_path)

    response = TestClient(server.app).post("/api/portfolio/analysis", json={"runs": [
        {"run_id": first, "allocated_capital": 1000},
        {"run_id": second, "allocated_capital": 2000},
    ]})

    assert response.status_code == 200
    portfolio = response.json()
    assert portfolio["status"] == "PROVISIONAL_RESEARCH_PORTFOLIO"
    assert portfolio["combined_financials_available"] is True
    assert portfolio["account_currency"] == "USD"
    assert portfolio["allocated_capital"] == 3000
    assert portfolio["net_pnl"] == 5
    assert portfolio["ending_equity"] == 3005
    assert portfolio["max_drawdown_money"] == 15
    assert portfolio["simultaneous_losing_trades_max"] == 2
    assert len(portfolio["by_strategy"]) == 2
    assert len(portfolio["by_instrument"]) == 2
    assert len(portfolio["correlations"]) == 1
    assert portfolio["correlations"][0]["daily_normalized_return_correlation"] is None
    assert portfolio["broker_validated"] is False


def test_portfolio_api_blocks_unmatched_capital_allocation(tmp_path, monkeypatch):
    first = "11111111-1111-4111-8111-111111111111"
    second = "22222222-2222-4222-8222-222222222222"
    _save_run(tmp_path, first, strategy="Alpha", symbol="XAUUSD", balance=1000, trades=[])
    _save_run(tmp_path, second, strategy="Beta", symbol="EURUSD", balance=2000, trades=[])
    monkeypatch.setattr(server, "RESULT_ROOT", tmp_path)

    response = TestClient(server.app).post("/api/portfolio/analysis", json={"runs": [
        {"run_id": first, "allocated_capital": 500},
        {"run_id": second, "allocated_capital": 2000},
    ]})

    assert response.status_code == 200
    portfolio = response.json()
    assert portfolio["status"] == "COMPATIBILITY_BLOCKED"
    assert portfolio["combined_financials_available"] is False
    assert "ALLOCATION_REQUIRES_RERUN" in {item["code"] for item in portfolio["blockers"]}
    assert "net_pnl" not in portfolio


def test_portfolio_blocks_mixed_quote_currency_and_signal_models():
    from strategy_lab.portfolio import analyze_portfolio

    inputs = []
    for run_id, currency, recomputed in (("a", "USD", False), ("b", "EUR", True)):
        manifest = {"action": "backtest", "source_root": r"C:\AI\Dukascopy_data",
                    "strategy": "Alpha", "symbol": "XAUUSD", "timeframe": "M1",
                    "starting_balance": 1000}
        saved = {"status": "DUKASCOPY_BIDASK_REPLAY",
                 "contract_spec_status": "USER_SUPPLIED_NOT_BROKER_VERIFIED",
                 "contract": {"account_currency": currency, "contract_size": 100,
                              "tick_size": 0.01, "quote_to_account": 1,
                              "volume_step": 0.01},
                 "independent_model": {"strategy_signals_recomputed": recomputed,
                                       "execution_policy": "OHLC_MODEL",
                                       "quote_quality": "COMPLETE_QUOTES", "trades": []}}
        inputs.append({"run_id": run_id, "allocated_capital": 1000,
                       "manifest": manifest, "bidask": saved})

    result = analyze_portfolio(inputs)
    codes = {item["code"] for item in result["blockers"]}
    assert result["status"] == "COMPATIBILITY_BLOCKED"
    assert {"ACCOUNT_CURRENCY_MISMATCH", "SIGNAL_MODEL_MISMATCH"} <= codes
    assert result["combined_financials_available"] is False
    assert "net_pnl" not in result


def test_portfolio_correlation_aligns_daily_normalized_returns():
    from strategy_lab.portfolio import analyze_portfolio

    dates = [1767225600000, 1767312000000, 1767398400000]
    inputs = []
    for run_id, scale in (("a", 1), ("b", 2)):
        trades = [{"entry_timestamp_ms": stamp - 60000,
                   "exit_timestamp_ms": stamp, "net_pnl": 10 * scale * (i + 1)}
                  for i, stamp in enumerate(dates)]
        inputs.append({"run_id": run_id, "allocated_capital": 1000,
                       "manifest": {"action": "backtest", "source_root": r"C:\AI\Dukascopy_data",
                                    "strategy": "Alpha", "symbol": "XAUUSD", "timeframe": "M1",
                                    "starting_balance": 1000},
                       "bidask": {"status": "DUKASCOPY_BIDASK_REPLAY",
                                  "contract_spec_status": "USER_SUPPLIED_NOT_BROKER_VERIFIED",
                                  "contract": {"account_currency": "USD", "contract_size": 100,
                                               "tick_size": 0.01, "quote_to_account": 1,
                                               "volume_step": 0.01},
                                  "independent_model": {"strategy_signals_recomputed": False,
                                                        "execution_policy": "OHLC_MODEL",
                                                        "quote_quality": "COMPLETE_QUOTES",
                                                        "trades": trades}}})

    result = analyze_portfolio(inputs)
    assert result["status"] == "PROVISIONAL_RESEARCH_PORTFOLIO"
    assert result["correlations"][0]["observation_days"] == 3
    assert result["correlations"][0]["daily_normalized_return_correlation"] == 1.0


def test_runs_endpoint_marks_saved_bidask_sidecars(tmp_path, monkeypatch):
    run_id = "11111111-1111-4111-8111-111111111111"
    _save_run(tmp_path, run_id, strategy="Alpha", symbol="XAUUSD", balance=1000, trades=[])
    (tmp_path / run_id / "result.json").write_text(json.dumps({"metrics": {}, "trades": []}),
                                                   encoding="utf-8")
    monkeypatch.setattr(server, "RESULT_ROOT", tmp_path)

    response = TestClient(server.app).get("/api/runs")

    assert response.status_code == 200
    assert response.json()["runs"][0]["run_id"] == run_id
    assert response.json()["runs"][0]["bidask_available"] is True
