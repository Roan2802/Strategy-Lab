"""Deliberately bad situations: none of them may come out of the factory looking strong without a warning."""
import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from strategy_lab.research import distribution, factory, factory_worker, protocol, validation  # noqa: E402
from strategy_lab.research.ledger import ResearchLedger  # noqa: E402
from strategy_lab.tests import factory_fixtures as fx  # noqa: E402
from strategy_lab.tests.test_factory import _trades, new_run  # noqa: E402

Q = factory.QUALITY_PROFILES["regelmatig"]


def _om(**kw):
    base = {"trades": 400, "net_profit": 3000.0, "net_return_pct": 30.0, "profit_factor": 1.5, "max_drawdown_pct": 8.0,
            "recovery_factor": 3.0, "win_rate": 0.5, "sharpe_daily": 1.2, "expectancy": 7.5, "avg_r": 0.2,
            "trades_per_week": 1.5}
    base.update(kw)
    return base


def _good_boot():
    return {"status": "OK", "ci_low": 1.0, "ci_high": 12.0}


def _statuses(checks):
    return {c["name"]: c["status"] for c in checks}


@pytest.fixture()
def prepared(tmp_path):
    db, st, rid, cfg = new_run(tmp_path, max_candidates=3)
    factory_worker.prepare_run(st, st.run(rid))
    led = ResearchLedger(cfg["ledger_path"])
    factory_worker.generate_candidates(st, st.run(rid), led)
    run = st.run(rid)
    mk = run["protocol"]["markets"]["XAUUSD:H4"]
    data = protocol.SearchData.from_file(mk["frames_path"], cfg["sealed_start"], symbol="XAUUSD", timeframe="H4")
    return {"db": db, "st": st, "rid": rid, "cfg": cfg, "led": led, "run": run, "data": data,
            "vault": protocol.SealedVault(db)}


def _runner(p, cand_row=None, generator=None):
    env = factory.Env(p["st"], p["st"].run(p["rid"]), p["data"], ledger=p["led"], vault=p["vault"],
                      generator=generator)
    return factory.CandidateRunner(env, cand_row or p["st"].candidates(p["rid"])[0])


# 1. great in train, losing out-of-sample
def test_great_in_train_losing_oos_is_rejected(prepared, monkeypatch):
    p = prepared
    real_wf = validation.walk_forward

    def losing_wf(fn, cands, start, end, *a, **k):
        out = real_wf(fn, cands, start, end, *a, **k)
        for t in out["oos_trades"]:
            t["pnl"] = -abs(t["pnl"]) - 1.0
        out["oos_metrics"] = validation.trade_metrics(out["oos_trades"], 10000)
        out["oos_net_profit"] = out["oos_metrics"]["net_profit"]
        out["wfe"] = -1.0
        return out

    monkeypatch.setattr(validation, "walk_forward", losing_wf)
    row = p["st"].candidates(p["rid"])[0]
    great = {"trades": 300, "net_profit": 5000.0, "net_return_pct": 50.0, "profit_factor": 2.5, "max_drawdown_pct": 5.0,
             "recovery_factor": 10.0, "trades_per_week": 2.0, "stability": 1.0}
    params = {x["name"]: x["default"] for x in row["candidate"]["spec"]["params"]}
    for st_name in ("L1", "L2", "L3"):
        p["st"].put_stage(p["rid"], row["cand_id"], st_name, "PASS", 0, [], great, {}, [])
    p["st"].put_stage(p["rid"], row["cand_id"], "L4", "PASS", 0, [], great,
                      {"best": {"params": params, "metrics": great}, "top_k": [params], "n_trials": 1}, [])
    status = _runner(p, row).run()
    assert status == "REJECTED"
    c = p["st"].candidate(p["rid"], row["cand_id"])
    assert c["status"] == "REJECTED" and c["stage"] == "L5"
    text = " ".join(c["reasons"])
    assert "OOS" in text or "scorecard" in text


