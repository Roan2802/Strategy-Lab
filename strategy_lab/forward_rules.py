"""Frozen delayed-forward model for Visual Strategy Builder rule specs (RULES_BIDASK_V1).

Signals are evaluated by ``rule_engine`` on closed Bid bars of the spec timeframe. Orders and exits are
managed on every published M1 Bid/Ask candle with the fill semantics of ``research/backtest.py``.
This module must not import ``forward_engine`` (the engine imports this module).
"""
from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from runtime.strategy_lab import execution, rule_engine as engine

from .daily_data import _utc, provider_symbol
from .data_manager import DataError


MODEL = "RULES_BIDASK_V1"
ENGINE_SOURCE = "runtime/strategy_lab/rule_engine.py"
RULES_SOURCE = "strategy_lab/forward_rules.py"
_SOURCES = {ENGINE_SOURCE: Path(engine.__file__).read_bytes(), RULES_SOURCE: Path(__file__).read_bytes()}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MS = timedelta(milliseconds=1)


def current_sources() -> dict[str, bytes]:
    """Code files whose bytes must be frozen with every rules registration."""
    return dict(_SOURCES)


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _epoch_ms(value: datetime) -> int:
    return (_utc(value) - _EPOCH) // _MS


def _iso_ms(value: int) -> str:
    return (_EPOCH + timedelta(milliseconds=int(value))).isoformat(timespec="microseconds")


def _finite(value, name, *, minimum=0.0, maximum=math.inf, strict=False) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise DataError(f"{name} must be a finite number")
    if value < minimum or value > maximum or (strict and value <= minimum):
        raise DataError(f"{name} is outside the supported range")
    return float(value)


def _reject_unsupported(spec: dict) -> None:
    """Features the research backtest models but this forward model does not: refuse instead of diverging silently.
    (partial_exits ARE supported with the same semantics; the V102 ``execution`` model - margin/swap/hours/rejections -
    is research-backtest only: the forward model has its own SHARED_MARGIN/RISK_LIMIT checks and no swap.)"""
    if spec.get("scale_in"):
        raise DataError("scale_in is not supported by the forward model (RULES_BIDASK_V1); "
                        "the research backtest would add to positions that the forward run never adds to")


def _num(value):
    return float(value) if value is not None and math.isfinite(value) else None


# ----------------------------------------------------------------------------- warm-up

def required_warmup_bars(spec: dict, parameters: dict | None = None) -> int:
    """Largest indicator look-back of the resolved spec x4 + 50 (rule_engine.lookback_bars)."""
    return engine.lookback_bars(engine.resolve(spec, parameters))


