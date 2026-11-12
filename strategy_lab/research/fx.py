"""Causal FX conversion service on real Dukascopy M1 Bid/Ask candles (V102).

Rules (see FX_CONVERSION_VALIDATION.md):
* The rate at ``ts`` is the OPEN of the last M1 bar whose open time is at or before ``ts`` (never a later bar).
* A pair is used directly or inverted (USD->EUR = 1/EURUSD); other pairs cross via USD (JPY->EUR = USDJPY^-1 * EURUSD).
* The rate is never 1.0 unless both currencies are the same. Missing data, a bar older than ``max_staleness`` or a
  non-positive/NaN price raises :class:`FxRateUnavailable` (``try_convert`` returns status FX_RATE_UNAVAILABLE).
* ``side`` names the price of the quoted pair that is used (``mid`` default). ``conservative`` picks, per leg and per
  amount sign, the realistic settlement side: a gain converts to FEWER account units (sell base at Bid, buy base at
  Ask), a loss to MORE account units. Broker conversion mark-ups are unknown (UNAVAILABLE) and not modelled.
"""
from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

OK, UNAVAILABLE = "OK", "FX_RATE_UNAVAILABLE"
SIDES = ("bid", "ask", "mid", "conservative")
# Pairs available as Dukascopy M1 series; every route between two currencies is built from these (crosses via USD).
PAIRS = ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDJPY", "USDCAD", "USDCHF")
_CHUNK_DAYS = 7
_MAX_CHUNKS = 64


class FxRateUnavailable(Exception):
    """No trustworthy conversion rate exists for the requested currencies/time."""


@dataclass(frozen=True)
class Conversion:
    from_ccy: str
    to_ccy: str
    ts: pd.Timestamp
    rate: float
    side: str
    source: str
    data_version: str
    uncertainty: float
    status: str = OK


def _ccy(value) -> str:
    out = str(value or "").strip().upper()
    if not out:
        raise ValueError("currency is required")
    return out


def _utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def route(from_ccy: str, to_ccy: str) -> list[tuple[str, bool]] | None:
    """Legs ``(pair, invert)`` converting ``from_ccy`` to ``to_ccy``: [] for identity, None when no route exists.
    ``invert`` False = base->quote (rate = price), True = quote->base (rate = 1/price)."""
    a, b = _ccy(from_ccy), _ccy(to_ccy)
    if a == b:
        return []
    if a + b in PAIRS:
        return [(a + b, False)]
    if b + a in PAIRS:
        return [(b + a, True)]
    if "USD" in (a, b):
        return None
    first, second = route(a, "USD"), route("USD", b)
    return None if first is None or second is None else first + second


def asof(series: pd.Series, times) -> np.ndarray:
    """Value at the last index at or before each of ``times`` (NaN where none yet). Invalid values stay NaN: an
    invalid print is never papered over with an older price."""
    idx = pd.DatetimeIndex(series.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    t = pd.DatetimeIndex(times)
    t = t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")
    pos = idx.searchsorted(t, side="right") - 1
    vals = series.to_numpy(float)
    out = np.where(pos >= 0, vals[np.clip(pos, 0, max(len(vals) - 1, 0))], np.nan) if len(vals) else np.full(len(t), np.nan)
    return out


def _version(manifest: dict) -> str:
    bid = (manifest.get("bid_source") or {}).get("sha256")
    ask = (manifest.get("ask_source") or {}).get("sha256")
    return hashlib.sha256(f"{bid}:{ask}".encode()).hexdigest() if bid and ask else "UNVERSIONED"


def _leg_side(invert: bool, side: str, positive: bool) -> str:
    """Pair price side for one leg. conservative: gain -> fewer account units, loss -> more."""
    if side != "conservative":
        return side
    fewer = "ask" if invert else "bid"
    more = "bid" if invert else "ask"
    return fewer if positive else more


def _price(bid: float, ask: float, which: str) -> float:
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2.0}[which]


