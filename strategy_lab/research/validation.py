"""Statistical Strategy Validation Engine.

Pure numpy/pandas implementation (no scipy needed: normal CDF/PPF are implemented here).

Conventions
-----------
* Trades are dicts: ``entry_time``/``exit_time`` (ISO UTC), ``side``, ``pnl`` (account currency,
  after costs), ``r_multiple``, ``costs``.
* Sharpe ratios inside PSR/DSR/MinTRL are NON-annualized, per observation.
* Trades are not i.i.d. (overlap, clustering, regime dependence). Wherever a p-value or
  confidence interval is produced it is computed on DAILY returns (or trade series) with a
  block bootstrap; i.i.d. results are labelled as such.
* ``status == "INSUFFICIENT_EVIDENCE"`` means: not enough data to say anything (n < 30 or zero
  variance). It is never a pass.
* Nothing here produces a "probability of future profit".
"""
from __future__ import annotations

import itertools
import math
from typing import Any, Callable

import numpy as np
import pandas as pd

EULER_GAMMA = 0.5772156649015329
MIN_OBS = 30
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
PF_CAP = 999.0


# --------------------------------------------------------------------------- normal dist
def norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def norm_sf(x: float) -> float:
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def norm_ppf(p: float) -> float:
    """Inverse normal CDF (Acklam rational approximation + one Halley step; ~1e-15)."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    plow = 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    elif p <= 1 - plow:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    else:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    e = norm_cdf(x) - p
    u = e * math.sqrt(2 * math.pi) * math.exp(x * x / 2)
    return x - u / (1 + x * u / 2)


def _f(x: Any) -> float | None:
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


# --------------------------------------------------------------------------- SR statistics
def _moments(returns) -> dict:
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 2:
        return {"n": n, "sr": 0.0, "skew": 0.0, "kurt": 3.0, "std": 0.0}
    mu, sd = r.mean(), r.std(ddof=1)
    pop_sd = r.std(ddof=0)
    if pop_sd <= 1e-12 * abs(mu):
        pop_sd = 0.0
    if pop_sd == 0:
        return {"n": n, "sr": 0.0, "skew": 0.0, "kurt": 3.0, "std": 0.0}
    z = (r - mu) / pop_sd
    return {"n": n, "sr": mu / sd, "skew": float((z ** 3).mean()),
            "kurt": float((z ** 4).mean()), "std": sd}


def psr_from_moments(sr: float, n: int, skew: float, kurt: float, sr_benchmark: float = 0.0) -> float:
    """PSR = Phi( (SR - SR*) sqrt(n-1) / sqrt(1 - g3 SR + (g4-1)/4 SR^2) ).

    ``kurt`` is the non-excess kurtosis g4 (3 for a normal)."""
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom <= 0:
        denom = 1e-12
    return norm_cdf((sr - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(denom))


def probabilistic_sharpe_ratio(returns, sr_benchmark: float = 0.0) -> dict:
    """Probabilistic Sharpe Ratio, Bailey & Lopez de Prado (2012), "The Sharpe Ratio Efficient
    Frontier", J. of Risk 15(2), eq. 9:

        PSR(SR*) = Phi( (SR_hat - SR*) * sqrt(n-1) / sqrt(1 - g3*SR_hat + (g4-1)/4 * SR_hat^2) )

    SR_hat is the per-observation (non-annualized) Sharpe ratio, g3 the skewness and g4 the
    (non-excess) kurtosis of the returns. ``sr_benchmark`` is in the same per-observation units.
    This is the probability that the TRUE Sharpe exceeds the benchmark given the sample - not a
    probability of future profit.
    """
    m = _moments(returns)
    out = {"value": None, "sr": _f(m["sr"]), "n": m["n"], "skew": _f(m["skew"]),
           "kurtosis": _f(m["kurt"]), "sr_benchmark": sr_benchmark}
    if m["n"] < MIN_OBS or m["std"] == 0:
        out["status"] = INSUFFICIENT
        return out
    out["value"] = psr_from_moments(m["sr"], m["n"], m["skew"], m["kurt"], sr_benchmark)
    out["status"] = "OK"
    return out


def expected_max_sr(n_trials: int, sr_variance: float) -> float:
    """E[max SR] under the null across N independent trials, Bailey & Lopez de Prado (2014),
    "The Deflated Sharpe Ratio", J. of Portfolio Mgmt 40(5), eq. (4):

        SR0 = sqrt(V) * ( (1-gamma) * Phi^-1(1 - 1/N) + gamma * Phi^-1(1 - 1/(N e)) )

    with gamma the Euler-Mascheroni constant."""
    if n_trials < 2 or sr_variance <= 0:
        return 0.0
    return math.sqrt(sr_variance) * ((1 - EULER_GAMMA) * norm_ppf(1 - 1.0 / n_trials)
                                     + EULER_GAMMA * norm_ppf(1 - 1.0 / (n_trials * math.e)))


def deflated_sharpe_ratio(returns, n_trials: int, sr_variance_across_trials: float) -> dict:
    """Deflated Sharpe Ratio, Bailey & Lopez de Prado (2014): DSR = PSR(SR0) where SR0 is the
    expected maximum Sharpe of ``n_trials`` skill-less trials (see :func:`expected_max_sr`).
    ``sr_variance_across_trials`` is the variance of the per-observation SR estimates across the
    tried configurations. Corrects for multiple testing / selection bias."""
    base = probabilistic_sharpe_ratio(returns, 0.0)
    sr0 = expected_max_sr(int(n_trials), float(sr_variance_across_trials))
    out = {"value": None, "sr": base["sr"], "sr0": sr0, "n": base["n"],
           "n_trials": int(n_trials), "sr_variance_across_trials": sr_variance_across_trials}
    if base["status"] == INSUFFICIENT:
        out["status"] = INSUFFICIENT
        return out
    m = _moments(returns)
    out["value"] = psr_from_moments(m["sr"], m["n"], m["skew"], m["kurt"], sr0)
    out["status"] = "OK"
    return out


def min_track_record_length(returns, sr_benchmark: float = 0.0, confidence: float = 0.95) -> dict:
    """Minimum Track Record Length, Bailey & Lopez de Prado (2012), eq. 11:

        MinTRL = 1 + (1 - g3*SR + (g4-1)/4 * SR^2) * (z_alpha / (SR - SR*))^2

    number of observations needed for PSR(SR*) to reach ``confidence``. Observations are in
    the unit of ``returns`` (e.g. days)."""
    m = _moments(returns)
    out = {"value": None, "n": m["n"], "sr": _f(m["sr"]), "confidence": confidence}
    if m["n"] < MIN_OBS or m["std"] == 0:
        out["status"] = INSUFFICIENT
        return out
    if m["sr"] <= sr_benchmark:
        out["status"] = "NOT_REACHABLE"
        return out
    z = norm_ppf(confidence)
    factor = 1 - m["skew"] * m["sr"] + (m["kurt"] - 1) / 4 * m["sr"] ** 2
    out["value"] = float(1 + factor * (z / (m["sr"] - sr_benchmark)) ** 2)
    out["sufficient"] = m["n"] >= out["value"]
    out["status"] = "OK"
    return out


# --------------------------------------------------------------------------- trade helpers
def _trades_df(trades) -> pd.DataFrame:
    df = pd.DataFrame(list(trades or []))
    if df.empty:
        return pd.DataFrame({"entry_time": pd.to_datetime([], utc=True),
                             "exit_time": pd.to_datetime([], utc=True), "pnl": []})
    df["pnl"] = df["pnl"].astype(float)
    if "exit_time" not in df:
        df["exit_time"] = df["entry_time"]
    df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True)
    if "entry_time" in df:
        df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
    else:
        df["entry_time"] = df["exit_time"]
    return df.sort_values("exit_time", kind="stable").reset_index(drop=True)


def daily_pnl_from_trades(trades) -> pd.Series:
    """Realized P&L by UTC exit day; zero-filled over business days between first and last exit."""
    df = _trades_df(trades)
    if df.empty:
        return pd.Series(dtype=float)
    day = df["exit_time"].dt.tz_convert("UTC").dt.tz_localize(None).dt.normalize()
    s = df.groupby(day)["pnl"].sum()
    full = pd.bdate_range(s.index.min(), s.index.max())
    return s.reindex(full.union(s.index), fill_value=0.0)


def daily_returns_from_trades(trades, capital: float) -> pd.Series:
    """Realized P&L by UTC exit day divided by equity at the start of that day
    (capital + cumulative realized P&L of earlier days; floored at 1e-9*capital)."""
    pnl = daily_pnl_from_trades(trades)
    if pnl.empty:
        return pnl
    equity_before = capital + pnl.cumsum().shift(1, fill_value=0.0)
    return pnl / equity_before.clip(lower=1e-9 * capital)


def trade_metrics(trades, capital: float | None = None) -> dict:
    """Standard metrics from a trade list. Sharpe/Sortino use daily realized P&L (business days,
    zero-filled), annualized with sqrt(252). profit_factor is capped at 999 when there are no losses."""
    df = _trades_df(trades)
    n = len(df)
    if n == 0:
        return {"n_trades": 0, "net_profit": 0.0, "profit_factor": 0.0, "win_rate": 0.0,
                "max_drawdown": 0.0, "max_drawdown_pct": 0.0, "recovery_factor": 0.0,
                "sharpe": 0.0, "sortino": 0.0}
    p = df["pnl"].to_numpy()
    wins, losses = p[p > 0].sum(), -p[p < 0].sum()
    pf = wins / losses if losses > 0 else (PF_CAP if wins > 0 else 0.0)
    eq = np.concatenate([[0.0], np.cumsum(p)])
    dd = float((np.maximum.accumulate(eq) - eq).max())
    dd_pct = 0.0
    if capital:
        e = capital + eq
        peak = np.maximum.accumulate(e)
        dd_pct = float((((peak - e) / np.where(peak > 0, peak, 1)).max()) * 100)
    net = float(p.sum())
    rec = net / dd if dd > 0 else (PF_CAP if net > 0 else 0.0)
    d = daily_pnl_from_trades(trades).to_numpy()
    sharpe = sortino = 0.0
    if len(d) > 1 and d.std(ddof=1) > 0:
        sharpe = float(d.mean() / d.std(ddof=1) * math.sqrt(252))
        dsd = math.sqrt(float((np.minimum(d, 0) ** 2).mean()))
        sortino = float(d.mean() / dsd * math.sqrt(252)) if dsd > 0 else 0.0
    return {"n_trades": n, "net_profit": net, "profit_factor": float(min(pf, PF_CAP)),
            "win_rate": float((p > 0).mean()), "max_drawdown": dd, "max_drawdown_pct": dd_pct,
            "recovery_factor": float(min(rec, PF_CAP)), "sharpe": sharpe, "sortino": sortino}


def _result_metrics(result: dict, capital: float | None = None) -> dict:
    m = trade_metrics(result.get("trades", []), capital)
    for k, v in (result.get("metrics") or {}).items():
        m.setdefault(k, v)
    return m


# --------------------------------------------------------------------------- bootstrap
def _auto_block(n: int) -> int:
    return max(2, int(round(n ** (1.0 / 3.0))))


def _resample_indices(n: int, reps: int, block, rng, kind: str = "stationary") -> np.ndarray:
    """(reps, n) index matrix. block None/1 -> iid; stationary (Politis & Romano 1994) with mean
    block length ``block``; or moving block (Kunsch 1989) with fixed length."""
    if block is None or block <= 1 or n < 2:
        return rng.integers(0, n, (reps, n))
    block = min(int(block), n)
    if kind == "moving":
        nb = int(math.ceil(n / block))
        starts = rng.integers(0, n - block + 1, (reps, nb))
        idx = (starts[:, :, None] + np.arange(block)).reshape(reps, -1)
        return idx[:, :n]
    idx = np.empty((reps, n), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, reps)
    restart = rng.random((reps, n)) < 1.0 / block
    fresh = rng.integers(0, n, (reps, n))
    for t in range(1, n):
        idx[:, t] = np.where(restart[:, t], fresh[:, t], (idx[:, t - 1] + 1) % n)
    return idx


def _apply_stat(stat: Callable, samples: np.ndarray) -> np.ndarray:
    try:
        out = np.asarray(stat(samples, axis=1), dtype=float)
        if out.shape == (samples.shape[0],):
            return out
    except TypeError:
        pass
    return np.array([float(stat(row)) for row in samples])


def bootstrap_confidence_interval(values, stat: Callable = np.mean, n: int = 2000, block=None,
                                  seed: int = 0, confidence: float = 0.95,
                                  method: str = "stationary") -> dict:
    """Percentile bootstrap CI for ``stat(values)``.

    block=None: iid bootstrap (use only for genuinely independent data). block="auto": mean block
    length ~ n^(1/3). block=int: that block length. method "stationary" (random block lengths,
    Politis & Romano 1994) or "moving" (fixed length). Block resampling respects serial
    dependence between trades/days; an iid bootstrap on dependent data gives CIs that are too narrow."""
    return _bootstrap(values, stat, n, block, seed, confidence, method)[0]


def _bootstrap(values, stat, n, block, seed, confidence, method):
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    out = {"estimate": None, "ci_low": None, "ci_high": None, "se": None, "n": len(x),
           "block": None, "confidence": confidence, "method": method if block else "iid"}
    if len(x) < MIN_OBS or np.all(x == x[0]):
        out["status"] = INSUFFICIENT
        if len(x):
            out["estimate"] = _f(stat(x))
        return out, None
    b = _auto_block(len(x)) if block == "auto" else block
    rng = np.random.default_rng(seed)
    idx = _resample_indices(len(x), n, b, rng, method)
    dist = _apply_stat(stat, x[idx])
    a = (1 - confidence) / 2
    out.update(estimate=_f(stat(x)), ci_low=_f(np.quantile(dist, a)),
               ci_high=_f(np.quantile(dist, 1 - a)), se=_f(dist.std(ddof=1)),
               block=b, status="OK")
    return out, dist


def _dist(a: np.ndarray) -> dict:
    q = np.percentile(a, [5, 25, 50, 75, 95])
    return {"mean": _f(a.mean()), "std": _f(a.std()), "min": _f(a.min()), "max": _f(a.max()),
            "p5": _f(q[0]), "p25": _f(q[1]), "p50": _f(q[2]), "p75": _f(q[3]), "p95": _f(q[4])}


# --------------------------------------------------------------------------- Monte Carlo
def monte_carlo(trades, capital: float, n: int = 2000, method: str = "block_bootstrap",
                seed: int = 0, cost_shock_per_trade: float = 0.0, ruin_drawdown_pct: float = 50,
                block="auto", n_bands: int = 50) -> dict:
    """Monte Carlo over the trade P&L sequence.

    method:
      * "shuffle"         - random permutation of the trades. Final equity is identical in every
                            run; only path/drawdown varies. IGNORES serial dependence (loss streaks
                            and clustering are destroyed) -> typically understates drawdown risk.
      * "bootstrap"       - iid resampling with replacement; also ignores serial dependence.
      * "block_bootstrap" - stationary block bootstrap (mean block length n_trades^(1/3) if
                            block="auto"); keeps short-range dependence. Default.
    ``cost_shock_per_trade`` is subtracted from every trade's P&L (extra cost scenario).
    ``risk_of_ruin`` = fraction of paths whose max drawdown (from running equity peak) reaches
    ``ruin_drawdown_pct`` or whose equity hits <= 0. It is a property of the resampled history,
    not a forecast.
    """
    if method not in ("block_bootstrap", "shuffle", "bootstrap"):
        raise ValueError(f"unknown method {method!r}")
    df = _trades_df(trades)
    pnl = df["pnl"].to_numpy() - float(cost_shock_per_trade)
    m = len(pnl)
    out = {"method": method, "n_sims": n, "n_trades": m, "capital": capital, "seed": seed,
           "cost_shock_per_trade": cost_shock_per_trade, "ruin_drawdown_pct": ruin_drawdown_pct,
           "notes_nl": []}
    if method == "shuffle":
        out["notes_nl"].append("Shuffle negeert seriele afhankelijkheid tussen trades en onderschat daardoor "
                               "waarschijnlijk het drawdown-risico.")
    if m < MIN_OBS:
        out["status"] = INSUFFICIENT
        return out
    rng = np.random.default_rng(seed)
    b = _auto_block(m) if block == "auto" else block
    out["block"] = b if method == "block_bootstrap" else None
    final = np.empty(n)
    dd_abs = np.empty(n)
    dd_pct = np.empty(n)
    grid = np.unique(np.linspace(0, m, min(n_bands, m + 1)).astype(int))
    band_store = np.empty((n, len(grid)))
    chunk = max(1, min(n, int(2e6 // max(m, 1))))
    for s in range(0, n, chunk):
        k = min(chunk, n - s)
        if method == "shuffle":
            idx = np.argsort(rng.random((k, m)), axis=1)
        elif method == "bootstrap":
            idx = rng.integers(0, m, (k, m))
        else:
            idx = _resample_indices(m, k, b, rng, "stationary")
        eq = capital + np.cumsum(pnl[idx], axis=1)
        eq = np.concatenate([np.full((k, 1), float(capital)), eq], axis=1)
        peak = np.maximum.accumulate(eq, axis=1)
        dd = peak - eq
        pct = np.where(peak > 0, dd / np.where(peak > 0, peak, 1), 1.0)
        pct = np.where(eq <= 0, 1.0, pct) * 100
        final[s:s + k] = eq[:, -1]
        dd_abs[s:s + k] = dd.max(axis=1)
        dd_pct[s:s + k] = pct.max(axis=1)
        band_store[s:s + k] = eq[:, grid]
    q = np.percentile(band_store, [5, 25, 50, 75, 95], axis=0)
    out.update(
        status="OK",
        final_equity=_dist(final),
        max_drawdown_pct=_dist(dd_pct),
        max_drawdown_abs=_dist(dd_abs),
        prob_loss=_f((final < capital).mean()),
        risk_of_ruin=_f((dd_pct >= ruin_drawdown_pct).mean()),
        equity_bands={"trade_index": grid.tolist(), "p5": q[0].tolist(), "p25": q[1].tolist(),
                      "p50": q[2].tolist(), "p75": q[3].tolist(), "p95": q[4].tolist()},
    )
    return out


# --------------------------------------------------------------------------- stress / drop-best
def default_cost_scenarios(base_commission_per_lot: float = 7.0, slippage_points: float = 1.0) -> dict:
    return {
        "baseline": {},
        "spread_+25%": {"spread_multiplier": 1.25},
        "spread_+50%": {"spread_multiplier": 1.5},
        "commission_x2": {"commission_per_lot": 2 * base_commission_per_lot},
        "slippage_+1pt": {"slippage_points": slippage_points},
        "entry_delay_1bar": {"entry_delay_bars": 1},
        "exit_delay_1bar": {"exit_delay_bars": 1},
    }


def cost_stress(backtest_fn: Callable, params: dict, start: str, end: str, scenarios: dict | None = None,
                base_commission_per_lot: float | None = None, slippage_points: float = 1.0,
                capital: float | None = None) -> dict:
    """Re-run the backtest with worse execution assumptions and report degradation vs baseline.

    Default scenarios: baseline, spread +25%, spread +50%, commission x2, slippage +1 point,
    entry 1 bar later, exit 1 bar later. "commission x2" uses ``commission_per_lot`` = 2 x
    ``base_commission_per_lot`` (default: params["commission_per_lot"] if present, else 7.0 - set it
    to your real commission). ``scenarios`` maps name -> stress kwargs for ``backtest_fn``."""
    if base_commission_per_lot is None:
        base_commission_per_lot = float((params or {}).get("commission_per_lot", 7.0))
    sc = scenarios or default_cost_scenarios(base_commission_per_lot, slippage_points)
    if "baseline" not in sc:
        sc = {"baseline": {}, **sc}
    res: dict[str, dict] = {}
    for name, kw in sc.items():
        r = backtest_fn(dict(params), start, end, **kw)
        res[name] = _result_metrics(r, capital)
    base = res["baseline"]
    for name, m in res.items():
        bn, sn = base["net_profit"], m["net_profit"]
        m["degradation"] = {
            "net_profit_delta": sn - bn,
            "net_profit_rel": (sn - bn) / abs(bn) if bn else None,
            "profit_factor_delta": m["profit_factor"] - base["profit_factor"],
            "n_trades_delta": m["n_trades"] - base["n_trades"],
        }
    s50 = res.get("spread_+50%")
    return {
        "scenarios": res,
        "baseline_net_profit": base["net_profit"],
        "survives_spread_50": (s50["net_profit"] > 0) if s50 else None,
        "all_positive": all(m["net_profit"] > 0 for m in res.values()),
        "n_scenarios_positive": sum(m["net_profit"] > 0 for m in res.values()),
        "status": "OK" if base["n_trades"] >= 1 else INSUFFICIENT,
    }


def drop_best(trades, k_trades=(1, 3, 5), by_day: bool = True) -> dict:
    """Effect of removing the k best trades (and the k best days when ``by_day``). A strategy
    whose profit vanishes after dropping a handful of trades is a lottery ticket, not an edge."""
    df = _trades_df(trades)
    out: dict[str, Any] = {"n_trades": len(df)}
    if df.empty:
        out["status"] = INSUFFICIENT
        return out
    p = df["pnl"].to_numpy()
    out["baseline_net"] = float(p.sum())
    order = np.sort(p)[::-1]

    def _row(remaining: np.ndarray, removed: np.ndarray) -> dict:
        w, l = remaining[remaining > 0].sum(), -remaining[remaining < 0].sum()
        return {"removed_pnl": float(removed.sum()), "net_after": float(remaining.sum()),
                "profit_factor_after": float(min(w / l, PF_CAP)) if l > 0 else (PF_CAP if w > 0 else 0.0),
                "still_positive": bool(remaining.sum() > 0)}

    out["by_trades"] = {str(k): _row(order[k:], order[:k]) for k in k_trades if k < len(order)}
    if by_day:
        day = df["exit_time"].dt.tz_convert("UTC").dt.date
        dpnl = np.sort(df.groupby(day)["pnl"].sum().to_numpy())[::-1]
        out["by_days"] = {str(k): _row(dpnl[k:], dpnl[:k]) for k in k_trades if k < len(dpnl)}
    out["status"] = "OK" if len(df) >= MIN_OBS else INSUFFICIENT
    return out


# --------------------------------------------------------------------------- walk-forward
def _expand_candidates(spec, max_combos: int, rng) -> list[dict]:
    if callable(spec):
        return list(spec(max_combos, rng))
    if isinstance(spec, dict):
        keys = list(spec)
        combos = [dict(zip(keys, v)) for v in itertools.product(*[list(spec[k]) for k in keys])]
    else:
        combos = [dict(c) for c in spec]
    if len(combos) > max_combos:
        pick = rng.choice(len(combos), size=max_combos, replace=False)
        combos = [combos[i] for i in sorted(pick)]
    return combos


def _objective_value(m: dict, objective: str) -> float:
    key = {"sharpe": "sharpe", "net_profit": "net_profit", "profit_factor": "profit_factor"}[objective]
    return float(m.get(key, 0.0))


def _fmt_day(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%d")


def walk_forward(backtest_fn: Callable, param_grid_or_sampler, start: str, end: str, train_days: int,
                 test_days: int, step_days: int, mode: str = "rolling", objective: str = "sharpe",
                 min_trades: int = 10, max_combos: int = 50, seed: int = 0,
                 capital: float | None = None) -> dict:
    """Walk-forward analysis (Pardo): optimise on a train window, evaluate the chosen parameters on
    the following unseen test window, then step forward.

    Windows are calendar days over [start, end); test ranges never overlap the train range of the
    same window. ``param_grid_or_sampler``: dict of lists (grid), list of dicts, or
    ``callable(n, rng) -> list[dict]``; capped at ``max_combos`` (random subset, seeded).
    Candidates with < ``min_trades`` train trades are not eligible; if none is eligible the window is
    "INSUFFICIENT". A window is also "INSUFFICIENT" when the test window has < ``min_trades`` trades.

    Window status is "OK" or "INSUFFICIENT".

    Walk-Forward Efficiency (explicit definition): per window with positive in-sample net profit,
        WFE_w = (OOS net profit / test_days) / (IS net profit / train_days)
    i.e. the per-day OOS profit rate over the per-day IS profit rate (annualizing both by the same
    factor cancels). ``wfe`` is the mean over windows with status OK and positive IS profit.
    WFE ~1 means OOS keeps pace with IS; << 0.5 or <0 signals overfitting."""
    if mode not in ("rolling", "expanding"):
        raise ValueError("mode must be rolling|expanding")
    if objective not in ("sharpe", "net_profit", "profit_factor"):
        raise ValueError("objective must be sharpe|net_profit|profit_factor")
    rng = np.random.default_rng(seed)
    cands = _expand_candidates(param_grid_or_sampler, max_combos, rng)
    s0, e0 = pd.Timestamp(start), pd.Timestamp(end)
    windows, oos_trades = [], []
    cur = s0
    while True:
        tr_start = s0 if mode == "expanding" else cur
        tr_end = cur + pd.Timedelta(days=train_days)
        te_end = tr_end + pd.Timedelta(days=test_days)
        if te_end > e0:
            break
        best, best_val, best_m = None, -math.inf, None
        for p in cands:
            r = backtest_fn(dict(p), _fmt_day(tr_start), _fmt_day(tr_end))
            m = _result_metrics(r, capital)
            if m["n_trades"] < min_trades:
                continue
            v = _objective_value(m, objective)
            if v > best_val:
                best, best_val, best_m = p, v, m
        w = {"train": [_fmt_day(tr_start), _fmt_day(tr_end)], "test": [_fmt_day(tr_end), _fmt_day(te_end)],
             "params": best, "train_metrics": best_m, "test_metrics": None, "wfe": None}
        if best is None:
            w["status"] = "INSUFFICIENT"
            w["reason"] = f"geen parametercombinatie met >= {min_trades} trades in train"
        else:
            r = backtest_fn(dict(best), _fmt_day(tr_end), _fmt_day(te_end))
            w["test_metrics"] = _result_metrics(r, capital)
            oos_trades.extend(r.get("trades", []))
            n_tr = (tr_end - tr_start).days
            if w["test_metrics"]["n_trades"] < min_trades:
                w["status"] = "INSUFFICIENT"
                w["reason"] = f"test-window heeft < {min_trades} trades"
            else:
                w["status"] = "OK"
            if best_m["net_profit"] > 0 and n_tr > 0:
                w["wfe"] = (w["test_metrics"]["net_profit"] / test_days) / (best_m["net_profit"] / n_tr)
        windows.append(w)
        cur += pd.Timedelta(days=step_days)
    ok = [w for w in windows if w["status"] == "OK"]
    wfes = [w["wfe"] for w in ok if w["wfe"] is not None]
    oos_m = trade_metrics(oos_trades, capital)
    summary = {
        "n_windows": len(windows), "n_windows_ok": len(ok), "n_candidates": len(cands),
        "wfe": float(np.mean(wfes)) if wfes else None,
        "oos_net_profit": oos_m["net_profit"], "oos_n_trades": oos_m["n_trades"],
        "oos_metrics": oos_m,
        "oos_profitable_windows_frac": (float(np.mean([w["test_metrics"]["net_profit"] > 0 for w in ok]))
                                        if ok else None),
        "oos_trades": oos_trades,
        "windows": windows, "mode": mode, "objective": objective,
        "wfe_definition": "mean over OK windows with IS>0 of (OOS net/test_days)/(IS net/train_days)",
    }
    summary["status"] = "OK" if ok and wfes else INSUFFICIENT
    return summary


def parameter_stability(backtest_fn: Callable, params: dict, param_specs: dict, start: str, end: str,
                        rel_step: float = 0.1, objective: str = "net_profit",
                        capital: float | None = None) -> dict:
    """Perturb each parameter by +/- ``rel_step`` (relative; integer params move at least 1) one at a
    time and re-run. ``param_specs``: name -> {"type": "int"|"float", "min": .., "max": ..} (min/max
    optional). Reports fraction of neighbours with positive net profit and the relative change of the
    objective vs the base run. A sharp optimum (large drops for small moves) indicates overfitting."""
    base_m = _result_metrics(backtest_fn(dict(params), start, end), capital)
    base_obj = _objective_value(base_m, objective)
    neigh = []
    for name, spec in param_specs.items():
        v = params[name]
        is_int = spec.get("type") == "int" or isinstance(v, int)
        delta = max(abs(v) * rel_step, 1.0 if is_int else 0.0)
        if delta == 0:
            delta = rel_step
        for sign in (-1, 1):
            nv = v + sign * delta
            if is_int:
                nv = int(round(nv))
            if "min" in spec:
                nv = max(nv, spec["min"])
            if "max" in spec:
                nv = min(nv, spec["max"])
            if nv == v:
                continue
            p2 = dict(params)
            p2[name] = nv
            m = _result_metrics(backtest_fn(p2, start, end), capital)
            o = _objective_value(m, objective)
            neigh.append({"param": name, "value": nv, "net_profit": m["net_profit"],
                          "objective": o, "n_trades": m["n_trades"],
                          "rel_change": (o - base_obj) / abs(base_obj) if base_obj else None})
    out = {"base_net_profit": base_m["net_profit"], "base_objective": base_obj, "objective": objective,
           "neighbours": neigh, "n_neighbours": len(neigh), "rel_step": rel_step}
    if not neigh or base_m["n_trades"] == 0:
        out["status"] = INSUFFICIENT
        return out
    changes = [n["rel_change"] for n in neigh if n["rel_change"] is not None]
    out.update(frac_positive=float(np.mean([n["net_profit"] > 0 for n in neigh])),
               mean_rel_change=float(np.mean(changes)) if changes else None,
               worst_rel_change=float(min(changes)) if changes else None, status="OK")
    return out


# --------------------------------------------------------------------------- PBO / CSCV
def pbo_cscv(performance_matrix, n_splits: int = 8, metric: str = "sharpe", max_combos: int = 5000,
             seed: int = 0) -> dict:
    """Probability of Backtest Overfitting via Combinatorially Symmetric Cross-Validation,
    Bailey, Borwein, Lopez de Prado & Zhu (2015), "The Probability of Backtest Overfitting",
    J. of Computational Finance 20(4).

    ``performance_matrix``: T x N (rows = time periods of returns, columns = trials/configurations).
    Rows are cut into ``n_splits`` (even) contiguous blocks; for each of the C(S, S/2) ways to choose
    half the blocks as in-sample (IS), the trial with best IS ``metric`` ("sharpe" or "mean") is
    selected, and its relative rank w = rank/(N+1) among all trials out-of-sample (OOS, the
    complementary blocks) gives logit lambda = ln(w/(1-w)). PBO = share of combinations with
    lambda <= 0 (IS winner at/below OOS median). Pure noise gives PBO ~ 0.5 (no information, not
    1); PBO ~ 0 means the IS winner keeps winning; PBO -> 1 means IS winners systematically fail.
    INSUFFICIENT_EVIDENCE when N < 2 or T < n_splits."""
    M = np.asarray(performance_matrix, dtype=float)
    out = {"pbo": None, "n_splits": n_splits, "logits": [], "status": INSUFFICIENT}
    if M.ndim != 2 or M.shape[1] < 2 or M.shape[0] < n_splits or n_splits < 2 or n_splits % 2:
        return out
    T, N = M.shape
    blocks = np.array_split(np.arange(T), n_splits)

    def perf(rows: np.ndarray) -> np.ndarray:
        sub = M[rows]
        mu = sub.mean(axis=0)
        if metric == "mean":
            return mu
        sd = sub.std(axis=0, ddof=1)
        return np.where(sd > 0, mu / np.where(sd > 0, sd, 1), 0.0)

    combos = list(itertools.combinations(range(n_splits), n_splits // 2))
    if len(combos) > max_combos:
        rng = np.random.default_rng(seed)
        combos = [combos[i] for i in rng.choice(len(combos), max_combos, replace=False)]
    logits, is_perf, oos_perf = [], [], []
    for c in combos:
        is_rows = np.concatenate([blocks[i] for i in c])
        oos_rows = np.concatenate([blocks[i] for i in range(n_splits) if i not in c])
        pi, po = perf(is_rows), perf(oos_rows)
        star = int(np.argmax(pi))
        rank = 1 + (po < po[star]).sum() + 0.5 * ((po == po[star]).sum() - 1)
        w = rank / (N + 1)
        logits.append(math.log(w / (1 - w)))
        is_perf.append(pi[star])
        oos_perf.append(po[star])
    lg = np.array(logits)
    out.update(pbo=float((lg <= 0).mean()), logits=lg.tolist(), n_combinations=len(combos),
               logit_mean=float(lg.mean()), logit_median=float(np.median(lg)),
               prob_oos_loss=float((np.array(oos_perf) < 0).mean()),
               is_oos_slope=float(np.polyfit(is_perf, oos_perf, 1)[0]) if np.std(is_perf) > 0 else None,
               n_trials=N, n_periods=T, metric=metric, status="OK")
    return out


# --------------------------------------------------------------------------- multiple testing
def multiple_testing(p_values=None, method: str = "holm", alpha: float = 0.05, sharpe=None,
                     n_obs: int | None = None) -> dict:
    """Family-wise (Holm 1979, step-down) or false-discovery-rate (Benjamini & Hochberg 1995)
    adjustment. Give ``p_values`` directly, or per-observation ``sharpe`` values plus ``n_obs`` (one-sided
    p = 1 - Phi(SR*sqrt(n_obs))). CAUTION: trades are not iid; p-values must come from daily returns
    with block bootstrap (see :func:`bootstrap_confidence_interval`), otherwise they are too small."""
    if method not in ("holm", "bh"):
        raise ValueError("method must be holm|bh")
    if p_values is None:
        if sharpe is None or not n_obs:
            raise ValueError("give p_values, or sharpe + n_obs")
        p_values = [norm_sf(float(s) * math.sqrt(n_obs)) for s in sharpe]
    p = np.asarray(p_values, dtype=float)
    m = len(p)
    out = {"method": method, "alpha": alpha, "n_tests": m, "p_raw": p.tolist()}
    if m == 0:
        out.update(p_adjusted=[], reject=[], status=INSUFFICIENT)
        return out
    order = np.argsort(p)
    adj = np.empty(m)
    if method == "holm":
        run = 0.0
        for rank, i in enumerate(order):
            run = max(run, min(1.0, (m - rank) * p[i]))
            adj[i] = run
    else:
        run = 1.0
        for rank in range(m - 1, -1, -1):
            i = order[rank]
            run = min(run, p[i] * m / (rank + 1))
            adj[i] = min(1.0, run)
    out.update(p_adjusted=adj.tolist(), reject=(adj <= alpha).tolist(), n_reject=int((adj <= alpha).sum()),
               status="OK")
    return out


def haircut_sharpe(sr: float, n_trials: int, n_obs: int) -> dict:
    """Bonferroni/Holm-style Sharpe haircut (cf. Harvey & Liu 2015, "Backtesting"). For the best of
    ``n_trials`` strategies the Holm adjustment of the smallest p-value equals Bonferroni:
        p = 1 - Phi(SR*sqrt(n_obs)),  p_adj = min(1, N*p),  SR_adj = Phi^-1(1 - p_adj)/sqrt(n_obs)
    ``sr`` is per observation (non-annualized); haircut = 1 - SR_adj/SR. Assumes iid returns -
    use daily returns, not trades, and treat the result as optimistic under dependence."""
    out = {"sr": sr, "n_trials": n_trials, "n_obs": n_obs}
    if n_obs < MIN_OBS or sr is None:
        out["status"] = INSUFFICIENT
        return out
    p = norm_sf(sr * math.sqrt(n_obs))
    p_adj = min(1.0, max(1, n_trials) * p)
    sr_adj = max(0.0, -norm_ppf(p_adj) / math.sqrt(n_obs)) if p_adj < 1 else 0.0
    out.update(p_raw=p, p_adjusted=p_adj, sr_adjusted=sr_adj,
               haircut=(1 - sr_adj / sr) if sr > 0 else None, status="OK")
    return out


def effect_vs_benchmark(strategy_daily_returns, benchmark_daily_returns, n: int = 2000, block="auto",
                        seed: int = 0) -> dict:
    """Mean daily difference strategy - benchmark with stationary block-bootstrap CI, a two-sided
    bootstrap p-value (centred on 0 is NOT used; p = 2*min(P(mean*<=0), P(mean*>=0))) and effect size
    (Cohen's d = mean diff / std diff, per day). Series are aligned on index when both are pandas."""
    a, b = strategy_daily_returns, benchmark_daily_returns
    if isinstance(a, pd.Series) and isinstance(b, pd.Series):
        a, b = a.align(b, join="inner")
    diff = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    ci, d = _bootstrap(diff, np.mean, n, block, seed, 0.95, "stationary")
    out = {k: ci[k] for k in ("estimate", "ci_low", "ci_high", "se", "n", "block", "status")}
    out["mean_diff"] = out.pop("estimate")
    if ci["status"] == INSUFFICIENT:
        return out
    out["p_value_bootstrap"] = float(min(1.0, 2 * min((d <= 0).mean(), (d >= 0).mean())))
    out["cohens_d"] = float(diff.mean() / diff.std(ddof=1))
    return out


# --------------------------------------------------------------------------- breakdowns
def _group_stats(g: pd.DataFrame) -> dict:
    p = g["pnl"].to_numpy()
    w, l = p[p > 0].sum(), -p[p < 0].sum()
    r = float(g["r_multiple"].mean()) if "r_multiple" in g and g["r_multiple"].notna().any() else None
    return {"n_trades": int(len(p)), "net_profit": float(p.sum()),
            "profit_factor": float(min(w / l, PF_CAP)) if l > 0 else (PF_CAP if w > 0 else 0.0),
            "win_rate": float((p > 0).mean()), "avg_r": r}


def regime_breakdown(trades, regime_labels_by_day: dict) -> dict:
    """Performance per regime label; a trade belongs to the regime of its ENTRY day (UTC,
    key "YYYY-MM-DD"). Trades on days without label go to "unlabeled"."""
    df = _trades_df(trades)
    if df.empty:
        return {"regimes": {}, "status": INSUFFICIENT}
    day = df["entry_time"].dt.strftime("%Y-%m-%d")
    df["regime"] = day.map(lambda d: regime_labels_by_day.get(d, "unlabeled"))
    regs = {str(k): _group_stats(g) for k, g in df.groupby("regime")}
    return {"regimes": regs, "status": "OK" if len(df) >= MIN_OBS else INSUFFICIENT}


def yearly_breakdown(trades) -> dict:
    """Per calendar year (UTC exit date) stats and fraction of profitable years."""
    df = _trades_df(trades)
    if df.empty:
        return {"years": {}, "n_years": 0, "frac_positive": None, "status": INSUFFICIENT}
    df["year"] = df["exit_time"].dt.year
    yrs = {str(k): _group_stats(g) for k, g in df.groupby("year")}
    pos = [v["net_profit"] > 0 for v in yrs.values()]
    return {"years": yrs, "n_years": len(yrs), "frac_positive": float(np.mean(pos)),
            "status": "OK" if len(yrs) >= 2 else INSUFFICIENT}


# --------------------------------------------------------------------------- scorecard
DEFAULT_THRESHOLDS = {
    "min_trades": 100,                 # sample size
    "min_net_profit": 0.0,             # net after costs must be > this
    "min_profit_factor": 1.2,
    "max_drawdown_pct": 30.0,
    "min_psr": 0.95,                   # PSR vs SR*=0
    "min_dsr": 0.95,                   # DSR given n_trials
    "max_pbo": 0.5,
    "min_wfe": 0.5,
    "min_oos_net_profit": 0.0,         # OOS net must be > this
    "min_oos_trades": 30,
    "min_oos_profitable_windows": 0.5,
    "min_cost_stress_net": 0.0,        # net at spread +50% must be > this
    "min_param_stability": 0.7,        # share of neighbours with positive net
    "min_drop_best_net": 0.0,          # net after removing 3 best trades must be > this
    "drop_best_k": 3,
    "min_yearly_positive": 0.6,
    "min_years": 2,
}
VERDICTS = ["REJECT", "NEEDS_MORE_DATA", "RESEARCH_CANDIDATE", "OOS_PASSED", "FORWARD_ELIGIBLE"]


def _unwrap(v):
    """Return (value, status) from a number or an engine result dict."""
    if isinstance(v, dict):
        return v.get("value"), v.get("status")
    return v, None


def _pick(ev: dict, flat: str, nested: Callable | None = None):
    if flat in ev and ev[flat] is not None:
        return ev[flat]
    if nested is not None:
        try:
            return nested(ev)
        except (KeyError, TypeError, IndexError, AttributeError):
            return None
    return None


def _chk(name, status, value, threshold, text):
    return {"name": name, "status": status, "value": value, "threshold": threshold,
            "explanation_nl": text}


def _nt(name, threshold, what):
    return _chk(name, "NOT_TESTED", None, threshold, f"Niet getest: {what} ontbreekt in het bewijs.")


def _evidence_values(ev: dict) -> dict:
    wf = ev.get("walk_forward") or {}
    return {
        "n_trades": _pick(ev, "n_trades"),
        "net_profit": _pick(ev, "net_profit"),
        "profit_factor": _pick(ev, "profit_factor"),
        "max_drawdown_pct": _pick(ev, "max_drawdown_pct"),
        "psr": ev.get("psr"),
        "dsr": ev.get("dsr"),
        "pbo": _pick(ev, "pbo", lambda e: e["pbo_result"]["pbo"]),
        "wfe": _pick(ev, "wfe", lambda e: e["walk_forward"]["wfe"]),
        "oos_net_profit": _pick(ev, "oos_net_profit", lambda e: e["walk_forward"]["oos_net_profit"]),
        "oos_n_trades": _pick(ev, "oos_n_trades", lambda e: e["walk_forward"]["oos_n_trades"]),
        "oos_windows": _pick(ev, "oos_profitable_windows_frac",
                             lambda e: e["walk_forward"]["oos_profitable_windows_frac"]),
        "spread50": _pick(ev, "cost_stress_spread50_net",
                          lambda e: e["cost_stress"]["scenarios"]["spread_+50%"]["net_profit"]),
        "stability": _pick(ev, "parameter_stability_frac_positive",
                           lambda e: e["parameter_stability"]["frac_positive"]),
        "yearly": _pick(ev, "yearly_positive_frac", lambda e: e["yearly"]["frac_positive"]),
        "n_years": _pick(ev, "n_years", lambda e: e["yearly"]["n_years"]),
        "n_trials": ev.get("n_trials"),
        "wf_status": wf.get("status") if isinstance(wf, dict) else None,
    }


def scorecard(evidence: dict, thresholds: dict | None = None) -> dict:
    """Combine evidence into a verdict and a list of checks.

    Evidence keys (all optional; flat numbers, or the dicts returned by this module, which are
    unwrapped): n_trades, net_profit, profit_factor, max_drawdown_pct, psr, dsr, n_trials, pbo (or
    pbo_result), wfe / oos_net_profit / oos_n_trades / oos_profitable_windows_frac (or walk_forward),
    cost_stress_spread50_net (or cost_stress), parameter_stability_frac_positive (or
    parameter_stability), drop_best (output of :func:`drop_best`) or net_after_drop_best,
    yearly_positive_frac / n_years (or yearly).

    Statuses: PASS | FAIL | INSUFFICIENT_EVIDENCE | NOT_TESTED. Thresholds: DEFAULT_THRESHOLDS.

    Verdict rules (never a probability of future profit):
      * hard FAIL on net_after_costs or dsr, or >= 2 FAILs        -> REJECT
      * exactly 1 other FAIL                                      -> NEEDS_MORE_DATA
      * any INSUFFICIENT_EVIDENCE, or sample/net/PF/DD not tested -> NEEDS_MORE_DATA
      * otherwise                                                 -> RESEARCH_CANDIDATE (cap when
        any evidence is NOT_TESTED)
      * + walk-forward/holdout WFE and OOS profitability PASS     -> OOS_PASSED
      * + every check PASS                                        -> FORWARD_ELIGIBLE
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    ev = evidence or {}
    v = _evidence_values(ev)
    checks = []

    # sample size
    if v["n_trades"] is None:
        checks.append(_nt("sample_size", th["min_trades"], "aantal trades"))
    else:
        ok = v["n_trades"] >= th["min_trades"]
        checks.append(_chk("sample_size", "PASS" if ok else INSUFFICIENT, v["n_trades"], th["min_trades"],
                           f"{v['n_trades']} trades; minimaal {th['min_trades']} nodig voor betekenisvolle statistiek."))
    # net after costs
    if v["net_profit"] is None:
        checks.append(_nt("net_after_costs", th["min_net_profit"], "netto resultaat na kosten"))
    else:
        ok = v["net_profit"] > th["min_net_profit"]
        checks.append(_chk("net_after_costs", "PASS" if ok else "FAIL", v["net_profit"], th["min_net_profit"],
                           "Netto resultaat na kosten is positief." if ok else
                           "Netto resultaat na kosten is niet positief: harde afwijzing."))
    # PF
    if v["profit_factor"] is None:
        checks.append(_nt("profit_factor", th["min_profit_factor"], "profit factor"))
    else:
        ok = v["profit_factor"] >= th["min_profit_factor"]
        checks.append(_chk("profit_factor", "PASS" if ok else "FAIL", v["profit_factor"], th["min_profit_factor"],
                           f"Profit factor {v['profit_factor']:.2f} (drempel {th['min_profit_factor']})."))
    # DD
    if v["max_drawdown_pct"] is None:
        checks.append(_nt("max_drawdown", th["max_drawdown_pct"], "maximale drawdown"))
    else:
        ok = v["max_drawdown_pct"] <= th["max_drawdown_pct"]
        checks.append(_chk("max_drawdown", "PASS" if ok else "FAIL", v["max_drawdown_pct"], th["max_drawdown_pct"],
                           f"Maximale drawdown {v['max_drawdown_pct']:.1f}% (limiet {th['max_drawdown_pct']}%)."))
    # PSR / DSR
    for key, thr_key, label in (("psr", "min_psr", "PSR"), ("dsr", "min_dsr", "DSR")):
        val, st = _unwrap(v[key])
        if v[key] is None:
            checks.append(_nt(key, th[thr_key], label))
        elif st == INSUFFICIENT or val is None:
            checks.append(_chk(key, INSUFFICIENT, None, th[thr_key],
                               f"{label} niet berekenbaar: te weinig waarnemingen (<30) of nul variantie."))
        else:
            ok = val >= th[thr_key]
            extra = f" bij {v['n_trials']} geprobeerde hypotheses" if key == "dsr" and v["n_trials"] else ""
            checks.append(_chk(key, "PASS" if ok else "FAIL", val, th[thr_key],
                               f"{label} = {val:.3f}{extra} (drempel {th[thr_key]}). Dit is de kans dat de echte Sharpe "
                               f"boven de benchmark ligt, geen kans op toekomstige winst."))
    # PBO
    pbo, pst = _unwrap(v["pbo"])
    if v["pbo"] is None:
        checks.append(_nt("pbo", th["max_pbo"], "PBO (CSCV)"))
    elif pst == INSUFFICIENT or pbo is None:
        checks.append(_chk("pbo", INSUFFICIENT, None, th["max_pbo"], "PBO niet berekenbaar (te weinig trials of perioden)."))
    else:
        ok = pbo <= th["max_pbo"]
        checks.append(_chk("pbo", "PASS" if ok else "FAIL", pbo, th["max_pbo"],
                           f"Kans op backtest-overfitting {pbo:.2f} (max {th['max_pbo']})."))
    # WFE
    wfe, wst = _unwrap(v["wfe"])
    if v["wfe"] is None and v["wf_status"] is None:
        checks.append(_nt("walk_forward_efficiency", th["min_wfe"], "walk-forward analyse"))
    elif wfe is None:
        checks.append(_chk("walk_forward_efficiency", INSUFFICIENT, None, th["min_wfe"],
                           "Walk-forward efficiency niet berekenbaar: te weinig geldige vensters."))
    else:
        ok = wfe >= th["min_wfe"]
        checks.append(_chk("walk_forward_efficiency", "PASS" if ok else "FAIL", wfe, th["min_wfe"],
                           f"WFE {wfe:.2f}: out-of-sample winstsnelheid t.o.v. in-sample (min {th['min_wfe']})."))
    # OOS profitability
    if v["oos_net_profit"] is None:
        checks.append(_nt("oos_profitability", th["min_oos_net_profit"], "out-of-sample resultaat"))
    else:
        n_oos = v["oos_n_trades"]
        if n_oos is not None and n_oos < th["min_oos_trades"]:
            checks.append(_chk("oos_profitability", INSUFFICIENT, v["oos_net_profit"], th["min_oos_trades"],
                               f"Slechts {n_oos} OOS-trades; minimaal {th['min_oos_trades']} nodig."))
        else:
            ok = v["oos_net_profit"] > th["min_oos_net_profit"]
            if ok and v["oos_windows"] is not None and v["oos_windows"] < th["min_oos_profitable_windows"]:
                ok = False
            checks.append(_chk("oos_profitability", "PASS" if ok else "FAIL", v["oos_net_profit"],
                               th["min_oos_net_profit"],
                               "Out-of-sample netto resultaat positief en consistent over vensters." if ok else
                               "Out-of-sample resultaat niet positief of niet consistent over vensters."))
    # cost stress
    if v["spread50"] is None:
        checks.append(_nt("cost_stress", th["min_cost_stress_net"], "kostenstress (spread +50%)"))
    else:
        ok = v["spread50"] > th["min_cost_stress_net"]
        checks.append(_chk("cost_stress", "PASS" if ok else "FAIL", v["spread50"], th["min_cost_stress_net"],
                           "Resultaat blijft positief bij +50% spread." if ok else
                           "Resultaat wordt negatief bij +50% spread: kostengevoelig."))
    # param stability
    if v["stability"] is None:
        checks.append(_nt("parameter_stability", th["min_param_stability"], "parameterstabiliteit"))
    else:
        ok = v["stability"] >= th["min_param_stability"]
        checks.append(_chk("parameter_stability", "PASS" if ok else "FAIL", v["stability"], th["min_param_stability"],
                           f"{v['stability']:.0%} van de naburige parameterwaarden is winstgevend."))
    # drop best
    k = str(th["drop_best_k"])
    db = _pick(ev, "net_after_drop_best", lambda e: e["drop_best"]["by_trades"][k]["net_after"])
    if db is None:
        checks.append(_nt("drop_best_trades", th["min_drop_best_net"], f"resultaat zonder beste {k} trades"))
    else:
        ok = db > th["min_drop_best_net"]
        checks.append(_chk("drop_best_trades", "PASS" if ok else "FAIL", db, th["min_drop_best_net"],
                           f"Zonder de beste {k} trades is het resultaat {'nog positief' if ok else 'niet meer positief'}."))
    # yearly
    if v["yearly"] is None:
        checks.append(_nt("yearly_consistency", th["min_yearly_positive"], "jaarlijkse consistentie"))
    elif v["n_years"] is not None and v["n_years"] < th["min_years"]:
        checks.append(_chk("yearly_consistency", INSUFFICIENT, v["yearly"], th["min_yearly_positive"],
                           f"Slechts {v['n_years']} jaar data; minimaal {th['min_years']} nodig."))
    else:
        ok = v["yearly"] >= th["min_yearly_positive"]
        checks.append(_chk("yearly_consistency", "PASS" if ok else "FAIL", v["yearly"], th["min_yearly_positive"],
                           f"{v['yearly']:.0%} van de jaren winstgevend (min {th['min_yearly_positive']:.0%})."))

    by = {c["name"]: c for c in checks}
    st = lambda n: by[n]["status"]
    fails = [c["name"] for c in checks if c["status"] == "FAIL"]
    insuff = [c["name"] for c in checks if c["status"] == INSUFFICIENT]
    not_tested = [c["name"] for c in checks if c["status"] == "NOT_TESTED"]
    basics_missing = any(st(n) == "NOT_TESTED" for n in ("sample_size", "net_after_costs", "profit_factor", "max_drawdown"))
    notes = []
    if st("net_after_costs") == "FAIL" or st("dsr") == "FAIL" or len(fails) >= 2:
        verdict = "REJECT"
        notes.append("Afgewezen: harde fout op netto resultaat of DSR, of meerdere checks falen: " + ", ".join(fails) + ".")
    elif fails:
        verdict = "NEEDS_MORE_DATA"
        notes.append("Eén check faalt (" + fails[0] + "): strategie is geen onderzoekskandidaat.")
    elif insuff or basics_missing:
        verdict = "NEEDS_MORE_DATA"
        notes.append("Onvoldoende bewijs of basisgegevens ontbreken: " + ", ".join(insuff + [n for n in not_tested]) + ".")
    else:
        verdict = "RESEARCH_CANDIDATE"
        if st("walk_forward_efficiency") == "PASS" and st("oos_profitability") == "PASS":
            verdict = "OOS_PASSED"
            if all(c["status"] == "PASS" for c in checks):
                verdict = "FORWARD_ELIGIBLE"
        if not_tested:
            notes.append("Niet getest: " + ", ".join(not_tested) + "; verdict is begrensd tot RESEARCH_CANDIDATE.")
    if not_tested and VERDICTS.index(verdict) > VERDICTS.index("RESEARCH_CANDIDATE"):
        verdict = "RESEARCH_CANDIDATE"
    # Provenance: these do not judge performance but whether the OOS/selection evidence is clean. A gap here only
    # blocks FORWARD_ELIGIBLE (caps at OOS_PASSED); it never turns a rejection into a pass.
    history = evidence.get("research_history")
    if isinstance(history, dict) and not history.get("in_lab"):
        known = isinstance(history.get("external_trials"), (int, float))
        checks.append(_chk("selection_history", "PASS" if known else INSUFFICIENT, history.get("external_trials"), "opgegeven",
                           "Buiten het lab geprobeerde varianten zijn meegeteld in N." if known else
                           "Strategie is buiten het lab ontwikkeld en het aantal daar geprobeerde varianten is niet opgegeven; "
                           "DSR/selectiecorrectie kan daardoor te mild zijn."))
    holdout = evidence.get("holdout")
    if isinstance(holdout, dict) and "same_strategy_exposures" in holdout:
        seen = int(holdout.get("same_strategy_exposures") or 0)
        checks.append(_chk("holdout_untouched", "PASS" if seen == 0 else INSUFFICIENT, seen, 0,
                           "De holdout was voor deze strategie nog niet ingezien." if seen == 0 else
                           f"De holdout-periode is voor deze strategie al {seen} keer eerder bekeken; het is geen schone "
                           "out-of-sample meer. Kies een nieuwere holdout of bevestig met een forwardtest."))
    gaps = [c["name"] for c in checks if c["name"] in ("selection_history", "holdout_untouched") and c["status"] != "PASS"]
    if gaps and verdict == "FORWARD_ELIGIBLE":
        verdict = "OOS_PASSED"
        notes.append("Begrensd tot OOS_PASSED door herkomst van het bewijs: " + ", ".join(gaps) + ".")
    return {
        "verdict": verdict, "checks": checks, "thresholds": th, "notes_nl": notes,
        "counts": {"PASS": sum(c["status"] == "PASS" for c in checks), "FAIL": len(fails),
                   "INSUFFICIENT_EVIDENCE": len(insuff), "NOT_TESTED": len(not_tested)},
        "disclaimer_nl": "Dit scorecard geeft geen kans op toekomstige winst; het beschrijft alleen de sterkte van het historische bewijs.",
    }


# --------------------------------------------------------------------------- Dutch report
_SYM = {"PASS": "PASS", "FAIL": "FAIL", "INSUFFICIENT_EVIDENCE": "ONVOLDOENDE BEWIJS", "NOT_TESTED": "NIET GETEST"}
_VERDICT_NL = {
    "REJECT": "Afgewezen: het bewijs spreekt de strategie tegen.",
    "NEEDS_MORE_DATA": "Meer data nodig: het bewijs is onvoldoende om te oordelen.",
    "RESEARCH_CANDIDATE": "Onderzoekskandidaat: in-sample bewijs ok, maar nog geen volledige out-of-sample bevestiging.",
    "OOS_PASSED": "Out-of-sample geslaagd: kandidaat voor forward-test, nog niet alle controles compleet.",
    "FORWARD_ELIGIBLE": "Geschikt voor forward-test: alle kernchecks zijn geslaagd.",
}


def _fmt(x, pct=False, nd=2):
    if x is None:
        return "n.v.t."
    return f"{x:.1%}" if pct else f"{x:,.{nd}f}"


def validation_report_nl(sc: dict, evidence: dict) -> str:
    """Markdown rapport (Nederlands) dat de kernvragen beantwoordt op basis van scorecard + bewijs."""
    ev = evidence or {}
    checks = {c["name"]: c for c in sc["checks"]}
    passed = [c for c in sc["checks"] if c["status"] == "PASS"]
    open_ = [c for c in sc["checks"] if c["status"] in ("FAIL", INSUFFICIENT, "NOT_TESTED")]
    L = [f"# Validatierapport", "", f"**Verdict: {sc['verdict']}** - {_VERDICT_NL[sc['verdict']]}", ""]
    L += [f"_{sc['disclaimer_nl']}_", ""]
    L += ["## Wat is aangetoond?"]
    L += [f"- {c['explanation_nl']}" for c in passed] or ["- Niets is overtuigend aangetoond."]
    L += ["", "## Wat is onzeker?"]
    L += [f"- [{_SYM[c['status']]}] {c['name']}: {c['explanation_nl']}" for c in open_] or ["- Geen open punten in de checks."]
    L += ["", "## Waar verdient de strategie geld?"]
    reg = (ev.get("regime_breakdown") or {}).get("regimes") or {}
    win = {k: r for k, r in reg.items() if r["net_profit"] > 0}
    L += [f"- Regime `{k}`: netto {_fmt(r['net_profit'])} over {r['n_trades']} trades (PF {_fmt(r['profit_factor'])})."
          for k, r in sorted(win.items(), key=lambda kv: -kv[1]["net_profit"])] or ["- Geen regime-uitsplitsing aangeleverd (niet getest)."]
    yr = (ev.get("yearly_breakdown") or ev.get("yearly") or {}).get("years") or {}
    L += [f"- Jaar {k}: netto {_fmt(r['net_profit'])}." for k, r in yr.items() if r["net_profit"] > 0]
    L += ["", "## Wanneer verliest zij?"]
    lose = {k: r for k, r in reg.items() if r["net_profit"] <= 0}
    L += [f"- Regime `{k}`: netto {_fmt(r['net_profit'])} over {r['n_trades']} trades." for k, r in lose.items()]
    L += [f"- Jaar {k}: netto {_fmt(r['net_profit'])}." for k, r in yr.items() if r["net_profit"] <= 0]
    if not lose and not any(r["net_profit"] <= 0 for r in yr.values()):
        L += ["- Geen verliesgevende regimes/jaren gevonden in het aangeleverde bewijs (of niet getest)."]
    mc = ev.get("monte_carlo") or {}
    if mc.get("status") == "OK":
        L += [f"- Monte Carlo ({mc['method']}): 95e percentiel max drawdown {_fmt(mc['max_drawdown_pct']['p95'])}%, "
              f"kans op ruin-drempel {_fmt(mc['risk_of_ruin'], pct=True)} (eigenschap van de herbemonsterde historie, geen voorspelling)."]
    L += ["", "## Kostengevoeligheid?"]
    cs = checks["cost_stress"]
    L += [f"- [{_SYM[cs['status']]}] {cs['explanation_nl']}"]
    scn = (ev.get("cost_stress") or {}).get("scenarios") or {}
    for name, m in scn.items():
        L += [f"  - {name}: netto {_fmt(m['net_profit'])}, PF {_fmt(m['profit_factor'])}"]
    L += ["", "## Hoeveel hypotheses geprobeerd?"]
    nt = ev.get("n_trials")
    L += [f"- {nt} geprobeerde configuraties; de DSR corrigeert hiervoor." if nt else
          "- Aantal geprobeerde hypotheses onbekend: DSR is dan niet betrouwbaar interpreteerbaar."]
    L += ["", "## Genoeg OOS-data?"]
    for n in ("oos_profitability", "walk_forward_efficiency"):
        L += [f"- [{_SYM[checks[n]['status']]}] {checks[n]['explanation_nl']}"]
    L += ["", "## Parameters stabiel?"]
    L += [f"- [{_SYM[checks['parameter_stability']['status']]}] {checks['parameter_stability']['explanation_nl']}"]
    L += ["", "## Statistisch overtuigend?"]
    for n in ("psr", "dsr", "pbo", "drop_best_trades", "yearly_consistency"):
        L += [f"- [{_SYM[checks[n]['status']]}] {checks[n]['explanation_nl']}"]
    L += ["", "## Open risico's"]
    L += ["- Trades zijn niet iid; p-waarden en CI's zijn op dagrendementen met blokbootstrap berekend.",
          "- Historische resultaten zeggen niets zeker over de toekomst (regimewissel, kosten, liquiditeit).",
          "- Backtest-aannames (fills, spread, slippage) kunnen afwijken van de werkelijkheid."]
    L += [f"- Open check: {c['name']} ({_SYM[c['status']]})." for c in open_]
    L += [f"- {n}" for n in sc.get("notes_nl", [])]
    L += ["", "## Checks", "", "| Check | Status | Waarde | Drempel |", "|---|---|---|---|"]
    for c in sc["checks"]:
        val = "-" if c["value"] is None else (f"{c['value']:.3f}" if isinstance(c["value"], float) else str(c["value"]))
        L += [f"| {c['name']} | {_SYM[c['status']]} | {val} | {c['threshold']} |"]
    return "\n".join(L) + "\n"
