"""Check 1: no look-ahead (prefix test) on real Dukascopy data + causal aggregation from M1.

Prefix test: evaluate the rule spec on the full array, then re-evaluate on data[:i+1] for a sample of bars and
require identical 3-valued entry states (both sides) and identical exit snapshots at bar i.
"""
import copy
import random

from _common import *
from runtime.strategy_lab import rule_engine as engine
from strategy_lab import forward_rules
from strategy_lab.research import codegen

SYMBOL = "XAUUSD"
TFS = ["M1", "M5", "M15", "H1", "H4", "D1"]
BARS = {"M1": 3000, "M5": 3000, "M15": 3000, "H1": 3000, "H4": 2500, "D1": 1500}
TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}
FREQ = {"M5": "5min", "M15": "15min", "H1": "1h", "H4": "4h", "D1": "1D"}
HTF = {"M1": "H1", "M5": "H1", "M15": "H4", "H1": "H4", "H4": "D1", "D1": None}
END = "2026-10-12"
SAMPLE_SIGNALS, SAMPLE_RANDOM = 25, 40


def stress_specs(tf):
    base = {"schema": engine.SCHEMA, "symbol": SYMBOL, "timeframe": tf, "direction": "both",
            "order": {"type": "market"}, "stop": {"type": "atr", "mult": 1.5, "period": 14},
            "target": {"type": "r_multiple", "value": 2.0}, "risk": {"percent": 0.5},
            "trailing": {"activate_r": 1.0, "atr_mult": 2.0, "period": 14}}
    specs = {}
    specs["stress_levels"] = {**base, "name": "StressLevels", "entry": {
        "long": {"any": [{"left": {"price": "close"}, "op": ">", "right": {"level": "prev_day_high"}},
                         {"left": {"price": "close"}, "op": "crosses_above", "right": {"level": "session_open"}}]},
        "short": {"any": [{"left": {"price": "close"}, "op": "<", "right": {"level": "prev_day_low"}},
                          {"left": {"price": "close"}, "op": "crosses_below", "right": {"level": "session_open"}}]}},
        "session": {"timezone": "America/New_York", "start": "09:30", "end": "16:00"}}
    specs["stress_patterns"] = {**base, "name": "StressPatterns", "entry": {
        "long": {"any": [{"pattern": "bullish_engulfing"}, {"pattern": "liquidity_sweep_low", "lookback": 10},
                         {"pattern": "fvg_bull", "lookback": 3}, {"pattern": "nr4"}]},
        "short": {"any": [{"pattern": "bearish_engulfing"}, {"pattern": "liquidity_sweep_high", "lookback": 10},
                          {"pattern": "fvg_bear", "lookback": 3}, {"pattern": "inside_bar"}]}}}
    specs["stress_indicators"] = {**base, "name": "StressIndicators", "entry": {
        "long": {"all": [{"left": {"indicator": "macd_hist"}, "op": "crosses_above", "right": {"value": 0}},
                         {"left": {"indicator": "adx", "period": 14}, "op": ">", "right": {"value": 15}}]},
        "short": {"any": [{"left": {"indicator": "rsi", "period": 14}, "op": "crosses_above", "right": {"value": 60}},
                          {"left": {"price": "close"}, "op": "crosses_below", "right": {"indicator": "vwap"}},
                          {"left": {"indicator": "roc", "period": 10}, "op": "<", "right": {"value": -0.2}},
                          {"left": {"price": "close"}, "op": "<", "right": {"indicator": "bb_lower", "period": 20}}]}}}
    if HTF[tf]:
        specs["stress_htf"] = {**base, "name": "StressHtf", "entry": {
            "long": {"left": {"price": "close"}, "op": ">",
                     "right": {"indicator": "ema", "period": 20, "timeframe": HTF[tf]}},
            "short": {"left": {"indicator": "rsi", "period": 14, "timeframe": HTF[tf]}, "op": "crosses_below",
                      "right": {"value": 50}}}}
    return specs


