"""Headless, bounded catch-up worker for a configured Dukascopy JForex export."""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .daily_data import DailyPartitionStore, FIELDS, provider_symbol
from .data_manager import DEFAULT_DAILY_STORE, DEFAULT_SOURCE, DISPLAY_ALIASES, SYMBOLS, DataError
from .dukascopy_datafeed import (
    DatafeedBlocked, DatafeedDelayed, DatafeedProvider, DatafeedTicksProvider, FeedClient, FetchState, evidence_text,
)

DEFAULT_SYNC_CONFIG = Path(__file__).with_name("daily_sync.local.json")
INBOX_FILE = re.compile(
    r"^(?P<symbol>[^_]+)_1 Min_(?P<side>Bid|Ask)_(?P<first>\d{4}\.\d{2}\.\d{2})_(?P<last>\d{4}\.\d{2}\.\d{2})(?P<rest>.*)\.csv$",
    re.IGNORECASE)
INBOX_SIZE_LIMIT = 1_000_000_000
TIME_HEADERS = {"time (eet)": "Europe/Helsinki", "time (utc)": "UTC", "time (gmt)": "UTC", "gmt time": "UTC"}
TIME_FORMATS = ("%Y.%m.%d %H:%M:%S", "%d.%m.%Y %H:%M:%S.%f", "%d.%m.%Y %H:%M:%S")
RETRY_STATUSES = {"ERROR", "KEPT", "DELAYED", "INCOMPLETE", "WAITING_FOR_DATA", "BATCH_LIMIT", "BLOCKED"}
CALENDAR_SOURCES = ("VERIFIED_PROVIDER", "DERIVED_FROM_DATA", "ASSUMED")
PROVIDERS = ("datafeed", "datafeed_ticks", "jforex", "inbox")
JFOREX_CREDENTIALS = Path(__file__).with_name("jforex_credentials.local.json")


class SyncBusy(DataError):
    pass


def _minute(clock: str) -> int:
    hour, minute = map(int, clock.split(":"))
    if not (0 <= hour < 24 and 0 <= minute < 60 or (hour, minute) == (24, 0)):
        raise DataError(f"Invalid clock time: {clock}")
    return hour * 60 + minute


@dataclass(frozen=True)
class MarketCalendar:
    timezone: ZoneInfo
    weekly: dict[int, list[list[str]]]
    overrides: dict[str, list[list[str]]]

    def __post_init__(self):
        if set(self.weekly) != set(range(7)):
            raise DataError("Configure all seven weekdays; use an explicit empty list for closed days")
        for day in self.overrides:
            date.fromisoformat(day)
        for periods in [*self.weekly.values(), *self.overrides.values()]:
            if any(_minute(start) >= _minute(end) for start, end in periods):
                raise DataError("Session intervals must be split at local midnight")

    def expected(self, day: date) -> pd.DatetimeIndex:
        minutes = pd.date_range(day.isoformat(), periods=1440, freq="min", tz="UTC")
        accepted = []
        intervals_by_day = {}
        for utc, local in zip(minutes, minutes.tz_convert(self.timezone)):
            local_day = local.date()
            if local_day not in intervals_by_day:
                intervals = self.overrides.get(local_day.isoformat(), self.weekly.get(local.weekday(), []))
                parsed = [(_minute(start), _minute(end)) for start, end in intervals]
                intervals_by_day[local_day] = parsed
            minute = local.hour * 60 + local.minute
            if any(start <= minute < end for start, end in intervals_by_day[local_day]):
                accepted.append(utc)
        return pd.DatetimeIndex(accepted, tz="UTC")


