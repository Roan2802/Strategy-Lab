# P0 validation — Jesse Strategy Lab

**Status: CONDITIONAL GO** for local Dukascopy research and strategy debugging. **NO-GO** for verified MT5 CFD result parity. Updated 2026-11-07.

## Scope

All strategy/backtest/optimization/execution price data used in this P0 validation came exclusively from `C:\AI\Dukascopy_data`. Original Duka files were read-only. No MT5 historical prices, external feed, live trade, or paper trade was used. Isolated unit fixtures are described as code tests only, not market-performance evidence.

## Completed

- Existing Jesse Docker stack is running (`salehmir/jesse:latest`, Jesse v3.6.0) with PostgreSQL healthy. The local dashboard is at `http://127.0.0.1:8767/`; health reports `ok`, `local-only`, and the Duka source root. Latest listener check found only 8767; 8765/8766 were not listening.
- All 11 discovered Duka symbols have data manifests. XAUUSD Bid/Ask source hashes and quality counts are recorded in the manifest and `DATA_QUALITY_REPORT.md`.
- XAUUSD M1 2026-09-14 loaded 1,380 candles. Jesse backtest run `85808ac8-84eb-478e-a405-7ba41a11f56d` completed with 13 trades and 1,432 events; Jesse model net P&L was `-70.2517536194` with fees `67.2535536194`.
- In-app UI confirmed run selection, metrics, trade table, trade jump, candle inspector, PASS/FAIL/SKIP checks, and causal replay cutoff. Chart controls expose 11 symbols, M1–D1, presets, run comparison, optimization and Bid/Ask ledger.
- Replay regression now covers both directions. Long run `fa82d87f-779e-4e38-893b-fc0295406d32` showed bullish entry triangles and TP/SL exit markers. A separate Duka-only September run `bc3cede8-dc2d-42bc-b762-339f45f9fe9a` showed 28 bearish entry triangles, 28 individual dotted entry-to-exit connectors, and TP/SL exits. Cursor cutoff hid future trades; replay speed options 1/3/5/10/25 candles per second auto-advanced. Evidence: `evidence/test_outputs/replay_trade_markers_20261107.json`.
- Optimizer diagnostics passed for bounded Random, Grid and TPE executions, deterministic Random seed repeat, input validation, a 3-to-5 Random resume, and selecting/re-running the best-of-three trial twice with exact parameters and identical results. All scores were negative; this proves plumbing, not a successful or profitable optimization.
- The configurable 50–90% chronological split was exercised in ChatGPT IAB: run `34cedb29-a799-4ddd-afb2-a51810027b96` completed with 70/30 = 966/414 candles, confirmed in the saved manifest and result. The holdout segment had zero trades, so no out-of-sample claim is made. A broad-space run retained a balance-feasibility error; see `OPTIMIZER_DIAGNOSTICS.md`.
- Bid/Ask execution replays Duka quote-side fills. The `LabBreakout` adapter re-executes LONG signals on Ask OHLC/EMA and SHORT signals on Bid OHLC/EMA; 2026-09-14 XAUUSD M1 yielded 1,379/1,380 saved-decision matches, one Ask-EMA PASS→FAIL divergence, and 12 model orders/fills. The API returned HTTP 200 using a temporary run copy. Contract parameters were QA-only, not broker specifications; M1 bars do not prove tick order or broker parity. Evidence: `evidence/test_outputs/bidask_signal_reexecution_dukascopy_20261106.json`.
- Desktop icon/theme check: refreshed SVG-rendered 4096×4096 PNG, 192/512 PWA images, and multi-size Windows ICO are served by the app; desktop shortcut points at the refreshed ICO and PWA manifest uses matching art. `Beeld → Kleurthema` first shows Donker/Licht; Licht opens green/pink/red palettes. The existing native app window's reload could not be driven in this continuation, so the refreshed taskbar icon was not visually rechecked. Evidence: `evidence/test_outputs/icon_theme_desktop_refresh_20261106.json`.
- Live replay cutoff check on Duka XAUUSD M1 (2026-09-14): at 09:33 UTC the API returned 574 candles/594 stored events up to the cursor and no future candles. This does not re-execute the strategy or recalculate EMA/state; see `evidence/test_outputs/dukascopy_replay_cutoff_20261106.json`.
- Historical `strategy_lab/tests` snapshot: 14 passed after the narrow-layout and enum-metadata changes; this was superseded by the 32-test continuation below. Jesse Docker core subset: 34 passed; logs are in `evidence/test_outputs/`.

## Open / blocked

- Jesse MCP-specific calls: **BLOCKED**, because no Jesse MCP tools are exposed in this session; local Docker research API and dashboard were used.
- Generic quote-side signal re-execution for instrumented strategies: **NOT TESTED**, except the LabBreakout adapter. Unsupported strategies retain stored intents.
- Generic causal prefix re-execution: **PASS for instrumented LabBreakout, StatefulRsiPending, and M1→H1/H4/D1 supplemental-feed probes**. Stateful future-mutation passed for 216 prefix decisions; H1, H4, and D1 future-mutations each passed for 901 decisions. Uninstrumented Python files, simultaneous supplemental-feed combinations, multi-symbol feeds, and exhaustive Stateful trace parity remain unsupported or **NOT TESTED**. See `GENERIC_REPLAY_VALIDATION.md`.
- MT5 parity: **BLOCKED by the Duka-only price-source constraint**; exact CFD contract, trigger, spread, margin, swap, and fill parity is not established.
- Full production offline network-isolation restart: **NOT TESTED**. A disposable Windows launcher/Compose copy successfully launched its hidden app and Edge window while Jesse egress was blocked; its project and test port were cleaned up. Evidence: `evidence/test_outputs/offline_windows_launcher_acceptance_20261106.json`.
- MT5 custom-symbol importer: **OPTIONAL / DEFERRED** by the user's explicit scope; do not retry. Earlier isolated evidence is retained for reference only. Original MT5 files and accounts are outside this goal.
- Unsupported in this adapter: multiworker optimization; TPE resume (sampler checkpoint not persisted). Multi-objective, walk-forward, holdout robustness, and multi-symbol portfolio optimization remain unverified.
- M1 OHLC cannot determine tick-level order/fill sequence. The ledger's test contract assumptions are not asserted as broker specs. `USATECHIDXUSD` has 2,670 Ask-only timestamps and incomplete quote rows are skipped.
- Lifecycle timestamp/order-ID joins were validated for named Dukascopy runs; see the correction and 7/7 reconciliation evidence in `REMAINING_ISSUES.md`. General lifecycle behavior for arbitrary strategies remains unverified.

