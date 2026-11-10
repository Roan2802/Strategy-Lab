"""Starter Jesse strategy using the Strategy Lab decision-trace contract."""
import jesse.helpers as jh
from jesse.strategies import Strategy
from strategy_lab.events import InspectorMixin


class MyStrategy(InspectorMixin, Strategy):
    def hyperparameters(self):
        return [
            {"name": "position_size", "type": float, "min": 0.01, "max": 1.0, "default": 0.01},
        ]

    def before(self):
        if jh.is_livetrading():
            self._lab_decision = "SKIP"
            return

        # Replace this example condition with your computed indicator and filters.
        close = float(self.current_candle[2])
        open_price = float(self.current_candle[1])
        with self.debug_candle(context={"index": int(self.index)}) as debug:
            debug.indicator("Candle body", close - open_price, timeframe=str(self.timeframe), unit="price")
            entry = debug.group("entry", "all", [
                debug.condition("Bullish candle", close, ">", open_price, unit="price"),
                debug.condition("No position", not self.position.is_open, "==", True,
                                skip_when_false=True),
            ], stop_on_skip=True)
            accepted = debug.evaluate(entry)
            debug.state("position_open", bool(self.position.is_open))
            if accepted:
                debug.signal("BUY", "Bullish candle", order_type="MARKET", price=float(self.price),
                             depends_on=["entry"])
            self._lab_decision = debug._decision

    def should_long(self):
        return not jh.is_livetrading() and getattr(self, "_lab_decision", "SKIP") == "PASS"

    def should_short(self):
        return False

    def go_long(self):
        self.buy = float(self.hp["position_size"]), float(self.price)
        self.record_lifecycle(event_type="ORDER_SUBMITTED", side="BUY", order_type="MARKET",
                              planned_price=float(self.price), quantity=float(self.hp["position_size"]))
