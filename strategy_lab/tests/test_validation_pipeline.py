"""Adapters, generic validation runner, trial accounting, tester ledger registration and Jesse run cancel."""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from strategy_lab import research_api, server  # noqa: E402
from strategy_lab.research import adapters, validation_runner  # noqa: E402
from strategy_lab.research.jobs import JobCancelled  # noqa: E402
from strategy_lab.research.ledger import ResearchLedger, hypothesis_id  # noqa: E402


class Ctx:
    def __init__(self):
        self.messages, self.cancelled = [], False

    def progress(self, fraction, message=""):
        self.messages.append(message)

    def check(self):
        if self.cancelled:
            raise JobCancelled()


def synthetic_adapter(**overrides):
    """Deterministic fake engine: one trade per business day; edge depends on parameter 'a'."""
    specs = {"a": {"type": "int", "min": 1, "max": 10}, "b": {"type": "float", "min": 0.5, "max": 2.0},
             "lots": {"type": "float", "min": 0.1, "max": 1.0}}

    def run(params, start, end, spread_multiplier=1.0, **_):
        days = pd.bdate_range(start, pd.Timestamp(end) - pd.Timedelta(days=1), tz="UTC")
        rng = np.random.default_rng(int(params["a"] * 100 + params["b"] * 10))
        trades = []
        for d in days:
            pnl = float(rng.normal(3.0 - abs(params["a"] - 5) * 0.4, 10.0)) - 0.5 * (spread_multiplier - 1)
            trades.append({"entry_time": (d + pd.Timedelta(hours=9)).isoformat(),
                           "exit_time": (d + pd.Timedelta(hours=15)).isoformat(), "side": "long", "pnl": pnl})
        return {"trades": trades, "metrics": adapters.normalize_metrics(trades, 10000)}

    kw = dict(kind="builder", name="Synth", ref="Synth", symbol="XAUUSD", timeframe="M15", start="2023-01-01",
              end="2024-07-01", capital=10000, run=run, defaults={"a": 5, "b": 1.0, "lots": 0.5}, param_specs=specs,
              family="builder:Synth", hyp_key="builder:synth", code_sha256="c" * 64, engine_nl="Synthetische engine",
              capabilities={"walk_forward": True, "stability": True, "forward": False},
              limitations_nl=["Forward niet beschikbaar in test."],
              supported_cost_scenarios={"baseline": {}, "spread_+50%": {"spread_multiplier": 1.5}},
              unsupported_cost_scenarios=[{"name": "slippage_+1pt", "reason_nl": "niet in test"}],
              dataset_sha256="d" * 64)
    kw.update(overrides)
    return adapters.StrategyAdapter(**kw)


FAST = {"walk_forward": {"train_days": 120, "test_days": 60, "step_days": 60, "max_combos": 3, "min_trades": 5},
        "pbo": {"max_combos": 3}, "monte_carlo": {"n": 200}}


# ------------------------------------------------------------------ runner
def test_runner_all_steps_produce_evidence_config_and_progress(tmp_path):
    ctx, led = Ctx(), ResearchLedger(tmp_path / "l.sqlite3")
    r = validation_runner.run_validation(ctx, synthetic_adapter(), FAST, ledger=led)
    for name in ("base", "cost_stress", "walk_forward", "parameter_stability", "pbo", "monte_carlo", "statistics",
                 "holdout"):
        assert r["steps"][name]["status"] == "DONE", (name, r["steps"][name])
    assert any(m.startswith("Walk-forward venster 1/") and "combinatie 1/3" in m for m in ctx.messages)
    assert any("test op ongeziene data" in m for m in ctx.messages)
    assert r["oos_equity"] and r["config"]["walk_forward"]["windows"]
    assert r["config"]["dataset_sha256"] == "d" * 64 and r["config"]["seeds"]["monte_carlo"] == 0
    assert "lots" in r["config"]["fixed_params"] and "lots" not in r["config"]["grid"]
    assert r["forward_gaps_nl"] and "prospectieve forward test" in r["forward_gaps_nl"][-1]
    entry = led.get(r["ledger_id"])
    assert entry["kind"] == "VALIDATION" and entry["statistics"]["n_trials"] == r["n_trials"]
    assert entry["statistics"]["configs_evaluated"] == r["trial_accounting"]["in_run_configs_new"]
    assert r["trial_accounting"]["in_run_configs"] == len({json.dumps(c, sort_keys=True) for c in (
        r["config"]["walk_forward"]["candidates"] + r["config"]["pbo"]["candidates"])}) + 4  # + stability moves


