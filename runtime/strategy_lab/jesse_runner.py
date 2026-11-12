"""Container-side bridge to Jesse's public research API (never live/session APIs)."""
from __future__ import annotations

import importlib
import json
import math
import os
import random
import re
import sys
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import jesse.helpers as jh
from jesse.research import backtest
import optuna
from optimizer_space import classify_trial_outcome, failed_trial_record, normalize_optimizer_space, training_split_index


ROOT = Path("/home/strategy_lab").resolve()
REQUESTS = (ROOT / "requests").resolve()
RESULTS = (ROOT / "results").resolve()
STRATEGIES = Path("/home/strategies").resolve()
PREFIXES = ("STRATEGY_LAB_EVENT ", "P0_INSPECTOR_JSON ")
JESSE_TIMEFRAMES = {"M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
                    "H1": "1h", "H4": "4h", "D1": "1D"}
TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30,
                     "1h": 60, "4h": 240, "1D": 1440}


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.ndarray):
        return clean(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def safe_path(base: Path, name: str) -> Path:
    path = (base / name).resolve()
    if base not in path.parents:
        raise ValueError(f"path escapes approved folder: {name}")
    return path


def load_rows(path: Path) -> np.ndarray:
    rows = []
    with path.open(encoding="utf-8-sig") as stream:
        header = stream.readline().strip().split(",")
        indices = {name: header.index(name) for name in ("timestamp", "open", "close", "high", "low", "volume")}
        for line in stream:
            cells = line.strip().split(",")
            rows.append([int(cells[indices["timestamp"]]), float(cells[indices["open"]]),
                         float(cells[indices["close"]]), float(cells[indices["high"]]),
                         float(cells[indices["low"]]), float(cells[indices["volume"]])])
    if not rows:
        raise ValueError(f"No candle rows in {path.name}")
    return np.asarray(rows, dtype=np.float64)


def bundle(exchange: str, symbol: str, candles: np.ndarray) -> dict:
    return {jh.key(exchange, symbol): {"exchange": exchange, "symbol": symbol, "candles": candles}}


def extract_events(logs: Any, run_id: str) -> list[dict]:
    events = []
    for line in logs or []:
        if isinstance(line, dict):
            line = line.get("message", line.get("log", json.dumps(line, default=str)))
        text = str(line)
        for prefix in PREFIXES:
            at = text.find(prefix)
            if at < 0:
                continue
            payload = text[at + len(prefix):].strip()
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            event.setdefault("run_id", run_id)
            event.setdefault("schema_version", "strategy-lab.events.v1")
            if "event_type" not in event:
                raw_type = event.get("event", "CANDLE_DECISION")
                event["event_type"] = "CANDLE_DECISION" if "decision" in event else raw_type
            if "timestamp_ms" not in event:
                event["timestamp_ms"] = event.get("fill_timestamp_ms", event.get("signal_timestamp_ms", event.get("closed_at_ms")))
            if "event_id" not in event:
                identity = event.get("order_id", event.get("trade_id", event.get("timestamp_ms", len(events))))
                event["event_id"] = f"{run_id}:{event['event_type']}:{identity}"
            events.append(clean(event))
            break
    return events


def flatten_trades(result: dict) -> list[dict]:
    output = []
    for row in result.get("trades", result.get("closed_trades", [])) or []:
        if isinstance(row, dict):
            output.append(clean(row))
    return output


def strategy_data_routes(strategy: type, request: dict, exchange: str, symbol: str) -> list[dict]:
    """Resolve optional causal higher-timeframe feeds declared by a strategy."""
    declared = getattr(strategy, "strategy_lab_data_timeframes", ())
    timeframes = declared(request.get("parameters", {})) if callable(declared) else declared
    if timeframes is None:
        timeframes = ()
    if isinstance(timeframes, str):
        timeframes = [timeframes]
    if not isinstance(timeframes, (list, tuple, set)):
        raise ValueError("strategy_lab_data_timeframes must return a sequence of timeframes")
    route_timeframe = str(request.get("timeframe", "M1")).upper()
    if timeframes and route_timeframe != "M1":
        raise ValueError("Supplemental timeframe feeds currently require an M1 strategy route")
    routes, seen = [], set()
    for value in timeframes:
        key = str(value).upper()
        timeframe = JESSE_TIMEFRAMES.get(key, str(value))
        if timeframe not in JESSE_TIMEFRAMES.values():
            raise ValueError(f"Unsupported strategy data timeframe: {value}")
        if timeframe == "1m" or timeframe in seen:
            continue
        seen.add(timeframe)
        routes.append({"exchange": exchange, "symbol": symbol, "timeframe": timeframe})
    return routes


def run(request: dict) -> dict:
    run_id = str(request["run_id"])
    os.environ["STRATEGY_LAB_RUN_ID"] = run_id
    os.environ["STRATEGY_LAB_STRATEGY_SHA256"] = str(request.get("strategy_sha256", ""))
    os.environ["STRATEGY_LAB_DATASET_SHA256"] = str(request.get("dataset_sha256", ""))
    strategy_name = str(request["strategy"])
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", strategy_name):
        raise ValueError("Invalid strategy module name")
    strategy_file = (STRATEGIES / strategy_name / "__init__.py").resolve()
    if STRATEGIES not in strategy_file.parents or not strategy_file.is_file():
        raise ValueError("Strategy not found under Jesse strategies folder")
    sys.path.insert(0, str(STRATEGIES))
    strategy_module = importlib.import_module(strategy_name)
    strategy = getattr(strategy_module, strategy_name, None)
    if strategy is None:
        classes = [getattr(strategy_module, k) for k in dir(strategy_module)
                   if isinstance(getattr(strategy_module, k), type)
                   and getattr(strategy_module, k).__module__ == strategy_module.__name__]
        strategy = classes[0] if classes else None
    if strategy is None:
        raise ValueError(f"No strategy class found in {strategy_name}")

    mode = request.get("action", "backtest")
    if mode == "bidask":
        from execution import ContractSpec, simulate_bidask
        target_path = safe_path(RESULTS, run_id)
        target_path.mkdir(parents=True, exist_ok=False)
        evaluator = getattr(strategy, "strategy_lab_quote_signal", None)
        indicator_spec_provider = getattr(strategy, "strategy_lab_quote_indicator_specs", None)
        indicator_specs = indicator_spec_provider(request.get("parameters", {})) if callable(indicator_spec_provider) else {}
        model = simulate_bidask(
            request["quote_candles"], request.get("events", []),
            ContractSpec(**request["contract"]), request.get("parameters", {}),
            float(request["starting_balance"]), strategy_name=strategy_name,
            trade_start_ms=int(request["trade_start_ms"]), signal_evaluator=evaluator,
            indicator_specs=indicator_specs,
        )
        payload = {**model, "run_id": run_id, "action": "bidask",
                   "strategy": strategy_name,
                   "strategy_quote_signal_supported": evaluator is not None}
        (target_path / "result.json").write_text(
            json.dumps(clean(payload), indent=2, allow_nan=False), encoding="utf-8")
        return clean(payload)

    source_path = safe_path(REQUESTS, request["candles_file"])
    candles = load_rows(source_path)
    start_ms, end_ms = int(request["start_ms"]), int(request["end_ms"])
    available_warmup = candles[candles[:, 0] < start_ms]
    trading = candles[(candles[:, 0] >= start_ms) & (candles[:, 0] < end_ms)]
    timeframe_minutes = TIMEFRAME_MINUTES[JESSE_TIMEFRAMES[str(request["timeframe"]).upper()]]
    # candles are 1-minute rows; warmup is requested in route-timeframe candles
    warm_count = int(request.get("warmup_candles", 210)) * timeframe_minutes
    if not len(trading):
        raise ValueError("Selected date range contains no Dukascopy candles")
    exchange, symbol = "Custom Data", str(request["jesse_symbol"])
    timeframe = JESSE_TIMEFRAMES[str(request["timeframe"]).upper()]
    route = [{"exchange": exchange, "symbol": symbol, "timeframe": timeframe, "strategy": strategy_name}]
    data_routes = strategy_data_routes(strategy, request, exchange, symbol)
    if data_routes:
        alignment = max(TIMEFRAME_MINUTES[row["timeframe"]] for row in data_routes)
        warm_count = ((warm_count + alignment - 1) // alignment) * alignment
    warmup = available_warmup[-warm_count:] if warm_count else available_warmup[:0]
    hp = request.get("parameters", {})
    base_config = {"starting_balance": float(request.get("starting_balance", 10000)),
                   "fee": float(request.get("fee", 0.0006)),
                   "type": request.get("account_type", "spot"),
                   "simulation_model": request.get("simulation_model", "perpetual_futures" if request.get("account_type") == "futures" else "spot"),
                   "annualization": 365, "exchange": exchange, "warm_up_candles": 0}
    if request.get("account_type") == "futures":
        base_config.update({"futures_leverage": float(request.get("leverage", 1)),
                            "futures_leverage_mode": "cross"})
    target_path = safe_path(RESULTS, run_id)
    target_path.mkdir(parents=True, exist_ok=False)
    event_stream_path = target_path / "events.jsonl"
    os.environ["STRATEGY_LAB_EVENTS_PATH"] = str(event_stream_path) if mode == "backtest" else os.devnull

    if mode == "optimize":
        train_percent = float(request.get("train_percent", 80))
        split = training_split_index(len(trading), train_percent)
        train, test = trading[:split], trading[split:]
        hp_specs = strategy.hyperparameters(None)
        metadata_path = safe_path(STRATEGIES / strategy_name, "parameter_metadata.json")
        parameter_metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
        definitions = []
        for source in hp_specs:
            spec = dict(source)
            spec.update(parameter_metadata.get(str(spec.get("name")), {}))
            kind = spec.get("type")
            spec["type"] = kind.__name__ if isinstance(kind, type) else str(kind).strip("'\"")
            definitions.append(spec)
        if request.get("optimizer_space_specs") is not None:
            hp_specs = request["optimizer_space_specs"]
            excluded_options = request.get("optimizer_preflight_excluded_options", {})
        else:
            hp_specs, excluded_options = normalize_optimizer_space(
                definitions, request.get("optimizer_space"), request.get("parameters", {}),
                str(request.get("account_type", "spot")),
            )
        objective = str(request.get("objective", "sharpe"))
        method = str(request.get("optimizer_method", "random")).lower()
        seed = int(request.get("seed", 42))
        total_trials = int(request.get("trials", 10))
        if method not in {"random", "grid", "tpe"}:
            raise ValueError("optimizer_method must be random, grid, or tpe")
        if int(request.get("workers", 1)) != 1:
            raise ValueError("workers must be 1: Jesse research backtests share process-global state")

        def normalized(spec):
            kind = spec.get("type")
            return kind.__name__ if isinstance(kind, type) else str(kind).strip("'\"")

        def suggest(trial):
            values = {}
            for spec in hp_specs:
                name, kind = str(spec["name"]), normalized(spec)
                if "fixed_value" in spec:
                    values[name] = spec["fixed_value"]
                    continue
                if kind == "int":
                    values[name] = trial.suggest_int(name, int(spec["min"]), int(spec["max"]), step=int(spec.get("step") or 1))
                elif kind == "float":
                    step = spec.get("step")
                    values[name] = trial.suggest_float(name, float(spec["min"]), float(spec["max"]), step=float(step) if step else None)
                elif kind == "categorical":
                    values[name] = trial.suggest_categorical(name, list(spec["options"]))
                else:
                    raise ValueError(f"Unsupported optimizer parameter type: {kind}")
            return values

        def grid_values(spec):
            if "fixed_value" in spec:
                return [spec["fixed_value"]]
            kind = normalized(spec)
            if kind == "categorical":
                return list(spec["options"])
            step = spec.get("step")
            if step:
                count = int((spec["max"] - spec["min"]) / step) + 1
                return [spec["min"] + i * step for i in range(count)]
            return [spec["min"], spec["max"]]

        study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
        all_trials = []
        resume_id = request.get("resume_run_id")
        if resume_id:
            if method == "tpe":
                raise ValueError("resume is supported for random and grid; Optuna trial state is not persisted between containers")
            previous_file = safe_path(RESULTS, str(resume_id)) / "result.json"
            previous = json.loads(previous_file.read_text(encoding="utf-8"))
            all_trials.extend(previous.get("all_trials", []))
            for item in all_trials:
                if item.get("status") in {"COMPLETED", "FILTERED"} and isinstance(item.get("score"), (int, float)):
                    try:
                        study.enqueue_trial(item.get("params", {}))
                    except Exception:
                        pass
        start_trial = len(all_trials)
        combinations = None
        if method == "grid":
            names = [str(x["name"]) for x in hp_specs]
            combinations = iter(dict(zip(names, combo)) for combo in __import__("itertools").islice(
                __import__("itertools").product(*(grid_values(x) for x in hp_specs)), start_trial, total_trials))

        metric_names = {"sharpe": "sharpe_ratio", "calmar": "calmar_ratio", "sortino": "sortino_ratio", "omega": "omega_ratio"}
        for index in range(start_trial, total_trials):
            trial_number = index
            trial = None
            params = {}
            phase = "PARAMETER_SAMPLING"
            train_metrics = None
            test_metrics = None
            try:
                rng = random.Random(seed + trial_number)
                if method == "grid":
                    params = next(combinations)
                elif method == "random":
                    params = {}
                    for spec in hp_specs:
                        name, kind = str(spec["name"]), normalized(spec)
                        if "fixed_value" in spec:
                            params[name] = spec["fixed_value"]
                            continue
                        if kind == "categorical": params[name] = rng.choice(spec["options"])
                        elif kind == "int":
                            step = int(spec.get("step") or 1); count = (int(spec["max"])-int(spec["min"])) // step + 1
                            params[name] = int(spec["min"]) + rng.randrange(count) * step
                        elif kind == "float":
                            step = spec.get("step")
                            params[name] = float(spec["min"]) + (rng.randrange(int((spec["max"]-spec["min"])/step)+1)*step if step else rng.random()*(spec["max"]-spec["min"]))
                else:
                    trial = study.ask()
                    params = suggest(trial)
                phase = "TRAINING"
                train_result = backtest(base_config, route, data_routes, bundle(exchange, symbol, train),
                                        bundle(exchange, symbol, warmup), hyperparameters=params,
                                        generate_csv=False, generate_json=False, generate_logs=False)
                train_metrics = clean(train_result.get("metrics", {}))
                phase = "HOLDOUT"
                test_result = backtest(base_config, route, data_routes, bundle(exchange, symbol, test),
                                       bundle(exchange, symbol, trading[max(0, split-warm_count):split]), hyperparameters=params,
                                       generate_csv=False, generate_json=False, generate_logs=False)
                test_metrics = clean(test_result.get("metrics", {}))
                metric = "net_profit" if objective == "recovery_factor" else metric_names.get(objective, "sharpe_ratio")
                score = float(train_metrics.get(metric) or 0)
                status, reason = ("FILTERED", "no training trades") if int(train_metrics.get("total") or 0) == 0 else ("COMPLETED", None)
                if objective == "recovery_factor":
                    dd = abs(float(train_metrics.get("max_drawdown") or 0))
                    score = score / (base_config["starting_balance"] * dd / 100) if dd else (score if score > 0 else 0)
                if method == "tpe":
                    study.tell(trial, score)
                outcome = classify_trial_outcome(status, reason, train_metrics, request.get("account_type", "spot"))
                all_trials.append({"trial": trial_number, "status": status, "outcome": outcome,
                                   "reason": reason, "params": params,
                                   "score": score, "objective": objective, "training_metrics": train_metrics,
                                   "testing_metrics": test_metrics})
            except StopIteration:
                break
            except Exception as exc:
                if method == "tpe" and trial is not None:
                    try:
                        study.tell(trial, 0.0)
                    except Exception:
                        pass
                all_trials.append(failed_trial_record(trial_number, type(exc).__name__, str(exc), phase,
                                                     params, train_metrics, test_metrics,
                                                     request.get("account_type", "spot")))
        best = sorted((x for x in all_trials if x.get("status") == "COMPLETED"),
                      key=lambda x: float(x.get("score") or 0), reverse=True)[:5]
        outcome_counts = {}
        for item in all_trials:
            key = str(item.get("outcome", "UNKNOWN"))
            outcome_counts[key] = outcome_counts.get(key, 0) + 1
        optimization = {"total_trials": total_trials, "completed_trials": len(all_trials), "objective_function": objective,
                        "method": method, "seed": seed, "workers": 1, "resumed_from": resume_id,
                        "train_percent": train_percent, "holdout_percent": 100 - train_percent,
                        "training_candles": len(train), "holdout_candles": len(test),
                        "preflight_excluded_options": excluded_options, "outcome_counts": outcome_counts,
                        "search_space": clean(hp_specs)}
        payload = {"run_id": run_id, "action": "optimize", "optimization": optimization,
                   "best_trials": clean(best), "all_trials": clean(all_trials), "metrics": {}, "trades": [], "events": [],
                   "trading_candles": len(trading), "warmup_candles": len(warmup),
                   "data_routes": clean(data_routes)}
    elif mode == "backtest":
        result = backtest(base_config, route, data_routes, bundle(exchange, symbol, trading),
                          bundle(exchange, symbol, warmup),
                          hyperparameters=hp, generate_csv=True, generate_json=True, generate_logs=True)
        trades = flatten_trades(result)
        logs = result.get("logs", [])
        if isinstance(logs, str):
            log_path = Path(logs)
            logs = log_path.read_text(encoding="utf-8", errors="replace").splitlines() if log_path.is_file() else [logs]
        if event_stream_path.is_file():
            with event_stream_path.open("r", encoding="utf-8") as stream:
                event_count = sum(1 for _ in stream)
        else:
            events = extract_events(logs, run_id)
            event_count = len(events)
            if events:
                event_stream_path.write_text("".join(json.dumps(event, separators=(",", ":"), allow_nan=False) + "\n"
                                                   for event in events), encoding="utf-8")
        payload = {"run_id": run_id, "action": "backtest", "metrics": clean(result.get("metrics", {})),
                   "trades": trades, "event_count": event_count, "logs": [str(x) for x in (logs or [])],
                   "trading_candles": len(trading), "warmup_candles": len(warmup),
                   "strategy": strategy_name, "strategy_file": str(strategy_file), "hyperparameters": hp,
                   "data_routes": clean(data_routes)}
    else:
        raise ValueError(f"Unsupported action: {mode}")
    payload = clean(payload)
    (target_path / "result.json").write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    return payload


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: jesse_runner.py REQUEST.json")
    req_path = Path(sys.argv[1]).resolve()
    if REQUESTS not in req_path.parents:
        raise SystemExit("request path outside approved folder")
    try:
        request = json.loads(req_path.read_text(encoding="utf-8"))
        result = run(request)
        print(json.dumps({"run_id": result.get("run_id"), "success": True,
                          "metrics": result.get("metrics", {}), "trade_count": len(result.get("trades", [])),
                          "event_count": int(result.get("event_count", len(result.get("events", []))))}, separators=(",", ":"), allow_nan=False))
    except Exception as exc:
        print(json.dumps({"error": str(exc), "traceback": traceback.format_exc()}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
