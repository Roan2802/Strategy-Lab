"""Independent conservative Bid/Ask OHLC execution ledger.

Requires explicit contract and cost settings; it does not alter Jesse fills.
Within-bar SL+TP ambiguity is resolved at SL and is recorded on the trade.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import math
import copy
import operator
from collections.abc import Sequence
from typing import Callable, Iterable

import numpy as np


@dataclass(frozen=True)
class ContractSpec:
    account_currency: str
    contract_size: float
    tick_size: float
    quote_to_account: float
    volume_step: float
    commission_per_lot_side: float = 0.0
    slippage_points: float = 0.0

    def validate(self) -> None:
        if not self.account_currency.strip():
            raise ValueError("account currency is required")
        if min(self.contract_size, self.tick_size, self.quote_to_account, self.volume_step) <= 0:
            raise ValueError("contract size, tick size, conversion rate, and volume step must be positive")
        if self.commission_per_lot_side < 0 or self.slippage_points < 0:
            raise ValueError("commission and slippage must be nonnegative")


def position_pnl(*, side: str, quantity_lots: float, entry: float, exit: float,
                 contract: ContractSpec, commission_per_lot_side: float | None = None) -> dict:
    """Independent account-currency P&L for prices already filled on correct quote sides."""
    contract.validate()
    if side not in {"long", "short"} or quantity_lots <= 0 or min(entry, exit) <= 0:
        raise ValueError("invalid side, quantity, or fill price")
    direction = 1 if side == "long" else -1
    gross = (exit - entry) * direction * quantity_lots * contract.contract_size * contract.quote_to_account
    fee_per_side = contract.commission_per_lot_side if commission_per_lot_side is None else commission_per_lot_side
    commission = 2 * quantity_lots * fee_per_side
    return {"gross_pnl": gross, "commission": commission, "net_pnl": gross - commission,
            "tick_value": contract.tick_size * contract.contract_size * contract.quote_to_account,
            "account_currency": contract.account_currency, "contract": asdict(contract)}


def conservative_ohlc_exit(*, side: str, bid: dict, ask: dict,
                           stop: float | None, target: float | None) -> dict | None:
    """Return an exit using executable quote side; stop wins if both levels touch."""
    if side == "long":
        o, h, l = bid["open"], bid["high"], bid["low"]
        stop_hit = stop is not None and l <= stop
        target_hit = target is not None and h >= target
        if stop_hit:
            return {"reason": "STOP_LOSS", "price": min(o, stop), "ambiguous": bool(target_hit)}
        if target_hit:
            return {"reason": "TAKE_PROFIT", "price": max(o, target), "ambiguous": False}
    elif side == "short":
        o, h, l = ask["open"], ask["high"], ask["low"]
        stop_hit = stop is not None and h >= stop
        target_hit = target is not None and l <= target
        if stop_hit:
            return {"reason": "STOP_LOSS", "price": max(o, stop), "ambiguous": bool(target_hit)}
        if target_hit:
            return {"reason": "TAKE_PROFIT", "price": min(o, target), "ambiguous": False}
    else:
        raise ValueError("side must be long or short")
    return None


def fill_market(side: str, bid: dict, ask: dict, slippage_points: float, tick_size: float) -> float:
    if side == "long":
        return ask["open"] + slippage_points * tick_size
    if side == "short":
        return bid["open"] - slippage_points * tick_size
    raise ValueError("side must be long or short")


def apply_ledger(trades: Iterable[dict], quotes: dict[int, dict], contract: ContractSpec) -> list[dict]:
    """Reprice existing Jesse trade timestamps on Duka BBO opens.

    This is an accounting overlay only. Since the entry/exit timestamps and
    position lifecycle come from Jesse, it is marked provisional and does not
    claim to replay spread-sensitive stop/limit triggers.
    """
    contract.validate()
    output = []
    for trade in trades:
        side = str(trade.get("type", trade.get("side", "long"))).lower()
        entry_ts = int(float(trade.get("opened_at", trade.get("entry_timestamp_ms", 0))))
        exit_ts = int(float(trade.get("closed_at", trade.get("exit_timestamp_ms", 0))))
        entry_quote, exit_quote = quotes.get(entry_ts), quotes.get(exit_ts)
        required = ("bid_open", "ask_open")
        if (not entry_quote or not exit_quote or
                any(k not in entry_quote for k in required) or any(k not in exit_quote for k in required) or
                not all(math.isfinite(float(q[k])) and float(q[k]) > 0 for q in (entry_quote, exit_quote) for k in required)):
            output.append({"trade_id": trade.get("id"), "status": "MISSING_DUKASCOPY_QUOTE", "jesse_pnl_provisional": trade.get("PNL", trade.get("pnl"))})
            continue
        is_long = side in {"long", "buy"}
        entry = entry_quote["ask_open"] if is_long else entry_quote["bid_open"]
        exit = exit_quote["bid_open"] if is_long else exit_quote["ask_open"]
        slip = contract.slippage_points * contract.tick_size
        entry = entry + slip if is_long else entry - slip
        exit = exit - slip if is_long else exit + slip
        qty = abs(float(trade.get("qty", trade.get("quantity", 0))))
        calc = position_pnl(side="long" if is_long else "short", quantity_lots=qty,
                            entry=float(entry), exit=float(exit), contract=contract)
        step_mismatch = abs(qty / contract.volume_step - round(qty / contract.volume_step)) > 1e-8
        output.append({"trade_id": trade.get("id"), "status": "JESSE_RESEARCH_PROVISIONAL",
                       "entry_quote": "ASK" if is_long else "BID",
                       "exit_quote": "BID" if is_long else "ASK",
                       "entry_price": entry, "exit_price": exit, **calc,
                       "jesse_pnl_provisional": trade.get("PNL", trade.get("pnl")),
                       "spread_trigger_replayed": False, "volume_step_valid": not step_mismatch,
                       "warning": "Volume is off configured lot step." if step_mismatch else None})
    return output


def _ema_series(candles: list[dict], key: str, period: int) -> list[float | None]:
    """Causal recursive EMA matching Jesse's close-seeded indicator."""
    alpha = 2.0 / (period + 1.0)
    previous = None
    count = 0
    values: list[float | None] = []
    for candle in candles:
        raw = candle.get(key)
        if raw is None or not math.isfinite(float(raw)):
            values.append(None)
            continue
        close = float(raw)
        previous = close if previous is None else previous + alpha * (close - previous)
        count += 1
        values.append(previous if count >= period else None)
    return values