# 2. very few trades
def test_very_few_trades_is_never_a_pass():
    checks = factory.quality_checks(_om(trades=12, trades_per_week=0.05), Q, boot={"status": "INSUFFICIENT_EVIDENCE"})
    s = _statuses(checks)
    # V10.1: a small sample is INSUFFICIENT_EVIDENCE, never a FAIL; the frequency criterion still fails it
    assert s["oos_trades"] == "INSUFFICIENT_EVIDENCE" and s["frequency"] == "FAIL"
    assert s["expectancy"] == "INSUFFICIENT_EVIDENCE" and "PASS" not in (s["oos_trades"], s["frequency"])
    sealed = factory.sealed_checks({"trades": 6, "net_profit": 900.0, "profit_factor": 4.0, "max_drawdown_pct": 1.0},
                                   Q, {"p_raw": 0.01, "p_holm": 0.01, "p_bh": 0.01}, 365)
    assert _statuses(sealed)["sealed_trades"] == "INSUFFICIENT_EVIDENCE"
    sc = validation.scorecard({"n_trades": 12, "net_profit": 900, "profit_factor": 4.0, "max_drawdown_pct": 1.0},
                              {"min_trades": Q["min_oos_trades"]})
    assert sc["verdict"] in ("NEEDS_MORE_DATA", "REJECT")


# 3. all profit from one day
def test_all_profit_from_one_day_is_flagged_and_fails_concentration():
    trades = _trades([-2] * 200 + [2000])
    d = distribution.analyse(trades, 10000)
    assert "PROFIT_CONCENTRATION" in d["flags"] and any("WAARSCHUWING" in w for w in d["warnings_nl"])
    s = _statuses(factory.quality_checks(_om(), Q, dist=d, boot=_good_boot()))
    assert s["concentration"] == "FAIL"


# 4. loses at higher spreads
def test_loses_at_higher_spread_fails_spread_stress():
    def bt(params, start, end, spread_multiplier=1.0, **kw):
        pnl = 10.0 - 15.0 * (spread_multiplier - 1.0) * 2
        return {"trades": _trades([pnl] * 50)}

    cost = validation.cost_stress(bt, {}, "2020-01-01", "2021-01-01", base_commission_per_lot=0.0, capital=10000)
    assert cost["baseline_net_profit"] > 0 and cost["survives_spread_50"] is False
    s = _statuses(factory.quality_checks(_om(), Q, cost=cost, boot=_good_boot()))
    assert s["spread_stress"] == "FAIL"


# 5. uses future candles -> rejected at L1
def test_leaky_spec_rejected_at_l1(prepared):
    p = prepared
    row = copy.deepcopy(p["st"].candidates(p["rid"])[0])
    spec = row["candidate"]["spec"]
    spec["entry"]["long"] = {"left": {"price": "close", "shift": -1}, "op": ">", "right": {"price": "close"}}
    row["cand_id"] = row["candidate"]["id"] = "leaky_spec"
    p["st"].add_candidates(p["rid"], [row["candidate"]])
    assert _runner(p, p["st"].candidate(p["rid"], "leaky_spec")).run() == "REJECTED"
    assert any("ongeldige spec" in r or "shift" in r for r in p["st"].candidate(p["rid"], "leaky_spec")["reasons"])


def test_leaky_signal_function_rejected_at_l1(prepared, monkeypatch):
    p = prepared
    real = factory._signals

    def peeking(spec, bid, params=None):
        long_, short = real(spec, bid, params)
        fut = bid["close"].shift(-1).to_numpy() > bid["close"].to_numpy()  # tomorrow's close: look-ahead
        return long_ | fut, short

    row = p["st"].candidates(p["rid"])[1]
    assert factory.causality_check(row["candidate"]["spec"], p["data"].bid.iloc[:3000], signal_fn=peeking)["ok"] is False
    assert factory.causality_check(row["candidate"]["spec"], p["data"].bid.iloc[:3000])["ok"] is True
    monkeypatch.setattr(factory, "_signals", peeking)
    assert _runner(p, row).run() == "REJECTED"
    assert any("look-ahead" in r for r in p["st"].candidate(p["rid"], row["cand_id"])["reasons"])


