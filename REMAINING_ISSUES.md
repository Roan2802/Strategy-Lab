# Remaining issues and precise status

## Blocked

- **Jesse MCP-specific calls:** No Jesse MCP tool is exposed in this session. Local Jesse Docker/research API and the local dashboard were exercised, but no MCP behavior is claimed. Retest when the connector is available.
- **MT5 parity comparison:** Price inputs are exclusively from `C:\AI\Dukascopy_data`; MT5 historical price data is excluded. Exact MT5 CFD execution/accounting parity cannot be compared under that constraint. No live/paper trading was used.
- **Generic strategy signal re-execution against Bid/Ask — PARTIAL:** `LabBreakout` and `StatefulRsiPending` have explicit adapters; other Python strategies without the callback remain on saved-order replay and are not quote-side signal-tested. Jesse MCP-specific calls remain unavailable.

## Tested but still limited

- **Bid/Ask accounting and LabBreakout signals:** A Duka XAUUSD M1 run recalculated `LabBreakout` signals with LONG on Ask OHLC/EMA and SHORT on Bid OHLC/EMA, then replayed quote-side fills. On 2026-09-14, 1,379/1,380 saved decisions matched, one Ask-EMA long signal changed PASS→FAIL, and the model emitted 12 orders/fills. The API passed end-to-end using a temporary run copy. Contract values were QA-only, not broker specifications; results do not establish exact broker/tick parity. Evidence: `evidence/test_outputs/bidask_signal_reexecution_dukascopy_20261106.json`.
- **OHLC execution order:** M1 bars have no tick ordering. The independent ledger resolves a same-bar SL/TP tie with SL first and marks ambiguous cases.
- **Optimizer:** Random/grid/TPE execution, parameter persistence, score/error recording, seed reproducibility, validation, and random resume passed on Duka input. Trial scores remained negative; no profitable candidate, walk-forward result, or holdout claim exists. Portfolio aggregation is now implemented under compatibility gates; see PORTFOLIO_ANALYSIS_STATUS.md. TPE resume is unsupported because sampler state is not checkpointed; a new regression confirms the API rejects resume before runner invocation. Multiworker operation is rejected because Jesse's state is process-global.
- **Optimizer UI (partial):** editable per-parameter domains/fixes, account-aware categories, method semantics, trial count, objective, seed, dates, configurable 50–90% candle-order training split, sortable results, heatmap, sensitivity plot and JSON export are verified. Run `34cedb29-a799-4ddd-afb2-a51810027b96` confirmed the 70/30 split (966/414 candles); `optimizer_analysis_ui_20261106.json` verifies the result views. Generic margin feasibility and TPE sampler-state resume remain open; infeasible samples remain retained as explicit `InsufficientBalance` errors.
- **Generic Inspector replay:** The causal prefix runner recomputes instrumented `LabBreakout`, `StatefulRsiPending`, and H1/H4/D1 supplemental-feed probes. StatefulRsiPending passed a 216-decision future-OHLC mutation; each M1→H1/H4/D1 probe passed 901 prefix decisions after mutation of 479 future rows. Computer Use previously verified the Inspector replay button on a matched `SKIP` decision (436-candle prefix). Simultaneous supplemental feeds, multi-symbol routes, and exhaustive Stateful prefix parity remain NOT TESTED. See `GENERIC_REPLAY_VALIDATION.md`.
- **Lifecycle joins:** The exact-ID resolver links an ID-less `ORDER_SUBMITTED` through the real Jesse entry order ID and only one same-symbol submission at the recorded signal time. The latest Duka run proves 7/7 signal→open→close joins and exact order execution times. Pending expiry caches cancellable order IDs before Jesse removes them; 13/13 expiry requests matched a cancellation event. One additional ID-bearing cancellation has no strategy request, but it is classified as Jesse's end-of-run cleanup after the final candle by `evidence/test_outputs/verify_lifecycle_teardown.py`; evidence is in `evidence/test_outputs/dukascopy_lifecycle_teardown_classification_20261106.json`. `POSITION_OPEN.trade_id` remains absent, but its actual entry order ID joins it to the closed trade. Full arbitrary-strategy lifecycle guarantees are not claimed.
- **Dukascopy data:** USATECHIDXUSD includes 2,670 Ask-only timestamps with incomplete quotes/volume. Other listed unmatched pairs include a small number of Bid-only XAUUSD, BTCUSD, and DE40 timestamps. The analyzer reports these; the execution ledger skips incomplete rows.
- **Offline restart — partial pass:** A fresh, disposable Jesse + PostgreSQL stack on Docker `network=none` completed a Duka-only backtest and one-trial optimization with exact baseline reproduction; browser UI passed with all remote requests blocked. A disposable Windows launcher/Compose copy also opened the hidden local server and Edge app window while Jesse had no network egress; that test stack is stopped. Production Compose volume, host-wide network denial and Windows reboot remain **NOT TESTED**. Evidence: `OFFLINE_TEST_RESULTS.md`.

