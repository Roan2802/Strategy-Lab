"""Forward-test monitoring: alerts and rolling metrics. Read-only and defensive: never raises into the dashboard.

Expected member expectations are read from member["configuration"]["expectations"]:
    {"mean_r": float, "std_r": float}  (R multiples = net_pnl / risk at entry)
Without them the statistical alerts (FORWARD_BELOW_EXPECTATION, REVIEW_REQUIRED) stay silent.
"""
from datetime import datetime, timezone
from math import fsum, sqrt
from statistics import mean, stdev

DEFAULT_THRESHOLDS = {
    "no_trade_days": 14, "portfolio_drawdown_pct": 10.0, "member_drawdown_pct": 15.0,
    "min_trades": 20, "se_multiple": 2.0, "cusum_h": 5.0, "cusum_k": 0.5,
}
OK_FEED = {"UP TO DATE", "MARKET CLOSED"}


def _alert(kind, severity, portfolio_id, message, value, threshold, registration_id=None):
    alert = {"kind": kind, "severity": severity, "portfolio_id": portfolio_id, "message": message,
             "value": value, "threshold": threshold}
    if registration_id is not None:
        alert["registration_id"] = registration_id
    return alert


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _parse(stamp):
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _max_drawdown(equities, start):
    peak, worst = start, 0.0
    for equity in equities:
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, 100 * (peak - equity) / peak)
    return worst


def _r_multiples(trades):
    values = []
    for trade in trades:
        pnl, risk = _num(trade.get("net_pnl")), _num(trade.get("risk"))
        if pnl is not None and risk:
            values.append(pnl / risk)
    return values


def _cusum(values, mu, sigma, k, h):
    """Lower one-sided CUSUM on standardised R; returns (statistic, triggered)."""
    if not sigma:
        return 0.0, False
    stat = peak = 0.0
    for value in values:
        stat = max(0.0, stat + (mu - value) / sigma - k)
        peak = max(peak, stat)
    return peak, peak > h


def rolling_metrics(closed_trades, windows=(20, 50, 100)) -> dict:
    trades = [t for t in (closed_trades or []) if isinstance(t, dict) and _num(t.get("net_pnl")) is not None]
    result = {}
    for window in windows:
        recent = trades[-window:]
        if not recent:
            result[window] = {"trades": 0, "winrate": None, "expectancy": None, "profit_factor": None,
                              "max_drawdown": None, "trade_frequency_change": None}
            continue
        pnls = [t["net_pnl"] for t in recent]
        gain = fsum(p for p in pnls if p > 0)
        loss = -fsum(p for p in pnls if p < 0)
        peak = equity = worst = 0.0
        for pnl in pnls:
            equity += pnl
            peak = max(peak, equity)
            worst = max(worst, peak - equity)
        change = None
        previous = trades[-2 * window:-window]
        if len(previous) >= 2 and len(recent) >= 2:
            def rate(group):
                first, last = _parse(group[0].get("exit_at")), _parse(group[-1].get("exit_at"))
                days = (last - first).total_seconds() / 86400 if first and last else 0
                return (len(group) - 1) / days if days > 0 else None
            now_rate, before_rate = rate(recent), rate(previous)
            if now_rate is not None and before_rate:
                change = now_rate / before_rate - 1
        result[window] = {"trades": len(recent), "winrate": 100 * sum(p > 0 for p in pnls) / len(pnls),
                          "expectancy": fsum(pnls) / len(pnls), "profit_factor": gain / loss if loss else None,
                          "max_drawdown": worst, "trade_frequency_change": change}
    return result


