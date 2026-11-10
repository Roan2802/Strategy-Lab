# Trade Inspector and replay tests

## Duka run under inspection

`85808ac8-84eb-478e-a405-7ba41a11f56d` is LabBreakout on XAUUSD M1, 2026-09-14 UTC. Its input is 1,380 Dukascopy candles and its event stream has 1,432 events: 1,380 candle decisions and 13 each of submitted, opened, cancelled, and closed lifecycle events.

## Browser checks

- Opened the run in the ChatGPT in-app browser at `http://127.0.0.1:8767/`; the UI identifies its only price root as `C:\AI\Dukascopy_data`.
- The 13-trade table showed side, UTC entry time, entry/exit prices, Jesse P&L, and a Spring action. Selecting a trade moved the replay cursor to its entry and limited visible candles/events to that point.
- Selecting a chart candle populated the inspector with Dukascopy Bid OHLC, paired Ask/spread information, strategy checks and event lifecycle. PASS/FAIL/SKIP filters are present.
- Replay stepping was checked in both directions: 00:00 UTC → 00:01 UTC (1 → 2 visible candles) → 00:00 UTC (1 candle). The page reported that future candles were not sent by the API.
- Run selection, A/B run comparison, optimizer controls, preset save/reload, and trial-parameter loading were exercised. Browser console showed no application errors during the successful regression flow.
- Static integrity check on the persisted Duka event stream found 1,432/1,432 timestamps, zero duplicate event IDs, and the expected per-type counts; JSON evidence is `evidence/test_outputs/dukascopy_event_integrity.json`.

## Limits

Inspector explanations depend on strategy-emitted structured checks; it does not reconstruct every arbitrary Python condition. Replay now uses Jesse's exact executed order timestamps for position opens/closes and excludes events that occur after the selected cursor. It still does not rerun arbitrary strategy state/indicators at each cursor. The ID linker joins a submission to a trade through the real entry order ID and matching signal timestamp, with ambiguous matches left unlinked. Duka run `dac05432-695b-4393-b0a0-6e43ac739c44` proves 7/7 submit→open→close links; 7/7 opens and 7/7 closes match Jesse order execution timestamps; zero unmatched lifecycle timestamp reconciliations. There were 13 expiry cancel requests and all 13 order IDs matched a stable cancellation event. A separate ID-bearing cancellation occurred at the last dataset timestamp without a matching request event. Evidence: `evidence/test_outputs/dukascopy_lifecycle_audit_20261106.json`. `POSITION_OPEN.trade_id` remains absent, but its actual entry `order_id` joins to the closed trade order list. UI verification covers this named Duka run, not every strategy or symbol.

Screenshot: `evidence/screenshots/dukascopy-xauusd-m1-final-regression.png` (PNG, 680×390). The full app viewport is not included in that chart-only export.

## UI-redesign note (2026-11-05)

The Inspector remains in the resizable dashboard and its existing candle/event behavior is preserved. Layout profile and panel hide/restore were verified in the ChatGPT in-app browser. Chrome/Edge comparisons remain blocked because those browser surfaces were unavailable.

The redesigned chart also rendered the existing Dukascopy run's entry/exit markers and stop-loss/take-profit event levels. The later Duka event-link regression closes the signal→entry→trade-close path described above; cancellation linkage remains open.

## Lifecycle join follow-up — 2026-11-06

- `event_links()` now resolves the ID-less `ORDER_SUBMITTED` event through a verified `POSITION_OPEN.order_id` found in the actual Jesse trade's order list, then bridges by exact signal timestamp and same symbol. It joins only when exactly one submission matches; ambiguity is reported and left unlinked.
- On Dukascopy run `1d35af9c-9908-4dff-943a-78ac18c45a67`, all 13/13 trades linked `ORDER_SUBMITTED` → `POSITION_OPEN` → `POSITION_CLOSE`; zero ambiguous joins. Source manifest: `C:\AI\Dukascopy_data`. Evidence: `evidence/test_outputs/dukascopy_event_link_audit_20261106.json`.
- Cancellation events remain incomplete: Jesse's `on_cancel` hook sees no canceled order IDs. No ID is inferred for canceled orders.
- `python -m pytest -q strategy_lab/tests` → 16 passed; new tests cover the unique bridge and refusal of ambiguous matches.

## Inspector de-duplication and trade navigation — 2026-11-06

The Inspector API previously concatenated timestamp matches and signal-timestamp matches, duplicating events whose two timestamps were the same. It now selects each event once. **Spring** now uses the actual entry order ID to identify the matching POSITION_OPEN and set replay to its signal timestamp. Browser verification on the first trade of Duka run `1d35af9c-9908-4dff-943a-78ac18c45a67` landed at `2026-09-14T01:00:00Z`; one ORDER_SUBMITTED and one POSITION_OPEN card were shown. Strategy Lab suite: 17 passed.

## P1 lifecycle and causal replay regression — 2026-11-06