## Explicitly out of scope

No live or paper trading; no writes to original Dukascopy files or MT5 data; no use of non-Dukascopy market prices. Pure unit tests may use small constructed fixtures to validate code paths; all chart/backtest/optimizer/execution market-price runs documented here use the Dukascopy directory.

## UI-redesign follow-up (2026-11-05)

- **PARTLY VERIFIED — browsers:** installed Chrome 157 and Edge 156 passed a matched headless UI matrix at 1280×720, 1920×1080 and 2560×1440 with no horizontal overflow or browser errors. Windows display scaling and native rendering at alternate scale remain unverified. The Edge PWA taskbar pin and app icon were confirmed in the live Windows session; details are in `DESKTOP_LAUNCHER_REPORT.md`.
- **PARTIAL — full-app screenshot package:** dashboard, viewport, edit mode, custom layout, advanced settings, replay and optimizer images exist in `evidence/screenshots`. The Inspector now also has an exact selected-trade signal-candle capture with five PASS checks and the linked order submission; see `evidence/screenshots/trade-inspector-signal-candle-20261106.png` and `evidence/test_outputs/trade_inspector_selected_signal_20261106.json`. Alternate native scaling and the broader screenshot matrix remain incomplete.
- **Partial — chart zones/strategy overlays:** entry/exit, SL/TP and the parameterized EMA are shown from available events/metadata. Arbitrary price zones and indicators are not inferred when a strategy does not emit them.
- **Verified — vertical resizing:** results panel height was pointer-resized from 360 to 292 px and persisted. The chart splitter was tested in ChatGPT IAB: pointer 540→592 px, keyboard +12 px, maximum clamp 1200 px; Plotly reflowed and no horizontal overflow appeared. The installed Chrome/Edge headless matrix also passed at three desktop viewports.
- **Verified — simple settings mode:** starts collapsed by default; engine, optimizer, and strategy-code controls are in advanced disclosure groups. Mode and open-group preferences persist in the current browser profile. The Duka regression manifest retained all parameter values after the UI change.
- **Untested — zoom/fullscreen native mode:** the custom chart-focus overlay and Escape behavior passed in the ChatGPT browser; browser zoom, Windows scaling and native browser fullscreen were not compared.
- **Port migration:** 8767 is the documented/default port and the only Strategy Lab listener observed in the latest check. Ports 8765 and 8766 had no listener. The launcher will not kill an unrelated process that occupies 8767.


- **Resolved — optimizer account categories:** LabBreakout Spot mode filters out short `order_mode=2`; the filter and excluded option are recorded in each optimization. User-defined numeric bounds/steps, per-parameter fixes and category subsets are implemented and verified through ChatGPT IAB and Jesse. See `OPTIMIZER_DIAGNOSTICS.md` and `evidence/test_outputs/optimizer_user_search_space_20261106.json`.
- **Still open — generic margin feasibility:** no broker/contract metadata defines a safe static affordability limit across symbols and equity states, so insufficient balance can still surface as an explicit failed trial. Do not infer a general margin limit from LabBreakout's `position_size` alone.
- **Runtime error behavior verified:** a current-code Duka XAUUSD Spot trial whose required notional exceeded the starting balance was retained with its exact `InsufficientBalance` error and sampled parameters; see `evidence/test_outputs/optimizer_insufficient_balance_20261106.json`.

## UI continuation — 2026-11-06

- **Verified — replay cursor and Inspector:** Start, Next and Previous aligned the Inspector to 00:00, 00:01 and 00:00 respectively; only exact-timestamp logged events were shown. This does not re-execute arbitrary strategy code at each cursor.
- **Partial — full-app screenshot files:** actual native Edge app-mode window, dashboard viewport, Edit Mode, custom layout, advanced settings, replay and optimizer screenshots are now saved under `evidence/screenshots/`. The Inspector trade-jump interaction was verified later (see `TRADE_INSPECTOR_TESTS.md`), but a dedicated screenshot file with the active trade was not captured in this pass.
- **Verified — final checks:** latest Strategy Lab suite 32/32 passed after the validation continuation; Python compile and JS syntax checks passed. Jesse core Docker subset was 34/34 in the earlier audit. Local health source remains exactly `C:\AI\Dukascopy_data`.

## P1 lifecycle and Inspector fixes — 2026-11-06

The API no longer duplicates Inspector events matching both candle and signal timestamps, and it excludes future fills from the signal candle. The trade table's **Spring** action uses the actual entry order ID to find its signal timestamp. Later follow-up captured expiry IDs before Jesse removes orders and reconciled open/close times to Jesse's exact executed order times. Current evidence: `evidence/test_outputs/dukascopy_lifecycle_audit_20261106.json`.

## P1 lifecycle regression — 2026-11-06

