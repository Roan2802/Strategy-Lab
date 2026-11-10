"""V10 factory integration: pre-registration, promotion ladder, final multiplicity gate, sealed isolation.
All data are synthetic FIXTURES: they test code paths, they are not evidence of strategy performance."""
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from strategy_lab.research import factory, factory_worker, protocol  # noqa: E402
from strategy_lab.research.ledger import ResearchLedger  # noqa: E402
from strategy_lab.tests import factory_fixtures as fx  # noqa: E402

LOADER = "strategy_lab.tests.factory_fixtures:synthetic_loader"


def body(tmp_path, **adv):
    a = {"max_candidates": 3, "max_trials_per_candidate": 4, "wf_top_k": 2, "monte_carlo_n": 100,
         "window_months": 3, "max_workers": 1, "loader": LOADER, "frames_dir": str(tmp_path / "frames"),
         "ledger_path": str(tmp_path / "ledger.sqlite3"), "max_minutes": 10, **fx.TEST_PROTOCOL}
    a.update(adv)
    return {"markets": ["XAUUSD"], "timeframes": ["H4"], "families": "all", "profile": "uitgebreid",
            "quality": "laag_frequent", "advanced": a}


def new_run(tmp_path, **adv):
    p = factory.plan(body(tmp_path, **adv))
    cfg = p.pop("config")
    db = tmp_path / "factory.sqlite3"
    st = factory.FactoryStore(db)
    rid = factory_worker.new_run_id()
    st.create_run(rid, cfg, p, factory.GeneratorShim.name)
    return db, st, rid, cfg


def test_preregistration_hash_freezes_protocol_and_changes_are_new_hypotheses(tmp_path):
    db, st, rid, cfg = new_run(tmp_path)
    reg = cfg["preregistration"]
    assert reg["sha256"] == protocol.sha(protocol.research_protocol(cfg))
    for k in ("budget", "search_space", "objectives", "quality", "costs", "walk_forward", "corrections", "final_test"):
        assert k in reg["protocol"]
    protocol.verify_preregistration(cfg)
    changed = {**cfg, "markets": ["XAUUSD", "US30"]}
    with pytest.raises(PermissionError):
        protocol.verify_preregistration(changed)
    # edited before start -> re-frozen at prepare, recorded as a new registration
    st.update_run(rid, config={**cfg, "quality_thresholds": {**cfg["quality_thresholds"], "min_pf_oos": 1.4}})
    factory_worker.prepare_run(st, st.run(rid))
    run = st.run(rid)
    assert run["config"]["preregistration"]["superseded_before_start"] == reg["sha256"]
    led = ResearchLedger(cfg["ledger_path"])
    regs = led.preregistrations()
    assert regs[-1]["sha256"] == run["config"]["preregistration"]["sha256"]
    purposes = {u["purpose"] for u in led.data_usage()}
    assert {"SEARCH", "VALIDATION_SELECTION", "SELECTION_EXPERIMENT"} <= purposes
    with pytest.raises(sqlite3.DatabaseError):
        with sqlite3.connect(cfg["ledger_path"]) as c:
            c.execute("DELETE FROM preregistrations")
    # a second run with the same protocol is the same hypothesis
    assert led.preregister(regs[-1]["sha256"], regs[-1]["protocol"])["new"] is False


def test_promotion_ladder_maps_legacy_statuses():
    m = factory.promotion_status
    assert m("QUICK_PASSED", "L2")["promotion_status"] == "EXPERIMENT"
    assert m("DEV_VALIDATED", "L4")["promotion_status"] == "DEVELOPMENT_PASS"
    assert m("WF_PASSED", "L5")["promotion_status"] == "ROBUSTNESS_PASS"
    assert m("FROZEN", "L6")["promotion_status"] == "FROZEN_CANDIDATE"
    assert m("OOS_APPROVED", "L8")["promotion_status"] == "HISTORICAL_OOS_PASS"
    assert m("FORWARD_ELIGIBLE", "L8")["promotion_status"] == "FORWARD_ELIGIBLE"
    assert m("REUSED_HOLDOUT", "L7") == {"promotion_status": "INSUFFICIENT_EVIDENCE",
                                         "highest_promotion": "FROZEN_CANDIDATE"}
    r = m("REJECTED", "L5", {"metrics": {"walk_forward_pass": True}})
    assert r == {"promotion_status": "REJECTED", "highest_promotion": "WALK_FORWARD_PASS"}
    assert m("REJECTED", "L5")["highest_promotion"] == "DEVELOPMENT_PASS"
    assert m("FINAL_FAILED", "L8")["highest_promotion"] == "FROZEN_CANDIDATE"
    assert set(factory.PROMOTION_STATUSES) >= {"PROSPECTIVE_FORWARD", "INSUFFICIENT_EVIDENCE"}


def _strong_daily(seed, mean):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2016-01-04", "2019-06-28")
    return {d.strftime("%Y-%m-%d"): float(v) for d, v in zip(idx, rng.normal(mean, 0.01, len(idx)))}