class FxService:
    def __init__(self, data_manager=None, max_staleness: timedelta = timedelta(hours=72)):
        if max_staleness <= timedelta(0):
            raise ValueError("max_staleness must be positive")
        self.dm = data_manager
        self.max_staleness = max_staleness
        self._chunks: OrderedDict = OrderedDict()

    # ------------------------------------------------------------------ data access
    def _chunk(self, pair: str, index: int):
        key = (pair, index)
        hit = self._chunks.get(key)
        if hit is not None:
            self._chunks.move_to_end(key)
            return hit
        if self.dm is None:
            raise FxRateUnavailable(f"no data manager: {pair} M1 candles unavailable")
        start = pd.Timestamp(index * _CHUNK_DAYS, unit="D", tz="UTC")
        try:
            frame, manifest = self.dm.candles(pair, "M1", start.isoformat(), (start + timedelta(days=_CHUNK_DAYS)).isoformat())
        except Exception as exc:  # missing symbol file / DataError: reported as unavailable, never invented
            raise FxRateUnavailable(f"{pair} M1 candles unavailable: {type(exc).__name__}: {exc}") from exc
        frame = frame[["bid_open", "ask_open"]]
        if frame.index.tz is None:
            frame = frame.tz_localize("UTC")
        else:
            frame = frame.tz_convert("UTC")
        item = (frame.sort_index(), _version(manifest or {}))
        self._chunks[key] = item
        while len(self._chunks) > _MAX_CHUNKS:
            self._chunks.popitem(last=False)
        return item

    def _bar(self, pair: str, ts: pd.Timestamp):
        """(bar_time, bid_open, ask_open, data_version) of the last M1 bar at or before ``ts`` within max_staleness."""
        first = int(math.floor((ts - self.max_staleness).value / 1e9 / 86400 / _CHUNK_DAYS))
        last = int(math.floor(ts.value / 1e9 / 86400 / _CHUNK_DAYS))
        for index in range(last, first - 1, -1):
            frame, version = self._chunk(pair, index)
            pos = int(frame.index.searchsorted(ts, side="right")) - 1
            if pos < 0:
                continue
            bar_time = frame.index[pos]
            if ts - bar_time > self.max_staleness:
                raise FxRateUnavailable(f"{pair}: last bar {bar_time.isoformat()} is older than "
                                        f"{self.max_staleness} before {ts.isoformat()} (stale)")
            return bar_time, float(frame["bid_open"].iloc[pos]), float(frame["ask_open"].iloc[pos]), version
        raise FxRateUnavailable(f"{pair}: no M1 bar within {self.max_staleness} at or before {ts.isoformat()} (missing or stale)")

    # ------------------------------------------------------------------ public API
    def rate(self, from_ccy, to_ccy, ts, side="mid", *, positive: bool = True) -> Conversion:
        a, b, t = _ccy(from_ccy), _ccy(to_ccy), _utc(ts)
        if side not in SIDES:
            raise ValueError(f"side must be one of {SIDES}")
        if a == b:
            return Conversion(a, b, t, 1.0, side, "IDENTITY", "IDENTITY", 0.0, OK)
        legs = route(a, b)
        if legs is None:
            raise FxRateUnavailable(f"no FX route {a}->{b} (known pairs: {', '.join(PAIRS)})")
        rate, uncertainty, sides, versions = 1.0, 0.0, [], []
        for pair, invert in legs:
            _, bid, ask, version = self._bar(pair, t)
            if not all(math.isfinite(x) and x > 0 for x in (bid, ask)) or ask < bid:
                raise FxRateUnavailable(f"{pair}: invalid price at {t.isoformat()} (bid={bid}, ask={ask})")
            used = _leg_side(invert, side, positive)
            price = _price(bid, ask, used)
            rate *= (1.0 / price) if invert else price
            uncertainty += (ask - bid) / 2.0 / ((ask + bid) / 2.0)
            sides.append(used)
            versions.append(version)
        source = (f"DUKASCOPY_M1:{legs[0][0]}" if len(legs) == 1 else "CROSS:" + "*".join(p for p, _ in legs))
        version = versions[0] if len(set(versions)) == 1 else hashlib.sha256("+".join(versions).encode()).hexdigest()
        resolved = sides[0] if len(set(sides)) == 1 else "mixed"
        return Conversion(a, b, t, float(rate), resolved, source, version, float(uncertainty), OK)

    def convert(self, amount, from_ccy, to_ccy, ts, side="mid") -> tuple[float, Conversion]:
        value = float(amount)
        conv = self.rate(from_ccy, to_ccy, ts, side, positive=value >= 0)
        return value * conv.rate, conv

    def try_convert(self, amount, from_ccy, to_ccy, ts, side="mid") -> tuple[float | None, Conversion]:
        try:
            return self.convert(amount, from_ccy, to_ccy, ts, side)
        except FxRateUnavailable as exc:
            legs = route(from_ccy, to_ccy)
            source = ("CROSS:" + "*".join(p for p, _ in legs) if legs and len(legs) > 1
                      else f"DUKASCOPY_M1:{legs[0][0]}" if legs else "UNROUTABLE")
            return None, Conversion(_ccy(from_ccy), _ccy(to_ccy), _utc(ts), math.nan, side, source,
                                    str(exc), math.nan, UNAVAILABLE)

    def convert_portfolio(self, balances: dict, to_ccy, ts, side="mid") -> tuple[float | None, list[Conversion]]:
        """Total of ``{currency: amount}`` in ``to_ccy`` at ``ts``. One unavailable leg makes the total None
        (a partial sum would be a fake number); the per-currency conversions are always returned."""
        parts, total = [], 0.0
        for ccy, amount in balances.items():
            value, conv = self.try_convert(amount, ccy, to_ccy, ts, side)
            parts.append(conv)
            total = None if value is None or total is None else total + value
        return total, parts

    def series(self, from_ccy, to_ccy, timeframe: str = "H1", start=None, end=None, side: str = "mid") -> pd.Series | None:
        """``to_ccy`` per 1 ``from_ccy`` at every candle OPEN of ``timeframe`` (causal). Empty Series for identity,
        None when a leg is unavailable. Invalid prices stay NaN (never filled from an older bar)."""
        a, b = _ccy(from_ccy), _ccy(to_ccy)
        legs = route(a, b)
        if legs is None:
            return None
        if not legs:
            return pd.Series(dtype=float)
        if self.dm is None:
            return None
        out = None
        for pair, invert in legs:
            try:
                frame, _ = self.dm.candles(pair, timeframe, start, end)
            except Exception:
                return None
            if frame.empty:
                return None
            if frame.index.tz is None:
                frame = frame.tz_localize("UTC")
            used = _leg_side(invert, side, True)
            price = _price(frame["bid_open"], frame["ask_open"], used)
            price = price.where((frame["bid_open"] > 0) & (frame["ask_open"] >= frame["bid_open"]) & np.isfinite(price))
            leg = (1.0 / price) if invert else price
            if out is None:
                out = leg
            else:
                grid = out.index.union(leg.index)
                out = pd.Series(asof(out, grid) * asof(leg, grid), index=grid)
        return out


