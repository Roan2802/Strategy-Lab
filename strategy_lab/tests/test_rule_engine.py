import ast
import copy
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from runtime.strategy_lab import rule_engine as engine
from strategy_lab.research import codegen
from strategy_lab.research.backtest import load_bid_ask, run_rules_backtest

NAN_EQ = dict(equal_nan=True)


def ms(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp() * 1000)


def synthetic_arrays(n=3000, seed=7, start="2026-03-02T00:00:00", step_ms=900_000):
    rng = np.random.default_rng(seed)
    t = ms(start) + np.arange(n, dtype=np.int64) * step_ms
    c = 2000 + np.cumsum(rng.normal(0, 1.0, n))
    o = np.concatenate(([c[0]], c[:-1]))
    h = np.maximum(o, c) + rng.random(n)
    l = np.minimum(o, c) - rng.random(n)
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": rng.random(n) * 100 + 1}


def _spec(**over):
    base = {"schema": engine.SCHEMA, "name": "T", "symbol": "XAUUSD", "timeframe": "M15", "direction": "long",
            "entry": {"long": {"left": {"price": "close"}, "op": ">", "right": {"value": 101}}},
            "stop": {"type": "points", "value": 1.0}, "target": None, "risk": {"percent": 1.0}}
    base.update(over)
    return base


# ------------------------------------------------------------------ validation

def test_validate_spec_accepts_minimal_and_templates():
    assert engine.validate_spec(_spec()) == []
    for name, spec in codegen.TEMPLATES.items():
        assert engine.validate_spec(spec) == [], name


@pytest.mark.parametrize("mutate,needle", [
    (lambda s: s.update(bogus=1), "unknown key"),
    (lambda s: s.update(schema="x"), "schema"),
    (lambda s: s.update(timeframe="M7"), "timeframe"),
    (lambda s: s.update(direction="up"), "direction"),
    (lambda s: s["entry"]["long"].update(op="=="), "unknown op"),
    (lambda s: s["entry"]["long"].update(right={"indicator": "wma", "period": 5}), "unknown indicator"),
    (lambda s: s["entry"]["long"].update(right={"indicator": "ema"}), "requires 'period'"),
    (lambda s: s["entry"]["long"].update(right={"indicator": "ema", "period": 0}), "period"),
    (lambda s: s["entry"]["long"].update(right={"indicator": "ema", "period": 5, "shift": -1}), "shift"),
    (lambda s: s["entry"]["long"].update(right={"indicator": "ema", "period": 5, "timeframe": "M5"}), "multiple"),
    (lambda s: s["entry"]["long"].update(right={"value": {"param": "nope"}}), "does not exist"),
    (lambda s: s["entry"]["long"].update(right={"level": "opening_range_high"}), "minutes"),
    (lambda s: s["entry"].update(long={"pattern": "doji"}), "unknown pattern"),
    (lambda s: s.update(stop={"type": "atr", "mult": -1, "period": 14}), "stop.mult"),
    (lambda s: s.update(stop={"type": "magic"}), "stop.type"),
    (lambda s: s.update(risk={"percent": 0}), "risk.percent"),
    (lambda s: s.update(risk={"percent": 500}), "risk.percent"),
    (lambda s: s.update(session={"timezone": "Mars/Base", "start": "09:30", "end": "16:00"}), "timezone"),
    (lambda s: s.update(session={"timezone": "UTC", "start": "9:30", "end": "16:00"}), "HH:MM"),
    (lambda s: s.update(params=[{"name": "p", "type": "int", "min": 5, "max": 1, "default": 3}]), "min > max"),
    (lambda s: s.update(params=[{"name": "p", "type": "int", "min": 1, "max": 9, "default": 30}]), "default"),
    (lambda s: s.update(entry={}), "entry.long"),
])
def test_validate_spec_catches_errors(mutate, needle):
    spec = _spec()
    mutate(spec)
    errors = engine.validate_spec(spec)
    assert errors and any(needle in e for e in errors), errors


