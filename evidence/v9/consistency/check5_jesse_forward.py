"""Check 5: forward LabBreakout (frozen Jesse strategy) vs the stored Jesse tester runs over the same real days.

The Jesse runs are existing Docker Jesse backtests (strategy_lab/runs/<id>/trades.csv) of the *same* LabBreakout
bytes (strategy_sha256 == forward_engine.LAB_SHA256) on Dukascopy Bid candles. Nothing is re-run in Docker.
"""
import csv
import hashlib
import json
import tempfile

from _common import *
from strategy_lab.data_manager import DataError
from strategy_lab.forward_engine import ForwardEngine, LAB_SHA256, LAB_SOURCE, MODEL
from strategy_lab.forward_registry import ForwardRegistry

RUNS = ROOT / "strategy_lab" / "runs"
LAB = ROOT / "runtime" / "strategies" / LAB_SOURCE
# prefix -> label
SELECTED = {"05cecfcb": "xauusd_default", "c2239cb4": "xauusd_interval30_ema34", "05010a4c": "xauusd_tight_params",
            "dac05432": "xauusd_short_stop_orders", "0866e3f6": "de40", "8be23934": "us500", "707dbcf6": "us30",
            "6428325a": "xauusd_september_month"}


def find_run(prefix):
    return next(p for p in RUNS.iterdir() if p.name.startswith(prefix))


def jesse_trades(run_dir):
    with open(run_dir / "trades.csv", encoding="utf-8") as stream:
        return [{"side": r["type"], "entry_ms": int(float(r["opened_at"])), "exit_ms": int(float(r["closed_at"])),
                 "entry": float(r["entry_price"]), "exit": float(r["exit_price"]), "qty": float(r["qty"]),
                 "pnl": float(r["PNL"])} for r in csv.DictReader(stream)]


def iso(ms):
    return pd.Timestamp(ms, unit="ms", tz="UTC")


def run_forward(manifest, work: Path):
    params = dict(manifest["parameters"])
    symbol = manifest["symbol"]
    rounded = round(round(params["position_size"] / 0.01) * 0.01, 8)
    note = {"position_size_rounded_to_lot_step": [params["position_size"], rounded]} if rounded != params["position_size"] else {}
    params["position_size"] = rounded
    first = date.fromisoformat(manifest["start_date"])
    last = date.fromisoformat(manifest["end_date"])
    store = DailyPartitionStore(work / "s.sqlite")
    stats = ingest_real_range(store, symbol, first - timedelta(days=1), last)
    frozen = datetime.combine(first, datetime.min.time(), tzinfo=timezone.utc)
    from strategy_lab.research import instruments
    assert not instruments.conversion_info(symbol, "USD")["needed"],         f"{symbol}: quote currency != USD, hardcoded quote_to_account=1 would be an FX fallback"
    spot_contract = {"account_currency": "USD", "contract_size": 100 if symbol == "XAUUSD" else 1, "tick_size": 0.01,
                     "quote_to_account": 1, "volume_step": 0.01, "commission_per_lot_side": 0.0, "slippage_points": 0}
    config = {"strategy": "LabBreakout", "symbol": symbol, "timeframe": "M1", "execution_model": MODEL,
              "data_version": "real-dukascopy", "starting_capital": 1_000_000.0, "trade_risk": 1.0,
              "portfolio_risk_limit": 1.0, "leverage": 1000, "news_filter": {"enabled": False}, "indicators": {},
              "contract": spot_contract,
              "parameters": {k: (int(v) if k in ("order_mode", "entry_interval", "ema_period", "pending_expiry_bars") else float(v))
                             for k, v in params.items()}}
    registry = ForwardRegistry(store)
    registration = registry.freeze(config, {LAB_SOURCE: LAB.read_bytes()}, frozen_at=frozen)
    eng = ForwardEngine(registry)
    portfolio = eng.create("Jesse", currency="USD", starting_capital=1_000_000.0, risk_limit=1.0)
    eng.enroll(portfolio, registration["id"], joined_at=frozen)
    day = first
    while day <= last:
        out = eng.advance(portfolio, as_of=observed_after(day))
        assert out["committed_days"] == [day.isoformat()], out
        day += timedelta(days=1)
    events = eng.report(portfolio)["events"]
    closed = [e["detail"] | {"event_candle_at": e["candle_at"]} for e in events if e["kind"] == "POSITION_CLOSED"]
    return closed, events, {**stats, **note}