Duka-only XAUUSD M1 run `dac05432-695b-4393-b0a0-6e43ac739c44`: 7/7 position opens and 7/7 closes match Jesse order execution timestamps; 13/13 pending-expiry cancellation IDs match cancellation events. At that time, one extra end-of-data cancellation had not been classified. Signal-to-fill replay visibility was verified in ChatGPT IAB. Evidence: `evidence/test_outputs/dukascopy_lifecycle_audit_20261106.json`.

## Lifecycle teardown classification — 2026-11-06

The extra cancellation is now explained: the last `CANDLE_DECISION` leaves order `d7b86418-98f3-4002-a3c2-33b078f9983a` pending at the final Dukascopy candle. Jesse then logs `cancel all remaining orders ... to prepare for a fresh start`, followed immediately by its `ORDER_CANCELLED` event at the same candle timestamp. The ID is not in any `ORDER_CANCEL_REQUEST`, which is expected for engine teardown. The reproducible verifier confirms 13 requested expiry IDs, 14 ID-bearing cancellation events, and classifies the one additional ID as `EXPECTED_JESSE_BACKTEST_TEARDOWN`. It reads only the existing Duka run manifest/result; source root and both price-file paths are verified as `C:\AI\Dukascopy_data`. No market files are modified.

## 2026-11-06 browser regression

The ChatGPT in-app browser verified the new 761–1250 px responsive layout and saved `Lab widescreen` profile at approximately 888 px and 1265 px. A second IAB tab loaded the shared profile after reload. Pointer drag and arrow controls passed. At that date, Chrome/Edge comparison and screenshot file export were still open; the 2026-11-06 continuation below supersedes the screenshot status. Regression commands at the time: Strategy Lab (18 passed); Jesse core subset (34 passed). Health source root: `C:\AI\Dukascopy_data` only.

## Current audit status correction — 2026-11-06

The earlier UI note that CUA drag-and-drop was unverified is superseded by the live pointer-drag regression in `UI_TEST_RESULTS.md`: Settings→Chart reorder passed in ChatGPT IAB and Cancel restored the original layout. The later ~903 px toolbar test also passed without horizontal page overflow. Current browser/screenshot status is recorded above; technical limits remain open.

The earlier Windows Snipping Tool limitation is superseded by browser/CDP screenshots saved directly to project files and a separate native Windows app-window capture. No screenshot was fabricated from chart-only exports.

## Desktop launcher cold-start follow-up — 2026-11-06

The Docker Desktop cold-start found and fixed a one-candidate path-resolution bug. The retest started Docker Engine 29.9.2, waited for healthy PostgreSQL and Jesse HTTP 200, then opened Edge app mode; see `DESKTOP_LAUNCHER_REPORT.md` and `STARTUP_TEST_RESULTS.md`. Shortcut cold-start recovery, rapid duplicate invocation, dead Python-server recovery, and already-running reuse are verified. Windows reboot, forced port collision, and unavailable Dukascopy-folder scenarios remain untested. Docker was left running after the test.

## Remaining P1/P2 limits

Spread-trigger replay and arbitrary-strategy state re-execution remain incomplete. The isolated Jesse/PostgreSQL offline computation and optimizer analysis views have since passed; only the actual Windows launcher/production Compose cold restart under network denial remains untested. The 8-symbol × 7-timeframe local route matrix and a second instrumented Inspector strategy are now verified for one Dukascopy day; neither establishes broad date-range or arbitrary-strategy parity. Do not interpret a local cold start as an offline restart.

### Offline launcher test refinement — 2026-11-06

This initial note is superseded by the corrected acceptance below: a disposable launcher/Compose copy subsequently opened and focused the expected Edge app window while Jesse egress was blocked. Only the `dsltest-dd1c74ae` containers/network and copied server were stopped; no volumes were removed and production port 8767 remained live. Production offline restart, host-wide network denial, and reboot remain open. See `evidence/test_outputs/offline_windows_launcher_acceptance_20261106.json`.

## Parameter simplification follow-up — 2026-11-06

- **Verified complete for LabBreakout:** four primary fields and five expandable additional fields; current advanced values and units are summarized and preserved in the Jesse payload. Browser test and Duka-only run are recorded in `UI_TEST_RESULTS.md`.
- **Still open at this point in the historical note:** full-dashboard screenshot package and Chrome/Edge compatibility comparison. These were later addressed by the browser compatibility matrix and screenshot capture documented below. Arbitrary-strategy replay state, spread-dependent trigger re-execution and offline restart remain open; the XAUUSD peak-memory benchmark is now complete (see `DATA_PERFORMANCE_REPORT.md`).

## Optimizer search space — 2026-11-06

Editable search domains now work end to end. The UI preflight prevents account-incompatible Spot short mode and makes fixed parameter values explicit. API preflight rejects out-of-metadata bounds, malformed integer ranges, empty categories, and incompatible fixed categories before loading candles. ChatGPT IAB Duka-only one-trial run `f06fb1b8-e636-4ed2-8968-c4d3ba36e828` preserved the selected bounds/fixed values. Its research returns were negative; no profitability claim. Initial probe against stale API code was invalidated. The existing API was restarted through the desktop launcher and healthy on port 8767 afterwards.