def test_resolve_substitutes_and_checks_ranges():
    spec = copy.deepcopy(codegen.TEMPLATES["ema_rsi_pullback"])
    resolved = engine.resolve(spec, {"ema_fast": 12})
    left = resolved["entry"]["long"]["all"][0]["left"]
    assert left["period"] == 12
    assert engine.resolve(spec)["entry"]["long"]["all"][0]["left"]["period"] == 20
    with pytest.raises(ValueError):
        engine.resolve(spec, {"ema_fast": 500})
    with pytest.raises(ValueError):
        engine.resolve(spec, {"nonexistent": 1})
    assert spec["entry"]["long"]["all"][0]["left"]["period"] == {"param": "ema_fast"}


# ------------------------------------------------------------------ indicators

def test_sma_ema_hand_values():
    out = engine.sma(np.array([1.0, 2, 3, 4, 5]), 3)
    assert np.allclose(out, [np.nan, np.nan, 2, 3, 4], **NAN_EQ)
    out = engine.ema(np.array([1.0, 2, 3, 4, 5, 6]), 3)  # seed = SMA(1,2,3)=2, alpha=0.5
    assert np.allclose(out, [np.nan, np.nan, 2, 3, 4, 5], **NAN_EQ)


def test_wilder_rsi_hand_values():
    out = engine.rsi(np.array([10.0, 11, 12, 11, 13, 12]), 3)
    assert np.isnan(out[:3]).all()
    assert out[3] == pytest.approx(100 - 100 / 3)
    assert out[4] == pytest.approx(100 - 100 / 6)
    assert out[5] == pytest.approx(100 * 20 / 33)
    assert engine.rsi(np.array([1.0, 2, 3, 4, 5]), 3)[-1] == 100.0


def test_wilder_atr_hand_values():
    h = np.array([10, 11, 12, 13, 12.0])
    l = np.array([9, 9.5, 10.5, 11, 10.0])
    c = np.array([9.5, 10.5, 11.5, 12, 11.0])
    out = engine.atr(h, l, c, 3)
    assert np.isnan(out[:3]).all()
    assert out[3] == pytest.approx((1.5 + 1.5 + 2) / 3)
    assert out[4] == pytest.approx(((1.5 + 1.5 + 2) / 3 * 2 + 2) / 3)


def test_warmup_is_skip_not_pass():
    arr = synthetic_arrays(30)
    spec = _spec(entry={"long": {"left": {"indicator": "ema", "period": 20}, "op": ">",
                                 "right": {"value": 0}}})
    res = engine.evaluate(spec, arr)
    state = res["states"]["long"]
    assert (state[:19] == -1).all() and (state[19:] == 1).all()
    assert not res["long_signal"][:19].any()
    assert engine.bar_decision(res, 5) == ("SKIP", "INDICATOR_WARMUP")
    assert engine.bar_checks(res, 5)[0]["result"] == "SKIP"


def test_vwap_zero_volume_fallback_recorded():
    arr = synthetic_arrays(40)
    arr["volume"] = np.zeros(40)
    spec = _spec(entry={"long": {"left": {"price": "close"}, "op": ">", "right": {"indicator": "vwap"}}})
    res = engine.evaluate(spec, arr)
    assert any("equal-weight" in n for n in res["notes"])
    vw = res["indicators"]["vwap"]
    typical = (arr["high"] + arr["low"] + arr["close"]) / 3
    day0 = arr["time"] // 86_400_000 == arr["time"][0] // 86_400_000
    assert vw[0] == pytest.approx(typical[0])
    assert vw[day0][-1] == pytest.approx(typical[day0].mean())


def test_patterns_basic():
    arr = {"time": ms("2026-03-02T00:00:00") + np.arange(4) * 900_000,
           "open": np.array([10, 10.5, 10.4, 9.0]), "close": np.array([10.5, 10.3, 10.2, 11.0]),
           "high": np.array([11, 11, 10.8, 11.1]), "low": np.array([9.5, 9.6, 9.9, 8.9]),
           "volume": np.ones(4)}
    spec = _spec(entry={"long": {"pattern": "inside_bar"}})
    res = engine.evaluate(spec, arr)
    assert list(res["long_signal"]) == [False, False, True, False]
    spec = _spec(entry={"long": {"pattern": "bullish_engulfing"}})
    assert list(engine.evaluate(spec, arr)["long_signal"]) == [False, False, False, True]