class _CausalHistory(Sequence):
    """Read-only prefix view; rows are copied only when the strategy accesses them."""

    def __init__(self, candles: list[dict], end_index: int):
        self._candles = candles
        self._length = end_index + 1

    def __len__(self) -> int:
        return self._length

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(self._length)
            return [dict(self._candles[i]) for i in range(start, stop, step)]
        index = operator.index(index)
        if index < 0:
            index += self._length
        if index < 0 or index >= self._length:
            raise IndexError("causal candle history index out of range")
        return dict(self._candles[index])

    def __iter__(self):
        for index in range(self._length):
            yield dict(self._candles[index])


def _rsi_series(values: np.ndarray, period: int) -> np.ndarray:
    """Return Jesse RSI for every prefix in one pass, with a no-Jesse fallback."""
    try:
        from jesse.indicators.rsi import rsi
        return np.asarray(rsi(values, period=period, sequential=True), dtype=np.float64)
    except ImportError:
        output = np.full(len(values), np.nan, dtype=np.float64)
        if len(values) <= period:
            return output
        changes = np.diff(values)
        gains = np.maximum(changes, 0.0)
        losses = np.maximum(-changes, 0.0)
        average_gain = float(np.sum(gains[:period]) / period)
        average_loss = float(np.sum(losses[:period]) / period)
        for index in range(period, len(values)):
            if index > period:
                average_gain = (average_gain * (period - 1) + gains[index - 1]) / period
                average_loss = (average_loss * (period - 1) + losses[index - 1]) / period
            output[index] = 100.0 if average_loss == 0 else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
        return output


def _precompute_quote_indicators(candles: list[dict], specs: dict) -> dict[str, np.ndarray]:
    """Precompute only allow-listed causal indicators and expose values by index."""
    output = {}
    for name, spec in (specs or {}).items():
        if not isinstance(name, str) or not isinstance(spec, dict) or spec.get("type") != "rsi":
            raise ValueError("quote replay supports only declared RSI indicators")
        source = str(spec.get("source", ""))
        if source not in {"bid_close", "ask_close"}:
            raise ValueError("quote RSI source must be bid_close or ask_close")
        period = int(spec.get("period", 0))
        if period < 2:
            raise ValueError("quote RSI period must be at least 2")
        values = np.fromiter(
            (float(candle[source]) if candle.get(source) is not None else np.nan for candle in candles),
            dtype=np.float64, count=len(candles),
        )
        output[name] = _rsi_series(values, period)
    return output