def aggregate_bid(frame: pd.DataFrame, timeframe: str, *, until: datetime | None = None) -> list[list]:
    """M1 Bid -> closed bars of ``timeframe`` as [open_ms, o, h, l, c, v]; buckets are UTC multiples."""
    ms = engine.TF_MS[timeframe]
    if frame.empty:
        return []
    index = frame.index.tz_convert("UTC")
    stamps = (index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta("1ms")
    work = pd.DataFrame({"bucket": np.asarray(stamps, dtype="int64") // ms,
                         "o": frame["bid_open"].to_numpy(float), "h": frame["bid_high"].to_numpy(float),
                         "l": frame["bid_low"].to_numpy(float), "c": frame["bid_close"].to_numpy(float),
                         "v": frame["bid_volume"].to_numpy(float)}).dropna()
    grouped = work.groupby("bucket", sort=True).agg(o=("o", "first"), h=("h", "max"), l=("l", "min"),
                                                   c=("c", "last"), v=("v", "sum"))
    limit = None if until is None else _epoch_ms(until)
    rows = []
    for bucket, row in grouped.iterrows():
        opened = int(bucket) * ms
        if limit is None or opened + ms <= limit:
            rows.append([opened, float(row.o), float(row.h), float(row.l), float(row.c), float(row.v)])
    return rows


def _rows_sha256(rows) -> str:
    return hashlib.sha256(_json(rows).encode("ascii")).hexdigest()


def build_warmup(frame: pd.DataFrame, partitions: list[dict], spec: dict, parameters: dict | None, *,
                 as_of: datetime, buffer_cap: int | None = None) -> dict:
    """Snapshot the last N closed Bid bars published at or before ``as_of`` (the freeze time)."""
    as_of = _utc(as_of)
    late = [item for item in partitions if datetime.fromisoformat(item["observed_at"]) > as_of]
    if late:
        raise DataError("The warm-up contains data observed after the freeze time")
    need = required_warmup_bars(spec, parameters)
    rows = aggregate_bid(frame[frame.index < as_of], spec["timeframe"], until=as_of)[-need:]
    if len(rows) < need:
        raise DataError(f"Warm-up needs {need} closed {spec['timeframe']} bars, only {len(rows)} available")
    days = {_iso_ms(row[0])[:10] for row in rows}
    used = [{key: item[key] for key in ("day", "sha256", "observed_at")}
            for item in partitions if item["day"] in days]
    cap = int(buffer_cap) if buffer_cap else 2 * need
    return {"as_of": as_of.isoformat(timespec="microseconds"), "required_bars": need, "buffer_cap": cap,
            "count": len(rows), "bars": rows, "sha256": _rows_sha256(rows),
            "last_bar_close_at": _iso_ms(rows[-1][0] + engine.TF_MS[spec["timeframe"]]),
            "partitions": sorted(used, key=lambda item: item["day"])}


def warmup_from_fetch(fetch, spec: dict, parameters: dict | None, *, as_of: datetime,
                      buffer_cap: int | None = None) -> dict:
    """``fetch(start, end) -> (m1_frame, partition_manifests)``; widens the window until enough bars exist."""
    as_of = _utc(as_of)
    need = required_warmup_bars(spec, parameters)
    minutes = need * engine.TF_MS[spec["timeframe"]] // 60_000
    days = int(math.ceil(minutes / 1440 * 1.5)) + 3
    while True:
        frame, partitions = fetch(as_of - timedelta(days=days), as_of)
        try:
            return build_warmup(frame, partitions, spec, parameters, as_of=as_of, buffer_cap=buffer_cap)
        except DataError as exc:
            if "only" not in str(exc) or days > 800:
                raise
            days *= 2


def store_fetch(store, symbol: str):
    """Fetch M1 from the daily partition store exactly as the forward engine sees it (first publication)."""
    def fetch(start: datetime, end: datetime):
        return store.read(symbol, start.date(), end.date(), as_of=end, first_publication=True)
    return fetch


# ----------------------------------------------------------------------------- configuration

def _contract(raw, symbol: str | None = None) -> execution.ContractSpec:
    names = [field.name for field in fields(execution.ContractSpec)]
    try:
        contract = execution.ContractSpec(**{name: raw[name] for name in names})
    except (KeyError, TypeError) as exc:
        raise DataError("Freeze every field of the Bid/Ask contract explicitly") from exc
    if not isinstance(contract.account_currency, str) or not contract.account_currency.strip():
        raise DataError("The contract requires an account currency")
    for name in ("contract_size", "tick_size", "quote_to_account", "volume_step"):
        _finite(getattr(contract, name), name, strict=True)
    for name in ("commission_per_lot_side", "slippage_points"):
        _finite(getattr(contract, name), name)
    if symbol is not None:
        _require_real_fx(symbol, contract)
    return contract


def _require_real_fx(symbol: str, contract) -> None:
    """A frozen ``quote_to_account`` is never a rate for a non-account quote currency: the forward engine converts
    every candle through FxService (fx_check DYNAMIC_FX) and ignores the frozen value, and ``enroll`` blocks when the
    service is missing. Registration therefore rejects what can never be converted: an instrument outside the
    registry (quote currency unverifiable) or a quote without a conversion pair. A non-USD account is left to the
    enroll-time check."""
    from .portfolio import fx_check

    check = fx_check(symbol, contract, fx_available=True)
    if check["status"] == "UNKNOWN_INSTRUMENT" or (
            check["status"] == "BLOCKED" and "no causal conversion" not in check["reason"]):
        raise DataError(f"FX_RATE_UNAVAILABLE: {check['reason']}")


def build_configuration(spec: dict, parameters: dict | None, *, contract, starting_capital: float,
                        trade_risk: float, portfolio_risk_limit: float, leverage: float = 1,
                        warmup: dict, data_version) -> dict:
    """The configuration that registry.freeze stores; every parameter is made explicit."""
    clean = {key: value for key, value in spec.items() if key != "meta"}
    errors = engine.validate_spec(clean)
    if errors:
        raise DataError("Invalid rule spec: " + "; ".join(errors))
    _reject_unsupported(clean)
    try:
        explicit = engine._param_values(clean, parameters)
    except ValueError as exc:
        raise DataError(str(exc)) from exc
    raw = contract if isinstance(contract, dict) else {f.name: getattr(contract, f.name)
                                                       for f in fields(execution.ContractSpec)}
    _contract(raw, clean["symbol"])
    return {
        "strategy": clean["name"], "execution_model": MODEL, "spec": clean,
        "spec_sha256": engine.spec_sha256(clean), "parameters": explicit, "indicators": {},
        "symbol": provider_symbol(clean["symbol"]), "timeframe": clean["timeframe"],
        "contract": {f.name: raw[f.name] for f in fields(execution.ContractSpec)},
        "starting_capital": starting_capital, "trade_risk": trade_risk,
        "portfolio_risk_limit": portfolio_risk_limit, "leverage": leverage,
        "news_filter": {"enabled": False}, "data_version": data_version, "warmup": warmup,
    }


def validate_registration(registration: dict):
    config = registration["configuration"]
    sources = registration.get("sources") or {}
    if config.get("execution_model") != MODEL:
        raise DataError("Not a rules registration")
    for name, loaded in _SOURCES.items():
        if hashlib.sha256(sources.get(name, b"")).digest() != hashlib.sha256(loaded).digest():
            raise DataError(f"The frozen {name} differs from the loaded version")
    spec = config.get("spec")
    errors = engine.validate_spec(spec)
    if errors:
        raise DataError("Invalid rule spec: " + "; ".join(errors))
    _reject_unsupported(spec)
    if config.get("news_filter") != {"enabled": False}:
        raise DataError("Rules strategies require the news filter disabled")
    if config.get("indicators") != {}:
        raise DataError("Rules registrations carry no separate indicator configuration")
    if (config.get("strategy") != spec["name"] or config.get("spec_sha256") != engine.spec_sha256(spec)
            or config.get("timeframe") != spec["timeframe"]):
        raise DataError("The frozen spec differs from its registration")
    if config.get("symbol") != provider_symbol(spec["symbol"]):
        raise DataError("The registration symbol must equal the spec symbol")
    names = {item["name"] for item in spec.get("params") or []}
    params = config.get("parameters")
    if not isinstance(params, dict) or set(params) != names:
        raise DataError("Freeze all spec parameters explicitly")
    try:
        canonical = engine._param_values(spec, params)
    except ValueError as exc:
        raise DataError(str(exc)) from exc
    if canonical != params:
        raise DataError("The frozen parameters are not canonical")
    contract = _contract(config.get("contract") or {}, config.get("symbol"))
    _finite(config.get("starting_capital"), "allocation", strict=True)
    _finite(config.get("trade_risk"), "trade risk fraction", strict=True, maximum=1)
    _finite(config.get("portfolio_risk_limit"), "portfolio risk fraction", strict=True, maximum=1)
    _finite(config.get("leverage", 1), "leverage", minimum=1, maximum=1000)
    _validate_warmup(config, registration.get("frozen_at"))
    return config, contract


def _validate_warmup(config: dict, frozen_at) -> None:
    warm = config.get("warmup")
    if not isinstance(warm, dict):
        raise DataError("The registration requires a warm-up snapshot")
    ms = engine.TF_MS[config["timeframe"]]
    bars = warm.get("bars")
    try:
        as_of = datetime.fromisoformat(warm["as_of"])
        cap, need = warm["buffer_cap"], warm["required_bars"]
        if (type(cap) is not int or type(need) is not int or need < 2 or cap < need
                or need != required_warmup_bars(config["spec"], config["parameters"])):
            raise DataError("The warm-up size does not match the spec")
        if not isinstance(bars, list) or warm.get("count") != len(bars) or not need <= len(bars) <= cap:
            raise DataError("The warm-up snapshot has the wrong number of bars")
        previous = None
        for row in bars:
            if (not isinstance(row, list) or len(row) != 6 or type(row[0]) is not int or row[0] % ms
                    or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in row[1:])
                    or (previous is not None and row[0] <= previous)):
                raise DataError("The warm-up snapshot contains an invalid bar")
            previous = row[0]
        if _rows_sha256(bars) != warm.get("sha256"):
            raise DataError("The warm-up snapshot checksum differs")
        if previous + ms > _epoch_ms(as_of):
            raise DataError("The warm-up contains a bar that was not closed at the freeze time")
        for item in warm.get("partitions", []):
            if datetime.fromisoformat(item["observed_at"]) > as_of:
                raise DataError("The warm-up contains data observed after the freeze time")
        if frozen_at is not None and as_of > datetime.fromisoformat(frozen_at):
            raise DataError("The warm-up was taken after the freeze time")
    except DataError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise DataError("The warm-up snapshot is malformed") from exc


