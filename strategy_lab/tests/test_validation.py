import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from strategy_lab.research import validation as v  # noqa: E402


# ----------------------------------------------------------------- helpers
def make_trades(pnls, start="2023-01-02", every_hours=24):
    t0 = pd.Timestamp(start, tz="UTC")
    out = []
    for i, p in enumerate(pnls):
        et = t0 + pd.Timedelta(hours=every_hours * i)
        out.append({"entry_time": (et - pd.Timedelta(hours=2)).isoformat(), "exit_time": et.isoformat(),
                    "side": "long", "pnl": float(p), "r_multiple": float(p) / 10, "costs": 1.0})
    return out


def fake_backtest(params, start, end, spread_multiplier=1.0, commission_per_lot=0.0, slippage_points=0.0,
                  entry_delay_bars=0, exit_delay_bars=0):
    """One trade per business day. Edge peaks at x=5; deterministic pseudo-noise; costs subtract."""
    days = pd.bdate_range(start, pd.Timestamp(end) - pd.Timedelta(days=1))
    x = params.get("x", 5)
    cost = 2.0 * spread_multiplier + commission_per_lot * 0.1 + slippage_points * 1.5 \
        + 0.8 * entry_delay_bars + 0.8 * exit_delay_bars
    trades = []
    for i, d in enumerate(days):
        noise = 6 * math.sin(d.dayofyear * 12.9898 + i * 78.233)
        pnl = 12 - (x - 5) ** 2 * 2 + noise - cost
        trades.append({"entry_time": (d + pd.Timedelta(hours=8)).isoformat() + "+00:00",
                       "exit_time": (d + pd.Timedelta(hours=12)).isoformat() + "+00:00",
                       "side": "long", "pnl": pnl, "r_multiple": pnl / 10, "costs": cost})
    return {"trades": trades, "metrics": {}}


# ----------------------------------------------------------------- PSR / DSR / MinTRL
def test_normal_cdf_ppf_roundtrip():
    assert v.norm_ppf(0.95) == pytest.approx(1.6448536, abs=1e-6)
    assert v.norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-6)
    for p in (1e-8, 0.01, 0.3, 0.5, 0.9, 1 - 1e-8):
        assert v.norm_cdf(v.norm_ppf(p)) == pytest.approx(p, rel=1e-9)


def test_psr_worked_example():
    # SR=0.1, n=250, skew=-0.5, kurt=5: denom = 1+0.05+0.01 = 1.06; z = 0.1*sqrt(249)/sqrt(1.06) = 1.5326
    assert v.psr_from_moments(0.1, 250, -0.5, 5.0, 0.0) == pytest.approx(0.93732, abs=1e-4)
    # benchmark 0.05: z = 0.05*sqrt(249)/sqrt(1 - (-0.5)(0.1) + 1*0.01) ... uses SR_hat in the denominator
    assert v.psr_from_moments(0.1, 250, -0.5, 5.0, 0.05) == pytest.approx(0.77826, abs=1e-4)


def test_psr_from_returns_normal_case_matches_closed_form():
    rng = np.random.default_rng(1)
    r = rng.normal(0.001, 0.01, 1000)
    res = v.probabilistic_sharpe_ratio(r)
    sr = r.mean() / r.std(ddof=1)
    expected = v.norm_cdf(sr * math.sqrt(999) / math.sqrt(1 + 0.5 * sr * sr))  # normal: skew 0, kurt 3
    assert res["status"] == "OK"
    assert res["value"] == pytest.approx(expected, abs=0.01)


def test_dsr_worked_example():
    # N=100 trials, V[SR]=0.01 -> SR0 = 0.1*((1-g)*z(0.99)+g*z(1-1/(100e))) = 0.2531
    assert v.expected_max_sr(100, 0.01) == pytest.approx(0.25306, abs=1e-4)
    assert v.psr_from_moments(0.1, 250, -0.5, 5.0, v.expected_max_sr(100, 0.01)) == pytest.approx(0.00949, abs=1e-4)
    # one trial -> no deflation -> DSR == PSR(0)
    rng = np.random.default_rng(2)
    r = rng.normal(0.002, 0.01, 500)
    assert v.deflated_sharpe_ratio(r, 1, 0.01)["value"] == pytest.approx(v.probabilistic_sharpe_ratio(r)["value"])
    # more trials -> lower DSR
    d10 = v.deflated_sharpe_ratio(r, 10, 0.0004)["value"]
    d500 = v.deflated_sharpe_ratio(r, 500, 0.0004)["value"]
    assert d500 < d10