def _lab_breakout_signal(candle: dict, *, ema_value: float | None, parameters: dict,
                         index: int, available: bool) -> tuple[str, str, str, float | None]:
    """Recreate only the audited LabBreakout rule on quote-side OHLC."""
    mode = int(parameters.get("order_mode", 0))
    side = "short" if mode == 2 else "long"
    quote = "bid" if side == "short" else "ask"
    close = candle.get(f"{quote}_close")
    high, low = candle.get(f"{quote}_high"), candle.get(f"{quote}_low")
    if any(value is None or not math.isfinite(float(value)) for value in (close, high, low)):
        return "SKIP", "MISSING_SIGNAL_QUOTE", quote.upper(), None
    ready = ema_value is not None and math.isfinite(float(ema_value))
    close, high, low = float(close), float(high), float(low)
    trend_pass = None if not ready else (close < ema_value if side == "short" else close > ema_value)
    range_value = high - low
    range_pass = range_value <= float(parameters.get("max_bar_range", 25.0))
    scheduled = index > 0 and index % max(1, int(parameters.get("entry_interval", 60))) == 0
    if not ready:
        return "SKIP", "EMA_WARMUP", quote.upper(), range_value
    if not available:
        return "SKIP", "POSITION_OR_PENDING_ORDER", quote.upper(), range_value
    if not scheduled:
        return "SKIP", "NOT_SCHEDULED", quote.upper(), range_value
    if not trend_pass:
        return "FAIL", "EMA_CONDITION", quote.upper(), range_value
    if not range_pass:
        return "FAIL", "BAR_RANGE_CONDITION", quote.upper(), range_value
    return "PASS", "ENTRY_CONDITIONS_MET", quote.upper(), range_value


