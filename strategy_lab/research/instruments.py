"""Instrument registry: contract facts per symbol with an honest provenance flag per field.

Every field is ``{"value": ..., "status": VERIFIED_FROM_DATA | ASSUMED | UNKNOWN, "note": ...}``. Only what the
Dukascopy data itself shows (price decimals, spread, first/last bar, observed trading hours, conversion pair
availability) is VERIFIED_FROM_DATA; contract size, lot step and commission are broker facts we do not have.
Currency conversion to the USD account uses the real Dukascopy EURUSD/USDJPY/USDCAD series (causal, per bar open).
"""
from __future__ import annotations

import json
import math
import threading
from pathlib import Path

import numpy as np
import pandas as pd

from . import backtest

VERIFIED, ASSUMED, UNKNOWN, BROKER = "VERIFIED_FROM_DATA", "ASSUMED", "UNKNOWN", "BROKER_VERIFIED"
ACCOUNT_CURRENCY = "USD"
HERE = Path(__file__).resolve().parent.parent
CACHE = HERE.parent / "evidence" / "v10" / "multimarket" / "instrument_stats_cache.json"
# Broker facts the user entered: {"DE40": {"contract_size": {"value": 1, "source": "<broker doc / statement>",
# "status": "BROKER_VERIFIED"}}}. Entries without a source or without BROKER_VERIFIED are ignored.
BROKER_OVERRIDES = HERE.parent / "evidence" / "v101" / "multimarket" / "broker_overrides.json"
# Fields that must be VERIFIED_FROM_DATA or BROKER_VERIFIED before any money-based approval is allowed.
MONEY_REQUIRED = ("contract_size", "tick_size", "lot_step", "min_lot", "commission_per_lot_side", "margin", "swap",
                  "slippage", "typical_spread", "account_conversion")
_OK_STATUS = (VERIFIED, BROKER)
_lock = threading.Lock()

# symbol -> (asset_class, quote ccy, contract_size, lot_step, margin note, hours note)
_STATIC = {
    "XAUUSD": ("metal", "USD", 100.0, 0.01, "Hefboom/margin broker-afhankelijk (metals ~1:20 retail).", "24x5 met dagelijkse pauze"),
    "EURUSD": ("fx", "USD", 100000.0, 0.01, "FX-margin broker-afhankelijk (~1:30 retail EU).", "24x5"),
    "GBPUSD": ("fx", "USD", 100000.0, 0.01, "FX-margin broker-afhankelijk.", "24x5"),
    "USDJPY": ("fx", "JPY", 100000.0, 0.01, "FX-margin broker-afhankelijk; quote JPY -> omrekening nodig.", "24x5"),
    "AUDUSD": ("fx", "USD", 100000.0, 0.01, "FX-margin broker-afhankelijk.", "24x5"),
    "USDCAD": ("fx", "CAD", 100000.0, 0.01, "FX-margin broker-afhankelijk; quote CAD -> omrekening nodig.", "24x5"),
    "US30": ("index", "USD", 1.0, 0.01, "Index-CFD margin broker-afhankelijk (~1:20).", "bijna 24x5 CFD, kern US-sessie"),
    "US500": ("index", "USD", 1.0, 0.01, "Index-CFD margin broker-afhankelijk.", "bijna 24x5 CFD, kern US-sessie"),
    "USATECHIDXUSD": ("index", "USD", 1.0, 0.01, "Index-CFD margin broker-afhankelijk.", "bijna 24x5 CFD, kern US-sessie"),
    "DE40": ("index", "EUR", 1.0, 0.01, "Index-CFD margin broker-afhankelijk; quote EUR -> omrekening nodig.", "kern EU-sessie, CFD bijna 24x5"),
    "BTCUSD": ("crypto", "USD", 1.0, 0.01, "Crypto-CFD margin broker-afhankelijk.", "24x7 (spot) / broker kan weekend sluiten"),
}
ALIASES = {"USTEC": "USATECHIDXUSD", "USA30IDXUSD": "US30", "USA500IDXUSD": "US500", "DEUIDXEUR": "DE40"}
SYMBOLS = list(_STATIC)


def canonical(symbol: str) -> str:
    s = symbol.upper()
    return ALIASES.get(s, s)


