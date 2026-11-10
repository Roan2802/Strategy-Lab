"""Versioned forward registrations and prospective feed eligibility."""
from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePosixPath
import sqlite3
from uuid import uuid4

import pandas as pd

from .daily_data import DailyPartitionStore, _utc, provider_symbol
from .data_manager import DISPLAY_ALIASES, DataError


REQUIRED = {
    "strategy", "parameters", "indicators", "symbol", "timeframe", "data_version",
    "execution_model", "contract", "starting_capital", "trade_risk",
    "portfolio_risk_limit", "news_filter",
}


def data_symbol(symbol: str) -> str:
    """Name the daily store accepts for a symbol; ``provider_symbol`` rejects its own DE40/US500 output."""
    return DISPLAY_ALIASES.get(symbol, symbol)


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(",", ":")).encode("ascii")


class ForwardRegistry:
    def __init__(self, data: DailyPartitionStore):
        self.data = data
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS frozen_strategies (
                id TEXT PRIMARY KEY, frozen_at TEXT NOT NULL, source_sha256 TEXT NOT NULL,
                snapshot_sha256 TEXT NOT NULL, configuration BLOB NOT NULL, source BLOB NOT NULL
            )""")

    def _connect(self):
        db = sqlite3.connect(self.data.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def freeze(self, configuration: dict, sources: dict[str, bytes], *,
               frozen_at: datetime | None = None) -> dict:
        missing = REQUIRED - configuration.keys()
        if missing:
            raise DataError(f"Missing forward settings: {', '.join(sorted(missing))}")
        normalized = {**configuration, "symbol": provider_symbol(data_symbol(configuration["symbol"]))}
        if not sources:
            raise DataError("Freeze the strategy and its source files")
        encoded = {}
        for name, content in sources.items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or name != path.as_posix() or ":" in name or "\\" in name:
                raise DataError("Frozen file names must be project-relative POSIX paths")
            encoded[name] = base64.b64encode(content).decode("ascii")
        source = _canonical(encoded)
        config = _canonical(normalized)
        registered = _utc(frozen_at or datetime.now(timezone.utc)).isoformat(timespec="microseconds")
        code_hash = hashlib.sha256(source).hexdigest()
        snapshot_hash = hashlib.sha256(_canonical([registered, code_hash, normalized])).hexdigest()
        identifier = str(uuid4())
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO frozen_strategies VALUES (?, ?, ?, ?, ?, ?)",
                       (identifier, registered, code_hash, snapshot_hash, config, source))
        return self.get(identifier)

    def get(self, identifier: str) -> dict:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM frozen_strategies WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise DataError("Unknown forward registration")
        configuration = json.loads(row["configuration"])
        source_hash = hashlib.sha256(row["source"]).hexdigest()
        fingerprint = hashlib.sha256(_canonical([row["frozen_at"], source_hash, configuration])).hexdigest()
        if source_hash != row["source_sha256"] or fingerprint != row["snapshot_sha256"]:
            raise DataError("Frozen strategy checksum mismatch")
        return {"id": identifier, "frozen_at": row["frozen_at"], "configuration": configuration,
                "source_sha256": source_hash, "snapshot_sha256": fingerprint, "status": "FROZEN",
                "mode": "DAILY_DELAYED_FORWARD",
                "sources": {name: base64.b64decode(content) for name, content in json.loads(row["source"]).items()}}

    def eligible_data(self, identifier: str, *, as_of: datetime) -> tuple[pd.DataFrame, list[dict]]:
        """Return original publications with candle opens and availability after registration."""
        registration = self.get(identifier)
        frozen = datetime.fromisoformat(registration["frozen_at"])
        cutoff = _utc(as_of)
        frame, manifests = self.data.read(data_symbol(registration["configuration"]["symbol"]), frozen.date(), cutoff.date(),
                                          as_of=cutoff, first_publication=True)
        manifests = [item for item in manifests if datetime.fromisoformat(item["observed_at"]) > frozen]
        eligible_days = {item["day"] for item in manifests}
        eligible = frame[(frame.index >= frozen) & frame.index.strftime("%Y-%m-%d").isin(eligible_days)]
        used_days = set(eligible.index.strftime("%Y-%m-%d"))
        return eligible, [item for item in manifests if item["day"] in used_days]
