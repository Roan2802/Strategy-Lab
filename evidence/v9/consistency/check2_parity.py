"""Check 2: forward engine (day-by-day, forward_rules) vs one research backtest over the same real range."""
import copy
import sys

from parity_lib import *
from strategy_lab.research import codegen

D = date


def tpl(key, tf=None, **over):
    spec = copy.deepcopy(codegen.TEMPLATES[key])
    spec.pop("description", None)
    if tf:
        spec["timeframe"] = tf
    spec.update(over)
    return spec


def rich(order, tf="M1"):
    return {"schema": engine.SCHEMA, "name": f"Rich{order['type'].title()}{tf}", "symbol": "XAUUSD", "timeframe": tf,
            "direction": "both",
            "entry": {"long": {"left": {"indicator": "rsi", "period": 5}, "op": "<",
                               "right": {"value": {"param": "low_level"}}},
                      "short": {"left": {"indicator": "rsi", "period": 5}, "op": ">", "right": {"value": 80}}},
            "stop": {"type": "atr", "period": 5, "mult": 1.5}, "target": {"type": "r_multiple", "value": 1.5},
            "break_even": {"activate_r": 0.8}, "trailing": {"activate_r": 1.0, "atr_mult": 1.0, "period": 5},
            "time_exit_bars": 12, "cooldown_bars": 2, "order": order, "risk": {"percent": 0.5},
            "params": [{"name": "low_level", "type": "float", "min": 10, "max": 40, "default": 20}]}


def simple_d1():
    return {"schema": engine.SCHEMA, "name": "SimpleD1", "symbol": "XAUUSD", "timeframe": "D1", "direction": "both",
            "entry": {"long": {"left": {"price": "close"}, "op": "crosses_above",
                               "right": {"indicator": "ema", "period": 8}},
                      "short": {"left": {"price": "close"}, "op": "crosses_below",
                                "right": {"indicator": "ema", "period": 8}}},
            "stop": {"type": "atr", "period": 10, "mult": 1.5}, "target": {"type": "r_multiple", "value": 2.0},
            "trailing": {"activate_r": 1.0, "atr_mult": 2.0, "period": 10}, "time_exit_bars": 6,
            "risk": {"percent": 0.5}}


def with_loose_donchian(tf):
    spec = tpl("donchian_breakout", tf)
    spec["params"] = [dict(p, max=40) if p["name"] == "channel" else p for p in spec["params"]]
    return spec


def feature_spec(name, tf, entry, **over):
    base = {"schema": engine.SCHEMA, "name": name, "symbol": "XAUUSD", "timeframe": tf, "direction": "both",
            "entry": entry, "order": {"type": "market"}, "stop": {"type": "atr", "period": 14, "mult": 1.5},
            "target": {"type": "r_multiple", "value": 2.0}, "risk": {"percent": 0.5}}
    base.update(over)
    return base


SWING_ATR = feature_spec("SwingAtrTarget", "M5", {
    "long": {"left": {"indicator": "rsi", "period": 14}, "op": "crosses_above", "right": {"value": 45}},
    "short": {"left": {"indicator": "rsi", "period": 14}, "op": "crosses_below", "right": {"value": 55}}},
    stop={"type": "swing", "lookback": 10}, target={"type": "atr", "mult": 2.0, "period": 14},
    break_even={"activate_r": 1.0}, cooldown_bars=3)
HTF_FILTER = feature_spec("HtfFilter", "M15", {
    "long": {"all": [{"left": {"price": "close"}, "op": ">", "right": {"indicator": "ema", "period": 20, "timeframe": "H4"}},
                     {"left": {"indicator": "rsi", "period": 14}, "op": "crosses_above", "right": {"value": 50}}]},
    "short": {"all": [{"left": {"price": "close"}, "op": "<", "right": {"indicator": "ema", "period": 20, "timeframe": "H4"}},
                      {"left": {"indicator": "rsi", "period": 14}, "op": "crosses_below", "right": {"value": 50}}]}},
    order={"type": "limit", "offset_atr": 0.3, "atr_period": 14, "expiry_bars": 3}, time_exit_bars=16)
LEVELS = feature_spec("LevelsSession", "M5", {
    "long": {"left": {"price": "close"}, "op": "crosses_above", "right": {"level": "prev_day_high"}},
    "short": {"left": {"price": "close"}, "op": "crosses_below", "right": {"level": "prev_day_low"}}},
    session={"timezone": "America/New_York", "start": "09:30", "end": "16:00"}, max_trades_per_day=2,
    trailing={"activate_r": 1.0, "atr_mult": 2.0, "period": 14})
A, B = D(2026, 9, 14), D(2026, 10, 9)
CASES = {
    "rich_m1_market": (rich({"type": "market"}), {"low_level": 20}, D(2026, 9, 21), D(2026, 9, 25)),
    "rich_m1_stop": (rich({"type": "stop", "offset_atr": 0.3, "atr_period": 5, "expiry_bars": 3}), {"low_level": 20},
                     D(2026, 9, 21), D(2026, 9, 25)),
    "rich_m1_limit": (rich({"type": "limit", "offset_atr": 0.4, "atr_period": 5, "expiry_bars": 4}), {"low_level": 20},
                      D(2026, 9, 21), D(2026, 9, 25)),
    "ema_rsi_m5": (tpl("ema_rsi_pullback", "M5"), None, A, B),
    "ema_rsi_m15": (tpl("ema_rsi_pullback", "M15"), None, A, B),
    "orb_m5": (tpl("opening_range_breakout"), None, A, B),
    "swing_atr_target_m5": (SWING_ATR, None, A, B),
    "htf_h4_filter_limit_m15": (HTF_FILTER, None, A, B),
    "levels_session_m5": (LEVELS, None, A, B),
    "bollinger_m15": (tpl("bollinger_mean_reversion"), None, A, B),
    "nr4_h1_stop": (tpl("nr4_inside_bar_breakout"), None, A, B),
    "donchian_h1_trailing": (with_loose_donchian("H1"), None, A, B),
    "donchian_h4_trailing": (with_loose_donchian("H4"), None, D(2026, 8, 17), B, 500000.0),
    "simple_d1": (simple_d1(), None, D(2026, 6, 1), B, 500000.0),
}

if __name__ == "__main__":
    names = sys.argv[1:] or list(CASES)
    payload = {"engine": "forward_rules.step via ForwardEngine.advance (one UTC day per call)",
               "backtest": "research.backtest.run_rules_backtest on DataManager Bid/Ask bars",
               "contract_assumed_not_broker_verified": CONTRACT, "capital": CAPITAL, "cases": {}}
    for name in names:
        spec, params, first, last, *rest = CASES[name]
        assert not engine.validate_spec(spec), engine.validate_spec(spec)
        result = run_case(name, spec, params, first, last, capital=rest[0] if rest else CAPITAL)
        payload["cases"][name] = result
        fb = result["forward_vs_backtest_aligned"]
        lh = result["backtest_longer_history_vs_aligned"]
        print(name, {k: fb[k] for k in ("trades_backtest", "trades_forward", "matched", "exact_trades",
                                         "net_pnl_backtest", "net_pnl_forward", "net_pnl_difference")},
              {k: v["trades"] for k, v in fb["causes"].items()}, "| longer-history:", lh["trades_forward"],
              lh["matched"], lh["exact_trades"], round(lh["net_pnl_difference"], 2), "| rej", result["forward_order_rejections"][:3],
              "t=", result["forward_runtime_s"], flush=True)
        save("check2_parity.json" if len(names) == len(CASES) else f"check2_parity_{'_'.join(names)}.json", payload)
