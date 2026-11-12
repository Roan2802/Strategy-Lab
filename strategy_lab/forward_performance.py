"""Read-only performance summaries for the delayed forward ledger."""
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta
from functools import lru_cache
from math import fsum, sqrt
from statistics import mean, stdev

from runtime.strategy_lab.execution import ContractSpec, position_pnl
from .data_manager import DataError
from .forward_registry import data_symbol
from .portfolio import fx_quote_currency


def _publication_matches(event, publication):
    return (bool(publication.get("sha256"))
            and event.get("partition_sha256") == publication["sha256"]
            and event.get("quote_observed_at") == publication["observed_at"])


def _trade_excursion(fill, close, symbol, days, partition):
    """Measure only the candles whose entire price range was observable after entry."""
    if fill is None or fill.get("fill_assumption") not in {
            "NEXT_OPEN", "QUOTE_SIDE_STOP_OR_WORSE_OPEN", "QUOTE_SIDE_LIMIT_OR_BETTER_OPEN"}:
        return {"status": "FILL_UNAVAILABLE"}
    entry = datetime.fromisoformat(close["entry_at"])
    exit_at = datetime.fromisoformat(close["exit_at"])
    if entry >= exit_at:
        return {"status": "NO_OBSERVABLE_BARS"}

    exit_day = exit_at.date()
    day = entry.date()
    columns = ("bid_high", "bid_low") if close["side"] == "long" else ("ask_high", "ask_low")
    high, low, count = -float("inf"), float("inf"), 0
    while day <= exit_day:
        stamp = day.isoformat()
        publication = days.get(stamp, {}).get("publications", {}).get(symbol)
        if not publication:
            return {"status": "PARTITION_UNVERIFIED"}
        if day == entry.date() and not _publication_matches(fill, publication):
            return {"status": "PARTITION_UNVERIFIED"}
        if day == exit_day and not _publication_matches(close, publication):
            return {"status": "PARTITION_UNVERIFIED"}
        if publication.get("status") != "MARKET CLOSED":
            frame = partition(symbol, stamp)
            if frame is None:
                return {"status": "PARTITION_UNVERIFIED"}
            if (day == entry.date() and entry not in frame.index) or (day == exit_day and exit_at not in frame.index):
                return {"status": "PARTITION_UNVERIFIED"}
            first = frame.index.searchsorted(entry, side="left" if fill["fill_assumption"] == "NEXT_OPEN" else "right")
            last = frame.index.searchsorted(exit_at, side="left")
            if last > first:
                visible = frame.iloc[first:last]
                high = max(high, float(visible[columns[0]].max()))
                low = min(low, float(visible[columns[1]].min()))
                count += len(visible)
        day += timedelta(days=1)
    if not count:
        return {"status": "NO_OBSERVABLE_BARS"}
    price = close["entry"]
    if close["side"] == "long":
        favorable, adverse = high - price, price - low
    else:
        favorable, adverse = price - low, high - price
    contract = close["contract"]
    rate = contract["quote_to_account"]
    if fx_quote_currency(symbol, contract["account_currency"]) is not None:
        # non-account quote: the rate is the one the engine took from FxService at the closing candle (event
        # ``fx_rate``); a frozen registration-time value is never used
        rate = close.get("fx_rate")
        if not isinstance(rate, (int, float)) or not rate > 0 or rate != rate or rate == float("inf"):
            return {"status": "FX_RATE_UNAVAILABLE"}
    multiplier = close["quantity"] * contract["contract_size"] * rate
    return {"status": "AVAILABLE", "bars": count,
            "mae": max(0.0, adverse) * multiplier, "mfe": max(0.0, favorable) * multiplier}


def _excursion_metrics(excursions):
    values = [item for item in excursions if item["status"] == "AVAILABLE"]
    return {
        "average_mae": mean(item["mae"] for item in values) if values else None,
        "average_mfe": mean(item["mfe"] for item in values) if values else None,
        "excursion_covered": len(values),
        "excursion_unverified": sum(item["status"] == "PARTITION_UNVERIFIED" for item in excursions),
    }


def _performance(capital, daily_equity, closed):
    pnls = [trade["net_pnl"] for trade in closed]
    reward_to_risk = [abs(t["target"] - t["entry"]) / abs(t["entry"] - t["stop"])
                      for t in closed if t.get("target") is not None and t["entry"] != t["stop"]]
    profit = fsum(pnl for pnl in pnls if pnl > 0)
    loss = -fsum(pnl for pnl in pnls if pnl < 0)
    net = fsum(pnls)
    peak = previous = capital
    underwater_since = None
    underwater_days = 0
    max_drawdown = max_drawdown_percent = 0.0
    returns, curve = [], []
    for day, equity in daily_equity:
        if previous > 0:
            returns.append((equity - previous) / previous)
        previous = equity
        peak = max(peak, equity)
        drawdown = peak - equity
        drawdown_percent = 100 * drawdown / peak
        max_drawdown = max(max_drawdown, drawdown)
        max_drawdown_percent = max(max_drawdown_percent, drawdown_percent)
        observed_day = date.fromisoformat(day)
        if drawdown > 0 and underwater_since is None:
            underwater_since = observed_day
        if underwater_since is not None:
            underwater_days = max(underwater_days, (observed_day - underwater_since).days)
        if drawdown == 0:
            underwater_since = None
        curve.append({"day": day, "equity": equity, "drawdown_percent": drawdown_percent})

    sharpe = sortino = None
    if len(returns) == len(curve) and len(returns) >= 2:
        average = mean(returns)
        volatility = stdev(returns)
        downside = sqrt(mean(min(0.0, value) ** 2 for value in returns))
        sharpe = average / volatility if volatility else None
        sortino = average / downside if downside else None
    return {
        "trade_count": len(pnls), "net_profit": net,
        "profit_factor": profit / loss if loss else None,
        "recovery_factor": net / max_drawdown if max_drawdown else None,
        "sharpe_daily": sharpe, "sortino_daily": sortino, "sample_days": len(curve),
        "max_drawdown": max_drawdown if curve else None,
        "max_drawdown_percent": max_drawdown_percent if curve else None,
        "max_underwater_days": underwater_days if curve else None,
        "winrate_percent": 100 * sum(pnl > 0 for pnl in pnls) / len(pnls) if pnls else None,
        "expectancy": net / len(pnls) if pnls else None,
        "average_reward_to_risk": mean(reward_to_risk) if reward_to_risk else None,
    }, curve