class JForexExport:
    """Read files from the official platform or invoke a preconfigured SDK bridge."""

    def __init__(self, directory: Path, command: list[str] | None = None):
        self.directory = directory.resolve()
        self.command = command

    def fetch(self, symbol: str, day: date) -> tuple[pd.DataFrame, pd.DataFrame]:
        symbol = provider_symbol(symbol)
        if not self.command:
            return self._read_pair(self.directory, symbol, day)
        self.directory.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="jforex-", dir=self.directory) as staging:
            try:
                completed = subprocess.run(
                    [*self.command, symbol, day.isoformat(), staging],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=120, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise DataError("Configured JForex bridge did not finish") from exc
            if completed.returncode:
                raise DataError(f"Configured JForex bridge exited with code {completed.returncode}")
            return self._read_pair(Path(staging), symbol, day)

    def _read_pair(self, directory: Path, symbol: str, day: date) -> tuple[pd.DataFrame, pd.DataFrame]:
        return (self._read_side(directory / f"{symbol}_{day}_BID.csv"),
                self._read_side(directory / f"{symbol}_{day}_ASK.csv"))

    @staticmethod
    def _read_side(path: Path) -> pd.DataFrame:
        if not path.is_file():
            raise FileNotFoundError(path.name)
        if path.stat().st_size > 2_000_000:
            raise DataError("JForex day export exceeds size limit")
        try:
            frame = pd.read_csv(path, nrows=1441)
            if list(frame.columns) != ["time_utc", *FIELDS]:
                raise DataError("Unexpected JForex day export columns")
            timestamps = frame.pop("time_utc").astype(str)
            if not timestamps.str.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00(?:Z|\+00:00)").all():
                raise DataError("JForex day export must use explicit UTC minute timestamps")
            frame.index = pd.DatetimeIndex(pd.to_datetime(timestamps, utc=True))
            return frame
        except (OSError, ValueError) as exc:
            raise DataError(f"Invalid day export: {path.name}") from exc


def jforex_credentials_available() -> bool:
    """The JForex adapter needs a demo login; without the credentials file the bridge is never started."""
    configured = os.environ.get("JFOREX_CREDENTIALS")
    return (Path(configured) if configured else JFOREX_CREDENTIALS).is_file()


class GatedProvider:
    """A provider that is skipped (not failed) while its precondition does not hold."""

    session_aware = False

    def __init__(self, provider, gate, reason: str):
        self.provider, self.gate, self.reason = provider, gate, reason
        self.last_detail: dict = {}

    def available(self) -> bool:
        return bool(self.gate())

    def fetch(self, symbol: str, day: date, expected=None):
        return self.provider.fetch(symbol, day)


class ChainProvider:
    """Try the configured acquisition methods in order; only a missing day moves on to the next method.

    Rate limiting, 429 and WAF handling live in the shared FeedClient, so a blocked or delayed method ends the
    attempt for every method that uses the same host. ``last_method`` and ``last_attempts`` describe the last fetch.
    """

    uses_backoff = True
    session_aware = True

    def __init__(self, methods: list[tuple[str, object]]):
        self.methods = methods
        self.last_method: str | None = None
        self.last_detail: dict = {}
        self.last_attempts: list[str] = []

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.methods]

    def bind_reference(self, reference) -> None:
        for _, method in self.methods:
            if getattr(method, "reference", False) is None:
                method.reference = reference

    def fetch(self, symbol: str, day: date, expected=None) -> tuple[pd.DataFrame, pd.DataFrame]:
        self.last_method, self.last_detail, self.last_attempts = None, {}, []
        for name, method in self.methods:
            if hasattr(method, "available") and not method.available():
                self.last_attempts.append(f"{name}: skipped ({method.reason})")
                continue
            try:
                bid, ask = (method.fetch(symbol, day, expected) if getattr(method, "session_aware", False)
                            else method.fetch(symbol, day))
            except FileNotFoundError:
                self.last_attempts.append(f"{name}: not available")
                continue
            self.last_attempts.append(f"{name}: ok")
            self.last_method, self.last_detail = name, dict(getattr(method, "last_detail", {}))
            return bid, ask
        raise FileNotFoundError("; ".join(self.last_attempts) or "no acquisition method configured")


def resolve_symbol(name: str) -> str:
    """Accept user symbols and Dukascopy file symbols such as DEUIDXEUR."""
    try:
        return provider_symbol(name)
    except DataError:
        if name.upper() in SYMBOLS.values():
            return name.upper()
        raise


