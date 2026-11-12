"""V10.2: the pipeline simulation's fast nested-WF I/O must equal the real nested_wf, and the simulation must run."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evidence" / "v102" / "stats"))

import pipeline_sim as ps  # noqa: E402
from strategy_lab.research import nested_wf  # noqa: E402


def _series(seed, K=3, T=2200, p=0.5):
    rng = np.random.default_rng(seed)
    r = ps.SIG * rng.standard_normal((K, T)) * (rng.random((K, T)) < p) + rng.uniform(-1, 2, (K, 1)) * 2e-4
    return np.where(r != 0, r, 0.0)


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("mode", ["rolling", "expanding"])
def test_fast_nested_equals_real(seed, mode):
    R = _series(seed)
    dates = pd.bdate_range("2008-01-07", periods=R.shape[1])
    trs = [ps.to_trades(R[j], dates) for j in range(len(R))]
    cfgs = [{"variant": "basis", "params": {"k": j}} for j in range(len(R))]
    fn = lambda cfg, s, e: {"trades": trs[cfg["params"]["k"]]}                       # noqa: E731
    d0, v0, v1 = dates[0], dates[1100], dates[-1]
    train = min(3 * 365, (v0 - d0).days) if mode == "rolling" else (v0 - d0).days
    start = (v0 - pd.Timedelta(days=train)).strftime("%Y-%m-%d") if mode == "rolling" else d0.strftime("%Y-%m-%d")
    kw = dict(train_days=train, test_days=182, mode=mode, gap={"purge_days": 0, "embargo_days": 0},
              objective="sharpe", min_trades=10, capital=ps.CAP)
    real = nested_wf.nested_walk_forward(fn, cfgs, start, v1.strftime("%Y-%m-%d"), **kw)
    with ps.fast_nested():
        fast = nested_wf.nested_walk_forward(fn, cfgs, start, v1.strftime("%Y-%m-%d"), **kw)
    assert len(real["windows"]) == len(fast["windows"]) > 3
    for a, b in zip(real["windows"], fast["windows"]):
        assert a["status"] == b["status"]
        assert (a.get("config") or {}).get("params") == (b.get("config") or {}).get("params")
    assert len(real["oos_trades"]) == len(fast["oos_trades"])
    assert sum(t["pnl"] for t in real["oos_trades"]) == pytest.approx(sum(t["pnl"] for t in fast["oos_trades"]))
    assert real["wfe"] == pytest.approx(fast["wfe"])
    assert nested_wf.slice_trades is not ps.fast_slice_trades          # patch restored


def test_search_runs_and_reports_every_gate_family():
    P = {**ps.DEFAULT, "edge": 2.0, "G": 40, "max_l5": 2, "boot_n": 60}
    out = ps.run_search(P, 7)
    assert out["skilled"] and out["cands"]
    c = out["cands"][-1]
    assert c["skilled"] and "dsr" in c and "pbo" in c and isinstance(c["flags"], dict)
    assert c["pass"] == (c["l5_pass"] and c["l6_pass"])