# ------------------------------------------------------------------ causality

HTF_SPEC = {
    "schema": engine.SCHEMA, "name": "HtfProbe", "symbol": "XAUUSD", "timeframe": "M15", "direction": "both",
    "entry": {
        "long": {"all": [{"left": {"price": "close"}, "op": ">",
                          "right": {"indicator": "ema", "period": 5, "timeframe": "H1"}},
                         {"left": {"indicator": "rsi", "period": 7, "timeframe": "H4", "shift": 1}, "op": ">",
                          "right": {"value": 30}}]},
        "short": {"any": [{"left": {"price": "close"}, "op": "crosses_below",
                           "right": {"level": "prev_day_low"}},
                          {"not": {"left": {"indicator": "macd_hist"}, "op": ">", "right": {"value": 0}}},
                          {"pattern": "fvg_bear", "lookback": 3},
                          {"pattern": "liquidity_sweep_high", "lookback": 10},
                          {"left": {"indicator": "vwap"}, "op": "<", "right": {"level": "session_open"}}]}},
    "stop": {"type": "swing", "lookback": 10}, "target": {"type": "atr", "mult": 3, "period": 14},
    "trailing": {"activate_r": 1.0, "atr_mult": 1.0, "period": 14}, "break_even": {"activate_r": 1.0},
    "session": {"timezone": "America/New_York", "start": "03:00", "end": "20:00"}, "risk": {"percent": 0.5}}


def _assert_same_prefix(a, b, k):
    assert np.array_equal(a["long_signal"][:k + 1], b["long_signal"][:k + 1])
    assert np.array_equal(a["short_signal"][:k + 1], b["short_signal"][:k + 1])
    for side in ("long", "short"):
        assert np.array_equal(a["states"][side][:k + 1], b["states"][side][:k + 1])
    assert a["checks"].keys() == b["checks"].keys()
    for name in a["checks"]:
        ca, cb = a["checks"][name], b["checks"][name]
        assert np.array_equal(ca["state"][:k + 1], cb["state"][:k + 1]), name
        for side in ("left", "right"):
            if ca[side] is not None:
                assert np.allclose(ca[side][:k + 1], cb[side][:k + 1], **NAN_EQ), (name, side)
    for name in a["indicators"]:
        assert np.allclose(a["indicators"][name][:k + 1], b["indicators"][name][:k + 1], **NAN_EQ), name
    for name in a["exits"]:
        assert np.allclose(a["exits"][name][:k + 1], b["exits"][name][:k + 1], **NAN_EQ), name


@pytest.mark.parametrize("name", list(codegen.TEMPLATES) + ["htf_probe"])
def test_causality_future_mutation_never_changes_past(name):
    spec = HTF_SPEC if name == "htf_probe" else codegen.TEMPLATES[name]
    step = {"M5": 300_000, "M15": 900_000, "H1": 3_600_000}[spec["timeframe"]]
    arr = synthetic_arrays(2500, step_ms=step)
    base = engine.evaluate(spec, arr)
    assert base["long_signal"].any() or base["short_signal"].any() or name == "htf_probe"
    rng = np.random.default_rng(99)
    for k in (700, 1301, 1802):  # 1301 / 1802 deliberately not on HTF boundaries
        mut = {key: value.copy() for key, value in arr.items()}
        for key in ("open", "high", "low", "close"):
            mut[key][k + 1:] = rng.uniform(1000, 3000, len(mut[key]) - k - 1)
        mut["volume"][k + 1:] = rng.uniform(0, 1e6, len(mut["volume"]) - k - 1)
        _assert_same_prefix(base, engine.evaluate(spec, mut), k)