def plan_symbol(name: str) -> str:
    """A spelling DailyPartitionStore accepts for the resolved Dukascopy symbol."""
    resolved = resolve_symbol(name)
    return resolved if resolved in SYMBOLS else DISPLAY_ALIASES[resolved]


def _decode_times(raw: pd.Series, zone: str) -> pd.DatetimeIndex:
    """Same wall-clock rules as DataManager._read_side: ambiguous fall-back hours are resolved in source order."""
    text = raw.astype(str).str.strip()
    naive = None
    for fmt in TIME_FORMATS:
        attempt = pd.to_datetime(text, format=fmt, errors="coerce")
        if attempt.notna().all():
            naive = attempt
            break
    if naive is None:
        raise DataError("Unrecognised or invalid timestamps in export")
    if zone == "UTC":
        return pd.DatetimeIndex(naive).tz_localize("UTC")
    first = naive.dt.tz_localize(zone, ambiguous=True, nonexistent="NaT")
    second = naive.dt.tz_localize(zone, ambiguous=False, nonexistent="NaT")
    if first.isna().any():
        raise DataError("Export contains nonexistent local timestamps")
    first_utc = first.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy()
    second_utc = second.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy()
    resolved = first_utc.copy()
    for position in np.flatnonzero(first_utc != second_utc):
        previous = resolved[position - 1] if position else None
        low, high = sorted((first_utc[position], second_utc[position]))
        resolved[position] = low if previous is None or low > previous else high
    return pd.DatetimeIndex(resolved).tz_localize("UTC")


def _time_zone(columns: list[str]) -> tuple[str, str]:
    for column in columns:
        if column.lower() in TIME_HEADERS:
            return column, TIME_HEADERS[column.lower()]
    raise DataError("Export has no recognised time column (Time (EET), Time (UTC), Gmt time)")


def read_inbox_export(path: Path) -> pd.DataFrame:
    """Parse one JForex export file; rows keep source order so ordering defects stay detectable."""
    if path.stat().st_size > INBOX_SIZE_LIMIT:
        raise DataError(f"{path.name}: export exceeds size limit")
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except (OSError, ValueError) as exc:
        raise DataError(f"{path.name}: unreadable CSV") from exc
    raw.columns = [str(column).strip() for column in raw.columns]
    time_column, zone = _time_zone(list(raw.columns))
    volume = "TickVolume" if "TickVolume" in raw.columns else "Volume" if "Volume" in raw.columns else None
    missing = {"Open", "High", "Low", "Close"} - set(raw.columns)
    if missing or volume is None:
        raise DataError(f"{path.name}: missing columns {sorted(missing) or ['Volume']}")
    try:
        index = _decode_times(raw[time_column], zone)
    except DataError as exc:
        raise DataError(f"{path.name}: {exc}") from exc
    return pd.DataFrame({field: pd.to_numeric(raw[column], errors="coerce").to_numpy()
                         for field, column in zip(FIELDS, ("Open", "High", "Low", "Close", volume))}, index=index)


def _last_source_row(source_directory: Path, source_symbol: str) -> tuple[pd.Timestamp, float] | None:
    """Last (UTC time, close) in the original CSV as DataManager would pick it (largest Bid file); reads only the tail."""
    if not source_directory.is_dir():
        return None
    matches = [p for p in source_directory.glob("*.csv")
               if re.match(rf"^{re.escape(source_symbol)}_1 Min_Bid_", p.name, re.IGNORECASE)]
    if not matches:
        return None
    path = max(matches, key=lambda p: p.stat().st_size)
    with path.open("rb") as stream:
        header = stream.readline().decode("utf-8-sig", "replace").strip().split(",")
        size = stream.seek(0, 2)
        stream.seek(max(0, size - 4096))
        tail = stream.read().decode("utf-8", "replace").splitlines()
    lines = [line for line in tail if line.strip() and line.strip().split(",")[0] != header[0]]
    if not lines:
        return None
    columns = [column.strip() for column in header]
    _, zone = _time_zone(columns)
    fields = lines[-1].split(",")
    return _decode_times(pd.Series([fields[0]]), zone)[0], float(fields[columns.index("Close")])