## Execution status labeling — 2026-11-06

The current Strategy Lab API separates provisional Jesse Bid-OHLC results from the Dukascopy Bid/Ask replay of persisted intents. That replay is not signal-recomputed and uses user-supplied, broker-unverified contract settings. Historical run artifacts created with earlier status labels remain archived; new endpoint responses carry the corrected scope. Regression suite passes 31 tests. Evidence is in `evidence/test_outputs/dukascopy_execution_statuses_20261106.json`.

## Contract-volume warning confirmed — 2026-11-06

The dashboard Bid/Ask overlay was exercised on Dukascopy-only run `40a6fd7e-2cf0-4853-94a9-5b7bce633ceb`. With an illustrative volume step of 0.01, all 7 Jesse trade quantities were rejected by the step check. The page surfaces the warning and marks both output models provisional; its computed monetary values must not be treated as valid CFD accounting. Evidence: `evidence/test_outputs/dukascopy_execution_ui_status_20261106.json`.

## Bid/Ask output gate — 2026-11-06

Unvalidated model money is hidden in the Bid/Ask overlay. The gate requires `EXECUTION_VALIDATED`, broker-verified contract specifications and recomputed strategy signals. A live UI check also confirmed that all 7 invalid 0.01-lot-step quantities yield no aggregate or per-trade P&L display. Evidence: `evidence/test_outputs/dukascopy_execution_ui_safety_20261106.json`.

## Chunked Dukascopy parsing — 2026-11-06

`DataManager._read_side` now parses 250,000-row chunks and carries DST fold decisions across chunk boundaries. Full `ensure()` old/new comparison on the same source yielded exact cache equality for all 8,244,053 joined XAUUSD rows and identical quality counts. Runtime fell from 50.04 s to 28.20 s (43.6%); peak RSS fell from 5.42 GB to 2.82 GB (47.9%). No duplicates or invalid times were dropped; 2 unmatched Bid timestamps and 0 unmatched Ask timestamps were counted. Source CSVs remained read-only. Reproduction, hashes, and all six process measurements: `evidence/test_outputs/benchmark_chunked_dukascopy_parse_20261106.py` and `evidence/test_outputs/chunked_dukascopy_parse_20261106.json`. The full Strategy Lab suite passes 49 tests.

## Status supersession — 2026-11-06

The 6-Nov full-import benchmark below supersedes earlier statements in this report that first-import peak memory remained unmeasured. XAUUSD full import is verified at 28.202 s / 2.82 GB RSS with exact cache equality. Other symbols' full first-import resource peaks remain unmeasured. The Edge PWA taskbar pin is confirmed by its AppsFolder `Van taakbalk losmaken` verb; the refreshed project icon is used by the desktop shortcut and PWA manifest.


## Optimizer analysis view — regression correction — 2026-11-06

Sortable trial results, evaluated-pair heatmap, parameter-sensitivity chart and reproducible JSON run export are implemented and verified in Chrome against existing Dukascopy run `6b388cd5-97b2-4acb-bcb3-89f87fd63d09`. These items are no longer open. The run's 5 training scores are negative; the heatmap does not invent/interpolate missing combinations. Remaining optimizer limits are generic margin feasibility and TPE sampler-state resume. Evidence: `evidence/test_outputs/optimizer_analysis_ui_20261106.json`.


## Launcher error-path test supersession — 2026-11-06

The source-folder-missing and port-conflict branches are now **PASS in an isolated launcher-copy harness** on test port 18767 with a fake Docker CLI. The Duka source and production port 8767 were untouched. The actual production-port collision with an unrelated service, native dialog rendering and Windows reboot remain untested. See `STARTUP_TEST_RESULTS.md` and `evidence/test_outputs/launcher_failure_scenarios_20261106.json`.

## Final high-value replay and launcher checks — 2026-11-06

- Hidden launcher target reuse passed via `wscript.exe` → `launch-hidden.vbs`; this does not prove an Explorer double-click or Windows reboot. See `evidence/test_outputs/launcher_reuse_20261106.json`.
- Live Duka replay at XAUUSD M1, 2026-09-14 09:33 UTC returned 574 candles and 594 stored events through the cursor, with no future candles. The endpoint only clips stored candles/events; it does not rerun LabBreakout or reconstruct indicator/strategy state. Generic causal state replay remains **MISSING**. See `evidence/test_outputs/dukascopy_replay_cutoff_20261106.json`.
- The custom high-resolution shortcut/PWA/taskbar icon and hover-opened theme palette are implemented and covered by `evidence/test_outputs/browser_compatibility_matrix_20261106.json`; no new UI code was needed in this follow-up.


## Optimizer outcomes and safe margin preflight — 2026-11-06 continuation

