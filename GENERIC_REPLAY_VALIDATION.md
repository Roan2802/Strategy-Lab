# Generic causal replay validation

Status: **PASS for LabBreakout, StatefulRsiPending, and H1/H4/D1 supplemental-feed probes on Dukascopy XAUUSD M1, 2026-09-14.** The result is Jesse research-model evidence, not broker validation.

The dashboard endpoint `POST /api/runs/{run_id}/causal-replay` verifies the saved run's source root, strategy SHA-256, Bid/Ask source SHA-256 values and dataset fingerprint. It loads only `C:\AI\Dukascopy_data`, supplies warm-up plus candles through the selected cursor to Jesse, and terminates the backtest at the next candle boundary. It returns the re-executed candle decision, lifecycle events at the cursor, exact prefix count and comparison with the original event. `future_candles_supplied` is false. It is a fresh Jesse prefix execution, not a hand-copied Python strategy adapter.

The existing `/api/replay` path remains an explicitly labelled recorded-event view (`RECORDED_EVENT_REPLAY`). The prefix endpoint labels a debug-contract strategy `FULL_CAUSAL_REPLAY`; if a strategy produces only persisted events it reports `RECORDED_EVENT_REPLAY`; no trace yields `UNSUPPORTED`. Replay UI exposes the prefix action from the Entry Inspector.

## Actual runs

| Strategy | Duka run | Cursor (UTC) | Result |
|---|---|---|---|
| StatefulRsiPending | `5e51591d-e963-44e4-9fe0-38b7fa36cb8f` | 2026-09-14 01:00Z | `FULL_CAUSAL_REPLAY`, 271 prefix candles, original decision matched |
| LabBreakout | `c2239cb4-4ed9-4e54-a356-8121d850ebff` | 2026-09-14 01:00Z | `FULL_CAUSAL_REPLAY`, 271 prefix candles, original decision matched |
| MultiTimeframeReplayProbe (H1) | `02cff6de-ef4a-4f64-a1ae-a4213e826375` | 2026-09-14 15:00Z | `FULL_CAUSAL_REPLAY`, 8,101 prefix rows, H1 EMA and state matched |
| MultiTimeframeReplayProbe (H4) | `70be83f2-884e-49ac-83a6-80d5bfb90d71` | 2026-09-14 15:00Z | `FULL_CAUSAL_REPLAY`, 8,101 prefix rows, H4 EMA and state matched |
| MultiTimeframeReplayProbe (D1) | `f3d00cb6-2fbb-4eb7-beda-cbb9f886ce52` | 2026-09-14 15:00Z | `FULL_CAUSAL_REPLAY`, 10,981 prefix rows, D1 EMA and state matched |

Both runs use the pair files under the approved Dukascopy root. The first strategy produced 11 closed trades over 1,380 trading candles. Both saved API responses are in `evidence/test_outputs/generic_replay_stateful_rsi_20261107.json` and `evidence/test_outputs/generic_replay_labbreakout_20261107.json`. For both strategies the selected decision was recomputed and matched the original; no future candles were supplied.

The MTF probe declares an additional Jesse `data_route` sourced from the same Dukascopy M1 stream. It ignores a forming higher-timeframe candle until its close boundary is observable. The execution warmups were aligned to each route (7,200 M1 rows for H1/H4; 10,080 for D1). Each route is a separate one-day Duka run on a single M1 trading symbol; multi-symbol feed combinations have not been validated.

Look-ahead mutation: **PASS** for StatefulRsiPending and the M1→H1/H4/D1 probes. StatefulRsiPending changed all future OHLC values in a temporary copy and compared 216 pre-cursor decisions; traces were identical, no post-cursor events were emitted, and source-input hashes were unchanged. For each MTF timeframe, all OHLC fields in the same 479 post-cursor rows were raised by 10,000; all 901 prefix decisions, including the corresponding EMA and state, remained identical. Each replay emitted zero post-cursor events and preserved the source hash. Evidence: `evidence/test_outputs/generic_replay_future_mutation_20261107.json` and `evidence/test_outputs/generic_replay_mtf_{h1,h4,d1}_future_mutation_20261107.json`.

Dashboard Computer Use: **PASS**. Microsoft Edge ran a Duka-only StatefulRsiPending backtest (run `5e51591d-e963-44e4-9fe0-38b7fa36cb8f`; 1,380 M1 candles, 11 trades, 1,425 events). The chart displayed the entry triangle, TP/SL markers, and dotted trade connectors with maximum points set to unlimited. Replay speed options 1/3/5/10/25 candles per second were present; at 25/sec the visible cursor advanced automatically from 1 to 226 candles, then replay was stopped. Machine-readable UI evidence: `evidence/test_outputs/cua_dashboard_replay_20261107.json`.

The Inspector replay button was then clicked for the 2026-09-14T03:45:00Z selected candle. It returned `FULL_CAUSAL_REPLAY`, reported the original decision matched, showed `SKIP · ENTRY_CONDITION_SKIPPED`, and reported a 436-candle prefix with no future candles supplied. This verifies both the endpoint and its UI path.

## Known boundaries

- Jesse MCP calls are **BLOCKED** because the server is unavailable; the local API exercised the same backend runner.
- Supplemental timeframe feeds currently require an M1 execution route. H1, H4, and D1 individually passed same-symbol future-mutation tests; simultaneous multiple supplemental feeds and multi-symbol routes remain **NOT TESTED**.
- Prefix event parity for arbitrary Python strategies is compared at the selected candle. Exhaustive parity across every candle is proven for the dedicated LabBreakout all-candle check (1,380/1,380); it remains **NOT TESTED** for StatefulRsiPending.
- Intrabar tick order and broker-specific execution remain outside the evidence.

## Cursor-boundary lifecycle regression — 2026-11-07

The fill checkpoint exposed a cursor accounting bug: Jesse finalizes an open position when the truncated prefix ends, so counting all returned trades incorrectly marked that position as closed at an earlier fill cursor. `closed_trade_count_at_cursor` now counts only trades whose actual `closed_at` is at or before the cursor; later prefix-finalized trades are reported separately as `prefix_teardown_trade_count`. A Duka-only `StatefulRsiPending` run was replayed at entry signal, fill, take-profit, and pending expiry. Every decision matched the saved decision, no future candles were supplied, the fill cursor had 0 closed trades and 1 prefix teardown, TP had 1 closed trade, and expiry retained its cancel events. Evidence: `evidence/test_outputs/stateful_replay_lifecycle_checkpoints_20261107.json`.

Replay chart trade overlays were also regression-checked: bullish and bearish triangles, each trade's own dotted entry-to-exit connector, TP/SL exit markers, and suppression of future entries/exits at the replay cursor are covered by 23 assertions in `strategy_lab/tests/test_chart_replay_overlays.js`. Existing visual verification of the chart and 1/3/5/10/25 candle-per-second replay is recorded in `evidence/test_outputs/cua_dashboard_replay_20261107.json`; this pass refreshed the existing desktop app through its launcher but did not capture a new UI screenshot.