def last_source_day(source_directory: Path, source_symbol: str) -> date | None:
    """Last UTC day in the original CSV."""
    row = _last_source_row(source_directory, source_symbol)
    return row[0].date() if row else None


def reference_close(store: DailyPartitionStore, source_directory: Path, symbol: str, now: datetime) -> float | None:
    """Last known Bid close: newest stored partition, else the tail of the original CSV."""
    mark = store.watermark(symbol, as_of=now)
    if mark:
        day = date.fromisoformat(mark["day"])
        frame, _ = store.read(symbol, day, day, as_of=now)
        return float(frame.bid_close.iloc[-1])
    row = _last_source_row(source_directory, resolve_symbol(symbol))
    return row[1] if row else None


@dataclass(frozen=True)
class Inbox:
    directory: Path
    source_directory: Path = DEFAULT_SOURCE


def _slice_day(frame: pd.DataFrame, day: date) -> pd.DataFrame:
    start = pd.Timestamp(day, tz="UTC")
    return frame.loc[(frame.index >= start) & (frame.index < start + pd.Timedelta(days=1))]


def _day_rejections(bid: pd.DataFrame, ask: pd.DataFrame) -> dict[date, str]:
    """UTC days that must not be ingested, with the first reason found."""
    reasons: dict[date, str] = {}

    def reject(index, mask, reason):
        for stamp in index[np.asarray(mask, dtype=bool)]:
            reasons.setdefault(stamp.date(), reason)

    for side, frame in (("Bid", bid), ("Ask", ask)):
        reject(frame.index, frame.index.duplicated(keep=False), f"{side}: duplicate timestamps")
        values = frame.index.asi8
        backwards = np.zeros(len(values), dtype=bool)
        backwards[1:] = values[1:] <= np.maximum.accumulate(values)[:-1]
        reject(frame.index, backwards & ~frame.index.duplicated(keep=False), f"{side}: non-monotonic timestamps")
        prices = frame[["open", "high", "low", "close"]]
        reject(frame.index, ((prices <= 0) | ~np.isfinite(prices)).any(axis=1).to_numpy(),
               f"{side}: impossible price (non-positive or missing)")
        reject(frame.index, (frame.high < frame[["open", "close", "low"]].max(axis=1)).to_numpy()
               | (frame.low > frame[["open", "close", "high"]].min(axis=1)).to_numpy(),
               f"{side}: high below low / inconsistent OHLC")
    common = bid.index[~bid.index.duplicated()].intersection(ask.index[~ask.index.duplicated()])
    if len(common):
        b, a = bid[~bid.index.duplicated()].loc[common], ask[~ask.index.duplicated()].loc[common]
        reject(common, ((a.open < b.open) | (a.close < b.close)).to_numpy(), "Negative spread (ask below bid)")
    return reasons