def test_min_track_record_length_worked_example():
    rng = np.random.default_rng(3)
    r = rng.normal(0.002, 0.01, 400)
    m = v._moments(r)
    z = 1.6448536269514722
    f = 1 - m["skew"] * m["sr"] + (m["kurt"] - 1) / 4 * m["sr"] ** 2
    res = v.min_track_record_length(r, 0.0, 0.95)
    assert res["value"] == pytest.approx(1 + f * (z / m["sr"]) ** 2, rel=1e-9)
    # hand number: SR=0.1, skew=-0.5, kurt=5 -> 1 + 1.06*(1.6449/0.1)^2 = 287.79
    assert 1 + 1.06 * (z / 0.1) ** 2 == pytest.approx(287.79, abs=0.01)
    assert v.min_track_record_length(-r)["status"] == "NOT_REACHABLE"


def test_sr_insufficient_evidence():
    assert v.probabilistic_sharpe_ratio(np.random.default_rng(0).normal(size=20))["status"] == "INSUFFICIENT_EVIDENCE"
    assert v.probabilistic_sharpe_ratio(np.full(100, 0.01))["status"] == "INSUFFICIENT_EVIDENCE"
    assert v.deflated_sharpe_ratio(np.zeros(100), 10, 0.01)["status"] == "INSUFFICIENT_EVIDENCE"
    assert v.min_track_record_length(np.ones(10))["status"] == "INSUFFICIENT_EVIDENCE"


# ----------------------------------------------------------------- bootstrap
def test_bootstrap_ci_covers_true_mean_on_iid():
    hits = 0
    reps = 40
    for s in range(reps):
        x = np.random.default_rng(100 + s).normal(0.5, 1.0, 200)
        ci = v.bootstrap_confidence_interval(x, n=500, block=None, seed=s)
        hits += ci["ci_low"] <= 0.5 <= ci["ci_high"]
    assert hits / reps >= 0.85


def test_block_bootstrap_wider_than_iid_on_ar1():
    rng = np.random.default_rng(7)
    n, rho = 600, 0.7
    e = rng.normal(size=n)
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = rho * x[t - 1] + e[t]
    iid = v.bootstrap_confidence_interval(x, n=1000, block=None, seed=1)
    blk = v.bootstrap_confidence_interval(x, n=1000, block="auto", seed=1)
    mov = v.bootstrap_confidence_interval(x, n=1000, block=8, seed=1, method="moving")
    w = lambda c: c["ci_high"] - c["ci_low"]
    assert blk["block"] == round(n ** (1 / 3))
    assert w(blk) > 1.2 * w(iid)
    assert w(mov) > 1.2 * w(iid)


def test_bootstrap_small_sample_insufficient():
    assert v.bootstrap_confidence_interval([1, 2, 3])["status"] == "INSUFFICIENT_EVIDENCE"


# ----------------------------------------------------------------- Monte Carlo
def test_monte_carlo_reproducible_and_percentiles_ordered():
    rng = np.random.default_rng(5)
    trades = make_trades(rng.normal(5, 40, 200))
    a = v.monte_carlo(trades, 10_000, n=300, seed=42)
    b = v.monte_carlo(trades, 10_000, n=300, seed=42)
    c = v.monte_carlo(trades, 10_000, n=300, seed=43)
    assert a["final_equity"] == b["final_equity"]
    assert a["final_equity"] != c["final_equity"]
    for key in ("final_equity", "max_drawdown_pct", "max_drawdown_abs"):
        d = a[key]
        assert d["p5"] <= d["p25"] <= d["p50"] <= d["p75"] <= d["p95"]
    bands = a["equity_bands"]
    assert all(lo <= hi for lo, hi in zip(bands["p5"], bands["p95"]))
    assert 0 <= a["risk_of_ruin"] <= 1


