"""V10 statistics: DSR inputs (N, effective N, V, T_eff), nested walk-forward and robustness (synthetic data only)."""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from strategy_lab.research import effective_trials as et  # noqa: E402
from strategy_lab.research import nested_wf, robustness, validation  # noqa: E402

scipy_stats = pytest.importorskip("scipy.stats")


# ------------------------------------------------------------------------------------------------ DSR / PSR
def test_dsr_reproduces_bailey_lopez_de_prado_2014_worked_example():
    # BLdP (2014) numerical example: annual SR 2.5, 5 years daily (T=1250), skew -3, kurt 10, N=100, V = 0.5/250
    V = 0.5 / 250
    sr0 = et.expected_max_sr(100, V)
    assert sr0 == pytest.approx(0.1132, abs=5e-4)
    sr = 2.5 / math.sqrt(250)
    dsr = et.psr(sr, 1250, -3.0, 10.0, sr0)
    assert dsr == pytest.approx(0.90, abs=0.005)
    assert validation.expected_max_sr(100, V) == pytest.approx(sr0, rel=1e-12)


def test_psr_matches_independent_scipy_reference():
    rng = np.random.default_rng(3)
    r = rng.standard_t(5, 800) * 0.01 + 0.0008
    sr = r.mean() / r.std(ddof=1)
    z = (r - r.mean()) / r.std(ddof=0)
    g3, g4 = scipy_stats.skew(r), scipy_stats.kurtosis(r, fisher=False)
    assert (z ** 3).mean() == pytest.approx(g3, rel=1e-9)
    ref = scipy_stats.norm.cdf((sr - 0.01) * math.sqrt(len(r) - 1) / math.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2))
    assert et.psr(sr, len(r), g3, g4, 0.01) == pytest.approx(ref, abs=1e-12)
    assert validation.probabilistic_sharpe_ratio(r, 0.01)["value"] == pytest.approx(ref, abs=1e-12)


def test_expected_max_sr_close_to_monte_carlo_of_independent_nulls():
    rng = np.random.default_rng(0)
    for N in (10, 100, 1000):
        mc = rng.standard_normal((4000, N)).max(axis=1).mean()
        assert et.expected_max_sr(N, 1.0) == pytest.approx(mc, rel=0.03)
    assert et.expected_max_sr(1, 1.0) == 0.0
    assert 0 < et.expected_max_sr(1.5, 1.0) < et.expected_max_sr(2, 1.0)


def test_min_track_record_length_closed_form():
    rng = np.random.default_rng(1)
    r = rng.normal(0.001, 0.01, 2000)
    m = validation._moments(r)
    out = validation.min_track_record_length(r, 0.0, 0.95)
    exp = 1 + (1 - m["skew"] * m["sr"] + (m["kurt"] - 1) / 4 * m["sr"] ** 2) * (1.6448536269514722 / m["sr"]) ** 2
    assert out["value"] == pytest.approx(exp, rel=1e-9)


# ------------------------------------------------------------------------------------------------ V and N
def test_variance_rebuilt_for_test_horizon_removes_short_window_noise():
    rng = np.random.default_rng(2)
    # 400 skill-less trials: 300 on 60-day windows (very noisy), 100 on 1000 days
    T = np.array([60] * 300 + [1000] * 100)
    srs = rng.standard_normal(len(T)) / np.sqrt(T)
    naive = float(np.var(srs, ddof=1))
    v = et.trial_variance(srs, T, 1500, min_T=1)
    assert v["status"] == "OK"
    assert v["V"] == pytest.approx(1 / 1500, rel=0.6)
    assert naive > 5 * v["V"]
    # trials with unknown or short horizon are excluded by default
    v2 = et.trial_variance(srs, np.where(T == 60, np.nan, T), 1500)
    assert v2["n_used"] == 100 and v2["n_excluded_short_or_unknown_T"] == 300


