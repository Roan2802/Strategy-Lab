"""Controlled, budgeted Strategy Discovery Engine.

Generates a bounded set of rule-spec hypotheses from explicit building blocks (families + mutations), pushes every
hypothesis through the same staged pipeline and records ALL of them (also errors and rejects) in the research ledger.
The score only RANKS hypotheses; it is not a probability of profit. Generated Jesse code is compiled, never executed.

Relation to the Strategy Discovery Factory (``factory.py``, FACTORY_PROTOCOL.md): ``run_discovery`` is the quick,
single-split special case (one in-sample/OOS split, no optimisation, no walk-forward, no sealed final test). Both use
the same Bid/Ask backtest engine, research ledger and trial accounting; ``generate_hypotheses`` doubles as the
factory's fallback generator when ``generator.py`` is not available.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
import random
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import numpy as np
import pandas as pd

from runtime.strategy_lab import rule_engine as engine

from . import backtest, codegen, validation
from .jobs import JobCancelled

SCHEMA = engine.SCHEMA
PIPELINE = "factory_quick_single_split"
STRESS_SPREAD_MULT = 1.5
STRESS_ENTRY_DELAY = 1
SMOKE_FRACTION = 0.10
SMOKE_MIN_BARS = 200
MAX_COMBOS_ENUMERATED = 20_000
WARMUP_DAYS = 30
MIN_IS_DAYS = 365
MIN_OOS_DAYS = 60
MAX_EQUITY_POINTS = 300
TOP_N = 25
VALIDATION_TYPE = "HISTORICAL_OOS"
DATA_REQUIREMENTS = ["Dukascopy M1 Bid/Ask OHLC"]


def _p(name, type_, lo, hi, default, step=None):
    out = {"name": name, "type": type_, "min": lo, "max": hi, "default": default}
    if step is not None:
        out["step"] = step
    return out


def _ind(name, period=None, **kw):
    out = {"indicator": name}
    if period is not None:
        out["period"] = period
    out.update(kw)
    return out


CLOSE = {"price": "close"}


def _cmp(left, op, right):
    return {"left": left, "op": op, "right": right}


def _val(v):
    return {"value": v}


def _spec(name, description, long, short, params, *, stop_mult=2.0, target_r=2.0, order=None, session=None,
          max_trades_per_day=None, cooldown_bars=0, time_exit_bars=None, trailing=None, break_even=None):
    return {"schema": SCHEMA, "name": name, "symbol": "XAUUSD", "timeframe": "M15", "direction": "both",
            "description": description,
            "entry": {"long": long, "short": short},
            "order": order or {"type": "market"},
            "stop": {"type": "atr", "mult": stop_mult, "period": 14},
            "target": {"type": "r_multiple", "value": target_r},
            "trailing": trailing, "break_even": break_even, "time_exit_bars": time_exit_bars,
            "cooldown_bars": cooldown_bars, "max_trades_per_day": max_trades_per_day, "session": session,
            "risk": {"percent": 0.5}, "params": params}


SESSION_NY = {"timezone": "America/New_York", "start": "09:30", "end": "16:00"}
SESSION_LONDON = {"timezone": "Europe/London", "start": "08:00", "end": "16:30"}
SESSION_LONDON_OPEN = {"timezone": "Europe/London", "start": "08:00", "end": "11:00"}
SESSION_NY_MORNING = {"timezone": "America/New_York", "start": "09:30", "end": "12:00"}
SESSION_NY_AFTERNOON = {"timezone": "America/New_York", "start": "13:00", "end": "16:00"}
SESSION_ASIA = {"timezone": "UTC", "start": "00:00", "end": "07:00"}
TRAIL = {"activate_r": 1.5, "atr_mult": 2.0, "period": 14}

_TREND = _spec(
    "Trend Following", "EMA-kruising met ADX-trendfilter.",
    {"all": [_cmp(_ind("ema", {"param": "ema_fast"}), "crosses_above", _ind("ema", {"param": "ema_slow"})),
             _cmp(_ind("adx", 14), ">", _val({"param": "adx_min"}))]},
    {"all": [_cmp(_ind("ema", {"param": "ema_fast"}), "crosses_below", _ind("ema", {"param": "ema_slow"})),
             _cmp(_ind("adx", 14), ">", _val({"param": "adx_min"}))]},
    [_p("ema_fast", "int", 5, 50, 20, 1), _p("ema_slow", "int", 20, 200, 50, 5), _p("adx_min", "int", 10, 40, 20, 1)],
    stop_mult=2.0, target_r=3.0)

_VOL_EXPANSION = _spec(
    "Volatility Expansion", "Stijgende volatiliteit (ATR) samen met een Donchian-doorbraak.",
    {"all": [_cmp(_ind("atr", 14), ">", _ind("atr", 14, shift={"param": "expansion_shift"})),
             _cmp(CLOSE, ">", _ind("donchian_high", {"param": "channel"}, shift=1))]},
    {"all": [_cmp(_ind("atr", 14), ">", _ind("atr", 14, shift={"param": "expansion_shift"})),
             _cmp(CLOSE, "<", _ind("donchian_low", {"param": "channel"}, shift=1))]},
    [_p("channel", "int", 10, 100, 20, 5), _p("expansion_shift", "int", 3, 20, 5, 1)],
    stop_mult=2.0, target_r=2.5)

_VWAP_PULLBACK = _spec(
    "Vwap Pullback", "Koers herneemt de VWAP in de richting van de EMA-trend.",
    {"all": [_cmp(CLOSE, ">", _ind("ema", {"param": "trend_ema"})), _cmp(CLOSE, "crosses_above", _ind("vwap"))]},
    {"all": [_cmp(CLOSE, "<", _ind("ema", {"param": "trend_ema"})), _cmp(CLOSE, "crosses_below", _ind("vwap"))]},
    [_p("trend_ema", "int", 20, 200, 50, 5)], stop_mult=1.5, target_r=2.0)

_SWEEP = _spec(
    "Liquidity Sweep Proxy",
    "PROXY op OHLC: prikt onder/boven de extremen van de laatste N candles en sluit terug erbinnen (geen orderflow).",
    {"pattern": "liquidity_sweep_low", "lookback": {"param": "lookback"}},
    {"pattern": "liquidity_sweep_high", "lookback": {"param": "lookback"}},
    [_p("lookback", "int", 5, 100, 20, 1)], stop_mult=1.5, target_r=2.0)

_REVERSAL = _spec(
    "Price Action Reversal", "Engulfing candle die de vorige daghoogte/-laagte aanraakt.",
    {"all": [{"pattern": "bullish_engulfing"}, _cmp({"price": "low"}, "<=", {"level": "prev_day_low"})]},
    {"all": [{"pattern": "bearish_engulfing"}, _cmp({"price": "high"}, ">=", {"level": "prev_day_high"})]},
    [_p("min_hold", "int", 1, 10, 1, 1)], stop_mult=1.5, target_r=2.0, cooldown_bars=1)

_TIME_OF_DAY = _spec(
    "Time Of Day", "Eenvoudige EMA/RSI-momentumregel, alleen tijdens een gekozen handelssessie.",
    {"all": [_cmp(CLOSE, "crosses_above", _ind("ema", {"param": "ema_n"})), _cmp(_ind("rsi", 14), ">", _val(50))]},
    {"all": [_cmp(CLOSE, "crosses_below", _ind("ema", {"param": "ema_n"})), _cmp(_ind("rsi", 14), "<", _val(50))]},
    [_p("ema_n", "int", 10, 100, 30, 1)], stop_mult=1.5, target_r=2.0, session=SESSION_LONDON_OPEN,
    max_trades_per_day=2)

_REGIME_MOMENTUM = _spec(
    "Regime Filtered Momentum", "ROC-momentum dat door nul kruist, alleen bij voldoende ADX-trendsterkte.",
    {"all": [_cmp(_ind("roc", {"param": "roc_n"}), "crosses_above", _val(0)),
             _cmp(_ind("adx", 14), ">", _val({"param": "adx_min"}))]},
    {"all": [_cmp(_ind("roc", {"param": "roc_n"}), "crosses_below", _val(0)),
             _cmp(_ind("adx", 14), ">", _val({"param": "adx_min"}))]},
    [_p("roc_n", "int", 5, 100, 20, 1), _p("adx_min", "int", 10, 40, 20, 1)], stop_mult=2.0, target_r=2.5)


# ------------------------------------------------------------------ building blocks (named mutations)
_HTF = {"M1": "H1", "M5": "H1", "M15": "H4", "M30": "H4", "H1": "H4", "H4": "D1", "D1": None}


def _block_htf_filter(spec: dict, on) -> None:
    """Add a higher-timeframe EMA(50) trend filter to every entry side (no-op when off or on D1)."""
    htf = _HTF.get(spec["timeframe"])
    if not on or htf is None:
        return
    for side, cond in list(spec["entry"].items()):
        op = ">" if side == "long" else "<"
        spec["entry"][side] = {"all": [cond, _cmp(CLOSE, op, _ind("ema", 50, timeframe=htf))]}


def _block_rsi_confirm(spec: dict, on) -> None:
    if not on:
        return
    for side, cond in list(spec["entry"].items()):
        extra = _cmp(_ind("rsi", 14), "<" if side == "long" else ">", _val(35 if side == "long" else 65))
        if "all" in cond:
            cond["all"].append(extra)
        else:
            spec["entry"][side] = {"all": [cond, extra]}


def _block_nr_pattern(spec: dict, value) -> None:
    def walk(node):
        if isinstance(node, dict):
            if node.get("pattern") in ("nr4", "nr7"):
                node["pattern"] = value
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(spec["entry"])


def _block_vol_measure(spec: dict, value) -> None:
    """Swap the first entry condition (volatility rising) between ATR and realized volatility."""
    if value == "atr":
        cond = _cmp(_ind("atr", 14), ">", _ind("atr", 14, shift={"param": "expansion_shift"}))
    elif value == "realized_vol":
        cond = _cmp(_ind("realized_vol", 20), ">", _ind("realized_vol", 20, shift={"param": "expansion_shift"}))
    else:
        raise ValueError(f"onbekende vol_measure {value!r}")
    for side in spec["entry"]:
        spec["entry"][side]["all"][0] = copy.deepcopy(cond)


BLOCKS = {"htf_filter": _block_htf_filter, "rsi_confirm": _block_rsi_confirm, "nr_pattern": _block_nr_pattern,
          "vol_measure": _block_vol_measure}

_COMMON = {"stop.mult": [1.5, 2.0, 3.0], "target.value": [1.5, 2.0, 3.0]}

FAMILIES: list[dict] = [
    {"id": "trend_following", "name": "Trendvolgend (EMA-kruising + ADX)", "default": True,
     "description": "Koopt/verkoopt wanneer een snelle EMA een trage EMA kruist en de ADX voldoende trend aangeeft.",
     "base_template": _TREND,
     "mutations": {"ema_fast": [10, 20, 30], "ema_slow": [50, 100, 200], "adx_min": [15, 25],
                   "stop.mult": [1.5, 2.5], "target.value": [2.0, 3.0], "htf_filter": [False, True]}},
    {"id": "breakout", "name": "Breakout (Donchian)", "default": True,
     "description": "Slotkoers buiten het Donchian-kanaal van de voorgaande candles.",
     "base_template": "donchian_breakout",
     "mutations": {"channel": [20, 40, 55], "stop.mult": [1.5, 2.0, 3.0], "target.value": [2.0, 3.0, 4.0],
                   "trailing": [None, TRAIL], "htf_filter": [False, True]}},
    {"id": "volatility_expansion", "name": "Volatiliteitsexpansie (ATR/realized vol + breakout)", "default": True,
     "description": "Doorbraak van een kanaal terwijl de volatiliteit (ATR of realized vol) oploopt.",
     "base_template": _VOL_EXPANSION,
     "mutations": {"channel": [20, 40, 55], "expansion_shift": [3, 5, 10], "vol_measure": ["atr", "realized_vol"],
                   "stop.mult": [1.5, 2.5], "target.value": [2.0, 3.0]}},
    {"id": "volatility_contraction", "name": "Volatiliteitscontractie (NR4/NR7/inside bar)", "default": True,
     "description": "Smalle-range candle plus inside bar in de EMA-trendrichting; stop-order voorbij de candle.",
     "base_template": "nr4_inside_bar_breakout",
     "mutations": {"trend_ema": [50, 100, 200], "nr_pattern": ["nr4", "nr7"], "order.expiry_bars": [2, 3, 5],
                   "order.offset_atr": [0.0, 0.1, 0.25], "stop.mult": [1.5, 2.0], "target.value": [2.0, 3.0]}},
    {"id": "mean_reversion", "name": "Mean reversion (Bollinger/RSI)", "default": True,
     "description": "Terugkeer binnen de Bollinger-band in een rustige markt (lage ADX), optioneel met RSI-bevestiging.",
     "base_template": "bollinger_mean_reversion",
     "mutations": {"bb_period": [14, 20, 30], "bb_mult": [2.0, 2.5], "adx_max": [20, 25, 30],
                   "rsi_confirm": [False, True], "time_exit_bars": [12, 24, 48], "target.value": [1.0, 1.5, 2.0]}},
    {"id": "vwap_pullback", "name": "VWAP-pullback", "default": True,
     "description": "Koers herneemt de VWAP in de richting van de EMA-trend.",
     "base_template": _VWAP_PULLBACK,
     "mutations": {"trend_ema": [50, 100, 200], "stop.mult": [1.5, 2.0], "target.value": [1.5, 2.0, 3.0],
                   "cooldown_bars": [0, 3], "session": [None, SESSION_LONDON, SESSION_NY]}},
    {"id": "session_breakout", "name": "Sessie-breakout (opening range NY/Londen)", "default": True,
     "description": "Breakout van de eerste minuten na de sessie-opening (New York of Londen), max. een/twee trades per dag.",
     "base_template": "opening_range_breakout",
     "mutations": {"or_minutes": [15, 30, 60], "session": [SESSION_NY, SESSION_LONDON],
                   "max_trades_per_day": [1, 2], "stop.mult": [1.5, 2.0], "target.value": [1.5, 2.0, 3.0]}},
    {"id": "liquidity_sweep_proxy", "name": "Liquidity sweep (PROXY, OHLC)", "default": True,
     "description": "PROXY op OHLC-data: prikt door recente extremen en sluit terug. Er is GEEN orderflow- of "
                    "orderboekinformatie; echte liquiditeit wordt niet gemeten.",
     "base_template": _SWEEP,
     "mutations": {"lookback": [10, 20, 40], "stop.mult": [1.0, 1.5, 2.0], "target.value": [1.5, 2.0, 3.0],
                   "htf_filter": [False, True]}},
    {"id": "price_action_reversal", "name": "Price-action reversal (engulfing bij vorige daghoogte/-laagte)",
     "default": True,
     "description": "Bullish/bearish engulfing candle die de vorige daglaagte/-hoogte raakt.",
     "base_template": _REVERSAL,
     "mutations": {"stop.mult": [1.5, 2.0, 3.0], "target.value": [1.5, 2.0, 3.0], "time_exit_bars": [None, 12, 24],
                   "session": [None, SESSION_LONDON, SESSION_NY], "trailing": [None, TRAIL]}},
    {"id": "time_of_day", "name": "Tijd-van-de-dag (sessiefilter-varianten)", "default": True,
     "description": "Dezelfde eenvoudige momentumregel in verschillende handelssessies; toont of tijd alleen al verschil maakt.",
     "base_template": _TIME_OF_DAY,
     "mutations": {"session": [SESSION_LONDON_OPEN, SESSION_NY_MORNING, SESSION_NY_AFTERNOON, SESSION_ASIA],
                   "ema_n": [20, 30, 50], "max_trades_per_day": [1, 2], "target.value": [1.5, 2.0, 3.0]}},
    {"id": "regime_filtered_momentum", "name": "Regime-gefilterd momentum (ROC + ADX/HTF-EMA)", "default": True,
     "description": "ROC-momentum met ADX-filter en optioneel een hogere-timeframe EMA-trendfilter.",
     "base_template": _REGIME_MOMENTUM,
     "mutations": {"roc_n": [10, 20, 40], "adx_min": [15, 20, 25], "htf_filter": [False, True],
                   "stop.mult": [1.5, 2.5], "target.value": [2.0, 3.0]}},
]

DEFAULT_WEIGHTS = {
    "risk_adjusted_return": 0.15, "drawdown": 0.10, "recovery": 0.08, "profit_factor": 0.10, "sample_size": 0.08,
    "yearly_stability": 0.08, "oos_performance": 0.15, "cost_sensitivity": 0.08, "parameter_stability": 0.08,
    "regime_robustness": 0.05, "dependency": 0.05,
}

DEFAULT_CRITERIA = {"min_trades": 30, "min_profit_factor": 1.1, "max_drawdown_pct": 30.0, "min_bars": 300,
                    "max_missing_ask_fraction": 0.05}
DEFAULT_BUDGET = {"max_candidates": 50, "max_minutes": 30.0, "max_workers": 3, "max_ram_mb": None}


def family_by_id(family_id: str) -> dict:
    for f in FAMILIES:
        if f["id"] == family_id:
            return f
    raise ValueError(f"Onbekende familie: {family_id!r}")


def default_family_ids() -> list[str]:
    return [f["id"] for f in FAMILIES if f.get("default")]


def _resolve_families(families) -> list[dict]:
    if not families:
        return [family_by_id(i) for i in default_family_ids()]
    return [f if isinstance(f, dict) else family_by_id(f) for f in families]


def base_spec(family: dict) -> dict:
    base = family["base_template"]
    if isinstance(base, str):
        return copy.deepcopy(codegen.TEMPLATES[base])
    return copy.deepcopy(base)


def parent_name(family: dict) -> str:
    base = family["base_template"]
    return base if isinstance(base, str) else f"inline:{family['id']}"


# ------------------------------------------------------------------ mutation application
def _set_param(spec: dict, name: str, value) -> bool:
    for p in spec.get("params") or []:
        if p["name"] == name:
            v = int(value) if p["type"] == "int" else float(value)
            p["min"], p["max"] = min(p["min"], v), max(p["max"], v)
            p["default"] = v
            return True
    return False


def _set_path(spec: dict, path: str, value) -> None:
    parts = path.split(".")
    node = spec
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    if isinstance(node, list):
        node[int(parts[-1])] = copy.deepcopy(value)
    else:
        if parts[-1] not in node and len(parts) > 1 and parts[-1] not in ("expiry_bars", "offset_atr"):
            raise KeyError(f"mutatiepad {path!r} bestaat niet in de basis-spec")
        node[parts[-1]] = copy.deepcopy(value)


def apply_mutations(spec: dict, mutations: dict) -> dict:
    """Return a mutated deep copy of `spec`. Keys: named block, parameter name, or dotted spec path."""
    out = copy.deepcopy(spec)
    for key, value in mutations.items():
        if key in BLOCKS:
            BLOCKS[key](out, value)
        elif _set_param(out, key, value):
            continue
        else:
            _set_path(out, key, value)
    return out


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def hypothesis_id(spec: dict, symbol: str, timeframe: str) -> str:
    """sha256 of canonical spec (name/description excluded) + symbol + timeframe, first 16 hex."""
    body = {k: v for k, v in spec.items() if k not in ("name", "description")}
    return hashlib.sha256(_canon({"spec": body, "symbol": symbol, "timeframe": timeframe}).encode()).hexdigest()[:16]


def _combos(family: dict, seed: int) -> list[dict]:
    """Baseline first, then mutation combinations in a seeded shuffled order."""
    mut = family.get("mutations") or {}
    keys = sorted(mut)
    total = math.prod(len(mut[k]) for k in keys) if keys else 1
    rng = random.Random(f"{seed}:{family['id']}")
    if total <= MAX_COMBOS_ENUMERATED:
        rows = [dict(zip(keys, c)) for c in itertools.product(*(mut[k] for k in keys))]
        rng.shuffle(rows)
    else:
        seen, rows = set(), []
        while len(rows) < MAX_COMBOS_ENUMERATED:
            idx = tuple(rng.randrange(len(mut[k])) for k in keys)
            if idx not in seen:
                seen.add(idx)
                rows.append({k: mut[k][i] for k, i in zip(keys, idx)})
    return [{}] + rows


def generate_hypotheses(families=None, symbols=("XAUUSD",), timeframes=("M15",), max_candidates: int = 50,
                        seed: int = 0) -> list[dict]:
    """Deterministic, unique, capped hypothesis list; round-robin over families x symbols x timeframes."""
    fams = _resolve_families(families)
    # markets (symbol x timeframe) rotate the starting family so a small budget still touches every symbol
    markets = [(s, t) for t in timeframes for s in symbols]
    slots = [(fams[(k + mi) % len(fams)], s, t) for k in range(len(fams)) for mi, (s, t) in enumerate(markets)]
    combos = {f["id"]: _combos(f, seed) for f in fams}
    pointer = {(f["id"], s, t): 0 for f, s, t in slots}
    seen: set[str] = set()
    out: list[dict] = []
    cap = max(0, int(max_candidates))
    while len(out) < cap:
        progressed = False
        for fam, sym, tf in slots:
            if len(out) >= cap:
                break
            key = (fam["id"], sym, tf)
            rows = combos[fam["id"]]
            while pointer[key] < len(rows):
                mutations = rows[pointer[key]]
                pointer[key] += 1
                spec = base_spec(fam)
                spec["symbol"], spec["timeframe"] = sym, tf
                spec = apply_mutations(spec, mutations)
                hid = hypothesis_id(spec, sym, tf)
                if hid in seen:
                    continue
                seen.add(hid)
                spec["name"] = f"Disc {fam['id']} {hid[:8]}"
                spec["description"] = f"{fam['name']}: {fam['description']}"[:400]
                out.append({"hypothesis_id": hid, "family": fam["id"], "parent": parent_name(fam),
                            "mutations": copy.deepcopy(mutations), "spec": spec, "symbol": sym, "timeframe": tf})
                progressed = True
                break
        if not progressed:
            break
    return out


# ------------------------------------------------------------------ scoring
def _clip01(x) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if not math.isfinite(x) else min(1.0, max(0.0, x))


def score(components: dict, weights: dict | None = None) -> float:
    """Weighted mean of components, each clipped to 0..1 (missing/None = 0). Weights are non-negative and
    normalized. The result RANKS hypotheses against each other; it is NOT a probability of profit or success."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    total = sum(max(0.0, float(v)) for v in w.values())
    if total <= 0:
        return 0.0
    return sum(max(0.0, float(wt)) * _clip01(components.get(name)) for name, wt in w.items()) / total