def test_monte_carlo_shuffle_keeps_final_equity_and_ruin_responds_to_threshold():
    trades = make_trades(np.random.default_rng(6).normal(2, 60, 120))
    total = sum(t["pnl"] for t in trades)
    sh = v.monte_carlo(trades, 5_000, n=200, method="shuffle", seed=1)
    assert sh["final_equity"]["min"] == pytest.approx(5_000 + total)
    assert sh["final_equity"]["max"] == pytest.approx(5_000 + total)
    assert any("seriele" in n for n in sh["notes_nl"])
    loose = v.monte_carlo(trades, 5_000, n=200, method="bootstrap", seed=1, ruin_drawdown_pct=99)
    tight = v.monte_carlo(trades, 5_000, n=200, method="bootstrap", seed=1, ruin_drawdown_pct=5)
    assert tight["risk_of_ruin"] >= loose["risk_of_ruin"]
    shocked = v.monte_carlo(trades, 5_000, n=200, method="bootstrap", seed=1, cost_shock_per_trade=20)
    assert shocked["final_equity"]["p50"] < tight["final_equity"]["p50"]


def test_monte_carlo_small_sample_insufficient():
    assert v.monte_carlo(make_trades([1, -1, 2]), 1000)["status"] == "INSUFFICIENT_EVIDENCE"


# ----------------------------------------------------------------- PBO
def test_pbo_noise_is_about_half_and_antipersistent_near_one():
    rng = np.random.default_rng(11)
    noise = rng.normal(0, 0.01, (160, 40))
    res = v.pbo_cscv(noise, n_splits=8)
    assert res["status"] == "OK" and res["n_combinations"] == 70
    # selection on noise carries no information: PBO ~ 0.5 on average (high variance per draw), not 1
    mean_pbo = np.mean([v.pbo_cscv(np.random.default_rng(s).normal(0, .01, (160, 40)), 8)["pbo"]
                        for s in range(20)])
    assert 0.4 <= mean_pbo <= 0.65
    # column-demeaned noise with mean metric: IS-best is exactly OOS-worst -> PBO = 1
    anti = noise - noise.mean(axis=0)
    assert v.pbo_cscv(anti, n_splits=8, metric="mean")["pbo"] > 0.95


def test_pbo_dominant_trial_near_zero():
    rng = np.random.default_rng(12)
    M = rng.normal(0, 0.01, (160, 30))
    M[:, 0] += 0.02
    res = v.pbo_cscv(M, n_splits=8)
    assert res["pbo"] < 0.05


def test_pbo_insufficient():
    rng = np.random.default_rng(1)
    assert v.pbo_cscv(rng.normal(size=(100, 1)))["status"] == "INSUFFICIENT_EVIDENCE"
    assert v.pbo_cscv(rng.normal(size=(5, 10)), n_splits=8)["status"] == "INSUFFICIENT_EVIDENCE"


# ----------------------------------------------------------------- stress / drop best / stability
def test_cost_stress_fake_backtest():
    res = v.cost_stress(fake_backtest, {"x": 5}, "2023-01-02", "2023-07-01")
    names = list(res["scenarios"])
    assert names == ["baseline", "spread_+25%", "spread_+50%", "commission_x2", "slippage_+1pt",
                     "entry_delay_1bar", "exit_delay_1bar"]
    base = res["scenarios"]["baseline"]["net_profit"]
    n = res["scenarios"]["baseline"]["n_trades"]
    # spread +50% adds 1.0 cost per trade
    assert res["scenarios"]["spread_+50%"]["net_profit"] == pytest.approx(base - 1.0 * n)
    assert res["scenarios"]["spread_+50%"]["degradation"]["net_profit_delta"] == pytest.approx(-n)
    assert res["scenarios"]["slippage_+1pt"]["net_profit"] == pytest.approx(base - 1.5 * n)
    assert res["survives_spread_50"] is True
    custom = v.cost_stress(fake_backtest, {"x": 5}, "2023-01-02", "2023-07-01",
                           scenarios={"huge": {"spread_multiplier": 50}})
    assert custom["survives_spread_50"] is None and custom["scenarios"]["huge"]["net_profit"] < 0


def test_drop_best():
    pnls = [10] * 60 + [-5] * 30 + [500, 400, 300]
    r = v.drop_best(make_trades(pnls))
    base = sum(pnls)
    assert r["baseline_net"] == base
    assert r["by_trades"]["1"]["net_after"] == base - 500
    assert r["by_trades"]["3"]["net_after"] == base - 1200
    assert r["by_trades"]["3"]["still_positive"] is True
    assert r["by_days"]["5"]["net_after"] == base - (1200 + 20)
    lottery = v.drop_best(make_trades([-1] * 60 + [200]))
    assert lottery["by_trades"]["1"]["still_positive"] is False


