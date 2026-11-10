import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from strategy_lab.research import lifecycle as lc  # noqa: E402
from strategy_lab.research.jobs import JobManager  # noqa: E402
from strategy_lab.research.ledger import ResearchLedger  # noqa: E402
from strategy_lab.research.lifecycle import Lifecycle  # noqa: E402
from strategy_lab.research.monitoring import forward_alerts, rolling_metrics  # noqa: E402


# ------------------------------------------------------------------ ledger
@pytest.fixture
def ledger(tmp_path):
    return ResearchLedger(tmp_path / "ledger.sqlite3")


def test_ledger_roundtrip_and_flatten(ledger):
    entry = ledger.record(kind="BACKTEST", family="breakout", symbol="EURUSD", timeframe="M15",
                          start="2023-01-01", end="2023-06-01", spec={"a": 1}, status="CANDIDATE",
                          metrics={"trades": 40, "net_profit": 12.5, "profit_factor": 1.4, "sharpe_daily": 0.5},
                          oos_metrics={"net_profit": 3.0}, trials=[{"p": 1}])
    row = ledger.get(entry)
    assert row["spec_sha256"] and row["trials"] == [{"p": 1}]
    listed = ledger.list(status="CANDIDATE")[0]
    assert (listed["trades"], listed["net_profit"], listed["profit_factor"], listed["oos_net_profit"]) == (40, 12.5, 1.4, 3.0)
    assert ledger.summary()["candidates"] == 1
    with pytest.raises(ValueError):
        ledger.record(kind="NOPE")


def test_ledger_is_append_only(ledger, tmp_path):
    entry = ledger.record(family="f")
    db = sqlite3.connect(ledger.path)
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE entries SET status='REJECTED' WHERE id=?", (entry,))
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM entries WHERE id=?", (entry,))
    db.close()
    child = ledger.record(family="f", parent_id=entry, status="REJECTED")
    assert ledger.get(child)["parent_id"] == entry and ledger.get(entry)["status"] == "TESTED"


def test_n_trials_counts_distinct_hypotheses_and_sharpe_variance(ledger):
    for hyp in ("h1", "h1", "h2", "h3"):
        ledger.record(hypothesis_id=hyp, family="A", symbol="EURUSD", timeframe="M15",
                      metrics={"sharpe_daily": len(hyp) + (hyp == "h2")})
    ledger.record(hypothesis_id="x", family="B", symbol="GBPUSD", timeframe="H1")
    assert ledger.n_trials() == 4
    assert ledger.n_trials(family="A") == 3
    assert ledger.n_trials(symbol="GBPUSD") == 1
    assert ledger.sharpe_variance(family="A") > 0
    assert ledger.sharpe_variance(family="B") is None


def test_data_exposure_overlap(ledger):
    ledger.record(symbol="EURUSD", start="2023-01-01", end="2023-06-01")
    ledger.record(symbol="EURUSD", start="2024-01-01", end="2024-03-01")
    ledger.record(symbol="GBPUSD", start="2023-01-01", end="2023-06-01")
    hits = ledger.data_exposure("EURUSD", "2023-05-01", "2023-09-01")
    assert len(hits) == 1 and hits[0]["start"] == "2023-01-01"
    assert ledger.data_exposure("EURUSD", "2023-06-01", "2023-12-01") == []


# --------------------------------------------------------------- lifecycle
@pytest.fixture
def life(tmp_path):
    return Lifecycle(tmp_path / "life.sqlite3")


def walk(life, name, steps):
    for status, kwargs in steps:
        life.transition(name, status, "test", **kwargs)


GOOD = {"evidence_ref": "led1", "evidence": {"verdict": "FORWARD_ELIGIBLE"}}