# ------------------------------------------------------------------ metrics helpers
def summarize(trades: list, capital: float) -> dict:
    tm = validation.trade_metrics(trades, capital)
    r = [t.get("r_multiple", 0.0) for t in trades]
    return {"trades": tm["n_trades"], "net_profit": tm["net_profit"], "profit_factor": tm["profit_factor"],
            "win_rate": tm["win_rate"], "max_drawdown_pct": tm["max_drawdown_pct"],
            "recovery_factor": tm["recovery_factor"], "sharpe_daily": tm["sharpe"], "sortino_daily": tm["sortino"],
            "expectancy": tm["net_profit"] / tm["n_trades"] if tm["n_trades"] else 0.0,
            "avg_r": float(np.mean(r)) if r else 0.0}


def _yearly_component(trades: list) -> float:
    """Share of profitable periods: calendar years if data spans >= 2 years, else quarters (>= 180 days), else months."""
    if not trades:
        return 0.0
    df = pd.DataFrame({"t": pd.to_datetime([t["exit_time"] for t in trades], utc=True),
                       "p": [t["pnl"] for t in trades]})
    span = (df["t"].max() - df["t"].min()).days
    local = df["t"].dt.tz_localize(None)
    key = local.dt.year if span >= 730 else (local.dt.to_period("Q") if span >= 180 else local.dt.to_period("M"))
    per = df.groupby(key.values)["p"].sum()
    return float((per > 0).mean()) if len(per) >= 2 else 0.0


