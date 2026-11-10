"""Polite, bounded reader for Dukascopy's public historical datafeed (one UTC day, one symbol, Bid and Ask).

Daily candles: https://datafeed.dukascopy.com/datafeed/{SYMBOL}/{YYYY}/{MM0}/{DD}/{BID|ASK}_candles_min_1.bi5 with a
ZERO-based month. Each file is LZMA compressed, 24-byte big-endian records ``>5if``:
(seconds since 00:00 UTC, open, close, low, high, volume) with prices as integers scaled by a per-instrument factor.
Fallback ticks: .../{HH}h_ticks.bi5, 20-byte records ``>3i2f``: (ms since the hour, ask, bid, ask volume, bid volume).

Access rules are fixed on purpose and never relaxed: honest User-Agent, at most one request per second
process-wide, at most two candle files (or 24 hourly tick files) per symbol-day, HTTP 429 is honoured, and any
HTTP 202/403 or WAF header stops the run (BLOCKED). A challenge is never solved or bypassed.
"""
from __future__ import annotations

import email.utils
import json
import lzma
import sqlite3
import struct
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from .data_manager import SYMBOLS, DataError

BASE_URL = "https://datafeed.dukascopy.com/datafeed"
USER_AGENT = "DukascopyStrategyLab/1.0 (local research)"
MIN_INTERVAL_SECONDS = 1.0
MAX_FILES_PER_SYMBOL_DAY = 2
MAX_TICK_FILES_PER_SYMBOL_DAY = 24
REQUEST_TIMEOUT_SECONDS = 90
BLOCK_COOLDOWN = timedelta(hours=6)
DEFAULT_RETRY_AFTER_SECONDS = 3600
MAX_RETRY_AFTER = timedelta(hours=24)
BACKOFF_STEPS = (timedelta(minutes=2), timedelta(minutes=5), timedelta(minutes=15), timedelta(minutes=30))
BACKOFF_MAX = timedelta(hours=1)
TRANSIENT_RETRY_WAITS = (5.0, 15.0)  # same file, same run: 503/timeouts from the CDN are usually momentary
PRICE_TOLERANCE = 0.30
RECORD = struct.Struct(">5if")
TICK = struct.Struct(">3i2f")
CANDLE_FILE_LIMIT = 2_000_000
TICK_FILE_LIMIT = 16_000_000
TICK_RAW_LIMIT = 80_000_000
TRANSPORT_LIMIT = TICK_FILE_LIMIT + 1
FIELDS = ("open", "high", "low", "close", "volume")

# Price = integer / factor. Verified per symbol against the original CSV exports (see DAILY_DUKASCOPY_PIPELINE.md).
POINT_FACTORS = {
    "XAUUSD": 1_000,
    "EURUSD": 100_000,
    "GBPUSD": 100_000,
    "USDJPY": 1_000,
    "USA30IDXUSD": 1_000,
    "USA500IDXUSD": 1_000,
    "USATECHIDXUSD": 1_000,
    "DEUIDXEUR": 1_000,
    "BTCUSD": 10,
}


class DatafeedBlocked(DataError):
    """HTTP 202/403 or a WAF header: stop the whole run, never bypass."""

    def __init__(self, message: str, evidence: dict):
        super().__init__(message)
        self.evidence = evidence