def freeze(registry, configuration: dict, *, frozen_at: datetime | None = None) -> dict:
    """Validate and freeze together with the exact rule engine and forward model bytes."""
    stamp = _utc(frozen_at or datetime.now(timezone.utc))
    validate_registration({"configuration": configuration, "sources": _SOURCES,
                           "frozen_at": stamp.isoformat(timespec="microseconds")})
    return registry.freeze(configuration, current_sources(), frozen_at=stamp)


# ----------------------------------------------------------------------------- state

def initial_state(config: dict, joined_at: datetime) -> dict:
    warm = config["warmup"]
    ms = engine.TF_MS[config["timeframe"]]
    micros = (_utc(joined_at) - _EPOCH) // timedelta(microseconds=1)
    # A bucket already running at enrollment is incomplete and is never used.
    skip = micros // (ms * 1000) if micros % (ms * 1000) else None
    return {"model": MODEL, "buffer": [list(row) for row in warm["bars"]][-warm["buffer_cap"]:],
            "bucket": None, "partial": None, "skip_bucket": skip, "bar_n": 0,
            "pending": None, "scheduled": None, "position": None, "mark": None,
            "realized_pnl": 0.0, "last_exit_bar": None, "trades_by_day": {}, "last_candle_at": None}


def _context(member: dict) -> dict:
    ctx = member.get("_rules")
    if ctx is None:
        config = member["config"]
        ctx = member["_rules"] = {
            "rs": engine.resolve(config["spec"], config["parameters"]),
            "ms": engine.TF_MS[config["timeframe"]],
        }
    return ctx