def test_v9_style_heterogeneous_variance_kills_a_strong_strategy_but_v10_does_not():
    rng = np.random.default_rng(4)
    daily = rng.normal(0.0016, 0.01, 1500)          # ~2.5 annualised over 6 years
    # V9: variance of annualised Sharpe of heterogeneous ledger rows / 252
    ledger_sr_ann = np.concatenate([rng.uniform(-16, 4, 400), rng.normal(0, 1, 1000)])
    old = validation.deflated_sharpe_ratio(daily, 1800, float(np.var(ledger_sr_ann, ddof=1)) / 252)
    # V10: V from OOS evaluations on the same horizon (skill-less), effective N = raw here
    oos_srs = rng.standard_normal(30) / math.sqrt(1500)
    rows = [{"hypothesis_id": f"h{i}", "family": "f", "group": f"g{i % 200}", "sr": None, "T": None}
            for i in range(1800)]
    rows += [{"hypothesis_id": f"o{i}", "family": "f", "group": f"g{i}", "sr": s, "T": 1500, "stage": "L5"}
             for i, s in enumerate(oos_srs)]
    rec = et.multiple_testing_records(daily, None, family="f", rows=rows)
    assert old["value"] < 0.05
    assert rec["global"]["value"] > 0.95
    assert rec["global"]["V_detail"]["source"] == "validation_oos_evaluations"
    for k in ("observed_sr", "sr0", "raw_n", "effective_n", "V", "T", "T_eff", "skew", "kurtosis", "method",
              "limitations"):
        assert k in rec["global"]


def test_effective_n_eigen_and_onc_on_known_structures():
    assert et.effective_n_eigen(np.eye(7)) == pytest.approx(7)
    assert et.effective_n_eigen(np.ones((7, 7))) == pytest.approx(1)
    rng = np.random.default_rng(5)
    base = rng.standard_normal((500, 4))
    cols = [base[:, k] + 0.05 * rng.standard_normal(500) for k in range(4) for _ in range(5)]
    frame = pd.DataFrame(np.column_stack(cols))
    en = et.effective_n(frame)
    assert en["onc_k"] == 4
    assert 3.5 <= en["n_eff"] <= 6
    assert et.effective_n(pd.DataFrame(rng.standard_normal((500, 12))))["n_eff"] >= 10


def test_two_level_effective_n_and_gate_uses_global_without_double_penalty():
    groups = [{"key": "a", "n_trials": 20, "n_eff_within": 4.0}, {"key": "b", "n_trials": 10, "n_eff_within": None}]
    out = et.two_level_effective_n(groups, {"n_raw": 2, "n_eff": 1.0})
    assert out["within_ratio_default"] == pytest.approx(0.2)
    assert out["n_eff"] == pytest.approx(0.5 * (4 + 2))
    g = et.gate({"global": {"status": "OK", "value": 0.97}, "family": {"status": "OK", "value": 0.99}}, 0.95)
    assert g["status"] == "PASS" and g["scope"] == "global"
    g = et.gate({"global": {"status": "OK", "value": 0.90}, "family": {"status": "OK", "value": 0.99}}, 0.95)
    assert g["status"] == "FAIL"


def test_effective_obs_under_autocorrelation():
    rng = np.random.default_rng(6)
    x = np.zeros(5000)
    e = rng.standard_normal(5000)
    for i in range(1, 5000):
        x[i] = 0.5 * x[i - 1] + e[i]
    eo = et.effective_obs(x)
    assert 1.8 < eo["inflation"] < 3.2 and eo["T_eff"] < 3000
    assert et.effective_obs(rng.standard_normal(5000))["inflation"] < 1.15


def test_pbo_known_values():
    rng = np.random.default_rng(7)
    good = rng.normal(0.004, 0.01, (400, 1))
    noise = rng.normal(0, 0.01, (400, 9))
    assert validation.pbo_cscv(np.hstack([good, noise]), n_splits=8)["pbo"] < 0.1
    pure = validation.pbo_cscv(rng.normal(0, 0.01, (400, 10)), n_splits=8)["pbo"]
    assert 0.2 < pure < 0.8


