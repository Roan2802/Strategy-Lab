# Replay validation — 2026-11-07

Replay chart markers were exercised in the browser using saved runs built only from `C:\AI\Dukascopy_data`.

- Bullish case: 13 closed trades; legend and chart showed bullish entry triangles, separate TP/SL exit markers, and per-trade dotted connectors.
- Bearish case: 28 closed trades; 28 bearish entry triangles and 28 dotted entry-to-exit connectors were verified on the full chart. At the replay cursor, one trade connector was visible while future entries/exits remained hidden; the first trade's SL marker appeared when its timestamp was reached.
- Auto replay speeds: 1, 3, 5, 10 and 25 candles per second. The 1/sec option advanced automatically; at 25/sec the cursor reached 875 visible candles after about 26 seconds.
- Chart range: `Alles tonen`/unlimited displays all selected candles; limit 0 was separately verified against the local API.
- Code now filters markers/connectors by replay cursor time (not merely the last available candle), so Dukascopy data gaps do not defer a marker beyond its actual event time.

Evidence: `evidence/test_outputs/replay_trade_markers_20261107.json`, `evidence/test_outputs/replay_validation_20261107.json`. Current regression suite: 76 passed; JavaScript syntax passed.

Replay shows the saved run's signals, fills and exits. It does not reconstruct arbitrary strategy Python state at every candle; only the separately documented LabBreakout causal adapter is supported.

## 2026-11-08 — replay pacing and rolling windows

The playback clock advances one available candle per tick, independently of Inspector requests and Plotly paint. At high speeds the chart can combine paint updates while retaining every candle in the displayed window. The chart only plots data through the current playback cursor. A display limit of 100, 500, 1,000 or 2,000 keeps the newest candles visible and can be changed without moving the cursor. The EMA uses the preceding playback history even when earlier candles leave the window.

On XAUUSD M1 for 2026-09-14 in the in-app browser, measurements with a 100-candle window yielded approximately 25, 50 and 99 candles per second at x25, x50 and x100. The chart rendered 500 and then 100 candles at the same final cursor, index 1,379. The six speed ticks aligned with the thumb travel, and the top toolbar fit without clipping in the two-row standard layout at a 1360-pixel viewport.
