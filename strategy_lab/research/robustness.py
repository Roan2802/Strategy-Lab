"""Robustness evidence for one strategy's out-of-sample trades: bootstrap CIs, cost / execution Monte Carlo,
parameter neighbourhood, causal regimes, concentration, baselines / null hypothesis and the evidence verdict.

Verdict classes (what the evidence supports, not a forecast):
* NO_EVIDENCE                  - no positive edge after costs, or it does not beat the null / baselines.
* POSITIVE_UNCERTAIN           - positive point estimate, but the uncertainty (CI, costs, neighbourhood,
                                 concentration, regimes) does not exclude chance or fragility.
* SUPPORTED_UNDER_ASSUMPTIONS  - positive, CI excludes zero on effective observations, beats the null and the
                                 baselines, survives cost/execution stress, broad parameter plateau, not
                                 concentrated. Still conditional on the cost model, data and the absence of
                                 regime change.
* INSUFFICIENT_EVIDENCE        - too few effective independent observations to say anything.
Trade-level re-pricing (cost MC, timing shifts, random-entry null) does not re-simulate stops/targets; the
deterministic execution scenarios re-run the engine (entry/exit delay, spread, slippage).
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np
import pandas as pd

from . import effective_trials, validation

INSUFFICIENT = validation.INSUFFICIENT
VERDICTS = ("NO_EVIDENCE", "POSITIVE_UNCERTAIN", "SUPPORTED_UNDER_ASSUMPTIONS", INSUFFICIENT)
MIN_EFFECTIVE_OBS = 60


def _f(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _tdf(trades) -> pd.DataFrame:
    df = pd.DataFrame(list(trades or []))
    if df.empty:
        return df
    df["pnl"] = df["pnl"].astype(float)
    for c in ("entry_time", "exit_time"):
        df[c] = pd.to_datetime(df[c], utc=True).dt.tz_localize(None)
    return df.sort_values("exit_time", kind="stable").reset_index(drop=True)


# ------------------------------------------------------------------------------------------------ bootstrap
def _sharpe(x, axis=None):
    x = np.asarray(x, dtype=float)
    sd = x.std(axis=axis, ddof=1)
    return np.where(sd > 0, x.mean(axis=axis) / np.where(sd > 0, sd, 1), 0.0)


def bootstrap_cis(daily_returns, trade_pnls, n: int = 2000, seed: int = 0, confidence: float = 0.95) -> dict:
    """Moving-block (Kunsch) and stationary (Politis-Romano) bootstrap CIs, block ~ T^(1/3) - mean daily return,
    daily Sharpe (per day) and mean trade P&L."""
    out = {}
    for name, x, stat in (("daily_mean", daily_returns, np.mean), ("daily_sharpe", daily_returns, _sharpe),
                          ("trade_mean", trade_pnls, np.mean)):
        x = np.asarray(list(x) if not isinstance(x, (np.ndarray, pd.Series)) else x, dtype=float)
        out[name] = {kind: validation.bootstrap_confidence_interval(x, stat, n=n, block="auto", seed=seed,
                                                                     confidence=confidence, method=kind)
                     for kind in ("moving", "stationary")}
        lows = [v["ci_low"] for v in out[name].values() if v.get("status") == "OK"]
        out[name]["ci_low_min"] = min(lows) if lows else None
    return out


def effective_independent_obs(daily_returns, trades) -> dict:
    """Effective observations: days (Bartlett autocorrelation) and trades (overlap + autocorrelation)."""
    d = effective_trials.effective_obs(np.asarray(daily_returns, dtype=float))
    df = _tdf(trades)
    out = {"days": d["T"], "days_eff": d["T_eff"], "days_inflation": d["inflation"], "trades": int(len(df))}
    if len(df) >= 2:
        span = (df["exit_time"].max() - df["entry_time"].min()).total_seconds()
        held = (df["exit_time"] - df["entry_time"]).dt.total_seconds().clip(lower=0).sum()
        conc = max(1.0, held / span) if span > 0 else 1.0
        t = effective_trials.effective_obs(df["pnl"].to_numpy())
        out.update(avg_concurrency=float(conc), trade_inflation=t["inflation"],
                   trades_eff=float(len(df) / conc / t["inflation"]))
    else:
        out.update(avg_concurrency=None, trade_inflation=None, trades_eff=float(len(df)))
    out["effective_obs"] = float(min(out["days_eff"], out["trades_eff"])) if len(df) else 0.0
    return out


# ------------------------------------------------------------------------------------------------ costs / execution
def _price_mult(df: pd.DataFrame) -> np.ndarray:
    """Account currency per price unit per lot, implied by each trade: (pnl + costs) / (signed move * qty)."""
    sign = np.where(df["side"].astype(str) == "long", 1.0, -1.0)
    move = (df["exit_price"].astype(float) - df["entry_price"].astype(float)) * sign
    gross = df["pnl"] + df.get("costs", pd.Series(0.0, index=df.index)).astype(float)
    qty = df["qty"].astype(float).replace(0, np.nan)
    m = gross / (move * qty)
    m = m.where(np.isfinite(m) & (move.abs() > 1e-12) & (m > 0))
    med = float(np.nanmedian(m)) if m.notna().any() else 1.0
    return m.fillna(med).to_numpy(float)


def spread_at(bid: pd.DataFrame, ask: pd.DataFrame, times) -> np.ndarray:
    """Spread (ask - bid open) of the bar at or before each time."""
    sp = (ask["open"] - bid["open"]).to_numpy(float)
    idx = bid.index.tz_localize(None) if bid.index.tz is not None else bid.index
    pos = np.clip(idx.searchsorted(pd.DatetimeIndex(times), side="right") - 1, 0, len(sp) - 1)
    return sp[pos]


def cost_monte_carlo(trades, *, spread_points=None, n: int = 1000, spread_mult_mean: float = 1.25,
                     spread_mult_cv: float = 0.5, slip_mean_points: float = 0.0, seed: int = 0) -> dict:
    """Random per-trade cost: spread multiplier ~ lognormal(mean ``spread_mult_mean``, cv ``spread_mult_cv``) on the
    real spread paid at entry and exit, plus exponential slippage (mean ``slip_mean_points``) per fill."""
    df = _tdf(trades)
    if len(df) < validation.MIN_OBS:
        return {"status": INSUFFICIENT, "n_trades": int(len(df))}
    rng = np.random.default_rng(seed)
    mult = _price_mult(df)
    qty = df["qty"].astype(float).to_numpy()
    sp = np.asarray(spread_points if spread_points is not None else np.zeros(len(df)), dtype=float)
    if sp.ndim == 0:
        sp = np.full(len(df), float(sp))
    sig = math.sqrt(math.log(1 + spread_mult_cv ** 2))
    mu = math.log(spread_mult_mean) - sig ** 2 / 2
    k = rng.lognormal(mu, sig, (n, len(df)))
    slip = rng.exponential(slip_mean_points, (n, len(df), 2)).sum(axis=2) if slip_mean_points > 0 else 0.0
    extra = ((k - 1.0) * sp + slip) * qty * mult  # spread is paid once per round trip (buy ask, sell bid)
    pnl = df["pnl"].to_numpy()[None, :] - extra
    net = pnl.sum(axis=1)
    wins = np.where(pnl > 0, pnl, 0).sum(axis=1)
    loss = -np.where(pnl < 0, pnl, 0).sum(axis=1)
    pf = np.where(loss > 0, wins / np.where(loss > 0, loss, 1), 999.0)
    return {"status": "OK", "n": n, "n_trades": int(len(df)), "base_net": float(df["pnl"].sum()),
            "net_p05": float(np.quantile(net, 0.05)), "net_p50": float(np.median(net)),
            "pf_p05": float(np.quantile(pf, 0.05)), "pf_p50": float(np.median(pf)),
            "prob_loss": float((net <= 0).mean()),
            "assumptions": {"spread_mult_mean": spread_mult_mean, "spread_mult_cv": spread_mult_cv,
                            "slip_mean_points": slip_mean_points, "spread_source": "echte bid/ask bij entry"
                            if spread_points is not None else "geen spreadgegevens"}}


def _bar_pos(index: pd.DatetimeIndex, times) -> np.ndarray:
    idx = index.tz_localize(None) if index.tz is not None else index
    return np.clip(idx.searchsorted(pd.DatetimeIndex(times), side="left"), 0, len(idx) - 1)


def _reprice(side, bid, ask, i_in, i_out):
    """Entry at the executable open of bar i_in, exit at the executable open of bar i_out."""
    bo, ao = bid["open"].to_numpy(float), ask["open"].to_numpy(float)
    long = side == "long"
    entry = np.where(long, ao[i_in], bo[i_in])
    exit_ = np.where(long, bo[i_out], ao[i_out])
    return (exit_ - entry) * np.where(long, 1.0, -1.0)


def timing_monte_carlo(trades, bid: pd.DataFrame, ask: pd.DataFrame, *, n: int = 500, max_shift_bars: int = 1,
                       seed: int = 0) -> dict:
    """Trade timing uncertainty: entry and exit bars shifted independently by U{-k..k}; trades re-priced at the
    executable bar opens with the same size and costs. Stops/targets are not re-simulated (approximation)."""
    df = _tdf(trades)
    if len(df) < validation.MIN_OBS or len(bid) < 10:
        return {"status": INSUFFICIENT, "n_trades": int(len(df))}
    rng = np.random.default_rng(seed)
    mult, qty = _price_mult(df), df["qty"].astype(float).to_numpy()
    costs = df.get("costs", pd.Series(0.0, index=df.index)).astype(float).to_numpy()
    i_in, i_out = _bar_pos(bid.index, df["entry_time"]), _bar_pos(bid.index, df["exit_time"])
    side = df["side"].astype(str).to_numpy()
    base = _reprice(side, bid, ask, i_in, i_out) * qty * mult - costs
    nets = []
    for _ in range(n):
        a = np.clip(i_in + rng.integers(-max_shift_bars, max_shift_bars + 1, len(df)), 0, len(bid) - 1)
        b = np.clip(i_out + rng.integers(-max_shift_bars, max_shift_bars + 1, len(df)), 0, len(bid) - 1)
        b = np.maximum(a + 1, b).clip(max=len(bid) - 1)
        nets.append(float((_reprice(side, bid, ask, a, b) * qty * mult - costs).sum()))
    nets = np.array(nets)
    return {"status": "OK", "n": n, "max_shift_bars": max_shift_bars, "base_net_repriced": float(base.sum()),
            "net_p05": float(np.quantile(nets, 0.05)), "net_p50": float(np.median(nets)),
            "prob_loss": float((nets <= 0).mean())}


def adverse_fill(trades, bid: pd.DataFrame, ask: pd.DataFrame, frac: float = 0.25) -> dict:
    """Adverse intrabar execution: every entry filled ``frac`` of the entry bar's range worse (long: towards the
    high, short: towards the low). Same size, same exits."""
    df = _tdf(trades)
    if df.empty or len(bid) < 2:
        return {"status": INSUFFICIENT}
    i = _bar_pos(bid.index, df["entry_time"])
    rngs = (ask["high"].to_numpy(float) - bid["low"].to_numpy(float))[i]
    extra = frac * rngs * df["qty"].astype(float).to_numpy() * _price_mult(df)
    net = float((df["pnl"].to_numpy() - extra).sum())
    return {"status": "OK", "frac": frac, "base_net": float(df["pnl"].sum()), "net": net, "positive": net > 0}


def execution_stress(backtest_fn: Callable, params: dict, start, end, capital: float, commission_per_lot: float,
                     scenarios: dict | None = None) -> dict:
    """Engine re-runs: entry delay, exit delay, both (delayed execution), wider spread, extra slippage."""
    scenarios = scenarios or {"entry_delay_1": {"entry_delay_bars": 1}, "exit_delay_1": {"exit_delay_bars": 1},
                              "delayed_execution": {"entry_delay_bars": 1, "exit_delay_bars": 1},
                              "spread_x1.5": {"spread_multiplier": 1.5}, "slippage_+2pt": {"slippage_points": 2.0}}
    out = {}
    for name, kw in scenarios.items():
        r = backtest_fn(params, start, end, commission_per_lot=commission_per_lot, **kw)
        tm = validation.trade_metrics(r.get("trades", []), capital)
        out[name] = {"net_profit": tm["net_profit"], "profit_factor": tm["profit_factor"], "trades": tm["n_trades"],
                     "kwargs": kw}
    nets = [v["net_profit"] for v in out.values()]
    return {"status": "OK", "scenarios": out, "all_positive": all(x > 0 for x in nets),
            "n_positive": int(sum(x > 0 for x in nets)), "n": len(nets)}


# ------------------------------------------------------------------------------------------------ parameters
_TIME_HINTS = ("hour", "uur", "minute", "minuut", "hh", "session", "start_h", "end_h")


def param_unit(name: str, spec: dict) -> str:
    if "choices" in spec:
        return "categorical"
    if any(h in name.lower() for h in _TIME_HINTS):
        return "time"
    return "int" if spec.get("type") == "int" else "float"


def _moves(name, sp, value, steps=(1, 2)) -> list:
    unit = param_unit(name, sp)
    if unit == "categorical":
        return [c for c in sp["choices"] if c != value]
    lo, hi = sp.get("min"), sp.get("max")
    if unit in ("int", "time"):
        step = int(sp.get("step") or 1)
        vals = [int(value) + s * k * step for k in steps for s in (-1, 1)]
    else:
        step = float(sp.get("step") or 0) or 0.1 * abs(float(value) or 1.0)
        step = max(step, 0.1 * abs(float(value))) if value else step
        vals = [round(float(value) + s * k * step, 10) for k in steps for s in (-1, 1)]
    if unit == "time" and (hi is None or hi <= 24):
        vals = [v % 24 for v in vals]
    return [v for v in vals if (lo is None or v >= lo) and (hi is None or v <= hi) and v != value]


def parameter_neighbourhood(backtest_fn: Callable, params: dict, space: dict, start, end, capital: float, *,
                            heatmap_size: int = 3, objective: str = "net_profit") -> dict:
    """Unit-aware one-at-a-time neighbours (int/time: +-1,2 steps; float: +-1,2 steps of max(step, 10%);
    categorical: every other choice) and a heatmap over the two most sensitive numeric parameters. Classifies
    BROAD_PLATEAU / MODERATE / ISOLATED_PEAK."""
    def run(p):
        tm = validation.trade_metrics(backtest_fn(p, start, end).get("trades", []), capital)
        return tm["net_profit"] if objective == "net_profit" else tm["sharpe"]

    base = run(params)
    nbs = []
    for name, sp in space.items():
        if name not in params:
            continue
        for v in _moves(name, sp, params[name]):
            val = run({**params, name: v})
            nbs.append({"param": name, "unit": param_unit(name, sp), "value": v, "objective": val})
    out = {"base": base, "objective": objective, "neighbours": nbs, "n_neighbours": len(nbs)}
    if not nbs:
        out.update(classification=INSUFFICIENT, status=INSUFFICIENT)
        return out
    vals = np.array([n["objective"] for n in nbs])
    frac_pos = float((vals > 0).mean())
    rel = float(np.median(vals) / base) if base > 0 else None
    sens = {}
    for nb in nbs:
        sens.setdefault(nb["param"], []).append(abs(nb["objective"] - base))
    numeric = [k for k in sens if param_unit(k, space[k]) != "categorical"]
    top = sorted(numeric, key=lambda k: -float(np.mean(sens[k])))[:2]
    heat = None
    if len(top) == 2 and heatmap_size >= 3:
        half = heatmap_size // 2
        axes = []
        for k in top:
            mv = sorted(set(_moves(k, space[k], params[k], steps=tuple(range(1, half + 1)))) | {params[k]})
            axes.append(mv)
        grid = [[run({**params, top[0]: a, top[1]: b}) for b in axes[1]] for a in axes[0]]
        heat = {"x_param": top[0], "x_values": axes[0], "y_param": top[1], "y_values": axes[1], "values": grid}
        g = np.array(grid, dtype=float)
        out["heatmap_frac_positive"] = float((g > 0).mean())
    is_peak = base > 0 and vals.max() <= base and (rel is None or rel < 0.3 or frac_pos < 0.5)
    if base <= 0:
        cls = "NO_EDGE_AT_BASE"
    elif frac_pos >= 0.7 and (rel or 0) >= 0.5 and (heat is None or out["heatmap_frac_positive"] >= 0.6):
        cls = "BROAD_PLATEAU"
    elif is_peak:
        cls = "ISOLATED_PEAK"
    else:
        cls = "MODERATE"
    out.update(frac_positive=frac_pos, median_relative=rel, sensitivity={k: float(np.mean(v)) for k, v in sens.items()},
               heatmap=heat, classification=cls, status="OK")
    return out


# ------------------------------------------------------------------------------------------------ regimes
def causal_regimes(bid: pd.DataFrame, ask: pd.DataFrame | None = None, adx_period: int = 14) -> dict:
    """Day -> labels using ONLY data of earlier days (label of day d is computed from closes up to d-1):
    trend/range (daily ADX >= 25), vol high/low (20-day realised vol vs its expanding median), up/down (sign of
    the 50-day return), spread high/low (daily median spread vs expanding median)."""
    from runtime.strategy_lab import rule_engine as engine
    if bid is None or len(bid) < 50:
        return {}
    d = bid.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    if len(d) < 60:
        return {}
    adx = pd.Series(engine.adx(d["high"].to_numpy(float), d["low"].to_numpy(float), d["close"].to_numpy(float),
                               adx_period), index=d.index)
    rv = np.log(d["close"]).diff().rolling(20).std()
    rv_med = rv.expanding(min_periods=60).median()
    mom = d["close"].pct_change(50)
    lab = pd.DataFrame({"trend": np.where(adx >= 25, "trend", "range"),
                        "vol": np.where(rv >= rv_med, "vol_hoog", "vol_laag"),
                        "direction": np.where(mom > 0, "op", "neer")}, index=d.index)
    valid = adx.notna() & rv.notna() & rv_med.notna() & mom.notna()
    if ask is not None and len(ask) == len(bid):
        sp = (ask["close"] - bid["close"]).resample("1D").median().reindex(d.index)
        sp_med = sp.expanding(min_periods=60).median()
        lab["spread"] = np.where(sp >= sp_med, "spread_hoog", "spread_laag")
        valid &= sp.notna() & sp_med.notna()
    lab = lab[valid].shift(1).dropna()  # label of day d from information up to d-1
    return {k.strftime("%Y-%m-%d"): dict(v) for k, v in lab.iterrows()}


def regime_stability(trades, labels: dict, min_trades: int = 10) -> dict:
    """Per dimension (trend, vol, direction, spread, session, year): trades, net, expectancy, PF per label.
    Stable when at most a third of the labels with >= ``min_trades`` trades lose and the profit is not concentrated
    (> 80% of the positive profit) in a RARE label (< 30% of the trades). A trend strategy earning in trend days
    that make up most of its trades shows its mechanism, not a fragility."""
    from .distribution import session_of
    df = _tdf(trades)
    if df.empty:
        return {"status": INSUFFICIENT}
    day = df["entry_time"].dt.strftime("%Y-%m-%d")
    dims = {"session": df["entry_time"].map(lambda t: session_of(pd.Timestamp(t))),
            "year": df["entry_time"].dt.year.astype(str)}
    for k in ("trend", "vol", "direction", "spread"):
        dims[k] = day.map(lambda x, k=k: (labels.get(x) or {}).get(k, "onbekend"))
    out, flags = {}, []
    for dim, lab in dims.items():
        rows = {}
        for name, g in df.groupby(lab):
            p = g["pnl"].to_numpy()
            w, l = p[p > 0].sum(), -p[p < 0].sum()
            rows[str(name)] = {"trades": int(len(p)), "net_profit": float(p.sum()), "expectancy": float(p.mean()),
                               "profit_factor": float(w / l) if l > 0 else (999.0 if w > 0 else 0.0)}
        big = {k: v for k, v in rows.items() if v["trades"] >= min_trades and k != "onbekend"}
        neg = [k for k, v in big.items() if v["net_profit"] < 0]
        pos_tot = sum(max(v["net_profit"], 0) for v in big.values())
        top = max(big.items(), key=lambda kv: kv[1]["net_profit"], default=(None, None))
        share = (max(top[1]["net_profit"], 0) / pos_tot) if top[0] and pos_tot > 0 else None
        # one losing year / session is normal for a real edge; dependence = many losing labels or one label
        # carrying the result
        n_dim = sum(v["trades"] for v in big.values())
        top_rare = bool(top[0]) and n_dim > 0 and big[top[0]]["trades"] / n_dim < 0.3
        stable = bool(big) and len(neg) <= len(big) / 3.0 and (share is None or share <= 0.8 or not top_rare)
        if not stable and big:
            flags.append(dim)
        out[dim] = {"labels": rows, "negative_labels": neg, "top_label": top[0], "top_share": share,
                    "stable": stable, "evaluated_labels": len(big)}
    out["unstable_dimensions"] = flags
    out["status"] = "OK"
    return out


# ------------------------------------------------------------------------------------------------ concentration
def concentration(trades, capital: float, start, end) -> dict:
    """Profit concentration with frequency-aware thresholds: k_days = max(3, ceil(2% of active days)) must be
    removable without the result turning negative; loss clustering vs an iid sequence (Wald-Wolfowitz runs test);
    rolling PF / expectancy over windows of ~3 months of trades; inactivity vs the expected longest gap."""
    df = _tdf(trades)
    n = len(df)
    if n < 10:
        return {"status": INSUFFICIENT, "n_trades": n}
    p = df["pnl"].to_numpy()
    net = float(p.sum())
    gross = float(p[p > 0].sum())
    srt = np.sort(p)[::-1]
    days = df.groupby(df["exit_time"].dt.normalize())["pnl"].sum().sort_values(ascending=False)
    n_days = len(days)
    out = {"status": "OK", "n_trades": n, "active_days": n_days, "net_profit": net}
    for k in (3, 5, 10):
        out[f"top{k}_trades_share"] = float(srt[:k].sum() / gross) if gross > 0 else None
        out[f"top{k}_days_share"] = float(days.iloc[:k].clip(lower=0).sum() / gross) if gross > 0 else None
        out[f"net_without_top{k}_days"] = float(net - days.iloc[:k].sum())
    k_req = max(3, int(math.ceil(0.02 * n_days)))
    out["k_days_required"] = k_req
    out["net_without_k_required_days"] = float(net - days.iloc[:k_req].sum())
    # loss clustering
    s = np.sign(p)
    s = s[s != 0]
    n1, n2 = int((s > 0).sum()), int((s < 0).sum())
    runs = int(1 + (s[1:] != s[:-1]).sum()) if len(s) else 0
    if n1 and n2:
        mu = 2 * n1 * n2 / (n1 + n2) + 1
        var = 2 * n1 * n2 * (2 * n1 * n2 - n1 - n2) / ((n1 + n2) ** 2 * (n1 + n2 - 1))
        z = (runs - mu) / math.sqrt(var) if var > 0 else 0.0
    else:
        z = 0.0
    streak, cur = 0, 0
    for x in s:
        cur = cur + 1 if x < 0 else 0
        streak = max(streak, cur)
    q = n2 / len(s) if len(s) else 0
    exp_streak = math.log(max(len(s), 2)) / math.log(1 / q) if 0 < q < 1 else None
    out["loss_clustering"] = {"runs_z": float(z), "clustered": z < -1.96, "max_losing_streak": streak,
                              "expected_max_streak_iid": exp_streak}
    # rolling PF / expectancy
    weeks = max((pd.Timestamp(end) - pd.Timestamp(start)).days / 7.0, 1.0)
    w = int(max(20, round(n / weeks * 13)))
    roll = []
    for i in range(0, max(1, n - w + 1), max(1, w // 2)):
        seg = p[i:i + w]
        l = -seg[seg < 0].sum()
        roll.append({"start": str(df["exit_time"].iloc[i])[:10], "expectancy": float(seg.mean()),
                     "profit_factor": float(seg[seg > 0].sum() / l) if l > 0 else 999.0})
    out["rolling"] = {"window_trades": w, "rows": roll,
                      "frac_positive_expectancy": float(np.mean([r["expectancy"] > 0 for r in roll]))}
    # inactivity relative to frequency
    t = df["entry_time"].sort_values()
    gaps = np.diff(np.concatenate([[pd.Timestamp(start).value], t.astype("int64").to_numpy(),
                                   [pd.Timestamp(end).value]])) / 86400e9
    lam = n / max((pd.Timestamp(end) - pd.Timestamp(start)).days, 1)
    exp_gap = math.log(max(n, 2)) / lam if lam > 0 else None
    out["inactivity"] = {"longest_gap_days": float(gaps.max()), "expected_longest_gap_days": exp_gap,
                         "excessive": bool(exp_gap and gaps.max() > 3 * exp_gap)}
    out["flags"] = [f for f, bad in (("profit_in_few_days", out["net_without_k_required_days"] <= 0),
                                     ("unstable_rolling", out["rolling"]["frac_positive_expectancy"] < 0.5),
                                     ("inactivity", out["inactivity"]["excessive"])) if bad]
    # serial dependence of losses is accounted for by T_eff and the block bootstrap and its cost by the drawdown
    # check; on its own it is a risk characteristic (e.g. trend following in ranges), not evidence against the edge
    out["info_flags"] = ["loss_clustering"] if out["loss_clustering"]["clustered"] else []
    return out


# ------------------------------------------------------------------------------------------------ baselines / null
def random_entry_null(trades, bid: pd.DataFrame, ask: pd.DataFrame, *, n: int = 300, seed: int = 0) -> dict:
    """Null hypothesis 'entry timing carries no information': every trade keeps its size, holding time and costs
    but gets a random entry bar in the same period and a random side. p = (1 + #null >= actual) / (n + 1)."""
    df = _tdf(trades)
    if len(df) < validation.MIN_OBS or len(bid) < 50:
        return {"status": INSUFFICIENT, "n_trades": int(len(df))}
    rng = np.random.default_rng(seed)
    mult, qty = _price_mult(df), df["qty"].astype(float).to_numpy()
    costs = df.get("costs", pd.Series(0.0, index=df.index)).astype(float).to_numpy()
    i_in, i_out = _bar_pos(bid.index, df["entry_time"]), _bar_pos(bid.index, df["exit_time"])
    hold = np.maximum(1, i_out - i_in)
    actual = float(df["pnl"].sum())
    nets = np.empty(n)
    for k in range(n):
        a = rng.integers(0, max(1, len(bid) - hold.max() - 1), len(df))
        side = np.where(rng.random(len(df)) < 0.5, "long", "short")
        nets[k] = float((_reprice(side, bid, ask, a, np.minimum(a + hold, len(bid) - 1)) * qty * mult - costs).sum())
    return {"status": "OK", "n": n, "actual_net": actual, "null_mean": float(nets.mean()),
            "null_p95": float(np.quantile(nets, 0.95)), "p_value": float((1 + (nets >= actual).sum()) / (n + 1))}


def spec_variants(spec: dict) -> list[tuple[str, dict]]:
    """Simpler versions of a rules spec for baseline comparison: without the session filter, without trailing /
    break-even (simpler exits), fixed 2R target with no trailing / break-even / time exit."""
    import copy
    out = []
    if spec.get("session"):
        s = copy.deepcopy(spec)
        s.pop("session", None)
        out.append(("zonder_sessiefilter", s))
    if spec.get("trailing") or spec.get("break_even"):
        s = copy.deepcopy(spec)
        s.pop("trailing", None)
        s.pop("break_even", None)
        out.append(("eenvoudigere_exits", s))
    s = copy.deepcopy(spec)
    for k in ("trailing", "break_even", "time_exit_bars"):
        s.pop(k, None)
    s["target"] = {"type": "r_multiple", "value": 2.0}
    if s != spec:
        out.append(("vaste_2R_exit", s))
    return out


# ------------------------------------------------------------------------------------------------ verdict
def verdict(*, oos_net: float, eff_obs: dict, cis: dict, null: dict | None, baselines_beaten: bool | None,
            cost_mc: dict | None, execution: dict | None, neighbourhood: dict | None, conc: dict | None,
            regimes: dict | None, simpler_variants: dict | None = None) -> dict:
    """Evidence class with the reasons that decided it."""
    reasons = []
    if (eff_obs or {}).get("effective_obs", 0) < MIN_EFFECTIVE_OBS:
        return {"verdict": INSUFFICIENT, "reasons": [f"{(eff_obs or {}).get('effective_obs', 0):.0f} effectieve "
                                                     f"onafhankelijke waarnemingen < {MIN_EFFECTIVE_OBS}"]}
    if oos_net <= 0:
        return {"verdict": "NO_EVIDENCE", "reasons": ["OOS netto <= 0 na kosten"]}
    if null and null.get("status") == "OK" and null["p_value"] > 0.10:
        return {"verdict": "NO_EVIDENCE", "reasons": [f"niet beter dan willekeurige instap (p = {null['p_value']:.2f})"]}
    if baselines_beaten is False:
        return {"verdict": "NO_EVIDENCE", "reasons": ["verslaat de eenvoudige baselines niet"]}
    lo = (cis or {}).get("daily_mean", {}).get("ci_low_min")
    if lo is None or lo <= 0:
        reasons.append("bootstrap-CI van het daggemiddelde bevat 0")
    if null and null.get("status") == "OK" and null["p_value"] > 0.05:
        reasons.append(f"nulhypothese (willekeurige instap) niet verworpen op 5% (p = {null['p_value']:.2f})")
    if cost_mc and cost_mc.get("status") == "OK" and cost_mc["prob_loss"] > 0.2:
        reasons.append(f"kans op verlies bij variabele kosten {cost_mc['prob_loss']:.0%}")
    if execution and execution.get("status") == "OK":
        bad = [k for k, v in execution["scenarios"].items() if v["net_profit"] <= 0 and k != "delayed_execution"]
        if bad:
            reasons.append("verliesgevend in uitvoeringsscenario: " + ", ".join(bad))
    better = [k for k, v in (simpler_variants or {}).items() if v.get("net_profit") is not None
              and v["net_profit"] >= oos_net]
    if better:
        reasons.append("eenvoudigere variant minstens even goed (complexiteit niet gerechtvaardigd): " + ", ".join(better))
    if neighbourhood and neighbourhood.get("classification") not in ("BROAD_PLATEAU", INSUFFICIENT, None):
        reasons.append(f"parameteromgeving: {neighbourhood['classification']}")
    if conc and conc.get("flags"):
        reasons.append("concentratie: " + ", ".join(conc["flags"]))
    if regimes and regimes.get("unstable_dimensions"):
        reasons.append("regime-instabiel: " + ", ".join(regimes["unstable_dimensions"]))
    return {"verdict": "POSITIVE_UNCERTAIN" if reasons else "SUPPORTED_UNDER_ASSUMPTIONS", "reasons": reasons}


def similarity(a: dict, b: dict) -> dict:
    """Candidate similarity for diversity: daily-return and equity correlation, entry-day Jaccard, architecture
    (signature blocks Jaccard). ``a``/``b``: {"daily": Series, "entry_days": set, "blocks": list, "market": str}."""
    out = {}
    da, db = a.get("daily"), b.get("daily")
    if da is not None and db is not None and len(da) and len(db):
        both = pd.concat([da, db], axis=1).fillna(0.0)
        if len(both) > 20 and both.std().min() > 0:
            out["returns_corr"] = float(both.corr().iloc[0, 1])
            eq = both.cumsum()
            out["equity_corr"] = float(eq.corr().iloc[0, 1])
    ea, eb = set(a.get("entry_days") or ()), set(b.get("entry_days") or ())
    if ea and eb:
        out["entries_jaccard"] = len(ea & eb) / len(ea | eb)
    ba, bb = set(map(str, a.get("blocks") or ())), set(map(str, b.get("blocks") or ()))
    if ba and bb:
        out["architecture_jaccard"] = len(ba & bb) / len(ba | bb)
    out["same_market"] = a.get("market") == b.get("market")
    out["near_identical"] = bool((out.get("returns_corr") or 0) > 0.7 or (out.get("entries_jaccard") or 0) > 0.6
                                 or (out.get("architecture_jaccard", 0) >= 0.99 and out["same_market"]
                                     and (out.get("equity_corr") or 0) > 0.7))
    return out