# 6. pure parameter overfit
def test_parameter_overfit_flags():
    stab = {"frac_positive": 0.2, "neighbours": [{}] * 6}
    pbo = {"pbo": 0.85, "status": "OK"}
    s = _statuses(factory.quality_checks(_om(), Q, stability=stab, pbo=pbo, roll={"wfe": 0.1},
                                         expd={"net_profit": -50}, boot=_good_boot()))
    assert s["param_stability"] == "FAIL" and s["overfit_flags"] == "FAIL"
    # best-in-sample of pure noise: PBO says the IS winner carries no information
    rng = np.random.default_rng(0)
    res = validation.pbo_cscv(rng.normal(0, 1, (400, 30)), n_splits=8)
    assert res["pbo"] > 0.3


# 7. re-test / adjust after the sealed test
def test_retest_after_sealed_is_blocked_and_reused_holdout(prepared):
    p = prepared
    st, rid, vault = p["st"], p["rid"], p["vault"]
    c = st.candidates(rid)[0]
    cand = c["candidate"]
    mk = st.run(rid)["protocol"]["markets"]["XAUUSD:H4"]
    params = {x["name"]: x["default"] for x in cand["spec"]["params"]}
    rec = {"run_id": rid, "candidate_id": c["cand_id"], "spec": cand["spec"], "params": params,
           "cost_model": mk["cost_model"], "risk_model": {}, "selection_criteria": {}, "code_sha256": "x",
           "dataset_sha256": mk["dataset_sha256"], "research_version": "t", "seed": 0, "symbol": "XAUUSD",
           "timeframe": "H4", "frames_path": mk["frames_path"], "sealed_start": "2019-07-01",
           "sealed_end": "2020-07-01", "signature": cand["signature"], "family": cand["family"], "lineage": []}
    fid = vault.freeze(rec)
    assert vault.freeze(rec) == fid  # immutable, same id
    first = vault.final_test(fid, who="test")
    assert first["evidence_class"] == "HISTORICAL_OOS"
    again = vault.final_test(fid, who="test")
    assert again["repeat_read"] is True and again["metrics"] == first["metrics"]
    bounds = {x["name"]: x for x in cand["spec"]["params"]}
    tweaked = {**rec, "params": {k: (bounds[k]["min"] if v != bounds[k]["min"] else bounds[k]["max"])
                                 for k, v in params.items()}}
    fid2 = vault.freeze(tweaked)
    with pytest.raises(protocol.SealedReuseError):
        vault.final_test(fid2, who="test")
    reused = vault.final_test(fid2, who="test", allow_reuse=True)
    assert reused["evidence_class"] == "REUSED_HOLDOUT"
    with pytest.raises(PermissionError):
        vault.final_test("frz_unknown", who="test")
    actions = [a["action"] for a in vault.audit_log()]
    for a in ("ACCESS_DONE", "REPEAT_READ", "BLOCKED_REUSED_HOLDOUT", "REFUSED"):
        assert a in actions
    import sqlite3
    with sqlite3.connect(p["db"]) as db, pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM sealed_audit")
    with sqlite3.connect(p["db"]) as db, pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE frozen SET record_json='{}'")
    # the same lineage cannot be re-optimised: L1 classifies it REUSED_HOLDOUT before any search
    assert _runner(p, c).run() == "REUSED_HOLDOUT"
    assert st.candidate(rid, c["cand_id"])["evidence_class"] == "REUSED_HOLDOUT"
    mt = vault.multiple_testing(search_trials=100)
    assert mt["n_final_tested"] == 2 and mt["dsr_n_trials"] == 102


# 8. optimizer requesting sealed bars
def test_optimizer_requesting_sealed_bars_raises(prepared):
    p = prepared
    row = p["st"].candidates(p["rid"])[0]
    r = _runner(p, row)
    with pytest.raises(protocol.SealedAccessError):
        r._bt(None, "2019-01-01", "2019-12-31")
    with pytest.raises(protocol.SealedAccessError):
        r._trial({}, "manual", "2018-01-01", "2020-01-01")
    run = p["st"].run(p["rid"])
    bad = copy.deepcopy(run)
    bad["protocol"]["markets"]["XAUUSD:H4"]["ranges"]["development"]["end"] = "2020-01-01"
    env = factory.Env(p["st"], bad, p["data"], ledger=p["led"], vault=p["vault"])
    runner = factory.CandidateRunner(env, row)
    with pytest.raises(protocol.SealedAccessError):
        runner.l4()
    assert p["data"].bid.index.max() < pd.Timestamp("2019-07-01", tz="UTC")
