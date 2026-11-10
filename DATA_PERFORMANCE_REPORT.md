# Dukascopy data performance — 2026-11-06

## Verified in this continuation

- XAUUSD M1 quote cache loaded successfully and reported 8,244,053 source quotes, with source hash prefix `1ddb8c54ff35…`.
- The new bounded optimizer and selected-trial backtest reused the parsed Dukascopy cache. Their manifests identify `C:\AI\Dukascopy_data`; no price data from elsewhere was introduced.
- Source files were not modified. No synthetic candles were added.

## Full-import comparison — 2026-11-06

The old implementation (preserved at `evidence/backups/20261106-chunked-datamanager-data_manager.py`) and current implementation ran in separate fresh processes with an empty temporary cache. Both read only the same original Dukascopy Bid and Ask CSVs, rebuilt the complete joined Parquet cache, and were compared exactly against the existing cache across every row, timestamp, OHLC value, and volume value. Both produced the same quote-quality counts and the same temporary cache size; source hashes were recorded. The original source files were read-only.

| Full import | Old implementation | Current implementation | Change |
|---|---:|---:|---:|
| Elapsed time | 50.038 s | 28.202 s | 43.6% faster |
| Peak process RSS | 5.42 GB | 2.82 GB | 47.9% lower |
| Temporary Parquet and manifest | 244,798,896 bytes | 244,798,896 bytes | unchanged |
| Joined rows | 8,244,053 | 8,244,053 | exact |

The complete-import improvement comes primarily from removing two Python sets containing millions of timestamps and avoiding a redundant full-frame sort when the joined index is already monotonic. CSV parsing itself is still chunked at 250,000 rows and keeps DST fold inference continuous across chunk boundaries. The isolated parser peaks fell about 28–30%; parse-only time was 5–7% slower, while full import was 43.6% faster.

The current import classified 2 Bid-only timestamps and 0 Ask-only timestamps, with no duplicates or invalid timestamps removed. Reproduce all six old/new measurements with `python evidence/test_outputs/benchmark_chunked_dukascopy_parse_20261106.py`; raw timings, memory samples, per-file hashes, and equality assertions are in `evidence/test_outputs/chunked_dukascopy_parse_20261106.json`.

## Existing data-quality boundary

The prior audit reports incomplete/unmatched Bid/Ask rows, including 2,670 Ask-only USATECHIDXUSD timestamps. The execution ledger skips incomplete quote rows. This continuation did not alter or reclassify the source data.

## Full-import memory and time validation — 2026-11-06

The current 250,000-row parser plus index-based Bid/Ask reconciliation was benchmarked end to end against the backed-up pre-change importer using fresh processes and temporary cache folders. It reproduced all 8,244,053 joined rows exactly against the established cache, preserved quality counts and output cache size, cut peak process RSS by 47.9% (5.42 GB to 2.82 GB), and cut first-import time by 43.6% (50.04 s to 28.20 s). The original Dukascopy CSV files were not modified. Detailed reproducible evidence is in `evidence/test_outputs/chunked_dukascopy_parse_20261106.json`.