def test_parameter_stability_fake():
    specs = {"x": {"type": "int", "min": 1, "max": 20}}
    ok = v.parameter_stability(fake_backtest, {"x": 5}, specs, "2023-01-02", "2023-07-01", rel_step=0.2)
    assert ok["status"] == "OK" and ok["n_neighbours"] == 2
    assert ok["frac_positive"] == 1.0
    assert ok["worst_rel_change"] < 0  # x=5 is the optimum
    sharp = v.parameter_stability(fake_backtest, {"x": 5}, specs, "2023-01-02", "2023-07-01", rel_step=1.0)
    assert sharp["frac_positive"] < 1.0


# ----------------------------------------------------------------- walk-forward
def test_walk_forward_fake_backtest_rolling_and_expanding():
    grid = {"x": [2, 4, 5, 6, 9]}
    kw = dict(start="2023-01-02", end="2024-01-01", train_days=120, test_days=60, step_days=60)
    r = v.walk_forward(fake_backtest, grid, objective="net_profit", **kw)
    assert r["status"] == "OK" and r["n_windows"] >= 4
    for w in r["windows"]:
        assert w["params"] == {"x": 5}
        assert pd.Timestamp(w["train"][1]) == pd.Timestamp(w["test"][0])
        assert w["status"] == "OK"
        assert w["test_metrics"]["n_trades"] > 10
    assert r["oos_net_profit"] == pytest.approx(sum(t["pnl"] for t in r["oos_trades"]))
    assert r["oos_n_trades"] == len(r["oos_trades"])
    # no overlap between consecutive OOS windows (step == test length)
    ends = [w["test"][1] for w in r["windows"]]
    assert ends == sorted(ends)
    assert r["wfe"] is not None and 0.2 < r["wfe"] < 3
    exp = v.walk_forward(fake_backtest, grid, mode="expanding", objective="sharpe", **kw)
    assert exp["windows"][0]["train"][0] == exp["windows"][-1]["train"][0] == "2023-01-02"
    # WFE explicit formula
    w = r["windows"][0]
    expected = (w["test_metrics"]["net_profit"] / 60) / (w["train_metrics"]["net_profit"] / 120)
    assert w["wfe"] == pytest.approx(expected)


def test_walk_forward_marks_insufficient_windows():
    r = v.walk_forward(fake_backtest, {"x": [5]}, "2023-01-02", "2023-06-01", 30, 14, 14, min_trades=1000)
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert all(w["status"] == "INSUFFICIENT" for w in r["windows"])
    few = v.walk_forward(fake_backtest, {"x": [5]}, "2023-01-02", "2023-06-01", 60, 7, 14, min_trades=10)
    assert all(w["status"] == "INSUFFICIENT" for w in few["windows"])  # 5 trades per test week


def test_walk_forward_caps_combos_and_accepts_sampler():
    grid = {"x": list(range(1, 30))}
    r = v.walk_forward(fake_backtest, grid, "2023-01-02", "2023-09-01", 100, 40, 80, max_combos=5)
    assert r["n_candidates"] == 5
    sampler = lambda n, rng: [{"x": int(i)} for i in rng.integers(1, 10, n)]
    r2 = v.walk_forward(fake_backtest, sampler, "2023-01-02", "2023-09-01", 100, 40, 80, max_combos=4)
    assert r2["n_candidates"] == 4


# ----------------------------------------------------------------- multiple testing & friends
def test_multiple_testing_holm_bh():
    p = [0.001, 0.01, 0.03, 0.04, 0.5]
    holm = v.multiple_testing(p, "holm")
    assert holm["p_adjusted"] == pytest.approx([0.005, 0.04, 0.09, 0.09, 0.5])
    bh = v.multiple_testing(p, "bh")
    assert bh["p_adjusted"] == pytest.approx([0.005, 0.025, 0.05, 0.05, 0.5])
    assert holm["n_reject"] == 2 and bh["n_reject"] == 4
    assert v.multiple_testing(p, "holm", alpha=0.03)["n_reject"] == 1
    assert v.multiple_testing(p, "bh", alpha=0.03)["n_reject"] == 2
    s = v.multiple_testing(sharpe=[0.2, 0.01], n_obs=400, method="holm")
    assert s["p_raw"][0] == pytest.approx(v.norm_sf(0.2 * 20))


