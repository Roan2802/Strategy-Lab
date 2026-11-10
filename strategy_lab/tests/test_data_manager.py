import csv
from pathlib import Path

from strategy_lab.data_manager import DataManager


def _write(path: Path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Time (EET)", "Open", "High", "Low", "Close", "Volume "])
        writer.writerows(rows)


def test_dst_fold_inference_and_nonexistent_hour(tmp_path):
    path = tmp_path / "XAUUSD_1 Min_Bid_2000.01.01_2030.01.01.csv"
    _write(path, [
        ["2026.10.25 03:00:00", 1, 2, 0.5, 1.5, 1],
        ["2026.10.25 03:30:00", 1, 2, 0.5, 1.5, 1],
        ["2026.10.25 03:00:00", 1, 2, 0.5, 1.5, 1],
        ["2026.10.25 03:30:00", 1, 2, 0.5, 1.5, 1],
        ["2026.03.29 03:15:00", 1, 2, 0.5, 1.5, 1],
    ])
    frame, stats = DataManager._read_side(path, "bid", chunksize=2)
    assert frame.index.is_monotonic_increasing
    assert frame.index.is_unique
    assert stats["duplicates"] == 0
    assert stats["invalid_time"] == 1
    assert (frame.index[1] - frame.index[0]).total_seconds() == 1800
    assert (frame.index[2] - frame.index[1]).total_seconds() == 1800


def test_cache_preserves_bid_ask_and_reports_gaps(tmp_path):
    source = tmp_path / "source"
    cache = tmp_path / "cache"
    source.mkdir()
    rows = [
        ["2026.09.14 12:00:00", 10, 11, 9, 10.5, 3],
        ["2026.09.14 12:01:00", 10.5, 12, 10, 11.5, 4],
        ["2026.09.14 12:04:00", 11.5, 12, 11, 11.8, 2],
    ]
    _write(source / "XAUUSD_1 Min_Bid_2000.01.01_2030.01.01.csv", rows)
    ask_rows = [[r[0], r[1] + 0.2, r[2] + 0.2, r[3] + 0.2, r[4] + 0.2, r[5]] for r in rows]
    ask_rows.append(["2026.09.14 12:05:00", 12, 13, 11, 12.5, 1])
    _write(source / "XAUUSD_1 Min_Ask_2000.01.01_2030.01.01.csv", ask_rows)
    before = [(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in source.iterdir()]
    manager = DataManager(source, cache)
    data = manager.ensure("XAUUSD")
    assert len(data.frame) == 4
    assert abs(data.frame.iloc[0]["ask_open"] - data.frame.iloc[0]["bid_open"] - 0.2) < 1e-9
    assert data.manifest["bid_missing_minutes"] == 2
    assert data.manifest["missing_bid_volume_rows"] == 1
    assert data.manifest["negative_bid_volume_rows"] == 0
    assert data.manifest["unmatched_bid_timestamps"] == 0
    assert data.manifest["unmatched_ask_timestamps"] == 1
    assert len(manager.candles("XAUUSD", "M1")[0]) == 3
    assert before == [(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in source.iterdir()]
    assert (cache / "XAUUSD_M1_bidask.parquet").exists()
    assert (cache / "XAUUSD_M1_ohlc.parquet").exists()


def test_duplicate_timestamp_is_dropped(tmp_path):
    path = tmp_path / "XAUUSD_1 Min_Bid_2000.01.01_2030.01.01.csv"
    _write(path, [["2026.09.14 12:00:00", 10, 11, 9, 10.5, 1],
                  ["2026.09.14 12:00:00", 10, 11, 9, 10.5, 1]])
    frame, stats = DataManager._read_side(path, "bid")
    assert len(frame) == 1
    assert stats["duplicates"] == 1