def _regime_component(trades: list, fr: "_Frames") -> float:
    """Share of the two realized-vol halves (in-sample median split) in which the net P&L is positive."""
    if not trades or fr.high_vol is None:
        return 0.0
    ts = pd.DatetimeIndex(pd.to_datetime([t["entry_time"] for t in trades]))
    if fr.bid_is.index.tz is not None and ts.tz is None:
        ts = ts.tz_localize(fr.bid_is.index.tz)
    pos = np.clip(fr.bid_is.index.searchsorted(ts), 0, len(fr.high_vol) - 1)
    hv = fr.high_vol[pos]
    pnl = np.array([t["pnl"] for t in trades])
    halves = [pnl[hv].sum() > 0 if hv.any() else False, pnl[~hv].sum() > 0 if (~hv).any() else False]
    return float(np.mean(halves))


def _dependency_component(daily: pd.Series, accepted: list) -> float:
    worst = 0.0
    for other in accepted:
        both = pd.concat([daily, other], axis=1).fillna(0.0)
        if len(both) >= 5 and both.iloc[:, 0].std() > 0 and both.iloc[:, 1].std() > 0:
            worst = max(worst, float(both.corr().iloc[0, 1]))
    return 1.0 - min(1.0, max(0.0, worst))


# ------------------------------------------------------------------ data
class _Frames:
    """Per (symbol, timeframe) data of one run: the full frame is loaded once and sliced (views) per stage."""

    def __init__(self, bid, ask, manifest, oos_fraction=None, windows=None, symbol=""):
        self.manifest = manifest or {}
        self.insufficient: str | None = None
        self.symbol = symbol
        self.bid_all, self.ask_all = bid, ask
        if windows is None:
            self._legacy_split(bid, ask, oos_fraction if oos_fraction is not None else 0.3)
        else:
            self._windowed_split(bid, ask, windows)
        self.n = len(bid)
        self.n_is = len(self.bid_is)
        ask_close = self.ask_is["close"].reindex(self.bid_is.index) if self.n_is else None
        self.missing_ask_fraction = float(ask_close.isna().mean()) if self.n_is else 1.0
        self.high_vol = None
        if self.n_is:
            close = self.bid_is["close"].astype(float)
            rv = np.log(close).diff().rolling(50).std().to_numpy()
            valid = np.isfinite(rv)
            if valid.sum() > 10:
                med = np.median(rv[valid])
                self.high_vol = np.where(valid, rv >= med, False)

    @staticmethod
    def _cut(frame, lo, hi):
        i0 = 0 if lo is None else int(frame.index.searchsorted(lo, side="left"))
        i1 = len(frame) if hi is None else int(frame.index.searchsorted(hi, side="left"))
        return frame.iloc[i0:i1]

    def _legacy_split(self, bid, ask, oos_fraction):
        n = len(bid)
        n_is = max(1, min(n - 1, int(n * (1.0 - oos_fraction)))) if n > 1 else n
        split_ts = bid.index[n_is] if n_is < n else None
        self.split_ts = split_ts
        self.bid_is, self.bid_oos = bid.iloc[:n_is], bid.iloc[n_is:]
        self.ask_is = self._cut(ask, None, split_ts)
        self.ask_oos = self._cut(ask, split_ts, None) if split_ts is not None else ask.iloc[0:0]
        warm_from = split_ts - pd.Timedelta(days=WARMUP_DAYS) if split_ts is not None else None
        self.bid_oos_w = self._cut(bid, warm_from, None) if split_ts is not None else bid.iloc[0:0]
        self.ask_oos_w = self._cut(ask, warm_from, None) if split_ts is not None else ask.iloc[0:0]
        self.periods = {"in_sample": {"start": _iso(self.bid_is.index[0]) if n_is else None,
                                      "end": _iso(self.bid_is.index[-1]) if n_is else None},
                        "out_of_sample": {"start": _iso(split_ts), "end": _iso(bid.index[-1]) if n else None},
                        "sufficient": n > 0, "reason": None}
        if n == 0:
            self.insufficient = f"onvoldoende data voor {self.symbol}: geen candles beschikbaar"
            self.periods["reason"] = self.insufficient

    def _windowed_split(self, bid, ask, w):
        if len(bid) == 0:
            self.split_ts = w["oos_start"]
            self.bid_is = self.bid_oos = self.bid_oos_w = bid
            self.ask_is = self.ask_oos = self.ask_oos_w = ask.iloc[0:0]
            self.insufficient = f"onvoldoende data voor {self.symbol}: geen candles beschikbaar"
            self.periods = {"in_sample": {"start": None, "end": None}, "out_of_sample": {"start": None, "end": None},
                            "sufficient": False, "reason": self.insufficient}
            return
        first, last = bid.index[0], bid.index[-1]
        is_start = max(w["is_start"], first + pd.Timedelta(days=WARMUP_DAYS))
        is_end = min(w["is_end"], last + pd.Timedelta(nanoseconds=1))
        oos_start = w["oos_start"]
        oos_end = min(w["oos_end"], last + pd.Timedelta(nanoseconds=1))
        is_days = max((is_end - is_start).days, 0)
        oos_days = max((oos_end - oos_start).days, 0)
        self.split_ts = oos_start
        self.bid_is, self.ask_is = self._cut(bid, is_start, is_end), self._cut(ask, is_start, is_end)
        self.bid_oos, self.ask_oos = self._cut(bid, oos_start, oos_end), self._cut(ask, oos_start, oos_end)
        warm_from = oos_start - pd.Timedelta(days=WARMUP_DAYS)
        self.bid_oos_w, self.ask_oos_w = self._cut(bid, warm_from, oos_end), self._cut(ask, warm_from, oos_end)
        problems = []
        if is_days < MIN_IS_DAYS:
            problems.append(f"in-sample {is_days} dagen < {MIN_IS_DAYS}")
        if oos_days < MIN_OOS_DAYS:
            problems.append(f"OOS {oos_days} dagen < {MIN_OOS_DAYS}")
        if problems:
            self.insufficient = f"onvoldoende data voor {self.symbol} ({'; '.join(problems)})"
        self.periods = {"in_sample": {"start": _iso(is_start), "end": _iso(is_end)},
                        "out_of_sample": {"start": _iso(oos_start), "end": _iso(oos_end)},
                        "sufficient": not problems, "reason": self.insufficient}


