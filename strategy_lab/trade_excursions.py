"""Conservative, price-unit MAE/MFE from the Duka candles saved with a run."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


METHOD = "bid_ohlc_after_known_fill_before_exit_bar"


def add_trade_excursions(trades: list[dict], candles: pd.DataFrame) -> dict:
    """Annotate trades with conservative raw-price MAE/MFE and return coverage.

    A market entry exactly at candle open makes that candle's full range
    observable after the fill, so it may be included. Pending fills do not
    reveal the pre/post-fill sequence in OHLC; their fill candle is excluded.
    The exit candle is always excluded because OHLC does not reveal when the
    exit occurred inside that candle.
    """
    required = {"timestamp", "high", "low"}
    if not required.issubset(candles.columns):
        return {"available": False, "reason": "Saved candles lack timestamp/high/low columns.",
                "method": METHOD, "available_trades": 0, "unavailable_trades": len(trades)}

    frame = candles.loc[:, ["timestamp", "high", "low"]].copy()
    frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce")
    frame["high"] = pd.to_numeric(frame["high"], errors="coerce")
    frame["low"] = pd.to_numeric(frame["low"], errors="coerce")
    frame = frame.dropna().sort_values("timestamp", kind="stable")
    if frame["timestamp"].duplicated().any():
        return {"available": False, "reason": "Saved candles contain duplicate timestamps.",
                "method": METHOD, "available_trades": 0, "unavailable_trades": len(trades)}

    timestamps = frame["timestamp"].to_numpy(dtype=np.int64)
    highs = frame["high"].to_numpy(dtype=np.float64)
    lows = frame["low"].to_numpy(dtype=np.float64)
    values: list[tuple[float, float]] = []

    for trade in trades:
        side = str(trade.get("type", trade.get("side", ""))).lower()
        try:
            entry_ms = int(float(trade.get("opened_at", trade.get("entry_timestamp_ms"))))
            exit_ms = int(float(trade.get("closed_at", trade.get("closed_at_ms"))))
            entry_price = float(trade.get("entry_price"))
        except (TypeError, ValueError, OverflowError):
            entry_ms = exit_ms = 0
            entry_price = math.nan

        orders = trade.get("orders", []) or []
        filled_market_at_open = False
        for order in orders:
            if not isinstance(order, dict) or str(order.get("type", "")).upper() != "MARKET" \
                    or order.get("reduce_only", False) or order.get("executed_at") is None:
                continue
            try:
                filled_market_at_open = int(float(order["executed_at"])) == entry_ms
            except (TypeError, ValueError, OverflowError):
                filled_market_at_open = False
            if filled_market_at_open:
                break
        start = int(np.searchsorted(timestamps, entry_ms,
                                    side="left" if filled_market_at_open else "right"))
        stop = int(np.searchsorted(timestamps, exit_ms, side="left"))
        valid_side = side in {"long", "buy", "short", "sell"}
        if not valid_side or not math.isfinite(entry_price) or exit_ms <= entry_ms or stop <= start:
            trade.update({"mfe_price": None, "mae_price": None, "excursion_bars": 0,
                          "excursion_method": METHOD,
                          "excursion_note": "Geen volledige Dukascopy-candle na de zekere fill en vóór de exitcandle beschikbaar."})
            continue

        interval_high = float(np.max(highs[start:stop]))
        interval_low = float(np.min(lows[start:stop]))
        if side in {"long", "buy"}:
            mfe = max(0.0, interval_high - entry_price)
            mae = max(0.0, entry_price - interval_low)
        else:
            mfe = max(0.0, entry_price - interval_low)
            mae = max(0.0, interval_high - entry_price)
        bars = stop - start
        trade.update({"mfe_price": mfe, "mae_price": mae, "excursion_bars": bars,
                      "excursion_method": METHOD,
                      "excursion_note": "Ruwe prijsafstand; fill- en exitcandle uitgesloten."})
        values.append((mfe, mae))

    return {"available": bool(values), "method": METHOD, "price_units": True,
            "available_trades": len(values), "unavailable_trades": len(trades) - len(values),
            "average_mfe_price": float(np.mean([x[0] for x in values])) if values else None,
            "average_mae_price": float(np.mean([x[1] for x in values])) if values else None,
            "note": "Raw Dukascopy Bid price units after a known market fill at candle open and before the exit candle; pending fill candle and exit candle are excluded. Not account-currency P&L."}
