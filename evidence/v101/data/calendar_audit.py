"""Check every configured market calendar against real stored M1 data (read-only; parquet cache of the original CSVs)."""
import json, sys
from datetime import date, timedelta
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "C:/AI/Jesse/Jesse-Audit")
from strategy_lab.daily_sync import load_sync_config
from strategy_lab.data_manager import SYMBOLS, DISPLAY_ALIASES

cfg = load_sync_config(Path("strategy_lab/daily_sync.local.json"))
cache = Path("strategy_lab/data_cache")
START = pd.Timestamp("2024-10-01", tz="UTC"); END = pd.Timestamp("2026-10-10", tz="UTC")
out = {}

def runs(mask):
    """minutes-of-day boolean(1440) -> list of 'HH:MM-HH:MM'"""
    res, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None: start = i
        if not v and start is not None:
            res.append(f"{start//60:02d}:{start%60:02d}-{i//60:02d}:{i%60:02d}"); start = None
    return res

for plan in cfg.plans:
    src = SYMBOLS.get(plan.symbol, plan.symbol)
    name = next((n for n in (plan.symbol, DISPLAY_ALIASES.get(plan.symbol, ""), src) if (cache / f"{n}_M1_bidask.parquet").exists()), None)
    if not name:
        out[plan.symbol] = {"error": "no parquet cache"}; continue
    d = pd.read_parquet(cache / f"{name}_M1_bidask.parquet", columns=["bid_close"])
    d = d[(d.index >= START) & (d.index < END)]
    tz = plan.calendar.timezone
    loc = d.index.tz_convert(tz)
    df = pd.DataFrame({"day": loc.date, "wd": loc.weekday, "m": loc.hour * 60 + loc.minute, "off": loc.map(lambda t: t.utcoffset().total_seconds() / 3600 if False else 0)})
    off = pd.Series(loc.tz_localize(None) - d.index.tz_localize(None).values if False else (loc.tz_localize(None) - d.index.tz_localize(None)).total_seconds() / 3600)
    df["off"] = off.values
    sym = {"timezone": str(tz), "rows": int(len(d)), "range": [str(d.index.min()), str(d.index.max())], "derived": {}}
    # derived session per local weekday, winter vs summer (utc offset of local tz as marker: smaller offset = winter)
    lo, hi = df.off.min(), df.off.max()
    for label, sel in (("standard_time", df.off == lo), ("daylight_time", df.off == hi)):
        sub = df[sel]
        ndays = sub.groupby("wd").day.nunique()
        for wd in range(7):
            n = int(ndays.get(wd, 0))
            if not n: sym["derived"].setdefault(str(wd), {})[label] = []; continue
            counts = np.zeros(1440); 
            c = sub[sub.wd == wd].groupby("m").day.nunique()
            counts[c.index.values] = c.values
            sym["derived"].setdefault(str(wd), {})[label] = runs(counts >= 0.5 * n)
        sym[f"days_{label}"] = int(sub.day.nunique()); sym[f"utc_offset_{label}"] = float(sub.off.iloc[0]) if len(sub) else None
    # compare against configured calendar day by day (UTC days)
    present = pd.Series(1, index=d.index)
    byday = d.groupby(d.index.date).size()
    extra_days, empty_days, extra_min, missing_min, exp_total = [], [], 0, 0, 0
    day = START.date()
    while day < END.date():
        exp = plan.calendar.expected(day)
        got = d.index[(d.index >= pd.Timestamp(day, tz="UTC")) & (d.index < pd.Timestamp(day + timedelta(days=1), tz="UTC"))] if False else None
        day += timedelta(days=1)
    # vectorised: expected set across range
    exp_all = pd.DatetimeIndex(np.concatenate([plan.calendar.expected(x).values for x in pd.date_range(START.date(), END.date() - timedelta(days=1)).date]), tz="UTC") if False else None
    expected = []
    for x in pd.date_range(START.date(), END.date() - timedelta(days=1)).date:
        e = plan.calendar.expected(x)
        if len(e): expected.append(e)
    exp_idx = expected[0].append(expected[1:]) if expected else pd.DatetimeIndex([], tz="UTC")
    got_idx = d.index
    outside = got_idx.difference(exp_idx); absent = exp_idx.difference(got_idx)
    sym["configured_vs_data"] = {"expected_minutes": int(len(exp_idx)), "observed_minutes": int(len(got_idx)),
        "observed_outside_calendar": int(len(outside)), "expected_but_absent": int(len(absent)),
        "coverage_pct": round(100 * (1 - len(absent) / max(1, len(exp_idx))), 3),
        "outside_pct_of_observed": round(100 * len(outside) / max(1, len(got_idx)), 4)}
    # absent days = whole UTC days where calendar expects bars but none (holiday candidates)
    ab = pd.Series(1, index=absent).groupby(absent.date).size()
    hol = []
    for x, n in ab.items():
        e = len(plan.calendar.expected(x))
        if e and n >= 0.9 * e: hol.append(str(x))
    sym["whole_days_expected_but_no_data_(holiday_candidates)"] = hol
    ot = pd.Series(1, index=outside).groupby(outside.date).size()
    sym["days_with_bars_outside_calendar"] = {"count": int(len(ot)), "worst": {str(k): int(v) for k, v in ot.sort_values(ascending=False).head(5).items()}}
    # DST check: outside/absent minutes in the two weeks around each DST switch vs rest
    out[plan.symbol] = sym
Path("evidence/v101/data/calendar_audit.json").write_text(json.dumps(out, indent=1))
for k, v in out.items():
    if "error" in v: print(k, v); continue
    c = v["configured_vs_data"]
    print(k, v["timezone"], "cov", c["coverage_pct"], "outside", c["observed_outside_calendar"], "(%s%%)" % c["outside_pct_of_observed"], "daysOutside", v["days_with_bars_outside_calendar"]["count"], "hol", len(v["whole_days_expected_but_no_data_(holiday_candidates)"]))
