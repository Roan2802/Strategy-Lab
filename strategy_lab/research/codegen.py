"""Generate a Jesse strategy from a rule spec; the generated code calls the same rule_engine as the backtester."""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from runtime.strategy_lab import rule_engine as engine

SCHEMA = engine.SCHEMA


def _p(name, type_, lo, hi, default, step=None):
    out = {"name": name, "type": type_, "min": lo, "max": hi, "default": default}
    if step is not None:
        out["step"] = step
    return out


TEMPLATES: dict[str, dict] = {
    "ema_rsi_pullback": {
        "schema": SCHEMA, "name": "EmaRsiPullback", "symbol": "XAUUSD", "timeframe": "M15", "direction": "both",
        "description": "Trendvolgend: koop een RSI-pullback in een EMA-trend, verkoop het spiegelbeeld.",
        "entry": {
            "long": {"all": [
                {"left": {"indicator": "ema", "period": {"param": "ema_fast"}},
                 "op": ">", "right": {"indicator": "ema", "period": {"param": "ema_slow"}}},
                {"left": {"indicator": "rsi", "period": {"param": "rsi_period"}}, "op": "crosses_above",
                 "right": {"value": {"param": "rsi_low"}}}]},
            "short": {"all": [
                {"left": {"indicator": "ema", "period": {"param": "ema_fast"}},
                 "op": "<", "right": {"indicator": "ema", "period": {"param": "ema_slow"}}},
                {"left": {"indicator": "rsi", "period": {"param": "rsi_period"}}, "op": "crosses_below",
                 "right": {"value": {"param": "rsi_high"}}}]}},
        "order": {"type": "market"},
        "stop": {"type": "atr", "mult": {"param": "stop_mult"}, "period": 14},
        "target": {"type": "r_multiple", "value": 2.0},
        "trailing": None, "break_even": None, "time_exit_bars": None, "cooldown_bars": 0,
        "max_trades_per_day": None, "session": None, "risk": {"percent": 0.5},
        "params": [_p("ema_fast", "int", 5, 50, 20, 1), _p("ema_slow", "int", 20, 200, 50, 5),
                   _p("rsi_period", "int", 2, 30, 14, 1), _p("rsi_low", "int", 20, 50, 40, 1),
                   _p("rsi_high", "int", 50, 80, 60, 1), _p("stop_mult", "float", 0.5, 4.0, 1.5, 0.25)]},
    "opening_range_breakout": {
        "schema": SCHEMA, "name": "OpeningRangeBreakout", "symbol": "XAUUSD", "timeframe": "M5",
        "direction": "both",
        "description": "Breakout van de eerste 30 minuten na de New York-open (09:30), een trade per dag.",
        "entry": {
            "long": {"left": {"price": "close"}, "op": ">",
                     "right": {"level": "opening_range_high", "minutes": {"param": "or_minutes"}}},
            "short": {"left": {"price": "close"}, "op": "<",
                      "right": {"level": "opening_range_low", "minutes": {"param": "or_minutes"}}}},
        "order": {"type": "market"},
        "stop": {"type": "atr", "mult": 1.5, "period": 14},
        "target": {"type": "r_multiple", "value": 2.0},
        "trailing": None, "break_even": None, "time_exit_bars": None, "cooldown_bars": 0,
        "max_trades_per_day": 1,
        "session": {"timezone": "America/New_York", "start": "09:30", "end": "16:00"},
        "risk": {"percent": 0.5},
        "params": [_p("or_minutes", "int", 5, 120, 30, 5)]},
    "donchian_breakout": {
        "schema": SCHEMA, "name": "DonchianBreakout", "symbol": "XAUUSD", "timeframe": "H1", "direction": "both",
        "description": "Kanaaldoorbraak: slotkoers buiten de Donchian-kanaal van de voorgaande candles.",
        "entry": {
            "long": {"left": {"price": "close"}, "op": ">",
                     "right": {"indicator": "donchian_high", "period": {"param": "channel"}, "shift": 1}},
            "short": {"left": {"price": "close"}, "op": "<",
                      "right": {"indicator": "donchian_low", "period": {"param": "channel"}, "shift": 1}}},
        "order": {"type": "market"},
        "stop": {"type": "atr", "mult": 2.0, "period": 14},
        "target": {"type": "r_multiple", "value": 3.0},
        "trailing": {"activate_r": 1.5, "atr_mult": 2.0, "period": 14}, "break_even": None,
        "time_exit_bars": None, "cooldown_bars": 2, "max_trades_per_day": None, "session": None,
        "risk": {"percent": 0.5},
        "params": [_p("channel", "int", 10, 100, 20, 5)]},
    "bollinger_mean_reversion": {
        "schema": SCHEMA, "name": "BollingerMeanReversion", "symbol": "XAUUSD", "timeframe": "M15",
        "direction": "both",
        "description": "Mean reversion in een rustige markt (lage ADX): terug binnen de Bollinger-band.",
        "entry": {
            "long": {"all": [
                {"left": {"price": "close"}, "op": "crosses_above",
                 "right": {"indicator": "bb_lower", "period": {"param": "bb_period"}, "mult": {"param": "bb_mult"}}},
                {"left": {"indicator": "adx", "period": 14}, "op": "<", "right": {"value": {"param": "adx_max"}}}]},
            "short": {"all": [
                {"left": {"price": "close"}, "op": "crosses_below",
                 "right": {"indicator": "bb_upper", "period": {"param": "bb_period"}, "mult": {"param": "bb_mult"}}},
                {"left": {"indicator": "adx", "period": 14}, "op": "<", "right": {"value": {"param": "adx_max"}}}]}},
        "order": {"type": "market"},
        "stop": {"type": "atr", "mult": 1.5, "period": 14},
        "target": {"type": "r_multiple", "value": 1.5},
        "trailing": None, "break_even": {"activate_r": 1.0}, "time_exit_bars": 24, "cooldown_bars": 1,
        "max_trades_per_day": None, "session": None, "risk": {"percent": 0.5},
        "params": [_p("bb_period", "int", 10, 50, 20, 2), _p("bb_mult", "float", 1.5, 3.0, 2.0, 0.25),
                   _p("adx_max", "int", 10, 40, 25, 1)]},
    "nr4_inside_bar_breakout": {
        "schema": SCHEMA, "name": "Nr4InsideBarBreakout", "symbol": "XAUUSD", "timeframe": "H1",
        "direction": "both",
        "description": "NR4 + inside bar in de richting van de EMA-trend; stop-order voorbij de signaalcandle.",
        "entry": {
            "long": {"all": [{"pattern": "nr4"}, {"pattern": "inside_bar"},
                             {"left": {"price": "close"}, "op": ">",
                              "right": {"indicator": "ema", "period": {"param": "trend_ema"}}}]},
            "short": {"all": [{"pattern": "nr4"}, {"pattern": "inside_bar"},
                              {"left": {"price": "close"}, "op": "<",
                               "right": {"indicator": "ema", "period": {"param": "trend_ema"}}}]}},
        "order": {"type": "stop", "offset_atr": 0.1, "atr_period": 14, "expiry_bars": 3},
        "stop": {"type": "atr", "mult": 1.5, "period": 14},
        "target": {"type": "r_multiple", "value": 2.0},
        "trailing": None, "break_even": None, "time_exit_bars": None, "cooldown_bars": 0,
        "max_trades_per_day": None, "session": None, "risk": {"percent": 0.5},
        "params": [_p("trend_ema", "int", 20, 200, 50, 5)]},
}


