# Generic Strategy Debug API

Status: **PASS for the two instrumented examples on the tested Jesse backtest path.** Jesse's external MCP adapter remains **BLOCKED** in this agent session; the local dashboard uses the documented Docker research runner.

## Contract

Import `InspectorMixin` from `strategy_lab.events` and subclass it with Jesse's `Strategy`. Call `debug_candle()` from `before()` once per trading candle. The helper records only values computed by the strategy; it does not recompute indicators.

```python
with self.debug_candle(context={"session": session_name}) as debug:
    debug.indicator("RSI", rsi_value, timeframe="1m", unit="index")
    accepted = debug.evaluate(debug.group("entry", "all", [
        debug.condition("Session open", session_open, "==", True),
        debug.condition("RSI threshold", lambda: rsi_value, "<=", 30,
                        depends_on=["RSI warmup"], unit="index"),
    ], stop_on_skip=True))
    debug.state("pending_order_ids", [str(o.id) for o in self.entry_orders])
    if accepted:
        debug.signal("BUY", "RSI setup", order_type="STOP", price=trigger,
                     depends_on=["entry"])
```

Supported groups are nested `all` and `any`; comparisons include numeric/equality tests, membership, ranges, truthiness and cross-above/cross-below. Callable values are evaluated only if reached. The trace distinguishes evaluated `PASS`/`FAIL` from short-circuit, dependency and missing-value `SKIP`. Indicator metadata can include timeframe, unit and source. Context and arbitrary JSON-compatible strategy state are recorded on that candle.

The helper emits versioned `strategy-lab.events.v1` JSONL records with timestamp, symbol, timeframe, parameters, decision, checks, indicators, signal, state, context, run ID and strategy/dataset source fingerprints. Lifecycle events include order/trade/position IDs and timestamps. Backtests stream to `events.jsonl`; readers iterate the stream and legacy `result.json.events` remains a compatibility fallback.

Strategies still own actual Jesse order calls and hooks. The recorder does not infer a signal from a trade and does not itself place or cancel orders. Call `record_lifecycle(...)` next to order, fill, cancellation and position hooks. Live/paper mode must be explicitly guarded by the strategy.

### Supplemental timeframe feeds

An instrumented strategy may declare causal supplemental feeds with `strategy_lab_data_timeframes(parameters)`, returning Jesse timeframe names or Strategy Lab names such as `H1`, `H4`, or `D1`. The runner registers these as Jesse `data_routes`; they are built from the same observed M1 Dukascopy stream, and the run manifest records the effective routes. Causal replay re-registers those routes and refuses a replay if they differ from the run manifest. Supplemental feeds currently require an M1 execution route because higher-timeframe data must be formed from the underlying M1 observations. H1, H4, and D1 have each passed an independent future-mutation check; multi-symbol combinations are not supported.

## Tested examples

- `runtime/strategies/ExampleStrategy`: minimal candle-body entry with the generic contract.
- `runtime/strategies/StatefulRsiPending`: RSI filter, stop entry, expiry cancellation, cooldown, stop-loss/take-profit and state snapshot; parameter labels are in its `parameter_metadata.json`.
- `runtime/strategies/MultiTimeframeReplayProbe`: configurable H1/H4/D1 EMA read by an M1 strategy, with the forming higher-timeframe candle excluded until its close boundary is observable. It is a diagnostic example and intentionally submits no orders.

## Limits

The dashboard runner currently creates one Jesse trading route per run. Strategies can declare same-symbol supplemental timeframes; multi-symbol or multiple trading routes, arbitrary external indicator side effects, broker contract semantics and runtime mutation of strategy code are not covered by this contract. Hash changes reject causal replay rather than replaying a different strategy version.