## Detailed evidence

- [Execution validation](EXECUTION_VALIDATION.md)
- [Optimizer diagnostics](OPTIMIZER_DIAGNOSTICS.md)
- [Trade Inspector tests](TRADE_INSPECTOR_TESTS.md)
- [Data quality](DATA_QUALITY_REPORT.md)
- [Browser regression](BROWSER_TEST_RESULTS.md)
- [Updated feature matrix](UPDATED_FEATURE_MATRIX.csv)
- [Remaining issues](REMAINING_ISSUES.md)

Reproduction scripts and JSON outputs are in `evidence/test_outputs/`. No original market-data files or MT5 data were changed.

## P1 lifecycle delta — 2026-11-06

The P0 data-source constraint remains in force: this run read prices only from `C:\AI\Dukascopy_data`. Current Duka XAUUSD M1 lifecycle test reconciles 7/7 opens and 7/7 closes to Jesse `executed_at`, with 13/13 pending-expiry cancel IDs matched. One additional ID-bearing cancellation has no strategy cancel-request event; its immediate context is Jesse's end-of-run cleanup after the final candle, not a missing expiry request. Reproducible classification is in `evidence/test_outputs/dukascopy_lifecycle_teardown_classification_20261106.json`. This is not evidence of MT5 financial parity or general replay-state reconstruction. Strategy Lab suite: 18 passed; Jesse core Docker subset: 34 passed. Full evidence is linked from `TEST_RESULTS.md`.

## P0 simple-mode and parameter visibility delta — 2026-11-06

The user-facing settings panel now separates four verified LabBreakout core controls from five advanced controls, with an expandable summary showing each advanced value and unit. In-app browser inspection verified help text, range/default details, and editability. The UI-triggered XAUUSD M1 backtest (`e3d00425-02c6-4dcd-b66f-5e9429afced0`) completed with 13 trades and 1,432 events; its manifest preserves `stop_distance=2.5` and all other selected values, and names only source files rooted at `C:\AI\Dukascopy_data`. Strategy Lab tests: 30 passed; Jesse Docker core subset: 34 passed. This closes the verified LabBreakout simple/advanced grouping item; Chrome/Edge comparison and the other unresolved technical items remain open.

## P1 final-order cleanup classification — 2026-11-06

The lifecycle run's extra final-time cancellation is confirmed as Jesse teardown, not an untracked expiry request. The final candle recorded the order as pending, Jesse logged cancellation of remaining orders for a fresh run, then emitted the cancel at that same timestamp. The 13/13 expiry requests remain fully matched. Reproduction and result: `evidence/test_outputs/verify_lifecycle_teardown.py` and `evidence/test_outputs/dukascopy_lifecycle_teardown_classification_20261106.json`.

## V4 continuation — 2026-11-06

The named `TRADE_INSPECTOR_DESIGN.zip` was not present in the project, Downloads, or supplied attachment folders; the project copy `TRADE_INSPECTOR_DESIGN.md` and the latest 6-Nov audit reports were read instead. The desktop launcher was cold-started after Docker Desktop was stopped; its initial executable-path defect was fixed and the cold retest passed. Account-aware LabBreakout Spot filtering passed a Duka Grid run and a Duka TPE smoke run; a selected trial was replayed as a backtest with exact parameters and matching dataset digest. Strategy Lab regression suite: 30 passed; see the latest continuation block in `TEST_RESULTS.md`. Remaining P1/P2 limits are enumerated in `REMAINING_ISSUES.md`.
# P0 continuation delta — 2026-11-06

The actual Windows desktop shortcut was exercised through a Docker Desktop cold stop/start. The first test exposed a scalar path-indexing defect (`Docker Desktop` resolved to `C`); after fixing path candidate selection, the retest started Docker Engine 29.9.2, observed PostgreSQL healthy, brought Jesse to HTTP 200, and opened the exact-title Edge app window. The local health endpoint confirms only `C:\AI\Dukascopy_data` as the source root. Logs and scenarios: `STARTUP_TEST_RESULTS.md`.

Responsive dashboard screenshots were saved at emulated 1280×720, 1920×1080, and 2560×1440; measured document widths were 1265, 1905, and 2545 CSS px respectively. The separate native Windows capture is `evidence/screenshots/desktop-app-window-native.png`. This does not establish pixel-equivalent rendering in standalone Chrome and standard Edge.

Strategy Lab regression suite is now 30/30 after the continuation changes. Jesse core Docker subset remains the prior 34/34 result; it was not rerun in this continuation.

## P0 execution-status and dashboard delta — 2026-11-06

The Strategy Lab process was restarted from the existing launcher and its health endpoint returned `ok`, `local-only`, and source root `C:\AI\Dukascopy_data`. The ChatGPT in-app dashboard successfully displayed distinct provisional Jesse and Dukascopy Bid/Ask replay statuses. On the selected 7-trade run the explicitly configured 0.01 lot-step check flagged 7/7 quantities; monetary outputs remain illustrative, with no signal recomputation or broker contract verification. Machine-readable evidence is in `evidence/test_outputs/dukascopy_execution_ui_status_20261106.json` and `evidence/test_outputs/dukascopy_execution_statuses_20261106.json`.