def test_htf_value_visible_only_after_htf_bar_closed():
    arr = synthetic_arrays(48, start="2026-03-02T00:00:00")  # M15, hour aligned
    spec = _spec(entry={"long": {"left": {"indicator": "sma", "period": 1, "timeframe": "H1"}, "op": ">",
                                 "right": {"value": 0}}})
    res = engine.evaluate(spec, arr)
    htf = res["indicators"]["sma(1)@H1"]
    c = arr["close"]
    assert np.isnan(htf[:3]).all()  # first H1 bar (bars 0-3) is not closed before bar 3's close
    assert htf[3] == pytest.approx(c[3])  # visible exactly when the last bar of the hour closes
    for i in (4, 5, 6):
        assert htf[i] == pytest.approx(c[3])  # still the previous, fully closed hour
    assert htf[7] == pytest.approx(c[7])
    # HTF shift counts HTF bars
    spec2 = _spec(entry={"long": {"left": {"indicator": "sma", "period": 1, "timeframe": "H1", "shift": 1},
                                  "op": ">", "right": {"value": 0}}})
    shifted = engine.evaluate(spec2, arr)["indicators"]["sma(1)@H1[-1]"]
    assert np.isnan(shifted[:7]).all() and shifted[7] == pytest.approx(c[3])


def test_session_filter_dst_correct():
    spec = _spec(session={"timezone": "America/New_York", "start": "09:30", "end": "16:00"})
    cases = [  # (UTC bar-open, expected in session)
        ("2026-03-06T14:29:00", False), ("2026-03-06T14:30:00", True),   # EST (UTC-5) before DST switch
        ("2026-03-06T20:59:00", True), ("2026-03-06T21:00:00", False),
        ("2026-03-09T13:29:00", False), ("2026-03-09T13:30:00", True),   # EDT (UTC-4) after 2026-03-08
        ("2026-03-09T19:59:00", True), ("2026-03-09T20:00:00", False),
    ]
    mask = engine.session_mask(spec, np.array([ms(t) for t, _ in cases]))
    assert list(mask) == [e for _, e in cases]
    # check shows up and gates the signal
    arr = synthetic_arrays(4, start="2026-03-09T13:15:00")
    arr["close"][:] = 200
    res = engine.evaluate(_spec(session=spec["session"]), arr)
    assert list(res["long_signal"]) == [False, True, True, True]
    assert "session" in res["checks"]
    assert engine.bar_decision(res, 0) == ("SKIP", "OUTSIDE_SESSION")


def test_opening_range_levels_use_only_completed_range():
    n = 24
    arr = synthetic_arrays(n, start="2026-03-09T13:30:00", step_ms=300_000)  # 09:30 NY (EDT)
    arr["high"][:] = np.arange(n) + 100.0
    arr["low"][:] = 50.0
    spec = _spec(timeframe="M5", session={"timezone": "America/New_York", "start": "09:30", "end": "16:00"},
                 entry={"long": {"left": {"price": "close"}, "op": ">",
                                 "right": {"level": "opening_range_high", "minutes": 15}}})
    res = engine.evaluate(spec, arr)
    orh = res["indicators"] if False else res["checks"]["long: close > opening_range_high(15m)"]["right"]
    assert np.isnan(orh[:3]).all()
    assert (orh[3:] == arr["high"][2]).all()


# ------------------------------------------------------------------ backtester

T0 = "2026-03-10T00:00:00"


def frames(bars, spread=0.2, nan_ask_at=()):
    idx = pd.date_range(T0, periods=len(bars), freq="15min", tz="UTC")
    bid = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=idx)
    bid["volume"] = 10.0
    ask = bid.copy()
    for col in ("open", "high", "low", "close"):
        ask[col] = bid[col] + spread
    for i in nan_ask_at:
        ask.iloc[i] = np.nan
    return bid, ask


FLAT = (100.0, 100.5, 99.5, 100.0)


def long_spec(**over):
    spec = _spec(max_trades_per_day=1)
    spec.update(over)
    return spec