def simulate_bidask(candles: list[dict], events: list[dict], contract: ContractSpec,
                    parameters: dict, starting_balance: float, *,
                    strategy_name: str | None = None, trade_start_ms: int | None = None,
                    signal_evaluator: Callable[[dict], dict] | None = None,
                    indicator_specs: dict | None = None) -> dict:
    """Replay Jesse-submitted market/stop entries on Dukascopy Bid/Ask OHLC.

    Signals/orders are taken from the strategy event stream. A market signal
    fills at the next bar open; stop entries trigger on the executable quote
    side. Long exits trigger on Bid, short exits on Ask. If both SL and TP are
    touched in one bar, SL wins. The result is validated for this declared OHLC
    model, not a claim about unknown tick order or a broker's matching engine.
    """
    contract.validate()
    if starting_balance <= 0:
        raise ValueError("starting balance must be positive")
    if not candles:
        raise ValueError("no Dukascopy candles supplied")
    lab_breakout_recompute = strategy_name == "LabBreakout" and trade_start_ms is not None
    recompute = lab_breakout_recompute or signal_evaluator is not None
    by_time: dict[int, list[dict]] = {}
    saved_decisions = {int(event["timestamp_ms"]): event.get("decision") for event in events
                       if event.get("event_type") == "CANDLE_DECISION" and event.get("timestamp_ms") is not None}
    for event in (() if recompute else events):
        if event.get("event_type") != "ORDER_SUBMITTED" and event.get("event") != "ORDER_SUBMITTED":
            continue
        stamp = event.get("timestamp_ms", event.get("signal_timestamp_ms"))
        if stamp is None:
            continue
        by_time.setdefault(int(stamp), []).append(event)
    pending: list[dict] = []
    position = None
    closed = []
    lifecycle = []
    balance = float(starting_balance)
    peak = balance
    max_dd = 0.0
    expiry = max(1, int(parameters.get("pending_expiry_bars", parameters.get("expiry_bars", 5))))
    stop_distance = float(parameters.get("stop_distance", 0))
    target_distance = float(parameters.get("target_distance", 0))
    mode = int(parameters.get("order_mode", 0))
    qty_default = float(parameters.get("position_size", 1.0))
    incomplete_quote_bars = 0
    decision_counts = {key: 0 for key in ("PASS", "FAIL", "SKIP")}
    saved_decision_matches = 0
    saved_decision_mismatches = 0
    mismatch_examples = []
    trade_index = -1
    signal_quote = "bid" if int(parameters.get("order_mode", 0)) == 2 else "ask"
    ema_values = _ema_series(candles, f"{signal_quote}_close", max(1, int(parameters.get("ema_period", 20)))) if lab_breakout_recompute else []
    quote_indicators = _precompute_quote_indicators(candles, indicator_specs or {}) if signal_evaluator is not None else {}
    last_close_index: int | None = None
    strategy_state: dict = {}

    def evaluate_signal(index: int, candle: dict, available: bool) -> list[dict]:
        nonlocal saved_decision_matches, saved_decision_mismatches, strategy_state
        if not recompute:
            return []
        timestamp = int(candle["timestamp_ms"])
        if signal_evaluator is not None:
            evaluated = signal_evaluator({
                # Match Jesse's strategy index: zero is the first trading
                # candle, while history_index preserves warm-up alignment.
                "index": trade_index, "history_index": index, "candle": dict(candle),
                "history": _CausalHistory(candles, index),
                "indicators": {name: (float(values[index]) if math.isfinite(float(values[index])) else None)
                               for name, values in quote_indicators.items()},
                "parameters": dict(parameters), "available": available,
                "position_open": position is not None, "pending_order": bool(pending),
                "last_close_index": last_close_index, "state": copy.deepcopy(strategy_state),
            })
            if not isinstance(evaluated, dict):
                raise ValueError("strategy_lab_quote_signal must return a dictionary")
            next_state = evaluated.get("state", strategy_state)
            if not isinstance(next_state, dict):
                raise ValueError("strategy_lab_quote_signal state must be a dictionary")
            strategy_state = copy.deepcopy(next_state)
            decision = str(evaluated.get("decision", "SKIP")).upper()
            if decision not in decision_counts:
                raise ValueError("strategy_lab_quote_signal decision must be PASS, FAIL, or SKIP")
            reason = str(evaluated.get("reason") or "NO_DEBUG_DECISION")
            quote = str(evaluated.get("signal_quote") or "ASK").upper()
            range_value = evaluated.get("signal"); range_value = range_value if isinstance(range_value, dict) else None
        else:
            evaluated = None
            decision, reason, quote, range_value = _lab_breakout_signal(
                candle, ema_value=ema_values[index], parameters=parameters,
                index=trade_index, available=available)
        decision_counts[decision] += 1
        saved = saved_decisions.get(timestamp)
        if saved is not None:
            if saved == decision:
                saved_decision_matches += 1
            else:
                saved_decision_mismatches += 1
                if len(mismatch_examples) < 20:
                    mismatch_examples.append({"timestamp_ms": timestamp, "saved": saved,
                                              "recomputed": decision, "reason": reason,
                                              "signal_quote": quote,
                                              "ema": ema_values[index] if lab_breakout_recompute else None,
                                              "range": range_value})
        if decision != "PASS":
            return []
        if signal_evaluator is not None:
            signal = evaluated.get("signal")
            if not isinstance(signal, dict):
                raise ValueError("strategy_lab_quote_signal must include a signal when decision is PASS")
            side = str(signal.get("side", "")).upper()
            if side not in {"BUY", "SELL", "LONG", "SHORT"}:
                raise ValueError("strategy_lab_quote_signal signal side must be BUY or SELL")
            order_type = str(signal.get("order_type") or "MARKET").upper()
            price = signal.get("planned_price", signal.get("price"))
            if order_type not in {"MARKET", "STOP", "LIMIT"}:
                raise ValueError("strategy_lab_quote_signal order_type must be MARKET, STOP, or LIMIT")
            if order_type != "MARKET" and price is None:
                raise ValueError("strategy_lab_quote_signal STOP/LIMIT signal requires a planned_price")
            quantity = float(signal.get("quantity", parameters.get("position_size", qty_default)))
            if not math.isfinite(quantity) or quantity <= 0:
                raise ValueError("strategy_lab_quote_signal quantity must be finite and positive")
            if price is not None and (not math.isfinite(float(price)) or float(price) <= 0):
                raise ValueError("strategy_lab_quote_signal planned_price must be finite and positive")
            return [{"event_type": "ORDER_SUBMITTED", "timestamp_ms": timestamp,
                     "signal_timestamp_ms": timestamp, "side": side,
                     "order_type": order_type, "planned_price": price,
                     "quantity": quantity}]
        mode = int(parameters.get("order_mode", 0))
        side = "SELL" if mode == 2 else "BUY"
        planned = (float(candle["bid_low"]) - float(parameters.get("stop_offset", 0.1))
                   if mode == 2 else float(candle["ask_high"]) + float(parameters.get("stop_offset", 0.1))
                   if mode == 1 else float(candle["ask_close"]))
        return [{"event_type": "ORDER_SUBMITTED", "timestamp_ms": timestamp,
                 "signal_timestamp_ms": timestamp, "side": side,
                 "order_type": "MARKET" if mode == 0 else "STOP",
                 "planned_price": planned,
                 "quantity": float(parameters.get("position_size", 1.0))}]

    def close_position(index: int, candle: dict) -> bool:
        nonlocal position, balance, peak, max_dd, last_close_index
        if position is None:
            return False
        side = position["side"]
        exit_result = conservative_ohlc_exit(side=side,
            bid={"open": candle["bid_open"], "high": candle["bid_high"], "low": candle["bid_low"]},
            ask={"open": candle["ask_open"], "high": candle["ask_high"], "low": candle["ask_low"]},
            stop=position["stop"], target=position["target"])
        if exit_result is None:
            return False
        slip = contract.slippage_points * contract.tick_size
        exit_price = exit_result["price"] - slip if side == "long" else exit_result["price"] + slip
        calc = position_pnl(side=side, quantity_lots=position["quantity"], entry=position["entry"],
                             exit=exit_price, contract=contract)
        on_step = abs(position["quantity"] / contract.volume_step - round(position["quantity"] / contract.volume_step)) <= 1e-8
        trade = {"trade_number": len(closed) + 1, "side": side,
                 "signal_timestamp_ms": position["signal_timestamp_ms"],
                 "entry_timestamp_ms": position["entry_timestamp_ms"],
                 "exit_timestamp_ms": int(candle["timestamp_ms"]),
                 "entry_price": position["entry"], "exit_price": exit_price,
                 "entry_quote": position["entry_quote"], "exit_quote": "BID" if side == "long" else "ASK",
                 "quantity_lots": position["quantity"], "stop": position["stop"], "target": position["target"],
                 "exit_reason": exit_result["reason"], "intrabar_ambiguous": exit_result["ambiguous"],
                 "entry_bar_exit": index == position["entry_index"], "volume_step_valid": on_step,
                 **calc}
        balance += calc["net_pnl"]
        peak = max(peak, balance)
        max_dd = max(max_dd, (peak - balance) / peak if peak else 0)
        closed.append(trade)
        last_close_index = trade_index
        lifecycle.append({"event_type": "POSITION_CLOSE", "timestamp_ms": trade["exit_timestamp_ms"],
                          "trade_number": trade["trade_number"], "reason": trade["exit_reason"]})
        position = None
        return True

    for index, candle in enumerate(candles):
        timestamp = int(candle.get("timestamp_ms", 0))
        if trade_start_ms is not None and timestamp >= trade_start_ms:
            trade_index += 1
        if trade_start_ms is not None and timestamp < trade_start_ms:
            continue
        quote_keys = ("bid_open", "bid_high", "bid_low", "ask_open", "ask_high", "ask_low")
        if any(k not in candle or candle[k] is None or not math.isfinite(float(candle[k])) or float(candle[k]) <= 0 for k in quote_keys):
            incomplete_quote_bars += 1
            if recompute and timestamp >= trade_start_ms:
                evaluate_signal(index, candle, False)
            continue
        if position is not None and close_position(index, candle):
            evaluate_signal(index, candle, False)
            continue

        closed_on_entry_bar = False
        if position is None and pending:
            keep = []
            for order in pending:
                # Match LabBreakout.should_cancel_entry(): the order expires
                # when the configured number of bars has elapsed, before that
                # candle is allowed to trigger a fill.
                if index - order["submitted_index"] >= expiry:
                    lifecycle.append({"event_type": "ORDER_CANCELLED", "timestamp_ms": timestamp,
                                      "reason": "PENDING_EXPIRY", "side": order["side"]})
                    continue
                side = order["side"]
                p = order["planned_price"]
                if order["order_type"] == "MARKET":
                    raw_fill = candle["ask_open"] if side == "long" else candle["bid_open"]
                    quote_side = "ASK" if side == "long" else "BID"
                    trigger = True
                elif order["order_type"] == "LIMIT" and side == "long":
                    trigger = candle["ask_low"] <= p
                    raw_fill = min(candle["ask_open"], p)
                    quote_side = "ASK"
                elif order["order_type"] == "LIMIT":
                    trigger = candle["bid_high"] >= p
                    raw_fill = max(candle["bid_open"], p)
                    quote_side = "BID"
                elif side == "long":
                    quote_open, trigger = candle["ask_open"], candle["ask_high"] >= p
                    raw_fill = max(quote_open, p)
                    quote_side = "ASK"
                else:
                    quote_open, trigger = candle["bid_open"], candle["bid_low"] <= p
                    raw_fill = min(quote_open, p)
                    quote_side = "BID"
                if not trigger:
                    keep.append(order)
                    continue
                slip = contract.slippage_points * contract.tick_size
                entry = raw_fill + slip if side == "long" else raw_fill - slip
                quantity = order["quantity"]
                stop = entry - stop_distance if side == "long" else entry + stop_distance
                target = entry + target_distance if side == "long" else entry - target_distance
                position = {"side": side, "entry": entry, "entry_quote": quote_side,
                            "entry_timestamp_ms": timestamp, "entry_index": index,
                            "signal_timestamp_ms": order["signal_timestamp_ms"],
                            "quantity": quantity, "stop": stop if stop_distance > 0 else None,
                            "target": target if target_distance > 0 else None}
                lifecycle.append({"event_type": "ORDER_FILLED", "timestamp_ms": timestamp,
                                  "order_type": order["order_type"], "fill_price": entry, "quote": quote_side})
                # Pending orders are OCO at the strategy position level.
                keep.clear()
                closed_on_entry_bar = close_position(index, candle)  # conservative same-bar SL/TP handling
                break
            pending = keep if position is None else []

        if closed_on_entry_bar:
            evaluate_signal(index, candle, False)
            continue

        if position is not None or pending:
            evaluate_signal(index, candle, False)
            continue
        generated = evaluate_signal(index, candle, True) if recompute else []
        for event in generated or by_time.get(timestamp, []):
            side_raw = str(event.get("side", "BUY")).lower()
            side = "long" if side_raw in {"buy", "long"} else "short"
            order_type = str(event.get("order_type", event.get("type", "MARKET"))).upper()
            quantity = abs(float(event.get("quantity", event.get("qty", qty_default))))
            planned_raw = event.get("planned_price", event.get("price", candle["bid_close"]))
            planned = None if planned_raw is None else float(planned_raw)
            if order_type not in {"MARKET", "STOP", "LIMIT"}:
                raise ValueError(f"unsupported quote-side order type: {order_type}")
            if order_type != "MARKET" and (planned is None or not math.isfinite(planned) or planned <= 0):
                raise ValueError(f"{order_type} quote-side order requires a finite positive planned price")
            normalized_order_type = "LIMIT" if "LIMIT" in order_type else "STOP" if "STOP" in order_type else "MARKET"
            pending.append({"side": side, "order_type": normalized_order_type,
                            "planned_price": planned, "quantity": quantity,
                            "submitted_index": index, "signal_timestamp_ms": int(event.get("signal_timestamp_ms", timestamp))})
            lifecycle.append({"event_type": "ORDER_SUBMITTED", "timestamp_ms": timestamp,
                              "side": side, "order_type": order_type, "planned_price": planned})

    winners = [x["net_pnl"] for x in closed if x["net_pnl"] > 0]
    losers = [x["net_pnl"] for x in closed if x["net_pnl"] < 0]
    gross_profit, gross_loss = sum(winners), abs(sum(losers))
    return {"status": "DUKASCOPY_BIDASK_REPLAY",
            "quote_quality": "INCOMPLETE_QUOTES_SKIPPED" if incomplete_quote_bars else "COMPLETE_QUOTES",
            "strategy_signals_recomputed": recompute,
            "precomputed_quote_indicators": sorted(quote_indicators),
            "signal_model": (f"{strategy_name} strategy_lab_quote_signal contract; quote-side signals recomputed by the strategy callback." if signal_evaluator is not None else
                             "LabBreakout quote-side signals: LONG reads Ask OHLC/EMA; SHORT reads Bid OHLC/EMA." if recompute
                             else "Persisted Jesse order intents; strategy signals were not recomputed."),
            "signal_decision_counts": decision_counts if recompute else None,
            "saved_decision_matches": saved_decision_matches if recompute else None,
            "saved_decision_mismatches": saved_decision_mismatches if recompute else None,
            "signal_mismatch_examples": mismatch_examples if recompute else [],
            "broker_contract_verified": False,
            "tick_order_known": False,
            "scope": ("Recalculates strategy_lab_quote_signal decisions and replays fills on Dukascopy Bid/Ask OHLC using the user-declared contract model; this does not establish broker/tick parity." if signal_evaluator is not None else
                      "Recalculates LabBreakout quote-side signals and replays fills on Dukascopy Bid/Ask OHLC using the user-declared contract model; this does not establish broker/tick parity." if recompute else
                      "Replays persisted Jesse order intents on Dukascopy Bid/Ask OHLC using the user-declared contract model; it does not rerun strategy signals or establish broker/tick parity."),
            "execution_policy": "market next bar open; stop/limit entries trigger and fill on executable quote; long exits Bid; short exits Ask; SL wins same-bar stop/target ambiguity",
            "contract": asdict(contract), "starting_balance": starting_balance,
            "ending_balance": balance, "net_profit": balance - starting_balance,
            "profit_factor": gross_profit / gross_loss if gross_loss else None,
            "recovery_factor": (balance - starting_balance) / (max_dd * starting_balance) if max_dd else None,
            "max_drawdown": max_dd,
            "win_rate": len(winners) / len(closed) if closed else None,
            "expectancy": sum(x["net_pnl"] for x in closed) / len(closed) if closed else None,
            "trades": closed, "lifecycle": lifecycle,
            "warnings": ([f"{incomplete_quote_bars} candle rows had incomplete Bid/Ask OHLC and were skipped by the execution replay."] if incomplete_quote_bars else []) +
                        ["M1 OHLC lacks tick ordering. SL-first policy resolves bars touching both SL and TP conservatively."] +
                        ([] if recompute else ["Only LabBreakout and strategies implementing strategy_lab_quote_signal have quote-side signal recomputation; others fall back to saved Jesse order intents."])}


