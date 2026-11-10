"""Prepare read-only Dukascopy M1 copies for the Jesse P0 audit.

Only source files inside C:\\AI\\Dukascopy_data are read. All derived outputs go
under evidence/market_data. Original inputs are never opened for writing.
"""
from __future__ import annotations
import csv, json, statistics, time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(r"C:\AI\Dukascopy_data")
OUT = ROOT / "evidence" / "market_data"
START = datetime(2026, 4, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 28, tzinfo=timezone.utc)
SAMPLE_START = datetime(2026, 9, 1, tzinfo=timezone.utc)
SAMPLE_END = datetime(2026, 9, 15, tzinfo=timezone.utc)
FIELDS = ("timestamp", "open", "close", "high", "low", "volume")
ZONE = ZoneInfo("Europe/Helsinki")


def utc_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def parse_eet_timestamp(value: str) -> int:
    # The source labels wall time as EET; use its EET/EEST civil timezone rules.
    local = datetime.strptime(value.strip(), "%Y.%m.%d %H:%M:%S").replace(tzinfo=ZONE)
    return int(local.astimezone(timezone.utc).timestamp() * 1000)


def read_pair(path: Path) -> dict[int, dict]:
    rows = {}
    start_ms = int(START.timestamp() * 1000)
    end_ms = int(END.timestamp() * 1000)
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            r = {(k or "").strip(): (v or "").strip() for k, v in raw.items() if k is not None}
            ms = parse_eet_timestamp(r["Time (EET)"])
            if not start_ms <= ms < end_ms:
                continue
            volume = r.get("TickVolume", r.get("Volume", "0"))
            rows[ms] = {
                "open": float(r["Open"]), "high": float(r["High"]),
                "low": float(r["Low"]), "close": float(r["Close"]),
                "volume": float(volume),
            }
    return rows


def jesse_row(ms: int, candle: dict) -> dict:
    return {"timestamp": ms, "open": candle["open"], "close": candle["close"],
            "high": candle["high"], "low": candle["low"], "volume": candle["volume"]}


def write_jesse(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def gap_summary(rows: list[dict]) -> dict:
    gaps = [(b["timestamp"] - a["timestamp"]) // 60000 - 1
            for a, b in zip(rows, rows[1:])
            if b["timestamp"] - a["timestamp"] > 60000]
    return {
        "gap_intervals": len(gaps),
        "missing_minutes_total": sum(gaps),
        "largest_gap_minutes": max(gaps, default=0),
        "gaps_over_60m": sum(x > 60 for x in gaps),
    }


def validate(rows: list[dict], label: str) -> dict:
    ts = [r["timestamp"] for r in rows]
    numeric = [r[k] for r in rows for k in ("open", "high", "low", "close", "volume")]
    valid_ohlc = all(
        min(r["open"], r["high"], r["low"], r["close"]) > 0
        and r["high"] >= max(r["open"], r["close"], r["low"])
        and r["low"] <= min(r["open"], r["close"], r["high"])
        and r["volume"] >= 0 for r in rows
    )
    return {
        "label": label, "count": len(rows),
        "first_utc": utc_iso(ts[0]) if ts else None,
        "last_utc": utc_iso(ts[-1]) if ts else None,
        "strictly_ascending_unique": all(b > a for a, b in zip(ts, ts[1:])),
        "minute_aligned": all(t % 60000 == 0 for t in ts),
        "ohlc_volume_valid": valid_ohlc,
        "volume_zero_count": sum(r["volume"] == 0 for r in rows),
        "volume_min": min((r["volume"] for r in rows), default=None),
        "volume_max": max((r["volume"] for r in rows), default=None),
        "decimal_places_max": max((len(str(v).partition(".")[2].rstrip("0")) for v in numeric), default=0),
        **gap_summary(rows),
    }


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[int(fraction * (len(s) - 1))], 6)


