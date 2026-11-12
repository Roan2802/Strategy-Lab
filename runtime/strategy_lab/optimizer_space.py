"""Validate user-defined optimizer domains against strategy metadata."""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Any


def training_split_index(candle_count: int, train_percent: float) -> int:
    """Split ordered candles into training and trailing holdout portions."""
    try:
        percent = float(train_percent)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("train_percent must be between 50 and 90") from exc
    if not math.isfinite(percent) or not 50 <= percent <= 90:
        raise ValueError("train_percent must be between 50 and 90")
    if candle_count < 2:
        raise ValueError("At least two candles are required for training and holdout")
    return max(1, min(candle_count - 1, int(candle_count * percent / 100)))


def classify_trial_outcome(
    status: str,
    reason: str | None,
    training_metrics: dict[str, Any] | None,
    account_type: str,
) -> str:
    """Describe what happened without dropping failed or losing trials."""
    status = str(status or "").upper()
    reason = str(reason or "")
    if status == "FILTERED":
        return "NO_TRAINING_TRADES" if "no training trades" in reason.lower() else "FILTERED"
    if status == "ERROR":
        if reason.startswith("InsufficientMargin:"):
            return "INSUFFICIENT_FUTURES_MARGIN_AT_ORDER"
        if reason.startswith("InsufficientBalance:"):
            return ("INSUFFICIENT_SPOT_BALANCE_AT_ORDER" if str(account_type).lower() == "spot"
                    else "INSUFFICIENT_FUTURES_MARGIN_AT_ORDER")
        if reason.startswith("InvalidStrategy:") and (
            "should_short cannot be True" in reason or "incompatible with account_type" in reason
        ):
            return "INVALID_PARAMETER_COMBINATION"
        return "TECHNICAL_ERROR"
    try:
        net_profit = float((training_metrics or {}).get("net_profit"))
    except (TypeError, ValueError, OverflowError):
        return "COMPLETED_SCORE_ONLY"
    if not math.isfinite(net_profit):
        return "COMPLETED_SCORE_ONLY"
    if net_profit > 0:
        return "COMPLETED_POSITIVE"
    if net_profit < 0:
        return "COMPLETED_NEGATIVE"
    return "COMPLETED_BREAKEVEN"


def failed_trial_record(
    trial_number: int,
    exception_type: str,
    error: str,
    phase: str,
    parameters: dict[str, Any],
    training_metrics: dict[str, Any] | None,
    testing_metrics: dict[str, Any] | None,
    account_type: str,
) -> dict[str, Any]:
    """Keep exact failure context and any completed half of a split run."""
    reason = f"{exception_type}: {error}"
    return {
        "trial": trial_number,
        "status": "ERROR",
        "outcome": classify_trial_outcome("ERROR", reason, training_metrics, account_type),
        "failure_phase": phase,
        "reason": reason,
        "params": parameters,
        "training_metrics": training_metrics,
        "testing_metrics": testing_metrics,
    }


def _kind(spec: dict[str, Any]) -> str:
    value = spec.get("type")
    return value.__name__ if isinstance(value, type) else str(value).strip("'\"")


