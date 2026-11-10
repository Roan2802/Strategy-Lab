# Testresultaten

Bijgewerkt 7 november 2026. Marktprijsinvoer van de Strategy Lab backtest is uitsluitend uit `C:\AI\Dukascopy_data` gelezen. Er zijn geen live/paper trades gestart.

## Uitgevoerd

| Test | Uitkomst | Bewijs |
|---|---:|---|
| `python -m compileall -q strategy_lab` | PASS | `evidence/test_outputs/strategy_lab_compileall.txt` |
| `python -m pytest -q strategy_lab/tests` | 14 passed after UI regression fix | `evidence/test_outputs/strategy_lab_pytest_20261105_final.txt` |
| Jesse core Docker regression subset | 34 passed | `evidence/test_outputs/jesse_core_pytest_container_20261105_final.txt` |
| Jesse core tests in Jesse 3.6 Docker image | 34 passed | `evidence/test_outputs/jesse_core_pytest_container.txt` |
| Full host `python -m pytest -q` collection | FAIL before tests | `evidence/test_outputs/jesse_host_collection_failure.txt`; Jesse vendored source requires missing host dependency `arrow` |
| Jesse + PostgreSQL Compose status | Running; Postgres healthy | `evidence/test_outputs/compose_status_final.txt` |
| Strategy Lab health | PASS, local-only, Dukascopy source root | `evidence/test_outputs/strategy_lab_health.json` |
| Browser `LabBreakout` XAUUSD M1 day | PASS, 1.380 candles; 13 trades; 1.432 events | run `strategy_lab/runs/85808ac8-84eb-478e-a405-7ba41a11f56d/` |
| Replay step forward/back | PASS, 00:00→00:01→00:00; 1→2→1 candle; no future | ChatGPT-browser observation; API enforces cutoff |
| Candle inspector | PASS on selected trade/candle, condition PASS/FAIL/SKIP panel and lifecycle events displayed | ChatGPT-browser observation; run `92f1064a-15ad-45de-a28c-6d8136a8f8cd` |
| Replay chart: bullish markers | PASS, bullish entry triangle plus TP and SL exit legend on 13-trade XAUUSD M1 run | `evidence/test_outputs/replay_trade_markers_20261107.json` |
| Replay chart: bearish markers and connectors | PASS, 28 bearish triangles and 28 per-trade dotted connectors; both TP and SL markers visible | run `bc3cede8-dc2d-42bc-b762-339f45f9fe9a`; source root independently confirmed as `C:\AI\Dukascopy_data`; JSON evidence above |
| Replay cutoff / speed | PASS, Spring replay showed the completed short trade and hid later trades; 25-candle/s setting auto-advanced | ChatGPT IAB observation; JSON evidence above |
| Static UI / suite after change | PASS, all app.js element IDs present and unique; `node --check strategy_lab/static/app.js`; 74 tests passed | final run 7 November 2026 |

The Strategy Lab tests contain tiny in-memory fixtures for deterministic parser/API/ledger/replay unit conditions. These are not historical price files, were not supplied as a Jesse strategy-performance dataset, and were not mixed into the Dukascopy backtest. Performance results above use the actual Duka XAUUSD pair only.

## Quote replay efficiency follow-up — 2026-11-07

| Test | Uitkomst | Bewijs |
|---|---:|---|
| `python -m pytest strategy_lab/tests/test_events_execution_replay.py -q` | 36 passed | Lazy causal history and future-mutation RSI regression included |
| Jesse RSI precompute versus sequential indicator output | PASS; identical over 986 finite points | Jesse 3.6 Docker image, 1,000-value deterministic fixture |
| Actual Duka StatefulRsiPending Bid/Ask rerun | PASS; 1,378/1,380 decisions match, 10 modeled trades | `evidence/test_outputs/stateful_bidask_rsi_precompute_dukascopy_20261107.json` |
| `python -m compileall -q runtime/strategy_lab runtime/strategies/StatefulRsiPending` | PASS | Local source compile |

## Latest Duka dataset audit

XAUUSD Bid/Ask files from 2004-01-01 through 2026-10-15 were read-only. V2 cache records 8,244,053 timestamp rows; 2 unmatched Bid timestamps; 4,032 gaps / 3,741,513 absent minutes; no duplicates; no invalid/nonexistent local timestamps; no missing or negative Bid-volume values. Date range selected in UI, 2026-09-14, correctly returned all 1,380 M1 candles. Source SHA256 values and detailed gap examples are in `strategy_lab/data_cache/XAUUSD_M1_bidask_manifest.json` and the new run manifest.

## Latest run details

- Jesse v3.6.0 via existing local Docker `research.backtest()` API.
- `LabBreakout`, XAUUSD M1, 2026-09-14, 210 warm-up bars, cache version 2.
- 13 closed trades, all long; Jesse net P&L `-70.2517536194`; fees `67.2535536194`; initial balance `10,000`.
- These are execution-pipeline metrics under Jesse's generic spot model. They are not financial parity or strategy quality evidence.

## Dukascopy optimization UI test

- First run: one day, XAUUSD M1. Run ID `bf1c297b-53a5-4910-82b9-325e2ed3b2d7`; 45/45 trials completed; zero candidates.
- Bounded follow-up: XAUUSD M1, 2026-09-01 through 2026-09-30. Run ID `8ea3dfcd-0e65-4aac-85c7-ff532e474fd3`; 30,209 trading candles and 45/45 trials completed; Jesse returned `best_trials=[]`; UI showed 0 configurations.
- Jesse's `n_trials` is `strategy parameter count × requested trials` (9 × 5 in this probe). The UI runs end-to-end, but it did not produce parameters to reuse. This probe is recorded as **Failed for usable optimization output**, not Passed. The strategy's negative/non-usable candidate scores are consistent with Jesse discarding scores at or below its threshold; no source change was made to force a candidate.
- Both runs' manifests point only to `C:\AI\Dukascopy_data`; no other historical price data was involved.

## Jesse core suite details

Command (project bind-mounted read-only, cache directed to container `/tmp`):

```sh
pytest -p no:cacheprovider -q tests/test_backtest.py tests/test_order.py tests/test_completed_trade.py tests/test_position.py tests/test_candle_execution_cutoff.py tests/test_simulator_parity.py
```

Result: 34 passed in 1.24s. Running in the container made dependencies available without installing global Windows packages or editing Jesse source. The full upstream suite was not run; only this relevant bounded subset was.

Host full-suite attempt fails at collection because Python 3.11 host lacks `arrow`; it also tries to collect bundled Jesse MCP and all Jesse tests. We did not add packages to global Python.

## Prior audit findings (not Duka performance runs)

