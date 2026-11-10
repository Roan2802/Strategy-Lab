import pytest

from runtime.strategy_lab.optimizer_space import (
    classify_trial_outcome,
    failed_trial_record,
    normalize_optimizer_space,
    training_split_index,
)


@pytest.fixture
def definitions():
    return [
        {"name": "mode", "type": "categorical", "options": [0, 1, 2],
         "default": 0, "account_options": {"spot": [0, 1], "futures": [0, 1, 2]}},
        {"name": "period", "type": "int", "min": 5, "max": 50, "step": 1, "default": 20},
        {"name": "risk", "type": "float", "min": 0.01, "max": 1.0, "step": 0.01, "default": 0.1},
    ]


def test_spot_space_excludes_short_category_and_narrows_numeric_bounds(definitions):
    controls = [
        {"name": "mode", "enabled": True, "options": [0, 1]},
        {"name": "period", "enabled": True, "min": 10, "max": 30, "step": 5},
        {"name": "risk", "enabled": True, "min": 0.01, "max": 0.05, "step": 0.01},
    ]
    specs, excluded = normalize_optimizer_space(definitions, controls, {}, "spot")
    assert specs[0]["options"] == [0, 1]
    assert specs[1]["min"] == 10 and specs[1]["max"] == 30 and specs[1]["step"] == 5
    assert specs[2]["max"] == 0.05
    assert excluded == {"mode": {"account_type": "spot", "excluded": [2]}}


def test_spot_rejects_incompatible_fixed_category(definitions):
    controls = [{"name": "mode", "enabled": False}, {"name": "period", "enabled": True},
                {"name": "risk", "enabled": True}]
    with pytest.raises(ValueError, match="infeasible"):
        normalize_optimizer_space(definitions, controls, {"mode": 2}, "spot")


@pytest.mark.parametrize("control", [
    {"name": "period", "enabled": True, "min": 4, "max": 30, "step": 1},
    {"name": "period", "enabled": True, "min": 10, "max": 30, "step": 0},
    {"name": "period", "enabled": True, "min": 10.5, "max": 30, "step": 1},
])
def test_integer_domain_rejects_expansion_or_invalid_step(definitions, control):
    controls = [{"name": "mode", "enabled": True, "options": [0, 1]}, control,
                {"name": "risk", "enabled": True}]
    with pytest.raises(ValueError):
        normalize_optimizer_space(definitions, controls, {}, "spot")


def test_disabled_parameter_uses_current_value_as_fixed_domain(definitions):
    controls = [{"name": "mode", "enabled": True, "options": [0, 1]},
                {"name": "period", "enabled": False}, {"name": "risk", "enabled": True}]
    specs, _ = normalize_optimizer_space(definitions, controls, {"period": 25}, "spot")
    assert specs[1]["fixed_value"] == 25
    assert specs[1]["options"] == [25]


def test_rejects_missing_duplicate_and_all_fixed_space(definitions):
    with pytest.raises(ValueError, match="Missing"):
        normalize_optimizer_space(definitions, [{"name": "mode"}], {}, "spot")
    duplicate = [{"name": "mode"}, {"name": "mode"}, {"name": "period"}, {"name": "risk"}]
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_optimizer_space(definitions, duplicate, {}, "spot")
    fixed = [{"name": "mode", "enabled": True, "options": [0]},
             {"name": "period", "enabled": True, "min": 20, "max": 20},
             {"name": "risk", "enabled": True, "min": 0.1, "max": 0.1}]
    with pytest.raises(ValueError, match="at least one"):
        normalize_optimizer_space(definitions, fixed, {}, "spot")


@pytest.mark.parametrize("count,percent,expected", [(10, 50, 5), (10, 80, 8), (10, 90, 9)])
def test_training_split_leaves_trailing_holdout(count, percent, expected):
    assert training_split_index(count, percent) == expected


@pytest.mark.parametrize("count,percent", [(1, 80), (10, 49), (10, 91), (10, float("nan"))])
def test_training_split_rejects_invalid_ranges(count, percent):
    with pytest.raises(ValueError):
        training_split_index(count, percent)


@pytest.mark.parametrize(("status", "reason", "metrics", "account_type", "expected"), [
    ("COMPLETED", None, {"net_profit": 25}, "spot", "COMPLETED_POSITIVE"),
    ("COMPLETED", None, {"net_profit": -25}, "spot", "COMPLETED_NEGATIVE"),
    ("COMPLETED", None, {"net_profit": 0}, "spot", "COMPLETED_BREAKEVEN"),
    ("COMPLETED", None, {"sharpe_ratio": -1}, "spot", "COMPLETED_SCORE_ONLY"),
    ("FILTERED", "no training trades", {}, "spot", "NO_TRAINING_TRADES"),
    ("ERROR", "InsufficientBalance: balance too low", {}, "spot", "INSUFFICIENT_SPOT_BALANCE_AT_ORDER"),
    ("ERROR", "InsufficientMargin: margin too low", {}, "futures", "INSUFFICIENT_FUTURES_MARGIN_AT_ORDER"),
    ("ERROR", "InvalidStrategy: should_short cannot be True on spot", {}, "spot", "INVALID_PARAMETER_COMBINATION"),
    ("ERROR", "RuntimeError: engine fault", {}, "spot", "TECHNICAL_ERROR"),
])
def test_trial_outcomes_preserve_distinct_optimizer_results(status, reason, metrics, account_type, expected):
    assert classify_trial_outcome(status, reason, metrics, account_type) == expected


def test_failed_holdout_trial_preserves_training_result_and_exact_error():
    training = {"net_profit": -125.5, "total": 4}
    parameters = {"position_size": 9.0}
    trial = failed_trial_record(2, "InsufficientBalance", "Available USD 10,000; requested USD 39,000",
                                "HOLDOUT", parameters, training, None, "spot")
    assert trial == {
        "trial": 2,
        "status": "ERROR",
        "outcome": "INSUFFICIENT_SPOT_BALANCE_AT_ORDER",
        "failure_phase": "HOLDOUT",
        "reason": "InsufficientBalance: Available USD 10,000; requested USD 39,000",
        "params": parameters,
        "training_metrics": training,
        "testing_metrics": None,
    }
