"""Shared helpers for the V9 consistency evidence scripts (real Dukascopy data, read-only)."""
from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "runtime"))
OUT = Path(__file__).resolve().parent

import numpy as np
import pandas as pd

from strategy_lab.data_manager import DataManager

CACHE = ROOT / "strategy_lab" / "data_cache"
_dm = None


def data_manager() -> DataManager:
    """Explicit cache_root disables the daily partition overlay: only the Dukascopy source folder is read."""
    global _dm
    if _dm is None:
        _dm = DataManager(cache_root=CACHE)
    return _dm


def save(name: str, payload: dict) -> Path:
    path = OUT / name
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def timer():
    t0 = time.time()
    return lambda: round(time.time() - t0, 1)


# ----------------------------------------------------------------------------- real data -> daily store

from strategy_lab.daily_data import DailyPartitionStore

FIELDS = ("open", "high", "low", "close", "volume")


def observed_after(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)


def ingest_real_range(store, symbol: str, first: date, last: date, m1: pd.DataFrame | None = None) -> dict:
    """Publish every UTC day in [first, last] of real Dukascopy M1 as a closed daily partition.

    Days without any quote become MARKET CLOSED records (as the daily sync does). Days the store refuses
    (negative spread, off-calendar ...) are returned in ``rejected`` with the store's reason.
    """
    if m1 is None:
        m1, _ = data_manager().candles(symbol, "M1", first.isoformat(), (last + timedelta(days=1)).isoformat())
    complete = m1[m1[["bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close"]]
                  .notna().all(axis=1)]
    stats = {"published": 0, "closed": 0, "rejected": [], "dropped_incomplete_quote_minutes": int(len(m1) - len(complete))}
    day = first
    while day <= last:
        rows = complete[(complete.index >= pd.Timestamp(day, tz="UTC")) & (complete.index < pd.Timestamp(day + timedelta(days=1), tz="UTC"))]
        bid = rows[[f"bid_{c}" for c in FIELDS]].rename(columns=lambda c: c[4:])
        ask = rows[[f"ask_{c}" for c in FIELDS]].rename(columns=lambda c: c[4:])
        if bid.empty:
            bid = pd.DataFrame({c: pd.Series(dtype="float64") for c in FIELDS},
                               index=pd.DatetimeIndex([], tz="UTC"))
            ask = bid.copy()
        result = store.ingest(symbol, day, bid, ask, expected_minutes=bid.index, observed_at=observed_after(day))
        if result["status"] == "UP TO DATE":
            stats["published"] += 1
        elif result["status"] == "MARKET CLOSED":
            stats["closed"] += 1
        else:
            stats["rejected"].append({"day": day.isoformat(), "status": result["status"], "detail": result.get("detail")})
        day += timedelta(days=1)
    return stats