def process_inbox(store: DailyPartitionStore, inbox: Inbox, plans: list["SymbolPlan"], *,
                  clock=None) -> tuple[list[dict], list[dict]]:
    """Ingest paired multi-day exports as full UTC days; move files only after every day committed.

    Returns (per-day ingest reports, per-file events). Event statuses: PROCESSED, KEPT, ERROR.
    Days are committed one by one (each atomically); a file pair is moved only if all of them settled.
    """
    clock = clock or (lambda: datetime.now(timezone.utc))
    reports: list[dict] = []
    events: list[dict] = []
    groups: dict[tuple, dict[str, Path]] = {}
    for path in sorted(inbox.directory.glob("*.csv")) if inbox.directory.is_dir() else []:
        match = INBOX_FILE.match(path.name)
        if match:
            key = (match["symbol"].upper(), match["first"], match["last"], match["rest"])
            groups.setdefault(key, {})[match["side"].lower()] = path

    def event(status, detail, *names):
        events.append({"status": status, "files": list(names), "detail": detail})

    plan_by_source = {resolve_symbol(plan.symbol): plan for plan in plans}
    blocked: set[str] = set()
    for key, sides in groups.items():
        if len(sides) != 2:
            blocked.add(key[0])
            event("ERROR", "Unpaired export: Bid and Ask files are both required; nothing ingested for this symbol",
                  next(iter(sides.values())).name)
    for key in sorted(groups, key=lambda k: (k[0], k[2], k[1], k[3])):
        symbol, sides = key[0], groups[key]
        if symbol in blocked:
            continue
        names = [sides[s].name for s in ("bid", "ask")]
        try:
            source_symbol = resolve_symbol(symbol)
        except DataError:
            event("ERROR", f"Unknown Dukascopy instrument {symbol}", *names)
            continue
        plan = plan_by_source.get(source_symbol)
        if plan is None:
            event("ERROR", f"{source_symbol} is not configured in daily-sync symbols", *names)
            continue
        try:
            bid, ask = read_inbox_export(sides["bid"]), read_inbox_export(sides["ask"])
            if bid.empty or ask.empty:
                raise DataError("Export contains no rows")
        except DataError as exc:
            event("ERROR", str(exc), *names)
            continue
        end = min(bid.index.max(), ask.index.max())
        last_day = end.date()
        floor = last_source_day(inbox.source_directory, source_symbol)
        first = max(plan.first_day, min(bid.index.min(), ask.index.min()).date(),
                    floor + timedelta(days=1) if floor else plan.first_day)
        rejected = _day_rejections(bid, ask)
        outcomes = []
        for offset in range(max(0, (last_day - first).days + 1)):
            day = first + timedelta(days=offset)
            observed = clock()
            if day in rejected:
                reports.append(store.record_status(plan.symbol, day, "ERROR", rejected[day], observed_at=observed))
            else:
                complete = day < last_day or end >= pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=23, minutes=59)
                if not complete:
                    midnight = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
                    observed = min(observed, midnight - timedelta(microseconds=1))
                reports.append(store.ingest(plan.symbol, day, _slice_day(bid, day), _slice_day(ask, day),
                                            expected_minutes=plan.calendar.expected(day), observed_at=observed))
            outcomes.append((day, reports[-1]["status"]))
        problems = [(day, status) for day, status in outcomes
                    if status not in {"UP TO DATE", "MARKET CLOSED"} and not (day == last_day and status == "INCOMPLETE")]
        if problems:
            event("KEPT", "Files left in inbox; unsettled days: " + ", ".join(f"{d} {s}" for d, s in problems[:10]), *names)
            continue
        target = inbox.directory / "processed" / clock().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target.mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.move(str(inbox.directory / name), str(target / name))
        event("PROCESSED", f"Ingested {first}..{last_day}; moved to {target.relative_to(inbox.directory)}", *names)
    return reports, events


@dataclass(frozen=True)
class SymbolPlan:
    symbol: str
    first_day: date
    update_at: int
    calendar: MarketCalendar
    calendar_source: str = "ASSUMED"

    def due(self, now: datetime) -> bool:
        return now >= self._update_on(now.astimezone(self.calendar.timezone).date())

    def next_update(self, now: datetime) -> datetime:
        local_day = now.astimezone(self.calendar.timezone).date()
        return self._update_on(local_day + timedelta(days=int(self.due(now))))

    def _update_on(self, target_day: date) -> datetime:
        zone = self.calendar.timezone
        target = datetime.combine(target_day, datetime.min.time()) + timedelta(minutes=self.update_at)
        while True:
            candidate = target.replace(tzinfo=zone).astimezone(timezone.utc)
            if candidate.astimezone(zone).replace(tzinfo=None) == target:
                return candidate
            target += timedelta(minutes=1)


@dataclass(frozen=True)
class SyncConfig:
    plans: list[SymbolPlan]
    provider: JForexExport | ChainProvider
    max_days: int
    portfolio_ids: list[str]
    max_forward_days: int
    inbox: Inbox | None = None
    source_directory: Path = DEFAULT_SOURCE
    fetch: bool = True


