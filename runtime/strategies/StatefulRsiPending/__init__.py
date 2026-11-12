import math
import jesse.helpers as jh
from jesse.indicators import rsi
from jesse.strategies import Strategy

from strategy_lab.events import InspectorMixin


class StatefulRsiPending(InspectorMixin, Strategy):
    """Debug-contract example: RSI filter, stop entry, expiry and cooldown state."""

    def hyperparameters(self):
        return [
            {"name": "rsi_period", "type": int, "min": 5, "max": 50, "default": 14},
            {"name": "rsi_entry_max", "type": float, "min": 15, "max": 60, "default": 45.0},
            {"name": "entry_interval", "type": int, "min": 5, "max": 240, "default": 30},
            {"name": "position_size", "type": float, "min": 0.01, "max": 1.0, "default": 0.01},
            {"name": "stop_offset", "type": float, "min": 0.01, "max": 25.0, "default": 0.2},
            {"name": "stop_distance", "type": float, "min": 0.1, "max": 25.0, "default": 2.0},
            {"name": "target_distance", "type": float, "min": 0.1, "max": 25.0, "default": 3.0},
            {"name": "expiry_bars", "type": int, "min": 1, "max": 30, "default": 5},
            {"name": "cooldown_bars", "type": int, "min": 0, "max": 100, "default": 3},
        ]

    @staticmethod
    def strategy_lab_quote_indicator_specs(parameters):
        """Declare an allow-listed causal indicator for the Bid/Ask replay engine."""
        return {"rsi": {"type": "rsi", "source": "ask_close",
                         "period": max(2, int(parameters["rsi_period"]))}}

    @staticmethod
    def strategy_lab_quote_signal(context):
        """Re-evaluate this strategy on executable Ask candles for the Bid/Ask ledger."""
        parameters = context["parameters"]
        value = context.get("indicators", {}).get("rsi")
        ready = value is not None and math.isfinite(float(value))
        value = float(value) if ready else None
        available = bool(context["available"])
        index = int(context["index"])
        last_close = context.get("last_close_index")
        last_close = -10**9 if last_close is None else int(last_close)
        cooldown_ready = index - last_close > int(parameters["cooldown_bars"])
        scheduled = index > 0 and index % int(parameters["entry_interval"]) == 0
        if not available:
            decision, reason = "SKIP", "POSITION_OR_PENDING_ORDER"
        elif not cooldown_ready:
            decision, reason = "SKIP", "COOLDOWN_ACTIVE"
        elif not scheduled:
            decision, reason = "SKIP", "NOT_SCHEDULED"
        elif not ready:
            decision, reason = "SKIP", "RSI_WARMUP"
        elif value > float(parameters["rsi_entry_max"]):
            decision, reason = "FAIL", "RSI_ABOVE_ENTRY_THRESHOLD"
        else:
            decision, reason = "PASS", "ENTRY_CONDITIONS_MET"
        result = {"decision": decision, "reason": reason, "signal_quote": "ASK"}
        if decision == "PASS":
            result["signal"] = {
                "side": "BUY", "order_type": "STOP",
                "planned_price": float(context["candle"]["ask_high"]) + float(parameters["stop_offset"]),
                "quantity": float(parameters["position_size"]),
            }
        return result

    def before(self):
        if jh.is_livetrading():
            self._stateful_decision = "SKIP"
            return

        period = int(self.hp["rsi_period"])
        ready = len(self.candles) >= period
        value = float(rsi(self.candles, period=period)) if ready else None
        has_capacity = not self.position.is_open and len(self.entry_orders) == 0
        last_close = int(getattr(self, "_last_close_index", -10**9))
        cooldown_ready = int(self.index) - last_close > int(self.hp["cooldown_bars"])
        scheduled = self.index > 0 and self.index % int(self.hp["entry_interval"]) == 0

        with self.debug_candle(context={"index": int(self.index)}) as debug:
            debug.indicator("RSI", value, timeframe=str(self.timeframe), unit="index",
                            source=f"jesse.indicators.rsi(period={period})")
            expression = debug.group("entry", "all", [
                debug.condition("Position/order available", has_capacity, "==", True,
                                skip_when_false=True),
                debug.condition("Cooldown complete", cooldown_ready, "==", True,
                                skip_when_false=True),
                debug.condition("Entry interval", scheduled, "==", True,
                                skip_when_false=True),
                debug.condition("RSI warmup", ready, "==", True, skip_when_false=True),
                debug.condition("RSI below entry threshold", lambda: value, "<=",
                                float(self.hp["rsi_entry_max"]), depends_on=["RSI warmup"],
                                timeframe=str(self.timeframe), unit="index"),
            ], stop_on_skip=True)
            accepted = debug.evaluate(expression)
            debug.state("index", int(self.index))
            debug.state("position_open", bool(self.position.is_open))
            debug.state("pending_order_ids", [str(order.id) for order in self.entry_orders])
            debug.state("pending_since_index", getattr(self, "_pending_since_index", None))
            debug.state("last_close_index", last_close if last_close > -10**8 else None)
            if accepted:
                trigger = float(self.current_candle[3]) + float(self.hp["stop_offset"])
                debug.signal("BUY", "RSI threshold passed; wait for bullish stop trigger",
                             order_type="STOP", price=trigger, depends_on=["entry"])
            self._stateful_decision = debug._decision

    def should_long(self):
        return not jh.is_livetrading() and getattr(self, "_stateful_decision", "SKIP") == "PASS"

    def should_short(self):
        return False

    def go_long(self):
        timestamp = int(self.current_candle[0])
        self._pending_signal_ms = timestamp
        quantity = float(self.hp["position_size"])
        trigger = float(self.current_candle[3]) + float(self.hp["stop_offset"])
        self._pending_since_index = int(self.index)
        self.buy = quantity, trigger
        self.record_lifecycle(event_type="ORDER_SUBMITTED", side="BUY", order_type="STOP",
                              planned_price=trigger, quantity=quantity,
                              signal_timestamp_ms=timestamp,
                              expiry_index=int(self.index) + int(self.hp["expiry_bars"]))

    def should_cancel_entry(self):
        started = getattr(self, "_pending_since_index", None)
        if started is None or not self.entry_orders:
            return False
        expired = int(self.index) - int(started) >= int(self.hp["expiry_bars"])
        if expired:
            self._cancel_ids = [str(order.id) for order in self.entry_orders
                                if getattr(order, "is_cancellable", False)]
            self.record_lifecycle(event_type="ORDER_CANCEL_REQUEST", reason="PENDING_EXPIRY",
                                  order_ids=self._cancel_ids)
        return expired

    def on_cancel(self):
        order_ids = list(getattr(self, "_cancel_ids", []))
        if not order_ids:
            order_ids = [str(order.id) for order in self.entry_orders]
        for order_id in order_ids:
            self.record_lifecycle(event_type="ORDER_CANCELLED", order_id=order_id,
                                  reason="PENDING_EXPIRY", order_ids=[order_id])
        self._pending_since_index = None
        self._cancel_ids = []

    def on_open_position(self, order):
        self._pending_since_index = None
        quantity = float(self.position.qty)
        entry = float(self.position.entry_price)
        self.stop_loss = quantity, entry - float(self.hp["stop_distance"])
        self.take_profit = quantity, entry + float(self.hp["target_distance"])
        self.record_lifecycle(event_type="POSITION_OPEN", order_id=str(order.id),
                              position_id=str(getattr(self.position, "id", "")),
                              signal_timestamp_ms=getattr(self, "_pending_signal_ms", None),
                              fill_timestamp_ms=int(self.current_candle[0]), side="long",
                              order_type=str(order.type), entry_price=entry, quantity=quantity,
                              stop_loss=float(self.stop_loss[1]), take_profit=float(self.take_profit[1]))

    def on_close_position(self, order, closed_trade):
        self._last_close_index = int(self.index)
        self.record_lifecycle(event_type="POSITION_CLOSE", order_id=str(order.id),
                              trade_id=str(getattr(closed_trade, "id", "")),
                              entry_price=float(closed_trade.entry_price),
                              exit_price=float(closed_trade.exit_price),
                              pnl=float(closed_trade.pnl), fee=float(closed_trade.fee),
                              exit_reason=str(getattr(order, "submitted_via", "")))
