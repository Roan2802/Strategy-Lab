"""Pure-numpy, causal rule evaluator for JSON rule specs (schema "strategy_lab.rules/1").

Depends only on numpy and the standard library so the very same module runs
inside Jesse (`from strategy_lab.rule_engine import ...`) and in the host app
(`from runtime.strategy_lab.rule_engine import ...`).

Causality contract
------------------
* Every signal at base-bar index i is computed from bars 0..i only (signals are
  evaluated at the bar CLOSE). Shifts must be >= 0.
* Higher-timeframe (HTF) operands: base bars are bucketed by
  ``floor(open_ms / htf_ms)`` (UTC-anchored). An HTF bar is exposed at base bar i
  only when ``htf_bar_end <= open_ms[i] + base_ms``, i.e. the HTF bar is fully
  closed at the close of bar i. ``shift`` on an HTF operand counts HTF bars.
* NaN warm-up -> condition state SKIP (-1); a SKIP never produces a signal.

Condition states are int8 arrays: 1 = PASS, 0 = FAIL, -1 = SKIP.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime
from numpy.lib.stride_tricks import sliding_window_view
from zoneinfo import ZoneInfo

import numpy as np

SCHEMA = "strategy_lab.rules/1"
TF_MS = {"M1": 60_000, "M5": 300_000, "M15": 900_000, "M30": 1_800_000,
         "H1": 3_600_000, "H4": 14_400_000, "D1": 86_400_000}
DAY_MS = 86_400_000
# Statistical / structure indicators added for the strategy generator (all trailing-window, causal).
STAT_INDICATORS = ("zscore", "linreg_slope", "hurst", "autocorr", "efficiency_ratio", "ret_zscore", "volume_ratio",
                   "range_atr", "range_width_atr", "swing_high", "swing_low", "range_mid", "vwap_dev_atr",
                   "atr_band_lower", "atr_band_upper")
INDICATORS = ("sma", "ema", "rsi", "atr", "adx", "macd", "macd_signal", "macd_hist", "bb_upper", "bb_mid",
              "bb_lower", "donchian_high", "donchian_low", "roc", "vwap", "realized_vol") + STAT_INDICATORS
PERIOD_REQUIRED = {"sma", "ema", "rsi", "atr", "adx", "bb_upper", "bb_mid", "bb_lower", "donchian_high",
                   "donchian_low", "roc", "realized_vol"} | set(STAT_INDICATORS)
MIN_PERIOD = {"zscore": 2, "linreg_slope": 2, "hurst": 32, "autocorr": 5, "efficiency_ratio": 2, "ret_zscore": 5,
              "volume_ratio": 2, "range_atr": 2, "range_width_atr": 2, "swing_high": 1, "swing_low": 1,
              "range_mid": 1, "vwap_dev_atr": 2, "atr_band_lower": 2, "atr_band_upper": 2}
VOLUME_INDICATORS = ("volume_ratio",)
SOURCES = ("open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4")
PRICES = ("open", "high", "low", "close")
WINDOW_LEVELS = ("window_high", "window_low", "window_mid")
LEVELS = ("prev_day_high", "prev_day_low", "prev_week_high", "prev_week_low", "session_open",
          "opening_range_high", "opening_range_low", "prev_day_close", "day_open") + WINDOW_LEVELS
OPS = (">", "<", ">=", "<=", "crosses_above", "crosses_below")
STRUCTURE_PATTERNS = ("swing_break_high", "swing_break_low", "mss_bull", "mss_bear", "structure_up", "structure_down")
EXTRA_PATTERNS = ("pin_bull", "pin_bear", "crt_bull", "crt_bear", "fvg_bull_retest", "fvg_bear_retest",
                  "range_touch_high", "range_touch_low", "random_entry") + STRUCTURE_PATTERNS
PATTERNS = ("inside_bar", "nr4", "nr7", "bullish_engulfing", "bearish_engulfing", "breakout_high",
            "breakout_low", "liquidity_sweep_high", "liquidity_sweep_low", "fvg_bull", "fvg_bear") + EXTRA_PATTERNS
CALENDAR = ("hour", "weekday")
_PATTERN_DEFAULT_LOOKBACK = {"fvg_bull_retest": 10, "fvg_bear_retest": 10, "range_touch_high": 20,
                             "range_touch_low": 20, **{q: 3 for q in STRUCTURE_PATTERNS}}
TOP_KEYS = {"schema", "name", "description", "symbol", "timeframe", "direction", "entry", "order", "stop",
            "target", "trailing", "break_even", "time_exit_bars", "cooldown_bars", "max_trades_per_day",
            "session", "risk", "params", "scale_in", "partial_exits"}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_ \-]*$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

DEFAULT_ORDER = {"type": "market", "offset_atr": 0.0, "atr_period": 14, "expiry_bars": 3}


# --------------------------------------------------------------------------- utils

def spec_sha256(spec: dict) -> str:
    payload = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _is_int(x) -> bool:
    return (isinstance(x, int) and not isinstance(x, bool)) or (isinstance(x, float) and x.is_integer())


def _hhmm(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


# --------------------------------------------------------------------------- indicators

def _first_valid(x: np.ndarray) -> int:
    idx = np.flatnonzero(~np.isnan(x))
    return int(idx[0]) if len(idx) else -1


def sma(x: np.ndarray, n: int) -> np.ndarray:
    """Simple moving average over the trailing n values (incl. current)."""
    return _roll(x, n, np.mean)


def ema(x: np.ndarray, n: int) -> np.ndarray:
    """EMA, alpha = 2/(n+1), seeded with the SMA of the first n valid values (value at index f+n-1)."""
    x = np.asarray(x, dtype=np.float64)
    out = np.full(len(x), np.nan)
    f = _first_valid(x)
    if f < 0 or len(x) - f < n:
        return out
    alpha = 2.0 / (n + 1.0)
    k = f + n - 1
    prev = float(np.mean(x[f:k + 1]))
    out[k] = prev
    xs = x.tolist()
    res = out.tolist()
    for i in range(k + 1, len(xs)):
        prev = prev + alpha * (xs[i] - prev)
        res[i] = prev
    return np.asarray(res)


def wilder(x: np.ndarray, n: int, first: int) -> np.ndarray:
    """Wilder smoothing: out[first+n-1] = mean(x[first:first+n]); out[i] = (out[i-1]*(n-1)+x[i])/n."""
    x = np.asarray(x, dtype=np.float64)
    out = np.full(len(x), np.nan)
    if first < 0 or len(x) - first < n:
        return out
    k = first + n - 1
    prev = float(np.mean(x[first:k + 1]))
    xs = x.tolist()
    res = out.tolist()
    res[k] = prev
    for i in range(k + 1, len(xs)):
        prev = (prev * (n - 1) + xs[i]) / n
        res[i] = prev
    return np.asarray(res)


def rsi(close: np.ndarray, n: int) -> np.ndarray:
    """Wilder RSI; first value at index n. avg_loss == 0 -> 100 (50 if also no gains)."""
    c = np.asarray(close, dtype=np.float64)
    d = np.diff(c, prepend=np.nan)
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    gain[0] = loss[0] = np.nan
    ag, al = wilder(gain, n, 1), wilder(loss, n, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100.0 - 100.0 / (1.0 + ag / al)
    out = np.where(al == 0, np.where(ag > 0, 100.0, 50.0), out)
    return np.where(np.isnan(ag) | np.isnan(al), np.nan, out)


def true_range(high, low, close) -> np.ndarray:
    h, l, c = (np.asarray(a, dtype=np.float64) for a in (high, low, close))
    pc = np.concatenate(([np.nan], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    tr[0] = np.nan
    return tr


def atr(high, low, close, n: int) -> np.ndarray:
    """Wilder ATR; TR[0] is undefined, first ATR at index n = mean(TR[1..n])."""
    return wilder(true_range(high, low, close), n, 1)


def adx(high, low, close, n: int) -> np.ndarray:
    """Wilder ADX; first value at index 2n-1."""
    h, l = np.asarray(high, dtype=np.float64), np.asarray(low, dtype=np.float64)
    up = np.diff(h, prepend=np.nan)
    down = -np.diff(l, prepend=np.nan)
    pdm = np.where((up > down) & (up > 0), up, 0.0)
    mdm = np.where((down > up) & (down > 0), down, 0.0)
    pdm[0] = mdm[0] = np.nan
    a = atr(h, l, close, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100.0 * wilder(pdm, n, 1) / a
        mdi = 100.0 * wilder(mdm, n, 1) / a
        s = pdi + mdi
        dx = np.where(s == 0, 0.0, 100.0 * np.abs(pdi - mdi) / s)
    dx = np.where(np.isnan(a), np.nan, dx)
    f = _first_valid(dx)
    return wilder(dx, n, f)


def _roll(x: np.ndarray, n: int, fn, **kw) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    out = np.full(len(x), np.nan)
    if 1 <= n <= len(x):
        out[n - 1:] = fn(sliding_window_view(x, n), axis=1, **kw)
    return out


def _shift(x: np.ndarray, s: int) -> np.ndarray:
    if s == 0:
        return x
    out = np.full(len(x), np.nan)
    if s < len(x):
        out[s:] = x[:-s]
    return out


def _roll_corr(x: np.ndarray, y: np.ndarray, n: int) -> np.ndarray:
    """Pearson correlation of x and y over the trailing n pairs (NaN on any NaN or zero variance)."""
    out = np.full(len(x), np.nan)
    if not 2 <= n <= len(x):
        return out
    wx, wy = sliding_window_view(x, n), sliding_window_view(y, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        dx = wx - wx.mean(axis=1, keepdims=True)
        dy = wy - wy.mean(axis=1, keepdims=True)
        den = np.sqrt((dx * dx).sum(axis=1) * (dy * dy).sum(axis=1))
        out[n - 1:] = np.where(den > 0, (dx * dy).sum(axis=1) / den, np.nan)
    return out


def hurst_proxy(close: np.ndarray, n: int) -> np.ndarray:
    """Aggregated-variance Hurst PROXY over the trailing n bars: slope of log Var(k-bar log return) vs log k
    (k = 1, 2, 4, 8) divided by 2. ~0.5 for a random walk; NOT an exact Hurst exponent."""
    logp = np.log(np.asarray(close, dtype=np.float64))
    ks = np.array([1, 2, 4, 8])
    cols = []
    for k in ks:
        rk = logp - _shift(logp, int(k))
        cols.append(_roll(rk, n, np.var))
    v = np.vstack(cols)
    with np.errstate(divide="ignore", invalid="ignore"):
        lv = np.log(v)
    lk = np.log(ks.astype(float))
    lk_c = lk - lk.mean()
    with np.errstate(invalid="ignore"):
        slope = (lk_c[:, None] * (lv - lv.mean(axis=0))).sum(axis=0) / (lk_c ** 2).sum()
    return np.where(np.isfinite(slope), slope / 2.0, np.nan)


def _pivots(x: np.ndarray, k: int, high: bool):
    """Confirmed pivot (swing) points with strength k. A pivot at bar j is known at bar j+k.
    Returns (last, prev): value of the latest and the one-before-latest pivot confirmed at or before each bar."""
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    last, prev = np.full(n, np.nan), np.full(n, np.nan)
    if n < 2 * k + 1:
        return last, prev
    fn = np.max if high else np.min
    left = _shift(_roll(x, k, fn), 1)        # extremes of bars j-k..j-1, indexed at j
    right = _roll(x, k, fn)                   # extremes of bars i-k+1..i, indexed at i (= j+k)
    cand = _shift(x, k)                       # x[j] seen at i = j+k
    left_at_i = _shift(left, k)
    with np.errstate(invalid="ignore"):
        flag = (cand > left_at_i) & (cand >= right) if high else (cand < left_at_i) & (cand <= right)
    flag &= ~np.isnan(cand) & ~np.isnan(left_at_i)
    vals = cand.tolist()
    cur = pv = math.nan
    lo, po = last.tolist(), prev.tolist()
    fl = flag.tolist()
    for i in range(n):
        if fl[i]:
            pv, cur = cur, vals[i]
        lo[i], po[i] = cur, pv
    return np.asarray(lo, dtype=np.float64), np.asarray(po, dtype=np.float64)


def _stat_indicator(name: str, a: dict, period: int, src: np.ndarray, *, mult: float, lag: int,
                    notes: list | None) -> np.ndarray:
    h, l, c = a["high"], a["low"], a["close"]
    if name == "zscore":
        with np.errstate(invalid="ignore", divide="ignore"):
            sd = _roll(src, period, np.std)
            return np.where(sd > 0, (src - sma(src, period)) / sd, np.nan)
    if name == "linreg_slope":
        w = np.arange(period, dtype=np.float64) - (period - 1) / 2.0
        out = np.full(len(src), np.nan)
        if period <= len(src):
            win = sliding_window_view(np.asarray(src, dtype=np.float64), period)
            slope = (win * w).sum(axis=1) / (w * w).sum()
            with np.errstate(invalid="ignore", divide="ignore"):
                out[period - 1:] = slope / win.mean(axis=1) * 100.0
        return out
    if name == "hurst":
        return hurst_proxy(src, period)
    if name == "autocorr":
        r = np.diff(np.log(np.asarray(src, dtype=np.float64)), prepend=np.nan)
        return _roll_corr(r, _shift(r, lag), period)
    if name == "efficiency_ratio":
        path = _roll(np.abs(np.diff(src, prepend=np.nan)), period, np.sum)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(path > 0, np.abs(src - _shift(src, period)) / path, np.nan)
    if name == "ret_zscore":
        r = np.diff(np.log(np.asarray(src, dtype=np.float64)), prepend=np.nan)
        mu, sd = _shift(_roll(r, period, np.mean), 1), _shift(_roll(r, period, np.std), 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(sd > 0, (r - mu) / sd, np.nan)
    if name == "volume_ratio":
        v = np.asarray(a["volume"], dtype=np.float64)
        base = _shift(sma(v, period), 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(base > 0, v / base, np.nan)
    if name == "range_atr":
        prev_atr = _shift(atr(h, l, c, period), 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(prev_atr > 0, (h - l) / prev_atr, np.nan)
    if name == "range_width_atr":
        a14 = atr(h, l, c, 14)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(a14 > 0, (_roll(h, period, np.max) - _roll(l, period, np.min)) / a14, np.nan)
    if name in ("swing_high", "swing_low"):
        return _pivots(h if name == "swing_high" else l, period, name == "swing_high")[0]
    if name == "range_mid":
        return (_roll(h, period, np.max) + _roll(l, period, np.min)) / 2.0
    if name == "vwap_dev_atr":
        vw = compute_indicator("vwap", a, notes=notes)
        av = atr(h, l, c, period)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(av > 0, (c - vw) / av, np.nan)
    if name in ("atr_band_lower", "atr_band_upper"):
        d = _shift(c, 1)
        off = mult * _shift(atr(h, l, c, period), 1)
        return d - off if name == "atr_band_lower" else d + off
    raise ValueError(f"unknown indicator {name}")


def source_series(a: dict, name: str) -> np.ndarray:
    o, h, l, c = a["open"], a["high"], a["low"], a["close"]
    return {"open": o, "high": h, "low": l, "close": c, "volume": a["volume"], "hl2": (h + l) / 2.0,
            "hlc3": (h + l + c) / 3.0, "ohlc4": (o + h + l + c) / 4.0}[name]


def compute_indicator(name: str, a: dict, period: int | None = None, *, source: str = "close",
                      fast: int = 12, slow: int = 26, signal: int = 9, mult: float = 2.0,
                      notes: list | None = None, lag: int = 1) -> np.ndarray:
    """Indicator on arrays `a` (time/open/high/low/close/volume). All formulas are standard and explicit."""
    src = source_series(a, source)
    if name in STAT_INDICATORS:
        return _stat_indicator(name, a, period, src, mult=mult, lag=lag, notes=notes)
    if name == "sma":
        return sma(src, period)
    if name == "ema":
        return ema(src, period)
    if name == "rsi":
        return rsi(src, period)
    if name == "atr":
        return atr(a["high"], a["low"], a["close"], period)
    if name == "adx":
        return adx(a["high"], a["low"], a["close"], period)
    if name in ("macd", "macd_signal", "macd_hist"):
        line = ema(src, fast) - ema(src, slow)
        if name == "macd":
            return line
        sig = ema(line, signal)
        return sig if name == "macd_signal" else line - sig
    if name in ("bb_upper", "bb_mid", "bb_lower"):
        mid = sma(src, period)
        if name == "bb_mid":
            return mid
        sd = _roll(src, period, np.std, ddof=0)
        return mid + mult * sd if name == "bb_upper" else mid - mult * sd
    if name == "donchian_high":
        return _roll(a["high"], period, np.max)
    if name == "donchian_low":
        return _roll(a["low"], period, np.min)
    if name == "roc":
        return (src / _shift(src, period) - 1.0) * 100.0
    if name == "realized_vol":
        lr = np.diff(np.log(src), prepend=np.nan)
        out = np.full(len(lr), np.nan)
        if period + 1 <= len(lr):
            out[period:] = np.std(sliding_window_view(lr[1:], period), axis=1, ddof=1)
        return out
    if name == "vwap":
        t = a["time"].astype(np.int64)
        vol = a["volume"].astype(np.float64)
        weights = vol
        if not np.any(vol > 0):
            weights = np.ones(len(vol))
            if notes is not None and "vwap: volume all zero -> equal-weight fallback" not in notes:
                notes.append("vwap: volume all zero -> equal-weight fallback")
        typical = (a["high"] + a["low"] + a["close"]) / 3.0
        day = t // DAY_MS
        is_start = np.concatenate(([True], day[1:] != day[:-1])) if len(day) else np.zeros(0, bool)
        start_idx = np.maximum.accumulate(np.where(is_start, np.arange(len(day)), 0))
        pv, w = typical * weights, weights.copy()
        cpv, cw = np.cumsum(pv), np.cumsum(w)
        sp = (cpv - pv)[start_idx]
        sw = (cw - w)[start_idx]
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(cw - sw > 0, (cpv - sp) / (cw - sw), np.nan)
    raise ValueError(f"unknown indicator {name}")


# --------------------------------------------------------------------------- time helpers

def _tz_offsets_ms(t: np.ndarray, tzname: str) -> np.ndarray:
    if tzname in ("UTC", "Etc/UTC") or len(t) == 0:
        return np.zeros(len(t), dtype=np.int64)
    zone = ZoneInfo(tzname)
    hours, inv = np.unique(t // 3_600_000, return_inverse=True)
    offs = np.array([int(datetime.fromtimestamp(int(h) * 3600, tz=zone).utcoffset().total_seconds() * 1000)
                     for h in hours], dtype=np.int64)
    return offs[inv]


def spec_timezone(spec: dict) -> str:
    sess = spec.get("session")
    return sess["timezone"] if isinstance(sess, dict) and sess.get("timezone") else "UTC"


def local_day_ids(spec: dict, time_ms) -> np.ndarray:
    """Calendar-day id in the session timezone (UTC when no session)."""
    t = np.asarray(time_ms, dtype=np.int64)
    return (t + _tz_offsets_ms(t, spec_timezone(spec))) // DAY_MS


def session_mask(spec: dict, time_ms) -> np.ndarray:
    """True where the bar OPEN time (local, DST-aware) lies in [start, end). Overnight sessions wrap."""
    t = np.asarray(time_ms, dtype=np.int64)
    sess = spec.get("session")
    if not sess:
        return np.ones(len(t), dtype=bool)
    minute = ((t + _tz_offsets_ms(t, sess["timezone"])) // 60_000) % 1440
    s, e = _hhmm(sess["start"]), _hhmm(sess["end"])
    return (minute >= s) & (minute < e) if s < e else (minute >= s) | (minute < e)


# --------------------------------------------------------------------------- validation

def _num(errs, path, v, *, lo=None, hi=None, integer=False, gt=None):
    if not _is_num(v):
        errs.append(f"{path}: must be a number, got {v!r}")
        return
    if integer and not _is_int(v):
        errs.append(f"{path}: must be an integer, got {v!r}")
    if lo is not None and v < lo:
        errs.append(f"{path}: must be >= {lo}, got {v!r}")
    if hi is not None and v > hi:
        errs.append(f"{path}: must be <= {hi}, got {v!r}")
    if gt is not None and v <= gt:
        errs.append(f"{path}: must be > {gt}, got {v!r}")


def _check_keys(errs, path, obj, allowed):
    for k in obj:
        if k not in allowed:
            errs.append(f"{path}: unknown key {k!r}")


def _val_operand(o, path, errs, base_ms):
    if not isinstance(o, dict):
        errs.append(f"{path}: operand must be an object")
        return
    kinds = [k for k in ("indicator", "price", "value", "level") if k in o]
    if len(kinds) != 1:
        errs.append(f"{path}: operand needs exactly one of indicator/price/value/level, got {sorted(o)}")
        return
    kind = kinds[0]
    if kind == "indicator":
        name = o["indicator"]
        allowed = {"indicator", "period", "source", "timeframe", "shift", "fast", "slow", "signal", "mult"}
        if name == "autocorr":
            allowed = allowed | {"lag"}
        _check_keys(errs, path, o, allowed)
        if name not in INDICATORS:
            errs.append(f"{path}: unknown indicator {name!r}")
            return
        if "period" in o:
            _num(errs, f"{path}.period", o["period"], lo=1, integer=True)
            if name in ("bb_upper", "bb_mid", "bb_lower", "realized_vol", "rsi", "adx") \
                    and _is_num(o["period"]) and o["period"] < 2:
                errs.append(f"{path}.period: must be >= 2 for {name}")
            if name in MIN_PERIOD and _is_num(o["period"]) and o["period"] < MIN_PERIOD[name]:
                errs.append(f"{path}.period: must be >= {MIN_PERIOD[name]} for {name}")
        elif name in PERIOD_REQUIRED:
            errs.append(f"{path}: indicator {name} requires 'period'")
        if "lag" in o:
            _num(errs, f"{path}.lag", o["lag"], lo=1, integer=True)
        for k, lo in (("fast", 1), ("slow", 1), ("signal", 1)):
            if k in o:
                _num(errs, f"{path}.{k}", o[k], lo=lo, integer=True)
        if "mult" in o:
            _num(errs, f"{path}.mult", o["mult"], gt=0)
        if "source" in o and o["source"] not in SOURCES:
            errs.append(f"{path}.source: unknown source {o['source']!r}")
        if "shift" in o:
            _num(errs, f"{path}.shift", o["shift"], lo=0, integer=True)
        if "timeframe" in o:
            tf = o["timeframe"]
            if tf not in TF_MS:
                errs.append(f"{path}.timeframe: unknown timeframe {tf!r}")
            elif base_ms and (TF_MS[tf] < base_ms or TF_MS[tf] % base_ms):
                errs.append(f"{path}.timeframe: {tf} must be a multiple of the base timeframe")
    elif kind == "price":
        _check_keys(errs, path, o, {"price", "shift"})
        if o["price"] not in PRICES:
            errs.append(f"{path}: unknown price {o['price']!r}")
        if "shift" in o:
            _num(errs, f"{path}.shift", o["shift"], lo=0, integer=True)
    elif kind == "value":
        _check_keys(errs, path, o, {"value"})
        _num(errs, f"{path}.value", o["value"])
    elif o["level"] in WINDOW_LEVELS:
        _check_keys(errs, path, o, {"level", "start", "end", "timezone"})
        for k in ("start", "end"):
            if not isinstance(o.get(k), str) or not _HHMM.match(o.get(k, "")):
                errs.append(f"{path}.{k}: window level requires HH:MM")
        if isinstance(o.get("start"), str) and isinstance(o.get("end"), str) and _HHMM.match(o["start"]) \
                and _HHMM.match(o["end"]) and _hhmm(o["start"]) >= _hhmm(o["end"]):
            errs.append(f"{path}: window start must be before end (no midnight wrap)")
        if "timezone" in o:
            try:
                ZoneInfo(str(o["timezone"]))
            except Exception:
                errs.append(f"{path}.timezone: unknown timezone {o['timezone']!r}")
    else:
        _check_keys(errs, path, o, {"level", "minutes"})
        if o["level"] not in LEVELS:
            errs.append(f"{path}: unknown level {o['level']!r}")
        if str(o["level"]).startswith("opening_range"):
            if "minutes" not in o:
                errs.append(f"{path}: opening_range level requires 'minutes'")
            else:
                _num(errs, f"{path}.minutes", o["minutes"], lo=1, integer=True)
        elif "minutes" in o:
            errs.append(f"{path}: 'minutes' only valid for opening_range levels")


def _val_cond(c, path, errs, base_ms):
    if not isinstance(c, dict) or not c:
        errs.append(f"{path}: condition must be a non-empty object")
        return
    if "all" in c or "any" in c:
        key = "all" if "all" in c else "any"
        _check_keys(errs, path, c, {key})
        if not isinstance(c[key], list) or not c[key]:
            errs.append(f"{path}.{key}: must be a non-empty list")
            return
        for i, sub in enumerate(c[key]):
            _val_cond(sub, f"{path}.{key}[{i}]", errs, base_ms)
    elif "not" in c:
        _check_keys(errs, path, c, {"not"})
        _val_cond(c["not"], f"{path}.not", errs, base_ms)
    elif "pattern" in c:
        p = c["pattern"]
        allowed = {"pattern", "lookback"}
        if p in ("range_touch_high", "range_touch_low"):
            allowed = allowed | {"touches", "tolerance_atr"}
        elif p == "random_entry":
            allowed = {"pattern", "seed", "probability"}
        _check_keys(errs, path, c, allowed)
        if p not in PATTERNS:
            errs.append(f"{path}: unknown pattern {p!r}")
        if "lookback" in c:
            _num(errs, f"{path}.lookback", c["lookback"], lo=1, integer=True)
        if "touches" in c:
            _num(errs, f"{path}.touches", c["touches"], lo=1, integer=True)
        if "tolerance_atr" in c:
            _num(errs, f"{path}.tolerance_atr", c["tolerance_atr"], lo=0)
        if p == "random_entry":
            _num(errs, f"{path}.seed", c.get("seed"), lo=0, integer=True)
            _num(errs, f"{path}.probability", c.get("probability"), gt=0, hi=0.5)
    elif "calendar" in c:
        _check_keys(errs, path, c, {"calendar", "in", "timezone"})
        kind = c["calendar"]
        if kind not in CALENDAR:
            errs.append(f"{path}: unknown calendar filter {kind!r}")
            return
        vals = c.get("in")
        hi = 23 if kind == "hour" else 6
        if not isinstance(vals, list) or not vals or not all(_is_int(v) and 0 <= v <= hi for v in vals):
            errs.append(f"{path}.in: must be a non-empty list of integers 0..{hi}")
        if "timezone" in c:
            try:
                ZoneInfo(str(c["timezone"]))
            except Exception:
                errs.append(f"{path}.timezone: unknown timezone {c['timezone']!r}")
    elif "left" in c or "op" in c or "right" in c:
        _check_keys(errs, path, c, {"left", "op", "right"})
        for k in ("left", "op", "right"):
            if k not in c:
                errs.append(f"{path}: missing {k!r}")
        if "op" in c and c["op"] not in OPS:
            errs.append(f"{path}: unknown op {c['op']!r}")
        for k in ("left", "right"):
            if k in c:
                _val_operand(c[k], f"{path}.{k}", errs, base_ms)
    else:
        errs.append(f"{path}: unknown condition keys {sorted(c)}")


def _find_params(node, out: list):
    if isinstance(node, dict):
        if set(node) == {"param"}:
            out.append(node["param"])
        else:
            for v in node.values():
                _find_params(v, out)
    elif isinstance(node, list):
        for v in node:
            _find_params(v, out)


def _subst(node, values: dict, errs: list | None):
    if isinstance(node, dict):
        if set(node) == {"param"}:
            name = node["param"]
            if name not in values:
                if errs is not None:
                    errs.append(f"param reference {name!r} is not defined in 'params'")
                return 1
            return values[name]
        return {k: _subst(v, values, errs) for k, v in node.items()}
    if isinstance(node, list):
        return [_subst(v, values, errs) for v in node]
    return node


def _coerce_param(p: dict, v):
    if p.get("type") == "int":
        if not _is_int(v):
            raise ValueError(f"param {p['name']}: expected int, got {v!r}")
        v = int(v)
    else:
        if not _is_num(v):
            raise ValueError(f"param {p['name']}: expected number, got {v!r}")
        v = float(v)
    if v < p["min"] - 1e-12 or v > p["max"] + 1e-12:
        raise ValueError(f"param {p['name']}: {v} outside [{p['min']}, {p['max']}]")
    return v


def _param_values(spec: dict, params: dict | None) -> dict:
    by = {p["name"]: p for p in (spec.get("params") or [])}
    values = {n: p["default"] for n, p in by.items()}
    for k, v in (params or {}).items():
        if k not in by:
            raise ValueError(f"unknown param {k!r}")
        values[k] = _coerce_param(by[k], v)
    return values


def resolve(spec: dict, params: dict | None = None) -> dict:
    """Return a deep copy of `spec` with every {"param": name} replaced and optional blocks defaulted."""
    values = _param_values(spec, params)
    errs: list = []
    body = {k: v for k, v in spec.items() if k != "params"}
    out = _subst(copy.deepcopy(body), values, errs)
    if errs:
        raise ValueError("; ".join(errs))
    out["params"] = copy.deepcopy(spec.get("params") or [])
    out["order"] = {**DEFAULT_ORDER, **(out.get("order") or {})}
    for k, d in (("target", None), ("trailing", None), ("break_even", None), ("time_exit_bars", None),
                 ("cooldown_bars", 0), ("max_trades_per_day", None), ("session", None), ("partial_exits", None)):
        out.setdefault(k, d)
    out.setdefault("risk", {"percent": 1.0})
    return out


def validate_spec(spec) -> list[str]:
    """Human-readable error list; empty list means valid."""
    errs: list[str] = []
    if not isinstance(spec, dict):
        return ["spec must be an object"]
    _check_keys(errs, "spec", spec, TOP_KEYS)
    if spec.get("schema") != SCHEMA:
        errs.append(f"schema must be {SCHEMA!r}")
    name = spec.get("name")
    if not isinstance(name, str) or not _NAME.match(name):
        errs.append("name: must start with a letter and contain only letters, digits, space, _ or -")
    if not isinstance(spec.get("symbol"), str) or not spec.get("symbol"):
        errs.append("symbol: required string")
    tf = spec.get("timeframe")
    if tf not in TF_MS:
        errs.append(f"timeframe: must be one of {sorted(TF_MS)}")
    base_ms = TF_MS.get(tf)
    direction = spec.get("direction")
    if direction not in ("long", "short", "both"):
        errs.append("direction: must be long, short or both")

    # params
    plist = spec.get("params", [])
    names: set = set()
    if not isinstance(plist, list):
        errs.append("params: must be a list")
        plist = []
    clean_params = []
    for i, p in enumerate(plist):
        path = f"params[{i}]"
        if not isinstance(p, dict):
            errs.append(f"{path}: must be an object")
            continue
        _check_keys(errs, path, p, {"name", "type", "min", "max", "step", "default", "description"})
        if not isinstance(p.get("name"), str) or not _IDENT.match(p.get("name", "")):
            errs.append(f"{path}.name: must be an identifier")
            continue
        if p["name"] in names:
            errs.append(f"{path}.name: duplicate {p['name']!r}")
        names.add(p["name"])
        if p.get("type") not in ("int", "float"):
            errs.append(f"{path}.type: must be 'int' or 'float'")
            continue
        ok = True
        for k in ("min", "max", "default"):
            if not _is_num(p.get(k)):
                errs.append(f"{path}.{k}: must be a number")
                ok = False
        if "step" in p:
            _num(errs, f"{path}.step", p["step"], gt=0)
        if ok:
            if p["min"] > p["max"]:
                errs.append(f"{path}: min > max")
            elif not p["min"] <= p["default"] <= p["max"]:
                errs.append(f"{path}.default: outside [min, max]")
            if p["type"] == "int" and not all(_is_int(p[k]) for k in ("min", "max", "default")):
                errs.append(f"{path}: int param needs integer min/max/default")
            clean_params.append(p)

    refs: list = []
    _find_params({k: v for k, v in spec.items() if k != "params"}, refs)
    for r in sorted(set(map(str, refs))):
        if r not in names:
            errs.append(f"param reference {r!r} does not exist in 'params'")

    values = {p["name"]: p["default"] for p in clean_params}
    body = _subst({k: v for k, v in spec.items() if k != "params"}, values, None)

    # entry
    entry = body.get("entry")
    if not isinstance(entry, dict):
        errs.append("entry: required object with 'long' and/or 'short'")
    else:
        _check_keys(errs, "entry", entry, {"long", "short"})
        for side in ("long", "short"):
            needed = direction in (side, "both")
            if side in entry:
                _val_cond(entry[side], f"entry.{side}", errs, base_ms)
            elif needed:
                errs.append(f"entry.{side}: required for direction {direction!r}")

    order = body.get("order")
    if order is not None:
        if not isinstance(order, dict):
            errs.append("order: must be an object")
        else:
            _check_keys(errs, "order", order, {"type", "offset_atr", "atr_period", "expiry_bars"})
            if order.get("type", "market") not in ("market", "stop", "limit"):
                errs.append("order.type: must be market, stop or limit")
            if "offset_atr" in order:
                _num(errs, "order.offset_atr", order["offset_atr"], lo=0)
            if "atr_period" in order:
                _num(errs, "order.atr_period", order["atr_period"], lo=1, integer=True)
            if "expiry_bars" in order:
                _num(errs, "order.expiry_bars", order["expiry_bars"], lo=1, integer=True)

    stop = body.get("stop")
    if not isinstance(stop, dict):
        errs.append("stop: required object")
    else:
        t = stop.get("type")
        if t == "atr":
            _check_keys(errs, "stop", stop, {"type", "mult", "period"})
            _num(errs, "stop.mult", stop.get("mult"), gt=0)
            _num(errs, "stop.period", stop.get("period"), lo=1, integer=True)
        elif t == "points":
            _check_keys(errs, "stop", stop, {"type", "value"})
            _num(errs, "stop.value", stop.get("value"), gt=0)
        elif t == "swing":
            _check_keys(errs, "stop", stop, {"type", "lookback"})
            _num(errs, "stop.lookback", stop.get("lookback"), lo=1, integer=True)
        elif t == "percent":
            _check_keys(errs, "stop", stop, {"type", "value"})
            _num(errs, "stop.value", stop.get("value"), gt=0, hi=50)
        else:
            errs.append("stop.type: must be atr, points, swing or percent")

    target = body.get("target")
    if target is not None:
        if not isinstance(target, dict):
            errs.append("target: must be an object or null")
        elif target.get("type") == "r_multiple":
            _check_keys(errs, "target", target, {"type", "value"})
            _num(errs, "target.value", target.get("value"), gt=0)
        elif target.get("type") == "atr":
            _check_keys(errs, "target", target, {"type", "mult", "period"})
            _num(errs, "target.mult", target.get("mult"), gt=0)
            _num(errs, "target.period", target.get("period"), lo=1, integer=True)
        elif target.get("type") == "level":
            _check_keys(errs, "target", target, {"type", "operand"})
            if "value" in (target.get("operand") or {}):
                errs.append("target.operand: a constant value is not a valid level target")
            else:
                _val_operand(target.get("operand"), "target.operand", errs, base_ms)
        else:
            errs.append("target.type: must be r_multiple, atr or level")

    trailing = body.get("trailing")
    if trailing is not None:
        if not isinstance(trailing, dict):
            errs.append("trailing: must be an object or null")
        else:
            _check_keys(errs, "trailing", trailing, {"activate_r", "atr_mult", "period"})
            _num(errs, "trailing.activate_r", trailing.get("activate_r"), lo=0)
            _num(errs, "trailing.atr_mult", trailing.get("atr_mult"), gt=0)
            _num(errs, "trailing.period", trailing.get("period"), lo=1, integer=True)
    be = body.get("break_even")
    if be is not None:
        if not isinstance(be, dict):
            errs.append("break_even: must be an object or null")
        else:
            _check_keys(errs, "break_even", be, {"activate_r"})
            _num(errs, "break_even.activate_r", be.get("activate_r"), gt=0)

    si = body.get("scale_in")
    if si is not None:
        if not isinstance(si, dict):
            errs.append("scale_in: must be an object or null")
        else:
            _check_keys(errs, "scale_in", si, {"max_adds", "add_every_r", "add_size_fraction"})
            _num(errs, "scale_in.max_adds", si.get("max_adds"), lo=1, hi=5, integer=True)
            _num(errs, "scale_in.add_every_r", si.get("add_every_r"), gt=0)
            _num(errs, "scale_in.add_size_fraction", si.get("add_size_fraction"), gt=0, hi=1)
    pe = body.get("partial_exits")
    if pe is not None:
        # [{"r": 1.0, "fraction": 0.5, "stop_r": 0.0}, ...]: take `fraction` of the INITIAL size at +r R; stop_r (optional)
        # moves the stop of the remainder to entry +stop_r R (0 = break-even) from the next bar.
        if not isinstance(pe, list) or not pe or len(pe) > 5:
            errs.append("partial_exits: must be a list of 1-5 levels or null")
        else:
            total, last_r = 0.0, 0.0
            for i, lv in enumerate(pe):
                path = f"partial_exits[{i}]"
                if not isinstance(lv, dict):
                    errs.append(f"{path}: must be an object")
                    continue
                _check_keys(errs, path, lv, {"r", "fraction", "stop_r"})
                _num(errs, f"{path}.r", lv.get("r"), gt=0)
                _num(errs, f"{path}.fraction", lv.get("fraction"), gt=0, hi=1)
                if lv.get("stop_r") is not None:
                    _num(errs, f"{path}.stop_r", lv.get("stop_r"))
                if _is_num(lv.get("r")):
                    if lv["r"] <= last_r:
                        errs.append(f"{path}.r: levels must be strictly increasing")
                    last_r = lv["r"]
                    if _is_num(lv.get("stop_r")) and lv["stop_r"] >= lv["r"]:
                        errs.append(f"{path}.stop_r: must be below the level's own r")
                total += lv["fraction"] if _is_num(lv.get("fraction")) else 0.0
            if total > 1.0 + 1e-9:
                errs.append("partial_exits: fractions must sum to <= 1")
            if body.get("scale_in") is not None:
                errs.append("partial_exits: cannot be combined with scale_in")
    if body.get("time_exit_bars") is not None:
        _num(errs, "time_exit_bars", body["time_exit_bars"], lo=1, integer=True)
    if body.get("cooldown_bars") is not None:
        _num(errs, "cooldown_bars", body["cooldown_bars"], lo=0, integer=True)
    if body.get("max_trades_per_day") is not None:
        _num(errs, "max_trades_per_day", body["max_trades_per_day"], lo=1, integer=True)

    sess = body.get("session")
    if sess is not None:
        if not isinstance(sess, dict):
            errs.append("session: must be an object or null")
        else:
            _check_keys(errs, "session", sess, {"timezone", "start", "end"})
            try:
                ZoneInfo(str(sess.get("timezone")))
            except Exception:
                errs.append(f"session.timezone: unknown timezone {sess.get('timezone')!r}")
            for k in ("start", "end"):
                if not isinstance(sess.get(k), str) or not _HHMM.match(sess.get(k, "")):
                    errs.append(f"session.{k}: must be HH:MM")
            if sess.get("start") == sess.get("end"):
                errs.append("session: start and end must differ")

    risk = body.get("risk")
    if not isinstance(risk, dict):
        errs.append("risk: required object {'percent': x}")
    else:
        _check_keys(errs, "risk", risk, {"percent"})
        _num(errs, "risk.percent", risk.get("percent"), gt=0, hi=100)
    return errs


def lookback_bars(spec: dict) -> int:
    """Window size for the Jesse strategy: largest needed history (params at max) * 4 + 50."""
    values = {p["name"]: p["max"] for p in (spec.get("params") or [])}
    body = _subst({k: v for k, v in spec.items() if k != "params"}, values, None)
    base_ms = TF_MS[body["timeframe"]]
    bpd = max(1, DAY_MS // base_ms)
    need = [2]

    def walk(node):
        if isinstance(node, dict):
            if "indicator" in node:
                ratio = max(1, TF_MS.get(node.get("timeframe", body["timeframe"]), base_ms) // base_ms)
                p = int(node.get("period", 14))
                if node["indicator"].startswith("macd"):
                    p = int(node.get("slow", 26)) + int(node.get("signal", 9))
                elif node["indicator"] == "adx":
                    p = 2 * p
                elif node["indicator"] == "vwap":
                    p = bpd * 2
                elif node["indicator"] == "vwap_dev_atr":
                    p = max(bpd * 2, p)
                elif node["indicator"] in ("hurst", "autocorr"):
                    p = p + 8 + int(node.get("lag", 1))
                elif node["indicator"] in ("swing_high", "swing_low"):
                    p = 10 * (2 * p + 1)
                need.append((p + int(node.get("shift", 0)) + 2) * ratio)
            elif "level" in node:
                lv = node["level"]
                need.append(10 * bpd if "week" in lv else 3 * bpd)
            elif "pattern" in node:
                pat = node["pattern"]
                if pat in STRUCTURE_PATTERNS:
                    need.append(10 * (2 * int(node.get("lookback", 3)) + 1) + 4)
                elif pat in ("range_touch_high", "range_touch_low"):
                    need.append(int(node.get("lookback", 20)) + 30)
                else:
                    need.append(int(node.get("lookback", 20)) + 4)
            else:
                for v in node.values():
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(body.get("entry"))
    walk(body.get("target"))
    st = body.get("stop") or {}
    need.append(int(st.get("period", st.get("lookback", 1))) * 2)
    for key in ("target", "trailing"):
        blk = body.get(key)
        if isinstance(blk, dict) and "period" in blk:
            need.append(int(blk["period"]) * 2)
    need.append(int((body.get("order") or {}).get("atr_period", 14)) * 2)
    if body.get("session"):
        need.append(bpd * 2)
    return int(max(need) * 4 + 50)


# --------------------------------------------------------------------------- evaluation

def operand_label(op: dict) -> str:
    if "indicator" in op:
        name = op["indicator"]
        if name.startswith("macd"):
            args = f"{op.get('fast', 12)},{op.get('slow', 26)},{op.get('signal', 9)}"
        elif name == "vwap":
            args = ""
        else:
            args = str(op["period"])
            if name.startswith(("bb_", "atr_band")):
                args += f",{op.get('mult', 2.0):g}"
            elif name == "autocorr":
                args += f",lag{int(op.get('lag', 1))}"
        src = op.get("source", "close")
        if src != "close" and name not in ("atr", "adx", "vwap", "donchian_high", "donchian_low") \
                and name not in ("volume_ratio", "range_atr", "range_width_atr", "swing_high", "swing_low",
                                 "range_mid", "vwap_dev_atr", "atr_band_lower", "atr_band_upper"):
            args += f",{src}"
        text = f"{name}({args})" if args else name
        if op.get("timeframe"):
            text += f"@{op['timeframe']}"
        if op.get("shift"):
            text += f"[-{int(op['shift'])}]"
        return text
    if "price" in op:
        return op["price"] + (f"[-{int(op['shift'])}]" if op.get("shift") else "")
    if "level" in op:
        if op["level"] in WINDOW_LEVELS:
            return f"{op['level']}({op['start']}-{op['end']}{' ' + op['timezone'] if op.get('timezone') else ''})"
        return op["level"] + (f"({int(op['minutes'])}m)" if "minutes" in op else "")
    return f"{op['value']:g}"


def _states(res: np.ndarray, valid: np.ndarray) -> np.ndarray:
    return np.where(valid, res.astype(np.int8), np.int8(-1)).astype(np.int8)


def _combine_all(parts):
    stack = np.stack(parts)
    return np.where((stack == 0).any(axis=0), 0, np.where((stack == -1).any(axis=0), -1, 1)).astype(np.int8)


def _combine_any(parts):
    stack = np.stack(parts)
    return np.where((stack == 1).any(axis=0), 1, np.where((stack == -1).any(axis=0), -1, 0)).astype(np.int8)


class _Ctx:
    def __init__(self, spec: dict, arrays: dict):
        self.spec = spec
        self.t = np.asarray(arrays["time"], dtype=np.int64)
        self.n = len(self.t)
        f = lambda k: np.asarray(arrays[k], dtype=np.float64)
        self.a = {"time": self.t, "open": f("open"), "high": f("high"), "low": f("low"), "close": f("close"),
                  "volume": f("volume") if "volume" in arrays else np.zeros(self.n)}
        for k, v in self.a.items():
            if len(v) != self.n:
                raise ValueError(f"array {k} length differs from time")
        self.base_ms = TF_MS[spec["timeframe"]]
        self.notes: list[str] = []
        self.checks: dict = {}
        self.indicators: dict = {}
        self._cache: dict = {}
        self._htf: dict = {}
        self._local = None

    # --- time / session helpers
    def local(self) -> dict:
        if self._local is None:
            tz = spec_timezone(self.spec)
            lm = self.t + _tz_offsets_ms(self.t, tz)
            day = lm // DAY_MS
            self._local = {"minute": (lm // 60_000) % 1440, "day": day, "week": (day + 3) // 7,
                           "start": _hhmm(self.spec["session"]["start"]) if self.spec.get("session") else 0}
        return self._local

    @staticmethod
    def _groups(ids: np.ndarray):
        if len(ids) == 0:
            return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
        starts = np.concatenate(([0], np.flatnonzero(ids[1:] != ids[:-1]) + 1))
        flag = np.zeros(len(ids), dtype=np.int64)
        flag[starts] = 1
        g = np.cumsum(flag) - 1
        return starts, g

    def _prev_group(self, ids, arr, fn) -> np.ndarray:
        starts, g = self._groups(ids)
        out = np.full(self.n, np.nan)
        if len(starts) == 0:
            return out
        agg = fn.reduceat(arr, starts)
        ok = g >= 1
        out[ok] = agg[g[ok] - 1]
        return out

    # --- HTF
    def htf(self, tf: str) -> dict:
        if tf not in self._htf:
            ms = TF_MS[tf]
            bucket = self.t // ms
            starts, _ = self._groups(bucket)
            ends = np.concatenate((starts[1:], [self.n])) - 1 if self.n else starts
            a = self.a
            arrs = {"time": bucket[starts] * ms if self.n else self.t,
                    "open": a["open"][starts], "close": a["close"][ends],
                    "high": np.maximum.reduceat(a["high"], starts) if self.n else a["high"],
                    "low": np.minimum.reduceat(a["low"], starts) if self.n else a["low"],
                    "volume": np.add.reduceat(a["volume"], starts) if self.n else a["volume"]}
            bar_end = arrs["time"] + ms
            vis = np.searchsorted(bar_end, self.t + self.base_ms, side="right") - 1
            self._htf[tf] = {"arrays": arrs, "vis": vis}
        return self._htf[tf]

    # --- operands
    def operand(self, op: dict):
        label = operand_label(op)
        if "value" in op:
            return np.full(self.n, float(op["value"])), label
        if "price" in op:
            return _shift(self.a[op["price"]], int(op.get("shift", 0))), label
        if "level" in op:
            return self._level(op), label
        key = json.dumps(op, sort_keys=True)
        if key not in self._cache:
            self._cache[key] = self._indicator(op)
            self.indicators[label] = self._cache[key]
        return self._cache[key], label

    def _indicator(self, op: dict) -> np.ndarray:
        kw = dict(source=op.get("source", "close"), fast=int(op.get("fast", 12)), slow=int(op.get("slow", 26)),
                  signal=int(op.get("signal", 9)), mult=float(op.get("mult", 2.0)), notes=self.notes,
                  lag=int(op.get("lag", 1)))
        period = int(op["period"]) if "period" in op else None
        shift = int(op.get("shift", 0))
        tf = op.get("timeframe")
        if tf and TF_MS[tf] != self.base_ms:
            h = self.htf(tf)
            vals = _shift(compute_indicator(op["indicator"], h["arrays"], period, **kw), shift)
            vis = h["vis"]
            return np.where(vis >= 0, vals[np.maximum(vis, 0)] if len(vals) else np.nan, np.nan)
        return _shift(compute_indicator(op["indicator"], self.a, period, **kw), shift)

    def _level(self, op: dict) -> np.ndarray:
        lv = op["level"]
        loc = self.local()
        a = self.a
        if lv.startswith("prev_day"):
            hi = lv.endswith("high")
            return self._prev_group(loc["day"], a["high"] if hi else a["low"], np.maximum if hi else np.minimum)
        if lv.startswith("prev_week"):
            hi = lv.endswith("high")
            return self._prev_group(loc["week"], a["high"] if hi else a["low"], np.maximum if hi else np.minimum)
        if lv in WINDOW_LEVELS:
            return self._window_level(op)
        starts, g = self._groups(loc["day"])
        out = np.full(self.n, np.nan)
        if self.n == 0:
            return out
        if lv == "prev_day_close":
            ends = np.concatenate((starts[1:], [self.n])) - 1
            closes = a["close"][ends]
            ok = g >= 1
            out[ok] = closes[g[ok] - 1]
            return out
        if lv == "day_open":
            return a["open"][starts][g]
        if lv == "session_open":
            mask = session_mask(self.spec, self.t)
            idx = np.flatnonzero(mask)
            first = np.full(len(starts), -1, dtype=np.int64)
            if len(idx):
                ug, pos = np.unique(g[idx], return_index=True)
                first[ug] = idx[pos]
            fi = first[g]
            ok = (fi >= 0) & (np.arange(self.n) >= fi)
            out[ok] = a["open"][fi[ok]]
            return out
        minutes = int(op["minutes"])
        s = loc["start"]
        in_or = (loc["minute"] >= s) & (loc["minute"] < s + minutes)
        hi = lv.endswith("high")
        vals = np.where(in_or, a["high"] if hi else a["low"], -np.inf if hi else np.inf)
        agg = (np.maximum if hi else np.minimum).reduceat(vals, starts)
        agg = np.where(np.isinf(agg), np.nan, agg)
        done = loc["minute"] >= s + minutes
        out = np.where(done, agg[g], np.nan)
        return out

    def _window_level(self, op: dict) -> np.ndarray:
        """High/low/mid of bars whose OPEN lies in [start, end) of the local day; exposed from the first bar
        opening at/after `end` (the window is complete), NaN before that and on days without window bars."""
        out = np.full(self.n, np.nan)
        if self.n == 0:
            return out
        tz = op.get("timezone") or spec_timezone(self.spec)
        lm = self.t + _tz_offsets_ms(self.t, tz)
        minute, day = (lm // 60_000) % 1440, lm // DAY_MS
        starts, g = self._groups(day)
        s, e = _hhmm(op["start"]), _hhmm(op["end"])
        inside = (minute >= s) & (minute < e)
        a = self.a
        hi = np.maximum.reduceat(np.where(inside, a["high"], -np.inf), starts)
        lo = np.minimum.reduceat(np.where(inside, a["low"], np.inf), starts)
        hi, lo = np.where(np.isinf(hi), np.nan, hi), np.where(np.isinf(lo), np.nan, lo)
        val = {"window_high": hi, "window_low": lo, "window_mid": (hi + lo) / 2.0}[op["level"]]
        done = minute >= e
        out[done] = val[g[done]]
        return out

    # --- conditions
    def _register(self, side: str, name: str, state, left, right, op):
        key = f"{side}: {name}"
        k, i = key, 2
        while k in self.checks:
            k = f"{key} #{i}"
            i += 1
        self.checks[k] = {"state": state, "left": left, "right": right, "operator": op}

    def cond(self, c: dict, side: str) -> np.ndarray:
        if "all" in c:
            return _combine_all([self.cond(x, side) for x in c["all"]])
        if "any" in c:
            return _combine_any([self.cond(x, side) for x in c["any"]])
        if "not" in c:
            s = self.cond(c["not"], side)
            return np.where(s == -1, -1, 1 - s).astype(np.int8)
        if "pattern" in c:
            return self._pattern(c, side)
        if "calendar" in c:
            tz = c.get("timezone") or spec_timezone(self.spec)
            lm = self.t + _tz_offsets_ms(self.t, tz)
            if c["calendar"] == "hour":
                unit = (lm // 3_600_000) % 24
            else:
                unit = (lm // DAY_MS + 3) % 7  # 1970-01-01 was a Thursday -> Monday = 0
            state = np.isin(unit, np.asarray([int(v) for v in c["in"]])).astype(np.int8)
            self._register(side, f"{c['calendar']} in {sorted(int(v) for v in c['in'])}", state, None, None,
                           "calendar")
            return state
        left, ll = self.operand(c["left"])
        right, rl = self.operand(c["right"])
        op = c["op"]
        if op in (">", "<", ">=", "<="):
            valid = ~(np.isnan(left) | np.isnan(right))
            with np.errstate(invalid="ignore"):
                res = {">": left > right, "<": left < right, ">=": left >= right, "<=": left <= right}[op]
        else:
            lp, rp = _shift(left, 1), _shift(right, 1)
            valid = ~(np.isnan(left) | np.isnan(right) | np.isnan(lp) | np.isnan(rp))
            with np.errstate(invalid="ignore"):
                res = (lp <= rp) & (left > right) if op == "crosses_above" else (lp >= rp) & (left < right)
        state = _states(res, valid)
        self._register(side, f"{ll} {op} {rl}", state, left, right, op)
        return state

    def _pattern(self, c: dict, side: str) -> np.ndarray:
        p = c["pattern"]
        a = self.a
        o, h, l, cl = a["open"], a["high"], a["low"], a["close"]
        n = self.n
        look = int(c.get("lookback", _PATTERN_DEFAULT_LOOKBACK.get(
            p, 20 if p.startswith(("breakout", "liquidity")) else 1)))
        idx = np.arange(n)
        valid = np.ones(n, dtype=bool)
        res = np.zeros(n, dtype=bool)
        with np.errstate(invalid="ignore"):
            if p in EXTRA_PATTERNS:
                res, valid = self._extra_pattern(c, p, look)
            elif p == "inside_bar":
                res = (h < _shift(h, 1)) & (l > _shift(l, 1))
                valid = idx >= 1
            elif p in ("nr4", "nr7"):
                w = int(p[2:])
                rng = h - l
                prev_min = _shift(_roll(rng, w - 1, np.min), 1)
                res = rng < prev_min
                valid = idx >= w - 1
            elif p == "bullish_engulfing":
                po, pc = _shift(o, 1), _shift(cl, 1)
                res = (pc < po) & (cl > o) & (o < pc) & (cl > po)
                valid = idx >= 1
            elif p == "bearish_engulfing":
                po, pc = _shift(o, 1), _shift(cl, 1)
                res = (pc > po) & (cl < o) & (o > pc) & (cl < po)
                valid = idx >= 1
            elif p in ("breakout_high", "liquidity_sweep_high"):
                ref = _shift(_roll(h, look, np.max), 1)
                res = (cl > ref) if p == "breakout_high" else ((h > ref) & (cl <= ref))
                valid = idx >= look
            elif p in ("breakout_low", "liquidity_sweep_low"):
                ref = _shift(_roll(l, look, np.min), 1)
                res = (cl < ref) if p == "breakout_low" else ((l < ref) & (cl >= ref))
                valid = idx >= look
            else:  # fvg_bull / fvg_bear: gap between bar j-2 and bar j; any in last `look` bars
                raw = (l > _shift(h, 2)) if p == "fvg_bull" else (h < _shift(l, 2))
                raw = np.where(idx >= 2, raw, False).astype(np.int64)
                cs = np.cumsum(raw)
                prev = np.concatenate((np.zeros(look, dtype=np.int64), cs[:-look])) if look < n else np.zeros(n, np.int64)
                res = (cs - prev) > 0
                valid = idx >= 2
        valid = valid & ~np.isnan(h) & ~np.isnan(l)
        state = _states(res, valid)
        if p == "random_entry":
            label = f"random_entry(seed={int(c['seed'])},p={float(c['probability']):g})"
        elif p.startswith(("breakout", "liquidity", "fvg", "range_touch", "swing_break", "mss", "structure")):
            label = f"{p}({look})"
        else:
            label = p
        self._register(side, label, state, None, None, "pattern")
        return state

    def _extra_pattern(self, c: dict, p: str, look: int):
        a = self.a
        o, h, l, cl = a["open"], a["high"], a["low"], a["close"]
        n = self.n
        idx = np.arange(n)
        if p in ("pin_bull", "pin_bear"):
            rng = h - l
            body = np.abs(cl - o)
            wick = (np.minimum(o, cl) - l) if p == "pin_bull" else (h - np.maximum(o, cl))
            res = (rng > 0) & (wick >= 0.6 * rng) & (body <= 0.35 * rng)
            return res, np.ones(n, dtype=bool)
        if p in ("crt_bull", "crt_bear"):
            h1, l1, h2, l2, c2 = _shift(h, 2), _shift(l, 2), _shift(h, 1), _shift(l, 1), _shift(cl, 1)
            inside = (c2 > l1) & (c2 < h1)
            if p == "crt_bull":
                res = (l2 < l1) & inside & (cl > h2)
            else:
                res = (h2 > h1) & inside & (cl < l2)
            return res, idx >= 2
        if p in STRUCTURE_PATTERNS:
            sh, sh_prev = _pivots(h, look, True)
            sl, sl_prev = _pivots(l, look, False)
            if p in ("structure_up", "structure_down"):
                up = p == "structure_up"
                res = ((sh > sh_prev) & (sl > sl_prev)) if up else ((sh < sh_prev) & (sl < sl_prev))
                return res, ~(np.isnan(sh_prev) | np.isnan(sl_prev))
            bull = p in ("swing_break_high", "mss_bull")
            ref, ref_prev = (_shift(sh, 1), _shift(sh_prev, 1)) if bull else (_shift(sl, 1), _shift(sl_prev, 1))
            pc = _shift(cl, 1)
            res = ((cl > ref) & (pc <= ref)) if bull else ((cl < ref) & (pc >= ref))
            valid = ~np.isnan(ref) & ~np.isnan(pc)
            if p in ("mss_bull", "mss_bear"):
                res = res & ((ref < ref_prev) if bull else (ref > ref_prev))
                valid &= ~np.isnan(ref_prev)
            return res, valid
        if p in ("fvg_bull_retest", "fvg_bear_retest"):
            bull = p == "fvg_bull_retest"
            raw = (l > _shift(h, 2)) if bull else (h < _shift(l, 2))
            raw = np.where(idx >= 2, raw, False)
            ev = np.where(raw, idx, -1)
            prior = np.concatenate(([-1], ev[:-1])) if n else ev  # latest gap formed strictly before i
            last = np.maximum.accumulate(prior).astype(np.int64)
            has = (last >= 0) & (idx - last <= look)
            j = np.maximum(last, 0)
            if bull:
                bottom, top = _shift(h, 2)[j], l[j]
                res = has & (l <= top) & (cl >= bottom)
            else:
                top, bottom = _shift(l, 2)[j], h[j]
                res = has & (h >= bottom) & (cl <= top)
            return res, idx >= 3
        if p in ("range_touch_high", "range_touch_low"):
            touches = int(c.get("touches", 3))
            tol = float(c.get("tolerance_atr", 0.25))
            hi = p == "range_touch_high"
            x = h if hi else l
            res = np.zeros(n, dtype=bool)
            valid = idx >= max(look, 15)
            if n > look:
                win = sliding_window_view(x, look)[:n - look]          # bars i-look..i-1 for i = look..n-1
                a14 = atr(h, l, cl, 14)[look - 1:n - 1]
                ref = win.max(axis=1) if hi else win.min(axis=1)
                cnt = (win >= (ref - tol * a14)[:, None]).sum(axis=1) if hi else \
                    (win <= (ref + tol * a14)[:, None]).sum(axis=1)
                cur_x, cur_c = x[look:], cl[look:]
                sweep = (cur_x > ref) & (cur_c < ref) if hi else (cur_x < ref) & (cur_c > ref)
                res[look:] = sweep & (cnt >= touches) & np.isfinite(a14)
                valid[look:] &= np.isfinite(a14)
            return res, valid
        if p == "random_entry":
            z = self.t.astype(np.uint64) + np.uint64(int(c["seed"]) * 0x9E3779B97F4A7C15 % (1 << 64))
            with np.errstate(over="ignore"):
                z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
                z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
                z = z ^ (z >> np.uint64(31))
            u = (z >> np.uint64(11)).astype(np.float64) / float(1 << 53)
            return u < float(c["probability"]), np.ones(n, dtype=bool)
        raise ValueError(f"unknown pattern {p}")


def _exit_arrays(rs: dict, a: dict, ctx: "_Ctx | None" = None) -> dict:
    ex: dict = {}
    st = rs["stop"]
    if st["type"] == "atr":
        ex["stop_atr"] = atr(a["high"], a["low"], a["close"], int(st["period"])) * float(st["mult"])
    elif st["type"] == "swing":
        k = int(st["lookback"])
        ex["swing_low"] = _roll(a["low"], k, np.min)
        ex["swing_high"] = _roll(a["high"], k, np.max)
    tg = rs.get("target")
    if tg and tg["type"] == "atr":
        ex["target_atr"] = atr(a["high"], a["low"], a["close"], int(tg["period"])) * float(tg["mult"])
    elif tg and tg["type"] == "level":
        ex["target_level"] = (ctx if ctx is not None else _Ctx(rs, a)).operand(tg["operand"])[0].astype(np.float64)
    tr = rs.get("trailing")
    if tr:
        ex["trail_atr"] = atr(a["high"], a["low"], a["close"], int(tr["period"]))
    if rs["order"]["type"] != "market" and rs["order"]["offset_atr"] > 0:
        ex["order_atr"] = atr(a["high"], a["low"], a["close"], int(rs["order"]["atr_period"]))
    return ex


def exit_snapshot(ex: dict, i: int) -> dict:
    """Scalar view of the exit arrays at signal bar i (NaN where undefined)."""
    return {k: float(v[i]) for k, v in ex.items()}


def stop_price(rs: dict, side: str, entry: float, snap: dict) -> float:
    st = rs["stop"]
    if st["type"] == "swing":
        return snap.get("swing_low" if side == "long" else "swing_high", math.nan)
    if st["type"] == "percent":
        d = abs(entry) * float(st["value"]) / 100.0
    else:
        d = snap.get("stop_atr", math.nan) if st["type"] == "atr" else float(st["value"])
    return entry - d if side == "long" else entry + d


def valid_stop(side: str, entry: float, stop: float) -> bool:
    return math.isfinite(stop) and math.isfinite(entry) and (stop < entry if side == "long" else stop > entry)


def target_price(rs: dict, side: str, entry: float, stop: float, snap: dict) -> float:
    tg = rs.get("target")
    if not tg:
        return math.nan
    if tg["type"] == "level":
        lvl = snap.get("target_level", math.nan)
        ok = math.isfinite(lvl) and (lvl > entry if side == "long" else lvl < entry)
        return lvl if ok else math.nan
    d = abs(entry - stop) * float(tg["value"]) if tg["type"] == "r_multiple" else snap.get("target_atr", math.nan)
    return entry + d if side == "long" else entry - d


def order_price(rs: dict, side: str, ref_close: float, snap: dict) -> float:
    """Pending-order price relative to the signal bar close. NaN for market orders."""
    od = rs["order"]
    if od["type"] == "market":
        return math.nan
    off = float(od["offset_atr"]) * snap.get("order_atr", 0.0 if od["offset_atr"] == 0 else math.nan)
    above = (od["type"] == "stop") == (side == "long")
    return ref_close + off if above else ref_close - off


def evaluate(spec: dict, arrays: dict, params: dict | None = None, *, validate: bool = True) -> dict:
    """Evaluate entry rules at bar close using only data <= each bar.

    Returns long_signal / short_signal (bool arrays), `states` (int8 per side, 1/0/-1), `checks`
    (name -> {state,left,right,operator}), `indicators`, `exits` (arrays for stop/target/trailing),
    `session_ok`, resolved `spec`, `notes`, `spec_sha256`.
    """
    if validate:
        errs = validate_spec(spec)
        if errs:
            raise ValueError("invalid spec: " + "; ".join(errs))
    rs = resolve(spec, params)
    ctx = _Ctx(rs, arrays)
    n = ctx.n
    session_ok = session_mask(rs, ctx.t)
    if rs.get("session"):
        ctx.checks["session"] = {"state": session_ok.astype(np.int8), "left": None, "right": None,
                                 "operator": f"in {rs['session']['start']}-{rs['session']['end']} "
                                             f"{rs['session']['timezone']}"}
    states, signals = {}, {}
    for side in ("long", "short"):
        active = rs["direction"] in (side, "both") and side in rs["entry"]
        if active:
            st = ctx.cond(rs["entry"][side], side)
            st = np.where(session_ok, st, 0).astype(np.int8)
        else:
            st = np.zeros(n, dtype=np.int8)
        states[side] = st
        signals[side] = st == 1
    return {"long_signal": signals["long"], "short_signal": signals["short"], "states": states,
            "checks": ctx.checks, "indicators": ctx.indicators, "exits": _exit_arrays(rs, ctx.a, ctx),
            "session_ok": session_ok, "spec": rs, "notes": ctx.notes, "n": n,
            "spec_sha256": spec_sha256(spec)}


def _nan_none(x):
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def bar_checks(res: dict, i: int) -> list[dict]:
    """Checks at bar i in the shape InspectorMixin.record_candle expects."""
    out = []
    for name, c in res["checks"].items():
        s = int(c["state"][i])
        out.append({"name": name, "value": _nan_none(c["left"][i]) if c["left"] is not None else None,
                    "operator": c["operator"],
                    "threshold": _nan_none(c["right"][i]) if c["right"] is not None else None,
                    "result": "PASS" if s == 1 else "FAIL" if s == 0 else "SKIP"})
    return out


def bar_decision(res: dict, i: int) -> tuple[str, str]:
    if res["long_signal"][i]:
        return "PASS", "LONG_ENTRY_CONDITIONS_MET"
    if res["short_signal"][i]:
        return "PASS", "SHORT_ENTRY_CONDITIONS_MET"
    if not res["session_ok"][i]:
        return "SKIP", "OUTSIDE_SESSION"
    rs = res["spec"]
    active = [int(res["states"][s][i]) for s in ("long", "short") if rs["direction"] in (s, "both")]
    if active and all(s == -1 for s in active):
        return "SKIP", "INDICATOR_WARMUP"
    return "FAIL", "ENTRY_CONDITIONS_NOT_MET"


# --------------------------------------------------------------------------- description (Dutch)

_PATTERN_NL = {
    "inside_bar": "inside bar (high lager en low hoger dan de vorige candle)",
    "nr4": "NR4 (kleinste range van de laatste 4 candles)",
    "nr7": "NR7 (kleinste range van de laatste 7 candles)",
    "bullish_engulfing": "bullish engulfing",
    "bearish_engulfing": "bearish engulfing",
    "breakout_high": "slotkoers boven de hoogste high van de voorgaande {n} candles",
    "breakout_low": "slotkoers onder de laagste low van de voorgaande {n} candles",
    "liquidity_sweep_high": "liquidity sweep boven de hoogste high van de voorgaande {n} candles (sluit terug eronder)",
    "liquidity_sweep_low": "liquidity sweep onder de laagste low van de voorgaande {n} candles (sluit terug erboven)",
    "fvg_bull": "bullish fair value gap in de laatste {n} candle(s)",
    "fvg_bear": "bearish fair value gap in de laatste {n} candle(s)",
    "pin_bull": "bullish pin bar (onderste lont >= 60% van de range, body <= 35%)",
    "pin_bear": "bearish pin bar (bovenste lont >= 60% van de range, body <= 35%)",
    "crt_bull": "CRT bullish: candle 2 prikt onder candle 1 en sluit erbinnen, candle 3 sluit boven de high van candle 2",
    "crt_bear": "CRT bearish: candle 2 prikt boven candle 1 en sluit erbinnen, candle 3 sluit onder de low van candle 2",
    "fvg_bull_retest": "terugkeer in de laatste bullish fair value gap (gevormd in de laatste {n} candles)",
    "fvg_bear_retest": "terugkeer in de laatste bearish fair value gap (gevormd in de laatste {n} candles)",
    "range_touch_high": "sweep boven range-high ({n} candles, >= {t} aanrakingen binnen {tol} x ATR) en slot terug eronder",
    "range_touch_low": "sweep onder range-low ({n} candles, >= {t} aanrakingen binnen {tol} x ATR) en slot terug erboven",
    "random_entry": "pseudo-willekeurige instap (kans {p} per candle, seed {seed}; baseline/controle)",
    "swing_break_high": "slot kruist boven de laatst bevestigde swing high (sterkte {n})",
    "swing_break_low": "slot kruist onder de laatst bevestigde swing low (sterkte {n})",
    "mss_bull": "market structure shift omhoog: slot boven laatste swing high na een lagere high (sterkte {n})",
    "mss_bear": "market structure shift omlaag: slot onder laatste swing low na een hogere low (sterkte {n})",
    "structure_up": "hogere highs en hogere lows (laatste twee bevestigde swings, sterkte {n})",
    "structure_down": "lagere highs en lagere lows (laatste twee bevestigde swings, sterkte {n})",
}
_OP_NL = {">": "is groter dan", "<": "is kleiner dan", ">=": "is groter dan of gelijk aan",
          "<=": "is kleiner dan of gelijk aan", "crosses_above": "kruist omhoog door",
          "crosses_below": "kruist omlaag door"}


def _cond_text(c: dict) -> str:
    if "all" in c:
        return "(" + " EN ".join(_cond_text(x) for x in c["all"]) + ")"
    if "any" in c:
        return "(" + " OF ".join(_cond_text(x) for x in c["any"]) + ")"
    if "not" in c:
        return "NIET " + _cond_text(c["not"])
    if "pattern" in c:
        n = int(c.get("lookback", _PATTERN_DEFAULT_LOOKBACK.get(
            c["pattern"], 20 if c["pattern"].startswith(("breakout", "liquidity")) else 1)))
        return _PATTERN_NL[c["pattern"]].format(n=n, t=int(c.get("touches", 3)), tol=c.get("tolerance_atr", 0.25),
                                                p=c.get("probability", ""), seed=c.get("seed", ""))
    if "calendar" in c:
        vals = ", ".join(str(int(v)) for v in c["in"])
        return (f"uur van de candle-opening in [{vals}]" if c["calendar"] == "hour"
                else f"weekdag (ma=0) in [{vals}]") + (f" ({c['timezone']})" if c.get("timezone") else "")
    return f"{operand_label(c['left'])} {_OP_NL[c['op']]} {operand_label(c['right'])}"


def describe(spec: dict) -> str:
    """Dutch human-readable rules text."""
    rs = resolve(spec)
    L = [f"STRATEGIE: {rs['name']} - {rs['symbol']} {rs['timeframe']} - richting: "
         f"{ {'long': 'alleen long', 'short': 'alleen short', 'both': 'long en short'}[rs['direction']] }"]
    if rs.get("description"):
        L.append(str(rs["description"]))
    L.append("")
    when = []
    s = rs.get("session")
    when.append(f"alleen handelen tussen {s['start']} en {s['end']} ({s['timezone']}, zomertijd-correct; "
                f"candle-openingstijd telt)" if s else "geen sessiefilter")
    if rs.get("cooldown_bars"):
        when.append(f"na een trade {rs['cooldown_bars']} candle(s) wachten (cooldown)")
    if rs.get("max_trades_per_day"):
        when.append(f"maximaal {rs['max_trades_per_day']} trade(s) per dag")
    when.append("een positie tegelijk; signalen worden beoordeeld op de slotkoers van elke candle")
    L.append("WANNEER: " + "; ".join(when))
    for side, nl in (("long", "LONG"), ("short", "SHORT")):
        if rs["direction"] in (side, "both") and side in rs["entry"]:
            L.append(f"ENTRY {nl}: {_cond_text(rs['entry'][side])}")
    od = rs["order"]
    if od["type"] == "market":
        L.append("ORDER: marktorder op de open van de volgende candle (long koopt op Ask, short verkoopt op Bid)")
    else:
        L.append(f"ORDER: {od['type']}-order op slotkoers van signaalcandle "
                 f"{'+/-' if od['offset_atr'] else ''}{od['offset_atr'] or ''}"
                 f"{' x ATR(' + str(od['atr_period']) + ')' if od['offset_atr'] else ''}, "
                 f"verloopt na {od['expiry_bars']} candle(s)")
    st = rs["stop"]
    L.append("STOP LOSS: " + {
        "atr": f"{st.get('mult')} x ATR({st.get('period')}) vanaf de instap",
        "points": f"{st.get('value')} punten vanaf de instap",
        "percent": f"{st.get('value')}% van de instapprijs",
        "swing": f"laagste low (long) / hoogste high (short) van de laatste {st.get('lookback')} candles"}[st["type"]])
    tg = rs.get("target")
    L.append("TAKE PROFIT: " + ("geen" if not tg else
             f"{tg['value']} x R (afstand tot de stop)" if tg["type"] == "r_multiple"
             else f"niveau {operand_label(tg['operand'])} op de signaalcandle (alleen als het aan de winstkant ligt)"
             if tg["type"] == "level" else f"{tg['mult']} x ATR({tg['period']}) vanaf de instap"))
    L.append(f"RISK: {rs['risk']['percent']}% van de rekening per trade (positiegrootte uit stopafstand)")
    ex = []
    if rs.get("break_even"):
        ex.append(f"break-even: stop naar instapprijs zodra +{rs['break_even']['activate_r']}R is bereikt")
    if rs.get("trailing"):
        t = rs["trailing"]
        ex.append(f"trailing stop: vanaf +{t['activate_r']}R op {t['atr_mult']} x ATR({t['period']}) onder de hoogste "
                  f"high sinds instap (short: omgekeerd)")
    if rs.get("partial_exits"):
        ex.append("gedeeltelijke exits: " + ", ".join(
            f"{round(lv['fraction'] * 100)}% bij +{lv['r']}R" + (f" (stop restant naar {lv['stop_r']:+g}R)"
                                                                if lv.get("stop_r") is not None else "")
            for lv in rs["partial_exits"]))
    if rs.get("time_exit_bars"):
        ex.append(f"tijd-exit na {rs['time_exit_bars']} candle(s)")
    L.append("EXIT: " + ("; ".join(ex) if ex else "alleen stop loss / take profit"))
    if rs["params"]:
        L.append("PARAMETERS: " + ", ".join(f"{p['name']}={p['default']} ({p['min']}-{p['max']})" for p in rs["params"]))
    return "\n".join(L)
