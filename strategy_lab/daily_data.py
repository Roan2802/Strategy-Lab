"""Durable local intake for paired, closed Dukascopy M1 bars."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .data_manager import DataError, PRICE_COLUMNS, SYMBOLS


# A revision listed in quarantined_partitions is never read, never compared against and does not settle its day.
LIVE = ("NOT EXISTS (SELECT 1 FROM quarantined_partitions q WHERE q.symbol=daily_partitions.symbol "
        "AND q.day=daily_partitions.day AND q.observed_at=daily_partitions.observed_at)")
FIELDS = (*PRICE_COLUMNS, "volume")
QUOTE_COLUMNS = tuple(f"{side}_{field}" for side in ("bid", "ask") for field in FIELDS)
SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_partitions (
    symbol TEXT NOT NULL,
    day TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    rows INTEGER NOT NULL,
    payload BLOB NOT NULL,
    PRIMARY KEY (symbol, day, observed_at)
);
CREATE INDEX IF NOT EXISTS partition_history
    ON daily_partitions(symbol, day, observed_at);
CREATE TABLE IF NOT EXISTS intake_attempts (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    day TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS intake_attempt_lookup ON intake_attempts(symbol, day, id);
CREATE TABLE IF NOT EXISTS quarantined_partitions (
    symbol TEXT NOT NULL, day TEXT NOT NULL, observed_at TEXT NOT NULL,
    quarantined_at TEXT NOT NULL, reason TEXT NOT NULL,
    PRIMARY KEY (symbol, day, observed_at)
);
CREATE TABLE IF NOT EXISTS daily_sync_runs (
    id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
    mode TEXT NOT NULL, status TEXT NOT NULL, detail TEXT
);
"""


def provider_symbol(symbol: str) -> str:
    alias = {"USTEC": "USATECHIDXUSD", "NASDAQ": "USATECHIDXUSD"}
    normalized = alias.get(symbol.upper(), symbol.upper())
    if normalized in SYMBOLS:
        return SYMBOLS[normalized]
    if normalized in SYMBOLS.values():  # already a provider name, e.g. DEUIDXEUR
        return normalized
    raise DataError(f"Unknown Dukascopy instrument: {symbol}")


