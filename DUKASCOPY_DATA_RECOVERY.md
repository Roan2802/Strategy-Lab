# Dukascopy data recovery (v10.2) - 2026-11-10/11

REAL = observed on this machine. Evidence: evidence/v102/data/ (status_before.json, sync_run1-4.txt, status_after.json, daily_sync.local.before_v102.json).

## Verdict
NOT current. 3 of 9 were current at the start; after recovery attempts the provider (datafeed.dukascopy.com) kept answering HTTP 503 / URLError from this machine, and by 2026-11-11 15:51 UTC the new session day also counts as missing, so 0 of 9 are current (overall ERROR, "9 achter"). No data is claimed current that is not stored.

## 1. Re-check and recovery (REAL)
Integrity for every stored symbol: paired bid+ask, UTC index, duplicates 0, negative spread 0, price scale ok, incomplete days 0, SQLite integrity_check ok, 10 quarantined partitions (from the v10.1 calendar repair, originals kept), no duplicate (symbol, day, revision) rows.

| Symbol | Before (10-11 20:44Z) lag / last candle | After (11-11 15:51Z) lag / last candle | Note |
|---|---|---|---|
| XAUUSD | 0 / 11-09 | 1 / 11-09 | new day 11-10 not yet published/downloaded (503) |
| EURUSD | 0 / 11-09 | 1 / 11-09 | same |
| GBPUSD | 0 / 11-09 | 1 / 11-09 | same |
| USDJPY | 3 / 11-05 | 3 / 11-06 | +1 day recovered 20:45Z (11-06), then 503 |
| US30 | 21 / 10-20 | 22 / 10-20 | no recovery possible, 503 |
| US500 | 24 / 10-14 | 25 / 10-14 | same |
| USTEC | 24 / 10-14 | 25 / 10-14 | same |
| DE40 | 22 (ERROR) / 10-14 | 23 / 10-14 | stale JForex error replaced by provider outage |
| BTCUSD | 23 / 10-17 | 24 / 10-17 | same |

Only one day (USDJPY 2026-11-06, 1320 rows) could be imported. Failures: `HTTP 503 from Dukascopy (na 3 pogingen)` and `datafeed unreachable: URLError`. Every run honoured the stored backoff (global hold, 2 min doubling to 6 h cap); runs inside a hold sent no request. No 429/WAF block seen, no bypass, 1 req/s, honest User-Agent. Faulty periods are re-imported per day partition only.
Not recoverable now: US30/US500/USTEC/DE40/BTCUSD back to mid-October until the provider answers. They continue automatically via the scheduled task.

Fix: a failed attempt older than a later successful import of the same symbol no longer shows as ERROR (DE40 showed a 2026-11-09 JForex error although JForex is gated off without credentials); it shows as catching up.

## 2. Scheduler (REAL)
Task "Dukascopy Strategy Lab Daily Sync": enabled, Ready, StartWhenAvailable, last result 0, missed runs 0. `daily_sync_runs`: 41 SCHEDULED rows (first 11-09 09:20Z, last 11-11 15:50Z) and 8 MANUAL rows; all RETRY (open days remain). No scheduled run between 11-10 ~21:00Z and 11-11 15:50Z: the machine was asleep/off, and the 15:50Z run is the catch-up after wake (StartWhenAvailable; real evidence, inferred from the gap, Task Scheduler history stays disabled). Provider outages are handled as DELAYED with backoff. Double download is prevented by the sync lock (earlier REAL BUSY result). The in-app worker was down (issue WORKER_DOWN reported honestly); the task does not need it.

## 3. Calendars
Source: Dukascopy "Range of markets", https://www.dukascopy.com/swiss/english/cfd/range-of-markets/ (no page date; (c) 1998-2026), fetched 2026-11-10: USA30/USA500/USATECH.IDX trade Sun-Fri 22:00-20:15 GMT summer, 23:00-21:15 GMT winter, break 20:15-22:00 / 21:15-23:00.
- US30, US500, USTEC: weekly hours equal this table in New York time (18:00-16:15, DST-aware) -> `VERIFIED_PROVIDER` (weekly hours only; holiday overrides stay derived from data).
- DE40: provider table gives winter close 21:00 GMT = 22:00 Berlin, derived calendar says 22:15 Berlin in winter -> mismatch, stays `DERIVED_FROM_DATA`.
- XAUUSD, EURUSD, GBPUSD, USDJPY, BTCUSD: not on that page -> `DERIVED_FROM_DATA`.
Closed markets (weekends, configured holidays) are MARKET_CLOSED, never missing.

## 4. API (additive, GET /api/data/supervisor)
New: `counts {current, closed, delayed, total}`, `last_success_sync`, `scheduler_status` (ACTIVE/MISSING/DISABLED/STALE/FAILED). Existing and kept: per symbol `last_candle, last_check, last_success, lag_days, next_attempt, reason_nl, status`, plus `scheduler`, `overall`, `issues`. POST /api/data/supervisor/check-sync is unchanged ("Nu synchroniseren").

## 5. Tests
strategy_lab/tests/test_v102_data_recovery.py: API fields and counts, stale error cleared, real config calendar provenance, originals (size+mtime of all 18+ files, sha256 of the smallest) unchanged, live provider probe (OBSERVED, skips when unavailable; skipped here). 503/429, incomplete days, catch-up, duplicates are covered by test_v10_supervisor.py, test_v101_data_acceptance.py, test_daily_data.py (SIMULATED).
Last full run: 831 passed, 1 skipped, 1 failed (test_server_api build-id check, changed by concurrent UI edits; passes when rerun alone).
