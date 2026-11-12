import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import jesse.helpers as jh
from jesse.research import backtest

root = Path("/home/research_scripts")
source = root / "P0_XAUUSD_Duka_M1_20260901_20260915.csv"
rows = []
with source.open(newline="", encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        rows.append([int(r["timestamp"]), float(r["open"]), float(r["close"]), float(r["high"]), float(r["low"]), float(r["volume"])])
arr = np.asarray(rows, dtype=np.float64)
start_ms = int(datetime(2026, 9, 14, tzinfo=timezone.utc).timestamp() * 1000)
end_ms = int(datetime(2026, 9, 15, tzinfo=timezone.utc).timestamp() * 1000)
pre = arr[arr[:, 0] < start_ms]
trading = arr[(arr[:, 0] >= start_ms) & (arr[:, 0] < end_ms)]
warm = pre[-210:]
key = jh.key("Custom Data", "XAU-USD")
route = [{"exchange": "Custom Data", "symbol": "XAU-USD", "timeframe": "1m", "strategy": "P0AuditProbe"}]
base_hp = {"entry_interval": 60, "position_size": 1.0, "stop_distance": 1.0, "target_distance": 1.0, "stop_offset": 0.1, "ema_period": 20, "max_bar_range": 25.0, "pending_expiry_bars": 5}
results = []
for mode in (0, 1, 2):
    hp = {**base_hp, "order_mode": mode}
    futures = mode == 2
    cfg = {"starting_balance": 10000, "fee": 0.0006, "type": "futures" if futures else "spot", "simulation_model": "perpetual_futures" if futures else "spot", "annualization": 365, "exchange": "Custom Data", "warm_up_candles": 210}
    if futures:
        cfg.update({"futures_leverage": 1, "futures_leverage_mode": "cross"})
    result = backtest(cfg, route, [], {key: {"exchange": "Custom Data", "symbol": "XAU-USD", "candles": trading.copy()}}, {key: {"exchange": "Custom Data", "symbol": "XAU-USD", "candles": warm.copy()}}, hyperparameters=hp, generate_csv=True, generate_json=True, generate_logs=True)
    results.append({"order_mode": mode, "result_keys": list(result.keys()), "metrics": result.get("metrics"), "trade_count": len(result.get("trades", result.get("closed_trades", [])) or []), "summary": {k: result.get(k) for k in ("total_trades", "net_profit", "total_net_profit", "fees", "total_fees") if k in result}, "logs_sample": (result.get("logs") or [])[-5:]})
    (root / f"P0_Duka_order_mode_{mode}.json").write_text(json.dumps(results[-1], indent=2, default=str), encoding="utf-8")
print(json.dumps({"source": str(source), "source_rows": len(arr), "warmup_rows": len(warm), "trading_rows": len(trading), "results": results}, indent=2, default=str))