def convert_trades(trades: list[dict], series: pd.Series | None, key: str = "exit_time",
                   max_staleness: timedelta | None = timedelta(hours=72)) -> list[dict]:
    """Copy of ``trades`` with pnl/costs converted at the rate known at ``trade[key]`` (exit = realisation time).
    Raises FxRateUnavailable on a missing, stale or invalid rate; ``series`` None/empty = no conversion needed."""
    if series is None or len(series) == 0:
        return [dict(t) for t in trades]
    idx = pd.DatetimeIndex(series.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    out = []
    for t in trades:
        when = _utc(t[key])
        pos = int(idx.searchsorted(when, side="right")) - 1
        if pos < 0:
            raise FxRateUnavailable(f"no conversion rate known at or before {when.isoformat()}")
        r = float(series.iloc[pos])
        if not math.isfinite(r) or r <= 0:
            raise FxRateUnavailable(f"invalid conversion rate {r} at {when.isoformat()}")
        if max_staleness is not None and when - idx[pos] > max_staleness:
            raise FxRateUnavailable(f"conversion rate at {when.isoformat()} is stale (last {idx[pos].isoformat()})")
        out.append({**t, "pnl": float(t["pnl"]) * r, "costs": float(t.get("costs") or 0.0) * r, "fx_rate": r})
    return out
