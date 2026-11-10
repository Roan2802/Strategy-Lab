"""New rule_engine features used by the strategy generator: hand values, validation and no-lookahead
(prefix truncation + future mutation) on synthetic data and on a real XAUUSD slice."""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from runtime.strategy_lab import rule_engine as engine
from strategy_lab.research.backtest import run_rules_backtest
from strategy_lab.tests.test_rule_engine import _assert_same_prefix, synthetic_arrays


def ms(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp() * 1000)


def spec_for(cond, short=None, **over):
    s = {"schema": engine.SCHEMA, "name": "Feature probe", "symbol": "XAUUSD", "timeframe": "M15",
         "direction": "both" if short else "long", "entry": {"long": cond, **({"short": short} if short else {})},
         "stop": {"type": "atr", "mult": 2.0, "period": 14}, "target": None, "risk": {"percent": 1.0}}
    s.update(over)
    return s


def _ind(name, period, **kw):
    return {"indicator": name, "period": period, **kw}


FEATURE_CONDS = {
    "zscore": {"left": _ind("zscore", 20), "op": "<", "right": {"value": -1.5}},
    "linreg_slope": {"left": _ind("linreg_slope", 20), "op": ">", "right": {"value": 0}},
    "hurst": {"left": _ind("hurst", 64), "op": ">", "right": {"value": 0.5}},
    "autocorr": {"left": _ind("autocorr", 20, lag=5), "op": "<=", "right": {"value": -0.1}},
    "efficiency_ratio": {"left": _ind("efficiency_ratio", 20), "op": ">", "right": {"value": 0.4}},
    "ret_zscore": {"left": _ind("ret_zscore", 50), "op": "<", "right": {"value": -2.0}},
    "volume_ratio": {"left": _ind("volume_ratio", 20), "op": "<", "right": {"value": 0.8}},
    "range_atr": {"left": _ind("range_atr", 14), "op": ">", "right": {"value": 1.5}},
    "range_width_atr": {"left": _ind("range_width_atr", 20, shift=1), "op": "<", "right": {"value": 4}},
    "swing_high": {"left": {"price": "close"}, "op": "crosses_above", "right": _ind("swing_high", 3)},
    "swing_low": {"left": {"price": "close"}, "op": "crosses_below", "right": _ind("swing_low", 3)},
    "range_mid": {"left": {"price": "close"}, "op": "crosses_above", "right": _ind("range_mid", 30)},
    "vwap_dev_atr": {"left": _ind("vwap_dev_atr", 14), "op": "<", "right": {"value": -1.0}},
    "atr_band_lower": {"left": {"price": "low"}, "op": "<=", "right": _ind("atr_band_lower", 14, mult=2.0)},
    "atr_band_upper": {"left": {"price": "high"}, "op": ">=", "right": _ind("atr_band_upper", 14, mult=2.0)},
    "prev_day_close": {"left": {"price": "close"}, "op": "crosses_above", "right": {"level": "prev_day_close"}},
    "day_open": {"left": {"price": "close"}, "op": "crosses_above", "right": {"level": "day_open"}},
    "window_high": {"left": {"price": "close"}, "op": "crosses_above",
                    "right": {"level": "window_high", "start": "00:00", "end": "07:00", "timezone": "UTC"}},
    "window_low": {"left": {"price": "close"}, "op": "crosses_below",
                   "right": {"level": "window_low", "start": "00:00", "end": "07:00"}},
    "window_mid": {"left": {"price": "close"}, "op": ">",
                   "right": {"level": "window_mid", "start": "13:30", "end": "14:30", "timezone": "Europe/London"}},
    "calendar_hour": {"all": [{"calendar": "hour", "in": [8, 9, 10], "timezone": "Europe/London"},
                              {"left": {"price": "close"}, "op": ">", "right": {"price": "open"}}]},
    "calendar_weekday": {"all": [{"calendar": "weekday", "in": [0, 4]},
                                 {"left": {"price": "close"}, "op": ">", "right": {"price": "open"}}]},
    **{p: {"pattern": p} for p in engine.EXTRA_PATTERNS if p != "random_entry"},
    "random_entry": {"pattern": "random_entry", "seed": 7, "probability": 0.05},
    "range_touch_custom": {"pattern": "range_touch_low", "lookback": 30, "touches": 2, "tolerance_atr": 0.5},
}


