"""Nested walk-forward validation with purging and embargo derived from the strategy's real dependency length.

Outer loop: rolling or expanding train windows followed by unseen test windows (the OOS evidence).
Inner loop (inside each outer train window only): level 1 picks parameters on the inner-train part, level 2
picks the architecture / filter variant on the inner-validation part; the chosen variant's parameters are then
re-picked on the full outer train window and evaluated once on the outer test window.

Purge / embargo (Lopez de Prado 2018, ch. 7) are computed from the dependency of one observation:
* purge   = longest holding period (a trade opened before a test window may not be scored in training when its
            outcome overlaps the test window) -> train trades whose exit falls in the gap/test are removed and the
            train window ends ``purge`` before the test window;
* embargo = holding period + longest indicator look-back: only needed when training data FOLLOWS a validation
            block (blocked k-fold inner mode); walk-forward training data always precedes its test data.
Bars are converted to calendar days with a 7/5 weekend factor, rounded up, plus one day.

Default evaluation slices one causal backtest per configuration over the whole range (``evaluate="slice"``) and
rescales every trade to a fresh window capital (pnl * capital / equity before the trade), so window results do
not depend on profits earned in earlier windows. ``evaluate="window"`` re-runs the backtest per window instead.
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np
import pandas as pd

from . import validation

BAR_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
INSUFFICIENT = validation.INSUFFICIENT


def _canon(cfg) -> str:
    import json
    return json.dumps(cfg, sort_keys=True, separators=(",", ":"), default=str)


def _bars_to_days(bars: float, timeframe: str) -> int:
    minutes = float(bars) * BAR_MINUTES.get(timeframe, 60)
    return int(math.ceil(minutes / 1440.0 * 7.0 / 5.0)) + 1


def dependency_gap(spec: dict | None, timeframe: str, params: dict | None = None, trades=None,
                   lookback_bars: int | None = None) -> dict:
    """Purge and embargo in calendar days from the spec (look-back, time exit) and observed holding periods."""
    if lookback_bars is None and spec is not None:
        try:
            from runtime.strategy_lab import rule_engine as engine
            lookback_bars = int(engine.lookback_bars(engine.resolve(spec, params)))
        except Exception:
            lookback_bars = None
    held = [int(t.get("bars_held") or 0) for t in (trades or []) if t.get("bars_held") is not None]
    observed = max(held) if held else None
    time_exit = (spec or {}).get("time_exit_bars")
    if isinstance(time_exit, str) and params:
        time_exit = params.get(time_exit.lstrip("$"))
    try:
        time_exit = int(time_exit) if time_exit is not None else None
    except (TypeError, ValueError):
        time_exit = None
    cands = [x for x in (time_exit, observed) if x]
    max_hold = max(cands) if cands else None
    source = "time_exit_bars" if time_exit and (observed is None or time_exit >= observed) else "observed_max_bars_held"
    if max_hold is None:
        max_hold, source = 100, "default_100_bars (geen time-exit en geen trades)"
    elif source == "observed_max_bars_held":
        max_hold = int(math.ceil(max_hold * 1.5))  # unseen longer trades: 50% margin on the observed maximum
    lb = int(lookback_bars or 0)
    return {"lookback_bars": lb, "max_holding_bars": int(max_hold), "holding_source": source,
            "timeframe": timeframe, "purge_days": _bars_to_days(max_hold, timeframe),
            "embargo_days": _bars_to_days(max_hold + lb, timeframe),
            "definition": "purge = max holding period; embargo = max holding + max look-back (bars -> dagen x7/5 +1)"}


# ------------------------------------------------------------------------------------------------ windows
def outer_windows(start, end, train_days: int, test_days: int, step_days: int | None = None, mode: str = "rolling",
                  purge_days: int = 0) -> list[dict]:
    if mode not in ("rolling", "expanding"):
        raise ValueError("mode must be rolling|expanding")
    step_days = step_days or test_days
    s0, e0 = pd.Timestamp(start), pd.Timestamp(end)
    out, cur = [], s0
    while True:
        tr0 = s0 if mode == "expanding" else cur
        te0 = cur + pd.Timedelta(days=train_days)
        te1 = te0 + pd.Timedelta(days=test_days)
        if te1 > e0:
            break
        tr1 = te0 - pd.Timedelta(days=purge_days)
        if tr1 > tr0:
            out.append({"train": (tr0, tr1), "test": (te0, te1)})
        cur += pd.Timedelta(days=step_days)
    return out


# ------------------------------------------------------------------------------------------------ trade slicing
def _frame(trades) -> pd.DataFrame:
    df = pd.DataFrame(list(trades or []))
    if df.empty:
        return pd.DataFrame(columns=["entry_time", "exit_time", "pnl"])
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True).dt.tz_localize(None)
    df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True).dt.tz_localize(None)
    df["pnl"] = df["pnl"].astype(float)
    return df.sort_values("exit_time", kind="stable").reset_index(drop=True)


def rescale_fresh_capital(trades, capital: float) -> list[dict]:
    """pnl_i * capital / equity_before_i: every trade as if the account held ``capital`` when it was opened."""
    df = _frame(trades)
    if df.empty:
        return []
    eq_before = capital + df["pnl"].cumsum().shift(1, fill_value=0.0)
    scale = capital / eq_before.clip(lower=1e-9 * capital)
    out = []
    for rec, sc in zip(df.to_dict("records"), scale):
        rec = dict(rec)
        rec["pnl"] = float(rec["pnl"] * sc)
        for k in ("costs", "qty"):
            if rec.get(k) is not None and np.isfinite(rec[k]):
                rec[k] = float(rec[k] * sc)
        rec["entry_time"] = pd.Timestamp(rec["entry_time"]).tz_localize("UTC").isoformat()
        rec["exit_time"] = pd.Timestamp(rec["exit_time"]).tz_localize("UTC").isoformat()
        out.append(rec)
    return out


def slice_trades(df: pd.DataFrame, start, end, purge_until=None) -> list[dict]:
    """Trades entered in [start, end). With ``purge_until`` trades whose exit is >= that time are removed (their
    outcome overlaps the following block)."""
    if df.empty:
        return []
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    m = (df["entry_time"] >= s) & (df["entry_time"] < e)
    if purge_until is not None:
        m &= df["exit_time"] < pd.Timestamp(purge_until)
    sub = df[m]
    recs = sub.to_dict("records")
    for r in recs:
        r["entry_time"] = pd.Timestamp(r["entry_time"]).tz_localize("UTC").isoformat()
        r["exit_time"] = pd.Timestamp(r["exit_time"]).tz_localize("UTC").isoformat()
    return recs


def window_metrics(trades, capital: float, start, end) -> dict:
    tm = validation.trade_metrics(trades, capital)
    n = tm["n_trades"]
    days = max((pd.Timestamp(end) - pd.Timestamp(start)).days, 1)
    daily = validation.daily_returns_from_trades(trades, capital)
    return {"trades": n, "net_profit": tm["net_profit"], "profit_factor": tm["profit_factor"],
            "win_rate": tm["win_rate"], "expectancy": tm["net_profit"] / n if n else 0.0,
            "max_drawdown_pct": tm["max_drawdown_pct"], "sharpe_daily": tm["sharpe"], "days": days,
            "net_per_day": tm["net_profit"] / days, "n_days": int(len(daily))}


def _score(m: dict, objective: str, min_trades: int) -> float:
    if m["trades"] < min_trades:
        return -math.inf
    if objective == "sharpe":
        return m["sharpe_daily"]
    if objective == "profit_factor":
        return min(m["profit_factor"], 50.0)
    return m["net_per_day"]


# ------------------------------------------------------------------------------------------------ nested WF
def nested_walk_forward(backtest_fn: Callable, configs: list[dict], start, end, *, train_days: int, test_days: int,
                        step_days: int | None = None, mode: str = "rolling", gap: dict | None = None,
                        inner_val_frac: float = 0.3, inner: str = "last_block", inner_folds: int = 3,
                        objective: str = "sharpe", min_trades: int = 10, capital: float = 10000.0,
                        evaluate: str = "slice", regimes: dict | None = None) -> dict:
    """``configs``: list of {"variant": str, "params": dict} (architecture/filter variant + parameters).
    ``backtest_fn(config, start, end) -> {"trades": [...]}`` (causal; never called outside [start, end)).
    Returns per-window train/OOS metrics, chosen configuration, parameter changes, pooled OOS trades and
    summary (profitable windows, worst OOS DD, WFE mean and pooled, degradation, single-window dependence)."""
    if not configs:
        raise ValueError("configs is leeg")
    gap = gap or {"purge_days": 0, "embargo_days": 0}
    purge, embargo = int(gap.get("purge_days") or 0), int(gap.get("embargo_days") or 0)
    wins = outer_windows(start, end, train_days, test_days, step_days, mode, purge)
    cache: dict = {}

    def trades_of(cfg, s, e):
        if evaluate == "window":
            return _frame(rescale_fresh_capital(backtest_fn(cfg, _d(s), _d(e)).get("trades", []), capital))
        key = _canon(cfg)
        if key not in cache:
            r = backtest_fn(cfg, _d(start), _d(end))
            cache[key] = _frame(rescale_fresh_capital(r.get("trades", []), capital))
        return cache[key]

    def eval_block(cfg, s, e, purge_until=None):
        tr = slice_trades(trades_of(cfg, s, e), s, e, purge_until)
        return tr, window_metrics(tr, capital, s, e)

    variants: dict = {}
    for c in configs:
        variants.setdefault(c.get("variant", "base"), []).append(c)

    def best_params(cands, s, e, purge_until):
        best, bv, bm = None, -math.inf, None
        for c in cands:
            _, m = eval_block(c, s, e, purge_until)
            v = _score(m, objective, min_trades)
            if v > bv:
                best, bv, bm = c, v, m
        return best, bm

    def inner_splits(tr0, tr1):
        span = (tr1 - tr0).days
        if inner == "kfold":
            edges = [tr0 + pd.Timedelta(days=int(span * i / inner_folds)) for i in range(inner_folds + 1)]
            out = []
            for i in range(inner_folds):
                v0, v1 = edges[i], edges[i + 1]
                train_blocks = []
                if v0 - pd.Timedelta(days=purge) > tr0:
                    train_blocks.append((tr0, v0 - pd.Timedelta(days=purge)))
                if v1 + pd.Timedelta(days=embargo) < tr1:
                    train_blocks.append((v1 + pd.Timedelta(days=embargo), tr1))
                out.append((train_blocks, (v0, v1)))
            return out
        v0 = tr1 - pd.Timedelta(days=max(1, int(span * inner_val_frac)))
        return [([(tr0, v0 - pd.Timedelta(days=purge))], (v0, tr1))]

    windows, oos_trades, prev_cfg = [], [], None
    for i, w in enumerate(wins):
        tr0, tr1 = w["train"]
        te0, te1 = w["test"]
        inner_log = []
        best_var, best_val = None, -math.inf
        for vname, cands in variants.items():
            vals = []
            for blocks, (v0, v1) in inner_splits(tr0, tr1):
                blocks = [b for b in blocks if b[1] > b[0]]
                if not blocks:
                    continue
                # level 1: parameters on the inner-train blocks (purged at the block end)
                scores = []
                for c in cands:
                    ms = [eval_block(c, b0, b1, b1)[1] for b0, b1 in blocks]
                    tot = {"trades": sum(m["trades"] for m in ms),
                           "sharpe_daily": float(np.mean([m["sharpe_daily"] for m in ms])),
                           "profit_factor": float(np.mean([m["profit_factor"] for m in ms])),
                           "net_per_day": float(np.mean([m["net_per_day"] for m in ms]))}
                    scores.append((_score(tot, objective, min_trades), c))
                scores.sort(key=lambda x: x[0], reverse=True)
                if not scores or scores[0][0] == -math.inf:
                    continue
                pick = scores[0][1]
                # level 2: the variant's picked parameters on the inner-validation block
                _, mv = eval_block(pick, v0, v1, tr1)
                vals.append(_score(mv, objective, max(1, min_trades // 2)))
            val = float(np.mean(vals)) if vals and all(np.isfinite(vals)) else -math.inf
            inner_log.append({"variant": vname, "inner_val_score": None if not np.isfinite(val) else val})
            if val > best_val:
                best_var, best_val = vname, val
        rec = {"index": i, "train": [_d(tr0), _d(tr1)], "test": [_d(te0), _d(te1)], "inner": inner_log,
               "purge_days": purge, "config": None, "params": None, "variant": best_var,
               "train_metrics": None, "test_metrics": None, "wfe": None, "params_changed": None}
        if best_var is None:
            rec.update(status="INSUFFICIENT", reason=f"geen variant met >= {min_trades} trades in de binnenste lus")
            windows.append(rec)
            continue
        cfg, trm = best_params(variants[best_var], tr0, tr1, te0)
        if cfg is None:
            rec.update(status="INSUFFICIENT", reason=f"geen parameters met >= {min_trades} trades in train")
            windows.append(rec)
            continue
        tt, tem = eval_block(cfg, te0, te1)
        oos_trades.extend(tt)
        rec.update(config=cfg, params=cfg.get("params"), train_metrics=trm, test_metrics=tem,
                   params_changed=None if prev_cfg is None else _canon(cfg) != _canon(prev_cfg),
                   status="OK" if tem["trades"] >= min_trades else "INSUFFICIENT")
        if rec["status"] != "OK":
            rec["reason"] = f"test-window heeft < {min_trades} trades"
        if trm["net_per_day"] > 0:
            rec["wfe"] = tem["net_per_day"] / trm["net_per_day"]
        if regimes:
            rec["regimes"] = _regime_table(tt, regimes)
        windows.append(rec)
        prev_cfg = cfg
    return _summarize(windows, oos_trades, capital, mode, gap, objective, inner, len(configs), regimes)


def _d(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _regime_table(trades, regimes: dict) -> dict:
    out: dict = {}
    for t in trades:
        lab = regimes.get(str(t["entry_time"])[:10], "onbekend")
        r = out.setdefault(lab, {"trades": 0, "net_profit": 0.0, "wins": 0})
        r["trades"] += 1
        r["net_profit"] += float(t["pnl"])
        r["wins"] += 1 if t["pnl"] > 0 else 0
    return out


MIN_WINDOWS_SINGLE = 4


def _summarize(windows, oos_trades, capital, mode, gap, objective, inner, n_configs, regimes) -> dict:
    ok = [w for w in windows if w["status"] == "OK"]
    oos_trades = sorted(oos_trades, key=lambda t: t["exit_time"])
    nets = [w["test_metrics"]["net_profit"] for w in ok]
    wfes = [w["wfe"] for w in ok if w["wfe"] is not None]
    is_rate = [w["train_metrics"]["net_per_day"] for w in ok]
    oos_rate = [w["test_metrics"]["net_per_day"] for w in ok]
    pooled_wfe = (float(np.mean(oos_rate)) / float(np.mean(is_rate))) if ok and np.mean(is_rate) > 0 else None
    total = float(sum(nets))
    best = max(nets) if nets else None
    single = {"best_window_share": (best / total) if nets and total > 0 else None,
              "net_without_best_window": (total - best) if nets else None}
    single["flag"] = bool(nets) and (total <= 0 or (single["best_window_share"] or 0) > 0.5
                                     or (single["net_without_best_window"] or 0) <= 0)
    # with few windows one window carries a large share by construction: the flag is then not decidable
    single.update(n_windows=len(nets), min_windows=MIN_WINDOWS_SINGLE, decidable=len(nets) >= MIN_WINDOWS_SINGLE)
    om = validation.trade_metrics(oos_trades, capital)
    is_sh = [w["train_metrics"]["sharpe_daily"] for w in ok]
    oos_sh = [w["test_metrics"]["sharpe_daily"] for w in ok]
    is_ex = [w["train_metrics"]["expectancy"] for w in ok]
    oos_ex = [w["test_metrics"]["expectancy"] for w in ok]
    degr = {"sharpe_is_mean": float(np.mean(is_sh)) if ok else None,
            "sharpe_oos_mean": float(np.mean(oos_sh)) if ok else None,
            "expectancy_is_mean": float(np.mean(is_ex)) if ok else None,
            "expectancy_oos_mean": float(np.mean(oos_ex)) if ok else None}
    degr["sharpe_ratio_oos_is"] = (degr["sharpe_oos_mean"] / degr["sharpe_is_mean"]
                                   if ok and degr["sharpe_is_mean"] and degr["sharpe_is_mean"] > 0 else None)
    degr["expectancy_ratio_oos_is"] = (degr["expectancy_oos_mean"] / degr["expectancy_is_mean"]
                                       if ok and degr["expectancy_is_mean"] and degr["expectancy_is_mean"] > 0 else None)
    changes = [w["params_changed"] for w in windows if w.get("params_changed") is not None]
    reg: dict = {}
    if regimes:
        for w in ok:
            for k, v in (w.get("regimes") or {}).items():
                r = reg.setdefault(k, {"trades": 0, "net_profit": 0.0, "wins": 0})
                for f in r:
                    r[f] += v[f]
    eq, e = [], capital
    for t in oos_trades:
        e += t["pnl"]
        eq.append({"time": t["exit_time"], "equity": e})
    return {
        "mode": mode, "objective": objective, "inner": inner, "n_configs": n_configs, "gap": gap,
        "n_windows": len(windows), "n_windows_ok": len(ok), "windows": windows, "oos_trades": oos_trades,
        "oos_equity": eq, "oos_metrics": om, "oos_net_profit": om["net_profit"], "oos_n_trades": om["n_trades"],
        "profitable_oos_windows": int(sum(1 for x in nets if x > 0)),
        "oos_profitable_windows_frac": float(np.mean([x > 0 for x in nets])) if nets else None,
        "worst_oos_window_dd_pct": max((w["test_metrics"]["max_drawdown_pct"] for w in ok), default=None),
        "pooled_oos_dd_pct": om["max_drawdown_pct"],
        "wfe": float(np.mean(wfes)) if wfes else None, "wfe_median": float(np.median(wfes)) if wfes else None,
        "wfe_pooled": pooled_wfe, "degradation": degr, "single_window_dependence": single,
        "param_change_rate": float(np.mean(changes)) if changes else None, "regimes": reg or None,
        "wfe_definition": ("per venster (OOS netto/dag) / (IS netto/dag) voor IS > 0; wfe = gemiddelde, wfe_pooled = "
                           "gemiddelde OOS-rate / gemiddelde IS-rate (stabieler bij kleine IS-winst)"),
        "status": "OK" if ok and wfes else INSUFFICIENT,
    }
