from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pandas as pd
import pytest

from strategy_lab.daily_data import DailyPartitionStore
from strategy_lab.data_manager import DataError
from strategy_lab.forward_engine import ForwardEngine, LAB_SOURCE, MODEL
from strategy_lab.forward_performance import _performance
from strategy_lab.forward_registry import ForwardRegistry
from strategy_lab import server


DAY = date(2026, 10, 19)
DAY2 = DAY + timedelta(days=1)
DAY3 = DAY2 + timedelta(days=1)
FROZEN = datetime(2026, 10, 19, 11, tzinfo=timezone.utc)
SOURCE = Path(__file__).resolve().parents[2] / "runtime" / "strategies" / LAB_SOURCE


def observed(day):
    return datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)


def publish(store, symbol, day, rows, *, observed_at=None):
    index = pd.date_range(f"{day}T12:00Z", periods=len(rows), freq="min")
    bid = pd.DataFrame([(*row, 1.0) for row in rows],
                       columns=["open", "high", "low", "close", "volume"], index=index, dtype=float)
    ask = bid.copy()
    ask.iloc[:, :4] += 0.2
    return store.ingest(symbol, day, bid, ask, expected_minutes=index, observed_at=observed_at or observed(day))


def setup(path, symbols=("XAUUSD", "EURUSD"), *, order_mode=0, data_version="fixture"):
    store = DailyPartitionStore(path)
    registry = ForwardRegistry(store)
    engine = ForwardEngine(registry)
    portfolio = engine.create("Fixture", currency="USD", starting_capital=1000, risk_limit=0.002)
    ids = []
    for symbol in symbols:
        config = {
            "strategy": "LabBreakout", "symbol": symbol, "timeframe": "M1",
            "execution_model": MODEL, "data_version": data_version, "starting_capital": 500,
            "trade_risk": 0.01, "portfolio_risk_limit": 0.002, "news_filter": {"enabled": False},
            "parameters": {"order_mode": order_mode, "entry_interval": 1, "position_size": 1,
                           "ema_period": 2, "max_bar_range": 25, "stop_distance": 1,
                           "target_distance": 3, "stop_offset": 0.1, "pending_expiry_bars": 2},
            "indicators": {}, "contract": {"account_currency": "USD", "contract_size": 1,
                                          "tick_size": 0.01, "quote_to_account": 1,
                                          "volume_step": 0.01, "slippage_points": 1,
                                          "commission_per_lot_side": 0.1},
        }
        registration = registry.freeze(config, {LAB_SOURCE: SOURCE.read_bytes()}, frozen_at=FROZEN)
        engine.enroll(portfolio, registration["id"], joined_at=FROZEN)
        ids.append(registration["id"])
    return store, engine, portfolio, ids


