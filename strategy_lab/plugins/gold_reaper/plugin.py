"""Strategy Lab adapter for the user's GoldReaper v2 engine (gold_reaper.py, unmodified copy).

Dukascopy delivers UTC Bid and Ask M1 candles; GoldReaper expects broker server time
(UTC+2 winter / UTC+3 summer, switched on the US DST calendar exactly like the script's own
``usd_dst``) and a Bid OHLC bar plus a spread. The per-minute spread is the real Dukascopy
Ask-Bid close difference. Trades come back in UTC in the Strategy Lab trade contract.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("strategy_lab_gold_reaper_engine", HERE / "gold_reaper.py")
gr = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = gr
_spec.loader.exec_module(gr)

PLUGIN = {
    "name": "GoldReaper v2 (Pass3436 Robuust)",
    "version": "v10 parity file",
    "description": "Eigen event-driven engine met 9 deelstrategieën op XAUUSD; draait ongewijzigd op Dukascopy Bid/Ask M1.",
    "symbols": ["XAUUSD"],
    "base_timeframe": "M1",
    "warmup_days": 14,
    # Configurations tried OUTSIDE the lab before import (e.g. an MT5 optimisation). Unknown until the author fills
    # it in; the name suggests pass 3436 of an external optimisation, so the selection bias may be large.
    "research_history": {"external_trials": None, "external_periods": None,
                         "note_nl": "Extern ontwikkeld (naam: 'Pass3436'); aantal extern geprobeerde varianten en de "
                                    "gebruikte optimalisatieperiode zijn niet opgegeven."},
    "params": [
        {"name": "max_dd", "type": "float", "min": 2, "max": 50, "default": 10.0, "label": "Max drawdown-profiel (%)"},
        {"name": "entry_offset_factor", "type": "float", "min": 0.3, "max": 1.2, "default": 0.70},
        {"name": "stop_loss_factor", "type": "float", "min": 0.4, "max": 1.5, "default": 0.75},
        {"name": "take_profit_factor", "type": "float", "min": 0.8, "max": 3.0, "default": 1.50},
        {"name": "trail_distance_factor", "type": "float", "min": 0.8, "max": 3.0, "default": 1.50},
        {"name": "be_trigger_factor", "type": "float", "min": 0.3, "max": 1.5, "default": 0.75},
        {"name": "commission_per_lot_side", "type": "float", "min": 0, "max": 10, "default": 0.0},
    ],
    "notes": [
        "M1-OHLC heeft geen intraminuut-volgorde; GoldReaper gebruikt zijn eigen synthetische 4-punts tickvolgorde.",
        "Nieuwsfilters (NFP/FOMC/CPI) gebruiken de vaste kalenderregels uit het script zelf, geen point-in-time kalender.",
        "Servertijd = UTC+2/UTC+3 volgens de Amerikaanse zomertijd, zoals broker_gmt_winter/summer in het script.",
    ],
}


def _offset(ts: pd.Timestamp, p) -> timedelta:
    return timedelta(hours=p.broker_gmt_summer if gr.usd_dst(ts.date()) else p.broker_gmt_winter)


def backtest(bid, ask, params, *, capital, start, end, stress=None, progress=None) -> dict:
    stress = stress or {}
    p = gr.Settings()
    for key, value in params.items():
        if hasattr(p, key) and value is not None:
            setattr(p, key, type(getattr(p, key))(value))
    p.input_mode = "m1"
    p.initial_balance = float(capital)
    if stress.get("commission_per_lot"):
        p.commission_per_lot_side = float(stress["commission_per_lot"])
    engine = gr.GoldReaper(p)

    joined = bid[["open", "high", "low", "close"]].join(ask[["close"]].rename(columns={"close": "ask_close"}), how="inner")
    spread = (joined["ask_close"] - joined["close"]).clip(lower=p.point) * float(stress.get("spread_multiplier", 1.0))
    s_utc, e_utc = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    joined = joined[joined.index < e_utc]
    total = max(1, len(joined))

    def source():
        for i, (ts, o, h, l, c, _a, spr) in enumerate(zip(joined.index, joined["open"], joined["high"],
                                                          joined["low"], joined["close"], joined["ask_close"],
                                                          spread.reindex(joined.index))):
            if progress and i % 20000 == 0:
                progress(i / total, f"GoldReaper {ts:%Y-%m-%d}")
            server = (ts + _offset(ts, p)).tz_localize(None).to_pydatetime()
            tv = 1 if o == h == l == c else 4
            yield server, float(o), float(h), float(l), float(c), float(spr), tv

    start_server = (s_utc + _offset(s_utc, p)).date()
    engine.run_m1_fast(source(), start_date=start_server)

    trades = []
    for t in engine.trades:
        opened = pd.Timestamp(t["open_time"])
        closed = pd.Timestamp(t["close_time"])
        entry_utc = (opened - _offset(opened, p)).tz_localize("UTC")
        exit_utc = (closed - _offset(closed, p)).tz_localize("UTC")
        if entry_utc < s_utc:
            continue
        risk = abs(float(t["entry"]) - float(t["sl"])) * float(t["lots"]) * p.contract_size if t.get("sl") else 0.0
        trades.append({"entry_time": entry_utc.isoformat(), "exit_time": exit_utc.isoformat(),
                       "side": "long" if t["side"] == "BUY" else "short",
                       "entry_price": float(t["entry"]), "exit_price": float(t["exit"]), "qty": float(t["lots"]),
                       "pnl": float(t["net"]), "costs": float(t["commission"]),
                       "r_multiple": float(t["net"]) / risk if risk > 0 else None,
                       "exit_reason": str(t["reason"]), "sub_strategy": int(t["strategy"])})
    equity, balance = [], float(capital)
    for t in trades:
        balance += t["pnl"]
        equity.append({"time": t["exit_time"], "equity": balance})
    return {"trades": trades, "equity": equity, "summary": engine.summary(), "notes": PLUGIN["notes"]}
