import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from strategy_lab.research import distribution, factory, factory_worker, protocol  # noqa: E402
from strategy_lab.research.ledger import ResearchLedger  # noqa: E402
from strategy_lab.tests import factory_fixtures as fx  # noqa: E402

LOADER = "strategy_lab.tests.factory_fixtures:synthetic_loader"


def body(tmp_path, **adv):
    a = {"max_candidates": 4, "max_trials_per_candidate": 4, "wf_top_k": 2, "monte_carlo_n": 100,
         "window_months": 3, "max_workers": 2, "loader": LOADER, "frames_dir": str(tmp_path / "frames"),
         "ledger_path": str(tmp_path / "ledger.sqlite3"), "max_minutes": 10, **fx.TEST_PROTOCOL}
    a.update(adv)
    return {"markets": ["XAUUSD"], "timeframes": ["H4"], "families": "all", "profile": "uitgebreid",
            "quality": "laag_frequent", "advanced": a}


def new_run(tmp_path, generator=factory.GeneratorShim.name, **adv):
    p = factory.plan(body(tmp_path, **adv))
    cfg = p.pop("config")
    db = tmp_path / "factory.sqlite3"
    st = factory.FactoryStore(db)
    rid = factory_worker.new_run_id()
    st.create_run(rid, cfg, p, generator)
    return db, st, rid, cfg


# ------------------------------------------------------------------------------------------------ protocol
def test_ranges_adjusted_to_real_availability_and_insufficient_support():
    full = protocol.build_ranges({"first_reliable": "2004-01-01", "last_complete_day": "2026-11-09"})
    assert full["development"]["start"] == "2004-01-31" and full["development"]["end"] == "2018-01-01"
    assert full["validation"] == {"start": "2018-01-01", "end": "2024-01-01", "days": 2191}
    assert full["sealed"]["start"] == "2024-01-01" and full["sealed"]["end"] == "2026-11-10"
    assert full["status"] == "ADJUSTED" and full["notes_nl"]
    late = protocol.build_ranges({"first_reliable": "2014-01-01", "last_complete_day": "2026-10-14"})
    assert late["development"]["start"] == "2014-01-31" and late["status"] == "ADJUSTED"
    young = protocol.build_ranges({"first_reliable": "2017-06-01", "last_complete_day": "2026-10-14"})
    assert young["status"] == "INSUFFICIENT_SUPPORT"
    no_seal = protocol.build_ranges({"first_reliable": "2004-01-01", "last_complete_day": "2024-02-01"})
    assert no_seal["sealed_status"] == "INSUFFICIENT_SUPPORT"


def test_availability_measures_without_inventing_candles():
    bid, ask, _ = fx.synthetic_frames()
    gap = (bid.index >= "2014-03-03") & (bid.index < "2014-03-15")
    bid2, ask2 = bid[~gap], ask[~gap]
    ask2 = ask2.drop(ask2.index[100:200])
    partial = bid2.iloc[:-3]  # last day incomplete
    av = protocol.data_availability(partial, ask2, "H4", today="2026-01-01")
    assert av["first_reliable"].startswith("2012-01-0")
    assert av["last_complete_day"] < partial.index[-1].strftime("%Y-%m-%d")
    assert any(g[0].startswith("2014-03") for g in av["gaps"]["multi_day_gaps"])
    assert av["missing_ask_fraction"] > 0
    assert av["rows"] == len(partial)  # nothing filled in
    assert av["spread"]["median_abs"] == pytest.approx(0.05, rel=1e-6)


def test_search_data_never_holds_sealed_bars(tmp_path):
    bid, ask, _ = fx.synthetic_frames()
    path = tmp_path / "f.parquet"
    protocol.frames_to_file(bid, ask, path)
    data = protocol.SearchData.from_file(path, "2019-07-01")
    assert data.bid.index.max() < pd.Timestamp("2019-07-01", tz="UTC")
    assert data.ask.index.max() < pd.Timestamp("2019-07-01", tz="UTC")
    with pytest.raises(protocol.SealedAccessError):
        data.slice("2019-01-01", "2019-08-01")
    with pytest.raises(protocol.SealedAccessError):
        protocol.assert_search_range("2019-07-01", "2019-07-02", "2019-07-01")
    protocol.assert_search_range("2016-01-01", "2019-07-01", "2019-07-01")  # end exclusive: allowed