def _iso(ts) -> str | None:
    return None if ts is None else pd.Timestamp(ts).isoformat()


def _downsample(equity, limit: int = MAX_EQUITY_POINTS) -> list:
    if not equity:
        return []
    if len(equity) <= limit:
        return [{"time": p["time"], "equity": p["equity"]} for p in equity]
    idx = np.unique(np.linspace(0, len(equity) - 1, limit).round().astype(int))
    return [{"time": equity[i]["time"], "equity": equity[i]["equity"]} for i in idx]


def _dataset_sha256(manifest: dict, timeframe: str, start, end) -> str:
    return hashlib.sha256(_canon({"manifest": manifest, "timeframe": timeframe, "start": start,
                                  "end": end}).encode()).hexdigest()


# ------------------------------------------------------------------ per-hypothesis pipeline
class _Stages:
    def __init__(self, ctx):
        self.rows, self.ctx = [], ctx

    def run(self, number, name, fn):
        self.ctx.check()
        t0 = time.monotonic()
        try:
            out = fn()
        except JobCancelled:
            raise
        except Exception as exc:
            self.rows.append({"stage": number, "name": name, "status": "ERROR",
                              "detail": f"{type(exc).__name__}: {exc}", "seconds": round(time.monotonic() - t0, 3)})
            raise _StageError(number, name, exc) from exc
        status, detail = out if isinstance(out, tuple) else ("OK", out)
        self.rows.append({"stage": number, "name": name, "status": status, "detail": detail,
                          "seconds": round(time.monotonic() - t0, 3)})
        return status, detail

    def skip(self, number, name, why):
        self.rows.append({"stage": number, "name": name, "status": "SKIP", "detail": why, "seconds": 0.0})


