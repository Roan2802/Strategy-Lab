"""V10.2 pipeline-level simulation: every promotion gate jointly (L5 gates + L6 NC-DSR) on synthetic daily returns.

The decision code is the FACTORY'S OWN: ``nested_wf.nested_walk_forward`` (rolling + expanding), ``factory.metrics_of /
quality_checks / dual_positive_gate / performance_cis / l5_decision``, ``validation.scorecard / pbo_cscv /
probabilistic_sharpe_ratio / bootstrap_confidence_interval``, ``robustness.verdict / concentration / regime_stability /
bootstrap_cis / effective_independent_obs``, ``distribution.analyse``, ``effective_trials.dsr_record / gate /
two_level_effective_n / effective_n``. Only the inputs are synthetic. Surrogates (documented in
STATISTICAL_SELECTION_AUDIT.md): parameter neighbours (correlated series), cost / execution stress (per-trade cost
shifts), random-entry null (normal approximation of the gross-edge test), no baseline / simpler-variant tests,
regime labels, a hash-free search funnel (L2/L3 single-config screen, L4 optimisation over C configs, L5 top-K).

    C:/Python311/python.exe evidence/v102/stats/pipeline_sim.py [--quick] [--only A,B] [--reps N] [--procs N]
Synthetic data only; never the sealed 2024+ period.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from strategy_lab.research import distribution, factory, nested_wf, robustness, validation  # noqa: E402
from strategy_lab.research import effective_trials as et  # noqa: E402

OUT = Path(__file__).resolve().parent
CAP = 10_000.0
SIG = 0.005                     # one R = 0.5% of capital (factory default risk_pct), pnl sd per trade ~ 1R
KAPPA = 0.10 * SIG              # round-trip cost per trade: 0.10 R
SPREAD_SHARE = 0.7              # share of the cost that is spread (+50% spread scenario)
ANN = math.sqrt(252)

DEFAULT = dict(pbo_all=False, plateau=False, keep_daily=False, G=120, C=24, K=4, T_dev=1500, T_val=1500, p_act=0.5, edge=0.0, null_net=0.0, rho_m=0.0, rho_c=0.85,
               regime=False, theta3=0.8, theta4=1.0, max_l5=6, H=5, profile="regelmatig", n_nb=8, rho_nb=0.8,
               boot_n=250, skilled=True, dist="normal")


def wilson(k, n, z=1.96):
    if not n:
        return {"k": k, "n": n, "rate": None, "lo": None, "hi": None}
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return {"k": int(k), "n": int(n), "rate": p, "lo": max(0.0, c - h), "hi": min(1.0, c + h)}


# ------------------------------------------------------------------------------------------------ data
def _innov(rng, shape, dist):
    if dist == "normal":
        return rng.standard_normal(shape)
    df = int(dist[1:])
    return rng.standard_t(df, shape) / math.sqrt(df / (df - 2.0))


def mean_per_active_day(sr_ann, p):
    """Mean P&L per active day (fraction of capital) giving a daily-series annualised Sharpe ``sr_ann``."""
    return sr_ann / ANN * SIG / math.sqrt(p)


def regime_masks(T):
    blk = (np.arange(T) // 250) % 2
    return blk == 0, np.where(blk == 1, 2.0, 1.0)      # A (edge, low vol) blocks, volatility multiplier


def sharpe_ann(r, axis=-1):
    sd = r.std(axis=axis, ddof=1)
    return np.where(sd > 0, r.mean(axis=axis) / np.where(sd > 0, sd, 1.0), 0.0) * ANN


def make_series(P, e, mask, mu, A, vol):
    """r = mask * (SIG * vol * e + mu on regime-A days). ``e`` unit-variance, ``mu`` mean per active day."""
    edge_day = A if P["regime"] else np.ones_like(A)
    v = vol if P["regime"] else 1.0
    return mask * (SIG * v * e + np.asarray(mu)[..., None] * edge_day)


def to_trades(r, dates):
    nz = np.nonzero(r)[0]
    d = dates[nz].strftime("%Y-%m-%d")
    pnl = r[nz] * CAP
    return [{"entry_time": f"{x}T09:00:00+00:00", "exit_time": f"{x}T17:00:00+00:00", "pnl": float(p), "qty": 1.0,
             "costs": KAPPA * CAP, "side": "long"} for x, p in zip(d, pnl)]


def labels_for(dates, rng):
    blk = (np.arange(len(dates)) // 250) % 2
    tr = rng.integers(0, 2, len(dates) // 60 + 1)[np.arange(len(dates)) // 60]
    return {d.strftime("%Y-%m-%d"): {"vol": "A" if b == 0 else "B", "trend": "t" if t else "r"}
            for d, b, t in zip(dates, blk, tr)}


# ------------------------------------------------------------------------------------------------ fast nested-WF I/O
_REC_CACHE: dict = {}


def _records(df):
    """Per cached trade frame: python records (ISO strings, as ``nested_wf.slice_trades`` returns) + entry times."""
    key = id(df)
    if key not in _REC_CACHE:
        recs = df.to_dict("records")
        for r in recs:
            r["entry_time"] = pd.Timestamp(r["entry_time"]).tz_localize("UTC").isoformat()
            r["exit_time"] = pd.Timestamp(r["exit_time"]).tz_localize("UTC").isoformat()
        _REC_CACHE[key] = (df, recs, df["entry_time"].to_numpy(), df["exit_time"].to_numpy())
    return _REC_CACHE[key]


def fast_slice_trades(df, start, end, purge_until=None):
    """Same selection as ``nested_wf.slice_trades`` without per-call DataFrame work."""
    if df.empty:
        return []
    _, recs, ent, ext = _records(df)
    i0 = int(np.searchsorted(ent, np.datetime64(pd.Timestamp(start)), side="left"))
    i1 = int(np.searchsorted(ent, np.datetime64(pd.Timestamp(end)), side="left"))
    if purge_until is None:
        return recs[i0:i1]
    cut = np.datetime64(pd.Timestamp(purge_until))
    return [recs[i] for i in range(i0, i1) if ext[i] < cut]


def fast_window_metrics(trades, capital, start, end):
    """Same numbers as ``nested_wf.window_metrics`` for one-trade-per-business-day books (as simulated here)."""
    n = len(trades)
    days = max((pd.Timestamp(end) - pd.Timestamp(start)).days, 1)
    if n == 0:
        return {"trades": 0, "net_profit": 0.0, "profit_factor": 0.0, "win_rate": 0.0, "expectancy": 0.0,
                "max_drawdown_pct": 0.0, "sharpe_daily": 0.0, "days": days, "net_per_day": 0.0, "n_days": 0}
    p = np.fromiter((t["pnl"] for t in trades), float, n)
    wins, losses = p[p > 0].sum(), -p[p < 0].sum()
    pf = wins / losses if losses > 0 else (validation.PF_CAP if wins > 0 else 0.0)
    e = capital + np.concatenate([[0.0], np.cumsum(p)])
    peak = np.maximum.accumulate(e)
    dd = float((((peak - e) / np.where(peak > 0, peak, 1)).max()) * 100)
    d0, d1 = trades[0]["exit_time"][:10], trades[-1]["exit_time"][:10]
    nd = int(np.busday_count(d0, d1)) + 1
    sharpe = 0.0
    if nd > 1:
        mean = p.sum() / nd
        var = (float((p * p).sum()) - nd * mean * mean) / (nd - 1)
        sharpe = float(mean / math.sqrt(var) * math.sqrt(252)) if var > 0 else 0.0
    net = float(p.sum())
    return {"trades": n, "net_profit": net, "profit_factor": float(min(pf, validation.PF_CAP)),
            "win_rate": float((p > 0).mean()), "expectancy": net / n, "max_drawdown_pct": dd, "sharpe_daily": sharpe,
            "days": days, "net_per_day": net / days, "n_days": nd}


class fast_nested:
    """Context manager: nested_wf uses the fast slice / metric functions (verified equal in test_v102_stats_sim)."""

    def __enter__(self):
        self._o = (nested_wf.slice_trades, nested_wf.window_metrics)
        nested_wf.slice_trades, nested_wf.window_metrics = fast_slice_trades, fast_window_metrics
        _REC_CACHE.clear()

    def __exit__(self, *a):
        nested_wf.slice_trades, nested_wf.window_metrics = self._o
        _REC_CACHE.clear()


# ------------------------------------------------------------------------------------------------ one search
def run_search(P: dict, seed: int) -> dict:
    """One factory-like search; returns every L5 candidate with all gate statuses and the pipeline decision."""
    rng = np.random.default_rng(seed)
    G, C, K, Td, Tv = P["G"], P["C"], P["K"], P["T_dev"], P["T_val"]
    T = Td + Tv
    dates = pd.bdate_range("2008-01-07", periods=T)
    A, vol = regime_masks(T)
    q = factory.QUALITY_PROFILES[P["profile"]]
    p = P["p_act"]
    edge, null_net = P["edge"], P["null_net"]
    mu_null = mean_per_active_day(null_net, p)
    # lineage 0 is skilled when edge > 0 and P["skilled"]
    M = _innov(rng, (T,), P["dist"])
    F = _innov(rng, (G, T), P["dist"])
    I0 = _innov(rng, (G, T), P["dist"])
    mask = rng.random((G, T)) < p
    rm, rc = P["rho_m"], P["rho_c"]

    def mix(Fi, Ii):
        return math.sqrt(rm) * M + math.sqrt(1 - rm) * (math.sqrt(rc) * Fi + math.sqrt(1 - rc) * Ii)

    skilled = bool(P["skilled"] and edge > 0)
    mu0 = np.full(G, mu_null)
    if skilled:
        mu0[0] = mean_per_active_day(edge, p)
    cfg0 = np.stack([make_series(P, mix(F[i], I0[i]), mask[i], mu0[i], A, vol) for i in range(G)])
    dev_sr0 = sharpe_ann(cfg0[:, :Td])
    l3 = [i for i in range(G) if dev_sr0[i] >= P["theta3"] or (skilled and i == 0)]
    cand_rows = []
    optimised = {}
    for i in l3:
        # L4 optimisation: C configurations (random search over a correlated parameter space)
        Ii = _innov(rng, (C, T), P["dist"])
        if skilled and i == 0:
            decay = np.concatenate([[1.0], rng.uniform(0.5, 1.0, C - 1)])
            if P["plateau"]:        # parameter plateau: every configuration has the same true edge, no decay
                decay = np.ones(C)
            mu_i = mean_per_active_day(edge, p) * decay
        else:
            mu_i = np.full(C, mu_null)
        ser = np.stack([cfg0[i]] + [make_series(P, mix(F[i], Ii[j]), mask[i], mu_i[j + 1], A, vol)
                                    for j in range(C - 1)])
        optimised[i] = (ser, mu_i)
    # multiplicity inputs of the ledger (what the factory counts): L2 default config of every lineage + C trials of
    # every optimised lineage; within-lineage effective N from the L4 configuration series, between from L3 series
    groups, within = [], {}
    for i in range(G):
        if i in optimised:
            fr = pd.DataFrame(optimised[i][0][:, :Td].T)
            ne = et.effective_n(fr, seed=0)["n_eff"]
            within[i] = ne
            groups.append({"n_trials": C, "n_eff_within": ne})
        else:
            groups.append({"n_trials": 1, "n_eff_within": None})
    reps = {i: cfg0[i, :Td // 5 * 5].reshape(-1, 5).sum(axis=1) for i in l3}
    between = et.effective_n(pd.DataFrame(reps), seed=0) if len(reps) >= 2 else None
    en = et.two_level_effective_n(groups * int(P["H"]), between)
    # L4 screen and L5 candidates
    l4 = []
    for i, (ser, mu_i) in optimised.items():
        dev = sharpe_ann(ser[:, :Td])
        top = np.argsort(dev)[::-1][:K]
        reach = bool(dev[top[0]] >= P["theta4"])
        l4.append((i, top, dev, reach))
    natural = sorted([x for x in l4 if x[3]], key=lambda x: -x[2][x[1][0]])
    l5 = [x for x in natural if not (skilled and x[0] == 0)][:int(P["max_l5"])]
    sk = next((x for x in l4 if x[0] == 0), None) if skilled else None
    if sk is not None:
        l5.append(sk)
    out = {"n_lineages": G, "n_l3": len(l3), "n_l4": len(l4), "n_l5_natural": len(natural), "raw_n": en["n_raw"],
           "eff_n": en["n_eff"], "skilled": skilled,
           "skilled_reach_l4": bool(sk and sk[3]), "skilled_reach_l3_natural": bool(skilled and dev_sr0[0] >= P["theta3"]),
           "cands": []}
    lab = labels_for(dates, rng)
    for i, top, dev, reach in l5:
        ser, mu_i = optimised[i]
        pbo_R = ser[np.argsort(dev)[::-1]] if P["pbo_all"] else None
        rec = evaluate(P, rng, ser[top], mu_i[top], dates, lab, q, en, T_dev=Td, pbo_R=pbo_R)
        rec.update(lineage=int(i), skilled=bool(skilled and i == 0), reach_l4=bool(reach))
        out["cands"].append(rec)
    return out


# ------------------------------------------------------------------------------------------------ L5 + L6 for one candidate
def evaluate(P, rng, R, mu, dates, labels, q, en, *, T_dev, pbo_R=None):
    """R: (K, T) config series (dev + validation), sorted by development Sharpe; mu: their true mean per active day."""
    K, T = R.shape
    Td = T_dev
    d0, v0, v1 = dates[0], dates[Td], dates[-1]
    tr_by = [to_trades(R[j], dates) for j in range(K)]
    cfgs = [{"variant": "basis", "params": {"k": j}} for j in range(K)]

    def fn(cfg, s, e):
        return {"trades": tr_by[cfg["params"]["k"]]}
    train_roll = min(3 * 365, (v0 - d0).days)
    min_tr = max(5, int(q["min_trades_per_week"] * 26 * 0.4))
    common = dict(test_days=182, gap={"purge_days": 0, "embargo_days": 0}, objective="sharpe", min_trades=min_tr,
                  capital=CAP)
    with fast_nested():
        roll = nested_wf.nested_walk_forward(fn, cfgs, (v0 - pd.Timedelta(days=train_roll)).strftime("%Y-%m-%d"),
                                             v1.strftime("%Y-%m-%d"), train_days=train_roll, mode="rolling", **common)
        expd = nested_wf.nested_walk_forward(fn, cfgs, d0.strftime("%Y-%m-%d"), v1.strftime("%Y-%m-%d"),
                                             train_days=(v0 - d0).days, mode="expanding", **common)
    oos_trades, exp_trades = roll.pop("oos_trades", []), expd.pop("oos_trades", [])
    roll.pop("oos_equity", None)
    expd.pop("oos_equity", None)
    if not roll["windows"]:
        return {"error": "no_windows"}
    oos_start, oos_end = roll["windows"][0]["test"][0], roll["windows"][-1]["test"][1]
    om = factory.metrics_of(oos_trades, CAP, oos_start, oos_end)
    em = factory.metrics_of(exp_trades, CAP, oos_start, oos_end)
    last_ok = next((w for w in reversed(roll["windows"]) if w["status"] == "OK"), None)
    jc = ((last_ok or {}).get("config") or cfgs[0])["params"]["k"]
    rv = R[jc, Td:]
    n_val_trades = int((rv != 0).sum())
    # surrogate neighbours: series sharing rho_nb of the chosen configuration's noise, smooth true-mean decay
    u = (rv - rv.mean()) / (rv.std() or 1.0)
    sd = float(rv.std())
    nb_net = []
    for kk in range(P["n_nb"]):
        decay = 1.0 - 0.10 * (1 + kk // 2)
        xi = rng.standard_normal(len(rv))
        series = sd * (P["rho_nb"] * u + math.sqrt(1 - P["rho_nb"] ** 2) * xi) + mu[jc] * P["p_act"] * decay
        nb_net.append(float(series.sum() * CAP))
    vals = np.array(nb_net)
    base = float(rv.sum() * CAP)
    frac_pos = float((vals > 0).mean())
    rel = float(np.median(vals) / base) if base > 0 else None
    is_peak = base > 0 and vals.max() <= base and (rel is None or rel < 0.3 or frac_pos < 0.5)
    cls = ("NO_EDGE_AT_BASE" if base <= 0 else "BROAD_PLATEAU" if frac_pos >= 0.7 and (rel or 0) >= 0.5
           else "ISOLATED_PEAK" if is_peak else "MODERATE")
    neigh = {"status": "OK", "frac_positive": frac_pos, "classification": cls}
    stability = {"status": "OK", "frac_positive": frac_pos, "classification": cls}
    shift = lambda f: base - f * SPREAD_SHARE * KAPPA * CAP * n_val_trades          # noqa: E731
    cost = {"scenarios": {"spread_+50%": {"net_profit": shift(0.5)}}}
    oos_net = float(sum(t["pnl"] for t in oos_trades))
    n_o = len(oos_trades)
    gross = oos_net + n_o * KAPPA * CAP
    execution = {"status": "OK", "scenarios": {
        "entry_delay_1": {"net_profit": 0.9 * gross - n_o * KAPPA * CAP},
        "exit_delay_1": {"net_profit": 0.9 * gross - n_o * KAPPA * CAP},
        "spread_x1.5": {"net_profit": oos_net - 0.5 * SPREAD_SHARE * KAPPA * CAP * n_o},
        "slippage_+2pt": {"net_profit": oos_net - 0.05 * SIG * CAP * n_o}}}
    # random-entry null (normal approximation): random entries earn -cost per trade, sd ~ 1R per trade
    if n_o >= validation.MIN_OBS:
        z = (oos_net + n_o * KAPPA * CAP) / (math.sqrt(n_o) * SIG * CAP)
        null = {"status": "OK", "p_value": float(1 - validation.norm_cdf(z))}
    else:
        null = {"status": validation.INSUFFICIENT}
    daily = validation.daily_returns_from_trades(oos_trades, CAP)
    pnls = [t["pnl"] for t in oos_trades]
    nb = int(P["boot_n"])
    dist = distribution.analyse(oos_trades, CAP)
    conc = robustness.concentration(oos_trades, CAP, oos_start, oos_end)
    regimes = robustness.regime_stability(oos_trades, labels)
    cis = robustness.bootstrap_cis(daily.to_numpy(), pnls, n=nb, seed=1)
    eff_obs = robustness.effective_independent_obs(daily.to_numpy(), oos_trades)
    boot = validation.bootstrap_confidence_interval(pnls, np.mean, n=nb, block="auto", seed=1)
    perf = factory.performance_cis(pnls, n=nb, seed=1)
    psr = validation.probabilistic_sharpe_ratio(daily)
    db = validation.drop_best(oos_trades)
    yb = validation.yearly_breakdown(oos_trades)
    pbo_tr = tr_by if pbo_R is None else [to_trades(pbo_R[j], dates) for j in range(len(pbo_R))]
    # the factory's own PBO (preconditions, exclusions) over the validation period; index 0 = best on development
    l4p = {"rungs": [{"trials": [{"params": {"k": j}} for j in range(len(pbo_tr))]}], "best": {"params": {"k": 0}},
           "top_k": [{"k": j} for j in range(K)]}
    pbo = factory.pbo_over_l4(l4p, lambda prm: nested_wf.slice_trades(nested_wf._frame(pbo_tr[prm["k"]]), v0, v1), CAP,
                              val_range=(v0.strftime("%Y-%m-%d"), v1.strftime("%Y-%m-%d")), cost_fp="sim",
                              chosen_cost_fp="sim", max_pbo=q["max_pbo"])
    # multiplicity: global record at the search level (ledger counts incl. history factor H)
    dsr = et.dsr_record(daily.to_numpy(), n_raw=max(1, int(en["n_raw"])), n_eff=max(1.0, en["n_eff"]),
                        variance={"V": None, "status": "NULL_FLOOR_ONLY"}, scope="global")
    # development leg of the dual-positive rule: chosen configuration on the development period
    dev_tr = [t for t in tr_by[jc] if t["entry_time"][:10] < v0.strftime("%Y-%m-%d")]
    dev_m = factory.metrics_of(dev_tr, CAP, d0.strftime("%Y-%m-%d"), v0.strftime("%Y-%m-%d"))
    dual = factory.dual_positive_gate(dev_m, om)
    evidence = {"n_trades": om["trades"], "net_profit": om["net_profit"], "profit_factor": om["profit_factor"],
                "max_drawdown_pct": om["max_drawdown_pct"], "psr": psr, "dsr": dsr, "n_trials": dsr["raw_n"],
                "pbo_result": pbo, "walk_forward": roll, "cost_stress": cost, "parameter_stability": stability,
                "drop_best": db, "yearly": yb}
    th = {"deferred_checks": ["dsr", "psr"], "min_trades": q["min_oos_trades"], "min_profit_factor": q["min_pf_oos"],
          "max_drawdown_pct": q["max_dd_pct"], "max_pbo": q["max_pbo"], "min_wfe": q["min_wfe"],
          "min_dsr": q["min_dsr"], "min_psr": q["min_psr"], "min_param_stability": q["min_param_stability"],
          "min_oos_trades": q["min_oos_trades"], "pbo_review": True}
    sc = validation.scorecard(evidence, th)
    checks = factory.quality_checks(om, q, dist=dist, cost=cost, stability=stability, pbo=pbo, roll=roll, expd=em,
                                    boot=boot, oos_start=oos_start, oos_end=oos_end,
                                    pf_ci=perf["profit_factor"], rf_ci=perf["recovery_factor"])
    checks.append(factory.single_window_check(roll["single_window_dependence"]))
    checks.append(factory._chk("dual_positive", dual["status"], dual["dev_net"], 0.0, "dual"))
    ev = robustness.verdict(oos_net=om["net_profit"], eff_obs=eff_obs, cis=cis, null=null, baselines_beaten=None,
                            cost_mc=None, execution=execution, neighbourhood=neigh, conc=conc, regimes=regimes)
    dec = factory.l5_decision(checks, sc, None, ev)
    l5_clean = not dec["reasons"] and not dec["insufficient"]      # every L5 check but PBO-above-limit passes
    l5_review = bool(dec["review_required"])                       # PBO is the only open point: REVIEW_REQUIRED
    l5_pass = l5_clean and not l5_review                           # automatic pass (= the previous hard-PBO rule)
    gate = et.gate({"global": dsr, "family": dsr}, q["min_dsr"])
    obs = dsr.get("observed_sr")
    # gate flags: name -> "FAIL" | "INSUFFICIENT_EVIDENCE" (anything not PASS stops promotion)
    flags = {}
    for c in checks:
        if c["status"] != "PASS":
            flags["q:" + c["name"]] = c["status"]
    for c in sc["checks"]:
        if c["status"] != "PASS" and not c.get("deferred"):
            flags["s:" + c["name"]] = c["status"]
    if ev["verdict"] != "SUPPORTED_UNDER_ASSUMPTIONS":
        for r in ev["reasons"]:
            flags["e:" + _ev_tag(r)] = ev["verdict"]
    pbo_flag = pbo.get("pbo") is not None and pbo["pbo"] > q["max_pbo"]
    wfe_flag = roll.get("wfe") is None or roll["wfe"] < q["min_wfe"]
    exp_flag = em.get("net_profit", 0) <= 0
    l6_ok = gate["status"] == "PASS"
    rec = {"l5_pass": bool(l5_pass), "l5_clean": bool(l5_clean), "l5_review": l5_review, "l6_pass": l6_ok,
           "pass": bool(l5_pass and l6_ok), "pass_if_accepted": bool(l5_clean and l6_ok),
           "review_and_l6": bool(l5_review and l6_ok), "pbo_status": pbo.get("status"),
           "pbo_pre_ok": not pbo.get("precondition_failed"), "n_cfg": int(pbo.get("n_configs") or 0),
            "flags": flags, "pbo": pbo.get("pbo"), "pbo_flag": bool(pbo_flag), "wfe": roll.get("wfe"),
            "wfe_flag": bool(wfe_flag), "exp_flag": bool(exp_flag), "dsr": gate.get("value"),
            "dsr_paper": (dsr.get("variants") or {}).get("paper_dsr", {}).get("value"),
            "psr": (psr or {}).get("value"), "sr_ann": (obs or 0.0) * ANN, "oos_net": om["net_profit"],
            "oos_trades": om["trades"], "dev_net": dev_m["net_profit"], "sc_verdict": sc["verdict"],
            "ev_verdict": ev["verdict"], "n_reasons": len(dec["reasons"]), "n_insuff": len(dec["insufficient"]),
            "pbo_cols": int(pbo.get("n_eligible") or 0), "sr0_ann": dsr.get("sr0_annualized"), "n_eff": en["n_eff"],
            "raw_n": en["n_raw"]}
    if P["keep_daily"]:
        rec["daily"] = [float(x) for x in daily.to_numpy()]
    return rec


_EV_TAGS = (("bootstrap-CI", "ci_daily_mean"), ("willekeurige instap", "random_entry"), ("variabele kosten", "cost_mc"),
            ("uitvoeringsscenario", "execution"), ("eenvoudigere", "simpler"), ("parameteromgeving", "neighbourhood"),
            ("concentratie", "concentration"), ("regime", "regime"), ("OOS netto", "oos_net"),
            ("effectieve", "eff_obs"), ("baselines", "baseline"))


def _ev_tag(text):
    for k, v in _EV_TAGS:
        if k in text:
            return v
    return "other"


# ------------------------------------------------------------------------------------------------ campaigns
def _work(args):
    P, seed = args
    try:
        return run_search(P, seed)
    except Exception as exc:                    # never lose a campaign to one replication
        return {"error": f"{type(exc).__name__}: {exc}", "cands": []}


def run_cell(name, over, reps, procs, base_seed=0, seed_key=None):
    P = {**DEFAULT, **over}
    seeds = [(P, zlib.crc32(repr((seed_key or name, k, base_seed)).encode())) for k in range(reps)]
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=procs) as ex:
        res = list(ex.map(_work, seeds, chunksize=1))
    return {"name": name, "params": P, "reps": reps, "seconds": round(time.time() - t0, 1), "searches": res}


# scenario grid (pre-registered in STATISTICAL_SELECTION_AUDIT.md before the run)
def grid(quick=False):
    g = {}
    for e in (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0):
        g[f"edge|{e}"] = dict(edge=e)
    g["null|net_negative(-0.5)"] = dict(edge=0.0, null_net=-0.5)
    g["null|correlated_markets(rho0.3)"] = dict(edge=0.0, rho_m=0.3)
    g["null|correlated_markets(rho0.6)"] = dict(edge=0.0, rho_m=0.6)
    g["null|t3_tails"] = dict(edge=0.0, dist="t3")
    g["null|regime_switching"] = dict(edge=0.0, regime=True)
    g["null|short_sample(T_val750)"] = dict(edge=0.0, T_val=750)
    g["null|long_sample(T_val2500)"] = dict(edge=0.0, T_val=2500, T_dev=2000)
    g["null|history_x1"] = dict(edge=0.0, H=1)
    g["null|history_x20"] = dict(edge=0.0, H=20)
    g["null|freq_low(p0.15)"] = dict(edge=0.0, p_act=0.15, profile="laag_frequent")
    g["null|freq_high(p0.9)"] = dict(edge=0.0, p_act=0.9)
    g["null|K12"] = dict(edge=0.0, K=12)
    for e in (1.0, 2.0):
        g[f"edge{e}|K12"] = dict(edge=e, K=12)
    for e in (0.5, 1.0, 2.0):
        g[f"edge{e}|short_sample(T_val750)"] = dict(edge=e, T_val=750)
        g[f"edge{e}|long_sample(T_val2500)"] = dict(edge=e, T_val=2500, T_dev=2000)
        g[f"edge{e}|correlated_markets(rho0.6)"] = dict(edge=e, rho_m=0.6)
        g[f"edge{e}|freq_low(p0.15)"] = dict(edge=e, p_act=0.15, profile="laag_frequent")
        g[f"edge{e}|freq_high(p0.9)"] = dict(edge=e, p_act=0.9)
        g[f"edge{e}|regime_switching"] = dict(edge=e, regime=True)
        g[f"edge{e}|history_x20"] = dict(edge=e, H=20)
    for name in ("edge|0.0", "null|correlated_markets(rho0.6)", "null|t3_tails", "null|regime_switching",
                 "null|history_x20", "null|freq_low(p0.15)", "edge|0.5", "edge|1.0", "edge|1.5", "edge|2.0", "edge|3.0"):
        g["pboall|" + name] = {**g[name], "pbo_all": True}
    return g


# ------------------------------------------------------------------------------------------------ V10.2 PBO review campaign
# Pre-registered in pbo_review_summary.md / STATISTICAL_SELECTION_AUDIT.md 6b before the run. Both methods use the same
# seeds per scenario (paired): "topk" = PBO over the 4 top-K configurations, "all" = PBO over all C = 24 L4 configurations.
REVIEW_METHODS = {"topk": dict(pbo_all=False), "all": dict(pbo_all=True)}
ADAPTIVE_ROUNDS = 5


def review_scenarios():
    s = {"null": (dict(edge=0.0), 300), "null_regime": (dict(edge=0.0, regime=True), 300),
         "null_corr0.6": (dict(edge=0.0, rho_m=0.6), 300)}
    for e in (0.5, 1.0, 1.5, 2.0, 3.0):
        s[f"edge{e}"] = (dict(edge=e), 150)
    for e in (0.5, 1.0, 2.0):
        s[f"plateau{e}"] = (dict(edge=e, plateau=True), 150)
        s[f"regime{e}"] = (dict(edge=e, regime=True), 150)
    return s


def _work_adaptive(args):
    """One replication of the adaptive process: ADAPTIVE_ROUNDS sequential searches on the same validation period; the
    researcher regenerates (new round) after seeing validation results and keeps whatever passes in any round."""
    P, key, rounds = args
    out = []
    for r in range(rounds):
        try:
            out.append(run_search(P, zlib.crc32(repr(("adaptive", key, r)).encode())))
        except Exception as exc:
            out.append({"error": f"{type(exc).__name__}: {exc}", "cands": []})
    return out


def run_adaptive_cell(name, over, reps, procs, seed_key):
    P = {**DEFAULT, **over, "keep_daily": True}
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=procs) as ex:
        res = list(ex.map(_work_adaptive, [(P, (seed_key, k), ADAPTIVE_ROUNDS) for k in range(reps)], chunksize=1))
    return {"name": name, "params": P, "reps": reps, "rounds": ADAPTIVE_ROUNDS, "seconds": round(time.time() - t0, 1),
            "replications": res}


def review_campaign(procs, only="", quick=False):
    path = OUT / ("pbo_review_results_quick.json" if quick else "pbo_review_results.json")
    res = json.loads(path.read_text()) if path.exists() else {}
    for scn, (over, reps) in review_scenarios().items():
        for meth, mo in REVIEW_METHODS.items():
            name = f"rv|{meth}|{scn}"
            reps_ = 6 if quick else reps
            if (only and scn not in only.split(",")) or (name in res and res[name]["reps"] >= reps_):
                continue
            res[name] = run_cell(name, {**over, **mo}, reps_, procs, seed_key=f"rv|{scn}")
            path.write_text(json.dumps(res, default=float))
            print(name, res[name]["seconds"], "s", flush=True)
    for meth, mo in REVIEW_METHODS.items():
        name = f"rv|{meth}|adaptive"
        reps_ = 4 if quick else 300
        if (only and "adaptive" not in only.split(",")) or (name in res and res[name]["reps"] >= reps_):
            continue
        res[name] = run_adaptive_cell(name, {**mo, "edge": 0.0}, reps_, procs, "rv|adaptive")
        path.write_text(json.dumps(res, default=float))
        print(name, res[name]["seconds"], "s", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", default="", help="'review' = PBO top-4 vs full set with REVIEW_REQUIRED")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--reps", type=int, default=300)
    ap.add_argument("--procs", type=int, default=20)
    ap.add_argument("--out", default="pipeline_results.json")
    a = ap.parse_args()
    if a.campaign == "review":
        return review_campaign(a.procs, a.only, a.quick)
    reps = 6 if a.quick else a.reps
    g = grid()
    sel = [x for x in a.only.split(",") if x] or list(g)
    path = OUT / (a.out if not a.quick else "pipeline_results_quick.json")
    res = json.loads(path.read_text()) if path.exists() else {}
    for name in sel:
        r = reps if (name.startswith(("null", "pboall|null")) or name.endswith("edge|0.0")) else max(60, reps // 2)
        if name in res and res[name]["reps"] >= r:
            continue
        res[name] = run_cell(name, g[name], r, a.procs)
        path.write_text(json.dumps(res, default=float))
        print(name, res[name]["seconds"], "s", flush=True)


if __name__ == "__main__":
    main()