# ------------------------------------------------------------------------------------------------ nested WF
def _trades_from_signal(idx, edge, rng, start, end, hold=3):
    out = []
    for t in idx[(idx >= pd.Timestamp(start)) & (idx < pd.Timestamp(end))][::5]:
        pnl = float(rng.normal(edge, 10))
        out.append({"entry_time": t.isoformat(), "exit_time": (t + pd.Timedelta(hours=hold)).isoformat(),
                    "side": "long", "entry_price": 100.0, "exit_price": 100.0 + pnl, "qty": 1.0, "pnl": pnl,
                    "bars_held": hold})
    return out


def test_dependency_gap_from_spec_and_trades():
    g = nested_wf.dependency_gap({"time_exit_bars": 48}, "M15", lookback_bars=200)
    assert g["max_holding_bars"] == 48 and g["purge_days"] == 2
    assert g["embargo_days"] == math.ceil((48 + 200) * 15 / 1440 * 7 / 5) + 1
    g2 = nested_wf.dependency_gap({}, "H1", trades=[{"bars_held": 40}], lookback_bars=50)
    assert g2["max_holding_bars"] == 60 and g2["holding_source"] == "observed_max_bars_held"


def test_nested_wf_windows_purged_and_never_read_outside_range():
    idx = pd.date_range("2015-01-01", "2021-01-01", freq="1h")
    calls = []

    def bt(cfg, s, e):
        calls.append((s, e))
        rng = np.random.default_rng(abs(hash(str(cfg["params"]))) % 2 ** 32)
        return {"trades": _trades_from_signal(idx, cfg["params"]["edge"], rng, s, e)}

    cfgs = [{"variant": "base", "params": {"edge": e}} for e in (0.0, 2.0, 4.0)]
    gap = {"purge_days": 3, "embargo_days": 5}
    for mode in ("rolling", "expanding"):
        res = nested_wf.nested_walk_forward(bt, cfgs, "2015-01-01", "2021-01-01", train_days=730, test_days=182,
                                            mode=mode, gap=gap, capital=10000, evaluate="window")
        assert res["n_windows"] >= 6 and res["status"] == "OK"
        for w in res["windows"]:
            assert pd.Timestamp(w["train"][1]) + pd.Timedelta(days=3) <= pd.Timestamp(w["test"][0])
        assert max(pd.Timestamp(e) for _, e in calls) <= pd.Timestamp("2021-01-01")
        assert res["profitable_oos_windows"] >= res["n_windows_ok"] // 2
        assert {"wfe", "wfe_pooled", "degradation", "single_window_dependence", "worst_oos_window_dd_pct"} <= set(res)
        assert all(w["params"]["edge"] > 0 for w in res["windows"] if w["status"] == "OK")


def test_nested_wf_slice_mode_one_backtest_per_config_and_purges_overlapping_trades():
    idx = pd.date_range("2016-01-01", "2020-01-01", freq="1h")
    n_calls = []

    def bt(cfg, s, e):
        n_calls.append(1)
        return {"trades": _trades_from_signal(idx, 1.0, np.random.default_rng(1), s, e, hold=24 * 6)}

    res = nested_wf.nested_walk_forward(bt, [{"params": {"a": 1}}, {"params": {"a": 2}}], "2016-01-01", "2020-01-01",
                                        train_days=500, test_days=180, gap={"purge_days": 7, "embargo_days": 7})
    assert len(n_calls) == 2
    df = nested_wf._frame(bt({"params": {}}, "2016-01-01", "2020-01-01")["trades"])
    tr = nested_wf.slice_trades(df, "2016-01-01", "2017-01-01", purge_until="2017-01-01")
    assert all(pd.Timestamp(t["exit_time"]) < pd.Timestamp("2017-01-01", tz="UTC") for t in tr)
    assert res["n_windows"] >= 3


