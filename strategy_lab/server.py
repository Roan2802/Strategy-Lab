"""Local-only FastAPI frontend and orchestration for the Python Strategy Lab."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import math
import subprocess
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field
from plotly.offline import get_plotlyjs

from .data_manager import DEFAULT_DAILY_STORE, DataError, DataManager, dataset_digest
from .daily_data import DailyPartitionStore, provider_symbol
from .daily_sync import DEFAULT_SYNC_CONFIG, SyncBusy, feed_status, load_sync_config, run_sync
from .execution import ContractSpec
from .forward_engine import ForwardEngine, LAB_SOURCE, MODEL, MODEL_SHA256, validate_registration
from .forward_performance import summarize_forward
from .forward_reference import create_reference, list_references
from .forward_registry import ForwardRegistry
from .windows_power import recent_power_events
from .replay import event_links, recorded_state_snapshot, replay_slice, reexecute_labbreakout
from .trade_excursions import add_trade_excursions
from .interpretation import interpret_run, research_status
from .portfolio import analyze_portfolio, forward_correlations
from runtime.strategy_lab.optimizer_space import normalize_optimizer_space, training_split_index


if __name__ == "__main__":
    # `python -m strategy_lab.server`: research code imports strategy_lab.server lazily; it must get this module
    # (same app, run lock and active-run state), not a second copy.
    import sys as _sys
    _sys.modules.setdefault("strategy_lab.server", _sys.modules[__name__])

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
STRATEGY_ROOT = RUNTIME / "strategies"
BRIDGE = RUNTIME / "strategy_lab"
REQUEST_ROOT = BRIDGE / "requests"
RESULT_ROOT = BRIDGE / "results"
RUNS_ROOT = Path(__file__).resolve().parent / "runs"
PRESET_ROOT = Path(__file__).resolve().parent / "presets"
LAYOUT_ROOT = Path(__file__).resolve().parent / "layouts"
LAYOUT_FILE = LAYOUT_ROOT / "layouts.json"
STATIC_ROOT = Path(__file__).resolve().parent / "static"
DATA = DataManager()
app = FastAPI(title="Dukascopy Strategy Lab", docs_url=None, redoc_url=None)
from . import research_api
research_api.configure(DATA)
app.include_router(research_api.router)
from .factory_api import router as _factory_router; app.include_router(_factory_router)  # noqa: E402,E702
from .multimarket_api import router as _mm_router; app.include_router(_mm_router)  # noqa: E402,E702
API_VERSION = "1.0.0"
SERVER_STARTED_AT = datetime.now(timezone.utc).isoformat()
ACTIVE_RUNS = 0
ACTIVE_RUNS_LOCK = threading.Lock()
RESEARCH_RUN_LOCK = threading.Lock()


def _fingerprint(paths: list[Path], namespace: str) -> str:
    digest = hashlib.sha256(namespace.encode("utf-8"))
    for path in sorted(set(paths), key=lambda item: str(item).casefold()):
        try:
            relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
            content = path.read_bytes()
        except (OSError, ValueError):
            relative, content = str(path), b"<missing>"
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def _backend_files() -> list[Path]:
    paths = [Path(__file__), Path(__file__).parent / "data_manager.py",
             Path(__file__).parent / "execution.py", Path(__file__).parent / "replay.py",
             Path(__file__).parent / "events.py", RUNTIME / "strategy_lab" / "events.py",
             RUNTIME / "strategy_lab" / "execution.py",
             Path(__file__).parent / "trade_excursions.py", RUNTIME / "strategy_lab" / "jesse_runner.py",
             RUNTIME / "strategy_lab" / "optimizer_space.py", Path(__file__).parent / "portfolio.py"]
    paths.extend(Path(__file__).parent / name for name in (
        "daily_data.py", "daily_sync.py", "forward_engine.py", "forward_registry.py",
        "forward_performance.py", "forward_reference.py", "windows_power.py", "research_api.py", "library.py", "plugins/__init__.py", "trickle.py", "forward_rules.py", "dukascopy_datafeed.py", "data_supervisor.py",
    ))
    paths.extend(sorted((Path(__file__).parent / "research").glob("*.py")))
    paths.append(RUNTIME / "strategy_lab" / "rule_engine.py")
    if STRATEGY_ROOT.is_dir():
        for source in STRATEGY_ROOT.glob("*/__init__.py"):
            paths.append(source)
            metadata = source.parent / "parameter_metadata.json"
            if metadata.exists():
                paths.append(metadata)
    return paths


def _frontend_files() -> list[Path]:
    names = ("index.html", "app.js", "app.css", "shell.js", "shell.css", "labels.js", "workflow.js", "workflow.css", "factory.js", "factory.css")
    return [STATIC_ROOT / name for name in names if (STATIC_ROOT / name).is_file()]


LOADED_BACKEND_BUILD_ID = _fingerprint(_backend_files(), "strategy-lab-backend")


def _runtime_version(frontend_expected: str | None = None) -> dict:
    backend_disk = _fingerprint(_backend_files(), "strategy-lab-backend")
    frontend_disk = _fingerprint(_frontend_files(), "strategy-lab-frontend")
    with ACTIVE_RUNS_LOCK:
        active_runs = ACTIVE_RUNS
    return {
        "api_version": API_VERSION,
        "backend_build_id": LOADED_BACKEND_BUILD_ID,
        "backend_disk_build_id": backend_disk,
        "backend_current": backend_disk == LOADED_BACKEND_BUILD_ID,
        "frontend_build_id": frontend_disk,
        "frontend_matches": None if frontend_expected is None else frontend_expected == frontend_disk,
        "active_runs": active_runs,
        "safe_to_restart": active_runs == 0,
        "process_id": os.getpid(),
        "started_at": SERVER_STARTED_AT,
    }


@app.middleware("http")
async def track_active_runs(request: Request, call_next):
    global ACTIVE_RUNS
    path = request.url.path
    tracked = request.method == "POST" and (path in {"/api/runs", "/api/data/sync"} or (
        path.startswith("/api/forward/portfolios/") and path.endswith("/historical-reference")
    ))
    if tracked:
        with ACTIVE_RUNS_LOCK:
            ACTIVE_RUNS += 1
    try:
        return await call_next(request)
    finally:
        if tracked:
            with ACTIVE_RUNS_LOCK:
                ACTIVE_RUNS -= 1


class RunRequest(BaseModel):
    action: str = "backtest"
    symbol: str
    timeframe: str = "M1"
    strategy: str
    start_date: str
    end_date: str
    parameters: dict = Field(default_factory=dict)
    starting_balance: float = 10000
    fee: float = 0.0006
    account_type: str = "spot"
    leverage: float = 1
    warmup_candles: int = 210
    trials: int = 10
    objective: str = "sharpe"
    optimizer_method: str = "random"
    optimizer_space: list[dict] | None = None
    train_percent: float = 80
    seed: int = 42
    workers: int = 1
    resume_run_id: str | None = None


class StrategySave(BaseModel):
    name: str
    source: str


class PresetSave(BaseModel):
    name: str
    strategy: str
    symbol: str
    timeframe: str
    parameters: dict
    config: dict = Field(default_factory=dict)


class LayoutSave(BaseModel):
    name: str = Field(min_length=1, max_length=48)
    layout: dict


class LedgerRequest(BaseModel):
    account_currency: str
    contract_size: float
    tick_size: float
    quote_to_account: float
    volume_step: float
    commission_per_lot_side: float = 0
    slippage_points: float = 0


class CausalReplayRequest(BaseModel):
    cursor_ms: int


class PortfolioRunAllocation(BaseModel):
    run_id: str
    allocated_capital: float


class PortfolioRequest(BaseModel):
    runs: list[PortfolioRunAllocation]


def _json_clean(value):
    return json.loads(json.dumps(value, default=lambda x: x.isoformat() if hasattr(x, "isoformat") else str(x)))


def _reconcile_lifecycle_fill_timestamps(result: dict) -> dict:
    """Use Jesse order execution timestamps for lifecycle events with exact order IDs."""
    orders_by_id = {}
    for trade in result.get("trades", []):
        for order in trade.get("orders", []) or []:
            if order.get("id"):
                orders_by_id.setdefault(str(order["id"]), []).append((trade, order))
    counts = {"POSITION_OPEN": 0, "POSITION_CLOSE": 0, "unmatched": 0}
    for event in result.get("events", []) or []:
        event_type = event.get("event_type", event.get("event"))
        if event_type not in {"POSITION_OPEN", "POSITION_CLOSE"}:
            continue
        matches = orders_by_id.get(str(event.get("order_id", "")), [])
        if event_type == "POSITION_CLOSE" and event.get("trade_id"):
            matches = [(trade, order) for trade, order in matches
                       if str(trade.get("id")) == str(event["trade_id"])]
        if len(matches) != 1:
            counts["unmatched"] += 1
            continue
        _, order = matches[0]
        executed_at = order.get("executed_at")
        if executed_at is None:
            counts["unmatched"] += 1
            continue
        timestamp_ms = int(float(executed_at))
        old_timestamp = event.get("timestamp_ms")
        if old_timestamp is not None and int(old_timestamp) != timestamp_ms:
            event["strategy_observed_timestamp_ms"] = int(old_timestamp)
        event["timestamp_ms"] = timestamp_ms
        event["timestamp_utc"] = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat()
        if event_type == "POSITION_OPEN":
            event["fill_timestamp_ms"] = timestamp_ms
        else:
            event["closed_at_ms"] = timestamp_ms
        counts[event_type] += 1
    return counts


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _causal_reexecution(run_manifest: dict, timestamp_ms: int, snapshot: dict) -> dict:
    """Re-run a small audited adapter only when code and Duka source still match."""
    if run_manifest.get("strategy") != "LabBreakout":
        return {"available": False, "reason": "No causal replay adapter is verified for this strategy."}
    strategy_path = STRATEGY_ROOT / "LabBreakout" / "__init__.py"
    if not strategy_path.is_file() or _sha(strategy_path) != run_manifest.get("strategy_sha256"):
        return {"available": False, "reason": "Current strategy source differs from the source used by this run."}
    saved_data = run_manifest.get("dataset_manifest") or {}
    saved_root = Path(str((saved_data.get("bid_source") or {}).get("path", ""))).parent
    if saved_root.resolve() != DATA.source_root.resolve() or DATA.source_root.resolve() != Path(r"C:\AI\Dukascopy_data").resolve():
        return {"available": False, "reason": "Run data source is not the approved Dukascopy directory."}
    try:
        start = _parse_date(run_manifest["start_date"])
        end = _parse_date(run_manifest["end_date"], upper=True)
        frame, current_data = DATA.candles(run_manifest["symbol"], run_manifest["timeframe"],
                                           (start - pd.Timedelta(days=90)).strftime("%Y-%m-%d"),
                                           end.strftime("%Y-%m-%d"))
    except Exception as exc:
        return {"available": False, "reason": f"Could not load the approved Dukascopy prefix: {exc}"}
    current_bid = (current_data.get("bid_source") or {}).get("sha256")
    current_ask = (current_data.get("ask_source") or {}).get("sha256")
    if current_bid != (saved_data.get("bid_source") or {}).get("sha256") or current_ask != (saved_data.get("ask_source") or {}).get("sha256"):
        return {"available": False, "reason": "Dukascopy source hashes differ from the source used by this run."}
    candles = frame.rename(columns={"bid_open": "open", "bid_high": "high",
                                    "bid_low": "low", "bid_close": "close"})
    candles = candles.dropna(subset=["open", "high", "low", "close"])
    return reexecute_labbreakout(candles, timestamp_ms,
                                 start_ms=int(start.timestamp() * 1000),
                                 warmup_candles=int(run_manifest.get("warmup_candles", 210)),
                                 parameters=run_manifest.get("parameters") or {},
                                 recorded_snapshot=snapshot)


def _split_prefix_teardown_events(replay_events: list[dict], recorded_events: list[dict],
                                  decision_event: dict | None) -> tuple[list[dict], list[dict]]:
    """Separate Jesse's forced end-of-prefix close from an actual strategy exit."""
    recorded_close_times = {int(event.get("timestamp_ms", -1)) for event in recorded_events
                            if event.get("event_type") == "POSITION_CLOSE"}
    state = (decision_event or {}).get("state") or {}
    position_was_open = bool(state.get("position_open"))
    lifecycle, teardown = [], []
    for event in replay_events:
        if event.get("event_type") != "POSITION_CLOSE":
            lifecycle.append(event)
            continue
        reason = str(event.get("exit_reason") or "").strip().lower()
        timestamp_ms = int(event.get("timestamp_ms", -1))
        is_unattributed_close = reason in {"", "none", "null"}
        if position_was_open and is_unattributed_close and timestamp_ms not in recorded_close_times:
            artifact = dict(event)
            artifact["classification"] = "JESSE_PREFIX_TEARDOWN"
            artifact["note"] = "Jesse closes an open position when the prefix backtest ends; this is not a strategy exit at the replay cursor."
            teardown.append(artifact)
        else:
            lifecycle.append(event)
    return lifecycle, teardown


