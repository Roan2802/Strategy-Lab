"""Shadow Research Portfolio (v102): separation, eligibility, prospective-only processing, safety, API.

Market data is synthetic and proves mechanics only; it is not evidence about any strategy.
"""
from datetime import timedelta
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from strategy_lab import forward_rules, server, shadow_forward
from strategy_lab.daily_data import DailyPartitionStore
from strategy_lab.data_manager import DataError
from strategy_lab.forward_engine import ForwardEngine
from strategy_lab.forward_registry import ForwardRegistry
from strategy_lab.shadow_forward import ShadowEngine, ShadowIneligible, ShadowNotSupported, _ShadowData, eligibility
from strategy_lab.tests.test_forward_rules import DAY, ENTRY_ROWS, FROZEN, flat_day, ingest, observed, path_day, spec

HOLD = [(101, 101.2, 100.9, 101.0)] * 3
WIN = ENTRY_ROWS + [(101, 101.2, 100.9, 101.0), (101.0, 103.6, 100.9, 103.5)] + [(100, 100.1, 99.9, 100)] * 3
DAY2 = DAY + timedelta(days=1)
WHY = "Experimenteel: onvoldoende bewijs, we volgen de bevroren regels prospectief."


def builder(store):
    def build(strategy, params, contract, *, capital, trade_risk, portfolio_limit, leverage, as_of):
        warm = forward_rules.warmup_from_fetch(forward_rules.store_fetch(store, strategy["symbol"]), strategy, params,
                                               as_of=as_of)
        return forward_rules.build_configuration(
            strategy, params, contract=contract, starting_capital=capital, trade_risk=trade_risk,
            portfolio_risk_limit=portfolio_limit, leverage=leverage, warmup=warm, data_version="fixture")
    return build


def shadow(tmp_path, symbols=("XAUUSD",), capital=30000):
    store = DailyPartitionStore(tmp_path / "reference.sqlite")
    for symbol in symbols:
        ingest(store, symbol, DAY - timedelta(days=1), flat_day(DAY - timedelta(days=1)))
    engine = ShadowEngine(ForwardRegistry(_ShadowData(store, tmp_path / "shadow.sqlite")))
    portfolio = engine.create_shadow("Schaduw", starting_capital=capital, risk_limit=0.05,
                                     limits={"max_positions": 5})
    return store, engine, portfolio


def register(store, engine, portfolio, strategy=None, **kw):
    return engine.register_strategy(portfolio, source={"kind": "builder", "spec": strategy or spec()},
                                    reason_nl=WHY, now=kw.pop("now", FROZEN), configuration_builder=builder(store), **kw)


def candidate(**over):
    base = {"outcome": "INSUFFICIENT_EVIDENCE", "stored_status": "INSUFFICIENT_EVIDENCE", "stage": "L6",
            "reasons_nl": ["te weinig OOS-trades; minimaal 30"], "technical": [{"name": "no_lookahead", "ok": True}],
            "rejection_kind": "onvoldoende bewijs", "duplicate": False}
    return {**base, **over}


# ----------------------------------------------------------------------------- separation

def test_shadow_store_is_a_separate_file_and_never_live_eligible(tmp_path):
    store, engine, portfolio = shadow(tmp_path)
    assert engine.registry.data.path != store.path and engine.registry.data.path.name == "shadow.sqlite"
    validated = ForwardEngine(ForwardRegistry(store))
    assert validated.portfolios() == []                                           # nothing shadow in the validated store
    row, = engine.shadow_portfolios()
    assert row["kind"] == "SHADOW_RESEARCH" and row["experimental"] is True and row["live_eligible"] is False
    with pytest.raises(DataError, match="never become LIVE_ELIGIBLE"):
        engine.promote(portfolio)
    with sqlite3.connect(engine.registry.data.path) as db:
        with pytest.raises(sqlite3.IntegrityError):                                # CHECK constraint, not just code
            db.execute("INSERT INTO shadow_portfolios VALUES ('x', 'SHADOW_RESEARCH', 1, 1, 'now')")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE shadow_portfolios SET live_eligible=1")
    with pytest.raises(DataError, match="create_shadow"):
        engine.create("x", currency="USD", starting_capital=1, risk_limit=0.1)
    plain = ForwardEngine(ForwardRegistry(_ShadowData(store, tmp_path / "shadow.sqlite")))
    stray = plain.create("Gewoon", currency="USD", starting_capital=1000, risk_limit=0.05)
    with pytest.raises(DataError, match="Unknown shadow portfolio"):
        engine.advance(stray)                                                      # not a shadow portfolio: refused