def _utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise DataError("Timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def _utc_index(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise DataError("M1 index must contain timezone-aware timestamps")
    index = index.tz_convert("UTC")
    if index.hasnans or index.has_duplicates or not (index.floor("min") == index).all():
        raise DataError("Invalid, duplicate or off-minute timestamps")
    return index


def _validate_side(frame: pd.DataFrame, day: date) -> pd.DataFrame:
    if len(frame) > 1440 or not set(FIELDS).issubset(frame.columns):
        raise DataError("Expected at most one UTC day of OHLC and volume")
    frame = frame.loc[:, list(FIELDS)].copy()
    frame.index = _utc_index(frame.index)
    if any(stamp.date() != day for stamp in frame.index):
        raise DataError("M1 timestamps outside the requested UTC day")
    try:
        frame = frame.astype("float64")
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataError("Non-numeric quote values") from exc
    if not np.isfinite(frame.to_numpy()).all():
        raise DataError("Non-finite price or volume")
    if (frame[list(PRICE_COLUMNS)] <= 0).any().any() or (frame.volume < 0).any():
        raise DataError("Non-positive price or negative volume")
    if (frame.high < frame[["open", "close", "low"]].max(axis=1)).any() or (
        frame.low > frame[["open", "close", "high"]].min(axis=1)
    ).any():
        raise DataError("Impossible OHLC prices")
    return frame.sort_index()


class DailyPartitionStore:
    """Commit an entire day or none of it. Earlier revisions remain readable."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def record_status(self, symbol: str, day: date, status: str, detail: str, *, observed_at: datetime) -> dict:
        if status not in {"DELAYED", "ERROR"}:
            raise DataError("Only non-publication statuses may be recorded without quotes")
        result = {"symbol": provider_symbol(symbol), "day": day.isoformat(),
                  "observed_at": _utc(observed_at).isoformat(timespec="microseconds"),
                  "status": status, "detail": detail}
        with closing(self._connect()) as db, db:
            self._record_attempt(db, result)
        return result

    @staticmethod
    def _record_attempt(db: sqlite3.Connection, result: dict) -> None:
        db.execute(
            "INSERT INTO intake_attempts(symbol, day, observed_at, status, detail) VALUES (?, ?, ?, ?, ?)",
            (result["symbol"], result["day"], result["observed_at"], result["status"], json.dumps(result)),
        )

    def settled_days(self, symbol: str, first_day: date) -> set[date]:
        with closing(self._connect()) as db:
            records = db.execute(
                "SELECT DISTINCT a.day FROM intake_attempts a WHERE a.symbol=? AND a.day>=? "
                "AND a.status IN ('UP TO DATE', 'MARKET CLOSED') AND NOT EXISTS (SELECT 1 FROM quarantined_partitions q "
                "WHERE q.symbol=a.symbol AND q.day=a.day AND q.quarantined_at>=a.observed_at)",
                (provider_symbol(symbol), first_day.isoformat()),
            ).fetchall()
        return {date.fromisoformat(record["day"]) for record in records}

    def intake_summary(self, symbol: str, first_day: date, pending_day: date | None) -> dict:
        source = provider_symbol(symbol)
        with closing(self._connect()) as db:
            latest = db.execute(
                "SELECT detail FROM intake_attempts WHERE symbol=? AND day>=? ORDER BY id DESC LIMIT 1",
                (source, first_day.isoformat()),
            ).fetchone()
            success = db.execute(
                "SELECT detail FROM intake_attempts WHERE symbol=? AND day>=? AND status='UP TO DATE' "
                "ORDER BY id DESC LIMIT 1", (source, first_day.isoformat()),
            ).fetchone()
            pending = db.execute(
                "SELECT detail FROM intake_attempts WHERE symbol=? AND day=? ORDER BY id DESC LIMIT 1",
                (source, pending_day.isoformat()),
            ).fetchone() if pending_day else None
        return {"latest": json.loads(latest[0]) if latest else None,
                "success": json.loads(success[0]) if success else None,
                "pending": json.loads(pending[0]) if pending else None}

    def start_sync(self, *, now: datetime, manual: bool) -> int:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE daily_sync_runs SET status='INTERRUPTED' WHERE status='RUNNING'")
            return db.execute(
                "INSERT INTO daily_sync_runs(started_at, mode, status) VALUES (?, ?, 'RUNNING')",
                (_utc(now).isoformat(timespec="microseconds"), "MANUAL" if manual else "SCHEDULED"),
            ).lastrowid

    def finish_sync(self, identifier: int, *, now: datetime, status: str, detail: dict) -> None:
        with closing(self._connect()) as db, db:
            db.execute("UPDATE daily_sync_runs SET finished_at=?, status=?, detail=? WHERE id=?",
                       (_utc(now).isoformat(timespec="microseconds"), status, json.dumps(detail), identifier))

    def sync_history(self, limit: int = 12) -> list[dict]:
        with closing(self._connect()) as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM daily_sync_runs ORDER BY id DESC LIMIT ?", (limit,))]
        for row in rows:
            row["detail"] = json.loads(row["detail"]) if row["detail"] else None
        return rows

    def forward_progress(self) -> dict:
        by_symbol = {}
        with closing(self._connect()) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='forward_members' AND type='table'").fetchone():
                return {}
            for row in db.execute("""
                SELECT f.configuration, p.id, p.name, p.last_day
                FROM forward_members m JOIN frozen_strategies f ON f.id=m.registration_id
                JOIN forward_portfolios p ON p.id=m.portfolio_id ORDER BY p.name, p.id
            """):
                symbol = json.loads(row["configuration"])["symbol"]
                portfolios = by_symbol.setdefault(symbol, {})
                portfolios[row["id"]] = {"name": row["name"], "last_day": row["last_day"]}
        return {symbol: list(portfolios.values()) for symbol, portfolios in by_symbol.items()}

    def closure(self, symbol: str, day: date, *, as_of: datetime) -> dict | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT detail FROM intake_attempts WHERE symbol=? AND day=? AND status='MARKET CLOSED' "
                "AND observed_at<=? ORDER BY observed_at DESC, id DESC LIMIT 1",
                (provider_symbol(symbol), day.isoformat(), _utc(as_of).isoformat(timespec="microseconds")),
            ).fetchone()
        return json.loads(row["detail"]) if row else None

    def watermark(self, symbol: str, *, as_of: datetime) -> dict | None:
        cutoff = _utc(as_of).isoformat(timespec="microseconds")
        with closing(self._connect()) as db:
            last = db.execute(f"SELECT MAX(day) FROM daily_partitions WHERE symbol=? AND observed_at<=? AND {LIVE}",
                              (provider_symbol(symbol), cutoff)).fetchone()[0]
        if last is None:
            return None
        day = date.fromisoformat(last)
        frame, manifests = self.read(symbol, day, day, as_of=as_of)
        return {**manifests[0], "last_candle_at": frame.index[-1].isoformat()}

    def ingest(
        self, symbol: str, day: date, bid: pd.DataFrame, ask: pd.DataFrame, *,
        expected_minutes: pd.DatetimeIndex, observed_at: datetime | None = None,
    ) -> dict:
        source = provider_symbol(symbol)
        observed = _utc(observed_at or datetime.now(timezone.utc))
        result = {"symbol": source, "day": day.isoformat(), "observed_at": observed.isoformat(timespec="microseconds")}
        payload = None
        try:
            expected = _utc_index(expected_minutes).sort_values()
            if any(stamp.date() != day for stamp in expected):
                raise DataError("Session calendar returned timestamps outside the requested UTC day")
            bids, asks = _validate_side(bid, day), _validate_side(ask, day)
            missing_bid = len(expected.difference(bids.index))
            missing_ask = len(expected.difference(asks.index))
            result.update(missing_bid=missing_bid, missing_ask=missing_ask)
            midnight = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
            if observed < midnight or not bids.index.equals(asks.index) or missing_bid or missing_ask:
                result["status"] = "INCOMPLETE"
            elif not len(expected) and not len(bids):
                result["status"] = "MARKET CLOSED"
            elif not bids.index.equals(expected):
                raise DataError("Provider bars outside configured session; review the calendar")
            else:
                for field in ("open", "close"):
                    if (asks[field] < bids[field]).any():
                        raise DataError(f"Negative {field} spread")
                paired = bids.add_prefix("bid_").join(asks.add_prefix("ask_"))
                rows = [[int(stamp.timestamp()), *values]
                        for stamp, *values in paired.itertuples(name=None)]
                payload = json.dumps(rows, separators=(",", ":"), allow_nan=False).encode("ascii")
                result.update(status="UP TO DATE", rows=len(rows), sha256=hashlib.sha256(payload).hexdigest())
        except DataError as exc:
            result.update(status="ERROR", detail=str(exc))

        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if payload is not None:
                previous = db.execute(
                    f"SELECT sha256, observed_at FROM daily_partitions WHERE symbol=? AND day=? AND {LIVE} "
                    "ORDER BY observed_at DESC, rowid DESC LIMIT 1", (source, result["day"]),
                ).fetchone()
                if previous and observed <= datetime.fromisoformat(previous["observed_at"]) and previous["sha256"] != result["sha256"]:
                    raise DataError("A revised partition must have a newer observation timestamp")
                changed = not previous or previous["sha256"] != result["sha256"]
                if changed:
                    db.execute(
                        "INSERT INTO daily_partitions VALUES (?, ?, ?, ?, ?, ?)",
                        (source, result["day"], result["sha256"], result["observed_at"], result["rows"], payload),
                    )
                result["added"] = changed
                result["revision_detected"] = bool(previous and changed)
            self._record_attempt(db, result)
        return result

    def live_partitions(self, symbol: str | None = None) -> list[sqlite3.Row]:
        """Newest non-quarantined revision of every stored day (payload included)."""
        with closing(self._connect()) as db:
            return db.execute(
                f"""SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol, day ORDER BY observed_at DESC, rowid DESC) AS rank
                    FROM daily_partitions WHERE {LIVE} AND (? IS NULL OR symbol=?)) WHERE rank=1 ORDER BY symbol, day""",
                (symbol and provider_symbol(symbol),) * 2).fetchall()

    def corrupt_days(self, symbol: str | None = None) -> list[dict]:
        """Days whose newest live revision no longer matches its stored checksum."""
        return [{"symbol": r["symbol"], "day": r["day"], "observed_at": r["observed_at"]}
                for r in self.live_partitions(symbol) if hashlib.sha256(r["payload"]).hexdigest() != r["sha256"]]

    def quarantine(self, symbol: str, day: str, observed_at: str, reason: str, *, at: datetime) -> None:
        """Take one revision out of use. The day becomes unsettled, so the next sync downloads it again; the
        quarantined bytes stay in the file for inspection."""
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO quarantined_partitions VALUES (?, ?, ?, ?, ?)",
                       (provider_symbol(symbol), day, observed_at, _utc(at).isoformat(timespec="microseconds"), reason))

    def quarantined(self, symbol: str | None = None) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM quarantined_partitions WHERE (? IS NULL OR symbol=?) ORDER BY quarantined_at",
                              (symbol and provider_symbol(symbol),) * 2).fetchall()
        return [dict(r) for r in rows]

    def read(
        self, symbol: str, first_day: date, last_day: date, *, as_of: datetime | None = None,
        first_publication: bool = False,
    ) -> tuple[pd.DataFrame, list[dict]]:
        cutoff = _utc(as_of or datetime.now(timezone.utc)).isoformat(timespec="microseconds")
        direction = "ASC" if first_publication else "DESC"
        with closing(self._connect()) as db:
            records = db.execute(
                f"""SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY day ORDER BY observed_at {direction}, rowid {direction}) AS rank
                    FROM daily_partitions WHERE symbol=? AND day BETWEEN ? AND ? AND observed_at<=? AND {LIVE}
                ) WHERE rank=1 ORDER BY day""",
                (provider_symbol(symbol), first_day.isoformat(), last_day.isoformat(), cutoff),
            ).fetchall()
        rows, manifests = [], []
        for record in records:
            payload = record["payload"]
            if hashlib.sha256(payload).hexdigest() != record["sha256"]:
                raise DataError(f"Checksum mismatch for {record['symbol']} {record['day']}")
            rows.extend(json.loads(payload))
            manifests.append({key: record[key] for key in ("symbol", "day", "sha256", "observed_at", "rows")})
        frame = pd.DataFrame(rows, columns=("epoch", *QUOTE_COLUMNS), dtype="float64")
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("epoch"), unit="s", utc=True), name="time_utc")
        return frame, manifests
