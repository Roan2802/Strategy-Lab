"""Create the real Shadow Research Portfolio and register eligible factory candidates (no orders, no broker).

Run from the project root: C:/Python311/python.exe evidence/v102/shadow/register_real.py
Writes evidence/v102/shadow/real_registration.json. Idempotent only in the sense that it refuses to reuse a name.
"""
import json
from pathlib import Path

from strategy_lab import factory_api, research_api, shadow_forward
from strategy_lab.data_manager import DataManager
from strategy_lab.research import factory

OUT = Path(__file__).with_name("real_registration.json")
DAILY_SYMBOLS = {"XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "USA30IDXUSD", "US30"}
WANT = [("TECHNICALLY_VALID", "EURUSD"), ("TECHNICALLY_VALID", "XAUUSD"), ("REJECTED", None)]

research_api.configure(DataManager())
store, vault = factory_api.store(), factory_api.vault()
engine = shadow_forward.shadow_engine()
existing = [p for p in engine.shadow_portfolios() if p["name"] == "Schaduw research 2026-11"]
if existing:
    raise SystemExit("portfolio already exists: " + existing[0]["id"])

pool = []
for run in store.runs():
    for row in store.candidates(run["run_id"]):
        if row["symbol"] not in DAILY_SYMBOLS or row["timeframe"] not in ("M15", "M30", "H1", "H4"):
            continue
        status = row["status"]
        if status == "TECHNICALLY_VALID" or (status == "REJECTED" and row["stage"] in ("L3", "L4", "L5")):
            detail = factory.candidate_detail(store, row["cand_id"], run["run_id"], vault)
            verdict = shadow_forward.eligibility(detail)
            if verdict["eligible"]:
                pool.append((status, row["symbol"], row["cand_id"], run["run_id"], row["name"], verdict))

chosen, seen = [], set()
for status, symbol in WANT:
    for item in pool:
        if item[0] == status and (symbol is None or item[1] == symbol) and item[2] not in seen \
                and (symbol is not None or item[1] not in {c[1] for c in chosen}):
            chosen.append(item)
            seen.add(item[2])
            break

portfolio = engine.create_shadow("Schaduw research 2026-11", currency="USD", starting_capital=30000, risk_limit=0.05)
registered, failed = [], []
for status, symbol, cand_id, run_id, name, verdict in chosen:
    try:
        registered.append(engine.register_strategy(
            portfolio, source={"kind": "factory", "candidate_id": cand_id, "run_id": run_id},
            reason_nl=(f"Experimenteel: kandidaat {name} ({verdict['evidence_status']}) wordt prospectief gevolgd om "
                       f"te zien of de bevroren regels buiten de onderzoeksperiode standhouden. Geen validatie.")))
    except Exception as exc:  # report, do not hide
        failed.append({"cand_id": cand_id, "error": f"{type(exc).__name__}: {exc}"})

advance = engine.advance(portfolio)
view = engine.portfolio_view(portfolio)
OUT.write_text(json.dumps({
    "portfolio_id": portfolio, "registered": registered, "failed": failed, "eligible_pool": len(pool),
    "advance": advance, "prospective_days": view["prospective_days"],
    "strategies": [{k: s[k] for k in ("id", "name", "symbol", "timeframe", "start_utc", "trades", "evidence_status",
                                      "source_evidence_status", "prospective_days")} for s in view["strategies"]],
    "ledger": view["ledger"], "limits": view["limits"], "warnings": view["warnings"],
}, indent=2, default=str), encoding="utf-8")
print(OUT.read_text(encoding="utf-8"))