def _evaluate(member: dict, ctx: dict) -> dict:
    data = np.asarray(member["state"]["buffer"], dtype=np.float64)
    arrays = {"time": data[:, 0].astype(np.int64), "open": data[:, 1], "high": data[:, 2],
              "low": data[:, 3], "close": data[:, 4], "volume": data[:, 5]}
    config = member["config"]
    return engine.evaluate(config["spec"], arrays, config["parameters"], validate=False)


def _public(order: dict) -> dict:
    return {key: order[key] for key in ("type", "side", "planned_price", "signal_at")}


def _snapshot(snap: dict) -> dict:
    return {key: _num(value) for key, value in snap.items()}


def _restore(snap: dict) -> dict:
    return {key: math.nan if value is None else value for key, value in snap.items()}


# ----------------------------------------------------------------------------- stepping

def step(portfolio, member, candle, stamp, budget, emit) -> None:
    """Process one published M1 candle for a rules member (see FORWARD_ENGINE_VALIDATION.md)."""
    state, ctx = member["state"], _context(member)
    ms = ctx["ms"]
    now_ms = _epoch_ms(datetime.fromisoformat(stamp))
    bucket = now_ms // ms
    state["last_candle_at"] = stamp
    ctx["bar_open_ms"] = bucket * ms
    if state["bucket"] is not None and bucket != state["bucket"]:
        _close_bar(portfolio, member, ctx, emit)
    _process_candle(portfolio, member, ctx, candle, stamp, budget, emit)
    if state["bucket"] != bucket:
        state["bucket"] = bucket
        state["partial"] = None if bucket == state["skip_bucket"] else [
            bucket * ms, candle["bid_open"], candle["bid_high"], candle["bid_low"],
            candle["bid_close"], candle["bid_volume"]]
    elif state["partial"] is not None:
        bar = state["partial"]
        bar[2], bar[3] = max(bar[2], candle["bid_high"]), min(bar[3], candle["bid_low"])
        bar[4], bar[5] = candle["bid_close"], bar[5] + candle["bid_volume"]
    position = state["position"]
    state["mark"] = (candle["bid_close"] if position["side"] == "long" else candle["ask_close"]) if position else None