def compare(jesse, forward, order_mode=0):
    rows, used = [], set()
    for j in jesse:
        match = None
        for k, f in enumerate(forward):
            if k not in used and f["side"] == j["side"] and abs((pd.Timestamp(f["entry_at"]) - iso(j["entry_ms"])).total_seconds()) <= 60:
                match = k
                break
        if match is None:
            rows.append({"jesse_entry": str(iso(j["entry_ms"])), "side": j["side"], "matched": False})
            continue
        used.add(match)
        f = forward[match]
        entry_delta = (pd.Timestamp(f["entry_at"]) - iso(j["entry_ms"])).total_seconds()
        exit_delta = (pd.Timestamp(f["exit_at"]) - iso(j["exit_ms"])).total_seconds()

        jesse_move = (j["exit"] - j["entry"]) * (1 if j["side"] == "long" else -1)
        fwd_move = (f["exit"] - f["entry"]) * (1 if j["side"] == "long" else -1)
        rows.append({"jesse_entry": str(iso(j["entry_ms"])), "forward_entry": f["entry_at"], "side": j["side"],
                     "matched": True, "entry_delta_s": entry_delta, "exit_delta_s": exit_delta,
                     "entry_delta_min": entry_delta / 60, "exit_delta_min": exit_delta / 60,
                     "jesse_entry_px": j["entry"], "forward_entry_px": f["entry"], "forward_reason": f["exit_reason"],
                     "jesse_move": jesse_move, "forward_move": fwd_move, "move_diff": fwd_move - jesse_move,
                     "forward_spread_open": f["spread_open"]})
    matched = [r for r in rows if r["matched"]]
    extra = [f for k, f in enumerate(forward) if k not in used]
    return {"jesse_trades": len(jesse), "forward_trades": len(forward), "matched_entries": len(matched),
            "unmatched_jesse": len(jesse) - len(matched), "extra_forward": len(extra),
            "entry_same_candle": sum(1 for r in matched if r["entry_delta_min"] == 0),
            "entry_delta_minutes_distribution": {str(k): sum(1 for r in matched if r["entry_delta_min"] == k)
                                                 for k in sorted({r["entry_delta_min"] for r in matched})},
            "exit_same_candle": sum(1 for r in matched if r["exit_delta_min"] == 0),
            "exit_delta_minutes_forward_minus_jesse": {str(k): sum(1 for r in matched if r["exit_delta_min"] == k)
                                                       for k in sorted({r["exit_delta_min"] for r in matched})},
            "sum_move_diff_points": sum(r["move_diff"] for r in matched),
            "mean_forward_spread_open": float(np.mean([r["forward_spread_open"] for r in matched])) if matched else None,
            "trades": rows, "extra_forward_trades": [{"entry_at": f["entry_at"], "side": f["side"]} for f in extra]}


def explain(manifest, entry_ms, forward_signal_candles=None):
    """Why a Jesse entry has no forward counterpart (or vice versa): signal inputs on Bid vs Ask around that candle."""
    p = manifest["parameters"]
    first = date.fromisoformat(manifest["start_date"])
    m1, _ = data_manager().candles(manifest["symbol"], "M1", (first - timedelta(days=2)).isoformat(),
                                   (date.fromisoformat(manifest["end_date"]) + timedelta(days=1)).isoformat())
    period = int(p["ema_period"])
    day0 = pd.Timestamp(first, tz="UTC")
    in_range = m1[m1.index >= day0]
    warm = m1[m1.index < day0].iloc[-210:]
    mode = int(p["order_mode"])
    q = "bid" if mode == 2 else "ask"
    signal_at = iso(entry_ms) - pd.Timedelta(minutes=1)
    fwd_ema = in_range[f"{q}_close"].ewm(alpha=2 / (period + 1), adjust=False).mean()
    jes_src = pd.concat([warm, in_range])["bid_close"]
    jes_ema = jes_src.ewm(alpha=2 / (period + 1), adjust=False).mean()
    row = in_range.loc[signal_at]
    idx = in_range.index.get_loc(signal_at)
    side = (lambda c, e: c < e) if mode == 2 else (lambda c, e: c > e)
    return {"signal_candle": str(signal_at), "index": int(idx), "scheduled": bool(idx > 0 and idx % int(p["entry_interval"]) == 0),
            "forward_inputs": {"quote": q, "close": float(row[f"{q}_close"]), "ema": float(fwd_ema.loc[signal_at]),
                               "trend_pass": bool(side(row[f"{q}_close"], fwd_ema.loc[signal_at])),
                               "range": float(row[f"{q}_high"] - row[f"{q}_low"]), "max_range": p["max_bar_range"]},
            "jesse_like_inputs": {"quote": "bid", "close": float(row.bid_close), "ema_approx": float(jes_ema.loc[signal_at]),
                                  "trend_pass": bool(side(row.bid_close, jes_ema.loc[signal_at])),
                                  "range": float(row.bid_high - row.bid_low)}}


if __name__ == "__main__":
    payload = {"lab_sha256": LAB_SHA256, "file_sha256": hashlib.sha256(LAB.read_bytes()).hexdigest(),
               "runs": {}}
    for prefix, label in SELECTED.items():
        run_dir = find_run(prefix)
        manifest = json.loads((run_dir / "manifest.json").read_text())
        assert manifest["strategy_sha256"] == LAB_SHA256, (prefix, manifest["strategy_sha256"])
        jesse = jesse_trades(run_dir)
        try:
            forward, events, stats = run_forward(manifest, Path(tempfile.mkdtemp(prefix=f"lab_{label}_")))
        except DataError as exc:
            payload["runs"][label] = {"run_id": run_dir.name, "parameters": manifest["parameters"],
                                      "status": "FORWARD_REFUSED_REGISTRATION", "reason": str(exc)}
            print(label, "refused:", exc, flush=True)
            continue
        result = compare(jesse, forward, int(manifest['parameters']['order_mode']))
        result.update(run_id=run_dir.name, symbol=manifest["symbol"], start=manifest["start_date"], end=manifest["end_date"],
                      parameters=manifest["parameters"], ingest=stats,
                      forward_rejections=[e["detail"].get("reason") for e in events if e["kind"] in ("ORDER_REJECTED", "SIGNAL_REJECTED")][:5])
        result["explanations_unmatched_jesse"] = [explain(manifest, int(pd.Timestamp(r["jesse_entry"]).value // 10**6))
                                                  for r in result["trades"] if not r["matched"]][:6]
        result["explanations_extra_forward"] = [explain(manifest, int((pd.Timestamp(f["entry_at"])).value // 10**6))
                                                for f in result["extra_forward_trades"]][:6]
        payload["runs"][label] = result
        print(label, {k: result[k] for k in ("jesse_trades", "forward_trades", "matched_entries", "entry_same_candle", "exit_same_candle",
                                              "unmatched_jesse", "extra_forward", "sum_move_diff_points", "mean_forward_spread_open")}, flush=True)
    print(save("check5_jesse_forward.json", payload))