Current continuation checks: Strategy Lab 31 tests passed; Python compileall and JavaScript syntax check passed; Jesse core Docker subset passed 34 tests in a network-disabled disposable container with the source mount read-only and cache directed to `/tmp`. These checks do not close MT5 parity, full offline restart, or the other limits listed in `REMAINING_ISSUES.md`.



## Symbol/timeframe end-to-end matrix — 2026-11-06

All requested 8 symbols × 7 timeframes completed through the Strategy Lab → Dukascopy loader/resampler → Jesse Docker backtest path for 2026-09-14. All 56 successful manifests point only to `C:\AI\Dukascopy_data`. A format defect was corrected for USDJPY, BTCUSD, EURUSD and GBPUSD; first-pass failures and corrected retests are retained in `evidence/test_outputs/dukascopy_symbol_timeframe_matrix_20261106/matrix_final.json`; source paths for every successful run were independently read from its Jesse manifest and verified in `evidence/test_outputs/dukascopy_matrix_manifest_source_verification_20261106.json`. Higher-timeframe one-day runs with zero trades/events verify the data/engine path only, not strategy behavior.


## P0 validation continuation — 2026-11-06

- Generic instrumented strategy check: `ExampleStrategy` run `441bc513-e23e-4223-8be2-018c8b2752c5`, XAUUSD M1, 2026-09-14, source exclusively under `C:\AI\Dukascopy_data`. It produced 1,380 candle decisions and 11 events each for submit/open/close. ChatGPT IAB **Spring** landed on the first trade's 01:00 UTC signal candle and displayed its structured PASS checks plus order submission. Evidence: `evidence/test_outputs/dukascopy_example_strategy_inspector_20261106.json` and `evidence/test_outputs/dukascopy_example_strategy_inspector_ui_20261106.json`.
- The one-day end-to-end route matrix completed all 56 symbol/timeframe combinations (8 symbols × 7 timeframes); every final manifest resolved to the Dukascopy folder. Higher-timeframe zero-trade results remain workflow checks, not strategy validation. Evidence: `evidence/test_outputs/dukascopy_symbol_timeframe_matrix_20261106/matrix_final.json` and `evidence/test_outputs/dukascopy_matrix_manifest_source_verification_20261106.json`.
- Current Strategy Lab suite and static checks are recorded in `evidence/test_outputs/strategy_lab_pytest_20261106_final_continuation.txt`, `strategy_lab_compileall_20261106_final_continuation.txt`, and `strategy_lab_node_check_20261106_final_continuation.txt`.

These checks close only the second-example Inspector smoke test and one-day route matrix. Spread-sensitive signal re-execution, arbitrary strategy-state replay, offline network isolation, broad resampling fidelity, and MT5 financial parity remain open.

## Full-month acceptance test and replay fix — 2026-11-06

The ChatGPT IAB completed the requested XAUUSD M1 `LabBreakout` run for September 2026 from Dukascopy only: 30,209 candles, 230 trades, 31,129 logged events, 0 winners, net Jesse result `-11.9757%`. The result is preserved as `JESSE_RESEARCH_PROVISIONAL`; it is a negative research result, not a validated broker-return estimate. Manifest source fingerprints for both Dukascopy files still match their current hashes.

The run exposed that Spring could be clamped to the previously selected single-day chart range. Saved-run selection now synchronizes chart and replay context to the selected run. Browser re-test selected the first trade's 02:00 UTC signal candle (decision PASS, ORDER_SUBMITTED; fill hidden), then the next M1 candle at 02:01 UTC (POSITION_OPEN visible). See `evidence/test_outputs/dukascopy_ui_fullmonth_spring_replay_fix_20261106.json`. Regression suite: 32 passed; JS syntax and Python compile checks passed.

## Optimizer control browser verification — 2026-11-06

The ChatGPT in-app browser exercised the editable optimizer controls on XAUUSD M1/2026-09-14. The UI showed Spot-feasible categories, user bounds `entry_interval=60..120 step 60`, and fixed parameter values; the actual Jesse trial preserved those settings and completed 1/1 with no trial errors. One old API process was found to have stale code; it was relaunched via `desktop-launcher.ps1` and returned healthy status on 8767. An out-of-bounds request was rejected with HTTP 422 before data loading. This verifies the local UI-to-runner path only; it does not close cold-start-from-Docker-off or all listed Windows failure scenarios. Evidence: `evidence/test_outputs/optimizer_user_search_space_20261106.json`.

The restarted default dashboard served the configured ICO at `/assets/dukascopy-strategy-lab.ico` (HTTP 200, 2,686 bytes); ChatGPT IAB verified the favicon reference and found zero browser-console errors. Evidence: `evidence/test_outputs/dashboard_favicon_route_20261106.json`.

## Replay trade markers — 2026-11-07

Added directional entry triangles, distinct TP/SL exit symbols, and a unique dotted entry-to-exit connector for each closed trade. Replay rendering now suppresses entries and exits beyond the replay cursor, so future trades are not exposed ahead of the replay. The chart accepts the existing 1/3/5/10/25 candles-per-second replay options. The restored HTML controls persist after reload and keep Markt and Strategie settings on separate tabs.

Browser verification used only XAUUSD M1 runs whose manifest source root is `C:\AI\Dukascopy_data`: a 13-trade long run displayed `Bullish entry`, `TP geraakt`, and `SL geraakt`; a September 2026 run displayed 28 bearish entry triangles, 28 dotted trade connectors, and both exit types. Spring replay of the first short trade showed only its connector and SL exit by 09:49 UTC; later entries/exits remained hidden. Evidence: `evidence/test_outputs/replay_trade_markers_20261107.json`. Static checks and the full Strategy Lab suite passed (74 tests).

## Chunked parsing and PWA status — 2026-11-06