def test_forward_lifecycle_waits_for_data_and_survives_restart(tmp_path, monkeypatch):
    path = tmp_path / "forward.sqlite"
    store, engine, portfolio, ids = setup(path)
    rising = [(100, 100.2, 99.8, 100), (101, 101.2, 100.8, 101)]
    publish(store, "XAUUSD", DAY, rising)
    waiting = engine.advance(portfolio, as_of=observed(DAY))
    assert waiting["missing_symbols"] == ["EURUSD"]
    assert engine.report(portfolio)["days"] == []
    publish(store, "EURUSD", DAY, rising)
    assert engine.advance(portfolio, as_of=observed(DAY))["committed_days"] == [DAY.isoformat()]
    before = engine.report(portfolio)
    assert [event["kind"] for event in before["events"]] == ["ORDER_SUBMITTED"] * 2
    assert all(member["state"]["pending"] for member in before["members"])

    engine = ForwardEngine(ForwardRegistry(DailyPartitionStore(path)))
    assert engine.report(portfolio) == before
    for symbol in ("XAUUSD", "EURUSD"):
        publish(store, symbol, DAY2, [(102, 102.4, 101.8, 102)])
    assert engine.advance(portfolio, as_of=observed(DAY2))["committed_days"] == [DAY2.isoformat()]
    midway = engine.report(portfolio)
    monkeypatch.setattr(server, "DEFAULT_DAILY_STORE", path)
    client = TestClient(server.app)
    route = f"/api/forward/portfolios/{portfolio}"
    running = client.get(route).json()["performance"]
    assert running["portfolio"]["gross_open_exposure"] == pytest.approx(102)
    assert running["portfolio"]["open_margin"] == pytest.approx(102.21)
    assert running["portfolio"]["open_stop_risk"] == pytest.approx(1.21)
    assert running["portfolio"]["equity"] == pytest.approx(999.59)
    assert sum(member["risk_rejections"] for member in running["members"].values()) == 1
    assert sum(bool(member["state"]["position"]) for member in midway["members"]) == 1
    assert [event["kind"] for event in midway["events"][-3:]] == [
        "ORDER_FILLED", "ORDER_REJECTED", "ORDER_SUBMITTED",
    ]
    assert next(event for event in midway["events"] if event["kind"] == "ORDER_REJECTED")["detail"]["reason"] == "RISK_LIMIT"
    fill = next(event for event in midway["events"] if event["kind"] == "ORDER_FILLED")
    assert fill["detail"]["entry"] == pytest.approx(102.21)
    assert fill["detail"]["quote_observed_at"] == observed(DAY2).isoformat(timespec="microseconds")

    engine = ForwardEngine(ForwardRegistry(DailyPartitionStore(path)))
    assert engine.report(portfolio) == midway
    for symbol in ("XAUUSD", "EURUSD"):
        publish(store, symbol, DAY3, [(101, 106, 100, 101)])
    engine.advance(portfolio, as_of=observed(DAY3))
    after = engine.report(portfolio)
    exit_event = next(event for event in after["events"] if event["kind"] == "POSITION_CLOSED")
    assert exit_event["detail"]["exit_reason"] == "STOP_LOSS"
    assert exit_event["detail"]["ambiguous"]
    assert exit_event["detail"]["net_pnl"] == pytest.approx(-1.42)
    assert after["portfolio"]["balance"] == pytest.approx(998.58)
    report = client.get(route).json()["performance"]
    total = report["portfolio"]
    assert total["trade_count"] == 1
    assert total["sample_days"] == 3
    assert total["net_profit"] == pytest.approx(-1.42)
    assert total["max_drawdown_percent"] == pytest.approx(0.142)
    assert total["average_reward_to_risk"] == pytest.approx(3)
    assert total["curve"][-1]["equity"] == pytest.approx(998.58)
    assert total["open_stop_risk"] == 0
    assert total["excursion_covered"] == 1
    assert total["average_mae"] == pytest.approx(0.41)
    assert total["average_mfe"] == pytest.approx(0.19)
    assert report["trade_excursions"][str(exit_event["id"])] == {
        "status": "AVAILABLE", "bars": 1, "mae": pytest.approx(0.41), "mfe": pytest.approx(0.19),
    }
    assert sum(member["net_profit"] for member in report["members"].values()) == pytest.approx(-1.42)
    assert engine.advance(portfolio, as_of=observed(DAY3))["committed_days"] == []
    assert engine.report(portfolio) == after
    assert client.get(route).json()["performance"] == report
    symbol = next(member["configuration"]["symbol"] for member in after["members"]
                  if member["registration_id"] == exit_event["registration_id"])
    revision = publish(store, symbol, DAY2, [(102, 105, 100, 102)], observed_at=observed(DAY3))
    assert revision["revision_detected"]
    assert client.get(route).json()["performance"] == report

    original_read = DailyPartitionStore.read
    def unreadable(self, symbol, first_day, last_day, **kwargs):
        if first_day == DAY2:
            raise DataError("Checksum mismatch in fixture")
        return original_read(self, symbol, first_day, last_day, **kwargs)
    with monkeypatch.context() as invalid:
        invalid.setattr(DailyPartitionStore, "read", unreadable)
        degraded = client.get(route).json()["performance"]
    assert degraded["portfolio"]["net_profit"] == total["net_profit"]
    assert degraded["portfolio"]["average_mae"] is None
    assert degraded["portfolio"]["excursion_unverified"] == 1
    assert degraded["trade_excursions"][str(exit_event["id"])]["status"] == "PARTITION_UNVERIFIED"

    third = engine.registry.get(ids[0])
    extra = engine.registry.freeze(third["configuration"], third["sources"], frozen_at=observed(DAY3))
    with pytest.raises(DataError, match="allocations exceed"):
        engine.enroll(portfolio, extra["id"], joined_at=observed(DAY3))


