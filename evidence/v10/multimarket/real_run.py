"""Real cross-market check: XAUUSD breakout_prev_day factory candidate e0a55476747937e4 (frozen params), dev data only."""
import json, sys, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from strategy_lab.data_manager import DataManager
from strategy_lab.research import multimarket, backtest, instruments, protocol
from strategy_lab.research.ledger import ResearchLedger

OUT = Path(__file__).parent
cand = json.load(urllib.request.urlopen("http://localhost:8767/api/factory/candidates/e0a55476747937e4"))["candidate"]
dm = DataManager()
led = ResearchLedger(ROOT / "strategy_lab" / "data_cache" / "research_ledger.sqlite3")


class Ctx:
    def check(self): pass
    def progress(self, f, m=""): print(f"{f:.2f} {m}", flush=True)


# ---- DE40 accounting before/after (same signals, only EUR->USD conversion differs)
spec = {k: v for k, v in cand["spec"].items() if k != "meta"}
spec["symbol"], spec["timeframe"] = "DE40", "H1"
start, end = "2014-03-01", protocol.SEALED_START
bid, ask, _ = backtest.load_bid_ask(dm, "DE40", "H1", "2014-01-01", end)
contract = instruments.contract_for("DE40", dm, conversion_series=True)
conv = instruments.conversion_series(dm, "DE40", "H1", "2014-01-01", end)
kw = dict(capital=10000, contract=contract, commission_per_lot=0.0, trade_from=start)
before = backtest.run_rules_backtest(spec, bid, ask, cand["params"], **kw)
after = backtest.run_rules_backtest(spec, bid, ask, cand["params"], conversion=conv, **kw)
pick = lambda r: {**{k: r["metrics"][k] for k in ("trades", "net_profit", "profit_factor", "max_drawdown", "max_drawdown_pct", "recovery_factor", "sharpe_daily")},
                  "currency_status": r["diagnostics"]["currency_status"], "final_equity": r["equity"][-1]["equity"]}
de40 = {"before_eur_as_usd": pick(before), "after_converted_with_eurusd": pick(after),
        "eurusd_open_min_max_mean": [float(conv.min()), float(conv.max()), float(conv.mean())],
        "note": "same signals/params; before amounts are EUR labelled USD, after converted causally at each bar open"}
print(json.dumps(de40, indent=1, default=str), flush=True)

t0 = time.time()
out = multimarket.run_cross_market(Ctx(), dm, {"kind": "factory", "label": cand["name"], "spec": cand["spec"], "params": cand["params"]},
                                   "all", ["M15", "H1"], ledger=led)
out["de40_accounting"] = de40
out["runtime_s"] = round(time.time() - t0, 1)
(OUT / "real_run.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
g = out["generalisation"]
print(g["class"], g["explanation_nl"], g["n_markets_positive"], g["n_markets_tested"], g["effective_markets"])
for c in out["results"]:
    m = c.get("metrics") or {}
    print(c["key"], c.get("status"), c.get("validation_status"), m.get("trades"), round(m.get("net_return_pct") or 0, 1), m.get("profit_factor"), c["flags"])