Trial outcomes are now explicit in the persisted result and UI: `COMPLETED_POSITIVE`, `COMPLETED_NEGATIVE`, `COMPLETED_BREAKEVEN`, `NO_TRAINING_TRADES`, `INSUFFICIENT_SPOT_BALANCE_AT_ORDER`, `INSUFFICIENT_FUTURES_MARGIN_AT_ORDER`, `INVALID_PARAMETER_COMBINATION`, and `TECHNICAL_ERROR`. Failures preserve exact message, TRAINING/HOLDOUT phase, candidate parameters and any completed split metrics. Current-code Spot and Futures affordability errors plus a negative Spot recovery-factor trial passed in live-local, Duka-only Jesse research runs; full Strategy Lab suite is 70/70. Evidence: `evidence/test_outputs/optimizer_outcome_classification_20261106.json`.

**Still open:** a general historical margin/affordability preflight. Jesse's Spot cost and Futures margin formulas were verified in source and their runtime failures are now distinguished. However `position_size` is a raw Jesse quantity, broker contract sizes are not verified, and available margin changes with the sequence of historical positions and P&L. A starting-balance-only cap risks filtering a valid later entry, so only declared account-incompatible categories are prefiltered. TPE sampler state resume remains explicitly unsupported. No Live/Paper trading occurred and original Dukascopy CSVs remained unchanged during the new runs.

## UI follow-up limits — 2026-11-06

The requested clean header, custom light-green palette, MT5-style Market/Strategy tabs, split Entry Inspector views, replay speed/auto-advance, selectable metric cards, and full selected-range chart option are implemented and browser-checked. Remaining dashboard metrics from the supplied list that require new data products or assumptions are not calculated: out-of-sample/Walk-Forward results for a standalone backtest, Monte Carlo percentiles, risk-of-ruin, CVaR/Expected Shortfall, parameter stability and regime/portfolio statistics. Trade MAE/MFE is now available as raw price excursion for runs with saved Dukascopy candles. They must remain unavailable until the engine stores the required series and assumptions.

The MT5 custom-symbol importer source and Duka CSV copy are validated, but importer execution remains NOT TESTED. The configured MT5 terminal is minimized and the MT5 MCP exposes no script-execution function. A portable copy was opened through Computer Use without an account connection, but the screenshot surface became inconsistent before the import result could be checked. During navigation, the uninstaller was inadvertently opened in the disposable copy; missing program files were restored from the existing local Program Files installation. No original installation, MT5 history, or Dukascopy source file was modified. See `evidence/test_outputs/mt5_importer_computer_use_attempt_20261106.json`.

## MT5 importer compile and inventory check — 2026-11-06

MT5 MCP initialized the explicitly configured `C:\AI\MT5_ICMarkets_Global\terminal64.exe`. The importer compiled to an `.ex5` (`compile_mql5` returned success and the output exists), but the compiler log text was empty, so this is not recorded as a clean diagnostic-log compile. A read-only `get_symbols` call confirmed `P0Duka_XAUUSD_2026` does not exist yet. The configured IC Markets terminal is minimized; no script-execution operation is exposed by MT5 MCP, and the visible terminal from `C:\Program Files` was left untouched. Importer execution remains NOT TESTED. No Strategy Tester run, order, or original-history change occurred. See `evidence/test_outputs/mt5_import_compile_inventory_20261106.json`.


## Trade-overlap status update — 2026-11-06

Per-run concurrent open positions are now charted from stored open/close timestamps and can be toggled in Analyse. A Duka-only run and an overlap fixture passed browser verification. Portfolio-level trade overlap/correlation across runs remains missing; this chart does not substitute for that calculation.

- **Verified — unlimited chart range:** selecting `Onbeperkt` sends limit 0 and the live API returned all 30,209 XAUUSD M1 candles for September 2026 (no 8,000-point cap). Evidence: `evidence/test_outputs/chart_unlimited_live_api_20261106.json`; no source data was modified.

- **Launcher PostgreSQL failure:** tested in an isolated copy with an unhealthy Postgres shim; launcher returned a logged error and exited within the reduced 5-second test timeout. Evidence: `evidence/test_outputs/launcher_postgres_failure_20261106.json`.
- **Still open — MT5 importer execution:** the Computer Use surface mapped the portable terminal window to the unrelated DrainSim game twice. No UI input was issued after mismatch. See `evidence/test_outputs/mt5_importer_computer_use_retry_20261106.json`.

- **MT5 importer retry (2026-11-07):** Computer Use again mapped the portable MT5 process to the original IC Markets account window. No UI action was taken and the importer was not run. Evidence: `evidence/test_outputs/mt5_importer_computer_use_retry_20261107.json`.

- **MT5 importer command-line test (2026-11-07):** start config loaded the script but did not produce completion or custom-symbol evidence after 120 s. Only isolated copies were stopped; original MT5 remained untouched. Importer remains NOT TESTED. Evidence: `evidence/test_outputs/mt5_importer_config_run_20261107.json`.