# ------------------------------------------------------------------------------------------------ plan / config
def test_plan_is_bounded_and_refuses_unbounded(tmp_path):
    p = factory.plan(body(tmp_path))
    for k in ("candidates", "backtests_estimate", "trials_estimate", "est_minutes", "workers", "cpu_note_nl", "ranges",
              "warnings"):
        assert k in p
    assert p["ranges"]["XAUUSD"]["dev"]["start"]
    with pytest.raises(ValueError):
        factory.plan(body(tmp_path, max_candidates=100000))
    with pytest.raises(ValueError):
        factory.plan(body(tmp_path, max_candidates=2000, max_trials_per_candidate=500, max_backtests=1000))
    with pytest.raises(ValueError):
        factory.normalize_config({"profile": "turbo"})
    with pytest.raises(ValueError):
        factory.normalize_config({"timeframes": ["M1"]})
    snel = factory.plan({**body(tmp_path), "profile": "snel"})
    assert any("GEEN verzegelde eindtest" in w for w in snel["warnings"])
    cfg = factory.normalize_config({"min_trades_per_week": 3, "max_dd_pct": 10})
    assert cfg["quality_thresholds"]["min_trades_per_week"] == 3 and cfg["quality_thresholds"]["max_dd_pct"] == 10
    assert cfg["workers"] == factory.DEFAULT_WORKERS <= max(1, factory.CPU_COUNT - 2) and factory.MACHINE_LIMIT_PCT <= 80


def test_pareto_ranks_and_param_sampling():
    pts = [(1, 2, 3), (2, 3, 4), (0, 0, 0), (3, 1, 1)]
    ranks = factory.pareto_ranks(pts)
    assert ranks[1] == 1 and ranks[3] == 1 and ranks[0] == 2 and ranks[2] == 3
    space = {"a": {"type": "int", "min": 5, "max": 50, "step": 5, "default": 20},
             "b": {"type": "float", "min": 1.0, "max": 3.0, "step": 0.5, "default": 2.0}}
    s1, s2 = factory.sample_params(space, 10, 3), factory.sample_params(space, 10, 3)
    assert s1 == s2 and s1[0] == {"a": 20, "b": 2.0} and len({json.dumps(x, sort_keys=True) for x in s1}) == 10
    assert all(5 <= x["a"] <= 50 and x["a"] % 5 == 0 and 1.0 <= x["b"] <= 3.0 for x in s1)


def test_representative_windows_are_inside_development_only():
    bid, _, _ = fx.synthetic_frames()
    w = factory.representative_windows(bid, "2012-01-31", "2016-01-01", 4, 3)
    assert 3 <= len(w) <= 4 and len({x["year"] for x in w}) == len(w)
    for x in w:
        assert "2012-01-31" <= x["start"] < x["end"] <= "2016-01-01"


# ------------------------------------------------------------------------------------------------ distribution
def _trades(pnls, start="2020-01-06", hours=24):
    t0 = pd.Timestamp(start, tz="UTC")
    out = []
    for i, p in enumerate(pnls):
        e = t0 + pd.Timedelta(hours=hours * i)
        out.append({"entry_time": e.isoformat(), "exit_time": (e + pd.Timedelta(hours=2)).isoformat(), "pnl": float(p),
                    "r_multiple": float(p) / 50})
    return out


def test_distribution_metrics_and_concentration_warning():
    trades = _trades([10, -5, 10, -5, 10, -5, 10, -5] * 10)
    d = distribution.analyse(trades, 10000)
    assert d["n_trades"] == 80 and d["net_profit"] == pytest.approx(200)
    assert d["winning_days"] == 40 and d["losing_days"] == 40
    assert d["max_consecutive_winning_trades"] == 1 and d["max_consecutive_losing_trades"] == 1
    assert "PROFIT_CONCENTRATION" not in d["flags"]
    assert set(d["rolling_trades"]) == {"20", "50", "100"} and d["rolling_trades"]["100"]["status"] == \
        "INSUFFICIENT_EVIDENCE"
    assert d["yearly"] and d["monthly"] and d["weekly"] and d["sessions"]
    lucky = distribution.analyse(_trades([-3] * 60 + [500]), 10000)
    assert "PROFIT_CONCENTRATION" in lucky["flags"] and lucky["warnings_nl"]
    assert lucky["without_top_days"]["1"]["still_positive"] is False