def _local_day(ctx, open_ms: int) -> int:
    return int(engine.local_day_ids(ctx["rs"], [open_ms])[0])


def _close_bar(portfolio, member, ctx, emit) -> None:
    """A TF bucket ended: evaluate the closed bar, update protective levels, queue the next order."""
    state, rs = member["state"], ctx["rs"]
    partial, state["partial"] = state["partial"], None
    closed = state["bar_n"]
    state["bar_n"] += 1
    if partial is None:
        return
    buffer = state["buffer"]
    buffer.append(partial)
    del buffer[:-member["config"]["warmup"]["buffer_cap"]]
    position = state["position"]
    flat = position is None and state["pending"] is None and state["scheduled"] is None
    result = None
    if position is not None and not position["exit_pending"]:
        entry, side = position["entry"], position["side"]
        r_fav = ((position["fav"] - entry) if side == "long" else (entry - position["fav"])) / position["risk_distance"]
        be, trailing = rs.get("break_even"), rs.get("trailing")
        if be and not position["be_done"] and r_fav >= be["activate_r"]:
            position["be_done"] = True
            position["stop"] = max(position["stop"], entry) if side == "long" else min(position["stop"], entry)
        if trailing and r_fav >= trailing["activate_r"]:
            result = _evaluate(member, ctx)
            atr = result["exits"]["trail_atr"][-1]
            if math.isfinite(atr):
                new = (position["fav"] - trailing["atr_mult"] * atr if side == "long"
                       else position["fav"] + trailing["atr_mult"] * atr)
                position["stop"] = max(position["stop"], new) if side == "long" else min(position["stop"], new)
        limit = rs.get("time_exit_bars")
        if limit and closed - position["entry_bar"] + 1 >= limit:
            position["exit_pending"] = True
    if not flat:
        return
    result = result or _evaluate(member, ctx)
    long_signal, short_signal = bool(result["long_signal"][-1]), bool(result["short_signal"][-1])
    signal_at = _iso_ms(partial[0] + ctx["ms"])
    if long_signal and short_signal:
        emit("SIGNAL_REJECTED", reason="CONFLICTING_SIGNALS", signal_at=signal_at)
        return
    if not (long_signal or short_signal):
        return
    side = "long" if long_signal else "short"
    last_exit = state["last_exit_bar"]
    if last_exit is not None and closed - last_exit < rs.get("cooldown_bars", 0):
        emit("SIGNAL_REJECTED", reason="COOLDOWN", signal_at=signal_at)
        return
    daily_limit = rs.get("max_trades_per_day")
    if daily_limit and state["trades_by_day"].get(str(_local_day(ctx, partial[0])), 0) >= daily_limit:
        emit("SIGNAL_REJECTED", reason="MAX_TRADES_PER_DAY", signal_at=signal_at)
        return
    snap = engine.exit_snapshot(result["exits"], len(buffer) - 1)
    reason = "LONG_ENTRY_CONDITIONS_MET" if long_signal else "SHORT_ENTRY_CONDITIONS_MET"
    kind = rs["order"]["type"]
    if kind == "market":
        order = {"type": "MARKET", "side": side, "planned_price": None, "signal_at": signal_at,
                 "snap": _snapshot(snap)}
        state["scheduled"] = order
    else:
        price = engine.order_price(rs, side, partial[4], snap)
        if not math.isfinite(price):
            emit("SIGNAL_REJECTED", reason="INVALID_ORDER_PRICE", signal_at=signal_at)
            return
        order = {"type": kind.upper(), "side": side, "planned_price": price, "signal_at": signal_at,
                 "snap": _snapshot(snap), "start": state["bar_n"],
                 "end": state["bar_n"] + rs["order"]["expiry_bars"] - 1}
        state["pending"] = order
    emit("ORDER_SUBMITTED", **_public(order), reason=reason, quote="BID", submitted_bar=closed)