class DatafeedDelayed(DataError):
    """HTTP 429, 5xx or an unreachable host: retry in a later scheduled run."""

    def __init__(self, message: str, retry_after_s: int | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


class RateLimiter:
    """Process-wide spacing between requests; the interval can only be raised above one second."""

    def __init__(self, interval: float = MIN_INTERVAL_SECONDS, *, clock=time.monotonic, sleep=time.sleep):
        self.interval = max(MIN_INTERVAL_SECONDS, float(interval))
        self._clock, self._sleep = clock, sleep
        self._last: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            if self._last is not None:
                remaining = self._last + self.interval - self._clock()
                if remaining > 0:
                    self._sleep(remaining)
            self._last = self._clock()


GLOBAL_LIMITER = RateLimiter()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def urllib_transport(url: str) -> Response:
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as reply:
            return Response(reply.status, {k.lower(): v for k, v in reply.headers.items()}, reply.read(TRANSPORT_LIMIT))
    except urllib.error.HTTPError as exc:
        return Response(exc.code, {k.lower(): v for k, v in exc.headers.items()}, b"")
    except (urllib.error.URLError, OSError) as exc:
        raise DatafeedDelayed(f"Dukascopy datafeed unreachable: {type(exc).__name__}") from exc


def instrument(symbol: str) -> str:
    """Dukascopy datafeed instrument name for a lab symbol (US500 -> USA500IDXUSD)."""
    return SYMBOLS.get(symbol.upper(), symbol)


def feed_url(symbol: str, day: date, side: str) -> str:
    return f"{BASE_URL}/{instrument(symbol)}/{day.year:04d}/{day.month - 1:02d}/{day.day:02d}/{side}_candles_min_1.bi5"


def tick_url(symbol: str, day: date, hour: int) -> str:
    return f"{BASE_URL}/{instrument(symbol)}/{day.year:04d}/{day.month - 1:02d}/{day.day:02d}/{hour:02d}h_ticks.bi5"


def _retry_after(value: str | None, now: datetime) -> int:
    cap = int(MAX_RETRY_AFTER.total_seconds())
    if not value:
        return DEFAULT_RETRY_AFTER_SECONDS
    value = value.strip()
    if value.isdigit():
        return min(int(value), cap)
    try:
        return min(cap, max(0, int((email.utils.parsedate_to_datetime(value) - now).total_seconds())))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS


def _decompress(body: bytes, limit: int) -> bytes:
    try:
        raw = lzma.LZMADecompressor().decompress(body, max_length=limit + 1)
    except lzma.LZMAError as exc:
        raise DataError("Dukascopy file is not valid LZMA data") from exc
    if len(raw) > limit:
        raise DataError("Dukascopy file expands beyond the size limit")
    return raw


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=FIELDS, index=pd.DatetimeIndex([], tz="UTC"), dtype="float64")


def decode_bi5(body: bytes, day: date, factor: int) -> pd.DataFrame:
    """Decode one candle file into OHLCV rows indexed by UTC minute. Only records the provider sent are returned."""
    if not body:
        return _empty()
    raw = _decompress(body, 1440 * RECORD.size)
    if len(raw) % RECORD.size:
        raise DataError("Dukascopy file has an unexpected record layout")
    if not raw:
        return _empty()
    records = np.array(list(RECORD.iter_unpack(raw)), dtype="float64")
    seconds = records[:, 0].astype("int64")
    if (seconds % 60).any() or seconds.min() < 0 or seconds.max() >= 86400 or (np.diff(seconds) <= 0).any():
        raise DataError("Dukascopy file has off-minute, out-of-day or non-increasing timestamps")
    prices = np.round(records[:, 1:5] / factor, int(round(np.log10(factor))))  # file order: open, close, low, high
    start = pd.Timestamp(day, tz="UTC")
    return pd.DataFrame({
        "open": prices[:, 0], "high": prices[:, 3], "low": prices[:, 2], "close": prices[:, 1],
        "volume": np.round(records[:, 5], 2),
    }, index=pd.DatetimeIndex(start + pd.to_timedelta(seconds, unit="s")))


def decode_ticks(body: bytes, day: date, hour: int, factor: int) -> pd.DataFrame:
    """Decode one hourly tick file into rows (ask, bid) indexed by UTC millisecond timestamp."""
    raw = _decompress(body, TICK_RAW_LIMIT) if body else b""
    if len(raw) % TICK.size:
        raise DataError("Dukascopy tick file has an unexpected record layout")
    if not raw:
        return pd.DataFrame(columns=("ask", "bid"), index=pd.DatetimeIndex([], tz="UTC"), dtype="float64")
    records = np.array(list(TICK.iter_unpack(raw)), dtype="float64")
    offsets = records[:, 0].astype("int64")
    if offsets.min() < 0 or offsets.max() >= 3_600_000 or (np.diff(offsets) < 0).any():
        raise DataError("Dukascopy tick file has out-of-hour or decreasing timestamps")
    decimals = int(round(np.log10(factor)))
    start = pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=hour)
    return pd.DataFrame({"ask": np.round(records[:, 1] / factor, decimals), "bid": np.round(records[:, 2] / factor, decimals)},
                        index=pd.DatetimeIndex(start + pd.to_timedelta(offsets, unit="ms")))


