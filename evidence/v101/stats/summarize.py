"""Print calibration tables (markdown) from calibration_results*.json."""
import json
import sys
from pathlib import Path

p = Path(__file__).resolve().parent / (sys.argv[1] if len(sys.argv) > 1 else "calibration_results.json")
d = json.loads(p.read_text())


def f(w):
    return f"{w['rate']:.3f} [{w['lo']:.3f}, {w['hi']:.3f}]"


if "S1" in d:
    print("| cell | reps | best SR ann | N_eff | T_eff | paper | NC | dep | NC raw N |\n|---|---|---|---|---|---|---|---|---|")
    for k, c in d["S1"]["result"].items():
        ps = c["pass"]
        print(f"| {k} | {c['reps']} | {c['median_best_sr_ann']:.2f} | {c['median_n_eff']:.0f} | {c['median_T_eff']:.0f} | "
              f"{f(ps['paper_dsr'])} | {f(ps['nc_dsr'])} | {f(ps['dep_dsr'])} | {f(ps['nc_dsr_raw_n'])} |")
if "S2" in d:
    print("\n| cell | reps | NC search N | NC OOS N | paper OOS N | paper search N | dep OOS N |\n|---|---|---|---|---|---|---|")
    for k, c in d["S2"]["result"].items():
        ps = c["pass"]
        print(f"| {k} | {c['reps']} | " + " | ".join(f(ps[x]) for x in ("nc_search_N", "nc_oos_N", "paper_oos_N",
                                                                         "paper_search_N", "dep_oos_N")) + " |")
if "S3" in d:
    print("\n| cell | reps | NC last-round N | NC validation-ledger N | NC search N |\n|---|---|---|---|---|")
    for k, c in d["S3"]["result"].items():
        ps = c["pass"]
        print(f"| {k} | {c['reps']} | " + " | ".join(f(ps[x]) for x in ("nc_last_round_N", "nc_validation_N",
                                                                         "nc_search_N")) + " |")
