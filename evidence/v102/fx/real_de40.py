"""Real-data FX check (V102): DE40 EmaRsiPullback trades, development period only (< SEALED_START), converted EUR->USD.

Compares (a) the causal EURUSD H1 conversion series used by the backtest with (b) an independent FxService lookup on
the real EURUSD M1 candles at each trade's entry and exit time, and shows what the old 'EUR labelled USD' result hid.
Read-only on the Dukascopy data."""
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import numpy as np
from strategy_lab.data_manager import DataManager
from strategy_lab.research import backtest, fx, instruments, protocol

OUT = Path(__file__).parent
spec = json.loads((ROOT / "strategy_lab" / "data_cache" / "builder_specs" / "EmaRsiPullback.json").read_text(encoding="utf-8"))
spec = {k: v for k, v in spec.items() if k != "meta"}
spec["symbol"], spec["timeframe"] = "DE40", "H1"
START, END = "2014-03-01", protocol.SEALED_START
dm = DataManager()
t0 = time.time()
bid, ask, man = backtest.load_bid_ask(dm, "DE40", "H1", "2014-01-01", END)
assert bid.index.max() < protocol.utc(END), "sealed bars leaked"
conv = instruments.conversion_series(dm, "DE40", "H1", "2014-01-01", END)
contract = instruments.contract_for("DE40", dm, conversion_series=True)   # 1.0 placeholder, ignored when conversion= is passed
kw = dict(capital=10000, contract=contract, trade_from=START)
raw = backtest.run_rules_backtest(spec, bid, ask, None, **kw)
conv_run = backtest.run_rules_backtest(spec, bid, ask, None, conversion=conv, **kw)
svc = fx.FxService(dm, max_staleness=timedelta(hours=72))
rows, worst = [], 0.0
for t in conv_run["trades"]:
    entry = svc.rate("EUR", "USD", t["entry_time"])
    exit_ = svc.rate("EUR", "USD", t["exit_time"])
    series_exit = instruments.rate_at(conv, t["exit_time"])
    d = abs(exit_.rate - series_exit)
    worst = max(worst, d)
    rows.append({"entry_time": t["entry_time"], "exit_time": t["exit_time"], "side": t["side"], "qty": t["qty"],
                 "pnl_usd": t["pnl"], "pnl_eur_equiv": t["pnl"] / series_exit,
                 "eurusd_entry_m1": entry.rate, "eurusd_exit_m1": exit_.rate, "eurusd_exit_h1_series": series_exit,
                 "abs_diff_exit": d, "uncertainty_rel_exit": exit_.uncertainty, "source": exit_.source})
pick = lambda r: {k: r["metrics"].get(k) for k in ("trades", "net_profit", "profit_factor", "max_drawdown", "max_drawdown_pct")}
out = {"period": [START, END], "bars": len(bid), "data_version_eurusd": rows[0] and svc.rate("EUR", "USD", conv_run["trades"][0]["exit_time"]).data_version,
       "old_eur_as_usd_no_conversion": {**pick(raw), "fx_status": raw["diagnostics"]["fx_status"],
                                        "metrics_money_blocked": raw["metrics"].get("money_blocked"),
                                        "quote_currency_metrics_EUR_not_account_currency": raw["metrics"].get("quote_currency_metrics")},
       "converted": {**pick(conv_run), "fx_status": conv_run["diagnostics"]["fx_status"],
                     "currency_status": conv_run["diagnostics"]["currency_status"]},
       "trades_checked": len(rows), "max_abs_diff_exit_rate_m1_vs_h1_series": worst,
       "eurusd_exit_rate_min_max": [min(r["eurusd_exit_m1"] for r in rows), max(r["eurusd_exit_m1"] for r in rows)],
       "median_uncertainty_rel": float(np.median([r["uncertainty_rel_exit"] for r in rows])) if rows else None,
       "pnl_ratio_usd_over_eur_mean": float(np.mean([r["pnl_usd"] / (r["pnl_eur_equiv"]) for r in rows if r["pnl_eur_equiv"]])) if rows else None,
       "sample_trades": rows[:12], "runtime_s": round(time.time() - t0, 1)}
(OUT / "real_de40.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
print(json.dumps({k: v for k, v in out.items() if k != "sample_trades"}, indent=1, default=str))