def test_correlation_matrix():
    idx = pd.bdate_range("2020-01-01", periods=60)
    a = pd.Series(np.random.default_rng(1).normal(0, 1, 60), index=idx)
    m = distribution.correlation_matrix({"a": a, "b": a * 2, "c": -a})
    assert m["status"] == "OK" and m["matrix"][0][1] == pytest.approx(1.0) and m["matrix"][0][2] == pytest.approx(-1.0)
    assert len(m["high_pairs"]) == 3


# ------------------------------------------------------------------------------------------------ pipeline
def test_full_pipeline_records_every_candidate_stage_and_trial(tmp_path):
    db, st, rid, cfg = new_run(tmp_path, max_candidates=5)
    out = factory_worker.Supervisor(db, idle_exit_s=0.3).serve()
    assert out == "idle"
    run = st.run(rid)
    assert run["status"] == "done" and run["phase"] == "done", run["error"]
    cands = st.candidates(rid)
    assert len(cands) == 5
    for c in cands:
        assert c["status"] not in ("GENERATED", "IN_PROGRESS")
        stages = st.stage_results(rid, c["cand_id"])
        assert "L1" in stages
        if c["status"] in ("REJECTED", "INSUFFICIENT_EVIDENCE", "ERROR"):
            assert c["reasons"], c
    led = ResearchLedger(cfg["ledger_path"])
    rows = led.list(limit=2000)
    run_rows = [r for r in rows if ((r.get("statistics") or {}).get("factory") or {}).get("run_id") == rid]
    trials = st.trials(rid)
    assert len(run_rows) >= len(cands) + len(trials) - sum(1 for t in trials if t["stage"] == "L5")
    assert all(t["ledger_id"] for t in trials)
    c = factory.counts(st, rid)
    assert c["generated"] == 5 and c["technically_valid"] >= c["quick_passed"] >= c["dev_validated"] >= c["wf_passed"]
    for row in factory.candidate_rows(st, rid):
        for k in ("id", "name", "symbol", "timeframe", "family", "group", "stage", "status", "evidence_class", "trades",
                  "net_return_pct", "max_dd_pct", "profit_factor", "recovery_factor", "reasons_nl"):
            assert k in row
    det = factory.candidate_detail(st, cands[0]["cand_id"], rid)
    for k in ("candidate", "dev", "optimisation", "walk_forward", "robustness", "distribution", "final_test",
              "scorecard", "warnings_nl", "reasons_nl", "baseline_comparison", "ledger_ids", "trades_sample"):
        assert k in det
    assert {"spec", "rules_nl", "params", "passport", "iq_sources", "proxies_nl", "hypothesis_nl"} <= set(det["candidate"])
    proto = run["protocol"]["markets"]["XAUUSD:H4"]
    assert proto["ranges"]["sealed"]["start"] == "2019-07-01" and proto["dataset_sha256"]
    assert protocol.file_sha256(proto["frames_path"]) == proto["dataset_sha256"]


def test_l6_to_l8_freeze_final_test_and_multiple_testing(tmp_path):
    db, st, rid, cfg = new_run(tmp_path, max_candidates=3)
    factory_worker.prepare_run(st, st.run(rid))
    led = ResearchLedger(cfg["ledger_path"])
    factory_worker.generate_candidates(st, st.run(rid), led)
    vault = protocol.SealedVault(db)
    cands = st.candidates(rid)
    fams = set()
    for c in cands:
        params = {x["name"]: x["default"] for x in c["candidate"]["spec"]["params"]}
        st.put_stage(rid, c["cand_id"], "L5", "PASS", 0, [], {"dsr": 0.99, "profit_factor": 1.5, "recovery_factor": 3,
                                                              "verdict": "FORWARD_ELIGIBLE"},
                     {"params": params, "daily_returns": {}}, [])
        st.set_candidate(rid, c["cand_id"], status="WF_PASSED", stage="L5")
        fams.add(c["family"])
    sel = factory.select_and_freeze(st, st.run(rid), vault, led)
    frozen = [s for s in sel if s["selected"]]
    assert 1 <= len(frozen) <= len(fams)
    for s in frozen:
        r = factory.final_test_candidate(st, st.run(rid), s["cand_id"], vault, led)
        assert r["status"] == "PASS" and r["payload"]["evidence_class"] == "HISTORICAL_OOS"
        again = factory.final_test_candidate(st, st.run(rid), s["cand_id"], vault, led)
        assert again["created_at"] == r["created_at"]  # idempotent, not re-run
    mt = factory.finalize_run(st, st.run(rid), vault, led)
    assert mt["n_final_tested"] == len(frozen) and mt["dsr_n_trials"] >= len(frozen)
    for s in frozen:
        per = mt["per_candidate"][s["frozen_id"]]
        assert per["p_holm"] >= per["p_raw"] and per["p_bh"] >= per["p_raw"]
        c = st.candidate(rid, s["cand_id"])
        assert c["status"] in ("FINAL_FAILED", "OOS_APPROVED", "FORWARD_ELIGIBLE")
    actions = [a["action"] for a in vault.audit_log()]
    assert actions.count("ACCESS_DONE") == len(frozen) and actions.count("ACCESS_START") == len(frozen)
    holdout_rows = [r for r in led.list(kind="HOLDOUT", limit=100)]
    assert len(holdout_rows) == len(frozen)