- Duka-only run `dac05432-695b-4393-b0a0-6e43ac739c44`: 7 trades, 1,449 events, source root `C:\AI\Dukascopy_data`.
- All 7 position-open and all 7 position-close event timestamps now match the exact Jesse order `executed_at`; before correction, all 14 logs were 60 seconds early. The prior observed event timestamp is preserved as `strategy_observed_timestamp_ms`.
- Pending-order expiry now stores only Jesse orders where `is_cancellable` is true, before Jesse removes them. All 13 expiry request IDs matched an `ORDER_CANCELLED` event with the same stable order ID. One additional ID-bearing cancellation occurs at the final data timestamp without a request event and remains separately identified.
- Causal API + ChatGPT-browser check: first entry signal `07:36Z`, actual fill `07:52Z`. At 07:36, Inspector showed the candle decision and one order submission with no position-open event. At 07:52, the position-open event appeared. Future fill information was excluded from the signal-candle Inspector.
- Evidence: `evidence/test_outputs/dukascopy_lifecycle_audit_20261106.json`.

## P1 lifecycle verification — 2026-11-06

On Duka-only run `dac05432-695b-4393-b0a0-6e43ac739c44`, 7/7 opens and 7/7 closes reconcile by exact Jesse order IDs and `executed_at`; 13/13 expiry cancellation IDs match their cancel events. One additional terminal-time cancel ID has no request event; later log-order analysis classified it as Jesse's expected backtest teardown. Inspector signal/fill separation was verified in ChatGPT IAB at 07:36Z and 07:52Z. Strategy Lab: 18 tests passed; Jesse Docker core subset: 34 passed. See `evidence/test_outputs/dukascopy_lifecycle_audit_20261106.json` and `evidence/test_outputs/dukascopy_lifecycle_teardown_classification_20261106.json`.

## Replay look-ahead test boundary — 2026-11-06

Added a unit test that changes only a future candle and verifies the replay API's cursor-visible price prefix and recorded event list are unchanged. This proves the replay slicing boundary for stored candles/events. It does not replay arbitrary Python strategy code, recompute historical indicators or restore state that a strategy did not record; those remain partial/unverified.

## Dashboard Bid/Ask overlay — 2026-11-06

ChatGPT browser verified the output labels and warnings on a Dukascopy-only 7-trade run: provisional Jesse repricing -238.73 USD; separate Bid/Ask quote replay -208.16 USD; complete quotes; 7/7 quantities violate the explicitly entered 0.01 lot-step assumption. UI states contract not broker-verified and signals not recomputed. This flags an actionable limitation: contract units/quantity mapping needs validation before monetary results can be interpreted. See `evidence/test_outputs/dukascopy_execution_ui_status_20261106.json`.

## Generic instrumented strategy Inspector — 2026-11-06

`ExampleStrategy` was run through the local Strategy Lab on Dukascopy XAUUSD M1 for 2026-09-14 (run `441bc513-e23e-4223-8be2-018c8b2752c5`). The event stream contains 1,380 `CANDLE_DECISION` events and 11 each of `ORDER_SUBMITTED`, `POSITION_OPEN`, and `POSITION_CLOSE`. In ChatGPT IAB, **Spring** on the first trade selected its 2026-09-14 01:00 UTC signal candle. The Inspector showed Dukascopy Bid/Ask values, PASS checks for schedule, bullish candle, and position availability, and one `ORDER_SUBMITTED`; the next-minute fill was not shown as if it existed at the signal time. This verifies the `InspectorMixin` contract for this second, independent example strategy only. Evidence: `evidence/test_outputs/dukascopy_example_strategy_inspector_20261106.json` and `evidence/test_outputs/dukascopy_example_strategy_inspector_ui_20261106.json`.

## Full local symbol/timeframe workflow — 2026-11-06

All 56 combinations of eight Dukascopy symbols and M1–D1 completed through the local data/route/backtest workflow for one UTC day after the dashed-pair mapping fix. Every manifest was checked for a source path under `C:\AI\Dukascopy_data`. Many higher timeframe runs had zero trades, so this is not strategy-performance or wide-range resampling validation. See `evidence/test_outputs/dukascopy_symbol_timeframe_matrix_20261106/matrix_final.json` and `.csv`.

## LabBreakout full-month run selection and Spring regression — 2026-11-06

On the UI-triggered September run `6428325a-599b-4e94-a459-83d462c6b897`, the first trade entered at 2026-09-01 02:01 UTC. Initially Spring could not reach it because replay bounds still reflected the prior 2026-09-14 chart. The run selector now loads manifest date/symbol/timeframe/strategy/settings before the chart. Re-test in ChatGPT IAB: Spring landed on 02:00 UTC, showing Dukascopy Bid/Ask quote and `PASS ENTRY_CONDITIONS_MET` plus `ORDER_SUBMITTED`; no future fill event was present. One forward M1 step landed on 02:01 UTC and showed `POSITION_OPEN`. Evidence: `evidence/test_outputs/dukascopy_ui_fullmonth_spring_replay_fix_20261106.json`. This validates this run's logged event cutoff and navigation; arbitrary Python strategy-state replay remains unverified.

## Replay entry/exit markers — 2026-11-07

Replay was checked on a 13-trade long run and a separate 28-trade short run, using manifests rooted only at `C:\AI\Dukascopy_data`. Bullish entries use upward triangles; bearish entries use downward triangles. TP and SL exits have distinct markers, and each trade has its own dotted segment from its entry timestamp/price to its exit timestamp/price. Cursor cutoff hides future entries, exits, and connectors. The bearish spring check showed exactly one connector at its replay cursor while later trade paths remained hidden. Evidence: `evidence/test_outputs/replay_trade_markers_20261107.json`.
