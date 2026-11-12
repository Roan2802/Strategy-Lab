"""V10.2 PBO review: CSCV preconditions, exclusion policy, REVIEW_REQUIRED decision logic, review log, promotion block
and the pbo-review endpoint. Synthetic FIXTURES: they test code paths, not strategy performance."""
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from strategy_lab.research import factory, factory_worker, protocol, validation  # noqa: E402
from strategy_lab.research.jobs import JobCancelled  # noqa: E402
from strategy_lab.research.ledger import ResearchLedger  # noqa: E402
from strategy_lab.tests.test_v10_stats_factory import _strong_daily, new_run  # noqa: E402

DAYS = [f"2019-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 28)]
VAL = ("2019-01-01", "2019-12-31")


def _trades(rng, days=DAYS, sd=10.0, mean=0.0):
    return [{"entry_time": f"{d}T10:00:00", "exit_time": f"{d}T12:00:00", "pnl": float(x)}
            for d, x in zip(days, rng.normal(mean, sd, len(days)))]


def _payload(n=12, best=0, top=(0, 1)):
    ps = [{"k": j} for j in range(n)]
    return {"rungs": [{"trials": [{"params": p} for p in ps]}], "best": {"params": ps[best]},
            "top_k": [ps[j] for j in top]}


def _pbo(trades_fn, payload=None, **kw):
    kw.setdefault("val_range", VAL)
    kw.setdefault("cost_fp", "x")
    kw.setdefault("chosen_cost_fp", "x")
    kw.setdefault("max_pbo", 0.5)
    return factory.pbo_over_l4(payload or _payload(), trades_fn, 10_000.0, **kw)


# ------------------------------------------------------------------------------------------------ preconditions
def test_preconditions_pass_and_result_is_auditable():
    rng = np.random.default_rng(1)
    out = _pbo(lambda p: _trades(rng))
    assert out["status"] == "OK" and 0.0 <= out["pbo"] <= 1.0
    assert out["config_set"] == "L4_all" and out["n_configs"] == 12 and out["n_eligible"] == 12 and out["excluded"] == []
    pre = out["preconditions"]
    assert pre["time_index"] == pre["cost_model"] == pre["selection_set"] == pre["exclusion_policy"] == "PASS"
    assert pre["metric"] == "NOTE" and "Pareto" in out["precondition_notes"]["metric"]
    assert len(out["configs"]) == 12 and all(len(c["hash"]) == 64 and c["params"] and "sharpe_daily" in c
                                             for c in out["configs"])


@pytest.mark.parametrize("kw, key", [
    ({"chosen_cost_fp": "other"}, "cost_model"),
    ({"val_range": ("2019-03-01", "2019-12-31")}, "time_index"),          # trades before the validation start
])
def test_failed_precondition_gives_insufficient_evidence_not_a_number(kw, key):
    rng = np.random.default_rng(2)
    out = _pbo(lambda p: _trades(rng), **kw)
    assert out["preconditions"][key] == "FAIL"
    assert out["status"] == validation.INSUFFICIENT and out["pbo"] is None and out["precondition_failed"] is True
    chk = factory.pbo_check(out, 0.5)
    assert chk["status"] == "INSUFFICIENT_EVIDENCE" and chk["value"] is None


def test_selection_set_must_contain_the_chosen_configurations():
    rng = np.random.default_rng(3)
    pl = _payload()
    pl["top_k"] = [{"k": 99}]
    out = _pbo(lambda p: _trades(rng), pl)
    assert out["preconditions"]["selection_set"] == "FAIL" and out["pbo"] is None


def test_too_few_configurations_is_not_applicable_not_a_failure():
    rng = np.random.default_rng(4)
    out = _pbo(lambda p: _trades(rng), _payload(n=1, top=(0,)))
    assert out["pbo"] is None and out["applicable"] is False and not out["precondition_failed"]
    assert factory.pbo_check(out, 0.5)["status"] == "PASS"


# ------------------------------------------------------------------------------------------------ exclusion policy
def test_exclusion_only_for_technical_reasons_never_for_performance():
    rng = np.random.default_rng(5)

    def fn(p):
        if p["k"] == 3:
            raise RuntimeError("backtest kapot")
        if p["k"] == 4:
            return []
        # k == 5 loses heavily; k == 6 wins heavily: both must stay in the set
        return _trades(rng, mean={5: -50.0, 6: 50.0}.get(p["k"], 0.0))
    out = _pbo(fn)
    assert {e["params"]["k"]: e["reason"].split(":")[0] for e in out["excluded"]} == \
        {3: "TECHNICAL_ERROR", 4: "ZERO_TRADES_VALIDATION"}
    assert out["n_configs"] == 12 and out["n_eligible"] == 10
    kept = {c["params"]["k"] for c in out["configs"]}
    assert {5, 6} <= kept and out["status"] == "OK"
    worst = min(out["configs"], key=lambda c: c["net_profit"])
    assert worst["params"]["k"] == 5


def test_more_than_half_excluded_fails_the_set_representation():
    rng = np.random.default_rng(6)
    out = _pbo(lambda p: [] if p["k"] % 3 else _trades(rng))
    assert out["preconditions"]["exclusion_policy"] == "FAIL" and out["pbo"] is None


def test_cancellation_is_never_swallowed_as_a_technical_exclusion():
    def fn(p):
        raise JobCancelled()
    with pytest.raises(JobCancelled):
        _pbo(fn)


# ------------------------------------------------------------------------------------------------ decision logic
def _checks(pbo_status="PASS", other=None):
    c = [factory._chk("oos_trades", "PASS", 200, 100, "genoeg"), factory._chk("dual_positive", "PASS", 1.0, 0.0, "dual ok"),
         factory._chk("pbo", pbo_status, 0.7, 0.5, "PBO 0.70 > 0.5")]
    if other:
        c.append(factory._chk(other[0], other[1], 0, 0, f"{other[0]} {other[1]}"))
    return c


SC = {"verdict": "OOS_PASSED", "counts": {"FAIL": 0}, "notes_nl": []}
EV = {"verdict": "SUPPORTED_UNDER_ASSUMPTIONS", "reasons": []}


def test_review_required_only_when_pbo_is_the_sole_non_pass_reason():
    d = factory.l5_decision(_checks("REVIEW_REQUIRED"), SC, True, EV)
    assert d["review_required"] and d["reasons"] == [] and d["insufficient"] == [] and d["review"]
    ok = factory.l5_decision(_checks("PASS"), SC, True, EV)
    assert not ok["review_required"] and not ok["reasons"]


@pytest.mark.parametrize("other, bucket", [(("max_dd_oos", "FAIL"), "reasons"),
                                           (("dual_positive_2", "FAIL"), "reasons"),
                                           (("expectancy", "INSUFFICIENT_EVIDENCE"), "insufficient")])
def test_any_other_problem_decides_first_and_keeps_the_pbo_warning_among_reasons(other, bucket):
    d = factory.l5_decision(_checks("REVIEW_REQUIRED", other), SC, True, EV)
    assert not d["review_required"] and d["review"] == []
    assert any("PBO" in r for r in d[bucket])


def test_failed_baseline_scorecard_or_robustness_beats_review():
    chk = _checks("REVIEW_REQUIRED")
    assert factory.l5_decision(chk, SC, False, EV)["reasons"]                       # baseline not beaten
    assert factory.l5_decision(chk, {**SC, "verdict": "REJECT", "counts": {"FAIL": 2}}, True, EV)["reasons"]
    assert factory.l5_decision(chk, SC, True, {"verdict": "NOT_SUPPORTED", "reasons": ["x"]})["reasons"]


def test_pbo_check_and_quality_checks_make_pbo_a_review_trigger_not_a_failure():
    q = factory.QUALITY_PROFILES["regelmatig"]
    assert factory.pbo_check({"pbo": 0.8, "status": "OK"}, 0.5)["status"] == "REVIEW_REQUIRED"
    assert factory.pbo_check({"pbo": 0.3, "status": "OK"}, 0.5)["status"] == "PASS"
    om = {"trades": 300, "trades_per_week": 3.0, "profit_factor": 1.6, "recovery_factor": 5.0, "max_drawdown_pct": 5.0,
          "expectancy": 5.0, "net_profit": 1500.0}
    chk = {c["name"]: c["status"] for c in factory.quality_checks(om, q, pbo={"pbo": 0.9, "status": "OK"},
                                                                  roll={"wfe": 0.9}, expd={"net_profit": 10})}
    assert chk["pbo"] == "REVIEW_REQUIRED" and chk["overfit_flags"] == "PASS"


def test_scorecard_pbo_review_mode_is_not_a_fail_and_default_stays_a_fail():
    ev = dict(n_trades=400, net_profit=900.0, profit_factor=1.4, max_drawdown_pct=8.0, psr=0.99, dsr=0.99, pbo=0.8,
              n_trials=10, wfe=0.9, oos_net_profit=500, oos_n_trades=200, oos_profitable_windows_frac=0.8,
              cost_stress_spread50_net=100.0, parameter_stability_frac_positive=0.9, net_after_drop_best=50.0,
              yearly_positive_frac=0.8, n_years=5)
    th = {"max_pbo": 0.5}
    base = {c["name"]: c["status"] for c in validation.scorecard(ev, th)["checks"]}
    rev = validation.scorecard(ev, {**th, "pbo_review": True})
    assert base["pbo"] == "FAIL"
    assert {c["name"]: c["status"] for c in rev["checks"]}["pbo"] == "REVIEW_REQUIRED"
    assert rev["counts"]["FAIL"] == 0 and rev["verdict"] != "FORWARD_ELIGIBLE"


def test_outcome_and_promotion_labels_know_review_required():
    assert "REVIEW_REQUIRED" in factory.OUTCOMES
    out = factory.classify_outcome({"status": "REVIEW_REQUIRED", "stage": "L5", "reasons": ["PBO 0.70 > 0.5"]})
    assert out["outcome"] == "REVIEW_REQUIRED" and out["missing_nl"] and "DSR" in " ".join(out["next_test_nl"])
    assert factory.promotion_status("REVIEW_REQUIRED", "L5")["promotion_status"] == "DEVELOPMENT_PASS"
    assert "REVIEW_REQUIRED" in factory.TERMINAL_STATUSES        # a stop never overwrites it


# ------------------------------------------------------------------------------------------------ N / registration
def test_pbo_configs_are_registered_but_never_counted_as_new_trials(tmp_path):
    st = factory.FactoryStore(tmp_path / "f.sqlite3")
    st.put_trial("t1", "r", "c", "L4", "r0", {"k": 1}, {"sharpe_daily": 1.0, "n_days": 100}, "ledger-1")
    rng = np.random.default_rng(7)
    pbo = _pbo(lambda p: _trades(rng))
    n = factory.register_pbo_configs(SimpleNamespace(store=st), "r", "c", pbo)
    assert n == 12 and factory.register_pbo_configs(SimpleNamespace(store=st), "r", "c", pbo) == 0   # idempotent
    reg = [t for t in st.trials("r", "c") if t["stage"] == factory.PBO_TRIAL_STAGE]
    assert len(reg) == 12 and all(t["metrics"]["config_hash"] and "sharpe_daily" in t["metrics"] for t in reg)
    assert next(t for t in reg if t["params"] == {"k": 1})["ledger_id"] == "ledger-1"   # points at the L4 ledger row
    assert st.count_trials("r") == 1 and st.count_trials() == 1              # N for the DSR is unchanged
    assert all(r["stage"] != factory.PBO_TRIAL_STAGE for r in factory._store_rows_for(st, "r"))


# ------------------------------------------------------------------------------------------------ review record
def test_pbo_review_record_is_pure_and_validates():
    r = factory.pbo_review_record("REVIEW_REQUIRED", "Accept", " Roan ", " akkoord ", pbo=0.7, max_pbo=0.5,
                                  at="2026-11-12T10:00:00+00:00")
    assert r["new_status"] == "WF_PASSED" and r["record"]["reviewer"] == "Roan" and r["record"]["decision"] == "accept"
    assert "DSR" in r["reasons"][0]
    assert factory.pbo_review_record("REVIEW_REQUIRED", "reject", "a", "b")["new_status"] == "REJECTED"
    with pytest.raises(factory.ReviewStateError):
        factory.pbo_review_record("WF_PASSED", "accept", "a", "b")
    for bad in (("maybe", "a", "b"), ("accept", "", "b"), ("accept", "a", "  ")):
        with pytest.raises(ValueError):
            factory.pbo_review_record("REVIEW_REQUIRED", *bad)


def _review_setup(tmp_path, n=3):
    db, st, rid, cfg = new_run(tmp_path)
    factory_worker.prepare_run(st, st.run(rid))
    led = ResearchLedger(cfg["ledger_path"])
    factory_worker.generate_candidates(st, st.run(rid), led)
    cands = st.candidates(rid)
    assert len(cands) >= n
    return db, st, rid, cfg, led, cands[:n]


def _inject_review_required(st, rid, c, mean):
    params = {x["name"]: x["default"] for x in c["candidate"]["spec"]["params"]}
    pr = {"required": True, "pbo": 0.71, "max_pbo": 0.5, "state": "PENDING"}
    st.put_stage(rid, c["cand_id"], "L5", "REVIEW_REQUIRED", 0, ["PBO 0.71 > 0.5"],
                 {"dsr": 0.99, "profit_factor": 1.5, "recovery_factor": 3, "pbo": 0.71, "pbo_review_required": True},
                 {"params": params, "daily_returns": _strong_daily(len(c["cand_id"]), mean), "pbo_review": pr,
                  "fixture": True}, [])
    st.set_candidate(rid, c["cand_id"], status="REVIEW_REQUIRED", stage="L5", reasons=["PBO 0.71 > 0.5"])


def test_promotion_is_blocked_until_review_and_accept_still_needs_the_dsr_gate(tmp_path):
    db, st, rid, cfg, led, (strong, weak, rej) = _review_setup(tmp_path)
    for c, mean in ((strong, 0.004), (weak, 0.0002), (rej, 0.004)):
        _inject_review_required(st, rid, c, mean)
    vault = protocol.SealedVault(db)
    # before any review nothing is selected or frozen, and nothing changes status
    assert factory.select_and_freeze(st, st.run(rid), vault, led) == []
    assert {st.candidate(rid, c["cand_id"])["status"] for c in (strong, weak, rej)} == {"REVIEW_REQUIRED"}
    assert not factory.l5_cleared(st, rid, strong["cand_id"])
    # accept: strong continues through L6 and is frozen; weak is accepted too but the DSR gate still rejects it
    for c in (strong, weak):
        r = factory.review_pbo(st, c["cand_id"], "accept", "Roan", "plateau-edge, PBO-waarschuwing begrepen",
                               run_id=rid, ledger=led)
        assert r["status"] == "WF_PASSED"
        assert factory.l5_cleared(st, rid, c["cand_id"])
    assert factory.review_pbo(st, rej["cand_id"], "reject", "Roan", "zie notitie", run_id=rid)["status"] == "REJECTED"
    sel = {s["cand_id"]: s for s in factory.select_and_freeze(st, st.run(rid), vault, led)}
    assert set(sel) == {strong["cand_id"], weak["cand_id"]}                      # the rejected one never reaches L6
    assert sel[strong["cand_id"]]["selected"] is True
    assert sel[weak["cand_id"]]["selected"] is False and any("globale DSR" in x for x in sel[weak["cand_id"]]["reasons"])
    assert st.candidate(rid, rej["cand_id"])["status"] == "REJECTED"
    assert not factory.l5_cleared(st, rid, rej["cand_id"])
    assert any(d["decision"].startswith("PBO-beoordeling") for d in led.decisions(candidate_id=strong["cand_id"]))


def test_review_log_is_append_only_and_final(tmp_path):
    db, st, rid, cfg, led, (c, other, _) = _review_setup(tmp_path)
    _inject_review_required(st, rid, c, 0.004)
    with pytest.raises(LookupError):
        factory.review_pbo(st, "bestaat-niet", "accept", "a", "b")
    with pytest.raises(factory.ReviewStateError):                               # not REVIEW_REQUIRED
        factory.review_pbo(st, other["cand_id"], "accept", "a", "b", run_id=rid)
    factory.review_pbo(st, c["cand_id"], "accept", "Roan", "eerste", run_id=rid, at="2026-11-12T10:00:00+00:00")
    with pytest.raises(factory.ReviewStateError):                               # final: cannot be redone or flipped
        factory.review_pbo(st, c["cand_id"], "reject", "Roan", "omgedraaid", run_id=rid)
    rows = st.pbo_reviews(rid, c["cand_id"])
    assert len(rows) == 1 and rows[0]["decision"] == "accept" and rows[0]["at"] == "2026-11-12T10:00:00+00:00"
    assert rows[0]["pbo"] == 0.71 and rows[0]["l5_sha256"]
    conn = st.db()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE pbo_reviews SET decision='reject'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM pbo_reviews")
    finally:
        conn.close()
    assert st.pbo_reviews(rid, c["cand_id"])[0]["decision"] == "accept"


# ------------------------------------------------------------------------------------------------ endpoint
def test_pbo_review_endpoint_404_409_422_200(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from strategy_lab import factory_api, server
    db, st, rid, cfg, led, (c, other, _) = _review_setup(tmp_path)
    monkeypatch.setattr(factory_api, "DB_PATH", db)
    _inject_review_required(st, rid, c, 0.004)
    client = TestClient(server.app)
    url = "/api/factory/candidates/{}/pbo-review"
    good = {"decision": "accept", "reviewer": "Roan", "note": "bewust geaccepteerd", "run_id": rid}
    assert client.post(url.format("bestaat-niet"), json=good).status_code == 404
    assert client.post(url.format(other["cand_id"]), json=good).status_code == 409          # not REVIEW_REQUIRED
    assert client.post(url.format(c["cand_id"]), json={**good, "decision": "misschien"}).status_code == 422
    assert client.post(url.format(c["cand_id"]), json={**good, "note": ""}).status_code == 422
    assert st.candidate(rid, c["cand_id"])["status"] == "REVIEW_REQUIRED"
    ok = client.post(url.format(c["cand_id"]), json=good)
    assert ok.status_code == 200 and ok.json()["status"] == "WF_PASSED" and ok.json()["review"]["reviewer"] == "Roan"
    assert st.candidate(rid, c["cand_id"])["status"] == "WF_PASSED"
    assert client.post(url.format(c["cand_id"]), json=good).status_code == 409              # a review is final
