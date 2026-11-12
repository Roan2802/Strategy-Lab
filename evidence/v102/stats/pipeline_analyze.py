"""Analysis of pipeline_results.json: FPR / power with Wilson CIs, gate matrix, conditional rejection overlap,
leave-one-out (marginal FPR / power effect of every gate). Writes pipeline_summary.json and pipeline_tables.md."""
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pipeline_sim import wilson  # noqa: E402

# flag -> gate group (the matrix of STATISTICAL_SELECTION_AUDIT.md)
GATE = {
    "q:oos_trades": "SAMPLE_SIZE", "s:sample_size": "SAMPLE_SIZE", "e:eff_obs": "SAMPLE_SIZE",
    "q:frequency": "FREQUENCY", "q:profit_factor_oos": "PF_FLOOR", "s:profit_factor": "PF_FLOOR",
    "q:recovery_factor_oos": "RF_FLOOR", "q:max_dd_oos": "DRAWDOWN", "s:max_drawdown": "DRAWDOWN",
    "q:expectancy": "EXPECTANCY_CI", "e:ci_daily_mean": "DAILY_MEAN_CI", "e:random_entry": "RANDOM_ENTRY_NULL",
    "q:concentration": "CONCENTRATION", "s:drop_best_trades": "CONCENTRATION", "e:concentration": "CONCENTRATION",
    "q:inactivity": "INACTIVITY", "q:single_window_dependence": "SINGLE_WINDOW",
    "q:spread_stress": "COST_STRESS", "s:cost_stress": "COST_STRESS", "e:cost_mc": "COST_STRESS",
    "e:execution": "EXECUTION_STRESS",
    "q:param_stability": "PARAM_STABILITY", "s:parameter_stability": "PARAM_STABILITY", "e:neighbourhood": "PARAM_STABILITY",
    "q:dual_positive": "DUAL_POSITIVE", "s:net_after_costs": "DUAL_POSITIVE", "e:oos_net": "DUAL_POSITIVE",
    "s:pbo": "PBO", "s:walk_forward_efficiency": "WFE", "s:oos_profitability": "OOS_WINDOWS",
    "s:yearly_consistency": "YEARLY", "e:regime": "REGIME", "e:simpler": "SIMPLER", "e:baseline": "BASELINE",
    "l6:dsr": "NC_DSR",
}
RISK = {"NC_DSR": "selection bias + sample noise", "PBO": "selection bias / overfit", "WFE": "overfit",
        "EXPANDING_WF": "overfit + regime", "DUAL_POSITIVE": "overfit (development leg) + sample noise",
        "EXPECTANCY_CI": "sample noise", "DAILY_MEAN_CI": "sample noise", "RANDOM_ENTRY_NULL": "sample noise (gross edge)",
        "PF_FLOOR": "performance floor (+ sample noise)", "RF_FLOOR": "performance floor", "DRAWDOWN": "performance floor",
        "COST_STRESS": "cost fragility", "EXECUTION_STRESS": "cost fragility", "PARAM_STABILITY": "parameter fragility",
        "CONCENTRATION": "sample noise (few days)", "SINGLE_WINDOW": "regime dependence / sample noise",
        "YEARLY": "regime dependence", "OOS_WINDOWS": "regime dependence", "REGIME": "regime dependence",
        "INACTIVITY": "regime dependence", "SAMPLE_SIZE": "sample size", "FREQUENCY": "trade frequency",
        "SIMPLER": "overfit (complexity)", "BASELINE": "naive baseline"}


def fail_gates(c):
    g = set()
    for f in c["flags"]:
        if f == "q:overfit_flags":
            if c["pbo_flag"]:
                g.add("PBO")
            if c["wfe_flag"]:
                g.add("WFE")
            if c["exp_flag"]:
                g.add("EXPANDING_WF")
        else:
            g.add(GATE.get(f, "OTHER:" + f))
    if not c["l6_pass"]:
        g.add("NC_DSR")
    return g


def collect(res, pred):
    out = []
    for name, cell in res.items():
        if pred(name, cell["params"]):
            for si, s in enumerate(cell["searches"]):
                for c in s["cands"]:
                    if "flags" in c:
                        c = dict(c)
                        c["_cell"], c["_search"] = name, si
                        out.append(c)
    return out


def rate(k, n):
    w = wilson(k, n)
    return w


def fmt(w):
    return "-" if w["rate"] is None else f"{w['rate']:.3f} [{w['lo']:.3f}, {w['hi']:.3f}]"


