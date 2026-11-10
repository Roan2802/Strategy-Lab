"""Real check (dev data only): LabBreakout (Jesse, Docker runner) on XAUUSD/EURUSD/DE40 M15, and gold_reaper (plugin)."""
import json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from strategy_lab import server, research_api
from strategy_lab.research import multimarket
from strategy_lab.research.ledger import ResearchLedger

OUT = Path(__file__).parent
dm = server.DATA
research_api.configure(dm)
led = ResearchLedger(ROOT / "strategy_lab" / "data_cache" / "research_ledger.sqlite3")


class Ctx:
    def check(self): pass
    def progress(self, f, m=""): print(f"{f:.2f} {m}", flush=True)


which = sys.argv[1]
t0 = time.time()
if which == "jesse":
    strat = {"kind": "jesse", "label": "LabBreakout", "name": "LabBreakout", "params": None, "origin_symbol": "XAUUSD"}
    out = multimarket.run_cross_market(Ctx(), dm, strat, ["XAUUSD", "EURUSD", "DE40"], ["M15"], "2023-06-01", "2023-09-01",
                                       ledger=led, min_days=60)
elif which == "jesse_de40":
    # sensitivity: same frozen config, only the start balance raised so one frozen unit of DE40 (price ~15.8k) fits
    strat = {"kind": "jesse", "label": "LabBreakout", "name": "LabBreakout", "params": None, "origin_symbol": "XAUUSD"}
    out = multimarket.run_cross_market(Ctx(), dm, strat, ["DE40"], ["M15"], "2023-06-01", "2023-09-01",
                                       ledger=led, min_days=60, capital=200000.0)
else:
    strat = {"kind": "plugin", "label": "gold_reaper", "name": "gold_reaper", "params": None}
    out = multimarket.run_cross_market(Ctx(), dm, strat, ["XAUUSD", "EURUSD", "DE40"], ["M15"], "2023-06-01", "2023-08-01",
                                       ledger=led, min_days=30)
out["runtime_s"] = round(time.time() - t0, 1)
(OUT / f"real_run_{which}.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
g = out["generalisation"]
print(g["class"], g["explanation_nl"], flush=True)
for c in out["results"]:
    m = c.get("metrics") or {}
    print(c["key"], c.get("status"), c.get("validation_status"), m.get("trades"), m.get("net_profit"), c["flags"], c.get("error") or c.get("reason"), flush=True)
print("money", out["money_approval"]["status"], "runtime", out["runtime_s"])
