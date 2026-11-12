import jesse.helpers as jh
from jesse.strategies import Strategy

from strategy_lab.events import InspectorMixin


class ExampleStrategy(InspectorMixin, Strategy):
    """Minimal user-strategy example that records decisions and lifecycle."""

    def hyperparameters(self):
        return [
            {"name": "entry_interval", "type": int, "min": 10, "max": 240, "default": 60},
            {"name": "position_size", "type": float, "min": 0.01, "max": 1.0, "default": 0.01},
        ]

    def before(self):
        if jh.is_livetrading():
            self._example_decision = "SKIP"
            return

        candle = self.current_candle
        open_price, close = float(candle[1]), float(candle[2])
        scheduled = self.index > 0 and self.index % int(self.hp["entry_interval"]) == 0
        available = not self.position.is_open and len(self.entry_orders) == 0
        with self.debug_candle() as debug:
            debug.indicator("Candle body", close - open_price, unit="price")
            entry = debug.group("entry", "all", [
                debug.condition("Position/order available", available, "==", True,
                                skip_when_false=True),
                debug.condition("Entry schedule", scheduled, "==", True,
                                skip_when_false=True),
                debug.condition("Bullish candle", close, ">=", open_price),
            ], stop_on_skip=True)
            accepted = debug.evaluate(entry)
            debug.state("index", int(self.index))
            debug.state("position_open", bool(self.position.is_open))
            debug.state("pending_order_ids", [str(order.id) for order in self.entry_orders])
            if accepted:
                debug.signal("BUY", "Bullish scheduled candle", order_type="MARKET",
                             price=float(self.price), depends_on=["entry"])
            self._example_decision = "PASS" if accepted else debug._decision

    def should_long(self):
        return not jh.is_livetrading() and getattr(self, "_example_decision", "SKIP") == "PASS"

    def should_short(self):
        return False

    def go_long(self):
        self._example_signal_timestamp = int(self.current_candle[0])
        quantity = float(self.hp["position_size"])
        self.buy = quantity, float(self.price)
        self.record_lifecycle(event_type="ORDER_SUBMITTED", side="BUY", order_type="MARKET",
                              planned_price=float(self.price), quantity=quantity,
                              signal_timestamp_ms=self._example_signal_timestamp)

    def go_short(self):
        return None

    def should_cancel_entry(self):
        return False

    def on_open_position(self, order):
        quantity = float(self.position.qty)
        entry = float(self.position.entry_price)
        self.stop_loss = (quantity, entry - 3.0)
        self.take_profit = (quantity, entry + 3.0)
        self.record_lifecycle(event_type="POSITION_OPEN", order_id=str(order.id),
                              position_id=str(getattr(self.position, "id", "")),
                              signal_timestamp_ms=getattr(self, "_example_signal_timestamp", int(self.current_candle[0])),
                              fill_timestamp_ms=int(self.current_candle[0]), side="long",
                              order_type=str(order.type), entry_price=entry, quantity=quantity,
                              stop_loss=float(self.stop_loss[1]), take_profit=float(self.take_profit[1]))

    def on_close_position(self, order, closed_trade):
        self.record_lifecycle(event_type="POSITION_CLOSE", order_id=str(order.id),
                              trade_id=str(getattr(closed_trade, "id", "")),
                              entry_price=float(closed_trade.entry_price),
                              exit_price=float(closed_trade.exit_price),
                              pnl=float(closed_trade.pnl), fee=float(closed_trade.fee),
                              exit_reason=str(getattr(order, "submitted_via", "")))

    def update_chart(self):
        if not jh.is_livetrading() and getattr(self, "_example_decision", None) == "PASS":
            self.add_horizontal_line_to_candle_chart("ExampleStrategy signal", float(self.current_candle[2]),
                                                     color="#60a5fa", line_style="dotted")
