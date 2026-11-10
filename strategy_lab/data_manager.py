"""Lazy, read-only Dukascopy M1 loader with an immutable Parquet cache."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_SOURCE = Path(r"C:\AI\Dukascopy_data")
DEFAULT_DAILY_STORE = Path(__file__).resolve().parent / "data_cache" / "daily_dukascopy.sqlite3"
SYMBOLS = {
    "XAUUSD": "XAUUSD",
    "US30": "USA30IDXUSD",
    "USA30IDXUSD": "USA30IDXUSD",
    "USDJPY": "USDJPY",
    "BTCUSD": "BTCUSD",
    "DE40": "DEUIDXEUR",
    "US500": "USA500IDXUSD",
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "AUDUSD": "AUDUSD",
    "USDCAD": "USDCAD",
    "USATECHIDXUSD": "USATECHIDXUSD",
}
DISPLAY_ALIASES = {"USA30IDXUSD": "US30", "DEUIDXEUR": "DE40", "USA500IDXUSD": "US500"}
PRICE_COLUMNS = ("open", "high", "low", "close")
CACHE_VERSION = 2


class DataError(ValueError):
    pass


@dataclass
class Dataset:
    symbol: str
    frame: pd.DataFrame
    manifest: dict


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class DataManager:
    def __init__(self, source_root: Path = DEFAULT_SOURCE, cache_root: Path | None = None,
                 daily_store_path: Path | None = None):
        self.source_root = Path(source_root)
        self.cache_root = Path(cache_root) if cache_root else Path(__file__).resolve().parent / "data_cache"
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.daily_store_path = (Path(daily_store_path) if daily_store_path else
                                 DEFAULT_DAILY_STORE if cache_root is None and self.source_root == DEFAULT_SOURCE else None)
        self._data_memory: dict[str, Dataset] = {}
        self._timeframe_memory: dict[tuple[str, str], pd.DataFrame] = {}
        self._fingerprint_memory: dict[tuple[str, int, int], dict] = {}

    def available_symbols(self) -> list[dict]:
        output = []
        for source_symbol in self._discovered_source_symbols():
            display_symbol = DISPLAY_ALIASES.get(source_symbol, source_symbol)
            output.append({"symbol": display_symbol, "source_symbol": source_symbol, "available": True})
        return output

    def _discovered_source_symbols(self) -> list[str]:
        if not self.source_root.is_dir():
            return []
        sides: dict[str, set[str]] = {}
        for path in self.source_root.glob("*.csv"):
            match = re.match(r"^(?P<symbol>[^_]+)_1 Min_(?P<side>Bid|Ask)_", path.name, re.IGNORECASE)
            if match:
                sides.setdefault(match.group("symbol").upper(), set()).add(match.group("side").lower())
        priority = {symbol: index for index, symbol in enumerate(
            ("XAUUSD", "USDJPY", "BTCUSD", "DEUIDXEUR", "USA30IDXUSD", "USA500IDXUSD", "EURUSD", "GBPUSD")
        )}
        return sorted((symbol for symbol, present in sides.items() if present == {"bid", "ask"}),
                      key=lambda symbol: (priority.get(symbol, 99), symbol))

    def _source_file(self, source_symbol: str, side: str) -> Path | None:
        if not self.source_root.is_dir():
            return None
        matches = [p for p in self.source_root.glob("*.csv")
                   if re.match(rf"^{re.escape(source_symbol)}_1 Min_{re.escape(side)}_", p.name, re.IGNORECASE)]
        if not matches:
            return None
        # If multiple exports exist, prefer the widest stated date range.
        return max(matches, key=lambda p: p.stat().st_size)

    @staticmethod
    def _source_fingerprint(path: Path) -> dict:
        stat = path.stat()
        return {"path": str(path), "bytes": stat.st_size, "sha256": _sha256(path)}

    def _fingerprint(self, path: Path) -> dict:
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        if key not in self._fingerprint_memory:
            self._fingerprint_memory[key] = {"path": str(path), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                                             "sha256": _sha256(path)}
        return self._fingerprint_memory[key]

    def ensure(self, symbol: str) -> Dataset:
        symbol = symbol.upper()
        if not self.source_root.is_dir():
            raise DataError(f"Dukascopy source directory is unavailable: {self.source_root}")
        source_symbol = SYMBOLS.get(symbol, symbol)
        if source_symbol not in self._discovered_source_symbols():
            raise DataError(f"Unsupported symbol: {symbol}")
        bid_path = self._source_file(source_symbol, "Bid")
        ask_path = self._source_file(source_symbol, "Ask")
        if bid_path is None or ask_path is None:
            raise DataError(f"Missing Dukascopy Bid/Ask M1 pair for {source_symbol} under {self.source_root}")
        parquet = self.cache_root / f"{symbol}_M1_bidask.parquet"
        manifest_path = self.cache_root / f"{symbol}_M1_bidask_manifest.json"
        if symbol not in self._data_memory:
            self._data_memory.clear()
            self._timeframe_memory.clear()
        # Hashes are checked on every load so stale caches cannot silently outlive source changes.
        bid_meta, ask_meta = self._fingerprint(bid_path), self._fingerprint(ask_path)
        expected = {"cache_version": CACHE_VERSION, "symbol": symbol, "source_symbol": source_symbol,
                    "bid_source": bid_meta, "ask_source": ask_meta}
        if parquet.exists() and manifest_path.exists():
            cached = json.loads(manifest_path.read_text(encoding="utf-8"))
            if all(cached.get(k) == v for k, v in expected.items()):
                if symbol not in self._data_memory:
                    frame = pd.read_parquet(parquet)
                    if "gap_classification" not in cached:
                        cached["gap_classification"] = self._classify_gaps(frame)
                        manifest_path.write_text(json.dumps(cached, indent=2), encoding="utf-8")
                    cached["cache_status"] = "hit"
                    self._data_memory[symbol] = Dataset(symbol, frame, cached)
                return self._data_memory[symbol]

        bid, bid_stats = self._read_side(bid_path, "bid")
        ask, ask_stats = self._read_side(ask_path, "ask")
        frame = bid.join(ask, how="outer")
        if not frame.index.is_monotonic_increasing:
            frame = frame.sort_index()
        frame.index.name = "time_utc"
        duplicates = bid_stats["duplicates"] + ask_stats["duplicates"]
        unmatched_bid = len(bid.index.difference(ask.index, sort=False))
        unmatched_ask = len(ask.index.difference(bid.index, sort=False))
        valid_bid = frame[[f"bid_{x}" for x in PRICE_COLUMNS]].notna().all(axis=1)
        for side in ("bid", "ask"):
            cols = [f"{side}_{x}" for x in PRICE_COLUMNS]
            complete = frame[cols].notna().all(axis=1)
            invalid = complete & ((frame[f"{side}_high"] < frame[[f"{side}_open", f"{side}_close", f"{side}_low"]].max(axis=1)) |
                                  (frame[f"{side}_low"] > frame[[f"{side}_open", f"{side}_close", f"{side}_high"]].min(axis=1)))
            if invalid.any():
                raise DataError(f"{int(invalid.sum())} invalid {side} OHLC rows for {symbol}")
        gaps = frame.index.to_series().diff().dt.total_seconds().div(60).sub(1)
        gap_positions = (gaps[valid_bid].dropna() > 0)
        gap_samples = []
        for timestamp in gaps[valid_bid].dropna().loc[lambda values: values > 0].index[:20]:
            missing_minutes = max(0, int(gaps.loc[timestamp]))
            previous_timestamp = timestamp - pd.Timedelta(minutes=missing_minutes + 1)
            gap_samples.append({"after_utc": previous_timestamp.isoformat(), "before_utc": timestamp.isoformat(),
                                "missing_minutes": missing_minutes})
        volume = frame["bid_volume"]
        volume_missing = int(volume.isna().sum())
        volume_negative = int((volume < 0).sum())
        manifest = {
            **expected,
            "source_policy": "Only read source files from C:\\AI\\Dukascopy_data; originals are never modified.",
            "timestamp_label": "EET wall time converted to UTC using Europe/Helsinki; ambiguous times inferred in source order.",
            "columns": list(frame.columns),
            "rows_union": len(frame), "bid_rows": len(bid), "ask_rows": len(ask),
            "unmatched_bid_timestamps": unmatched_bid, "unmatched_ask_timestamps": unmatched_ask,
            "duplicates_dropped": duplicates,
            "invalid_or_nonexistent_timestamps_dropped": bid_stats["invalid_time"] + ask_stats["invalid_time"],
            "bid_gap_intervals": int(gap_positions.sum()),
            "bid_missing_minutes": int(gaps[valid_bid].clip(lower=0).sum()),
            "gap_classification": self._classify_gaps(frame),
            "bid_gap_samples": gap_samples,
            "negative_bid_volume_rows": volume_negative,
            "missing_bid_volume_rows": volume_missing,
            "first_utc": frame.index.min().isoformat() if len(frame) else None,
            "last_utc": frame.index.max().isoformat() if len(frame) else None,
            "bid_volume_kind": "Dukascopy TickVolume/Volume as supplied; not real traded volume.",
            "cache_status": "rebuilt",
            "last_imported_at": datetime.now(timezone.utc).isoformat(),
        }
        temp = parquet.with_suffix(".parquet.tmp")
        frame.to_parquet(temp, index=True, compression="zstd")
        temp.replace(parquet)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        dataset = Dataset(symbol, frame, manifest)
        self._data_memory[symbol] = dataset
        return dataset

    @staticmethod
    def _classify_gaps(frame: pd.DataFrame) -> dict:
        """Identify likely schedule closures by repeated UTC quote-window patterns.

        These are schedule candidates inferred from Dukascopy timestamps, not
        exchange-calendar proof. Holidays and source outages remain unclassified.
        """
        gaps = frame.index.to_series().diff().dt.total_seconds().div(60).sub(1)
        if "bid_open" in frame:
            gaps = gaps[frame["bid_open"].notna()]
        gaps = gaps.dropna()
        counts = {"likely_daily_break": 0, "likely_weekend_closure": 0, "unclassified": 0}
        minutes = {key: 0 for key in counts}
        for timestamp, missing in gaps[gaps > 0].items():
            missing = int(missing)
            before = timestamp.tz_convert("Europe/Helsinki")
            after = (timestamp - pd.Timedelta(minutes=missing + 1)).tz_convert("Europe/Helsinki")
            if (missing >= 1000 and after.weekday() == 4 and before.weekday() == 0 and
                    after.hour >= 22 and before.hour <= 2):
                kind = "likely_weekend_closure"
            elif (30 <= missing <= 360 and after.hour >= 22 and before.hour <= 1 and
                    (before.date() - after.date()).days in (0, 1)):
                kind = "likely_daily_break"
            else:
                kind = "unclassified"
            counts[kind] += 1
            minutes[kind] += missing
        return {"heuristic": "Europe/Helsinki wall-clock daily and Friday-to-Monday windows; candidates, not proof",
                "intervals": counts, "missing_minutes": minutes}

    @staticmethod
    def _read_side(path: Path, side: str, chunksize: int = 250_000) -> tuple[pd.DataFrame, dict]:
        wanted = {"Time (EET)", "Open", "High", "Low", "Close", "TickVolume", "Volume"}
        header = pd.read_csv(path, encoding="utf-8-sig", nrows=0)
        columns = [str(column).strip() for column in header.columns]
        required = {"Time (EET)", "Open", "High", "Low", "Close"}
        missing = required - set(columns)
        if missing:
            raise DataError(f"{path.name}: missing columns {sorted(missing)}")
        usecols = [column for column in header.columns if str(column).strip() in wanted]
        nat_value = np.iinfo(np.int64).min
        frames = []
        invalid_time = 0
        previous_utc_ns = nat_value
        for raw in pd.read_csv(path, encoding="utf-8-sig", usecols=usecols, chunksize=chunksize):
            raw.columns = raw.columns.str.strip()
            naive = pd.to_datetime(raw["Time (EET)"].astype(str).str.strip(),
                                   format="%Y.%m.%d %H:%M:%S", errors="coerce")
            # Resolve the repeated fall-back hour in source order, carrying the
            # previous raw row across chunk boundaries.
            localized = naive.dt.tz_localize("Europe/Helsinki", ambiguous=True, nonexistent="NaT")
            alternate = naive.dt.tz_localize("Europe/Helsinki", ambiguous=False, nonexistent="NaT")
            ambiguous_mask = localized.notna() & alternate.notna() & (localized != alternate)
            localized_values = localized.array.asi8.copy()
            alternate_values = alternate.array.asi8
            for position in np.flatnonzero(ambiguous_mask.to_numpy()):
                previous = localized_values[position - 1] if position else previous_utc_ns
                candidates = sorted((localized_values[position], alternate_values[position]))
                later = [candidate for candidate in candidates if previous == nat_value or candidate > previous]
                localized_values[position] = later[0] if later else candidates[0]
            if len(localized_values):
                previous_utc_ns = int(localized_values[-1])
            invalid_time += int(np.count_nonzero(localized_values == nat_value))
            frame = pd.DataFrame(index=pd.DatetimeIndex(pd.to_datetime(localized_values, unit="ns", utc=True)))
            for col in PRICE_COLUMNS:
                frame[f"{side}_{col}"] = pd.to_numeric(raw[col.title()], errors="coerce").to_numpy()
            volume_col = "TickVolume" if "TickVolume" in raw.columns else "Volume" if "Volume" in raw.columns else None
            frame[f"{side}_volume"] = (pd.to_numeric(raw[volume_col], errors="coerce").to_numpy()
                                        if volume_col else float("nan"))
            frames.append(frame)
        frame = pd.concat(frames) if frames else pd.DataFrame(
            columns=[f"{side}_{column}" for column in (*PRICE_COLUMNS, "volume")],
            index=pd.DatetimeIndex([], tz="UTC"))
        frame = frame[~frame.index.isna()]
        duplicates = int(frame.index.duplicated(keep="first").sum())
        frame = frame[~frame.index.duplicated(keep="first")].sort_index()
        return frame, {"duplicates": duplicates, "invalid_time": invalid_time}

    def candles(self, symbol: str, timeframe: str, start: str | None = None, end: str | None = None) -> tuple[pd.DataFrame, dict]:
        data = self.ensure(symbol)
        freq_map = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min", "H1": "1h", "H4": "4h", "D1": "1D"}
        timeframe = timeframe.upper()
        if timeframe not in freq_map:
            raise DataError(f"Unsupported timeframe: {timeframe}")
        memory_key = (symbol.upper(), timeframe)
        out = None
        windowed = bool(start or end)
        start_ts = pd.Timestamp(start) if start else None
        end_ts = pd.Timestamp(end) if end else None
        if start_ts is not None and start_ts.tzinfo is None:
            start_ts = start_ts.tz_localize("UTC")
        if end_ts is not None and end_ts.tzinfo is None:
            end_ts = end_ts.tz_localize("UTC")
        window = data.frame
        daily_manifests = []
        if self.daily_store_path and self.daily_store_path.is_file() and not window.empty:
            from .daily_data import DailyPartitionStore

            last_source = window.index[-1]
            first_day = max(last_source, start_ts.floor(freq_map[timeframe]) if start_ts is not None else last_source)
            end_day = end_ts.tz_convert("UTC").date() if end_ts is not None else datetime.now(timezone.utc).date()
            if first_day.tz_convert("UTC").date() <= end_day and (end_ts is None or end_ts > last_source):
                daily, daily_manifests = DailyPartitionStore(self.daily_store_path).read(
                    data.manifest["source_symbol"], first_day.tz_convert("UTC").date(), end_day,
                )
                daily = daily[daily.index > last_source]
                if end_ts is not None:
                    daily = daily[daily.index < end_ts]
                if not daily.empty:
                    days = set(daily.index.strftime("%Y-%m-%d"))
                    daily_manifests = [item for item in daily_manifests if item["day"] in days]
                    window = pd.concat([window, daily])
                else:
                    daily_manifests = []
        if windowed or daily_manifests:
            if start_ts is not None:
                window = window.loc[window.index >= start_ts.floor(freq_map[timeframe])]
            if end_ts is not None:
                window = window.loc[window.index < end_ts]
            out = self._aggregate(window, freq_map[timeframe])
        if out is None:
            out = self._timeframe_memory.get(memory_key)
        if out is None:
            if not windowed:
                self._timeframe_memory.clear()
            tf_path = self.cache_root / f"{symbol}_{timeframe}_ohlc.parquet"
            tf_manifest_path = self.cache_root / f"{symbol}_{timeframe}_manifest.json"
            source_key = {"bid_sha256": data.manifest["bid_source"]["sha256"],
                          "ask_sha256": data.manifest["ask_source"]["sha256"], "timeframe": timeframe}
            cached_tf = json.loads(tf_manifest_path.read_text(encoding="utf-8")) if tf_manifest_path.exists() else {}
            if tf_path.exists() and all(cached_tf.get(k) == v for k, v in source_key.items()):
                out = pd.read_parquet(tf_path)
            else:
                out = self._aggregate(data.frame, freq_map[timeframe])
                temp_tf = tf_path.with_suffix(".parquet.tmp")
                out.to_parquet(temp_tf, index=True, compression="zstd")
                temp_tf.replace(tf_path)
                tf_manifest_path.write_text(json.dumps({**source_key, "rows": len(out)}, indent=2), encoding="utf-8")
            if not windowed:
                self._timeframe_memory[memory_key] = out
        if start_ts is not None:
            out = out[out.index >= (start_ts.floor(freq_map[timeframe]) if windowed else start_ts)]
        if end_ts is not None:
            out = out[out.index < end_ts]
        manifest = {**data.manifest, "timeframe": timeframe, "timeframe_rows": len(out)}
        if daily_manifests:
            manifest.update(daily_partitions=daily_manifests, last_utc=window.index.max().isoformat())
        return out, manifest

    @staticmethod
    def _aggregate(frame: pd.DataFrame, frequency: str) -> pd.DataFrame:
        aggregation = {}
        for side in ("bid", "ask"):
            aggregation.update({f"{side}_open": "first", f"{side}_high": "max", f"{side}_low": "min",
                                f"{side}_close": "last", f"{side}_volume": "sum"})
        out = frame.resample(frequency, label="left", closed="left", origin="start_day").agg(aggregation)
        out = out[out["bid_open"].notna()]
        out["spread_open"] = out["ask_open"] - out["bid_open"]
        out["spread_close"] = out["ask_close"] - out["bid_close"]
        out.index.name = "time_utc"
        return out


def dataset_digest(manifest: dict, timeframe: str, start: str, end: str) -> str:
    payload = json.dumps({"manifest": manifest, "timeframe": timeframe, "start": start, "end": end},
                         sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()