@pytest.mark.parametrize("held", [True, False])
def test_forward_excursion_excludes_short_stop_fill_and_exit_bars(tmp_path, monkeypatch, held):
    path = tmp_path / "forward.sqlite"
    store, engine, portfolio, _ = setup(path, ("XAUUSD",), order_mode=2)
    rows = [(100, 100.1, 99.9, 100), (99, 99.2, 98.8, 99)]
    if held:
        rows.extend([(98.8, 99.3, 98.1, 98.5), (98.4, 98.55, 98.15, 98.3)])
    rows.append((97, 100, 95, 97))
    publish(store, "XAUUSD", DAY, rows)
    engine.advance(portfolio, as_of=observed(DAY))
    monkeypatch.setattr(server, "DEFAULT_DAILY_STORE", path)
    response = TestClient(server.app).get(f"/api/forward/portfolios/{portfolio}")
    assert response.status_code == 200
    report = response.json()["performance"]
    excursion, = report["trade_excursions"].values()
    if held:
        assert excursion == {"status": "AVAILABLE", "bars": 1,
                             "mae": pytest.approx(0.06), "mfe": pytest.approx(0.34)}
    else:
        assert excursion == {"status": "NO_OBSERVABLE_BARS"}
        assert report["portfolio"]["average_mfe"] is None


def test_failed_commit_rolls_back_orders_and_progress(tmp_path):
    path = tmp_path / "forward.sqlite"
    store, engine, portfolio, _ = setup(path, ("XAUUSD",))
    publish(store, "XAUUSD", DAY, [(100, 100.2, 99.8, 100), (101, 101.2, 100.8, 101)])
    original = engine.report(portfolio)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""CREATE TRIGGER fixture_abort BEFORE INSERT ON forward_days
                      BEGIN SELECT RAISE(ABORT, 'fixture abort'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="fixture abort"):
        engine.advance(portfolio, as_of=observed(DAY))
    assert engine.report(portfolio) == original
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TRIGGER fixture_abort")
    assert engine.advance(portfolio, as_of=observed(DAY))["committed_days"] == [DAY.isoformat()]
    assert len(engine.report(portfolio)["events"]) == 1


def test_local_api_freezes_observed_data_and_enrolls_without_replaying_history(tmp_path, monkeypatch):
    path = tmp_path / "forward.sqlite"
    store, engine, fixture_portfolio, ids = setup(path, ("XAUUSD",))
    publish(store, "XAUUSD", DAY, [(100, 100.2, 99.8, 100)])
    monkeypatch.setattr(server, "DEFAULT_DAILY_STORE", path)
    monkeypatch.setattr(server, "DATA", SimpleNamespace(ensure=lambda symbol: SimpleNamespace(manifest={
        "bid_source": {"sha256": "fixture-bid"}, "ask_source": {"sha256": "fixture-ask"},
        "last_utc": "2026-10-18T12:00:00+00:00",
    })))
    client = TestClient(server.app)
    config = engine.registry.get(ids[0])["configuration"]
    created = client.post("/api/forward/portfolios", json={
        "name": "API fixture", "currency": "USD", "starting_capital": 1000, "risk_limit": 0.002,
    })
    assert created.status_code == 201
    portfolio = created.json()["id"]
    assert {entry["id"] for entry in client.get("/api/forward/portfolios").json()["portfolios"]} == {
        fixture_portfolio, portfolio,
    }
    frozen = client.post("/api/forward/registrations", json={
        key: config[key] for key in ("symbol", "parameters", "contract", "starting_capital", "trade_risk",
                                     "portfolio_risk_limit")
    })
    assert frozen.status_code == 201, frozen.text
    record = frozen.json()
    assert record["configuration"]["data_version"]["daily_watermark"]["day"] == DAY.isoformat()
    assert "sources" not in record
    route = f"/api/forward/portfolios/{portfolio}"
    assert client.post(route + "/members", json={"registration_id": record["id"]}).status_code == 201
    assert client.post(route + "/advance", json={}).json()["committed_days"] == []
    report = client.get(route).json()
    assert report["mode"] == "DAILY_DELAYED_FORWARD"
    assert report["members"][0]["snapshot_sha256"] == record["snapshot_sha256"]
    assert report["events"] == []
    performance = report["performance"]
    member = performance["members"][record["id"]]
    assert member["status"] == "AWAITING_FIRST_CANDLE"
    assert member["sharpe_daily"] is None
    assert performance["portfolio"]["unallocated_capital"] == 500