def main():
    res = json.loads((HERE / "pipeline_results.json").read_text())
    out = {"cells": {}, "gates": {}, "overlap": {}, "loo": {}, "pbo": {}}
    md = []
    # ---- per cell
    allc = collect(res, lambda n, p: True)
    bad = sum((not fail_gates(c)) != bool(c["pass"]) for c in allc)
    out["flag_level_consistency"] = {"candidates": len(allc), "mismatches": bad}
    md.append(f"Consistency: flag-level pass (no gate failing) equals the factory decision for {len(allc) - bad} of {len(allc)} candidates.\n")
    md.append("## Pipeline-level FPR / power per cell (search level, Wilson 95%)\n")
    md.append("| cell | searches | L5 cands/search | any pass (null lineage) | skilled reaches L4 | skilled L5 pass | skilled pipeline pass (power) | power given L4 |\n|---|---|---|---|---|---|---|---|")
    for name, cell in res.items():
        S = [s for s in cell["searches"] if "cands" in s and not s.get("error")]
        n = len(S)
        if not n:
            continue
        sk = cell["params"]["edge"] > 0
        fp = sum(any(c.get("pass") and not c.get("skilled") for c in s["cands"]) for s in S)
        reach = sum(s["skilled_reach_l4"] for s in S) if sk else 0
        l5 = sum(any(c.get("l5_pass") for c in s["cands"] if c.get("skilled")) for s in S) if sk else 0
        pw = sum(any(c.get("pass") for c in s["cands"] if c.get("skilled")) for s in S) if sk else 0
        pw_given = sum(any(c.get("pass") for c in s["cands"] if c.get("skilled")) for s in S if s["skilled_reach_l4"]) if sk else 0
        ncand = sum(len(s["cands"]) for s in S) / n
        out["cells"][name] = {"searches": n, "cands_per_search": ncand, "fp_any": rate(fp, n),
                              "skilled_reach_l4": rate(reach, n) if sk else None, "skilled_l5_pass": rate(l5, n) if sk else None,
                              "power": rate(pw, n) if sk else None, "power_given_l4": rate(pw_given, reach) if sk else None,
                              "errors": len(cell["searches"]) - n}
        md.append(f"| {name} | {n} | {ncand:.1f} | {fmt(rate(fp, n))} | " +
                  (f"{fmt(rate(reach, n))} | {fmt(rate(l5, n))} | {fmt(rate(pw, n))} | {fmt(rate(pw_given, reach))} |" if sk else "- | - | - | - |"))
    # ---- gate rejection rates: null candidates (pooled null cells) and skilled by edge (default-profile cells)
    null = collect(res, lambda n, p: p["edge"] == 0 and p["K"] == 4)
    skl = {}
    for e in (0.5, 1.0, 2.0, 3.0):
        skl[e] = collect(res, lambda n, p, e=e: p["edge"] == e and n == f"edge|{e}")
    skl = {e: [c for c in v if c.get("skilled")] for e, v in skl.items()}
    md.append(f"\n## Gate rejection rate (candidate level): null candidates pooled over {len(null)} candidates vs skilled lineage\n")
    pops = {"null": null, **{f"skilled {e}": v for e, v in skl.items()}}
    gates = sorted({g for c in null + sum(skl.values(), []) for g in fail_gates(c)})
    md.append("| gate | risk | " + " | ".join(f"{k} (n={len(v)})" for k, v in pops.items()) + " |\n|---|---|" + "---|" * len(pops))
    for g in gates:
        row = []
        for k, v in pops.items():
            row.append(fmt(rate(sum(g in fail_gates(c) for c in v), len(v))))
            out["gates"].setdefault(g, {})[k] = rate(sum(g in fail_gates(c) for c in v), len(v))
        md.append(f"| {g} | {RISK.get(g, '')} | " + " | ".join(row) + " |")
    # ---- conditional rejection overlap among null + weak candidates (those that look positive: oos_net > 0)
    weak = [c for c in null if c["oos_net"] > 0]
    md.append(f"\n## Conditional rejection overlap P(column rejects | row rejects), null candidates with positive OOS net (n={len(weak)})\n")
    major = [g for g in ("NC_DSR", "PBO", "WFE", "EXPANDING_WF", "EXPECTANCY_CI", "DAILY_MEAN_CI", "RANDOM_ENTRY_NULL",
                         "PF_FLOOR", "RF_FLOOR", "COST_STRESS", "PARAM_STABILITY", "CONCENTRATION", "SINGLE_WINDOW", "REGIME",
                         "YEARLY", "DUAL_POSITIVE") if g in gates]
    fg = [fail_gates(c) for c in weak]
    md.append("| rejects (row) -> also rejects (col) | n | " + " | ".join(major) + " |\n|---|---|" + "---|" * len(major))
    for a in major:
        na = sum(a in f for f in fg)
        cells = []
        for b in major:
            k = sum(a in f and b in f for f in fg)
            cells.append("-" if not na else f"{k / na:.2f}")
            out["overlap"].setdefault(a, {})[b] = (k / na) if na else None
        md.append(f"| {a} | {na} | " + " | ".join(cells) + " |")
    # ---- leave-one-out
    md.append("\n## Leave-one-out: pass rate when ONE gate is ignored (all other gates keep deciding)\n")
    md.append("Null: candidate-level pass rate over all null candidates, and search-level false-positive rate over null cells (default K 4 cells). Skilled: pass rate at net Sharpe 1.0 / 2.0 / 3.0 (searches in which the skilled lineage was evaluated).\n")
    md.append("| ignored gate | null cand. pass | null search FPR | power 1.0 | power 2.0 | power 3.0 |\n|---|---|---|---|---|---|")
    nullcells = {n: c for n, c in res.items() if c["params"]["edge"] == 0 and c["params"]["K"] == 4}
    skill_all = {e: [c for c in collect(res, lambda n, p, e=e: p["edge"] == e and n == f"edge|{e}") if c.get("skilled")] for e in (1.0, 2.0, 3.0)}

    def loo_row(ign):
        pc = sum(fail_gates(c) <= ign for c in null)
        sfp, sn = 0, 0
        for cell in nullcells.values():
            for s in cell["searches"]:
                if "cands" not in s or s.get("error"):
                    continue
                sn += 1
                sfp += any(fail_gates(c) <= ign for c in s["cands"])
        pw = {e: rate(sum(fail_gates(c) <= ign for c in v), len(v)) for e, v in skill_all.items()}
        return rate(pc, len(null)), rate(sfp, sn), pw

    for g in [None] + gates:
        ign = set() if g is None else {g}
        a, b, pw = loo_row(ign)
        out["loo"][g or "FULL PIPELINE"] = {"null_candidate_pass": a, "null_search_fpr": b, "power": {str(e): w for e, w in pw.items()}}
        md.append(f"| {g or 'none (full pipeline)'} | {fmt(a)} | {fmt(b)} | " + " | ".join(fmt(pw[e]) for e in (1.0, 2.0, 3.0)) + " |")
    # also: ignoring BOTH PBO and its duplicates
    # ---- PBO informativeness (R3)
    md.append("\n## PBO informativeness (R3): P(PBO > max_pbo) and mean PBO\n")
    md.append("| population | n | PBO flag rate | mean PBO |\n|---|---|---|---|")
    for k, v in [("null (K 4)", null)] + [(f"skilled {e} (K 4)", c) for e, c in skl.items()] + \
            [("null (K 12)", collect(res, lambda n, p: p["edge"] == 0 and p["K"] == 12))] + \
            [(f"skilled {e} (K 12)", [c for c in collect(res, lambda n, p, e=e: p["edge"] == e and p["K"] == 12) if c.get("skilled")]) for e in (1.0, 2.0)]:
        pb = [c["pbo"] for c in v if c.get("pbo") is not None]
        fl = sum(c["pbo_flag"] for c in v)
        out["pbo"][k] = {"n": len(v), "flag": rate(fl, len(v)), "mean_pbo": sum(pb) / len(pb) if pb else None}
        md.append(f"| {k} | {len(v)} | {fmt(rate(fl, len(v)))} | {sum(pb) / len(pb):.3f} |" if pb else f"| {k} | {len(v)} | - | - |")
    # ---- sole blockers of the skilled lineage
    md.append("\n## Skilled lineage that reached L4: share blocked by each gate / sole blocker\n")
    md.append("| edge | n | pipeline pass | " + " | ".join(["gate:fails (sole)"]) + " |\n|---|---|---|---|")
    for e in (0.5, 1.0, 2.0, 3.0):
        v = [c for c in skl[e]]
        cnt, sole = Counter(), Counter()
        for c in v:
            f = fail_gates(c)
            for g in f:
                cnt[g] += 1
            if len(f) == 1:
                sole[next(iter(f))] += 1
        top = ", ".join(f"{g}:{cnt[g] / len(v):.2f} ({sole[g] / len(v):.2f})" for g, _ in cnt.most_common(7))
        md.append(f"| {e} | {len(v)} | {sum(c['pass'] for c in v) / len(v):.3f} | {top} |")
    (HERE / "pipeline_summary.json").write_text(json.dumps(out, indent=1, default=float))
    (HERE / "pipeline_tables.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