def test_backtest_long_market_fill_at_next_ask_open_and_tp():
    bars = [FLAT, FLAT, (100, 102.2, 99.9, 102.0),      # signal at close of bar 2
            (102.0, 102.5, 101.5, 102.3),               # entry bar: Ask open = 102.2
            (102.3, 104.5, 102.0, 100.0),               # bid high reaches TP 104.2
            FLAT, FLAT]
    bid, ask = frames(bars)
    res = run_rules_backtest(long_spec(target={"type": "r_multiple", "value": 2.0}), bid, ask)
    assert res["engine"] == "STRATEGY_LAB_RULES_V1"
    (t,) = res["trades"]
    assert t["side"] == "long" and t["entry_price"] == pytest.approx(102.2)
    assert t["entry_time"] == bid.index[3].isoformat()
    assert t["exit_reason"] == "TAKE_PROFIT" and t["exit_price"] == pytest.approx(104.2)
    assert t["qty"] == pytest.approx(1.0)                 # 1% of 10000 / (1.0 * 100)
    assert t["pnl"] == pytest.approx(200.0) and t["r_multiple"] == pytest.approx(2.0)
    assert t["bars_held"] == 1 and res["diagnostics"]["ambiguous_bars"] == 0
    assert res["metrics"]["net_profit"] == pytest.approx(200.0)
    assert res["metrics"]["trades"] == 1
    assert len(res["equity"]) == len(bars)
    assert res["spec_sha256"] == engine.spec_sha256(long_spec(target={"type": "r_multiple", "value": 2.0}))


def test_backtest_sl_and_tp_same_bar_assumes_sl_first():
    bars = [FLAT, FLAT, (100, 102.2, 99.9, 102.0), (102.0, 102.5, 101.5, 102.3),
            (102.3, 104.5, 100.9, 100.0), FLAT]
    bid, ask = frames(bars)
    res = run_rules_backtest(long_spec(target={"type": "r_multiple", "value": 2.0}), bid, ask)
    (t,) = res["trades"]
    assert t["exit_reason"] == "STOP_LOSS" and t["exit_price"] == pytest.approx(101.2)
    assert t["pnl"] == pytest.approx(-100.0)
    assert res["diagnostics"]["ambiguous_bars"] == 1


def test_backtest_short_exits_on_ask():
    short = {"left": {"price": "close"}, "op": "<", "right": {"value": 99}}
    bars = [FLAT, FLAT, (100, 100.1, 97.9, 98.0),        # signal
            (98.0, 98.4, 97.5, 98.1),                    # entry Bid open 98.0
            (98.2, 98.9, 98.0, 98.5),                    # bid high 98.9 < 99 but Ask high 99.1 >= 99
            FLAT]
    bid, ask = frames(bars)
    spec = _spec(direction="short", entry={"short": short}, max_trades_per_day=1)
    res = run_rules_backtest(spec, bid, ask)
    (t,) = res["trades"]
    assert t["side"] == "short" and t["entry_price"] == pytest.approx(98.0)
    assert t["exit_reason"] == "STOP_LOSS" and t["exit_price"] == pytest.approx(99.0)
    assert t["pnl"] == pytest.approx(-100.0)


def hold_spec():
    return long_spec(stop={"type": "points", "value": 5.0})


HOLD_BARS = [FLAT, FLAT, (100, 102.2, 99.9, 102.0), (102.0, 102.5, 101.5, 102.3), (102.3, 103.0, 102.0, 102.6),
             (102.6, 103.2, 102.5, 103.0), (103.0, 103.5, 102.8, 103.2)]


def test_spread_multiplier_reduces_profit():
    bid, ask = frames(HOLD_BARS)
    base = run_rules_backtest(hold_spec(), bid, ask)
    wide = run_rules_backtest(hold_spec(), bid, ask, spread_multiplier=1.5)
    assert base["trades"][0]["exit_reason"] == "END_OF_DATA"
    assert wide["metrics"]["net_profit"] < base["metrics"]["net_profit"]
    assert wide["trades"][0]["entry_price"] > base["trades"][0]["entry_price"]


def test_entry_delay_shifts_entries():
    bid, ask = frames(HOLD_BARS)
    a = run_rules_backtest(hold_spec(), bid, ask)["trades"][0]
    b = run_rules_backtest(hold_spec(), bid, ask, entry_delay_bars=1)["trades"][0]
    assert pd.Timestamp(b["entry_time"]) - pd.Timestamp(a["entry_time"]) == pd.Timedelta("15min")
    assert b["entry_price"] == pytest.approx(102.3 + 0.2)