def test_haircut_sharpe_grows_with_trials():
    h1 = v.haircut_sharpe(0.2, 1, 400)
    h100 = v.haircut_sharpe(0.2, 100, 400)
    assert h1["haircut"] == pytest.approx(0.0, abs=1e-9)
    assert 0 < h100["haircut"] < 1
    assert v.haircut_sharpe(0.2, 10**6, 400)["sr_adjusted"] < h100["sr_adjusted"]
    assert v.haircut_sharpe(0.1, 100, 400)["haircut"] == 1.0  # t=2 cannot survive 100 trials
    assert v.haircut_sharpe(0.1, 5, 10)["status"] == "INSUFFICIENT_EVIDENCE"


def test_effect_vs_benchmark():
    rng = np.random.default_rng(9)
    idx = pd.bdate_range("2022-01-03", periods=400)
    bench = pd.Series(rng.normal(0, 0.01, 400), index=idx)
    strat = bench + 0.003 + pd.Series(rng.normal(0, 0.005, 400), index=idx)
    r = v.effect_vs_benchmark(strat, bench)
    assert r["status"] == "OK" and r["ci_low"] > 0 and r["mean_diff"] == pytest.approx(0.003, abs=0.001)
    assert r["cohens_d"] > 0.3 and r["p_value_bootstrap"] < 0.05


def test_breakdowns_and_daily_returns():
    trades = make_trades([10, -4, 6, 8, -2, 5] * 15, start="2022-06-01", every_hours=72)
    y = v.yearly_breakdown(trades)
    assert y["n_years"] >= 2 and sum(r["n_trades"] for r in y["years"].values()) == 90
    labels = {pd.Timestamp(t["entry_time"]).strftime("%Y-%m-%d"): ("trend" if i % 2 else "range")
              for i, t in enumerate(trades)}
    rb = v.regime_breakdown(trades, labels)["regimes"]
    assert set(rb) == {"trend", "range"} and rb["trend"]["n_trades"] + rb["range"]["n_trades"] == 90
    dr = v.daily_returns_from_trades(trades, 10_000)
    assert dr.iloc[0] == pytest.approx(trades[0]["pnl"] / 10_000)
    assert len(dr) >= 90


# ----------------------------------------------------------------- scorecard
GOOD = dict(n_trades=400, net_profit=5000, profit_factor=1.6, max_drawdown_pct=12, psr=0.99,
            dsr=0.97, n_trials=20, pbo=0.2, wfe=0.8, oos_net_profit=1500, oos_n_trades=120,
            oos_profitable_windows_frac=0.8, cost_stress_spread50_net=3000,
            parameter_stability_frac_positive=0.9, net_after_drop_best=3500,
            yearly_positive_frac=0.8, n_years=4)


def test_scorecard_all_pass_is_forward_eligible():
    sc = v.scorecard(GOOD)
    assert sc["verdict"] == "FORWARD_ELIGIBLE"
    assert all(c["status"] == "PASS" for c in sc["checks"])
    assert {c["name"] for c in sc["checks"]} >= {"sample_size", "psr", "dsr", "pbo", "cost_stress", "drop_best_trades"}
    assert "kans op toekomstige winst" in sc["disclaimer_nl"]


def test_scorecard_overfitted_case_not_candidate():
    ev = dict(n_trades=300, net_profit=9000, profit_factor=2.4, max_drawdown_pct=8, psr=0.999,
              dsr=0.15, n_trials=500, pbo=0.8, wfe=0.05, oos_net_profit=-400, oos_n_trades=60)
    sc = v.scorecard(ev)
    assert sc["verdict"] in ("REJECT", "NEEDS_MORE_DATA")
    assert v.VERDICTS.index(sc["verdict"]) < v.VERDICTS.index("RESEARCH_CANDIDATE")
    ev2 = dict(ev)
    ev2.pop("dsr")
    assert v.scorecard(ev2)["verdict"] == "REJECT"  # PBO + WFE + OOS fail


