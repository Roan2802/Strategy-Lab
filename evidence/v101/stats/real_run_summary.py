"""V10.1 real Dukascopy factory run summary: stage counts, outcomes (V10.1 classification), reasons, DSR variants,
dual-positive gate, ledger completeness and duplicate/holdout bookkeeping. Read-only (factory store + ledger, run
view via the API).

    C:/Python311/python.exe evidence/v101/stats/real_run_summary.py <run_id>
"""
from __future__ import annotations

import collections
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "strategy_lab" / "data_cache" / "factory.sqlite3"
LEDGER = ROOT / "strategy_lab" / "data_cache" / "research_ledger.sqlite3"


def _get(path):
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:8767{path}").read())


def main(rid: str):
    view = _get(f"/api/factory/runs/{rid}")
    rows = _get(f"/api/factory/runs/{rid}/candidates")
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    stage_status, reasons = collections.Counter(), collections.Counter()
    for r in db.execute("SELECT stage, status, reasons_json FROM stage_results WHERE run_id=?", (rid,)):
        stage_status[f"{r['stage']}:{r['status']}"] += 1
        if r["status"] != "PASS":
            for x in json.loads(r["reasons_json"] or "[]")[:6]:
                reasons[f"{r['stage']}: {x.split(':')[0].split('(')[0].strip()[:70]}"] += 1
    outcomes = collections.Counter(r["outcome"] for r in rows)
    kinds = collections.Counter((r["outcome"], r["rejection_kind"]) for r in rows)
    missing = collections.Counter(m for r in rows for m in r.get("missing_nl") or [])
    l5 = []
    for r in db.execute("SELECT s.cand_id, s.status, s.metrics_json, s.payload_json, c.symbol, c.timeframe, c.family "
                        "FROM stage_results s JOIN candidates c ON c.run_id=s.run_id AND c.cand_id=s.cand_id "
                        "WHERE s.run_id=? AND s.stage='L5'", (rid,)):
        m, p = json.loads(r["metrics_json"] or "{}"), json.loads(r["payload_json"] or "{}")
        d = (p.get("statistics") or {}).get("dsr") or {}
        v = d.get("variants") or {}
        sw = (((p.get("walk_forward") or {}).get("rolling") or {}).get("single_window_dependence") or {})
        l5.append({"cand_id": r["cand_id"], "market": f"{r['symbol']}:{r['timeframe']}", "family": r["family"],
                   "status": r["status"], "trades": m.get("trades"), "pf": m.get("profit_factor"),
                   "net": m.get("net_profit"), "dev_net_chosen": m.get("dev_net_chosen"),
                   "dual_positive": m.get("dual_positive"), "oos_sharpe_ann": d.get("observed_sr_annualized"),
                   "raw_n": d.get("raw_n"), "effective_n": d.get("effective_n"), "T": d.get("T"),
                   "T_eff": d.get("T_eff"), "skew": d.get("skew"), "kurtosis": d.get("kurtosis"),
                   "nc_dsr": (v.get("nc_dsr") or {}).get("value"), "paper_dsr": (v.get("paper_dsr") or {}).get("value"),
                   "dep_dsr": (v.get("dep_dsr") or {}).get("value"),
                   "sr0_ann_nc": (v.get("nc_dsr") or {}).get("sr0_annualized"),
                   "sr0_ann_paper": (v.get("paper_dsr") or {}).get("sr0_annualized"),
                   "reliable": (d.get("reliability") or {}).get("reliable"),
                   "single_window": {k: sw.get(k) for k in ("flag", "n_windows", "decidable")},
                   "evidence_verdict": m.get("evidence_verdict")})
    led = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
    n_ledger = led.execute("SELECT COUNT(*) FROM entries WHERE statistics_json LIKE ?", (f"%{rid}%",)).fetchone()[0]
    n_trials_store = db.execute("SELECT COUNT(*) FROM trials WHERE run_id=?", (rid,)).fetchone()[0]
    n_stage = db.execute("SELECT COUNT(*) FROM stage_results WHERE run_id=?", (rid,)).fetchone()[0]
    holdout = led.execute("SELECT COUNT(*) FROM entries WHERE kind='HOLDOUT' AND statistics_json LIKE ?",
                          (f"%{rid}%",)).fetchone()[0]
    out = {"run_id": rid, "status": view["status"], "phase": view["phase"], "started_at": view.get("started_at"),
           "finished_at": view.get("finished_at"), "config_request": {k: (view.get("config") or {}).get(k) for k in (
               "markets", "timeframes", "profile", "quality", "max_candidates", "trials", "workers", "max_minutes")},
           "final_test": ((view.get("config") or {}).get("profile_settings") or {}).get("final_test"),
           "preregistration_sha256": ((view.get("config") or {}).get("preregistration") or {}).get("sha256"),
           "counts": view["counts"], "stage_status_counts": dict(stage_status),
           "outcomes": dict(outcomes), "outcome_kinds": {f"{a}|{b}": n for (a, b), n in kinds.items()},
           "top_missing_nl": missing.most_common(10), "top_rejection_reasons": reasons.most_common(30),
           "l5_candidates": l5,
           "ledger": {"rows_for_run": n_ledger, "store_trials": n_trials_store, "stage_results": n_stage,
                      "holdout_rows_for_run": holdout},
           "resources": view.get("resources")}
    path = Path(__file__).resolve().parent / "real_run_summary.json"
    path.write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: out[k] for k in ("status", "counts", "stage_status_counts", "outcomes", "outcome_kinds",
                                          "ledger")}, indent=1, default=str))
    for k, v in out["top_rejection_reasons"][:20]:
        print(v, k)


if __name__ == "__main__":
    main(sys.argv[1])