def class_name(spec: dict) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", str(spec["name"]))
    name = "".join(p[:1].upper() + p[1:] for p in parts if p)
    return name if name and name[0].isalpha() else f"S{name}"


def _doc_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')


def _hyperparameters(spec: dict) -> str:
    rows = [f'            {{"name": {p["name"]!r}, "type": {p["type"]}, "min": {p["min"]!r}, '
            f'"max": {p["max"]!r}, "default": {p["default"]!r}}},' for p in spec.get("params") or []]
    return "[\n" + "\n".join(rows) + ("\n        ]" if rows else "        ]")


_TEMPLATE = '''"""__DOC__

Generated by Strategy Lab codegen. Spec sha256: __SHA__
Do not edit by hand: regenerate from spec.json. Rules are evaluated by strategy_lab.rule_engine,
the same module the Strategy Lab rules backtester uses.
"""
import json
import math

import numpy as np
import jesse.helpers as jh
from jesse.strategies import Strategy
from strategy_lab.events import InspectorMixin
from strategy_lab.rule_engine import (evaluate, validate_spec, spec_sha256, bar_checks, bar_decision,
                                      exit_snapshot, stop_price, target_price, order_price, valid_stop,
                                      local_day_ids)

SPEC_SHA256 = __SHA_LITERAL__
SPEC_JSON = __JSON_LITERAL__
SPEC = json.loads(SPEC_JSON)
LOOKBACK_BARS = __LOOKBACK__
_ERRORS = validate_spec(SPEC)
if _ERRORS:
    raise ValueError("invalid rule spec: " + "; ".join(_ERRORS))
if spec_sha256(SPEC) != SPEC_SHA256:
    raise ValueError("embedded spec does not match SPEC_SHA256")


class __CLASS__(InspectorMixin, Strategy):
    def hyperparameters(self):
        return __HP__

    def _lab_init(self):
        if not hasattr(self, "_lab_trades"):
            self._lab_trades = {}
            self._lab_last_exit = -10 ** 9
            self._lab_order_index = None
            self._lab_long = self._lab_short = False
            self._lab_plan = {}
            self._lab_stop = None

    def _lab_params(self):
        return {p["name"]: self.hp[p["name"]] for p in SPEC.get("params", [])}

    def _lab_make_plan(self, side, res, i):
        rs = res["spec"]
        close = float(self.candles[-1][2])
        snap = exit_snapshot(res["exits"], i)
        entry = close if rs["order"]["type"] == "market" else order_price(rs, side, close, snap)
        stop = stop_price(rs, side, entry, snap)
        if not valid_stop(side, entry, stop):
            return None
        qty = float(self.balance) * rs["risk"]["percent"] / 100.0 / abs(entry - stop)
        qty = min(qty, 0.99 * float(self.balance) * float(getattr(self, "leverage", 1) or 1) / entry)
        if not math.isfinite(qty) or qty <= 0:
            return None
        return {"qty": qty, "entry": entry, "snap": snap}

    def before(self):
        if jh.is_livetrading():
            return
        self._lab_init()
        c = self.candles[-LOOKBACK_BARS:]
        arrays = {"time": c[:, 0].astype(np.int64), "open": c[:, 1].astype(float), "close": c[:, 2].astype(float),
                  "high": c[:, 3].astype(float), "low": c[:, 4].astype(float), "volume": c[:, 5].astype(float)}
        res = evaluate(SPEC, arrays, self._lab_params(), validate=False)
        i = len(c) - 1
        rs = res["spec"]
        self._lab_res, self._lab_i = res, i
        day = int(local_day_ids(rs, arrays["time"][-1:])[0])
        self._lab_day = day
        limit = rs.get("max_trades_per_day")
        flat = not self.position.is_open and len(self.entry_orders) == 0
        allowed = (flat and self.index - self._lab_last_exit >= rs.get("cooldown_bars", 0)
                   and not (limit and self._lab_trades.get(day, 0) >= limit))
        want_long, want_short = bool(res["long_signal"][i]), bool(res["short_signal"][i])
        if want_long and want_short:
            want_long = want_short = False
        self._lab_plan = {}
        self._lab_long = self._lab_short = False
        if allowed and want_long:
            self._lab_plan["long"] = self._lab_make_plan("long", res, i)
            self._lab_long = self._lab_plan["long"] is not None
        if allowed and want_short:
            self._lab_plan["short"] = self._lab_make_plan("short", res, i)
            self._lab_short = self._lab_plan["short"] is not None
        decision, reason = bar_decision(res, i)
        if decision == "PASS" and not (self._lab_long or self._lab_short):
            decision, reason = "SKIP", "NOT_ALLOWED_OR_INVALID_STOP"
        checks = bar_checks(res, i) + [{"name": "Position/cooldown/daily limit allow entry", "value": allowed,
                                        "operator": "==", "threshold": True,
                                        "result": "PASS" if allowed else "SKIP"}]
        self.record_candle(checks=checks, decision=decision, reason=reason,
                           state={"index": int(self.index), "position_open": bool(self.position.is_open),
                                  "spec_sha256": SPEC_SHA256, "notes": list(res["notes"])})

    def should_long(self):
        return not jh.is_livetrading() and bool(getattr(self, "_lab_long", False))

    def should_short(self):
        return not jh.is_livetrading() and bool(getattr(self, "_lab_short", False))

    def _lab_submit(self, side):
        plan = self._lab_plan[side]
        rs = self._lab_res["spec"]
        self._lab_snap = plan["snap"]
        if rs["order"]["type"] != "market":
            self._lab_order_index = self.index
        order = (plan["qty"], plan["entry"])
        if side == "long":
            self.buy = order
        else:
            self.sell = order
        self.record_lifecycle(event_type="ORDER_SUBMITTED", timestamp_ms=int(self.current_candle[0]),
                              side="BUY" if side == "long" else "SELL", order_type=rs["order"]["type"].upper(),
                              planned_price=plan["entry"], quantity=plan["qty"],
                              signal_timestamp_ms=int(self.current_candle[0]))

    def go_long(self):
        self._lab_submit("long")

    def go_short(self):
        self._lab_submit("short")

    def should_cancel_entry(self):
        started = getattr(self, "_lab_order_index", None)
        if started is None or len(self.entry_orders) == 0:
            return False
        return self.index - started >= self._lab_res["spec"]["order"]["expiry_bars"]

    def on_cancel(self):
        self._lab_order_index = None

    def on_open_position(self, order):
        self._lab_init()
        self._lab_order_index = None
        rs = self._lab_res["spec"]
        side = "long" if self.position.is_long else "short"
        entry, qty = float(self.position.entry_price), abs(float(self.position.qty))
        snap = getattr(self, "_lab_snap", {})
        stop = stop_price(rs, side, entry, snap)
        if not valid_stop(side, entry, stop):
            self.liquidate()
            return
        target = target_price(rs, side, entry, stop, snap)
        self.stop_loss = qty, stop
        if math.isfinite(target):
            self.take_profit = qty, target
        self._lab_stop, self._lab_fav, self._lab_risk = stop, entry, abs(entry - stop)
        self._lab_entry_index, self._lab_be = self.index, False
        self._lab_trades[self._lab_day] = self._lab_trades.get(self._lab_day, 0) + 1
        self.record_lifecycle(event_type="POSITION_OPEN", order_id=str(order.id), side=side, entry_price=entry,
                              quantity=qty, stop_loss=stop, take_profit=target if math.isfinite(target) else None,
                              fill_timestamp_ms=int(self.current_candle[0]))

    def update_position(self):
        if jh.is_livetrading() or not self.position.is_open or self._lab_stop is None:
            return
        rs = self._lab_res["spec"]
        i = self._lab_i
        is_long = self.position.is_long
        entry, qty = float(self.position.entry_price), abs(float(self.position.qty))
        high, low = float(self.current_candle[3]), float(self.current_candle[4])
        self._lab_fav = max(self._lab_fav, high) if is_long else min(self._lab_fav, low)
        r_fav = ((self._lab_fav - entry) if is_long else (entry - self._lab_fav)) / self._lab_risk
        stop = self._lab_stop
        be, tr = rs.get("break_even"), rs.get("trailing")
        if be and not self._lab_be and r_fav >= be["activate_r"]:
            self._lab_be = True
            stop = max(stop, entry) if is_long else min(stop, entry)
        if tr and r_fav >= tr["activate_r"]:
            atr_now = float(self._lab_res["exits"]["trail_atr"][i])
            if math.isfinite(atr_now):
                new = self._lab_fav - tr["atr_mult"] * atr_now if is_long else self._lab_fav + tr["atr_mult"] * atr_now
                stop = max(stop, new) if is_long else min(stop, new)
        if stop != self._lab_stop:
            self._lab_stop = stop
            self.stop_loss = qty, stop
        tx = rs.get("time_exit_bars")
        if tx and self.index - self._lab_entry_index + 1 >= tx:
            self.liquidate()

    def on_close_position(self, order, closed_trade):
        self._lab_init()
        self._lab_last_exit = self.index
        self._lab_stop = None
        self.record_lifecycle(event_type="POSITION_CLOSE", order_id=str(order.id),
                              trade_id=str(getattr(closed_trade, "id", "")),
                              entry_price=float(closed_trade.entry_price), exit_price=float(closed_trade.exit_price),
                              pnl=float(closed_trade.pnl), fee=float(closed_trade.fee),
                              exit_reason=str(getattr(order, "submitted_via", "")))
'''