def test_legal_path_and_illegal_transition(life):
    life.register("s", "python")
    with pytest.raises(ValueError, match="Ongeldige overgang"):
        life.transition("s", "OOS_VALIDATED", "skip", **GOOD)
    walk(life, "s", [("PROTOTYPE", {}), ("BACKTESTING", {}), ("OPTIMIZING", {}), ("BACKTEST_VALIDATED", GOOD),
                     ("OOS_VALIDATED", GOOD),
                     ("FORWARD_TESTING", {"evidence_ref": "reg1",
                                          "evidence": {"verdict": "FORWARD_ELIGIBLE", "registration_id": "reg1"}})])
    assert life.get("s")["status"] == "FORWARD_TESTING"
    life.transition("s", "RETIRED", "klaar")
    with pytest.raises(ValueError):
        life.transition("s", "PROTOTYPE", "nope")


def test_failed_can_restart_and_unknown_inputs(life):
    life.register("s", "ai")
    walk(life, "s", [("PROTOTYPE", {}), ("BACKTESTING", {}), ("VALIDATION_FAILED", {}), ("PROTOTYPE", {})])
    with pytest.raises(ValueError):
        life.register("s", "ai")
    with pytest.raises(ValueError):
        life.register("t", "magic")


def test_evidence_gating(life):
    life.register("s", "builder")
    walk(life, "s", [("PROTOTYPE", {}), ("BACKTESTING", {})])
    with pytest.raises(ValueError, match="evidence_ref"):
        life.transition("s", "BACKTEST_VALIDATED", "x", evidence={"verdict": "OOS_PASSED"})
    with pytest.raises(ValueError, match="verdict"):
        life.transition("s", "BACKTEST_VALIDATED", "x", evidence_ref="l", evidence={"verdict": "REJECTED"})
    life.transition("s", "BACKTEST_VALIDATED", "x", evidence_ref="l", evidence={"verdict": "RESEARCH_CANDIDATE"})
    with pytest.raises(ValueError):  # RESEARCH_CANDIDATE is not enough for OOS
        life.transition("s", "OOS_VALIDATED", "x", evidence_ref="l", evidence={"verdict": "RESEARCH_CANDIDATE"})
    life.transition("s", "OOS_VALIDATED", "x", evidence_ref="l", evidence={"verdict": "OOS_PASSED"})
    with pytest.raises(ValueError, match="registratie"):
        life.transition("s", "FORWARD_TESTING", "x", evidence_ref="l", evidence={"verdict": "OOS_PASSED"})
    life.transition("s", "FORWARD_TESTING", "x", evidence_ref="r",
                    evidence={"verdict": "OOS_PASSED", "registration_id": "r"})
    with pytest.raises(ValueError, match="forward-trades"):
        life.transition("s", "PAPER_ELIGIBLE", "x", evidence_ref="r",
                        evidence={"forward_trades": 10, "forward_days": 90, "gate_c": "PASS"})
    with pytest.raises(ValueError, match="gate_c"):
        life.transition("s", "PAPER_ELIGIBLE", "x", evidence_ref="r",
                        evidence={"forward_trades": 40, "forward_days": 90, "gate_c": "FAIL"})
    life.transition("s", "PAPER_ELIGIBLE", "x", evidence_ref="r",
                    evidence={"forward_trades": 40, "forward_days": 90, "gate_c": "PASS"})
    with pytest.raises(ValueError, match="REALTIME_PAPER_FEED_UNAVAILABLE"):
        life.transition("s", "LIVE_REVIEW_REQUIRED", "x", evidence_ref="r", evidence={"gate_d": "PASS"})
    gates = {g["gate"]: g for g in life.gates("s")}
    assert gates["C"]["status"] == "PASS" and gates["D"]["status"] == "BLOCKED" and gates["E"]["status"] == "BLOCKED"