def test_forward_ratios_use_day_series_and_preserve_undefined_values():
    days = [("2026-10-19", 110.0), ("2026-10-20", 99.0), ("2026-10-21", 110.0)]
    trades = [{"net_pnl": net, "entry": 100, "target": 104, "stop": 98} for net in (10, -11, 11)]
    metrics, curve = _performance(100, days, trades)
    assert metrics["net_profit"] == 10
    assert metrics["profit_factor"] == pytest.approx(21 / 11)
    assert metrics["recovery_factor"] == pytest.approx(10 / 11)
    assert metrics["max_drawdown_percent"] == 10
    assert metrics["max_underwater_days"] == 1
    assert metrics["winrate_percent"] == pytest.approx(200 / 3)
    assert metrics["sharpe_daily"] == pytest.approx(0.311740, abs=0.000001)
    assert metrics["sortino_daily"] == pytest.approx(0.641500, abs=0.000001)
    assert curve[-1]["drawdown_percent"] == 0
    single_day, _ = _performance(100, days[:1], trades[:1])
    assert single_day["sharpe_daily"] is None
    assert single_day["sortino_daily"] is None
    assert single_day["profit_factor"] is None


def test_historical_reference_uses_frozen_engine_without_changing_forward_history(tmp_path, monkeypatch):
    path = tmp_path / "forward.sqlite"
    version = {"legacy_bid_sha256": "fixture-bid", "legacy_ask_sha256": "fixture-ask"}
    store, engine, portfolio, (registration,) = setup(path, ("XAUUSD",), data_version=version)
    first, last = DAY - timedelta(days=3), DAY - timedelta(days=1)
    index = [pd.Timestamp(f"{day}T12:0{minute}Z") for day in (first, last) for minute in (0, 1)]
    bid = pd.DataFrame([(100, 100.2, 99.8, 100, 1), (101, 101.2, 100.8, 101, 1),
                        (102, 102.4, 101.8, 102, 1), (101, 106, 100, 101, 1)],
                       index=index, columns=["open", "high", "low", "close", "volume"], dtype=float)
    ask = bid.copy()
    ask.iloc[:, :4] += 0.2
    frame = bid.add_prefix("bid_").join(ask.add_prefix("ask_"))
    dataset = SimpleNamespace(frame=frame, manifest={
        "bid_source": {"sha256": "fixture-bid"}, "ask_source": {"sha256": "fixture-ask"},
    })
    monkeypatch.setattr(server, "DATA", SimpleNamespace(ensure=lambda symbol: dataset))
    monkeypatch.setattr(server, "DEFAULT_DAILY_STORE", path)
    client = TestClient(server.app)
    original = engine.report(portfolio)
    route = f"/api/forward/portfolios/{portfolio}/members/{registration}/historical-reference"
    inputs = {"start_day": first.isoformat(), "end_day": last.isoformat()}
    assert client.post(route, json={**inputs, "end_day": DAY.isoformat()}).status_code == 422
    dataset.manifest["bid_source"]["sha256"] = "different"
    assert client.post(route, json=inputs).status_code == 422
    dataset.manifest["bid_source"]["sha256"] = "fixture-bid"
    response = client.post(route, json=inputs)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["mode"] == "HISTORICAL_BACKTEST"
    assert result["snapshot_sha256"] == engine.registry.get(registration)["snapshot_sha256"]
    assert result["quote_count"] == 4
    assert result["minutes_without_quotes"] == 3 * 1440 - 4
    assert result["days_without_quotes"] == [(first + timedelta(days=1)).isoformat()]
    assert set(result["partition_sha256"]) == {first.isoformat(), last.isoformat()}
    assert result["metrics"]["trade_count"] == 1
    assert result["metrics"]["net_profit"] == pytest.approx(-1.42)
    assert result["metrics"]["average_mae"] == pytest.approx(0.41)
    assert client.post(route, json=inputs).json() == result
    assert client.get(f"/api/forward/portfolios/{portfolio}").json()["historical_references"] == [result]
    assert engine.report(portfolio) == original
    assert store.watermark("XAUUSD", as_of=observed(DAY)) is None
