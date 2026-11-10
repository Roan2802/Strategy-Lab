# Chart capabilities (P0)

Only local Dukascopy-derived bars were loaded in Jesse. Browser checks were made in the authenticated dashboard at localhost.

| Capability | Result | Evidence / limit |
|---|---|---|
| Candlestick chart | PASS | Backtest result displayed OHLC candles. |
| Zoom in/out | PASS | Interactive chart controls responded. |
| Horizontal scroll | PASS | Historical timeline navigation available. |
| Select candle / inspect OHLC | PASS | Selected candle produced OHLC values. |
| Entry markers | PASS | Backtest trade markers visible. |
| Exit markers | PASS | Exit markers visible. |
| SL/TP levels | PASS, trade visualization | Execution rows and exit/order markers; custom static/dynamic levels can be drawn by strategy API. |
| Indicator overlay | PASS | Probe EMA20 displayed on candle chart. |
| Separate indicator panel | PASS | Probe “Entry Inspector” extra line chart visible. |
| Horizontal lines | PASS | Probe reference/threshold lines visible. |
| Price zones / ranges | PARTIAL | No native zone/range primitive confirmed in strategy API; implement polygon/box in separate chart layer or custom UI. |
| Equity curve | PASS | Backtest result chart available. |
| Trade list | PASS | 13 trades listed with jump action. |
| Jump to trade candle | PASS | “Jump to trade” navigated/centered chart to trade time. |
| Individual executions | PASS | Expanded trade details showed orders/fill information. |
| Chart image export | NOT TESTED | UI screenshot was observed but not persisted as a file; no image-export test recorded. |

## API observations

`Strategy.update_chart()` supports `add_line_to_candle_chart`, `add_horizontal_line_to_candle_chart`, `add_horizontal_line_to_extra_chart` and `add_extra_line_chart`. The P0 strategy successfully drew EMA20, a reference line, an Entry Inspector score trace, and a PASS threshold. For candle-specific lists of all entry checks, rejected reasons, state snapshots, and chart-selection-driven details, use structured event logs and a separate local Python UI or add a supported frontend event-data API. No Jesse engine change is indicated for analysis/storage; chart integration may need frontend work. A rectangle/range primitive was not found/verified.

## Evidence

Visual checks were performed on the P0 XAU-USD 1m result in Jesse; trade jump opened a trade centered at 2026-09-14 03:01. Screenshots appeared in the desktop automation but were not saved under `evidence/screenshots` as actual PNG files. The UI capability observations are direct but therefore less reproducible than the logs.