The new Dukascopy side parser passed exact Bid and Ask comparison against the existing Parquet cache for all 8,244,053 Bid and 8,244,051 Ask rows. The later full old/new `ensure()` comparison verified 43.6% faster first import and 47.9% lower peak RSS; the exact metrics are recorded in the next section and JSON evidence. Full Strategy Lab suite: 49 passed. The local PWA is installed in Edge with the custom manifest icon and exact app title; the existing desktop shortcut still carries the project ICO. The Edge PWA taskbar pin is now confirmed from its AppsFolder `Van taakbalk losmaken` verb; its new PNG/ICO artwork is served locally.

## Full-import benchmark — 2026-11-06

The old and current Dukascopy XAUUSD importers were run in separate fresh Python processes with empty temporary caches. All 8,244,053 joined rows and all OHLC/volume values exactly matched the existing Parquet cache; both versions produced identical quote-quality counts and cache size. The current chunked/index-based path reduced import time from 50.04 s to 28.20 s (43.6%) and peak RSS from 5.42 GB to 2.82 GB (47.9%). Original Bid/Ask CSVs remained read-only. Full evidence: `evidence/test_outputs/benchmark_chunked_dukascopy_parse_20261106.py` and `evidence/test_outputs/chunked_dukascopy_parse_20261106.json`. Strategy Lab suite: 49 passed.


## 2026-11-06 optimizer UI and import-performance continuation

- Chrome exercised the optimizer renderer using an existing Duka-only run. Score sort, heatmap parameter selectors/axis labels, sensitivity chart, and JSON export all passed with zero browser errors. No new market run was started. Evidence: `evidence/test_outputs/optimizer_analysis_ui_20261106.json`; screenshot: `evidence/screenshots/optimizer-analysis-20261106.png`.
- Dukascopy full-import old/new comparison was exact for all 8,244,053 joined rows, OHLC/volume values and quote quality. Time improved 43.6% (50.038 s to 28.202 s); peak RSS fell 47.9% (5.42 GB to 2.82 GB). Raw inputs remained unmodified. Evidence: `DATA_PERFORMANCE_REPORT.md` and `evidence/test_outputs/chunked_dukascopy_parse_20261106.json`.
- `python -m pytest strategy_lab/tests -q`: 49 passed; `python -m compileall -q strategy_lab`, `node --check strategy_lab/static/app.js`, and `python -m json.tool evidence/test_outputs/optimizer_analysis_ui_20261106.json`: PASS.
- Remaining high-impact limits: broker-verified CFD accounting/MT5 parity, spread-sensitive signal re-execution, arbitrary-strategy state replay, and fully network-isolated offline restart. Jesse MCP-specific functions were unavailable.


## Offline-deny component validation — 2026-11-06

Chrome loaded the localhost Strategy Lab and previously saved Dukascopy XAUUSD M1 backtest/optimizer views with every non-loopback browser request blocked. Local CSS/JS/Plotly/favicon assets returned HTTP 200; chart (10,070 points), 230 trades, metrics, five optimizer trials, sensitivity/heatmap and three stored layouts were read successfully. Health and dataset APIs report only `C:\AI\Dukascopy_data`. A fresh cached Jesse image also passed package import and `jesse install-live --no-strict` with Docker `--network=none`. No fresh backtest/optimization was run in this test. The complete offline restart remains NOT TESTED because the actual Docker/PostgreSQL/Jesse stack and Windows launcher were not restarted under network isolation. Full logs: `OFFLINE_TEST_RESULTS.md`.


## Isolated offline calculation validation — 2026-11-06

A fresh disposable Jesse + TimescaleDB stack started with Docker network egress disabled (`network=none`, shared loopback namespace, no host-published ports, PostgreSQL on tmpfs). Jesse returned HTTP 200. The copied current runner completed a fresh Duka XAUUSD M1 backtest (30,209 candles, 230 trades) and one-trial optimizer (1/1, zero errors). Backtest metrics/trade/event counts and optimizer trial parameters/status/score exactly matched saved Duka-only baselines. The request's source manifests point only to `C:\AI\Dukascopy_data`; raw data was not mounted into the container. The containers were removed. Reproducible evidence: `evidence/test_outputs/offline_jesse_stack_20261106.py` and `.json`. This does not verify the actual Windows launcher or production Compose stack restart while offline.


## Launcher error-path regression — 2026-11-06

A test copy of the production launcher was run with an isolated fake Docker CLI and test port 18767. Missing-source handling returned exit 1, logged the expected error, and did not invoke Docker. A live test listener caused exit 1 and the explicit port-conflict message; the listener remained alive. The real data root and production port 8767 were untouched. Evidence and harness: `evidence/test_outputs/launcher_failure_scenarios_20261106.json` / `.ps1`. This validates the launcher branch logic, not a real unrelated service on production port 8767 or popup rendering.


## Bid/Ask signals and app icon delta — 2026-11-06

- The live local app has been restarted and returned `ok`, `local-only`, `C:\AI\Dukascopy_data`, with one listener on 8767. The app window reloaded successfully.
- Strategy Lab suite: **59 passed**; JavaScript syntax, Python compile, API route (HTTP 200), and Duka XAUUSD quote-side signal replay passed. No live/paper trades; original market files remain untouched.
- `LabBreakout` now has a limited quote-side signal adapter. Generic arbitrary-strategy signals/state and broker-specific contract parity remain explicitly blocked or untested; see `REMAINING_ISSUES.md`.

## Desktop shortcut live check — 2026-11-06

The actual `.lnk` was opened from the Windows Desktop folder and the hidden launcher reused healthy Docker/Jesse/PostgreSQL and the sole Strategy Lab listener. The evidence verifies one titled app window, zero visible Docker Desktop windows and zero visible PowerShell/CMD windows, with health `ok` and `source_root=C:\AI\Dukascopy_data`. Explorer keyboard activation passed; a literal mouse double-click could not be confirmed by the UI automation and remains open, as do Windows reboot and full offline launcher restart.

## 2026-11-06 continuation corrections