def _closed_trade_counts_at_cursor(trades: list[dict], cursor_ms: int) -> tuple[int, int]:
    """Exclude positions Jesse closes only because the causal prefix ended."""
    closed_by_cursor = 0
    for trade in trades:
        closed_at = trade.get("closed_at", trade.get("closed_at_ms"))
        if closed_at is not None and float(closed_at) <= int(cursor_ms):
            closed_by_cursor += 1
    return closed_by_cursor, len(trades)


def _reexecute_strategy_prefix(run_id: str, cursor_ms: int) -> dict:
    """Run the saved strategy through Jesse on a Dukascopy prefix ending at the cursor."""
    manifest = json.loads((_run_dir(run_id) / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("action") != "backtest":
        raise HTTPException(422, "Causal replay requires a saved backtest run")
    if Path(str(manifest.get("source_root", ""))).resolve() != DATA.source_root.resolve() or DATA.source_root.resolve() != Path(r"C:\AI\Dukascopy_data").resolve():
        raise HTTPException(409, "Causal replay is restricted to the approved Dukascopy source")
    strategy_name = str(manifest.get("strategy", ""))
    strategy_file = STRATEGY_ROOT / strategy_name / "__init__.py"
    if not strategy_file.is_file() or _sha(strategy_file) != manifest.get("strategy_sha256"):
        raise HTTPException(409, "Strategy source differs from the saved run; causal replay refused")
    start = _parse_date(manifest["start_date"])
    end = _parse_date(manifest["end_date"], upper=True)
    frame, data_manifest = DATA.candles(manifest["symbol"], manifest["timeframe"],
        (start - pd.Timedelta(days=90)).strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    saved_data = manifest.get("dataset_manifest") or {}
    for side in ("bid_source", "ask_source"):
        if (data_manifest.get(side) or {}).get("sha256") != (saved_data.get(side) or {}).get("sha256"):
            raise HTTPException(409, f"Dukascopy {side} hash differs from the saved run")
    digest = dataset_digest(data_manifest, manifest["timeframe"], manifest["start_date"], manifest["end_date"])
    if digest != manifest.get("dataset_sha256"):
        raise HTTPException(409, "Dukascopy dataset fingerprint differs from the saved run")
    timestamp = pd.to_datetime(int(cursor_ms), unit="ms", utc=True)
    prefix = frame.loc[frame.index <= timestamp]
    trading_start, trading_end = start, end
    trading = prefix.loc[(prefix.index >= trading_start) & (prefix.index < trading_end)]
    if trading.empty:
        raise HTTPException(422, "No Dukascopy run candle exists at or before the replay cursor")
    candle_timestamp = int(trading.index[-1].value // 1_000_000)
    prefix = prefix.loc[prefix.index <= trading.index[-1]]
    warmup_count = max(0, int(manifest.get("warmup_candles", 210)))
    data_route_minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30,
                          "1h": 60, "4h": 240, "1D": 1440}
    try:
        alignment = max((data_route_minutes[str(route["timeframe"]) ]
                        for route in manifest.get("data_routes", [])), default=1)
    except (KeyError, TypeError) as exc:
        raise HTTPException(409, "Saved strategy data routes are invalid; causal replay refused") from exc
    if alignment > 1:
        warmup_count = ((warmup_count + alignment - 1) // alignment) * alignment
    warmup = prefix.loc[prefix.index < trading_start].tail(warmup_count)
    trading = prefix.loc[(prefix.index >= trading_start) & (prefix.index < trading_end)]
    candle_duration = {"M1": 60_000, "M5": 300_000, "M15": 900_000, "M30": 1_800_000,
                       "H1": 3_600_000, "H4": 14_400_000, "D1": 86_400_000}.get(str(manifest["timeframe"]).upper())
    if candle_duration is None:
        raise HTTPException(422, "Unsupported replay timeframe")
    replay_id = str(uuid.uuid4())
    REQUEST_ROOT.mkdir(parents=True, exist_ok=True)
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    request_csv = REQUEST_ROOT / f"{replay_id}_candles.csv"
    request_file = REQUEST_ROOT / f"{replay_id}.json"
    replay_result = RESULT_ROOT / replay_id
    rows = pd.concat([warmup, trading])
    values = pd.DataFrame({"timestamp": rows.index.view("int64") // 1_000_000,
                           "open": rows.bid_open, "close": rows.bid_close,
                           "high": rows.bid_high, "low": rows.bid_low,
                           "volume": rows.bid_volume.fillna(0)}).dropna(subset=["open", "close", "high", "low"])
    values.to_csv(request_csv, index=False, lineterminator="\n")
    request = {"action": "backtest", "run_id": replay_id, "strategy": strategy_name,
        "strategy_sha256": manifest["strategy_sha256"], "dataset_sha256": digest,
        "candles_file": request_csv.name, "start_ms": int(start.timestamp() * 1000),
        "end_ms": candle_timestamp + candle_duration, "warmup_candles": warmup_count,
        "parameters": manifest.get("parameters", {}), "symbol": manifest["symbol"],
        "jesse_symbol": manifest["jesse_symbol"], "timeframe": manifest["timeframe"],
        "starting_balance": manifest["starting_balance"], "fee": manifest["fee"],
        "account_type": manifest["account_type"], "leverage": manifest["leverage"]}
    request_file.write_text(json.dumps(request, indent=2), encoding="utf-8")
    try:
        with RESEARCH_RUN_LOCK:
            completed = subprocess.run(["docker", "compose", "-f", str(RUNTIME / "docker" / "docker-compose.yml"),
                "exec", "-T", "jesse", "python", "/home/strategy_lab/jesse_runner.py",
                f"/home/strategy_lab/requests/{replay_id}.json"], cwd=RUNTIME,
                capture_output=True, text=True, timeout=900)
        result_path = replay_result / "result.json"
        if completed.returncode or not result_path.is_file():
            raise HTTPException(500, "Causal replay runner failed: " + (completed.stdout + "\n" + completed.stderr)[-3000:])
        replay_data = json.loads(result_path.read_text(encoding="utf-8"))
        replay_data_routes = replay_data.get("data_routes", [])
        if replay_data_routes != manifest.get("data_routes", []):
            raise HTTPException(409, "Strategy data routes differ from the saved run; causal replay refused")
        replay_events = []
        event_path = replay_result / "events.jsonl"
        if event_path.is_file():
            with event_path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    try: event = json.loads(line)
                    except json.JSONDecodeError: continue
                    replay_events.append(event)
        _reconcile_lifecycle_fill_timestamps({"trades": replay_data.get("trades", []),
                                              "events": replay_events})
        replay_events = [event for event in replay_events
                         if int(event.get("timestamp_ms", -1)) == candle_timestamp]
        recorded_events = [event for event in _iter_events(run_id, until_ms=candle_timestamp)
                           if int(event.get("timestamp_ms", -1)) == candle_timestamp]
        replay_decision = next((e for e in replay_events if e.get("event_type") == "CANDLE_DECISION"), None)
        recorded_decision = next((e for e in recorded_events if e.get("event_type") == "CANDLE_DECISION"), None)
        def semantic(event):
            if not event: return None
            state = dict(event.get("state") or {})
            if "pending_order_ids" in state:
                state["pending_order_count"] = len(state.pop("pending_order_ids") or [])
            return {"decision": event.get("decision"), "reason": event.get("reason"),
                    "checks": event.get("checks", []), "indicators": event.get("indicators", []),
                    "signal": event.get("signal"), "state": state}
        signature, recorded_signature = semantic(replay_decision), semantic(recorded_decision)
        lifecycle_events, teardown_events = _split_prefix_teardown_events(
            [e for e in replay_events if e.get("event_type") != "CANDLE_DECISION"],
            recorded_events, replay_decision)
        teardown_trade_ids = {str(event.get("trade_id")) for event in teardown_events if event.get("trade_id")}
        closed_trade_count, prefix_trade_count = _closed_trade_counts_at_cursor(
            replay_data.get("trades", []), candle_timestamp)
        status = ("FULL_CAUSAL_REPLAY" if replay_decision else
                  "RECORDED_EVENT_REPLAY" if recorded_events else "UNSUPPORTED")
        return {"status": status,
            "run_id": run_id, "replay_execution_id": replay_id, "strategy": strategy_name,
            "strategy_sha256": manifest["strategy_sha256"], "dataset_sha256": digest,
            "data_routes": replay_data_routes,
            "timestamp_ms": candle_timestamp, "prefix_candles": int(len(rows)),
            "future_candles_supplied": False,
            "decision_recomputed": replay_decision is not None,
            "decision_event": replay_decision or recorded_decision,
            "reason": (None if replay_decision else
                       "Strategy emitted no candle decision; showing recorded events only." if recorded_events else
                       "Strategy emitted no candle decision or recorded inspector events."),
            "lifecycle_events_at_cursor": lifecycle_events,
            "prefix_teardown_events": teardown_events,
            "closed_trade_count_at_cursor": max(0, closed_trade_count - len(teardown_trade_ids)),
            "closed_trade_count_including_prefix_teardown": prefix_trade_count,
            "prefix_teardown_trade_count": max(0, prefix_trade_count - closed_trade_count),
            "decision_matches_original_run": (signature == recorded_signature) if signature is not None and recorded_signature is not None else None,
            "original_decision_event_available": recorded_decision is not None,
            "replay_events_at_cursor": len(replay_events)}
    finally:
        request_file.unlink(missing_ok=True)
        request_csv.unlink(missing_ok=True)
        if replay_result.exists() and replay_result.parent.resolve() == RESULT_ROOT.resolve():
            shutil.rmtree(replay_result)


def _parse_hyperparameters(source_path: Path) -> list[dict]:
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    entries = []
    type_names = {"int": "int", "float": "float", "bool": "bool", "str": "str"}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != "hyperparameters":
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Dict):
                continue
            try:
                keys = [ast.literal_eval(k) for k in child.keys]
                values = [ast.literal_eval(v) if not isinstance(v, ast.Name) else type_names.get(v.id, v.id) for v in child.values]
            except (ValueError, TypeError):
                continue
            item = dict(zip(keys, values))
            if isinstance(item.get("name"), str) and item.get("name") not in {x["name"] for x in entries}:
                if item.get("type") in {"int", "float", "bool", "str", "categorical"}:
                    entries.append(item)
    return entries


def _strategies() -> list[dict]:
    output = []
    if not STRATEGY_ROOT.is_dir():
        return output
    for folder in sorted(STRATEGY_ROOT.iterdir()):
        source = folder / "__init__.py"
        if not folder.is_dir() or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", folder.name) or not source.is_file():
            continue
        if folder.name.startswith("_"):
            continue
        parameters = _parse_hyperparameters(source)
        metadata_path = folder / "parameter_metadata.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            metadata = {}
        for param in parameters:
            param.update(metadata.get(param["name"], {}))
        output.append({"name": folder.name, "parameters": parameters,
                       "managed_by_lab": (folder / ".strategy_lab_managed").exists(),
                       "sha256": _sha(source)})
    return output


@app.get("/api/layouts")
def get_layouts():
    try:
        data = json.loads(LAYOUT_FILE.read_text(encoding="utf-8")) if LAYOUT_FILE.exists() else {"version": 1, "layouts": {}}
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("layouts"), dict):
            raise ValueError("unsupported layout schema")
        return data
    except (OSError, json.JSONDecodeError, ValueError):
        return {"version": 1, "layouts": {}, "recovered": True}


@app.post("/api/layouts")
def save_layout(request: LayoutSave):
    if not re.fullmatch(r"[\w -]{1,48}", request.name.strip()):
        raise HTTPException(422, "Ongeldige layoutnaam")
    layout = request.layout
    widget_ids = {
        "market-controls", "settings", "chart", "inspector", "results", "trades", "analytics",
        "optimization-settings", "optimization-results", "portfolio", "comparison", "logs",
    }
    panels = layout.get("panels", [])
    if not isinstance(panels, list) or len(panels) != len(set(panels)) or set(panels) != widget_ids:
        raise HTTPException(422, "Layout moet alle bekende widgets bevatten")
    widths = layout.get("widths", {})
    if not isinstance(widths, dict) or any(not isinstance(v, (int, float)) or not 180 <= v <= 680 for v in widths.values()):
        raise HTTPException(422, "Paneelbreedtes moeten tussen 180 en 680 px liggen")
    heights = layout.get("heights", {})
    if not isinstance(heights, dict) or any(
        key not in {"settings", "chart", "inspector", "results"}
        or not isinstance(value, (int, float))
        or not (480 <= value <= 1200 if key == "chart" else 180 <= value <= 1200)
        for key, value in heights.items()
    ):
        raise HTTPException(422, "Chart moet 480–1200 px zijn; andere panelen 180–1200 px")
    sizes = layout.get("sizes", {})
    if not isinstance(sizes, dict) or any(
        panel not in widget_ids or not isinstance(size, dict)
        or not isinstance(size.get("columns"), int) or not 1 <= size["columns"] <= 12
        or not isinstance(size.get("rows"), int) or not 1 <= size["rows"] <= 16
        for panel, size in sizes.items()
    ):
        raise HTTPException(422, "Widgetmaten moeten 1–12 kolommen en 1–16 rijen gebruiken")
    positions = layout.get("positions", {})
    if not isinstance(positions, dict) or any(
        panel not in widget_ids or not isinstance(position, dict)
        or not isinstance(position.get("column"), int) or not 1 <= position["column"] <= 12
        or not isinstance(position.get("row"), int) or not 1 <= position["row"] <= 40
        or position["column"] + sizes.get(panel, {}).get("columns", 1) > 13
        for panel, position in positions.items()
    ):
        raise HTTPException(422, "Widgetposities moeten binnen 12 kolommen en 40 rijen liggen")
    hidden = layout.get("hidden", [])
    if not isinstance(hidden, list) or any(panel not in widget_ids for panel in hidden):
        raise HTTPException(422, "Onbekend verborgen paneel")
    LAYOUT_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        data = get_layouts()
        data.setdefault("layouts", {})[request.name.strip()] = layout
        temp = LAYOUT_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps({"version": 1, "layouts": data["layouts"]}, indent=2), encoding="utf-8")
        temp.replace(LAYOUT_FILE)
    except OSError as exc:
        raise HTTPException(500, f"Layout opslaan mislukt: {exc}") from exc
    return {"saved": request.name.strip()}


def _parse_date(value: str, upper: bool = False) -> pd.Timestamp:
    try:
        day = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if upper:
            day += timedelta(days=1)
        return pd.Timestamp(day)
    except ValueError as exc:
        raise HTTPException(422, f"Date must use YYYY-MM-DD: {value}") from exc


def _end_boundary(value: str | None) -> str | None:
    if value and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return (datetime.strptime(value, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    return value


def _frame_json(frame: pd.DataFrame, limit: int = 4000) -> list[dict]:
    if limit and len(frame) > limit:
        stride = max(1, len(frame) // limit)
        # Keep the last point in each stride so the most recent candle is present.
        frame = frame.iloc[::stride]
    rows = []
    for ts, row in frame.iterrows():
        rows.append({"timestamp_ms": int(ts.timestamp() * 1000), "timestamp": ts.isoformat(),
                     **{k: (None if pd.isna(v) else float(v)) for k, v in row.items()}})
    return rows


def _run_dir(run_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f-]{36}", run_id):
        raise HTTPException(422, "Invalid run ID")
    path = (RESULT_ROOT / run_id).resolve()
    if RESULT_ROOT.resolve() not in path.parents:
        raise HTTPException(404, "Run not found")
    return path


def _load_run(run_id: str) -> dict:
    path = _run_dir(run_id) / "result.json"
    if not path.is_file():
        raise HTTPException(404, "Run result not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_events(run_id: str, *, until_ms: int | None = None):
    """Read the append-only JSONL event stream, falling back to legacy results."""
    event_path = _run_dir(run_id) / "events.jsonl"
    if event_path.is_file():
        with event_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try: event = json.loads(line)
                except json.JSONDecodeError: continue
                if until_ms is None or int(event.get("timestamp_ms", -1)) <= int(until_ms):
                    yield event
        return
    for event in _load_run(run_id).get("events", []):
        if until_ms is None or int(event.get("timestamp_ms", -1)) <= int(until_ms):
            yield event


def _reconcile_lifecycle_event_file(trades: list[dict], event_path: Path) -> tuple[dict, int]:
    """Rewrite lifecycle timestamps with exact trade order times without holding all events."""
    orders_by_id: dict[str, list[tuple[dict, dict]]] = {}
    for trade in trades:
        for order in trade.get("orders", []) or []:
            if order.get("id"):
                orders_by_id.setdefault(str(order["id"]), []).append((trade, order))
    counts = {"POSITION_OPEN": 0, "POSITION_CLOSE": 0, "unmatched": 0}
    total = 0
    temp_path = event_path.with_suffix(".jsonl.tmp")
    with event_path.open("r", encoding="utf-8") as source, temp_path.open("w", encoding="utf-8") as target:
        for line in source:
            try: event = json.loads(line)
            except json.JSONDecodeError: continue
            total += 1
            event_type = event.get("event_type", event.get("event"))
            if event_type in {"POSITION_OPEN", "POSITION_CLOSE"}:
                matches = orders_by_id.get(str(event.get("order_id", "")), [])
                if event_type == "POSITION_CLOSE" and event.get("trade_id"):
                    matches = [(trade, order) for trade, order in matches
                               if str(trade.get("id")) == str(event["trade_id"])]
                if len(matches) != 1 or matches[0][1].get("executed_at") is None:
                    counts["unmatched"] += 1
                else:
                    _, order = matches[0]
                    timestamp_ms = int(float(order["executed_at"]))
                    old_timestamp = event.get("timestamp_ms")
                    if old_timestamp is not None and int(old_timestamp) != timestamp_ms:
                        event["strategy_observed_timestamp_ms"] = int(old_timestamp)
                    event["timestamp_ms"] = timestamp_ms
                    event["timestamp_utc"] = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat()
                    if event_type == "POSITION_OPEN": event["fill_timestamp_ms"] = timestamp_ms
                    else: event["closed_at_ms"] = timestamp_ms
                    counts[event_type] += 1
            target.write(json.dumps(event, separators=(",", ":"), allow_nan=False) + "\n")
    os.replace(temp_path, event_path)
    return counts, total


@app.get("/", response_class=HTMLResponse)
def index():
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    frontend_id = _fingerprint(_frontend_files(), "strategy-lab-frontend")
    html = html.replace("__FRONTEND_BUILD_ID__", frontend_id)
    html = re.sub(r"(/assets/(?:app|shell|workflow)\.css\?v=)[^\"']+", rf"\g<1>{frontend_id}", html)
    html = re.sub(r"(/assets/(?:app|shell|labels|workflow)\.js\?v=)[^\"']+", rf"\g<1>{frontend_id}", html)
    return HTMLResponse(html, headers={"Cache-Control": "no-store, max-age=0"})


@app.get("/assets/plotly.min.js")
def plotly_js():
    return Response(get_plotlyjs(), media_type="application/javascript")


FRONTEND_ASSETS = {"app.css", "app.js", "shell.css", "shell.js", "labels.js", "workflow.js", "workflow.css", "factory.js", "factory.css"}


@app.get("/assets/{asset_name}")
def static_asset(asset_name: str):
    static_icons = {
        "dukascopy-strategy-lab.ico": "image/x-icon",
        "dukascopy-strategy-lab.png": "image/png",
        "dukascopy-strategy-lab-192.png": "image/png",
        "dukascopy-strategy-lab-512.png": "image/png",
        "manifest.webmanifest": "application/manifest+json",
    }
    if asset_name in static_icons:
        icon = ROOT / "assets" / asset_name
        if icon.is_file():
            return FileResponse(icon, media_type=static_icons[asset_name])
        raise HTTPException(404, "Asset not found")
    if asset_name not in FRONTEND_ASSETS:
        raise HTTPException(404, "Asset not found")
    return FileResponse(STATIC_ROOT / asset_name)


@app.get("/api/health")
def health():
    source_exists = DATA.source_root.is_dir()
    return {"status": "ok" if source_exists else "error", "source_root": str(DATA.source_root),
            "source_exists": source_exists, "mode": "local-only", **_runtime_version()}


@app.get("/api/version")
def version(frontend_build_id: str | None = None):
    return _runtime_version(frontend_build_id)


@app.post("/api/runtime/restart")
def request_restart(request: Request):
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(403, "Runtime restart is available only from localhost")
    if request.headers.get("origin"):
        raise HTTPException(403, "Browser-originated restart requests are not accepted")
    with ACTIVE_RUNS_LOCK:
        if ACTIVE_RUNS:
            raise HTTPException(409, f"Cannot restart while {ACTIVE_RUNS} backtest/optimization request(s) are active")
    runtime_server = getattr(app.state, "uvicorn_server", None)
    if runtime_server is None:
        raise HTTPException(503, "Graceful runtime control is unavailable")
    threading.Timer(0.2, setattr, args=(runtime_server, "should_exit", True)).start()
    return {"restart_requested": True, "active_runs": 0}


@app.get("/api/datasets")
def datasets():
    return {"source_root": str(DATA.source_root), "datasets": DATA.available_symbols(),
            "timeframes": ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]}


@app.get("/api/data/meta")
def data_meta(symbol: str):
    try:
        data = DATA.ensure(symbol)
        return {"symbol": data.symbol, "manifest": data.manifest}
    except DataError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/data/monitor")
def monitor_daily_data():
    try:
        config = load_sync_config(DEFAULT_SYNC_CONFIG)
        store = DailyPartitionStore(DEFAULT_DAILY_STORE)
        data = feed_status(store, config, now=datetime.now(timezone.utc),
                           forward_by_symbol=store.forward_progress())
        return {**data, "windows_power": recent_power_events()}
    except FileNotFoundError:
        return {"configured": False, "symbols": [], "runs": []}
    except DataError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/data/trickle")
def trickle_status():
    from . import trickle
    instance = trickle.current()
    return {"running": instance is not None, **(instance.status if instance else {"state": "OFF"})}


@app.post("/api/data/trickle/nudge")
def trickle_nudge():
    from . import trickle
    instance = trickle.current()
    if instance:
        instance.nudge()
    return {"running": instance is not None}


@app.get("/api/data/supervisor")
def data_supervisor_status():
    from . import data_supervisor
    try:
        return data_supervisor.default().status()
    except FileNotFoundError:
        return {"overall": "ERROR", "overall_nl": "De dagelijkse synchronisatie is nog niet ingesteld.",
                "symbols": [], "issues": [{"code": "NOT_CONFIGURED", "nl": "daily_sync.local.json ontbreekt"}]}
    except DataError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/data/supervisor/check-sync")
def data_supervisor_check_sync(request: Request):
    origin = request.headers.get("origin")
    if origin and (request.url.hostname not in {"127.0.0.1", "::1", "localhost"}
                   or origin != str(request.base_url).rstrip("/")):
        raise HTTPException(403, "Synchronization requires the local Strategy Lab origin")
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "Synchronization requires an application/json request")
    from . import data_supervisor
    try:
        job = data_supervisor.default().check_sync()
    except FileNotFoundError as exc:
        raise HTTPException(409, "De dagelijkse synchronisatie is nog niet ingesteld.") from exc
    return {"job": job, "poll": "/api/data/supervisor"}


@app.post("/api/data/sync")
def sync_daily_data(request: Request):
    origin = request.headers.get("origin")
    if origin and (request.url.hostname not in {"127.0.0.1", "::1", "localhost"}
                   or origin != str(request.base_url).rstrip("/")):
        raise HTTPException(403, "Synchronization requires the local Strategy Lab origin")
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "Synchronization requires an application/json request")
    try:
        config = load_sync_config(DEFAULT_SYNC_CONFIG)
        return run_sync(DailyPartitionStore(DEFAULT_DAILY_STORE), config, manual=True)
    except FileNotFoundError as exc:
        raise HTTPException(409, "De dagelijkse synchronisatie is nog niet ingesteld.") from exc
    except SyncBusy as exc:
        raise HTTPException(409, "Er loopt al een synchronisatie.") from exc
    except DataError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/strategies")
def strategies():
    return {"strategies": _strategies()}


@app.get("/api/strategies/{name}/source")
def strategy_source(name: str):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise HTTPException(422, "Invalid strategy name")
    path = STRATEGY_ROOT / name / "__init__.py"
    if not path.is_file():
        raise HTTPException(404, "Strategy not found")
    return {"name": name, "source": path.read_text(encoding="utf-8")}


@app.post("/api/strategies")
def save_strategy(request: StrategySave):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", request.name):
        raise HTTPException(422, "Name must be a Python module identifier")
    try:
        ast.parse(request.source)
    except SyntaxError as exc:
        raise HTTPException(422, f"Python syntax error at line {exc.lineno}: {exc.msg}") from exc
    target = STRATEGY_ROOT / request.name
    if target.exists() and not (target / ".strategy_lab_managed").exists():
        raise HTTPException(409, "This is an existing Jesse strategy. Create a new name; the Lab will not overwrite it.")
    target.mkdir(parents=True, exist_ok=True)
    path = target / "__init__.py"
    if path.exists():
        backup = target / "backups"
        backup.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(path, backup / f"{stamp}.py")
    path.write_text(request.source, encoding="utf-8")
    (target / ".strategy_lab_managed").write_text("Created by local Strategy Lab\n", encoding="utf-8")
    return {"name": request.name, "sha256": _sha(path), "parameters": _parse_hyperparameters(path)}


@app.get("/api/candles")
def candles(symbol: str, timeframe: str = "M1", start: str | None = None,
            end: str | None = None, limit: int = Query(4000, ge=0), tail: bool = False):
    try:
        frame, manifest = DATA.candles(symbol, timeframe, start, _end_boundary(end))
        visible = frame.tail(limit) if tail and limit else frame
        return {"symbol": symbol, "timeframe": timeframe, "candles": _frame_json(visible, limit),
                "manifest": manifest, "available_rows": len(frame)}
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/replay")
def replay(symbol: str, timeframe: str, start: str, end: str, cursor_ms: int,
           run_id: str | None = None):
    try:
        frame, manifest = DATA.candles(symbol, timeframe, start, _end_boundary(end))
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc
    run_manifest = {}
    events = []
    if run_id:
        events = list(_iter_events(run_id, until_ms=cursor_ms))
        try:
            run_manifest = json.loads((_run_dir(run_id) / "manifest.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            run_manifest = {}
    event_symbol = str(run_manifest.get("jesse_symbol") or symbol)
    visible, visible_events = replay_slice(frame, cursor_ms, events)
    if len(visible):
        candle_timestamp_ms = int(visible.index[-1].value // 1_000_000)
        state_snapshot = recorded_state_snapshot(visible_events, candle_timestamp_ms,
                                                 symbol=symbol, timeframe=timeframe,
                                                 event_symbol=event_symbol)
        if run_manifest and state_snapshot.get("available"):
            state_snapshot["reexecution"] = _causal_reexecution(run_manifest, candle_timestamp_ms, state_snapshot)
    else:
        state_snapshot = {"available": False, "state_available": False, "timestamp_ms": None,
                          "reason": "No candle is visible at this replay cursor.", "decision_event_count": 0}
    replay_mode = "RECORDED_EVENT_REPLAY" if run_id and visible_events else "UNSUPPORTED"
    return {"symbol": symbol, "timeframe": timeframe, "cursor_ms": cursor_ms,
            "candles": _frame_json(visible, 10000), "events": visible_events,
            "state_snapshot": state_snapshot, "manifest": manifest,
            "replay_mode": replay_mode, "future_candles_returned": False}


@app.post("/api/runs/{run_id}/causal-replay")
def causal_replay(run_id: str, request: CausalReplayRequest):
    with ACTIVE_RUNS_LOCK:
        if ACTIVE_RUNS:
            raise HTTPException(409, "Wacht tot de actieve backtest of optimalisatie klaar is")
    try:
        return _reexecute_strategy_prefix(run_id, request.cursor_ms)
    except HTTPException:
        raise
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(500, f"Causale heruitvoering mislukt: {exc}") from exc


_RUN_SUMMARIES: dict[str, tuple] = {}  # ponytail: unbounded, one small dict per run dir


@app.get("/api/runs")
def runs():
    output = []
    if RESULT_ROOT.exists():
        for path in RESULT_ROOT.iterdir():
            meta = path / "manifest.json"
            result = path / "result.json"
            if meta.is_file() and result.is_file():
                key = (meta.stat().st_mtime_ns, result.stat().st_mtime_ns, result.stat().st_size)
                cached = _RUN_SUMMARIES.get(path.name)
                if not cached or cached[0] != key:
                    item = json.loads(meta.read_text(encoding="utf-8"))
                    data = json.loads(result.read_text(encoding="utf-8"))
                    item.setdefault("run_id", path.name)
                    item["metrics"] = data.get("metrics", {})
                    item["trade_count"] = len(data.get("trades", []))
                    cached = _RUN_SUMMARIES[path.name] = (key, item)
                item = dict(cached[1])
                item["bidask_available"] = (path / "bidask_execution.json").is_file()
                output.append(item)
    return {"runs": sorted(output, key=lambda x: x.get("created_at", ""), reverse=True)}


class RunCancelled(Exception):
    """The running Jesse backtest was stopped by the user or by a cancelled validation job."""


COMPOSE = ["docker", "compose", "-f", str(RUNTIME / "docker" / "docker-compose.yml")]
RUNNER_TIMEOUT_S = 900
_ACTIVE_RUN_LOCK = threading.Lock()
_ACTIVE_RUN: dict = {}
_LAST_CANCELLED: dict = {}
# Kills the in-container runner for one request file; the Jesse image has no ps/pkill, so read /proc.
_CONTAINER_KILL = (
    "import os,signal,sys\n"
    "me=os.getpid()\n"
    "for p in os.listdir('/proc'):\n"
    "    if not p.isdigit() or int(p)==me: continue\n"
    "    try:\n"
    "        cmd=open('/proc/%s/cmdline'%p,'rb').read()\n"
    "        if sys.argv[1].encode() in cmd: os.kill(int(p),signal.SIGKILL); print('killed',p)\n"
    "    except OSError: pass\n")

RUNNER_DIAGNOSTICS = (
    (("cannot be True if the exchange type is", "opent ook shortposities"), "spot_short",
     "Deze strategie opent ook shortposities. Kies het rekenmodel Futures en start opnieuw."),
    (("No candle rows", "contains no Dukascopy candles", "Selected period has no Dukascopy candles"), "no_candles",
     "Geen Dukascopy-candles in de gekozen periode. Kies een periode binnen de beschikbare data (zie Data)."),
    (("Unknown strategy parameter",), "unknown_parameter",
     "Onbekende parameter voor deze strategie. Controleer de parameternamen in hyperparameters()."),
    (("outside allowed range",), "out_of_range",
     "Een parameterwaarde valt buiten het toegestane bereik (min/max in hyperparameters())."),
    (("Cannot connect to the Docker daemon", "error during connect", "docker daemon is not running",
      "is the docker daemon running", 'service "jesse" is not running', "No such container",
      "The system cannot find the file specified", "dockerDesktopLinuxEngine"), "docker_not_running",
     "Docker of de Jesse-container draait niet. Start Docker Desktop en de Jesse-container en probeer opnieuw."),
    (("timed out after", "TimeoutExpired"), "timeout",
     "De Jesse-backtest duurde te lang en is gestopt. Kies een kortere periode of een hogere timeframe."),
)


def diagnose_runner_failure(text: str) -> tuple[str, str] | None:
    """Map raw runner/Docker output to (code, actionable Dutch message)."""
    for needles, code, message in RUNNER_DIAGNOSTICS:
        if any(n in (text or "") for n in needles):
            return code, message
    return None


def _stop_runner(proc, run_id: str) -> None:
    try:
        subprocess.run(COMPOSE + ["exec", "-T", "jesse", "python", "-c", _CONTAINER_KILL,
                                  f"/home/strategy_lab/requests/{run_id}.json"],
                       cwd=RUNTIME, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        proc.kill()
        proc.communicate(timeout=10)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass


def _run_runner(run_id: str, strategy: str, action: str, *, source: str = "tester", is_cancelled=None,
                timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run one runner request in the Jesse container (serialised), polling for cancel and timeout."""
    timeout = RUNNER_TIMEOUT_S if timeout is None else timeout
    while not RESEARCH_RUN_LOCK.acquire(timeout=0.5):
        if is_cancelled and is_cancelled():
            raise RunCancelled()
    cancel = threading.Event()
    try:
        if is_cancelled and is_cancelled():
            raise RunCancelled()
        args = COMPOSE + ["exec", "-T", "jesse", "python", "/home/strategy_lab/jesse_runner.py",
                          f"/home/strategy_lab/requests/{run_id}.json"]
        proc = subprocess.Popen(args, cwd=RUNTIME, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        t0 = datetime.now(timezone.utc)
        with _ACTIVE_RUN_LOCK:
            _ACTIVE_RUN.update(run_id=run_id, strategy=strategy, action=action, source=source,
                               started_at=t0.isoformat(), t0=t0, cancel=cancel)
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if cancel.is_set() or (is_cancelled and is_cancelled()):
                    _stop_runner(proc, run_id)
                    raise RunCancelled()
                if (datetime.now(timezone.utc) - t0).total_seconds() > timeout:
                    _stop_runner(proc, run_id)
                    raise subprocess.TimeoutExpired(args, timeout)
        return subprocess.CompletedProcess(args, proc.returncode, stdout or "", stderr or "")
    finally:
        with _ACTIVE_RUN_LOCK:
            _ACTIVE_RUN.clear()
        RESEARCH_RUN_LOCK.release()


def _ledger_metrics(metrics: dict | None) -> dict:
    m = {k: v for k, v in (metrics or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if "total" in m:
        m["trades"] = m["total"]
    return m


def _record_tester_ledger(request: RunRequest, definitions: list[dict], parameters: dict, result: dict,
                          manifest: dict) -> None:
    """Every tester backtest and optimizer trial becomes a ledger row (trial accounting); never fatal."""
    try:
        from .research.ledger import hypothesis_id
        led = research_api.ledger()
        family = f"jesse:{request.strategy}"
        defaults = {d["name"]: d.get("default") for d in definitions if "default" in d}
        common = dict(kind="BACKTEST", family=family, symbol=manifest["symbol"], timeframe=request.timeframe,
                      start=request.start_date, end=request.end_date, code_sha256=manifest["strategy_sha256"],
                      dataset_sha256=manifest["dataset_sha256"],
                      cost_model={"fee": request.fee, "account_type": request.account_type,
                                  "prices": "Dukascopy Bid OHLC (Jesse)"})
        if request.action == "backtest":
            params = {**defaults, **parameters}
            led.record(**common, hypothesis_id=hypothesis_id(family, params), params=params, status="TESTED",
                       metrics=_ledger_metrics(result.get("metrics")), statistics={"run_id": manifest["run_id"]})
            return
        trials = result.get("all_trials") or []
        skip = 0
        if request.resume_run_id:
            prior = RUNS_ROOT / request.resume_run_id / "result.json"
            if prior.is_file():
                skip = len(json.loads(prior.read_text(encoding="utf-8")).get("all_trials") or [])
        for trial in trials[skip:]:
            params = {**defaults, **parameters, **(trial.get("params") or {})}
            failed = str(trial.get("status", "")).upper() in {"FAILED", "ERROR"}
            led.record(**common, hypothesis_id=hypothesis_id(family, params), params=params,
                       status="ERROR" if failed else "TESTED", score=trial.get("score"),
                       metrics=_ledger_metrics(trial.get("training_metrics")),
                       oos_metrics=_ledger_metrics(trial.get("testing_metrics")) or None,
                       statistics={"run_id": manifest["run_id"], "trial": trial.get("trial"),
                                   "optimizer_method": request.optimizer_method, "objective": request.objective})
    except Exception as exc:  # the tester must never fail because of ledger bookkeeping
        print(f"[strategy-lab] ledger registration skipped: {exc}", flush=True)


def _cleanup_run_files(run_id: str) -> None:
    (REQUEST_ROOT / f"{run_id}.json").unlink(missing_ok=True)
    (REQUEST_ROOT / f"{run_id}_candles.csv").unlink(missing_ok=True)
    target = RESULT_ROOT / run_id
    if target.is_dir() and target.parent.resolve() == RESULT_ROOT.resolve():
        shutil.rmtree(target, ignore_errors=True)


def _execute_run(request: RunRequest, *, persist: bool = True, source: str = "tester", is_cancelled=None,
                 timeout: float | None = None) -> dict:
    if request.action not in {"backtest", "optimize"}:
        raise HTTPException(422, "action must be backtest or optimize")
    available = {x["name"]: x for x in _strategies()}
    if request.strategy not in available:
        raise HTTPException(422, "Select a strategy under the Jesse project strategies folder")
    if request.account_type not in {"spot", "futures"}:
        raise HTTPException(422, "account_type must be spot or futures")
    if request.starting_balance <= 0 or request.fee < 0 or request.warmup_candles < 0:
        raise HTTPException(422, "Invalid balance, fee, or warmup")
    if request.action == "optimize" and not 1 <= request.trials <= 100:
        raise HTTPException(422, "trials must be from 1 to 100")
    if request.action == "optimize":
        try:
            training_split_index(2, request.train_percent)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if request.optimizer_method not in {"random", "grid", "tpe"} or request.workers != 1:
        raise HTTPException(422, "optimizer_method must be random, grid, or tpe; workers must be 1")
    if request.objective not in {"sharpe", "calmar", "sortino", "omega", "recovery_factor"}:
        raise HTTPException(422, "Unsupported Jesse optimization objective")
    optimizer_space_specs, optimizer_preflight_excluded = None, {}
    if request.action == "optimize":
        try:
            optimizer_space_specs, optimizer_preflight_excluded = normalize_optimizer_space(
                available[request.strategy]["parameters"], request.optimizer_space,
                request.parameters, request.account_type,
            )
        except ValueError as exc:
            raise HTTPException(422, f"Invalid optimizer search space: {exc}") from exc
    start = _parse_date(request.start_date)
    end = _parse_date(request.end_date, upper=True)
    if start >= end:
        raise HTTPException(422, "End date must follow start date")
    selected = request.symbol.upper()
    jesse_symbol = {"XAUUSD": "XAU-USD", "US30": "US30-USD", "USA30IDXUSD": "US30-USD",
                    "USDJPY": "USD-JPY", "BTCUSD": "BTC-USD", "DE40": "DE40-EUR", "US500": "US500-USD",
                    "EURUSD": "EUR-USD", "GBPUSD": "GBP-USD", "AUDUSD": "AUD-USD", "USDCAD": "USD-CAD",
                    "USATECHIDXUSD": "USATECHIDXUSD"}.get(selected, selected)
    try:
        frame, data_manifest = DATA.candles(selected, request.timeframe,
                                            (start - pd.Timedelta(days=90)).strftime("%Y-%m-%d"),
                                            end.strftime("%Y-%m-%d"))
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc
    run_id = str(uuid.uuid4())
    begin = start - pd.Timedelta(days=90)
    selected_frame = frame[(frame.index >= begin) & (frame.index < end)]
    if selected_frame.empty or not ((selected_frame.index >= start) & (selected_frame.index < end)).any():
        raise HTTPException(422, "Selected period has no Dukascopy candles")
    if request.timeframe.upper() != "M1":
        # Jesse aggregates its route timeframe from 1-minute candles itself; feeding pre-aggregated
        # bars as if they were minutes left higher-timeframe routes without a single closed candle.
        tf_minutes = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}.get(request.timeframe.upper(), 1)
        warm_days = max(7, math.ceil(request.warmup_candles * tf_minutes / 1440 * 2) + 7)
        try:
            minute_frame, _ = DATA.candles(selected, "M1", (start - pd.Timedelta(days=warm_days)).strftime("%Y-%m-%d"),
                                           end.strftime("%Y-%m-%d"))
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        selected_frame = minute_frame[(minute_frame.index >= start - pd.Timedelta(days=warm_days)) & (minute_frame.index < end)]
    if request.resume_run_id:
        prior_dir = _run_dir(request.resume_run_id)
        prior_manifest_path, prior_result_path = prior_dir / "manifest.json", prior_dir / "result.json"
        if not prior_manifest_path.is_file() or not prior_result_path.is_file():
            raise HTTPException(422, "Resume run must have a saved manifest and results")
        prior_manifest = json.loads(prior_manifest_path.read_text(encoding="utf-8"))
        prior_result = json.loads(prior_result_path.read_text(encoding="utf-8"))
        comparable = {"action": "optimize", "symbol": selected, "timeframe": request.timeframe,
                      "start_date": request.start_date, "end_date": request.end_date,
                      "strategy": request.strategy, "strategy_sha256": available[request.strategy]["sha256"],
                      "starting_balance": request.starting_balance, "fee": request.fee,
                      "account_type": request.account_type, "leverage": request.leverage,
                      "optimizer_method": request.optimizer_method, "seed": request.seed,
                      "train_percent": request.train_percent}
        if any(prior_manifest.get(key) != value for key, value in comparable.items()):
            raise HTTPException(422, "Resume run settings must match strategy, Duka data range, account, method, and seed")
        current_digest = dataset_digest(data_manifest, request.timeframe, request.start_date, request.end_date)
        if prior_manifest.get("dataset_sha256") != current_digest:
            raise HTTPException(422, "Resume run Dukascopy source fingerprint differs")
        if request.optimizer_method == "tpe":
            raise HTTPException(422, "TPE resume is unavailable because Jesse's Optuna study is not persisted")
        if not isinstance(prior_result.get("all_trials"), list):
            raise HTTPException(422, "Resume run has no per-trial checkpoint data")
        if len(prior_result["all_trials"]) >= request.trials:
            raise HTTPException(422, "Requested total trial count must exceed the saved checkpoint")
    REQUEST_ROOT.mkdir(parents=True, exist_ok=True)
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    csv_path = REQUEST_ROOT / f"{run_id}_candles.csv"
    ms = selected_frame.index.view("int64") // 1_000_000
    array = pd.DataFrame({"timestamp": ms,
                          "open": selected_frame.bid_open, "high": selected_frame.bid_high,
                          "low": selected_frame.bid_low, "close": selected_frame.bid_close,
                          "volume": selected_frame.bid_volume.fillna(0)}).dropna(subset=["open", "high", "low", "close"])
    array.to_csv(csv_path, index=False, lineterminator="\n")
    definitions = {x["name"]: x for x in available[request.strategy]["parameters"]}
    parameters = {}
    for key, value in request.parameters.items():
        if key not in definitions:
            raise HTTPException(422, f"Unknown strategy parameter: {key}")
        spec = definitions[key]
        try:
            if spec["type"] == "int":
                casted = int(value)
            elif spec["type"] == "float":
                casted = float(value)
            elif spec["type"] == "bool":
                casted = bool(value)
            elif spec["type"] == "categorical":
                match = next((option for option in spec.get("options", []) if str(option) == str(value)), None)
                if match is None:
                    raise ValueError("not in categorical options")
                casted = match
            else:
                casted = str(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, f"Invalid parameter {key}") from exc
        if spec["type"] in {"int", "float"} and (("min" in spec and casted < spec["min"]) or ("max" in spec and casted > spec["max"])):
            raise HTTPException(422, f"Parameter {key} outside allowed range")
        parameters[key] = casted
    req = {"action": request.action, "run_id": run_id, "strategy": request.strategy,
           "strategy_sha256": available[request.strategy]["sha256"],
           "dataset_sha256": dataset_digest(data_manifest, request.timeframe, request.start_date, request.end_date),
           "candles_file": f"{run_id}_candles.csv", "start_ms": int(start.timestamp()*1000),
           "end_ms": int(end.timestamp()*1000), "warmup_candles": request.warmup_candles,
           "parameters": parameters, "symbol": selected, "jesse_symbol": jesse_symbol,
           "timeframe": request.timeframe, "starting_balance": request.starting_balance,
           "fee": request.fee, "account_type": request.account_type, "leverage": request.leverage,
           "trials": request.trials, "objective": request.objective,
           "optimizer_method": request.optimizer_method, "seed": request.seed,
           "train_percent": request.train_percent,
           "optimizer_space": request.optimizer_space,
           "optimizer_space_specs": optimizer_space_specs,
           "optimizer_preflight_excluded_options": optimizer_preflight_excluded,
           "workers": request.workers, "resume_run_id": request.resume_run_id}
    request_path = REQUEST_ROOT / f"{run_id}.json"
    request_path.write_text(json.dumps(req, indent=2), encoding="utf-8")
    try:
        completed = _run_runner(run_id, request.strategy, request.action, source=source,
                                is_cancelled=is_cancelled, timeout=timeout)
    except RunCancelled:
        _cleanup_run_files(run_id)
        _LAST_CANCELLED.clear()
        _LAST_CANCELLED.update(run_id=run_id, strategy=request.strategy, source=source, status="CANCELLED",
                               cancelled_at=datetime.now(timezone.utc).isoformat())
        raise
    except subprocess.TimeoutExpired as exc:
        _cleanup_run_files(run_id)
        raise HTTPException(503, f"Jesse runner failed: timed out after {exc.timeout:.0f} s. "
                                 + diagnose_runner_failure("timed out after")[1]) from exc
    except OSError as exc:
        raise HTTPException(503, f"Jesse runner failed: {exc}. "
                                 + diagnose_runner_failure("No such container")[1]) from exc
    result_path = RESULT_ROOT / run_id / "result.json"
    if completed.returncode or not result_path.is_file():
        detail = (completed.stdout + "\n" + completed.stderr)[-6000:]
        if not persist:
            _cleanup_run_files(run_id)
        if "cannot be True if the exchange type is" in detail:
            raise HTTPException(422, "Deze strategie opent ook shortposities. Kies bij Instellingen → Geavanceerd → "
                                     "Kosten en rekenmodel het rekenmodel Futures en start opnieuw.")
        found = diagnose_runner_failure(detail)
        raise HTTPException(500, (found[1] + "\n\n" if found else "") + "Jesse research run failed: " + detail)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if not persist:
        _cleanup_run_files(run_id)
        result.pop("events", None)
        return {"run_id": run_id, "result": result, "parameters": parameters,
                "strategy_sha256": available[request.strategy]["sha256"],
                "dataset_sha256": dataset_digest(data_manifest, request.timeframe, request.start_date,
                                                 request.end_date),
                "account_type": request.account_type}
    event_path = result_path.parent / "events.jsonl"
    if not event_path.is_file() and result.get("events"):
        event_path.write_text("".join(json.dumps(event, separators=(",", ":"), allow_nan=False) + "\n"
                                    for event in result["events"]), encoding="utf-8")
    if event_path.is_file():
        reconciliation, event_count = _reconcile_lifecycle_event_file(result.get("trades", []), event_path)
    else:
        reconciliation, event_count = _reconcile_lifecycle_fill_timestamps(result), len(result.get("events", []))
    result["lifecycle_timestamp_reconciliation"] = reconciliation
    result["event_count"] = event_count
    result.pop("events", None)
    result_path.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    manifest = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
                "action": request.action, "symbol": selected, "jesse_symbol": jesse_symbol,
                "timeframe": request.timeframe, "start_date": request.start_date,
                "end_date": request.end_date, "strategy": request.strategy,
                "strategy_sha256": available[request.strategy]["sha256"], "parameters": parameters,
                "starting_balance": request.starting_balance, "fee": request.fee,
                "account_type": request.account_type, "leverage": request.leverage,
                "optimizer_method": request.optimizer_method, "seed": request.seed,
                "train_percent": request.train_percent,
                "optimizer_space": request.optimizer_space,
                "optimizer_space_specs": optimizer_space_specs,
                "optimizer_preflight_excluded_options": optimizer_preflight_excluded,
                "workers": request.workers, "resume_run_id": request.resume_run_id,
                "dataset_manifest": data_manifest,
                "data_routes": result.get("data_routes", []),
                "dataset_sha256": dataset_digest(data_manifest, request.timeframe, request.start_date, request.end_date),
                "execution_model": "JESSE_RESEARCH_PROVISIONAL",
                "execution_model_details": "Jesse research backtest uses Bid OHLC; contract-specific Bid/Ask triggers and broker accounting are not validated.",
                "engine": "Jesse research API via existing Docker service",
                "warmup_candles": int(result.get("warmup_candles", request.warmup_candles)),
                "source_root": str(DATA.source_root)}
    (RESULT_ROOT / run_id / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    local_run_dir = RUNS_ROOT / run_id
    local_run_dir.mkdir(parents=True, exist_ok=True)
    (local_run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    (local_run_dir / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    if result.get("trades"):
        pd.DataFrame(result["trades"]).to_csv(local_run_dir / "trades.csv", index=False)
    # Input files are retained with the run manifest for reproducibility.
    shutil.copy2(csv_path, local_run_dir / "input_candles.csv")
    _record_tester_ledger(request, available[request.strategy]["parameters"], parameters, result, manifest)
    return {"manifest": manifest, "result": {k: result.get(k) for k in ("metrics", "best_trials", "all_trials", "optimization", "trading_candles", "warmup_candles")},
            "trade_count": len(result.get("trades", [])), "event_count": event_count}


@app.post("/api/runs")
def create_run(request: RunRequest):
    # Register before data preparation (loading years of M1 can take a minute) so Annuleren works immediately.
    prep_cancel = threading.Event()
    with _ACTIVE_RUN_LOCK:
        registered = not _ACTIVE_RUN
        if registered:
            t0 = datetime.now(timezone.utc)
            _ACTIVE_RUN.update(run_id="preparing", strategy=request.strategy, action=request.action, source="tester",
                               started_at=t0.isoformat(), t0=t0, cancel=prep_cancel)
    try:
        return _execute_run(request, is_cancelled=prep_cancel.is_set)
    except RunCancelled as exc:
        raise HTTPException(409, "Backtest geannuleerd door de gebruiker; gedeeltelijke bestanden zijn opgeruimd.") from exc
    finally:
        with _ACTIVE_RUN_LOCK:
            if _ACTIVE_RUN.get("cancel") is prep_cancel:
                _ACTIVE_RUN.clear()


class JesseRunError(Exception):
    def __init__(self, status: int, message_nl: str, code: str | None = None, detail: str = ""):
        super().__init__(message_nl)
        self.status, self.message_nl, self.code, self.detail = status, message_nl, code, detail


def run_jesse_backtest(strategy: str, symbol: str, timeframe: str, start: str, end: str,
                       parameters: dict | None = None, fee: float = 0.0006, account_type: str = "spot", *,
                       starting_balance: float = 10000, leverage: float = 1, warmup_candles: int = 210,
                       is_cancelled=None, persist: bool = False, timeout: float | None = None) -> dict:
    """One real Jesse backtest through the runner; ``start``/``end`` are inclusive dates (YYYY-MM-DD).

    Raises RunCancelled when cancelled, JesseRunError (with a Dutch message) on any failure."""
    request = RunRequest(action="backtest", symbol=symbol, timeframe=timeframe, strategy=strategy,
                         start_date=start, end_date=end, parameters=dict(parameters or {}), fee=fee,
                         account_type=account_type, starting_balance=starting_balance, leverage=leverage,
                         warmup_candles=warmup_candles)
    try:
        return _execute_run(request, persist=persist, source="validation", is_cancelled=is_cancelled,
                            timeout=timeout)
    except HTTPException as exc:
        detail = str(exc.detail)
        found = diagnose_runner_failure(detail)
        raise JesseRunError(exc.status_code, found[1] if found else detail, found[0] if found else None,
                            detail) from exc


@app.get("/api/runs/active")
def active_run():
    with _ACTIVE_RUN_LOCK:
        active = dict(_ACTIVE_RUN)
    out = {"running": bool(active), "run_id": active.get("run_id"), "strategy": active.get("strategy"),
           "action": active.get("action"), "source": active.get("source"), "started_at": active.get("started_at"),
           "elapsed_s": round((datetime.now(timezone.utc) - active["t0"]).total_seconds(), 1) if active else None}
    if _LAST_CANCELLED:
        out["last_cancelled"] = dict(_LAST_CANCELLED)
    return out


@app.post("/api/runs/cancel")
def cancel_run():
    with _ACTIVE_RUN_LOCK:
        active = dict(_ACTIVE_RUN)
    if not active:
        raise HTTPException(409, "Er draait op dit moment geen Jesse-backtest om te annuleren.")
    active["cancel"].set()
    deadline = datetime.now(timezone.utc) + timedelta(seconds=40)
    stopped = False
    while datetime.now(timezone.utc) < deadline:
        with _ACTIVE_RUN_LOCK:
            if _ACTIVE_RUN.get("run_id") != active["run_id"]:
                stopped = True
                break
        threading.Event().wait(0.2)
    return {"cancelled": True, "stopped": stopped, "run_id": active["run_id"], "strategy": active.get("strategy"),
            "source": active.get("source"),
            "message_nl": "Backtest gestopt en gedeeltelijke bestanden opgeruimd." if stopped else
                          "Annulering aangevraagd; de backtest stopt binnen enkele seconden."}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    manifest = json.loads((_run_dir(run_id) / "manifest.json").read_text(encoding="utf-8"))
    result = _load_run(run_id)
    result["event_count"] = int(result.pop("event_count", len(result.pop("events", []))))
    result.pop("events", None)
    source_root = str(Path(manifest.get("source_root", "")).resolve())
    allowed_root = str(DATA.source_root.resolve())
    local_candles = RUNS_ROOT / run_id / "input_candles.csv"
    if source_root == allowed_root and local_candles.is_file():
        try:
            candles = pd.read_csv(local_candles, usecols=["timestamp", "high", "low"])
            result["trade_excursions"] = add_trade_excursions(result.get("trades", []), candles)
        except (OSError, ValueError, pd.errors.ParserError) as exc:
            result["trade_excursions"] = {"available": False,
                                          "reason": f"Saved Dukascopy candles could not be read: {exc}"}
    else:
        result["trade_excursions"] = {"available": False,
                                      "reason": "Saved Dukascopy candle copy or exact approved source manifest is missing."}
    return {"manifest": manifest, "result": result}


@app.get("/api/runs/{run_id}/interpretation")
def get_run_interpretation(run_id: str):
    manifest = json.loads((_run_dir(run_id) / "manifest.json").read_text(encoding="utf-8"))
    result = _load_run(run_id)
    extra = research_status(manifest, RUNS_ROOT, ledger=research_api.ledger(), lifecycle=research_api.lifecycle(),
                            forward_db=DEFAULT_DAILY_STORE)
    return interpret_run(manifest, result, extra)


@app.get("/api/inspect")
def inspect_candle(symbol: str, timeframe: str, timestamp_ms: int, run_id: str | None = None):
    ts = pd.to_datetime(timestamp_ms, unit="ms", utc=True)
    frequencies = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
                   "H1": "1h", "H4": "4h", "D1": "1D"}
    frequency = frequencies.get(timeframe.upper())
    if frequency is None:
        raise HTTPException(422, f"Unsupported timeframe: {timeframe}")
    candle_start = ts.floor(frequency)
    frame, _ = DATA.candles(symbol, timeframe, candle_start.isoformat(),
                            (candle_start + pd.Timedelta(frequency)).isoformat())
    if ts not in frame.index:
        return {"candle": None, "events": [], "message": "No Dukascopy candle at this UTC timestamp"}
    row = frame.loc[ts]
    result = {"candle": {"timestamp_ms": timestamp_ms, "timestamp_utc": ts.isoformat(),
               **{k: (None if pd.isna(v) else float(v)) for k, v in row.items()}}, "events": []}
    if run_id:
        result["events"] = [e for e in _iter_events(run_id, until_ms=timestamp_ms)
                            if int(e.get("timestamp_ms") or -1) == timestamp_ms]
        try:
            run_manifest = json.loads((_run_dir(run_id) / "manifest.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            run_manifest = {}
    else:
        run_manifest = {}
    result["state_snapshot"] = recorded_state_snapshot(result["events"], timestamp_ms,
                                                        symbol=symbol, timeframe=timeframe,
                                                        event_symbol=str(run_manifest.get("jesse_symbol") or symbol))
    if run_manifest and result["state_snapshot"].get("available"):
        result["state_snapshot"]["reexecution"] = _causal_reexecution(run_manifest, timestamp_ms,
                                                                        result["state_snapshot"])
    return result


@app.get("/api/runs/{run_id}/trades")
def trades(run_id: str):
    run = _load_run(run_id)
    return {"trades": run.get("trades", []), "links": event_links(_iter_events(run_id), run.get("trades", []))}


@app.get("/api/runs/{run_id}/replay-exits")
def replay_exits(run_id: str):
    return {"events": [event for event in _iter_events(run_id)
                       if (event.get("event_type") or event.get("event")) == "POSITION_CLOSE"]}


@app.post("/api/runs/{run_id}/bidask-ledger")
def bidask_ledger(run_id: str, request: LedgerRequest):
    from .execution import ContractSpec, apply_ledger, simulate_bidask
    spec = ContractSpec(**request.model_dump())
    try:
        spec.validate()
        manifest = json.loads((_run_dir(run_id) / "manifest.json").read_text(encoding="utf-8"))
        result = _load_run(run_id)
        start_ts = pd.to_datetime(manifest["start_date"], utc=True)
        end_ts = pd.to_datetime(_end_boundary(manifest["end_date"]), utc=True)
        frame, _ = DATA.candles(manifest["symbol"], manifest["timeframe"],
                                (start_ts - pd.Timedelta(days=90)).isoformat(), end_ts.isoformat())
        frame = frame.dropna(subset=["bid_open", "bid_high", "bid_low", "bid_close"])
        warmup_count = max(0, int(manifest.get("warmup_candles", 210)))
        warmup = frame.loc[frame.index < start_ts].tail(warmup_count)
        trading = frame.loc[(frame.index >= start_ts) & (frame.index < end_ts)]
        frame = pd.concat([warmup, trading])
        trades_out = result.get("trades", [])
        quotes = {}
        execution_candles = []
        columns = ("bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close")
        for timestamp, row in frame.iterrows():
            ts = int(timestamp.timestamp() * 1000)
            quote = {"timestamp_ms": ts, **{key: float(row[key]) for key in columns if pd.notna(row[key])}}
            execution_candles.append(quote)
            quotes[ts] = quote
        ledger = apply_ledger(trades_out, quotes, spec)
        strategy_name = manifest.get("strategy")
        signal_contract = (STRATEGY_ROOT / str(strategy_name) / "__init__.py"
                           if strategy_name and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(strategy_name)) else None)
        has_quote_signal = bool(signal_contract and signal_contract.is_file() and
                                "strategy_lab_quote_signal" in signal_contract.read_text(encoding="utf-8"))
        if has_quote_signal:
            execution_id = str(uuid.uuid4())
            request_file = REQUEST_ROOT / f"{execution_id}.json"
            result_dir = RESULT_ROOT / execution_id
            request_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"action": "bidask", "run_id": execution_id, "strategy": strategy_name,
                       "strategy_sha256": manifest.get("strategy_sha256", ""),
                       "dataset_sha256": manifest.get("dataset_manifest", {}).get("dataset_sha256", ""),
                       "parameters": manifest.get("parameters", {}), "contract": request.model_dump(),
                       "starting_balance": float(manifest["starting_balance"]),
                       "trade_start_ms": int(start_ts.timestamp() * 1000),
                       "quote_candles": execution_candles, "events": list(_iter_events(run_id))}
            request_file.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
            try:
                with RESEARCH_RUN_LOCK:
                    completed = subprocess.run(["docker", "compose", "-f", str(RUNTIME / "docker" / "docker-compose.yml"),
                        "exec", "-T", "jesse", "python", "/home/strategy_lab/jesse_runner.py",
                        f"/home/strategy_lab/requests/{execution_id}.json"], cwd=RUNTIME,
                        capture_output=True, text=True, timeout=900)
                result_path = result_dir / "result.json"
                if completed.returncode or not result_path.is_file():
                    raise RuntimeError("Dukascopy Bid/Ask strategy runner failed: " +
                                       (completed.stdout + "\n" + completed.stderr)[-3000:])
                independent = json.loads(result_path.read_text(encoding="utf-8"))
            finally:
                request_file.unlink(missing_ok=True)
                if result_dir.exists() and result_dir.parent.resolve() == RESULT_ROOT.resolve():
                    shutil.rmtree(result_dir)
        else:
            independent = simulate_bidask(execution_candles, list(_iter_events(run_id)), spec,
                manifest.get("parameters", {}), float(manifest["starting_balance"]),
                strategy_name=strategy_name, trade_start_ms=int(start_ts.timestamp() * 1000))
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc
    output = {"status": "DUKASCOPY_BIDASK_REPLAY", "contract_spec_status": "USER_SUPPLIED_NOT_BROKER_VERIFIED",
              "strategy_signals_recomputed": independent["strategy_signals_recomputed"],
              "note": f"JESSE_RESEARCH_PROVISIONAL contains Jesse fills. Timestamp reprice uses those existing times. DUKASCOPY_BIDASK_REPLAY: {independent['scope']}", "contract": request.model_dump(),
              "timestamp_reprice": {"status": "JESSE_RESEARCH_PROVISIONAL", "trades": ledger,
                  "net_pnl": sum(x.get("net_pnl", 0) for x in ledger if x.get("net_pnl") is not None)},
              "independent_model": independent}
    path = _run_dir(run_id) / "bidask_execution.json"
    path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return output


@app.get("/api/presets")
def presets():
    PRESET_ROOT.mkdir(parents=True, exist_ok=True)
    return {"presets": [json.loads(p.read_text(encoding="utf-8")) for p in PRESET_ROOT.glob("*.json")]}


@app.post("/api/presets")
def save_preset(request: PresetSave):
    PRESET_ROOT.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", request.name).strip("_")[:60]
    if not safe_name:
        raise HTTPException(422, "Preset name is required")
    item = {"name": request.name, "strategy": request.strategy, "symbol": request.symbol,
            "timeframe": request.timeframe, "parameters": request.parameters, "config": request.config,
            "saved_at": datetime.now(timezone.utc).isoformat()}
    (PRESET_ROOT / f"{safe_name}.json").write_text(json.dumps(item, indent=2), encoding="utf-8")
    return item


@app.get("/api/metrics/compare")
def compare_runs(first: str, second: str):
    a, b = _load_run(first), _load_run(second)
    return {"first": {"run_id": first, "metrics": a.get("metrics", {}), "trades": len(a.get("trades", []))},
            "second": {"run_id": second, "metrics": b.get("metrics", {}), "trades": len(b.get("trades", []))}}


@app.post("/api/portfolio/analysis")
def portfolio_analysis(request: PortfolioRequest):
    if not 2 <= len(request.runs) <= 12:
        raise HTTPException(422, "Select between 2 and 12 runs")
    if len({item.run_id for item in request.runs}) != len(request.runs):
        raise HTTPException(422, "Run IDs must be unique")
    inputs = []
    for item in request.runs:
        run_dir = _run_dir(item.run_id)
        try:
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            bidask = json.loads((run_dir / "bidask_execution.json").read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise HTTPException(422, f"Run {item.run_id} has no saved Bid/Ask portfolio result") from exc
        except json.JSONDecodeError as exc:
            raise HTTPException(422, f"Run {item.run_id} has malformed saved portfolio data") from exc
        inputs.append({"run_id": item.run_id, "allocated_capital": item.allocated_capital,
                       "manifest": manifest, "bidask": bidask})
    try:
        return analyze_portfolio(inputs)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class ForwardPortfolioRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    currency: str = Field(min_length=3, max_length=3)
    starting_capital: float
    risk_limit: float


class ForwardFreezeRequest(BaseModel):
    symbol: str
    parameters: dict
    contract: dict
    starting_capital: float
    trade_risk: float
    portfolio_risk_limit: float
    leverage: float = 1


class ForwardEnrollmentRequest(BaseModel):
    registration_id: uuid.UUID


class ForwardAdvanceRequest(BaseModel):
    max_days: int = Field(default=3, ge=1, le=31)


class ForwardReferenceRequest(BaseModel):
    start_day: date
    end_day: date


def _forward_engine() -> ForwardEngine:
    return ForwardEngine(ForwardRegistry(DailyPartitionStore(DEFAULT_DAILY_STORE)))


@app.get("/api/monitoring/alerts")
def monitoring_alerts():
    from .research.monitoring import forward_alerts
    try:
        config = load_sync_config(DEFAULT_SYNC_CONFIG)
    except (FileNotFoundError, DataError):
        config = None
    try:
        store = DailyPartitionStore(DEFAULT_DAILY_STORE)
        return {"alerts": forward_alerts(_forward_engine(), data_store=store, sync_config=config,
                                         now=datetime.now(timezone.utc))}
    except DataError as exc:
        return {"alerts": [{"kind": "MONITOR_ERROR", "severity": "warning", "message": str(exc)}]}


@app.get("/api/forward/portfolios")
def list_forward_portfolios():
    return {"portfolios": _forward_engine().portfolios()}


@app.post("/api/forward/portfolios", status_code=201)
def create_forward_portfolio(request: ForwardPortfolioRequest):
    try:
        return {"id": _forward_engine().create(**request.model_dump())}
    except DataError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/forward/registrations", status_code=201)
def freeze_forward_registration(request: ForwardFreezeRequest):
    try:
        source_symbol = provider_symbol(request.symbol)
        registration_time = datetime.now(timezone.utc)
        manifest = DATA.ensure(source_symbol).manifest
        engine = _forward_engine()
        sources = {LAB_SOURCE: (STRATEGY_ROOT / LAB_SOURCE).read_bytes()}
        config = {
            **request.model_dump(), "symbol": source_symbol, "strategy": "LabBreakout",
            "timeframe": "M1", "execution_model": MODEL, "indicators": {},
            "news_filter": {"enabled": False},
            "data_version": {
                "legacy_bid_sha256": manifest["bid_source"]["sha256"],
                "legacy_ask_sha256": manifest["ask_source"]["sha256"],
                "legacy_last_candle_at": manifest["last_utc"],
                "daily_watermark": engine.registry.data.watermark(source_symbol, as_of=registration_time),
            },
        }
        validate_registration({"configuration": config, "sources": sources})
        frozen = engine.registry.freeze(config, sources, frozen_at=registration_time)
        return {key: value for key, value in frozen.items() if key != "sources"}
    except (DataError, FileNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/forward/portfolios/{portfolio_id}/members", status_code=201)
def enroll_forward_member(portfolio_id: uuid.UUID, request: ForwardEnrollmentRequest):
    try:
        _forward_engine().enroll(str(portfolio_id), str(request.registration_id))
        return {"registration_id": str(request.registration_id), "status": "ENROLLED"}
    except DataError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/forward/portfolios/{portfolio_id}/advance")
def advance_forward_portfolio(portfolio_id: uuid.UUID, request: ForwardAdvanceRequest):
    try:
        return _forward_engine().advance(str(portfolio_id), max_days=request.max_days)
    except DataError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/forward/portfolios/{portfolio_id}")
def report_forward_portfolio(portfolio_id: uuid.UUID):
    try:
        engine = _forward_engine()
        report = engine.report(str(portfolio_id))
        model_current = report["portfolio"]["model_sha256"] == MODEL_SHA256
        data = engine.registry.data
        return {**report, "performance": summarize_forward(report, model_current=model_current, data=data),
                "historical_references": list_references(data, str(portfolio_id))}
    except DataError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/forward/portfolios/{portfolio_id}/correlations")
def forward_portfolio_correlations(portfolio_id: uuid.UUID, min_obs: int = Query(30, ge=3, le=10_000),
                                   threshold: float = Query(0.7, gt=0, le=1)):
    try:
        return forward_correlations(_forward_engine().report(str(portfolio_id)), min_obs=min_obs,
                                    threshold=threshold)
    except DataError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/forward/portfolios/{portfolio_id}/ledger")
def forward_portfolio_ledger(portfolio_id: uuid.UUID):
    try:
        return _forward_engine().verify_ledger(str(portfolio_id))
    except DataError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/forward/portfolios/{portfolio_id}/members/{registration_id}/historical-reference")
def create_forward_reference(portfolio_id: uuid.UUID, registration_id: uuid.UUID, request: ForwardReferenceRequest):
    if not RESEARCH_RUN_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Er draait al een onderzoekstaak. Probeer opnieuw als die klaar is.")
    try:
        return create_reference(_forward_engine(), str(portfolio_id), str(registration_id),
                                request.start_day, request.end_day, DATA)
    except DataError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        RESEARCH_RUN_LOCK.release()


def _port_free(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _research_jobs_running() -> bool:
    try:
        return any(job.get("status") in ("queued", "running") for job in research_api.JOBS.list(limit=50))
    except Exception:  # never let the watchdog crash the server
        return True


def _auto_reload_watchdog(server, port: int, interval: float = 10.0) -> None:
    """Restart this server process when backend code on disk changes.

    A successor is started only after the new code imports cleanly, and only while no backtest,
    optimization or research job is running; it waits for the port to be released."""
    import subprocess
    import sys
    import time
    pending = None
    while not server.should_exit:
        time.sleep(interval)
        disk = _fingerprint(_backend_files(), "strategy-lab-backend")
        if disk == LOADED_BACKEND_BUILD_ID:
            pending = None
            continue
        if disk != pending:  # wait one more interval so half-written files settle
            pending = disk
            continue
        with ACTIVE_RUNS_LOCK:
            busy = ACTIVE_RUNS > 0
        if busy or _research_jobs_running():
            continue
        check = subprocess.run([sys.executable, "-c", "import strategy_lab.server"], cwd=str(ROOT),
                               capture_output=True, timeout=120)
        if check.returncode != 0:
            continue  # broken code on disk: keep serving the working version
        env = {**os.environ, "STRATEGY_LAB_WAIT_PORT": "1", "STRATEGY_LAB_PORT": str(port)}
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        log = ROOT / "strategy_lab" / "data_cache" / "server_autoreload.log"
        with open(log, "ab") as out:
            subprocess.Popen([sys.executable, "-m", "strategy_lab.server"], cwd=str(ROOT), env=env,
                             stdout=out, stderr=out, creationflags=flags, close_fds=True)
        server.should_exit = True
        return


def main():
    import time
    import uvicorn
    port = int(os.environ.get("STRATEGY_LAB_PORT", "8767"))
    if os.environ.pop("STRATEGY_LAB_WAIT_PORT", None):
        deadline = time.monotonic() + 60
        while not _port_free(port) and time.monotonic() < deadline:
            time.sleep(0.5)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, reload=False))
    app.state.uvicorn_server = server
    if os.environ.get("STRATEGY_LAB_TRICKLE", "1") != "0":
        from . import trickle
        trickle.start()
    if os.environ.get("STRATEGY_LAB_AUTO_RELOAD", "1") != "0":
        threading.Thread(target=_auto_reload_watchdog, args=(server, port), daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()


