"""Profit distribution analysis per strategy: where does the P&L come from, and how concentrated is it?

Everything is computed from the real trade list (UTC timestamps). Days are UTC exit days; weekends without trades
are not counted as trading days. A concentration WARNING is raised when e.g. > 50% of net profit comes from <= 3 days.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from runtime.strategy_lab import rule_engine as engine

from . import validation

# UTC entry-hour buckets (approximate; DST shifts the real sessions by an hour)
SESSIONS_UTC = (("azie", 0, 7), ("londen", 7, 13), ("londen_newyork", 13, 16), ("newyork", 16, 21), ("laat", 21, 24))
CONCENTRATION_DAYS = 3
CONCENTRATION_SHARE = 0.5


def _f(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _frame(trades) -> pd.DataFrame:
    df = pd.DataFrame(list(trades or []))
    if df.empty:
        return df
    df["pnl"] = df["pnl"].astype(float)
    df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True)
    df["entry_time"] = pd.to_datetime(df.get("entry_time", df["exit_time"]), utc=True)
    return df.sort_values("exit_time", kind="stable").reset_index(drop=True)


def _streak(signs) -> tuple[int, int]:
    best_w = best_l = cur_w = cur_l = 0
    for s in signs:
        if s > 0:
            cur_w, cur_l = cur_w + 1, 0
        elif s < 0:
            cur_l, cur_w = cur_l + 1, 0
        else:
            cur_w = cur_l = 0
        best_w, best_l = max(best_w, cur_w), max(best_l, cur_l)
    return best_w, best_l


def _period_table(series: pd.Series, capital: float) -> list[dict]:
    return [{"period": str(k), "pnl": _f(v), "return_pct": _f(v / capital * 100.0)} for k, v in series.items()]


def session_of(ts: pd.Timestamp) -> str:
    h = ts.hour
    for name, lo, hi in SESSIONS_UTC:
        if lo <= h < hi:
            return name
    return "laat"


def regime_labels(bid: pd.DataFrame, adx_period: int = 14, trend_adx: float = 25.0) -> dict:
    """Day -> 'trend'/'range' (daily ADX >= 25) + '_hoog'/'_laag' realized-vol suffix (median split). Labels use
    data up to and including that day (causal on daily closes)."""
    if bid is None or len(bid) < 50:
        return {}
    d = bid.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    if len(d) < adx_period * 3:
        return {}
    a = engine.adx(d["high"].to_numpy(float), d["low"].to_numpy(float), d["close"].to_numpy(float), adx_period)
    rv = np.log(d["close"]).diff().rolling(20).std()
    med = float(rv.median()) if rv.notna().any() else 0.0
    out = {}
    for i, day in enumerate(d.index):
        if not np.isfinite(a[i]) or not np.isfinite(rv.iloc[i]):
            continue
        out[day.strftime("%Y-%m-%d")] = ("trend" if a[i] >= trend_adx else "range") + (
            "_hoog" if rv.iloc[i] >= med else "_laag")
    return out


def analyse(trades, capital: float, *, regimes: dict | None = None, top_n=(5, 10)) -> dict:
    df = _frame(trades)
    out = {"n_trades": int(len(df)), "warnings_nl": [], "flags": []}
    if df.empty:
        out["status"] = "INSUFFICIENT_EVIDENCE"
        return out
    net = float(df["pnl"].sum())
    day = df["exit_time"].dt.tz_convert("UTC").dt.tz_localize(None).dt.normalize()
    daily = df.groupby(day)["pnl"].sum()
    eq_before = capital + daily.cumsum().shift(1, fill_value=0.0)
    daily_ret = daily / eq_before.clip(lower=1e-9 * capital) * 100.0
    win_days, loss_days = int((daily > 0).sum()), int((daily < 0).sum())
    best = daily.sort_values(ascending=False)
    worst = daily.sort_values()
    gross_loss = float(-daily[daily < 0].sum())
    out.update(
        net_profit=net, trading_days=int(len(daily)),
        daily_avg_pct=_f(daily_ret.mean()), daily_median_pct=_f(daily_ret.median()),
        winning_days=win_days, losing_days=loss_days,
        winning_day_share=_f(win_days / len(daily)) if len(daily) else None,
        avg_winning_day=_f(daily[daily > 0].mean()) if win_days else None,
        avg_losing_day=_f(daily[daily < 0].mean()) if loss_days else None,
    )
    share = {}
    for n in top_n:
        share[str(n)] = _f(best.head(n).clip(lower=0).sum() / net) if net > 0 else None
    out["profit_share_best_days"] = share
    share3 = _f(best.head(CONCENTRATION_DAYS).clip(lower=0).sum() / net) if net > 0 else None
    out["profit_share_best_3_days"] = share3
    out["loss_share_worst_5_days"] = _f(-worst.head(5).clip(upper=0).sum() / gross_loss) if gross_loss > 0 else None
    without = {}
    for n in (1, 3, 5, 10):
        rest = daily.drop(best.head(n).index)
        without[str(n)] = {"net_after": _f(rest.sum()), "still_positive": bool(rest.sum() > 0)}
    out["without_top_days"] = without
    if net > 0 and share3 is not None and share3 > CONCENTRATION_SHARE:
        out["flags"].append("PROFIT_CONCENTRATION")
        out["warnings_nl"].append(f"WAARSCHUWING: {share3:.0%} van de nettowinst komt uit de beste "
                                  f"{CONCENTRATION_DAYS} dagen; het resultaat hangt aan een paar dagen.")
    if net > 0 and not without["5"]["still_positive"]:
        out["flags"].append("DEPENDS_ON_FEW_DAYS")
        out["warnings_nl"].append("WAARSCHUWING: zonder de beste 5 dagen is het resultaat niet meer positief.")
    local = df["exit_time"].dt.tz_convert("UTC").dt.tz_localize(None)
    out["weekly"] = _period_table(df.groupby(local.dt.to_period("W"))["pnl"].sum().tail(520), capital)
    out["monthly"] = _period_table(df.groupby(local.dt.to_period("M"))["pnl"].sum(), capital)
    out["yearly"] = _period_table(df.groupby(local.dt.year)["pnl"].sum(), capital)
    wd, ld = _streak(np.sign(daily.to_numpy()))
    wt, lt = _streak(np.sign(df["pnl"].to_numpy()))
    out.update(max_consecutive_winning_days=wd, max_consecutive_losing_days=ld,
               max_consecutive_winning_trades=wt, max_consecutive_losing_trades=lt)
    # drawdown durations on the realized equity (per trade exit)
    eq = capital + df["pnl"].cumsum().to_numpy()
    times = df["exit_time"].dt.tz_localize(None).to_numpy()
    peak_val, peak_t, longest_under, longest_dd = capital, df["entry_time"].dt.tz_localize(None).to_numpy()[0], 0.0, 0.0
    dd_start = None
    for v, t in zip(eq, times):
        if v >= peak_val:
            if dd_start is not None:
                longest_dd = max(longest_dd, (t - dd_start) / np.timedelta64(1, "D"))
            dd_start = None
            longest_under = max(longest_under, (t - peak_t) / np.timedelta64(1, "D"))
            peak_val, peak_t = v, t
        elif dd_start is None:
            dd_start = peak_t
    if dd_start is not None:
        longest_dd = max(longest_dd, (times[-1] - dd_start) / np.timedelta64(1, "D"))
        longest_under = max(longest_under, (times[-1] - peak_t) / np.timedelta64(1, "D"))
    out["longest_drawdown_days"] = _f(longest_dd)
    out["longest_time_under_high_days"] = _f(max(longest_under, longest_dd))
    rolling = {}
    p = df["pnl"]
    for w in (20, 50, 100):
        if len(p) >= w:
            r = p.rolling(w).sum().dropna()
            rolling[str(w)] = {"min": _f(r.min()), "median": _f(r.median()), "max": _f(r.max()),
                               "share_positive": _f((r > 0).mean()),
                               "series": [_f(x) for x in r.iloc[:: max(1, len(r) // 200)]]}
        else:
            rolling[str(w)] = {"status": "INSUFFICIENT_EVIDENCE"}
    out["rolling_trades"] = rolling
    sess = df["entry_time"].map(session_of)
    out["sessions"] = {k: validation._group_stats(g) for k, g in df.assign(session=sess).groupby("session")}
    if regimes:
        key = df["entry_time"].dt.strftime("%Y-%m-%d").map(lambda d: regimes.get(d, "onbekend"))
        reg = {k: validation._group_stats(g) for k, g in df.assign(regime=key).groupby("regime")}
        out["regimes"] = reg
        pos = [k for k, v in reg.items() if k != "onbekend" and v["n_trades"] >= 10]
        if pos and all(reg[k]["net_profit"] > 0 for k in pos):
            out["regime_note_nl"] = "Winstgevend in alle regimes met >= 10 trades."
        elif pos:
            bad = [k for k in pos if reg[k]["net_profit"] <= 0]
            out["regime_note_nl"] = "Verliesgevend in regime(s): " + ", ".join(bad) + "."
    # inactivity: longest stretch without any trade entry
    ent = df["entry_time"].sort_values()
    gaps = ent.diff().dt.total_seconds().div(86400).dropna()
    out["longest_inactive_days"] = _f(gaps.max()) if len(gaps) else None
    out["status"] = "OK" if len(df) >= validation.MIN_OBS else "INSUFFICIENT_EVIDENCE"
    return out


def daily_return_series(trades, capital: float) -> pd.Series:
    return validation.daily_returns_from_trades(trades, capital)


def correlation_matrix(series_by_id: dict, min_overlap: int = 20) -> dict:
    """Pearson correlation of daily returns (business days, zero-filled where a strategy had no exit)."""
    ids = [k for k, s in series_by_id.items() if s is not None and len(s) >= 2]
    if len(ids) < 2:
        return {"ids": ids, "matrix": [], "status": "INSUFFICIENT_EVIDENCE"}
    frame = pd.concat([series_by_id[k].rename(k) for k in ids], axis=1).fillna(0.0)
    if len(frame) < min_overlap:
        return {"ids": ids, "matrix": [], "status": "INSUFFICIENT_EVIDENCE"}
    corr = frame.corr().fillna(0.0)
    mat = [[_f(corr.loc[a, b]) for b in ids] for a in ids]
    high = [{"a": a, "b": b, "corr": _f(corr.loc[a, b])} for i, a in enumerate(ids) for b in ids[i + 1:]
            if abs(corr.loc[a, b]) >= 0.7]
    return {"ids": ids, "matrix": mat, "high_pairs": high, "n_days": int(len(frame)), "status": "OK"}