The initial audit had bounded synthetic-only unit/research smoke tests for Jesse candle/order semantics and two-parameter optimization. They validated the mechanics of Jesse's API and are not evidence about market performance. After the user's Duka-only correction, strategy backtests in this continuation use only Duka source. Earlier synthetic outputs remain historically labeled in `evidence/test_outputs/audit_synthetic.*`; they should not be treated as Dukascopy results or reused for strategy comparison.

## Not tested / blocked

- Jesse MCP: unavailable in this session; local Jesse research REST API was usable.
- Bid/Ask ledger: needs explicit contract size, tick size, lot step, commission and currency conversion per symbol.
- MT5 native tester parity: not run; no MT5 price history used per instruction.
- Windows full pytest: blocked by missing `arrow`; container core subset passed.
- Total offline restart (network isolation), long-history throughput benchmark, user HCC read, tick replay and simultaneous SL/TP ordering from tick data: not tested.
- Saved preset round trip, all discovered symbols, M5–D1 end-to-end backtests, and optimization candidate reuse remain untested. The optimizer did complete two bounded Duka runs but yielded no candidate to reuse.
- Plotly chart PNGs were exported to `evidence/screenshots/`. Full-app viewport captures were not saved; UI evidence was visually checked inline in the ChatGPT in-app browser. See `evidence/screenshots/README.md`.

## Final continuation check — 2026-11-06

- Strategy Lab: `python -m compileall -q strategy_lab`, `node --check strategy_lab/static/app.js`, and `python -m pytest -q strategy_lab/tests` all passed; 14 tests passed. Captured output: `evidence/test_outputs/strategy_lab_pytest_20261106_final.txt`.
- Jesse core Docker subset: 34 tests passed (backtest, order, completed trade, position, candle execution cutoff, simulator parity). No Jesse source or Docker volume was changed.
- Local health returned `status=ok`, `source_exists=true`, `mode=local-only`, and source root exactly `C:\AI\Dukascopy_data`.
- In the ChatGPT in-app browser, replay Start/Next/Previous kept the Inspector candle aligned to cursor timestamps 00:00/00:01/00:00. The API presents only matching timestamp events; it does not rerun arbitrary strategy state.
- Full-app screenshot export remains BLOCKED: the in-app browser exposed screenshot viewing but no supported project-file save path. Browser URL policy rejected data-URL navigation; no workaround was attempted. Existing Plotly PNG evidence remains available.

## Event-link regression — 2026-11-06

`python -m pytest -q strategy_lab/tests` passed 16 tests. The linker now attaches a submit event to an open/closed trade only using a real entry order ID and a unique same-symbol signal timestamp. Dukascopy run `1d35af9c-9908-4dff-943a-78ac18c45a67` proved 13/13 complete signal→open→close links, zero ambiguous links. Details: `evidence/test_outputs/dukascopy_event_link_audit_20261106.json`. Cancel hook IDs remain unavailable.

## P1 event and Inspector regression — 2026-11-06

The local strategy test suite now passes 17 tests after adding ambiguity-safe event linkage and Inspector de-duplication coverage. The browser **Spring** flow was validated on the first trade of the Dukascopy-only regression run: it selected the actual Jesse entry order, jumped to the exact signal timestamp, and rendered one submission card and one position-open card. The audit JSON reports 13/13 signal/open/close joins, zero ambiguous. Browser asset URL is `/assets/app.js?v=20261106.3`.
Latest Jesse Docker core rerun on 2026-11-06: `tests/test_backtest.py`, `test_order.py`, `test_completed_trade.py`, `test_position.py`, `test_candle_execution_cutoff.py`, and `test_simulator_parity.py` → 34 passed (0.19s). Compose emitted only the existing obsolete `version`-field warning.

P1 lifecycle follow-up: Duka run `dac05432-695b-4393-b0a0-6e43ac739c44` (XAUUSD M1, 2026-09-14) produced 7 trades. Exact Jesse order IDs and `executed_at` values reconcile 7/7 opens and 7/7 closes; 0 unmatched lifecycle timestamp reconciliations. Pending expiry IDs were cached from cancellable Jesse order objects before cancellation: 13/13 request IDs matched an `ORDER_CANCELLED` event. One additional ID-bearing cancel at the final source timestamp had no request event. See `evidence/test_outputs/dukascopy_lifecycle_audit_20261106.json`.

## P1 lifecycle regression — 2026-11-06

- Dukascopy-only regression run `dac05432-695b-4393-b0a0-6e43ac739c44`: XAUUSD M1, 2026-09-14, 7 trades and 1,449 events. Manifest source root is exactly `C:\AI\Dukascopy_data`.
- Jesse order IDs and `executed_at` reconcile 7/7 position opens and 7/7 closes. The former log timestamps were 60 seconds early; original observed timestamps remain preserved as `strategy_observed_timestamp_ms`.
- 13/13 pending-expiry cancellation IDs match request-to-cancel events. One extra ID-bearing cancellation at the final source timestamp has no corresponding request event and remains unresolved.
- Strategy Lab checks: `compileall`, `node --check`, and pytest; 18 passed. Jesse Docker core subset: 34 passed across backtest/order/completed-trade/position/candle-cutoff/simulator-parity tests.
- UI replay evidence: signal 07:36Z showed submission only; fill 07:52Z showed position open. Full-app screenshot export and cross-browser comparison remain blocked by the current browser surface.
- Run evidence: `evidence/test_outputs/dukascopy_lifecycle_audit_20261106.json`; pytest output: `evidence/test_outputs/strategy_lab_pytest_20261106_lifecycle.txt`.

## Final P0 toolbar and drag regression — 2026-11-06

- `python -m compileall -q strategy_lab` — PASS.
- `node --check strategy_lab/static/app.js` — PASS.
- `python -m pytest -q strategy_lab/tests` — 18 passed.
- Jesse Docker core subset with plugin autoload disabled — 34 passed. Disabled autoload avoids an incompatible container-level anyio pytest plugin; no source files or persistent volumes were changed.
- ChatGPT IAB pointer drag — PASS: Settings and Chart swapped visually; Cancel restored the original layout.
- Top toolbar action — PASS: Duka-only XAUUSD M1, 2026-09-14; run `c2656509-0bfe-4e62-b60d-911e5bbd2e26`; 13 trades, 1,432 events; exact input values and Duka Bid/Ask source paths recorded in its manifest.
- Local API health — PASS: `status=ok`, `mode=local-only`, source root exactly `C:\AI\Dukascopy_data`.

## Toolbar run actions and replay look-ahead regression — 2026-11-06

