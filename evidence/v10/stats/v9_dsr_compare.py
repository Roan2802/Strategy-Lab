"""Old (V9) vs new (V10) DSR for the 24 candidates of the V9 real factory run that reached L5.

Inputs are read-only: the factory store (L5 payload daily returns + stored V9 DSR inputs) and the research ledger
restricted to rows that existed when the V9 run finished. Writes v9_dsr_compare.json next to this file.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from strategy_lab.research import effective_trials as et, validation  # noqa: E402
from strategy_lab.research.ledger import ResearchLedger  # noqa: E402

RUN = "fr_20261110_094132_eafc58"
LAB = ROOT / "strategy_lab" / "data_cache"


def main():
    db = sqlite3.connect(f"file:{LAB / 'factory.sqlite3'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    run = db.execute("SELECT * FROM runs WHERE run_id=?", (RUN,)).fetchone()
    finished = run["finished_at"]
    led = ResearchLedger(LAB / "research_ledger.sqlite3")
    rows = et.ledger_trials(led, until=finished)
    l5 = db.execute("SELECT s.cand_id, s.metrics_json, s.payload_json, c.family, c.symbol, c.timeframe FROM "
                    "stage_results s JOIN candidates c ON c.run_id=s.run_id AND c.cand_id=s.cand_id "
                    "WHERE s.run_id=? AND s.stage='L5'", (RUN,)).fetchall()
    series = {}
    out = []
    for r in l5:
        p = json.loads(r["payload_json"] or "{}")
        m = json.loads(r["metrics_json"] or "{}")
        d = pd.Series(p.get("daily_returns") or {}, dtype=float)
        if len(d) < 30:
            continue
        d.index = pd.to_datetime(d.index)
        series[r["cand_id"]] = d
        st = p.get("statistics") or {}
        mt = et.multiple_testing_records(d.to_numpy(), None, family=f"factory:{r['family']}", rows=rows)
        g = mt["global"]
        out.append({"cand_id": r["cand_id"], "market": f"{r['symbol']}:{r['timeframe']}", "family": r["family"],
                    "oos_sharpe_ann": g["observed_sr_annualized"], "T": g["T"], "T_eff": g["T_eff"],
                    "old_dsr": m.get("dsr"), "old_n_trials": st.get("n_trials"), "old_V_per_day": st.get("sr_variance"),
                    "old_sr0_ann": validation.expected_max_sr(int(st.get("n_trials") or 2),
                                                              float(st.get("sr_variance") or 0)) * math.sqrt(252),
                    "new_dsr_global": g["value"], "new_dsr_global_raw_n": g["value_raw_n"],
                    "new_dsr_family": mt["family"]["value"], "new_dsr_global_paper_v": g.get("value_paper_v"), "new_V_paper": g.get("V_paper"), "new_raw_n": g["raw_n"], "new_eff_n": g["effective_n"],
                    "new_V_per_day": g["V"], "new_V_source": g["V_detail"]["source"],
                    "new_sr0_ann": g["sr0_annualized"],
                    "sensitivity_V_search_trials": g["V_detail"].get("sensitivity_search_trials_V"),
                    "psr": validation.probabilistic_sharpe_ratio(d)["value"]})
    # between-candidate redundancy among the L5 candidates (only they have stored OOS series in V9)
    frame = pd.concat(series, axis=1).fillna(0.0)
    between = et.effective_n(frame)
    ratio = between["n_eff"] / between["n_raw"]
    for o in out:
        V = o["new_V_per_day"]
        d = series[o["cand_id"]]
        s = et.sharpe_stats(d.to_numpy())
        eo = et.effective_obs(d.to_numpy())
        n_adj = max(1.0, o["new_raw_n"] * ratio)
        o["new_dsr_global_between_adjusted"] = et.psr(s["sr"], eo["T_eff"], s["skew"], s["kurt"],
                                                      et.expected_max_sr(n_adj, V))
        o["n_between_adjusted"] = n_adj
        sens = o["sensitivity_V_search_trials"]
        o["dsr_if_V_from_all_search_trials"] = (et.psr(s["sr"], eo["T_eff"], s["skew"], s["kurt"],
                                                       et.expected_max_sr(o["new_raw_n"], sens)) if sens else None)
    res = {"run_id": RUN, "ledger_rows_until": finished, "n_ledger_rows": len(rows), "candidates": out,
           "between_candidates_effective_n": {k: between[k] for k in ("n_raw", "n_eff", "eigen", "onc_k",
                                                                      "mean_abs_corr")},
           "summary": {"max_old_dsr": max(o["old_dsr"] or 0 for o in out),
                       "max_new_dsr_global": max(o["new_dsr_global"] or 0 for o in out),
                       "n_new_global_ge_095": sum((o["new_dsr_global"] or 0) >= 0.95 for o in out),
                       "max_oos_sharpe_ann": max(o["oos_sharpe_ann"] for o in out),
                       "old_sr0_ann_median": float(np.median([o["old_sr0_ann"] for o in out])),
                       "new_sr0_ann_median": float(np.median([o["new_sr0_ann"] for o in out]))}}
    path = Path(__file__).resolve().parent / "v9_dsr_compare.json"
    path.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res["summary"], indent=1), json.dumps(res["between_candidates_effective_n"]))
    for o in sorted(out, key=lambda x: -x["oos_sharpe_ann"])[:8]:
        print(o["market"], o["family"], round(o["oos_sharpe_ann"], 2), "old", o["old_dsr"], "new",
              round(o["new_dsr_global"], 3), "fam", round(o["new_dsr_family"], 3))


if __name__ == "__main__":
    main()
