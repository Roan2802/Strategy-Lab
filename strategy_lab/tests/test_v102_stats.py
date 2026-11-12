"""V10.2 statistical selection audit: DSR verification, double-correction fixes, gate decision logic."""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from strategy_lab.research import effective_trials as et  # noqa: E402
from strategy_lab.research import factory, validation  # noqa: E402

GOOD = dict(n_trades=400, net_profit=900.0, profit_factor=1.4, max_drawdown_pct=8.0, psr=0.99, dsr=0.99, pbo=0.2,
            wfe=0.8, oos_net_profit=900.0, oos_n_trades=400, oos_profitable_windows_frac=0.8,
            cost_stress_spread50_net=500.0, parameter_stability_frac_positive=0.9, net_after_drop_best=700.0,
            yearly_positive_frac=0.8, n_years=6, n_trials=1000)
TH = {"min_trades": 150, "min_oos_trades": 150, "min_profit_factor": 1.2}


def test_dsr_closed_forms():
    # BLdP 2014 worked example, SR0 formula, DSR(N=1) = PSR(0), monotone in N, NC variant uses 1/T_eff
    sr0 = et.expected_max_sr(100, 0.5 / 250)
    assert sr0 == pytest.approx(0.1132, abs=1e-4)
    assert et.psr(2.5 / math.sqrt(250), 1250, -3, 10, sr0) == pytest.approx(0.9004, abs=1e-4)
    assert et.psr(0.05, 500, 0.0, 3.0, et.expected_max_sr(1, 1e-3)) == pytest.approx(et.psr(0.05, 500, 0.0, 3.0, 0.0))
    vals = [et.psr(0.06, 750, 0, 3, et.expected_max_sr(n, 1 / 750)) for n in (2, 10, 100, 1000, 10000)]
    assert vals == sorted(vals, reverse=True)
    rng = np.random.default_rng(3)
    r = rng.standard_normal(750) * 0.01 + 0.0006
    st, eo = et.sharpe_stats(r), et.effective_obs(r)
    rec = et.dsr_record(r, n_raw=500, n_eff=120, variance={"V": None, "status": "NULL_FLOOR_ONLY"}, scope="global")
    assert rec["value"] == pytest.approx(et.psr(st["sr"], eo["T_eff"], st["skew"], st["kurt"],
                                                et.expected_max_sr(120, 1 / eo["T_eff"])))
    assert rec["variants"]["nc_dsr"]["value"] == pytest.approx(rec["value"])
    assert rec["variants"]["paper_dsr"]["status"] == validation.INSUFFICIENT


def test_effective_n_two_level_properties():
    # independent trials: N_eff = N; identical copies of a candidate: N_eff = number of distinct candidates
    groups = [{"n_trials": 24, "n_eff_within": 24.0} for _ in range(5)]
    assert et.two_level_effective_n(groups, None)["n_eff"] == pytest.approx(120.0)
    assert et.two_level_effective_n(groups, {"n_raw": 5, "n_eff": 1.0})["n_eff"] == pytest.approx(24.0)
    g2 = [{"n_trials": 24, "n_eff_within": 3.0}, {"n_trials": 1, "n_eff_within": None}]
    out = et.two_level_effective_n(g2, None)
    assert 1.0 <= out["n_eff"] <= out["n_raw"] == 25


def test_scorecard_defers_dsr_psr_and_never_passes_without_them():
    bad = {**GOOD, "dsr": 0.02, "psr": 0.30}
    old = validation.scorecard(bad, TH)
    assert old["verdict"] == "REJECT"                       # without deferral: hard FAIL on DSR (and PSR)
    deferred = validation.scorecard(bad, {**TH, "deferred_checks": ["dsr", "psr"]})
    assert deferred["verdict"] == "OOS_PASSED" and deferred["counts"]["FAIL"] == 0
    by = {c["name"]: c for c in deferred["checks"]}
    assert by["dsr"]["status"] == "FAIL" and by["dsr"]["deferred"]      # still shown, never hidden
    assert deferred["verdict"] != "FORWARD_ELIGIBLE"                   # a deferred shortfall blocks FORWARD_ELIGIBLE
    # every other hard fail still rejects
    assert validation.scorecard({**bad, "net_profit": -5.0}, {**TH, "deferred_checks": ["dsr", "psr"]})["verdict"] == "REJECT"
    # the deferred decision is made by the single L6 gate, which still rejects (INSUFFICIENT_EVIDENCE for SR > 0)
    rec = {"global": {"status": "OK", "value": 0.02}, "family": {"value": 0.5}}
    assert et.gate(rec, 0.95)["status"] == "FAIL"


