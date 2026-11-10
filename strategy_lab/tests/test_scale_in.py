import numpy as np
import pandas as pd

from runtime.strategy_lab import rule_engine
from strategy_lab.research.backtest import run_rules_backtest


def _frames(n=600, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-05", periods=n, freq="15min", tz="UTC")
    close = 2000 + np.cumsum(rng.normal(0.4, 2.0, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0.1, 1.5, n)
    low = np.minimum(open_, close) - rng.uniform(0.1, 1.5, n)
    bid = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1.0}, index=idx)
    ask = bid.copy()
    for c in ("open", "high", "low", "close"):
        ask[c] = bid[c] + 0.2
    return bid, ask


def _spec(scale=None):
    spec = {"schema": "strategy_lab.rules/1", "name": "ScaleProbe", "symbol": "XAUUSD", "timeframe": "M15",
            "direction": "long",
            "entry": {"long": {"all": [{"left": {"indicator": "ema", "period": 10}, "op": "crosses_above",
                                         "right": {"indicator": "ema", "period": 30}}]}},
            "order": {"type": "market"}, "stop": {"type": "atr", "mult": 2.0, "period": 14},
            "target": {"type": "r_multiple", "value": 4.0}, "risk": {"percent": 1.0}}
    if scale:
        spec["scale_in"] = scale
    return spec


def test_scale_in_validates_and_rejects_bad_values():
    assert rule_engine.validate_spec(_spec({"max_adds": 2, "add_every_r": 1.0, "add_size_fraction": 0.5})) == []
    assert rule_engine.validate_spec(_spec({"max_adds": 9, "add_every_r": 1.0, "add_size_fraction": 0.5}))
    assert rule_engine.validate_spec(_spec({"max_adds": 1, "add_every_r": 0, "add_size_fraction": 0.5}))


def test_scale_in_never_exceeds_initial_risk_and_only_adds_to_winners():
    bid, ask = _frames()
    base = run_rules_backtest(_spec(), bid, ask, capital=10000)
    scaled = run_rules_backtest(_spec({"max_adds": 2, "add_every_r": 1.0, "add_size_fraction": 0.5}),
                                bid, ask, capital=10000)
    assert scaled["diagnostics"].get("scale_in_adds", 0) > 0
    for t in scaled["trades"]:
        # worst case at the stop stays within the initial 1R (small tolerance for spread/rounding; no gaps here)
        assert t["r_multiple"] >= -1.05
        if t["adds"]:
            assert t["mfe_r"] >= 1.0
    assert [t["entry_time"] for t in base["trades"]][:1] == [t["entry_time"] for t in scaled["trades"]][:1]