- Toolbar backtest `c2656509-0bfe-4e62-b60d-911e5bbd2e26`: Dukascopy-only XAUUSD M1, 2026-09-14; 13 trades, 1,432 events.
- Toolbar optimizer `24c9e4d5-eeb9-4f33-930a-75a65a29e686`: 5 trials settled; 1 valid score-0 result and 4 trial errors. UI feedback reports that split and selects the Optimization tab.
- Added a synthetic-unit look-ahead boundary test: replacing only the future candle leaves the cursor-visible candle prefix and timestamp-filtered recorded events unchanged. This validates cursor projection only; it does not establish arbitrary strategy re-execution or recomputed hidden state.
- Final commands after the UI and test changes: `python -m compileall -q strategy_lab`; `node --check strategy_lab/static/app.js`; `python -m pytest -q strategy_lab/tests` — 19 passed; Jesse Docker core subset — 34 passed. `/api/health` remains local-only with source root exactly `C:\AI\Dukascopy_data`.

## Optimizer wrapping and Bid/Ask entry regressions — 2026-11-06

- Added ten quote-side and gap cases plus account-conversion, commission, tick-value and lot-step assertions. `python -m pytest -q strategy_lab/tests/test_events_execution_replay.py` — 19 passed.
- Full Strategy Lab suite — 30 passed; `python -m compileall -q strategy_lab` and `node --check strategy_lab/static/app.js` — PASS.
- Existing Jesse Docker core subset — 34 passed with plugin autoload disabled.
- ChatGPT IAB verified optimizer result cards wrap long parameter JSON without a horizontal scrollbar. The UI optimizer run settled 5/5 with 1 valid result and 4 errors; these errors remain visible and the search space remains unconstrained.
- Health endpoint — `status=ok`, `mode=local-only`, source root `C:\AI\Dukascopy_data`.

## Simple/advanced settings regression — 2026-11-06

- `python -m pytest -q strategy_lab/tests` — 30 passed; `python -m compileall -q strategy_lab` and `node --check strategy_lab/static/app.js` — PASS; existing Jesse Docker core subset — 34 passed.
- ChatGPT IAB verified the main/advanced parameter grouping, parameter help disclosure and preservation of the advanced stop-distance value in an actual Duka-only Jesse backtest (`e3d00425-02c6-4dcd-b66f-5e9429afced0`, 13 trades, 1,432 events).
- Request and run manifest confirm XAUUSD M1, 2026-09-14, `stop_distance=2.5`, all other LabBreakout values preserved, and price-source root and Bid/Ask files exclusively under `C:\AI\Dukascopy_data`.

## Optimizer plan UI check — 2026-11-06

- `node --check strategy_lab/static/app.js`, `python -m compileall -q strategy_lab`, and `python -m pytest -q strategy_lab/tests` — PASS; 30 tests passed.
- ChatGPT IAB verified Random's continuous search-space notice and Grid's 768 parameter combinations with a correct first-5-of-768 notice. The listed 80/20 split and date range matched `runtime/strategy_lab/jesse_runner.py`; this check did not submit an optimization run.
- Existing optimizer semantics remain unchanged. Jesse Docker core tests were last run in the preceding audit pass (34 passed); no backend or Jesse strategy source changed in this UI patch.

## Lifecycle teardown reclassification — 2026-11-06

- `python evidence/test_outputs/verify_lifecycle_teardown.py` — PASS. It checked the existing Duka manifest/result pair, verified both source paths are under `C:\AI\Dukascopy_data`, compared the final candle timestamp with the additional cancel event, and confirmed Jesse's end-of-run cleanup log precedes that event.
- Outcome: 13/13 strategy expiry cancel IDs have request events; one additional ID-bearing cancellation is `EXPECTED_JESSE_BACKTEST_TEARDOWN`, not an unmatched strategy request. Machine-readable output: `evidence/test_outputs/dukascopy_lifecycle_teardown_classification_20261106.json`.
- This was read-only analysis of the existing run; no price files or MT5 data were changed and no new backtest was run.
# Continuation run — 2026-11-06

- `python -m compileall -q strategy_lab runtime\strategy_lab`: passed.
- `node --check strategy_lab\static\app.js`: passed.
- `python -m pytest -q strategy_lab\tests`: **30 passed** after the account-aware optimizer domain, saved-optimizer reload UI, app favicon and launcher path fix.
- Docker Desktop cold-stop/start via the actual hidden launcher: passed after the detected path-resolution fix. Docker Engine 29.9.2; both Compose services running; PostgreSQL healthy; Jesse HTTP 200; Strategy Lab health `ok`, `local-only`, source root `C:\AI\Dukascopy_data`.
- Duka-only optimizer Grid, 2 trials, Spot: short order mode filtered before Jesse sampling, 2/2 trials completed with negative scores and negative training P&L. Selected-trial backtest used the exact saved parameters and matching data digest.
- Duka-only optimizer TPE, 1 trial, Spot: preflight excluded short mode 2, the sampled legal mode completed with a negative score.
- Browser screenshots on emulated 1280×720, 1920×1080 and 2560×1440 viewports plus native Edge app-mode window are saved under `evidence/screenshots/`.

At the time of this historical checkpoint, these checks did not close the documented P1/P2 limits: broker-validated Bid/Ask-trigger execution, arbitrary strategy-state replay, Inspector contracts for additional instrumented strategies, memory optimization, broad date-range/resampling coverage, network-isolated offline restart, and expanded optimizer analyses. Later continuations completed the XAUUSD import memory benchmark and optimizer result views. Remaining scope is in the latest sections of `REMAINING_ISSUES.md`.


## Execution-status integrity — 2026-11-06

- Regression coverage: `python -m pytest -q strategy_lab/tests` — 31 passed; `python -m compileall -q strategy_lab runtime/strategy_lab` and `node --check strategy_lab/static/app.js` passed.
- Live API request against existing Dukascopy run `85808ac8-84eb-478e-a405-7ba41a11f56d`: Jesse repricing is `JESSE_RESEARCH_PROVISIONAL`; stored-order-intent quote replay is `DUKASCOPY_BIDASK_REPLAY`, `COMPLETE_QUOTES`, 13 trades. Evidence: `evidence/test_outputs/dukascopy_execution_statuses_20261106.json`.
- The audit API now states that strategy signals are not recomputed and user-supplied contract assumptions are not broker-verified. Existing archived run records are unchanged.

## Execution-status and Docker regression — 2026-11-06

- Strategy Lab: `python -m pytest -q strategy_lab/tests` — **31 passed**; `python -m compileall -q strategy_lab runtime/strategy_lab` and `node --check strategy_lab/static/app.js` passed.
- Jesse core: `docker run --rm --network none -e JESSE_CACHE_DIR=/tmp/jesse-test-cache -v 'C:\AI\Jesse\Jesse-Audit\jesse:/workspace:ro' -w /workspace salehmir/jesse:latest bash -lc 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -p no:cacheprovider -q tests/test_backtest.py tests/test_order.py tests/test_completed_trade.py tests/test_position.py tests/test_candle_execution_cutoff.py tests/test_simulator_parity.py'` — **34 passed**. The original source mount was read-only; only the disposable container `/tmp` cache was writable.
- ChatGPT browser: Bid/Ask UI rendered both status labels and the warning that all 7 quantities in the selected Duka run fail the entered 0.01 lot-step check. See `evidence/test_outputs/dukascopy_execution_ui_status_20261106.json`.
- Local API health: `ok`, `local-only`, `source_root=C:\AI\Dukascopy_data`.

