"""Event-driven bar backtester on Dukascopy Bid AND Ask OHLC driven by rule_engine signals.

Signals are computed on Bid candles at bar close (rule_engine, causal). Fills use the executable quote
side: long entries buy on Ask, long exits sell on Bid; shorts mirror. See STRATEGY_BUILDER_GUIDE.md.
"""
from __future__ import annotations

import dataclasses
import math
from datetime import timedelta

import numpy as np
import pandas as pd

from runtime.strategy_lab import execution as xm, rule_engine as engine
from runtime.strategy_lab.execution import ContractSpec, conservative_ohlc_exit, position_pnl

ENGINE_NAME = "STRATEGY_LAB_RULES_V1"
_COLS = ("open", "high", "low", "close", "volume")
# symbol -> (contract_size, tick_size, volume_step); quote currency assumed == account currency (USD)
_DEFAULT_CONTRACTS = {
    "XAUUSD": (100.0, 0.01, 0.01), "XAGUSD": (5000.0, 0.001, 0.01), "EURUSD": (100000.0, 0.00001, 0.01),
    "GBPUSD": (100000.0, 0.00001, 0.01), "AUDUSD": (100000.0, 0.00001, 0.01), "BTCUSD": (1.0, 0.01, 0.01),
    "DE40": (1.0, 0.1, 0.01), "US500": (1.0, 0.1, 0.01), "USTEC": (1.0, 0.1, 0.01),
    "USDJPY": (100000.0, 0.001, 0.01), "USDCAD": (100000.0, 0.00001, 0.01), "US30": (1.0, 0.1, 0.01),
    "USATECHIDXUSD": (1.0, 0.1, 0.01),
}
# symbol -> (quote currency, conversion pair, True if account-per-quote = pair mid, False if 1/mid); USD quotes absent
_NON_USD_QUOTE = {"DE40": ("EUR", "EURUSD", True), "DEUIDXEUR": ("EUR", "EURUSD", True),
                  "USDJPY": ("JPY", "USDJPY", False), "USDCAD": ("CAD", "USDCAD", False)}


def quote_conversion(symbol: str):
    """(quote_ccy, conversion_pair, multiply) for non-USD-quoted symbols, else None."""
    return _NON_USD_QUOTE.get(symbol.upper())
_MAX_EQUITY_POINTS = 50_000


def load_bid_ask(data_manager, symbol: str, timeframe: str, start: str | None, end: str | None):
    """Adapt DataManager.candles output to (bid_df, ask_df, manifest) with open/high/low/close/volume columns."""
    frame, manifest = data_manager.candles(symbol, timeframe, start, end)
    bid = frame[[f"bid_{c}" for c in _COLS]].copy()
    ask = frame[[f"ask_{c}" for c in _COLS]].copy()
    bid.columns = list(_COLS)
    ask.columns = list(_COLS)
    return bid, ask, manifest


def default_contract(symbol: str, commission_per_lot: float = 0.0, slippage_points: float = 0.0) -> ContractSpec:
    """Account currency USD. For EUR/JPY/CAD-quoted symbols ``quote_to_account`` stays 1.0 here (placeholder, NOT a
    rate): pass ``conversion=`` (fx.FxService.series) to run_rules_backtest for the real causal rate, otherwise the
    run is flagged ``currency_status=QUOTE_CURRENCY_UNCONVERTED`` / ``fx_status=FX_RATE_UNAVAILABLE`` and its money
    metrics are blocked (the amounts would be quote currency, not the account currency)."""
    size, tick, step = _DEFAULT_CONTRACTS.get(symbol.upper(), (1.0, 0.01, 0.01))
    return ContractSpec("USD", size, tick, 1.0, step, commission_per_lot, slippage_points)