# ---------------------------------------------------------------------------------------------- V102 execution model
# Optional, explicit and OFF by default: with ``execution=None`` the engines behave exactly as before (no margin,
# unlimited lots, no swap, no hours filter). Every block carries a status label; nothing is presented as broker fact
# unless the caller says so (CONTRACTUAL = broker-verified input, SIMULATION_ASSUMPTION / SCENARIO = lab assumption).
MARGIN_STATUSES = ("CONTRACTUAL", "SIMULATION_ASSUMPTION")
SWAP_STATUSES = ("VERIFIED", "SCENARIO")
_EXEC_KEYS = {"margin", "volume", "swap", "trading_hours", "reject_every_n", "stop_gap_points"}
_MARGIN_KEYS = {"leverage", "initial_rate", "maintenance_rate", "stop_out_level_pct", "on_insufficient", "status"}
_SWAP_KEYS = {"long_per_lot_per_night", "short_per_lot_per_night", "rollover_hour_utc", "triple_weekday",
              "skip_weekdays", "status"}


def validate_execution(model) -> list[str]:
    """Error list for an execution-model dict (empty = valid)."""
    if model is None:
        return []
    if not isinstance(model, dict):
        return ["execution: must be an object or null"]
    errs = [f"execution: unknown key {k!r}" for k in model if k not in _EXEC_KEYS]
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    m = model.get("margin")
    if m is not None:
        if not isinstance(m, dict):
            errs.append("execution.margin: must be an object")
        else:
            errs += [f"execution.margin: unknown key {k!r}" for k in m if k not in _MARGIN_KEYS]
            if m.get("initial_rate") is None and not (num(m.get("leverage")) and m["leverage"] > 0):
                errs.append("execution.margin: needs leverage > 0 or initial_rate")
            for k in ("initial_rate", "maintenance_rate"):
                if m.get(k) is not None and not (num(m[k]) and 0 < m[k] <= 1):
                    errs.append(f"execution.margin.{k}: must be in (0, 1]")
            if m.get("stop_out_level_pct") is not None and not (num(m["stop_out_level_pct"]) and m["stop_out_level_pct"] >= 0):
                errs.append("execution.margin.stop_out_level_pct: must be >= 0")
            if m.get("on_insufficient", "cap") not in ("cap", "reject"):
                errs.append("execution.margin.on_insufficient: must be cap or reject")
            if m.get("status") not in MARGIN_STATUSES:
                errs.append(f"execution.margin.status: must be one of {MARGIN_STATUSES}")
    v = model.get("volume")
    if v is not None:
        if not isinstance(v, dict) or set(v) - {"min_lot", "max_lot"}:
            errs.append("execution.volume: object with min_lot/max_lot only")
        else:
            for k, x in v.items():
                if x is not None and not (num(x) and x > 0):
                    errs.append(f"execution.volume.{k}: must be > 0")
            if v.get("min_lot") and v.get("max_lot") and v["min_lot"] > v["max_lot"]:
                errs.append("execution.volume: min_lot > max_lot")
    s = model.get("swap")
    if s is not None:
        if not isinstance(s, dict):
            errs.append("execution.swap: must be an object")
        else:
            errs += [f"execution.swap: unknown key {k!r}" for k in s if k not in _SWAP_KEYS]
            for k in ("long_per_lot_per_night", "short_per_lot_per_night"):
                if not num(s.get(k)):
                    errs.append(f"execution.swap.{k}: required number (account currency per lot per night, negative = cost)")
            if s.get("status") not in SWAP_STATUSES:
                errs.append(f"execution.swap.status: must be one of {SWAP_STATUSES} (no unlabelled swap)")
            if not (num(s.get("rollover_hour_utc", 22)) and 0 <= s.get("rollover_hour_utc", 22) < 24):
                errs.append("execution.swap.rollover_hour_utc: must be in [0, 24)")
            tw = s.get("triple_weekday", 2)
            if tw is not None and tw not in range(7):
                errs.append("execution.swap.triple_weekday: 0=Mon..6=Sun or null")
            if any(d not in range(7) for d in s.get("skip_weekdays", (5, 6))):
                errs.append("execution.swap.skip_weekdays: weekdays 0..6")
    h = model.get("trading_hours")
    if h is not None:
        if not isinstance(h, dict) or set(h) - {"utc_hours", "weekdays"}:
            errs.append("execution.trading_hours: object with utc_hours/weekdays only")
        elif any(x not in range(24) for x in h.get("utc_hours", ())) or any(x not in range(7) for x in h.get("weekdays", ())):
            errs.append("execution.trading_hours: utc_hours 0..23, weekdays 0..6")
    if model.get("reject_every_n") is not None and not (isinstance(model["reject_every_n"], int) and model["reject_every_n"] >= 1):
        errs.append("execution.reject_every_n: integer >= 1")
    if model.get("stop_gap_points") is not None and not (num(model["stop_gap_points"]) and model["stop_gap_points"] >= 0):
        errs.append("execution.stop_gap_points: >= 0")
    return errs