Captured logs for this rerun are `evidence/test_outputs/strategy_lab_pytest_20261106_execution_status.txt` and `evidence/test_outputs/jesse_core_docker_20261106_execution_status.txt`.

## Hide unvalidated Bid/Ask monetary output — 2026-11-06

`strategy_lab/static/app.js` now renders quote-ledger aggregate amounts and per-trade financial fields only when the API reports `EXECUTION_VALIDATED`, `BROKER_VERIFIED`, a verified broker contract and recomputed signals. The live ChatGPT-browser regression showed `netto verborgen · model niet gevalideerd` for both models, omitted P&L fields, and displayed the 7/7 lot-step warning. Strategy Lab 31 tests, Python compileall and `node --check` pass. Evidence: `evidence/test_outputs/dukascopy_execution_ui_safety_20261106.json`.


## 8-symbol × 7-timeframe Jesse workflow matrix — 2026-11-06

Executed 56 Dukascopy-only local Jesse backtests covering XAUUSD, USDJPY, BTCUSD, DE40, US30, US500, EURUSD and GBPUSD on M1, M5, M15, M30, H1, H4 and D1 for 2026-09-14. Every successful run manifest was checked to point only under `C:\AI\Dukascopy_data`. The first sweep passed 28/56 and exposed a Jesse route-symbol formatting bug for USDJPY, BTCUSD, EURUSD and GBPUSD. After mapping their Jesse symbols to dashed pairs, all 28 affected cases passed. Run IDs, candle counts, trade/event counts, source paths and original errors are in `evidence/test_outputs/dukascopy_symbol_timeframe_matrix_20261106/matrix_final.json` and `.csv`; manifest paths were independently verified in `evidence/test_outputs/dukascopy_matrix_manifest_source_verification_20261106.json`.

This proves route/import/resample/engine completion for one day, not broad historical equivalence. Many M5–D1 runs produced zero trades/events; these are workflow checks only. Wider date windows and independent resampling fidelity remain open.


## P0 continuation final regression — 2026-11-06

- `python -m pytest -q strategy_lab/tests` — **32 passed** (`evidence/test_outputs/strategy_lab_pytest_20261106_final_continuation.txt`).
- `python -m compileall -q strategy_lab runtime\strategy_lab runtime\strategies` — PASS (`evidence/test_outputs/strategy_lab_compileall_20261106_final_continuation.txt`).
- `node --check strategy_lab\static\app.js` — PASS (`evidence/test_outputs/strategy_lab_node_check_20261106_final_continuation.txt`).
- `/api/health` — `ok`, `local-only`, source root exactly `C:\AI\Dukascopy_data` (`evidence/test_outputs/strategy_lab_health_20261106_final_continuation.json`).
- ChatGPT IAB selected `ExampleStrategy` trade 1 via **Spring** and verified the 01:00 UTC signal candle, three logged PASS checks and the `ORDER_SUBMITTED` event. Evidence: `evidence/test_outputs/dukascopy_example_strategy_inspector_ui_20261106.json`.
- Rechecked the saved 8-symbol × 7-timeframe manifest: 56/56 final cases passed (28 direct plus 28 after symbol-mapping fix); all final manifests reference Dukascopy-rooted files. One-day route coverage only.

No live or paper trades were used. Original Dukascopy and MT5 data were not changed.

## Full-month UI run and replay-range regression — 2026-11-06

- ChatGPT IAB launched an XAUUSD M1 `LabBreakout` backtest for 2026-09-01–2026-09-30. Run `6428325a-599b-4e94-a459-83d462c6b897`: 30,209 candles, 230 trades, 0 winners, 230 losers, net `-1197.5673969231207` (`-11.975673969231208%`). Result remains explicitly `JESSE_RESEARCH_PROVISIONAL` (Bid OHLC; no broker contract/financial validation).
- Manifest BID and ASK source paths are both inside `C:\AI\Dukascopy_data`; both current source-file SHA-256 hashes still match the run manifest. No original Dukascopy source was edited.
- Browser regression found saved-run selection retained the previous single-day replay bounds. Fixed run selection to load the run's context before the chart. ChatGPT IAB verification: Spring → signal cursor 2026-09-01 02:00 UTC with PASS decision and ORDER_SUBMITTED, no future fill; one M1 step → 02:01 UTC POSITION_OPEN. Evidence: `evidence/test_outputs/dukascopy_ui_fullmonth_spring_replay_fix_20261106.json`.
- `python -m pytest -q strategy_lab/tests` — 32 passed; `node --check strategy_lab\static\app.js` — PASS; `python -m compileall -q strategy_lab` — PASS.

## Editable optimizer search space — 2026-11-06

- Added per-parameter enable/fix controls, numeric bounds and steps, and category selection. API preflight rejects ranges outside declared strategy bounds; an attempted `entry_interval` lower bound of 1 returned HTTP 422 with `Optimizer bounds for entry_interval must stay inside 10–240`, without creating a run.
- Actual ChatGPT IAB run `f06fb1b8-e636-4ed2-8968-c4d3ba36e828`: Dukascopy XAUUSD M1, 2026-09-14, Spot, Random seed 42, one trial. User domain: entry interval 60–120 step 60, order mode 0, position size fixed at 0.01, remaining parameters fixed. One trial completed, zero errors; saved parameters remained within that space. Training/test net P&L remained negative (`-0.5177578460` / `-0.0415438282`); Sharpe was null and score 0. No profitability claim.
- Port 8767 process was restarted through `desktop-launcher.ps1` after identifying that the pre-existing process retained old Python code. Launcher exited 0; `/api/health` returned `ok`, `local-only`, and `C:\AI\Dukascopy_data`.
- `python -m pytest -q strategy_lab/tests` — 40 passed; `python -m compileall -q strategy_lab runtime\strategy_lab` — PASS; `node --check strategy_lab\static\app.js` — PASS; both evidence JSON files passed `python -m json.tool`.
- The first stale-process probe `f1478661-8e05-42e0-9bd4-7cc1951b670f` was marked invalid and excluded from acceptance evidence. The accepted run's manifest/result and test evidence are in `evidence/test_outputs/optimizer_user_search_space_20261106.json`.
- The dashboard favicon endpoint was repaired after the live log showed 404; launcher restart served the project icon with HTTP 200 (`image/x-icon`, 2,686 bytes), and ChatGPT IAB reported no console errors. Evidence: `evidence/test_outputs/dashboard_favicon_route_20261106.json`.

