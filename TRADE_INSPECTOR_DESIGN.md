# Trade Inspector design

## Proof of concept evidence

`evidence/code/P0AuditProbe.py` logs each evaluated candle as a structured `P0_INSPECTOR_JSON` record with `decision` PASS/FAIL/SKIP, reason, close/high/low, EMA value, range value, scheduled flag, position/pending-order gate, and UTC timestamp. Lifecycle records include signal and fill time, order type/side/size, position/order IDs, SL/TP, close trade ID, order IDs, execution data and P&L. The output in `evidence/results/P0_XAUUSD_inspector_events.jsonl` contains 1,432 parsed events from 1,380 candle decisions and 13 trades.

The normal CSV has 13 trade rows; the debug stream has 13 opens/closes. Join after close by `trade_id`; join fills to order objects by order ID; associate signal decisions by `(symbol, timeframe, signal_timestamp_ms)` and use actual fill timestamp separately. Do not assume Jesse position ID equals closed trade ID. The event sample shows order IDs bridge the close object and trade row; different event IDs require explicit joins.

## Data model

Persist append-only event records with fields:

- `run_id`, strategy/version hash, symbol, timeframe, candle timestamp UTC/ms.
- `event`: `CANDLE_DECISION`, `ORDER_SUBMITTED`, `ORDER_CANCEL_REQUEST`, `ORDER_CANCELLED`, `ORDER_FILLED`, `POSITION_OPEN`, `POSITION_CLOSE`.
- `checks`: named `{value, comparator, threshold, result}` entries; retain unrounded raw values.
- `decision`, `reason`, strategy-state snapshot, available position/pending orders.
- `order_id`, `position_id`, `trade_id`, side/type, requested/filled quantity and price, SL/TP, event time.
- `data_source_id`: immutable manifest/file hash from `C:\AI\Dukascopy_data` conversion.

Keep candle input separate from events; resolve the selected candle with UTC timestamp, not array index, because data may contain gaps.

## UI behavior

1. Candlestick view with overlays and entry/exit/SL/TP markers.
2. Selecting a candle shows every evaluated check including failures and SKIP reasons, exact indicator values, state, and active/pending orders.
3. Selecting a trade jumps to signal candle and offers a second jump to fill/exit candle.
4. Replay advances a cursor and only exposes bars through the replay time; recompute/display state as events advance.
5. Filter by decision/reason; rejected signals must remain queryable.

## Architecture and effort

Start with a separate local Python inspector reading exported JSONL/CSV plus the Duka-derived candle files. This avoids invasive engine changes and preserves Jesse upgradeability. Add a chart overlay adapter or local web chart (estimated medium effort); use Jesse `update_chart()` for simple lines/score overlays. Complex zones/ranges and synchronized candle selection likely need frontend/standalone chart code (medium/high). No engine change is necessary unless the event lifecycle or chart API cannot expose the required stable identifiers; current hooks exposed enough POC data. Add automated Duka fixture tests for event schema, stable joins, DST conversion, gaps, and signal-vs-fill distinctions before integrating more UI.

## Known lifecycle caveats

`on_cancel()` fires after active orders are cancelled; distinguish the prior reason using an explicit `CANCEL_REQUEST` event. POC cancellation hook calls were also seen after position lifecycle activity, so consumers should inspect `entry_orders` and event context rather than equating every hook with rejected/expired pending entries. M1 OHLC cannot prove within-bar tick ordering for simultaneous stop/target touches.