- The desktop icon master export is now 4096×4096. The actual desktop shortcut points at the new cache-busted ICO path; PWA manifest/favicon are versioned `20261106.6`. Asset routes and local health were checked against the live 8767 service. Strategy Lab regression: 60 passed. See `evidence/test_outputs/icon_theme_desktop_refresh_20261106.json` and `evidence/test_outputs/tpe_resume_guard_20261106.json`.
- A disposable offline launcher run reached a focused Edge app window with PostgreSQL healthy, Jesse HTTP 200 and blocked Jesse egress. Only the test containers/network and copied server were stopped; production 8767 remained live. Full production offline restart, host-wide network denial and Windows reboot remain open. See `OFFLINE_TEST_RESULTS.md`.

## Optimizer outcome follow-up — 2026-11-06

Current-code local Jesse research runs using only Dukascopy XAUUSD M1 classify Spot insufficient balance and Futures insufficient margin separately, preserve exact errors/phases/parameters, and retain negative completed trials. The browser table shows those outcomes; `NO_TRAINING_TRADES` displays no score rather than a misleading zero. Evidence: `evidence/test_outputs/optimizer_outcome_classification_20261106.json` and `evidence/screenshots/optimizer-outcome-classification-20261106.png`.

Latest verification: `python -m pytest -q strategy_lab/tests` — 70 passed; `node --check strategy_lab/static/app.js` and `python -m compileall -q strategy_lab runtime/strategy_lab` passed. `GET /api/health` reports `ok`, `local-only`, and source root `C:\AI\Dukascopy_data`. Full output: `evidence/test_outputs/strategy_lab_validation_20261106_final.txt`.

The general historical margin preflight remains unresolved because available funds change with historical fills and P&L, and no verified broker contract model exists. P0 remains conditional: generic strategy replay and MT5 financial parity are not established; no Live/Paper trades were used.

## Toolbar layout follow-up — 2026-11-06

The top filter and action controls were grouped into aligned responsive rows. The current local app loaded the cache-busted stylesheet and the ChatGPT in-app browser visually confirmed the toolbar grouping at approximately 1265×720. Narrow viewport rendering remains unverified. Evidence: `evidence/test_outputs/toolbar_alignment_regression_20261106.json`. Strategy Lab regression suite: 70 passed; JS syntax and Python compile checks passed before this layout-only change.

## UI and run-action follow-up — 2026-11-06

The dashboard now keeps the Dukascopy source path out of the header; the local-data health label remains. Backtest controls are split into Market and Strategy tabs, and Entry Inspector has separate Candle and Accountmodel tabs. The Beeld menu now offers Donker or one Lichtgroen option; the light palette editor accepts a native spectrum color and validated hex value and derives matching pastel surfaces. Metric cards can be toggled and explain their definitions on hover. These cards use available Jesse results; missing metrics remain blank rather than estimated. Chart point limit now includes `Alles tonen` for the full selected period, and replay can auto-advance from 1 candle/2 seconds up to 5 candles/second.

Headless Chromium at 1440×1100 verified the source path is absent from the header, both setting tabs, both Inspector tabs, custom hex theme persistence in the test browser, nine metric cards with nine hover help controls, and 1,380/1,380 candles for the selected Dukascopy day at `limit=0`; there were zero JavaScript errors. Replay advanced five minutes in 1.5 seconds at the fastest setting and the API reported only six candles visible at the cursor. Screenshot: `evidence/screenshots/strategy-lab-current-20261106.png`; machine evidence: `evidence/test_outputs/ui_followup_and_p0_browser_20261106.json`.

`Bestand > Backtest uitvoeren` completed on Dukascopy XAUUSD M1 for 2026-09-14: 13 trades, zero winners, net Jesse result −70.2518; it remains explicitly provisional. `Bestand > Optimaliseren` completed 1/1 trial and opened the optimization result tab; its one training trial remains `ERROR` with the original insufficient-balance message and TRAINING phase. Runs `9adfb4a4-1b3c-4bca-b7f1-9cbb01f8f6f4` and `c429a818-fbad-48db-9ea2-53be75365349` are the post-fix menu-action checks.

The MT5 importer CSV provenance is verified for 174,475 rows, but `P0DukaCustomSymbolImporter.mq5` has not been executed and the custom symbol is not established. The actual MT5 terminal was minimized during this continuation, and the available MT5 MCP does not expose a run-script operation. No original MT5 history or raw Dukascopy CSV was modified. OOS/Walk-Forward, Monte Carlo, risk-of-ruin, CVaR and parameter-stability metrics remain unavailable. Trade MAE/MFE is now derived in current code as raw Bid-price excursion from saved Dukascopy candles; the active port-8767 process has not loaded that code yet (see the 2026-11-07 live-code-freshness evidence).

Chart limit follow-up (2026-11-06): default display is now `Alles tonen`; API limit zero skips thinning and returns the full selected range. A regression test confirms all rows are returned when the range exceeds 8,000 candles. See `UI_TEST_RESULTS.md` and `evidence/test_outputs/chart_unlimited_20261106.json`.

### MT5 importer follow-up — 2026-11-06

MT5 MCP connected to `C:\AI\MT5_ICMarkets_Global\terminal64.exe`, compiled the isolated Dukascopy importer to `.ex5`, then performed a read-only symbol inventory. The test custom symbol is absent, so CSV provenance remains verified but MT5-side row import and chart visibility are still NOT TESTED. The intended terminal window was minimized and MT5 MCP exposes no script runner; another visible Program Files terminal was not used. No trades or Strategy Tester execution occurred and original MT5 history was not modified. Evidence: `evidence/test_outputs/mt5_import_compile_inventory_20261106.json`.

Computer Use continuation opened the portable audit copy without connecting an account; Algo Trading was unchecked and the account-open dialog was canceled. The importer inputs dialog appeared, but the subsequent screenshot surface became inconsistent with the MT5 accessibility tree, so execution and imported bars could not be verified. The importer remains **NOT TESTED**. During navigation, the uninstall entry was inadvertently opened in that disposable copy; its missing program files were restored from the existing local Program Files installation while preserving the copied Config and MQL5 data. Neither original installation nor original market data was modified. Evidence: `evidence/test_outputs/mt5_importer_computer_use_attempt_20261106.json`.