### Insufficient-balance trial classification

A bounded Duka XAUUSD M1 Spot trial with valid metadata bounds and USD 10,000 balance sampled `position_size=9.0250107552`; Jesse rejected an order requiring USD 39,180.0716. The single trial is retained as `ERROR` with the exact `InsufficientBalance` reason and sampled parameters. No trade was placed and no historical source file was modified. This demonstrates visible runtime classification; generic margin prefiltering remains unimplemented because contract units and broker margin rules are not configured. Evidence: `evidence/test_outputs/optimizer_insufficient_balance_20261106.json`.

## Configurable optimizer split browser validation — 2026-11-06

- ChatGPT IAB on current-code local server port 8768 loaded Dukascopy XAUUSD M1 for 2026-09-14 (1,380 candles).
- Bounded Random run `34cedb29-a799-4ddd-afb2-a51810027b96`: 1/1 completed, seed 42, 70% training / 30% holdout = 966/414 candles, only `entry_interval=60..120 step 60` enabled, remaining parameters fixed. Training had 8 losing trades; holdout had 0 trades, so no out-of-sample performance claim is made.
- Broad-space attempt `523a4a90-3ba4-4180-965c-f0cf392241a3` retained a trial-level `InsufficientBalance` error; it is feasibility evidence, not a valid candidate.
- UI, run manifest and `result.json` agree on the split. Source files were read only from `C:\AI\Dukascopy_data`; no live/paper trades. Evidence: `evidence/test_outputs/optimizer_train_holdout_20261106.json`.
- Regression rerun after the split implementation: `python -m pytest -q strategy_lab/tests` — **47 passed**; `python -m compileall -q strategy_lab runtime\strategy_lab`, `node --check strategy_lab\static\app.js`, and `python -m json.tool evidence\test_outputs\optimizer_train_holdout_20261106.json` — PASS.

## Latest regression — 2026-11-06

After chunked import and index reconciliation changes, `python -m pytest strategy_lab/tests -q` passed 49/49; `python -m compileall -q strategy_lab` and `node --check strategy_lab/static/app.js` also passed. The complete old/new import comparison is documented in `DATA_PERFORMANCE_REPORT.md`.


## Optimizer UI regression — 2026-11-06

Chrome browser automation exercised the actual dashboard renderer against existing local run `6b388cd5-97b2-4acb-bcb3-89f87fd63d09`; its manifest and export identify only `C:\AI\Dukascopy_data`. The five saved trials sorted numerically by descending training score. The heatmap updated its X/Y axis labels after changing both parameter selectors; the sensitivity plot showed five evaluated points with labeled axes. The UI downloaded a JSON export with run ID, dataset SHA-256, and all five trials. There were no browser console/page errors and no new backtest or optimizer run. Full machine-readable evidence: `evidence/test_outputs/optimizer_analysis_ui_20261106.json`; screenshot: `evidence/screenshots/optimizer-analysis-20261106.png`.

This browser check exposed and fixed Plotly axis titles that had been passed in an unsupported string form. It does not implement TPE sampler-state resume or generic margin preflight. Scores are training-set diagnostics only; no positive profitability conclusion is made.


## Offline-deny browser regression — 2026-11-06

Fresh Chrome context aborted all non-loopback HTTP(S) requests. Local app assets, health/data endpoints, saved XAUUSD Duka backtest/chart/trades/metrics, saved optimizer heatmap/sensitivity and layout-profile reads passed with no browser errors or remote requests. This reuses persisted Dukascopy runs; no new run was launched. A disposable Jesse image container with Docker `--network=none` imported Jesse and completed `jesse install-live --no-strict` (exit 0). These scoped checks do not establish full Compose/launcher offline restart or offline calculation. Evidence: `evidence/test_outputs/offline_browser_smoke_20261106.json` and `offline_jesse_image_network_none_20261106.txt`.


## Offline Jesse/PostgreSQL backtest and optimizer — 2026-11-06

On a disposable Docker `network=none` namespace, a temporary PostgreSQL database became ready and the Jesse server completed `jesse install-live --no-strict` and served HTTP 200. The current Strategy Lab `jesse_runner.py` and LabBreakout strategy were copied into the disposable test project; Duka-derived candle CSV copies were the only market inputs. A new September XAUUSD M1 backtest processed 30,209 candles + 210 warmup candles, generated 230 trades and exactly reproduced the existing saved metrics/trade/event counts. A new one-trial Random optimization completed 1/1 with zero errors and exactly reproduced the stored trial status, parameters and score (0). No live/paper run or source-file write occurred. Containers used tmpfs/no published ports and were removed afterward. Evidence: `evidence/test_outputs/offline_jesse_stack_20261106.json`.

This verifies offline Jesse/PG computation in a disposable environment, not the Windows desktop launcher's cold start under network denial; see `OFFLINE_TEST_RESULTS.md`.

Final Strategy Lab regression rerun after this continuation: `python -m pytest strategy_lab/tests -q` — **49 passed**. Captured output: `evidence/test_outputs/strategy_lab_pytest_20261106_offline_continuation.txt`.


## Launcher fail-closed branches — 2026-11-06

The isolated launcher harness passed missing-source and port-conflict checks. It used a temporary launcher copy, test port 18767, fake Docker CLI and temporary log paths. Both scenarios exited 1 with the expected logged error; the port listener was not killed. Production port 8767, Docker windows/services and original Dukascopy data were not changed. The native error dialog and Windows reboot remain untested. Evidence: `evidence/test_outputs/launcher_failure_scenarios_20261106.json`.


## UI icon and theme refresh — 2026-11-06

- `python -m pytest strategy_lab/tests -q`: **53 passed**.
- `node --check strategy_lab/static/app.js`: passed.
- `python -m compileall -q strategy_lab`: passed.
- PWA manifest, 192/512 PNG and ICO routes: HTTP 200; SVG-rendered desktop image: 2048×2048.
- Browser verified theme choices and green palette; dark restored. Evidence: `evidence/test_outputs/ui_icon_theme_refresh_20261106.json`.


## LabBreakout Bid/Ask signal re-execution — 2026-11-06

- `python -m pytest strategy_lab/tests -q`: **56 passed**. `node --check strategy_lab/static/app.js` and `python -m compileall -q strategy_lab` passed.
- End-to-end POST `/api/runs/{id}/bidask-ledger`: HTTP 200 in an isolated temporary run directory; only source market data was `C:\AI\Dukascopy_data`.
- Duka XAUUSD M1 2026-09-14: 210 warm-up and 1,380 trading candles; 1,379 saved decision matches, one Ask-EMA PASS→FAIL divergence, 12 generated orders and 12 Ask-side fills.
- API/UI now identify the quote-side model and display signal match counts/examples. Arbitrary strategy re-execution remains BLOCKED/NOT TESTED; test contract values are not brokerage specs. No live/paper trade. Evidence: `evidence/test_outputs/bidask_signal_reexecution_dukascopy_20261106.json`.