def load_sync_config(path: Path) -> SyncConfig:
    path = path.resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        names = raw.get("providers")
        if names is None:
            single = raw.get("provider") or ("inbox" if raw.get("inbox_directory") else
                                             "jforex" if raw.get("jforex_command") or raw.get("export_directory") else "datafeed")
            names = [single]
        if (not isinstance(names, list) or not names or len(set(names)) != len(names)
                or any(name not in PROVIDERS for name in names)):
            raise DataError(f"providers must be a unique, nonempty list from: {', '.join(PROVIDERS)}")
        command = raw.get("jforex_command")
        if command is not None and (not isinstance(command, list)
                or not command or not all(isinstance(item, str) and item for item in command)):
            raise DataError("jforex_command must be a nonempty array of strings")
        if "inbox" in names and not raw.get("inbox_directory"):
            raise DataError("provider inbox needs inbox_directory")
        fetchers = [name for name in names if name != "inbox"]
        staging = path.parent / (raw.get("export_directory") or raw.get("inbox_directory") or "data_cache/jforex_staging")
        if names == ["jforex"]:
            provider = JForexExport(staging, command)
        elif not fetchers:
            provider = JForexExport(staging, None)
        else:
            client = FeedClient()
            methods = []
            for name in fetchers:
                if name == "jforex":
                    if command is None:
                        raise DataError("provider jforex needs jforex_command")
                    methods.append((name, GatedProvider(JForexExport(staging, command), jforex_credentials_available,
                                                        "no jforex_credentials.local.json")))
                else:
                    methods.append((name, (DatafeedProvider if name == "datafeed" else DatafeedTicksProvider)(client=client)))
            provider = ChainProvider(methods)
        plans = []
        for symbol, settings in raw["symbols"].items():
            calendar = MarketCalendar(ZoneInfo(settings["timezone"]),
                                      {int(day): periods for day, periods in settings["weekly"].items()},
                                      settings.get("overrides", {}))
            update_at = _minute(settings["update_at"])
            if update_at == 1440:
                raise DataError("Use 00:00 for an update at midnight")
            source_label = settings.get("calendar_source", "ASSUMED")
            if source_label not in CALENDAR_SOURCES:
                raise DataError(f"calendar_source must be one of: {', '.join(CALENDAR_SOURCES)}")
            plans.append(SymbolPlan(plan_symbol(symbol), date.fromisoformat(settings["first_day"]),
                                    update_at, calendar, source_label))
        if not plans or len({plan.symbol for plan in plans}) != len(plans):
            raise DataError("Configure at least one instrument with unique Dukascopy symbols")
        budgets = (raw.get("max_days_per_symbol", 3), raw.get("max_forward_days", 3))
        if any(type(value) is not int or not 1 <= value <= 31 for value in budgets):
            raise DataError("Catch-up budgets must be integers from 1 to 31")
        portfolio_ids = raw.get("forward_portfolio_ids", [])
        if not isinstance(portfolio_ids, list) or not all(isinstance(item, str) for item in portfolio_ids):
            raise DataError("forward_portfolio_ids must be an array of IDs")
        source = Path(raw.get("source_directory", DEFAULT_SOURCE))
        source = source if source.is_absolute() else path.parent / source
        inbox = None
        if "inbox" in names:
            inbox = Inbox(path.parent / raw["inbox_directory"], source)
        return SyncConfig(plans, provider, budgets[0], portfolio_ids, budgets[1], inbox, source, bool(fetchers))
    except FileNotFoundError:
        raise
    except DataError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        raise DataError("Invalid local daily-sync configuration") from exc