def _epoch_ms(index: pd.DatetimeIndex) -> np.ndarray:
    idx = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    return ((idx - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta("1ms")).to_numpy("int64")


def _num(x):
    x = float(x)
    return x if math.isfinite(x) else None


def _metrics(trades, eq, idx, in_pos_bars, n):
    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    wins, losses = pnls[pnls > 0], pnls[pnls < 0]
    peak = np.maximum.accumulate(eq) if len(eq) else eq
    dd = peak - eq
    max_dd = float(dd.max()) if len(dd) else 0.0
    max_dd_pct = float((dd / peak).max() * 100.0) if len(dd) else 0.0
    net = float(pnls.sum()) if len(pnls) else 0.0
    sharpe = sortino = None
    if len(eq) > 2:
        daily = pd.Series(eq, index=idx).resample("1D").last().dropna()
        r = daily.pct_change().dropna().to_numpy()
        if len(r) > 2:
            sd = r.std(ddof=1)
            down = math.sqrt(float(np.mean(np.minimum(r, 0.0) ** 2)))
            sharpe = _num(r.mean() / sd * math.sqrt(252)) if sd > 0 else None
            sortino = _num(r.mean() / down * math.sqrt(252)) if down > 0 else None
    return {"net_profit": net,
            "profit_factor": _num(wins.sum() / -losses.sum()) if len(losses) else None,
            "win_rate": float(len(wins) / len(pnls) * 100.0) if len(pnls) else 0.0,
            "expectancy": float(pnls.mean()) if len(pnls) else 0.0,
            "avg_r": float(np.mean([t["r_multiple"] for t in trades])) if trades else 0.0,
            "max_drawdown": max_dd, "max_drawdown_pct": max_dd_pct,
            "recovery_factor": _num(net / max_dd) if max_dd > 0 else None,
            "sharpe_daily": sharpe, "sortino_daily": sortino, "trades": len(trades),
            "exposure_pct": float(in_pos_bars / n * 100.0) if n else 0.0}


_MONEY_METRICS = ("net_profit", "profit_factor", "expectancy", "max_drawdown", "max_drawdown_pct", "recovery_factor",
                  "sharpe_daily", "sortino_daily")
FX_UNAVAILABLE = "FX_RATE_UNAVAILABLE"


def _metrics_blocked(trades, in_pos_bars, n, quote_metrics, quote_ccy):
    """Money metrics are withheld (None) when trades cannot be expressed in the account currency. Unit-free numbers
    stay; where available the quote-currency metrics are shown separately and labelled with their currency."""
    wins = [t for t in trades if (t.get("r_multiple") or 0.0) > 0]
    out = {k: None for k in _MONEY_METRICS}
    out.update({"win_rate": float(len(wins) / len(trades) * 100.0) if trades else 0.0,
                "avg_r": float(np.mean([t["r_multiple"] for t in trades])) if trades else 0.0,
                "trades": len(trades), "exposure_pct": float(in_pos_bars / n * 100.0) if n else 0.0,
                "money_blocked": True, "money_status": FX_UNAVAILABLE})
    if quote_metrics is not None:
        out["quote_currency_metrics"] = {"currency": quote_ccy, "note": "NOT account currency; research only",
                                         **{k: quote_metrics[k] for k in _MONEY_METRICS}}
    return out


def run_rules_backtest(spec: dict, bid: pd.DataFrame, ask: pd.DataFrame, params: dict | None = None, *,
                       capital: float = 10000, contract: ContractSpec | None = None,
                       commission_per_lot: float = 0.0, slippage_points: float = 0.0,
                       spread_multiplier: float = 1.0, entry_delay_bars: int = 0,
                       exit_delay_bars: int = 0, trade_from=None,
                       conversion: pd.Series | None = None, execution: dict | None = None,
                       conversion_max_staleness: timedelta = timedelta(hours=72)) -> dict:
    """``trade_from``: bars before this UTC timestamp only warm up indicators (no signals, no metrics).
    ``conversion``: account-currency per 1 quote-currency unit, UTC-indexed (fx.FxService.series); the rate known at
    each bar's open (last value at or before it, at most ``conversion_max_staleness`` old, invalid prints stay
    unavailable) sizes, marks and settles trades, so non-USD quotes are converted causally. A missing rate never
    becomes 1.0: entries are skipped, a trade that must close without a rate is flagged ``FX_RATE_UNAVAILABLE`` and
    the run's money metrics are blocked.
    ``execution``: optional V102 model (margin/volume/swap/trading_hours/reject_every_n/stop_gap_points, see
    runtime/strategy_lab/execution.py); None keeps the legacy behaviour exactly."""
    errs = engine.validate_spec(spec)
    if errs:
        raise ValueError("invalid spec: " + "; ".join(errs))
    xerrs = xm.validate_execution(execution)
    if xerrs:
        raise ValueError("invalid execution model: " + "; ".join(xerrs))
    if capital <= 0 or spread_multiplier <= 0 or entry_delay_bars < 0 or exit_delay_bars < 0:
        raise ValueError("capital/spread_multiplier must be > 0 and delays >= 0")
    eff_params = engine._param_values(spec, params)
    rs = engine.resolve(spec, params)
    if rs.get("partial_exits") and exit_delay_bars:
        raise ValueError("partial_exits with exit_delay_bars > 0: NOT_SUPPORTED (partial fills are not delayed)")
    contract = contract or default_contract(rs["symbol"])
    contract = dataclasses.replace(
        contract, commission_per_lot_side=commission_per_lot or contract.commission_per_lot_side,
        slippage_points=slippage_points or contract.slippage_points)
    contract.validate()
    cs, q2a, tick, step = contract.contract_size, contract.quote_to_account, contract.tick_size, contract.volume_step
    slip = contract.slippage_points * tick
    ex = execution or {}
    mg, sw, hours = ex.get("margin"), ex.get("swap"), ex.get("trading_hours")
    init_rate, maint_rate = xm.margin_rates(mg) if mg else (None, None)
    min_lot, max_lot = (ex.get("volume") or {}).get("min_lot"), (ex.get("volume") or {}).get("max_lot")
    reject_n, stop_gap = ex.get("reject_every_n"), float(ex.get("stop_gap_points") or 0.0) * tick
    qc = quote_conversion(rs["symbol"])
    quote_ccy, acct = (qc[0] if qc else "USD"), contract.account_currency.upper()

    # ---- align & clean: never fabricate candles
    ask_aligned = ask.reindex(bid.index)
    ask_only = int((~ask.index.isin(bid.index)).sum())
    bid_ok = bid[list(_COLS[:4])].notna().all(axis=1).to_numpy() & ~bid.index.duplicated()
    dropped_bid = int((~bid_ok).sum())
    bid = bid[bid_ok]
    ask_aligned = ask_aligned[bid_ok]
    idx = bid.index
    n = len(idx)
    ask_ok = ask_aligned[list(_COLS[:4])].notna().all(axis=1).to_numpy()
    missing_ask = int((~ask_ok).sum())
    B = {c: bid[c].to_numpy(float) for c in _COLS}
    A = {c: ask_aligned[c].to_numpy(float) for c in _COLS}
    t_ms = _epoch_ms(idx)
    qarr = None
    if conversion is not None:
        cv = conversion.copy()
        cv.index = cv.index.tz_localize("UTC") if cv.index.tz is None else cv.index.tz_convert("UTC")
        cv = cv[~cv.index.duplicated()].sort_index()
        uidx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        pos_ = cv.index.searchsorted(uidx, side="right") - 1          # last rate at or before the bar open
        vals = cv.to_numpy(float)
        qarr = vals[np.clip(pos_, 0, max(len(vals) - 1, 0))] if len(vals) else np.full(n, np.nan)
        stale = pos_ < 0
        if len(vals):
            stale = stale | ((uidx - cv.index[np.clip(pos_, 0, len(vals) - 1)]) > conversion_max_staleness)
        qarr = np.where(np.isfinite(qarr) & (qarr > 0) & ~stale, qarr, np.nan)
    fx_placeholder = quote_ccy != acct and qarr is None and contract.quote_to_account == 1.0

    def Q(j):
        if qarr is None:
            return q2a
        v = qarr[j]
        return v if math.isfinite(v) else None

    sig = engine.evaluate(spec, {"time": t_ms, **B}, params)
    ex_arrays = sig["exits"]
    day_ids = engine.local_day_ids(rs, t_ms)

    if spread_multiplier != 1.0:
        for c in _COLS[:4]:
            mid = (B[c] + A[c]) / 2.0
            A[c] = mid + (A[c] - mid) * spread_multiplier
            B[c] = mid - (mid - B[c]) * spread_multiplier
    long_sig, short_sig = sig["long_signal"], sig["short_signal"]
    first = 0
    if trade_from is not None:
        ts = pd.Timestamp(trade_from)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        first = int(idx.searchsorted(ts))
        long_sig, short_sig = long_sig.copy(), short_sig.copy()
        long_sig[:first] = False
        short_sig[:first] = False
    order = rs["order"]
    iso = lambda j: idx[j].isoformat()
    diag = {"bars": n, "ambiguous_bars": 0, "missing_ask_bars": missing_ask,
            "missing_bid_bars": dropped_bid + ask_only, "rejected_invalid_stop": 0, "rejected_min_lot": 0,
            "skipped_missing_quote": 0, "conflicting_signals": 0, "unfilled_orders": 0, "signals": 0}
    if qarr is not None:
        diag["fx_missing_bars"] = int(np.isnan(qarr[first:]).sum())
    if ex:
        diag.update({"rejected_insufficient_margin": 0, "rejected_outside_hours": 0, "rejected_orders": 0,
                     "size_limited_entries": 0, "size_limits": [], "stop_outs": 0, "swap_total": 0.0})

    realized = 0.0
    trades: list[dict] = []
    eq = np.empty(n)
    pos = None
    scheduled = None   # market entry waiting for its bar
    pending = None     # stop/limit order
    last_exit_bar = -10**9
    trades_per_day: dict = {}
    in_pos_bars = 0
    last_eq = float(capital)
    attempts = 0

    def open_position(side, entry, snap, j, intrabar, slipped):
        nonlocal attempts
        stop = engine.stop_price(rs, side, entry, snap)
        if not engine.valid_stop(side, entry, stop):
            diag["rejected_invalid_stop"] += 1
            return None
        risk_dist = abs(entry - stop)
        q = Q(j)
        if q is None:
            diag["skipped_missing_conversion"] = diag.get("skipped_missing_conversion", 0) + 1
            return None
        if hours and not xm.in_trading_hours(int(t_ms[j]), hours):
            diag["rejected_outside_hours"] += 1
            return None
        if reject_n:
            attempts += 1
            if attempts % reject_n == 0:
                diag["rejected_orders"] += 1
                return None
        lots = (capital + realized) * rs["risk"]["percent"] / 100.0 / (risk_dist * cs * q)
        lots = round(math.floor(lots / step + 1e-9) * step, 8)
        theoretical, limited, need = lots, [], None
        if max_lot and lots > max_lot:
            lots, limited = xm.floor_lots(max_lot, step), limited + ["MAX_LOT"]
        if mg and lots > 0:
            free = capital + realized                      # one position at a time: equity == balance when flat
            need = xm.required_margin(lots, entry, cs, q, init_rate)
            if need > free + 1e-9:
                limited.append("INSUFFICIENT_MARGIN")
                if mg.get("on_insufficient", "cap") == "cap":
                    lots = min(lots, xm.floor_lots(free / (cs * entry * q * init_rate), step))
                else:
                    lots = 0.0
        if limited:
            diag["size_limited_entries"] += 1
            if len(diag["size_limits"]) < 500:
                diag["size_limits"].append({"time": iso(j), "theoretical_lots": theoretical, "executed_lots": max(lots, 0.0),
                                            "limited_by": limited, "executable": lots >= max(step, min_lot or 0.0)})
        if lots < max(step, min_lot or 0.0) - 1e-12 or lots <= 0:
            if "INSUFFICIENT_MARGIN" in limited:
                diag["rejected_insufficient_margin"] += 1
            else:
                diag["rejected_min_lot"] += 1
            return None
        target = engine.target_price(rs, side, entry, stop, snap)
        trades_per_day[day_ids[j]] = trades_per_day.get(day_ids[j], 0) + 1
        plan = []
        if rs.get("partial_exits"):
            r_target = abs(target - entry) / risk_dist if math.isfinite(target) else math.inf
            for lv in xm.partial_plan(rs["partial_exits"], lots, step, min_lot):
                if lv["r"] < r_target - 1e-12:
                    sgn = 1.0 if side == "long" else -1.0
                    plan.append({**lv, "price": entry + sgn * lv["r"] * risk_dist, "done": False,
                                 "new_stop": None if lv["stop_r"] is None else entry + sgn * lv["stop_r"] * risk_dist})
        return {"side": side, "entry": entry, "entry0": entry, "qty0": lots, "adds": 0, "add_at": None,
                "risk_amt0": lots * risk_dist * cs * q,
                "stop": stop, "target": target, "qty": lots, "risk": risk_dist,
                "entry_bar": j, "fav": entry, "mae": 0.0, "mfe": 0.0, "be_done": False, "intrabar": intrabar,
                "slip_fills": 1 if slipped else 0, "exit_at": None, "exit_reason": None,
                "swap": 0.0, "swap_t": int(t_ms[j]), "plan": plan, "partial_qty": 0.0, "partial_net": 0.0,
                "partial_comm": 0.0, "partials": [], "pending_stop": None, "fx_missing": False}

    def contract_at(q):
        return contract if qarr is None else dataclasses.replace(contract, quote_to_account=float(q))

    def partial_fill(j, lv, price):
        nonlocal realized
        p = pos
        if abs(p["qty"] - lv["qty"]) < 1e-9:           # the level takes everything that is left
            close_position(j, price, "TAKE_PROFIT", False)
            return
        q = Q(j)
        lv["done"] = True
        rec = {"time": iso(j), "price": float(price), "qty": float(lv["qty"]), "r": lv["r"]}
        if q is None:
            p["fx_missing"] = True
            rec.update(net_pnl=None, commission=None, fx_status=FX_UNAVAILABLE)
        else:
            calc = position_pnl(side=p["side"], quantity_lots=lv["qty"], entry=p["entry"], exit=price,
                                contract=contract_at(q))
            realized += calc["net_pnl"]
            p["partial_net"] += calc["net_pnl"]
            p["partial_comm"] += calc["commission"]
            rec.update(net_pnl=float(calc["net_pnl"]), commission=float(calc["commission"]), gross_pnl=float(calc["gross_pnl"]))
        p["partials"].append(rec)
        p["qty"] = round(p["qty"] - lv["qty"], 8)
        p["partial_qty"] = round(p["partial_qty"] + lv["qty"], 8)
        if lv["new_stop"] is not None:
            p["pending_stop"] = lv["new_stop"] if p["pending_stop"] is None else (
                max(p["pending_stop"], lv["new_stop"]) if p["side"] == "long" else min(p["pending_stop"], lv["new_stop"]))

    def close_position(j, price, reason, slipped):
        nonlocal pos, realized, last_exit_bar
        p = pos
        q = Q(j)
        fills = p["slip_fills"] + (1 if slipped else 0)
        total_qty = p["qty"] + p["partial_qty"]
        d_ = 1.0 if p["side"] == "long" else -1.0
        extra = {}
        if sw:
            extra["swap"] = float(p["swap"])
            diag["swap_total"] += p["swap"]
        if p["plan"]:
            extra["partial_fills"] = p["partials"]
        if q is None or p["fx_missing"]:
            # no trustworthy rate at settlement: never substitute 1.0 or an old rate -> flagged, money blocked
            diag["fx_unavailable_trades"] = diag.get("fx_unavailable_trades", 0) + 1
            trades.append({"entry_time": iso(p["entry_bar"]), "exit_time": iso(j), "side": p["side"],
                           "entry_price": float(p["entry"]), "exit_price": float(price), "qty": float(total_qty),
                           "pnl": None, "costs": None,
                           "r_multiple": float((price - p["entry"]) * d_ / p["risk"]), "r_basis": "PRICE_ONLY",
                           "fx_status": FX_UNAVAILABLE, "exit_reason": reason,
                           "mae_r": float(p["mae"]), "mfe_r": float(p["mfe"]), "bars_held": int(j - p["entry_bar"]),
                           "adds": int(p.get("adds", 0)), **extra})
            pos, last_exit_bar = None, j
            return
        calc = position_pnl(side=p["side"], quantity_lots=p["qty"], entry=p["entry"], exit=price, contract=contract_at(q))
        slip_cost = fills * slip * p["qty"] * cs * q
        risk_amt = p.get("risk_amt0") or p["qty"] * p["risk"] * cs * q
        net = calc["net_pnl"] + p["partial_net"] + p["swap"]
        realized += calc["net_pnl"] + p["swap"]
        trades.append({"entry_time": iso(p["entry_bar"]), "exit_time": iso(j), "side": p["side"],
                       "entry_price": float(p["entry"]), "exit_price": float(price), "qty": float(total_qty),
                       "pnl": float(net), "costs": float(calc["commission"] + p["partial_comm"] + slip_cost),
                       "r_multiple": float(net / risk_amt), "exit_reason": reason,
                       "mae_r": float(p["mae"]), "mfe_r": float(p["mfe"]), "bars_held": int(j - p["entry_bar"]),
                       "adds": int(p.get("adds", 0)), **extra})
        pos, last_exit_bar = None, j

    def exit_fill(side, level, reason):
        """Price at `level` on the executable side, with adverse slippage for stop/market-type exits."""
        slipped = reason in ("STOP_LOSS", "TIME_EXIT", "END_OF_DATA")
        s = slip if slipped else 0.0
        if reason == "STOP_LOSS":
            s += stop_gap
        return (level - s if side == "long" else level + s), slipped

    def excursions(p, j):
        r_ = p["risk"]
        if p["side"] == "long":
            adv, fav = B["low"][j] - p["entry"], B["high"][j] - p["entry"]
            p["fav"] = max(p["fav"], B["high"][j])
        else:
            adv, fav = p["entry"] - A["high"][j], p["entry"] - A["low"][j]
            p["fav"] = min(p["fav"], A["low"][j])
        p["mae"], p["mfe"] = min(p["mae"], adv / r_), max(p["mfe"], fav / r_)

    for j in range(n):
        ok = bool(ask_ok[j])
        if ok:
            bq = {"open": B["open"][j], "high": B["high"][j], "low": B["low"][j]}
            aq = {"open": A["open"][j], "high": A["high"][j], "low": A["low"][j]}
            # 1. scheduled market entry
            if scheduled is not None and scheduled["bar"] == j:
                side = scheduled["side"]
                px = (A["open"][j] + slip) if side == "long" else (B["open"][j] - slip)
                pos = open_position(side, px, scheduled["snap"], j, False, slip > 0)
                scheduled = None
            # 1b. winner-only scale-in: only after a profitable trigger, never averaging down;
            # the combined stop is tightened so the worst case at the stop stays <= the initial risk.
            if pos is not None and pos.get("add_at") == j and pos["exit_at"] is None:
                p, side = pos, pos["side"]
                p["add_at"] = None
                si = rs["scale_in"]
                add_q = round(math.floor(p["qty0"] * si["add_size_fraction"] / step + 1e-9) * step, 8)
                px = (A["open"][j] + slip) if side == "long" else (B["open"][j] - slip)
                qj = Q(j)
                if add_q >= step and qj:
                    new_q = p["qty"] + add_q
                    avg = (p["entry"] * p["qty"] + px * add_q) / new_q
                    room = p["risk_amt0"] / (new_q * cs * qj)
                    need = avg - room if side == "long" else avg + room
                    new_stop = max(p["stop"], need) if side == "long" else min(p["stop"], need)
                    market = B["open"][j] if side == "long" else A["open"][j]
                    if (side == "long" and new_stop >= market) or (side == "short" and new_stop <= market):
                        diag["scale_in_skipped"] = diag.get("scale_in_skipped", 0) + 1
                    else:
                        p.update(qty=new_q, entry=avg, stop=new_stop, adds=p["adds"] + 1,
                                 slip_fills=p["slip_fills"] + (1 if slip > 0 else 0))
                        diag["scale_in_adds"] = diag.get("scale_in_adds", 0) + 1
            # 2. pending stop/limit order
            if pending is not None and pos is None:
                if j > pending["end"]:
                    diag["unfilled_orders"] += 1
                    pending = None
                elif j >= pending["start"]:
                    side, kind, P = pending["side"], order["type"], pending["price"]
                    fill = None
                    if side == "long":
                        if kind == "stop" and A["high"][j] >= P:
                            fill = max(A["open"][j], P) + slip
                        elif kind == "limit" and A["low"][j] <= P:
                            fill = min(A["open"][j], P)
                    else:
                        if kind == "stop" and B["low"][j] <= P:
                            fill = min(B["open"][j], P) - slip
                        elif kind == "limit" and B["high"][j] >= P:
                            fill = max(B["open"][j], P)
                    if fill is not None:
                        pos = open_position(side, fill, pending["snap"], j, True, kind == "stop" and slip > 0)
                        pending = None
        elif scheduled is not None and scheduled["bar"] <= j:
            diag["skipped_missing_quote"] += 1
            scheduled = None
        if pos is not None:
            in_pos_bars += 1
            if sw and ok:   # overnight financing: nightly charge per remaining lot at every rollover passed since the last bar
                nights = xm.swap_nights(pos["swap_t"], int(t_ms[j]), sw)
                if nights:
                    per_lot = sw["long_per_lot_per_night"] if pos["side"] == "long" else sw["short_per_lot_per_night"]
                    pos["swap"] += nights * pos["qty"] * per_lot
                pos["swap_t"] = int(t_ms[j])
        # 3. manage position
        if pos is not None and ok:
            p, side = pos, pos["side"]
            if p["exit_at"] is not None:
                if j >= p["exit_at"]:
                    excursions(p, j)
                    px, sl = exit_fill(side, B["open"][j] if side == "long" else A["open"][j], p["exit_reason"])
                    close_position(j, px, p["exit_reason"], sl)
            else:
                target = None if math.isnan(p["target"]) else p["target"]
                fresh_intrabar = p["intrabar"] and p["entry_bar"] == j
                if fresh_intrabar:
                    target = None  # path inside a stop/limit fill bar is unknown; ignore TP (conservative)
                if p["plan"]:
                    ev = xm.partial_bar_events(
                        side=side, bid=bq, ask=aq, stop=p["stop"], target=target,
                        levels=[] if fresh_intrabar else [lv for lv in p["plan"] if not lv["done"]])
                    diag["ambiguous_bars"] += int(ev["ambiguous"])
                    if ev["stop"] is not None:
                        excursions(p, j)
                        px, sl = exit_fill(side, ev["stop"]["price"], "STOP_LOSS")
                        close_position(j, px, "STOP_LOSS", sl)
                    else:
                        if ev["levels"] or ev["target"]:
                            excursions(p, j)
                        for lv, price in ev["levels"]:
                            if pos is not None:
                                partial_fill(j, lv, price)
                        if ev["target"] is not None and pos is not None:
                            close_position(j, ev["target"]["price"], "TAKE_PROFIT", False)
                else:
                    hit = conservative_ohlc_exit(side=side, bid=bq, ask=aq, stop=p["stop"], target=target)
                    if hit is not None:
                        diag["ambiguous_bars"] += int(hit["ambiguous"])
                        if exit_delay_bars == 0:
                            excursions(p, j)
                            px, sl = exit_fill(side, hit["price"], hit["reason"])
                            close_position(j, px, hit["reason"], sl)
                        else:
                            p["exit_at"], p["exit_reason"] = j + exit_delay_bars, hit["reason"]
            if pos is not None:
                p = pos
                excursions(p, j)
                if p["exit_at"] is None:
                    if p["pending_stop"] is not None:      # remainder stop after a partial exit applies from the next bar
                        p["stop"] = max(p["stop"], p["pending_stop"]) if side == "long" else min(p["stop"], p["pending_stop"])
                        p["pending_stop"] = None
                    r_fav = ((p["fav"] - p["entry0"]) if side == "long" else (p["entry0"] - p["fav"])) / p["risk"]
                    be, tr = rs.get("break_even"), rs.get("trailing")
                    if be and not p["be_done"] and r_fav >= be["activate_r"]:
                        p["be_done"] = True
                        p["stop"] = max(p["stop"], p["entry"]) if side == "long" else min(p["stop"], p["entry"])
                    if tr and r_fav >= tr["activate_r"]:
                        a_ = ex_arrays["trail_atr"][j]
                        if math.isfinite(a_):
                            new = p["fav"] - tr["atr_mult"] * a_ if side == "long" else p["fav"] + tr["atr_mult"] * a_
                            p["stop"] = max(p["stop"], new) if side == "long" else min(p["stop"], new)
                    si = rs.get("scale_in")
                    if si and p["adds"] < si["max_adds"] and p["add_at"] is None and j + 1 < n:
                        close_px = B["close"][j] if side == "long" else A["close"][j]
                        r_now = ((close_px - p["entry0"]) if side == "long" else (p["entry0"] - close_px)) / p["risk"]
                        if r_now >= (p["adds"] + 1) * si["add_every_r"]:
                            p["add_at"] = j + 1
                    tx = rs.get("time_exit_bars")
                    if tx and j - p["entry_bar"] + 1 >= tx:
                        p["exit_at"], p["exit_reason"] = j + 1 + exit_delay_bars, "TIME_EXIT"
        # 3b. margin stop-out (conservative: judged at the bar's adverse extreme)
        if pos is not None and ok and mg and mg.get("stop_out_level_pct") is not None and Q(j):
            p, qj = pos, Q(j)
            worst = B["low"][j] if p["side"] == "long" else A["high"][j]
            diff = (worst - p["entry"]) if p["side"] == "long" else (p["entry"] - worst)
            eq_w = capital + realized + diff * p["qty"] * cs * qj + p["swap"]
            level = xm.margin_level_pct(eq_w, xm.required_margin(p["qty"], worst, cs, qj, maint_rate or init_rate))
            if level <= mg["stop_out_level_pct"]:
                diag["stop_outs"] += 1
                close_position(j, worst, "STOP_OUT", False)
        # 4. equity mark (liquidate at the final bar)
        if pos is not None and j == n - 1 and ok:
            px, sl = exit_fill(pos["side"], B["close"][j] if pos["side"] == "long" else A["close"][j], "END_OF_DATA")
            close_position(j, px, "END_OF_DATA", sl)
        if ok:
            qj = Q(j)
            if pos is None or qj is not None:               # unavailable rate while in a position: equity is carried, never invented
                unreal = 0.0
                if pos is not None:
                    mark = B["close"][j] if pos["side"] == "long" else A["close"][j]
                    diff = (mark - pos["entry"]) if pos["side"] == "long" else (pos["entry"] - mark)
                    unreal = diff * pos["qty"] * cs * qj + pos["swap"]
                    if mg:
                        eq_now = capital + realized + unreal
                        used = xm.required_margin(pos["qty"], mark, cs, qj, init_rate)
                        level = xm.margin_level_pct(eq_now, used)
                        diag["min_margin_level_pct"] = min(diag.get("min_margin_level_pct", math.inf), level)
                        diag["max_used_margin"] = max(diag.get("max_used_margin", 0.0), used)
                        diag["max_portfolio_leverage"] = max(diag.get("max_portfolio_leverage", 0.0),
                                                             pos["qty"] * cs * mark * qj / eq_now if eq_now > 0 else math.inf)
                last_eq = capital + realized + unreal
        eq[j] = last_eq
        # 5. signal at bar close
        if pos is None and scheduled is None and pending is None and j + 1 + entry_delay_bars < n:
            lg, sh = bool(long_sig[j]), bool(short_sig[j])
            if lg and sh:
                diag["conflicting_signals"] += 1
            elif lg or sh:
                diag["signals"] += 1
                limit = rs.get("max_trades_per_day")
                if (j - last_exit_bar >= rs.get("cooldown_bars", 0)
                        and not (limit and trades_per_day.get(day_ids[j], 0) >= limit)):
                    side = "long" if lg else "short"
                    snap = engine.exit_snapshot(ex_arrays, j)
                    start = j + 1 + entry_delay_bars
                    if order["type"] == "market":
                        scheduled = {"side": side, "bar": start, "snap": snap}
                    else:
                        price = engine.order_price(rs, side, B["close"][j], snap)
                        if math.isfinite(price):
                            pending = {"side": side, "price": price, "start": start,
                                       "end": start + order["expiry_bars"] - 1, "snap": snap}

    keep = np.arange(first, n)
    if n - first > _MAX_EQUITY_POINTS:
        k = math.ceil((n - first) / _MAX_EQUITY_POINTS)
        keep = np.unique(np.concatenate((np.arange(first, n, k), [n - 1])))
        diag["equity_thinned_every"] = int(k)
    equity = [{"time": iso(int(j)), "equity": float(eq[j])} for j in keep]
    assumptions = [
        "Signals computed on Bid candles at bar close; no look-ahead (rule_engine is causal).",
        "Market entry at the open of bar i+1+entry_delay_bars: long buys Ask open, short sells Bid open.",
        "Stop/limit orders trigger on Ask (long) / Bid (short) highs/lows within expiry_bars; gaps fill at the open.",
        "Long exits fill on Bid, short exits on Ask. SL and TP touched in the same bar -> SL first (ambiguous_bars).",
        "A stop/limit fill bar ignores TP (intrabar path unknown); SL is still checked in that bar.",
        "Break-even/trailing updates from a bar apply from the next bar; trailing anchors on the best price since entry.",
        "Position size = risk.percent of current realized equity / stop distance via ContractSpec, floored to volume_step.",
        "Commission is per lot per side; slippage_points applied adversely to stop/market fills (not to TP/limit fills).",
        f"spread_multiplier={spread_multiplier} scales the Ask-Bid distance around mid for fills only.",
        "Bars lacking Ask quotes are kept for signals but never filled on; bars lacking Bid are dropped (never fabricated).",
        "Open position at the end of data is closed at the last bar close (END_OF_DATA).",
        "Equity is marked to market at Bid close (long) / Ask close (short).",
        "Order fills are all-or-nothing: partial fills of a single order are NOT_SUPPORTED (M1 OHLC has no depth/volume model).",
    ] + list(sig["notes"])
    if rs.get("partial_exits"):
        assumptions.append("partial_exits: each level fills at the better of its price and the bar open on the executable "
                           "side; stop first when stop and a level are touched in one bar (level not credited); the new "
                           "stop of the remainder applies from the next bar; commission is charged per partial fill.")
    if ex:
        assumptions.append("Execution model (margin/volume/swap/hours) is explicit and labelled; unlabelled values are "
                           "never used. Intrabar order of events inside one bar is unknown (conservative: stop first).")
    assumptions.append("Contract spec is an assumption, NOT broker-verified (contract size/tick/lot step/commission); "
                       "money amounts are research estimates.")
    if quote_ccy == acct:
        diag["currency_status"] = "USD_QUOTE" if acct == "USD" else "ACCOUNT_CURRENCY_QUOTE"
    elif qarr is not None:
        diag["currency_status"] = "CONVERTED_CAUSAL"
        assumptions.append(f"{quote_ccy}-quoted: P&L, sizing and equity converted to {acct} with the supplied "
                           "conversion series at each bar open (causal, no look-ahead; never a later/current rate; "
                           f"a rate older than {conversion_max_staleness} or invalid is unavailable, never 1.0).")
    elif contract.quote_to_account == 1.0:
        diag["currency_status"] = "QUOTE_CURRENCY_UNCONVERTED"
        assumptions.append(f"{rs['symbol']} is quoted in {quote_ccy}; with quote_to_account=1.0 and no conversion series "
                           f"amounts are {quote_ccy}, not {acct}: FX_RATE_UNAVAILABLE, money metrics blocked.")
    else:
        diag["currency_status"] = "FIXED_RATE"
    fx_blocked = fx_placeholder or diag.get("fx_unavailable_trades", 0) > 0
    diag["fx_status"] = (FX_UNAVAILABLE if fx_blocked else
                         "FX_GAPS_ENTRIES_SKIPPED" if diag.get("skipped_missing_conversion") else
                         "NOT_NEEDED" if quote_ccy == acct else "OK")
    diag["contract_status"] = "ASSUMED_NOT_BROKER_VERIFIED"
    diag["partial_fills_model"] = "NOT_SUPPORTED"
    if ex or rs.get("partial_exits"):
        diag["execution_model"] = {
            "margin": ({"status": mg["status"], "initial_rate": init_rate, "maintenance_rate": maint_rate,
                        "stop_out_level_pct": mg.get("stop_out_level_pct"), "on_insufficient": mg.get("on_insufficient", "cap")}
                       if mg else "NOT_MODELLED"),
            "volume": {"min_lot": min_lot, "max_lot": max_lot, "step": step} if (min_lot or max_lot) else {"step": step},
            "swap": ({"status": sw["status"], "long_per_lot_per_night": sw["long_per_lot_per_night"],
                      "short_per_lot_per_night": sw["short_per_lot_per_night"]} if sw else "NOT_MODELLED"),
            "trading_hours": hours or "NOT_MODELLED", "reject_every_n": reject_n, "stop_gap_points": ex.get("stop_gap_points"),
            "partial_exits": rs.get("partial_exits") or "NONE"}
    if rs["symbol"].upper() not in _DEFAULT_CONTRACTS and contract.contract_size == 1.0:
        assumptions.append("Unknown symbol: default contract spec (size 1, tick 0.01) assumed; pass contract=.")
    diag["assumptions"] = assumptions
    if fx_blocked:
        done = [t for t in trades if t.get("pnl") is not None]
        quote_metrics = _metrics(done, eq[first:], idx[first:], in_pos_bars, n - first) if fx_placeholder else None
        metrics = _metrics_blocked(trades, in_pos_bars, n - first, quote_metrics, quote_ccy)
    else:
        metrics = _metrics(trades, eq[first:], idx[first:], in_pos_bars, n - first)
    return {"engine": ENGINE_NAME, "trades": trades, "equity": equity,
            "metrics": metrics, "diagnostics": diag,
            "spec_sha256": engine.spec_sha256(spec), "params": eff_params}


STRESS_SCENARIOS = ("baseline", "spread_x2", "spread_x3", "extra_slippage_5pts", "extra_slippage_20pts", "stop_gap_20pts",
                    "order_rejection_every_2", "insufficient_margin_leverage_2", "outside_trading_hours_08_16_utc")


def run_execution_stress(spec: dict, bid: pd.DataFrame, ask: pd.DataFrame, params: dict | None = None, *,
                         capital: float = 10000, contract: ContractSpec | None = None,
                         conversion: pd.Series | None = None, execution: dict | None = None,
                         scenarios: tuple = STRESS_SCENARIOS, **kw) -> dict:
    """Run the same spec under execution stress. Each scenario changes ONE thing against the baseline (the optional
    ``execution`` model); results are deltas, not predictions. Not an exact replay: M1/H1 OHLC cannot tell the order
    of events inside a bar (SL+TP in one bar -> stop first, counted in ``ambiguous_bars``) and has no depth/volume, so
    partial fills of one order are reported NOT_SUPPORTED instead of simulated."""
    base_ex = execution or {}
    contract = contract or default_contract(engine.resolve(spec, params)["symbol"])

    def one(name):
        ex, extra = dict(base_ex), {}
        if name == "spread_x2":
            extra["spread_multiplier"] = 2.0
        elif name == "spread_x3":
            extra["spread_multiplier"] = 3.0
        elif name.startswith("extra_slippage_"):
            extra["slippage_points"] = contract.slippage_points + float(name.split("_")[2].replace("pts", ""))
        elif name == "stop_gap_20pts":
            ex["stop_gap_points"] = 20.0
        elif name == "order_rejection_every_2":
            ex["reject_every_n"] = 2
        elif name == "insufficient_margin_leverage_2":
            ex["margin"] = {"leverage": 2.0, "on_insufficient": "reject", "status": "SIMULATION_ASSUMPTION"}
        elif name == "outside_trading_hours_08_16_utc":
            ex["trading_hours"] = {"utc_hours": list(range(8, 16)), "weekdays": [0, 1, 2, 3, 4]}
        elif name != "baseline":
            raise ValueError(f"unknown stress scenario {name!r}")
        return run_rules_backtest(spec, bid, ask, params, capital=capital, contract=contract, conversion=conversion,
                                  execution=ex or None, **{**kw, **extra})

    out, base_net = {}, None
    for name in scenarios:
        res = one(name)
        net, d = res["metrics"].get("net_profit"), res["diagnostics"]
        base_net = net if name == "baseline" else base_net
        out[name] = {"status": "RUN", "trades": res["metrics"]["trades"], "net_profit": net,
                     "delta_net_profit_vs_baseline": (None if net is None or base_net is None else net - base_net),
                     "money_status": res["metrics"].get("money_status", "OK"),
                     "diagnostics": {k: d.get(k) for k in (
                         "ambiguous_bars", "unfilled_orders", "rejected_orders", "rejected_insufficient_margin",
                         "rejected_outside_hours", "rejected_min_lot", "size_limited_entries", "stop_outs",
                         "fx_status") if k in d}}
    out["same_candle_sl_tp"] = {"status": "CONSERVATIVE_STOP_FIRST", "note": "counted per run in ambiguous_bars"}
    out["pending_orders"] = {"status": "MODELLED", "note": "stop/limit within expiry_bars; unfilled counted in unfilled_orders"}
    out["partial_fills_of_one_order"] = {"status": "NOT_SUPPORTED", "note": "no depth/volume model on OHLC data"}
    return {"scenarios": out, "execution_model_base": base_ex or "NOT_MODELLED"}
