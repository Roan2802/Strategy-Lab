"""Reproduce Jesse's M1-to-higher-timeframe candle aggregation checks."""

import json

import numpy as np

from jesse.services.candle_service import (
    generate_candle_from_one_minutes,
    generate_completed_candles_from_observed_minutes,
)

BASE_MS = 1_735_689_600_000  # 2025-01-01 00:00:00 UTC
TIMEFRAMES = {'5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240, '1D': 1440}


def main() -> None:
    results = {}
    for timeframe, count in TIMEFRAMES.items():
        candles = np.array([
            [BASE_MS + i * 60_000, 100 + i, 100 + i + 0.5, 100 + i + 1, 100 + i - 1, 0 if i % 2 else 2]
            for i in range(count)
        ], dtype=np.float64)
        aggregate = generate_candle_from_one_minutes(timeframe, candles)
        expected = np.array([
            candles[0, 0], candles[0, 1], candles[-1, 2], candles[:, 3].max(), candles[:, 4].min(), candles[:, 5].sum()
        ])
        assert np.array_equal(aggregate, expected), (timeframe, aggregate, expected)
        results[timeframe] = aggregate.tolist()

    observed = np.array([
        [BASE_MS + i * 60_000, 10 + i, 10 + i + 0.25, 11 + i, 9 + i, 1]
        for i in (0, 1, 3, 4)
    ], dtype=np.float64)
    completed = generate_completed_candles_from_observed_minutes('5m', observed, BASE_MS + 5 * 60_000)
    assert completed.shape == (1, 6)
    assert completed[0, 0] == BASE_MS and completed[0, 5] == 4
    results['5m_with_one_missing_minute'] = completed[0].tolist()
    print(json.dumps({'passed': True, 'aggregates': results}, indent=2))


if __name__ == '__main__':
    main()
