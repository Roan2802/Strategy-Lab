from jesse.strategies import Strategy


class AuditProbe(Strategy):
    """Temporary deterministic probe for Jesse audit runs; never connects to a live exchange."""

    def hyperparameters(self) -> list:
        return [
            {'name': 'entry_interval', 'type': int, 'min': 4, 'max': 6, 'default': 4},
            {'name': 'target_distance', 'type': float, 'min': 0.5, 'max': 1.5, 'default': 1.0},
        ]

    def should_long(self) -> bool:
        return self.index > 0 and self.index % self.hp['entry_interval'] == 0

    def should_short(self) -> bool:
        return False

    def go_long(self) -> None:
        self.buy = 1, self.price

    def on_open_position(self, order) -> None:
        self.take_profit = self.position.qty, self.position.entry_price + self.hp['target_distance']
        self.stop_loss = self.position.qty, self.position.entry_price - self.hp['target_distance']

    def should_cancel_entry(self) -> bool:
        return False

    def update_chart(self) -> None:
        self.add_horizontal_line_to_candle_chart('Audit entry reference', self.price, color='blue')
