"""Cross-market generalisation test: one frozen strategy, many markets/timeframes, development data only.

Frozen parameters are never re-optimised (``reoptimise=True`` is a separate experiment with its own run id and a
ledger family suffix). Every market x timeframe cell is a *selection experiment* recorded in the research ledger,
and the generalisation class is computed over ALL requested cells, so picking the best market afterwards cannot
improve the evidence: a single-market run is capped at MARKET_SPECIFIC and carries the trial count.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from runtime.strategy_lab import rule_engine as engine

from . import adapters, backtest, factory, instruments, protocol, validation
from . import ledger as _ledger_mod
from .jobs import JobCancelled

MIN_TRADES = 30
MIN_DAYS = 365
STABILITY_MAX_NEIGHBOURS = 6
CLASSES = ("MARKET_SPECIFIC", "PARTIAL_GENERALISATION", "BROAD_GENERALISATION", "NO_EDGE", "NOT_TESTABLE")
SKIPPED = ("INSUFFICIENT_DATA", "INSTRUMENT_BOUND")  # cells that are not evidence either way


def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def strategy_sha(spec: dict) -> str:
    core = {k: v for k, v in spec.items() if k not in ("meta", "symbol", "timeframe", "name")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def resolve_period(start, end, first_utc, warmup_days: int):
    """Development range only: [max(DEV_START, first+warmup), min(end, SEALED_START)). Sealed bars are refused."""
    end = end or protocol.SEALED_START
    protocol.assert_search_range(start or protocol.DEV_START, end, protocol.SEALED_START)
    s = protocol.utc(start or protocol.DEV_START)
    if first_utc:
        s = max(s, (protocol.utc(first_utc) + pd.Timedelta(days=warmup_days)).ceil("D"))
    return s, protocol.utc(end)


def _neighbours(spec: dict, params: dict, cap: int) -> list[dict]:
    out = []
    for p in spec.get("params", []):
        name, v = p["name"], params.get(p["name"], p.get("default"))
        if v is None:
            continue
        step = p.get("step") or (1 if p.get("type") == "int" else abs(v) * 0.1 or 0.1)
        for sign in (-1, 1):
            nv = v + sign * step
            nv = int(round(nv)) if p.get("type") == "int" else float(nv)
            if "min" in p:
                nv = max(nv, p["min"])
            if "max" in p:
                nv = min(nv, p["max"])
            if nv != v:
                out.append({**params, name: nv})
    return out[:cap]


def _vol_labels(bid: pd.DataFrame) -> dict:
    """Causal volatility regime per UTC day (percentile of 20d trailing vol among all earlier days)."""
    d = bid["close"].resample("1D").last().dropna()
    vol = np.log(d).diff().rolling(20).std().shift(1)
    labels, hist = {}, []
    for ts, v in vol.items():
        if not math.isfinite(v):
            continue
        if len(hist) >= 60:
            pct = np.searchsorted(np.sort(hist), v) / len(hist)
            labels[ts.strftime("%Y-%m-%d")] = "LOW" if pct < 1 / 3 else ("HIGH" if pct > 2 / 3 else "MID")
        hist.append(v)
    return labels


def _regime_r(trades, labels: dict) -> dict:
    acc: dict = {}
    for t in trades:
        reg = labels.get(str(t["entry_time"])[:10])
        if reg:
            acc.setdefault(reg, []).append(t["r_multiple"])
    return {k: {"trades": len(v), "avg_r": float(np.mean(v))} for k, v in acc.items()}


def _daily_equity(eq_pts, capital):
    if not eq_pts:
        return pd.Series(dtype=float)
    s = pd.Series([p["equity"] for p in eq_pts], index=pd.to_datetime([p["time"] for p in eq_pts], utc=True))
    s = s[~s.index.duplicated()]
    return s.resample("1D").last().ffill()


def _downsample(series: pd.Series, limit=300):
    if series.empty:
        return []
    step = max(1, len(series) // limit)
    sub = series.iloc[::step]
    if sub.index[-1] != series.index[-1]:
        sub = pd.concat([sub, series.iloc[[-1]]])
    return [{"time": ts.isoformat(), "equity": float(v)} for ts, v in sub.items()]


def _series_metrics(eq: pd.Series, capital: float) -> dict:
    if len(eq) < 3:
        return {}
    peak = eq.cummax()
    r = eq.pct_change().dropna().to_numpy()
    sd = r.std(ddof=1) if len(r) > 2 else 0.0
    return {"net_return_pct": float((eq.iloc[-1] / capital - 1) * 100.0),
            "max_drawdown_pct": float(((peak - eq) / peak).max() * 100.0),
            "sharpe_daily": _num(r.mean() / sd * math.sqrt(252)) if sd > 0 else None}


def effective_markets(corr: np.ndarray) -> float:
    """Participation ratio of the correlation eigenvalues: N for uncorrelated markets, 1 if all move as one."""
    if corr.size == 0:
        return 0.0
    if corr.shape[0] == 1:
        return 1.0
    lam = np.clip(np.linalg.eigvalsh(np.nan_to_num(corr, nan=0.0)), 0, None)
    return float(lam.sum() ** 2 / np.sum(lam ** 2)) if np.sum(lam ** 2) > 0 else 0.0


def classify(cells: list[dict], combined: dict, n_requested_markets: int, eff_pos: float) -> tuple[str, str]:
    markets: dict = {}
    for c in cells:
        if c["validation_status"] in SKIPPED:
            continue
        markets.setdefault(c["symbol"], []).append(c)
    n = len(markets)
    pos = [m for m, cs in markets.items() if sum(c["edge_positive"] for c in cs) * 2 >= len(cs)]
    npos = len(pos)
    net_ok = (combined.get("net_return_pct") or 0) > 0
    if n == 0 and any(c["validation_status"] == "INSTRUMENT_BOUND" for c in cells) and not any(
            c.get("status") == "OK" for c in cells):
        return "NOT_TESTABLE", "Alle gevraagde markten zijn INSTRUMENT_BOUND: de strategie is aan een instrument gebonden."
    if n == 0:
        return "NO_EDGE", "Geen enkele markt had genoeg data om te testen."
    if npos == 0:
        return "NO_EDGE", (f"Geen markt toont een robuuste positieve edge (0/{n} markten positief na kostenstress "
                           "en minimaal 30 trades).")
    if npos <= 1 or npos / n < 0.25 or eff_pos < 1.5:
        return "MARKET_SPECIFIC", (f"Edge zit in {npos} van {n} geteste markten ({', '.join(pos)}); "
                                   "de strategie generaliseert niet.")
    if n >= 4 and npos / n >= 0.6 and npos >= 4 and eff_pos >= 3 and net_ok:
        return "BROAD_GENERALISATION", (f"{npos} van {n} markten positief, ~{eff_pos:.1f} effectief onafhankelijke "
                                        "positieve markten en een positieve gecombineerde curve.")
    capped = " (minder dan 4 markten getest: nooit hoger dan gedeeltelijk)" if n < 4 else ""
    return "PARTIAL_GENERALISATION", (f"{npos} van {n} markten positief (~{eff_pos:.1f} effectief onafhankelijk)"
                                     f"{capped}.")


def _validation_status(m: dict, edge_positive: bool, spread_ok, stab_frac) -> str:
    if m["trades"] < MIN_TRADES:
        return "INSUFFICIENT_TRADES"
    pf = m["profit_factor"] or 0
    if m["net_profit"] > 0 and pf > 1.1 and spread_ok and (stab_frac is None or stab_frac >= 0.6):
        return "PASS"
    if m["net_profit"] > 0 and pf > 1.0:
        return "WEAK"
    return "FAIL"


def _cell_metrics(res: dict, capital: float, start, end) -> dict:
    m = res["metrics"]
    return {"trades": m["trades"], "trades_per_week": m["trades"] / factory.weeks_between(start, end),
            "net_profit": m["net_profit"], "net_return_pct": m["net_profit"] / capital * 100.0,
            "profit_factor": _num(m["profit_factor"]), "recovery_factor": _num(m["recovery_factor"]),
            "max_drawdown_pct": m["max_drawdown_pct"], "sharpe_daily": _num(m["sharpe_daily"]),
            "sortino_daily": _num(m["sortino_daily"]), "win_rate": m["win_rate"], "avg_r": m["avg_r"]}


def _ledger_record(ledger, family, symbol, tf, start, end, spec, params, metrics, status, run_id, extra, *,
                   ledger_status="TESTED", cost_model=None, **more):
    sha = strategy_sha(spec)
    hid = _ledger_mod.hypothesis_id(f"{sha}|{symbol}|{tf}|{family}", params)
    return ledger.record(kind="BACKTEST", status=ledger_status, hypothesis_id=hid, family=family, symbol=symbol,
                         timeframe=tf, start=str(start)[:10], end=str(end)[:10],
                         spec={k: v for k, v in spec.items() if k != "meta"}, params=params, metrics=metrics,
                         verdict=status,
                         cost_model=cost_model or {"commission": "factory.cost_model_for", "spread": "real bid/ask"},
                         statistics={"multimarket": {"run_id": run_id, "selection_experiment": True, **extra}}, **more)


def run_cross_market(ctx, dm, strategy: dict, markets, timeframes, start=None, end=None, *, ledger,
                     reoptimise: bool = False, risk_model: str = "native", risk_percent: float | None = None,
                     capital: float = 10000.0, stability: bool = True, run_id: str | None = None,
                     reopt_candidates: int = 8, live: dict | None = None, account_currency: str = instruments.ACCOUNT_CURRENCY,
                     adapter_options: dict | None = None, min_days: int | None = None, **risk_kw) -> dict:
    """Run a frozen rule strategy on every market x timeframe. ``strategy`` = {kind,label,spec,params}.

    ``live`` (optional dict) is updated in place with partial results for progress polling."""
    run_id = run_id or f"mm_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{hashlib.sha1(str(time.time()).encode()).hexdigest()[:6]}"
    if strategy.get("kind") in ADAPTER_KINDS:
        return run_cross_market_adapter(ctx, dm, strategy, markets, timeframes, start, end, ledger=ledger,
                                        reoptimise=reoptimise, risk_model=risk_model, capital=capital, run_id=run_id,
                                        live=live, adapter_options=adapter_options, min_days=min_days,
                                        account_currency=account_currency, **risk_kw)
    spec = {k: v for k, v in strategy["spec"].items() if k != "meta"}
    errs = engine.validate_spec(spec)
    if errs:
        raise ValueError("invalid spec: " + "; ".join(errs))
    frozen = copy.deepcopy({**{p["name"]: p.get("default") for p in spec.get("params", [])},
                            **(strategy.get("params") or {})})
    if markets in (None, "all"):
        markets = list(instruments.SYMBOLS)
    markets = [instruments.canonical(m) for m in markets]
    timeframes = [t.upper() for t in timeframes or ["M15", "H1"]]
    if end is not None or start is not None:
        protocol.assert_search_range(start or protocol.DEV_START, end or protocol.SEALED_START, protocol.SEALED_START)
    sha = strategy_sha(spec)
    family = f"multimarket:{sha[:12]}" + ("|reopt" if reoptimise else "")
    origin = (strategy["spec"].get("symbol") or "").upper() or None
    cfg = {"run_id": run_id, "strategy": {"kind": strategy.get("kind"), "label": strategy.get("label"),
                                         "spec_sha256": sha, "origin_symbol": origin},
           "markets": markets, "timeframes": timeframes, "start": start, "end": end or protocol.SEALED_START,
           "sealed_start": protocol.SEALED_START, "params_frozen": frozen, "reoptimise": reoptimise,
           "risk_model": risk_model, "risk_percent": risk_percent, "capital": capital, "family": family,
           "account_currency": account_currency, "new_hypothesis": bool(reoptimise)}
    results, equities, ledger_ids, daily = [], {}, [], {}
    total = len(markets) * len(timeframes)
    done = 0
    if live is not None:
        live.update(config=cfg, results=results, equities=equities)
    for sym in markets:
        info = instruments.spec_for(sym, dm, account=account_currency)
        for tf in timeframes:
            ctx.check()
            ctx.progress(done / max(total, 1), f"{sym} {tf}")
            cell = {"symbol": sym, "timeframe": tf, "key": f"{sym}|{tf}", "flags": [], "provisional": True}
            results.append(cell)
            try:
                _run_cell(cell, ctx, dm, spec, frozen, info, sym, tf, start, end, capital, risk_model, risk_percent,
                          stability, reoptimise, reopt_candidates, ledger, family, run_id, origin, equities, daily,
                          ledger_ids, risk_kw, account_currency)
            except JobCancelled:
                raise
            except Exception as exc:
                cell.update(status="ERROR", validation_status="INSUFFICIENT_DATA", edge_positive=False,
                            error=f"{type(exc).__name__}: {exc}")
                cell["flags"].append("ERROR")
            done += 1
    ctx.progress(0.97, "samenvatten")
    return _summarise(cfg, results, equities, daily, ledger, ledger_ids, capital)


def _run_cell(cell, ctx, dm, spec, frozen, info, sym, tf, start, end, capital, risk_model, risk_percent, stability,
              reoptimise, reopt_candidates, ledger, family, run_id, origin, equities, daily, ledger_ids, risk_kw,
              account=instruments.ACCOUNT_CURRENCY):
    rng = info["fields"]["data_range"]["value"] if info["data_available"] else None
    if not rng:
        cell.update(status="NO_DATA", validation_status="INSUFFICIENT_DATA", edge_positive=False)
        cell["flags"].append("NO_DATA")
        return
    warm = protocol.warmup_days_for(spec, tf, frozen)
    s, e = resolve_period(start, end, rng["first"], warm)
    cell.update(start=s.strftime("%Y-%m-%d"), end=e.strftime("%Y-%m-%d"), period_days=(e - s).days)
    if (e - s).days < MIN_DAYS:
        cell.update(status="INSUFFICIENT_DATA", validation_status="INSUFFICIENT_DATA", edge_positive=False)
        cell["flags"].append("SHORT_HISTORY")
        return
    begin = (s - pd.Timedelta(days=warm)).strftime("%Y-%m-%d")
    bid, ask, man = backtest.load_bid_ask(dm, sym, tf, begin, e.strftime("%Y-%m-%d"))
    assert bid.index.max() < protocol.utc(protocol.SEALED_START), "sealed bars leaked into the frame"
    if bid.empty:
        cell.update(status="NO_DATA", validation_status="INSUFFICIENT_DATA", edge_positive=False)
        return
    conv_needed = info["fields"]["account_conversion"]["value"]["needed"]
    conv = (instruments.conversion_series(dm, sym, tf, begin, e.strftime("%Y-%m-%d"), account=account)
            if conv_needed else None)
    if conv_needed and conv is None:
        cell["flags"].append("PROVISIONAL_CURRENCY")
    cell["currency_conversion"] = ("USD_QUOTE" if not conv_needed else
                                   "CONVERTED_CAUSAL" if conv is not None else "UNCONVERTED")
    if conv_needed and conv is None:
        cell["currency_conversion"] = "UNCONVERTED"
    cell["flags"].append("PROVISIONAL_CONTRACT")
    if origin and sym == origin:
        cell["flags"].append("ORIGIN_MARKET_IN_SAMPLE")  # parameters were found on this market's development data
    cm = factory.cost_model_for(sym, capital, (spec.get("risk") or {}).get("percent", 1.0))
    contract = instruments.contract_for(sym, dm, account=account)
    cell["cost_model"] = {"commission_per_lot_side": cm["commission_per_lot"], "spread": "real bid/ask",
                          "contract": {"size": contract.contract_size, "tick": contract.tick_size,
                                       "lot_step": contract.volume_step}}
    mspec, notes = instruments.normalise_spec(spec, risk_model, symbol=sym, timeframe=tf, dm=dm,
                                              from_symbol=origin, risk_percent=risk_percent, **risk_kw)
    cell["risk_notes"] = notes

    def run(params, a, b, **kw):
        return backtest.run_rules_backtest(
            mspec, bid[(bid.index < b)], ask[(ask.index < b)], params, capital=capital, contract=contract,
            commission_per_lot=cm["commission_per_lot"], trade_from=a,
            conversion=conv if conv is not None and len(conv) else None, **kw)

    if reoptimise:
        _reopt_cell(cell, run, mspec, spec, s, e, capital, reopt_candidates, ledger, family, run_id, sym, tf,
                    ledger_ids)
        return
    res = run(dict(frozen), s, e)
    m = _cell_metrics(res, capital, s, e)
    cell["metrics"] = m
    cell["params"] = res["params"]
    cell["currency_status"] = res["diagnostics"].get("currency_status")
    spread_res = run(dict(frozen), s, e, spread_multiplier=1.5)
    sm = _cell_metrics(spread_res, capital, s, e)
    spread_ok = spread_res["metrics"]["net_profit"] > 0
    cell["cost_stress"] = {"spread_+50%": {"net_return_pct": sm["net_return_pct"], "profit_factor": sm["profit_factor"],
                                           "trades": sm["trades"]}, "survives_spread_50": spread_ok}
    stab_frac = None
    if stability and m["trades"] >= MIN_TRADES:
        neigh = []
        for p2 in _neighbours(spec, frozen, STABILITY_MAX_NEIGHBOURS):
            ctx.check()
            r2 = run(p2, s, e)
            neigh.append({"params": p2, "net_profit": r2["metrics"]["net_profit"], "trades": r2["metrics"]["trades"]})
        if neigh:
            stab_frac = float(np.mean([n["net_profit"] > 0 for n in neigh]))
        cell["stability"] = {"neighbours": len(neigh), "frac_positive": stab_frac,
                             "note": "buurwaarden +-1 stap per parameter (max 6); diagnostisch, niet geoptimaliseerd"}
    pf = m["profit_factor"] or 0
    cell["edge_positive"] = bool(m["trades"] >= MIN_TRADES and m["net_profit"] > 0 and pf > 1.05 and spread_ok)
    cell["validation_status"] = _validation_status(m, cell["edge_positive"], spread_ok, stab_frac)
    cell["validation_note"] = "lichte controle (spread +50%, parameterburen); geen walk-forward/DSR per markt"
    cell["status"] = "OK"
    if m["trades"] < MIN_TRADES:
        cell["flags"].append("FEW_TRADES")
    if not spread_ok:
        cell["flags"].append("FAILS_SPREAD_50")
    if stab_frac is not None and stab_frac < 0.6:
        cell["flags"].append("UNSTABLE_PARAMS")
    labels = _vol_labels(bid[bid.index >= s])
    cell["regime_avg_r"] = _regime_r(res["trades"], labels)
    yr = validation.yearly_breakdown(res["trades"])
    cell["yearly"] = {"frac_profitable_years": yr.get("frac_profitable_years") or yr.get("fraction_profitable")}
    eq = _daily_equity(res["equity"], capital)
    daily[cell["key"]] = eq
    equities[cell["key"]] = _downsample(eq)
    ledger_ids.append(_ledger_record(
        ledger, family, sym, tf, s, e, mspec, res["params"], {**m, "spread50_net_profit": sm["net_profit"]},
        cell["validation_status"], run_id,
        {"edge_positive": cell["edge_positive"], "currency_status": cell["currency_status"],
         "neighbours_evaluated": (cell.get("stability") or {}).get("neighbours", 0)}))
    cell["ledger_id"] = ledger_ids[-1]


def _reopt_cell(cell, run, mspec, spec, s, e, capital, n_cand, ledger, family, run_id, sym, tf, ledger_ids):
    """Instrument-specific re-optimisation = NEW hypothesis: random combos on the first 60% (train), best by net profit
    with >= MIN_TRADES, then reported on the untouched remaining 40% (test). Every combo is a ledger trial."""
    rng = np.random.default_rng(int(hashlib.sha256(f"{run_id}{sym}{tf}".encode()).hexdigest()[:8], 16))
    cut = s + (e - s) * 0.6
    cands = []
    for _ in range(max(1, n_cand)):
        p = {}
        for d in spec.get("params", []):
            lo, hi, step = d.get("min", d.get("default")), d.get("max", d.get("default")), d.get("step") or 1
            k = int((hi - lo) / step) if hi > lo else 0
            v = lo + step * int(rng.integers(0, k + 1))
            p[d["name"]] = int(v) if d.get("type") == "int" else float(v)
        cands.append(p)
    scored = []
    for p in cands:
        r = run(p, s, cut)
        m = _cell_metrics(r, capital, s, cut)
        scored.append((m["net_profit"] if m["trades"] >= MIN_TRADES else -1e18, p, m, r["params"]))
        ledger_ids.append(_ledger_record(ledger, family, sym, tf, s, cut, mspec, r["params"], m, "REOPT_TRAIN",
                                         run_id, {"new_hypothesis": True, "phase": "train"}))
    scored.sort(key=lambda x: x[0], reverse=True)
    _, best, train_m, _ = scored[0]
    test = run(best, cut, e)
    tm = _cell_metrics(test, capital, cut, e)
    ledger_ids.append(_ledger_record(ledger, family, sym, tf, cut, e, mspec, test["params"], tm, "REOPT_TEST", run_id,
                                     {"new_hypothesis": True, "phase": "test"}))
    pf = tm["profit_factor"] or 0
    cell.update(status="OK", params=best, metrics=tm, train_metrics=train_m, candidates_tried=len(cands),
                edge_positive=bool(tm["trades"] >= MIN_TRADES and tm["net_profit"] > 0 and pf > 1.05),
                validation_status="REOPTIMISED_TEST_" + ("POSITIVE" if tm["net_profit"] > 0 else "NEGATIVE"),
                validation_note="NIEUWE hypothese: per instrument geoptimaliseerd op 60% train, gerapporteerd op 40% test; "
                                "niet vergelijkbaar met bevroren parameters",
                test_window={"start": cut.strftime("%Y-%m-%d"), "end": e.strftime("%Y-%m-%d")})
    cell["flags"].append("NEW_HYPOTHESIS")
    cell["flags"].append("PROVISIONAL_CONTRACT")


def _summarise(cfg, results, equities, daily, ledger, ledger_ids, capital) -> dict:
    ok = [c for c in results if c.get("status") == "OK"]
    series = {k: v for k, v in daily.items() if len(v) > 2}
    combined = {"equity": [], "by_timeframe": {}}
    corr_out, eff, eff_pos = {"markets": [], "matrix": []}, 0.0, 0.0
    regime = {}
    if series:
        def comb(keys):
            grid = pd.DatetimeIndex(sorted(set().union(*[series[k].index for k in keys])))
            rets = [(series[k].reindex(grid).ffill().fillna(capital) / capital - 1.0) for k in keys]
            eq = capital * (1.0 + pd.concat(rets, axis=1).mean(axis=1))
            return eq
        eq = comb(list(series))
        combined["equity"] = _downsample(eq)
        combined.update(_series_metrics(eq, capital))
        combined["method"] = ("gelijk gewogen: kapitaal gelijk verdeeld over alle cellen; "
                              "cellen zonder data tellen als cash")
        for tf in cfg["timeframes"]:
            keys = [k for k in series if k.endswith("|" + tf)]
            if keys:
                combined["by_timeframe"][tf] = {"n_cells": len(keys), **_series_metrics(comb(keys), capital)}
        per_mkt = {}
        for k, v in series.items():
            per_mkt.setdefault(k.split("|")[0], []).append(v.pct_change().fillna(0.0))
        names = sorted(per_mkt)
        frame = pd.concat([pd.concat(per_mkt[n], axis=1).mean(axis=1).rename(n) for n in names], axis=1).fillna(0.0)
        if len(names) > 1 and len(frame) > 20:
            c = frame.corr().fillna(0.0)
            corr_out = {"markets": names, "matrix": [[round(float(x), 4) for x in row] for row in c.to_numpy()],
                        "basis": "dagelijkse mark-to-market rendementen van de bevroren strategie, gemiddeld over timeframes"}
            eff = effective_markets(c.to_numpy())
            pos_names = [n for n in names if any(r["symbol"] == n and r["edge_positive"] for r in ok)]
            if pos_names:
                eff_pos = effective_markets(c.loc[pos_names, pos_names].to_numpy())
        else:
            eff, eff_pos = float(len(names)), float(len([n for n in names if any(r["symbol"] == n and r["edge_positive"] for r in ok)]))
    for c in ok:
        for reg, v in (c.get("regime_avg_r") or {}).items():
            regime.setdefault(reg, []).append(v["avg_r"])
    n_markets_tested = len({c["symbol"] for c in ok})
    cls, why = (classify(results, combined, len(cfg["markets"]), eff_pos) if not cfg["reoptimise"] else
                ("NOT_COMPARABLE", "Hergeoptimaliseerde run: een nieuwe hypothese; geen generalisatieklasse van de bevroren strategie."))
    pos_markets = sorted({c["symbol"] for c in ok if c["edge_positive"]})
    n_cells = len(ok)
    best = max(ok, key=lambda c: c["metrics"].get("sharpe_daily") or -9, default=None)
    sel = {"n_cells_tested": n_cells, "evidence_class": "EXPLORATORY_SELECTION",
           "note_nl": ("Elke markt x timeframe is een ledger-trial. Het beste resultaat kiezen verbetert het bewijs niet; "
                       "de klasse geldt over alle geteste cellen.")}
    if best and n_cells > 1:
        sh = [c["metrics"].get("sharpe_daily") for c in ok if c["metrics"].get("sharpe_daily") is not None]
        if len(sh) > 1:
            exp_max = validation.expected_max_sr(len(sh), float(np.var(sh, ddof=1)))
            sel.update(best_cell=best["key"], best_sharpe=best["metrics"].get("sharpe_daily"),
                       expected_max_sharpe_by_chance=exp_max,
                       best_beats_chance=bool((best["metrics"].get("sharpe_daily") or 0) > exp_max))
    try:
        sel["ledger_trials_family"] = ledger.n_trials(family=cfg["family"])
    except Exception:
        pass
    gen = {"class": cls, "explanation_nl": why, "n_markets_tested": n_markets_tested,
           "n_markets_positive": len(pos_markets), "markets_positive": pos_markets,
           "origin_market": cfg["strategy"].get("origin_symbol"),
           "n_markets_positive_excl_origin": len([m for m in pos_markets if m != cfg["strategy"].get("origin_symbol")]),
           "n_cells_positive": sum(c["edge_positive"] for c in ok), "n_cells_tested": n_cells,
           "instrument_bound_markets": sorted({c["symbol"] for c in results if c.get("validation_status") == "INSTRUMENT_BOUND"}),
           "n_markets_instrument_bound": len({c["symbol"] for c in results if c.get("validation_status") == "INSTRUMENT_BOUND"}),
           "effective_markets": eff, "effective_positive_markets": eff_pos,
           "correlation_matrix": corr_out,
           "regime_dependence": {"vol_regime_avg_r": {k: float(np.mean(v)) for k, v in regime.items()},
                                 "positive_in_all_regimes": bool(regime and all(np.mean(v) > 0 for v in regime.values())),
                                 "note": "gemiddelde R per volatiliteitsregime (causaal, LOW/MID/HIGH) over alle cellen"},
           "selection": sel}
    tested = sorted({c["symbol"] for c in ok}) or list(cfg["markets"])
    return {"config": cfg, "results": results, "combined": combined, "equities": equities, "generalisation": gen,
            "ledger_ids": ledger_ids, "provisional_note_nl": instruments.spec_for("XAUUSD")["money_status_note_nl"],
            "money_approval": instruments.money_approval(tested, account=cfg.get("account_currency",
                                                                                 instruments.ACCOUNT_CURRENCY))}


# ------------------------------------------------------------------------------------ Jesse / plugin strategies
ADAPTER_KINDS = ("jesse", "plugin")
_PRICE_SCALE_NOTE_NL = ("vaste prijsafstanden in de bevroren config zijn niet schaal-vrij: dezelfde waarde is op een "
                        "andere markt een heel andere afstand (geen automatische vertaling)")


def _jesse_definitions(name: str, opts: dict) -> list[dict]:
    strategies = opts.get("strategies")
    if strategies is None:
        from .. import server
        strategies = server._strategies()
    return next((s["parameters"] for s in strategies if s["name"] == name), [])


def _equity_points(trades: list[dict], capital: float) -> list[dict]:
    eq, out = float(capital), []
    for t in sorted(trades, key=lambda t: t["exit_time"]):
        eq += float(t["pnl"])
        out.append({"time": t["exit_time"], "equity": eq})
    return out


def _adapter_metrics(trades: list[dict], capital: float, start, end) -> dict:
    m = adapters.normalize_metrics(trades, capital)
    gross = float(sum(t["pnl"] for t in trades) + sum(float(t.get("costs") or 0.0) for t in trades))
    costs = float(sum(float(t.get("costs") or 0.0) for t in trades))
    return {"trades": m["n_trades"], "trades_per_week": m["n_trades"] / factory.weeks_between(start, end),
            "net_profit": m["net_profit"], "net_return_pct": m["net_profit"] / capital * 100.0,
            "profit_factor": _num(m["profit_factor"]), "recovery_factor": _num(m["recovery_factor"]),
            "max_drawdown_pct": m["max_drawdown_pct"], "sharpe_daily": _num(m["sharpe_daily"]),
            "sortino_daily": _num(m.get("sortino")), "win_rate": m["win_rate"], "avg_r": None,
            "costs_total": costs, "gross_profit_before_costs": gross,
            "costs_pct_of_gross": (costs / abs(gross) * 100.0) if gross else None}


def run_cross_market_adapter(ctx, dm, strategy: dict, markets, timeframes, start=None, end=None, *, ledger,
                             reoptimise: bool = False, risk_model: str = "native", capital: float = 10000.0,
                             run_id: str | None = None, live: dict | None = None, adapter_options: dict | None = None,
                             min_days: int | None = None, account_currency: str = instruments.ACCOUNT_CURRENCY,
                             make=None, **_) -> dict:
    """Frozen Jesse strategy / Python plugin on every market x timeframe through ``adapters.make_adapter``.

    ``strategy`` = {kind: jesse|plugin, name|label, params, origin_symbol?}. Instrument-bound strategies are
    reported per market as NOT_SUPPORTED / INSTRUMENT_BOUND (never translated). Every market attempt, run or not,
    is a ledger row. Own sizing of the strategy is kept (risk_normalisation NOT_APPLICABLE); P/L of non-account
    quote currencies is converted at the rate known at each trade's exit time."""
    kind = strategy["kind"]
    run_id = run_id or f"mm_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{hashlib.sha1(str(time.time()).encode()).hexdigest()[:6]}"
    if reoptimise:
        raise ValueError(f"NOT_SUPPORTED: reoptimise voor {kind}: een nieuwe hypothese vereist een eigen versie/run")
    if risk_model != "native":
        raise ValueError(f"NOT_SUPPORTED: risk_model {risk_model!r} voor {kind}; de strategie houdt haar eigen "
                         "positiegrootte (native)")
    opts = dict(adapter_options or {})
    src_dir = opts.pop("jesse_source_dir", None)
    make = make or adapters.make_adapter
    name = str(strategy.get("name") or strategy["label"])
    ref = {"name": name}
    ident = adapters.strategy_identity(kind, ref, strategies=opts.get("strategies"), source_dir=src_dir)
    module, defs = None, None
    requested = [t.upper() for t in timeframes or []]
    if kind == "plugin":
        from .. import plugins
        module = plugins.load(name)
        timeframes = [str(module.PLUGIN.get("base_timeframe", "M1")).upper()]
    else:
        defs = _jesse_definitions(name, opts)
        timeframes = [t.upper() for t in timeframes or ["M15"]]
    binding = adapters.instrument_binding(kind, ref, definitions=defs, source_dir=src_dir, module=module,
                                          origin_symbol=strategy.get("origin_symbol"))
    if markets in (None, "all"):
        markets = list(instruments.SYMBOLS)
    markets = [instruments.canonical(m) for m in markets]
    if start is not None or end is not None:
        protocol.assert_search_range(start or protocol.DEV_START, end or protocol.SEALED_START, protocol.SEALED_START)
    spec = {"kind": kind, "name": name, "code_sha256": ident["sha256"]}
    sha = strategy_sha(spec)
    family = f"multimarket:{kind}:{sha[:12]}"
    frozen = copy.deepcopy(strategy.get("params") or {})
    cfg = {"run_id": run_id, "strategy": {"kind": kind, "label": name, "spec_sha256": sha,
                                         "code_sha256": ident["sha256"],
                                         "origin_symbol": (strategy.get("origin_symbol") or "").upper() or None},
           "markets": markets, "timeframes": timeframes, "timeframes_requested": requested,
           "start": start, "end": end or protocol.SEALED_START, "sealed_start": protocol.SEALED_START,
           "params_frozen": frozen, "reoptimise": False, "risk_model": "native", "risk_normalisation": (
               "NOT_APPLICABLE: de strategie bepaalt zelf haar positiegrootte; vergelijk R/% en kosten, geen lots"),
           "risk_percent": None, "capital": capital, "account_currency": account_currency, "family": family,
           "new_hypothesis": False, "instrument_binding": binding}
    results, equities, ledger_ids, daily = [], {}, [], {}
    if live is not None:
        live.update(config=cfg, results=results, equities=equities)
    total, done = len(markets) * len(timeframes), 0
    for sym in markets:
        info = instruments.spec_for(sym, dm, account=account_currency)
        for tf in timeframes:
            ctx.check()
            ctx.progress(done / max(total, 1), f"{sym} {tf}")
            cell = {"symbol": sym, "timeframe": tf, "key": f"{sym}|{tf}", "kind": kind, "flags": [],
                    "provisional": True}
            results.append(cell)
            reason = adapters.market_block_reason(binding, sym)
            try:
                if reason:
                    cell.update(status="NOT_SUPPORTED", validation_status="INSTRUMENT_BOUND", edge_positive=False,
                                reason=reason, binding=binding["source"])
                    cell["flags"].append("INSTRUMENT_BOUND")
                    ledger_ids.append(_ledger_record(
                        ledger, family, sym, tf, start or protocol.DEV_START, end or protocol.SEALED_START, spec, frozen,
                        {}, "INSTRUMENT_BOUND", run_id, {"not_run": True, "reason": reason}, ledger_status="REJECTED",
                        cost_model={"note": "not run"}, rejection_reason=reason, code_sha256=ident["sha256"]))
                    cell["ledger_id"] = ledger_ids[-1]
                else:
                    _run_adapter_cell(cell, ctx, dm, make, kind, ref, opts, binding, info, sym, tf, start, end, capital,
                                      min_days, account_currency, frozen, ledger, family, spec, run_id, ident, equities,
                                      daily, ledger_ids, strategy.get("origin_symbol"))
            except JobCancelled:
                raise
            except Exception as exc:
                cell.update(status="ERROR", validation_status="INSUFFICIENT_DATA", edge_positive=False,
                            error=f"{type(exc).__name__}: {exc}")
                cell["flags"].append("ERROR")
                if re.search(r"not enough (balance|margin)|insufficient", str(exc), re.I):
                    cell["error_code"] = "FROZEN_SIZE_EXCEEDS_BALANCE"
                    cell["error_note_nl"] = ("de bevroren positiegrootte past niet binnen het startkapitaal op deze "
                                             "markt (hoge prijs); een andere grootte/kapitaal is een andere config")
                if "ledger_id" not in cell:
                    ledger_ids.append(_ledger_record(
                        ledger, family, sym, tf, start or protocol.DEV_START, end or protocol.SEALED_START, spec, frozen,
                        {}, "ERROR", run_id, {"not_run": True, "error": cell["error"]}, ledger_status="ERROR",
                        cost_model={"note": "not run"}, rejection_reason=cell["error"][:500],
                        code_sha256=ident["sha256"]))
                    cell["ledger_id"] = ledger_ids[-1]
            done += 1
    ctx.progress(0.97, "samenvatten")
    out = _summarise(cfg, results, equities, daily, ledger, ledger_ids, capital)
    out["engine_note_nl"] = (
        "Jesse: echte Jesse-backtest op Bid-candles met fee-fractie, vaste positiegrootte, zonder spread/slippage/swap; "
        "kostenstress alleen fee x1,5 (achteraf, lineair). Plugin: eigen engine; spreadstress alleen als de plugin die "
        "verwerkt. Bedragen zijn onderzoeksschattingen (zie money_approval)." if kind == "jesse" else
        "Plugin: eigen engine op de basis-timeframe van de plugin; spreadstress alleen als de plugin die verwerkt. "
        "Bedragen zijn onderzoeksschattingen (zie money_approval).")
    return out


