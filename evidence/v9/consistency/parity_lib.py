"""Forward-engine vs research-backtest parity harness on real Dukascopy data."""
from __future__ import annotations

import tempfile

from _common import *
from runtime.strategy_lab import rule_engine as engine
from runtime.strategy_lab.execution import ContractSpec
from strategy_lab import forward_rules
from strategy_lab.forward_engine import ForwardEngine
from strategy_lab.forward_registry import ForwardRegistry
from strategy_lab.research import backtest

TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
# Assumed (NOT broker-verified) XAUUSD research contract; identical in forward and backtest.
CONTRACT = {"account_currency": "USD", "contract_size": 100, "tick_size": 0.01, "quote_to_account": 1,
            "volume_step": 0.01, "commission_per_lot_side": 3.5, "slippage_points": 3}
CAPITAL = 10000.0


def warm_days(spec, params):
    need = forward_rules.required_warmup_bars(spec, params)
    return int(need * TF_MIN[spec["timeframe"]] / 1440 * 1.7) + 8


def build_forward(path: Path, spec: dict, params: dict | None, first_day: date, last_day: date, *,
                  symbol="XAUUSD", capital=CAPITAL, contract=CONTRACT, buffer_cap=None):
    """Real-data store with every day published; returns (store, engine, portfolio_id, registration, stats)."""
    from strategy_lab.research import instruments
    assert not instruments.conversion_info(symbol, contract["account_currency"])["needed"],         f"{symbol}: quote currency != account currency, hardcoded quote_to_account=1 would be an FX fallback"
    store = DailyPartitionStore(path)
    start = first_day - timedelta(days=warm_days(spec, params))
    stats = ingest_real_range(store, symbol, start, last_day)
    frozen = datetime.combine(first_day, datetime.min.time(), tzinfo=timezone.utc)
    warm = forward_rules.warmup_from_fetch(forward_rules.store_fetch(store, symbol), spec, params,
                                           as_of=frozen, buffer_cap=buffer_cap)
    config = forward_rules.build_configuration(
        spec, params, contract=contract, starting_capital=capital, trade_risk=0.05, portfolio_risk_limit=0.5,
        leverage=500, warmup=warm, data_version="real-dukascopy")
    registration = forward_rules.freeze(ForwardRegistry(store), config, frozen_at=frozen)
    engine_ = ForwardEngine(ForwardRegistry(store))
    portfolio = engine_.create("Parity", currency="USD", starting_capital=capital, risk_limit=0.5)
    engine_.enroll(portfolio, registration["id"], joined_at=frozen)
    return store, engine_, portfolio, registration, {**stats, "ingest_start": start.isoformat()}


def advance_daily(engine_, portfolio, first_day: date, last_day: date, *, fresh_engine=None):
    """Publish-then-process one day at a time; ``fresh_engine`` builds a new engine (process restart) per day."""
    day = first_day
    while day <= last_day:
        eng = fresh_engine() if fresh_engine else engine_
        result = eng.advance(portfolio, as_of=observed_after(day))
        assert result["status"] in ("UP TO DATE", "BATCH_LIMIT") and result["committed_days"] == [day.isoformat()], result
        day += timedelta(days=1)


def closed_trades(report):
    return [{"event_id": e["id"], **e["detail"]} for e in report["events"] if e["kind"] == "POSITION_CLOSED"]


def backtest_run(spec, params, start, end, trade_from, *, symbol="XAUUSD", capital=CAPITAL, contract=CONTRACT):
    bid, ask, manifest = backtest.load_bid_ask(data_manager(), symbol, spec["timeframe"], start, end)
    result = backtest.run_rules_backtest(spec, bid, ask, params, capital=capital, contract=ContractSpec(**contract),
                                         trade_from=trade_from)
    return result