def test_live_eligible_requires_reviewer(life, monkeypatch):
    monkeypatch.setattr(lc, "REALTIME_PAPER_AVAILABLE", True)
    life.register("s", "library")
    walk(life, "s", [("PROTOTYPE", {}), ("BACKTESTING", {}), ("BACKTEST_VALIDATED", GOOD), ("OOS_VALIDATED", GOOD),
                     ("FORWARD_TESTING", {"evidence_ref": "r", "evidence": {"verdict": "OOS_PASSED", "registration_id": "r"}}),
                     ("PAPER_ELIGIBLE", {"evidence_ref": "r", "evidence": {"forward_trades": 30, "forward_days": 60, "gate_c": "PASS"}}),
                     ("LIVE_REVIEW_REQUIRED", {"evidence_ref": "r", "evidence": {"gate_d": "PASS"}})])
    with pytest.raises(ValueError, match="human_reviewer"):
        life.transition("s", "LIVE_ELIGIBLE", "x", evidence_ref="r", evidence={"gate_e": "PASS"})
    with pytest.raises(ValueError, match="gate_e"):
        life.transition("s", "LIVE_ELIGIBLE", "x", evidence_ref="r", evidence={"human_reviewer": "Jan"})
    life.transition("s", "LIVE_ELIGIBLE", "x", evidence_ref="r", evidence={"human_reviewer": "Jan", "gate_e": "PASS"})
    assert life.get("s")["status"] == "LIVE_ELIGIBLE"


def test_history_append_only_and_verdict(life):
    life.register("s", "python", spec={"k": 1})
    life.record_verdict("s", "REJECTED", "led")
    item = life.get("s")
    assert item["last_verdict"] == "REJECTED" and item["status"] == "IDEA" and item["spec"] == {"k": 1}
    db = sqlite3.connect(life.path)
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE history SET reason='x'")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM history")
    db.close()
    assert [g["gate"] for g in life.gates("s")] == list("ABCDE")


# -------------------------------------------------------------------- jobs
def wait_for(manager, job_id, states, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        job = manager.get(job_id)
        if job["status"] in states:
            return job
        time.sleep(0.01)
    raise AssertionError(manager.get(job_id))


def test_job_progress_and_result():
    manager = JobManager()
    gate = threading.Event()

    def work(ctx, x):
        ctx.progress(0.5, "halverwege")
        gate.wait(2)
        return x * 2

    job_id = manager.submit("backtest", "t", work, 21)
    end = time.time() + 2
    while manager.get(job_id)["progress"] < 0.5 and time.time() < end:
        time.sleep(0.01)
    assert manager.get(job_id)["message"] == "halverwege"
    assert "result" not in manager.list()[0]
    gate.set()
    job = wait_for(manager, job_id, {"done"})
    assert job["result"] == 42 and job["progress"] == 1.0


def test_job_cancel_error_and_timeout():
    manager = JobManager(max_concurrent=1)

    def loop(ctx):
        while True:
            ctx.check()
            time.sleep(0.01)

    running = manager.submit("x", "loop", loop)
    queued = manager.submit("x", "queued", loop)
    wait_for(manager, running, {"running"})
    assert manager.cancel(queued) and manager.get(queued)["status"] == "cancelled"
    assert manager.cancel(running)
    assert wait_for(manager, running, {"cancelled"})["status"] == "cancelled"

    def boom(ctx):
        raise RuntimeError("kapot")

    failed = wait_for(manager, manager.submit("x", "boom", boom), {"error"})
    assert "kapot" in failed["error"]
    timed = wait_for(manager, manager.submit("x", "slow", loop, timeout_s=0.1), {"error"})
    assert timed["error"] == "TIMEOUT"


def test_job_cap():
    manager = JobManager(max_concurrent=2)
    ids = [manager.submit("x", str(i), lambda ctx: 1) for i in range(60)]
    wait_for(manager, ids[-1], {"done"})
    time.sleep(0.1)
    assert len(manager.list(limit=100)) <= 50


# -------------------------------------------------------------- monitoring
NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)


def closed_event(i, pnl, risk=10.0, reg="r1"):
    at = (NOW - timedelta(days=100 - i)).isoformat()
    return {"registration_id": reg, "kind": "POSITION_CLOSED", "candle_at": at, "day": at[:10],
            "detail": {"net_pnl": pnl, "risk": risk, "exit_at": at, "entry_at": at}}


class StubEngine:
    def __init__(self, report):
        self._report = report

    def portfolios(self):
        return [self._report["portfolio"]]

    def report(self, portfolio_id):
        return self._report


