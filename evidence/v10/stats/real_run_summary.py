"""Scenario J: summary of the real V10 Dukascopy factory run (read-only on the factory store, via the API for the run view).

    C:/Python311/python.exe evidence/v10/stats/real_run_summary.py <run_id>
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


def main(rid: str):
    view = json.loads(urllib.request.urlopen(f"http://127.0.0.1:8767/api/factory/runs/{rid}").read())
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    stage_status = collections.Counter()
    reasons = collections.Counter()
    for r in db.execute("SELECT stage, status, reasons_json FROM stage_results WHERE run_id=?", (rid,)):
        stage_status[f"{r['stage']}:{r['status']}"] += 1
        if r["status"] != "PASS":
            for x in json.loads(r["reasons_json"] or "[]")[:6]:
                key = x.split(":")[0].split("(")[0].strip()[:70]
                reasons[f"{r['stage']}: {key}"] += 1
    cand_status = collections.Counter(r[0] for r in db.execute("SELECT status FROM candidates WHERE run_id=?", (rid,)))
    l5 = []
    for r in db.execute("SELECT s.cand_id, s.status, s.metrics_json, s.payload_json, c.symbol, c.timeframe, c.family "
                        "FROM stage_results s JOIN candidates c ON c.run_id=s.run_id AND c.cand_id=s.cand_id "
                        "WHERE s.run_id=? AND s.stage='L5'", (rid,)):
        m = json.loads(r["metrics_json"] or "{}")
        p = json.loads(r["payload_json"] or "{}")
        dsr = ((p.get("statistics") or {}).get("dsr") or {})
        l5.append({"cand_id": r["cand_id"], "market": f"{r['symbol']}:{r['timeframe']}", "family": r["family"],
                   "status": r["status"], "trades": m.get("trades"), "pf": m.get("profit_factor"),
                   "net": m.get("net_profit"), "wfe": m.get("wfe"), "wfe_pooled": m.get("wfe_pooled"),
                   "oos_sharpe_ann": dsr.get("observed_sr_annualized"), "dsr_global": m.get("dsr"),
                   "dsr_family": m.get("dsr_family"), "dsr_paper_v": dsr.get("value_paper_v"),
                   "raw_n": dsr.get("raw_n"), "effective_n": dsr.get("effective_n"), "sr0_ann": dsr.get("sr0_annualized"),
                   "T": dsr.get("T"), "T_eff": dsr.get("T_eff"), "evidence_verdict": m.get("evidence_verdict"),
                   "walk_forward_pass": m.get("walk_forward_pass"), "robustness_pass": m.get("robustness_pass"),
                   "evidence_class_validation": (p.get("evidence_class_validation") or {}).get("evidence_class"),
                   "purge_days": ((p.get("walk_forward") or {}).get("gap") or {}).get("purge_days"),
                   "single_window_flag": (((p.get("walk_forward") or {}).get("rolling") or {})
                                          .get("single_window_dependence") or {}).get("flag")})
    out = {"run_id": rid, "status": view["status"], "phase": view["phase"], "started_at": view.get("started_at"),
           "finished_at": view.get("finished_at"), "config_request": {k: (view.get("config") or {}).get(k) for k in (
               "markets", "timeframes", "profile", "quality", "max_candidates", "trials", "workers", "max_minutes")},
           "preregistration_sha256": ((view.get("config") or {}).get("preregistration") or {}).get("sha256"),
           "counts": view["counts"], "stage_status_counts": dict(stage_status), "candidate_status": dict(cand_status),
           "top_rejection_reasons": reasons.most_common(25), "l5_candidates": l5,
           "multiple_testing_sealed": view.get("multiple_testing"), "resources": view.get("resources")}
    path = Path(__file__).resolve().parent / "real_run_summary.json"
    path.write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: out[k] for k in ("status", "counts", "stage_status_counts", "candidate_status")}, indent=1))
    for k, v in out["top_rejection_reasons"][:15]:
        print(v, k)


if __name__ == "__main__":
    main(sys.argv[1])