def _run_adapter_cell(cell, ctx, dm, make, kind, ref, opts, binding, info, sym, tf, start, end, capital, min_days,
                      account, frozen, ledger, family, spec, run_id, ident, equities, daily, ledger_ids, origin):
    rng = info["fields"]["data_range"]["value"] if info["data_available"] else None
    if not rng:
        cell.update(status="NO_DATA", validation_status="INSUFFICIENT_DATA", edge_positive=False)
        cell["flags"].append("NO_DATA")
        return
    s, e = resolve_period(start, end, rng["first"], 14)
    s_str, e_str = s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d")
    need = MIN_DAYS if min_days is None else int(min_days)
    cell.update(start=s_str, end=e_str, period_days=(e - s).days)
    if (e - s).days < need:
        cell.update(status="INSUFFICIENT_DATA", validation_status="INSUFFICIENT_DATA", edge_positive=False)
        cell["flags"].append("SHORT_HISTORY")
        return
    if need < MIN_DAYS:
        cell["flags"].append("SHORT_PERIOD")
    t0 = time.time()
    ad = make(kind, ref, sym, tf, s_str, e_str, capital, check=ctx.check, **opts)
    merged = ad.merge(frozen)
    cell["params"] = merged
    cell["engine"] = {"engine_nl": ad.engine_nl, "code_sha256": ad.code_sha256, "dataset_sha256": ad.dataset_sha256}
    res = ad.fn(merged, s_str, e_str)
    trades = res["trades"]
    conv = None
    if info["fields"]["account_conversion"]["value"]["needed"]:
        conv = instruments.conversion_series(dm, sym, "H1" if tf == "M1" else tf, s_str, e_str, account=account)
        if conv is None or not len(conv):
            cell["flags"].append("PROVISIONAL_CURRENCY")
            cell["currency_conversion"] = "UNCONVERTED"
            conv = None
        else:
            cell["currency_conversion"] = "CONVERTED_CAUSAL_AT_EXIT"
            trades = instruments.convert_trades(trades, conv)
    else:
        cell["currency_conversion"] = "ACCOUNT_CURRENCY_QUOTE"
    m = _adapter_metrics(trades, capital, s, e)
    cell["metrics"] = m
    cell["risk_normalisation"] = "NOT_APPLICABLE_OWN_SIZING"
    cell["cost_model"] = {"source": "strategy engine", "costs_total": m["costs_total"],
                          "costs_pct_of_gross": m["costs_pct_of_gross"],
                          "spread": "bid/ask only if the engine models it (plugin); Jesse: none",
                          "swap": "not modelled", "slippage": "not modelled"}
    # cost stress: plugin re-run with spread x1.5 when it processes it; Jesse fees are linear -> exact post-hoc x1.5
    scen = getattr(ad, "supported_cost_scenarios", {}) or {}
    stress_ok, stress = None, {}
    if "spread_+50%" in scen:
        r2 = ad.fn(merged, s_str, e_str, **scen["spread_+50%"])
        t2 = instruments.convert_trades(r2["trades"], conv) if conv is not None else r2["trades"]
        stress_ok, stress = sum(t["pnl"] for t in t2) > 0, {"scenario": "spread_+50% (engine re-run)",
                                                           "net_profit": float(sum(t["pnl"] for t in t2))}
    elif kind == "jesse":
        net = float(sum(t["pnl"] - 0.5 * float(t.get("costs") or 0.0) for t in trades))
        stress_ok, stress = net > 0, {"scenario": "fee_x1.5 (post-hoc, linear in notional)", "net_profit": net}
    else:
        cell["flags"].append("COST_STRESS_UNAVAILABLE")
    cell["cost_stress"] = {**stress, "survives": stress_ok}
    pf = m["profit_factor"] or 0
    cell["edge_positive"] = bool(m["trades"] >= MIN_TRADES and m["net_profit"] > 0 and pf > 1.05 and stress_ok is not False)
    cell["validation_status"] = _validation_status(m, cell["edge_positive"], stress_ok is not False, None)
    cell["validation_note"] = "lichte controle (kostenstress); geen walk-forward/DSR/parameterstabiliteit per markt"
    cell["currency_status"] = cell["currency_conversion"]
    cell["status"] = "OK"
    cell["runtime_s"] = round(time.time() - t0, 1)
    if hasattr(ad, "extra_state"):
        cell["engine"].update(ad.extra_state())
    cell["flags"].append("PROVISIONAL_CONTRACT")
    if m["trades"] < MIN_TRADES:
        cell["flags"].append("FEW_TRADES")
    if stress_ok is False:
        cell["flags"].append("FAILS_COST_STRESS")
    if origin and sym == instruments.canonical(origin):
        cell["flags"].append("ORIGIN_MARKET_IN_SAMPLE")
    warn = binding.get("price_param_warnings") or []
    if warn:
        price = (instruments._stats(dm, sym) or {}).get("price_median")
        rows = [{**w, "pct_of_median_price": (w["value"] / price * 100.0) if price else None} for w in warn]
        cell["price_scale"] = {"params": rows, "median_price": price, "note_nl": _PRICE_SCALE_NOTE_NL}
        cell["flags"].append("PRICE_SCALE_PARAMS")
        if any(r["pct_of_median_price"] is not None and not (
                adapters.PRICE_SCALE_MIN_PCT <= r["pct_of_median_price"] <= adapters.PRICE_SCALE_MAX_PCT)
                for r in rows):
            cell["flags"].append("PRICE_SCALE_EXTREME")
    eq = _daily_equity(_equity_points(trades, capital), capital)
    daily[cell["key"]] = eq
    equities[cell["key"]] = _downsample(eq)
    ledger_ids.append(_ledger_record(
        ledger, family, sym, tf, s, e, spec, merged, {**m, "stress": stress}, cell["validation_status"], run_id,
        {"edge_positive": cell["edge_positive"], "currency_status": cell["currency_status"], "kind": kind},
        cost_model=cell["cost_model"], code_sha256=ident["sha256"], dataset_sha256=ad.dataset_sha256))
    cell["ledger_id"] = ledger_ids[-1]
