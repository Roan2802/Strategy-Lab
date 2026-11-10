"""Synthetic market data for the factory tests (never used as evidence)."""
import numpy as np
import pandas as pd

START, END = "2012-01-02", "2020-07-01"
TEST_PROTOCOL = {"dev_start": "2012-01-01", "development_end": "2016-01-01", "sealed_start": "2019-07-01"}


def synthetic_frames(symbol="XAUUSD", timeframe="H4", seed=11, spread=0.05, drift=0.0, ar=0.05):
    freq = {"H4": "4h", "H1": "1h", "M30": "30min", "M15": "15min"}[timeframe]
    idx = pd.date_range(START, END, freq=freq, tz="UTC", inclusive="left")
    idx = idx[idx.dayofweek < 5]
    n = len(idx)
    rng = np.random.default_rng(seed + sum(map(ord, symbol)))
    noise = rng.normal(0, 0.004, n)
    ret = np.zeros(n)
    for i in range(1, n):
        ret[i] = ar * ret[i - 1] + noise[i] + drift
    close = 1500.0 * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[1500.0], close[:-1]])
    span = np.abs(rng.normal(0, 0.002, n)) * close
    bid = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + span,
                        "low": np.minimum(open_, close) - span, "close": close,
                        "volume": rng.integers(50, 500, n).astype(float)}, index=idx)
    ask = bid.copy()
    for c in ("open", "high", "low", "close"):
        ask[c] = bid[c] + spread
    return bid, ask, {"synthetic": True, "symbol": symbol, "timeframe": timeframe, "seed": seed}


def synthetic_loader(symbol, timeframe):
    return synthetic_frames(symbol, timeframe)
