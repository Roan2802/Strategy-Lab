"""Supervisor/worker processes: crash-resume without duplicate trials, pause/resume and a real stop."""
import json
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import psutil
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from strategy_lab.research import factory, factory_worker  # noqa: E402
from strategy_lab.tests.test_factory import new_run  # noqa: E402


def spawn_supervisor(db, idle_exit=4):
    return subprocess.Popen([sys.executable, "-m", "strategy_lab.research.factory_worker", "--db", str(db),
                             "--idle-exit", str(idle_exit)], cwd=str(ROOT), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def kill_tree(proc):
    try:
        parent = psutil.Process(proc.pid)
    except psutil.NoSuchProcess:
        return []
    kids = parent.children(recursive=True)
    for p in [parent] + kids:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs([parent] + kids, timeout=10)
    return [k.pid for k in kids]


def wait_for(cond, timeout=120, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


def test_crash_resume_without_duplicate_trials(tmp_path):
    db, st, rid, cfg = new_run(tmp_path, max_candidates=8, max_trials_per_candidate=8, max_workers=2)
    st.update_run(rid, config={**cfg, "timeframes": ["M30"]})
    proc = spawn_supervisor(db)
    try:
        assert wait_for(lambda: st.count_trials(rid) >= 3, timeout=240), "geen trials binnen de tijd"
        killed = kill_tree(proc)
    finally:
        kill_tree(proc)
    assert killed, "workerprocessen moeten bestaan"
    before = st.count_trials(rid)
    run = st.run(rid)
    assert run["status"] == "running" and run["phase"] in ("researching", "selecting", "final_test")
    out = factory_worker.Supervisor(db, idle_exit_s=0.3).serve()
    assert out == "idle"
    run = st.run(rid)
    assert run["status"] == "done", run["error"]
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT cand_id, rung, params_json, stage FROM trials WHERE run_id=?", (rid,)).fetchall()
    keys = Counter((r[0], r[1], r[2], r[3]) for r in rows)
    assert max(keys.values()) == 1, "dubbele trials na hervatten"
    assert st.count_trials(rid) >= before
    for c in st.candidates(rid):
        l4 = st.stage_result(rid, c["cand_id"], "L4")
        if l4 and l4["payload"] and l4["payload"].get("n_trials"):
            n = sum(1 for r in rows if r[0] == c["cand_id"] and r[3] == "L4")
            assert n == l4["payload"]["n_trials"], (c["cand_id"], n, l4["payload"]["n_trials"])
    assert any("hervat" in x["message"] for x in st.log_tail(rid, 200))
    # ledger: one row per trial; a trial interrupted between ledger write and checkpoint may appear twice
    from strategy_lab.research.ledger import ResearchLedger
    led = ResearchLedger(cfg["ledger_path"])
    l4_rows = [e for e in led.list(limit=5000)
               if ((e.get("statistics") or {}).get("factory") or {}).get("run_id") == rid
               and (e["statistics"]["factory"].get("stage") == "L4")]
    per = Counter((e["statistics"]["factory"]["candidate_id"], e["hypothesis_id"],
                   e["statistics"]["factory"].get("trial_rung")) for e in l4_rows)
    dups = sum(v - 1 for v in per.values() if v > 1)
    assert dups <= int(cfg["workers"])
    assert led.n_trials() == len({e["hypothesis_id"] for e in led.list(limit=5000)})  # duplicates never inflate N


def test_pause_resume_and_real_stop(tmp_path):
    db, st, rid, cfg = new_run(tmp_path, max_candidates=10, max_trials_per_candidate=8, max_workers=2)
    st.update_run(rid, config={**cfg, "timeframes": ["M30"]})
    proc = spawn_supervisor(db, idle_exit=30)
    try:
        assert wait_for(lambda: st.run(rid)["phase"] == "researching" and
                        any(t["state"] in ("running", "done") for t in st.tasks(rid)), timeout=240)
        st.update_run(rid, control="pause")
        assert wait_for(lambda: st.run(rid)["status"] == "paused", timeout=30)
        time.sleep(2.5)
        snap = {(t["cand_id"], t["state"]) for t in st.tasks(rid)}
        trials0 = st.count_trials(rid)
        time.sleep(5)
        assert {(t["cand_id"], t["state"]) for t in st.tasks(rid)} == snap, "taken veranderd tijdens pauze"
        assert st.count_trials(rid) == trials0
        st.update_run(rid, control=None)
        assert wait_for(lambda: st.run(rid)["status"] == "running", timeout=30)
        assert wait_for(lambda: st.count_trials(rid) > trials0 or
                        sum(t["state"] == "done" for t in st.tasks(rid)) > sum(s == "done" for _, s in snap), 120)
        sup = psutil.Process(proc.pid)
        st.update_run(rid, control="stop")
        assert wait_for(lambda: st.run(rid)["status"] == "stopped", timeout=60)
        assert wait_for(lambda: not sup.children(recursive=True), timeout=30), "workers draaien nog na stop"
        tasks = st.tasks(rid)
        assert not any(t["state"] in ("queued", "running") for t in tasks)
        stopped = [c for c in st.candidates(rid) if c["status"] == "STOPPED"]
        assert stopped or all(t["state"] == "done" for t in tasks)
        n_after = st.count_trials(rid)
        time.sleep(3)
        assert st.count_trials(rid) == n_after, "na stop worden nog trials geschreven"
    finally:
        kill_tree(proc)
