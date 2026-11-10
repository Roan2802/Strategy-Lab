"""After the v10.1 calendar update: which UTC days in real data (2024-10..2026-10) still differ from the calendar, ignoring
the future-dated overrides (they do not apply to the audited period)."""
import json, sys
from datetime import timedelta
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "C:/AI/Jesse/Jesse-Audit")
from strategy_lab.daily_sync import load_sync_config
from strategy_lab.data_manager import SYMBOLS, DISPLAY_ALIASES
cfg = load_sync_config(Path("strategy_lab/daily_sync.local.json"))
cache = Path("strategy_lab/data_cache")
START = pd.Timestamp("2024-10-01", tz="UTC"); END = pd.Timestamp("2026-10-10", tz="UTC")
res = {}
for plan in cfg.plans:
    src = SYMBOLS.get(plan.symbol, plan.symbol)
    name = next(n for n in (plan.symbol, DISPLAY_ALIASES.get(plan.symbol, ""), src) if (cache / f"{n}_M1_bidask.parquet").exists())
    d = pd.read_parquet(cache / f"{name}_M1_bidask.parquet", columns=["bid_close"])
    got = d.index[(d.index >= START) & (d.index < END)]
    exp = [plan.calendar.expected(x) for x in pd.date_range(START.date(), END.date() - timedelta(days=1)).date]
    exp = [e for e in exp if len(e)]
    exp = exp[0].append(exp[1:])
    diff = got.symmetric_difference(exp)
    by = pd.Series(1, index=diff).groupby(diff.date).size()
    tz = plan.calendar.timezone
    days = {}
    for x, n in by.items():
        t0 = pd.Timestamp(x, tz="UTC"); g = got[(got >= t0) & (got < t0 + pd.Timedelta(days=1))]; e = exp[(exp >= t0) & (exp < t0 + pd.Timedelta(days=1))]
        days[str(x)] = {"wd": pd.Timestamp(x).day_name()[:3], "mismatch_min": int(n), "observed": int(len(g)), "expected": int(len(e))}
    res[plan.symbol] = {"observed": int(len(got)), "expected": int(len(exp)), "mismatch_minutes": int(len(diff)), "mismatch_days": len(days),
                        "match_pct": round(100 * (1 - len(diff) / max(1, len(exp))), 3), "days": days}
    print(plan.symbol, "mismatch_min", len(diff), "days", len(days), "match%", res[plan.symbol]["match_pct"])
Path("evidence/v101/data/calendar_residual.json").write_text(json.dumps(res, indent=1))
