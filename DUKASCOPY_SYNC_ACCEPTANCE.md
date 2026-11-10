# Dukascopy sync acceptance (v10.1) - 2026-11-10

Legend: REAL = observed on this machine; SIMULATED = fixture, mocked transport, virtual clock (strategy_lab/tests).
Evidence: evidence/v101/data/. Full suite: 798 passed.

| # | Item | Verdict |
|---|------|---------|
| 1 | Real scheduled-task installation | PASS (REAL), limits below |
| 2 | Backlog catch-up | PARTIAL (provider intermittently failing, retries scheduled) |
| 3 | Calendars + provenance | PASS as DERIVED_FROM_DATA; no VERIFIED_PROVIDER |
| 4 | Integrity | PASS; originals unchanged but not ACL-protected |
| 5 | Recovery scenarios | PASS (mostly SIMULATED) |
| 6 | Overall status never green while lagging | PASS |

## 1. Installation (REAL)
- Task "Dukascopy Strategy Lab Daily Sync": enabled, Interactive logon as the user, action `pythonw -m strategy_lab.data_check --scheduled --config ...daily_sync.local.json`, WorkingDirectory the repo.
- Triggers: time trigger repeating every 30 min (anchor 00:00:15 local) and At-logon. StartWhenAvailable=True (catch-up after sleep or power-off), WakeToRun=False, MultipleInstances=IgnoreNew, 2 h limit, restart 3x/5 min, allowed on battery.
- Observed runs: LastRunTime 16:30:16, 17:00:16 and 20:30:16 local, LastTaskResult 0, NumberOfMissedRuns 0, next run always 30 min later.
- Proof that runs attempt updates: `daily_sync_runs` has SCHEDULED rows every 30 min (ids 1-41 by 19:00 UTC 2026-11-10, first 2026-11-09 09:20 UTC); `intake_attempts` has per-day provider attempts (UP TO DATE / DELAYED / ERROR); supervisor kv `last_tick_at` and `last_scheduled` update on every run.
- App closed: the task does not depend on the server; the 11-09 runs and the 16:30/20:30 runs happened independent of the app. Nothing was killed or stopped.
- Double trigger (REAL): the 17:00 run reported `sync: BUSY` because the in-app worker held the lock.
- Limit: the Task Scheduler operational event log is disabled. Enabling it is a system setting, so it was not changed; LastRunTime/LastTaskResult and the SQLite rows are the evidence. Logon-trigger restart and sleep/wake were not performed for real (SIMULATED, section 5).

## 2. Backlog (REAL)
Lag = missing complete session days. Before (16:52 local) vs latest (20:31 local):

| Symbol | Before | After | Note |
|---|---|---|---|
| XAUUSD | 0 | 0 | last candle 2026-11-09 23:59 UTC |
| EURUSD | 0 | 0 | |
| GBPUSD | 3 | 0 | caught up |
| USDJPY | 14 | 6 | last 2026-11-02 |
| US30 | 18 | 22 | 6 stored days quarantined by calendar repair (section 3), re-downloading |
| US500 | 23 | 24 | 2 days quarantined |
| USTEC | 23 | 24 | 2 days quarantined |
| DE40 | 23 (calendar error) | 22 | 2026-10-14 now stored (1335 rows) under the corrected calendar; status still ERROR from a stale 2026-11-09 JForex attempt, clears at its next step |
| BTCUSD | 24 | 23 | |

- Failures seen (REAL): `HTTP 503 from Dukascopy (na 3 pogingen)` and `Dukascopy datafeed unreachable: URLError`, handled as DELAYED with stored backoff (30 min, 1 h, 2 h, 4 h, 6 h cap) and a global hold. A single probe GET returned 200, so failures are intermittent. No 429 or WAF block seen; limits never bypassed (1 request/s, honest User-Agent).
- Not claimed up to date: 6 of 9 instruments lag, overall PARTIAL. Retries continue through the scheduled task and the in-app worker.