def margin_rates(margin: dict) -> tuple[float, float | None]:
    """(initial_rate, maintenance_rate) as fractions of notional; initial defaults to 1/leverage."""
    initial = margin.get("initial_rate") or 1.0 / float(margin["leverage"])
    return float(initial), (float(margin["maintenance_rate"]) if margin.get("maintenance_rate") else None)


def required_margin(lots: float, price: float, contract_size: float, rate: float, margin_rate: float) -> float:
    """Margin in account currency: notional (lots x contract x price x quote->account rate) x margin rate."""
    return lots * contract_size * price * rate * margin_rate


def margin_level_pct(equity: float, used_margin: float) -> float:
    return math.inf if used_margin <= 0 else equity / used_margin * 100.0


def floor_lots(value: float, step: float) -> float:
    return round(math.floor(value / step + 1e-9) * step, 8)


def swap_nights(t0_ms: int, t1_ms: int, cfg: dict) -> int:
    """Number of nightly financing charges for a position held over (t0, t1]: one per daily rollover instant
    (``rollover_hour_utc``) that is not on a skipped weekday; the ``triple_weekday`` rollover counts 3 (it carries
    the weekend value date). Granularity = the instants passed to it (bar opens): a rollover inside an H4 bar is
    charged at the next bar open."""
    if t1_ms <= t0_ms:
        return 0
    hour_ms = int(cfg.get("rollover_hour_utc", 22) * 3_600_000)
    skip, triple = cfg.get("skip_weekdays", (5, 6)), cfg.get("triple_weekday", 2)
    day_ms = 86_400_000
    d = (t0_ms - hour_ms) // day_ms + 1                            # first rollover strictly after t0
    nights = 0
    while d * day_ms + hour_ms <= t1_ms:
        weekday = (d + 3) % 7                                      # 1970-01-01 was a Thursday
        if weekday not in skip:
            nights += 3 if (triple is not None and weekday == triple) else 1
        d += 1
    return nights


