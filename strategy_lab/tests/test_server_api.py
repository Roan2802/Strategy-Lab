import csv
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from strategy_lab import server
from strategy_lab.data_manager import DataManager


def _write_pair(source: Path, symbol: str, rows: list[list], side: str):
    suffix = f"{symbol}_1 Min_{side}_2000.01.01_2030.01.01.csv"
    with (source / suffix).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Time (EET)", "Open", "High", "Low", "Close", "TickVolume"])
        writer.writerows(rows)


def _make_pair(source: Path, symbol: str, rows: list[list]):
    _write_pair(source, symbol, rows, "Bid")
    ask = [[row[0], *[float(value) + 0.1 for value in row[1:5]], row[5]] for row in rows]
    _write_pair(source, symbol, ask, "Ask")


def test_causal_replay_classifies_forced_prefix_close_separately_from_strategy_exit():
    teardown = {"event_type": "POSITION_CLOSE", "timestamp_ms": 1000,
                "trade_id": "forced", "exit_reason": "None"}
    decision = {"state": {"position_open": True}}

    lifecycle, artifacts = server._split_prefix_teardown_events([teardown], [], decision)

    assert lifecycle == []
    assert len(artifacts) == 1
    assert artifacts[0]["classification"] == "JESSE_PREFIX_TEARDOWN"
    assert "not a strategy exit" in artifacts[0]["note"]


def test_causal_replay_keeps_a_close_recorded_at_the_same_timestamp():
    close = {"event_type": "POSITION_CLOSE", "timestamp_ms": 1000,
             "trade_id": "actual", "exit_reason": "TAKE_PROFIT"}
    decision = {"state": {"position_open": True}}

    lifecycle, artifacts = server._split_prefix_teardown_events([close], [close], decision)

    assert lifecycle == [close]
    assert artifacts == []


def test_causal_replay_excludes_prefix_end_close_from_cursor_trade_count():
    closed, total = server._closed_trade_counts_at_cursor([
        {"id": "open-at-cursor", "opened_at": 100, "closed_at": 300},
        {"id": "closed-before-cursor", "opened_at": 100, "closed_at": 200},
    ], 200)
    assert closed == 1
    assert total == 2


def test_dashboard_favicon_is_served_from_project_icon():
    response = TestClient(server.app).get("/assets/dukascopy-strategy-lab.ico")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/x-icon")
    assert response.content.startswith(b"\x00\x00\x01\x00")


def test_runtime_build_endpoint_and_index_asset_versions_match_disk():
    client = TestClient(server.app)
    version = client.get("/api/version").json()
    health = client.get("/api/health").json()
    page = client.get("/")

    assert version["api_version"] == server.API_VERSION
    assert version["backend_build_id"] == version["backend_disk_build_id"]
    assert version["backend_current"] is True
    assert version["frontend_build_id"]
    assert version["active_runs"] == 0 and version["safe_to_restart"] is True
    assert health["backend_build_id"] == version["backend_build_id"]
    assert page.headers["cache-control"] == "no-store, max-age=0"
    assert f'name="strategy-lab-frontend-build" content="{version["frontend_build_id"]}"' in page.text
    assert f"/assets/app.js?v={version['frontend_build_id']}" in page.text
    assert f"/assets/app.css?v={version['frontend_build_id']}" in page.text
    matched = client.get("/api/version", params={"frontend_build_id": version["frontend_build_id"]}).json()
    stale = client.get("/api/version", params={"frontend_build_id": "stale-build"}).json()
    assert matched["frontend_matches"] is True
    assert stale["frontend_matches"] is False


def test_runtime_build_endpoint_detects_server_code_changed_after_start(monkeypatch):
    monkeypatch.setattr(server, "LOADED_BACKEND_BUILD_ID", "old-server-build")
    payload = TestClient(server.app).get("/api/version").json()
    assert payload["backend_build_id"] == "old-server-build"
    assert payload["backend_disk_build_id"] != "old-server-build"
    assert payload["backend_current"] is False


def test_streamed_events_are_read_from_replay_prefix_without_loading_legacy_json(tmp_path, monkeypatch):
    run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    result_root = tmp_path / "results"
    run_dir = result_root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        '{"event_type":"CANDLE_DECISION","timestamp_ms":100,"decision":"PASS"}\n'
        'not-json\n'
        '{"event_type":"POSITION_OPEN","timestamp_ms":200,"trade_id":"t1"}\n',
        encoding="utf-8")
    monkeypatch.setattr(server, "RESULT_ROOT", result_root)
    monkeypatch.setattr(server, "_load_run", lambda _run_id: pytest.fail("legacy result should not load"))

    assert list(server._iter_events(run_id, until_ms=100)) == [
        {"event_type": "CANDLE_DECISION", "timestamp_ms": 100, "decision": "PASS"}]
    assert len(list(server._iter_events(run_id))) == 2