class _StageError(Exception):
    def __init__(self, number, name, exc):
        super().__init__(f"stage {number} ({name}): {type(exc).__name__}: {exc}")
        self.number, self.stage_name, self.original = number, name, exc


def _neighbour_params(spec: dict) -> tuple[str | None, list]:
    for p in spec.get("params") or []:
        base = p["default"]
        vals = []
        for f in (0.8, 1.2):
            v = base * f
            v = int(round(v)) if p["type"] == "int" else float(v)
            v = min(p["max"], max(p["min"], v))
            if v == base and p["type"] == "int":
                v = min(p["max"], max(p["min"], base + (1 if f > 1 else -1)))
            if v != base and v not in vals:
                vals.append(v)
        if vals:
            return p["name"], vals
    return None, []


def _rejection_reasons(m: dict, crit: dict) -> str | None:
    if m["trades"] < crit["min_trades"]:
        return f"te weinig trades ({m['trades']} < {crit['min_trades']})"
    if m["net_profit"] <= 0:
        return f"in-sample verliesgevend (netto {m['net_profit']:.2f})"
    if m["profit_factor"] < crit["min_profit_factor"]:
        return f"profit factor te laag ({m['profit_factor']:.2f} < {crit['min_profit_factor']:.2f})"
    if m["max_drawdown_pct"] > crit["max_drawdown_pct"]:
        return f"drawdown te hoog ({m['max_drawdown_pct']:.1f}% > {crit['max_drawdown_pct']:.1f}%)"
    return None


class _Run:
    """Shared state of one discovery run."""

    def __init__(self, ctx, cfg, ledger, n_trials, sr_var):
        self.ctx, self.cfg, self.ledger = ctx, cfg, ledger
        self.n_trials, self.sr_var = n_trials, sr_var
        self.lock = threading.Lock()
        self.accepted_daily: list = []
        self.records: list[dict] = []