def test_runner_step_flags_report_not_tested_with_reason(tmp_path):
    cfg = {**FAST, "walk_forward": {"enabled": False}, "stability": {"enabled": False}, "pbo": {"enabled": False},
           "cost_stress": {"enabled": False}}
    r = validation_runner.run_validation(Ctx(), synthetic_adapter(), cfg, ledger=ResearchLedger(tmp_path / "l"))
    for name in ("walk_forward", "parameter_stability", "pbo", "cost_stress"):
        assert r["steps"][name]["status"] == "NOT_TESTED" and r["steps"][name]["reason_nl"]
    checks = {c["name"]: c for c in r["scorecard"]["checks"]}
    assert checks["walk_forward_efficiency"]["status"] == "NOT_TESTED"
    assert "uitgeschakeld" in checks["walk_forward_efficiency"]["explanation_nl"]
    assert checks["parameter_stability"]["status"] == "NOT_TESTED"
    assert r["scorecard"]["verdict"] in ("REJECT", "NEEDS_MORE_DATA", "RESEARCH_CANDIDATE")
    assert r["timing"]["backtests"] == 2  # base + holdout only


def test_runner_unsupported_spread_stress_is_not_tested_not_faked(tmp_path):
    adapter = synthetic_adapter(supported_cost_scenarios={"baseline": {}, "fee ×2": {}},
                                unsupported_cost_scenarios=[{"name": "spread_+50%", "reason_nl": "Bid-only engine"}],
                                capabilities={"walk_forward": False, "forward": False})
    cfg = {**FAST, "stability": {"enabled": False}, "pbo": {"enabled": False}}
    r = validation_runner.run_validation(Ctx(), adapter, cfg, ledger=ResearchLedger(tmp_path / "l"))
    check = next(c for c in r["scorecard"]["checks"] if c["name"] == "cost_stress")
    assert check["status"] == "NOT_TESTED" and "Bid-only engine" in check["explanation_nl"]
    assert r["steps"]["walk_forward"]["status"] == "NOT_SUPPORTED"
    assert set(r["evidence"]["cost_stress"]["scenarios"]) == {"baseline", "fee ×2"}


def test_runner_failed_optional_step_is_reported_and_cancel_propagates(tmp_path):
    base = synthetic_adapter()
    calls = {"n": 0}

    def flaky(params, start, end, **kw):
        calls["n"] += 1
        if params.get("a") != 5:
            raise RuntimeError("engine kapot")
        return base._run(params, start, end, **kw)

    r = validation_runner.run_validation(Ctx(), synthetic_adapter(run=flaky), FAST, ledger=ResearchLedger(tmp_path / "l"))
    assert r["steps"]["parameter_stability"]["status"] == "ERROR"
    assert "engine kapot" in r["steps"]["parameter_stability"]["reason_nl"]
    ctx = Ctx()
    ctx.cancelled = True
    with pytest.raises(JobCancelled):
        validation_runner.run_validation(ctx, synthetic_adapter(), FAST, ledger=ResearchLedger(tmp_path / "m"))


# ------------------------------------------------------------------ trial accounting
def test_repeated_validation_increases_n(tmp_path):
    led = ResearchLedger(tmp_path / "l.sqlite3")
    first = validation_runner.run_validation(Ctx(), synthetic_adapter(), FAST, ledger=led)
    second = validation_runner.run_validation(Ctx(), synthetic_adapter(), FAST, ledger=led)
    assert second["n_trials"] > first["n_trials"]
    assert second["trial_accounting"]["ledger_prior"] >= first["trial_accounting"]["in_run_configs_new"]
    assert led.n_trials(symbol="XAUUSD", timeframe="M15", include_validation_configs=True) > \
        led.n_trials(symbol="XAUUSD", timeframe="M15")