def test_run_api_adds_conservative_trade_excursions_from_saved_approved_candles(tmp_path, monkeypatch):
    run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    result_root = tmp_path / "results"
    result_dir = result_root / run_id
    result_dir.mkdir(parents=True)
    local_runs = tmp_path / "runs"
    local_run = local_runs / run_id
    local_run.mkdir(parents=True)
    monkeypatch.setattr(server, "RESULT_ROOT", result_root)
    monkeypatch.setattr(server, "RUNS_ROOT", local_runs)

    manifest = {"source_root": str(server.DATA.source_root.resolve())}
    (result_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = {"trades": [
        {"id": "long", "type": "long", "opened_at": 0, "closed_at": 240000, "entry_price": 10.0,
         "orders": [{"type": "MARKET", "reduce_only": False, "executed_at": 0}]},
        {"id": "short", "type": "short", "opened_at": 0, "closed_at": 180000, "entry_price": 10.0,
         "orders": [{"type": "MARKET", "reduce_only": False, "executed_at": 0}]},
        {"id": "short-hold", "type": "short", "opened_at": 0, "closed_at": 60000, "entry_price": 10.0},
    ]}
    (result_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    with (local_run / "input_candles.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        writer.writerows([
            [0, 10, 11, 9, 10, 1], [60000, 10, 13, 8, 10, 1],
            [120000, 10, 12, 9, 10, 1], [180000, 10, 14, 10, 10, 1],
            [240000, 10, 99, 1, 10, 1], [300000, 10, 999, 0, 10, 1],
        ])

    payload = TestClient(server.app).get(f"/api/runs/{run_id}").json()
    trades = payload["result"]["trades"]
    assert (trades[0]["mfe_price"], trades[0]["mae_price"], trades[0]["excursion_bars"]) == (4.0, 2.0, 4)
    assert (trades[1]["mfe_price"], trades[1]["mae_price"], trades[1]["excursion_bars"]) == (2.0, 3.0, 3)
    assert trades[2]["mfe_price"] is None and trades[2]["mae_price"] is None
    summary = payload["result"]["trade_excursions"]
    assert summary["available_trades"] == 2 and summary["unavailable_trades"] == 1
    assert summary["average_mfe_price"] == 3.0 and summary["average_mae_price"] == 2.5
    assert payload["manifest"]["source_root"] == str(server.DATA.source_root.resolve())


def test_pwa_manifest_and_app_icons_are_served():
    client = TestClient(server.app)
    manifest_response = client.get("/assets/manifest.webmanifest")
    assert manifest_response.status_code == 200
    assert manifest_response.headers["content-type"].startswith("application/manifest+json")
    manifest = manifest_response.json()
    assert manifest["display"] == "standalone"
    assert manifest["id"] == manifest["start_url"] == manifest["scope"] == "/"
    assert {icon["sizes"] for icon in manifest["icons"]} == {"192x192", "512x512"}
    for name in ("dukascopy-strategy-lab-192.png", "dukascopy-strategy-lab-512.png"):
        response = client.get(f"/assets/{name}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_tpe_resume_is_rejected_before_creating_a_new_run(tmp_path, monkeypatch):
    run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    result_root = tmp_path / "results"
    prior_dir = result_root / run_id
    prior_dir.mkdir(parents=True)
    request_root = tmp_path / "requests"
    data_manifest = {"source_root": r"C:\AI\Dukascopy_data", "source_hash": "fixture-only"}
    monkeypatch.setattr(server, "RESULT_ROOT", result_root)
    monkeypatch.setattr(server, "REQUEST_ROOT", request_root)
    monkeypatch.setattr(server, "_strategies", lambda: [{
        "name": "Probe", "sha256": "strategy-hash",
        "parameters": [{"name": "period", "type": "int", "min": 1, "max": 2, "default": 1}],
    }])

    class FixtureData:
        def candles(self, *_args):
            index = server.pd.date_range("2026-09-14", periods=2, freq="min", tz="UTC")
            frame = server.pd.DataFrame({
                "bid_open": [10.0, 10.0], "bid_high": [11.0, 11.0],
                "bid_low": [9.0, 9.0], "bid_close": [10.0, 10.0], "bid_volume": [1, 1],
            }, index=index)
            return frame, data_manifest

    monkeypatch.setattr(server, "DATA", FixtureData())
    monkeypatch.setattr(server.subprocess, "run", lambda *_a, **_k: pytest.fail("runner must not start"))
    start_date, end_date = "2026-09-14", "2026-09-15"
    prior_manifest = {
        "action": "optimize", "symbol": "XAUUSD", "timeframe": "M1",
        "start_date": start_date, "end_date": end_date, "strategy": "Probe",
        "strategy_sha256": "strategy-hash", "starting_balance": 10000.0,
        "fee": 0.0006, "account_type": "spot", "leverage": 1.0,
        "optimizer_method": "tpe", "seed": 42, "train_percent": 80.0,
        "dataset_sha256": server.dataset_digest(data_manifest, "M1", start_date, end_date),
    }
    (prior_dir / "manifest.json").write_text(json.dumps(prior_manifest), encoding="utf-8")
    (prior_dir / "result.json").write_text(json.dumps({"all_trials": []}), encoding="utf-8")
    request = server.RunRequest(
        action="optimize", symbol="XAUUSD", strategy="Probe", start_date=start_date,
        end_date=end_date, optimizer_method="tpe", resume_run_id=run_id,
    )

    with pytest.raises(HTTPException) as error:
        server.create_run(request)

    assert error.value.status_code == 422
    assert error.value.detail == "TPE resume is unavailable because Jesse's Optuna study is not persisted"
    assert not request_root.exists()


def test_dynamic_symbol_discovery_only_accepts_bid_ask_m1_pairs(tmp_path, monkeypatch):
    source, cache = tmp_path / "dukascopy", tmp_path / "cache"
    source.mkdir()
    rows = [["2026.09.14 12:00:00", 10, 11, 9, 10.5, 3]]
    _make_pair(source, "EURUSD", rows)
    _make_pair(source, "DEUIDXEUR", rows)
    _write_pair(source, "AUDUSD", rows, "Bid")
    (source / "EURUSD_Ticks_2026.csv").write_text("ignored", encoding="utf-8")
    manager = DataManager(source, cache)
    found = {item["symbol"]: item["source_symbol"] for item in manager.available_symbols()}
    assert found == {"DE40": "DEUIDXEUR", "EURUSD": "EURUSD"}
    monkeypatch.setattr(server, "DATA", manager)
    response = TestClient(server.app).get("/api/datasets")
    assert response.status_code == 200
    assert [item["symbol"] for item in response.json()["datasets"]] == ["DE40", "EURUSD"]


def test_chart_api_treats_selected_end_date_as_inclusive(tmp_path, monkeypatch):
    source, cache = tmp_path / "dukascopy", tmp_path / "cache"
    source.mkdir()
    rows = [
        ["2026.09.14 12:00:00", 10, 11, 9, 10.5, 3],
        ["2026.09.14 12:01:00", 10.5, 12, 10, 11.5, 4],
        ["2026.09.15 03:00:00", 11.5, 12, 11, 11.8, 2],
    ]
    _make_pair(source, "XAUUSD", rows)
    monkeypatch.setattr(server, "DATA", DataManager(source, cache))
    response = TestClient(server.app).get(
        "/api/candles", params={"symbol": "XAUUSD", "timeframe": "M1", "start": "2026-09-14",
                                "end": "2026-09-14", "limit": 100}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["available_rows"] == 2
    assert {row["timestamp"][:10] for row in payload["candles"]} == {"2026-09-14"}


def test_chart_api_limit_zero_returns_all_selected_candles(tmp_path, monkeypatch):
    source, cache = tmp_path / "dukascopy", tmp_path / "cache"
    source.mkdir()
    timestamps = server.pd.date_range("2026-09-14", periods=10_000, freq="min")
    rows = [[timestamp.strftime("%Y.%m.%d %H:%M:%S"), minute, minute + 1, minute - 1, minute + 0.5, 3]
            for minute, timestamp in enumerate(timestamps)]
    _make_pair(source, "XAUUSD", rows)
    monkeypatch.setattr(server, "DATA", DataManager(source, cache))
    response = TestClient(server.app).get(
        "/api/candles", params={"symbol": "XAUUSD", "timeframe": "M1", "start": "2026-09-14",
                                "end": "2026-09-24", "limit": 0}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["available_rows"] > 8_000
    assert len(payload["candles"]) == payload["available_rows"]


def test_inspector_excludes_future_events_even_when_signal_timestamp_matches(tmp_path, monkeypatch):
    source, cache = tmp_path / "dukascopy", tmp_path / "cache"
    source.mkdir()
    _make_pair(source, "XAUUSD", [["2026.09.14 12:00:00", 10, 11, 9, 10.5, 3]])
    monkeypatch.setattr(server, "DATA", DataManager(source, cache))
    run_id = "11111111-1111-4111-8111-111111111111"
    result_root = tmp_path / "results"
    run_dir = result_root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({"jesse_symbol": "XAU-USD"}), encoding="utf-8")
    monkeypatch.setattr(server, "RESULT_ROOT", result_root)
    stamp = int(server.pd.Timestamp("2026-09-14T09:00:00Z").timestamp() * 1000)
    monkeypatch.setattr(server, "_load_run", lambda _run_id: {"events": [
        {"event_type": "ORDER_SUBMITTED", "event_id": "submit", "timestamp_ms": stamp,
         "signal_timestamp_ms": stamp},
        {"event_type": "POSITION_OPEN", "event_id": "open", "timestamp_ms": stamp + 60_000,
         "signal_timestamp_ms": stamp},
        {"event_type": "ORDER_SUBMITTED", "event_id": "future-fill", "timestamp_ms": stamp + 60_000,
         "signal_timestamp_ms": stamp},
    ]})
    response = TestClient(server.app).get("/api/inspect", params={
        "symbol": "XAUUSD", "timeframe": "M1", "timestamp_ms": stamp, "run_id": run_id})
    assert response.status_code == 200
    assert [event["event_id"] for event in response.json()["events"]] == ["submit"]


def test_replay_api_returns_exact_recorded_state_without_future_snapshot(tmp_path, monkeypatch):
    source, cache = tmp_path / "dukascopy", tmp_path / "cache"
    source.mkdir()
    _make_pair(source, "XAUUSD", [
        ["2026.09.14 12:00:00", 10, 11, 9, 10.5, 3],
        ["2026.09.14 12:01:00", 10.5, 12, 10, 11.5, 4],
        ["2026.09.14 12:02:00", 11.5, 12, 11, 11.8, 2],
    ])
    monkeypatch.setattr(server, "DATA", DataManager(source, cache))
    run_id = "22222222-2222-4222-8222-222222222222"
    result_root = tmp_path / "results"
    run_dir = result_root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({"jesse_symbol": "XAUUSD"}), encoding="utf-8")
    monkeypatch.setattr(server, "RESULT_ROOT", result_root)
    visible_ts = int(server.pd.Timestamp("2026-09-14T09:01:00Z").timestamp() * 1000)
    future_ts = visible_ts + 60_000
    monkeypatch.setattr(server, "_load_run", lambda _run_id: {"events": [
        {"event_type": "CANDLE_DECISION", "timestamp_ms": visible_ts, "symbol": "XAUUSD",
         "timeframe": "M1", "strategy": "Probe", "decision": "PASS", "reason": "OK",
         "checks": [{"name": "close", "result": "PASS"}], "state": {"ema": 10.8}},
        {"event_type": "CANDLE_DECISION", "timestamp_ms": future_ts, "symbol": "XAUUSD",
         "timeframe": "M1", "strategy": "Probe", "decision": "FAIL", "reason": "FUTURE",
         "checks": [], "state": {"ema": 999}},
    ]})
    response = TestClient(server.app).get("/api/replay", params={
        "symbol": "XAUUSD", "timeframe": "M1", "start": "2026-09-14", "end": "2026-09-14",
        "cursor_ms": visible_ts, "run_id": run_id})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["candles"]) == 2 and payload["future_candles_returned"] is False
    snapshot = payload["state_snapshot"]
    assert snapshot["available"] and snapshot["decision"] == "PASS"
    assert snapshot["state"] == {"ema": 10.8}
    assert snapshot["timestamp_ms"] == visible_ts
    assert payload["replay_mode"] == "RECORDED_EVENT_REPLAY"


def test_reconcile_lifecycle_timestamps_from_exact_jesse_order_ids():
    result = {"trades": [{"id": "t1", "orders": [
        {"id": "entry", "executed_at": 120_000},
        {"id": "exit", "executed_at": 180_000},
    ]}], "events": [
        {"event_type": "POSITION_OPEN", "event_id": "open", "order_id": "entry",
         "timestamp_ms": 60_000, "signal_timestamp_ms": 0},
        {"event_type": "POSITION_CLOSE", "event_id": "close", "order_id": "exit",
         "trade_id": "t1", "timestamp_ms": 120_000},
        {"event_type": "POSITION_CLOSE", "event_id": "unknown", "order_id": "missing",
         "trade_id": "t1", "timestamp_ms": 120_000},
    ]}
    counts = server._reconcile_lifecycle_fill_timestamps(result)
    assert counts == {"POSITION_OPEN": 1, "POSITION_CLOSE": 1, "unmatched": 1}
    assert result["events"][0]["timestamp_ms"] == 120_000
    assert result["events"][0]["signal_timestamp_ms"] == 0
    assert result["events"][0]["strategy_observed_timestamp_ms"] == 60_000
    assert result["events"][1]["timestamp_ms"] == 180_000
    assert result["events"][2]["timestamp_ms"] == 120_000


def test_lab_strategy_discovery_includes_categorical_parameter():
    result = server._strategies()
    strategy = next(row for row in result if row["name"] == "LabBreakout")
    order_mode = next(param for param in strategy["parameters"] if param["name"] == "order_mode")
    assert {key: order_mode[key] for key in ("name", "type", "options", "default")} == {
        "name": "order_mode", "type": "categorical", "options": [0, 1, 2], "default": 0}


def test_lab_metadata_adds_human_labels_without_changing_engine_values():
    strategy = next(row for row in server._strategies() if row["name"] == "LabBreakout")
    mode = next(param for param in strategy["parameters"] if param["name"] == "order_mode")
    assert mode["options"] == [0, 1, 2] and mode["default"] == 0
    assert mode["account_options"] == {"spot": [0, 1], "futures": [0, 1, 2]}
    assert mode["option_labels"]["1"] == "Long stop boven candle-high"
    assert set(mode["option_details"]) == {"0", "1", "2"}
    assert "kies Futures" in mode["option_details"]["2"]["limitations"]
    assert mode["simple"] is True
    assert next(p for p in strategy["parameters"] if p["name"] == "stop_distance")["simple"] is False


def test_layout_store_persists_valid_layout_and_rejects_unknown_panels(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LAYOUT_ROOT", tmp_path)
    monkeypatch.setattr(server, "LAYOUT_FILE", tmp_path / "layouts.json")
    client = TestClient(server.app)
    panels = ["market-controls", "settings", "chart", "inspector", "results", "trades", "analytics",
              "optimization-settings", "optimization-results", "portfolio", "comparison", "logs"]
    layout = {"panels": panels, "sizes": {"chart": {"columns": 2, "rows": 8}, "market-controls": {"columns": 12, "rows": 1}},
              "positions": {"market-controls": {"column": 1, "row": 1}, "chart": {"column": 4, "row": 3}},
              "widths": {"settings": 300, "inspector": 320}, "hidden": ["logs"]}
    response = client.post("/api/layouts", json={"name": "Research", "layout": layout})
    assert response.status_code == 200
    assert client.get("/api/layouts").json()["layouts"]["Research"] == layout
    invalid = {**layout, "panels": ["settings", "chart", "inspector", "evil"]}
    assert client.post("/api/layouts", json={"name": "Bad", "layout": invalid}).status_code == 422
    invalid_height = {**layout, "heights": {"chart": 5000}}
    assert client.post("/api/layouts", json={"name": "Bad height", "layout": invalid_height}).status_code == 422
    invalid_size = {**layout, "sizes": {"chart": {"columns": 99, "rows": 8}}}
    assert client.post("/api/layouts", json={"name": "Bad size", "layout": invalid_size}).status_code == 422
    invalid_position = {**layout, "positions": {"chart": {"column": 12, "row": 3}}}
    assert client.post("/api/layouts", json={"name": "Bad position", "layout": invalid_position}).status_code == 422
    out_of_range_position = {**layout, "positions": {"chart": {"column": 4, "row": 41}}}
    assert client.post("/api/layouts", json={"name": "Too far down", "layout": out_of_range_position}).status_code == 422
    undersized_chart = {**layout, "heights": {"chart": 479}}
    assert client.post("/api/layouts", json={"name": "Undersized chart", "layout": undersized_chart}).status_code == 422


def test_corrupt_layout_store_recovers_empty(tmp_path, monkeypatch):
    path = tmp_path / "layouts.json"
    path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(server, "LAYOUT_FILE", path)
    assert server.get_layouts() == {"version": 1, "layouts": {}, "recovered": True}
    path.write_text("[]", encoding="utf-8")
    assert server.get_layouts() == {"version": 1, "layouts": {}, "recovered": True}


def test_bidask_endpoint_reports_scope_without_claiming_execution_validation(tmp_path, monkeypatch):
    source, cache = tmp_path / "dukascopy", tmp_path / "cache"
    source.mkdir()
    rows = [["2026.09.14 12:00:00", 100, 101, 99, 100.5, 3],
            ["2026.09.14 12:01:00", 100.5, 101.5, 100, 101, 4]]
    _make_pair(source, "EURUSD", rows)
    monkeypatch.setattr(server, "DATA", DataManager(source, cache))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({
        "symbol": "EURUSD", "timeframe": "M1", "start_date": "2026-09-14",
        "end_date": "2026-09-14", "parameters": {}, "starting_balance": 10_000,
    }), encoding="utf-8")
    monkeypatch.setattr(server, "_run_dir", lambda _run_id: run_dir)
    monkeypatch.setattr(server, "_load_run", lambda _run_id: {"trades": [], "events": []})
    response = TestClient(server.app).post("/api/runs/test/bidask-ledger", json={
        "account_currency": "USD", "contract_size": 1, "tick_size": 0.0001,
        "quote_to_account": 1, "volume_step": 0.01,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "DUKASCOPY_BIDASK_REPLAY"
    assert data["contract_spec_status"] == "USER_SUPPLIED_NOT_BROKER_VERIFIED"
    assert data["strategy_signals_recomputed"] is False
    assert data["timestamp_reprice"]["status"] == "JESSE_RESEARCH_PROVISIONAL"
    assert data["independent_model"]["status"] == "DUKASCOPY_BIDASK_REPLAY"
    assert data["independent_model"]["quote_quality"] == "COMPLETE_QUOTES"
    assert data["independent_model"]["strategy_signals_recomputed"] is False


def test_missing_dukascopy_root_reports_unhealthy_and_rejects_data(tmp_path, monkeypatch):
    source = tmp_path / "unavailable-dukascopy"
    manager = DataManager(source, tmp_path / "cache")
    monkeypatch.setattr(server, "DATA", manager)
    client = TestClient(server.app)

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "error"
    assert health.json()["source_exists"] is False
    assert client.get("/api/datasets").json()["datasets"] == []
    missing_pair = client.get("/api/data/meta", params={"symbol": "XAUUSD"})
    assert missing_pair.status_code == 422
    assert "Dukascopy source directory is unavailable" in missing_pair.json()["detail"]


def test_higher_timeframe_run_feeds_one_minute_candles_to_jesse(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "RESULT_ROOT", tmp_path / "results")
    monkeypatch.setattr(server, "REQUEST_ROOT", tmp_path / "requests")
    monkeypatch.setattr(server, "_strategies", lambda: [{"name": "Probe", "sha256": "h", "parameters": []}])
    asked = []

    class FixtureData:
        def candles(self, symbol, timeframe, start, end):
            asked.append(timeframe)
            freq = {"M1": "min", "M15": "15min"}[timeframe]
            index = server.pd.date_range("2026-09-01", "2026-09-15", freq=freq, tz="UTC", inclusive="left")
            frame = server.pd.DataFrame({"bid_open": 10.0, "bid_high": 11.0, "bid_low": 9.0, "bid_close": 10.0,
                                         "bid_volume": 1}, index=index)
            return frame, {"source_root": r"C:\AI\Dukascopy_data", "source_hash": "fixture"}

    monkeypatch.setattr(server, "DATA", FixtureData())

    def stop_runner(*_a, **_k):
        raise OSError("stop before docker")

    monkeypatch.setattr(server.subprocess, "run", stop_runner)
    monkeypatch.setattr(server.subprocess, "Popen", stop_runner)
    request = server.RunRequest(action="backtest", symbol="XAUUSD", strategy="Probe", timeframe="M15",
                                start_date="2026-09-14", end_date="2026-09-14")
    with pytest.raises(HTTPException):
        server.create_run(request)
    csv = next((tmp_path / "requests").glob("*_candles.csv"))
    stamps = server.pd.read_csv(csv)["timestamp"]
    assert "M1" in asked and (stamps.diff().dropna() == 60_000).all()