def test_single_window_dependence_flag():
    idx = pd.date_range("2016-01-01", "2020-01-01", freq="1h")

    def bt(cfg, s, e):
        out = _trades_from_signal(idx, -0.5, np.random.default_rng(2), s, e)
        out.append({"entry_time": "2019-03-01T10:00:00", "exit_time": "2019-03-01T12:00:00", "side": "long",
                    "entry_price": 1.0, "exit_price": 2.0, "qty": 1.0, "pnl": 50000.0, "bars_held": 2})
        return {"trades": out}

    res = nested_wf.nested_walk_forward(bt, [{"params": {}}], "2016-01-01", "2020-01-01", train_days=500,
                                        test_days=180, min_trades=5)
    assert res["single_window_dependence"]["flag"] is True


# ------------------------------------------------------------------------------------------------ robustness
def _bars(n=4000, seed=0, spread=0.02):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2018-01-01", periods=n, freq="1h", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    op = np.concatenate([[100.0], close[:-1]])
    bid = pd.DataFrame({"open": op, "high": np.maximum(op, close) + 0.05, "low": np.minimum(op, close) - 0.05,
                        "close": close}, index=idx)
    ask = bid + spread
    return bid, ask


def _informed_trades(bid, ask, hold=5, every=20, lookahead=True):
    out = []
    o = bid["open"].to_numpy()
    for i in range(0, len(bid) - hold - 1, every):
        side = ("long" if o[i + hold] > o[i] else "short") if lookahead else "long"
        e = ask["open"].iloc[i] if side == "long" else bid["open"].iloc[i]
        x = bid["open"].iloc[i + hold] if side == "long" else ask["open"].iloc[i + hold]
        pnl = (x - e) * (1 if side == "long" else -1) * 10
        out.append({"entry_time": bid.index[i].isoformat(), "exit_time": bid.index[i + hold].isoformat(),
                    "side": side, "entry_price": e, "exit_price": x, "qty": 10.0, "pnl": pnl, "costs": 0.0,
                    "bars_held": hold})
    return out


def test_random_entry_null_separates_informed_from_uninformed():
    bid, ask = _bars()
    good = robustness.random_entry_null(_informed_trades(bid, ask), bid, ask, n=200)
    assert good["p_value"] < 0.01
    bad = robustness.random_entry_null(_informed_trades(bid, ask, lookahead=False), bid, ask, n=200)
    assert bad["p_value"] > 0.05


def test_cost_mc_timing_adverse_and_effective_obs():
    bid, ask = _bars()
    tr = _informed_trades(bid, ask)
    sp = robustness.spread_at(bid, ask, pd.to_datetime([t["entry_time"] for t in tr]).tz_localize(None))
    assert np.allclose(sp, 0.02)
    mc = robustness.cost_monte_carlo(tr, spread_points=sp, n=300, spread_mult_mean=2.0)
    assert mc["status"] == "OK" and mc["net_p50"] < mc["base_net"]
    assert robustness.cost_monte_carlo(tr, spread_points=sp, n=300, spread_mult_mean=1.0, spread_mult_cv=1e-6
                                       )["net_p50"] == pytest.approx(mc["base_net"], rel=1e-3)
    tm = robustness.timing_monte_carlo(tr, bid, ask, n=100, max_shift_bars=2)
    assert tm["base_net_repriced"] == pytest.approx(sum(t["pnl"] for t in tr), rel=1e-6)
    assert tm["net_p50"] < tm["base_net_repriced"]
    af = robustness.adverse_fill(tr, bid, ask, 0.25)
    assert af["net"] < af["base_net"]
    daily = validation.daily_returns_from_trades(tr, 10000)
    eo = robustness.effective_independent_obs(daily, tr)
    assert eo["effective_obs"] > 60 and eo["avg_concurrency"] <= 1.01