@pytest.mark.parametrize("name", sorted(FEATURE_CONDS))
def test_new_feature_specs_validate_and_fire(name):
    spec = spec_for(FEATURE_CONDS[name])
    assert engine.validate_spec(spec) == []
    res = engine.evaluate(spec, synthetic_arrays(3000))
    st = res["states"]["long"]
    assert (st != -1).any(), "never defined"
    if name not in ("volume_ratio",):
        assert (st == 1).any(), "never fires on 3000 synthetic bars"
    assert engine.lookback_bars(spec) > 50
    assert "Feature probe" in engine.describe(spec)


def _truncation_and_mutation(spec, arr, cuts):
    full = engine.evaluate(spec, arr)
    rng = np.random.default_rng(5)
    n = len(arr["time"])
    for k in cuts:
        prefix = engine.evaluate(spec, {key: v[:k + 1] for key, v in arr.items()})
        _assert_same_prefix(full, _pad(prefix, n), k)
        mut = {key: v.copy() for key, v in arr.items()}
        base = float(np.nanmedian(arr["close"]))
        for key in ("open", "high", "low", "close"):
            mut[key][k + 1:] = rng.uniform(base * 0.5, base * 1.5, n - k - 1)
        mut["high"][k + 1:] = np.maximum.reduce([mut["open"][k + 1:], mut["close"][k + 1:], mut["high"][k + 1:]])
        mut["low"][k + 1:] = np.minimum.reduce([mut["open"][k + 1:], mut["close"][k + 1:], mut["low"][k + 1:]])
        mut["volume"][k + 1:] = rng.uniform(0, 1e6, n - k - 1)
        _assert_same_prefix(full, engine.evaluate(spec, mut), k)


def _pad(res, n):
    """Extend a prefix evaluation to length n so _assert_same_prefix can slice both results identically."""
    def ext(x):
        if x is None:
            return None
        out = np.full(n, np.nan if x.dtype.kind == "f" else 0, dtype=x.dtype)
        out[:len(x)] = x
        return out
    return {"long_signal": ext(res["long_signal"]), "short_signal": ext(res["short_signal"]),
            "states": {k: ext(v) for k, v in res["states"].items()},
            "checks": {k: {"state": ext(c["state"]), "left": ext(c["left"]), "right": ext(c["right"])}
                       for k, c in res["checks"].items()},
            "indicators": {k: ext(v) for k, v in res["indicators"].items()},
            "exits": {k: ext(v) for k, v in res["exits"].items()}}


@pytest.mark.parametrize("name", sorted(FEATURE_CONDS))
def test_new_feature_no_lookahead_synthetic(name):
    spec = spec_for(FEATURE_CONDS[name], target={"type": "level", "operand": _ind("range_mid", 20)})
    _truncation_and_mutation(spec, synthetic_arrays(2400), (500, 1301, 1803, 2398))


@pytest.fixture(scope="module")
def real_xau():
    from strategy_lab.data_manager import DataManager
    from strategy_lab.research.backtest import _epoch_ms, load_bid_ask
    try:
        bid, ask, _ = load_bid_ask(DataManager(), "XAUUSD", "M15", "2019-01-01", "2019-03-01")
    except Exception as exc:  # dataset not present on this machine
        pytest.skip(f"Dukascopy dataset unavailable: {exc}")
    if len(bid) < 1000:
        pytest.skip("not enough real XAUUSD bars")
    arr = {"time": _epoch_ms(bid.index), **{c: bid[c].to_numpy(float) for c in ("open", "high", "low", "close",
                                                                                 "volume")}}
    return arr, bid, ask