## Trade Inspector selected-trade screenshot — 2026-11-06

Captured the first trade from existing Duka-only run 6428325a-599b-4e94-a459-83d462c6b897 at its exact signal candle (2026-09-01 02:00 UTC), before the 02:01 UTC fill. The Inspector displays ENTRY_CONDITIONS_MET, five PASS checks and the linked ORDER_SUBMITTED event. Replay was stopped at capture. No new run or trade was started. Screenshot: evidence/screenshots/trade-inspector-signal-candle-20261106.png; structured evidence: evidence/test_outputs/trade_inspector_selected_signal_20261106.json.


## Trade-overlap analytics — 2026-11-06

Added a selectable per-run open-position concurrency chart computed from stored entry/exit timestamps. Legacy chart selections migrate with this chart enabled. Browser verification passed on existing Duka run 9adfb4a4-1b3c-4bca-b7f1-9cbb01f8f6f4 (13 complete trades; max concurrency 1; no overlap time) and a synthetic overlap fixture (max concurrency 2; 2,000 ms overlap). Toggle off/on, dark card palette, all six graph labels and zero browser errors verified. Evidence: evidence/test_outputs/trade_overlap_analytics_browser_20261106.json; graph screenshot: evidence/screenshots/trade-overlap-analytics-20261106.png. Portfolio-level overlap remains unimplemented.

## Chart point cap — live API verification — 2026-11-06

The chart selector now offers `Onbeperkt` (`limit=0`). Against the live local API, the selected XAUUSD M1 September 2026 range returned all 30,209 available candles (30,209/30,209), with first/last timestamps matching the requested range. Health reports `C:\AI\Dukascopy_data`; no source files were modified and no trades were run. Evidence: `evidence/test_outputs/chart_unlimited_live_api_20261106.json`. Regression: full Strategy Lab suite 71 passed; the targeted unlimited-limit test passed.

## PostgreSQL-unhealthy startup failure — isolated test — 2026-11-06

A test copy of `desktop-launcher.ps1` was exercised with a local Docker CLI shim reporting a ready Engine and running Jesse/PostgreSQL containers while PostgreSQL health remained `unhealthy`. The launcher exited 1 and logged its explicit PostgreSQL timeout in 6.69 seconds (the test copy reduced the timeout to 5 seconds). Production Compose, port 8767, Docker state and market data were untouched. Evidence: `evidence/test_outputs/launcher_postgres_failure_20261106.json`; log: `evidence/logs/launcher-postgres-unhealthy-20261106.log`.

## MT5 importer UI retry — 2026-11-06

The portable MT5 test copy was launched with `/portable`; its `common.ini` contains no login key and its copied CSV remains under the test copy. Computer Use again returned the unrelated DrainSim window for the MT5 window-state capture. No UI action was taken after the mismatch. Import execution remains NOT TESTED; no trade/order or source-data change occurred. Evidence: `evidence/test_outputs/mt5_importer_computer_use_retry_20261106.json`.

## MT5 importer UI retry — 2026-11-07

Computer Use identified the portable process path, but the returned UI state showed the separate `C:\Program Files\MetaTrader 5 IC Markets Global\terminal64.exe` window with a brokerage account. No action was taken in that window and no script was executed. Importer verification remains NOT TESTED. No trade/order or source-data change occurred. Evidence: `evidence/test_outputs/mt5_importer_computer_use_retry_20261107.json`.

## MT5 importer command-line retry — 2026-11-07

A clean run copy was created from the disposable MT5 test copy, excluding its broker-history `Bases` and old logs. The config supplied only `[StartUp]` script/symbol/period fields and no account login fields. MT5 build 6230 logged the importer script as loaded on three attempts, including a debug build compiled with 0 errors/0 warnings, but no `P0_IMPORT_OK` marker or custom-symbol data appeared after 120 seconds. The two exact audit-copy processes were stopped; the original IC Markets terminal remained running and untouched. No trades or orders were attempted. Importer execution remains NOT TESTED. Evidence: `evidence/test_outputs/mt5_importer_config_run_20261107.json`; terminal log: `evidence/logs/mt5-importer-config-run-20261107.log`.

## Fresh regression and Docker failure checks — 2026-11-07

The Strategy Lab regression suite passed 71/71; `compileall`, JavaScript syntax and live `/api/health` passed. Health reports `C:\AI\Dukascopy_data`, `source_exists=true`, `mode=local-only`. An isolated launcher copy with a failing Docker CLI and Docker Desktop unavailable exited cleanly with the expected logged error. Evidence: `evidence/test_outputs/current_regression_20261107.json` and `evidence/test_outputs/launcher_docker_unavailable_20261107.json`.

## Jesse service-start failure check — 2026-11-07

A disposable launcher copy with a Docker shim verified the failure path when Docker is healthy but Jesse/PostgreSQL cannot start. The launcher surfaced the exact Compose-start error and exited nonzero; production Docker and port 8767 were untouched. Evidence: `evidence/test_outputs/launcher_jesse_start_failure_20261107.json`.

## MT5 importer Computer Use retry — 2026-11-07 (isolated copy)

The user authorized another Computer Use attempt. The isolated portable MT5 copy started with no login fields, but after selecting its exact process/window (`C:\AI\MT5_P0_Importer_Interactive_20261107\terminal64.exe`, window title `Netting - BTCUSD,M1`), the screenshot and accessibility state resolved to File Explorer (`MT5_P0_Importer_Test - Verkenner`). No MT5 control was clicked and no account UI was used. The isolated process was stopped. Importer execution remains **NOT TESTED**; the original IC Markets terminal, original MT5 history, and Dukascopy source were untouched. Evidence: `evidence/test_outputs/mt5_importer_computer_use_retry_20261107b.json`.

## Current desktop shortcut and live app readback — 2026-11-07

