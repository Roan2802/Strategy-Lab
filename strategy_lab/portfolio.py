"""Compatibility-gated analysis of saved Dukascopy Bid/Ask research runs."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
from typing import Any

from runtime.strategy_lab.execution import ContractSpec


APPROVED_SOURCE_ROOT = r"C:\AI\Dukascopy_data"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _time_ms(trade: dict, field: str) -> int | None:
    value = _number(trade.get(field))
    return int(value) if value is not None and value > 0 else None


def analyze_portfolio(inputs: list[dict]) -> dict:
    """Combine run P&L only when saved model, currency, and capital are compatible.

    The result stays a research-model portfolio: this function never promotes
    user-supplied contract terms to broker-verified execution.
    """
    if not 2 <= len(inputs) <= 12:
        raise ValueError("Select between 2 and 12 unique runs")
    run_ids = [str(item.get("run_id", "")) for item in inputs]
    if any(not run_id for run_id in run_ids) or len(set(run_ids)) != len(run_ids):
        raise ValueError("Run IDs must be present and unique")

    blockers: list[dict] = []
    summaries = []
    normalized = []
    currency_set, execution_statuses, signal_modes, policies = set(), set(), set(), set()
    for item in inputs:
        manifest = item.get("manifest") or {}
        saved = item.get("bidask") or {}
        model = saved.get("independent_model") or {}
        run_id = str(item["run_id"])
        allocation = _number(item.get("allocated_capital"))
        starting_balance = _number(manifest.get("starting_balance"))
        contract_values = saved.get("contract") or {}
        summary = {"run_id": run_id, "strategy": manifest.get("strategy"),
                   "symbol": manifest.get("symbol"), "timeframe": manifest.get("timeframe"),
                   "allocated_capital": allocation, "run_starting_balance": starting_balance,
                   "status": saved.get("status"), "trade_count": len(model.get("trades", [])),
                   "net_pnl": _number(model.get("net_profit")),
                   "strategy_signals_recomputed": model.get("strategy_signals_recomputed")}
        summaries.append(summary)

        def block(code: str, message: str) -> None:
            blockers.append({"run_id": run_id, "code": code, "message": message})

        if manifest.get("action") != "backtest":
            block("NOT_A_BACKTEST", "Portfolio analysis accepts completed backtest runs only.")
        if str(manifest.get("source_root", "")).rstrip("\\/").casefold() != APPROVED_SOURCE_ROOT.casefold():
            block("NON_APPROVED_DATA_ROOT", "Run is not sourced exclusively from the approved Dukascopy directory.")
        if saved.get("status") != "DUKASCOPY_BIDASK_REPLAY":
            block("MISSING_BIDASK_MODEL", "Run has no saved Dukascopy Bid/Ask execution model.")
        if saved.get("contract_spec_status") != "USER_SUPPLIED_NOT_BROKER_VERIFIED":
            block("INCOMPATIBLE_CONTRACT_STATUS", "Contract validation status is absent or incompatible.")
        try:
            ContractSpec(**contract_values).validate()
        except (TypeError, ValueError) as exc:
            block("INVALID_CONTRACT", f"Saved contract settings are incomplete or invalid: {exc}")
        else:
            currency_set.add(str(contract_values.get("account_currency", "")).upper())
        execution_statuses.add(saved.get("contract_spec_status"))
        signal_modes.add(model.get("strategy_signals_recomputed"))
        policies.add(model.get("execution_policy"))
        if model.get("quote_quality") != "COMPLETE_QUOTES":
            block("INCOMPLETE_QUOTES", "A run contains skipped or incomplete Bid/Ask quote bars.")
        if allocation is None or allocation <= 0 or starting_balance is None:
            block("INVALID_ALLOCATION", "Enter positive capital equal to the run's saved starting balance.")
        elif not math.isclose(allocation, starting_balance, rel_tol=1e-9, abs_tol=1e-8):
            block("ALLOCATION_REQUIRES_RERUN", "Allocation must equal the saved backtest starting balance; rerun the strategy for a different capital allocation.")

        trades = []
        for trade in model.get("trades", []):
            pnl = _number(trade.get("net_pnl"))
            opened = _time_ms(trade, "entry_timestamp_ms")
            closed = _time_ms(trade, "exit_timestamp_ms")
            if pnl is None or opened is None or closed is None or closed < opened:
                block("INVALID_TRADE_SERIES", "A modeled trade lacks finite P&L or valid entry/exit timestamps.")
                continue
            trades.append({**trade, "net_pnl": pnl, "entry_timestamp_ms": opened,
                           "exit_timestamp_ms": closed, "run_id": run_id,
                           "strategy": manifest.get("strategy"), "symbol": manifest.get("symbol")})
        normalized.append({"run_id": run_id, "manifest": manifest, "trades": trades,
                           "allocation": allocation})

    if len(currency_set) > 1:
        blockers.append({"run_id": None, "code": "ACCOUNT_CURRENCY_MISMATCH",
                         "message": "All runs must convert P&L into the same account currency."})
    if len(execution_statuses) > 1:
        blockers.append({"run_id": None, "code": "EXECUTION_STATUS_MISMATCH",
                         "message": "Runs must use the same contract-verification status."})
    if len(signal_modes) > 1:
        blockers.append({"run_id": None, "code": "SIGNAL_MODEL_MISMATCH",
                         "message": "Runs must either all replay strategy signals or all use saved order intents."})
    if len(policies) > 1:
        blockers.append({"run_id": None, "code": "EXECUTION_POLICY_MISMATCH",
                         "message": "Runs must use the same Bid/Ask execution policy."})
    if blockers:
        return {"status": "COMPATIBILITY_BLOCKED", "combined_financials_available": False,
                "account_currency": next(iter(currency_set)) if len(currency_set) == 1 else None,
                "runs": summaries, "blockers": blockers,
                "note": "Independent run summaries are informational only; no portfolio P&L was combined."}

    trades = [trade for run in normalized for trade in run["trades"]]
    total_capital = sum(run["allocation"] for run in normalized)
    daily_by_run = {run["run_id"]: defaultdict(float) for run in normalized}
    groups: dict[str, dict[str, dict]] = {key: defaultdict(lambda: {"trade_count": 0, "wins": 0, "losses": 0, "net_pnl": 0.0})
                                             for key in ("strategy", "instrument", "month")}
    pnl_at_time: dict[int, float] = defaultdict(float)
    loss_intervals = []
    for trade in trades:
        close = trade["exit_timestamp_ms"]
        pnl = trade["net_pnl"]
        date = datetime.fromtimestamp(close / 1000, timezone.utc).date().isoformat()
        daily_by_run[trade["run_id"]][date] += pnl
        pnl_at_time[close] += pnl
        month = date[:7]
        for key, value in (("strategy", str(trade.get("strategy") or "Unknown")),
                           ("instrument", str(trade.get("symbol") or "Unknown")),
                           ("month", month)):
            group = groups[key][value]
            group["trade_count"] += 1
            group["wins"] += int(pnl > 0)
            group["losses"] += int(pnl < 0)
            group["net_pnl"] += pnl
        if pnl < 0 and close > trade["entry_timestamp_ms"]:
            loss_intervals.append((trade["entry_timestamp_ms"], close))

    equity = total_capital
    peak = equity
    max_drawdown_money = 0.0
    max_drawdown_percent = 0.0
    equity_curve = [{"timestamp_ms": None, "equity": equity}]
    for stamp in sorted(pnl_at_time):
        equity += pnl_at_time[stamp]
        peak = max(peak, equity)
        drawdown = max(0.0, peak - equity)
        max_drawdown_money = max(max_drawdown_money, drawdown)
        max_drawdown_percent = max(max_drawdown_percent, drawdown / peak * 100 if peak > 0 else 0.0)
        equity_curve.append({"timestamp_ms": stamp, "equity": equity})

    sweep = []
    for start, end in loss_intervals:
        sweep.extend(((start, 1), (end, -1)))
    concurrent = max_concurrent = 0
    for _, delta in sorted(sweep, key=lambda item: (item[0], item[1])):
        concurrent += delta
        max_concurrent = max(max_concurrent, concurrent)

    dates = sorted({date for daily in daily_by_run.values() for date in daily})
    correlations = []
    for i, first in enumerate(normalized):
        for second in normalized[i + 1:]:
            a = [daily_by_run[first["run_id"]].get(day, 0.0) / first["allocation"] for day in dates]
            b = [daily_by_run[second["run_id"]].get(day, 0.0) / second["allocation"] for day in dates]
            corr = None
            reason = None
            if len(dates) < 3:
                reason = "Fewer than three distinct trading dates overlap."
            elif max(a) == min(a) or max(b) == min(b):
                reason = "At least one run has zero variance in daily normalized returns."
            else:
                mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
                covariance = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
                sum_a = sum((x - mean_a) ** 2 for x in a)
                sum_b = sum((y - mean_b) ** 2 for y in b)
                corr = covariance / math.sqrt(sum_a * sum_b)
            correlations.append({"first_run_id": first["run_id"], "second_run_id": second["run_id"],
                                 "daily_normalized_return_correlation": corr, "observation_days": len(dates),
                                 "reason_unavailable": reason})

    def group_records(group: dict) -> list[dict]:
        return [{"key": key, **value} for key, value in sorted(group.items())]

    return {"status": "PROVISIONAL_RESEARCH_PORTFOLIO", "combined_financials_available": True,
            "broker_validated": False, "account_currency": next(iter(currency_set)),
            "capital_allocation_basis": "Saved backtest starting balance; no P&L scaling was applied.",
            "allocated_capital": total_capital, "net_pnl": sum(pnl_at_time.values()),
            "ending_equity": equity, "max_drawdown_money": max_drawdown_money,
            "max_drawdown_percent": max_drawdown_percent, "trade_count": len(trades),
            "simultaneous_losing_trades_max": max_concurrent,
            "equity_curve": equity_curve, "correlations": correlations,
            "by_strategy": group_records(groups["strategy"]),
            "by_instrument": group_records(groups["instrument"]),
            "by_month": group_records(groups["month"]), "runs": summaries,
            "note": "All amounts are provisional Dukascopy Bid/Ask research-model results; quote-side OHLC does not establish tick order or broker accounting."}


# ----------------------------------------------------------------------------- forward portfolio helpers

def fx_check(symbol: str, contract) -> dict:
    """Is the frozen quote_to_account rate plausible for this instrument? Never silently accept a wrong one.

    BLOCKED  quote currency differs from the account but no conversion pair exists, or the account is not USD.
    PLACEHOLDER_RATE  conversion needed but the frozen rate is 1.0: flagged in every report, not blocked (existing
             registrations use it); set a real quote_to_account to clear it.
    WARNING  a conversion is needed and a rate was frozen: it is static, so P&L drifts with the real FX rate.
    UNKNOWN_INSTRUMENT  not in the instrument registry: quote currency cannot be verified.
    OK       account-currency quote.
    """
    from .research import instruments

    name = instruments.canonical(symbol)
    spec = instruments.conversion_info(name)
    account = str(contract.account_currency).upper()
    if account != instruments.ACCOUNT_CURRENCY:
        return {"status": "BLOCKED", "symbol": name,
                "reason": f"account currency {account} has no causal conversion source (registry converts to USD only)"}
    if name not in instruments.SYMBOLS:
        return {"status": "UNKNOWN_INSTRUMENT", "symbol": name,
                "reason": "instrument missing from the registry; quote currency and conversion are unverified"}
    if not spec["needed"]:
        return {"status": "OK", "symbol": name, "quote": spec["quote"]}
    if not spec.get("pair"):
        return {"status": "BLOCKED", "symbol": name,
                "reason": f"{spec['quote']} quote needs a conversion pair that is not available"}
    if abs(float(contract.quote_to_account) - 1.0) < 1e-12:
        return {"status": "PLACEHOLDER_RATE", "symbol": name, "pair": spec["pair"],
                "reason": f"{spec['quote']} quote with frozen quote_to_account 1.0: P&L, margin and risk are misstated "
                          f"unless that is the true rate; conversion via {spec['pair']} is not applied"}
    return {"status": "WARNING", "symbol": name, "pair": spec["pair"], "quote": spec["quote"],
            "reason": f"static frozen {spec['quote']} rate; real conversion via {spec['pair']} drifts"}


def _pearson(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    mean_a, mean_b = sum(a) / n, sum(b) / n
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_a <= 1e-24 or var_b <= 1e-24:
        return None
    return sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b)) / math.sqrt(var_a * var_b)


def correlation_pairs(series: dict[str, dict[str, float]], *, min_obs: int, symbols: dict[str, str] | None = None,
                      threshold: float = 0.7) -> list[dict]:
    """Pairwise Pearson on daily returns aligned on common days only. Short or flat series are never
    reported as a number: status INSUFFICIENT_EVIDENCE carries the reason and n_obs."""
    symbols = symbols or {}
    ids = sorted(series)
    pairs = []
    for i, first in enumerate(ids):
        for second in ids[i + 1:]:
            days = sorted(set(series[first]) & set(series[second]))
            row = {"first": first, "second": second, "n_obs": len(days), "min_obs": min_obs,
                   "same_symbol": bool(symbols.get(first)) and symbols.get(first) == symbols.get(second),
                   "correlation": None, "status": "OK", "reason": None}
            if len(days) < min_obs:
                row.update(status="INSUFFICIENT_EVIDENCE", reason=f"{len(days)} aligned days < min_obs {min_obs}")
            else:
                value = _pearson([series[first][d] for d in days], [series[second][d] for d in days])
                if value is None:
                    row.update(status="INSUFFICIENT_EVIDENCE", reason="zero variance in an aligned series")
                else:
                    row["correlation"] = value
                    row["high_correlation"] = abs(value) >= threshold
            pairs.append(row)
    return pairs


def forward_daily_returns(report: dict) -> dict[str, dict[str, float]]:
    """Per member: realised P&L closed on a UTC day / member capital at that day's start (0.0 on quiet days),
    for every committed day since the member joined. Prospective (DAILY_DELAYED_FORWARD) evidence only."""
    pnl: dict[tuple[str, str], float] = defaultdict(float)
    for event in report["events"]:
        if event["kind"] == "POSITION_CLOSED":
            pnl[(event["registration_id"], event["day"])] += event["detail"]["net_pnl"]
    series = {}
    for member in report["members"]:
        capital, rows = member["allocation"], {}
        for day in report["days"]:
            if day["day"] < member["joined_at"][:10]:
                continue
            gain = pnl.get((member["registration_id"], day["day"]), 0.0)
            rows[day["day"]] = gain / capital if capital > 0 else 0.0
            capital += gain
        series[member["registration_id"]] = rows
    return series


def forward_correlations(report: dict, *, min_obs: int = 30, threshold: float = 0.7) -> dict:
    """Correlation report with three evidence classes kept strictly apart (never pooled).

    HISTORICAL_BACKTEST / HISTORICAL_OOS come from ``configuration["pre_registration_returns"]`` frozen with each
    registration (daily returns known before registration); DAILY_DELAYED_FORWARD from the committed ledger.
    """
    if type(min_obs) is not int or min_obs < 3:
        raise ValueError("min_obs must be an integer >= 3")
    symbols = {m["registration_id"]: m["configuration"]["symbol"] for m in report["members"]}
    classes = {}
    for name in ("HISTORICAL_BACKTEST", "HISTORICAL_OOS"):
        series = {}
        for member in report["members"]:
            raw = (member["configuration"].get("pre_registration_returns") or {}).get(name)
            if isinstance(raw, dict) and raw:
                series[member["registration_id"]] = {str(k): float(v) for k, v in raw.items()
                                                     if _number(v) is not None}
        classes[name] = _correlation_class(series, min_obs, symbols, threshold, "no frozen daily returns for this class")
    classes["DAILY_DELAYED_FORWARD"] = _correlation_class(
        {k: v for k, v in forward_daily_returns(report).items()}, min_obs, symbols, threshold,
        "no committed forward days")
    classes["REALTIME_PAPER"] = {"status": "NOT_IMPLEMENTED", "pairs": []}
    warnings = sorted({tuple(sorted((p["first"], p["second"]))) for c in classes.values()
                       for p in c.get("pairs", []) if p.get("high_correlation") or p["same_symbol"]})
    return {"portfolio_id": report["portfolio"]["id"], "min_obs": min_obs, "high_correlation_threshold": threshold,
            "basis": "DAILY_RETURNS_ALIGNED_ON_COMMON_DAYS", "classes": classes,
            "correlation_risk_pairs": [list(pair) for pair in warnings]}


def _correlation_class(series, min_obs, symbols, threshold, empty_reason) -> dict:
    if len(series) < 2:
        return {"status": "INSUFFICIENT_EVIDENCE", "reason": empty_reason if not series else "fewer than two series",
                "pairs": []}
    pairs = correlation_pairs(series, min_obs=min_obs, symbols=symbols, threshold=threshold)
    ok = any(p["status"] == "OK" for p in pairs)
    return {"status": "OK" if ok else "INSUFFICIENT_EVIDENCE", "pairs": pairs}
