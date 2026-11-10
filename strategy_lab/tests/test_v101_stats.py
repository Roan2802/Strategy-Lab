"""V10.1: DSR variants side by side, outcome statuses, dual-positive promotion rule, controlled-signal tests and
optimiser correctness. Synthetic data only (never real sealed data)."""
import math
import os
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from strategy_lab.research import effective_trials as et  # noqa: E402
from strategy_lab.research import factory, factory_worker, nested_wf, protocol  # noqa: E402
from strategy_lab.research.ledger import ResearchLedger  # noqa: E402
from strategy_lab.tests.test_factory import new_run  # noqa: E402
from strategy_lab.tests.test_factory_safety import _om, _runner, _statuses, prepared  # noqa: E402,F401

Q = factory.QUALITY_PROFILES["regelmatig"]


# ------------------------------------------------------------------------------------------------ DSR variants
def _var(srs, T):
    return et.trial_variance(srs, [T] * len(srs), T, min_T=2)


def test_three_variants_side_by_side_and_gate_is_nc_dsr():
    rng = np.random.default_rng(0)
    r = rng.normal(0.08, 1.0, 1500)
    rec = et.dsr_record(r, n_raw=100, n_eff=40.0, variance=_var(rng.normal(0, 0.03, 100), 1500), scope="global")
    v = rec["variants"]
    assert set(v) == {"paper_dsr", "nc_dsr", "dep_dsr"} and rec["gate_variant"] == "nc_dsr"
    assert v["nc_dsr"]["gate"] and not v["paper_dsr"]["gate"]
    assert "Null-calibrated" in v["nc_dsr"]["label"]
    assert v["nc_dsr"]["value"] == pytest.approx(rec["value"])
    assert v["paper_dsr"]["N"] == 100 and v["dep_dsr"]["N"] == 40.0 and v["nc_dsr"]["V"] == pytest.approx(rec["V_null"])
    for k in v:
        lo, hi = v[k]["sr_ci95"]
        assert lo < rec["observed_sr"] < hi and v[k]["se_sr"] > 0
    assert rec["reliability"]["reliable"] is True


def test_paper_variant_reproduces_bldp_worked_example():
    st = {"sr": 2.5 / math.sqrt(250), "T": 1250, "skew": -3.0, "kurt": 10.0}
    eo = {"T_eff": 1250.0}
    v = et.dsr_variants(st, eo, n_raw=100, n_eff=100, V_paper=0.5 / 250)
    assert v["paper_dsr"]["sr0"] == pytest.approx(0.1132, abs=1e-4)
    assert v["paper_dsr"]["value"] == pytest.approx(0.9004, abs=1e-3)