The configured Windows Desktop resolves to `C:\Users\Roanv\Desktop`; `Dukascopy Strategy Lab.lnk` exists and targets `wscript.exe` with `launch-hidden.vbs`, the project working directory, and the 4096 px project icon. The live readback found exactly one listener on 127.0.0.1:8767, one `python -m strategy_lab.server` process, one Edge app window titled `Dukascopy Strategy Lab`, zero visible Docker Desktop main windows, and `/api/health` status `ok` with source root exactly `C:\AI\Dukascopy_data`. Evidence: `evidence/test_outputs/desktop_shortcut_current_state_20261107.json`. This is a running-state check, not a new cold-start or mouse-double-click test.

## Tester tabs and parameter entry — 2026-11-07

Browser automation exercised all eight tester workspaces (Overzicht, Invoerparameters, Optimalisatie, Resultaten, Trades, Grafiek, Inspector, Journaal), Market/Strategie settings, and reload persistence. Nine strategy parameters were exposed across core and advanced groups; optimizer bounds displayed Start/Stop/Stap. No run was launched. Screenshots and structured evidence: `evidence/screenshots/strategy-tester-overview-20261107.png`, `evidence/screenshots/strategy-tester-inputs-20261107.png`, `evidence/test_outputs/strategy_tester_tabs_browser_20261107.json`.

The four focused validation reports are `STRATEGY_TESTER_UI_REPORT.md`, `PARAMETER_GRID_VALIDATION.md`, `BACKTEST_SETTINGS_VALIDATION.md`, and `FINANCIAL_METRICS_VALIDATION.md`. These distinguish tested UI/engine behavior from still-unverified broker metrics and unsupported robustness analyses.

## Trade MAE/MFE — 2026-11-07

The Trades tab now includes MFE and MAE columns with hover explanations. The API derives raw-price excursions from the saved run candles only when the run manifest points to the approved Dukascopy root. The calculation includes a candle only for a recorded MARKET fill at that exact candle open; it excludes pending-order fill candles and all exit candles. The live-local calculation returned values for 13/13 trades in Duka XAUUSD M1 run `9adfb4a4-1b3c-4bca-b7f1-9cbb01f8f6f4`; 72/72 Strategy Lab tests passed, and JS syntax plus Python compile checks passed. These price-unit values are not account-currency P&L or broker validation. See `evidence/test_outputs/mae_mfe_dukascopy_20261107.json`.
The isolated local UI check also displayed all 13 trade rows with both price-excursion columns and verified the hover explanation. Screenshot: `evidence/screenshots/mae-mfe-trades-dukascopy-20261107-viewport.png`. The production port 8767 process was left running because the execution environment rejected the process-stop command; the new code is verified in tests and the isolated instance but has not yet been loaded into the existing production server process.

## MT5 importer follow-up — 2026-11-07 (portable non-interactive copy)

A further run used only `C:\AI\MT5_P0_Importer_Interactive_20261107`. The copied Dukascopy CSV has 174,475 strictly increasing rows (epoch 1775001600–1790553540) and SHA-256 `A2573F...B677B510`; the project importer source hash remained unchanged. A test-only instrumented script without the input dialog compiled with 0 errors and 0 warnings. MT5 again logged only that the script loaded; no import marker or verified custom bars appeared. The test process was stopped. Importer execution remains **NOT TESTED**. No account login, orders, trades, original MT5 history writes, or raw Dukascopy writes occurred. Evidence: `evidence/test_outputs/mt5_importer_noninteractive_retry_20261107.json`.

## Live app code freshness — 2026-11-07

The production health endpoint remains `ok` and points only to `C:\AI\Dukascopy_data`. The active server PID 17816 predates the current MAE/MFE endpoint code; the live 13-trade API response does not contain `mfe_price`, while the code and 72-test suite do. Docker shows only the Jesse and PostgreSQL services, and `docker top jesse` shows the normal service processes, with no one-off backtest runner. I left the existing server process untouched; the app must be safely reloaded before claiming the new MAE/MFE fields are live. Evidence: `evidence/test_outputs/live_app_staleness_20261107.json`.
# MT5 importer retry — 2026-11-07

The UI-driven attempt used the isolated portable test copy and the Dukascopy-only CSV copy. The terminal's Journal confirmed the importer script loaded, but no importer stage output, marker file, custom symbol, or imported bar count was produced. This remains **NOT TESTED**; compile success and script loading do not prove import functionality. MT5 MCP initialization against the portable copy timed out. The disposable terminal was closed and the original MT5 installation/data were left untouched. See `evidence/test_outputs/mt5_importer_computer_use_retry_20261107c.json`.


## MT5 importer Computer Use retry — 2026-11-07

The isolated portable MT5 copy loaded the importer script, but Computer Use found no output in Experts and no import-stage records in Journal beyond the load confirmation. The test copy produced no `import_marker_*` files or verified custom-symbol bars. A portable-terminal MT5 MCP initialization timed out. The isolated process exited; no original MT5 data or Dukascopy source was changed. The importer remains **NOT TESTED**. Evidence: `evidence/test_outputs/mt5_importer_computer_use_retry_20261107c.json`.


## Final P0 reconciliation — 2026-11-07

The current local build has now been checked at `127.0.0.1:8767`: backend build `33557689c9a7a8e37030fc2fde2eb9085ee900a4385151b10792a08d76289c65` matches disk, frontend build `9c86cfab23416a396e89fd791282cd571aac4596e1df693c5297306cf72941c2` matches the served UI, health is `ok`/`local-only`, and the only configured market-data root is `C:\AI\Dukascopy_data`. Current regression: 76 passed; JS syntax and Python compileall passed. The live run API now includes MFE and MAE for all 23 trades in Duka run `c2239cb4-4ed9-4e54-a356-8121d850ebff`. This supersedes the earlier stale-process note above (PID 17816); the checked process is PID 28408.

