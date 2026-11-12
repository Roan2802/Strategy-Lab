"""Reproducibility: the PBO-review code must give the same automatic passes as the stored (hard-PBO) cells for the same seeds."""
import json, sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent))
import pipeline_sim as ps
def main():
    g = ps.grid()
    res = {}
    for name in ("pboall|edge|3.0", "edge|2.0", "null|regime_switching"):
        new = ps.run_cell(name, g[name], 12, 16)
        f = "pipeline_results_pboall.json" if name.startswith("pboall") else "pipeline_results.json"
        old = json.load(open(f))[name]
        n_same = n = 0
        for a, b in zip(new["searches"], old["searches"]):
            ca, cb = a["cands"], b["cands"]
            n += 1
            same = len(ca) == len(cb) and all(x["pass"] == y["pass"] and x["pbo"] == y["pbo"] and abs((x["dsr"] or 0) - (y["dsr"] or 0)) < 1e-9 for x, y in zip(ca, cb))
            n_same += same
        print(name, "identical searches", n_same, "/", n, "errors", sum("error" in s for s in new["searches"]),
              "passes new", sum(c["pass"] for s in new["searches"] for c in s["cands"]),
              "old", sum(c["pass"] for s in old["searches"][:12] for c in s["cands"]),
              "review new", sum(c["l5_review"] for s in new["searches"] for c in s["cands"]))


if __name__ == "__main__":
    main()