def _process_candle(portfolio, member, ctx, candle, stamp, budget, emit) -> None:
    state, contract, rs = member["state"], member["contract"], ctx["rs"]
    bar = state["bar_n"]
    slip = contract.slippage_points * contract.tick_size
    bid = {name: candle[f"bid_{name}"] for name in ("open", "high", "low")}
    ask = {name: candle[f"ask_{name}"] for name in ("open", "high", "low")}

    order = state["scheduled"]
    if order is not None:
        state["scheduled"] = None
        price = ask["open"] + slip if order["side"] == "long" else bid["open"] - slip
        _open_position(portfolio, member, ctx, order, price, candle, stamp, budget, emit, intrabar=False)
    order = state["pending"]
    if order is not None and state["position"] is None:
        if bar > order["end"]:
            emit("ORDER_CANCELLED", reason="PENDING_EXPIRY", **_public(order))
            state["pending"] = None
        elif bar >= order["start"]:
            price = _pending_fill(order, bid, ask, slip, rs["order"]["type"])
            if price is not None:
                state["pending"] = None
                _open_position(portfolio, member, ctx, order, price, candle, stamp, budget, emit, intrabar=True)

    position = state["position"]
    if position is None:
        return
    side = position["side"]
    position["fav"] = max(position["fav"], bid["high"]) if side == "long" else min(position["fav"], ask["low"])
    if position["exit_pending"]:
        _close_position(portfolio, member, ctx, candle, stamp, emit, "TIME_EXIT",
                        bid["open"] if side == "long" else ask["open"], ambiguous=False)
        return
    target = position["target"]
    fresh = position["intrabar"] and position["entry_at"] == stamp
    if fresh:
        target = None
    if position.get("plan"):
        event = execution.partial_bar_events(
            side=side, bid=bid, ask=ask, stop=position["stop"], target=target,
            levels=[] if fresh else [lv for lv in position["plan"] if not lv["done"]])
        if event["stop"] is not None:
            _close_position(portfolio, member, ctx, candle, stamp, emit, "STOP_LOSS", event["stop"]["price"],
                            ambiguous=event["ambiguous"])
            return
        for level, price in event["levels"]:
            if state["position"] is not None:
                _partial_fill(portfolio, member, ctx, candle, stamp, emit, level, price)
        if event["target"] is not None and state["position"] is not None:
            _close_position(portfolio, member, ctx, candle, stamp, emit, "TAKE_PROFIT", event["target"]["price"],
                            ambiguous=False)
        position = state["position"]
        if position is not None and position["pending_stop"] is not None:   # remainder stop applies from the next candle
            position["stop"] = (max(position["stop"], position["pending_stop"]) if side == "long"
                                else min(position["stop"], position["pending_stop"]))
            position["pending_stop"] = None
        return
    hit = execution.conservative_ohlc_exit(side=side, bid=bid, ask=ask, stop=position["stop"], target=target)
    if hit is not None:
        _close_position(portfolio, member, ctx, candle, stamp, emit, hit["reason"], hit["price"],
                        ambiguous=hit["ambiguous"])