Replay trade markers and all five auto-speeds were visually verified with bullish and bearish Duka runs; future entry/exit marks stay hidden until the cursor reaches them. See `REPLAY_VALIDATION.md`. A current dashboard end-to-end run and readback are documented in `STRATEGY_TESTER_E2E_RESULTS.md`; build evidence: `CURRENT_BUILD_VALIDATION.md`.

The audit is complete for locally executable P0 scope. Individual residual blockers remain explicit: arbitrary-strategy state re-execution, Jesse MCP-specific calls (connector unavailable), MT5 importer runtime (both importer and minimal OnStart probe fail initialization in the isolated terminal), exact MT5 CFD parity, and production-wide offline/reboot verification. These are not passed or silently waived. No live/paper trades or changes to original MT5/Dukascopy data.

## V7 generic debug/replay addendum — 2026-11-07

The earlier “generic causal state reconstruction ... BLOCKED/NOT TESTED” statement is superseded for Python strategies that implement the `InspectorMixin` standard contract. The local API has recomputed and matched decisions for `LabBreakout`, `StatefulRsiPending`, and `MultiTimeframeReplayProbe`; future-OHLC mutation passed for 216 Stateful and 901 decisions for each of H1/H4/D1. This does not cover arbitrary uninstrumented Python files, simultaneous supplemental feeds, multi-symbol feeds, or every-candle Stateful trace parity. Jesse MCP-specific calls remain blocked only at the connector boundary; MT5 importer experiments remain optional/deferred and are not part of acceptance.

Latest current-build check: server PID 5392 reports backend current; served frontend ID matches; health is `ok` and `local-only`; only `C:\AI\Dukascopy_data` is the configured source root. The desktop-launcher log confirms it refreshed the existing app window, with Docker Desktop hidden and both Jesse services ready. H1/H4/D1 routes each passed causal replay and a 901-decision future-mutation test. Strategy Lab suite: 82 passed; Python and Jesse-container compileall, JS syntax, and 23 chart-overlay assertions passed. Dukascopy Bid/Ask source hashes match the run manifests. Full readback: `evidence/test_outputs/p0_current_validation_20261107.json`.

Latest runtime verification: 80 Strategy Lab tests passed; JS syntax and Python compileall passed; `/api/health` and `/api/version` report current backend and frontend, `local-only`, and source root exactly `C:\AI\Dukascopy_data`; both XAUUSD source-file SHA-256 values match their cached manifest values. Docker Jesse is up and PostgreSQL is healthy. Evidence: `evidence/test_outputs/v7_current_runtime_check_20261107.json`.

## Replay chart and lifecycle cursor check — 2026-11-07

- Chart overlay regression: **23 assertions passed** for bullish/bearish entry triangles, per-trade dotted entry-to-exit connectors, TP/SL markers and hiding future trades/exits during replay. Earlier Computer Use evidence provides the visual check; the current pass refreshed the existing single desktop app via the launcher, with backend build current and frontend matching.
- A real bug was fixed: an open trade force-closed by Jesse when a causal prefix ends was included in the closed-trade count at an earlier replay cursor. Counts now use each trade's actual `closed_at` and expose prefix cleanup separately.
- Live Duka causal checkpoints at entry signal, fill, TP and pending expiry matched the saved decisions; no future candles were supplied. At fill, closed-at-cursor is 0 and prefix cleanup is 1; at TP it is 1 and cleanup is 0.
- Final regression: **85 passed**, Python compileall PASS, Jesse-container compileall PASS, JavaScript syntax PASS. Evidence: `evidence/test_outputs/stateful_replay_lifecycle_checkpoints_20261107.json`, `evidence/test_outputs/p0_current_validation_20261107.json`.

## Bid/Ask callback index regression — 2026-11-07

The generic quote-signal callback now uses a Jesse-compatible strategy index beginning at zero on the first trading candle; `history_index` retains the index in the warm-up-inclusive candle stream. This fixes scheduled signal drift after warm-up and aligns cooldown state. The Duka StatefulRsiPending callback replay matched 1,379/1,380 saved candle decisions (one explicit Ask-vs-Bid RSI threshold difference), produced 9 quote-model trades, and preserved source hashes. Current server PID 22084 is build-current, frontend-matched, local-only, and idle. Strategy Lab suite: 88 passed; chart overlay assertions: 23 passed. Evidence: `evidence/test_outputs/stateful_bidask_quote_index_regression_20261107.json`.

## Portfolio dashboard and end-of-turn runtime check — 2026-11-07

Portfolio analysis is now implemented and tested; the earlier partial/not-implemented statements in this report are superseded. The current local service (PID 15800) reports health `ok`, `local-only`, backend current, served frontend current, 0 active runs, and source root exactly `C:\AI\Dukascopy_data`. Docker Jesse is running and PostgreSQL healthy. Full Strategy Lab regression: 93 passed; `node --check strategy_lab/static/app.js`, 23 replay overlay assertions, and Python compileall passed.

Computer Use opened Results → Portfolio, selected two saved Duka StatefulRsiPending runs, and visibly rendered aggregate equity, drawdown, strategy/instrument/month and correlation tables. Compatible saved LabBreakout runs also passed the live API calculation. An incompatible mixed signal-mode request returned `COMPATIBILITY_BLOCKED` and no combined net P&L. See `PORTFOLIO_ANALYSIS_STATUS.md` and the three `evidence/test_outputs/portfolio_analysis_*_20261107.json` files.

The audit deliverables are reconciled for all locally executable P0 scope. Remaining isolated items are explicitly blocked/limited: Jesse MCP connector unavailable; quote-side adapters for strategies that do not implement the explicit callback contract (unsupported by design; only MCP-only work is blocked); broker-verified MT5 CFD parity (cannot be established from OHLC alone without broker contract/tick ground truth); production-wide offline reboot/restart (deferred; not required for this development round); and OPTIONAL / DEFERRED MT5 importer (isolated terminal failed to initialize even a minimal script; per scope, no further attempts). No live/paper trades were executed and the original Dukascopy and MT5 data were not changed.