def in_trading_hours(t_ms: int, cfg: dict | None) -> bool:
    if not cfg:
        return True
    hour = (t_ms // 3_600_000) % 24
    weekday = ((t_ms // 86_400_000) + 3) % 7
    return hour in cfg.get("utc_hours", range(24)) and weekday in cfg.get("weekdays", range(7))


def partial_plan(levels: list[dict], qty0: float, step: float, min_lot: float | None = None) -> list[dict]:
    """Planned partial fills ``[{r, qty, stop_r}]`` for ``levels`` ({r, fraction, stop_r}) on an initial ``qty0``.
    Each quantity floors to the volume step; a level that floors below the minimum lot is skipped; a remainder
    that would be left below the minimum lot rides with the last planned level (the position is then fully closed
    there). Shared by the research backtest and the forward model so both size partials identically."""
    floor_min = max(step, min_lot or step)
    plan, used = [], 0.0
    for lv in levels:
        q = floor_lots(qty0 * float(lv["fraction"]), step)
        if q < floor_min or used + q > qty0 + 1e-9:
            continue
        plan.append({"r": float(lv["r"]), "qty": q, "stop_r": lv.get("stop_r")})
        used = round(used + q, 8)
    if plan and 0 < round(qty0 - used, 8) < floor_min:
        plan[-1]["qty"] = round(plan[-1]["qty"] + (qty0 - used), 8)
    return plan


def partial_bar_events(*, side: str, bid: dict, ask: dict, stop: float | None, target: float | None,
                       levels: list[dict]) -> dict:
    """One bar's exits for a position with pending partial ``levels`` ({price, qty}, ascending distance).
    Stop first (conservative, same as conservative_ohlc_exit): if the stop is touched everything left exits at the
    stop and any level/target touched in the same bar is NOT credited (``ambiguous``). Otherwise every touched level
    fills at the better of its price and the bar open, then the final target takes the remainder."""
    if side == "long":
        o, h, l = bid["open"], bid["high"], bid["low"]
        stop_hit = stop is not None and l <= stop
        touched = [lv for lv in levels if h >= lv["price"]]
        target_hit = target is not None and h >= target
        fill = lambda price: max(o, price)
        stop_price = min(o, stop) if stop is not None else None
    elif side == "short":
        o, h, l = ask["open"], ask["high"], ask["low"]
        stop_hit = stop is not None and h >= stop
        touched = [lv for lv in levels if l <= lv["price"]]
        target_hit = target is not None and l <= target
        fill = lambda price: min(o, price)
        stop_price = max(o, stop) if stop is not None else None
    else:
        raise ValueError("side must be long or short")
    if stop_hit:
        return {"stop": {"reason": "STOP_LOSS", "price": stop_price}, "levels": [], "target": None,
                "ambiguous": bool(touched or target_hit)}
    return {"stop": None, "levels": [(lv, fill(lv["price"])) for lv in touched],
            "target": {"reason": "TAKE_PROFIT", "price": fill(target)} if target_hit else None, "ambiguous": False}