def test_costs_commission_and_slippage_applied():
    bid, ask = frames(HOLD_BARS)
    base = run_rules_backtest(hold_spec(), bid, ask)["trades"][0]
    costly = run_rules_backtest(hold_spec(), bid, ask, commission_per_lot=3.5, slippage_points=10)["trades"][0]
    assert costly["costs"] > base["costs"]
    assert costly["pnl"] < base["pnl"]
    assert costly["entry_price"] == pytest.approx(base["entry_price"] + 0.1)


def test_no_trades_on_missing_ask_bars():
    bars = [FLAT, FLAT, (100, 102.2, 99.9, 102.0), FLAT, FLAT, FLAT]
    bid, ask = frames(bars, nan_ask_at=(3,))
    res = run_rules_backtest(long_spec(), bid, ask)
    assert res["trades"] == []
    assert res["diagnostics"]["missing_ask_bars"] == 1
    assert res["diagnostics"]["skipped_missing_quote"] == 1
    assert res["diagnostics"]["bars"] == len(bars)


def test_bid_missing_bars_dropped_and_counted():
    bid, ask = frames(HOLD_BARS)
    bid.iloc[4] = np.nan
    res = run_rules_backtest(hold_spec(), bid, ask)
    assert res["diagnostics"]["bars"] == len(HOLD_BARS) - 1
    assert res["diagnostics"]["missing_bid_bars"] == 1


def test_backtest_rejects_invalid_spec():
    bid, ask = frames(HOLD_BARS)
    with pytest.raises(ValueError):
        run_rules_backtest(_spec(bogus=True), bid, ask)


def test_backtest_on_templates_runs_and_metrics_present():
    arr = synthetic_arrays(1500, step_ms=900_000)
    idx = pd.to_datetime(arr["time"], unit="ms", utc=True)
    bid = pd.DataFrame({k: arr[k] for k in ("open", "high", "low", "close", "volume")}, index=idx)
    ask = bid.copy()
    for col in ("open", "high", "low", "close"):
        ask[col] = bid[col] + 0.3
    for name, spec in codegen.TEMPLATES.items():
        if spec["timeframe"] != "M15":
            continue
        res = run_rules_backtest(spec, bid, ask)
        assert set(res["metrics"]) >= {"net_profit", "profit_factor", "win_rate", "expectancy", "avg_r",
                                       "max_drawdown", "max_drawdown_pct", "recovery_factor", "sharpe_daily",
                                       "sortino_daily", "trades", "exposure_pct"}
        json.dumps(res)  # JSON-serialisable


# ------------------------------------------------------------------ codegen

def _module_constant(code: str, name: str):
    for node in ast.parse(code).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == name:
            return ast.literal_eval(node.value)
    raise AssertionError(name)


@pytest.mark.parametrize("name", list(codegen.TEMPLATES))
def test_codegen_compiles_and_embeds_same_spec(name):
    spec = codegen.TEMPLATES[name]
    code = codegen.generate_jesse_strategy(spec)
    compile(code, name, "exec")
    sha = engine.spec_sha256(spec)
    assert _module_constant(code, "SPEC_SHA256") == sha
    assert engine.spec_sha256(json.loads(_module_constant(code, "SPEC_JSON"))) == sha
    assert f"class {codegen.class_name(spec)}(InspectorMixin, Strategy)" in code
    assert ast.get_docstring(ast.parse(code)).startswith("STRATEGIE:")
    roots = set()
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add(node.module.split(".")[0])
    assert roots <= {"jesse", "numpy", "json", "math", "strategy_lab"}, roots
    for p in spec.get("params", []):
        assert f'"name": {p["name"]!r}' in code


def test_codegen_rejects_invalid_spec():
    with pytest.raises(ValueError):
        codegen.generate_jesse_strategy(_spec(bogus=1))


