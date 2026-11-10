# Strategy developer guide

## Add a strategy

1. Create `runtime/strategies/<StrategyName>/__init__.py` with one Jesse `Strategy` subclass whose class name matches the folder.
2. Define typed `hyperparameters()` entries and add `parameter_metadata.json` for labels, units, help text and optimizer constraints.
3. Subclass `InspectorMixin`; compute indicators once, use `debug_candle()` in `before()`, and record each order/position lifecycle hook.
4. Guard order logic against live trading. Strategy Lab's runner itself submits only offline research backtests to the local Jesse service.
5. Restart through `start-local.ps1` / the `Dukascopy Strategy Lab` shortcut so the backend hash and container-mounted source are current.
6. Select the strategy in the dashboard, choose an available instrument/timeframe/date range, set the Market and Strategy tabs, then run a backtest.
7. Inspect the trade table and chart. Click a candle or trade to open exact recorded checks/state. The Inspector's causal replay button reruns the strategy through that candle; compare status and the decision-match result.
8. Open Optimization, set parameter domains and train/holdout split, run bounded trials, then select/re-run a trial to verify its exact parameters.

## Examples

- `runtime/strategies/ExampleStrategy` is a minimal long-only scheduled candle-body example.
- `runtime/strategies/StatefulRsiPending` demonstrates an RSI indicator, stop-entry, expiry cancellation, cooldown, state and SL/TP hooks.
- `STRATEGY_TEMPLATE.py` is the starter file. Rename the class/folder and replace its example condition before using it.

## Data and execution boundaries

Strategy Lab research backtests read prices only from `C:\AI\Dukascopy_data`. Jesse's standard backtest remains provisional and processes the Bid OHLC route. The separate Bid/Ask ledger/simulator requires explicit instrument contract inputs and reports its coverage; those values must come from the user or verified instrument specification. No broker contract, account-currency conversion, tick sequence or MT5 parity is inferred.

For causal supplemental timeframe logic, declare `strategy_lab_data_timeframes(parameters)` on the strategy class. The tested path requires an M1 execution route; Jesse derives additional timeframe candles from the same Dukascopy M1 observations. Exclude a forming higher-timeframe candle until its close boundary is known. `MultiTimeframeReplayProbe` demonstrates H1, H4, and D1, each with a passing future-mutation check. Simultaneous additional feeds and multi-symbol combinations remain untested. `RECORDED_EVENT_REPLAY` shows saved observations; only the generic prefix endpoint with a replayed `CANDLE_DECISION` claims `FULL_CAUSAL_REPLAY`.

## Optional Bid/Ask signal callback

Strategies that need quote-side signal recomputation can implement `strategy_lab_quote_signal(context)` and optionally `strategy_lab_quote_indicator_specs(parameters)` on the strategy class. The callback receives `index` relative to the first trading candle (zero after warm-up), `history_index` for the warm-up-inclusive causal candle array, the current candle, parameters, availability/position/pending state, a lazy `history` sequence ending at the current candle, a per-candle `indicators` mapping, last-close index and isolated strategy state. The history view copies a row only when accessed; iterating or slicing the whole prefix still costs time proportional to the prefix.

Indicator specs are declarative and allow-listed. Currently the replay engine precomputes RSI once for `bid_close` or `ask_close` using Jesse's sequential RSI kernel and exposes only the current value. The StatefulRsiPending example uses the Ask-close RSI spec. A `PASS` decision must return a signal with `side` (`BUY`/`SELL` or `LONG`/`SHORT`), `order_type` (`MARKET`, `STOP`, or `LIMIT`), and a positive `quantity`; STOP/LIMIT also require a positive planned price. MARKET may omit its planned price. The current StatefulRsiPending Duka run matched 1,379/1,380 saved decisions; the remaining Ask-vs-Bid RSI threshold divergence is recorded as quote-side model behavior. Other Python indicators, multi-route and higher-timeframe callback contexts remain unsupported until explicitly implemented and tested.