def ticks_to_minutes(ticks: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Bid and Ask M1 OHLC from ticks; volume is the tick count of the minute."""
    groups = ticks.groupby(ticks.index.floor("min"))
    count = groups.size().astype("float64")
    sides = []
    for side in ("bid", "ask"):
        prices = groups[side]
        sides.append(pd.DataFrame({"open": prices.first(), "high": prices.max(), "low": prices.min(),
                                   "close": prices.last(), "volume": count}))
    return sides[0], sides[1]


def fill_flat_minutes(frame: pd.DataFrame, expected: pd.DatetimeIndex) -> tuple[pd.DataFrame, int]:
    """Add flat zero-volume minutes for in-session minutes without any tick, after the day's first tick.

    This is the same representation the provider's candle files and the original exports use for no-tick minutes.
    """
    if frame.empty:
        return frame, 0
    missing = expected[(expected > frame.index[0]) & ~expected.isin(frame.index)]
    if not len(missing):
        return frame, 0
    union = frame.index.union(missing)
    closes = frame.close.reindex(union).ffill()
    filled = frame.reindex(union)
    gap = filled.close.isna()
    for column in ("open", "high", "low", "close"):
        filled.loc[gap, column] = closes[gap]
    filled.loc[gap, "volume"] = 0.0
    return filled, len(missing)


def drop_closed_flat_minutes(frame: pd.DataFrame, expected: pd.DatetimeIndex) -> pd.DataFrame:
    """Remove no-tick placeholder minutes (flat, volume 0) outside the configured session.

    The original exports hold no rows while a market is closed, but do keep in-session zero-volume minutes.
    Records with real ticks outside the session are kept so the calendar mismatch is reported, not hidden.
    """
    flat = (frame.volume == 0) & (frame.open == frame.high) & (frame.open == frame.low) & (frame.open == frame.close)
    return frame.loc[~(flat & ~frame.index.isin(expected))]


def check_price_scale(frames: tuple[pd.DataFrame, ...], reference: float, symbol: str, day: date) -> None:
    """Reject a day whose decoded prices are not within +-30% of the last known close (wrong scale factor)."""
    low, high = reference * (1 - PRICE_TOLERANCE), reference * (1 + PRICE_TOLERANCE)
    for frame in frames:
        if frame.empty:
            continue
        prices = frame[["open", "high", "low", "close"]].to_numpy()
        if prices.min() < low or prices.max() > high:
            raise DataError(f"{symbol} {day}: decoded prices {prices.min()}..{prices.max()} are outside "
                            f"+-{PRICE_TOLERANCE:.0%} of last known close {reference}; wrong point factor, day rejected")


class FeedClient:
    """One shared HTTP gate: rate limit, per-symbol-day file budget, 429 pause and WAF detection."""

    def __init__(self, transport: Callable[[str], Response] = urllib_transport, limiter: RateLimiter | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.transport = transport
        self.limiter = limiter or GLOBAL_LIMITER
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.files: dict[tuple, int] = {}
        self.paused_until: datetime | None = None

    def get(self, url: str, key: tuple, budget: int, max_bytes: int) -> bytes | None:
        """Body of a 200 reply, None for 404. Raises DatafeedBlocked / DatafeedDelayed per the access rules."""
        now = self.clock()
        if self.paused_until and now < self.paused_until:
            raise DatafeedDelayed("Dukascopy asked for a pause (HTTP 429); no request sent",
                                  int((self.paused_until - now).total_seconds()))
        if self.files.get(key, 0) >= budget:
            raise DataError(f"Request budget of {budget} files for {key[0]} {key[1]} exhausted")
        self.files[key] = self.files.get(key, 0) + 1
        reply = self._fetch_with_retry(url)
        waf = {k: v for k, v in reply.headers.items() if k.startswith("x-amzn-waf")}
        if reply.status in (202, 403) or waf:
            raise DatafeedBlocked(f"Dukascopy datafeed blocked the request (HTTP {reply.status}); run stopped",
                                  {"url": url, "status": reply.status, "headers": waf or
                                   {k: v for k, v in reply.headers.items() if k in ("server", "x-cache", "via")}})
        if reply.status == 429:
            seconds = _retry_after(reply.headers.get("retry-after"), now)
            self.paused_until = now + timedelta(seconds=seconds)
            raise DatafeedDelayed(f"HTTP 429 from Dukascopy (retry_after_s={seconds})", seconds)
        if reply.status == 404:
            return None
        if reply.status >= 500:
            raise DatafeedDelayed(f"HTTP {reply.status} from Dukascopy (na {1 + len(TRANSIENT_RETRY_WAITS)} pogingen)")
        if reply.status != 200:
            raise DataError(f"Unexpected HTTP {reply.status} from Dukascopy datafeed")
        if len(reply.body) > max_bytes:
            raise DataError("Dukascopy file exceeds size limit")
        return reply.body


    def _fetch_with_retry(self, url: str) -> Response:
        """Retry the same file after a 5xx or unreachable host, with short pauses (rate limit still applies)."""
        waits = list(getattr(self, "retry_waits", TRANSIENT_RETRY_WAITS))
        while True:
            self.limiter.wait()
            try:
                reply = self.transport(url)
            except DatafeedDelayed:
                if not waits:
                    raise
            else:
                if reply.status < 500 or not waits:
                    return reply
            getattr(self.limiter, "_sleep", time.sleep)(waits.pop(0))


class _FeedProvider:
    session_aware = True

    def __init__(self, *, transport: Callable[[str], Response] = urllib_transport, limiter: RateLimiter | None = None,
                 reference: Callable[[str], float | None] | None = None, today: Callable[[], date] | None = None,
                 factors: dict[str, int] | None = None, clock: Callable[[], datetime] | None = None,
                 client: FeedClient | None = None):
        self.client = client or FeedClient(transport, limiter, clock)
        self.reference = reference
        self.factors = dict(factors or POINT_FACTORS)
        self.clock = clock or self.client.clock
        self._today = today
        self.last_detail: dict = {}

    @property
    def _files(self) -> dict:
        return self.client.files

    def today(self) -> date:
        return self._today() if self._today else self.clock().date()

    def _preflight(self, symbol: str, day: date) -> float:
        if instrument(symbol) not in self.factors:
            raise DataError(f"No verified point factor for {symbol}")
        if day >= self.today():
            raise DataError(f"{day} is not a completed UTC day")
        reference = self.reference(symbol) if self.reference else None
        if reference is None or not reference > 0:
            raise DataError(f"{symbol}: no last known close to validate the price scale; day not fetched")
        return reference


class DatafeedProvider(_FeedProvider):
    """Primary method: ``fetch(symbol, day)`` returns (bid, ask) frames shaped exactly like the JForex day export."""

    def fetch(self, symbol: str, day: date, expected: pd.DatetimeIndex | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
        reference = self._preflight(symbol, day)
        frames = []
        for side in ("BID", "ASK"):
            body = self.client.get(feed_url(symbol, day, side), (symbol, day), MAX_FILES_PER_SYMBOL_DAY, CANDLE_FILE_LIMIT)
            if body is None:
                raise FileNotFoundError(f"{symbol} {day} {side}")
            frame = decode_bi5(body, day, self.factors[instrument(symbol)])
            if frame.empty:
                raise FileNotFoundError(f"{symbol} {day} {side} empty")
            frames.append(frame)
        check_price_scale(tuple(frames), reference, symbol, day)
        if expected is not None:
            frames = [drop_closed_flat_minutes(frame, expected) for frame in frames]
        self.last_detail = {"files": 2}
        return frames[0], frames[1]


class DatafeedTicksProvider(_FeedProvider):
    """Fallback: build M1 from the hourly tick files of the session hours (only used when candles are missing)."""

    def fetch(self, symbol: str, day: date, expected: pd.DatetimeIndex | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
        reference = self._preflight(symbol, day)
        hours = sorted(set(expected.hour)) if expected is not None else range(24)
        key = (symbol, day, "ticks")
        parts = []
        for hour in hours:
            body = self.client.get(tick_url(symbol, day, hour), key, MAX_TICK_FILES_PER_SYMBOL_DAY, TICK_FILE_LIMIT)
            if body is None:
                raise FileNotFoundError(f"{symbol} {day} {hour:02d}h ticks")
            parts.append(decode_ticks(body, day, hour, self.factors[instrument(symbol)]))
        ticks = pd.concat(parts) if parts else decode_ticks(b"", day, 0, self.factors[instrument(symbol)])
        if ticks.empty:
            raise FileNotFoundError(f"{symbol} {day} no ticks")
        bid, ask = ticks_to_minutes(ticks)
        check_price_scale((bid, ask), reference, symbol, day)
        filled = 0
        if expected is not None:
            (bid, count), (ask, _) = fill_flat_minutes(bid, expected), fill_flat_minutes(ask, expected)
            filled = count
        self.last_detail = {"files": len(parts), "ticks": len(ticks), "filled_flat_minutes": filled}
        return bid, ask


def backoff_delay(failures: int) -> timedelta:
    """Transient outages (5xx, timeouts): 2, 5, 15, 30 min, then 1 h per further consecutive failure.
    429 uses Retry-After and a block uses BLOCK_COOLDOWN instead."""
    index = max(0, failures - 1)
    return BACKOFF_STEPS[index] if index < len(BACKOFF_STEPS) else BACKOFF_MAX


STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS fetch_backoff (
    symbol TEXT NOT NULL, day TEXT NOT NULL, failures INTEGER NOT NULL, next_attempt_at TEXT NOT NULL,
    reason TEXT NOT NULL, blocked INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (symbol, day)
);
CREATE TABLE IF NOT EXISTS fetch_methods (
    symbol TEXT NOT NULL, day TEXT NOT NULL, observed_at TEXT NOT NULL, method TEXT NOT NULL, detail TEXT NOT NULL,
    PRIMARY KEY (symbol, day, observed_at)
);
"""
GLOBAL = "*"


class FetchState:
    """Retry backoff and per-day acquisition method, kept beside the partitions in the same SQLite file."""

    def __init__(self, path: Path):
        self.path = Path(path)
        with closing(self._connect()) as db, db:
            db.executescript(STATE_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def hold(self, symbol: str, day: date, now: datetime) -> dict | None:
        """The active backoff (global outage/429/block first, then this symbol-day), or None."""
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM fetch_backoff WHERE (symbol=? AND day=?) OR (symbol=? AND day=?) "
                              "ORDER BY blocked DESC, next_attempt_at DESC",
                              (GLOBAL, GLOBAL, symbol, day.isoformat())).fetchall()
        for row in rows:
            until = datetime.fromisoformat(row["next_attempt_at"])
            if now < until:
                return {"next_attempt_at": until.isoformat(timespec="seconds"), "reason": row["reason"],
                        "failures": row["failures"], "blocked": bool(row["blocked"])}
        return None

    def active_holds(self, now: datetime) -> list[dict]:
        """Every backoff still in force at ``now`` (symbol == GLOBAL means the whole provider)."""
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM fetch_backoff").fetchall()
        return [{"symbol": r["symbol"], "day": r["day"], "failures": r["failures"], "reason": r["reason"],
                 "blocked": bool(r["blocked"]), "next_attempt_at": r["next_attempt_at"]}
                for r in rows if now < datetime.fromisoformat(r["next_attempt_at"])]

    def fail(self, symbol: str, day: date | None, now: datetime, reason: str, *, retry_after_s: int | None = None,
             blocked: bool = False) -> datetime:
        """Register a transient failure; day=None records an outage that holds every symbol."""
        key = (GLOBAL, GLOBAL) if day is None else (symbol, day.isoformat())
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT failures FROM fetch_backoff WHERE symbol=? AND day=?", key).fetchone()
            failures = (row["failures"] if row else 0) + 1
            delay = BLOCK_COOLDOWN if blocked else max(backoff_delay(failures), timedelta(seconds=retry_after_s or 0))
            until = now + delay
            db.execute("INSERT OR REPLACE INTO fetch_backoff VALUES (?, ?, ?, ?, ?, ?)",
                       (*key, failures, until.isoformat(timespec="seconds"), reason[:500], int(blocked)))
        return until

    def clear(self, symbol: str, day: date) -> None:
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM fetch_backoff WHERE (symbol=? AND day=?) OR (symbol=? AND day=?)",
                       (symbol, day.isoformat(), GLOBAL, GLOBAL))

    def record_method(self, symbol: str, day: date, observed_at: datetime, method: str, detail: dict) -> None:
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO fetch_methods VALUES (?, ?, ?, ?, ?)",
                       (symbol, day.isoformat(), observed_at.isoformat(timespec="microseconds"), method,
                        json.dumps(detail, sort_keys=True)))

    def methods(self, symbol: str, day: date) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT observed_at, method, detail FROM fetch_methods WHERE symbol=? AND day=? "
                              "ORDER BY observed_at", (symbol, day.isoformat())).fetchall()
        return [{"observed_at": r["observed_at"], "method": r["method"], "detail": json.loads(r["detail"])} for r in rows]


def evidence_text(evidence: dict) -> str:
    return json.dumps(evidence, sort_keys=True, ensure_ascii=True)