def export_symbol(symbol: str, bid_file: str, ask_file: str, point: float) -> dict:
    bid_path, ask_path = SOURCE / bid_file, SOURCE / ask_file
    started = time.perf_counter()
    bid, ask = read_pair(bid_path), read_pair(ask_path)
    common = sorted(set(bid) & set(ask))
    rows = [jesse_row(t, bid[t]) for t in sorted(bid)]
    if not rows:
        raise RuntimeError(f"No {symbol} Dukascopy bars in selected range")
    base = f"Duka_{symbol}_M1_20260401_20260928"
    large_path, sample_path = OUT / f"{base}_jesse.csv", OUT / f"Duka_{symbol}_M1_20260901_20260915_jesse.csv"
    write_jesse(rows, large_path)
    sample = [r for r in rows if int(SAMPLE_START.timestamp()*1000) <= r["timestamp"] < int(SAMPLE_END.timestamp()*1000)]
    write_jesse(sample, sample_path)

    sidecar = OUT / f"{base}_bid_ask.csv"
    mt5_rates = OUT / f"{base}_mt5_custom_rates.csv"
    open_spreads = [ask[t]["open"] - bid[t]["open"] for t in common]
    close_spreads = [ask[t]["close"] - bid[t]["close"] for t in common]
    with sidecar.open("w", newline="", encoding="utf-8") as sf, mt5_rates.open("w", newline="", encoding="utf-8") as mf:
        sw, mw = csv.writer(sf, lineterminator="\n"), csv.writer(mf, lineterminator="\n")
        sw.writerow(("timestamp_utc", "timestamp_ms", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close", "bid_tick_volume", "ask_tick_volume", "spread_open", "spread_close"))
        mw.writerow(("time_epoch_seconds", "open", "high", "low", "close", "tick_volume", "spread_points", "real_volume"))
        for t in common:
            b, a = bid[t], ask[t]
            op_spread, cl_spread = a["open"] - b["open"], a["close"] - b["close"]
            sw.writerow((utc_iso(t), t, b["open"], b["high"], b["low"], b["close"], a["open"], a["high"], a["low"], a["close"], b["volume"], a["volume"], op_spread, cl_spread))
            avg_spread_points = max(0, min(32767, round(((op_spread + cl_spread) / 2) / point)))
            mw.writerow((t // 1000, b["open"], b["high"], b["low"], b["close"], int(b["volume"]), avg_spread_points, 0))

    result = {
        "source_files": [str(bid_path), str(ask_path)],
        "source_timestamp_label": "EET; converted with Europe/Helsinki zoneinfo to UTC",
        "price_side_used_for_Jesse_and_MT5": "bid OHLC",
        "volume_field": "Dukascopy TickVolume or Volume, preserved as supplied; not real traded volume",
        "spread_for_MT5_custom_symbol": f"per-bar average of ask-bid open and close, divided by {point} price point and rounded to integer points",
        "large": validate(rows, f"Dukascopy {symbol} 6-month M1 bid"),
        "sample": validate(sample, f"Dukascopy {symbol} 2-week M1 bid"),
        "bid_rows": len(bid), "ask_rows": len(ask), "aligned_bid_ask_rows": len(common),
        "unmatched_bid_rows": len(set(bid)-set(ask)), "unmatched_ask_rows": len(set(ask)-set(bid)),
        "spread_open_price": {"mean": round(statistics.fmean(open_spreads), 6) if open_spreads else None, "median": round(statistics.median(open_spreads), 6) if open_spreads else None, "p95": percentile(open_spreads, .95), "max": round(max(open_spreads), 6) if open_spreads else None},
        "spread_close_price": {"mean": round(statistics.fmean(close_spreads), 6) if close_spreads else None, "median": round(statistics.median(close_spreads), 6) if close_spreads else None, "p95": percentile(close_spreads, .95), "max": round(max(close_spreads), 6) if close_spreads else None},
        "files": [large_path.name, sample_path.name, sidecar.name, mt5_rates.name],
        "prepare_elapsed_seconds": round(time.perf_counter()-started, 3),
    }
    return result


def verify_xau_timezone_crosscheck() -> dict:
    # Independent same-folder cross-check: the UTC MT5-shaped Dukascopy file's
    # 2026-09-27 23:58 bar must equal the EEST raw bid at 2026-09-28 02:58.
    shaped = SOURCE / "XAUUSD_DUKA_M1_OHLC_MT5.csv"
    values = None
    with shaped.open("r", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r["<DATE>"] == "2026.09.27" and r["<TIME>"] == "23:58:00":
                values = [float(r[f"<{k}>"]) for k in ("OPEN", "HIGH", "LOW", "CLOSE")]
                break
    raw_path = SOURCE / "XAUUSD_1 Min_Bid_2004.01.01_2026.10.15.csv"
    raw = None
    with raw_path.open("r", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["Time (EET)"].strip() == "2026.09.28 02:58:00":
                raw = [float(r[k]) for k in ("Open", "High", "Low", "Close")]
                break
    return {"utc_shaped_timestamp": "2026-09-27T23:58:00Z", "eet_raw_timestamp": "2026-09-28 02:58 EEST", "same_folder_ohlc_exact_match": values == raw, "utc_shaped_ohlc": values, "eet_raw_bid_ohlc": raw}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    manifest = {
        "source_data_root_read_only": str(SOURCE),
        "original_files_modified": False,
        "range_utc": [START.isoformat(), END.isoformat()],
        "sample_range_utc": [SAMPLE_START.isoformat(), SAMPLE_END.isoformat()],
        "xau_timezone_crosscheck": verify_xau_timezone_crosscheck(),
        "xauusd": export_symbol("XAUUSD", "XAUUSD_1 Min_Bid_2004.01.01_2026.10.15.csv", "XAUUSD_1 Min_Ask_2004.01.01_2026.10.15.csv", point=0.001),
        "us30": export_symbol("US30", "USA30IDXUSD_1 Min_Bid_2014.01.01_2026.10.12.csv", "USA30IDXUSD_1 Min_Ask_2014.01.01_2026.10.12.csv", point=0.001),
    }
    manifest["total_prepare_elapsed_seconds"] = round(time.perf_counter()-started, 3)
    checks = [manifest[s][key] for s in ("xauusd", "us30") for key in ("large", "sample")]
    if not manifest["xau_timezone_crosscheck"]["same_folder_ohlc_exact_match"]:
        raise RuntimeError(f"Timezone cross-check did not match: {manifest['xau_timezone_crosscheck']}")
    if any(c["count"] == 0 or not c["strictly_ascending_unique"] or not c["minute_aligned"] or not c["ohlc_volume_valid"] for c in checks):
        raise RuntimeError("A Dukascopy output failed its row/timestamp/OHLC validation")
    path = OUT / "p0_dukascopy_manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))

if __name__ == "__main__":
    main()