def feed_status(store: DailyPartitionStore, config: SyncConfig, *, now: datetime,
                forward_by_symbol: dict | None = None) -> dict:
    now = now.astimezone(timezone.utc)
    rows = []
    for plan in config.plans:
        settled = store.settled_days(plan.symbol, plan.first_day)
        days = (now.date() - plan.first_day).days
        pending = [day for offset in range(days)
                   if (day := plan.first_day + timedelta(days=offset)) not in settled]
        first = pending[0] if pending else None
        summary = store.intake_summary(plan.symbol, plan.first_day, first)
        latest = summary["latest"] or {}
        attempt = summary["pending"] or {}
        watermark = store.watermark(plan.symbol, as_of=now) or {}
        rows.append({
            "symbol": plan.symbol, "status": (attempt.get("status", "DELAYED") if first else
                                              "MARKET CLOSED" if latest.get("status") == "MARKET CLOSED" else "UP TO DATE"),
            "last_candle_at": watermark.get("last_candle_at"),
            "last_success_at": (summary["success"] or {}).get("observed_at"),
            "next_update_at": plan.next_update(now).isoformat(),
            "pending_days": len(pending), "first_pending_day": first.isoformat() if first else None,
            "expected_minutes_first_day": len(plan.calendar.expected(first)) if first else 0,
            "missing_bid": attempt.get("missing_bid"), "missing_ask": attempt.get("missing_ask"),
            "last_added_rows": latest.get("rows", 0) if latest.get("added") and not latest.get("revision_detected") else 0,
            "last_attempt": latest or None, "forward": (forward_by_symbol or {}).get(plan.symbol, []),
        })
    return {"configured": True, "symbols": rows, "runs": store.sync_history()}


def synchronize(store, provider, symbol: str, calendar: MarketCalendar, *,
                first_day: date, now: datetime, max_days: int, clock=None) -> list[dict]:
    """Walk missing days in order. A later day never jumps a failed open session."""
    if now.utcoffset() is None or not 1 <= max_days <= 31:
        raise DataError("Use an aware sync time and a catch-up budget from 1 to 31 days")
    now = now.astimezone(timezone.utc)
    clock = clock or (lambda: datetime.now(timezone.utc))
    settled = store.settled_days(symbol, first_day)
    reports = []
    day = first_day
    state = FetchState(store.path) if getattr(provider, "uses_backoff", False) else None
    while day < now.date() and len(reports) < max_days:
        if day not in settled:
            expected = calendar.expected(day)
            if state and not expected.empty and (hold := state.hold(symbol, day, now)):
                reports.append({"symbol": provider_symbol(symbol), "day": day.isoformat(),
                                "observed_at": clock().isoformat(timespec="microseconds"),
                                "status": "BLOCKED" if hold["blocked"] else "DELAYED",
                                "detail": f"Backoff until {hold['next_attempt_at']} ({hold['reason']}); no request sent"})
                break
            outage = None
            try:
                if expected.empty:
                    bid = ask = pd.DataFrame(columns=FIELDS, index=expected)
                elif getattr(provider, "session_aware", False):
                    bid, ask = provider.fetch(symbol, day, expected)
                else:
                    bid, ask = provider.fetch(symbol, day)
                report = store.ingest(symbol, day, bid, ask, expected_minutes=expected, observed_at=clock())
                if getattr(provider, "last_method", None) and not expected.empty:
                    report["method"] = provider.last_method
                    if provider.last_detail:
                        report["method_detail"] = provider.last_detail
            except FileNotFoundError:
                attempts = "; ".join(getattr(provider, "last_attempts", []))
                report = store.record_status(symbol, day, "DELAYED", "Waiting for both Dukascopy exports"
                                             + (f" ({attempts})" if attempts else ""), observed_at=clock())
            except DatafeedBlocked as exc:
                report = {**store.record_status(symbol, day, "ERROR", f"BLOCKED: {exc} {evidence_text(exc.evidence)}",
                                                observed_at=clock()), "status": "BLOCKED", "evidence": exc.evidence}
                outage = {"blocked": True}
            except DatafeedDelayed as exc:
                report = store.record_status(symbol, day, "DELAYED", str(exc), observed_at=clock())
                outage = {"retry_after_s": exc.retry_after_s}
            except DataError as exc:
                report = store.record_status(symbol, day, "ERROR", str(exc), observed_at=clock())
            reports.append(report)
            if state:
                _track(state, symbol, day, report, outage, clock())
            if report["status"] not in {"UP TO DATE", "MARKET CLOSED"}:
                break
        day += timedelta(days=1)
    return reports


