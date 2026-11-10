# Current build validation — 2026-11-07

- Local API: `http://127.0.0.1:8767/`; health `ok`, mode `local-only`, source root `C:\AI\Dukascopy_data`.
- Backend build: `33557689c9a7a8e37030fc2fde2eb9085ee900a4385151b10792a08d76289c65`; disk build matches and `backend_current=true`.
- Frontend build: `9c86cfab23416a396e89fd791282cd571aac4596e1df693c5297306cf72941c2`; query reports `frontend_matches=true`.
- Server process: PID `28408`, started `2026-11-07T16:32:01.164921+00:00`; 0 active runs at check. Docker Jesse and healthy PostgreSQL services were running.
- Fresh verification: 76 Strategy Lab tests passed; JavaScript syntax and Python compileall passed.
- Live API readback of Duka run `c2239cb4-4ed9-4e54-a356-8121d850ebff` returned 23 trades and 1,472 events; MFE and MAE values were present for all 23 trades.
- Evidence: `evidence/test_outputs/current_build_validation_20261107.json`.

No live/paper trading was performed. Trade P&L is a provisional Jesse research-model result, not broker-validated.

## 2026-11-07 quote replay and chart follow-up

Current source verification after the quote-RSI and replay-chart changes: Strategy Lab suite **82 passed**; Python compileall, JavaScript syntax, chart replay regression (9 assertions), and Jesse-container compileall passed. The Duka StatefulRsiPending Bid/Ask rerun preserved 1,378/1,380 saved-decision matches and 10 model trades; see `evidence/test_outputs/stateful_bidask_rsi_precompute_dukascopy_20261107.json`.

The currently running server is PID 15188 with 0 active runs and reports `safe_to_restart=true`, but `backend_current=false`. Its asset endpoint serves the updated chart code. The Windows launch/restart command was rejected by the execution policy, and Computer Use could not establish the browser URL for a refresh. Therefore the source/tests are current, while the live backend and visual browser refresh are **NOT VERIFIED**; do not claim the complete P0 goal is deployed or complete yet.

## Superseding runtime check — 2026-11-07 20:34 local

The stale-process status above is superseded. Current server PID `5392` reports `backend_current=true`; the served page frontend ID matches (`frontend_matches=true`). Health is `ok`, mode `local-only`, source root `C:\AI\Dukascopy_data`, 0 active runs. Docker Jesse is running and PostgreSQL is healthy. Current M1→H1/H4/D1 runs and the three 901-decision future-mutation checks are documented in `GENERIC_REPLAY_VALIDATION.md`. Suite 82/82, Python compileall, Jesse-container compileall, JavaScript syntax, and 23 replay-overlay assertions passed. Both source CSV SHA-256 values still match the saved Dukascopy manifest. Machine evidence: `evidence/test_outputs/p0_current_validation_20261107.json`.

Earlier Computer Use evidence verifies bullish and bearish markers, per-trade dotted connectors, unlimited chart points, and all five replay speeds. A fresh UI refresh attempt in this pass was stopped by Computer Use because it could not confirm the browser URL; no new visual claim is made for that attempt.

## Superseding runtime check — 2026-11-07 (lifecycle correction)

The current server PID 31472 runs backend build `8e56fe7d21759cac39fe0fe0412e0a50a5dae35bb265cada0d3b0ea5bbf2ffcc`; `backend_current=true`, `frontend_matches=true`, health is `ok`, and active runs are 0. Launcher log `C:\Users\Roanv\AppData\Local\DukascopyStrategyLab\logs\startup-20261107-205718-39500.log` confirms refresh of the single existing app window and local source `C:\AI\Dukascopy_data`. Strategy Lab tests: 85 passed. Live causal entry/fill/TP/expiry checkpoints pass; the fill-time open position is no longer counted as closed by prefix finalization. See `GENERIC_REPLAY_VALIDATION.md` and `evidence/test_outputs/stateful_replay_lifecycle_checkpoints_20261107.json`.

## Quote-side callback index correction — 2026-11-07

Current server PID 22084 runs backend `006a4fc245d1294e76fb045ffa22c805a8e23f1e715ebde039bd726ef09fedc8`; served frontend matches, health is `ok`, mode is `local-only`, source root is `C:\AI\Dukascopy_data`, and active runs are 0. The desktop-launcher log confirms it refreshed the existing single app window. Strategy Lab: 88 passed; host and Jesse-container compile, JS syntax and 23 chart-overlay assertions passed. A current Duka quote-side replay of `StatefulRsiPending` matched 1,379/1,380 decisions; original Bid/Ask source hashes remain unchanged. See `BIDASK_EXECUTION_VALIDATION.md` and `evidence/test_outputs/stateful_bidask_quote_index_regression_20261107.json`.

## Portfolio dashboard and end-of-turn runtime check — 2026-11-07

Portfolio analysis is now implemented and tested; the earlier partial/not-implemented statements in this report are superseded. The current local service (PID 15800) reports health `ok`, `local-only`, backend current, served frontend current, 0 active runs, and source root exactly `C:\AI\Dukascopy_data`. Docker Jesse is running and PostgreSQL healthy. Full Strategy Lab regression: 93 passed; `node --check strategy_lab/static/app.js`, 23 replay overlay assertions, and Python compileall passed.

Computer Use opened Results → Portfolio, selected two saved Duka StatefulRsiPending runs, and visibly rendered aggregate equity, drawdown, strategy/instrument/month and correlation tables. Compatible saved LabBreakout runs also passed the live API calculation. An incompatible mixed signal-mode request returned `COMPATIBILITY_BLOCKED` and no combined net P&L. See `PORTFOLIO_ANALYSIS_STATUS.md` and the three `evidence/test_outputs/portfolio_analysis_*_20261107.json` files.

The audit deliverables are reconciled for all locally executable P0 scope. Remaining isolated items are explicitly blocked/limited: Jesse MCP connector unavailable; quote-side adapters for strategies that do not implement the explicit callback contract (unsupported by design; only MCP-only work is blocked); broker-verified MT5 CFD parity (cannot be established from OHLC alone without broker contract/tick ground truth); production-wide offline reboot/restart (deferred; not required for this development round); and OPTIONAL / DEFERRED MT5 importer (isolated terminal failed to initialize even a minimal script; per scope, no further attempts). No live/paper trades were executed and the original Dukascopy and MT5 data were not changed.