- **Docker unavailable branch:** isolated launcher-copy test passed (clear error in 0.52 s; no real Docker/Desktop or port 8767 touched). Evidence: `evidence/test_outputs/launcher_docker_unavailable_20261107.json`.
- **Fresh app regressions:** 71 Strategy Lab tests passed; compileall, JS syntax and live health check passed with only the approved Dukascopy source root. Evidence: `evidence/test_outputs/current_regression_20261107.json`.

- **Jesse Compose start failure:** isolated Docker-shim test passed; launcher logged the compose start failure and exited 1. Production services and port 8767 were untouched. Evidence: `evidence/test_outputs/launcher_jesse_start_failure_20261107.json`.
- **MT5 custom-symbol CSV importer — NOT TESTED:** The isolated portable terminal starts, but the user-authorized Computer Use retry mapped the selected MT5 window state to File Explorer; no importer UI action occurred. The config-based attempts logged the script as loaded but emitted no completion marker or custom symbol. Evidence: `evidence/test_outputs/mt5_importer_computer_use_retry_20261107b.json` and `evidence/test_outputs/mt5_importer_config_run_20261107.json`.

## Trade MAE/MFE from saved Dukascopy candles — 2026-11-07

The Trades tab now shows per-trade MFE and MAE in raw price units and provides hover explanations. Values use only the run's saved candle copy when its manifest source root resolves exactly to `C:\AI\Dukascopy_data`. A market entry recorded exactly at a candle open may include that candle; the exit candle and pending-fill candle are excluded because OHLC cannot resolve their intrabar sequence. This is a descriptive Bid-price excursion, not account-currency P&L or broker-validated execution. Full Strategy Lab suite passes 72 tests; the live-local endpoint calculation returned excursions for all 13 trades of Duka XAUUSD M1 run `9adfb4a4-1b3c-4bca-b7f1-9cbb01f8f6f4`. Evidence: `evidence/test_outputs/mae_mfe_dukascopy_20261107.json`.

## MT5 importer execution — runtime blocked (2026-11-07)

The isolated portable terminal was opened and tested through Computer Use. The importer compiled from its current source with 0 errors and 0 warnings, but MT5 immediately logged `initializing of P0DukaCustomSymbolImporter (AUDUSD,M1) failed with code 0`. A minimal print-only `OnStart` probe failed the same way, before producing any output; automated trading was enabled only in this isolated copy during diagnosis and restored to disabled before shutdown. This isolates the failure to MT5 script initialization in this copy rather than the importer CSV path. No `import_marker_*` files, custom symbol, or copied bars were produced, so import success remains unverified and this single function is **BLOCKED** by the script runtime. The isolated terminal was closed, probe files were removed, and the original IC Markets terminal remained untouched and running; no account login, trades, or original data changes occurred. Evidence: `evidence/test_outputs/mt5_importer_computer_use_retry_20261107d.json`.

## Live app code freshness — 2026-11-07

The production health endpoint remains `ok` and points only to `C:\AI\Dukascopy_data`. The active server PID 17816 predates the current MAE/MFE endpoint code; the live 13-trade API response does not contain `mfe_price`, while the code and 72-test suite do. Docker shows only the Jesse and PostgreSQL services, and `docker top jesse` shows the normal service processes, with no one-off backtest runner. I left the existing server process untouched; the app must be safely reloaded before claiming the new MAE/MFE fields are live. Evidence: `evidence/test_outputs/live_app_staleness_20261107.json`.

## LabBreakout causal replay adapter — 2026-11-07

`/api/replay` and `/api/inspect` now include a causal `LabBreakout` recomputation when the run's current strategy hash and Dukascopy source hashes match. The adapter recomputes EMA and the logged candle decision from the causal Bid prefix, and uses only the exact event snapshot for position/pending-order availability. Full one-day XAUUSD M1 comparison matched all 1,380 of 1,380 decision, reason, and EMA snapshots. Arbitrary-strategy Python execution and inferred engine position/order state remain unsupported. See `evidence/test_outputs/labbreakout_causal_replay_all_candles_20261107.json`.


## Status reconciliation — 2026-11-07

The prior stale-process note is superseded: at 2026-11-07T18:01:35.870235+01:00, local health/version reported server PID 28408, backend current, frontend match true, 0 active runs, source root `C:\AI\Dukascopy_data`; the live API returned MFE/MAE for all 23 trades in run `c2239cb4-4ed9-4e54-a356-8121d850ebff`. Current suite passed 76 tests. See `CURRENT_BUILD_VALIDATION.md`.

At this audit snapshot the genuinely blocked or unverified items are: Jesse MCP-specific calls (unavailable connector); quote-side signal re-execution for strategies other than the LabBreakout adapter; exact MT5 CFD accounting/trigger parity; full network-isolated production restart; and Windows reboot verification. Causal prefix replay now passes for contract-instrumented LabBreakout and StatefulRsiPending. The MT5 custom-symbol importer is **OPTIONAL / DEFERRED** per the user's later instruction and must not be retried.

