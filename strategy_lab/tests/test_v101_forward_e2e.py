"""End-to-end pipeline in an isolated temporary vault.

TECHNICAL PROOF ONLY. Every price below is synthetic (a seeded sawtooth with a deliberately built-in positive edge).
Nothing here is evidence that any strategy works on real markets, and the "sealed" period is a fixture period inside
this temporary vault: the real sealed 2024+ period is never read.

Chain: develop -> backtest -> optimise -> walk-forward -> robustness -> statistical validation -> freeze ->
sealed final test (after freeze) -> reproducible rerun -> forward registration -> day-by-day processing ->
restart without duplicates / append-only history -> new version = new registration -> crash recovery.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from runtime.strategy_lab import rule_engine
from strategy_lab import forward_rules
from strategy_lab.daily_data import DailyPartitionStore
from strategy_lab.data_manager import DataError
from strategy_lab.forward_engine import ForwardEngine
from strategy_lab.forward_performance import summarize_forward
from strategy_lab.forward_registry import ForwardRegistry
from strategy_lab.research import nested_wf, protocol, robustness, validation
from strategy_lab.research.protocol import SealedAccessError, SearchData, SealedVault
from strategy_lab.tests.test_forward_rules import CONTRACT, ingest

LABEL = "TECHNICAL_PROOF_ONLY_SYNTHETIC_DATA"
START = "2026-01-01"
SEALED_START, SEALED_END = "2026-05-01", "2026-06-01"     # fixture sealed period inside the temporary vault
FORWARD_FIRST = datetime(2026, 6, 2, tzinfo=timezone.utc)
FROZEN_AT = datetime(2026, 6, 2, 6, tzinfo=timezone.utc)
FORWARD_DAYS = 5
TOTAL_DAYS = 151 + 1 + FORWARD_DAYS                        # through the last forward day
CAPITAL = 10_000.0
SPEC = {"schema": rule_engine.SCHEMA, "name": "SawBreakoutFixture", "symbol": "XAUUSD", "timeframe": "M15",
        "direction": "long",
        "entry": {"long": {"left": {"price": "close"}, "op": ">", "right": {"value": {"param": "level"}}}},
        "stop": {"type": "points", "value": 1.0}, "target": {"type": "r_multiple", "value": 2.0},
        "risk": {"percent": 1.0}, "max_trades_per_day": 1,
        "params": [{"name": "level", "type": "float", "min": 100.2, "max": 100.8, "step": 0.1, "default": 100.5}]}
GRID = [{"variant": "base", "params": {"level": round(100.2 + 0.1 * i, 1)}} for i in range(7)]


def synthetic_m1(days, seed=7, p_fail=0.3):
    """One rising sawtooth per UTC day; 30% of days fail (reverse after touching the trigger): edge by construction."""
    rng = np.random.default_rng(seed)
    index = pd.date_range(START, periods=days * 1440, freq="min", tz="UTC")
    close = np.empty(len(index))
    minute = np.arange(1440)
    for day in range(days):
        base = 99.0 + 0.004 * minute
        if rng.random() < p_fail:
            base = np.where(minute < 400, base, 100.6 - 0.006 * (minute - 400))
        close[day * 1440:(day + 1) * 1440] = base + rng.normal(0, 0.10, 1440)
    open_ = np.concatenate([[close[0]], close[:-1]])
    span = np.abs(rng.normal(0, 0.03, len(index)))
    bid = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + span,
                        "low": np.minimum(open_, close) - span, "close": close, "volume": 1.0}, index=index)
    ask = bid.copy()
    ask[["open", "high", "low", "close"]] += 0.05
    return bid, ask


def bars(frame, rule="15min"):
    return frame.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def row_hashes(path):
    """sha256 of every ledger row, keyed by table and primary key: the append-only check."""
    out = {}
    with closing(sqlite3.connect(path)) as db:
        for table, key in (("forward_events", "id"), ("forward_days", "day"), ("forward_ledger", "day"),
                           ("forward_flags", "candle_at||symbol||kind")):
            for row in db.execute(f"SELECT {key}, * FROM {table}"):
                out[(table, str(row[0]))] = hashlib.sha256(repr(row).encode()).hexdigest()
    return out


def registration_for(store, level=100.5, *, extra=None):
    warm = forward_rules.warmup_from_fetch(forward_rules.store_fetch(store, "XAUUSD"), SPEC, {"level": level},
                                           as_of=FROZEN_AT)
    config = forward_rules.build_configuration(SPEC, {"level": level}, contract=CONTRACT, starting_capital=CAPITAL,
                                               trade_risk=0.01, portfolio_risk_limit=0.05, leverage=100,
                                               warmup=warm, data_version={"label": LABEL, **(extra or {})})
    config["pre_registration_returns"] = (extra or {}).get("returns", {})
    return config


def forward_run(path, m1_bid, m1_ask, config, *, days=FORWARD_DAYS, restart=False, crash=None, snapshots=None):
    """Fresh database: warm-up partitions -> freeze -> enrol -> publish and advance one UTC day at a time."""
    store = DailyPartitionStore(path)
    for offset in (-2, -1):                                                      # published before registration
        day = (FORWARD_FIRST + timedelta(days=offset)).date()
        window = m1_bid.loc[str(day)]
        ingest(store, "XAUUSD", day, window, spread=0.05, observed_at=datetime.combine(
            day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
    registry = ForwardRegistry(store)
    engine = ForwardEngine(registry)
    portfolio = engine.create("E2E fixture", currency="USD", starting_capital=CAPITAL, risk_limit=0.05)
    registration = forward_rules.freeze(registry, config, frozen_at=FROZEN_AT)
    engine.enroll(portfolio, registration["id"], joined_at=FROZEN_AT)
    for offset in range(days):
        day = (FORWARD_FIRST + timedelta(days=offset)).date()
        ingest(store, "XAUUSD", day, m1_bid.loc[str(day)], spread=0.05,
               observed_at=datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
        if snapshots is not None:
            snapshots.append(row_hashes(path))
        if restart:
            engine = ForwardEngine(ForwardRegistry(DailyPartitionStore(path)))     # new process every day
        engine.advance(portfolio, as_of=datetime.combine(day + timedelta(days=1), datetime.min.time(),
                                                         tzinfo=timezone.utc))
    return SimpleNamespace(store=store, engine=engine, portfolio=portfolio, registration=registration, path=path)


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    root = tmp_path_factory.mktemp("v101_e2e")
    stages = []
    bid, ask = synthetic_m1(TOTAL_DAYS)
    b15, a15 = bars(bid[bid.index < SEALED_END]), bars(ask[ask.index < SEALED_END])
    frames = root / "vault" / "frames.parquet"
    dataset_sha = protocol.frames_to_file(b15, a15, frames)
    search = SearchData.from_file(frames, SEALED_START, symbol="XAUUSD", timeframe="M15", dataset_sha256=dataset_sha)
    out = SimpleNamespace(root=root, stages=stages, bid=bid, ask=ask, search=search, dataset_sha=dataset_sha,
                          frames=frames)

    def stage(name, **detail):
        stages.append({"stage": name, "label": LABEL, **detail})

    stage("develop", spec=SPEC["name"], rows_m15=len(b15), first_sealed_bar_visible=bool(
        (search.bid.index >= protocol.utc(SEALED_START)).any()))

    dev_start, dev_end, val_end = "2026-01-05", "2026-03-15", "2026-04-30"
    dev = search.backtest(SPEC, {"level": 100.5}, dev_start, dev_end, capital=CAPITAL)
    dev_metrics = validation.trade_metrics(dev["trades"], CAPITAL)
    stage("backtest", trades=len(dev["trades"]), net_profit=dev_metrics["net_profit"], sharpe=dev_metrics["sharpe"])

    scores = {}
    for config in GRID:
        result = search.backtest(SPEC, config["params"], dev_start, dev_end, capital=CAPITAL)
        scores[config["params"]["level"]] = validation.trade_metrics(result["trades"], CAPITAL)["sharpe"]
    chosen = max(scores, key=scores.get)
    stage("optimise", n_trials=len(GRID), chosen_level=chosen, scores=scores)
    params = {"level": chosen}

    def fn(config, start, end):
        return search.backtest(SPEC, config["params"], start, end, capital=CAPITAL)

    wf = nested_wf.nested_walk_forward(fn, GRID, dev_start, val_end, train_days=30, test_days=15, mode="rolling",
                                       objective="sharpe", min_trades=3, capital=CAPITAL)
    oos_trades = wf["oos_trades"]
    stage("walk_forward", windows=wf["n_windows"], windows_ok=wf["n_windows_ok"], oos_trades=len(oos_trades),
          oos_net_profit=wf["oos_net_profit"])

    dev_all = search.backtest(SPEC, params, dev_start, val_end, capital=CAPITAL)
    daily = validation.daily_returns_from_trades(dev_all["trades"], CAPITAL)
    cis = robustness.bootstrap_cis(daily.to_numpy(), [t["pnl"] for t in dev_all["trades"]], n=300, seed=1)
    neighbourhood = robustness.parameter_neighbourhood(
        lambda p, s, e: search.backtest(SPEC, p, s, e, capital=CAPITAL), params,
        {"level": {"type": "float", "min": 100.2, "max": 100.8, "step": 0.1}}, dev_start, val_end, CAPITAL)
    stage("robustness", bootstrap=sorted(cis), neighbourhood_status=neighbourhood["status"],
          neighbourhood_class=neighbourhood["classification"], n_neighbours=neighbourhood["n_neighbours"])

    psr = validation.probabilistic_sharpe_ratio(daily)
    dsr = validation.deflated_sharpe_ratio(daily, len(GRID), float(np.var(list(scores.values()))) / 252 or 1e-6)
    stage("statistical_validation", psr=psr["value"], dsr=dsr["value"], n_trials=len(GRID), n_days=psr["n"])

    record = {"spec": SPEC, "params": params, "cost_model": {"capital": CAPITAL, "commission_per_lot": 0.0,
                                                           "slippage_points": 0.0},
              "risk_model": {"percent": 1.0}, "selection_criteria": {"objective": "sharpe", "grid": len(GRID)},
              "code_sha256": hashlib.sha256(Path(rule_engine.__file__).read_bytes()).hexdigest(),
              "dataset_sha256": dataset_sha, "research_version": "v101-e2e-fixture", "seed": 7, "symbol": "XAUUSD",
              "timeframe": "M15", "frames_path": str(frames), "sealed_start": SEALED_START,
              "sealed_end": SEALED_END, "signature": "sig-saw-breakout-fixture", "family": "saw_fixture",
              "run_id": "e2e", "candidate_id": "e2e-1"}
    vault = SealedVault(root / "vault" / "sealed.sqlite")
    frozen_id = vault.freeze(record)
    stage("freeze", frozen_id=frozen_id, record_sha256=protocol.sha(record), params=params)
    frozen_at = vault.frozen(frozen_id)["created_at"]

    sealed = vault.final_test(frozen_id, who="e2e-test")
    stage("sealed_final_test", evidence_class=sealed["evidence_class"], trades=sealed["metrics"]["trades"],
          net_profit=sealed["metrics"]["net_profit"], p_value=sealed["bootstrap"].get("p_value"),
          period=sealed["sealed"], after_freeze=vault.audit_log()[-1]["at"] >= frozen_at)

    rerun_vault = SealedVault(root / "vault2" / "sealed.sqlite")
    rerun_id = rerun_vault.freeze(record)
    rerun = rerun_vault.final_test(rerun_id, who="e2e-rerun")
    stage("reproducible_rerun", same_frozen_id=rerun_id == frozen_id,
          same_trades=rerun["trades"] == sealed["trades"], same_metrics=rerun["metrics"] == sealed["metrics"])

    returns = {"HISTORICAL_BACKTEST": {d.date().isoformat(): float(v) for d, v in daily.items()},
               "HISTORICAL_OOS": {d.date().isoformat(): float(v) for d, v in
                                  validation.daily_returns_from_trades(sealed["trades"], CAPITAL).items()}}
    extra = {"vault_frozen_id": frozen_id, "record_sha256": protocol.sha(record), "dataset_sha256": dataset_sha,
             "sealed_evidence_class": sealed["evidence_class"], "returns": returns}
    bootstrap_store = DailyPartitionStore(root / "bootstrap.sqlite")
    for offset in (-2, -1):
        day = (FORWARD_FIRST + timedelta(days=offset)).date()
        ingest(bootstrap_store, "XAUUSD", day, bid.loc[str(day)], spread=0.05, observed_at=datetime.combine(
            day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
    out.config = registration_for(bootstrap_store, params["level"], extra=extra)
    out.config_v2 = registration_for(bootstrap_store, round(params["level"] - 0.1 if params["level"] > 100.5 else params["level"] + 0.1, 1),
                                  extra=extra)
    out.record, out.frozen_id, out.vault, out.sealed, out.rerun = record, frozen_id, vault, sealed, rerun
    out.rerun_vault, out.params, out.wf, out.extra = rerun_vault, params, wf, extra

    snapshots = []
    out.main = forward_run(root / "forward.sqlite", bid, ask, out.config, restart=True, snapshots=snapshots)
    out.snapshots = snapshots
    report = out.main.engine.report(out.main.portfolio)
    perf = summarize_forward(report, model_current=True, data=out.main.store)
    stage("forward_registration", registration_id=out.main.registration["id"],
          snapshot_sha256=out.main.registration["snapshot_sha256"], frozen_at=out.main.registration["frozen_at"])
    stage("forward_day_by_day", days=len(report["days"]), events=len(report["events"]),
          trades=perf["portfolio"]["trade_count"], net_profit=perf["portfolio"]["net_profit"],
          evidence_class=report["mode"], ledger=out.main.engine.verify_ledger(out.main.portfolio)["status"])
    if os.environ.get("V101_WRITE_EVIDENCE"):
        target = Path(__file__).resolve().parents[2] / "evidence" / "v101" / "forward" / "e2e_pipeline_report.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"label": LABEL, "note": "Synthetic fixture; proves mechanics, not edge.",
                                      "stages": stages}, indent=1, default=str), encoding="utf-8")
    return out


def stage_of(pipeline, name):
    return next(row for row in pipeline.stages if row["stage"] == name)


def test_stages_run_in_the_documented_order_and_are_labelled_synthetic(pipeline):
    assert [row["stage"] for row in pipeline.stages] == [
        "develop", "backtest", "optimise", "walk_forward", "robustness", "statistical_validation", "freeze",
        "sealed_final_test", "reproducible_rerun", "forward_registration", "forward_day_by_day"]
    assert all(row["label"] == LABEL for row in pipeline.stages)
    assert stage_of(pipeline, "backtest")["trades"] > 30 and stage_of(pipeline, "backtest")["net_profit"] > 0
    assert stage_of(pipeline, "optimise")["n_trials"] == 7
    walk = stage_of(pipeline, "walk_forward")
    assert walk["windows"] >= 3 and walk["oos_trades"] > 20 and walk["oos_net_profit"] > 0
    stats = stage_of(pipeline, "statistical_validation")
    assert 0.9 < stats["psr"] <= 1 and stats["n_days"] > 30       # the built-in edge is recoverable, by construction
    assert stage_of(pipeline, "robustness")["bootstrap"]


def test_search_stages_never_see_the_fixture_sealed_period(pipeline):
    assert stage_of(pipeline, "develop")["first_sealed_bar_visible"] is False
    assert pipeline.search.bid.index.max() < protocol.utc(SEALED_START)
    with pytest.raises(SealedAccessError):
        pipeline.search.backtest(SPEC, pipeline.params, "2026-04-01", "2026-05-15", capital=CAPITAL)


def test_freeze_is_immutable_and_bound_to_code_params_and_data_hashes(pipeline):
    frozen = pipeline.vault.frozen(pipeline.frozen_id)["record"]
    assert frozen["params"] == pipeline.params and frozen["dataset_sha256"] == pipeline.dataset_sha
    assert frozen["code_sha256"] == hashlib.sha256(Path(rule_engine.__file__).read_bytes()).hexdigest()
    assert protocol.sha(frozen) == protocol.sha(pipeline.record) == stage_of(pipeline, "freeze")["record_sha256"]
    assert pipeline.vault.freeze(pipeline.record) == pipeline.frozen_id          # same content, same identity
    changed = {**pipeline.record, "params": {"level": 100.2}}
    assert pipeline.vault.freeze(changed) != pipeline.frozen_id                  # a changed parameter is a new freeze
    with closing(sqlite3.connect(pipeline.vault.path)) as db:
        for sql in ("UPDATE frozen SET record_json='{}'", "DELETE FROM frozen"):
            with pytest.raises(sqlite3.DatabaseError, match="immutable"):
                db.execute(sql)


def test_sealed_final_test_ran_once_after_freeze_inside_the_fixture_period_only(pipeline):
    sealed = stage_of(pipeline, "sealed_final_test")
    assert sealed["evidence_class"] == "HISTORICAL_OOS" and sealed["after_freeze"]
    assert sealed["period"] == [SEALED_START, SEALED_END] and sealed["trades"] > 10
    first, last = (pipeline.sealed["trades"][i]["entry_time"] for i in (0, -1))
    assert pd.Timestamp(first) >= protocol.utc(SEALED_START) and pd.Timestamp(last) < protocol.utc(SEALED_END)
    log = [(row["action"], row["frozen_id"]) for row in reversed(pipeline.vault.audit_log())]
    assert log == [("ACCESS_START", pipeline.frozen_id), ("ACCESS_DONE", pipeline.frozen_id)]
    repeat = pipeline.vault.final_test(pipeline.frozen_id, who="e2e-test")
    assert repeat["repeat_read"] and repeat["metrics"] == pipeline.sealed["metrics"]      # nothing re-run
    assert pipeline.vault.audit_log()[0]["action"] == "REPEAT_READ"
    with pytest.raises(PermissionError, match="niet bevroren"):                    # never before / without a freeze
        pipeline.vault.final_test("frz_unfrozen_candidate")
    assert pipeline.vault.audit_log()[0]["action"] == "REFUSED"
    assert len(pipeline.vault.results()) == 1


def test_sealed_result_is_reproducible_in_a_second_isolated_vault(pipeline):
    rerun = stage_of(pipeline, "reproducible_rerun")
    assert rerun == {**rerun, "same_frozen_id": True, "same_trades": True, "same_metrics": True}
    assert pipeline.rerun["bootstrap"] == pipeline.sealed["bootstrap"]
    assert pipeline.rerun_vault.path != pipeline.vault.path


def test_forward_registration_is_bound_to_the_frozen_candidate(pipeline):
    config = pipeline.main.registration["configuration"]
    assert config["parameters"] == pipeline.params                       # same parameters as the vault freeze
    assert config["spec_sha256"] == rule_engine.spec_sha256(pipeline.record["spec"])
    assert config["data_version"]["vault_frozen_id"] == pipeline.frozen_id
    assert config["data_version"]["record_sha256"] == protocol.sha(pipeline.record)
    assert pipeline.vault.result(pipeline.frozen_id) is not None      # registered only after the sealed test existed
    assert pipeline.main.registration["mode"] == "DAILY_DELAYED_FORWARD"
    assert set(pipeline.main.registration["sources"]) == {forward_rules.ENGINE_SOURCE, forward_rules.RULES_SOURCE}


def test_new_candles_are_processed_day_by_day_with_virtual_orders_and_stored_performance(pipeline):
    engine, portfolio = pipeline.main.engine, pipeline.main.portfolio
    report = engine.report(portfolio)
    assert [d["day"] for d in report["days"]] == [
        (FORWARD_FIRST + timedelta(days=i)).date().isoformat() for i in range(FORWARD_DAYS)]
    assert all(d["publications"]["XAUUSD"]["sha256"] for d in report["days"])      # data snapshot hash per day
    assert min(e["candle_at"] for e in report["events"]) >= FROZEN_AT.isoformat(timespec="microseconds")
    submitted = [e for e in report["events"] if e["kind"] == "ORDER_SUBMITTED"]
    filled = [e for e in report["events"] if e["kind"] == "ORDER_FILLED"]
    closed = [e for e in report["events"] if e["kind"] == "POSITION_CLOSED"]
    assert len(submitted) >= 3 and len(filled) == len(submitted) - len(
        [e for e in report["events"] if e["kind"] == "ORDER_REJECTED"])
    assert {e["detail"]["position_id"] for e in filled} >= {e["detail"]["position_id"] for e in closed}
    assert all(e["detail"]["quote"] in {"ASK", "BID"} for e in filled)
    perf = summarize_forward(report, model_current=True, data=pipeline.main.store)["portfolio"]
    assert perf["trade_count"] == len(closed) and perf["sample_days"] == FORWARD_DAYS
    assert report["days"][-1]["equity"] == pytest.approx(perf["equity"])
    assert report["portfolio"]["balance"] == pytest.approx(CAPITAL + sum(e["detail"]["net_pnl"] for e in closed))
    assert report["evidence_classes"]["REALTIME_PAPER"]["implemented"] is False
    assert engine.verify_ledger(portfolio) == {**engine.verify_ledger(portfolio), "status": "OK",
                                               "sealed_days": FORWARD_DAYS}


def test_restart_creates_no_duplicates_and_never_rewrites_earlier_records(pipeline):
    engine = ForwardEngine(ForwardRegistry(DailyPartitionStore(pipeline.main.path)))
    before = row_hashes(pipeline.main.path)
    assert engine.advance(pipeline.main.portfolio, as_of=FORWARD_FIRST + timedelta(days=FORWARD_DAYS))[
        "committed_days"] == []
    assert row_hashes(pipeline.main.path) == before                              # restart is a no-op
    snapshots = pipeline.snapshots + [before]
    for earlier, later in zip(snapshots, snapshots[1:]):                          # append-only after every new day
        assert all(later.get(key) == digest for key, digest in earlier.items())
        assert len(later) > len(earlier)
    ids = [e["detail"]["position_id"] for e in engine.report(pipeline.main.portfolio)["events"]
           if e["kind"] == "ORDER_FILLED"]
    assert len(ids) == len(set(ids))


def test_a_changed_strategy_version_is_a_new_registration_and_leaves_the_old_one_untouched(pipeline, tmp_path):
    other = forward_run(tmp_path / "v2.sqlite", pipeline.bid, pipeline.ask, pipeline.config_v2, days=2)
    old, new = pipeline.main.registration, other.registration
    assert new["configuration"]["parameters"] != old["configuration"]["parameters"]
    assert new["configuration"]["spec_sha256"] == old["configuration"]["spec_sha256"]   # same code, new parameters
    assert new["snapshot_sha256"] != old["snapshot_sha256"] and new["id"] != old["id"]
    registry = pipeline.main.engine.registry
    assert registry.get(old["id"])["snapshot_sha256"] == old["snapshot_sha256"]          # old registration unchanged
    v2_here = forward_rules.freeze(registry, pipeline.config_v2, frozen_at=FROZEN_AT)       # same database
    assert v2_here["id"] not in {old["id"], new["id"]} and v2_here["snapshot_sha256"] == new["snapshot_sha256"]
    with pytest.raises(DataError, match="committed portfolio day"):          # history cannot be re-opened
        pipeline.main.engine.enroll(pipeline.main.portfolio, v2_here["id"], joined_at=FROZEN_AT)
    with pytest.raises(DataError):                                           # nor can one registration join twice
        pipeline.main.engine.enroll(pipeline.main.portfolio, old["id"], joined_at=FROZEN_AT + timedelta(days=30))
    assert len(pipeline.main.engine.report(pipeline.main.portfolio)["members"]) == 1
    assert other.engine.report(other.portfolio)["members"][0]["registration_id"] == new["id"]


def test_a_crash_in_the_middle_of_processing_recovers_to_the_clean_forward_run(pipeline, tmp_path, monkeypatch):
    real, calls = forward_rules.step, {"n": 0}

    def crashing(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2000:
            raise RuntimeError("simulated process crash")
        return real(*args, **kwargs)

    monkeypatch.setattr(forward_rules, "step", crashing)
    with pytest.raises(RuntimeError, match="simulated process crash"):
        forward_run(tmp_path / "crash.sqlite", pipeline.bid, pipeline.ask, pipeline.config, days=3)
    assert calls["n"] == 2000
    monkeypatch.setattr(forward_rules, "step", real)
    path = tmp_path / "crash.sqlite"
    engine = ForwardEngine(ForwardRegistry(DailyPartitionStore(path)))
    portfolio = engine.portfolios()[0]["id"]
    committed = engine.report(portfolio)
    assert len(committed["days"]) == 1                          # day 1 (1440 candles) committed, day 2 rolled back
    assert engine.verify_ledger(portfolio)["status"] == "OK"
    store = engine.registry.data
    for offset in (1, 2):
        day = (FORWARD_FIRST + timedelta(days=offset)).date()
        ingest(store, "XAUUSD", day, pipeline.bid.loc[str(day)], spread=0.05, observed_at=datetime.combine(
            day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
    engine.advance(portfolio, as_of=FORWARD_FIRST + timedelta(days=3))
    clean = forward_run(tmp_path / "clean.sqlite", pipeline.bid, pipeline.ask, pipeline.config, days=3)
    strip = lambda r: [(e["kind"], e["day"], e["candle_at"], e["detail"]) for e in r["events"]]
    recovered = engine.report(portfolio)
    assert strip(recovered) == strip(clean.engine.report(clean.portfolio))
    assert recovered["events"][:len(committed["events"])] == committed["events"]
    assert recovered["portfolio"]["balance"] == clean.engine.report(clean.portfolio)["portfolio"]["balance"]
    assert engine.verify_ledger(portfolio)["status"] == "OK"
