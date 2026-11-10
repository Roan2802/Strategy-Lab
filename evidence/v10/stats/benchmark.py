"""V10 anti-overfitting benchmark campaign (scenarios A-I on synthetic data, J = real factory run summary).

Synthetic scenarios use the SAME statistical modules and thresholds as factory L5/L6 (nested_wf, robustness,
effective_trials, validation.pbo_cscv, quality profile 'regelmatig'); only the rule engine is replaced by a small
vectorised engine (MA-cross / breakout / mean-reversion on synthetic bid/ask bars) so hundreds of independent
experiments fit in minutes. Synthetic results are calibration evidence for the pipeline, never strategy evidence.

    C:/Python311/python.exe evidence/v10/stats/benchmark.py [--quick] [--only A,B,...]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from strategy_lab.research import effective_trials as et  # noqa: E402
from strategy_lab.research import nested_wf, robustness, validation  # noqa: E402

OUT = Path(__file__).resolve().parent
Q = {"min_oos_trades": 150, "min_pf_oos": 1.20, "min_rf_oos": 1.5, "max_dd_pct": 15.0, "max_pbo": 0.5,
     "min_wfe": 0.5, "min_dsr": 0.95}
CAPITAL = 10000.0
TICK = 0.1          # synthetic price ~1000: 1 tick = 1 bp, comparable to the engine (slippage in ticks)


# ------------------------------------------------------------------------------------------------ synthetic data
def bars(years: float, seed: int, *, spread_bp: float = 1.5, trend: float = 0.0, vol: float = 0.0015,
         start="2010-01-04") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hourly weekday bars. Log-returns = persistent OU drift (strength ``trend``, the injected edge) + noise.
    Spread ~ Dukascopy-like: base ``spread_bp`` basis points, wider in the Asian session and on volatile hours."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=int(years * 365.25 * 24), freq="1h", tz="UTC")
    idx = idx[idx.dayofweek < 5]
    n = len(idx)
    mu = np.zeros(n)
    if trend:
        k = 1.0 / 120.0                       # drift half-life ~ 3.5 trading days
        e = rng.standard_normal(n)
        for i in range(1, n):
            mu[i] = (1 - k) * mu[i - 1] + math.sqrt(2 * k) * e[i]
        mu *= trend * vol
    ret = mu + rng.standard_normal(n) * vol * (1 + 0.5 * (rng.random(n) < 0.05))
    close = 1000.0 * np.exp(np.cumsum(ret))
    op = np.concatenate([[1000.0], close[:-1]])
    span = np.abs(rng.normal(0, vol / 2, n)) * close
    bid = pd.DataFrame({"open": op, "high": np.maximum(op, close) + span, "low": np.minimum(op, close) - span,
                        "close": close}, index=idx)
    asia = (idx.hour < 7).astype(float)
    sp = close * spread_bp / 1e4 * (1 + 0.8 * asia + 2.0 * np.abs(ret) / vol / 3)
    ask = bid.copy()
    for c in ("open", "high", "low", "close"):
        ask[c] = bid[c] + sp
    return bid, ask


# ------------------------------------------------------------------------------------------------ toy engine
def signal(bid: pd.DataFrame, family: str, p: dict) -> np.ndarray:
    c = bid["close"]
    if family == "ma_cross":
        s = np.sign(c.rolling(p["fast"]).mean() - c.rolling(p["slow"]).mean())
    elif family == "breakout":
        hi, lo = bid["high"].rolling(p["lookback"]).max().shift(1), bid["low"].rolling(p["lookback"]).min().shift(1)
        s = pd.Series(np.where(c > hi, 1.0, np.where(c < lo, -1.0, np.nan)), index=c.index).ffill()
    elif family == "mean_rev":
        z = (c - c.rolling(p["lookback"]).mean()) / c.rolling(p["lookback"]).std()
        s = pd.Series(np.where(z > p["z"], -1.0, np.where(z < -p["z"], 1.0, np.nan)), index=c.index)
        s = s.where(~((z.abs() < 0.25)), 0.0).ffill()
    elif family == "random":
        rng = np.random.default_rng(p["seed"])
        s = pd.Series(np.repeat(rng.choice([-1.0, 1.0], len(c) // 24 + 1), 24)[:len(c)], index=c.index)
    else:
        raise ValueError(family)
    return np.nan_to_num(np.asarray(s, dtype=float))


def run(bid, ask, family, p, start, end, *, entry_delay_bars=0, exit_delay_bars=0, spread_multiplier=1.0,
        slippage_points=0.0, commission_per_lot=0.0, max_hold=None) -> dict:
    """Position = signal of the previous bar, filled at the next bar open (long buys ask, sells bid). Signal is
    computed with data up to the bar close only (causal). Trades opened in [start, end) and closed before end."""
    s = signal(bid, family, p)
    i0 = bid.index.searchsorted(pd.Timestamp(start, tz="UTC"))
    i1 = bid.index.searchsorted(pd.Timestamp(end, tz="UTC"))
    bo, ao = bid["open"].to_numpy(), ask["open"].to_numpy()
    ao = bo + (ao - bo) * spread_multiplier
    slippage_points = slippage_points * TICK
    times = bid.index
    trades, pos, ent, ent_i = [], 0.0, 0.0, 0
    d_in = 1 + entry_delay_bars
    for i in range(max(i0, 1), i1):
        want = s[i - 1]
        hold = max_hold or p.get("max_hold")
        if pos != 0 and (want != pos or (hold and i - ent_i >= hold)):
            j = min(i + exit_delay_bars, i1 - 1)
            px = (bo[j] - slippage_points) if pos > 0 else (ao[j] + slippage_points)
            qty = CAPITAL / ent
            pnl = (px - ent) * pos * qty - 2 * commission_per_lot * qty / 100
            trades.append({"entry_time": times[ent_i].isoformat(), "exit_time": times[j].isoformat(),
                           "side": "long" if pos > 0 else "short", "entry_price": ent, "exit_price": px, "qty": qty,
                           "pnl": float(pnl), "costs": float(2 * commission_per_lot * qty / 100),
                           "bars_held": int(j - ent_i)})
            pos = 0.0
        if pos == 0 and want != 0 and i + d_in - 1 < i1 - 1:
            k = i + d_in - 1
            pos = want
            ent = (ao[k] + slippage_points) if want > 0 else (bo[k] - slippage_points)
            ent_i = k
    return {"trades": trades}


GRIDS = {"ma_cross": [{"fast": f, "slow": s} for f in (6, 12, 24) for s in (48, 96, 168)],
         "breakout": [{"lookback": lb, "max_hold": h} for lb in (24, 48, 96, 168) for h in (24, 72)],
         "mean_rev": [{"lookback": lb, "z": z} for lb in (24, 72) for z in (1.5, 2.0, 2.5)]}


def neighbour_space(family):
    if family == "ma_cross":
        return {"fast": {"type": "int", "min": 2, "max": 60, "step": 2}, "slow": {"type": "int", "min": 20, "max": 400,
                                                                                    "step": 12}}
    if family == "breakout":
        return {"lookback": {"type": "int", "min": 6, "max": 400, "step": 12},
                "max_hold": {"type": "int", "min": 6, "max": 240, "step": 12}}
    return {"lookback": {"type": "int", "min": 10, "max": 300, "step": 12},
            "z": {"type": "float", "min": 0.5, "max": 4.0, "step": 0.25}}


# ------------------------------------------------------------------------------------------------ pipeline
def pipeline(bid, ask, families: dict, *, wf_start, end, train_days=730, test_days=182, seed=0,
             extra_rows=None, family_of_interest=None, n_null=200) -> dict:
    """Search (nested WF chooses params per family on train only), robustness and DSR gates for the best family.
    Returns the gate results; ``final_pass`` = every gate a factory candidate needs at L5 + L6."""
    cache = {}

    def bt_cfg(cfg, s, e):
        key = json.dumps(cfg, sort_keys=True)
        if key not in cache:
            cache[key] = run(bid, ask, cfg["variant"], cfg["params"], str(bid.index[0])[:10], end)["trades"]
        s0, e0 = pd.Timestamp(s, tz="UTC"), pd.Timestamp(e, tz="UTC")
        return {"trades": [t for t in cache[key] if s0 <= pd.Timestamp(t["entry_time"]) < e0]}

    results = {}
    rows = list(extra_rows or [])
    for fam, grid in families.items():
        cfgs = [{"variant": fam, "params": p} for p in grid]
        gap = nested_wf.dependency_gap(None, "H1", trades=bt_cfg(cfgs[0], wf_start, end)["trades"],
                                       lookback_bars=max(max(p.values()) for p in grid if p))
        roll = nested_wf.nested_walk_forward(bt_cfg, cfgs, wf_start, end, train_days=train_days, test_days=test_days,
                                             mode="rolling", gap=gap, min_trades=10, capital=CAPITAL)
        expd = nested_wf.nested_walk_forward(bt_cfg, cfgs, str(bid.index[0])[:10], end,
                                             train_days=train_days + (pd.Timestamp(wf_start) -
                                                                      pd.Timestamp(str(bid.index[0])[:10])).days,
                                             test_days=test_days, mode="expanding", gap=gap, min_trades=10,
                                             capital=CAPITAL)
        oos_start = roll["windows"][0]["test"][0] if roll["windows"] else wf_start
        for c in cfgs:   # every configuration is a trial; its OOS-period series is a V source (same horizon)
            tr = bt_cfg(c, oos_start, end)["trades"]
            d = validation.daily_returns_from_trades(tr, CAPITAL)
            rows.append({"hypothesis_id": json.dumps(c, sort_keys=True), "family": fam, "group": fam, "stage": "L5",
                         "sr": float(d.mean() / d.std(ddof=1)) if len(d) > 2 and d.std(ddof=1) > 0 else None,
                         "T": int(len(d)), "rep_series": None, "n_eff_within": None})
        results[fam] = {"roll": roll, "expd": expd, "cfgs": cfgs, "oos_start": oos_start}
    # within-family effective N from the configs' OOS series
    for fam, r in results.items():
        frame = pd.concat({json.dumps(c["params"]): validation.daily_pnl_from_trades(
            bt_cfg(c, r["oos_start"], end)["trades"]) for c in r["cfgs"]}, axis=1).fillna(0.0)
        en = et.effective_n(frame.loc[:, frame.std() > 0]) if frame.shape[1] >= 3 else {"n_eff": None, "n_raw": 0}
        if en.get("n_eff"):
            for row in rows:
                if row["family"] == fam:
                    row["n_eff_within"] = en["n_eff"] / max(en["n_raw"], 1) * len(r["cfgs"])
        r["n_eff_within"] = en.get("n_eff")
    best_fam = max(results, key=lambda f: results[f]["roll"]["oos_net_profit"])
    r = results[best_fam]
    roll, expd = r["roll"], r["expd"]
    oos_trades = roll["oos_trades"]
    oos_start = r["oos_start"]
    om = validation.trade_metrics(oos_trades, CAPITAL)
    daily = validation.daily_returns_from_trades(oos_trades, CAPITAL)
    last = next((w for w in reversed(roll["windows"]) if w["status"] == "OK"), None)
    chosen = (last or {}).get("config") or r["cfgs"][0]
    b_oos, a_oos = bid[bid.index >= pd.Timestamp(oos_start, tz="UTC")], ask[ask.index >= pd.Timestamp(oos_start,
                                                                                                         tz="UTC")]
    fam = chosen["variant"]

    def fn(params, s, e, **kw):
        return run(bid, ask, fam, params, s, e, **kw)

    neigh = robustness.parameter_neighbourhood(fn, chosen["params"], neighbour_space(fam), oos_start, end, CAPITAL)
    execution = robustness.execution_stress(fn, chosen["params"], oos_start, end, CAPITAL, 0.0)
    ets = pd.to_datetime([t["entry_time"] for t in oos_trades], utc=True).tz_localize(None) if oos_trades else []
    cost_mc = robustness.cost_monte_carlo(oos_trades, spread_points=robustness.spread_at(b_oos, a_oos, ets)
                                          if len(ets) else None, n=300, seed=seed)
    null = robustness.random_entry_null(oos_trades, b_oos, a_oos, n=n_null, seed=seed)
    conc = robustness.concentration(oos_trades, CAPITAL, oos_start, end)
    regimes = robustness.regime_stability(oos_trades, robustness.causal_regimes(bid, ask))
    cis = robustness.bootstrap_cis(daily.to_numpy(), [t["pnl"] for t in oos_trades], n=500, seed=seed)
    eff = robustness.effective_independent_obs(daily.to_numpy(), oos_trades)
    ev = robustness.verdict(oos_net=om["net_profit"], eff_obs=eff, cis=cis, null=null, baselines_beaten=None,
                            cost_mc=cost_mc, execution=execution, neighbourhood=neigh, conc=conc, regimes=regimes)
    mt = et.multiple_testing_records(daily.to_numpy(), None, family=family_of_interest or best_fam, rows=rows,
                                     seed=seed) if len(daily) > 30 else None
    gate = et.gate(mt, Q["min_dsr"]) if mt else {"status": "INSUFFICIENT_EVIDENCE", "value": None}
    cols = [validation.daily_returns_from_trades(bt_cfg(c, oos_start, end)["trades"], CAPITAL) for c in r["cfgs"]]
    pbo = validation.pbo_cscv(pd.concat(cols, axis=1).fillna(0.0).to_numpy(), n_splits=8) if len(cols) >= 2 else {}
    boot = validation.bootstrap_confidence_interval([t["pnl"] for t in oos_trades], np.mean, n=500, block="auto",
                                                    seed=seed)
    weeks = max((pd.Timestamp(end) - pd.Timestamp(oos_start)).days / 7.0, 1)
    rf = om["recovery_factor"]
    wf_checks = {
        "oos_trades": om["n_trades"] >= Q["min_oos_trades"], "pf": om["profit_factor"] >= Q["min_pf_oos"],
        "rf": rf >= Q["min_rf_oos"], "dd": om["max_drawdown_pct"] <= Q["max_dd_pct"],
        "expectancy_ci": boot.get("status") == "OK" and boot["ci_low"] > 0,
        "wfe": roll.get("wfe") is not None and roll["wfe"] >= Q["min_wfe"],
        "expanding": expd["oos_net_profit"] > 0,
        "pbo": pbo.get("pbo") is None or pbo["pbo"] <= Q["max_pbo"],
        "single_window": not roll["single_window_dependence"]["flag"]}
    wf_pass = all(wf_checks.values())
    rob_pass = ev["verdict"] == "SUPPORTED_UNDER_ASSUMPTIONS"
    return {"best_family": best_fam, "oos_net": om["net_profit"], "oos_pf": om["profit_factor"],
            "oos_sharpe_ann": om["sharpe"], "oos_trades": om["n_trades"], "trades_per_week": om["n_trades"] / weeks,
            "wf_checks": wf_checks, "wf_pass": wf_pass, "evidence_verdict": ev["verdict"],
            "verdict_reasons": ev["reasons"], "robustness_pass": rob_pass,
            "dsr_global": (mt or {}).get("global", {}).get("value"),
            "dsr_global_paper_v": (mt or {}).get("global", {}).get("value_paper_v"), "dsr_family": (mt or {}).get("family", {}).get("value"),
            "raw_n": (mt or {}).get("global", {}).get("raw_n"), "eff_n": (mt or {}).get("global", {}).get("effective_n"),
            "V": (mt or {}).get("global", {}).get("V"), "psr": validation.probabilistic_sharpe_ratio(daily)["value"],
            "gate": gate["status"], "pbo": pbo.get("pbo"), "null_p": null.get("p_value"),
            "neighbourhood": neigh.get("classification"), "concentration_flags": conc.get("flags"),
            "final_pass": wf_pass and rob_pass and gate["status"] == "PASS",
            "n_eff_within": {f: v.get("n_eff_within") for f, v in results.items()}}


def summarize(rows, key="final_pass"):
    n = len(rows)
    k = sum(1 for r in rows if r.get(key))
    # Clopper-Pearson-ish 95% upper bound via Wilson
    z = 1.96
    p = k / n if n else 0
    hi = (p + z * z / (2 * n) + z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n) if n else None
    return {"n": n, "k": k, "rate": p, "wilson95_upper": hi}


# ------------------------------------------------------------------------------------------------ scenarios
def scen_A(n_exp, years=8, start=0):
    """No edge: random walk + realistic spreads, full search over 3 families x grids; repeated independently."""
    rows = []
    for i in range(start, start + n_exp):
        bid, ask = bars(years, seed=1000 + i)
        end = str(bid.index[-1] + pd.Timedelta(hours=1))[:10]
        res = pipeline(bid, ask, GRIDS, wf_start="2012-01-02", end=end, seed=i)
        rows.append({k: res[k] for k in ("best_family", "oos_net", "oos_sharpe_ann", "wf_pass", "robustness_pass",
                                         "evidence_verdict", "dsr_global", "dsr_global_paper_v", "psr", "gate",
                                         "final_pass", "raw_n", "eff_n")})
    gate_only = summarize(rows, "gate")
    return {"description": "Geen edge (random walk, Dukascopy-achtige spread), volledige zoektocht per experiment.",
            "experiments": rows, "false_positive_final": summarize(rows),
            "false_positive_wf_only": summarize(rows, "wf_pass"),
            "false_positive_robustness_only": summarize(rows, "robustness_pass"),
            "psr_only_ge_095": summarize([{"x": (r["psr"] or 0) >= 0.95} for r in rows], "x"),
            "dsr_gate_pass": {"n": len(rows), "k": sum(r["gate"] == "PASS" for r in rows)},
            "dsr_paper_v_ge_095": {"n": len(rows), "k": sum((r["dsr_global_paper_v"] or 0) >= 0.95 for r in rows)},
            "verdict_counts": pd.Series([r["evidence_verdict"] for r in rows]).value_counts().to_dict()}


def scen_B(n_exp, trends=(0.35,), years_list=(3, 6, 10)):
    """Injected edge (persistent drift -> trend-following works); detection rate vs sample length."""
    out = {}
    for tr in trends:
        for years in years_list:
            rows = []
            for i in range(n_exp):
                bid, ask = bars(years, seed=5000 + 31 * i + years, trend=tr)
                end = str(bid.index[-1] + pd.Timedelta(hours=1))[:10]
                wf_start = str(bid.index[0] + pd.Timedelta(days=int(min(730, years * 365 / 3))))[:10]
                res = pipeline(bid, ask, {"ma_cross": GRIDS["ma_cross"], "breakout": GRIDS["breakout"]},
                               wf_start=wf_start, end=end, train_days=int(min(730, years * 365 / 3)), seed=i)
                rows.append({k: res[k] for k in ("best_family", "oos_net", "oos_sharpe_ann", "wf_pass",
                                                 "robustness_pass", "evidence_verdict", "verdict_reasons",
                                                 "dsr_global", "dsr_global_paper_v", "gate", "final_pass",
                                                 "wf_checks")})
            out[f"trend={tr}|years={years}"] = {"detection": summarize(rows), "wf_pass": summarize(rows, "wf_pass"),
                                                "robustness_pass": summarize(rows, "robustness_pass"),
                                                "gate_pass": {"n": len(rows), "k": sum(r["gate"] == "PASS" for r in rows)},
                                                "paper_v_dsr_pass": {"n": len(rows), "k": sum(
                                                    (r["dsr_global_paper_v"] or 0) >= 0.95 for r in rows)},
                                                "median_oos_sharpe_ann": float(np.median([r["oos_sharpe_ann"]
                                                                                          for r in rows])),
                                                "experiments": rows}
    return {"description": "Bekende edge (persistente drift), detectie per steekproeflengte.", "cells": out}


def scen_C(n_exp, n_configs=200, years=8):
    """Overfit: 200 random configurations searched in-sample on noise; best IS looks good, OOS / PBO / DSR expose it."""
    rows = []
    for i in range(n_exp):
        bid, ask = bars(years, seed=9000 + i, spread_bp=0.2)
        rng = np.random.default_rng(i)
        cfgs = [{"fast": int(rng.integers(2, 40)), "slow": int(rng.integers(41, 400))} for _ in range(n_configs)]
        split = "2014-01-01"
        is_m, oos_m, cols = [], [], []
        for c in cfgs:
            t_is = run(bid, ask, "ma_cross", c, "2010-01-04", split)["trades"]
            t_oos = run(bid, ask, "ma_cross", c, split, "2018-01-01")["trades"]
            is_m.append(validation.trade_metrics(t_is, CAPITAL)["sharpe"])
            oos_m.append(validation.trade_metrics(t_oos, CAPITAL)["sharpe"])
            cols.append(validation.daily_returns_from_trades(t_is + t_oos, CAPITAL))
        b = int(np.argmax(is_m))
        mat = pd.concat(cols, axis=1).fillna(0.0).to_numpy()
        pbo = validation.pbo_cscv(mat, n_splits=8)
        d_is = validation.daily_returns_from_trades(run(bid, ask, "ma_cross", cfgs[b], "2010-01-04", split)["trades"],
                                                    CAPITAL)
        srs = [s / math.sqrt(252) for s in is_m]
        var = et.trial_variance(srs, [len(d_is)] * len(srs), len(d_is))
        dsr_is = et.dsr_record(d_is.to_numpy(), n_raw=n_configs, n_eff=n_configs, variance=var, scope="is")
        rows.append({"best_is_sharpe_ann": is_m[b], "its_oos_sharpe_ann": oos_m[b], "pbo": pbo["pbo"],
                     "psr_is_single_test": validation.probabilistic_sharpe_ratio(d_is)["value"],
                     "dsr_is_n200": dsr_is["value"]})
    return {"description": "Overfit: beste van 200 configuraties in-sample op ruis.", "experiments": rows,
            "median_best_is_sharpe": float(np.median([r["best_is_sharpe_ann"] for r in rows])),
            "median_oos_sharpe": float(np.median([r["its_oos_sharpe_ann"] for r in rows])),
            "median_pbo": float(np.median([r["pbo"] for r in rows])),
            "frac_psr_is_ge_095": float(np.mean([(r["psr_is_single_test"] or 0) >= 0.95 for r in rows])),
            "frac_dsr_is_ge_095": float(np.mean([(r["dsr_is_n200"] or 0) >= 0.95 for r in rows]))}


def scen_D():
    """Profit from a few days: losing noise strategy + 3 outsized winning days."""
    rng = np.random.default_rng(42)
    days = pd.bdate_range("2018-01-01", "2023-12-31")
    tr = [{"entry_time": (d + pd.Timedelta(hours=9)).isoformat(), "exit_time": (d + pd.Timedelta(hours=12)).isoformat(),
           "side": "long", "entry_price": 100, "exit_price": 100, "qty": 1, "pnl": float(rng.normal(-1.0, 25))}
          for d in days]
    for d in days[[200, 700, 1200]]:
        tr.append({"entry_time": (d + pd.Timedelta(hours=14)).isoformat(),
                   "exit_time": (d + pd.Timedelta(hours=15)).isoformat(), "side": "long", "entry_price": 100,
                   "exit_price": 100, "qty": 1, "pnl": 1500.0})
    c = robustness.concentration(tr, CAPITAL, "2018-01-01", "2024-01-01")
    daily = validation.daily_returns_from_trades(tr, CAPITAL)
    cis = robustness.bootstrap_cis(daily.to_numpy(), [t["pnl"] for t in tr], n=500)
    ev = robustness.verdict(oos_net=sum(t["pnl"] for t in tr), eff_obs=robustness.effective_independent_obs(
        daily.to_numpy(), tr), cis=cis, null=None, baselines_beaten=None, cost_mc=None, execution=None,
        neighbourhood=None, conc=c, regimes=None)
    return {"description": "Winst uit 3 dagen.", "net": sum(t["pnl"] for t in tr), "flags": c["flags"],
            "top3_days_share": c["top3_days_share"], "net_without_k_required_days": c["net_without_k_required_days"],
            "k_days_required": c["k_days_required"], "verdict": ev["verdict"], "reasons": ev["reasons"]}


def scen_E(n_exp=10, years=6):
    """Costs turn a gross edge into a loss: a weak drift edge traded by high-turnover MA crosses; gross positive at
    zero spread, negative at a 4 bp spread (~2.7x the base synthetic spread)."""
    grid = [{"fast": 3, "slow": 12}, {"fast": 4, "slow": 16}, {"fast": 3, "slow": 24}]
    rows = []
    for i in range(n_exp):
        bid, ask = bars(years, seed=7000 + i, trend=0.12, spread_bp=4.0)
        end = str(bid.index[-1] + pd.Timedelta(hours=1))[:10]
        res = pipeline(bid, ask, {"ma_cross": grid}, wf_start="2012-01-02", end=end, seed=i)
        bid0, ask0 = bars(years, seed=7000 + i, trend=0.12, spread_bp=0.0)
        gross = sum(t["pnl"] for t in run(bid0, ask0, "ma_cross", grid[0], "2014-01-01", end)["trades"])
        net = sum(t["pnl"] for t in run(bid, ask, "ma_cross", grid[0], "2014-01-01", end)["trades"])
        rows.append({"gross_zero_spread_net_oos": gross, "same_config_net_after_costs_oos": net,
                     "pipeline_oos_net": res["oos_net"], "verdict": res["evidence_verdict"],
                     "final_pass": res["final_pass"], "reasons": res["verdict_reasons"]})
    return {"description": "Kosten maken een bruto-edge verliesgevend (zwakke drift, hoge omzet, 4 bp spread).",
            "experiments": rows, "pass": summarize(rows),
            "frac_gross_positive": float(np.mean([r["gross_zero_spread_net_oos"] > 0 for r in rows])),
            "frac_net_negative": float(np.mean([r["same_config_net_after_costs_oos"] <= 0 for r in rows]))}


def scen_F():
    """50 near-identical variants (same signal, jittered parameters) vs 50 independent strategies."""
    bid, ask = bars(6, seed=77)
    variants = [{"fast": 12 + (k % 3), "slow": 96 + k} for k in range(50)]
    cols = {i: validation.daily_pnl_from_trades(run(bid, ask, "ma_cross", v, "2010-01-04", "2016-01-01")["trades"])
            for i, v in enumerate(variants)}
    corr_frame = pd.concat(cols, axis=1).fillna(0.0)
    indep = {i: validation.daily_pnl_from_trades(run(bid, ask, "random", {"seed": i}, "2010-01-04", "2016-01-01")[
        "trades"]) for i in range(50)}
    ind_frame = pd.concat(indep, axis=1).fillna(0.0)
    a, b = et.effective_n(corr_frame), et.effective_n(ind_frame)
    V = 1 / 1500
    return {"description": "Gecorreleerde varianten: effectieve N.",
            "correlated_variants": {k: a[k] for k in ("n_raw", "n_eff", "eigen", "onc_k", "mean_abs_corr")},
            "independent": {k: b[k] for k in ("n_raw", "n_eff", "eigen", "onc_k", "mean_abs_corr")},
            "sr0_ann_raw_vs_eff": [et.expected_max_sr(50, V) * math.sqrt(252),
                                   et.expected_max_sr(a["n_eff"], V) * math.sqrt(252)]}


def scen_G():
    """Many families: the candidate's family DSR vs the global (search-level) DSR; gate = global."""
    rng = np.random.default_rng(5)
    T = 1500
    rows = []
    for f in range(10):
        for k in range(50):
            rows.append({"hypothesis_id": f"{f}-{k}", "family": f"fam{f}", "group": f"fam{f}-{k // 10}",
                         "sr": float(rng.standard_normal() / math.sqrt(T)), "T": T, "stage": "L5"})
    out = {"description": "10 families x 50 trials; familie- vs globale DSR voor een sterke en een middelmatige kandidaat."}
    for name, mean in (("strong_~2.5ann", 0.0016), ("moderate_~1.6ann", 0.0010)):
        daily = rng.normal(mean, 0.01, T)
        mt = et.multiple_testing_records(daily, None, family="fam0", rows=rows)
        out[name] = {"family": {k: mt["family"][k] for k in ("raw_n", "effective_n", "sr0_annualized", "value")},
                     "global": {k: mt["global"][k] for k in ("raw_n", "effective_n", "sr0_annualized", "value",
                                                             "observed_sr_annualized")},
                     "gate": et.gate(mt, 0.95)}
    return out