def spec_set(tf):
    out = {}
    for key, template in codegen.TEMPLATES.items():
        item = copy.deepcopy(template)
        item["timeframe"] = tf
        out[key] = item
    out.update(stress_specs(tf))
    return out


def arrays(frame):
    index = frame.index.tz_convert("UTC")
    ms = ((index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta("1ms")).to_numpy("int64")
    return {"time": ms, "open": frame.bid_open.to_numpy(float), "high": frame.bid_high.to_numpy(float),
            "low": frame.bid_low.to_numpy(float), "close": frame.bid_close.to_numpy(float),
            "volume": frame.bid_volume.to_numpy(float)}


def cut(a, n):
    return {k: v[:n] for k, v in a.items()}


def same(x, y):
    return (np.isnan(x) and np.isnan(y)) or x == y


def prefix_test(spec, a, rng):
    full = engine.evaluate(spec, a)
    n = len(a["time"])
    first = min(n - 1, engine.lookback_bars(engine.resolve(spec, None)) // 4)
    longs, shorts = np.flatnonzero(full["long_signal"]), np.flatnonzero(full["short_signal"])
    signals = [int(i) for i in list(longs) + list(shorts)]
    picks = set(rng.sample(signals, min(len(signals), SAMPLE_SIGNALS * 2)))
    picks |= set(rng.sample(range(first, n), min(SAMPLE_RANDOM, n - first)))
    picks |= {n - 1, n - 2, first}
    mismatches = []
    for i in sorted(picks):
        part = engine.evaluate(spec, cut(a, i + 1), validate=False)
        bad = [side for side in ("long", "short") if int(part["states"][side][i]) != int(full["states"][side][i])]
        for key, series in full["exits"].items():
            if not same(float(part["exits"][key][i]), float(series[i])):
                bad.append(f"exit:{key}")
        if bad:
            mismatches.append({"bar": int(i), "time": int(a["time"][i]), "fields": bad})
    return {"bars": n, "long_signals": int(len(longs)), "short_signals": int(len(shorts)),
            "bars_tested": len(picks), "mismatches": mismatches, "pass": not mismatches}


def prefix_section():
    dm, rng = data_manager(), random.Random(20261109)
    results = {}
    for tf in TFS:
        frame, _ = dm.candles(SYMBOL, tf, None, END)
        frame = frame.iloc[-BARS[tf]:]
        a = arrays(frame)
        results[tf] = {"first_bar": str(frame.index[0]), "last_bar": str(frame.index[-1]), "specs": {}}
        for name, spec in spec_set(tf).items():
            errs = engine.validate_spec(spec)
            if errs:
                results[tf]["specs"][name] = {"skipped": "; ".join(errs)}
                continue
            results[tf]["specs"][name] = prefix_test(spec, a, rng)
        print(tf, {k: (v.get("pass"), v.get("long_signals"), v.get("short_signals"))
                   for k, v in results[tf]["specs"].items()}, flush=True)
    return results


def aggregation_section():
    """Compare DataManager (research) aggregation, an independent re-aggregation and the forward aggregator."""
    dm = data_manager()
    m1, _ = dm.candles(SYMBOL, "M1", "2026-09-01", "2026-10-12")
    out = {}
    rng = random.Random(7)
    cols = ["bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close", "bid_volume"]
    for tf in TFS[1:]:
        ms = TF_MIN[tf] * 60_000
        bars, _ = dm.candles(SYMBOL, tf, "2026-09-01", "2026-10-12")
        epoch = ((m1.index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta("1ms")).to_numpy("int64")
        ref = m1.assign(_b=epoch // ms).groupby("_b").agg(
            bid_open=("bid_open", "first"), bid_high=("bid_high", "max"), bid_low=("bid_low", "min"),
            bid_close=("bid_close", "last"), ask_open=("ask_open", "first"), ask_high=("ask_high", "max"),
            ask_low=("ask_low", "min"), ask_close=("ask_close", "last"), bid_volume=("bid_volume", "sum"),
            n=("bid_open", "size"))
        ref.index = ref.index * ms
        label_ms = ((bars.index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta("1ms")).to_numpy("int64")
        aligned = bool((label_ms % ms == 0).all())
        bars_ms = bars.copy()
        bars_ms.index = label_ms
        both = bars_ms[cols].join(ref[cols], rsuffix="_ref", how="outer")
        worst = max(float((both[c] - both[c + "_ref"]).abs().max()) for c in cols)
        incomplete = int((ref["n"] < TF_MIN[tf]).sum())
        immutable_bad = partial_flagged = forward_bad = tests = 0
        for _ in range(15):
            cut_at = m1.index[rng.randrange(len(m1) // 3, len(m1))]
            part = m1[m1.index < cut_at]
            part_bars = dm._aggregate(part, FREQ[tf])
            closed = part_bars[part_bars.index + pd.Timedelta(minutes=TF_MIN[tf]) <= cut_at]
            tests += 1
            full_same = bars.reindex(closed.index)
            if not np.allclose(closed[cols[:8]].to_numpy(), full_same[cols[:8]].to_numpy()):
                immutable_bad += 1
            if part_bars.index[-1] + pd.Timedelta(minutes=TF_MIN[tf]) > cut_at:
                partial_flagged += 1
            fwd = forward_rules.aggregate_bid(part, tf, until=cut_at.to_pydatetime())
            fwd_frame = pd.DataFrame(fwd, columns=["t", "o", "h", "l", "c", "v"]).set_index("t")
            ref_frame = closed.copy()
            ref_frame.index = ((ref_frame.index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta("1ms")).to_numpy("int64")
            joined = fwd_frame.join(ref_frame[["bid_open", "bid_high", "bid_low", "bid_close", "bid_volume"]], how="outer")
            if len(fwd_frame) != len(closed) or not np.allclose(
                    joined[["o", "h", "l", "c", "v"]].to_numpy(),
                    joined[["bid_open", "bid_high", "bid_low", "bid_close", "bid_volume"]].to_numpy()):
                forward_bad += 1
        out[tf] = {"bars": int(len(bars)), "labels_aligned_to_utc_multiples": aligned,
                   "max_abs_diff_vs_independent_aggregation": worst,
                   "bars_with_fewer_m1_than_nominal": incomplete,
                   "truncation_tests": tests, "truncation_closed_bar_changed": immutable_bad,
                   "truncation_last_bar_partial_cases": partial_flagged,
                   "forward_aggregate_vs_research_mismatch_tests": forward_bad,
                   "pass": bool(aligned and worst < 1e-9 and immutable_bad == 0 and forward_bad == 0)}
        print(tf, out[tf], flush=True)
    return out


if __name__ == "__main__":
    elapsed = timer()
    payload = {"symbol": SYMBOL, "data_end": END, "source": "C:/AI/Dukascopy_data via DataManager (read-only)",
               "sample": {"signals_per_spec": SAMPLE_SIGNALS * 2, "random_bars": SAMPLE_RANDOM}}
    payload["aggregation"] = aggregation_section()
    payload["prefix"] = prefix_section()
    flat = [(tf, name, r) for tf, v in payload["prefix"].items() for name, r in v["specs"].items() if "pass" in r]
    payload["summary"] = {"specs_tested": len(flat), "specs_failed": [(a, b) for a, b, r in flat if not r["pass"]],
                          "specs_with_no_signals": [(a, b) for a, b, r in flat if r["long_signals"] + r["short_signals"] == 0],
                          "specs_skipped": [(tf, n, r["skipped"]) for tf, v in payload["prefix"].items()
                                            for n, r in v["specs"].items() if "skipped" in r],
                          "total_prefix_evaluations": sum(r["bars_tested"] for _, _, r in flat),
                          "runtime_s": elapsed()}
    print(save("check1_lookahead.json", payload))
    print(payload["summary"])