def test_paper_variants_insufficient_without_cross_trial_v_and_short_sample_unreliable():
    r = np.random.default_rng(1).normal(0.1, 1, 45)
    rec = et.dsr_record(r, n_raw=1, n_eff=1.0, variance=_var([0.1], 45), scope="global")
    assert rec["variants"]["paper_dsr"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert rec["variants"]["nc_dsr"]["status"] == "OK"
    assert rec["reliability"]["reliable"] is False and rec["reliability"]["reasons_nl"]


# ------------------------------------------------------------------------------------------------ controlled signals
def _search(seed, N=50, T=1000, edge_ann=0.0):
    rng = np.random.default_rng(seed)
    r = rng.standard_normal((N, T))
    r[0] += edge_ann / math.sqrt(252)
    srs = r.mean(1) / r.std(1, ddof=1)
    i = int(np.argmax(srs))
    n_eff = et.effective_n(pd.DataFrame(r.T))["n_eff"]
    return et.dsr_record(r[i], n_raw=N, n_eff=n_eff, variance=_var(srs, T), scope="global")


def test_meaningless_signals_are_not_promoted():
    vals = [_search(s)["value"] for s in range(20)]
    assert sum(v >= 0.95 for v in vals) == 0


def test_known_injected_edge_detected_with_enough_data():
    hits = [_search(100 + s, T=1500, edge_ann=3.0)["value"] >= 0.95 for s in range(10)]
    assert sum(hits) >= 8


def test_seeded_results_are_reproducible():
    a, b = _search(7), _search(7)
    assert a["value"] == b["value"] and a["variants"] == b["variants"]


def test_no_double_correction_gate_uses_global_record_only():
    g = _search(3, edge_ann=3.0, T=1500)
    f = dict(g, value=0.10)
    gate = et.gate({"global": g, "family": f}, 0.95)
    assert gate["value"] == g["value"] and gate["family_value"] == 0.10


def test_weak_signal_is_uncertainty_not_rejection():
    om = _om(trades=400, net_profit=600.0, profit_factor=1.10, recovery_factor=1.2, expectancy=1.5)
    s = _statuses(factory.quality_checks(om, Q, boot={"status": "OK", "ci_low": -1.0, "ci_high": 4.0},
                                         pf_ci={"status": "OK", "ci_low": 0.95, "ci_high": 1.30},
                                         rf_ci={"status": "OK", "ci_low": -0.5, "ci_high": 3.0}))
    assert s["profit_factor_oos"] == s["recovery_factor_oos"] == s["expectancy"] == "INSUFFICIENT_EVIDENCE"
    # a shortfall that the CI establishes is a FAIL
    s2 = _statuses(factory.quality_checks(om, Q, pf_ci={"status": "OK", "ci_low": 1.02, "ci_high": 1.15}))
    assert s2["profit_factor_oos"] == "FAIL"
    # DSR below threshold with a positive Sharpe -> INSUFFICIENT_EVIDENCE outcome, never PASSED
    o = factory.classify_outcome({"status": "INSUFFICIENT_EVIDENCE", "stage": "L6",
                                  "reasons": ["meervoudig testen: globale DSR 0.4 < 0.95 ..."]})
    assert o["outcome"] == "INSUFFICIENT_EVIDENCE" and o["rejection_kind"] == "onvoldoende bewijs"
    assert any("toevalstrials" in m for m in o["missing_nl"])


def test_small_sample_is_insufficient_not_loss():
    s = _statuses(factory.quality_checks(_om(trades=12, net_profit=-50.0, profit_factor=0.8, expectancy=-4.0,
                                             recovery_factor=0.0), Q, boot={"status": "INSUFFICIENT_EVIDENCE"}))
    assert s["oos_trades"] == s["expectancy"] == s["profit_factor_oos"] == "INSUFFICIENT_EVIDENCE"
    s = _statuses(factory.quality_checks(_om(trades=80, net_profit=-500.0, profit_factor=0.8, expectancy=-6.0,
                                             recovery_factor=0.0), Q, boot={"status": "OK", "ci_low": -9, "ci_high": -1}))
    assert s["expectancy"] == s["profit_factor_oos"] == "FAIL"


# ------------------------------------------------------------------------------------------------ single window
def test_single_window_rule_needs_enough_windows():
    few = factory.single_window_check({"flag": True, "best_window_share": 0.8, "n_windows": 2, "min_windows": 4})
    many = factory.single_window_check({"flag": True, "best_window_share": 0.8, "n_windows": 8, "min_windows": 4})
    ok = factory.single_window_check({"flag": False, "n_windows": 2})
    assert (few["status"], many["status"], ok["status"]) == ("INSUFFICIENT_EVIDENCE", "FAIL", "PASS")
    assert nested_wf.MIN_WINDOWS_SINGLE == 4


# ------------------------------------------------------------------------------------------------ dual positive
@pytest.mark.parametrize("dev,oos,expected", [
    ({"net_profit": 500, "profit_factor": 1.3}, {"net_profit": 300, "trades": 200, "expectancy": 1.5}, "PASS"),
    ({"net_profit": -500, "profit_factor": 0.8}, {"net_profit": 300, "trades": 200, "expectancy": 1.5}, "FAIL"),
    ({"net_profit": 500, "profit_factor": 1.3}, {"net_profit": -300, "trades": 200, "expectancy": -1.5}, "FAIL"),
    ({"net_profit": 500, "profit_factor": 0.99}, {"net_profit": 300, "trades": 200, "expectancy": 1.5}, "FAIL"),
    ({"net_profit": 500, "profit_factor": 1.3}, {"net_profit": -30, "trades": 10, "expectancy": -3}, "INSUFFICIENT_EVIDENCE"),
    ({"net_profit": -500, "profit_factor": 0.8}, {"net_profit": -300, "trades": 200, "expectancy": -1.5}, "FAIL"),
])
def test_dual_positive_gate(dev, oos, expected):
    g = factory.dual_positive_gate(dev, oos)
    assert g["status"] == expected
    if not (g["dev_positive"] and g["oos_positive"]):
        assert g["status"] != "PASS"


def test_l6_refuses_to_freeze_when_development_negative(tmp_path):
    db, st, rid, cfg = new_run(tmp_path)
    factory_worker.prepare_run(st, st.run(rid))
    led = ResearchLedger(cfg["ledger_path"])
    factory_worker.generate_candidates(st, st.run(rid), led)
    c = st.candidates(rid)[0]
    params = {x["name"]: x["default"] for x in c["candidate"]["spec"]["params"]}
    idx = pd.bdate_range("2016-01-04", "2019-06-28")
    daily = {d.strftime("%Y-%m-%d"): float(v) for d, v in zip(idx, np.random.default_rng(5).normal(0.004, 0.01, len(idx)))}
    st.put_stage(rid, c["cand_id"], "L4", "PASS", 0, [], {"net_profit": -800.0, "profit_factor": 0.9}, {}, [])
    st.put_stage(rid, c["cand_id"], "L5", "PASS", 0, [], {"dsr": 0.99, "net_profit": 4000.0, "trades": 300,
                                                         "expectancy": 13.0, "profit_factor": 1.6, "recovery_factor": 4},
                 {"params": params, "daily_returns": daily, "fixture": True}, [])
    st.set_candidate(rid, c["cand_id"], status="WF_PASSED", stage="L5")
    sel = {s["cand_id"]: s for s in factory.select_and_freeze(st, st.run(rid), protocol.SealedVault(db), led)}
    assert sel[c["cand_id"]]["selected"] is False
    row = st.candidate(rid, c["cand_id"])
    assert row["status"] == "REJECTED" and row["frozen_id"] is None
    out = next(x for x in factory.candidate_rows(st, rid) if x["id"] == c["cand_id"])
    assert out["outcome"] == "REJECTED" and out["is_candidate"] is False
    det = factory.candidate_detail(st, c["cand_id"], rid)
    assert det["dual_positive"]["dev_positive"] is False and "missing_nl" in det and "next_test_nl" in det


# ------------------------------------------------------------------------------------------------ outcomes
@pytest.mark.parametrize("status,stage,reasons,outcome,kind", [
    ("ERROR", "L3", ["fout in L3: KeyError"], "TECHNICAL_BLOCKED", "technisch"),
    ("TECHNICAL_BLOCKED", "L1", ["onvoldoende Ask-dekking (12% ontbreekt)"], "TECHNICAL_BLOCKED", "technisch"),
    ("REJECTED", "L1", ["onvoldoende Ask-dekking (12% ontbreekt)"], "TECHNICAL_BLOCKED", "technisch"),
    ("REJECTED", "L1", ["gebruikt toekomstige candles (look-ahead): x"], "REJECTED", "technisch"),
    ("REJECTED", "L3", ["ontwikkelperiode verliesgevend na kosten (PF 0.80 < 0,95)"], "REJECTED", "statistisch"),
    ("REJECTED", "L5", ["40 OOS-trades; minimaal 150 nodig."], "INSUFFICIENT_EVIDENCE", "onvoldoende bewijs"),
    ("REJECTED", "L6", ["meervoudig testen: globale DSR 0.3 < 0.95 over de volledige zoektocht"],
     "INSUFFICIENT_EVIDENCE", "onvoldoende bewijs"),
    ("REUSED_HOLDOUT", "L1", ["REUSED_HOLDOUT: ..."], "INSUFFICIENT_EVIDENCE", "onvoldoende bewijs"),
    ("WF_PASSED", "L5", [], "PASSED", None),
    ("DEV_TESTED", "L3", [], "IN_PROGRESS", None),
])
def test_outcome_mapping_old_and_new_records(status, stage, reasons, outcome, kind):
    o = factory.classify_outcome({"status": status, "stage": stage, "reasons": reasons})
    assert (o["outcome"], o["rejection_kind"]) == (outcome, kind)
    assert o["stored_status"] == status and o["next_test_nl"]
    if outcome in ("INSUFFICIENT_EVIDENCE", "TECHNICAL_BLOCKED"):
        assert o["missing_nl"]


def test_promotion_ladder_has_technical_blocked():
    assert factory.promotion_status("ERROR", "L3")["promotion_status"] == "TECHNICAL_BLOCKED"
    assert "TECHNICAL_BLOCKED" in factory.TERMINAL_STATUSES


# ------------------------------------------------------------------------------------------------ optimiser
def test_random_search_is_seeded_unique_and_inside_space():
    space = {"a": {"type": "int", "min": 2, "max": 40, "step": 1, "default": 10},
             "b": {"type": "float", "min": 0.5, "max": 3.0, "step": 0.25, "default": 1.0}}
    x, y = factory.sample_params(space, 20, "s:1"), factory.sample_params(space, 20, "s:1")
    assert x == y and x[0] == {"a": 10, "b": 1.0}
    assert len({protocol.canon(p) for p in x}) == len(x)
    assert all(2 <= p["a"] <= 40 and 0.5 <= p["b"] <= 3.0 for p in x)
    assert factory.sample_params(space, 20, "s:2") != x


def test_successive_halving_keeps_top_ceil_n_over_eta(prepared):
    p = prepared
    runner = _runner(p)
    eta = int(runner.env.prof["eta"])

    def score(params):
        return random.Random(protocol.canon(params)).random()

    def fake_trial(params, rung, start, end, extra_windows=None):
        m = {"trades": 200, "trades_per_week": 5.0, "net_profit": 1000.0, "profit_factor": 1.5,
             "recovery_factor": 1.0 + 5 * score(params), "stability": 1.0, "max_drawdown_pct": 5.0}
        return {"params": params, "metrics": m, "key": protocol.canon([rung, params]), "reused": False}

    runner._trial = fake_trial
    try:
        runner.l4()
    except factory.StageReject:
        pass
    pay = runner.state["L4_payload"]
    rungs = pay["rungs"]
    n0 = rungs[0]["n"]
    assert n0 == len({protocol.canon(t["params"]) for t in rungs[0]["trials"]})
    for a, b in zip(rungs, rungs[1:]):
        assert b["n"] == max(1, math.ceil(a["n"] / eta))
        best_a = sorted(a["trials"], key=lambda t: -score(t["params"]))[: b["n"]]
        assert {protocol.canon(t["params"]) for t in b["trials"]} == {protocol.canon(t["params"]) for t in best_a}
    assert pay["best"]["params"] == max(rungs[-1]["trials"], key=lambda t: score(t["params"]))["params"]
    assert pay["optimizer"]["used"] == "random_successive_halving"


def test_optuna_status_reported():
    st = factory.optuna_status()
    try:
        import optuna  # noqa: F401
        assert st["status"] == "AVAILABLE"
    except ImportError:
        assert st["status"] == "NOT_AVAILABLE"


# ------------------------------------------------------------------------------------------------ integrity
def _frozen_record(**kw):
    rec = {k: None for k in protocol.FROZEN_REQUIRED}
    rec.update(spec={"entry": {}}, params={"a": 1}, symbol="SYN", timeframe="H1", signature="sig", family="fam",
               seed=1, sealed_start="2024-01-01", sealed_end="2025-01-01")
    rec.update(kw)
    return rec


def test_frozen_record_is_content_addressed_and_immutable(tmp_path):
    v = protocol.SealedVault(tmp_path / "vault.sqlite3")
    a = v.freeze(_frozen_record())
    assert v.freeze(_frozen_record()) == a
    b = v.freeze(_frozen_record(params={"a": 2}))
    assert b != a and v.frozen(a)["record"]["params"] == {"a": 1}


def test_repeated_rounds_on_validation_period_are_recognised(tmp_path):
    led = ResearchLedger(tmp_path / "ledger.sqlite3")
    led.record_data_usage(run_id="run_1", symbol="SYN", timeframe="H1", start="2018-01-01", end="2024-01-01",
                          purpose="VALIDATION_SELECTION")
    first = led.validation_reuse("SYN", "2018-01-01", "2024-01-01", exclude_run="run_1")
    second = led.validation_reuse("SYN", "2018-01-01", "2024-01-01", exclude_run="run_2")
    assert first["evidence_class"] == "FIRST_USE_SELECTION"
    assert second["evidence_class"] == "REUSED_FOR_SELECTION" and second["prior_runs"] == ["run_1"]


def test_duplicate_is_marked_and_not_called_statistical():
    o = factory.classify_outcome({"status": "REJECTED", "stage": "L1",
                                  "reasons": ["duplicaat: deze signatuur is al eerder getest (onderzoekslog)"]})
    assert o["duplicate"] and o["rejection_kind"] == "technisch" and o["outcome"] == "REJECTED"


def test_superseded_supervisor_stops(tmp_path):
    db = tmp_path / "factory.sqlite3"
    factory.FactoryStore(db)
    sup = factory_worker.Supervisor(str(db), idle_exit_s=0.1)
    assert sup.claim()
    with factory.closing(sup.store.db()) as c, c:
        c.execute("UPDATE supervisor SET pid=? WHERE id=1", (os.getpid() + 1,))
    with pytest.raises(factory_worker.Superseded):
        sup.heartbeat(force=True)
