"""Minimal example plugin: moving-average cross on M15, one position, ATR-free fixed-distance stop.

Copy this folder, rename it, and replace the logic in ``backtest``. Everything the app needs is here.
"""
from __future__ import annotations

PLUGIN = {
    "name": "Voorbeeld MA-cross",
    "version": "1.0",
    "description": "Sjabloon: koop als de snelle SMA boven de trage kruist, verkoop bij omgekeerde kruising.",
    "symbols": ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "US30", "US500", "DE40", "BTCUSD"],
    "base_timeframe": "M1",
    "warmup_days": 5,
    "params": [
        {"name": "fast", "type": "int", "min": 5, "max": 50, "default": 20},
        {"name": "slow", "type": "int", "min": 20, "max": 200, "default": 50},
        {"name": "lots", "type": "float", "min": 0.01, "max": 10, "default": 0.1},
        {"name": "contract_size", "type": "float", "min": 1, "max": 100000, "default": 100},
    ],
    "notes": ["Signaal op gesloten M15-candle, uitvoering op de open van de volgende M15-candle (Ask voor kopen, Bid voor verkopen)."],
}


def backtest(bid, ask, params, *, capital, start, end, stress=None, progress=None) -> dict:
    import pandas as pd

    bars = bid.resample("15min").agg({"open": "first", "close": "last"}).dropna()
    asks = ask.resample("15min").agg({"open": "first"}).reindex(bars.index)
    fast = bars["close"].rolling(int(params["fast"])).mean()
    slow = bars["close"].rolling(int(params["slow"])).mean()
    above = (fast > slow).astype(int)
    cross = above.diff()          # +1 = kruising omhoog, -1 = omlaag; bekend bij de slotkoers
    start_ts, end_ts = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    lots, size = float(params["lots"]), float(params["contract_size"])
    trades, position = [], None
    times = list(bars.index)
    for i in range(1, len(times) - 1):
        nxt = times[i + 1]                         # uitvoering op de volgende candle: geen vooruitkijken
        if cross.iloc[i] == 1 and position is None and start_ts <= nxt < end_ts:
            position = (nxt, float(asks["open"].iloc[i + 1]))
        elif cross.iloc[i] == -1 and position is not None:
            exit_price = float(bars["open"].iloc[i + 1])
            pnl = (exit_price - position[1]) * lots * size
            trades.append({"entry_time": position[0].isoformat(), "exit_time": nxt.isoformat(), "side": "long",
                           "entry_price": position[1], "exit_price": exit_price, "qty": lots, "pnl": pnl,
                           "costs": 0.0, "r_multiple": None, "exit_reason": "CROSS_DOWN"})
            position = None
    return {"trades": trades, "notes": PLUGIN["notes"]}
