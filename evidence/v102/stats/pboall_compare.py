"""Compare PBO over top-K (K=4, pipeline_results.json) with PBO over all C=24 L4 configs (pipeline_results_pboall.json)."""
import json, math, sys
from pathlib import Path
H = Path(__file__).parent

def wilson(k, n, z=1.96):
    if not n: return (None, None)
    p = k / n; d = 1 + z*z/n; c = (p + z*z/(2*n)) / d; h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / d
    return (round(max(0, c-h), 3), round(min(1, c+h), 3))

def stats(cell):
    S = [s for s in cell["searches"] if "error" not in s]
    skilled = cell["params"]["edge"] > 0 and cell["params"].get("skilled", True)
    if skilled:
        ev = [next((c for c in s["cands"] if c["skilled"]), None) for s in S]
        hits = sum(bool(c and c["pass"]) for c in ev); n = len(ev)
    else:
        hits = sum(any(c["pass"] for c in s["cands"]) for s in S); n = len(S)
    cs = [c for s in S for c in s["cands"] if (c["skilled"] if skilled else True)]
    pb = [c["pbo"] for c in cs if c.get("pbo") is not None]
    return {"n": n, "hits": hits, "rate": round(hits/n, 3) if n else None, "ci": wilson(hits, n),
            "pbo_flag": round(sum(c["pbo_flag"] for c in cs)/len(cs), 3) if cs else None,
            "mean_pbo": round(sum(pb)/len(pb), 3) if pb else None, "pbo_cols": cs[0]["pbo_cols"] if cs else None}

base = json.loads((H/"pipeline_results.json").read_text())
new = json.loads((H/"pipeline_results_pboall.json").read_text())
out = {}
print(f"{'cel':40} {'top-K: rate [CI] flag mPBO':34} {'alle 24: rate [CI] flag mPBO'}")
for k, v in new.items():
    name = k.split("|", 1)[1]
    a, b = (stats(base[name]) if name in base else None), stats(v)
    out[name] = {"top_k": a, "all_l4": b}
    fa = f"{a['rate']} {a['ci']} {a['pbo_flag']} {a['mean_pbo']}" if a else "-"
    print(f"{name:40} {fa:34} {b['rate']} {b['ci']} {b['pbo_flag']} {b['mean_pbo']} (K={b['pbo_cols']})")
(H/"pboall_comparison.json").write_text(json.dumps(out, indent=1))