def _ev(verdict="SUPPORTED_UNDER_ASSUMPTIONS"):
    return {"verdict": verdict, "reasons": []}


def test_l5_decision_dsr_shortfall_is_not_a_l5_rejection_but_failures_and_small_samples_are_classified():
    passed = [{"name": "x", "status": "PASS", "explanation_nl": "ok"}]
    th = {**TH, "deferred_checks": ["dsr", "psr"]}
    sc = validation.scorecard({**GOOD, "dsr": 0.01, "psr": 0.2}, th)
    d = factory.l5_decision(passed, sc, None, _ev())
    assert not d["reasons"] and not d["insufficient"]
    # real failure keeps rejecting
    failing = passed + [{"name": "spread_stress", "status": "FAIL", "explanation_nl": "verlies"}]
    assert factory.l5_decision(failing, sc, None, _ev())["reasons"] == ["verlies"]
    # too few trades: scorecard NEEDS_MORE_DATA without any FAIL is missing evidence, not a rejection
    small = validation.scorecard({**GOOD, "n_trades": 40, "oos_n_trades": 40}, th)
    assert small["verdict"] == "NEEDS_MORE_DATA" and small["counts"]["FAIL"] == 0
    d = factory.l5_decision(passed, small, None, _ev())
    assert not d["reasons"] and d["insufficient"]
    # a scorecard FAIL (e.g. cost stress) is still a rejection
    sc_fail = validation.scorecard({**GOOD, "cost_stress_spread50_net": -1.0, "wfe": 0.1}, th)
    assert factory.l5_decision(passed, sc_fail, None, _ev())["reasons"]
    # robustness verdict is consulted only when nothing else decided
    assert factory.l5_decision(passed, sc, None, _ev("POSITIVE_UNCERTAIN"))["reasons"]
    assert factory.l5_decision(passed, sc, False, _ev())["reasons"]


def test_l6_outcome_for_positive_sharpe_dsr_shortfall_is_insufficient_evidence():
    out = factory.classify_outcome({"status": "INSUFFICIENT_EVIDENCE", "stage": "L6",
                                    "reasons": ["meervoudig testen: globale DSR 0.02 < 0.95 ..."]})
    assert out["outcome"] == "INSUFFICIENT_EVIDENCE"


def test_pbo_over_l4_uses_every_first_rung_configuration():
    rng = np.random.default_rng(3)
    days = [f"2019-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 28)]
    params = [{"k": j} for j in range(24)]
    payload = {"rungs": [{"trials": [{"params": p} for p in params] + [{"params": params[0]}]},
                         {"trials": [{"params": params[0]}]}]}
    seen = []

    def trades_fn(p):
        seen.append(p["k"])
        return [{"entry_time": f"{d}T10:00:00", "exit_time": f"{d}T12:00:00", "pnl": float(x)}
                for d, x in zip(days, rng.normal(0, 10, len(days)))]
    out = factory.pbo_over_l4(payload, trades_fn, 10_000.0)
    assert sorted(seen) == list(range(24))          # all 24, duplicate removed, later rungs ignored
    assert out["config_set"] == "L4_all" and out["n_configs"] == 24
    assert out["pbo"] is not None and 0.0 <= out["pbo"] <= 1.0
    assert factory.pbo_over_l4({"rungs": []}, trades_fn, 1.0)["pbo"] is None