def _evaluate(h: dict, fr: _Frames, run: _Run) -> dict:
    """Run all stages for one hypothesis; returns the ledger fields. Never raises except JobCancelled."""
    cfg, ctx = run.cfg, run.ctx
    crit, capital, comm = cfg["criteria"], cfg["capital"], cfg["commission_per_lot"]
    spec = h["spec"]
    st = _Stages(ctx)
    rec = {"kind": "DISCOVERY", "hypothesis_id": h["hypothesis_id"], "parent_id": None, "family": h["family"],
           "symbol": h["symbol"], "timeframe": h["timeframe"], "start": cfg["start"], "end": cfg["end"],
           "spec": spec, "spec_sha256": engine.spec_sha256(spec), "params": None, "mutations": h["mutations"],
           "code_sha256": None, "dataset_sha256": _dataset_sha256(fr.manifest, h["timeframe"], cfg["start"], cfg["end"]),
           "cost_model": {"capital": capital, "commission_per_lot": comm, "slippage_points": 0.0,
                          "fills": "Bid/Ask: long koopt Ask, verkoopt Bid",
                          "stress": {"spread_multiplier": STRESS_SPREAD_MULT, "entry_delay_bars": STRESS_ENTRY_DELAY},
                          "oos_fraction": cfg["oos_fraction"], "validation": VALIDATION_TYPE},
           "data_requirements": DATA_REQUIREMENTS, "status": "REJECTED", "rejection_reason": None,
           "metrics": None, "oos_metrics": None, "score": None}
    stats = {"n_trials_in_run": run.n_trials, "psr": None, "dsr": None, "stages": st.rows, "components": None,
             "periods": fr.periods, "validation": VALIDATION_TYPE}
    rec["statistics"] = stats
    state: dict = {}

    def bt(bid, ask, **kw):
        return backtest.run_rules_backtest(spec, bid, ask, None, capital=capital, commission_per_lot=comm, **kw)

    def reject(reason):
        rec["status"], rec["rejection_reason"] = "REJECTED", reason

    try:
        # 1 spec validation
        def s1():
            errs = engine.validate_spec(spec)
            if errs:
                raise ValueError("ongeldige spec: " + "; ".join(errs[:3]))
            return "spec geldig"
        st.run(1, "spec_validatie", s1)

        # 2 code generation + compile only (NEVER executed)
        def s2():
            code = codegen.generate_jesse_strategy(spec)
            compile(code, f"<{h['hypothesis_id']}>", "exec")
            rec["code_sha256"] = hashlib.sha256(code.encode()).hexdigest()
            return f"{len(code)} tekens gecompileerd, niet uitgevoerd"
        st.run(2, "codegeneratie_compile", s2)

        # 3 smoke backtest on first 10% of in-sample
        def s3():
            k = min(fr.n_is, max(int(fr.n_is * SMOKE_FRACTION), SMOKE_MIN_BARS))
            r = bt(fr.bid_is.iloc[:k], fr.ask_is[fr.ask_is.index <= fr.bid_is.index[k - 1]])
            return f"{len(r['trades'])} trades op {k} bars"
        st.run(3, "smoke_backtest", s3)

        # 4 data sufficiency
        def s4():
            if fr.n_is < crit["min_bars"]:
                return "FAIL", f"onvoldoende data: {fr.n_is} bars in-sample < {crit['min_bars']}"
            if fr.missing_ask_fraction > crit["max_missing_ask_fraction"]:
                return "FAIL", (f"onvoldoende data: {fr.missing_ask_fraction:.1%} ontbrekende Ask-candles "
                                f"> {crit['max_missing_ask_fraction']:.1%}")
            return f"{fr.n_is} bars, {fr.missing_ask_fraction:.2%} ontbrekende Ask"
        status, detail = st.run(4, "datasufficientie", s4)
        if status == "FAIL":
            reject(detail)
            st.skip(5, "insample_backtest", "overgeslagen")
        else:
            # 5 full in-sample
            def s5():
                r = bt(fr.bid_is, fr.ask_is)
                state["is"] = r
                rec["equity_is"] = _downsample(r.get("equity"))
                rec["params"] = r["params"]
                rec["metrics"] = summarize(r["trades"], capital)
                m = rec["metrics"]
                return f"{m['trades']} trades, netto {m['net_profit']:.2f}"
            st.run(5, "insample_backtest", s5)
            m = rec["metrics"]
            reason = _rejection_reasons(m, crit)
            if reason:
                reject(reason)
                st.skip(6, "kostenstress", "overgeslagen: in-sample afgewezen")
            else:
                # 6 cost stress
                def s6():
                    r = bt(fr.bid_is, fr.ask_is, spread_multiplier=STRESS_SPREAD_MULT,
                           entry_delay_bars=STRESS_ENTRY_DELAY)
                    state["stress"] = summarize(r["trades"], capital)
                    net = state["stress"]["net_profit"]
                    return ("FAIL" if net <= 0 else "OK"), f"netto {net:.2f} bij spread x{STRESS_SPREAD_MULT} en entry +1 bar"
                status, _ = st.run(6, "kostenstress", s6)
                if status == "FAIL":
                    reject("kostengevoelig: verlies bij spread +50% en entry 1 bar later")
                    st.skip(7, "oos_backtest", "overgeslagen: kostenstress gefaald")

        # 7 OOS (only for in-sample + stress survivors: the holdout is touched once)
        if rec["status"] == "REJECTED" and rec["rejection_reason"] is None and "stress" in state:
            def s7():
                # frozen IS spec/params; the warm-up prefix only primes indicators (trade_from blocks earlier trades)
                r = bt(fr.bid_oos_w, fr.ask_oos_w, trade_from=fr.split_ts)
                cut = fr.split_ts
                trades = [t for t in r["trades"] if _ts_ge(t["entry_time"], cut)]
                rec["oos_metrics"] = summarize(trades, capital)
                rec["equity_oos"] = _downsample(r.get("equity"))
                state["oos_trades"] = trades
                o = rec["oos_metrics"]
                return ("FAIL" if o["net_profit"] <= 0 else "OK"), f"{o['trades']} trades, netto {o['net_profit']:.2f}"
            if fr.split_ts is None or len(fr.bid_oos) == 0:
                reject("geen OOS-data beschikbaar")
                st.skip(7, "oos_backtest", "geen OOS-data")
            else:
                status, _ = st.run(7, "oos_backtest", s7)
                if status == "FAIL":
                    reject("OOS verliesgevend")

        # 8 scoring
        def s8():
            comps = {k: 0.0 for k in DEFAULT_WEIGHTS}
            m = rec["metrics"]
            if m is not None:
                trades = state["is"]["trades"]
                comps["risk_adjusted_return"] = _clip01(m["sharpe_daily"] / 3.0)
                comps["drawdown"] = _clip01(1.0 - m["max_drawdown_pct"] / max(crit["max_drawdown_pct"], 1e-9))
                comps["recovery"] = _clip01(m["recovery_factor"] / 5.0)
                comps["profit_factor"] = _clip01((m["profit_factor"] - 1.0) / 1.5)
                comps["sample_size"] = _clip01(m["trades"] / (5.0 * max(crit["min_trades"], 1)))
                comps["yearly_stability"] = _yearly_component(trades)
                comps["regime_robustness"] = _regime_component(trades, fr)
                daily = validation.daily_pnl_from_trades(trades)
                with run.lock:
                    comps["dependency"] = _dependency_component(daily, run.accepted_daily)
                state["daily"] = daily
                rets = validation.daily_returns_from_trades(trades, capital)
                psr = validation.probabilistic_sharpe_ratio(rets)
                var = run.sr_var if run.sr_var else (1.0 / max(psr.get("n") or 1, 1))
                dsr = validation.deflated_sharpe_ratio(rets, run.n_trials, var)
                stats["psr"], stats["dsr"] = psr.get("value"), dsr.get("value")
                if "stress" in state and m["net_profit"] > 0:
                    comps["cost_sensitivity"] = _clip01(state["stress"]["net_profit"] / m["net_profit"])
                if "stress" in state:
                    name, vals = _neighbour_params(spec)
                    if name:
                        ratios = []
                        for v in vals:
                            ctx.check()
                            r = backtest.run_rules_backtest(spec, fr.bid_is, fr.ask_is, {name: v}, capital=capital,
                                                            commission_per_lot=comm)
                            net = summarize(r["trades"], capital)["net_profit"]
                            ratios.append(_clip01(net / m["net_profit"]))
                        comps["parameter_stability"] = float(np.mean(ratios))
                        stats["parameter_neighbours"] = {"param": name, "values": vals, "ratios": ratios}
                o = rec["oos_metrics"]
                if o is not None and o["net_profit"] > 0:
                    comps["oos_performance"] = 0.5 + 0.5 * _clip01((o["profit_factor"] - 1.0) / 1.5)
            stats["components"] = comps
            rec["score"] = score(comps, cfg["weights"])
            return f"score {rec['score']:.3f}"
        if rec["metrics"] is not None:
            st.run(8, "scoring", s8)
        else:
            st.skip(8, "scoring", "geen metrics")

        # 9 decision
        if rec["status"] == "REJECTED" and rec["rejection_reason"] is None:
            rec["status"] = "CANDIDATE"
            with run.lock:
                run.accepted_daily.append(state["daily"])
        st.rows.append({"stage": 9, "name": "beslissing", "status": rec["status"],
                        "detail": rec["rejection_reason"] or "alle criteria gehaald (nog geen volledige validatie)",
                        "seconds": 0.0})
    except JobCancelled:
        rec["status"], rec["rejection_reason"], rec["score"] = "ERROR", "geannuleerd tijdens verwerking", None
        rec["cancelled"] = True
    except _StageError as exc:
        rec["status"], rec["score"] = "ERROR", None
        rec["rejection_reason"] = f"fout in stage {exc.number} ({exc.stage_name}): {type(exc.original).__name__}: {exc.original}"
    return rec