def test_optimizer_trials_increase_n_and_ledger_rows_are_not_double_counted(tmp_path):
    led = ResearchLedger(tmp_path / "l.sqlite3")
    adapter = synthetic_adapter(jesse_strategy="Synth")
    configs = {adapter.hypothesis_id({"a": 5}): {}}
    base = validation_runner.trial_accounting(led, adapter, configs, set())
    with_opt = validation_runner.trial_accounting(led, adapter, configs, {"o1", "o2", "o3"})
    assert with_opt["total"] == base["total"] + 3 and with_opt["optimizer_trials"] == 3
    led.record(kind="BACKTEST", family="jesse:Synth", symbol="XAUUSD", timeframe="M15", hypothesis_id="o1")
    after = validation_runner.trial_accounting(led, adapter, configs, {"o1", "o2", "o3"})
    assert after["optimizer_trials_not_in_ledger"] == 2 and after["total"] == with_opt["total"]
    led.record(kind="BACKTEST", family="builder:Synth", symbol="XAUUSD", timeframe="M15",
               hypothesis_id=adapter.hypothesis_id({"a": 5}))
    again = validation_runner.trial_accounting(led, adapter, configs, {"o1", "o2", "o3"})
    assert again["in_run_configs_new"] == 0 and again["total"] == after["total"]


def test_optimizer_trial_ids_from_saved_runs(tmp_path):
    defs = [{"name": "x", "type": "int", "min": 1, "max": 9, "default": 3}]
    for i, (action, strategy, trials) in enumerate((
            ("optimize", "S", [{"params": {"x": 1}}, {"params": {"x": 2}}, {"params": {"x": 1}}]),
            ("optimize", "S", [{"params": {"x": 2}}, {"params": {"x": 4}}]),
            ("optimize", "Other", [{"params": {"x": 7}}]), ("backtest", "S", []))):
        d = tmp_path / f"run{i}"
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({"action": action, "strategy": strategy, "symbol": "XAUUSD",
                                                     "parameters": {}}), encoding="utf-8")
        (d / "result.json").write_text(json.dumps({"all_trials": trials}), encoding="utf-8")
    ids = adapters.optimizer_trial_ids("S", "XAUUSD", tmp_path, defs)
    assert ids == {hypothesis_id("jesse:S", {"x": v}) for v in (1, 2, 4)}


def test_tester_backtests_and_optimizer_trials_are_recorded_once_per_param_set(tmp_path, monkeypatch):
    led = ResearchLedger(tmp_path / "l.sqlite3")
    monkeypatch.setattr(research_api, "_ledger", led)
    defs = [{"name": "x", "type": "int", "min": 1, "max": 9, "default": 3}]
    manifest = {"run_id": "r1", "symbol": "XAUUSD", "strategy_sha256": "s", "dataset_sha256": "d"}
    req = server.RunRequest(symbol="XAUUSD", strategy="S", start_date="2026-01-01", end_date="2026-02-01",
                            timeframe="M15")
    server._record_tester_ledger(req, defs, {}, {"metrics": {"total": 4, "net_profit": 1.0}}, manifest)
    server._record_tester_ledger(req, defs, {"x": 3}, {"metrics": {"total": 4}}, manifest)
    assert led.n_trials(family="jesse:S") == 1
    opt = server.RunRequest(action="optimize", symbol="XAUUSD", strategy="S", start_date="2026-01-01",
                            end_date="2026-02-01", timeframe="M15")
    server._record_tester_ledger(opt, defs, {}, {"all_trials": [
        {"trial": 0, "params": {"x": 3}, "status": "COMPLETED", "training_metrics": {"total": 2}},
        {"trial": 1, "params": {"x": 5}, "status": "COMPLETED", "training_metrics": {"total": 2}},
        {"trial": 2, "params": {"x": 6}, "status": "FAILED"}]}, manifest)
    assert led.n_trials(family="jesse:S") == 3
    row = led.list(family="jesse:S", limit=1)[0]
    assert row["hypothesis_id"] == hypothesis_id("jesse:S", {"x": 6}) and row["status"] == "ERROR"
    monkeypatch.setattr(research_api, "_ledger", object())  # broken ledger must not break the tester
    server._record_tester_ledger(req, defs, {}, {"metrics": {}}, manifest)