def _member_alerts(portfolio, member, events, now, limits):
    pid, rid = portfolio.get("id"), member.get("registration_id")
    alerts = []
    state = member.get("state") or {}
    mine = [e for e in events if e.get("registration_id") == rid]
    closed = [e.get("detail") or {} for e in mine if e.get("kind") == "POSITION_CLOSED"]
    active = bool(state.get("last_candle_at"))

    if active:
        traded = [_parse(e.get("candle_at")) for e in mine if e.get("kind") in {"ORDER_FILLED", "POSITION_CLOSED"}]
        reference = max((t for t in traded if t), default=None) or _parse(member.get("joined_at"))
        if reference and (now - reference).days > limits["no_trade_days"]:
            days = (now - reference).days
            alerts.append(_alert("NO_TRADES", "warning", pid,
                                 f"Geen trades gedurende {days} dagen voor registratie {rid}.",
                                 days, limits["no_trade_days"], rid))

    allocation = _num(member.get("allocation"))
    pnls = [_num(t.get("net_pnl")) or 0.0 for t in closed]
    if allocation and pnls:
        curve, balance = [], allocation
        for pnl in pnls:
            balance += pnl
            curve.append(balance)
        drawdown = _max_drawdown(curve, allocation)
        if drawdown > limits["member_drawdown_pct"]:
            alerts.append(_alert("DRAWDOWN_LIMIT", "critical", pid,
                                 f"Drawdown van registratie {rid} is {drawdown:.1f}% (limiet {limits['member_drawdown_pct']}%).",
                                 drawdown, limits["member_drawdown_pct"], rid))

    expect = (member.get("configuration") or {}).get("expectations") or {}
    mu, sigma = _num(expect.get("mean_r")), _num(expect.get("std_r"))
    r_values = _r_multiples(closed)
    if mu is not None and len(r_values) >= limits["min_trades"]:
        n = len(r_values)
        sd = sigma or (stdev(r_values) if n > 1 else 0.0)
        average = mean(r_values)
        floor = mu - limits["se_multiple"] * sd / sqrt(n)
        if average < floor:
            alerts.append(_alert("FORWARD_BELOW_EXPECTATION", "warning", pid,
                                 f"Forward-verwachting ({average:.2f}R per trade, {n} trades) ligt onder de "
                                 f"backtestverwachting ({mu:.2f}R) min {limits['se_multiple']} standaardfouten.",
                                 average, floor, rid))
        stat, hit = _cusum(r_values, mu, sd, limits["cusum_k"], limits["cusum_h"])
        if hit:
            alerts.append(_alert("REVIEW_REQUIRED", "critical", pid,
                                 f"Structurele breuk gedetecteerd (CUSUM {stat:.1f} > {limits['cusum_h']}); handmatige review nodig.",
                                 stat, limits["cusum_h"], rid))

    if member.get("model_current") is False:
        alerts.append(_alert("CONFIG_CHANGED", "critical", pid,
                             f"Registratie {rid} komt niet meer overeen met de huidige bron/configuratie.",
                             False, True, rid))
    return alerts


def _portfolio_alerts(report, now, limits, engine):
    portfolio = report.get("portfolio") or {}
    pid = portfolio.get("id")
    members, events, days = report.get("members") or [], report.get("events") or [], report.get("days") or []
    alerts = []

    capital = _num(portfolio.get("starting_capital"))
    equities = [_num(d.get("equity")) for d in days if _num(d.get("equity")) is not None]
    if capital and equities:
        drawdown = _max_drawdown(equities, capital)
        if drawdown > limits["portfolio_drawdown_pct"]:
            alerts.append(_alert("DRAWDOWN_LIMIT", "critical", pid,
                                 f"Portfolio-drawdown is {drawdown:.1f}% (limiet {limits['portfolio_drawdown_pct']}%).",
                                 drawdown, limits["portfolio_drawdown_pct"]))

    risk_limit, balance = _num(portfolio.get("risk_limit")), _num(portfolio.get("balance"))
    open_risk = fsum(_num(((m.get("state") or {}).get("position") or {}).get("risk")) or 0.0 for m in members)
    if risk_limit and balance and open_risk > balance * risk_limit:
        alerts.append(_alert("RISK_LIMIT", "critical", pid,
                             f"Open risico ({open_risk:.2f}) overschrijdt het portfoliolimiet ({balance * risk_limit:.2f}).",
                             open_risk, balance * risk_limit))
    rejected = [e for e in events if e.get("kind") == "ORDER_REJECTED"
                and (e.get("detail") or {}).get("reason") in {"SHARED_MARGIN", "RISK_LIMIT"}]
    if rejected:
        alerts.append(_alert("RISK_LIMIT", "warning", pid,
                             f"{len(rejected)} order(s) geweigerd door margin- of risicolimiet.",
                             len(rejected), 0))

    model_current = report.get("model_current")
    current_sha = getattr(engine, "current_model_sha256", None)
    if model_current is None and current_sha and portfolio.get("model_sha256"):
        model_current = portfolio["model_sha256"] == current_sha
    for member in members:
        member = {**member, "model_current": member.get("model_current", model_current)}
        alerts.extend(_member_alerts(portfolio, member, events, now, limits))
    return alerts


def forward_alerts(forward_engine, data_store=None, sync_config=None, now=None, thresholds=None) -> list[dict]:
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    now = (now or datetime.now(timezone.utc))
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    alerts = []

    if data_store is not None and sync_config is not None:
        try:
            from ..daily_sync import feed_status
            for row in feed_status(data_store, sync_config, now=now).get("symbols", []):
                if row.get("status") not in OK_FEED:
                    alerts.append(_alert("DATA_STALE", "warning", None,
                                         f"Data voor {row.get('symbol')} is niet actueel (status {row.get('status')}, "
                                         f"{row.get('pending_days')} dag(en) achter).",
                                         row.get("status"), "UP TO DATE"))
        except Exception as exc:
            alerts.append(_alert("DATA_STALE", "warning", None,
                                 f"Datastatus kon niet worden bepaald: {exc}", None, "UP TO DATE"))

    try:
        portfolios = forward_engine.portfolios() or []
    except Exception:
        return alerts
    for entry in portfolios:
        try:
            report = forward_engine.report(entry["id"])
            report.setdefault("portfolio", entry)
            alerts.extend(_portfolio_alerts(report, now, limits, forward_engine))
        except Exception:
            continue
    return alerts