## Final regression after Bid/Ask replay and UI refresh — 2026-11-06

- `python -m pytest strategy_lab/tests -q`: **59 passed**.
- `node --check strategy_lab/static/app.js`: passed.
- `python -m compileall -q strategy_lab`: passed.
- Latest Bid/Ask replay evidence: `evidence/test_outputs/bidask_signal_reexecution_dukascopy_20261106.json`; icon/theme evidence: `evidence/test_outputs/ui_icon_theme_refresh_20261106.json`.
- Dukascopy source data was read-only; no live/paper trades. Generic arbitrary strategy signal replay and verified broker contract terms remain open.

## TPE resume explicit-unsupported regression — 2026-11-06

`python -m pytest -q strategy_lab/tests/test_server_api.py::test_tpe_resume_is_rejected_before_creating_a_new_run`: 1 passed. The synthetic endpoint fixture confirms HTTP 422 before request-file creation and before Jesse runner invocation. The full Strategy Lab suite then passed 60/60. Evidence: `evidence/test_outputs/tpe_resume_guard_20261106.json`. No historical market prices, backtest, or trade were used in this guard test.


## Optimizer outcome labels and preserved runtime failures — 2026-11-06

After the optimizer outcome changes, `python -m pytest -q strategy_lab/tests` passed **70/70**; `node --check strategy_lab/static/app.js` and `python -m compileall -q strategy_lab runtime/strategy_lab` passed. Four live-local research optimizations ran only on Dukascopy XAUUSD M1 for 2026-09-14 (one trial each; no live/paper sessions): Spot high-size returned `INSUFFICIENT_SPOT_BALANCE_AT_ORDER` during TRAINING with exact requested spend USD 39,112.092; Futures high-size returned `INSUFFICIENT_FUTURES_MARGIN_AT_ORDER` during TRAINING with exact requested value USD 43,458 against USD 10,000 available margin; the small Spot recovery-factor trial was retained as `COMPLETED_NEGATIVE`, score -0.9999999999959528, train net -2.7276252189768, holdout net -0.49562355451359996. A companion Sharpe-objective trial retained `COMPLETED_NEGATIVE` despite a null Sharpe score of 0. All four manifests identify only `C:\AI\Dukascopy_data`; Bid/Ask file hashes, byte lengths and mtimes still match the manifests. Evidence: `evidence/test_outputs/optimizer_outcome_classification_20261106.json`, Jesse source evidence `evidence/test_outputs/jesse_margin_model_source_20261106.json`, and browser capture `evidence/screenshots/optimizer-outcome-classification-20261106.png`.

Trial rows now label positive, negative, break-even, no-training-trade, insufficient Spot balance, insufficient Futures margin, invalid parameter combination and technical error outcomes. When the holdout phase fails, completed training metrics, exact error, phase and sampled parameters are retained. Failed and negative trials are not dropped; only COMPLETED trials enter best-trial ranking, and no-trade rows are excluded from score plots.

## Current-code regression — 2026-11-07

- `python -m pytest strategy_lab/tests -q`: **72 passed in 2.49s**.
- `python -m compileall -q strategy_lab`: passed.
- `node --check strategy_lab/static/app.js`: passed.
- Live `/api/health`: `ok`, local-only, source root exactly `C:\AI\Dukascopy_data`.
- The existing port-8767 process predates the MAE/MFE API change; live API readback does not yet include the new fields. See `evidence/test_outputs/live_app_staleness_20261107.json`.

## LabBreakout causal replay — 2026-11-07

- `python -m pytest strategy_lab/tests -q`: **74 passed**.
- `python -m compileall -q strategy_lab runtime/strategy_lab`: passed.
- `node --check strategy_lab/static/app.js`: passed.
- `/api/replay` in-process against Dukascopy XAUUSD M1 run `9adfb4a4-1b3c-4bca-b7f1-9cbb01f8f6f4`: first candle recomputation matched logged decision, reason, and EMA.
- Full causal prefix comparison: **1,380/1,380** decision/reason/EMA matches; source hashes match the approved `C:\AI\Dukascopy_data`; no future candle used. Evidence: `evidence/test_outputs/labbreakout_causal_replay_all_candles_20261107.json`.
- Arbitrary Python strategy re-execution remains untested/unsupported; stateful position and pending-order values still come from the exact recorded event.

## MT5 importer Computer Use retry — 2026-11-07

The isolated portable MT5 copy showed the importer script as loaded in Journal, but produced no stage output, marker files, custom symbol, or verifiable bars. The script compiled in the isolated copy with 0 errors and 0 warnings; that does not establish runtime import success. The Experts tab remained empty. MT5 MCP initialization against the portable terminal timed out. The disposable terminal was stopped; original MT5 and Dukascopy files remained untouched. Status: **NOT TESTED**. Evidence: `evidence/test_outputs/mt5_importer_computer_use_retry_20261107c.json`.

## Strategy Tester tabs and replay markers — 2026-11-07

Browser-use checked all eight Strategy Tester views, settings tabs, reload persistence, and the Start/Stop/Stap optimizer labels. UI-only; no run launched. Screenshots and machine evidence are linked in `P0_VALIDATION_REPORT.md`.

Replay chart visuals passed for bullish and bearish runs: directional entry triangles, distinct TP/SL exits, and a dotted connector per trade. Future paths remain hidden until the replay cursor reaches them. Exact run IDs and counts: `evidence/test_outputs/replay_trade_markers_20261107.json`.


## Final current-build regression — 2026-11-07

- `python -m pytest strategy_lab/tests -q`: **76 passed in 2.15s**.
- `node --check strategy_lab/static/app.js`: PASS.
- `python -m compileall -q strategy_lab runtime/strategy_lab`: PASS.
- `/api/health` and `/api/version`: backend current, frontend matches, local-only, source root `C:\AI\Dukascopy_data`.
- Live Duka run `c2239cb4-4ed9-4e54-a356-8121d850ebff`: 23 trades, 1,472 events, MFE/MAE present for every trade.
- Replay: bullish/bearish entry/exit markers, per-trade dotted connector, replay cursor cutoff, and 1/3/5/10/25 candles/sec auto-advance checked via browser.

Evidence: `evidence/test_outputs/current_build_validation_20261107.json`, `evidence/test_outputs/strategy_tester_e2e_20261107.json`, `evidence/test_outputs/replay_validation_20261107.json`.

## V7 generic replay and dashboard check — 2026-11-07

