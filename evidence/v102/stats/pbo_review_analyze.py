"""Tables for the PBO-review campaign (pipeline_sim.py --campaign review) -> pbo_review_tables.md / pbo_review_tables.json.

Per scenario and method ("topk" = PBO over the 4 top-K configurations, "all" = PBO over all 24 L4 configurations):
  auto      automatic promotion = passes L5 without REVIEW_REQUIRED and passes the L6 DSR gate
            (identical to the previous rule "PBO > 0.5 is a hard fail": verified by pbo_review_repro.py)
  review    the unit lands in REVIEW_REQUIRED at L5 (PBO is its only open point)
  upper     promotion if every REVIEW_REQUIRED were accepted (L5 clean and L6 pass) = worst case for the owner
Unit = the search (null scenarios: any L5 candidate of the search) or the skilled candidate (edge scenarios).
All rates with Wilson 95% intervals."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pipeline_sim as ps  # noqa: E402
from strategy_lab.research import factory  # noqa: E402
from strategy_lab.research import effective_trials as et  # noqa: E402


def fmt(w):
    return "-" if w["rate"] is None else f"{w['rate']:.3f} [{w['lo']:.3f}; {w['hi']:.3f}]"


def cell_stats(cell):
    S = [s for s in cell["searches"] if "error" not in s]
    skilled = cell["params"]["edge"] > 0 and cell["params"].get("skilled", True)
    units = []
    for s in S:
        cs = [c for c in s["cands"] if "l5_pass" in c]
        if skilled:
            cs = [c for c in cs if c["skilled"]]
        units.append(cs)
    n = len(units)
    k = lambda f: sum(any(f(c) for c in cs) for cs in units)       # noqa: E731
    return {"n": n, "errors": len(cell["searches"]) - len(S),
            "auto": ps.wilson(k(lambda c: c["pass"]), n), "review": ps.wilson(k(lambda c: c["l5_review"]), n),
            "upper": ps.wilson(k(lambda c: c["pass_if_accepted"]), n),
            "review_then_l6": ps.wilson(k(lambda c: c["review_and_l6"]), n),
            "cands": sum(len(cs) for cs in units), "l5_clean": sum(c["l5_clean"] for cs in units for c in cs),
            "pbo_pre_ok": all(c.get("pbo_pre_ok") for cs in units for c in cs),
            "pbo_cols": next((c["pbo_cols"] for cs in units for c in cs), None)}


def adaptive_stats(cell):
    P = cell["params"]
    q = factory.QUALITY_PROFILES[P["profile"]]
    R = cell["rounds"]
    reps = cell["replications"]
    out = {"cands": sum(len(x["cands"]) for rep in reps for x in rep),
           "l5_clean": sum(c["l5_clean"] for rep in reps for x in rep for c in x["cands"]),
           "l5_review": sum(c["l5_review"] for rep in reps for x in rep for c in x["cands"])}
    for r in range(1, R + 1):
        acc = {k: 0 for k in ("own_auto", "own_upper", "own_review", "led_auto", "led_upper")}
        n = 0
        for rep in reps:
            rounds = rep[:r]
            if any("error" in x for x in rounds):
                continue
            n += 1
            cands = [c for x in rounds for c in x["cands"]]
            n_raw = sum(x["raw_n"] for x in rounds)
            n_eff = max(1.0, sum(x["eff_n"] for x in rounds))
            acc["own_auto"] += any(c["pass"] for c in cands)
            acc["own_upper"] += any(c["pass_if_accepted"] for c in cands)
            acc["own_review"] += any(c["l5_review"] for c in cands)
            led_a = led_u = False
            for c in cands:
                if not c["l5_clean"]:
                    continue
                dsr = et.dsr_record(__import__("numpy").asarray(c["daily"]), n_raw=max(1, int(n_raw)), n_eff=n_eff,
                                    variance={"V": None, "status": "NULL_FLOOR_ONLY"}, scope="global")
                ok = et.gate({"global": dsr, "family": dsr}, q["min_dsr"])["status"] == "PASS"
                led_u |= ok
                led_a |= ok and not c["l5_review"]
            acc["led_auto"] += led_a
            acc["led_upper"] += led_u
        out[r] = {k: ps.wilson(v, n) for k, v in acc.items()}
    return out


def main():
    res = json.loads((HERE / "pbo_review_results.json").read_text())
    scen = ps.review_scenarios()
    tab, js = [], {}
    tab.append("| scenario | reps | methode | PBO-kolommen | auto-promotie [Wilson 95%] | REVIEW_REQUIRED-aandeel [95%] | "
               "bovengrens: alles geaccepteerd [95%] | L5-kandidaten / L5 schoon |")
    tab.append("|---|---|---|---|---|---|---|---|")
    for scn in scen:
        for meth in ("topk", "all"):
            cell = res.get(f"rv|{meth}|{scn}")
            if not cell:
                continue
            st = cell_stats(cell)
            js[f"{meth}|{scn}"] = st
            tab.append(f"| {scn} | {st['n']} | {'top-4' if meth == 'topk' else 'alle 24'} | {st['pbo_cols']} | "
                       f"{fmt(st['auto'])} | {fmt(st['review'])} | {fmt(st['upper'])} | {st['cands']} / {st['l5_clean']} |")
    ad = ["", "| methode | rondes | N-teller | auto-promotie [95%] | REVIEW_REQUIRED [95%] | bovengrens [95%] |", "|---|---|---|---|---|---|"]
    for meth in ("topk", "all"):
        cell = res.get(f"rv|{meth}|adaptive")
        if not cell:
            continue
        a = adaptive_stats(cell)
        js[f"{meth}|adaptive"] = a
        ad.append(f"| {'top-4' if meth == 'topk' else 'alle 24'} | kandidaten {a['cands']} | L5 schoon {a['l5_clean']} | "
                  f"waarvan REVIEW_REQUIRED {a['l5_review']} | | |")
        for r in (1, 3, 5):
            ad.append(f"| {'top-4' if meth == 'topk' else 'alle 24'} | {r} | alleen eigen ronde | {fmt(a[r]['own_auto'])} | "
                      f"{fmt(a[r]['own_review'])} | {fmt(a[r]['own_upper'])} |")
            ad.append(f"| {'top-4' if meth == 'topk' else 'alle 24'} | {r} | cumulatief (ledger telt alle rondes) | "
                      f"{fmt(a[r]['led_auto'])} | {fmt(a[r]['own_review'])} | {fmt(a[r]['led_upper'])} |")
    (HERE / "pbo_review_tables.json").write_text(json.dumps(js, indent=1, default=float))
    (HERE / "pbo_review_tables.md").write_text("\n".join(tab + ad) + "\n", encoding="utf-8")
    print("\n".join(tab + ad))


if __name__ == "__main__":
    main()
