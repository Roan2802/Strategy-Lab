"""Example Strategy Lab strategy with versioned PASS/FAIL/SKIP instrumentation."""
import math

import jesse.helpers as jh
from jesse.indicators import ema
from jesse.strategies import Strategy
from strategy_lab.events import InspectorMixin


class LabBreakout(InspectorMixin, Strategy):
    """A small EMA/range probe intended for offline research only."""

    def hyperparameters(self):
        return [
            {"name": "order_mode", "type": "categorical", "options": [0, 1, 2], "default": 0},
            {"name": "entry_interval", "type": int, "min": 10, "max": 240, "default": 60},
            {"name": "position_size", "type": float, "min": 0.01, "max": 10.0, "default": 1.0},
            {"name": "ema_period", "type": int, "min": 5, "max": 100, "default": 20},
            {"name": "max_bar_range", "type": float, "min": 1.0, "max": 100.0, "default": 25.0},
            {"name": "stop_distance", "type": float, "min": 0.1, "max": 25.0, "default": 1.0},
            {"name": "target_distance", "type": float, "min": 0.1, "max": 25.0, "default": 1.0},
            {"name": "stop_offset", "type": float, "min": 0.001, "max": 5.0, "default": 0.1},
            {"name": "pending_expiry_bars", "type": int, "min": 1, "max": 30, "default": 5},
        ]

    def before(self):
        if jh.is_livetrading():
            return
        self._lab_pending_order_ids = self._lab_cancellable_entry_order_ids()
        c = self.current_candle
        ready = len(self.candles) >= self.hp["ema_period"]
        ema_value = float(ema(self.candles, period=self.hp["ema_period"])) if ready else None
        close, high, low = float(c[2]), float(c[3]), float(c[4])
        mode = int(self.hp["order_mode"])
        trend_pass = None if ema_value is None or not math.isfinite(ema_value) else (close < ema_value if mode == 2 else close > ema_value)
        range_value = high - low
        range_pass = range_value <= float(self.hp["max_bar_range"])
        scheduled = self.index > 0 and self.index % int(self.hp["entry_interval"]) == 0
        available = not self.position.is_open and len(self.entry_orders) == 0
        checks = [
            {"name": "EMA ready", "value": ready, "operator": "==", "threshold": True, "result": "PASS" if ready else "SKIP"},
            {"name": "EMA direction", "value": close, "operator": "<" if mode == 2 else ">", "threshold": ema_value, "result": "PASS" if trend_pass else "FAIL" if trend_pass is False else "SKIP"},
            {"name": "Maximum bar range", "value": range_value, "operator": "<=", "threshold": float(self.hp["max_bar_range"]), "result": "PASS" if range_pass else "FAIL"},
            {"name": "Entry schedule", "value": scheduled, "operator": "==", "threshold": True, "result": "PASS" if scheduled else "SKIP"},
            {"name": "Position/order available", "value": available, "operator": "==", "threshold": True, "result": "PASS" if available else "SKIP"},
        ]
        if not ready:
            decision, reason = "SKIP", "EMA_WARMUP"
        elif not available:
            decision, reason = "SKIP", "POSITION_OR_PENDING_ORDER"
        elif not scheduled:
            decision, reason = "SKIP", "NOT_SCHEDULED"
        elif not trend_pass:
            decision, reason = "FAIL", "EMA_CONDITION"
        elif not range_pass:
            decision, reason = "FAIL", "BAR_RANGE_CONDITION"
        else:
            decision, reason = "PASS", "ENTRY_CONDITIONS_MET"
        self._lab_decision = decision
        self._lab_order_started = getattr(self, "_lab_order_started", None)
        self.record_candle(checks=checks, decision=decision, reason=reason,
            state={"index": int(self.index), "position_open": bool(self.position.is_open),
                   "pending_order_ids": [str(o.id) for o in self.entry_orders],
                   "close": close, "high": high, "low": low, "ema": ema_value})

    def should_long(self):
        return not jh.is_livetrading() and self._lab_decision == "PASS" and int(self.hp["order_mode"]) in (0, 1)

    def should_short(self):
        return not jh.is_livetrading() and self._lab_decision == "PASS" and int(self.hp["order_mode"]) == 2

    def _lab_cancellable_entry_order_ids(self):
        return [str(order.id) for order in self.entry_orders
                if getattr(order, "is_cancellable", False)]

    def go_long(self):
        self._lab_signal_timestamp = int(self.current_candle[0])
        qty = float(self.hp["position_size"])
        mode = int(self.hp["order_mode"])
        price = float(self.current_candle[3]) + float(self.hp["stop_offset"]) if mode == 1 else float(self.price)
        self.buy = qty, price
        if mode == 1:
            self._lab_order_started = self.index
        self.record_lifecycle(event_type="ORDER_SUBMITTED", timestamp_ms=int(self.current_candle[0]),
            side="BUY", order_type="STOP" if mode == 1 else "MARKET", planned_price=price,
            quantity=qty, signal_timestamp_ms=int(self.current_candle[0]))

    def go_short(self):
        self._lab_signal_timestamp = int(self.current_candle[0])
        qty = float(self.hp["position_size"])
        price = float(self.current_candle[4]) - float(self.hp["stop_offset"])
        self.sell = qty, price
        self._lab_order_started = self.index
        self.record_lifecycle(event_type="ORDER_SUBMITTED", timestamp_ms=int(self.current_candle[0]),
            side="SELL", order_type="STOP", planned_price=price, quantity=qty,
            signal_timestamp_ms=int(self.current_candle[0]))

    def should_cancel_entry(self):
        order_ids = self._lab_cancellable_entry_order_ids()
        if not order_ids or self._lab_order_started is None:
            return False
        expired = self.index - self._lab_order_started >= int(self.hp["pending_expiry_bars"])
        if expired:
            self._lab_pending_order_ids = order_ids
            self._lab_cancel_reason = "PENDING_EXPIRY"
            self.record_lifecycle(event_type="ORDER_CANCEL_REQUEST", reason="PENDING_EXPIRY",
                                  order_ids=self._lab_pending_order_ids)
        return expired

    def on_cancel(self):
        self._lab_order_started = None
        order_ids = list(getattr(self, "_lab_pending_order_ids", []))
        if not order_ids:
            order_ids = self._lab_cancellable_entry_order_ids()
        reason = getattr(self, "_lab_cancel_reason", None)
        if reason is None:
            reason = "BACKTEST_END" if self.index >= len(self.candles) - 1 else "JESSE_CANCEL_HOOK"
        if order_ids:
            for order_id in order_ids:
                self.record_lifecycle(event_type="ORDER_CANCELLED", order_id=order_id,
                    reason=reason, order_ids=[order_id])
        else:
            self.record_lifecycle(event_type="ORDER_CANCELLED", reason=reason, order_ids=[])
        self._lab_pending_order_ids = []
        self._lab_cancel_reason = None

    def on_open_position(self, order):
        self._lab_pending_order_ids = []
        self._lab_cancel_reason = None
        qty = float(self.position.qty)
        entry = float(self.position.entry_price)
        stop_distance, target_distance = float(self.hp["stop_distance"]), float(self.hp["target_distance"])
        if self.position.is_long:
            self.stop_loss, self.take_profit = (qty, entry - stop_distance), (qty, entry + target_distance)
        else:
            self.stop_loss, self.take_profit = (qty, entry + stop_distance), (qty, entry - target_distance)
        self.record_lifecycle(event_type="POSITION_OPEN", order_id=str(order.id),
            position_id=str(getattr(self.position, "id", "")),
            signal_timestamp_ms=getattr(self, "_lab_signal_timestamp", int(self.current_candle[0])),
            fill_timestamp_ms=int(self.current_candle[0]), side="long" if self.position.is_long else "short",
            order_type=str(order.type), entry_price=entry, quantity=qty,
            stop_loss=float(self.stop_loss[1]), take_profit=float(self.take_profit[1]))

    def on_close_position(self, order, closed_trade):
        linked_orders = [{"id": str(o.id), "side": str(o.side), "type": str(o.type),
                          "price": float(o.price), "filled_qty": float(o.filled_qty)}
                         for o in (getattr(closed_trade, "orders", []) or [])]
        self.record_lifecycle(event_type="POSITION_CLOSE", order_id=str(order.id),
            trade_id=str(getattr(closed_trade, "id", "")),
            position_id=str(getattr(self.position, "id", "")),
            entry_price=float(closed_trade.entry_price), exit_price=float(closed_trade.exit_price),
            pnl=float(closed_trade.pnl), fee=float(closed_trade.fee),
            opened_at_ms=getattr(closed_trade, "opened_at", None),
            closed_at_ms=getattr(closed_trade, "closed_at", None), orders=linked_orders,
            exit_reason=str(getattr(order, "submitted_via", "")))

    def update_chart(self):
        if jh.is_livetrading():
            return
        if len(self.candles) >= self.hp["ema_period"]:
            value = float(ema(self.candles, period=self.hp["ema_period"]))
            self.add_line_to_candle_chart(f"EMA {self.hp['ema_period']}", value, color="#38bdf8")