def test_l6_final_global_dsr_gate_freeze_final_and_forward_paths(tmp_path):
    """FIXTURE: L5 results are injected (labelled synthetic), the L6-L8 code paths run for real."""
    db, st, rid, cfg = new_run(tmp_path)
    factory_worker.prepare_run(st, st.run(rid))
    led = ResearchLedger(cfg["ledger_path"])
    factory_worker.generate_candidates(st, st.run(rid), led)
    vault = protocol.SealedVault(db)
    cands = st.candidates(rid)
    assert len(cands) >= 2
    strong, weak = cands[0], cands[1]
    for c, mean in ((strong, 0.004), (weak, 0.0002)):
        params = {x["name"]: x["default"] for x in c["candidate"]["spec"]["params"]}
        st.put_stage(rid, c["cand_id"], "L5", "PASS", 0, [], {"dsr": 0.99, "profit_factor": 1.5, "recovery_factor": 3,
                                                              "verdict": "FORWARD_ELIGIBLE", "robustness_pass": True},
                     {"params": params, "daily_returns": _strong_daily(len(c["cand_id"]), mean), "fixture": True}, [])
        st.set_candidate(rid, c["cand_id"], status="WF_PASSED", stage="L5")
    sel = {s["cand_id"]: s for s in factory.select_and_freeze(st, st.run(rid), vault, led)}
    assert sel[weak["cand_id"]]["selected"] is False
    assert any("globale DSR" in r for r in sel[weak["cand_id"]]["reasons"])
    assert st.candidate(rid, weak["cand_id"])["status"] == "REJECTED"
    assert sel[strong["cand_id"]]["selected"] is True
    l6 = st.stage_result(rid, strong["cand_id"], "L6")
    assert l6["payload"]["gate"]["scope"] == "global" and l6["payload"]["gate"]["status"] == "PASS"
    rec = l6["payload"]["record"]
    assert rec["selection_criteria"]["preregistration_sha256"] == st.run(rid)["config"]["preregistration"]["sha256"]
    r = factory.final_test_candidate(st, st.run(rid), strong["cand_id"], vault, led)
    assert r["payload"]["evidence_class"] == "HISTORICAL_OOS"
    assert any(u["purpose"] == "SEALED_FINAL" for u in led.data_usage())
    mt = factory.finalize_run(st, st.run(rid), vault, led)
    assert mt["dsr_scope"] == "sealed_tests_only" and mt["search_trials_used_in_sealed_dsr"] is False
    assert mt["sealed_dsr_n"] == mt["n_final_tested"]
    c = st.candidate(rid, strong["cand_id"])
    assert c["status"] in ("FINAL_FAILED", "OOS_APPROVED", "FORWARD_ELIGIBLE")
    row = next(x for x in factory.candidate_rows(st, rid) if x["id"] == strong["cand_id"])
    assert row["promotion_status"] in ("REJECTED", "HISTORICAL_OOS_PASS", "FORWARD_ELIGIBLE")
    det = factory.candidate_detail(st, strong["cand_id"], rid)
    assert det["multiple_testing_final"]["status"] == "PASS" and "promotion_status" in det
    cnt = factory.counts(st, rid)
    assert sum(cnt["promotion"].values()) == cnt["generated"]


def test_sealed_period_never_read_by_search_and_validation_stages(tmp_path, monkeypatch):
    """Runs L1-L5 in-process for real candidates and records every range the search asks for."""
    db, st, rid, cfg = new_run(tmp_path, max_candidates=2)
    factory_worker.prepare_run(st, st.run(rid))
    led = ResearchLedger(cfg["ledger_path"])
    factory_worker.generate_candidates(st, st.run(rid), led)
    run = st.run(rid)
    seal = protocol.utc(cfg["sealed_start"])
    asked = []
    orig = protocol.assert_search_range

    def spy(start, end, sealed_start):
        asked.append((protocol.utc(start), protocol.utc(end)))
        return orig(start, end, sealed_start)

    monkeypatch.setattr(protocol, "assert_search_range", spy)
    mk = run["protocol"]["markets"]["XAUUSD:H4"]
    for row in st.candidates(rid):
        data = protocol.SearchData.from_file(mk["frames_path"], cfg["sealed_start"], symbol="XAUUSD", timeframe="H4")
        assert data.bid.index.max() < seal and data.ask.index.max() < seal
        env = factory.Env(st, run, data, ledger=led, vault=protocol.SealedVault(db))
        factory.CandidateRunner(env, row).run()
        if st.stage_result(rid, row["cand_id"], "L5") is None:
            # FIXTURE: force the candidate into L5 (injected L4 result) so the nested WF / robustness path runs
            spec_params = row["candidate"]["spec"].get("params") or []
            dflt = {x["name"]: x["default"] for x in spec_params}
            alt = {x["name"]: (x["max"] if "max" in x and x["max"] != x["default"] else x["default"]) for x in spec_params}
            st.put_stage(rid, row["cand_id"], "L4", "PASS", 0, [], {"fixture": True},
                         {"best": {"params": dflt, "metrics": {}}, "top_k": [dflt, alt], "fixture": True}, [])
            status = factory.CandidateRunner(env, st.candidate(rid, row["cand_id"])).run(stages=("L5",))
            assert status != "ERROR", st.candidate(rid, row["cand_id"])["reasons"]
    assert asked, "zoekcode vroeg geen data op"
    assert max(e for _, e in asked) <= seal
    assert all(s < seal for s, _ in asked)
    for row in st.candidates(rid):
        l5 = st.stage_result(rid, row["cand_id"], "L5")
        if l5 and l5.get("payload"):
            wf = l5["payload"]["walk_forward"]
            assert wf["nested"] and wf["gap"]["purge_days"] >= 1
            assert all(pd.Timestamp(w["test"][1]) <= seal.tz_localize(None) for w in wf["rolling"]["windows"])
            st5 = l5["payload"]["statistics"]
            assert {"global", "family"} <= set(st5["dsr_records"])
            assert st5["dsr"]["method"] == "DSR_V10"
    decisions = led.decisions(run_id=rid)
    assert any(d["stage"] == "L5" and d["decision"].startswith("WF venster") for d in decisions)
    assert any((st.stage_result(rid, r["cand_id"], "L5") or {}).get("payload") for r in st.candidates(rid))