def summarize_forward(report, *, model_current, data):
    """Derive statistics from committed rows without reprocessing any orders."""
    portfolio, days = report["portfolio"], report["days"]
    days_by_date = {day["day"]: day for day in days}

    @lru_cache(maxsize=16)
    def partition(symbol, day):
        recorded_day = days_by_date[day]
        expected = recorded_day["publications"][symbol]
        try:
            parsed = date.fromisoformat(day)
            frame, manifests = data.read(data_symbol(symbol), parsed, parsed,
                                         as_of=datetime.fromisoformat(recorded_day["processed_at"]),
                                         first_publication=True)
        except DataError:
            return None
        return frame if manifests == [expected] else None

    all_closed, by_member = [], defaultdict(list)
    for event in report["events"]:
        by_member[event["registration_id"]].append(event)
        if event["kind"] == "POSITION_CLOSED":
            all_closed.append(event["detail"])

    total, curve = _performance(portfolio["starting_capital"],
                                [(day["day"], day["equity"]) for day in days], all_closed)
    members, trade_excursions = {}, {}
    for member in report["members"]:
        state, registration = member["state"], member["registration_id"]
        allocation, since = member["allocation"], member["joined_at"][:10]
        position = state["position"]
        contract = ContractSpec(**member["configuration"]["contract"])
        if state.get("fx_rate"):
            contract = replace(contract, quote_to_account=state["fx_rate"])
        closed, daily_pnl = [], defaultdict(list)
        fills, member_excursions = {}, []
        last_signal = last_trade = None
        rejected = 0
        for event in by_member[registration]:
            detail, kind = event["detail"], event["kind"]
            if kind in {"ORDER_SUBMITTED", "SIGNAL_REJECTED"}:
                last_signal = detail["signal_at"]
            if kind == "ORDER_FILLED":
                fills[detail["entry_at"]] = detail
            if kind == "POSITION_CLOSED":
                closed.append(detail)
                daily_pnl[event["day"]].append(detail["net_pnl"])
                excursion = _trade_excursion(fills.pop(detail["entry_at"], None), detail,
                                             member["configuration"]["symbol"], days_by_date, partition)
                trade_excursions[event["id"]] = excursion
                member_excursions.append(excursion)
            if kind in {"ORDER_FILLED", "POSITION_CLOSED"}:
                last_trade = {"kind": kind, "at": event["candle_at"]}
            if kind == "ORDER_REJECTED" and detail["reason"] in {"SHARED_MARGIN", "RISK_LIMIT", "SYMBOL_RISK_LIMIT", "GROUP_RISK_LIMIT", "PORTFOLIO_HALT"}:
                rejected += 1
        balance, series = allocation, []
        for day in days:
            if day["day"] >= since:
                balance += fsum(daily_pnl.get(day["day"], ()))
                series.append((day["day"], balance))
        metrics, _ = _performance(allocation, series, closed)
        mark = state["mark"]
        open_pnl = exposure = 0.0
        if position and mark is not None and not state.get("fx_rate") and fx_quote_currency(
                member["configuration"]["symbol"], contract.account_currency) is not None:
            open_pnl = exposure = None   # FX_RATE_UNAVAILABLE: the frozen quote_to_account is not a rate
        elif position and mark is not None:
            open_pnl = position_pnl(side=position["side"], quantity_lots=position["quantity"],
                                    entry=position["entry"], exit=mark, contract=contract)["net_pnl"]
            exposure = mark * position["quantity"] * contract.contract_size * contract.quote_to_account
        members[registration] = {
            **metrics, **_excursion_metrics(member_excursions),
            "basis": "DAILY_REALIZED_BALANCE", "last_signal": last_signal,
            "last_trade": last_trade, "risk_rejections": rejected,
            "open_pnl": open_pnl, "gross_open_exposure": exposure,
            "open_margin": position["margin"] if position else 0.0,
            "open_stop_risk": position["risk"] if position else 0.0,
            "status": "MODEL_MISMATCH" if not model_current else (
                "FORWARD_TESTING" if state.get("last_candle_at") else "AWAITING_FIRST_CANDLE"),
        }
    equity = days[-1]["equity"] if days else portfolio["balance"]
    return {
        "portfolio": {
            **total, **_excursion_metrics(trade_excursions.values()),
            "basis": "DAILY_MARK_TO_MARKET_EQUITY", "equity": equity, "curve": curve,
            "unallocated_capital": portfolio["starting_capital"] - fsum(m["allocation"] for m in report["members"]),
            "portfolio_stop_budget": max(0.0, min(portfolio["balance"], equity)) * portfolio["risk_limit"],
            **{key: (None if any(member[key] is None for member in members.values())
                     else fsum(member[key] for member in members.values())) for key in (
                "open_pnl", "gross_open_exposure", "open_margin", "open_stop_risk",
            )},
        },
        "members": members, "model_current": model_current, "trade_excursions": trade_excursions,
    }
