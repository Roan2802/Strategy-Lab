"""Strategy generator: bounded, diverse rule-spec candidates from mechanism families.

Every family is a distinct mechanism template (its own entry logic and bounded parameter space); exits/risk are
generated from a separate exit architecture (stop type, target type, trailing, break-even, time exit, cooldown,
max trades per day). Sizing is always risk-% of equity. Grid, martingale and averaging down are never generated;
winner-only scale-in exists only behind the explicit opt-in ``allow_scale_in``.

IQ Capital dossiers are used as *inspiration* via ``components/iq_components.json``; a candidate that uses them is
always an own research variant, never an exact reconstruction. Evidence grades A/B/C describe rule completeness only.

Public contract (used by the factory pipeline): families_catalog, generate, level1_check, dedup, baselines.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from functools import lru_cache
from pathlib import Path

import numpy as np

from runtime.strategy_lab import rule_engine as engine

SCHEMA = engine.SCHEMA
COMPONENTS_PATH = Path(__file__).resolve().parent / "components" / "iq_components.json"
GROUPS = ("trend", "breakout", "mean_reversion", "price_action", "statistical", "vwap_volume", "hybrid")
RISK_PERCENT = 0.5
MAX_ATTEMPTS = 14
NEAR_DUP_REL = 0.10
HTF = {"M1": "M15", "M5": "H1", "M15": "H4", "M30": "H4", "H1": "H4", "H4": "D1", "D1": None}
INTRADAY_MAX_MS = engine.TF_MS["H1"]

VWAP_NOTE = ("VWAP is berekend op Dukascopy-volume; in deze dataset is dat constant 1 per M1-bar, waardoor de VWAP "
             "feitelijk een tijdgewogen gemiddelde (TWAP) is. Geen echte futures-sessie-VWAP.")
VOLUME_NOTE = ("Volume = Dukascopy tick/activiteitsvolume (PROXY). Dit is GEEN echt futuresvolume en geen orderflow; "
               "in de huidige dataset is het constant 1 per M1-bar en dus niet informatief.")
SWEEP_NOTE = "Liquiditeit/structuur is een OHLC-patroon (PROXY); er is geen orderboek-, footprint- of liquidatiedata."
HURST_NOTE = "Hurst is een eigen aggregated-variance PROXY, geen exacte Hurst-exponent."
SESSION_EXIT_NOTE = ("Sessie-close is benaderd met time_exit_bars = sessielengte in bars; de backtester heeft geen "
                     "klok-exit, dus een late entry kan na de sessie doorlopen.")
CFD_NOTE = ("Bron gebruikt futures/andere instrumenten; toepassing op Dukascopy CFD/FX/index is een NIEUWE hypothese.")


# --------------------------------------------------------------------------------------------- spec helpers

def ref(name: str) -> dict:
    return {"param": name}


def ind(name, period=None, **kw) -> dict:
    out = {"indicator": name}
    if period is not None:
        out["period"] = period
    out.update(kw)
    return out


def cmp(left, op, right) -> dict:
    return {"left": left, "op": op, "right": right}


def val(v) -> dict:
    return {"value": v}


def price(p, shift=0) -> dict:
    return {"price": p, **({"shift": shift} if shift else {})}


def lvl(name, **kw) -> dict:
    return {"level": name, **kw}


def pat(name, **kw) -> dict:
    return {"pattern": name, **kw}


def ALL(*conds) -> dict:
    flat = []
    for c in conds:
        if c is None:
            continue
        flat.extend(c["all"] if set(c) == {"all"} else [c])
    return flat[0] if len(flat) == 1 else {"all": flat}


CLOSE, OPEN, HIGH, LOW = price("close"), price("open"), price("high"), price("low")
UP, DOWN = cmp(CLOSE, ">", OPEN), cmp(CLOSE, "<", OPEN)  # candle colour: makes long/short entries mutually exclusive


def _grid(lo, hi, step):
    n = int(round((hi - lo) / step))
    return [round(lo + i * step, 10) for i in range(n + 1)]


class Ctx:
    """Per-candidate build context: symbol/timeframe, a seeded RNG and the accumulating parameter list."""

    def __init__(self, symbol: str, timeframe: str, rng: random.Random):
        self.symbol, self.timeframe, self.rng = symbol, timeframe, rng
        self.params: list[dict] = []
        self.base_defaults: dict = {}
        self.changes: list[dict] = []

    def p(self, name, type_, lo, hi, step, base):
        """Declare a bounded parameter; its default is sampled from the grid (base = family reference default)."""
        grid = _grid(lo, hi, step)
        v = self.rng.choice(grid)
        if type_ == "int":
            lo, hi, step, v, base = int(lo), int(hi), int(step), int(round(v)), int(base)
        self.params.append({"name": name, "type": type_, "min": lo, "max": hi, "step": step, "default": v})
        self.base_defaults[name] = base
        if v != base:
            self.changes.append({"what": f"param:{name}", "from": base, "to": v})
        return ref(name)

    def choice(self, what, options, base=None):
        v = self.rng.choice(options)
        b = options[0] if base is None else base
        if v != b:
            self.changes.append({"what": what, "from": _plain(b), "to": _plain(v)})
        return v

    @property
    def htf(self):
        return HTF.get(self.timeframe)

    @property
    def bars_per_hour(self):
        return max(1, 3_600_000 // engine.TF_MS[self.timeframe])


def _plain(v):
    if isinstance(v, dict):
        return v.get("id") or v.get("name") or json.dumps(v, sort_keys=True)
    return v


# --------------------------------------------------------------------------------------------- sessions per symbol

_INDEX_US = ("US30", "US500", "USTEC", "USATECHIDXUSD", "USA30IDXUSD", "USA500IDXUSD")
_INDEX_EU = ("DE40", "DEUIDXEUR")


def home_sessions(symbol: str) -> list[dict]:
    """Cash-open reference sessions per symbol (local, DST-aware via timezone)."""
    s = symbol.upper()
    if s in _INDEX_US:
        return [{"id": "NY", "timezone": "America/New_York", "open": "09:30"}]
    if s in _INDEX_EU:
        return [{"id": "Frankfurt", "timezone": "Europe/Berlin", "open": "09:00"}]
    if s.startswith("XAU") or s.startswith("XAG"):
        return [{"id": "London", "timezone": "Europe/London", "open": "08:00"},
                {"id": "NY", "timezone": "America/New_York", "open": "09:30"}]
    return [{"id": "London", "timezone": "Europe/London", "open": "08:00"},
            {"id": "NY", "timezone": "America/New_York", "open": "08:00"}]


def _hhmm_add(text: str, minutes: int) -> str:
    h, m = map(int, text.split(":"))
    t = min(23 * 60 + 59, h * 60 + m + minutes)
    return f"{t // 60:02d}:{t % 60:02d}"


def _or_options(ctx, start: str, options) -> list:
    """Opening-range lengths whose window [start, start+m) contains at least one bar OPEN of the timeframe."""
    bar_min = engine.TF_MS[ctx.timeframe] // 60_000
    s = engine._hhmm(start)
    ok = [m for m in options if any((t % bar_min) == 0 for t in range(s, s + m))]
    if not ok:
        first = ((s + bar_min - 1) // bar_min) * bar_min
        ok = [first - s + bar_min]
    return ok


def _session(home: dict, start_offset_min: int, length_min: int) -> dict:
    return {"timezone": home["timezone"], "start": _hhmm_add(home["open"], start_offset_min),
            "end": _hhmm_add(home["open"], start_offset_min + length_min)}


def _session_minutes(sess: dict) -> int:
    s, e = engine._hhmm(sess["start"]), engine._hhmm(sess["end"])
    return e - s if e > s else e + 1440 - s


# --------------------------------------------------------------------------------------------- entry builders
# Each builder returns {"long", "short", "blocks", optional "direction", "session", "order", "level_target",
# "level_target_long", "level_target_short", "max_trades_per_day", "proxies", "variant", "exits", "iq"}.

def _ma(ctx, name, lo, hi, step, base):
    kind = ctx.choice("ma_type", ["ema", "sma"])
    return ind(kind, ctx.p(name, "int", lo, hi, step, base)), kind


def b_ma_cross(ctx):
    fast, k = _ma(ctx, "ma_fast", 5, 30, 5, 20)
    slow = ind(k, ctx.p("ma_slow", "int", 40, 200, 10, 50))
    return {"long": cmp(fast, "crosses_above", slow), "short": cmp(fast, "crosses_below", slow),
            "blocks": [f"entry:{k}_cross"], "variant": k}


def b_ma_slope(ctx):
    n = ctx.p("slope_n", "int", 10, 60, 5, 20)
    ma, k = _ma(ctx, "ma_n", 20, 200, 10, 50)
    return {"long": ALL(cmp(ind("linreg_slope", n), "crosses_above", val(0)), cmp(CLOSE, ">", ma)),
            "short": ALL(cmp(ind("linreg_slope", n), "crosses_below", val(0)), cmp(CLOSE, "<", ma)),
            "blocks": ["entry:linreg_slope_cross", f"filter:{k}"], "variant": k}


def b_swing_structure(ctx):
    k = ctx.p("swing_k", "int", 2, 5, 1, 3)
    return {"long": ALL(pat("structure_up", lookback=k), pat("swing_break_high", lookback=k)),
            "short": ALL(pat("structure_down", lookback=k), pat("swing_break_low", lookback=k)),
            "blocks": ["filter:hh_hl_structure", "entry:swing_break"], "variant": "structure_break"}


def b_adx_strength(ctx):
    thr = ctx.p("adx_min", "int", 15, 35, 5, 25)
    ma, k = _ma(ctx, "ma_n", 20, 100, 10, 50)
    return {"long": ALL(cmp(ind("adx", 14), "crosses_above", val(thr)), cmp(CLOSE, ">", ma)),
            "short": ALL(cmp(ind("adx", 14), "crosses_above", val(thr)), cmp(CLOSE, "<", ma)),
            "blocks": ["entry:adx_cross", f"filter:{k}"], "variant": k}


def b_pullback_mean(ctx):
    fast = ind("ema", ctx.p("ema_fast", "int", 10, 50, 5, 20))
    slow = ind("ema", ctx.p("ema_slow", "int", 50, 200, 25, 100))
    return {"long": ALL(cmp(CLOSE, ">", slow), cmp(LOW, "<=", fast), cmp(CLOSE, ">", fast)),
            "short": ALL(cmp(CLOSE, "<", slow), cmp(HIGH, ">=", fast), cmp(CLOSE, "<", fast)),
            "blocks": ["filter:ema_trend", "entry:touch_hold_ema"], "variant": "touch_hold"}


def b_mtf_trend(ctx):
    htf = ctx.htf or "D1"
    ema_h = ind("ema", ctx.p("htf_ema", "int", 20, 100, 10, 50), timeframe=htf)
    last_h = ind("sma", 1, timeframe=htf)
    trig = ind("ema", ctx.p("ema_trigger", "int", 10, 50, 5, 20))
    return {"long": ALL(cmp(last_h, ">", ema_h), cmp(CLOSE, "crosses_above", trig)),
            "short": ALL(cmp(last_h, "<", ema_h), cmp(CLOSE, "crosses_below", trig)),
            "blocks": [f"filter:htf_ema@{htf}", "entry:ema_cross"], "variant": htf}


def b_momentum_persistence(ctx):
    n = ctx.p("roc_n", "int", 10, 60, 5, 20)
    return {"long": ALL(cmp(ind("roc", n), ">", val(0)), cmp(ind("roc", n, shift=n), ">", val(0)),
                        cmp(CLOSE, ">", price("high", 1))),
            "short": ALL(cmp(ind("roc", n), "<", val(0)), cmp(ind("roc", n, shift=n), "<", val(0)),
                         cmp(CLOSE, "<", price("low", 1))),
            "blocks": ["filter:roc_persistence", "entry:new_bar_high"], "variant": "roc2"}


def b_orb(ctx):
    home = ctx.choice("session", home_sessions(ctx.symbol))
    minutes = ctx.choice("or_minutes", _or_options(ctx, home["open"], [15, 30, 60]), 30)
    sess = _session(home, 0, ctx.choice("entry_window_min", [150, 240, 390], 240))
    return {"long": cmp(CLOSE, "crosses_above", lvl("opening_range_high", minutes=minutes)),
            "short": cmp(CLOSE, "crosses_below", lvl("opening_range_low", minutes=minutes)),
            "session": sess, "max_trades_per_day": ctx.choice("max_trades_per_day", [1, 2]),
            "blocks": [f"entry:orb{minutes}", f"session:{home['id']}"], "variant": f"{home['id']}_{minutes}"}


def b_asian_range(ctx):
    rng = {"timezone": "UTC", "start": "00:00", "end": ctx.choice("asia_end", ["06:00", "07:00"], "07:00")}
    sess = {"timezone": "Europe/London", "start": "07:00", "end": ctx.choice("entry_end", ["11:00", "13:00"])}
    return {"long": cmp(CLOSE, "crosses_above", lvl("window_high", **rng)),
            "short": cmp(CLOSE, "crosses_below", lvl("window_low", **rng)),
            "session": sess, "max_trades_per_day": 1, "level_target": lvl("window_mid", **rng),
            "blocks": ["level:asian_range", "entry:range_break", "session:London_morning"], "variant": rng["end"]}


def b_london_breakout(ctx):
    """Break of the pre-London (Frankfurt) range during the first London hours, confirmed by candle colour."""
    rng = {"timezone": "Europe/London", "start": ctx.choice("pre_london_start", ["06:00", "07:00"], "07:00"),
           "end": "08:00"}
    sess = {"timezone": "Europe/London", "start": "08:00", "end": ctx.choice("entry_end", ["10:00", "12:00"])}
    return {"long": ALL(cmp(CLOSE, "crosses_above", lvl("window_high", **rng)), UP),
            "short": ALL(cmp(CLOSE, "crosses_below", lvl("window_low", **rng)), DOWN),
            "session": sess, "max_trades_per_day": 1, "level_target": None,
            "blocks": ["level:pre_london_range", "entry:range_break_colour", "session:London_open"],
            "variant": rng["start"]}


def b_ny_breakout(ctx):
    """London-session range (accumulation) broken after the New York open."""
    rng = {"timezone": "Europe/London", "start": "08:00", "end": ctx.choice("london_end", ["12:00", "13:00"], "13:00")}
    sess = {"timezone": "America/New_York", "start": "09:30", "end": ctx.choice("entry_end", ["11:30", "13:00"])}
    return {"long": cmp(CLOSE, "crosses_above", lvl("window_high", **rng)),
            "short": cmp(CLOSE, "crosses_below", lvl("window_low", **rng)),
            "session": sess, "max_trades_per_day": 1, "level_target": lvl("window_mid", **rng),
            "blocks": ["level:london_range", "entry:range_break", "session:NY"], "variant": rng["end"]}


def b_prev_day_break(ctx):
    v = ctx.choice("prev_level", ["day", "week"])
    return {"long": cmp(CLOSE, "crosses_above", lvl(f"prev_{v}_high")),
            "short": cmp(CLOSE, "crosses_below", lvl(f"prev_{v}_low")), "max_trades_per_day": 1,
            "blocks": [f"entry:prev_{v}_break"], "variant": v}


def b_donchian(ctx):
    ch = ctx.p("channel", "int", 10, 100, 5, 20)
    return {"long": cmp(CLOSE, ">", ind("donchian_high", ch, shift=1)),
            "short": cmp(CLOSE, "<", ind("donchian_low", ch, shift=1)),
            "blocks": ["entry:donchian_break"], "variant": "close"}


def b_consolidation(ctx):
    n = ctx.p("box_n", "int", 10, 40, 5, 20)
    w = ctx.p("box_width_atr", "float", 2.0, 6.0, 0.5, 4.0)
    return {"long": ALL(cmp(ind("range_width_atr", n, shift=1), "<", val(w)),
                        cmp(CLOSE, ">", ind("donchian_high", n, shift=1))),
            "short": ALL(cmp(ind("range_width_atr", n, shift=1), "<", val(w)),
                         cmp(CLOSE, "<", ind("donchian_low", n, shift=1))),
            "blocks": ["filter:tight_box", "entry:box_break"], "variant": "box"}


def b_nr(ctx):
    p_ = ctx.choice("nr_pattern", ["nr4", "nr7"], "nr4")
    trend = ind("ema", ctx.p("trend_ema", "int", 20, 200, 10, 50))
    off = ctx.p("offset_atr", "float", 0.0, 0.5, 0.05, 0.1)
    return {"long": ALL(pat(p_), cmp(CLOSE, ">", trend)), "short": ALL(pat(p_), cmp(CLOSE, "<", trend)),
            "order": {"type": "stop", "offset_atr": off, "atr_period": 14, "expiry_bars": 2},
            "blocks": [f"entry:{p_}", "filter:ema_side", "order:stop"], "variant": p_}


def b_atr_expansion(ctx):
    thr = ctx.p("range_atr_min", "float", 1.2, 3.0, 0.2, 1.8)
    return {"long": ALL(cmp(ind("range_atr", 14), ">", val(thr)), cmp(CLOSE, ">", OPEN),
                        cmp(CLOSE, ">", price("high", 1))),
            "short": ALL(cmp(ind("range_atr", 14), ">", val(thr)), cmp(CLOSE, "<", OPEN),
                         cmp(CLOSE, "<", price("low", 1))),
            "blocks": ["filter:range_expansion", "entry:directional_bar"], "variant": "range_atr"}


def b_breakout_retest(ctx):
    n = ctx.p("channel", "int", 10, 60, 5, 20)
    s = ctx.p("retest_shift", "int", 3, 12, 1, 5)
    old_h, old_l = ind("donchian_high", n, shift=s), ind("donchian_low", n, shift=s)
    return {"long": ALL(cmp(ind("donchian_high", n, shift=1), ">", old_h), cmp(LOW, "<=", old_h),
                        cmp(CLOSE, ">", old_h)),
            "short": ALL(cmp(ind("donchian_low", n, shift=1), "<", old_l), cmp(HIGH, ">=", old_l),
                         cmp(CLOSE, "<", old_l)),
            "blocks": ["entry:breakout_retest"], "variant": "retest"}


def b_bollinger(ctx):
    n = ctx.p("bb_n", "int", 14, 40, 2, 20)
    m = ctx.p("bb_mult", "float", 1.5, 3.0, 0.25, 2.0)
    adx_max = ctx.p("adx_max", "int", 15, 35, 5, 25)
    return {"long": ALL(cmp(CLOSE, "crosses_above", ind("bb_lower", n, mult=m)), cmp(ind("adx", 14), "<", val(adx_max))),
            "short": ALL(cmp(CLOSE, "crosses_below", ind("bb_upper", n, mult=m)), cmp(ind("adx", 14), "<", val(adx_max))),
            "level_target": ind("bb_mid", n), "blocks": ["entry:bb_reentry", "filter:low_adx"], "variant": "reentry"}


def b_rsi_extremes(ctx):
    n = ctx.p("rsi_n", "int", 2, 21, 1, 14)
    lo = ctx.p("rsi_lo", "int", 10, 35, 5, 30)
    hi = ctx.p("rsi_hi", "int", 65, 90, 5, 70)
    return {"long": cmp(ind("rsi", n), "crosses_above", val(lo)), "short": cmp(ind("rsi", n), "crosses_below", val(hi)),
            "level_target": ind("sma", 20), "blocks": ["entry:rsi_extreme_exit"], "variant": "cross_back"}


def b_vwap_deviation_mr(ctx):
    lo = ctx.p("dev_lo", "float", -3.0, -1.0, 0.25, -2.0)
    hi = ctx.p("dev_hi", "float", 1.0, 3.0, 0.25, 2.0)
    return {"long": cmp(ind("vwap_dev_atr", 14), "crosses_above", val(lo)),
            "short": cmp(ind("vwap_dev_atr", 14), "crosses_below", val(hi)),
            "level_target": ind("vwap"), "blocks": ["entry:vwap_dev_fade"], "variant": "atr_units", "proxies": [VWAP_NOTE]}


def b_atr_extreme(ctx):
    m = ctx.p("band_mult", "float", 2.0, 4.0, 0.1, 3.1)
    return {"long": ALL(cmp(LOW, "<=", ind("atr_band_lower", 14, mult=m)), UP),
            "short": ALL(cmp(HIGH, ">=", ind("atr_band_upper", 14, mult=m)), DOWN),
            "blocks": ["entry:atr_band_touch"], "variant": "touch", "exits": ["atr_atr", "atr_r_time", "atr_r"]}


def b_zscore(ctx):
    n = ctx.p("z_n", "int", 20, 100, 10, 50)
    zl = ctx.p("z_lo", "float", -3.0, -1.5, 0.25, -2.0)
    zh = ctx.p("z_hi", "float", 1.5, 3.0, 0.25, 2.0)
    return {"long": cmp(ind("zscore", n), "crosses_above", val(zl)), "short": cmp(ind("zscore", n), "crosses_below", val(zh)),
            "level_target": ind("sma", n), "blocks": ["entry:zscore_reentry"], "variant": "close"}


def b_failed_breakout(ctx):
    n = ctx.p("channel", "int", 10, 60, 5, 20)
    return {"long": ALL(cmp(price("close", 1), "<", ind("donchian_low", n, shift=2)),
                        cmp(CLOSE, ">", ind("donchian_low", n, shift=2))),
            "short": ALL(cmp(price("close", 1), ">", ind("donchian_high", n, shift=2)),
                         cmp(CLOSE, "<", ind("donchian_high", n, shift=2))),
            "level_target": ind("range_mid", n), "blocks": ["entry:failed_breakout"], "variant": "two_bar"}


def b_range_mid(ctx):
    n = ctx.p("range_n", "int", 20, 80, 10, 40)
    er = ctx.p("er_max", "float", 0.1, 0.4, 0.05, 0.25)
    return {"long": ALL(cmp(ind("efficiency_ratio", n), "<", val(er)), cmp(LOW, "<=", ind("donchian_low", n, shift=1)),
                        cmp(CLOSE, ">", ind("donchian_low", n, shift=1)), UP),
            "short": ALL(cmp(ind("efficiency_ratio", n), "<", val(er)), cmp(HIGH, ">=", ind("donchian_high", n, shift=1)),
                         cmp(CLOSE, "<", ind("donchian_high", n, shift=1)), DOWN),
            "level_target": ind("range_mid", n), "exits": ["atr_level", "swing_level"],
            "blocks": ["filter:low_efficiency", "entry:range_edge_reject"], "variant": "edge"}


def b_sweep(ctx):
    v = ctx.choice("sweep_level", ["rolling", "prev_day"])
    if v == "rolling":
        lb = ctx.p("lookback", "int", 10, 60, 5, 20)
        return {"long": ALL(pat("liquidity_sweep_low", lookback=lb), UP),
                "short": ALL(pat("liquidity_sweep_high", lookback=lb), DOWN),
                "blocks": ["entry:sweep_rolling"], "variant": v, "proxies": [SWEEP_NOTE]}
    return {"long": ALL(cmp(LOW, "<", lvl("prev_day_low")), cmp(CLOSE, ">", lvl("prev_day_low")), UP),
            "short": ALL(cmp(HIGH, ">", lvl("prev_day_high")), cmp(CLOSE, "<", lvl("prev_day_high")), DOWN),
            "blocks": ["entry:sweep_prev_day"], "variant": v, "proxies": [SWEEP_NOTE]}


def b_mss(ctx):
    k = ctx.p("swing_k", "int", 2, 5, 1, 3)
    return {"long": pat("mss_bull", lookback=k), "short": pat("mss_bear", lookback=k),
            "blocks": ["entry:mss"], "variant": "mss", "proxies": [SWEEP_NOTE]}


def b_swing_breakout(ctx):
    k = ctx.p("swing_k", "int", 2, 6, 1, 3)
    return {"long": pat("swing_break_high", lookback=k), "short": pat("swing_break_low", lookback=k),
            "blocks": ["entry:swing_break"], "variant": "pivot"}


def b_fvg(ctx):
    lb = ctx.p("fvg_age", "int", 3, 20, 1, 10)
    trend = ind("ema", ctx.p("trend_ema", "int", 20, 200, 10, 50))
    return {"long": ALL(pat("fvg_bull_retest", lookback=lb), cmp(CLOSE, ">", trend)),
            "short": ALL(pat("fvg_bear_retest", lookback=lb), cmp(CLOSE, "<", trend)),
            "blocks": ["entry:fvg_retest", "filter:ema_side"], "variant": "retest", "proxies": [SWEEP_NOTE]}


def b_inside_bar(ctx):
    trend = ind("ema", ctx.p("trend_ema", "int", 20, 200, 10, 50))
    return {"long": ALL(pat("inside_bar"), cmp(CLOSE, ">", trend)), "short": ALL(pat("inside_bar"), cmp(CLOSE, "<", trend)),
            "order": {"type": "stop", "offset_atr": ctx.p("offset_atr", "float", 0.0, 0.5, 0.05, 0.1), "atr_period": 14,
                      "expiry_bars": 2},
            "blocks": ["entry:inside_bar", "order:stop", "filter:ema_side"], "variant": "stop_order"}


def b_engulfing(ctx):
    v = ctx.choice("location", ["band", "rsi"])
    if v == "band":
        n = ctx.p("bb_n", "int", 14, 40, 2, 20)
        lf, sf = cmp(LOW, "<=", ind("bb_lower", n)), cmp(HIGH, ">=", ind("bb_upper", n))
    else:
        lf = cmp(ind("rsi", 14), "<", val(ctx.p("rsi_lo", "int", 25, 45, 5, 40)))
        sf = cmp(ind("rsi", 14), ">", val(ctx.p("rsi_hi", "int", 55, 75, 5, 60)))
    return {"long": ALL(pat("bullish_engulfing"), lf), "short": ALL(pat("bearish_engulfing"), sf),
            "blocks": ["entry:engulfing", f"filter:{v}"], "variant": v}


def b_pin(ctx):
    n = ctx.p("support_n", "int", 10, 50, 5, 20)
    return {"long": ALL(pat("pin_bull"), cmp(LOW, "<=", ind("donchian_low", n, shift=1))),
            "short": ALL(pat("pin_bear"), cmp(HIGH, ">=", ind("donchian_high", n, shift=1))),
            "blocks": ["entry:pin_bar", "filter:at_extreme"], "variant": "extreme"}


def b_range_hl(ctx):
    n = ctx.p("range_n", "int", 20, 80, 10, 40)
    w = ctx.p("box_width_atr", "float", 3.0, 8.0, 0.5, 5.0)
    return {"long": ALL(cmp(ind("range_width_atr", n, shift=1), "<", val(w)), pat("bullish_engulfing"),
                        cmp(LOW, "<=", ind("donchian_low", n, shift=1))),
            "short": ALL(cmp(ind("range_width_atr", n, shift=1), "<", val(w)), pat("bearish_engulfing"),
                         cmp(HIGH, ">=", ind("donchian_high", n, shift=1))),
            "level_target": ind("range_mid", n), "blocks": ["filter:range_box", "entry:edge_engulfing"], "variant": "box"}


def b_three_touch(ctx):
    lb = ctx.p("range_n", "int", 20, 60, 5, 30)
    t = ctx.choice("touches", [3, 2, 4], 3)
    tol = ctx.p("touch_tol_atr", "float", 0.1, 0.5, 0.05, 0.25)
    return {"long": ALL(pat("range_touch_low", lookback=lb, touches=t, tolerance_atr=tol), UP),
            "short": ALL(pat("range_touch_high", lookback=lb, touches=t, tolerance_atr=tol), DOWN),
            "level_target": ind("range_mid", lb), "blocks": [f"entry:range_touch{t}_reclaim"], "variant": f"t{t}",
            "proxies": [SWEEP_NOTE]}


def b_crt(ctx):
    side = ctx.choice("direction", ["long", "short", "both"], "both")
    sess = None
    if ctx.symbol.upper() in _INDEX_US or ctx.symbol.upper().startswith("XAU"):
        if ctx.choice("ny_window", [True, False], True):
            sess = {"timezone": "America/New_York", "start": "09:30", "end": "12:00"}
    out = {"long": pat("crt_bull"), "short": pat("crt_bear"), "direction": side, "session": sess,
           "max_trades_per_day": 2, "blocks": ["entry:crt_3candle"] + (["session:NY_09:30-12:00"] if sess else []),
           "variant": side}
    if side == "long":
        out["level_target"] = price("high", 2)
    elif side == "short":
        out["level_target"] = price("low", 2)
    return out


def b_pa_reversal(ctx):
    return {"long": ALL({"any": [pat("bullish_engulfing"), pat("pin_bull")]}, cmp(LOW, "<=", lvl("prev_day_low"))),
            "short": ALL({"any": [pat("bearish_engulfing"), pat("pin_bear")]}, cmp(HIGH, ">=", lvl("prev_day_high"))),
            "level_target": lvl("prev_day_close"), "blocks": ["entry:reversal_candle", "filter:prev_day_level"],
            "variant": "prev_day"}


def b_autocorr(ctx):
    v = ctx.choice("acf_regime", ["momentum", "reversion"])
    n = ctx.p("acf_n", "int", 20, 80, 10, 40)
    lag = ctx.choice("acf_lag", [1, 2, 5], 1)
    if v == "momentum":
        thr = ctx.p("acf_min", "float", 0.0, 0.3, 0.05, 0.1)
        bo = ctx.p("breakout_n", "int", 5, 30, 5, 10)
        return {"long": ALL(cmp(ind("autocorr", n, lag=lag), ">", val(thr)), cmp(CLOSE, ">", ind("donchian_high", bo, shift=1))),
                "short": ALL(cmp(ind("autocorr", n, lag=lag), ">", val(thr)), cmp(CLOSE, "<", ind("donchian_low", bo, shift=1))),
                "blocks": ["filter:positive_autocorr", "entry:donchian_break"], "variant": f"mom_lag{lag}"}
    thr = ctx.p("acf_max", "float", -0.3, 0.0, 0.05, -0.1)
    z = ctx.p("z_lo", "float", -3.0, -1.0, 0.25, -1.5)
    zh = ctx.p("z_hi", "float", 1.0, 3.0, 0.25, 1.5)
    return {"long": ALL(cmp(ind("autocorr", n, lag=lag), "<", val(thr)), cmp(ind("zscore", 20), "<", val(z))),
            "short": ALL(cmp(ind("autocorr", n, lag=lag), "<", val(thr)), cmp(ind("zscore", 20), ">", val(zh))),
            "level_target": ind("sma", 20), "blocks": ["filter:negative_autocorr", "entry:zscore_fade"],
            "variant": f"rev_lag{lag}"}


def b_hurst(ctx):
    side = ctx.choice("direction", ["long", "both"], "long")
    hn = ctx.p("hurst_n", "int", 64, 256, 32, 128)
    hmin = ctx.p("hurst_min", "float", 0.45, 0.65, 0.05, 0.5)
    amax = ctx.p("acf_max", "float", -0.3, 0.0, 0.05, -0.1)
    bo = ctx.p("breakout_n", "int", 3, 20, 1, 5)
    regime = ALL(cmp(ind("hurst", hn), ">", val(hmin)), cmp(ind("autocorr", 20, lag=5), "<=", val(amax)))
    return {"long": ALL(regime, cmp(CLOSE, ">", ind("donchian_high", bo, shift=1))),
            "short": ALL(regime, cmp(CLOSE, "<", ind("donchian_low", bo, shift=1))), "direction": side,
            "blocks": ["filter:hurst_proxy", "filter:acf_lag5", "entry:n_bar_break"], "variant": side,
            "exits": ["atr_target_trail", "atr_trail", "atr_r"], "proxies": [HURST_NOTE]}


def b_hour_of_day(ctx):
    h = ctx.p("entry_hour", "int", 0, 23, 1, 8)
    side = ctx.choice("direction", ["long", "short"], "long")
    tz = ctx.choice("calendar_tz", ["UTC", "America/New_York"], "UTC")
    cond = {"calendar": "hour", "in": [h], "timezone": tz}
    return {"long": cond, "short": copy.deepcopy(cond), "direction": side, "max_trades_per_day": 1,
            "blocks": ["entry:hour_of_day", f"calendar_tz:{tz}"], "variant": f"{side}_{tz}", "exits": ["atr_r_time", "pct_time"]}


def b_day_of_week(ctx):
    d = ctx.p("weekday", "int", 0, 4, 1, 0)
    h = ctx.p("entry_hour", "int", 0, 23, 1, 8)
    side = ctx.choice("direction", ["long", "short"], "long")
    cond = ALL({"calendar": "weekday", "in": [d]}, {"calendar": "hour", "in": [h]})
    return {"long": cond, "short": copy.deepcopy(cond), "direction": side, "max_trades_per_day": 1,
            "blocks": ["entry:day_of_week"], "variant": side, "exits": ["atr_r_time", "pct_time"]}


def b_vol_clustering(ctx):
    v = ctx.choice("vol_state", ["expanding", "contracting"])
    s = ctx.p("rv_short", "int", 5, 20, 5, 10)
    lg = ctx.p("rv_long", "int", 40, 120, 20, 60)
    if v == "expanding":
        bo = ctx.p("breakout_n", "int", 10, 40, 5, 20)
        return {"long": ALL(cmp(ind("realized_vol", s), ">", ind("realized_vol", lg)), cmp(CLOSE, ">", ind("donchian_high", bo, shift=1))),
                "short": ALL(cmp(ind("realized_vol", s), ">", ind("realized_vol", lg)), cmp(CLOSE, "<", ind("donchian_low", bo, shift=1))),
                "blocks": ["filter:vol_expanding", "entry:donchian_break"], "variant": v}
    return {"long": ALL(cmp(ind("realized_vol", s), "<", ind("realized_vol", lg)), cmp(CLOSE, "crosses_above", ind("bb_lower", 20))),
            "short": ALL(cmp(ind("realized_vol", s), "<", ind("realized_vol", lg)), cmp(CLOSE, "crosses_below", ind("bb_upper", 20))),
            "level_target": ind("bb_mid", 20), "blocks": ["filter:vol_contracting", "entry:bb_reentry"], "variant": v}


def b_trend_range_switch(ctx):
    n = ctx.p("er_n", "int", 10, 40, 5, 20)
    hi = ctx.p("er_hi", "float", 0.3, 0.6, 0.05, 0.4)
    lo = ctx.p("er_lo", "float", 0.05, 0.25, 0.05, 0.15)
    ema_n = ind("ema", ctx.p("ema_n", "int", 10, 50, 5, 20))
    er = ind("efficiency_ratio", n)
    return {"long": {"any": [ALL(cmp(er, ">", val(hi)), cmp(CLOSE, "crosses_above", ema_n)),
                             ALL(cmp(er, "<", val(lo)), cmp(ind("zscore", 20), "crosses_above", val(-2.0)))]},
            "short": {"any": [ALL(cmp(er, ">", val(hi)), cmp(CLOSE, "crosses_below", ema_n)),
                              ALL(cmp(er, "<", val(lo)), cmp(ind("zscore", 20), "crosses_below", val(2.0)))]},
            "blocks": ["regime:efficiency_ratio", "entry:trend_or_fade"], "variant": "er_switch"}


def b_momentum_exhaustion(ctx):
    n = ctx.p("er_n", "int", 10, 40, 5, 20)
    hi = ctx.p("er_hi", "float", 0.4, 0.8, 0.05, 0.5)
    return {"long": ALL(cmp(ind("efficiency_ratio", n, shift=1), ">", val(hi)), cmp(ind("roc", n), "<", val(0)), pat("pin_bull")),
            "short": ALL(cmp(ind("efficiency_ratio", n, shift=1), ">", val(hi)), cmp(ind("roc", n), ">", val(0)), pat("pin_bear")),
            "blocks": ["filter:strong_trend", "entry:rejection_against_trend"], "variant": "pin"}


def b_large_move(ctx):
    n = ctx.p("ret_n", "int", 50, 200, 25, 100)
    z = ctx.p("move_z", "float", 2.0, 4.0, 0.25, 3.0)
    zn = ctx.p("move_z_neg", "float", -4.0, -2.0, 0.25, -3.0)
    return {"long": cmp(ind("ret_zscore", n), "<", val(zn)), "short": cmp(ind("ret_zscore", n), ">", val(z)),
            "blocks": ["entry:large_move_fade"], "variant": "fade", "exits": ["atr_r_time", "atr_r"]}


def b_vwap_pullback(ctx):
    trend = ind("ema", ctx.p("trend_ema", "int", 20, 200, 10, 50))
    return {"long": ALL(cmp(CLOSE, ">", trend), cmp(LOW, "<=", ind("vwap")), cmp(CLOSE, ">", ind("vwap"))),
            "short": ALL(cmp(CLOSE, "<", trend), cmp(HIGH, ">=", ind("vwap")), cmp(CLOSE, "<", ind("vwap"))),
            "blocks": ["filter:ema_trend", "entry:vwap_touch_hold"], "variant": "touch", "proxies": [VWAP_NOTE]}


def b_vwap_breakout(ctx):
    home = ctx.choice("session", home_sessions(ctx.symbol))
    sess = _session(home, 30, 300)
    return {"long": ALL(cmp(CLOSE, "crosses_above", ind("vwap")), cmp(ind("vwap"), ">", ind("vwap", shift=1))),
            "short": ALL(cmp(CLOSE, "crosses_below", ind("vwap")), cmp(ind("vwap"), "<", ind("vwap", shift=1))),
            "session": sess, "max_trades_per_day": 2, "blocks": ["entry:vwap_cross", "filter:vwap_slope",
                                                                 f"session:{home['id']}"],
            "variant": home["id"], "proxies": [VWAP_NOTE]}


def b_vwap_dev_momentum(ctx):
    d = ctx.p("dev_entry", "float", 0.5, 2.0, 0.25, 1.0)
    dn = ctx.p("dev_entry_neg", "float", -2.0, -0.5, 0.25, -1.0)
    return {"long": ALL(cmp(ind("vwap_dev_atr", 14), "crosses_above", val(d)), cmp(ind("vwap"), ">", ind("vwap", shift=1))),
            "short": ALL(cmp(ind("vwap_dev_atr", 14), "crosses_below", val(dn)), cmp(ind("vwap"), "<", ind("vwap", shift=1))),
            "blocks": ["entry:vwap_dev_momentum"], "variant": "momentum", "proxies": [VWAP_NOTE]}


def b_volume_contraction(ctx):
    vr = ctx.p("vol_ratio_max", "float", 0.5, 0.9, 0.05, 0.7)
    bo = ctx.p("breakout_n", "int", 10, 40, 5, 20)
    return {"long": ALL(cmp(ind("volume_ratio", 20, shift=1), "<", val(vr)), cmp(CLOSE, ">", ind("donchian_high", bo, shift=1))),
            "short": ALL(cmp(ind("volume_ratio", 20, shift=1), "<", val(vr)), cmp(CLOSE, "<", ind("donchian_low", bo, shift=1))),
            "blocks": ["filter:volume_contraction_proxy", "entry:donchian_break"], "variant": "tickvol",
            "proxies": [VOLUME_NOTE]}


def b_volume_expansion(ctx):
    vr = ctx.p("vol_ratio_min", "float", 1.3, 3.0, 0.1, 1.8)
    return {"long": ALL(cmp(ind("volume_ratio", 20), ">", val(vr)), cmp(CLOSE, ">", OPEN), cmp(CLOSE, ">", price("high", 1))),
            "short": ALL(cmp(ind("volume_ratio", 20), ">", val(vr)), cmp(CLOSE, "<", OPEN), cmp(CLOSE, "<", price("low", 1))),
            "blocks": ["filter:volume_expansion_proxy", "entry:directional_bar"], "variant": "tickvol",
            "proxies": [VOLUME_NOTE]}


def b_effort_result(ctx):
    v = ctx.choice("effort_state", ["continuation", "reversal"])
    if v == "continuation":
        r = ctx.p("range_atr_min", "float", 1.2, 2.5, 0.1, 1.5)
        vr = ctx.p("vol_ratio_max", "float", 0.5, 1.0, 0.05, 0.8)
        return {"long": ALL(cmp(ind("range_atr", 14), ">", val(r)), cmp(ind("volume_ratio", 20), "<", val(vr)), cmp(CLOSE, ">", OPEN)),
                "short": ALL(cmp(ind("range_atr", 14), ">", val(r)), cmp(ind("volume_ratio", 20), "<", val(vr)), cmp(CLOSE, "<", OPEN)),
                "blocks": ["filter:high_result_low_effort"], "variant": v, "proxies": [VOLUME_NOTE],
                "exits": ["atr_r_time", "atr_r"]}
    r = ctx.p("range_atr_max", "float", 0.3, 0.9, 0.1, 0.6)
    vr = ctx.p("vol_ratio_min", "float", 1.2, 2.5, 0.1, 1.5)
    return {"long": ALL(cmp(ind("range_atr", 14), "<", val(r)), cmp(ind("volume_ratio", 20), ">", val(vr)),
                        cmp(LOW, "<=", ind("donchian_low", 20, shift=1)), UP),
            "short": ALL(cmp(ind("range_atr", 14), "<", val(r)), cmp(ind("volume_ratio", 20), ">", val(vr)),
                         cmp(HIGH, ">=", ind("donchian_high", 20, shift=1)), DOWN),
            "blocks": ["filter:low_result_high_effort", "filter:at_extreme"], "variant": v, "proxies": [VOLUME_NOTE],
            "exits": ["atr_r_time", "atr_r"]}


def b_orb_adx_atr(ctx):
    home = ctx.choice("session", home_sessions(ctx.symbol))
    minutes = ctx.choice("or_minutes", _or_options(ctx, home["open"], [15, 30]), 15)
    adx_min = ctx.p("adx_min", "int", 15, 30, 5, 20)
    sess = _session(home, 0, 360)
    return {"long": ALL(cmp(CLOSE, ">", lvl("opening_range_high", minutes=minutes)), cmp(ind("adx", 14), ">", val(adx_min))),
            "short": ALL(cmp(CLOSE, "<", lvl("opening_range_low", minutes=minutes)), cmp(ind("adx", 14), ">", val(adx_min))),
            "session": sess, "max_trades_per_day": 1, "exits": ["atr_r", "session_close", "atr_r_be"],
            "blocks": [f"entry:orb{minutes}", "filter:adx", f"session:{home['id']}"], "variant": f"{home['id']}_{minutes}"}


def b_vwap_pullback_trend(ctx):
    drift = ctx.p("drift_pct", "float", 0.05, 0.3, 0.05, 0.1)
    drift_neg = ctx.p("drift_pct_neg", "float", -0.3, -0.05, 0.05, -0.1)
    bars = max(1, ctx.bars_per_hour)
    sess = {"timezone": "America/New_York", "start": "10:30", "end": "15:30"}
    return {"long": ALL(cmp(CLOSE, ">", ind("vwap")), cmp(ind("vwap"), ">", ind("vwap", shift=1)),
                        cmp(ind("roc", bars), ">=", val(drift)), cmp(CLOSE, "<", OPEN)),
            "short": ALL(cmp(CLOSE, "<", ind("vwap")), cmp(ind("vwap"), "<", ind("vwap", shift=1)),
                         cmp(ind("roc", bars), "<=", val(drift_neg)), cmp(CLOSE, ">", OPEN)),
            "session": sess, "max_trades_per_day": 4,
            "blocks": ["filter:vwap_side", "filter:vwap_slope", "filter:drift_60m", "entry:counter_candle", "session:NY"],
            "variant": "drift", "proxies": [VWAP_NOTE]}


def b_sweep_mss(ctx):
    k = ctx.p("swing_k", "int", 2, 4, 1, 3)
    n = ctx.p("sweep_window", "int", 5, 20, 1, 10)
    m = ctx.p("range_n", "int", 20, 60, 10, 30)
    return {"long": ALL(pat("mss_bull", lookback=k), cmp(ind("donchian_low", n), "<", ind("donchian_low", m, shift=n))),
            "short": ALL(pat("mss_bear", lookback=k), cmp(ind("donchian_high", n), ">", ind("donchian_high", m, shift=n))),
            "blocks": ["context:recent_sweep", "entry:mss"], "variant": "sweep_then_mss", "proxies": [SWEEP_NOTE]}


def b_range_break_volc(ctx):
    n = ctx.p("box_n", "int", 10, 40, 5, 20)
    w = ctx.p("box_width_atr", "float", 2.0, 6.0, 0.5, 4.0)
    s = ctx.p("rv_short", "int", 5, 20, 5, 10)
    contr = cmp(ind("realized_vol", s, shift=1), "<", ind("realized_vol", 60, shift=1))
    return {"long": ALL(contr, cmp(ind("range_width_atr", n, shift=1), "<", val(w)), cmp(CLOSE, ">", ind("donchian_high", n, shift=1))),
            "short": ALL(contr, cmp(ind("range_width_atr", n, shift=1), "<", val(w)), cmp(CLOSE, "<", ind("donchian_low", n, shift=1))),
            "blocks": ["filter:realized_vol_contraction", "filter:tight_box", "entry:box_break"], "variant": "rv"}


def b_momentum_regime(ctx):
    n = ctx.p("roc_n", "int", 10, 60, 5, 20)
    adx_min = ctx.p("adx_min", "int", 15, 35, 5, 20)
    htf = ctx.htf or "D1"
    flt_l = cmp(ind("sma", 1, timeframe=htf), ">", ind("ema", 50, timeframe=htf))
    flt_s = cmp(ind("sma", 1, timeframe=htf), "<", ind("ema", 50, timeframe=htf))
    return {"long": ALL(cmp(ind("roc", n), "crosses_above", val(0)), cmp(ind("adx", 14), ">", val(adx_min)), flt_l),
            "short": ALL(cmp(ind("roc", n), "crosses_below", val(0)), cmp(ind("adx", 14), ">", val(adx_min)), flt_s),
            "blocks": ["entry:roc_cross", "filter:adx", f"filter:htf_ema@{htf}"], "variant": htf}


def b_stat_mr_session(ctx):
    home = ctx.choice("session", home_sessions(ctx.symbol))
    sess = _session(home, ctx.choice("session_offset_min", [60, 240], 60), 240)
    n = ctx.p("z_n", "int", 20, 80, 10, 40)
    zl = ctx.p("z_lo", "float", -3.0, -1.5, 0.25, -2.0)
    zh = ctx.p("z_hi", "float", 1.5, 3.0, 0.25, 2.0)
    adx_max = ctx.p("adx_max", "int", 15, 30, 5, 25)
    return {"long": ALL(cmp(ind("zscore", n), "crosses_above", val(zl)), cmp(ind("adx", 14), "<", val(adx_max))),
            "short": ALL(cmp(ind("zscore", n), "crosses_below", val(zh)), cmp(ind("adx", 14), "<", val(adx_max))),
            "session": sess, "level_target": ind("sma", n),
            "blocks": ["entry:zscore_reentry", "filter:low_adx", f"session:{home['id']}"], "variant": home["id"]}


# --------------------------------------------------------------------------------------------- family table

def F(fid, group, label, desc, mechanisms, builder, *, iq=(), symbols="all", tfs=("M15", "H1"), intraday=False,
      exits=None, hypothesis=None):
    return {"id": fid, "group": group, "label_nl": label, "description_nl": desc, "mechanisms": list(mechanisms),
            "builder": builder, "iq": list(iq), "suitable_symbols": symbols, "default_timeframes": list(tfs),
            "intraday": intraday, "exits": exits, "hypothesis_nl": hypothesis or desc}


_IDX_GOLD = ["US30", "US500", "USTEC", "DE40", "XAUUSD"]

FAMILIES: list[dict] = [
    # trend
    F("trend_ma_cross", "trend", "MA-kruising", "Snelle EMA/SMA kruist trage EMA/SMA.", ["trend", "moving average"],
      b_ma_cross, tfs=("M15", "H1", "H4")),
    F("trend_ma_slope", "trend", "MA-helling", "Lineaire-regressiehelling wordt positief/negatief boven/onder een MA.",
      ["trend", "slope"], b_ma_slope, tfs=("M15", "H1", "H4")),
    F("trend_swing_structure", "trend", "HH/HL-swingstructuur",
      "Hogere highs en lows (bevestigde pivots) en een slot boven de laatste swing high.", ["trend", "market structure"],
      b_swing_structure, iq=[("32_andre_trend_color_change", "ac_uptrend_hh_hl"), ("08_patrick_pbd_structure", "pbd_impulse_balance")]),
    F("trend_adx_strength", "trend", "ADX-trendsterkte", "ADX kruist boven drempel terwijl de koers aan de goede kant van de MA ligt.",
      ["trend strength"], b_adx_strength),
    F("trend_pullback_mean", "trend", "Pullback naar het gemiddelde",
      "In een EMA-trend raakt de koers de snelle EMA en sluit erboven (short omgekeerd).", ["trend", "pullback"],
      b_pullback_mean, iq=[("32_andre_trend_color_change", "ac_first_green")]),
    F("trend_mtf_filter", "trend", "Multi-timeframe trendfilter",
      "Instap op lagere timeframe alleen in de richting van de hogere-timeframe EMA (alleen afgesloten HTF-bars).",
      ["trend", "multi-timeframe"], b_mtf_trend, iq=[("11_imre_one_time_framing", "otf_daily_state")]),
    F("trend_momentum_persistence", "trend", "Momentumpersistentie",
      "Positief momentum nu en een periode eerder, met een nieuwe kortetermijnhigh.", ["momentum"], b_momentum_persistence),
    # breakout
    F("breakout_orb", "breakout", "Opening range breakout (thuis-sessie)",
      "Slot buiten de opening range van de cash-opening van het symbool.", ["opening range", "session"], b_orb,
      iq=[("29_hannes_orb", "ho_reference_candle"), ("29_hannes_orb", "ho_sl_pct"), ("06_matteo_overnight_bias_orb", "onb_orb15_break")],
      intraday=True, exits=["atr_r", "pct_r", "session_close", "atr_r_be", "swing_r"]),
    F("breakout_asian_range", "breakout", "Asian range breakout",
      "Doorbraak van de Aziatische range (00:00-07:00 UTC) tijdens de Londense ochtend.", ["session range", "breakout"],
      b_asian_range, intraday=True, exits=["atr_r", "atr_level", "session_close", "swing_r"]),
    F("breakout_london", "breakout", "London breakout",
      "Doorbraak van de pre-Londen (Frankfurt) range 06/07:00-08:00 in de eerste Londense uren, met candlekleur.",
      ["pre-session range", "session"], b_london_breakout, intraday=True,
      exits=["atr_r", "session_close", "atr_r_be", "pct_r"]),
    F("breakout_ny", "breakout", "New York breakout van de Londen-range",
      "De Londense sessierange (accumulatie) wordt na de New York-opening doorbroken.",
      ["session range", "session"], b_ny_breakout, intraday=True,
      iq=[("33_jonas_london_us_orb", "jl_london_range"), ("33_jonas_london_us_orb", "jl_us_break")],
      exits=["atr_r", "session_close", "atr_r_be", "atr_level"]),
    F("breakout_prev_day", "breakout", "Vorige dag/week high-low breakout", "Slot kruist de vorige dag- of weekhigh/-low.",
      ["levels", "breakout"], b_prev_day_break),
    F("breakout_donchian", "breakout", "Donchian breakout", "Slot buiten het Donchian-kanaal van de vorige N candles.",
      ["channel breakout"], b_donchian, tfs=("M15", "H1", "H4")),
    F("breakout_consolidation", "breakout", "Consolidatie-breakout", "Smalle box (breedte in ATR) gevolgd door doorbraak.",
      ["volatility contraction", "breakout"], b_consolidation, iq=[("08_patrick_pbd_structure", "pbd_impulse_balance")]),
    F("breakout_nr", "breakout", "NR4/NR7 breakout", "Smalste-range candle; stop-order voorbij de candle in trendrichting.",
      ["volatility contraction"], b_nr, iq=[("35_trader_x_nr4_breakout", "tx_nr4"), ("35_trader_x_nr4_breakout", "tx_break_next")]),
    F("breakout_atr_expansion", "breakout", "ATR-expansie", "Candle-range groot t.o.v. vorige ATR met richting en nieuwe high/low.",
      ["volatility expansion"], b_atr_expansion),
    F("breakout_retest", "breakout", "Breakout + retest", "Eerdere kanaalhigh is doorbroken; koers test die terug en houdt.",
      ["breakout", "retest"], b_breakout_retest, iq=[("07_patrick_3touch_range", "p3t_breakout_retest")]),
    # mean reversion
    F("mr_bollinger", "mean_reversion", "Bollinger-reversal", "Herintrede binnen de Bollinger-band bij lage ADX; doel middenband.",
      ["mean reversion", "bands"], b_bollinger, exits=["atr_level", "atr_r_time", "atr_r", "swing_level"]),
    F("mr_rsi_extremes", "mean_reversion", "RSI-extremen", "RSI keert terug uit oversold/overbought.", ["mean reversion", "oscillator"],
      b_rsi_extremes, exits=["atr_level", "atr_r_time", "atr_r"]),
    F("mr_vwap_deviation", "mean_reversion", "VWAP-afwijking (fade)", "Grote afwijking van VWAP in ATR-eenheden, terug richting VWAP.",
      ["mean reversion", "vwap"], b_vwap_deviation_mr, iq=[("31_heiko_6e_vpoc_reversion", "hk_vwap_reversion")],
      exits=["atr_level", "atr_r_time", "atr_r"]),
    F("mr_atr_extreme", "mean_reversion", "ATR-extreem reversal", "Prijs raakt vorige close -/+ k x ATR; terugvering.",
      ["mean reversion", "volatility band"], b_atr_extreme,
      iq=[("19_mnq_atr_extreme_long", "atrx_band"), ("19_mnq_atr_extreme_long", "atrx_touch"),
          ("19_mnq_atr_extreme_long", "atrx_tp"), ("19_mnq_atr_extreme_long", "atrx_sl")]),
    F("mr_zscore", "mean_reversion", "Z-score reversal", "Z-score van de koers keert terug uit een extreem; doel het gemiddelde.",
      ["mean reversion", "statistics"], b_zscore, exits=["atr_level", "atr_r_time", "atr_r"]),
    F("mr_failed_breakout", "mean_reversion", "Mislukte breakout", "Slot buiten het kanaal en de volgende candle sluit terug erbinnen.",
      ["failed breakout"], b_failed_breakout, exits=["atr_level", "swing_level", "atr_r"],
      iq=[("13_andrea_failed_auction", "fa_dip_below_val")]),
    F("mr_range_mid", "mean_reversion", "Terug naar range-midden", "In een lage-efficiency range: afwijzing aan de rand, doel het midden.",
      ["range", "mean reversion"], b_range_mid, iq=[("08_patrick_pbd_structure", "pbd_tp_next_boundary")]),
    # price action / liquidity
    F("pa_liquidity_sweep", "price_action", "Liquidity sweep", "Prik door recente extremen of vorige-dag high/low en slot terug.",
      ["liquidity", "reversal"], b_sweep,
      iq=[("14_candace_turtle_soup", "ts_sweep_reclaim"), ("14_candace_turtle_soup", "ts_liquidity_level"),
          ("27_tim_liquidation_sweep", "tl_sweep_proxy")], exits=["swing_r", "atr_r", "atr_r_be", "atr_r_time"]),
    F("pa_mss", "price_action", "Market structure shift", "Slot voorbij de laatste swing na een tegengestelde swingreeks.",
      ["market structure"], b_mss, iq=[("14_candace_turtle_soup", "ts_structure_confirm"), ("15_candace_ict_2022", "ict_displacement")]),
    F("pa_swing_breakout", "price_action", "Swing breakout", "Slot kruist de laatst bevestigde swing high/low.", ["market structure"],
      b_swing_breakout, iq=[("30_patrick_fvg_last", "pf_structure_break")]),
    F("pa_fvg", "price_action", "Fair value gap retest", "Terugkeer in de laatste FVG in trendrichting.", ["imbalance", "retest"],
      b_fvg, iq=[("15_candace_ict_2022", "ict_fvg_def"), ("15_candace_ict_2022", "ict_fvg_retest"),
                 ("30_patrick_fvg_last", "pf_last_fvg_retest")]),
    F("pa_inside_bar", "price_action", "Inside bar breakout", "Inside bar met stop-order voorbij de candle in EMA-richting.",
      ["volatility contraction", "price action"], b_inside_bar),
    F("pa_engulfing", "price_action", "Engulfing op locatie", "Engulfing candle bij Bollinger-extreem of RSI-zone.", ["price action"],
      b_engulfing),
    F("pa_pin_rejection", "price_action", "Pin bar / rejection", "Pin bar aan een N-candle extreem.", ["rejection"], b_pin,
      iq=[("20_mnq_shooting_star_short", "ss_star")]),
    F("pa_range_hl", "price_action", "Range highs/lows", "Engulfing aan de rand van een smalle range; doel het midden.",
      ["range", "rejection"], b_range_hl, exits=["atr_level", "swing_level", "swing_r"]),
    F("pa_three_touch", "price_action", "Three-touch range reclaim",
      "Range-grens meermaals getest, dan sweep en slot terug in de range; doel het midden.", ["range", "liquidity"],
      b_three_touch, iq=[("07_patrick_3touch_range", "p3t_three_touch"), ("07_patrick_3touch_range", "p3t_sweep_reclaim"),
                         ("07_patrick_3touch_range", "p3t_tp_other_side")], exits=["atr_level", "swing_level", "swing_r"]),
    F("pa_crt", "price_action", "Candle range theory (CRT 3-candle)",
      "C2 prikt buiten C1 en sluit erbinnen; C3 sluit voorbij C2; doel de andere kant van C1.", ["liquidity", "three candle"],
      b_crt, iq=[("18_mike_crt_3candle", "crt_sweep_inside"), ("18_mike_crt_3candle", "crt_c3_break"),
                 ("18_mike_crt_3candle", "crt_tp_c1"), ("18_mike_crt_3candle", "crt_ny_window"),
                 ("18_mike_crt_3candle", "crt_trades_day")], exits=["atr_level", "swing_level", "atr_r", "swing_r"]),
    F("pa_reversal", "price_action", "Price-action reversal bij vorige-dag niveau",
      "Engulfing of pin bar die de vorige daghigh/-low raakt; doel vorige slot.", ["reversal", "levels"], b_pa_reversal,
      iq=[("26_henrik_profile_singleprints", "hp_prev_levels"), ("26_henrik_profile_singleprints", "hp_accept_reject")],
      exits=["atr_level", "swing_level", "swing_r", "atr_r_be"]),
    # statistical
    F("stat_autocorr_regime", "statistical", "Autocorrelatie-regime",
      "Positieve autocorrelatie -> breakout volgen; negatieve -> z-score fade.", ["autocorrelation", "regime"], b_autocorr,
      iq=[("21_trader_math_hurst_autocorr", "tm_acf")], exits=["atr_r_time", "atr_r", "atr_trail", "atr_level"]),
    F("stat_hurst_regime", "statistical", "Hurst + autocorrelatie breakout",
      "Hurst-proxy boven drempel en negatieve lag-5 autocorrelatie; N-candle breakout.", ["hurst", "autocorrelation"], b_hurst,
      iq=[("21_trader_math_hurst_autocorr", "tm_hurst"), ("21_trader_math_hurst_autocorr", "tm_acf"),
          ("21_trader_math_hurst_autocorr", "tm_breakout5"), ("21_trader_math_hurst_autocorr", "tm_exits")]),
    F("stat_hour_of_day", "statistical", "Uur-van-de-dag seizoenspatroon", "Instap op een vast uur met tijd-exit; test alleen de tijdsbias.",
      ["seasonality"], b_hour_of_day, iq=[("22_lewis_seasonality", "ls_calendar_window"), ("22_lewis_seasonality", "ls_time_exit")],
      intraday=True),
    F("stat_day_of_week", "statistical", "Dag-van-de-week effect", "Instap op vaste weekdag en uur met tijd-exit.", ["seasonality"],
      b_day_of_week, iq=[("22_lewis_seasonality", "ls_calendar_window"), ("22_lewis_seasonality", "ls_time_exit")], intraday=True),
    F("stat_vol_clustering", "statistical", "Volatiliteitsclustering",
      "Kortetermijn vs langetermijn realized vol bepaalt breakout (expansie) of fade (contractie).", ["volatility regime"],
      b_vol_clustering),
    F("stat_trend_range_switch", "statistical", "Trend/range-switch", "Efficiency ratio schakelt tussen trendvolgen en fade.",
      ["regime switching"], b_trend_range_switch, iq=[("34_tom_balance_vs_trend", "tb_switch")]),
    F("stat_momentum_exhaustion", "statistical", "Momentumuitputting", "Na een zeer efficiënte trend een rejection-candle tegen de trend.",
      ["exhaustion"], b_momentum_exhaustion),
    F("stat_large_move_reversal", "statistical", "Grote-beweging reversal",
      "Rendement-z-score extreem (staart van de verdeling) -> fade met tijd-exit.", ["return distribution"], b_large_move),
    # vwap / volume
    F("vwap_pullback", "vwap_volume", "VWAP-pullback", "In EMA-trend raakt de koers de VWAP en sluit aan de trendkant.",
      ["vwap", "pullback"], b_vwap_pullback, iq=[("23_robert_3vwap_bookmap", "rv_vwap_pullback")], intraday=True),
    F("vwap_breakout", "vwap_volume", "VWAP-breakout", "Slot kruist de VWAP met VWAP-helling in dezelfde richting, in sessie.",
      ["vwap", "breakout"], b_vwap_breakout, iq=[("04_matteo_vault_break", "vault_vwap_confirm")], intraday=True,
      exits=["atr_r", "atr_r_be", "session_close", "swing_r"]),
    F("vwap_deviation", "vwap_volume", "VWAP-afwijking (momentum)", "Afwijking van VWAP in ATR kruist omhoog/omlaag: momentum volgen.",
      ["vwap", "momentum"], b_vwap_dev_momentum, intraday=True),
    F("volume_contraction", "vwap_volume", "Volumecontractie (proxy)", "Laag relatief tickvolume gevolgd door doorbraak.",
      ["volume proxy", "breakout"], b_volume_contraction, iq=[("01_bruni_low_effort_value_area", "bruni_low_volume")]),
    F("volume_expansion", "vwap_volume", "Volume-expansie (proxy)", "Hoog relatief tickvolume met richting-candle en nieuwe high/low.",
      ["volume proxy", "momentum"], b_volume_expansion),
    F("volume_effort_result", "vwap_volume", "Effort vs result (proxy)",
      "Range (resultaat) vs tickvolume (inspanning): voortzetting of reversal.", ["volume proxy", "effort result"],
      b_effort_result, iq=[("09_pietro_effort_result", "er_relative_range"), ("09_pietro_effort_result", "er_relative_volume"),
                           ("09_pietro_effort_result", "er_fixed_horizon"), ("01_bruni_low_effort_value_area", "bruni_low_volume")]),
    # hybrid
    F("hybrid_orb_adx_atr", "hybrid", "ORB + ADX + ATR-stop", "Opening range break met ADX-filter, max 1 trade per dag.",
      ["opening range", "trend strength"], b_orb_adx_atr, symbols=_IDX_GOLD, intraday=True,
      iq=[("06_matteo_overnight_bias_orb", "onb_orb15_break"), ("06_matteo_overnight_bias_orb", "onb_adx20"),
          ("06_matteo_overnight_bias_orb", "onb_max1"), ("06_matteo_overnight_bias_orb", "onb_stop_session_atr"),
          ("06_matteo_overnight_bias_orb", "onb_exit_1430")]),
    F("hybrid_vwap_pullback_trend", "hybrid", "VWAP-drift pullback",
      "Koers boven stijgende VWAP met positieve 60-min drift; instap op tegenkleur-candle in NY-sessie.",
      ["vwap", "drift", "pullback"], b_vwap_pullback_trend, symbols=_IDX_GOLD, intraday=True,
      iq=[("03_matteo_drift_vwap", "drift_close_vs_vwap"), ("03_matteo_drift_vwap", "drift_vwap_slope"),
          ("03_matteo_drift_vwap", "drift_60m_return"), ("03_matteo_drift_vwap", "drift_counter_candle"),
          ("03_matteo_drift_vwap", "drift_entry_window"), ("03_matteo_drift_vwap", "drift_max_trades")]),
    F("hybrid_sweep_mss", "hybrid", "Sweep + market structure shift",
      "Recente nieuwe low/high buiten de range (sweep), daarna MSS in de andere richting.", ["liquidity", "market structure"],
      b_sweep_mss, iq=[("14_candace_turtle_soup", "ts_sweep_reclaim"), ("14_candace_turtle_soup", "ts_structure_confirm"),
                       ("15_candace_ict_2022", "ict_displacement")]),
    F("hybrid_range_break_volc", "hybrid", "Range-breakout + volatiliteitscontractie",
      "Smalle box en dalende realized vol, gevolgd door doorbraak.", ["volatility contraction", "breakout"], b_range_break_volc,
      iq=[("35_trader_x_nr4_breakout", "tx_vol_condition")]),
    F("hybrid_momentum_regime", "hybrid", "Momentum + regimefilter", "ROC kruist nul met ADX- en hogere-timeframe trendfilter.",
      ["momentum", "regime"], b_momentum_regime, iq=[("11_imre_one_time_framing", "otf_daily_state")]),
    F("hybrid_stat_mr_session", "hybrid", "Statistische mean reversion + sessiefilter",
      "Z-score reversal bij lage ADX, alleen binnen een sessievenster; doel het gemiddelde.", ["mean reversion", "session"],
      b_stat_mr_session, intraday=True, exits=["atr_level", "atr_r_time", "session_close"]),
]
_BY_ID = {f["id"]: f for f in FAMILIES}

GROUP_EXITS = {
    "trend": ["atr_r", "swing_r", "atr_trail", "atr_r_be", "pct_r"],
    "breakout": ["atr_r", "swing_r", "pct_r", "atr_trail", "atr_r_be"],
    "mean_reversion": ["atr_level", "atr_r_time", "atr_r", "swing_r"],
    "price_action": ["swing_r", "atr_r", "atr_r_be", "atr_level", "atr_r_time"],
    "statistical": ["atr_r_time", "atr_r", "atr_trail"],
    "vwap_volume": ["atr_r", "atr_level", "atr_r_be", "atr_r_time"],
    "hybrid": ["atr_r", "atr_r_be", "atr_trail", "session_close"],
}


# --------------------------------------------------------------------------------------------- exit architecture

def _apply_exit(spec: dict, profile: str, ctx: Ctx, built: dict) -> tuple[list, list]:
    """Set stop/target/trailing/break-even/time exit for `profile`; returns (blocks, notes)."""
    blocks, notes = [], []
    lt = built.get("level_target")
    if profile.endswith("level") and lt is None:
        profile = "atr_r"
    stop_kind = {"atr": "atr", "swing": "swing", "pct": "percent"}.get(profile.split("_")[0], "atr")
    if profile in ("atr_atr", "atr_target_trail", "atr_level", "session_close", "atr_r", "atr_r_be", "atr_r_time",
                   "atr_trail") or stop_kind == "atr":
        spec["stop"] = {"type": "atr", "mult": ctx.p("sl_atr", "float", 0.75, 4.0, 0.25, 2.0), "period": 14}
        blocks.append("stop:atr")
    if stop_kind == "swing":
        spec["stop"] = {"type": "swing", "lookback": ctx.p("sl_swing", "int", 2, 30, 1, 10)}
        blocks.append("stop:swing")
    elif stop_kind == "percent":
        spec["stop"] = {"type": "percent", "value": ctx.p("sl_pct", "float", 0.1, 1.5, 0.05, 0.3)}
        blocks.append("stop:fixed_percent")
    if profile in ("atr_r", "swing_r", "pct_r", "atr_r_be", "atr_r_time", "session_close", "pct_time"):
        spec["target"] = {"type": "r_multiple", "value": ctx.p("tp_r", "float", 1.0, 4.0, 0.25, 2.0)}
        blocks.append("target:r_multiple")
    elif profile == "atr_atr":
        spec["target"] = {"type": "atr", "mult": ctx.p("tp_atr", "float", 1.0, 5.0, 0.25, 2.0), "period": 14}
        blocks.append("target:atr")
    elif profile in ("atr_level", "swing_level"):
        spec["target"] = {"type": "level", "operand": copy.deepcopy(lt)}
        blocks.append("target:level")
    elif profile == "atr_target_trail":
        spec["target"] = {"type": "atr", "mult": ctx.p("tp_atr", "float", 2.0, 6.0, 0.5, 5.0), "period": 20}
        spec["trailing"] = {"activate_r": ctx.p("trail_activate_r", "float", 0.0, 2.0, 0.25, 0.0),
                            "atr_mult": ctx.p("trail_atr", "float", 2.0, 5.0, 0.5, 4.0), "period": 20}
        blocks += ["target:atr", "exit:trailing"]
    if profile == "atr_trail":
        spec["target"] = None
        spec["trailing"] = {"activate_r": ctx.p("trail_activate_r", "float", 0.0, 2.0, 0.25, 1.0),
                            "atr_mult": ctx.p("trail_atr", "float", 1.0, 4.0, 0.25, 2.5), "period": 14}
        blocks += ["target:none", "exit:trailing"]
    if profile == "atr_r_be":
        spec["break_even"] = {"activate_r": ctx.p("be_r", "float", 0.5, 1.5, 0.25, 1.0)}
        blocks.append("exit:break_even")
    if profile in ("atr_r_time", "pct_time"):
        spec["time_exit_bars"] = ctx.p("time_exit", "int", 2, 48, 2, 12)
        blocks.append("exit:time")
    if profile == "session_close":
        sess = spec.get("session")
        if sess:
            bars = max(1, _session_minutes(sess) * 60_000 // engine.TF_MS[spec["timeframe"]])
            spec["time_exit_bars"] = int(bars)
            blocks.append("exit:session_close_approx")
            notes.append(SESSION_EXIT_NOTE)
        else:
            spec["time_exit_bars"] = ctx.p("time_exit", "int", 4, 48, 4, 16)
            blocks.append("exit:time")
    return blocks, notes


def _overlays(spec: dict, ctx: Ctx, fam: dict, built: dict) -> list:
    blocks = []
    if spec.get("max_trades_per_day") is None and ctx.rng.random() < 0.3:
        spec["max_trades_per_day"] = ctx.rng.choice([1, 2, 3])
        blocks.append("risk:max_trades_per_day")
        ctx.changes.append({"what": "max_trades_per_day", "from": None, "to": spec["max_trades_per_day"]})
    elif spec.get("max_trades_per_day") is not None:
        blocks.append("risk:max_trades_per_day")
    if ctx.rng.random() < 0.3:
        spec["cooldown_bars"] = ctx.p("cooldown", "int", 1, 12, 1, 3)
        blocks.append("risk:cooldown")
    tf_ms = engine.TF_MS[spec["timeframe"]]
    if (fam["group"] in ("trend", "breakout", "price_action") and ctx.htf and "htf" not in json.dumps(built.get("blocks"))
            and ctx.rng.random() < 0.3):
        htf = ctx.htf
        for side in list(spec["entry"]):
            op = ">" if side == "long" else "<"
            spec["entry"][side] = ALL(spec["entry"][side], cmp(ind("sma", 1, timeframe=htf), op, ind("ema", 50, timeframe=htf)))
        blocks.append(f"filter:htf_ema@{htf}")
        ctx.changes.append({"what": "filter", "from": None, "to": f"htf_ema50@{htf}"})
    if spec.get("session") is None and tf_ms <= INTRADAY_MAX_MS and not fam["intraday"] and ctx.rng.random() < 0.25:
        home = ctx.rng.choice(home_sessions(ctx.symbol))
        spec["session"] = _session(home, 0, 360)
        blocks.append(f"session:{home['id']}")
        ctx.changes.append({"what": "session", "from": None, "to": home["id"]})
    return blocks


# --------------------------------------------------------------------------------------------- components

@lru_cache(maxsize=1)
def load_components() -> dict:
    return json.loads(COMPONENTS_PATH.read_text(encoding="utf-8"))


def _component(dossier: str, cid: str) -> tuple[dict, dict]:
    for d in load_components()["dossiers"]:
        if d["dossier"] == dossier:
            for c in d["components"]:
                if c["id"] == cid:
                    return d, c
    raise KeyError(f"component {dossier}/{cid} not in iq_components.json")


def _iq_sources(pairs) -> list[dict]:
    out = []
    for dossier, cid in pairs:
        d, c = _component(dossier, cid)
        note = f"Geïnspireerd op {d['trader']} ({d['title']}): {c['text_nl']}"
        if c.get("reason_nl"):
            note += f" [{c['provenance']}: {c['reason_nl']}]"
        out.append({"dossier": dossier, "component": cid, "provenance": c["provenance"], "note_nl": note})
    return out


# --------------------------------------------------------------------------------------------- normalization

def _strip(node):
    if isinstance(node, dict):
        if set(node) == {"param"}:
            return "#"
        return {k: _strip(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_strip(v) for v in node]
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        return "#"
    return node


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _structure(spec: dict) -> dict:
    body = {k: v for k, v in spec.items() if k not in ("name", "description", "params", "risk")}
    return _strip(body)


def signature(candidate_or_spec: dict, family: str | None = None) -> str:
    """Dedup key: family + symbol + timeframe + spec structure with every number / parameter value removed."""
    spec = candidate_or_spec.get("spec", candidate_or_spec)
    fam = family if family is not None else candidate_or_spec.get("family", "")
    return hashlib.sha256(_canon({"family": fam, "symbol": spec["symbol"], "timeframe": spec["timeframe"],
                                  "structure": _structure(spec)}).encode()).hexdigest()[:20]


def candidate_id(spec: dict, family: str) -> str:
    body = {k: v for k, v in spec.items() if k not in ("name", "description")}
    return hashlib.sha256(_canon({"family": family, "spec": body}).encode()).hexdigest()[:16]


def _leaves(cond) -> int:
    if not isinstance(cond, dict):
        return 0
    for k in ("all", "any"):
        if k in cond:
            return sum(_leaves(c) for c in cond[k])
    if "not" in cond:
        return _leaves(cond["not"])
    return 1


def complexity(spec: dict) -> int:
    """Leaf entry conditions (largest side) + tunable params + active exit/risk features. Not rewarded anywhere."""
    leaves = max((_leaves(spec["entry"].get(s)) for s in ("long", "short") if s in spec["entry"]), default=0)
    feats = sum(1 for k in ("trailing", "break_even", "time_exit_bars", "max_trades_per_day", "session", "scale_in")
                if spec.get(k))
    feats += int(bool(spec.get("cooldown_bars"))) + int((spec.get("target") or {}).get("type") == "level")
    feats += int((spec.get("order") or {}).get("type", "market") != "market")
    return int(leaves + len(spec.get("params") or []) + feats)


def _auto_proxies(spec: dict) -> list[str]:
    text = json.dumps(spec)
    out = []
    if '"vwap' in text:
        out.append(VWAP_NOTE)
    if "volume_ratio" in text or '"source": "volume"' in text:
        out.append(VOLUME_NOTE)
    if any(k in text for k in ("liquidity_sweep", "range_touch", "mss_", "fvg_", "crt_", "swing_break")):
        out.append(SWEEP_NOTE)
    if '"hurst"' in text:
        out.append(HURST_NOTE)
    return out


# --------------------------------------------------------------------------------------------- catalogue / build

def families_catalog() -> list[dict]:
    out = []
    for f in FAMILIES:
        out.append({"id": f["id"], "group": f["group"], "label_nl": f["label_nl"], "description_nl": f["description_nl"],
                    "mechanisms": list(f["mechanisms"]), "suitable_symbols": f["suitable_symbols"],
                    "iq_sources": sorted({d for d, _ in f["iq"]}), "default_timeframes": list(f["default_timeframes"])})
    return out


def _suitable(fam: dict, symbol: str, timeframe: str) -> bool:
    if fam["suitable_symbols"] != "all" and symbol.upper() not in fam["suitable_symbols"]:
        return False
    if fam["intraday"] and engine.TF_MS.get(timeframe, 0) > INTRADAY_MAX_MS:
        return False
    return timeframe in engine.TF_MS


def build_candidate(family_id: str, symbol: str, timeframe: str, rng: random.Random, *,
                    allow_scale_in: bool = False) -> dict:
    """One candidate of `family_id` (random structural variant, exit profile, sampled parameter defaults)."""
    fam = _BY_ID[family_id]
    ctx = Ctx(symbol, timeframe, rng)
    built = fam["builder"](ctx)
    direction = built.get("direction", "both")
    spec = {"schema": SCHEMA, "name": "tmp", "symbol": symbol, "timeframe": timeframe, "direction": direction,
            "entry": {}, "order": built.get("order") or {"type": "market"}, "stop": None, "target": None,
            "trailing": None, "break_even": None, "time_exit_bars": None, "cooldown_bars": 0,
            "max_trades_per_day": built.get("max_trades_per_day"), "session": built.get("session"),
            "risk": {"percent": RISK_PERCENT}, "params": None}
    for side in ("long", "short"):
        if direction in (side, "both"):
            spec["entry"][side] = copy.deepcopy(built[side])
    exits = built.get("exits") or fam["exits"] or GROUP_EXITS[fam["group"]]
    if built.get("level_target") is None:
        exits = [e for e in exits if not e.endswith("level")] or ["atr_r"]
    if not spec.get("session"):
        exits = [e for e in exits if e != "session_close"] or ["atr_r"]
    profile = ctx.choice("exit_profile", exits)
    blocks = list(built.get("blocks") or [])
    eb, notes = _apply_exit(spec, profile, ctx, built)
    blocks += eb
    blocks += _overlays(spec, ctx, fam, built)
    iq_pairs = list(fam["iq"])
    if allow_scale_in and fam["group"] in ("trend", "breakout", "hybrid") and rng.random() < 0.25:
        spec["scale_in"] = {"max_adds": ctx.p("max_adds", "int", 1, 3, 1, 2),
                            "add_every_r": ctx.p("add_every_r", "float", 0.5, 2.0, 0.25, 1.0), "add_size_fraction": 0.5}
        blocks.append("risk:scale_in_winners_only")
        iq_pairs.append(("45_marco_loading_the_boat", "lb_winner_adds"))
        iq_pairs.append(("45_marco_loading_the_boat", "lb_risk_cap"))
    spec["params"] = ctx.params
    spec = {k: v for k, v in spec.items() if not (k in ("trailing", "break_even", "time_exit_bars", "max_trades_per_day",
                                                       "session") and v is None)}
    cid = candidate_id(spec, family_id)
    iq = _iq_sources(iq_pairs)
    inspired = sorted({d for d, _ in iq_pairs})
    traders = sorted({_component(d, c)[0]["trader"] for d, c in iq_pairs})
    insp_text = (f" Geïnspireerd op {', '.join(traders)} (IQ Capital dossiers {', '.join(inspired)}), eigen "
                 f"onderzoeksvariant; GEEN exacte reconstructie. {CFD_NOTE}") if inspired else ""
    spec["name"] = f"Gen {family_id} {symbol} {timeframe} {cid[:6]}"
    spec["description"] = (f"{fam['label_nl']}: {fam['description_nl']}" + insp_text)[:600]
    proxies = []
    for p_ in list(built.get("proxies") or []) + notes + _auto_proxies(spec):
        if p_ not in proxies:
            proxies.append(p_)
    if inspired and CFD_NOTE not in proxies:
        proxies.append(CFD_NOTE)
    hyp = f"{fam['hypothesis_nl']} Getest op {symbol} {timeframe} met exitprofiel '{profile}'."
    if inspired:
        hyp += f" Geïnspireerd op {', '.join(traders)}, eigen onderzoeksvariant (geen exacte reconstructie)."
    cand = {
        "id": cid, "name": spec["name"], "symbol": symbol, "timeframe": timeframe, "family": family_id,
        "group": fam["group"], "spec": spec,
        "param_space": {p["name"]: {"type": p["type"], "min": p["min"], "max": p["max"], "step": p["step"]}
                        for p in ctx.params},
        "blocks": blocks, "iq_sources": iq,
        "passport": {"base_template": f"family:{family_id}:{built.get('variant', 'base')}", "changes": [],
                     "exit_profile": profile},
        "hypothesis_nl": hyp, "rules_nl": engine.describe(spec), "complexity": complexity(spec),
        "signature": "", "proxies_nl": proxies, "allow_scale_in": bool(allow_scale_in),
    }
    cand["passport"]["changes"] = ([{"what": "exit_profile", "from": exits[0], "to": profile}] if profile != exits[0]
                                   else []) + [c for c in ctx.changes if c["what"] != "exit_profile"]
    cand["signature"] = signature(cand)
    return cand


def _interleave(fams: list[dict]) -> list[dict]:
    by_group: dict = {}
    for f in fams:
        by_group.setdefault(f["group"], []).append(f)
    order, i = [], 0
    while any(by_group.values()):
        for g in GROUPS:
            if by_group.get(g):
                order.append(by_group[g].pop(0))
        i += 1
    return order


def generate(config: dict, seed: int) -> list[dict]:
    """Bounded, deduplicated, diverse candidate list.

    config: symbols, timeframes, families ('all' or ids), max_candidates, diversity (round robin over groups and
    families with a per-family cap), allow_scale_in (default False, winner-only adds).
    """
    symbols = list(config.get("symbols") or ["XAUUSD"])
    timeframes = list(config.get("timeframes") or ["M15"])
    req = config.get("families", "all")
    fams = list(FAMILIES) if req in (None, "all") else [_BY_ID[f] for f in req]
    cap_total = max(0, int(config.get("max_candidates", 50)))
    diversity = bool(config.get("diversity", True))
    allow_scale_in = bool(config.get("allow_scale_in", False))
    markets = [(s, t) for t in timeframes for s in symbols]
    if not fams or not markets or cap_total == 0:
        return []
    order = _interleave(fams) if diversity else fams
    per_family_cap = max(1, math.ceil(cap_total / len(order) * 1.5)) if diversity else cap_total
    count = {f["id"]: 0 for f in order}
    turn = {f["id"]: 0 for f in order}
    exhausted: set = set()
    seen: set = set()
    out: list[dict] = []

    def attempt(fam) -> dict | None:
        fid = fam["id"]
        for _ in range(len(markets)):
            k = turn[fid]
            turn[fid] += 1
            sym, tf = markets[(k + order.index(fam)) % len(markets)]
            if not _suitable(fam, sym, tf):
                continue
            for a in range(MAX_ATTEMPTS):
                rng = random.Random(f"{seed}|{fid}|{sym}|{tf}|{k}|{a}")
                cand = build_candidate(fid, sym, tf, rng, allow_scale_in=allow_scale_in)
                if cand["signature"] in seen or engine.validate_spec(cand["spec"]):
                    continue
                return cand
        return None

    while len(out) < cap_total and len(exhausted) < len(order):
        progressed = False
        for fam in order:
            if len(out) >= cap_total:
                break
            fid = fam["id"]
            if fid in exhausted:
                continue
            if count[fid] >= per_family_cap:
                exhausted.add(fid)
                continue
            cand = attempt(fam)
            if cand is None:
                exhausted.add(fid)
                continue
            seen.add(cand["signature"])
            out.append(cand)
            count[fid] += 1
            progressed = True
            if not diversity:
                break
        if not progressed and diversity:
            break
    kept, _ = dedup(out)
    return kept


# --------------------------------------------------------------------------------------------- dedup

def _numbers(node, acc):
    if isinstance(node, dict):
        for k in sorted(node):
            _numbers(node[k], acc)
    elif isinstance(node, list):
        for v in node:
            _numbers(v, acc)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        acc.append(float(node))
    return acc


def dedup(candidates, existing_signatures=frozenset()):
    """Drop exact structural duplicates (same signature) and near-duplicates (same symbol/timeframe/structure across
    families with every resolved number within 10%). Returns (kept, dropped_with_reason)."""
    kept, dropped = [], []
    sigs = {s: None for s in (existing_signatures or ())}
    by_struct: dict = {}
    for c in candidates:
        sig = c.get("signature") or signature(c)
        if sig in sigs:
            dropped.append({"id": c["id"], "family": c.get("family"), "signature": sig, "duplicate_of": sigs[sig],
                            "reason": "duplicaat: identieke structuur (signature) " +
                                      ("in bestaande set" if sigs[sig] is None else f"als {sigs[sig]}")})
            continue
        spec = c["spec"]
        skey = _canon({"s": spec["symbol"], "t": spec["timeframe"], "x": _structure(spec)})
        nums = _numbers(engine.resolve(spec), [])
        near = None
        for other_id, other_nums in by_struct.get(skey, []):
            if len(other_nums) == len(nums) and all(
                    abs(a - b) <= NEAR_DUP_REL * max(abs(a), abs(b), 1e-12) for a, b in zip(nums, other_nums)):
                near = other_id
                break
        if near:
            dropped.append({"id": c["id"], "family": c.get("family"), "signature": sig, "duplicate_of": near,
                            "reason": f"bijna-duplicaat van {near}: zelfde structuur, alle waarden binnen 10%"})
            continue
        sigs[sig] = c["id"]
        by_struct.setdefault(skey, []).append((c["id"], nums))
        kept.append(c)
    return kept, dropped


# --------------------------------------------------------------------------------------------- baselines

def _baseline(family, label, spec, hyp, proxies=()):
    spec["name"] = f"Baseline {family} {spec['symbol']} {spec['timeframe']}"
    spec["description"] = label
    cid = candidate_id(spec, family)
    cand = {"id": cid, "name": spec["name"], "symbol": spec["symbol"], "timeframe": spec["timeframe"],
            "family": family, "group": "baseline", "spec": spec,
            "param_space": {p["name"]: {"type": p["type"], "min": p["min"], "max": p["max"], "step": p.get("step")}
                            for p in spec.get("params") or []},
            "blocks": [f"baseline:{family}"], "iq_sources": [],
            "passport": {"base_template": f"baseline:{family}", "changes": []}, "hypothesis_nl": hyp,
            "rules_nl": engine.describe(spec), "complexity": complexity(spec), "signature": "",
            "proxies_nl": list(proxies), "allow_scale_in": False}
    cand["signature"] = signature(cand)
    return cand


def _exit_part(exits_from: dict | None) -> dict:
    keys = ("stop", "target", "trailing", "break_even", "time_exit_bars", "cooldown_bars", "max_trades_per_day",
            "session")
    if exits_from:
        src = exits_from.get("spec", exits_from)
        rs = engine.resolve(src)
        return {k: copy.deepcopy(rs.get(k)) for k in keys if rs.get(k) is not None}
    return {"stop": {"type": "atr", "mult": 2.0, "period": 14}, "target": {"type": "r_multiple", "value": 2.0}}


def baselines(symbol: str, timeframe: str, exits_from: dict | None = None) -> list[dict]:
    """Comparison baselines: buy&hold proxy, pseudo-random entries with the same exits, plain MA cross."""
    base = {"schema": SCHEMA, "symbol": symbol, "timeframe": timeframe, "order": {"type": "market"},
            "risk": {"percent": RISK_PERCENT}, "params": []}
    bh = {**copy.deepcopy(base), "direction": "long",
          "entry": {"long": cmp(CLOSE, ">", val(0))}, "stop": {"type": "percent", "value": 20.0}, "target": None}
    rnd = {**copy.deepcopy(base), "direction": "both",
           "entry": {"long": ALL(pat("random_entry", seed=11, probability=0.02), UP),
                     "short": ALL(pat("random_entry", seed=11, probability=0.02), DOWN)}, **_exit_part(exits_from)}
    ma = {**copy.deepcopy(base), "direction": "both",
          "entry": {"long": cmp(ind("ema", 20), "crosses_above", ind("ema", 50)),
                    "short": cmp(ind("ema", 20), "crosses_below", ind("ema", 50))},
          "stop": {"type": "atr", "mult": 2.0, "period": 14}, "target": {"type": "r_multiple", "value": 2.0}}
    return [
        _baseline("baseline_buy_hold", "Buy & hold PROXY: altijd long, 20% noodstop, geen target.", bh,
                  "Vergelijkingsbasis: passief long zijn in de markt.",
                  ["Buy & hold is benaderd met een doorlopende long-positie en 20% noodstop (geen echte belegging)."]),
        _baseline("baseline_random_entry", "Pseudo-willekeurige instap (vaste seed; richting = candlekleur) met dezelfde exits.",
                  rnd,
                  "Nulmodel: willekeurige instaptijden met identieke exits; een strategie moet dit verslaan.",
                  ["Willekeur is deterministisch (hash van bartijd + seed), dus reproduceerbaar."]),
        _baseline("baseline_ma_cross", "Eenvoudige EMA(20/50)-kruising, 2 x ATR stop, 2R target.", ma,
                  "Eenvoudige trendvolg-basis."),
    ]


# --------------------------------------------------------------------------------------------- level 1

def _arrays(bid) -> dict:
    from .backtest import _epoch_ms
    clean = bid[["open", "high", "low", "close", "volume"]].dropna(subset=["open", "high", "low", "close"])
    clean = clean[~clean.index.duplicated()]
    return {"time": _epoch_ms(clean.index), **{c: clean[c].to_numpy(float) for c in ("open", "high", "low", "close",
                                                                                     "volume")}}


def _same_prefix(a: dict, b: dict, k: int) -> str | None:
    if not np.array_equal(a["long_signal"][:k + 1], b["long_signal"][:k + 1]):
        return "long-signaal"
    if not np.array_equal(a["short_signal"][:k + 1], b["short_signal"][:k + 1]):
        return "short-signaal"
    for name, ca in a["checks"].items():
        cb = b["checks"].get(name)
        if cb is None or not np.array_equal(ca["state"][:k + 1], cb["state"][:k + 1]):
            return f"conditie '{name}'"
        for side in ("left", "right"):
            if ca[side] is not None and not np.allclose(ca[side][:k + 1], cb[side][:k + 1], equal_nan=True):
                return f"operand {side} van '{name}'"
    for name, x in a["exits"].items():
        if not np.allclose(x[:k + 1], b["exits"][name][:k + 1], equal_nan=True):
            return f"exit-array '{name}'"
    return None


def _uses(spec: dict, names) -> bool:
    text = json.dumps(spec)
    return any(f'"{n}"' in text for n in names)


def level1_check(candidate: dict, bid, ask, *, existing_signatures=frozenset(), run_backtest: bool = True) -> dict:
    """Cheap pre-backtest gate on a short real slice. Every check reports {ok, detail}; ok overall = all ok."""
    checks: dict = {}
    warnings: list[str] = []
    stats: dict = {}
    spec = candidate.get("spec") or {}

    def put(name, ok, detail):
        checks[name] = {"ok": bool(ok), "detail": detail}

    # schema
    errs = engine.validate_spec(spec)
    missing = [k for k in ("id", "family", "group", "spec", "signature", "param_space") if k not in candidate]
    if spec.get("scale_in") and not candidate.get("allow_scale_in"):
        errs = errs + ["scale_in aanwezig zonder expliciete opt-in allow_scale_in"]
    put("schema", not errs and not missing,
        "geldig" if not errs and not missing else "; ".join((errs + [f"ontbrekend veld {m}" for m in missing])[:4]))
    if errs:
        for k in ("data_available", "indicators_defined", "no_lookahead", "reproducible", "no_contradiction"):
            put(k, False, "overgeslagen: spec ongeldig")
        put("not_duplicate", False, "overgeslagen: spec ongeldig")
        return _l1_result(checks, warnings, stats)

    # data available
    n_bid = 0 if bid is None else len(bid)
    arr = _arrays(bid) if n_bid else None
    n = 0 if arr is None else len(arr["time"])
    problems = []
    if n < 300:
        problems.append(f"te weinig Bid-bars ({n} < 300)")
    miss_ask = 1.0
    if ask is not None and n:
        miss_ask = float(ask.reindex(bid.index)["close"].isna().mean())
        if miss_ask > 0.05:
            problems.append(f"{miss_ask:.1%} ontbrekende Ask-candles")
    elif not n or ask is None:
        problems.append("geen Ask-data")
    if n and _uses(spec, engine.VOLUME_INDICATORS + ("volume",)):
        v = arr["volume"]
        cv = float(np.nanstd(v) / np.nanmean(v)) if np.nanmean(v) > 0 else 0.0
        if cv < 0.05:
            problems.append(f"volume-proxy niet informatief (variatiecoefficient {cv:.3f}; Dukascopy-volume is in "
                            f"deze dataset constant per M1-bar)")
    if n and _uses(spec, ("vwap", "vwap_dev_atr")):
        warnings.append("VWAP is tijdgewogen (TWAP) omdat het volume constant is; label PROXY.")
    stats.update(bars=n, missing_ask_fraction=round(miss_ask, 4), lookback_bars=engine.lookback_bars(spec))
    put("data_available", not problems, "; ".join(problems) if problems else f"{n} bars, {miss_ask:.2%} ontbrekende Ask")
    if n < 50:
        for k in ("indicators_defined", "no_lookahead", "reproducible", "no_contradiction"):
            put(k, False, "overgeslagen: geen bruikbare data")
        _dup_check(candidate, spec, existing_signatures, put)
        return _l1_result(checks, warnings, stats)

    # evaluate
    try:
        res = engine.evaluate(spec, arr)
    except Exception as exc:  # runtime failure of a valid spec is itself a level-1 reject
        for k in ("indicators_defined", "no_lookahead", "reproducible", "no_contradiction"):
            put(k, False, f"evaluatie mislukt: {type(exc).__name__}: {exc}")
        _dup_check(candidate, spec, existing_signatures, put)
        return _l1_result(checks, warnings, stats)

    # indicators defined
    undefined = [name for name, c in res["checks"].items() if name != "session" and not (c["state"] != -1).any()]
    undefined += [f"indicator {k}" for k, x in res["indicators"].items() if not np.isfinite(x).any()]
    undefined += [f"exit {k}" for k, x in res["exits"].items() if not np.isfinite(x).any()]
    put("indicators_defined", not undefined,
        "alle condities/indicatoren krijgen waarden" if not undefined else
        "nooit gedefinieerd in slice (warm-up te lang of data ontbreekt): " + ", ".join(undefined[:4]))

    # no lookahead: prefix truncation + future mutation
    leak = None
    cuts = sorted({max(30, n // 3), n // 2 + 1, (2 * n) // 3, n - 2})
    rng = np.random.default_rng(20260101)
    try:
        for k in cuts:
            pre = engine.evaluate(spec, {key: v[:k + 1] for key, v in arr.items()})
            diff = _same_prefix(_pad(pre, n), res, k)
            if diff:
                leak = f"truncatie op bar {k}: {diff} verschilt"
                break
            mut = {key: v.copy() for key, v in arr.items()}
            med = float(np.nanmedian(arr["close"]))
            o = rng.uniform(med * 0.8, med * 1.2, n - k - 1)
            c = rng.uniform(med * 0.8, med * 1.2, n - k - 1)
            mut["open"][k + 1:], mut["close"][k + 1:] = o, c
            mut["high"][k + 1:] = np.maximum(o, c) * 1.001
            mut["low"][k + 1:] = np.minimum(o, c) * 0.999
            mut["volume"][k + 1:] = rng.uniform(1, 1000, n - k - 1)
            diff = _same_prefix(engine.evaluate(spec, mut), res, k)
            if diff:
                leak = f"toekomstmutatie na bar {k}: {diff} verschilt"
                break
    except Exception as exc:
        leak = f"prefixtest mislukt: {type(exc).__name__}: {exc}"
    put("no_lookahead", leak is None, f"prefix/mutatie op {len(cuts)} punten identiek" if leak is None else leak)

    # reproducible
    rep = []
    res2 = engine.evaluate(spec, arr)
    if not (np.array_equal(res["long_signal"], res2["long_signal"]) and
            np.array_equal(res["short_signal"], res2["short_signal"])):
        rep.append("twee evaluaties verschillen")
    if candidate.get("id") and candidate.get("family") and candidate_id(spec, candidate["family"]) != candidate["id"]:
        rep.append("id komt niet overeen met spec-hash")
    if run_backtest and ask is not None:
        from .backtest import run_rules_backtest
        try:
            b1 = run_rules_backtest(spec, bid, ask)
            b2 = run_rules_backtest(spec, bid, ask)
            if _canon(b1["trades"]) != _canon(b2["trades"]):
                rep.append("twee backtests geven andere trades")
            stats["trades"] = len(b1["trades"])
            stats["conflicting_signals"] = b1["diagnostics"]["conflicting_signals"]
        except Exception as exc:
            rep.append(f"backtest mislukt: {type(exc).__name__}: {exc}")
    put("reproducible", not rep, "evaluatie en backtest deterministisch" if not rep else "; ".join(rep))

    # contradictions
    con = []
    ls, ss = res["long_signal"], res["short_signal"]
    both = int((ls & ss).sum())
    stats.update(long_signals=int(ls.sum()), short_signals=int(ss.sum()))
    if both:
        con.append(f"long en short signaal op dezelfde bar ({both}x)")
    rs = res["spec"]
    if rs["direction"] == "both" and _canon(rs["entry"].get("long")) == _canon(rs["entry"].get("short")):
        con.append("long- en short-entry zijn identiek")
    allowed = int(res["session_ok"].sum())
    frac = float((ls | ss).sum() / allowed) if allowed else 0.0
    if frac > 0.6:
        msg = f"entry vrijwel altijd waar ({frac:.0%} van de handelbare bars; degeneratie)"
        if candidate.get("group") == "baseline" or rs.get("max_trades_per_day"):
            warnings.append(msg + " - toegestaan: baseline of begrensd door max_trades_per_day")
        else:
            con.append(msg)
    tg = rs.get("target")
    if tg and tg["type"] == "level" and (ls.any() or ss.any()):
        lv = res["exits"]["target_level"]
        good = np.concatenate((lv[ls] > arr["close"][ls], lv[ss] < arr["close"][ss]))
        if good.size and not good.any():
            con.append("level-target ligt nooit aan de winstkant van de signaalprijs")
        elif good.size and good.mean() < 0.5:
            warnings.append(f"level-target slechts in {good.mean():.0%} van de signalen aan de winstkant")
    if rs["stop"]["type"] == "swing" and rs.get("target") and rs["target"]["type"] == "r_multiple" \
            and rs["target"]["value"] <= 0:
        con.append("target <= 0R")
    put("no_contradiction", not con, "geen tegenstrijdigheden" if not con else "; ".join(con))
    if not (ls.any() or ss.any()):
        warnings.append("geen enkel signaal in deze slice")

    _dup_check(candidate, spec, existing_signatures, put)
    return _l1_result(checks, warnings, stats)


def _pad(res: dict, n: int) -> dict:
    def ext(x):
        if x is None:
            return None
        out = np.full(n, np.nan if x.dtype.kind == "f" else 0, dtype=x.dtype)
        out[:len(x)] = x
        return out
    return {"long_signal": ext(res["long_signal"]), "short_signal": ext(res["short_signal"]),
            "checks": {k: {"state": ext(c["state"]), "left": ext(c["left"]), "right": ext(c["right"])}
                       for k, c in res["checks"].items()},
            "exits": {k: ext(v) for k, v in res["exits"].items()}}


def _dup_check(candidate, spec, existing, put):
    sig = signature(spec, candidate.get("family", ""))
    issues = []
    if candidate.get("signature") and candidate["signature"] != sig:
        issues.append("signature komt niet overeen met de spec")
    if sig in (existing or ()):
        issues.append("signature bestaat al (duplicaat)")
    put("not_duplicate", not issues, "uniek" if not issues else "; ".join(issues))


def _l1_result(checks, warnings, stats) -> dict:
    order = ("schema", "data_available", "indicators_defined", "no_lookahead", "reproducible", "no_contradiction",
             "not_duplicate")
    checks = {k: checks.get(k, {"ok": False, "detail": "niet uitgevoerd"}) for k in order}
    reasons = [f"{k}: {v['detail']}" for k, v in checks.items() if not v["ok"]]
    return {"ok": not reasons, "reasons": reasons, "checks": checks, "warnings": warnings, "stats": stats}


# --------------------------------------------------------------------------------------------- smoke run

def run_smoke(out_path, *, symbols=("XAUUSD", "US30", "DE40"), timeframes=("M15", "H1"), max_candidates=200, seed=7,
              start="2019-01-01", end="2019-03-01", data_manager=None) -> dict:
    """Generate candidates and run level1_check on a real dev slice (read-only data); writes a JSON summary."""
    import time
    from collections import Counter, defaultdict

    from strategy_lab.data_manager import DataManager

    from .backtest import load_bid_ask

    t0 = time.monotonic()
    config = {"symbols": list(symbols), "timeframes": list(timeframes), "families": "all",
              "max_candidates": max_candidates, "diversity": True}
    cands = generate(config, seed)
    t_gen = time.monotonic() - t0
    dm = data_manager or DataManager()
    frames, slices = {}, {}
    rows, seen = [], set()
    per_family = defaultdict(lambda: {"generated": 0, "level1_ok": 0, "rejected": 0})
    per_group = defaultdict(lambda: {"generated": 0, "level1_ok": 0, "rejected": 0})
    reason_counts, warn_counts = Counter(), Counter()
    for c in cands:
        key = (c["symbol"], c["timeframe"])
        if key not in frames:
            bid, ask, manifest = load_bid_ask(dm, c["symbol"], c["timeframe"], start, end)
            frames[key] = (bid, ask)
            slices[f"{key[0]} {key[1]}"] = {"bars": len(bid), "first": str(bid.index[0]) if len(bid) else None,
                                            "last": str(bid.index[-1]) if len(bid) else None}
        r = level1_check(c, *frames[key], existing_signatures=seen)
        seen.add(c["signature"])
        for d in (per_family[c["family"]], per_group[c["group"]]):
            d["generated"] += 1
            d["level1_ok" if r["ok"] else "rejected"] += 1
        for reason in r["reasons"]:
            reason_counts[reason.split(":")[0]] += 1
        for w in r["warnings"]:
            warn_counts[w.split(";")[0][:80]] += 1
        rows.append({"id": c["id"], "name": c["name"], "family": c["family"], "group": c["group"], "symbol": c["symbol"],
                     "timeframe": c["timeframe"], "complexity": c["complexity"], "signature": c["signature"],
                     "exit_profile": c["passport"].get("exit_profile"), "iq_dossiers": sorted({s["dossier"] for s in
                                                                                             c["iq_sources"]}),
                     "level1_ok": r["ok"], "reasons": r["reasons"], "warnings": r["warnings"], "stats": r["stats"]})
    base_rows = []
    for sym in symbols:
        for tf in timeframes:
            for b in baselines(sym, tf):
                r = level1_check(b, *frames[(sym, tf)])
                base_rows.append({"id": b["id"], "family": b["family"], "symbol": sym, "timeframe": tf,
                                  "level1_ok": r["ok"], "reasons": r["reasons"]})
    comps = load_components()
    summary = {
        "generated_at_utc": pd_now(), "seed": seed, "config": config, "data_slice": {"start": start, "end": end,
                                                                                      "slices": slices,
                                                                                      "source": "Dukascopy Bid/Ask via DataManager (read-only)"},
        "families_in_catalog": len(FAMILIES), "candidates": len(cands),
        "level1_ok": sum(r["level1_ok"] for r in rows), "level1_rejected": sum(not r["level1_ok"] for r in rows),
        "per_group": dict(sorted(per_group.items())), "per_family": dict(sorted(per_family.items())),
        "reject_reason_counts": dict(reason_counts.most_common()), "warning_counts": dict(warn_counts.most_common()),
        "rejects": [r for r in rows if not r["level1_ok"]], "candidates_detail": rows, "baselines": base_rows,
        "iq_components_totals": comps["totals"],
        "complexity": {"min": min(r["complexity"] for r in rows), "median": float(np.median([r["complexity"] for r in rows])),
                       "max": max(r["complexity"] for r in rows)},
        "seconds": {"generate": round(t_gen, 2), "total": round(time.monotonic() - t0, 2)},
        "notes_nl": ["Level 1 is een goedkope poort (schema, data, warm-up, no-lookahead prefix/mutatie, reproduceerbaarheid, "
                     "tegenstrijdigheden, duplicaten); het zegt NIETS over winstgevendheid.",
                     "Dukascopy-volume is in deze dataset constant 1 per M1-bar: volume-proxyfamilies worden daarom op "
                     "data_available afgewezen; VWAP is feitelijk TWAP."],
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return summary


def pd_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "generator_smoke.json"
    s = run_smoke(out)
    print(json.dumps({k: s[k] for k in ("candidates", "level1_ok", "level1_rejected", "per_group",
                                        "reject_reason_counts", "seconds")}, indent=1, ensure_ascii=False))