def scen_H(tmp: Path):
    """Leakage: search code asking for sealed bars is refused; purge removes trades whose outcome overlaps test."""
    from strategy_lab.research import protocol
    bid, ask = bars(3, seed=3, start="2022-01-03")
    path = tmp / "h_frames.parquet"
    protocol.frames_to_file(bid, ask, path)
    sd = protocol.SearchData.from_file(path, "2024-01-01", symbol="SYN", timeframe="H1")
    blocked = 0
    for s, e in (("2023-06-01", "2024-02-01"), ("2024-01-01", "2024-06-01"), ("2023-12-31", "2024-01-02")):
        try:
            sd.slice(s, e)
        except protocol.SealedAccessError:
            blocked += 1
    held_last = sd.bid.index.max()
    df = nested_wf._frame([{"entry_time": "2023-03-30T10:00:00", "exit_time": "2023-04-03T10:00:00", "pnl": 1.0},
                           {"entry_time": "2023-03-01T10:00:00", "exit_time": "2023-03-02T10:00:00", "pnl": 1.0}])
    purged = nested_wf.slice_trades(df, "2023-01-01", "2023-04-01", purge_until="2023-04-01")
    return {"description": "Lekkage geblokkeerd.", "sealed_requests": 3, "blocked": blocked,
            "search_data_last_bar": str(held_last), "sealed_start": "2024-01-01",
            "purge_kept": len(purged), "purge_removed": 2 - len(purged)}


