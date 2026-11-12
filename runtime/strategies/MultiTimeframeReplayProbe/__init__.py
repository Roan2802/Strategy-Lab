import jesse.helpers as jh
from jesse.indicators import ema
from jesse.strategies import Strategy

from strategy_lab.events import InspectorMixin


class MultiTimeframeReplayProbe(InspectorMixin, Strategy):
    """Diagnostic example proving causal use of completed higher-timeframe candles."""

    @staticmethod
    def strategy_lab_data_timeframes(parameters):
        return [str(parameters.get("data_timeframe", "H1")).upper()]

    def hyperparameters(self):
        return [
            {"name": "data_timeframe", "type": "categorical", "options": ["H1", "H4", "D1"], "default": "H1"},
            {"name": "h1_ema_period", "type": int, "min": 2, "max": 30, "default": 5},
        ]

    def before(self):
        if jh.is_livetrading():
            self._probe_decision = "SKIP"
            return

        period = max(2, int(self.hp["h1_ema_period"]))
        timeframe = str(self.hp.get("data_timeframe", "H1")).upper()
        jesse_timeframe = {"H1": "1h", "H4": "4h", "D1": "1D"}[timeframe]
        duration_ms = {"H1": 3_600_000, "H4": 14_400_000, "D1": 86_400_000}[timeframe]
        higher = self.get_candles(self.exchange, self.symbol, jesse_timeframe)
        # Jesse exposes the forming higher-timeframe candle. Only include a candle
        # whose close boundary is known by the close of this M1 decision candle.
        known_by_ms = int(self.current_candle[0]) + 60_000
        completed = higher[(higher[:, 0] + duration_ms) <= known_by_ms]
        ready = len(completed) >= period
        htf_ema = float(ema(completed, period=period)) if ready else None
        close = float(self.current_candle[2])

        with self.debug_candle(context={"data_timeframe": timeframe}) as debug:
            debug.indicator(f"{timeframe} EMA", htf_ema, timeframe=timeframe, unit="price",
                             source=f"jesse.indicators.ema(period={period})")
            accepted = debug.evaluate(debug.group("entry", "all", [
                debug.condition(f"{timeframe} warmup ready", ready, "==", True, skip_when_false=True,
                                timeframe=timeframe),
                debug.condition(f"M1 close above completed {timeframe} EMA", close, ">", htf_ema,
                                depends_on=[f"{timeframe} warmup ready"], timeframe="M1", unit="price"),
            ], stop_on_skip=True))
            debug.state(f"completed_{timeframe.lower()}_candles", int(len(completed)))
            debug.state(f"last_completed_{timeframe.lower()}_timestamp_ms",
                        int(completed[-1, 0]) if len(completed) else None)
            self._probe_decision = "PASS" if accepted else debug._decision

    def should_long(self):
        return False

    def should_short(self):
        return False

    def go_long(self):
        return None

    def go_short(self):
        return None