# ------------------------------------------------------------------------------------------------ API
def test_api_contract(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from strategy_lab import factory_api
    db = tmp_path / "factory.sqlite3"
    monkeypatch.setattr(factory_api, "DB_PATH", db)
    monkeypatch.setattr(factory_worker, "ensure_supervisor", lambda *a, **k: {"alive": False, "test": True})
    app = FastAPI()
    app.include_router(factory_api.router)
    client = TestClient(app)
    cat = client.get("/api/factory/catalog").json()
    assert {"families", "profiles", "quality_profiles", "markets", "timeframes"} <= set(cat)
    assert set(cat["profiles"]) == {"snel", "uitgebreid", "diepgaand"}
    assert {"regelmatig", "zeer_actief", "laag_frequent", "custom"} <= set(cat["quality_profiles"])
    b = body(tmp_path, max_candidates=3)
    plan = client.post("/api/factory/plan", json=b).json()
    assert {"candidates", "backtests_estimate", "trials_estimate", "est_minutes", "workers", "ranges"} <= set(plan)
    assert client.post("/api/factory/plan", json={**b, "profile": "x"}).status_code == 422
    rid = client.post("/api/factory/runs", json=b).json()["run_id"]
    assert client.get(f"/api/factory/runs/{rid}").json()["status"] == "queued"
    assert client.post(f"/api/factory/runs/{rid}/pause").json()["status"] == "queued"
    assert factory.FactoryStore(db).control(rid) == "pause"
    client.post(f"/api/factory/runs/{rid}/resume")
    factory_worker.Supervisor(db, idle_exit_s=0.3).serve()
    view = client.get(f"/api/factory/runs/{rid}").json()
    for k in ("status", "phase", "phase_nl", "progress", "counts", "resources", "families", "log_tail", "config",
              "protocol", "multiple_testing"):
        assert k in view
    assert view["status"] == "done" and set(factory.COUNT_KEYS) <= set(view["counts"])
    rows = client.get(f"/api/factory/runs/{rid}/candidates").json()
    assert len(rows) == 3
    det = client.get(f"/api/factory/candidates/{rows[0]['id']}").json()
    assert det["id"] == rows[0]["id"]
    r = client.post(f"/api/factory/candidates/{rows[0]['id']}/forward", json={})
    assert r.status_code == 409 and "FORWARD_ELIGIBLE" in r.json()["detail"]
    assert client.get("/api/factory/candidates/nope").status_code == 404
    assert "audit" in client.get("/api/factory/audit").json()
    assert client.post(f"/api/factory/runs/{rid}/stop").status_code == 409  # finished runs cannot be stopped
    assert any(x["run_id"] == rid for x in client.get("/api/factory/runs").json()["runs"])


def test_snel_profile_spreads_families_across_groups():
    from strategy_lab.research import factory
    from strategy_lab.research.generator import families_catalog
    group = {f["id"]: f["group"] for f in families_catalog()}
    cfg = factory.normalize_config({"markets": ["XAUUSD"], "timeframes": ["M15"], "families": "all", "profile": "snel"}) \
        if hasattr(factory, "normalize_config") else None
    if cfg is None:
        import pytest; pytest.skip("resolve_config not exposed")
    assert len({group[f] for f in cfg["families"]}) == len(cfg["families"])