def _partial_fill(portfolio, member, ctx, candle, stamp, emit, level, price) -> None:
    """Take ``level['qty']`` at ``price`` (same semantics as research/backtest.py partial_exits)."""
    state, contract = member["state"], member["contract"]
    position = state["position"]
    if abs(position["quantity"] - level["qty"]) < 1e-9:          # the level takes everything that is left
        _close_position(portfolio, member, ctx, candle, stamp, emit, "TAKE_PROFIT", price, ambiguous=False)
        return
    pnl = execution.position_pnl(side=position["side"], quantity_lots=level["qty"], entry=position["entry"],
                                 exit=price, contract=contract)
    portfolio["balance"] += pnl["net_pnl"]
    state["realized_pnl"] += pnl["net_pnl"]
    level["done"] = True
    position["partial_net"] += pnl["net_pnl"]
    position["partial_gross"] += pnl["gross_pnl"]
    position["partial_comm"] += pnl["commission"]
    position["partial_qty"] = round(position["partial_qty"] + level["qty"], 8)
    position["quantity"] = round(position["quantity"] - level["qty"], 8)
    if level["new_stop"] is not None:
        pending = position["pending_stop"]
        position["pending_stop"] = level["new_stop"] if pending is None else (
            max(pending, level["new_stop"]) if position["side"] == "long" else min(pending, level["new_stop"]))
    emit("POSITION_PARTIAL_CLOSE", side=position["side"], entry_at=position["entry_at"], entry=position["entry"],
         exit_at=stamp, exit=price, quantity=level["qty"], remaining=position["quantity"], level_r=level["r"],
         partial_net_pnl=pnl["net_pnl"], commission=pnl["commission"], quote="BID" if position["side"] == "long" else "ASK",
         fill_assumption="PARTIAL_LEVEL_OR_BETTER_OPEN")


def _pending_fill(order, bid, ask, slip, kind):
    side, price = order["side"], order["planned_price"]
    if side == "long":
        if kind == "stop" and ask["high"] >= price:
            return max(ask["open"], price) + slip
        if kind == "limit" and ask["low"] <= price:
            return min(ask["open"], price)
    else:
        if kind == "stop" and bid["low"] <= price:
            return min(bid["open"], price) - slip
        if kind == "limit" and bid["high"] >= price:
            return max(bid["open"], price)
    return None