def test_parameter_neighbourhood_plateau_vs_isolated_peak():
    space = {"a": {"type": "int", "min": 1, "max": 50, "step": 1}, "b": {"type": "float", "min": 0.5, "max": 5,
                                                                          "step": 0.5},
             "mode": {"choices": ["x", "y"], "type": "str"}, "start_hour": {"type": "int", "min": 0, "max": 23}}
    base = {"a": 20, "b": 2.0, "mode": "x", "start_hour": 8}

    def mk(f):
        def bt(p, s, e):
            return {"trades": [{"entry_time": "2020-01-01T00:00:00", "exit_time": "2020-01-02T00:00:00",
                                "pnl": f(p)}]}
        return bt

    flat = robustness.parameter_neighbourhood(mk(lambda p: 100 - abs(p["a"] - 20) - 5 * abs(p["b"] - 2)), base, space,
                                              "2020", "2021", 10000)
    assert flat["classification"] == "BROAD_PLATEAU" and flat["heatmap"] is not None
    units = {n["param"]: n["unit"] for n in flat["neighbours"]}
    assert units == {"a": "int", "b": "float", "mode": "categorical", "start_hour": "time"}
    peak = robustness.parameter_neighbourhood(mk(lambda p: 100 if p == base else -20), base, space, "2020", "2021",
                                              10000)
    assert peak["classification"] == "ISOLATED_PEAK"


def test_causal_regimes_do_not_change_when_the_future_changes():
    bid, ask = _bars(n=24 * 400, seed=3)
    lab = robustness.causal_regimes(bid, ask)
    cut = bid.index[24 * 300]
    bid2 = bid.copy()
    bid2.loc[bid2.index >= cut, ["open", "high", "low", "close"]] *= 3.0
    ask2 = bid2 + 0.02
    lab2 = robustness.causal_regimes(bid2, ask2)
    day = cut.strftime("%Y-%m-%d")
    past = [d for d in lab if d <= day]
    assert past and all(lab[d] == lab2[d] for d in past)


def test_concentration_flags_few_day_profit_and_frequency_aware_k():
    rng = np.random.default_rng(8)
    days = pd.bdate_range("2019-01-01", periods=300)
    tr = [{"entry_time": d.isoformat(), "exit_time": (d + pd.Timedelta(hours=2)).isoformat(),
           "pnl": float(rng.normal(-2, 5))} for d in days]
    for d in days[[10, 100, 200]]:
        tr.append({"entry_time": d.isoformat(), "exit_time": (d + pd.Timedelta(hours=3)).isoformat(), "pnl": 900.0})
    c = robustness.concentration(tr, 10000, "2019-01-01", "2020-03-01")
    assert "profit_in_few_days" in c["flags"] and c["k_days_required"] == max(3, math.ceil(0.02 * c["active_days"]))
    ok = robustness.concentration([dict(t, pnl=abs(t["pnl"]) + 1) for t in tr[:300]], 10000, "2019-01-01",
                                  "2020-03-01")
    assert "profit_in_few_days" not in ok["flags"]