def scen_I(tmp: Path):
    """Cross-market cherry-pick: one strategy on 6 markets, the best market is picked. Recorded as 6 selection
    experiments; the multiplicity-aware DSR of the pick is lower than its naive PSR (evidence not improved)."""
    from strategy_lab.research.ledger import ResearchLedger
    led = ResearchLedger(tmp / "i_ledger.sqlite3")
    res = []
    for m in range(6):
        bid, ask = bars(6, seed=300 + m)
        tr = run(bid, ask, "ma_cross", {"fast": 12, "slow": 96}, "2012-01-02", "2016-01-01")["trades"]
        d = validation.daily_returns_from_trades(tr, CAPITAL)
        led.record_data_usage(run_id="bench_I", symbol=f"SYN{m}", timeframe="H1", start="2012-01-02", end="2016-01-01",
                              purpose="SELECTION_EXPERIMENT")
        led.record(kind="DISCOVERY", family="ma_cross", symbol=f"SYN{m}", timeframe="H1", start="2012-01-02",
                   end="2016-01-01", hypothesis_id=f"ma|SYN{m}",
                   metrics={"sharpe_daily": float(d.mean() / d.std(ddof=1) * math.sqrt(252)), "n_days": len(d)},
                   statistics={"factory": {"stage": "L5", "signature": f"ma|SYN{m}"}})
        res.append((float(d.mean() / d.std(ddof=1)), d))
    best = max(range(6), key=lambda k: res[k][0])
    d = res[best][1]
    mt = et.multiple_testing_records(d.to_numpy(), led, family="ma_cross")
    return {"description": "Cherry-pick over 6 markten.", "selection_experiments_recorded":
            len(led.data_usage(purpose="SELECTION_EXPERIMENT")), "picked_market": f"SYN{best}",
            "picked_sharpe_ann": res[best][0] * math.sqrt(252), "naive_psr": validation.probabilistic_sharpe_ratio(d)["value"],
            "dsr_global": mt["global"]["value"], "raw_n": mt["global"]["raw_n"],
            "evidence_improved": bool((mt["global"]["value"] or 0) > (validation.probabilistic_sharpe_ratio(d)["value"]
                                                                       or 0))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", default="A,B,C,D,E,F,G,H,I")
    ap.add_argument("--n-a", type=int, default=100)
    ap.add_argument("--n-b", type=int, default=20)
    ap.add_argument("--out", default="benchmark_results.json")
    ap.add_argument("--a-start", type=int, default=0)
    a = ap.parse_args()
    only = set(a.only.split(","))
    tmp = OUT / "_tmp"
    tmp.mkdir(exist_ok=True)
    n_a, n_b = (6, 3) if a.quick else (a.n_a, a.n_b)
    path = OUT / a.out
    results = json.loads(path.read_text()) if path.is_file() else {}
    for key, fn in (("A", lambda: scen_A(n_a, start=a.a_start)), ("B", lambda: scen_B(n_b)), ("C", lambda: scen_C(3 if a.quick else 20)),
                    ("D", scen_D), ("E", lambda: scen_E(3 if a.quick else 10)), ("F", scen_F), ("G", scen_G),
                    ("H", lambda: scen_H(tmp)), ("I", lambda: scen_I(tmp))):
        if key not in only:
            continue
        t0 = time.time()
        if key == "I" and (tmp / "i_ledger.sqlite3").exists():
            (tmp / "i_ledger.sqlite3").unlink()
        results[key] = fn()
        results[key]["seconds"] = round(time.time() - t0, 1)
        results[key]["quick"] = a.quick
        print(key, round(time.time() - t0, 1), "s", flush=True)
        path.write_text(json.dumps(results, indent=1, default=str))
    print("written", path)


if __name__ == "__main__":
    main()
