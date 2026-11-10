"""V10.1 DSR calibration campaign: false-positive rate and power of three DSR definitions on synthetic returns.

Return-level Monte Carlo (fast, vectorised) with the SAME functions the factory uses
(``effective_trials.dsr_variants``, ``expected_max_sr``, ``psr``, ``effective_obs``, ``effective_n_eigen``).
Synthetic data only: calibration evidence for the statistics, never strategy evidence.

    C:/Python311/python.exe evidence/v101/stats/calibration.py [--quick] [--only S1,S2,...]

Settings
* S1  DSR-native: N trials on one period, the best in-sample Sharpe is tested (what BLdP 2014 models).
* S2  factory-like two-stage: N_dev trials on a development period, the top K go to an out-of-sample period, the
      best OOS series is tested. Compares search-level N (current gate) with OOS-level N.
* S3  repeated rounds on the same validation period, later rounds generated from the best OOS result so far
      (validation-informed search). Compares last-round N, ledger (every validation evaluation) N, search N.
* REF reference computations: E[max SR] closed form vs Monte Carlo, PSR vs scipy, BLdP (2014) worked example.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from strategy_lab.research import effective_trials as et  # noqa: E402
from strategy_lab.research import validation  # noqa: E402

OUT = Path(__file__).resolve().parent
ANN = math.sqrt(252)
THRESH = 0.95
NAMES = ("paper_dsr", "nc_dsr", "dep_dsr", "nc_dsr_raw_n")


def _seed(key) -> int:
    return zlib.crc32(repr(key).encode())


def wilson(k: int, n: int, z: float = 1.96) -> dict:
    if not n:
        return {"k": k, "n": n, "rate": None, "lo": None, "hi": None}
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return {"k": int(k), "n": int(n), "rate": p, "lo": max(0.0, c - h), "hi": min(1.0, c + h)}


# ------------------------------------------------------------------------------------------------ generator
def gen(rng, N, T, sr_true, *, dist="normal", ar=0.0, garch=False, rho=0.0, families=1, rho_family=0.0,
        active=1.0, regime=False, cost=0.0):
    """(N, T) daily returns with unit innovation variance. ``sr_true`` per-trial mean per active day (array or
    scalar). ``rho``: one common factor; ``families``/``rho_family``: block correlation inside families.
    ``active``: fraction of days with a position (trade frequency). ``regime``: 250-day blocks alternating
    low/high volatility (x2), the edge exists only in low-vol blocks. ``cost``: subtracted per active day."""
    def innov(shape):
        if dist == "normal":
            return rng.standard_normal(shape)
        df = int(dist[1:])
        return rng.standard_t(df, shape) / math.sqrt(df / (df - 2.0))
    e = innov((N, T))
    if rho > 0:
        e = math.sqrt(rho) * innov((1, T)) + math.sqrt(1 - rho) * e
    if families > 1 and rho_family > 0:
        fam = np.arange(N) % families
        f = innov((families, T))[fam]
        e = math.sqrt(rho_family) * f + math.sqrt(1 - rho_family) * e
    if garch:
        a, b = 0.08, 0.90
        w = 1 - a - b
        s2 = np.ones(N)
        x = np.empty_like(e)
        for t in range(T):
            x[:, t] = np.sqrt(s2) * e[:, t]
            s2 = w + a * x[:, t] ** 2 + b * s2
        e = x
    if ar:
        x = np.empty_like(e)
        x[:, 0] = e[:, 0]
        k = math.sqrt(1 - ar * ar)
        for t in range(1, T):
            x[:, t] = ar * x[:, t - 1] + k * e[:, t]
        e = x
    mu = np.asarray(sr_true, dtype=float)
    mu = (np.full(N, float(mu)) if mu.ndim == 0 else mu).reshape(N, 1)
    if regime:
        blk = (np.arange(T) // 250) % 2
        vol = np.where(blk == 1, 2.0, 1.0)
        r = e * vol + mu * (blk == 0)
    else:
        r = e + mu
    if active < 1.0:
        mask = rng.random((N, T)) < active
        r = np.where(mask, r - cost, 0.0)
    elif cost:
        r = r - cost
    return r


def sharpe_rows(r):
    sd = r.std(axis=1, ddof=1)
    return np.where(sd > 0, r.mean(axis=1) / np.where(sd > 0, sd, 1), 0.0)


def evaluate(series, n_raw, n_eff, V_paper):
    st = et.sharpe_stats(series)
    eo = et.effective_obs(series)
    v = et.dsr_variants(st, eo, n_raw=n_raw, n_eff=n_eff, V_paper=V_paper, V_paper_status="OK")
    vals = {k: v[k]["value"] for k in v}
    V_null = 1.0 / max(eo["T_eff"], 1.0)
    vals["nc_dsr_raw_n"] = et.psr(st["sr"], eo["T_eff"], st["skew"], st["kurt"], et.expected_max_sr(n_raw, V_null))
    return vals, st, eo


def paper_V(srs, T, T_test):
    return et.trial_variance(srs, np.full(len(srs), T), T_test, min_T=2)["V"]


# ------------------------------------------------------------------------------------------------ S1
def s1_cell(reps, N, T, edge_ann=0.0, n_skilled=1, seed=0, eff_n=True, **g):
    """Best of N on the same period. ``edge_ann``: annualised SR of the skilled trial(s); others have 0 (or
    the cost-induced negative SR when ``neg_spread`` is given in g)."""
    rng = np.random.default_rng(seed)
    neg_spread = g.pop("neg_spread", 0.0)
    hits = {k: 0 for k in NAMES}
    best_sr, sr0s, neffs, teffs = [], {k: [] for k in NAMES[:3]}, [], []
    for _ in range(reps):
        mu = np.zeros(N)
        if neg_spread:
            mu = -np.abs(rng.normal(0, neg_spread / ANN, N))
        mu[:n_skilled] = edge_ann / ANN
        r = gen(rng, N, T, mu, **g)
        srs = sharpe_rows(r)
        i = int(np.argmax(srs))
        n_eff = et.effective_n_eigen(np.corrcoef(r)) if (eff_n and N > 1) else float(N)
        n_eff = max(1.0, min(float(N), n_eff))
        vals, st, eo = evaluate(r[i], N, n_eff, paper_V(srs, T, T))
        for k in NAMES:
            hits[k] += int((vals[k] or 0) >= THRESH)
        best_sr.append(st["sr"] * ANN)
        neffs.append(n_eff)
        teffs.append(eo["T_eff"])
    return {"N": N, "T": T, "edge_ann": edge_ann, "reps": reps, "params": {k: v for k, v in g.items()},
            "neg_spread_ann": neg_spread, "pass": {k: wilson(hits[k], reps) for k in NAMES},
            "median_best_sr_ann": float(np.median(best_sr)), "median_n_eff": float(np.median(neffs)),
            "median_T_eff": float(np.median(teffs))}


def run_s1(reps, quick):
    cells = {}
    Ns = (10, 100) if quick else (10, 100, 500)
    Ts = (250, 750, 1500)
    edges = (0.0, 0.5, 1.0, 2.0)
    for N in Ns:
        for T in Ts:
            for e in edges:
                r = reps if e == 0.0 else max(100, reps // 2)
                cells[f"N={N}|T={T}|edge={e}"] = s1_cell(r, N, T, e, seed=_seed((N, T, e)) )
    stress = {
        "t3_heavy_tails": dict(dist="t3"), "t5": dict(dist="t5"), "ar0.2": dict(ar=0.2), "garch": dict(garch=True),
        "regimes": dict(regime=True), "low_frequency_20pct": dict(active=0.2),
        "correlated_rho0.7": dict(rho=0.7), "families10_rho0.8": dict(families=10, rho_family=0.8),
        "costs_negative_true_sr": dict(neg_spread=1.5),
    }
    for name, g in stress.items():
        for e in (0.0, 1.0):
            r = reps if e == 0.0 else max(100, reps // 2)
            cells[f"{name}|N=100|T=750|edge={e}"] = s1_cell(r, 100, 750, e, seed=_seed((name, e)) , **dict(g))
    # many optimisations: 1000 highly correlated parameter variants; multi-market selection: 8 markets
    cells["many_optimisations_N1000_rho0.9|T=750|edge=0.0"] = s1_cell(max(60, reps // 4), 1000, 750, 0.0, seed=7,
                                                                       rho=0.9)
    cells["many_optimisations_N1000_rho0.9|T=750|edge=1.5"] = s1_cell(max(60, reps // 4), 1000, 750, 1.5, seed=8,
                                                                       rho=0.9)
    cells["multi_market_8|T=1500|edge=0.0"] = s1_cell(reps, 8, 1500, 0.0, seed=9, rho=0.2)
    cells["multi_market_8|T=1500|edge=1.0"] = s1_cell(max(100, reps // 2), 8, 1500, 1.0, seed=10, rho=0.2)
    return cells


# ------------------------------------------------------------------------------------------------ S2
def s2_cell(reps, N_dev, K, T_dev, T_oos, edge_ann=0.0, seed=0, **g):
    """Dev search over N_dev trials, top K to OOS, best OOS tested. Skilled trial index 0 keeps its edge in both
    periods. Variants: search-level N (N_dev + K, current factory gate) vs OOS-level N (K)."""
    rng = np.random.default_rng(seed)
    keys = ("nc_search_N", "nc_oos_N", "paper_oos_N", "paper_search_N", "dep_oos_N")
    hits = {k: 0 for k in keys}
    for _ in range(reps):
        mu = np.zeros(N_dev)
        mu[0] = edge_ann / ANN
        r = gen(rng, N_dev, T_dev + T_oos, mu, **g)
        dev, oos = r[:, :T_dev], r[:, T_dev:]
        top = np.argsort(sharpe_rows(dev))[::-1][:K]
        o = oos[top]
        srs = sharpe_rows(o)
        i = int(np.argmax(srs))
        n_eff_oos = max(1.0, min(float(K), et.effective_n_eigen(np.corrcoef(o)))) if K > 1 else 1.0
        series = o[i]
        st, eo = et.sharpe_stats(series), et.effective_obs(series)
        Vp = paper_V(srs, T_oos, T_oos) if K > 1 else None
        Vn = 1.0 / max(eo["T_eff"], 1.0)
        v = {"nc_search_N": et.psr(st["sr"], eo["T_eff"], st["skew"], st["kurt"], et.expected_max_sr(N_dev + K, Vn)),
             "nc_oos_N": et.psr(st["sr"], eo["T_eff"], st["skew"], st["kurt"], et.expected_max_sr(n_eff_oos, Vn)),
             "paper_oos_N": et.psr(st["sr"], st["T"], st["skew"], st["kurt"], et.expected_max_sr(K, Vp or Vn)),
             "paper_search_N": et.psr(st["sr"], st["T"], st["skew"], st["kurt"],
                                      et.expected_max_sr(N_dev + K, Vp or Vn)),
             "dep_oos_N": et.psr(st["sr"], eo["T_eff"], st["skew"], st["kurt"],
                                 et.expected_max_sr(n_eff_oos, Vp or Vn))}
        for k in keys:
            hits[k] += int(v[k] >= THRESH)
    return {"N_dev": N_dev, "K": K, "T_dev": T_dev, "T_oos": T_oos, "edge_ann": edge_ann, "reps": reps,
            "params": g, "pass": {k: wilson(hits[k], reps) for k in keys}}


def run_s2(reps, quick):
    cells = {}
    for N_dev, K in ((200, 10), (1000, 20)) if not quick else ((200, 10),):
        for T_oos in (750, 1500):
            for e in (0.0, 0.5, 1.0, 2.0):
                r = reps if e == 0.0 else max(100, reps // 2)
                cells[f"Ndev={N_dev}|K={K}|Toos={T_oos}|edge={e}"] = s2_cell(r, N_dev, K, 1250, T_oos, e,
                                                                             seed=_seed((N_dev, T_oos, e)) )
    return cells


# ------------------------------------------------------------------------------------------------ S3
def s3_cell(reps, rounds, N_dev, K, T_dev, T_oos, rho_mut, edge_ann=0.0, seed=0):
    """Repeated rounds on the SAME validation period. Round 1 is a fresh search; later rounds mutate the best OOS
    series so far (correlation ``rho_mut`` over both periods: validation-informed generation). The best OOS series
    over all rounds is tested. N variants: last round only (K), every validation evaluation ever (rounds*K, the
    ledger count) and the full search (rounds*(N_dev+K))."""
    rng = np.random.default_rng(seed)
    keys = ("nc_last_round_N", "nc_validation_N", "nc_search_N")
    hits = {k: 0 for k in keys}
    T = T_dev + T_oos
    for _ in range(reps):
        best, best_sr = None, -np.inf
        for r in range(rounds):
            fresh = gen(rng, N_dev, T, 0.0)
            if r == 0 or best is None:
                trials = fresh
                if edge_ann:
                    trials[0] += edge_ann / ANN
            else:
                trials = math.sqrt(rho_mut) * best[None, :] + math.sqrt(1 - rho_mut) * fresh
            top = np.argsort(sharpe_rows(trials[:, :T_dev]))[::-1][:K]
            o = sharpe_rows(trials[top, T_dev:])
            j = int(np.argmax(o))
            if o[j] > best_sr:
                best_sr, best = o[j], trials[top[j]].copy()
        series = best[T_dev:]
        st, eo = et.sharpe_stats(series), et.effective_obs(series)
        Vn = 1.0 / max(eo["T_eff"], 1.0)
        for k, N in (("nc_last_round_N", K), ("nc_validation_N", rounds * K), ("nc_search_N", rounds * (N_dev + K))):
            hits[k] += int(et.psr(st["sr"], eo["T_eff"], st["skew"], st["kurt"], et.expected_max_sr(N, Vn)) >= THRESH)
    return {"rounds": rounds, "N_dev": N_dev, "K": K, "T_oos": T_oos, "rho_mut": rho_mut, "edge_ann": edge_ann,
            "reps": reps, "pass": {k: wilson(hits[k], reps) for k in keys}}


def run_s3(reps, quick):
    cells = {}
    for rho in (0.0, 0.5, 0.8):
        for e in (0.0, 2.0):
            r = reps if e == 0.0 else max(100, reps // 2)
            cells[f"rounds=5|Ndev=100|K=10|Toos=1000|rho_mut={rho}|edge={e}"] = s3_cell(
                r, 5, 100, 10, 1000, 1000, rho, e, seed=_seed(("s3", rho, e)))
    return cells


# ------------------------------------------------------------------------------------------------ references
def references(quick):
    from scipy import stats
    out = {"expected_max": [], "psr_vs_scipy": [], "worked_example": {}}
    rng = np.random.default_rng(42)
    reps = 400 if quick else 1500
    for N in (10, 100, 1000):
        for T, dist, ar in ((750, "normal", 0.0), (750, "t3", 0.0), (750, "normal", 0.2)):
            if N == 1000 and (dist != "normal" or ar):
                continue
            n_rep = reps if N < 1000 else max(150, reps // 6)
            mx = []
            teff = []
            for _ in range(n_rep):
                r = gen(rng, N, T, 0.0, dist=dist, ar=ar)
                mx.append(sharpe_rows(r).max())
                if ar:
                    teff.append(et.effective_obs(r[0])["T_eff"])
            emp = float(np.mean(mx))
            se = float(np.std(mx, ddof=1) / math.sqrt(n_rep))
            T_used = float(np.median(teff)) if teff else T
            cf = et.expected_max_sr(N, 1.0 / T)
            cf_eff = et.expected_max_sr(N, 1.0 / T_used)
            out["expected_max"].append({"N": N, "T": T, "dist": dist, "ar": ar, "reps": n_rep, "mc_mean": emp,
                                        "mc_se": se, "closed_form_1_over_T": cf, "closed_form_1_over_T_eff": cf_eff,
                                        "rel_err_T": (cf - emp) / emp, "rel_err_T_eff": (cf_eff - emp) / emp})
    # correlated trials: E[max] vs closed form with N raw and with effective N
    for rho in (0.5, 0.9):
        mx, ne = [], []
        for _ in range(300 if quick else 800):
            r = gen(rng, 100, 750, 0.0, rho=rho)
            s = sharpe_rows(r)
            mx.append(s.max() - s.mean())   # deviation of the best from the common factor
            ne.append(et.effective_n_eigen(np.corrcoef(r)))
        out.setdefault("correlated_expected_max", []).append({
            "N": 100, "rho": rho, "mc_mean_max_minus_mean": float(np.mean(mx)), "median_n_eff_eigen": float(np.median(ne)),
            "closed_form_raw_N_V_cross": et.expected_max_sr(100, (1 - rho) / 750),
            "closed_form_n_eff_V_null": et.expected_max_sr(float(np.median(ne)), 1 / 750)})
    for sr, T, sk, ku in ((0.1, 500, 0.0, 3.0), (0.05, 1250, -1.0, 8.0), (0.2, 250, 0.5, 4.0)):
        z = sr * math.sqrt(T - 1) / math.sqrt(1 - sk * sr + (ku - 1) / 4 * sr * sr)
        out["psr_vs_scipy"].append({"sr": sr, "T": T, "skew": sk, "kurt": ku, "code": et.psr(sr, T, sk, ku, 0.0),
                                    "scipy": float(stats.norm.cdf(z))})
    sr = 2.5 / math.sqrt(250)
    sr0 = et.expected_max_sr(100, 0.5 / 250)
    out["worked_example"] = {"paper": "BLdP 2014: SR_ann 2.5, T 1250, skew -3, kurt 10, N 100, V 0.5/250",
                             "sr0": sr0, "sr0_paper": 0.1132, "dsr": et.psr(sr, 1250, -3, 10, sr0), "dsr_paper": 0.9004}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", default="REF,S1,S2,S3")
    ap.add_argument("--reps", type=int, default=400)
    a = ap.parse_args()
    reps = 100 if a.quick else a.reps
    path = OUT / ("calibration_results_quick.json" if a.quick else "calibration_results.json")
    res = json.loads(path.read_text()) if path.exists() else {}
    for part in a.only.split(","):
        t0 = time.time()
        fn = {"REF": lambda: references(a.quick), "S1": lambda: run_s1(reps, a.quick),
              "S2": lambda: run_s2(reps, a.quick), "S3": lambda: run_s3(reps, a.quick)}[part]
        res[part] = {"result": fn(), "seconds": round(time.time() - t0, 1), "reps_null": reps,
                     "threshold": THRESH, "seed_policy": "fixed per cell (hash of cell parameters)"}
        path.write_text(json.dumps(res, indent=1, default=float))
        print(part, "done", res[part]["seconds"], "s", flush=True)


if __name__ == "__main__":
    main()
