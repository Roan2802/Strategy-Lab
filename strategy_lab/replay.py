"""Cursor-based historical replay; every view is cut off at the selected candle."""
from __future__ import annotations

from collections.abc import Iterable
import pandas as pd


def replay_slice(candles: pd.DataFrame, cursor_ms: int, events: list[dict] | None = None) -> tuple[pd.DataFrame, list[dict]]:
    visible = candles[candles.index.view("int64") // 1_000_000 <= int(cursor_ms)].copy()
    visible_events = [event for event in (events or []) if int(event.get("timestamp_ms", -1)) <= int(cursor_ms)]
    return visible, visible_events


def recorded_state_snapshot(events: list[dict], timestamp_ms: int, *,
                            symbol: str, timeframe: str, event_symbol: str | None = None) -> dict:
    """Return only an exact-candle snapshot emitted during the original run.

    This deliberately does not infer or replay state for strategies that did
    not record a CANDLE_DECISION event at the selected candle.
    """
    wanted_symbols = {str(symbol), str(event_symbol or symbol)}
    timeframe_aliases = {"M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
                         "H1": "1h", "H4": "4h", "D1": "1d"}
    wanted_timeframe = timeframe_aliases.get(str(timeframe).upper(), str(timeframe).lower())
    candidates = [event for event in events
                  if event.get("event_type", event.get("event")) == "CANDLE_DECISION"
                  and int(event.get("timestamp_ms", -1)) == int(timestamp_ms)
                  and str(event.get("symbol", "")) in wanted_symbols
                  and timeframe_aliases.get(str(event.get("timeframe", "")).upper(),
                                            str(event.get("timeframe", "")).lower()) == wanted_timeframe]
    if len(candidates) != 1:
        if len(candidates) > 1:
            reason = "Multiple decision snapshots exist for this candle; state is ambiguous."
        else:
            reason = "This strategy did not record a decision snapshot for this candle. State was not inferred."
        return {"available": False, "state_available": False, "timestamp_ms": int(timestamp_ms),
                "reason": reason, "decision_event_count": len(candidates)}

    event = candidates[0]
    state = event.get("state")
    return {"available": True, "state_available": isinstance(state, dict) and bool(state),
            "timestamp_ms": int(timestamp_ms), "source": "recorded_during_original_backtest",
            "event_id": event.get("event_id"), "strategy": event.get("strategy"),
            "decision": event.get("decision"), "reason": event.get("reason"),
            "checks": event.get("checks", []), "state": state if isinstance(state, dict) else {},
            "state_message": None if isinstance(state, dict) and state else
                "Decision checks were recorded, but this strategy emitted no state snapshot."}


def reexecute_labbreakout(candles: pd.DataFrame, cursor_ms: int, *, start_ms: int,
                          warmup_candles: int, parameters: dict,
                          recorded_snapshot: dict) -> dict:
    """Recompute the audited LabBreakout candle decision from a causal Bid prefix.

    Position and pending-order state must come from the exact decision event;
    this adapter never reconstructs those stateful engine values from price.
    """
    if not recorded_snapshot.get("available") or not recorded_snapshot.get("state_available"):
        return {"available": False, "reason": "An exact recorded strategy state is required for causal re-execution."}
    state = recorded_snapshot.get("state") or {}
    if "position_open" not in state or not isinstance(state.get("pending_order_ids"), list):
        return {"available": False, "reason": "Recorded position and pending-order state is incomplete."}
    visible = candles.loc[candles.index.view("int64") // 1_000_000 <= int(cursor_ms)]
    trading = visible.loc[visible.index.view("int64") // 1_000_000 >= int(start_ms)]
    warmup = visible.loc[visible.index.view("int64") // 1_000_000 < int(start_ms)].tail(max(0, int(warmup_candles)))
    if not len(trading):
        return {"available": False, "reason": "No run candle exists at or before the selected cursor."}
    candle = trading.iloc[-1]
    timestamp_ms = int(trading.index[-1].value // 1_000_000)
    if timestamp_ms != int(cursor_ms):
        return {"available": False, "reason": "The cursor must match an exact run candle."}
    prefix = pd.concat([warmup, trading])
    period = max(1, int(parameters.get("ema_period", 20)))
    close_values = prefix["close"].astype(float).tolist()
    alpha = 2.0 / (period + 1.0)
    ema_value = None
    previous = None
    for close_value in close_values:
        previous = close_value if previous is None else previous + alpha * (close_value - previous)
    if len(close_values) >= period:
        ema_value = float(previous)
    index = len(trading) - 1
    mode = int(parameters.get("order_mode", 0))
    available = not bool(state["position_open"]) and not state["pending_order_ids"]
    scheduled = index > 0 and index % max(1, int(parameters.get("entry_interval", 60))) == 0
    range_value = float(candle["high"]) - float(candle["low"])
    range_pass = range_value <= float(parameters.get("max_bar_range", 25.0))
    trend_pass = None if ema_value is None else (float(candle["close"]) < ema_value if mode == 2 else float(candle["close"]) > ema_value)
    if ema_value is None:
        decision, reason = "SKIP", "EMA_WARMUP"
    elif not available:
        decision, reason = "SKIP", "POSITION_OR_PENDING_ORDER"
    elif not scheduled:
        decision, reason = "SKIP", "NOT_SCHEDULED"
    elif not trend_pass:
        decision, reason = "FAIL", "EMA_CONDITION"
    elif not range_pass:
        decision, reason = "FAIL", "BAR_RANGE_CONDITION"
    else:
        decision, reason = "PASS", "ENTRY_CONDITIONS_MET"
    logged_ema = state.get("ema")
    logged_reason = recorded_snapshot.get("reason")
    ema_matches = (logged_ema is None and ema_value is None) or (
        logged_ema is not None and ema_value is not None and
        abs(float(logged_ema) - ema_value) <= max(1e-10, abs(ema_value) * 1e-12))
    return {"available": True, "adapter": "LabBreakout Bid OHLC v1",
            "timestamp_ms": timestamp_ms, "index": index, "ema": ema_value,
            "decision": decision, "reason": reason,
            "logged_decision": recorded_snapshot.get("decision"),
            "logged_reason": logged_reason,
            "logged_ema": logged_ema,
            "matches_recorded": (decision == recorded_snapshot.get("decision") and
                                 reason == logged_reason and ema_matches),
            "ema_matches_recorded": ema_matches,
            "causal_candles": len(prefix), "warmup_candles_used": len(warmup),
            "state_source": "exact recorded snapshot (position/pending only)",
            "future_data_used": False}


def event_links(events: Iterable[dict], trades: list[dict]) -> dict:
    """Resolve lifecycle IDs, bridging submits only through a unique signal match.

    Jesse assigns an entry order ID after ORDER_SUBMITTED. POSITION_OPEN carries
    that ID and the originating signal timestamp. Ambiguous matches stay open.
    """
    by_order: dict[str, list[dict]] = {}
    by_trade: dict[str, list[dict]] = {}
    submits_by_signal: dict[tuple[str, int], list[dict]] = {}
    candle_event_count = 0
    for event in events:
        event_type = event.get("event_type", event.get("event"))
        if event_type == "CANDLE_DECISION":
            candle_event_count += 1
            continue
        if event.get("order_id"):
            by_order.setdefault(str(event["order_id"]), []).append(event)
        for order_id in event.get("order_ids", []) or []:
            if order_id:
                by_order.setdefault(str(order_id), []).append(event)
        for order in event.get("orders", []) or []:
            if order.get("id"):
                by_order.setdefault(str(order["id"]), []).append(event)
        if event.get("trade_id"):
            by_trade.setdefault(str(event["trade_id"]), []).append(event)
        if event_type == "ORDER_SUBMITTED" and event.get("signal_timestamp_ms") is not None:
            key = (str(event.get("symbol", "")), int(event["signal_timestamp_ms"]))
            submits_by_signal.setdefault(key, []).append(event)
    links = []
    for trade in trades:
        tid = str(trade.get("id", trade.get("trade_id", "")))
        order_ids = {str(o.get("id", o.get("order_id", ""))) for o in trade.get("orders", []) if o.get("id", o.get("order_id"))}
        matches = list(by_trade.get(tid, []))
        for oid in order_ids:
            matches.extend(by_order.get(oid, []))
        ambiguous_signal_submit_count = 0
        open_events = [e for e in matches
                       if e.get("event_type", e.get("event")) == "POSITION_OPEN"
                       and e.get("signal_timestamp_ms") is not None
                       and str(e.get("order_id", "")) in order_ids]
        for opened in open_events:
            key = (str(opened.get("symbol", trade.get("symbol", ""))),
                   int(opened["signal_timestamp_ms"]))
            candidates = submits_by_signal.get(key, [])
            if len(candidates) == 1:
                matches.extend(candidates)
            elif len(candidates) > 1:
                ambiguous_signal_submit_count += 1
        links.append({"trade_id": tid, "order_ids": sorted(order_ids),
                      "event_ids": sorted({e.get("event_id", "") for e in matches if e.get("event_id")}),
                      "linked_event_count": len({e.get("event_id", id(e)) for e in matches}),
                      "ambiguous_signal_submit_count": ambiguous_signal_submit_count})
    return {"trades": links, "candle_event_count": candle_event_count}