# ----------------------------------------------------------------------------- eligibility

def test_eligibility_allows_only_technically_valid_insufficient_or_statistically_rejected():
    assert eligibility(candidate())["eligible"]
    untested = eligibility(candidate(outcome="IN_PROGRESS", stored_status="TECHNICALLY_VALID", stage="L1",
                                     reasons_nl=["te weinig trades voor dit profiel (0.02/week < 0.40)"]))
    assert untested["eligible"] and untested["evidence_status"] == "TECHNICALLY_VALID_UNTESTED"
    stat = candidate(outcome="REJECTED", stored_status="REJECTED", stage="L3", rejection_kind="statistisch",
                     reasons_nl=["ontwikkelperiode verliesgevend na kosten (PF 0.75 < 0,95)"])
    assert eligibility(stat)["eligible"]
    cases = {
        "lookahead": candidate(outcome="REJECTED", stored_status="REJECTED", stage="L1", rejection_kind="technisch",
                               reasons_nl=["no_lookahead: toekomstige candles"]),
        "blocked": candidate(outcome="TECHNICAL_BLOCKED", stored_status="REJECTED", stage="L1"),
        "data": candidate(outcome="REJECTED", stored_status="REJECTED", stage="L3", rejection_kind="statistisch",
                          reasons_nl=["Ask-dekking te laag"]),
        "dup": candidate(outcome="REJECTED", stored_status="REJECTED", stage="L3", duplicate=True,
                         reasons_nl=["duplicaat van een eerdere kandidaat"]),
        "passed": candidate(outcome="PASSED", stored_status="WF_PASSED", stage="L5"),
        "running": candidate(outcome="IN_PROGRESS", stored_status="STOPPED", stage=None),
        "bad_check": candidate(technical=[{"name": "no_lookahead", "ok": False}]),
        "error": candidate(outcome="TECHNICAL_BLOCKED", stored_status="ERROR"),
    }
    for name, detail in cases.items():
        verdict = eligibility(detail)
        assert not verdict["eligible"] and verdict["problems"], name


def test_registration_refuses_ineligible_and_unsupported_sources(tmp_path, monkeypatch):
    store, engine, portfolio = shadow(tmp_path)
    for kind in ("jesse", "plugin"):
        with pytest.raises(ShadowNotSupported) as err:
            engine.register_strategy(portfolio, source={"kind": kind, "spec_name": "X"}, reason_nl=WHY)
        assert err.value.code == "NOT_SUPPORTED"
    scaled = spec(scale_in={"max_adds": 2, "add_every_r": 0.5, "add_size_fraction": 0.5})
    with pytest.raises(ShadowNotSupported, match="scale_in"):
        engine.register_strategy(portfolio, source={"kind": "builder", "spec": scaled}, reason_nl=WHY)
    with pytest.raises(ShadowIneligible) as err:
        engine.register_strategy(portfolio, source={"kind": "builder", "spec": spec(timeframe="M7")}, reason_nl=WHY)
    assert "INVALID_SPEC" in err.value.reasons[0]
    monkeypatch.setattr(shadow_forward, "_resolve_source", lambda source: (
        spec(), None, None, eligibility(candidate(outcome="TECHNICAL_BLOCKED", stored_status="ERROR"))))
    with pytest.raises(ShadowIneligible, match="TECHNICAL_BLOCKED"):
        engine.register_strategy(portfolio, source={"kind": "factory", "candidate_id": "c1"}, reason_nl=WHY)
    with pytest.raises(DataError, match="reason_nl"):
        engine.register_strategy(portfolio, source={"kind": "builder", "spec": spec()}, reason_nl=" ")
    assert engine.portfolio_view(portfolio)["strategies"] == []


# ----------------------------------------------------------------------------- registration freezes everything