# ------------------------------------------------------------------ Jesse adapter (mocked runner)
JESSE_DEFS = [{"name": "LabBreakout", "sha256": "abc", "parameters": [
    {"name": "order_mode", "type": "categorical", "options": [0, 1, 2], "default": 0},
    {"name": "entry_interval", "type": "int", "min": 10, "max": 240, "default": 60},
    {"name": "position_size", "type": "float", "min": 0.01, "max": 10.0, "default": 1.0}]}]


def _jesse_trade(day, pnl, kind="long"):
    t0 = pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=10)
    return {"opened_at": t0.value // 10**6, "closed_at": (t0 + pd.Timedelta(hours=1)).value // 10**6,
            "PNL": pnl, "fee": 0.5, "type": kind, "entry_price": 1.0, "exit_price": 1.1, "qty": 1}


def test_jesse_adapter_normalizes_trades_dates_cache_fee_and_futures_switch():
    calls = []

    def runner(name, symbol, timeframe, start, end, params, fee=0.0006, account_type="spot", **kw):
        calls.append((start, end, dict(params), fee, account_type))
        if params.get("order_mode") == 2 and account_type == "spot":
            raise server.JesseRunError(422, "short", "spot_short")
        return {"result": {"trades": [_jesse_trade(start, -2.0 * fee / 0.0006)],
                           "metrics": {"max_drawdown": -12.5, "total": 1}}}

    a = adapters.make_adapter("jesse", "LabBreakout", "XAUUSD", "M15", "2026-01-01", "2026-03-01", 10000,
                              runner=runner, strategies=JESSE_DEFS, dataset_sha256="x")
    assert a.family == "jesse:LabBreakout" and a.code_sha256 == "abc"
    assert a.param_specs["order_mode"]["options"] == [0, 1, 2] and "position_size" in a.fixed_params
    assert set(a.supported_cost_scenarios) == {"baseline", "fee ×1,5", "fee ×2"}
    assert {u["name"] for u in a.unsupported_cost_scenarios} >= {"spread_+50%", "slippage_+1pt"}
    assert all("Bid/Ask-engine" in u["reason_nl"] for u in a.unsupported_cost_scenarios)
    r = a.fn({}, "2026-01-01", "2026-02-01")
    assert calls[-1][:2] == ("2026-01-01", "2026-01-31")  # half-open -> inclusive Jesse end date
    t = r["trades"][0]
    assert t["pnl"] == -2.0 and t["side"] == "long" and t["exit_time"].startswith("2026-01-01T11:00")
    assert r["metrics"]["max_drawdown_pct"] == 12.5 and r["metrics"]["n_trades"] == 1
    a.fn({"entry_interval": 60}, "2026-01-01", "2026-02-01")
    assert len(calls) == 1  # identical (params, period, cost) -> cache
    stressed = a.fn({}, "2026-01-01", "2026-02-01", fee_multiplier=2.0)
    assert calls[-1][3] == pytest.approx(0.0012) and stressed["trades"][0]["pnl"] == pytest.approx(-4.0)
    a.fn({"order_mode": 2}, "2026-01-01", "2026-02-01")
    assert calls[-1][4] == "futures" and a.extra_state()["account_type"] == "futures"
    assert "futures" in a.extra_state()["account_type_note"]
    assert a.hypothesis_id({"entry_interval": 60.0}) == hypothesis_id("jesse:LabBreakout",
                                                                      {"order_mode": 0, "entry_interval": 60,
                                                                       "position_size": 1.0})
    cands = a.candidates(4, 0)
    assert len(cands) == 4 and cands[0] == a.defaults
    with pytest.raises(ValueError):
        a.fn({}, "2026-01-01", "2026-02-01", spread_multiplier=1.5)