## 3. Calendars (REAL data, 2024-10..2026-10, cache of the original CSVs)
- Method: evidence/v101/data/calendar_audit.py (derive per local weekday, standard vs daylight time, compare with config), calendar_residual.py, holiday_derive.py.
- Timezone/DST: derived local sessions are identical in standard and daylight time for every symbol (New York UTC-5/-4, Berlin UTC+1/+2 both present), so configured timezones are right. Bars outside the calendar: 0 minutes for all 9 symbols.
- Defect found and fixed: US30/US500/USTEC never trade 16:15-17:00 New York in the original data, but the calendar said 17:00 and the provider candle files carry flat zero-volume placeholders there, so 45 synthetic minutes per day were stored (verified 2026-10-14/15). Calendar now ends 16:15; non-conforming stored days were quarantined and are re-downloaded.
- Holidays (identical patterns in two consecutive years, added as overrides): 24/25/31 Dec 2026, 1 Jan 2027, Good Friday 2027-03-26, Thanksgiving 2026-11-26/27, MLK 2027-01-18, Presidents 2027-02-15 for XAU and US indices; DE40 24/25/31 Dec, 1 Jan, Good Friday; FX 1 Jan. Not covered (inconsistent between years): EURUSD 25/31 Dec; other later US holidays. Remaining mismatches in the audited past are only holiday and early-close days.
- Provenance: `calendar_source` (VERIFIED_PROVIDER / DERIVED_FROM_DATA / ASSUMED) in config, loaded into SymbolPlan, shown per symbol in supervisor output; unknown values rejected. All 9 symbols DERIVED_FROM_DATA. None is VERIFIED_PROVIDER: no Dukascopy session table was consulted.
- DE40 change of 2026-11-10 (Mon-Thu 00:00-22:15, Fri 00:00-22:00 Berlin) confirmed in both CET and CEST.

## 4. Integrity
- Paired bid+ask, UTC index, duplicates 0, negative spread 0, price scale ok for every stored symbol (supervisor quality block). SQLite integrity_check ok. One transaction per day, revisions kept with original observation time, no duplicate revision rows.
- sha256 per partition verified on read; new: corrupt_days, quarantine, re-verification (at most every 10 min) in status.
- Originals in C:\AI\Dukascopy_data: size and mtime of all 22 files equal those recorded when the cache was built (originals_check.txt). Not modified; files still carry the write permission (no read-only attribute or ACL set).
- Real repair: the 17:30 scheduled run quarantined 10 non-conforming days (45 extra placeholder minutes each); later runs re-downloaded them.

## 5. Recovery scenarios
Existing tests (test_v10_supervisor.py, test_daily_data.py) plus new test_v101_data_acceptance.py, SIMULATED unless marked:
- normal day, no new data (no request), weekend/closure, holiday override: pass.
- 503 backoff, 429 Retry-After, long outage ceiling, WAF block not bypassed: pass; REAL: 503 and URLError backoffs observed.
- sleep/wake clock jump, Windows restart catch-up: pass (simulated, not performed on the machine).
- network loss: REAL URLError; stuck worker/updater: pass; double trigger lock: pass and REAL (BUSY).
- missed days catch-up, incomplete data, provider revision, wrong point factor, negative spread: pass.
- NEW: corrupted partition shows ERROR (no crash), is never read, is quarantined and re-downloaded with original bytes kept; calendar-mismatched partition quarantined; repair skipped while a sync holds the lock; unknown calendar_source rejected; overall not green with missing days.
- Not covered: damage to the SQLite file beyond partition payload bytes.

## 6. Overall status
`overall` is UP_TO_DATE only when no instrument lags; otherwise PARTIAL, or ERROR when nothing is current or nobody works on the backlog. REAL now: PARTIAL, "3 van 9 instrumenten actueel, 6 lopen achter". Corrupt partitions are quality problems (ERROR). Scheduler/worker problems are listed in `issues`. Test: test_overall_is_never_green_while_any_day_is_missing.

## Open points
- Backlog unfinished; provider intermittently unreachable from this machine.
- DE40 status text shows a stale JForex error until its next step.
- Task Scheduler history disabled; restart/sleep not tested live.
- Code changes: daily_data.py (quarantine, live-revision reads), data_supervisor.py (repair, corrupt check), daily_sync.py and data_check.py (calendar_source, repair on scheduled/sync runs), daily_sync.local.json (calendar changes; backup evidence/v101/data/daily_sync.local.before_v101.json).