def _track(state: FetchState, symbol: str, day: date, report: dict, outage: dict | None, observed: datetime) -> None:
    if report["status"] in {"UP TO DATE", "MARKET CLOSED"}:
        state.clear(symbol, day)
        if report.get("method"):
            state.record_method(symbol, day, observed, report["method"], report.get("method_detail", {}))
    elif outage is not None:
        state.fail(symbol, None, observed, str(report.get("detail", "")), **outage)
    else:
        state.fail(symbol, day, observed, f"{report['status']}: {report.get('detail', '')}")


def _advance_forward(store: DailyPartitionStore, config: SyncConfig, now: datetime) -> list[dict]:
    if config.portfolio_ids:
        from .forward_engine import ForwardEngine
        from .forward_registry import ForwardRegistry

        engine = ForwardEngine(ForwardRegistry(store))
        forward = []
        for identifier in config.portfolio_ids:
            try:
                result = engine.advance(identifier, as_of=now, max_days=config.max_forward_days)
            except DataError as exc:
                result = {"status": "ERROR", "detail": str(exc)}
            forward.append({"portfolio_id": identifier, **result})
        return forward
    return []


def run_sync(store: DailyPartitionStore, config: SyncConfig, *, manual: bool = False, clock=None) -> dict:
    clock = clock or (lambda: datetime.now(timezone.utc))
    lock_path = store.path.with_suffix(".sync-lock.sqlite3")
    with closing(sqlite3.connect(lock_path, timeout=0)) as lock:
        try:
            lock.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
            raise SyncBusy("A daily synchronization is already running") from exc
        now = clock()
        run_id = store.start_sync(now=now, manual=manual)
        try:
            intake, inbox_events = [], []
            if config.fetch:
                if hasattr(config.provider, "bind_reference"):
                    config.provider.bind_reference(
                        lambda symbol: reference_close(store, config.source_directory, symbol, clock()))
                for plan in config.plans:
                    if manual or plan.due(now):
                        intake.extend(synchronize(store, config.provider, plan.symbol, plan.calendar,
                                                  first_day=plan.first_day, now=now,
                                                  max_days=config.max_days, clock=clock))
                        if any(item["status"] == "BLOCKED" for item in intake):
                            break
            if config.inbox:
                due = [plan for plan in config.plans if manual or plan.due(now)]
                if due:
                    reports, inbox_events = process_inbox(store, config.inbox, due, clock=clock)
                    intake.extend(reports)
            forward = _advance_forward(store, config, clock())
            retry = any(item["status"] in RETRY_STATUSES for item in [*intake, *forward, *inbox_events])
            result = {"intake": intake, "inbox": inbox_events, "forward": forward, "status": "RETRY" if retry else "COMPLETE"}
        except Exception as exc:
            store.finish_sync(run_id, now=clock(), status="ERROR", detail={"error_type": type(exc).__name__})
            raise
        store.finish_sync(run_id, now=clock(), status=result["status"], detail=result)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_SYNC_CONFIG)
    parser.add_argument("--store", type=Path, default=DEFAULT_DAILY_STORE,
                        help="Use an explicitly separate SQLite store")
    parser.add_argument("--now", action="store_true", help="Run even before the configured local update time")
    parser.add_argument("--check-config", action="store_true", help="Validate settings without starting a synchronization")
    args = parser.parse_args()
    config = load_sync_config(args.config)
    if args.check_config:
        return 0
    store = DailyPartitionStore(args.store)
    try:
        result = run_sync(store, config, manual=args.now)
    except SyncBusy:
        return 0
    print(json.dumps(result, ensure_ascii=False))
    return int(result["status"] == "RETRY")


if __name__ == "__main__":
    raise SystemExit(main())
