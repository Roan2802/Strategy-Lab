import csv, json
from collections import defaultdict
from pathlib import Path

base = Path(r'C:\AI\Jesse\Jesse-Audit\evidence\market_data')
results = {}
for symbol in ('XAUUSD', 'US30'):
    path = base / f'Duka_{symbol}_M1_20260401_20260928_jesse.csv'
    buckets = {5: defaultdict(list), 15: defaultdict(list), 60: defaultdict(list)}
    count = 0
    with path.open(newline='', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            ts = int(r['timestamp']) // 1000
            candle = (float(r['open']), float(r['high']), float(r['low']), float(r['close']), float(r['volume']))
            for n, out in buckets.items():
                out[ts - ts % (n * 60)].append((ts, candle))
            count += 1
    tf = {}
    for n, grouped in buckets.items():
        invalid = 0
        for series in grouped.values():
            for (t1, _), (t2, _) in zip(series, series[1:]):
                if t2 - t1 != 60:
                    invalid += 1
        first_ts = min(grouped)
        first = [c for _, c in grouped[first_ts]]
        tf['M5' if n == 5 else 'M15' if n == 15 else 'H1'] = {
            'bar_count': len(grouped), 'invalid_internal_minute_steps': invalid,
            'sample_first_utc_epoch': first_ts,
            'sample_first_ohlcv_aggregate': [first[0][0], max(x[1] for x in first), min(x[2] for x in first), first[-1][3], sum(x[4] for x in first)]
        }
    results[symbol] = {'source': path.name, 'm1_rows': count, 'timeframes': tf}
out = Path(r'C:\AI\Jesse\Jesse-Audit\evidence\results\dukascopy_aggregation_validation.json')
out.write_text(json.dumps(results, indent=2), encoding='utf-8')
print(json.dumps(results, indent=2))
