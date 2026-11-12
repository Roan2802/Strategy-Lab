"""Bounded, offline synthetic smoke checks for Jesse's research API."""

import json
import sys
import time
from pathlib import Path

import numpy as np

import jesse.helpers as jh
from jesse.research import backtest, optimize

sys.path.insert(0, str(Path.cwd() / 'strategies'))
from AuditProbe import AuditProbe


EXCHANGE = 'Sandbox'
SYMBOL = 'SYNTH-USD'
TIMEFRAME = '1m'
BASE_MS = 1_735_689_600_000


def make_candles(offset: int, count: int, start_day: int = 0) -> np.ndarray:
    rows = []
    close = 100.0
    rng = np.random.default_rng(100 + offset)
    for i in range(count):
        opening = close
        close = max(1.0, close + float(rng.choice([-0.9, -0.4, -0.1, 0.3, 0.7, 1.1])))
        high = max(opening, close) + 0.45
        low = min(opening, close) - 0.45
        timestamp = BASE_MS + start_day * 86_400_000 + i * 60_000
        rows.append([timestamp, opening, close, high, low, 10.0])
    return np.asarray(rows, dtype=np.float64)


def candle_bundle(array: np.ndarray) -> dict:
    return {jh.key(EXCHANGE, SYMBOL): {
        'exchange': EXCHANGE,
        'symbol': SYMBOL,
        'candles': array,
    }}


def main() -> None:
    candles = make_candles(0, 3 * 1440)
    route = [{'exchange': EXCHANGE, 'symbol': SYMBOL, 'timeframe': TIMEFRAME, 'strategy': AuditProbe}]
    config = {
        'starting_balance': 10_000,
        'fee': 0,
        'type': 'futures',
        'futures_leverage': 1,
        'futures_leverage_mode': 'cross',
        'exchange': EXCHANGE,
        'warm_up_candles': 0,
    }

    started = time.perf_counter()
    first = backtest(config, route, [], candle_bundle(candles), generate_logs=True)
    second = backtest(config, route, [], candle_bundle(candles), generate_logs=True)
    stable_metric_keys = ('total', 'finishing_balance', 'net_profit', 'win_rate', 'max_drawdown')
    stable_trade_keys = ('symbol', 'type', 'entry_price', 'exit_price', 'qty', 'opened_at', 'closed_at', 'fee', 'PNL')
    first_trades = [tuple(trade[key] for key in stable_trade_keys) for trade in first['trades']]
    second_trades = [tuple(trade[key] for key in stable_trade_keys) for trade in second['trades']]
    deterministic = (
        all(first['metrics'][key] == second['metrics'][key] for key in stable_metric_keys)
        and first_trades == second_trades
    )

    train = candle_bundle(make_candles(0, 3 * 1440))
    test = candle_bundle(make_candles(2, 3 * 1440, start_day=10))
    train_warmup = candle_bundle(make_candles(0, 60, start_day=-1))
    test_warmup = candle_bundle(make_candles(2, 60, start_day=9))
    optimize_config = {
        'exchange': {
            'name': EXCHANGE,
            'balance': 10_000,
            'fee': 0,
            'type': 'futures',
            'futures_leverage': 1,
            'futures_leverage_mode': 'cross',
        },
        'warm_up_candles': 0,
    }
    optimization = optimize(
        config=optimize_config,
        routes=[{'symbol': SYMBOL, 'timeframe': TIMEFRAME, 'strategy': 'AuditProbe'}],
        data_routes=[],
        training_candles=train,
        training_warmup_candles=train_warmup,
        testing_candles=test,
        testing_warmup_candles=test_warmup,
        optimal_total=5,
        fast_mode=True,
        cpu_cores=1,
        trials=1,
        objective_function='sharpe',
        best_candidates_count=2,
        progress_bar=False,
    )
    if not optimization['best_trials']:
        print(json.dumps(optimization, indent=2, default=str))
        raise SystemExit(2)
    best = optimization['best_trials'][0]
    reused = backtest(
        config,
        route,
        [],
        candle_bundle(candles),
        hyperparameters=best['params'],
    )

    result = {
        'deterministic_repeat_metrics_and_trades': deterministic,
        'trade_count': len(first['trades']),
        'order_count': sum(len(trade.get('orders', [])) for trade in first['trades']),
        'has_logs': bool(first.get('logs')),
        'optimize_requested_trials': optimization['total_trials'],
        'optimize_completed_trials': optimization['completed_trials'],
        'best_params': best['params'],
        'best_dna_present': bool(best.get('dna')),
        'best_params_reused_in_backtest': bool(reused.get('metrics')),
        'elapsed_seconds': round(time.perf_counter() - started, 3),
    }
    print(json.dumps(result, indent=2, default=str))
    if not deterministic or not result['trade_count'] or not optimization['completed_trials']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