def _open_position(portfolio, member, ctx, order, entry, candle, stamp, budget, emit, *, intrabar) -> None:
    config, contract, state, rs = member["config"], member["contract"], member["state"], ctx["rs"]
    side = order["side"]
    public = _public(order)
    snap = _restore(order["snap"])
    stop = engine.stop_price(rs, side, entry, snap)
    if not engine.valid_stop(side, entry, stop):
        emit("ORDER_REJECTED", reason="INVALID_STOP", **public)
        return
    distance = abs(entry - stop)
    unit = contract.contract_size * contract.quote_to_account
    member_equity = member["allocation"] + state["realized_pnl"]
    percent = min(rs["risk"]["percent"], config["trade_risk"] * 100.0)
    step_size = contract.volume_step
    lots = round(math.floor(member_equity * percent / 100.0 / (distance * unit) / step_size + 1e-9) * step_size, 8)
    if lots < step_size or lots <= 0:
        emit("ORDER_REJECTED", reason="MIN_LOT", **public)
        return
    target = engine.target_price(rs, side, entry, stop, snap)
    target = target if math.isfinite(target) else None
    if min(stop, target if target is not None else stop) <= 0:
        emit("ORDER_REJECTED", reason="INVALID_PROTECTIVE_PRICE", **public)
        return
    plan = []
    if rs.get("partial_exits"):
        r_target = abs(target - entry) / distance if target is not None else math.inf
        sign = 1.0 if side == "long" else -1.0
        for level in execution.partial_plan(rs["partial_exits"], lots, step_size):
            if level["r"] < r_target - 1e-12:
                plan.append({**level, "price": entry + sign * level["r"] * distance, "done": False,
                             "new_stop": None if level["stop_r"] is None else entry + sign * level["stop_r"] * distance})
    multiplier = lots * unit
    margin = entry * multiplier / config.get("leverage", 1)
    risk = distance * multiplier + 2 * lots * contract.commission_per_lot_side
    available = min(member_equity, budget["equity"])
    if margin > available or margin + budget["margin"] > budget["equity"]:
        emit("ORDER_REJECTED", reason="SHARED_MARGIN", margin=margin, **public)
        return
    limit = min(portfolio["risk_limit"], config["portfolio_risk_limit"])
    if risk + budget["risk"] > budget["equity"] * limit:
        emit("ORDER_REJECTED", reason="RISK_LIMIT", risk=risk, **public)
        return
    budget["margin"] += margin
    budget["risk"] += risk
    day = str(_local_day(ctx, ctx["bar_open_ms"]))
    state["trades_by_day"] = {key: value for key, value in state["trades_by_day"].items()
                              if int(key) >= int(day) - 2}
    state["trades_by_day"][day] = state["trades_by_day"].get(day, 0) + 1
    position = {"side": side, "signal_at": order["signal_at"], "entry_at": stamp, "entry": entry,
                "quantity": lots, "stop": stop, "target": target, "margin": margin, "risk": risk,
                "initial_stop": stop, "risk_distance": distance, "fav": entry, "be_done": False,
                "intrabar": intrabar, "entry_bar": state["bar_n"], "exit_pending": False}
    if plan:
        position.update(plan=plan, partial_net=0.0, partial_gross=0.0, partial_comm=0.0, partial_qty=0.0,
                        pending_stop=None)
    state["position"] = position
    emit("ORDER_FILLED", **{key: position[key] for key in (
        "side", "signal_at", "entry_at", "entry", "quantity", "stop", "target", "margin", "risk")},
        order_type=order["type"], slippage_points=contract.slippage_points,
        quote="ASK" if side == "long" else "BID", spread_open=candle["ask_open"] - candle["bid_open"],
        fill_assumption=("NEXT_OPEN" if order["type"] == "MARKET" else
                         "QUOTE_SIDE_STOP_OR_WORSE_OPEN" if order["type"] == "STOP" else
                         "QUOTE_SIDE_LIMIT_OR_BETTER_OPEN"))


def _close_position(portfolio, member, ctx, candle, stamp, emit, reason, level, *, ambiguous) -> None:
    state, contract = member["state"], member["contract"]
    position = state["position"]
    side = position["side"]
    slipped = reason in ("STOP_LOSS", "TIME_EXIT")
    slip = contract.slippage_points * contract.tick_size if slipped else 0.0
    price = level - slip if side == "long" else level + slip
    pnl = execution.position_pnl(side=side, quantity_lots=position["quantity"], entry=position["entry"],
                                 exit=price, contract=contract)
    portfolio["balance"] += pnl["net_pnl"]
    state["realized_pnl"] += pnl["net_pnl"]
    state["position"] = None
    state["last_exit_bar"] = state["bar_n"]
    summary = {key: position[key] for key in ("side", "signal_at", "entry_at", "entry", "quantity", "target", "margin", "risk")}
    if position.get("plan"):   # the closed record covers the whole trade; earlier partial fills are already in the balance
        summary["quantity"] = round(position["quantity"] + position["partial_qty"], 8)
        pnl = {**pnl, "gross_pnl": pnl["gross_pnl"] + position["partial_gross"],
               "commission": pnl["commission"] + position["partial_comm"],
               "net_pnl": pnl["net_pnl"] + position["partial_net"], "partial_fills_net_pnl": position["partial_net"]}
    emit("POSITION_CLOSED", **summary,
        stop=position["initial_stop"], exit_stop=position["stop"], exit_at=stamp, exit=price,
        exit_reason=reason, ambiguous=ambiguous, quote="BID" if side == "long" else "ASK",
        spread_open=candle["ask_open"] - candle["bid_open"],
        fill_assumption="TIME_EXIT_NEXT_BAR_OPEN" if reason == "TIME_EXIT" else "SL_BEFORE_TP_IF_BOTH", **pnl)
