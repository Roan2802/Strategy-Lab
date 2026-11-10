"""Versioned, append-only strategy event helpers for Jesse strategy classes."""
from __future__ import annotations

import json
import math
import os
import atexit
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "strategy-lab.events.v1"
_EVENT_STREAM_PATH: str | None = None
_EVENT_STREAM = None


def _close_event_stream() -> None:
    global _EVENT_STREAM, _EVENT_STREAM_PATH
    if _EVENT_STREAM is not None:
        _EVENT_STREAM.close()
    _EVENT_STREAM = None
    _EVENT_STREAM_PATH = None


atexit.register(_close_event_stream)


def _clean(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _read_value(value: Any) -> Any:
    return value() if callable(value) else value


def _compare(actual: Any, operator: str, threshold: Any) -> bool:
    op = str(operator).strip().upper()
    if op in {"==", "EQ", "EQUALS"}: return actual == threshold
    if op in {"!=", "NE", "NOT_EQUALS"}: return actual != threshold
    if op in {">", "GT"}: return actual > threshold
    if op in {">=", "GE"}: return actual >= threshold
    if op in {"<", "LT"}: return actual < threshold
    if op in {"<=", "LE"}: return actual <= threshold
    if op in {"TRUTHY", "IS_TRUE"}: return bool(actual)
    if op in {"FALSEY", "IS_FALSE"}: return not bool(actual)
    if op == "BETWEEN": return threshold[0] <= actual <= threshold[1]
    if op == "OUTSIDE": return actual < threshold[0] or actual > threshold[1]
    if op == "IN": return actual in threshold
    if op == "NOT_IN": return actual not in threshold
    if op == "CONTAINS": return threshold in actual
    if op == "CROSSES_ABOVE":
        return actual["previous"] <= threshold["previous"] and actual["current"] > threshold["current"]
    if op == "CROSSES_BELOW":
        return actual["previous"] >= threshold["previous"] and actual["current"] < threshold["current"]
    raise ValueError(f"Unsupported debug condition operator: {operator}")


class StrategyDebugCandle:
    """Build and emit a structured, single-candle decision trace.

    Values are supplied by the strategy, so computed indicators are recorded
    rather than recalculated. Callable values are evaluated only when their
    condition is reached, allowing groups to record true short-circuit SKIPs.
    """
    def __init__(self, strategy: Any, context: dict | None = None):
        self.strategy = strategy
        self.context = dict(context or {})
        self.checks: list[dict] = []
        self.indicators: list[dict] = []
        self.states: dict[str, Any] = {}
        self.signal_value: dict | None = None
        self._results: dict[str, str] = {}
        self._evaluated: bool | None = None
        self._decision: str | None = None
        self._reason: str | None = None
        self._finished = False

    def __enter__(self): return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.finish()
        return False

    @staticmethod
    def condition(name: str, actual: Any, operator: str = "TRUTHY", threshold: Any = True,
                  *, depends_on: list[str] | None = None, timeframe: str | None = None,
                  unit: str | None = None, description: str | None = None,
                  skip_when_false: bool = False) -> dict:
        return {"kind": "condition", "name": str(name), "actual": actual,
                "operator": operator, "threshold": threshold,
                "depends_on": list(depends_on or []), "timeframe": timeframe,
                "unit": unit, "description": description,
                "skip_when_false": bool(skip_when_false)}

    @staticmethod
    def group(name: str, logic: str, conditions: list[dict], *,
              stop_on_skip: bool = False) -> dict:
        logic = str(logic).lower()
        if logic not in {"all", "any"}:
            raise ValueError("debug group logic must be 'all' or 'any'")
        return {"kind": "group", "name": str(name), "logic": logic,
                "conditions": list(conditions), "stop_on_skip": bool(stop_on_skip)}

    def indicator(self, name: str, value: Any, *, timeframe: str | None = None,
                  unit: str | None = None, source: str | None = None) -> Any:
        record = {"name": str(name), "value": _read_value(value),
                  "timeframe": timeframe, "unit": unit, "source": source}
        identity = (record["name"], timeframe)
        for index, existing in enumerate(self.indicators):
            if (existing["name"], existing.get("timeframe")) == identity:
                self.indicators[index] = record
                return record["value"]
        self.indicators.append(record)
        return record["value"]

    def state(self, name: str, value: Any) -> Any:
        self.states[str(name)] = _read_value(value)
        return self.states[str(name)]

    def signal(self, side: str, reason: str, *, order_type: str | None = None,
               price: Any = None, depends_on: list[str] | None = None) -> dict:
        normalized = str(side).upper()
        if normalized not in {"BUY", "SELL", "LONG", "SHORT", "NONE", "HOLD"}:
            raise ValueError("signal side must be BUY, SELL, LONG, SHORT, NONE, or HOLD")
        blocked = [str(item) for item in (depends_on or []) if self._results.get(str(item)) != "PASS"]
        self.signal_value = {"side": "NONE" if blocked else normalized, "reason": str(reason),
                             "order_type": order_type, "price": _read_value(price),
                             "depends_on": list(depends_on or []), "blocked_by": blocked,
                             "attempted_side": normalized if blocked else None}
        if not blocked: self._reason = str(reason)
        return self.signal_value

    def _record_skip(self, node: dict, *, blocked_by: str | None = None) -> None:
        name = str(node.get("name", "condition"))
        if node.get("kind") == "group":
            for child in node.get("conditions", []): self._record_skip(child, blocked_by=blocked_by)
            record = {"name": name, "operator": node.get("logic", "all").upper(),
                      "result": "SKIP", "evaluated": False, "blocked_by": blocked_by}
        else:
            record = {key: value for key, value in node.items() if key not in {"kind", "actual"}}
            record.update({"name": name, "actual": None, "result": "SKIP",
                           "evaluated": False, "blocked_by": blocked_by})
        self.checks.append(_clean(record)); self._results[name] = "SKIP"

    def _evaluate(self, node: dict) -> bool | None:
        name = str(node.get("name", "condition"))
        if node.get("kind") == "group":
            logic = node["logic"]
            values: list[bool | None] = []
            short_circuit = False
            for child in node.get("conditions", []):
                if short_circuit:
                    self._record_skip(child, blocked_by=name)
                    continue
                value = self._evaluate(child); values.append(value)
                short_circuit = ((logic == "all" and value is False) or
                                 (logic == "any" and value is True) or
                                 (node.get("stop_on_skip", False) and value is None))
            outcome = (False if any(value is False for value in values) else
                       None if any(value is None for value in values) else True) if logic == "all" else (
                       True if any(value is True for value in values) else
                       None if any(value is None for value in values) else False)
            result = "SKIP" if outcome is None else "PASS" if outcome else "FAIL"
            self.checks.append({"name": name, "operator": logic.upper(), "result": result,
                                "evaluated": True, "children": [str(c.get("name", "condition"))
                                for c in node.get("conditions", [])]})
            self._results[name] = result
            return outcome
        blocked = [item for item in node.get("depends_on", []) if self._results.get(str(item)) != "PASS"]
        if blocked:
            record = {key: value for key, value in node.items() if key not in {"kind", "actual"}}
            record.update({"actual": None, "result": "SKIP", "evaluated": False,
                           "blocked_by": blocked})
            self.checks.append(_clean(record)); self._results[name] = "SKIP"
            return None
        actual, threshold = _read_value(node.get("actual")), _read_value(node.get("threshold"))
        if actual is None or (threshold is None and str(node.get("operator", "TRUTHY")).upper() not in {"TRUTHY", "FALSEY", "IS_TRUE", "IS_FALSE"}):
            record = {key: value for key, value in node.items() if key not in {"kind", "actual"}}
            record.update({"actual": actual, "threshold": threshold, "result": "SKIP",
                           "evaluated": False, "skip_reason": "MISSING_COMPARISON_VALUE"})
            self.checks.append(_clean(record)); self._results[name] = "SKIP"
            return None
        outcome = _compare(actual, node.get("operator", "TRUTHY"), threshold)
        skipped = not outcome and bool(node.get("skip_when_false", False))
        result = "SKIP" if skipped else "PASS" if outcome else "FAIL"
        record = {key: value for key, value in node.items() if key not in {"kind", "actual"}}
        record.update({"actual": actual, "threshold": threshold, "result": result, "evaluated": True})
        if skipped: record["skip_reason"] = "FALSE_CONDITION_CONFIGURED_AS_SKIP"
        self.checks.append(_clean(record)); self._results[name] = result
        return None if skipped else outcome

    def evaluate(self, expression: dict, *, reason: str | None = None) -> bool | None:
        if self._evaluated is not None:
            raise RuntimeError("Only one root decision expression may be evaluated per candle")
        self._evaluated = self._evaluate(expression)
        self._decision = "SKIP" if self._evaluated is None else "PASS" if self._evaluated else "FAIL"
        self._reason = reason or ("ENTRY_CONDITIONS_MET" if self._evaluated else
                                  "ENTRY_CONDITION_SKIPPED" if self._evaluated is None else "ENTRY_CONDITION_FAILED")
        return self._evaluated

    def finish(self, *, decision: str | None = None, reason: str | None = None) -> dict | None:
        if self._finished: return None
        if self._evaluated is None and self._decision is None: self._decision = "SKIP"
        final_decision = decision or self._decision or "SKIP"
        final_reason = reason or self._reason or "NO_DEBUG_DECISION"
        event = candle_event(run_id=getattr(self.strategy, "_strategy_lab_run_id",
                                os.environ.get("STRATEGY_LAB_RUN_ID", "unassigned")),
            symbol=str(self.strategy.symbol), timeframe=str(self.strategy.timeframe),
            timestamp_ms=int(self.strategy.current_candle[0]), strategy=type(self.strategy).__name__,
            parameters=dict(self.strategy.hp), checks=self.checks, decision=final_decision,
            reason=final_reason, state=self.states, indicators=self.indicators,
            signal=self.signal_value, context=self.context,
            strategy_sha256=os.environ.get("STRATEGY_LAB_STRATEGY_SHA256"),
            dataset_sha256=os.environ.get("STRATEGY_LAB_DATASET_SHA256"))
        emit(self.strategy, event); self._finished = True
        return event


def candle_event(*, run_id: str, symbol: str, timeframe: str, timestamp_ms: int,
                 strategy: str, parameters: dict, checks: list[dict], decision: str,
                 reason: str, state: dict | None = None,
                 indicators: list[dict] | None = None, signal: dict | None = None,
                 context: dict | None = None, strategy_sha256: str | None = None,
                 dataset_sha256: str | None = None) -> dict:
    if decision not in {"PASS", "FAIL", "SKIP"}:
        raise ValueError("decision must be PASS, FAIL, or SKIP")
    if not run_id or not symbol or not strategy:
        raise ValueError("run_id, symbol and strategy are required")
    return _clean({
        "schema_version": SCHEMA_VERSION, "event_type": "CANDLE_DECISION",
        "event_id": f"{run_id}:{symbol}:{timeframe}:{int(timestamp_ms)}:decision",
        "run_id": run_id, "timestamp_ms": int(timestamp_ms),
        "timestamp_utc": datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat(),
        "symbol": symbol, "timeframe": timeframe, "strategy": strategy,
        "parameters": parameters, "checks": checks, "decision": decision,
        "reason": reason, "state": state or {}, "indicators": indicators or [],
        "signal": signal, "context": context or {},
        "strategy_sha256": strategy_sha256, "dataset_sha256": dataset_sha256,
    })


def lifecycle_event(*, run_id: str, event_type: str, timestamp_ms: int,
                    symbol: str, timeframe: str, order_id: str | None = None,
                    trade_id: str | None = None, position_id: str | None = None,
                    **fields: Any) -> dict:
    if event_type not in {"ORDER_SUBMITTED", "ORDER_CANCEL_REQUEST", "ORDER_CANCELLED",
                          "ORDER_FILLED", "POSITION_OPEN", "POSITION_CLOSE"}:
        raise ValueError(f"Unsupported lifecycle event: {event_type}")
    return _clean({"schema_version": SCHEMA_VERSION, "event_type": event_type,
                   "event_id": f"{run_id}:{event_type}:{order_id or trade_id or timestamp_ms}",
                   "run_id": run_id, "timestamp_ms": int(timestamp_ms),
                   "timestamp_utc": datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat(),
                   "symbol": symbol, "timeframe": timeframe, "order_id": order_id,
                   "trade_id": trade_id, "position_id": position_id, **fields})


def emit(strategy, event: dict) -> None:
    """Stream events to JSONL during research runs; retain log fallback elsewhere."""
    global _EVENT_STREAM, _EVENT_STREAM_PATH
    target = os.environ.get("STRATEGY_LAB_EVENTS_PATH")
    if target:
        if target != _EVENT_STREAM_PATH:
            _close_event_stream()
            os.makedirs(os.path.dirname(target), exist_ok=True)
            _EVENT_STREAM = open(target, "a", encoding="utf-8", buffering=1024 * 1024)
            _EVENT_STREAM_PATH = target
        _EVENT_STREAM.write(json.dumps(event, separators=(",", ":"), allow_nan=False) + "\n")
        return
    strategy.log("STRATEGY_LAB_EVENT " + json.dumps(event, separators=(",", ":"), allow_nan=False))


class InspectorMixin:
    """Mixin for user strategies; call `record_candle(...)` from `before()`.

    Example: `self.record_candle(checks=[...], decision='FAIL', reason='ATR_LOW')`.
    Jesse strategy hooks still own order placement and fills; call
    `record_lifecycle(...)` next to each order/position hook for IDs and timing.
    """
    def debug_candle(self, *, context: dict | None = None) -> StrategyDebugCandle:
        """Start the standard structured debug contract for the current candle."""
        return StrategyDebugCandle(self, context)

    def record_candle(self, *, checks: list[dict], decision: str, reason: str,
                      state: dict | None = None) -> dict:
        timestamp_ms = int(self.current_candle[0])
        run_id = getattr(self, "_strategy_lab_run_id", os.environ.get("STRATEGY_LAB_RUN_ID", "unassigned"))
        event = candle_event(run_id=run_id, symbol=str(self.symbol), timeframe=str(self.timeframe),
                             timestamp_ms=timestamp_ms, strategy=type(self).__name__,
                             parameters=dict(self.hp), checks=checks, decision=decision,
                             reason=reason, state=state,
                             strategy_sha256=os.environ.get("STRATEGY_LAB_STRATEGY_SHA256"),
                             dataset_sha256=os.environ.get("STRATEGY_LAB_DATASET_SHA256"))
        emit(self, event)
        return event

    def record_lifecycle(self, *, event_type: str, order_id: str | None = None,
                         trade_id: str | None = None, position_id: str | None = None,
                         timestamp_ms: int | None = None, **fields: Any) -> dict:
        event = lifecycle_event(run_id=getattr(self, "_strategy_lab_run_id", os.environ.get("STRATEGY_LAB_RUN_ID", "unassigned")),
                                 event_type=event_type, timestamp_ms=timestamp_ms or int(self.current_candle[0]),
                                 symbol=str(self.symbol), timeframe=str(self.timeframe),
                                 order_id=order_id, trade_id=trade_id,
                                 position_id=position_id,
                                 strategy_sha256=os.environ.get("STRATEGY_LAB_STRATEGY_SHA256"),
                                 dataset_sha256=os.environ.get("STRATEGY_LAB_DATASET_SHA256"), **fields)
        emit(self, event)
        return event