def generate_jesse_strategy(spec: dict) -> str:
    """Complete Jesse strategy source for `spec` (raises ValueError if the spec is invalid)."""
    errs = engine.validate_spec(spec)
    if errs:
        raise ValueError("invalid spec: " + "; ".join(errs))
    sha = engine.spec_sha256(spec)
    spec_json = json.dumps(spec, sort_keys=True, ensure_ascii=False)
    code = (_TEMPLATE
            .replace("__DOC__", _doc_escape(engine.describe(spec)))
            .replace("__SHA_LITERAL__", repr(sha))
            .replace("__SHA__", sha)
            .replace("__JSON_LITERAL__", repr(spec_json))
            .replace("__LOOKBACK__", str(engine.lookback_bars(spec)))
            .replace("__CLASS__", class_name(spec))
            .replace("__HP__", _hyperparameters(spec)))
    compile(code, f"<{class_name(spec)}>", "exec")
    return code


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def write_strategy(spec: dict, strategies_root, overwrite: bool = False) -> Path:
    """Write <root>/<Name>/__init__.py + spec.json. A different existing strategy needs overwrite=True
    (a timestamped .bak copy of the old files is kept alongside)."""
    code = generate_jesse_strategy(spec)
    spec_text = json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    target = Path(strategies_root) / class_name(spec)
    files = {"__init__.py": code, "spec.json": spec_text}
    if target.exists():
        existing = {name: (target / name).read_text(encoding="utf-8") if (target / name).is_file() else None
                    for name in files}
        if existing == files:
            return target
        if not overwrite:
            raise FileExistsError(f"strategy {target.name} already exists and differs; pass overwrite=True")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        for name in files:
            if (target / name).is_file():
                shutil.copy2(target / name, target / f"{name}.bak-{stamp}")
    target.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        _atomic_write(target / name, text)
    return target