- `python -m pytest strategy_lab/tests -q`: **80 passed**.
- `node --check strategy_lab/static/app.js`: **PASS**.
- `python -m compileall -q strategy_lab runtime/strategy_lab runtime/strategies/ExampleStrategy runtime/strategies/StatefulRsiPending`: **PASS**.
- Health: `ok`, `local-only`, source root exactly `C:\AI\Dukascopy_data`; live backend build equals disk build.
- In Microsoft Edge Computer Use, a dashboard-triggered StatefulRsiPending XAUUSD M1 run completed (1,380 candles, 11 trades, 1,425 events). Chart markers and unlimited point selection were visible; replay auto-advanced at 25 candles/sec.
- Generic causal endpoint matched the saved decision for both StatefulRsiPending and LabBreakout. Future-OHLC mutation regression passed for 216 prefix decisions of StatefulRsiPending.
- Evidence: `GENERIC_REPLAY_VALIDATION.md`, `UI_TEST_RESULTS.md`, `evidence/test_outputs/cua_dashboard_replay_20261107.json`, and `evidence/test_outputs/generic_replay_future_mutation_20261107.json`.
- This is not full V7 completion: cross-run portfolio analytics, generic quote-side signal execution, higher-timeframe replay/mutation, broader financial validation and offline isolation remain open. MT5 importer is optional/deferred; no trades were sent.

## P0 causal H1 replay and current runtime — 2026-11-07

- Strategy Lab suite: **82 passed**; Python compileall, Jesse-container strategy compileall, and `node --check` passed. Replay chart regression passed **23 assertions**, including bullish/bearish entries, per-trade line endpoints, TP/SL identity, and hiding future exits.
- Duka-only H1 supplemental route: run `5885cf74-b83e-4070-b5c2-3327257fc90e`; H1 data route recorded; actual warmup aligned to 240 M1 candles. Causal replay decision matched at 15:00Z.
- Future mutation changed 479 rows after the replay cursor; all **901** pre-cursor traces and H1 EMA matched, zero post-cursor events were emitted, and source hash was unchanged.
- Runtime: local-only health `ok`, backend and served frontend current, Docker Jesse running and PostgreSQL healthy. Current Computer Use refresh was blocked by its target-URL policy; prior visual pass remains documented and this turn made no new visual UI claim.
- Full evidence: `evidence/test_outputs/p0_current_validation_20261107.json`, `evidence/test_outputs/generic_replay_mtf_future_mutation_20261107.json`, `GENERIC_REPLAY_VALIDATION.md`.

## H1/H4/D1 replay causality matrix — 2026-11-07

- `MultiTimeframeReplayProbe` now selects H1, H4, or D1 through a categorical parameter. Each mode ran through `/api/runs` on XAUUSD M1 for 2026-09-14 using the same Dukascopy Bid source and its own Jesse supplemental route.
- Prefix replays returned `FULL_CAUSAL_REPLAY`, matched the saved decision, emitted a non-null period-specific EMA and completed-candle state, and supplied no candles beyond the 15:00Z cursor.
- Adversarial future mutation raised OHLC by 10,000 on 479 rows after the cursor. For each timeframe, all 901 earlier decision traces and the EMA were identical; no events after the cursor were emitted; the saved input hash was unchanged.
- Run IDs: H1 `02cff6de-ef4a-4f64-a1ae-a4213e826375`; H4 `70be83f2-884e-49ac-83a6-80d5bfb90d71`; D1 `f3d00cb6-2fbb-4eb7-beda-cbb9f886ce52`. Reproduction: `python evidence/scripts/validate_mtf_future_mutation.py --run-id <run-id>`.
- Per-timeframe evidence: `evidence/test_outputs/generic_replay_mtf_h1_future_mutation_20261107.json`, `..._h4_...json`, `..._d1_...json`. Simultaneous feeds and multi-symbol replay are still untested.

## 2026-11-07 lifecycle cursor correction

- Strategy Lab suite: **85 passed**; host/container compileall and JavaScript syntax passed. Replay chart overlay regression: **23 assertions passed** for bullish/bearish triangles, same-trade dotted connectors, TP/SL markers and cursor cutoff.
- Live Duka causal replay at signal, fill, TP and expiry matched the saved decision at all four checkpoints; future candles supplied: **false**. At fill, closed trades at cursor are now 0 and the prefix-only cleanup is reported separately as 1; at TP, closed-at-cursor is 1.
- Evidence: vidence/test_outputs/stateful_replay_lifecycle_checkpoints_20261107.json and GENERIC_REPLAY_VALIDATION.md.

## Quote-side strategy index correction — 2026-11-07

The generic Bid/Ask callback now receives its strategy index relative to the first trading candle after warm-up, alongside a separate `history_index` for the causal prefix. Closed-position state uses the same relative index, preserving Jesse's schedule/cooldown semantics. A real Duka API rerun improved StatefulRsiPending decision parity from 1,378/1,380 to 1,379/1,380; one remaining difference is an Ask-side RSI threshold divergence, not hidden as a match. The 9-trade research quote model used explicit illustrative assumptions, with broker/tick parity unverified. Source Bid and Ask hashes matched the saved manifest. Regressions: 88 tests passed, and Jesse/container compile passed. See `BIDASK_EXECUTION_VALIDATION.md` and `evidence/test_outputs/stateful_bidask_quote_index_regression_20261107.json`.

## Portfolio dashboard and end-of-turn runtime check — 2026-11-07

Portfolio analysis is now implemented and tested; the earlier partial/not-implemented statements in this report are superseded. The current local service (PID 15800) reports health `ok`, `local-only`, backend current, served frontend current, 0 active runs, and source root exactly `C:\AI\Dukascopy_data`. Docker Jesse is running and PostgreSQL healthy. Full Strategy Lab regression: 93 passed; `node --check strategy_lab/static/app.js`, 23 replay overlay assertions, and Python compileall passed.

Computer Use opened Results → Portfolio, selected two saved Duka StatefulRsiPending runs, and visibly rendered aggregate equity, drawdown, strategy/instrument/month and correlation tables. Compatible saved LabBreakout runs also passed the live API calculation. An incompatible mixed signal-mode request returned `COMPATIBILITY_BLOCKED` and no combined net P&L. See `PORTFOLIO_ANALYSIS_STATUS.md` and the three `evidence/test_outputs/portfolio_analysis_*_20261107.json` files.

The audit deliverables are reconciled for all locally executable P0 scope. Remaining isolated items are explicitly blocked/limited: Jesse MCP connector unavailable; quote-side adapters for strategies that do not implement the explicit callback contract (unsupported by design; only MCP-only work is blocked); broker-verified MT5 CFD parity (cannot be established from OHLC alone without broker contract/tick ground truth); production-wide offline reboot/restart (deferred; not required for this development round); and OPTIONAL / DEFERRED MT5 importer (isolated terminal failed to initialize even a minimal script; per scope, no further attempts). No live/paper trades were executed and the original Dukascopy and MT5 data were not changed.