def test_regime_stability_and_verdict_classes():
    tr = [{"entry_time": f"2020-0{m}-1{d}T09:00:00", "exit_time": f"2020-0{m}-1{d}T12:00:00", "pnl": 10.0}
          for m in range(1, 10) for d in range(0, 9)]
    labels = {t["entry_time"][:10]: {"trend": "trend" if i % 2 else "range", "vol": "vol_hoog", "direction": "op"}
              for i, t in enumerate(tr)}
    rs = robustness.regime_stability(tr, labels)
    assert rs["status"] == "OK" and not rs["unstable_dimensions"]
    eo = {"effective_obs": 500}
    cis = {"daily_mean": {"ci_low_min": 0.001}}
    good = robustness.verdict(oos_net=100, eff_obs=eo, cis=cis, null={"status": "OK", "p_value": 0.01},
                              baselines_beaten=True, cost_mc={"status": "OK", "prob_loss": 0.01},
                              execution={"status": "OK", "all_positive": True, "n": 5, "n_positive": 5,
                                         "scenarios": {"entry_delay_1": {"net_profit": 5.0}}},
                              neighbourhood={"classification": "BROAD_PLATEAU"}, conc={"flags": []}, regimes=rs)
    assert good["verdict"] == "SUPPORTED_UNDER_ASSUMPTIONS"
    unc = robustness.verdict(oos_net=100, eff_obs=eo, cis={"daily_mean": {"ci_low_min": -0.001}},
                             null={"status": "OK", "p_value": 0.07}, baselines_beaten=True, cost_mc=None,
                             execution=None, neighbourhood=None, conc=None, regimes=None)
    assert unc["verdict"] == "POSITIVE_UNCERTAIN"
    assert robustness.verdict(oos_net=-1, eff_obs=eo, cis=cis, null=None, baselines_beaten=None, cost_mc=None,
                              execution=None, neighbourhood=None, conc=None, regimes=None)["verdict"] == "NO_EVIDENCE"
    assert robustness.verdict(oos_net=100, eff_obs={"effective_obs": 10}, cis=cis, null=None, baselines_beaten=None,
                              cost_mc=None, execution=None, neighbourhood=None, conc=None,
                              regimes=None)["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_similarity_flags_near_identical_candidates():
    rng = np.random.default_rng(9)
    idx = pd.bdate_range("2019-01-01", periods=300)
    a = pd.Series(rng.normal(0, 1, 300), index=idx)
    s = robustness.similarity({"daily": a, "entry_days": set(idx[:100]), "blocks": ["x", "y"], "market": "M"},
                              {"daily": a + rng.normal(0, 0.1, 300), "entry_days": set(idx[:100]),
                               "blocks": ["x", "y"], "market": "M"})
    assert s["near_identical"] and s["returns_corr"] > 0.9
    d = robustness.similarity({"daily": a, "entry_days": set(idx[:50]), "blocks": ["x"], "market": "M"},
                              {"daily": pd.Series(rng.normal(0, 1, 300), index=idx), "entry_days": set(idx[200:]),
                               "blocks": ["z"], "market": "N"})
    assert not d["near_identical"]


def test_spec_variants_simplify():
    spec = {"session": {"start": "08:00"}, "trailing": {"type": "atr"}, "target": {"type": "atr"},
            "time_exit_bars": 10}
    names = [n for n, _ in robustness.spec_variants(spec)]
    assert names == ["zonder_sessiefilter", "eenvoudigere_exits", "vaste_2R_exit"]


def test_gate_uses_null_variance_so_skill_dispersion_does_not_block_a_strong_strategy():
    rng = np.random.default_rng(11)
    T = 1300
    daily = rng.normal(0.0018, 0.01, T)                  # ~2.9 annualised
    # 17 trials with GENUINELY different true Sharpe ratios (skilled and cost-dominated variants)
    true = np.linspace(-0.15, 0.2, 17)
    rows = [{"hypothesis_id": f"t{i}", "family": "f", "group": f"t{i}", "stage": "L5", "T": T,
             "sr": float(s + rng.standard_normal() / math.sqrt(T))} for i, s in enumerate(true)]
    rec = et.multiple_testing_records(daily, None, family="f", rows=rows)["global"]
    assert rec["V"] == pytest.approx(1 / rec["T_eff"])
    assert rec["V_paper"] > 20 * rec["V_null"]
    assert rec["value"] > 0.95 > rec["value_paper_v"]
    # under the global null the two V's agree and nothing passes
    null_rows = [dict(r, sr=float(rng.standard_normal() / math.sqrt(T))) for r in rows]
    weak = rng.normal(0.0, 0.01, T)
    rn = et.multiple_testing_records(weak, None, family="f", rows=null_rows)["global"]
    assert rn["value"] < 0.95 and rn["V_paper"] == pytest.approx(rn["V_null"], rel=1.0)