def test_validation_reruns_once_on_futures_after_spot_short_switch(tmp_path):
    calls = []

    def runner(name, symbol, timeframe, start, end, params, fee=0.0006, account_type="spot", **kw):
        calls.append(account_type)
        if params.get("order_mode") == 2 and account_type == "spot":
            raise server.JesseRunError(422, "short", "spot_short")
        days = pd.bdate_range(start, end)
        return {"result": {"trades": [_jesse_trade(d.strftime("%Y-%m-%d"), 1.0 if d.day % 3 else -1.5)
                                      for d in days], "metrics": {}}}

    a = adapters.make_adapter("jesse", "LabBreakout", "XAUUSD", "M15", "2026-01-01", "2026-07-01", 10000,
                              runner=runner, strategies=JESSE_DEFS, dataset_sha256="x")
    cfg = {"walk_forward": {"train_days": 60, "test_days": 30, "step_days": 30, "max_combos": 6, "min_trades": 5},
           "stability": {"enabled": False}, "pbo": {"max_combos": 6}, "monte_carlo": {"n": 100}}
    led = ResearchLedger(tmp_path / "l")
    r = validation_runner.run_validation(Ctx(), a, cfg, ledger=led)
    assert r.get("rerun_nl") and "futures" in r["rerun_nl"] and r["config"]["account_type"] == "futures"
    first_futures = calls.index("futures")
    assert all(c == "futures" for c in calls[first_futures:])
    assert len(led.list(kind="VALIDATION")) == 1 and len(led.list(kind="HOLDOUT")) == 1


def test_jesse_adapter_failure_message_is_dutch_and_cancel_raises():
    def failing(*_a, **_k):
        raise server.JesseRunError(500, "Geen Dukascopy-candles in de gekozen periode.", "no_candles")

    a = adapters.make_adapter("jesse", "LabBreakout", "XAUUSD", "M15", "2026-01-01", "2026-03-01", 10000,
                              runner=failing, strategies=JESSE_DEFS, dataset_sha256="x")
    with pytest.raises(RuntimeError, match="Geen Dukascopy-candles"):
        a.fn({}, "2026-01-01", "2026-02-01")
    ctx = Ctx()

    def cancelled(*_a, **_k):
        ctx.cancelled = True
        raise server.RunCancelled()

    b = adapters.make_adapter("jesse", "LabBreakout", "XAUUSD", "M15", "2026-01-01", "2026-03-01", 10000,
                              runner=cancelled, strategies=JESSE_DEFS, dataset_sha256="x", check=ctx.check)
    with pytest.raises(JobCancelled):
        b._run(b.defaults, "2026-01-01", "2026-02-01")
    with pytest.raises(ValueError, match="Onbekende Jesse-strategie"):
        adapters.make_adapter("jesse", "Nope", "XAUUSD", "M15", "2026-01-01", "2026-03-01", 10000,
                              runner=failing, strategies=JESSE_DEFS, dataset_sha256="x")


def test_plugin_capabilities_follow_declared_params_and_stress():
    from strategy_lab import plugins
    gold = plugins.load("gold_reaper")
    caps, lim = adapters.plugin_capabilities(gold)
    assert caps["walk_forward"] and caps["stability"] and caps["cost_stress_spread"] and not caps["forward"]
    assert "commission_per_lot_side" not in adapters.param_grid(adapters.plugin_param_specs(gold), {})
    example = plugins.load("voorbeeld_ma_cross")
    caps, lim = adapters.plugin_capabilities(example)
    assert not caps["cost_stress_spread"] and any("spread_multiplier" in x for x in lim)


def test_diagnostics_map_runner_failures_to_dutch():
    cases = {"ValueError: No candle rows in x.csv": "no_candles",
             "Unknown strategy parameter: foo": "unknown_parameter",
             "Parameter x outside allowed range": "out_of_range",
             "shorting cannot be True if the exchange type is spot": "spot_short",
             "Deze strategie opent ook shortposities. Kies bij Instellingen": "spot_short",
             "error during connect: Get http://...: open //./pipe/dockerDesktopLinuxEngine": "docker_not_running",
             "timed out after 900 s": "timeout"}
    for text, code in cases.items():
        found = server.diagnose_runner_failure(text)
        assert found and found[0] == code and found[1]
    assert server.diagnose_runner_failure("iets anders") is None


# ------------------------------------------------------------------ cancel / active
class BlockingProc:
    def __init__(self, *args, **kwargs):
        self.killed = threading.Event()
        self.returncode = None

    def communicate(self, timeout=None):
        if self.killed.wait(timeout or 0):
            self.returncode = -9
            return "", "killed"
        raise subprocess.TimeoutExpired("docker", timeout)

    def kill(self):
        self.killed.set()