## V8.1 — local data and forward registration

`python -m pytest -q strategy_lab/tests`: **100 passed**. Seven new fixture tests cover partition idempotency, as-of revisions, incomplete and closed days, rollback after a forced failure, combined historical reads, checksum failure, ordered catch-up, DST and forward registration eligibility. The CLI help command also exits successfully. These results do not verify a real provider download, an installed Windows schedule or an order-processing forward engine. See `DAILY_DUKASCOPY_PIPELINE.md` and `FORWARD_ENGINE_VALIDATION.md`.

The subsequent V8.1 suite reaches **103 passed**. The three added forward tests cover the supported LabBreakout daily engine and its local API. They check shared risk, restoration of pending and open orders, atomic rollback, duplicate processing and exclusion of old quotes from a new enrollment. Actual provider delivery and Windows scheduling are separate gates.

## V8.1 — scheduler, monitoring and forward performance

- `python -m pytest -q strategy_lab/tests` passed all **107** tests. `node --check strategy_lab/static/app.js` passed.
- A temporary Windows Task Scheduler task ran the worker under the interactive Windows user. The isolated SQLite database contains one `SCHEDULED` run with status `COMPLETE` and one Bid/Ask partition with one paired minute. The production task remains unregistered.
- Windows System event log query succeeded locally. The in-app browser opened the current production Datamonitor, which reported missing configuration and disabled manual synchronization.
- An isolated browser instance showed three forward equity observations, a realized loss of 1.42 USD, an undefined ratio for a strategy without trades and a warning for rejected orders. The API tests verify the same fixture ledger before and after engine restart, including open risk, exposure and drawdown.
- Automatic JForex provider delivery, production enrollment over subsequent real dates, MAE/MFE and backtest-to-forward comparison remain open.

## V8.1 — forward trade excursions

- `python -m pytest -q strategy_lab/tests` passed **109** tests. JavaScript syntax and the replay overlay/window test passed.
- Verified long market fills with the Bid range, short stop fills with the Ask range, exclusion of ambiguous fill and exit bars, same-bar unavailability, and unchanged excursion results after a provider revision. A simulated checksum failure removed only the affected excursion figures.
- The isolated browser displayed MAE 0.41 USD, MFE 0.19 USD and coverage 1/1, consistent with the strategy and closed-trade rows.
- Production JForex delivery, accumulation of prospective production results and comparison with a historical backtest remain open.

## V8.1 — historical forward reference

- `python -m pytest -q strategy_lab/tests` passed **110** tests. JavaScript syntax and replay overlay/window tests passed.
- A deterministic test reproduced the frozen model’s fill, exit and net P&L, reported a day without quotes, refused source-hash mismatches and overlapping dates, and returned the same persisted result on repeat. Forward events, days and the quote watermark remained unchanged.
- The in-app browser calculated and displayed a historical reference for XAUUSD M1 on 2026-09-14 in an isolated database. A second API run with an illustrative 20× leverage setting processed 1,380 real Dukascopy candle pairs, recorded 326 simulated closed trades and left the test portfolio at zero forward days and events. Sixty UTC minutes had no recorded quotes.
- Production daily delivery, accumulation of prospective results, training comparison and OOS validation remain open.



## V8 — 2026-11-09 (Claude-sessie)

- `python -m pytest -q strategy_lab/tests`: **322 passed** (was 110). Nieuw: rule engine/backtest/codegen (58), validatie (32), discovery (26), research-infra (18), forward rules (16), datafeed (37 incl. inbox), plugins (5), scale-in (2), bibliotheek (8).
- JS: `node --check` app.js en shell.js; replay-overlay regressie geslaagd.
- Echte Dukascopy-runs: builderbacktest XAUUSD M15 (5 mnd, 1,2 s); validatie EmaRsiPullback 2025-06→2026-10 (oordeel REJECT, 8 s); discovery 6 hypotheses XAUUSD+EURUSD 2004–2024 (alle IS-verliesgevend, bewaard); GoldReaper-plugin aug-2026 (34 trades, 2 s/maand) en validatie apr–okt 2026 (REJECT: PSR/DSR onvoldoende op ~5 maanden).
- Browser: alle 10 schermen zonder horizontale overloop op 1366×768 en 1920×1000 (donker); rondleiding doorlopen (UI-agent).
- Niet live bewezen: datafeed-download van 2026-11-09 middag (host 503/time-out); JForex-login (geen credentials, bewust niet nodig).

## V9.1 (2026-11-10)
- pytest: 388 passed (`python -m pytest strategy_lab/tests -q`).
- Real-data validations (API): evidence/v9/validation/*.json — Builder rolling+expanding REJECT, LabBreakout REJECT, GoldReaper (then FORWARD_ELIGIBLE, before provenance checks).
- Real-browser validations: evidence/v9/browser/proof-chrome.json — Builder REJECT, LabBreakout REJECT, GoldReaper OOS_PASSED (provenance-capped); 0 JS errors.
- Real-browser cancel of a Jesse backtest: evidence/v9/browser/cancel.json — stopped in 6.2 s.
- Forward consistency: FORWARD_CONSISTENCY_V9.md (PARTIAL; 36 tests incl. 7 on real data).
- V8 UX regression: Chrome 1600x900 19/19, Edge 1920x1080 19/19, 0 JS errors (evidence/v9/e2e/).

## V10 (2026-11-10)
- pytest: 686 passed.
- Browser-E2E (ux_e2e.py, nieuwe structuur): Chrome 1600x900 49/49, Edge 1920x1080 49/49, quick 27/27 op Chrome 1280x720/1600x900/2560x1440 en Edge 1920x1080, dark+light, 0 JS-fouten.
- Benchmark anti-overfitting: evidence/v10/stats (ANTI_OVERFITTING_VALIDATION.md).
- Echte fabrieksrun: fr_20261110_141731_16a4ea, 0 kandidaten na L5.
- Multi-market echte run: evidence/v10/multimarket/real_run.json.
- Data-supervisor scenario's: DUKASCOPY_MONITOR_VALIDATION.md.

## V10.1 (2026-11-10)
- pytest 802 geslaagd; browser-E2E Chrome 1600x900 53/53 en Edge 1920x1080 53/53 (volledig), snel 31/31 op Chrome 1280x720/1600x900/2560x1440 en Edge 1920x1080, dark+light, 0 JS-fouten (evidence/v101/ui, evidence/v101/pytest_final.log).
- Echte fabrieksrun fr_20261110_193539_c1af37: 370 -> 0 na L5.