def _cast(value: Any, kind: str, name: str) -> Any:
    try:
        if kind == "int":
            number = float(value)
            if not math.isfinite(number) or not number.is_integer():
                raise ValueError
            return int(number)
        if kind == "float":
            number = float(value)
            if not math.isfinite(number):
                raise ValueError
            return number
        if kind == "bool":
            if isinstance(value, bool):
                return value
            if str(value).lower() in {"true", "1"}:
                return True
            if str(value).lower() in {"false", "0"}:
                return False
            raise ValueError
        if kind == "str":
            return str(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Invalid fixed value for {name}") from exc
    return value


def normalize_optimizer_space(
    definitions: list[dict[str, Any]],
    controls: list[dict[str, Any]] | None,
    parameters: dict[str, Any],
    account_type: str,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return Jesse-compatible domains and account-incompatible category removals.

    Numeric ranges may narrow, but never widen, the strategy's declared domain.
    A disabled parameter is represented as one fixed value, so every Jesse run
    still receives the full parameter dictionary.
    """
    defs = {str(item["name"]): item for item in deepcopy(definitions)}
    if controls is None:
        controls = [{"name": name, "enabled": True} for name in defs]
    if not isinstance(controls, list):
        raise ValueError("optimizer_space must be a list")
    by_name: dict[str, dict[str, Any]] = {}
    for control in controls:
        if not isinstance(control, dict) or not isinstance(control.get("name"), str):
            raise ValueError("Every optimizer-space entry needs a parameter name")
        name = control["name"]
        if name not in defs:
            raise ValueError(f"Unknown optimizer parameter: {name}")
        if name in by_name:
            raise ValueError(f"Duplicate optimizer parameter: {name}")
        by_name[name] = control
    if set(by_name) != set(defs):
        missing = sorted(set(defs) - set(by_name))
        raise ValueError("Missing optimizer parameters: " + ", ".join(missing))

    output: list[dict[str, Any]] = []
    excluded: dict[str, dict[str, Any]] = {}
    active_dimensions = 0
    account_type = str(account_type).lower()
    for name, definition in defs.items():
        control = by_name[name]
        enabled = control.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"Optimizer enabled flag for {name} must be boolean")
        kind = _kind(definition)
        spec = {key: value for key, value in definition.items()
                if key in {"name", "type", "min", "max", "step", "options", "default"}}
        spec["name"], spec["type"] = name, kind
        original_options = list(definition.get("options", []))
        account_options = definition.get("account_options")
        allowed_options = original_options
        if isinstance(account_options, dict) and account_type in account_options:
            permitted = account_options[account_type]
            allowed_options = [value for value in original_options
                               if value in permitted or str(value) in {str(item) for item in permitted}]
            removed = [value for value in original_options if value not in allowed_options]
            if removed:
                excluded[name] = {"account_type": account_type, "excluded": removed}

        if kind in {"bool", "str"} and original_options:
            kind = "categorical"
            spec["type"] = kind
            allowed_options = [value for value in original_options
                               if not isinstance(account_options, dict)
                               or account_type not in account_options
                               or value in account_options[account_type]
                               or str(value) in {str(item) for item in account_options[account_type]}]
            spec["options"] = allowed_options

        if enabled:
            if kind == "categorical":
                selected = control.get("options", allowed_options)
                if not isinstance(selected, list) or not selected:
                    raise ValueError(f"Choose at least one feasible category for {name}")
                if len({str(value) for value in selected}) != len(selected):
                    raise ValueError(f"Duplicate category selected for {name}")
                invalid = [value for value in selected
                           if not any(str(value) == str(candidate) for candidate in allowed_options)]
                if invalid:
                    raise ValueError(f"Infeasible category for {name} under account_type={account_type}: {invalid}")
                spec["options"] = [next(candidate for candidate in allowed_options
                                        if str(candidate) == str(value)) for value in selected]
                if len(spec["options"]) > 1:
                    active_dimensions += 1
                else:
                    spec["fixed_value"] = spec["options"][0]
            elif kind in {"int", "float"}:
                base_min, base_max = definition.get("min"), definition.get("max")
                if base_min is None or base_max is None:
                    raise ValueError(f"Strategy has no declared bounds for {name}")
                try:
                    low = _cast(control.get("min", base_min), kind, name)
                    high = _cast(control.get("max", base_max), kind, name)
                    raw_step = control.get("step", definition.get("step", 1 if kind == "int" else None))
                    step = _cast(raw_step, kind, name) if raw_step not in (None, "") else None
                except ValueError as exc:
                    raise ValueError(f"Invalid optimizer bounds for {name}") from exc
                if kind == "float" and (not math.isfinite(low) or not math.isfinite(high)
                                         or (step is not None and not math.isfinite(step))):
                    raise ValueError(f"Optimizer bounds for {name} must be finite")
                if low < base_min or high > base_max or low > high:
                    raise ValueError(f"Optimizer bounds for {name} must stay inside {base_min}–{base_max}")
                if kind == "int" and (step is None or step < 1):
                    raise ValueError(f"Integer optimizer step for {name} must be at least 1")
                if kind == "float" and step is not None and step <= 0:
                    raise ValueError(f"Float optimizer step for {name} must be positive or blank")
                spec.update(min=low, max=high, step=step)
                if low == high:
                    spec["fixed_value"] = low
                else:
                    active_dimensions += 1
            else:
                raise ValueError(f"Unsupported optimizer parameter type: {kind}")
        else:
            raw = parameters.get(name, definition.get("default"))
            if raw is None:
                raise ValueError(f"Fixed parameter {name} has no value or default")
            if kind == "categorical":
                match = next((candidate for candidate in allowed_options
                              if str(candidate) == str(raw)), None)
                if match is None:
                    raise ValueError(f"Fixed value for {name} is infeasible under account_type={account_type}")
                spec["options"] = [match]
                spec["fixed_value"] = match
            elif kind in {"int", "float", "bool", "str"}:
                value = _cast(raw, kind, name)
                if kind in {"int", "float"} and (
                    ("min" in definition and value < definition["min"])
                    or ("max" in definition and value > definition["max"])
                ):
                    raise ValueError(f"Fixed value for {name} is outside its declared domain")
                spec = {"name": name, "type": "categorical", "options": [value],
                        "fixed_value": value, "default": value}
            else:
                raise ValueError(f"Unsupported optimizer parameter type: {kind}")
        output.append(spec)

    if defs and active_dimensions == 0:
        raise ValueError("Enable at least one optimizer parameter with more than one value")
    return output, excluded