def test_write_strategy_refuses_overwrite(tmp_path):
    spec = copy.deepcopy(codegen.TEMPLATES["donchian_breakout"])
    path = codegen.write_strategy(spec, tmp_path)
    assert path == tmp_path / "DonchianBreakout"
    assert (path / "__init__.py").is_file() and json.loads((path / "spec.json").read_text()) == spec
    assert codegen.write_strategy(spec, tmp_path) == path  # identical content: no-op
    changed = copy.deepcopy(spec)
    changed["risk"]["percent"] = 1.5
    with pytest.raises(FileExistsError):
        codegen.write_strategy(changed, tmp_path)
    assert json.loads((path / "spec.json").read_text())["risk"]["percent"] == 0.5
    codegen.write_strategy(changed, tmp_path, overwrite=True)
    assert json.loads((path / "spec.json").read_text())["risk"]["percent"] == 1.5
    assert list(path.glob("__init__.py.bak-*")) and list(path.glob("spec.json.bak-*"))


def test_generated_strategy_matches_rule_engine_with_stubbed_jesse(monkeypatch):
    spec = codegen.TEMPLATES["donchian_breakout"]
    code = codegen.generate_jesse_strategy(spec)
    jesse = types.ModuleType("jesse")
    helpers = types.ModuleType("jesse.helpers")
    helpers.is_livetrading = lambda: False
    strategies = types.ModuleType("jesse.strategies")
    strategies.Strategy = type("Strategy", (), {})
    jesse.helpers, jesse.strategies = helpers, strategies
    events = types.ModuleType("strategy_lab.events")

    class Mixin:
        def record_candle(self, **kw):
            self.recorded.append(kw)

        def record_lifecycle(self, **kw):
            pass

    events.InspectorMixin = Mixin
    for key, mod in (("jesse", jesse), ("jesse.helpers", helpers), ("jesse.strategies", strategies),
                     ("strategy_lab.events", events), ("strategy_lab.rule_engine", engine)):
        monkeypatch.setitem(sys.modules, key, mod)
    namespace = {"__name__": "generated"}
    exec(compile(code, "generated", "exec"), namespace)
    cls = namespace["DonchianBreakout"]

    arr = synthetic_arrays(900, step_ms=3_600_000)
    candles = np.column_stack([arr["time"], arr["open"], arr["close"], arr["high"], arr["low"], arr["volume"]])
    expected = engine.evaluate(spec, arr)
    obj = cls()
    obj.recorded, obj.entry_orders = [], []
    obj.position = types.SimpleNamespace(is_open=False)
    obj.hp = {p["name"]: p["default"] for p in spec["params"]}
    obj.balance, obj.leverage = 10_000.0, 1
    seen = 0
    for k in range(100, 900):
        obj.candles, obj.index = candles[:k + 1], k
        obj.price, obj.current_candle = float(candles[k, 2]), candles[k]
        obj.before()
        assert obj.should_long() == bool(expected["long_signal"][k] and obj._lab_plan.get("long")), k
        seen += int(expected["long_signal"][k])
    assert seen > 0
    assert len(obj.recorded) == 800
    assert all(rec["decision"] in ("PASS", "FAIL", "SKIP") for rec in obj.recorded)


# ------------------------------------------------------------------ real data smoke

def test_real_data_smoke_ema_rsi_pullback_xauusd_m15():
    from strategy_lab.data_manager import DataManager
    try:
        manager = DataManager()
        bid, ask, manifest = load_bid_ask(manager, "XAUUSD", "M15", "2026-02-02", "2026-03-04")
    except Exception as exc:  # dataset not available on this machine
        pytest.skip(f"Dukascopy dataset unavailable: {exc}")
    if len(bid) < 500:
        pytest.skip("not enough XAUUSD data in 2026 window")
    res = run_rules_backtest(codegen.TEMPLATES["ema_rsi_pullback"], bid, ask)
    assert res["engine"] == "STRATEGY_LAB_RULES_V1"
    assert res["diagnostics"]["bars"] > 500
    assert res["metrics"]["trades"] == len(res["trades"])
    assert len(res["equity"]) == res["diagnostics"]["bars"]
    assert all(t["entry_time"] < t["exit_time"] or t["bars_held"] == 0 for t in res["trades"])
    assert "bid_source" in manifest