def test_registration_freezes_the_full_record_and_changes_need_a_new_version(tmp_path):
    store, engine, portfolio = shadow(tmp_path)
    first = register(store, engine, portfolio)
    assert first["version"] == 1 and first["start_utc"] == FROZEN.isoformat(timespec="microseconds")
    assert first["live_eligible"] is False and first["evidence_status"] == "AWAITING_FIRST_FORWARD_DAY"
    detail = engine.strategy_view(first["strategy_id"])
    record = detail["record"]
    for key in ("spec_sha256", "code_sha256", "parameters", "indicators", "symbol", "timeframe", "cost_model",
                "execution_model", "risk_model", "data_snapshot_sha256", "start_utc", "info_cutoff_utc",
                "research_period_used", "reason_nl", "backtest_reference"):
        assert key in record, key
    assert record["reason_nl"] == WHY and record["info_cutoff_utc"] <= record["start_utc"]
    assert record["execution_model"] == forward_rules.MODEL and record["live_eligible"] is False
    assert record["risk_model"]["margin_model"]["status"] == "ASSUMED"            # labelled assumption, not a broker value
    # a changed spec is a new registration (version 2); version 1 is untouched and cannot be edited
    second = register(store, engine, portfolio, spec(stop={"type": "points", "value": 1.5}), now=FROZEN)
    assert second["version"] == 2 and second["strategy_id"] != first["strategy_id"]
    again = engine.strategy_view(first["strategy_id"])
    assert again["record_sha256"] == detail["record_sha256"] and again["record"]["version"] == 1
    assert engine.strategy_view(second["strategy_id"])["record"]["supersedes"] == first["strategy_id"]
    with sqlite3.connect(engine.registry.data.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("UPDATE shadow_strategies SET reason_nl='x'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("DELETE FROM shadow_strategies")


# ----------------------------------------------------------------------------- prospective-only processing

def test_only_candles_published_after_registration_count_and_zero_days_is_reported_honestly(tmp_path):
    store, engine, portfolio = shadow(tmp_path)
    register(store, engine, portfolio)
    result = engine.advance(portfolio, as_of=FROZEN + timedelta(hours=1))
    assert result["status"] == "UP TO DATE" and result["committed_days"] == []
    view = engine.portfolio_view(portfolio)
    assert view["prospective_days"] == 0 and view["strategies"][0]["evidence_status"] == "AWAITING_FIRST_FORWARD_DAY"
    assert view["strategies"][0]["trades"] == 0 and view["equity"] == [] and view["live_eligible"] is False
    # candles dated BEFORE the registration instant, downloaded afterwards, are never processed
    ingest(store, "XAUUSD", DAY, path_day(DAY, WIN, start="08:00"))                # 08:00-08:09 < 11:00 registration
    engine.advance(portfolio, as_of=observed(DAY))
    report = engine.report(portfolio)
    assert [d["day"] for d in report["days"]] == [DAY.isoformat()] and report["events"] == []
    detail = engine.strategy_view(engine.portfolio_view(portfolio)["strategies"][0]["id"])
    assert detail["trades"] == [] and detail["clustering"]["status"] == "NO_TRADES"
    assert detail["drift"]["status"] == "INSUFFICIENT_EVIDENCE"


def test_forward_events_carry_audit_timestamps_and_no_trade_precedes_its_data(tmp_path):
    store, engine, portfolio = shadow(tmp_path)
    reg = register(store, engine, portfolio, allocation=30000)
    ingest(store, "XAUUSD", DAY, path_day(DAY, WIN))
    engine.advance(portfolio, as_of=observed(DAY))
    detail = engine.strategy_view(reg["strategy_id"])
    assert detail["trades"], "the fixture path must produce a hypothetical trade"
    trade = detail["trades"][0]
    assert trade["status"] == "CLOSED" and trade["hypothetical"] is True
    assert trade["evidence_class"] == "DAILY_DELAYED_FORWARD"
    assert trade["fill_assumption"] == "NEXT_OPEN" and trade["side"] == "long"
    assert trade["signal_ts"] <= trade["candle_ts"] <= trade["exit_at"]
    assert trade["received_at"] == observed(DAY).isoformat(timespec="microseconds")   # when the partition was ingested
    assert trade["received_at"] > trade["exit_at"]                                     # never used before it existed
    assert trade["costs"]["spread_open"] > 0 and trade["costs"]["commission"] is not None
    assert trade["order_ts"] and trade["net_pnl"] is not None
    for event in engine.report(portfolio)["events"]:
        assert event["detail"]["received_at"] == event["detail"]["quote_observed_at"] > event["candle_at"]
    summary = detail["strategy"]
    assert summary["trades"] == 1 and summary["prospective_days"] == 1
    assert summary["evidence_status"] == "DAILY_DELAYED_FORWARD_INSUFFICIENT"
    assert any("onder" in w for w in summary["warnings"])
    assert detail["backtest_vs_forward"]["status"] == "NO_BACKTEST_REFERENCE"
    assert detail["backtest_vs_forward"]["forward"]["evidence_class"] == "DAILY_DELAYED_FORWARD"
    assert detail["clustering"]["status"] == "OK" and detail["clustering"]["trades"] == 1


# ----------------------------------------------------------------------------- safety

def test_resync_restart_and_replay_create_no_duplicate_trades_and_keep_the_chain(tmp_path):
    store, engine, portfolio = shadow(tmp_path)
    register(store, engine, portfolio, allocation=30000)
    ingest(store, "XAUUSD", DAY, path_day(DAY, WIN))
    engine.advance(portfolio, as_of=observed(DAY))
    before = [(e["id"], e["kind"], e["detail"]) for e in engine.report(portfolio)["events"]]
    head = engine.verify_ledger(portfolio)
    ingest(store, "XAUUSD", DAY, path_day(DAY, WIN))                               # identical re-download: no new revision
    restarted = ShadowEngine(ForwardRegistry(_ShadowData(store, tmp_path / "shadow.sqlite")))
    again = restarted.advance(portfolio, as_of=observed(DAY))
    assert again["committed_days"] == [] and again["status"] == "UP TO DATE"
    assert [(e["id"], e["kind"], e["detail"]) for e in restarted.report(portfolio)["events"]] == before
    assert restarted.verify_ledger(portfolio) == head and head["status"] == "OK"
    fills = [e for e in restarted.report(portfolio)["events"] if e["kind"] == "ORDER_FILLED"]
    assert len(fills) == 1                                                           # one trade, however often it is replayed
    with sqlite3.connect(restarted.registry.data.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("DELETE FROM forward_events")


def test_missing_essential_data_pauses_with_a_reason_and_commits_nothing(tmp_path):
    store, engine, portfolio = shadow(tmp_path)
    register(store, engine, portfolio)
    result = engine.advance(portfolio, as_of=observed(DAY) + timedelta(days=2))     # DAY itself was never published
    assert result["status"] == "WAITING_FOR_DATA" and result["committed_days"] == []
    view = engine.portfolio_view(portfolio)
    assert view["prospective_days"] == 0
    assert any("gepauzeerd" in w for w in view["warnings"]) and view["last_advance"]["status"] == "WAITING_FOR_DATA"


def test_missing_fx_rate_pauses_the_shadow_portfolio_without_fake_fills(tmp_path):
    from strategy_lab.research.fx import FxRateUnavailable

    class Down:
        def rate(self, *args, **kwargs):
            raise FxRateUnavailable("EURUSD: no M1 bar (missing)")

    store = DailyPartitionStore(tmp_path / "reference.sqlite")
    ingest(store, "DE40", DAY - timedelta(days=1), flat_day(DAY - timedelta(days=1)))
    engine = ShadowEngine(ForwardRegistry(_ShadowData(store, tmp_path / "shadow.sqlite")), fx=Down())
    portfolio = engine.create_shadow("FX", starting_capital=30000, risk_limit=0.05)
    reg = register(store, engine, portfolio, spec(symbol="DE40"), allocation=30000)
    ingest(store, "DE40", DAY, path_day(DAY, WIN))
    result = engine.advance(portfolio, as_of=observed(DAY))
    assert result["status"] == "PAUSED" and result["reason"] == "FX_RATE_UNAVAILABLE" and result["committed_days"] == []
    assert engine.report(portfolio)["events"] == [] and engine.report(portfolio)["days"] == []
    assert any("gepauzeerd" in w for w in engine.portfolio_view(portfolio)["warnings"])
    engine._fx = SimpleNamespace(rate=lambda *a, **k: SimpleNamespace(rate=1.1, source="TEST", data_version="t"))
    ok = engine.advance(portfolio, as_of=observed(DAY))                              # rate back: same day now commits
    assert ok["committed_days"] == [DAY.isoformat()]
    fill, = [e for e in engine.report(portfolio)["events"] if e["kind"] == "ORDER_FILLED"]
    assert fill["detail"]["fx_rate"] == 1.1 and fill["detail"]["fx_quote"] == "EUR"
    assert engine.strategy_view(reg["strategy_id"])["strategy"]["trades"] == 1


# ----------------------------------------------------------------------------- API namespace

def test_shadow_api_namespace_and_shapes(tmp_path, monkeypatch):
    store = DailyPartitionStore(tmp_path / "reference.sqlite")
    ingest(store, "XAUUSD", DAY - timedelta(days=1), flat_day(DAY - timedelta(days=1)))
    monkeypatch.setattr(server, "SHADOW_DB", tmp_path / "shadow.sqlite")
    monkeypatch.setattr(server, "DEFAULT_DAILY_STORE", store.path)
    monkeypatch.setattr(shadow_forward, "_default_configuration", lambda *a, **k: builder(store)(*a, **k))
    client = TestClient(server.app)
    assert client.get("/api/shadow/portfolios").json()["portfolios"] == []
    created = client.post("/api/shadow/portfolios", json={"name": "S", "starting_capital": 30000})
    assert created.status_code == 201 and created.json()["live_eligible"] is False
    pid = created.json()["id"]
    listed, = client.get("/api/shadow/portfolios").json()["portfolios"]
    assert listed["id"] == pid and listed["kind"] == "SHADOW_RESEARCH" and listed["experimental"] is True
    assert client.get("/api/forward/portfolios").json()["portfolios"] == []         # validated namespace stays empty
    body = {"source": {"kind": "builder", "spec_name": "NoSuchSpec"}, "reason_nl": WHY}
    assert client.post(f"/api/shadow/portfolios/{pid}/register", json=body).status_code == 404
    jesse = {"source": {"kind": "jesse", "spec_name": "X"}, "reason_nl": WHY}
    refused = client.post(f"/api/shadow/portfolios/{pid}/register", json=jesse)
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "NOT_SUPPORTED"
    monkeypatch.setattr(shadow_forward, "_resolve_source", lambda source: (
        spec(), None, None, {"eligible": True, "problems": [], "evidence_status": "UNVALIDATED_BUILDER_SPEC",
                             "reasons_nl": []}))
    ok = client.post(f"/api/shadow/portfolios/{pid}/register",
                     json={"source": {"kind": "builder", "spec_name": "Fixture"}, "reason_nl": WHY})
    assert ok.status_code == 201, ok.text
    sid = ok.json()["strategy_id"]
    view = client.get(f"/api/shadow/portfolios/{pid}").json()
    row, = view["strategies"]
    assert {"id", "name", "symbol", "timeframe", "start_utc", "trades", "net", "max_dd_pct", "pf", "last_update",
            "evidence_status", "warnings"} <= set(row)
    assert row["id"] == sid and row["evidence_status"] == "AWAITING_FIRST_FORWARD_DAY"
    assert {"equity", "daily", "accounting", "stress", "correlations", "ledger"} <= set(view)
    detail = client.get(f"/api/shadow/strategies/{sid}").json()
    assert {"equity", "daily", "trades", "clustering", "backtest_vs_forward", "drift", "warnings"} <= set(detail)
    advanced = client.post(f"/api/shadow/portfolios/{pid}/advance", json={"max_days": 3}).json()
    assert advanced["committed_days"] == [] and advanced["evidence_class"] == "DAILY_DELAYED_FORWARD"
    promote = client.post(f"/api/shadow/portfolios/{pid}/promote")
    assert promote.status_code == 409 and promote.json()["detail"]["code"] == "NEVER_LIVE_ELIGIBLE"
    assert client.get("/api/shadow/strategies/00000000-0000-0000-0000-000000000000").status_code == 404
