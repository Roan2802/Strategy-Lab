import json
import math
from datetime import datetime, timezone

import jesse.helpers as jh
from jesse.indicators import ema
from jesse.strategies import Strategy


class P0AuditProbe(Strategy):
    """Dukascopy-only P0 probe. Trading is disabled in live mode."""

    def hyperparameters(self) -> list:
        return [
            {'name': 'order_mode', 'type': int, 'min': 0, 'max': 2, 'default': 0},  # 0 market, 1 buy stop, 2 sell stop
            {'name': 'entry_interval', 'type': int, 'min': 20, 'max': 360, 'default': 60},
            {'name': 'position_size', 'type': float, 'min': 0.01, 'max': 10.0, 'default': 1.0},
            {'name': 'stop_distance', 'type': float, 'min': 0.1, 'max': 25.0, 'default': 1.0},
            {'name': 'target_distance', 'type': float, 'min': 0.1, 'max': 25.0, 'default': 1.0},
            {'name': 'stop_offset', 'type': float, 'min': 0.001, 'max': 5.0, 'default': 0.1},
            {'name': 'ema_period', 'type': int, 'min': 5, 'max': 100, 'default': 20},
            {'name': 'max_bar_range', 'type': float, 'min': 1.0, 'max': 100.0, 'default': 25.0},
            {'name': 'pending_expiry_bars', 'type': int, 'min': 1, 'max': 30, 'default': 5},
        ]

    def _candle_facts(self) -> dict:
        c = self.current_candle
        close = float(c[2])
        high = float(c[3])
        low = float(c[4])
        ready = len(self.candles) >= self.hp['ema_period']
        ema_value = float(ema(self.candles, period=self.hp['ema_period'])) if ready else None
        trend_pass = None if ema_value is None or not math.isfinite(ema_value) else (
            close < ema_value if self.hp['order_mode'] == 2 else close > ema_value
        )
        range_value = high - low
        range_pass = range_value <= self.hp['max_bar_range']
        scheduled = self.index > 0 and self.index % self.hp['entry_interval'] == 0
        can_enter = not self.position.is_open and len(self.entry_orders) == 0
        if not ready:
            decision, reason = 'SKIP', 'EMA_WARMUP'
        elif not can_enter:
            decision, reason = 'SKIP', 'POSITION_OR_PENDING_ORDER'
        elif not scheduled:
            decision, reason = 'SKIP', 'NOT_SCHEDULED'
        elif not trend_pass:
            decision, reason = 'FAIL', 'EMA_CONDITION'
        elif not range_pass:
            decision, reason = 'FAIL', 'BAR_RANGE_CONDITION'
        else:
            decision, reason = 'PASS', 'ENTRY_CONDITIONS_MET'
        return {
            'timestamp_ms': int(c[0]),
            'timestamp_utc': datetime.fromtimestamp(float(c[0]) / 1000, timezone.utc).isoformat(),
            'symbol': self.symbol,
            'timeframe': self.timeframe,
            'index': int(self.index),
            'close': close,
            'ema': ema_value,
            'ema_period': int(self.hp['ema_period']),
            'trend_condition': 'PASS' if trend_pass is True else 'FAIL' if trend_pass is False else 'SKIP',
            'range': range_value,
            'max_bar_range': float(self.hp['max_bar_range']),
            'range_condition': 'PASS' if range_pass else 'FAIL',
            'scheduled': bool(scheduled),
            'position_open': bool(self.position.is_open),
            'entry_order_ids': [str(getattr(o, 'id', '')) for o in self.entry_orders],
            'trade_id': str(getattr(self.position, 'id', '')) if self.position.is_open else None,
            'decision': decision,
            'reason': reason,
        }

    def before(self) -> None:
        if jh.is_livetrading():
            return
        facts = self._candle_facts()
        self._p0_facts = facts
        self.log('P0_INSPECTOR_JSON ' + json.dumps(facts, separators=(',', ':'), allow_nan=False))

    def should_long(self) -> bool:
        if jh.is_livetrading() or self.hp['order_mode'] == 2:
            return False
        return bool(getattr(self, '_p0_facts', {}).get('decision') == 'PASS')

    def should_short(self) -> bool:
        if jh.is_livetrading() or self.hp['order_mode'] != 2:
            return False
        return bool(getattr(self, '_p0_facts', {}).get('decision') == 'PASS')

    def go_long(self) -> None:
        self._p0_signal_ts_ms = int(self.current_candle[0])
        qty = float(self.hp['position_size'])
        if self.hp['order_mode'] == 1:
            price = float(self.current_candle[3]) + float(self.hp['stop_offset'])
        else:
            price = float(self.price)
        self.buy = qty, price
        self.log('P0_INSPECTOR_JSON ' + json.dumps({
            'event': 'ORDER_SUBMITTED', 'side': 'BUY', 'mode': int(self.hp['order_mode']),
            'signal_timestamp_ms': self._p0_signal_ts_ms, 'planned_price': price, 'qty': qty,
        }, separators=(',', ':')))

    def go_short(self) -> None:
        self._p0_signal_ts_ms = int(self.current_candle[0])
        qty = float(self.hp['position_size'])
        price = float(self.current_candle[4]) - float(self.hp['stop_offset'])
        self.sell = qty, price
        self.log('P0_INSPECTOR_JSON ' + json.dumps({
            'event': 'ORDER_SUBMITTED', 'side': 'SELL', 'mode': int(self.hp['order_mode']),
            'signal_timestamp_ms': self._p0_signal_ts_ms, 'planned_price': price, 'qty': qty,
        }, separators=(',', ':')))

    def on_open_position(self, order) -> None:
        entry = float(self.position.entry_price)
        qty = float(self.position.qty)
        stop = float(self.hp['stop_distance'])
        target = float(self.hp['target_distance'])
        if self.position.is_long:
            self.stop_loss = qty, entry - stop
            self.take_profit = qty, entry + target
        else:
            self.stop_loss = qty, entry + stop
            self.take_profit = qty, entry - target
        self.log('P0_INSPECTOR_JSON ' + json.dumps({
            'event': 'POSITION_OPEN',
            'position_id': str(getattr(self.position, 'id', '')),
            'entry_order_id': str(getattr(order, 'id', '')),
            'entry_order_side': str(getattr(order, 'side', '')),
            'entry_order_type': str(getattr(order, 'type', '')),
            'signal_timestamp_ms': getattr(self, '_p0_signal_ts_ms', None),
            'fill_timestamp_ms': int(self.current_candle[0]),
            'entry_price': entry, 'qty': qty,
            'stop_loss': float(self.stop_loss[1]), 'take_profit': float(self.take_profit[1]),
        }, separators=(',', ':'), allow_nan=False))

    def on_close_position(self, order, closed_trade) -> None:
        trade_orders = getattr(closed_trade, 'orders', []) or []
        order_records = [{
            'id': str(getattr(o, 'id', '')),
            'side': str(getattr(o, 'side', '')),
            'type': str(getattr(o, 'type', '')),
            'qty': float(getattr(o, 'qty', 0) or 0),
            'filled_qty': float(getattr(o, 'filled_qty', 0) or 0),
            'price': float(getattr(o, 'price', 0) or 0),
            'executed_at': getattr(o, 'executed_at', None),
            'submitted_via': str(getattr(o, 'submitted_via', '')),
        } for o in trade_orders]
        self.log('P0_INSPECTOR_JSON ' + json.dumps({
            'event': 'POSITION_CLOSE', 'trade_id': str(getattr(closed_trade, 'id', '')),
            'exit_order_id': str(getattr(order, 'id', '')),
            'exit_order_side': str(getattr(order, 'side', '')),
            'exit_order_type': str(getattr(order, 'type', '')),
            'entry_price': float(getattr(closed_trade, 'entry_price', 0) or 0),
            'exit_price': float(getattr(closed_trade, 'exit_price', 0) or 0),
            'qty': float(getattr(closed_trade, 'qty', 0) or 0),
            'pnl': float(getattr(closed_trade, 'pnl', 0) or 0),
            'fee': float(getattr(closed_trade, 'fee', 0) or 0),
            'opened_at_ms': getattr(closed_trade, 'opened_at', None),
            'closed_at_ms': getattr(closed_trade, 'closed_at', None),
            'orders': order_records,
        }, separators=(',', ':'), allow_nan=False))

    def should_cancel_entry(self) -> bool:
        if not self.entry_orders:
            self._p0_pending_since_index = None
            return False
        started = getattr(self, '_p0_pending_since_index', None)
        if started is None:
            self._p0_pending_since_index = self.index
            return False
        expired = self.index - started >= self.hp['pending_expiry_bars']
        if expired:
            orders = [{
                'id': str(getattr(o, 'id', '')),
                'side': str(getattr(o, 'side', '')),
                'type': str(getattr(o, 'type', '')),
                'price': float(getattr(o, 'price', 0) or 0),
            } for o in self.entry_orders]
            self.log('P0_INSPECTOR_JSON ' + json.dumps({
                'event': 'CANCEL_REQUEST', 'reason': 'PENDING_EXPIRY',
                'timestamp_ms': int(self.current_candle[0]), 'index': int(self.index),
                'pending_since_index': int(started), 'orders': orders,
            }, separators=(',', ':'), allow_nan=False))
        return bool(expired)

    def on_cancel(self) -> None:
        self._p0_pending_since_index = None
        self.log('P0_INSPECTOR_JSON ' + json.dumps({
            'event': 'ORDER_CANCEL_HOOK', 'timestamp_ms': int(self.current_candle[0]),
            'index': int(self.index), 'position_open': bool(self.position.is_open),
            'remaining_entry_order_ids': [str(getattr(o, 'id', '')) for o in self.entry_orders],
        }, separators=(',', ':'), allow_nan=False))

    def update_chart(self) -> None:
        facts = getattr(self, '_p0_facts', None)
        if not facts:
            return
        ema_value = facts['ema']
        if ema_value is not None and math.isfinite(float(ema_value)):
            self.add_line_to_candle_chart('EMA ' + str(self.hp['ema_period']), float(ema_value), color='#1dd6c1')
        self.add_horizontal_line_to_candle_chart('P0 reference', float(facts['close']), color='#f0b429', line_style='dotted')
        score = {'PASS': 1.0, 'FAIL': 0.0, 'SKIP': -1.0}[facts['decision']]
        self.add_extra_line_chart('Entry Inspector', 'Decision (PASS=1, FAIL=0, SKIP=-1)', score, color='#a78bfa')
        self.add_horizontal_line_to_extra_chart('Entry Inspector', 'PASS threshold', 0.5, color='#35c759', line_style='dotted')