def test_cancel_endpoint_stops_running_backtest_and_kills_container_process(monkeypatch):
    client = TestClient(server.app)
    assert client.post("/api/runs/cancel").status_code == 409
    assert client.get("/api/runs/active").json()["running"] is False
    kills = []
    monkeypatch.setattr(server.subprocess, "Popen", BlockingProc)
    monkeypatch.setattr(server.subprocess, "run", lambda args, **kw: kills.append(args) or
                        subprocess.CompletedProcess(args, 0, "", ""))
    outcome = {}

    def worker():
        try:
            server._run_runner("rid-1", "LabBreakout", "backtest")
        except server.RunCancelled:
            outcome["cancelled"] = True

    thread = threading.Thread(target=worker)
    thread.start()
    for _ in range(100):
        if client.get("/api/runs/active").json()["running"]:
            break
        time.sleep(0.02)
    active = client.get("/api/runs/active").json()
    assert active["run_id"] == "rid-1" and active["strategy"] == "LabBreakout" and active["elapsed_s"] >= 0
    response = client.post("/api/runs/cancel")
    thread.join(10)
    assert response.status_code == 200 and response.json()["stopped"] is True
    assert outcome.get("cancelled")
    assert any("/home/strategy_lab/requests/rid-1.json" in a for a in kills[-1])
    assert client.get("/api/runs/active").json()["running"] is False
    assert not server.RESEARCH_RUN_LOCK.locked()


def test_runner_timeout_and_job_cancel_stop_the_process(monkeypatch):
    monkeypatch.setattr(server.subprocess, "Popen", BlockingProc)
    monkeypatch.setattr(server.subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(args, 0, "", ""))
    with pytest.raises(subprocess.TimeoutExpired):
        server._run_runner("rid-2", "S", "backtest", timeout=0.6)
    with pytest.raises(server.RunCancelled):
        server._run_runner("rid-3", "S", "backtest", is_cancelled=lambda: True)
    assert not server.RESEARCH_RUN_LOCK.locked()


# ------------------------------------------------------------------ API
def test_research_strategies_lists_three_kinds_with_honest_capabilities():
    items = TestClient(server.app).get("/api/research/strategies").json()["strategies"]
    by = {(i["kind"], i["id"]): i for i in items}
    assert ("builder", "EmaRsiPullback") in by and by[("builder", "EmaRsiPullback")]["jesse_twin"] == "EmaRsiPullback"
    assert ("jesse", "EmaRsiPullback") not in by
    lab = by[("jesse", "LabBreakout")]
    assert lab["capabilities"]["validate"] and not lab["capabilities"]["cost_stress_spread"]
    assert lab["capabilities"]["forward"] is (lab["sha256"] == __import__(
        "strategy_lab.forward_engine", fromlist=["LAB_SHA256"]).LAB_SHA256)
    gold = by[("plugin", "gold_reaper")]
    assert gold["capabilities"]["forward"] is False and gold["limitations_nl"]


def test_validate_endpoint_accepts_legacy_spec_and_strategy_kinds(monkeypatch):
    submitted = []
    monkeypatch.setattr(research_api.JOBS, "submit", lambda *a, **k: submitted.append(a) or "job1")
    client = TestClient(server.app)
    spec = json.loads((research_api.SPEC_ROOT / "EmaRsiPullback.json").read_text(encoding="utf-8"))
    assert client.post("/api/validate", json={"spec": spec, "start": "2025-01-01", "end": "2025-06-01"}).json() == \
        {"job_id": "job1"}
    body = {"strategy": {"kind": "jesse", "name": "LabBreakout"}, "symbol": "XAUUSD", "timeframe": "M15",
            "start": "2026-01-01", "end": "2026-03-01", "walk_forward": {"enabled": True, "max_combos": 2}}
    assert client.post("/api/validate", json=body).status_code == 200
    assert research_api._strategy_ref(submitted[-1][3]) == ("jesse", "LabBreakout")
    assert client.post("/api/validate", json={**body, "strategy": {"kind": "jesse", "name": "Nope"}}).status_code == 404
    assert client.post("/api/validate", json={**body, "strategy": {"kind": "rust"}}).status_code == 422
    plugin = {"strategy": {"kind": "plugin", "name": "gold_reaper"}, "start": "2026-01-01", "end": "2026-03-01"}
    assert client.post("/api/validate", json=plugin).status_code == 200