@pytest.mark.parametrize("name", sorted(FEATURE_CONDS))
def test_new_feature_no_lookahead_real_xauusd(real_xau, name):
    arr, _, _ = real_xau
    n = len(arr["time"])
    spec = spec_for(FEATURE_CONDS[name], target={"type": "level", "operand": {"level": "prev_day_high"}})
    _truncation_and_mutation(spec, arr, (n // 4, n // 2 + 7, n - 3))


# ------------------------------------------------------------------ hand values / semantics

def test_zscore_efficiency_atr_band_hand_values():
    c = np.array([1.0, 2.0, 3.0, 4.0, 10.0])
    a = {"time": np.arange(5) * 900_000, "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": np.ones(5)}
    z = engine.compute_indicator("zscore", a, 3)
    win = c[2:5]
    assert z[4] == pytest.approx((10 - win.mean()) / win.std())
    er = engine.compute_indicator("efficiency_ratio", a, 4)
    assert er[4] == pytest.approx(9 / 9)
    assert np.isnan(er[3])
    band = engine.compute_indicator("atr_band_lower", a, 2, mult=3.0)
    atr2 = engine.atr(a["high"], a["low"], a["close"], 2)
    assert band[4] == pytest.approx(c[3] - 3.0 * atr2[3])


def test_volume_ratio_excludes_current_bar():
    v = np.array([1, 1, 1, 1, 5], float)
    a = {"time": np.arange(5) * 900_000, "open": v, "high": v, "low": v, "close": v, "volume": v}
    assert engine.compute_indicator("volume_ratio", a, 4)[4] == pytest.approx(5.0)


def test_hurst_proxy_random_walk_near_half_and_trend_higher():
    rng = np.random.default_rng(1)
    rw = 1000 * np.exp(np.cumsum(rng.normal(0, 0.001, 6000)))
    h = engine.hurst_proxy(rw, 128)
    assert 0.42 < np.nanmedian(h) < 0.58
    # strongly mean-reverting (anti-persistent) returns -> clearly below 0.5
    r = rng.normal(0, 0.001, 6000)
    anti = 1000 * np.exp(np.cumsum(r - 0.9 * np.concatenate(([0], r[:-1]))))
    assert np.nanmedian(engine.hurst_proxy(anti, 128)) < 0.35


def test_autocorr_alternating_returns_negative():
    c = 100 * np.exp(np.cumsum(np.tile([0.01, -0.01], 50)))
    a = {"time": np.arange(100) * 900_000, "open": c, "high": c, "low": c, "close": c, "volume": np.ones(100)}
    ac = engine.compute_indicator("autocorr", a, 20, lag=1)
    assert np.nanmax(ac) < -0.99


def test_pivots_confirmed_with_lag():
    h = np.array([1, 2, 5, 2, 1, 1, 1, 3, 9, 3, 1, 1], float)
    last, prev = engine._pivots(h, 2, True)
    assert np.isnan(last[3])          # pivot at bar 2 not yet confirmed at bar 3
    assert last[4] == 5               # confirmed at 2 + 2
    assert last[9] == 5 and last[10] == 9 and prev[10] == 5


def test_window_level_only_after_window_closed():
    t = ms("2026-03-02T00:00:00") + np.arange(96) * 900_000  # one UTC day, M15
    c = np.linspace(100, 110, 96)
    a = {"time": t, "open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": np.ones(96)}
    spec = spec_for({"left": {"price": "close"}, "op": ">",
                     "right": {"level": "window_high", "start": "00:00", "end": "07:00"}})
    res = engine.evaluate(spec, a)
    right = next(iter(res["checks"].values()))["right"]
    assert np.isnan(right[:28]).all()                 # bars opening before 07:00
    assert right[28] == pytest.approx(c[27] + 0.5)    # 07:00 bar sees the completed 00:00-07:00 high


def test_calendar_hour_and_weekday():
    t = ms("2026-03-02T00:00:00") + np.arange(96 * 7) * 900_000  # Monday 2026-03-02 .. Sunday
    c = np.full(len(t), 100.0)
    a = {"time": t, "open": c, "high": c, "low": c, "close": c, "volume": np.ones(len(t))}
    res = engine.evaluate(spec_for({"calendar": "weekday", "in": [0]}), a)
    assert res["long_signal"][:96].all() and not res["long_signal"][96:].any()
    res = engine.evaluate(spec_for({"calendar": "hour", "in": [9], "timezone": "Europe/Berlin"}), a)
    hours = ((t // 3_600_000) % 24)[res["long_signal"]]
    assert set(hours.tolist()) == {8}                 # 09:00 Berlin (CET) = 08:00 UTC in March before DST


def test_crt_and_pin_patterns_hand_cases():
    o = np.array([10, 10.0, 9.5, 9.6])
    h = np.array([11, 11.0, 10.2, 10.6])
    l = np.array([9, 9.0, 8.5, 9.5])
    c = np.array([10, 10.0, 9.9, 10.5])
    a = {"time": np.arange(4) * 900_000, "open": o, "high": h, "low": l, "close": c, "volume": np.ones(4)}
    res = engine.evaluate(spec_for({"pattern": "crt_bull"}), a)
    assert res["long_signal"].tolist() == [False, False, False, True]
    o2, c2, h2, l2 = np.array([10.0]), np.array([10.1]), np.array([10.2]), np.array([8.0])
    a2 = {"time": np.zeros(1, np.int64), "open": o2, "high": h2, "low": l2, "close": c2, "volume": np.ones(1)}
    assert engine.evaluate(spec_for({"pattern": "pin_bull"}), a2)["long_signal"][0]


def test_random_entry_deterministic_and_seed_dependent():
    arr = synthetic_arrays(5000)
    s1 = spec_for({"pattern": "random_entry", "seed": 1, "probability": 0.1})
    r1, r1b = engine.evaluate(s1, arr), engine.evaluate(s1, arr)
    assert np.array_equal(r1["long_signal"], r1b["long_signal"])
    r2 = engine.evaluate(spec_for({"pattern": "random_entry", "seed": 2, "probability": 0.1}), arr)
    assert not np.array_equal(r1["long_signal"], r2["long_signal"])
    assert 0.08 < r1["long_signal"].mean() < 0.12


@pytest.mark.parametrize("mutate,needle", [
    (lambda s: s["entry"]["long"]["left"].update(lag=0), "lag"),
    (lambda s: s["entry"]["long"]["left"].update(period=10, indicator="hurst"), ">= 32"),
    (lambda s: s["entry"]["long"].update(right={"level": "window_high", "start": "07:00", "end": "01:00"}),
     "before end"),
    (lambda s: s["entry"]["long"].update(right={"level": "window_high", "start": "07:00"}), "HH:MM"),
    (lambda s: s["entry"]["long"].update(right={"level": "prev_day_close", "start": "07:00"}), "unknown key"),
    (lambda s: s.update(stop={"type": "percent", "value": 0}), "stop.value"),
    (lambda s: s.update(target={"type": "level", "operand": {"value": 3}}), "constant value"),
    (lambda s: s.update(entry={"long": {"calendar": "hour", "in": [25]}}), "0..23"),
    (lambda s: s.update(entry={"long": {"calendar": "month", "in": [1]}}), "unknown calendar"),
    (lambda s: s.update(entry={"long": {"pattern": "random_entry", "seed": 1, "probability": 0.9}}), "probability"),
    (lambda s: s.update(entry={"long": {"pattern": "inside_bar", "touches": 2}}), "unknown key"),
])
def test_new_feature_validation_errors(mutate, needle):
    spec = spec_for({"left": _ind("autocorr", 20, lag=2), "op": "<", "right": {"value": 0}})
    assert engine.validate_spec(spec) == []
    mutate(spec)
    errs = engine.validate_spec(spec)
    assert any(needle in e for e in errs), errs


def _frames(n=400, seed=3):
    arr = synthetic_arrays(n, seed=seed)
    idx = pd.to_datetime(arr["time"], unit="ms", utc=True)
    bid = pd.DataFrame({k: arr[k] for k in ("open", "high", "low", "close", "volume")}, index=idx)
    ask = bid.copy()
    for k in ("open", "high", "low", "close"):
        ask[k] = bid[k] + 0.2
    return bid, ask


def test_percent_stop_and_level_target_in_backtest():
    bid, ask = _frames()
    spec = spec_for({"left": {"price": "close"}, "op": ">", "right": {"price": "open"}},
                    stop={"type": "percent", "value": 0.5},
                    target={"type": "level", "operand": _ind("donchian_high", 20)})
    res = run_rules_backtest(spec, bid, ask)
    assert res["trades"]
    for t in res["trades"]:
        if t["exit_reason"] == "STOP_LOSS":
            assert t["exit_price"] == pytest.approx(t["entry_price"] * 0.995, rel=1e-6)
    snap = {"target_level": 105.0}
    rs = engine.resolve(spec)
    assert engine.target_price(rs, "long", 100.0, 99.0, snap) == 105.0
    assert np.isnan(engine.target_price(rs, "long", 106.0, 105.0, snap))   # level behind entry -> no target
    assert engine.stop_price(rs, "short", 200.0, {}) == pytest.approx(201.0)