def _f(value, status, note="", source=None):
    return {"value": value, "status": status, "note": note,
            "source": source or {VERIFIED: "dukascopy_data", ASSUMED: "lab_default", UNKNOWN: "none"}.get(status, "")}


def _overrides() -> dict:
    try:
        raw = json.loads(BROKER_OVERRIDES.read_text(encoding="utf-8")) if BROKER_OVERRIDES.is_file() else {}
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _decimals_tick(prices: np.ndarray) -> float | None:
    """Smallest price increment implied by the quoted decimals (10^-d), from a sample of real prices."""
    sample = prices[np.isfinite(prices)]
    if sample.size == 0:
        return None
    sample = sample[:: max(1, sample.size // 20000)]
    for d in range(0, 9):
        if np.all(np.abs(sample * 10 ** d - np.round(sample * 10 ** d)) < 1e-6 * max(1.0, d)):
            return float(10.0 ** -d)
    return None


def _measure(dm, symbol: str) -> dict:
    """Facts measured from the H1 bid/ask candles of the whole available history."""
    frame, man = dm.candles(symbol, "H1")
    if frame.empty:
        return {}
    bid_c, ask_c = frame["bid_close"].to_numpy(float), frame["ask_close"].to_numpy(float)
    spread = ask_c - bid_c
    spread = spread[np.isfinite(spread) & (spread >= 0)]
    tick = _decimals_tick(frame["bid_close"].to_numpy(float))
    hours = frame.index.hour.value_counts().sort_index()
    share = hours / hours.max()
    days = frame.index.dayofweek.value_counts().sort_index()
    recent = frame[frame.index >= frame.index.max() - pd.Timedelta(days=730)]
    rs = (recent["ask_close"] - recent["bid_close"]).to_numpy(float)
    rs = rs[np.isfinite(rs) & (rs >= 0)]
    return {"first_utc": frame.index.min().isoformat(), "last_utc": frame.index.max().isoformat(),
            "bars_h1": int(len(frame)), "tick": tick,
            "spread_median": float(np.median(spread)) if spread.size else None,
            "spread_p90": float(np.percentile(spread, 90)) if spread.size else None,
            "spread_median_last2y": float(np.median(rs)) if rs.size else None,
            "active_utc_hours": [int(h) for h in share.index[share >= 0.2]],
            "weekday_share": {int(k): round(float(v / days.sum()), 4) for k, v in days.items()},
            "price_median": float(np.median(bid_c[np.isfinite(bid_c)])),
            "manifest_sha": (man.get("bid_source") or {}).get("sha256")}


def _stats(dm, symbol: str, refresh: bool = False) -> dict:
    with _lock:
        cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.is_file() else {}
    hit = cache.get(symbol)
    if hit and not refresh:
        return hit
    if dm is None:
        return {}
    try:
        stats = _measure(dm, symbol)
    except Exception as exc:  # missing data file / DataError: reported, never invented
        return {"error": f"{type(exc).__name__}: {exc}"}
    if stats:
        with _lock:
            cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.is_file() else {}
            cache[symbol] = stats
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    return stats


# USD per 1 unit of currency: (Dukascopy pair, True = pair mid, False = 1/mid); USD itself needs no pair.
_USD_PER = {"EUR": ("EURUSD", True), "GBP": ("GBPUSD", True), "AUD": ("AUDUSD", True),
            "JPY": ("USDJPY", False), "CAD": ("USDCAD", False)}


def _legs(quote: str | None, account: str) -> list | None:
    """Conversion legs (ccy, pair, mid?, inverse) from ``quote`` to ``account``, or None if a leg is unknown."""
    if quote is None:
        return None
    legs = []
    for ccy, inverse in ((quote, False), (account, True)):
        if ccy == "USD":
            continue
        if ccy not in _USD_PER:
            return None
        legs.append((ccy, *_USD_PER[ccy], inverse))
    return legs


def conversion_info(symbol: str, account: str = ACCOUNT_CURRENCY) -> dict:
    """How the P&L of ``symbol`` reaches the account currency: none needed, or the Dukascopy pair(s) + operation."""
    symbol = canonical(symbol)
    quote = _STATIC[symbol][1] if symbol in _STATIC else None
    if quote == account:
        return {"needed": False, "quote": quote, "account": account}
    legs = _legs(quote, account)
    if not legs:
        return {"needed": True, "quote": quote, "account": account, "pair": None, "operation": None}
    if len(legs) == 1 and account == ACCOUNT_CURRENCY:  # historical single-pair shape
        return {"needed": True, "quote": quote, "account": account, "pair": legs[0][1],
                "operation": "mid" if legs[0][2] else "1/mid"}
    return {"needed": True, "quote": quote, "account": account, "pair": "+".join(l[1] for l in legs),
            "legs": [{"pair": l[1], "operation": "mid" if l[2] else "1/mid", "divide": l[3]} for l in legs],
            "operation": "cross"}


def _leg_series(dm, pair: str, mid_op: bool, timeframe: str, start, end) -> pd.Series | None:
    try:
        frame, _ = dm.candles(pair, timeframe, start, end)
    except Exception:
        return None
    if frame.empty:
        return None
    mid = (frame["bid_open"] + frame["ask_open"]) / 2.0
    return mid if mid_op else 1.0 / mid


def conversion_series(dm, symbol: str, timeframe: str, start: str | None, end: str | None,
                      account: str = ACCOUNT_CURRENCY) -> pd.Series | None:
    """``account`` units per 1 quote-currency unit at each conversion-bar OPEN (causal), or None if unavailable.
    Empty Series when no conversion is needed. Cross rates combine two USD legs on an as-of (ffill) time grid."""
    symbol = canonical(symbol)
    info = conversion_info(symbol, account)
    if not info["needed"]:
        return pd.Series(dtype=float)
    legs = _legs(info["quote"], account)
    if not legs:
        return None
    out = None
    for ccy, pair, mid_op, inverse in legs:
        ser = _leg_series(dm, pair, mid_op, timeframe, start, end)
        if ser is None:
            return None
        if out is None:
            out = (1.0 / ser) if inverse else ser
        else:
            grid = out.index.union(ser.index)
            lhs, rhs = out.reindex(grid).ffill(), ser.reindex(grid).ffill()
            out = (lhs / rhs if inverse else lhs * rhs).dropna()
    return out


def rate_at(series: pd.Series, ts) -> float:
    """Account-per-quote rate known at ``ts``: the last value at or before it. Never a later or 'current' rate;
    raises when the series has nothing yet at that time."""
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    idx = series.index.tz_localize("UTC") if series.index.tz is None else series.index.tz_convert("UTC")
    pos = int(idx.searchsorted(t, side="right")) - 1
    if pos < 0 or not math.isfinite(float(series.iloc[pos])) or float(series.iloc[pos]) <= 0:
        raise ValueError(f"no conversion rate known at or before {t.isoformat()}")
    return float(series.iloc[pos])


def convert_trades(trades: list[dict], series: pd.Series | None, key: str = "exit_time") -> list[dict]:
    """Copy of ``trades`` with pnl/costs converted to the account currency at the rate known at the trade's
    ``key`` time (exit = when the P&L is realised). ``series`` None/empty = no conversion needed."""
    if series is None or len(series) == 0:
        return [dict(t) for t in trades]
    out = []
    for t in trades:
        r = rate_at(series, t[key])
        out.append({**t, "pnl": float(t["pnl"]) * r, "costs": float(t.get("costs") or 0.0) * r, "fx_rate": r})
    return out


def size_position(equity: float, risk_percent: float, stop_distance: float, contract_size: float, rate: float,
                  lot_step: float, min_lot: float | None = None) -> dict:
    """Lots risking ``risk_percent`` of ``equity`` (account currency) at a price stop ``stop_distance``, floored to
    ``lot_step``. ``rate`` = account currency per 1 quote-currency unit at the entry time."""
    if min(equity, risk_percent, stop_distance, contract_size, rate, lot_step) <= 0:
        raise ValueError("equity, risk, stop distance, contract size, rate and lot step must be positive")
    raw = equity * risk_percent / 100.0 / (stop_distance * contract_size * rate)
    lots = round(math.floor(raw / lot_step + 1e-9) * lot_step, 8)
    if lots < (min_lot or lot_step):
        return {"lots": 0.0, "risk_amount": 0.0, "rejected": "BELOW_MIN_LOT", "raw_lots": raw}
    risk = lots * stop_distance * contract_size * rate
    return {"lots": lots, "risk_amount": risk, "raw_lots": raw, "risk_pct_of_equity": risk / equity * 100.0}


def exposure(lots: float, price: float, contract_size: float, rate: float) -> float:
    """Notional exposure in the account currency."""
    return lots * price * contract_size * rate


def portfolio_open_risk(positions: list[dict], equity: float) -> dict:
    """Worst-case stop risk (no netting) across open positions, each in account currency at ITS entry-time rate.
    position = {lots, stop_distance, contract_size, rate, price}."""
    risks = [p["lots"] * p["stop_distance"] * p["contract_size"] * p["rate"] for p in positions]
    gross = [exposure(p["lots"], p["price"], p["contract_size"], p["rate"]) for p in positions]
    return {"open_risk": float(sum(risks)), "open_risk_pct": float(sum(risks) / equity * 100.0),
            "gross_exposure": float(sum(gross)), "per_position_risk": risks}


def _derived_status(*fields) -> str:
    return BROKER if all(f["status"] == BROKER for f in fields) else (
        VERIFIED if all(f["status"] in _OK_STATUS for f in fields) else ASSUMED)


def money_gate(fields: dict) -> dict:
    """Money-based approval needs every MONEY_REQUIRED field VERIFIED_FROM_DATA or BROKER_VERIFIED."""
    blocking = [n for n in MONEY_REQUIRED if (fields.get(n) or {}).get("status") not in _OK_STATUS]
    return {"money_approval_allowed": not blocking, "blocking_fields": blocking,
            "status": "BROKER_VERIFIED" if not blocking else "PROVISIONAL",
            "rule": "geldbedragen/winst in valuta zijn voorlopig tot elk blokkerend veld verifieerbaar is"}


def money_approval(symbols, dm=None, account: str = ACCOUNT_CURRENCY) -> dict:
    """Aggregate gate over markets: one blocked market blocks the money-based approval of the whole result."""
    per = {}
    for sym in symbols:
        g = spec_for(sym, dm, account=account).get("money_gate") or {
            "money_approval_allowed": False, "blocking_fields": ["UNKNOWN_INSTRUMENT"], "status": "PROVISIONAL"}
        per[canonical(sym)] = g
    blocked = {k: v["blocking_fields"] for k, v in per.items() if not v["money_approval_allowed"]}
    return {"money_approval_allowed": not blocked, "status": "PROVISIONAL" if blocked else "BROKER_VERIFIED",
            "blocking": blocked, "per_market": per,
            "note_nl": ("Financiele conclusies (bedragen, rendement in geld, positiegrootte in lots, margin) zijn "
                        "PROVISIONAL en blokkeren geldgebaseerde goedkeuring tot de blokkerende velden "
                        "BROKER_VERIFIED zijn (zie BROKER_ASSUMPTIONS_REGISTER.md). R-multiples en % blijven bruikbaar.")}


def spec_for(symbol: str, dm=None, refresh: bool = False, account: str = ACCOUNT_CURRENCY) -> dict:
    symbol = canonical(symbol)
    if symbol not in _STATIC:
        return {"symbol": symbol, "status": "UNKNOWN_INSTRUMENT", "provisional": True}
    cls, quote, size, step, margin, hours = _STATIC[symbol]
    st = _stats(dm, symbol, refresh)
    data_ok = bool(st) and "error" not in st
    tick = st.get("tick") if data_ok else None
    fallback_tick = backtest._DEFAULT_CONTRACTS.get(symbol, (1, 0.01, 0.01))[1]
    conv = conversion_info(symbol, account)
    conv_ok = (not conv["needed"]) or bool(conv.get("pair"))
    spread_ticks = (st["spread_median"] / tick) if data_ok and tick and st.get("spread_median") is not None else None
    fields = {
        "asset_class": _f(cls, ASSUMED, "classificatie op naam"),
        "quote_currency": _f(quote, ASSUMED, "Dukascopy-conventie; niet via broker bevestigd"),
        "tick_size": (_f(tick, VERIFIED, "kleinste prijsstap uit de geciteerde decimalen; broker-tick kan groter zijn")
                      if tick else _f(fallback_tick, ASSUMED, "geen data beschikbaar; backtest-default")),
        "contract_size": _f(size, ASSUMED, "typische CFD-/FX-conventie; broker-specifiek", "convention"),
        "lot_step": _f(step, ASSUMED, "typisch 0.01; broker-specifiek", "convention"),
        "min_lot": _f(step, ASSUMED, "aangenomen gelijk aan lotstap", "convention"),
        "max_lot": _f(None, UNKNOWN, "broker-specifiek; niet gemodelleerd"),
        "commission_per_lot_side": _f(None, UNKNOWN, "afhankelijk van broker/account; alleen stress-aanname"),
        "slippage": _f({"points": 0.0}, ASSUMED, "standaard 0 punten; alleen als stress/parameter (geen gemeten fill-slippage)",
                       "lab_default"),
        "swap": _f(None, UNKNOWN, "overnight financiering/swap wordt door geen enkele engine gemodelleerd"),
        "margin": _f(None, UNKNOWN, margin),
        "trading_hours": (_f({"observed_active_utc_hours": st["active_utc_hours"], "weekday_share": st["weekday_share"],
                              "calendar_note": hours}, VERIFIED,
                             "uren/weekdagen waar H1-bars voorkomen; feestdagen/DST niet gemodelleerd")
                          if data_ok else _f({"calendar_note": hours}, ASSUMED, "geen data")),
        "typical_spread": (_f({"median": st["spread_median"], "p90": st["spread_p90"],
                               "median_last_2y": st["spread_median_last2y"], "median_in_ticks": spread_ticks},
                              VERIFIED, "ask_close - bid_close over alle H1-bars") if data_ok
                           else _f(None, UNKNOWN, "geen data")),
        "data_range": (_f({"first": st["first_utc"], "last": st["last_utc"], "bars_h1": st["bars_h1"]}, VERIFIED, "")
                       if data_ok else _f(None, UNKNOWN, st.get("error", "geen data in cache; start een meting"))),
        "account_conversion": _f(conv, VERIFIED if conv_ok else UNKNOWN,
                                 "echte Dukascopy-conversiereeks, causaal per bar-open" if conv["needed"] and conv_ok
                                 else ("geen conversie nodig (quote = rekeningvaluta)" if not conv["needed"]
                                       else "conversiereeks ontbreekt -> PROVISIONAL")),
    }
    for name, item in (_overrides().get(symbol) or {}).items():
        if (name in fields and isinstance(item, dict) and item.get("status") == BROKER and item.get("source")
                and item.get("value") is not None):
            fields[name] = _f(item["value"], BROKER, item.get("note", "door gebruiker ingevoerd brokerfeit"),
                              str(item["source"]))
    pip = 0.01 if quote == "JPY" else 0.0001
    cs, tk = fields["contract_size"], fields["tick_size"]
    fields["tick_value"] = _f({"quote_ccy_per_tick_per_lot": tk["value"] * cs["value"]}, _derived_status(tk, cs),
                              "tick_size x contract_size in quote-valuta; rekeningwaarde = x conversiekoers op dat moment",
                              "derived")
    fields["pip_value"] = (_f({"pip_size": pip, "quote_ccy_per_pip_per_lot": pip * cs["value"]}, _derived_status(cs),
                              "pip = 0.0001 (0.01 voor JPY); x contract_size", "derived") if cls == "fx"
                           else _f(None, UNKNOWN, "pip is niet gedefinieerd voor dit instrument; gebruik tick/punt",
                                   "n/a"))
    fields["currency"] = _f({"quote": fields["quote_currency"]["value"], "account": account},
                            fields["quote_currency"]["status"], "quote-valuta + rekeningvaluta",
                            fields["quote_currency"]["source"])
    gate = money_gate(fields)
    return {"symbol": symbol, "account_currency": account, "fields": fields,
            "data_available": data_ok, "conversion_available": conv_ok,
            "status": ("OK" if data_ok and conv_ok else "INCOMPLETE"),
            "money_status": gate["status"], "money_gate": gate,
            "money_status_note_nl": ("Contractspecificaties zijn aannames (niet broker-bevestigd): "
                                     "geldbedragen zijn onderzoeksschattingen; R-multiples en % zijn robuuster."),
            "money_verified": gate["money_approval_allowed"]}


def registry(dm=None, refresh: bool = False, account: str = ACCOUNT_CURRENCY) -> list[dict]:
    return [spec_for(s, dm, refresh, account) for s in SYMBOLS]


def contract_for(symbol: str, dm=None, account: str = ACCOUNT_CURRENCY):
    """ContractSpec in the account currency with the registry's size/tick/step (quote_to_account placeholder 1.0;
    the real rate comes from conversion_series)."""
    from runtime.strategy_lab.execution import ContractSpec
    s = spec_for(symbol, dm, account=account)
    f = s["fields"]
    return ContractSpec(account, float(f["contract_size"]["value"]), float(f["tick_size"]["value"]), 1.0,
                        float(f["lot_step"]["value"]))


# ------------------------------------------------------------------------------------------------ risk models
RISK_MODELS = ("pct_equity_atr", "pct_stop", "fixed_lot")


def scale_price_distance(distance: float, from_symbol: str, to_symbol: str, dm=None) -> float:
    """Convert a fixed price-distance (e.g. 'stop 500 points on XAUUSD') to another instrument by relative price
    level: distance * median_price(to) / median_price(from). Raw fixed distances are NOT comparable across
    instruments (gold 5.0 vs EURUSD 0.0005); relative or ATR-based stops are preferred and used by default."""
    a, b = _stats(dm, canonical(from_symbol)), _stats(dm, canonical(to_symbol))
    if not a.get("price_median") or not b.get("price_median"):
        raise ValueError("price level unavailable for scaling")
    return float(distance) * b["price_median"] / a["price_median"]


def normalise_spec(spec: dict, model: str = "pct_equity_atr", *, symbol: str, timeframe: str, dm=None,
                   from_symbol: str | None = None, risk_percent: float | None = None, fixed_lot: float | None = None,
                   percent_stop: float = 1.0, atr_period: int = 14, atr_mult: float = 2.0) -> tuple[dict, list[str]]:
    """Per-market spec copy with a comparable risk model. Returns (spec, notes).

    * ``native`` behaviour: the spec's own stop is kept; only symbol/timeframe change. A ``points`` stop is
      converted by relative price level (see scale_price_distance); ``atr``/``swing``/``percent`` stops are
      scale-free and kept.
    * ``pct_equity_atr`` (default): ATR stop (period/mult) + risk.percent of equity.
    * ``pct_stop``: percentage-of-price stop (``percent_stop`` %) + risk.percent of equity.
    * ``fixed_lot``: contract-aware lot is NOT supported by the sizing of the rule backtester (it sizes by risk);
      reported as NOT_SUPPORTED instead of silently substituting."""
    out = json.loads(json.dumps(spec))
    out["symbol"], out["timeframe"] = canonical(symbol), timeframe
    notes: list[str] = []
    if risk_percent is not None:
        out.setdefault("risk", {})["percent"] = float(risk_percent)
    if model == "native":
        st = out.get("stop") or {}
        if st.get("type") == "points" and from_symbol and canonical(from_symbol) != canonical(symbol):
            old = float(st["value"])
            st["value"] = scale_price_distance(old, from_symbol, symbol, dm)
            notes.append(f"vaste stopafstand {old} ({from_symbol}) herschaald naar {st['value']:.6g} ({symbol}) "
                         "via relatief prijsniveau")
        return out, notes
    if model == "pct_equity_atr":
        out["stop"] = {"type": "atr", "period": int(atr_period), "mult": float(atr_mult)}
        notes.append(f"stop = {atr_mult} x ATR({atr_period}); risico {out['risk']['percent']}% van equity per trade")
        return out, notes
    if model == "pct_stop":
        out["stop"] = {"type": "percent", "value": float(percent_stop)}
        notes.append(f"stop = {percent_stop}% van de prijs; risico {out['risk']['percent']}% van equity per trade")
        return out, notes
    if model == "fixed_lot":
        raise NotImplementedError("fixed_lot: NOT_SUPPORTED door de risico-gebaseerde sizing van de regel-backtester")
    raise ValueError(f"onbekend risicomodel {model!r}; kies uit {RISK_MODELS + ('native',)}")