def _ts(value):
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def compare(forward: list[dict], bt_trades: list[dict], tf: str) -> dict:
    """Match by (side, entry bar bucket) in order; classify every deviation with a root cause."""
    minutes = TF_MIN[tf]
    bt = [t for t in bt_trades if t["exit_reason"] != "END_OF_DATA"]
    bucket = lambda ts: _ts(ts).floor(f"{minutes}min") if minutes < 1440 else _ts(ts).floor("1D")
    fwd_pool = list(forward)
    rows, missing = [], []
    for trade in bt:
        key = (trade["side"], _ts(trade["entry_time"]))
        match = next((f for f in fwd_pool if f["side"] == key[0] and bucket(f["entry_at"]) == key[1]), None)
        if match is None:
            missing.append({"entry_time": trade["entry_time"], "side": trade["side"], "pnl": trade["pnl"]})
            continue
        fwd_pool.remove(match)
        entry_same = _ts(match["entry_at"]) == _ts(trade["entry_time"])
        exit_same = _ts(match["exit_at"]) == _ts(trade["exit_time"])
        exit_bar_same = bucket(match["exit_at"]) == _ts(trade["exit_time"])
        reason_same = match["exit_reason"] == trade["exit_reason"]
        price_close = lambda a, b: abs(a - b) <= 1e-9 * max(1.0, abs(b))
        entry_px = price_close(match["entry"], trade["entry_price"])
        exit_px = price_close(match["exit"], trade["exit_price"])
        qty_same = abs(match["quantity"] - trade["qty"]) <= 1e-9
        pnl_diff = match["net_pnl"] - trade["pnl"]
        if not entry_px:
            cause = "ENTRY_PRICE_DIFFERS"
        elif not qty_same:
            cause = "SIZE_DRIFT_FROM_EARLIER_PNL_DIFFERENCE"
        elif not reason_same or not exit_px:
            cause = "INTRABAR_EXIT_RESOLVED_ON_M1"
        elif not exit_bar_same:
            cause = "EXIT_IN_DIFFERENT_BAR"
        elif entry_same and exit_same:
            cause = "EXACT"
        else:
            cause = "TIMESTAMP_GRANULARITY_ONLY"
        rows.append({"entry_bt": trade["entry_time"], "entry_fwd": match["entry_at"], "side": trade["side"],
                     "exit_bt": trade["exit_time"], "exit_fwd": match["exit_at"],
                     "reason_bt": trade["exit_reason"], "reason_fwd": match["exit_reason"],
                     "entry_px_bt": trade["entry_price"], "entry_px_fwd": match["entry"],
                     "exit_px_bt": trade["exit_price"], "exit_px_fwd": match["exit"],
                     "qty_bt": trade["qty"], "qty_fwd": match["quantity"],
                     "pnl_bt": trade["pnl"], "pnl_fwd": match["net_pnl"], "pnl_diff": pnl_diff, "cause": cause})
    extra = [{"entry_at": f["entry_at"], "side": f["side"], "pnl": f["net_pnl"]} for f in fwd_pool]
    causes: dict = {}
    for r in rows:
        c = causes.setdefault(r["cause"], {"trades": 0, "sum_pnl_diff": 0.0, "sum_abs_pnl_diff": 0.0})
        c["trades"] += 1
        c["sum_pnl_diff"] += r["pnl_diff"]
        c["sum_abs_pnl_diff"] += abs(r["pnl_diff"])
    pnl_bt = sum(t["pnl"] for t in bt)
    pnl_fwd = sum(f["net_pnl"] for f in forward)
    return {"trades_backtest": len(bt), "trades_forward": len(forward), "matched": len(rows),
            "missing_in_forward": missing, "extra_in_forward": extra, "causes": causes,
            "net_pnl_backtest": pnl_bt, "net_pnl_forward": pnl_fwd, "net_pnl_difference": pnl_fwd - pnl_bt,
            "sum_abs_trade_pnl_difference": sum(abs(r["pnl_diff"]) for r in rows),
            "sum_pnl_unmatched_backtest": sum(m["pnl"] for m in missing),
            "sum_pnl_unmatched_forward": sum(e["pnl"] for e in extra),
            "deviating_trades": [r for r in rows if r["cause"] != "EXACT"],
            "exact_trades": sum(1 for r in rows if r["cause"] == "EXACT")}


def run_case(name, spec, params, first_day: date, last_day: date, *, longer_history_days=45, workdir=None,
             capital=CAPITAL):
    tf = spec["timeframe"]
    work = Path(workdir or tempfile.mkdtemp(prefix=f"parity_{name}_"))
    elapsed = timer()
    store, eng, portfolio, registration, stats = build_forward(work / "store.sqlite", spec, params, first_day, last_day, capital=capital)
    warm = registration["configuration"]["warmup"]
    advance_daily(eng, portfolio, first_day, last_day)
    report = eng.report(portfolio)
    forward = closed_trades(report)
    t_fwd = elapsed()
    end = (last_day + timedelta(days=1)).isoformat()
    warm_first = pd.Timestamp(warm["bars"][0][0], unit="ms", tz="UTC")
    frozen = datetime.combine(first_day, datetime.min.time(), tzinfo=timezone.utc)
    aligned = backtest_run(spec, params, warm_first.isoformat(), end, frozen, capital=capital)
    longer = backtest_run(spec, params, (warm_first - pd.Timedelta(days=longer_history_days)).isoformat(), end, frozen,
                          capital=capital)
    rejected = [e["detail"]["reason"] for e in report["events"] if e["kind"] in ("ORDER_REJECTED",)]
    diag = aligned["diagnostics"]
    out = {"case": name, "timeframe": tf, "order": (spec.get("order") or {"type": "market"})["type"], "capital": capital, "first_day": first_day.isoformat(),
           "last_day": last_day.isoformat(), "warmup_bars": warm["count"], "buffer_cap": warm["buffer_cap"],
           "ingest": stats, "forward_runtime_s": t_fwd,
           "forward_vs_backtest_aligned": compare(forward, aligned["trades"], tf),
           "backtest_longer_history_vs_aligned": compare(
               [{"entry_at": t["entry_time"], "exit_at": t["exit_time"], "side": t["side"],
                 "exit_reason": t["exit_reason"], "entry": t["entry_price"], "exit": t["exit_price"],
                 "quantity": t["qty"], "net_pnl": t["pnl"]} for t in longer["trades"] if t["exit_reason"] != "END_OF_DATA"],
               aligned["trades"], tf),
           "forward_order_rejections": rejected,
           "backtest_diagnostics": {k: diag.get(k) for k in ("ambiguous_bars", "unfilled_orders", "signals",
                                                           "rejected_invalid_stop", "rejected_min_lot", "missing_ask_bars")}}
    return out