def test_scorecard_tiny_sample_needs_more_data():
    ev = dict(GOOD, n_trades=12, psr={"value": None, "status": "INSUFFICIENT_EVIDENCE"},
              dsr={"value": None, "status": "INSUFFICIENT_EVIDENCE"})
    sc = v.scorecard(ev)
    assert sc["verdict"] == "NEEDS_MORE_DATA"
    assert {c["name"]: c["status"] for c in sc["checks"]}["sample_size"] == "INSUFFICIENT_EVIDENCE"


def test_scorecard_missing_oos_capped_at_research_candidate():
    ev = {k: x for k, x in GOOD.items() if k not in ("wfe", "oos_net_profit", "oos_n_trades", "oos_profitable_windows_frac")}
    sc = v.scorecard(ev)
    st = {c["name"]: c["status"] for c in sc["checks"]}
    assert st["walk_forward_efficiency"] == "NOT_TESTED" and st["oos_profitability"] == "NOT_TESTED"
    assert v.VERDICTS.index(sc["verdict"]) <= v.VERDICTS.index("RESEARCH_CANDIDATE")
    assert sc["verdict"] == "RESEARCH_CANDIDATE"
    # missing anything else also caps the verdict
    assert v.scorecard({k: x for k, x in GOOD.items() if k != "pbo"})["verdict"] == "RESEARCH_CANDIDATE"


def test_scorecard_hard_fail_net_or_dsr_rejects_and_thresholds_configurable():
    assert v.scorecard(dict(GOOD, net_profit=-1))["verdict"] == "REJECT"
    assert v.scorecard(dict(GOOD, dsr=0.4))["verdict"] == "REJECT"
    loose = v.scorecard(dict(GOOD, dsr=0.4), thresholds={"min_dsr": 0.3})
    assert loose["verdict"] == "FORWARD_ELIGIBLE"
    assert v.scorecard({})["verdict"] == "NEEDS_MORE_DATA"


def test_scorecard_unwraps_engine_dicts():
    wf = v.walk_forward(fake_backtest, {"x": [4, 5, 6]}, "2023-01-02", "2024-01-01", 120, 60, 60)
    cs = v.cost_stress(fake_backtest, {"x": 5}, "2023-01-02", "2023-07-01")
    ev = dict(GOOD, walk_forward=wf, cost_stress=cs)
    for k in ("wfe", "oos_net_profit", "oos_n_trades", "oos_profitable_windows_frac", "cost_stress_spread50_net"):
        ev.pop(k)
    st = {c["name"]: c["status"] for c in v.scorecard(ev)["checks"]}
    assert st["walk_forward_efficiency"] == "PASS" and st["cost_stress"] == "PASS"


def test_validation_report_nl_sections():
    trades = make_trades(np.random.default_rng(3).normal(3, 20, 150))
    ev = dict(GOOD, yearly_breakdown=v.yearly_breakdown(trades), monte_carlo=v.monte_carlo(trades, 10_000, n=100))
    sc = v.scorecard(ev)
    md = v.validation_report_nl(sc, ev)
    for h in ("Wat is aangetoond?", "Wat is onzeker?", "Waar verdient de strategie geld?", "Wanneer verliest zij?",
              "Kostengevoeligheid?", "Hoeveel hypotheses geprobeerd?", "Genoeg OOS-data?", "Parameters stabiel?",
              "Statistisch overtuigend?", "Open risico's"):
        assert h in md
    assert sc["verdict"] in md


def test_scorecard_provenance_caps_forward_eligible():
    unknown = v.scorecard({**GOOD, "research_history": {"external_trials": None}})
    assert unknown["verdict"] == "OOS_PASSED"
    assert {c["name"]: c["status"] for c in unknown["checks"]}["selection_history"] == "INSUFFICIENT_EVIDENCE"
    peeked = v.scorecard({**GOOD, "holdout": {"same_strategy_exposures": 3, "untouched": False}})
    assert peeked["verdict"] == "OOS_PASSED"
    clean = v.scorecard({**GOOD, "research_history": {"external_trials": 3436},
                         "holdout": {"same_strategy_exposures": 0}})
    assert clean["verdict"] == "FORWARD_ELIGIBLE"
    rejected = v.scorecard({**GOOD, "net_profit": -10, "research_history": {"external_trials": None}})
    assert rejected["verdict"] == "REJECT"
