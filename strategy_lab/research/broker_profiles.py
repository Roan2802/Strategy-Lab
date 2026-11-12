"""Named broker profiles: per-symbol contract facts with a source, a status and an effective-from history (V102).

A profile is a JSON file ``strategy_lab/data_cache/broker_profiles/<name>.json``::

    {"name": "demo_xyz", "description": "...", "aliases": {"GER40": "DE40"},
     "symbols": {"DE40": {"contract_size": [{"value": 1, "status": "BROKER_VERIFIED", "source": "spec sheet 2026-01-02",
                                             "effective_from": null, "note": ""}, ...]}}}

Rules
* Nothing is stored without a ``source`` (``set_field`` raises). No live connection, no account changes: values come
  from the user (``set_field``) or from an MT5 symbol-specification export file (``import_mt5``).
* Status of a stored value is BROKER_VERIFIED (taken from a broker document/export), DERIVED or ASSUMED. A field that is
  absent is UNAVAILABLE. ``verification_class`` maps every status used in the lab to VERIFIED/DERIVED/ASSUMED/UNAVAILABLE.
* A field is a history: the entry with the latest ``effective_from`` at or before the queried time applies
  (``effective_from`` null = always). Contract changes over time are new entries, never overwrites.

MT5 symbol-spec export (CSV with header, or JSON list/dict of objects), column names case-insensitive, any subset:
Symbol, Digits, ContractSize, TickSize, TickValue, VolumeMin, VolumeMax, VolumeStep, CurrencyBase, CurrencyProfit,
CurrencyMargin, SwapMode (MT5 SYMBOL_SWAP_MODE 0-9, number or name), SwapLong, SwapShort, Swap3Day (MT5
SYMBOL_SWAP_ROLLOVER3DAYS: 0=Sunday..6=Saturday), MarginInitial, MarginMaintenance, Spread, StopsLevel, Leverage,
CommissionPerLot.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path

import pandas as pd

PROFILE_DIR = Path(__file__).resolve().parent.parent / "data_cache" / "broker_profiles"
STORE_STATUSES = ("BROKER_VERIFIED", "DERIVED", "ASSUMED")
_CLASS = {"VERIFIED_FROM_DATA": "VERIFIED", "BROKER_VERIFIED": "VERIFIED", "VERIFIED": "VERIFIED", "DERIVED": "DERIVED",
          "ASSUMED": "ASSUMED", "UNKNOWN": "UNAVAILABLE", "UNAVAILABLE": "UNAVAILABLE"}
# field -> validator kind
FIELDS = {
    "contract_size": "pos", "tick_size": "pos", "tick_value": "pos", "digits": "int", "lot_step": "pos", "min_lot": "pos",
    "max_lot": "pos", "leverage": "pos", "margin_initial_rate": "rate", "margin_maintenance_rate": "rate",
    "margin_per_lot": "nonneg", "stop_out_level_pct": "nonneg", "commission_per_lot_side": "nonneg",
    "slippage_points": "nonneg", "spread_points": "nonneg", "stops_level": "nonneg",
    "swap_long": "num", "swap_short": "num", "swap_mode": "str", "swap_triple_day": "weekday",
    "swap_rollover_hour_utc": "hour", "base_currency": "ccy", "quote_currency": "ccy", "profit_currency": "ccy",
    "margin_currency": "ccy", "sessions": "sessions",
}
_MT5_COLUMNS = {
    "contractsize": "contract_size", "ticksize": "tick_size", "tickvalue": "tick_value", "digits": "digits",
    "volumemin": "min_lot", "volumemax": "max_lot", "volumestep": "lot_step", "currencybase": "base_currency",
    "currencyprofit": "profit_currency", "currencymargin": "margin_currency", "swapmode": "swap_mode",
    "swaplong": "swap_long", "swapshort": "swap_short", "swap3day": "swap_triple_day",
    "margininitial": "margin_per_lot", "marginmaintenance": "margin_maintenance_per_lot", "spread": "spread_points",
    "stopslevel": "stops_level", "leverage": "leverage", "commissionperlot": "commission_per_lot_side"}


def verification_class(status: str | None) -> str:
    """VERIFIED / DERIVED / ASSUMED / UNAVAILABLE for any status string used in the lab."""
    return _CLASS.get(status or "UNAVAILABLE", "ASSUMED")


def _path(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", name or ""):
        raise ValueError("profile name: 1-64 characters of letters, digits, _ or -")
    return PROFILE_DIR / f"{name}.json"


def _write(profile: dict) -> None:
    path = _path(profile["name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def list_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILE_DIR.glob("*.json")) if PROFILE_DIR.is_dir() else []


def create_profile(name: str, description: str = "") -> dict:
    if _path(name).exists():
        raise ValueError(f"profile {name!r} already exists")
    profile = {"name": name, "description": description, "aliases": {}, "symbols": {}}
    _write(profile)
    return profile


def load_profile(name: str) -> dict:
    try:
        raw = json.loads(_path(name).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise KeyError(f"unknown broker profile {name!r}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("symbols"), dict):
        raise ValueError(f"broker profile {name!r} is malformed")
    raw.setdefault("aliases", {})
    return raw


def _check(field: str, value):
    kind = FIELDS.get(field)
    if kind is None:
        raise ValueError(f"unknown field {field!r}; supported: {', '.join(sorted(FIELDS))}")
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    if kind == "str":
        ok = isinstance(value, str) and bool(value.strip())
    elif kind == "ccy":
        ok = isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z]{3}", value.strip()))
        value = value.strip().upper() if ok else value
    elif kind == "sessions":
        ok = (isinstance(value, list) and bool(value) and all(
            isinstance(s, dict) and set(s) <= {"weekday", "from_utc", "to_utc"} and s.get("weekday") in range(7)
            and re.fullmatch(r"\d{2}:\d{2}", str(s.get("from_utc", ""))) and re.fullmatch(r"\d{2}:\d{2}", str(s.get("to_utc", "")))
            for s in value))
    elif kind == "weekday":
        ok = isinstance(value, int) and not isinstance(value, bool) and value in range(7)
    elif kind == "hour":
        ok = num(value) and 0 <= value < 24
    elif kind == "int":
        ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
    elif kind == "num":
        ok = num(value)
    elif kind == "rate":
        ok = num(value) and 0 < value <= 1
    elif kind == "nonneg":
        ok = num(value) and value >= 0
    else:
        ok = num(value) and value > 0
    if not ok:
        raise ValueError(f"invalid value {value!r} for field {field!r} (kind {kind})")
    return value


def set_field(profile: str | dict, symbol: str, field: str, value, source: str, *, status: str = "BROKER_VERIFIED",
              effective_from: str | None = None, note: str = "") -> dict:
    """Manual input (API-free). ``source`` is mandatory (spec sheet, statement, export file...). Entries for the same
    field and ``effective_from`` are replaced, other effective dates form the contract history. Returns the entry."""
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required: a value without a source is not stored")
    if status not in STORE_STATUSES:
        raise ValueError(f"status must be one of {STORE_STATUSES}")
    prof = load_profile(profile) if isinstance(profile, str) else profile
    sym = str(symbol).strip().upper()
    if not sym:
        raise ValueError("symbol is required")
    eff = None if effective_from is None else pd.Timestamp(effective_from).tz_localize("UTC") if pd.Timestamp(
        effective_from).tzinfo is None else pd.Timestamp(effective_from).tz_convert("UTC")
    entry = {"value": _check(field, value), "status": status, "source": source.strip(),
             "effective_from": None if eff is None else eff.isoformat(), "note": note}
    rows = [r for r in prof["symbols"].setdefault(sym, {}).setdefault(field, []) if r.get("effective_from") != entry["effective_from"]]
    rows.append(entry)
    rows.sort(key=lambda r: (r["effective_from"] is not None, r["effective_from"] or ""))
    prof["symbols"][sym][field] = rows
    _write(prof)
    return entry


def set_alias(profile: str | dict, broker_symbol: str, lab_symbol: str) -> None:
    prof = load_profile(profile) if isinstance(profile, str) else profile
    prof.setdefault("aliases", {})[broker_symbol.strip().upper()] = lab_symbol.strip().upper()
    _write(prof)


def history(profile: dict, symbol: str, field: str) -> list[dict]:
    return list((profile.get("symbols", {}).get(symbol.upper(), {}) or {}).get(field, []))


def value_at(profile: dict, symbol: str, field: str, ts=None) -> dict | None:
    """The entry in force at ``ts`` (UTC; None = the latest entry), or None when the field is absent (UNAVAILABLE)."""
    rows = history(profile, symbol, field)
    if ts is not None:
        t = pd.Timestamp(ts)
        t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
        rows = [r for r in rows if r.get("effective_from") is None or pd.Timestamp(r["effective_from"]) <= t]
    return rows[-1] if rows else None


def fields_at(profile: dict, symbol: str, ts=None) -> dict[str, dict]:
    return {f: e for f in FIELDS if (e := value_at(profile, symbol, f, ts)) is not None}


# ---------------------------------------------------------------------------------------------- MT5 import
_SWAP_MODE_NAMES = {"DISABLED": 0, "POINTS": 1, "CURRENCY_SYMBOL": 2, "CURRENCY_MARGIN": 4, "CURRENCY_DEPOSIT": 5,
                    "INTEREST_CURRENT": 6, "INTEREST_OPEN": 7, "REOPEN_CURRENT": 8, "REOPEN_BID": 9, "INTEREST": 3}


def _swap_mode(raw) -> str:
    text = str(raw).strip().upper().replace("SYMBOL_SWAP_MODE_", "")
    return str(_SWAP_MODE_NAMES[text]) if text in _SWAP_MODE_NAMES else str(int(float(text)))


def _num(raw):
    return float(str(raw).strip().replace(",", "."))


def _read_rows(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, dict):
            data = [{"Symbol": k, **v} for k, v in data.items()] if all(isinstance(v, dict) for v in data.values()) else [data]
        return list(data)
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
    return list(csv.DictReader(text.splitlines(), dialect=dialect))


def import_mt5(profile: str, path, *, symbol_map: dict | None = None, as_of: str | None = None,
               only: list[str] | None = None) -> dict:
    """Import an MT5 symbol-specification export (CSV/JSON, see module docstring) into ``profile`` as
    BROKER_VERIFIED values with source ``mt5_export:<file>:sha256=<12>``. ``symbol_map`` maps broker names to lab
    symbols (also stored as aliases). ``as_of`` is the export date (effective_from of every value). Unparseable cells
    are skipped and reported, never guessed. No live connection is made."""
    file = Path(path)
    digest = hashlib.sha256(file.read_bytes()).hexdigest()[:12]
    source = f"mt5_export:{file.name}:sha256={digest}"
    prof = load_profile(profile)
    mapping = {k.strip().upper(): v.strip().upper() for k, v in (symbol_map or {}).items()}
    imported, skipped = 0, []
    for row in _read_rows(file):
        norm = {re.sub(r"[^a-z0-9]", "", str(k).lower()): v for k, v in row.items() if k is not None}
        broker_symbol = str(norm.get("symbol", "")).strip().upper()
        if not broker_symbol or (only and broker_symbol not in {s.upper() for s in only}):
            continue
        lab = mapping.get(broker_symbol, broker_symbol)
        if lab != broker_symbol:
            prof.setdefault("aliases", {})[broker_symbol] = lab
        for col, field in _MT5_COLUMNS.items():
            raw = norm.get(col)
            if raw is None or str(raw).strip() == "":
                continue
            try:
                if field == "swap_mode":
                    value = _swap_mode(raw)
                elif field in ("base_currency", "profit_currency", "margin_currency"):
                    value = str(raw).strip()
                elif field == "digits":
                    value = int(_num(raw))
                elif field == "swap_triple_day":
                    value = (int(_num(raw)) - 1) % 7            # MT5 0=Sunday..6=Saturday -> 0=Monday..6=Sunday
                elif field in ("margin_maintenance_per_lot",):
                    continue                                    # stored only as note below; not a lab field
                else:
                    value = _num(raw)
                if field in ("min_lot", "max_lot", "lot_step", "contract_size", "tick_size", "tick_value") and value <= 0:
                    raise ValueError("must be > 0")
                if field in ("leverage", "margin_per_lot") and value <= 0:
                    continue                                    # 0 = "computed by the broker's margin mode": no fact
                entry = {"value": _check(field, value), "status": "BROKER_VERIFIED", "source": source,
                         "effective_from": None if as_of is None else pd.Timestamp(as_of).tz_localize("UTC").isoformat()
                         if pd.Timestamp(as_of).tzinfo is None else pd.Timestamp(as_of).tz_convert("UTC").isoformat(),
                         "note": f"MT5 column {col}"}
            except (ValueError, KeyError) as exc:
                skipped.append({"symbol": broker_symbol, "column": col, "raw": str(raw), "error": str(exc)})
                continue
            rows = [r for r in prof["symbols"].setdefault(lab, {}).setdefault(field, [])
                    if r.get("effective_from") != entry["effective_from"]]
            rows.append(entry)
            rows.sort(key=lambda r: (r["effective_from"] is not None, r["effective_from"] or ""))
            prof["symbols"][lab][field] = rows
            imported += 1
    _write(prof)
    return {"profile": profile, "source": source, "imported_values": imported, "skipped": skipped}


# ---------------------------------------------------------------------------------------------- swap for the engines
def swap_execution_config(profile: dict, symbol: str, ts=None, *, status_ok=("VERIFIED",)) -> tuple[dict | None, str]:
    """Execution-model ``swap`` block from VERIFIED profile data (account currency per lot per night) or
    (None, reason). Only MT5 mode 5 (deposit currency, money per lot) and mode 1 (points, converted with the broker's
    TickValue/TickSize/Digits, all of which must be verified) are converted; anything else stays a labelled scenario."""
    parts = {f: value_at(profile, symbol, f, ts) for f in ("swap_long", "swap_short", "swap_mode", "tick_value", "tick_size",
                                                         "digits", "swap_triple_day", "swap_rollover_hour_utc")}
    if not parts["swap_long"] or not parts["swap_short"] or not parts["swap_mode"]:
        return None, "swap_long/swap_short/swap_mode missing: UNAVAILABLE (use a labelled SCENARIO)"
    if any(verification_class(parts[k]["status"]) not in status_ok for k in ("swap_long", "swap_short", "swap_mode")):
        return None, "swap values are not VERIFIED"
    mode, long_, short_ = parts["swap_mode"]["value"], parts["swap_long"]["value"], parts["swap_short"]["value"]
    if mode == "5":
        scale = 1.0
    elif mode == "1" and all(parts[k] for k in ("tick_value", "tick_size", "digits")) and all(
            verification_class(parts[k]["status"]) in status_ok for k in ("tick_value", "tick_size", "digits")):
        scale = (10.0 ** -parts["digits"]["value"]) / parts["tick_size"]["value"] * parts["tick_value"]["value"]
    else:
        return None, f"swap mode {mode} is not convertible to account currency per lot"
    cfg = {"long_per_lot_per_night": long_ * scale, "short_per_lot_per_night": short_ * scale, "status": "VERIFIED",
           "triple_weekday": parts["swap_triple_day"]["value"] if parts["swap_triple_day"] else 2}
    if parts["swap_rollover_hour_utc"]:
        cfg["rollover_hour_utc"] = parts["swap_rollover_hour_utc"]["value"]
    return cfg, "from VERIFIED profile data"


def margin_execution_config(profile: dict, symbol: str, ts=None, *, stop_out_level_pct: float | None = None) -> tuple[dict | None, str]:
    """Execution-model ``margin`` block: CONTRACTUAL only when the profile holds a VERIFIED leverage or initial margin
    rate; otherwise (None, reason). Stop-out level is account/broker policy and must be passed or verified."""
    rate = value_at(profile, symbol, "margin_initial_rate", ts)
    lev = value_at(profile, symbol, "leverage", ts)
    pick = rate or lev
    if pick is None:
        return None, "leverage / margin_initial_rate missing: UNAVAILABLE"
    block = {"initial_rate": rate["value"]} if rate else {"leverage": lev["value"]}
    maint = value_at(profile, symbol, "margin_maintenance_rate", ts)
    if maint:
        block["maintenance_rate"] = maint["value"]
    so = value_at(profile, symbol, "stop_out_level_pct", ts)
    if so:
        block["stop_out_level_pct"] = so["value"]
    elif stop_out_level_pct is not None:
        block["stop_out_level_pct"] = stop_out_level_pct
    block["status"] = "CONTRACTUAL" if verification_class(pick["status"]) == "VERIFIED" else "SIMULATION_ASSUMPTION"
    return block, f"from profile ({pick['status']})"