## V7 goal status reconciliation — 2026-11-07

The generic replay statement above is superseded for strategies implementing the `InspectorMixin` debug contract: `StatefulRsiPending`, `LabBreakout`, and M1→H1/H4/D1 `MultiTimeframeReplayProbe` return `FULL_CAUSAL_REPLAY` through the Jesse prefix API. Future-mutation tests passed for Stateful (216 decisions) and each H1/H4/D1 supplemental context (901 decisions). Simultaneous feeds, multi-symbol routes, and exhaustive Stateful per-candle trace equality remain **NOT TESTED**. See `GENERIC_REPLAY_VALIDATION.md`.

Current UI Computer Use verified a Duka-only StatefulRsiPending backtest, chart markers/connectors, unlimited selected-range points, and automatic replay at 25 candles/sec. The Inspector causal-replay button was also clicked: it matched the selected candle decision over a 436-candle prefix and supplied no future candles. See `UI_TEST_RESULTS.md` and `evidence/test_outputs/cua_dashboard_replay_20261107.json`.

Still open for the full V7 objective: generic quote-side signal re-execution for arbitrary instrumented Python strategies; broker/accounting parity; cross-run portfolio analytics; broader independent financial metric validation; simultaneous supplemental feeds and multi-symbol mutation checks; and a true network-isolated production-stack restart. Jesse MCP is unavailable. The MT5 importer is **OPTIONAL / DEFERRED** per the user's explicit scope and must not be retried. These items mean the entire V7 goal is **not complete**.

## Quote replay performance follow-up — 2026-11-07

The `StatefulRsiPending` Bid/Ask callback no longer copies and recalculates the full candle prefix on every decision. It declares an allow-listed Ask-close RSI spec; the engine uses Jesse's sequential RSI kernel once per run and passes only the causal current value. Generic callback history is now a lazy prefix view. The Duka XAUUSD M1 rerun preserved the prior result (1,378/1,380 saved-decision matches, 2 quote-side differences, 10 modeled trades). Arbitrary custom indicators and arbitrary-strategy Bid/Ask adapters remain untested. Evidence: `evidence/test_outputs/stateful_bidask_rsi_precompute_dukascopy_20261107.json`.

## Replay cursor close-count correction — 2026-11-07

**Resolved:** a prefix run can contain a trade record that Jesse closes only while finalizing the truncated backtest. The API previously counted that record as closed at an earlier cursor (including the entry fill cursor). It now counts a trade as closed at the cursor only when its actual `closed_at` is not later than that cursor, and reports prefix-only closures separately. Replayed Duka signal, fill, TP and expiry checkpoints all matched the saved decision; fill reports zero closed trades plus one prefix cleanup, and TP reports one closed trade. Evidence: `evidence/test_outputs/stateful_replay_lifecycle_checkpoints_20261107.json`; 85 Strategy Lab tests pass.


## Bid/Ask generic callback time base — 2026-11-07

**Resolved:** instrumented quote callbacks now receive a trading-relative `index` starting at zero after warm-up and a separate warm-up-inclusive `history_index`; close/cooldown state uses the trading-relative index. Live Duka replay improved StatefulRsiPending saved-decision agreement to 1,379/1,380. The one remaining difference is the intentionally selected Ask-close RSI crossing a threshold that differed on Jesse's Bid-based signal. Bid and Ask source hashes are unchanged. The general callback contract supports arbitrary code only when a strategy implements it; other indicators/specs and broker execution remain limited or unverified. Evidence: `evidence/test_outputs/stateful_bidask_quote_index_regression_20261107.json`.
- **Cross-run portfolio analytics:** The prior open item is resolved. The Portfolio view calculates only compatible saved Dukascopy Bid/Ask runs and blocks incompatible signal/execution modes without combined financials. Results remain provisional; broker and tick parity are not claimed. Evidence: `PORTFOLIO_ANALYSIS_STATUS.md`.

- **Blocked at the connector boundary:** Jesse MCP-specific calls; no Jesse MCP connector is exposed in this session. Local API and Docker validation passed.
- **Unproven by available price inputs:** exact MT5 CFD accounting/tick parity; Dukascopy OHLC has no intrabar tick order or broker-verified contract settings. The tested bid/ask ledger remains a research model.
- **Supported only by explicit strategy contract:** quote-side signal re-execution for strategies that do not implement the declared callback; these are not treated as tested or inferred. Causal replay supports instrumented strategies through the shared contract.
- **Deferred by scope:** full production-wide offline reboot/restart is not required for this development round; bounded offline component tests passed. MT5 importer is **OPTIONAL / DEFERRED** and must not be retried.

These items do not block locally executable P0 acceptance. No live/paper trades were executed and the original Dukascopy and MT5 data were not changed.



## V8 — open punten (2026-11-09)