def _ts_ge(iso: str, cut) -> bool:
    t = pd.Timestamp(iso)
    if cut.tzinfo is not None and t.tzinfo is None:
        t = t.tz_localize(cut.tzinfo)
    elif cut.tzinfo is None and t.tzinfo is not None:
        t = t.tz_localize(None)
    return t >= cut


# ------------------------------------------------------------------ run
class _NullCtx:
    cancelled = False

    def progress(self, fraction, message=""):
        pass

    def check(self):
        pass


def _rss_mb():
    try:
        import psutil
        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        return None


def _merge(defaults: dict, given: dict | None) -> dict:
    return {**defaults, **{k: v for k, v in (given or {}).items() if v is not None or k == "max_ram_mb"}}


_TRANSIENT = ("cancelled", "equity_is", "equity_oos")


def _write(run: _Run, rec: dict) -> None:
    fields = {k: v for k, v in rec.items() if k not in _TRANSIENT}
    with run.lock:
        run.ledger.record(**fields)
        run.records.append(rec)


def _utc(value) -> pd.Timestamp:
    t = pd.Timestamp(value)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _parse_windows(config: dict) -> dict | None:
    """Explicit in_sample/out_of_sample ISO windows (both required together); None = legacy oos_fraction split."""
    ins, oos = config.get("in_sample"), config.get("out_of_sample")
    if not ins and not oos:
        return None
    if not (ins and oos):
        raise ValueError("geef zowel in_sample als out_of_sample op")
    w = {"is_start": _utc(ins["start"]), "is_end": _utc(ins["end"]),
         "oos_start": _utc(oos["start"]), "oos_end": _utc(oos["end"])}
    if w["is_end"] <= w["is_start"] or w["oos_end"] <= w["oos_start"]:
        raise ValueError("einddatum moet na de startdatum liggen")
    if w["oos_start"] < w["is_end"]:
        raise ValueError("out_of_sample mag niet overlappen met in_sample")
    return w