def make_report(pnls, expectations=None, days=None, risk_limit=0.05, position=None, joined=None):
    events = [closed_event(i, p) for i, p in enumerate(pnls)]
    member = {"registration_id": "r1", "joined_at": joined or (NOW - timedelta(days=120)).isoformat(),
              "allocation": 1000.0, "state": {"last_candle_at": NOW.isoformat(), "position": position},
              "configuration": {"expectations": expectations} if expectations else {}}
    portfolio = {"id": "p1", "name": "P", "starting_capital": 1000.0, "balance": 1000.0 + sum(pnls),
                 "risk_limit": risk_limit}
    return {"portfolio": portfolio, "members": [member], "events": events, "days": days or []}


def kinds(alerts):
    return {a["kind"] for a in alerts}


def test_alerts_empty_and_malformed():
    assert forward_alerts(StubEngine({"portfolio": {"id": "p"}}), now=NOW) == []

    class Broken:
        def portfolios(self):
            raise RuntimeError

    assert forward_alerts(Broken(), now=NOW) == []
    assert forward_alerts(StubEngine({"portfolio": {"id": "p"}, "members": [{}], "events": None}), now=NOW) == []


def test_no_statistical_alert_below_20_trades():
    pnls = [-10.0] * 15
    alerts = forward_alerts(StubEngine(make_report(pnls, {"mean_r": 0.5, "std_r": 1.0})), now=NOW)
    assert not kinds(alerts) & {"FORWARD_BELOW_EXPECTATION", "REVIEW_REQUIRED"}


def test_below_expectation_and_cusum_on_clear_break():
    pnls = [10.0, -5.0] * 5 + [-10.0] * 25  # healthy start, then a clear break
    alerts = forward_alerts(StubEngine(make_report(pnls, {"mean_r": 0.5, "std_r": 1.0})), now=NOW)
    assert {"FORWARD_BELOW_EXPECTATION", "REVIEW_REQUIRED"} <= kinds(alerts)
    healthy = [5.0, 5.0, -5.0] * 10
    alerts = forward_alerts(StubEngine(make_report(healthy, {"mean_r": 0.33, "std_r": 0.5})), now=NOW)
    assert not kinds(alerts) & {"FORWARD_BELOW_EXPECTATION", "REVIEW_REQUIRED"}


def test_no_trades_drawdown_and_risk_alerts():
    report = make_report([1.0], joined=(NOW - timedelta(days=30)).isoformat())
    report["events"] = [closed_event(0, 1.0)]  # last trade 100 days ago
    assert "NO_TRADES" in kinds(forward_alerts(StubEngine(report), now=NOW))

    days = [{"day": "d1", "equity": 1000.0}, {"day": "d2", "equity": 800.0}]
    alerts = forward_alerts(StubEngine(make_report([], days=days)), now=NOW)
    assert any(a["kind"] == "DRAWDOWN_LIMIT" and a["portfolio_id"] == "p1" for a in alerts)

    position = {"risk": 500.0}
    alerts = forward_alerts(StubEngine(make_report([], position=position)), now=NOW)
    assert "RISK_LIMIT" in kinds(alerts)

    report = make_report([])
    report["events"] = [{"registration_id": "r1", "kind": "ORDER_REJECTED", "candle_at": NOW.isoformat(),
                         "detail": {"reason": "RISK_LIMIT"}}]
    assert "RISK_LIMIT" in kinds(forward_alerts(StubEngine(report), now=NOW))


def test_config_changed():
    report = make_report([])
    report["model_current"] = False
    assert "CONFIG_CHANGED" in kinds(forward_alerts(StubEngine(report), now=NOW))


def test_rolling_metrics():
    trades = [{"net_pnl": p, "exit_at": (NOW + timedelta(days=i)).isoformat()}
              for i, p in enumerate([10, -5, 10, -5] * 10)]
    metrics = rolling_metrics(trades, windows=(10, 20))
    assert metrics[10]["trades"] == 10 and metrics[10]["winrate"] == 50.0
    assert metrics[10]["expectancy"] == pytest.approx(2.5) and metrics[10]["profit_factor"] == pytest.approx(2.0)
    assert metrics[10]["max_drawdown"] == pytest.approx(5.0)
    assert metrics[20]["trade_frequency_change"] == pytest.approx(0.0)
    assert rolling_metrics([])[20]["winrate"] is None