- **Dagelijkse download live nog niet bewezen** (PARTIAL): datafeed gaf 's ochtends 200 (XAUUSD correct gedecodeerd), daarna 503/time-outs. De geplande taak probeert met backoff (30 min → 6 u). Eerste geslaagde run moet de puntfactoren van de 8 overige symbolen bevestigen (±30%-prijscontrole voorkomt verkeerde schaal).
- **Forward voor Python-plugins** (DEFERRED): plugins kunnen backtesten en valideren, maar nog niet bevroren forward draaien.
- **Walk-forward/parameterstabiliteit voor plugins** (DEFERRED): te duur bij eigen engines; oordeel daardoor begrensd.
- **Forward-verwachtingsalerts** (PARTIAL): FORWARD_BELOW_EXPECTATION/CUSUM vereisen opgeslagen backtestverwachtingen per registratie.
- **Nieuwsfilters, orderflow/L2, realtime paper (Gate D)**: DATA_REQUIRED.
- **ML-module**: DEFERRED.
- **Lichte thema**: niet op alle breedtes visueel gecontroleerd.
- Wijzigingen aan `rule_engine.py`/`forward_rules.py` maken bestaande rule-registraties ongeldig (bewuste versiebevriezing); productie heeft er nog geen.

## V8 UX overhaul (2026-11-09) — see UX_V8_REPORT.md §5
- Statistical validation only for Builder specs and plugins, not hand-written Jesse strategies.
- Plugins not selectable in the tester workspace.
- No dedicated rejected-signal overlay; recorded decision events only.
- US500/DE40 daily fill blocked on unverified bi5 point factor.
- Jesse tester runs cannot be cancelled mid-run.

## V9.1 (2026-11-10) — see V9_REAL_MARKET_VALIDATION.md
- Plugins: no forward registration, optimizer or decision log (shown as limitation).
- Jesse validation: spread/slippage stress NOT_TESTED (engine uses Bid + fee fraction).
- Contract specs remain assumptions (not broker-verified); DE40 EUR conversion defaults to 1.0.
- GoldReaper needs PLUGIN["research_history"]["external_trials"] and a fresh holdout before FORWARD_ELIGIBLE.
- PBO with 4 combinations (Jesse/plugin) is weak evidence.

## V10 (2026-11-10) — zie V10_IMPLEMENTATION_REPORT.md
- Geen enkele echte kandidaat haalde walk-forward (V9 240, V10 419 gegenereerd); bevriezen/eindtest/forward alleen met fixtures getest.
- Multi-market alleen voor Builder- en fabrieksstrategieën (Jesse/plugins: NOT_SUPPORTED).
- DSR-gate gebruikt V_null; keuze ter beoordeling.
- Single-window-regel geeft FAIL bij korte historie (zou INSUFFICIENT_EVIDENCE kunnen zijn).
- Oude ledgerregels zonder returnreeksen: effectief N = ruw N.
- Data: 7/9 markten nog aan het inhalen (providerstoringen); slaapstand/langdurige storing alleen met fixtures getest.
- Nieuwe CSS/JS staat in shell.css/workflow.js i.p.v. aparte v10-bestanden.

## V10.1 (2026-11-10) — zie FINAL_REMAINING_ISSUES.md en V10_1_FEATURE_AUDIT.md
Volledige, geconsolideerde lijst: `FINAL_REMAINING_ISSUES.md`. Bewijsniveau per functie: `V10_1_FEATURE_AUDIT.md`.
- Geen echt prospectief forwardbewijs (0 forwardportefeuilles); realtime paper trading NOT IMPLEMENTED.
- Geen strategie haalde walk-forward in een echte run (V9 240, V10 419, V10.1 370 gegenereerd); 0 PASSED; positief pad L6-L8 alleen met fixtures; Optuna afwezig; AI-assistent niet gebouwd.
- DSR: gate = NC-DSR (niet de paper-DSR), zoekniveau-effectief N; NC-DSR straft zoektochten die nooit op validatie kijken te zwaar. Vervangt de V9/V10-uitspraken over de DSR-gate (DSR_FINAL_METHODOLOGY.md).
- Contractonzekerheid wordt niet automatisch als beslissende factor herkend; contractspecificaties niet broker-geverifieerd, geldbedragen PROVISIONAL.
- Forward: bestaande portefeuilles opnieuw aanmaken (nieuwe engineversie); statisch FX-tarief; PLACEHOLDER_RATE gemarkeerd, niet geblokkeerd.
- Uitvoering: partial exits, margin/max lot/stop-out en overnight swap niet gemodelleerd.
- Data: 6 van 9 markten lopen achter, provider-503's; kalenders DERIVED_FROM_DATA (niet provider-geverifieerd); originelen niet read-only beschermd; Task Scheduler-geschiedenislog uitgeschakeld; echte herstart/slaapstand niet uitgevoerd.
- Vervangen punten uit V10 (bijv. "7/9 markten inhalen", "single-window geeft FAIL", "Jesse/plugins NOT_SUPPORTED", "DSR-keuze ter beoordeling"): zie sectie 6 van V10_1_FEATURE_AUDIT.md.