def run_discovery(ctx, config: dict, load_frames, ledger) -> dict:
    ctx = ctx or _NullCtx()
    cfg = dict(config)
    cfg["budget"] = _merge(DEFAULT_BUDGET, config.get("budget"))
    cfg["criteria"] = _merge(DEFAULT_CRITERIA, config.get("criteria"))
    cfg["weights"] = {**DEFAULT_WEIGHTS, **(config.get("weights") or {})}
    cfg["capital"] = float(config.get("capital") or 10000)
    cfg["commission_per_lot"] = float(config.get("commission_per_lot") or 0.0)
    cfg["oos_fraction"] = float(config.get("oos_fraction") or 0.3)
    if not 0.05 <= cfg["oos_fraction"] <= 0.6:
        raise ValueError("oos_fraction moet tussen 0.05 en 0.6 liggen")
    windows = _parse_windows(config)
    if windows is not None:
        cfg["start"], cfg["end"] = windows["is_start"].date().isoformat(), windows["oos_end"].date().isoformat()
        load_start = (windows["is_start"] - pd.Timedelta(days=WARMUP_DAYS)).date().isoformat()
        load_end = cfg["end"]
    else:
        load_start, load_end = cfg["start"], cfg["end"]
    budget = cfg["budget"]
    symbols = list(config.get("symbols") or ["XAUUSD"])
    timeframes = list(config.get("timeframes") or ["M15"])
    max_candidates = max(0, int(budget["max_candidates"]))
    hyps = generate_hypotheses(config.get("families"), symbols, timeframes, max_candidates, int(config.get("seed", 0)))

    n_prior = ledger.n_trials()
    var = ledger.sharpe_variance()
    run = _Run(ctx, cfg, ledger, len(hyps) + n_prior, (var / 252.0) if var else None)
    workers = max(1, int(budget["max_workers"]))
    max_ram = budget.get("max_ram_mb")
    ram_note = None
    if max_ram and _rss_mb() is None:
        ram_note = "max_ram_mb niet afgedwongen: psutil niet beschikbaar"
    ram_pauses = 0
    t_start = time.monotonic()
    deadline = t_start + float(budget["max_minutes"]) * 60.0
    stopped = "alle hypotheses verwerkt"
    frames_cache: dict = {}  # per-run: every (symbol, timeframe) frame is loaded once and sliced per stage
    pending: set = set()
    done_count = 0
    total = max(len(hyps), 1)

    def finish(fut_set):
        nonlocal done_count
        for fut in fut_set:
            fut.result()
            done_count += 1
            ctx.progress(done_count / total, f"{done_count}/{len(hyps)} hypotheses getest")

    def get_frames(h):
        key = (h["symbol"], h["timeframe"])
        if key not in frames_cache:
            try:
                bid, ask, manifest = load_frames(h["symbol"], h["timeframe"], load_start, load_end)
                frames_cache[key] = _Frames(bid, ask, manifest, cfg["oos_fraction"], windows, h["symbol"])
            except Exception as exc:
                frames_cache[key] = exc
        return frames_cache[key]

    def worker(h, fr):
        rec = _evaluate(h, fr, run)
        _write(run, rec)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        try:
            for h in hyps:
                ctx.check()
                if time.monotonic() >= deadline:
                    stopped = "tijdbudget (max_minutes) bereikt"
                    break
                while max_ram and (rss := _rss_mb()) is not None and rss > max_ram:
                    ram_pauses += 1
                    if not pending:
                        stopped = f"geheugenlimiet overschreden ({rss:.0f} MB > {max_ram} MB)"
                        break
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    finish(done)
                if stopped.startswith("geheugenlimiet"):
                    break
                while len(pending) >= workers:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    finish(done)
                    ctx.check()
                fr = get_frames(h)
                if isinstance(fr, Exception):
                    rec = {"kind": "DISCOVERY", "hypothesis_id": h["hypothesis_id"], "family": h["family"],
                           "symbol": h["symbol"], "timeframe": h["timeframe"], "start": cfg["start"],
                           "end": cfg["end"], "spec": h["spec"], "spec_sha256": engine.spec_sha256(h["spec"]),
                           "mutations": h["mutations"], "data_requirements": DATA_REQUIREMENTS, "status": "ERROR",
                           "rejection_reason": f"data laden mislukt: {type(fr).__name__}: {fr}",
                           "statistics": {"n_trials_in_run": run.n_trials, "stages": []}}
                    _write(run, rec)
                    done_count += 1
                    continue
                if fr.insufficient:
                    _write(run, {"kind": "DISCOVERY", "hypothesis_id": h["hypothesis_id"], "family": h["family"],
                                 "symbol": h["symbol"], "timeframe": h["timeframe"], "start": cfg["start"],
                                 "end": cfg["end"], "spec": h["spec"], "spec_sha256": engine.spec_sha256(h["spec"]),
                                 "mutations": h["mutations"], "data_requirements": DATA_REQUIREMENTS,
                                 "status": "REJECTED", "rejection_reason": fr.insufficient, "score": None,
                                 "metrics": None, "oos_metrics": None,
                                 "statistics": {"n_trials_in_run": run.n_trials, "stages": [],
                                                "periods": fr.periods, "validation": VALIDATION_TYPE}})
                    done_count += 1
                    ctx.progress(done_count / total, f"{done_count}/{len(hyps)} hypotheses getest")
                    continue
                pending.add(pool.submit(worker, h, fr))
            else:
                if max_candidates > 0 and len(hyps) >= max_candidates:
                    stopped = "max_candidates bereikt"
        except JobCancelled:
            stopped = "geannuleerd"
        finally:
            done, _ = wait(pending) if pending else (set(), set())
            finish(done)
    ctx.progress(1.0 if stopped == "alle hypotheses verwerkt" else done_count / total, "klaar")
    periods = [{"symbol": sym, "timeframe": tf, **fr.periods} if isinstance(fr, _Frames) else
               {"symbol": sym, "timeframe": tf, "in_sample": None, "out_of_sample": None, "sufficient": False,
                "reason": f"data laden mislukt: {type(fr).__name__}: {fr}"}
               for (sym, tf), fr in frames_cache.items()]
    return _summary(run, hyps, stopped, ram_pauses, ram_note, time.monotonic() - t_start, n_prior, periods)


def _summary(run: _Run, hyps, stopped, ram_pauses, ram_note, seconds, n_prior, periods=()) -> dict:
    recs = run.records
    cancelled = sum(1 for r in recs if r.get("cancelled"))
    cand = [r for r in recs if r["status"] == "CANDIDATE"]
    rej = [r for r in recs if r["status"] == "REJECTED"]
    err = [r for r in recs if r["status"] == "ERROR" and not r.get("cancelled")]
    ranked = sorted((r for r in recs if r["status"] in ("CANDIDATE", "REJECTED") and r.get("score") is not None),
                    key=lambda r: (r["status"] == "CANDIDATE", r["score"]), reverse=True)[:TOP_N]
    top = [{"hypothesis_id": r["hypothesis_id"], "family": r["family"], "symbol": r["symbol"],
            "timeframe": r["timeframe"], "tf": r["timeframe"], "status": r["status"],
            "rejection_reason": r["rejection_reason"], "score": r["score"], "metrics": r["metrics"],
            "oos_metrics": r["oos_metrics"], "spec": r["spec"], "dsr": (r["statistics"] or {}).get("dsr"),
            "equity_is": r.get("equity_is") or [], "equity_oos": r.get("equity_oos") or []} for r in ranked]
    by_symbol: dict = {}
    for r in recs:
        row = by_symbol.setdefault(r["symbol"], {"symbol": r["symbol"], "tested": 0, "candidates": 0, "rejected": 0,
                                                 "errors": 0})
        row["tested"] += 1
        key = {"CANDIDATE": "candidates", "REJECTED": "rejected", "ERROR": "errors"}.get(r["status"])
        if key:
            row[key] += 1
    n_trials = run.n_trials
    summary_nl = (
        f"Er zijn {len(recs)} hypotheses getest ({len(cand)} kandidaten, {len(rej)} afgewezen, {len(err)} fouten); "
        f"gestopt: {stopped}. De OOS-test hier is een historische out-of-sample test ({VALIDATION_TYPE}) op data "
        f"die na de in-sample periode ligt, met de in-sample gekozen en bevroren parameters; het is GEEN "
        f"prospectieve forward test. Kandidaten hebben alleen de in-sample criteria, de kostenstress en een enkele "
        f"OOS-test doorstaan en moeten nog Valideren (walk-forward, Monte Carlo, parameterstabiliteit, holdout) en "
        f"daarna een bevroren forward test doorstaan. Wie de beste uit veel hypotheses kiest, overschat de "
        f"werkelijke prestatie (selectiebias): de Deflated Sharpe Ratio (DSR) is berekend met {n_trials} trials "
        f"(deze run plus {n_prior} eerdere ledger-entries) en een lage DSR betekent dat het resultaat ook door "
        f"toeval verklaard kan worden. De score rangschikt alleen en is geen kans op winst; resultaten zijn "
        f"gebaseerd op OHLC-proxies zonder orderflow.")
    return {"tested": len(recs), "candidates": len(cand), "rejected": len(rej), "errors": len(err),
            "cancelled": cancelled, "planned": len(hyps), "stopped_reason": stopped, "top": top,
            "by_symbol": sorted(by_symbol.values(), key=lambda x: x["symbol"]), "periods": list(periods),
            "validation": VALIDATION_TYPE, "pipeline": PIPELINE,
            "weights": run.cfg["weights"], "summary_nl": summary_nl, "n_trials": n_trials,
            "ram_pauses": ram_pauses, "ram_note": ram_note, "seconds": round(seconds, 2)}
