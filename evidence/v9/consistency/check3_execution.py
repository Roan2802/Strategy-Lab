"""Check 3: Bid/Ask execution semantics measured on real Dukascopy quotes + contract-spec verification status."""
import json
import math
import tempfile

from check2_parity import CASES, rich
from parity_lib import *
from runtime.strategy_lab.execution import ContractSpec, position_pnl
from strategy_lab.data_manager import DISPLAY_ALIASES, SYMBOLS
from strategy_lab.research.backtest import _DEFAULT_CONTRACTS

SLIP = CONTRACT["slippage_points"] * CONTRACT["tick_size"]
COMM = CONTRACT["commission_per_lot_side"]
STEP = CONTRACT["volume_step"]


def close(a, b, tol=1e-7):
    return abs(a - b) <= tol * max(1.0, abs(b))


def verify_case(name, days=None):
    spec, params, first, last, *_ = CASES[name]
    if days:
        last = first + timedelta(days=days - 1)
    work = Path(tempfile.mkdtemp(prefix=f"exec_{name}_"))
    store, eng, portfolio, registration, stats = build_forward(work / "s.sqlite", spec, params, first, last)
    advance_daily(eng, portfolio, first, last)
    report = eng.report(portfolio)
    m1, _ = data_manager().candles("XAUUSD", "M1", first.isoformat(), (last + timedelta(days=1)).isoformat())
    events = report["events"]
    kind = spec["order"]["type"]
    counts = {"trades": 0, "entry_side_ok": 0, "entry_price_rule_ok": 0, "exit_side_rule_ok": 0, "exit_price_rule_ok": 0,
              "spread_open_ok": 0, "pnl_formula_ok": 0, "lot_step_ok": 0, "sizing_ok": 0, "sl_wins_when_both": 0,
              "ambiguous_exits": 0}
    bad = []
    realized = 0.0
    fills = {}
    ambiguous_cost = 0.0
    for e in events:
        d = e["detail"]
        stamp = pd.Timestamp(e["candle_at"])
        if e["kind"] == "ORDER_FILLED":
            fills[(d["signal_at"])] = (stamp, d)
        if e["kind"] != "POSITION_CLOSED":
            continue
        counts["trades"] += 1
        fill_stamp, fill = fills[d["signal_at"]]
        q_in, q_out = m1.loc[fill_stamp], m1.loc[stamp]
        long_ = d["side"] == "long"
        counts["entry_side_ok"] += int(fill["quote"] == ("ASK" if long_ else "BID") and d["quote"] == ("BID" if long_ else "ASK"))
        counts["spread_open_ok"] += int(close(fill["spread_open"], q_in.ask_open - q_in.bid_open) and close(d["spread_open"], q_out.ask_open - q_out.bid_open))
        if kind == "market":
            ok = close(d["entry"], q_in.ask_open + SLIP) if long_ else close(d["entry"], q_in.bid_open - SLIP)
        elif long_:
            ok = q_in.ask_low - 1e-9 <= d["entry"] <= q_in.ask_high + SLIP + 1e-9
        else:
            ok = q_in.bid_low - SLIP - 1e-9 <= d["entry"] <= q_in.bid_high + 1e-9
        if kind == "limit":
            ok = ok and (d["entry"] <= q_in.ask_open + 1e-9 if long_ else d["entry"] >= q_in.bid_open - 1e-9)
        counts["entry_price_rule_ok"] += int(ok)
        reason = d["exit_reason"]
        if reason == "TAKE_PROFIT":
            expect = max(q_out.bid_open, d["target"]) if long_ else min(q_out.ask_open, d["target"])
        elif reason == "STOP_LOSS":
            expect = (min(q_out.bid_open, d["exit_stop"]) - SLIP) if long_ else (max(q_out.ask_open, d["exit_stop"]) + SLIP)
        else:
            expect = (q_out.bid_open - SLIP) if long_ else (q_out.ask_open + SLIP)
        counts["exit_price_rule_ok"] += int(close(d["exit"], expect))
        # exit executes on the opposite side: long stop/target are tested on Bid, short on Ask
        counts["exit_side_rule_ok"] += int(
            (long_ and (reason != "STOP_LOSS" or q_out.bid_low <= d["exit_stop"]) and (reason != "TAKE_PROFIT" or q_out.bid_high >= d["target"]))
            or (not long_ and (reason != "STOP_LOSS" or q_out.ask_high >= d["exit_stop"]) and (reason != "TAKE_PROFIT" or q_out.ask_low <= d["target"])) or reason == "TIME_EXIT")
        sign = 1 if long_ else -1
        gross = (d["exit"] - d["entry"]) * sign * d["quantity"] * CONTRACT["contract_size"]
        counts["pnl_formula_ok"] += int(close(d["net_pnl"], gross - 2 * d["quantity"] * COMM) and close(d["commission"], 2 * d["quantity"] * COMM))
        counts["lot_step_ok"] += int(close(d["quantity"] / STEP, round(d["quantity"] / STEP)))
        pct = min(registration["configuration"]["spec"]["risk"]["percent"], registration["configuration"]["trade_risk"] * 100)
        sized = math.floor((CAPITAL + realized) * pct / 100 / (abs(d["entry"] - d["stop"]) * CONTRACT["contract_size"]) / STEP + 1e-9) * STEP
        counts["sizing_ok"] += int(close(d["quantity"], round(sized, 8)))
        realized += d["net_pnl"]
        if d["ambiguous"]:
            counts["ambiguous_exits"] += 1
            counts["sl_wins_when_both"] += int(reason == "STOP_LOSS")
            alt = position_pnl(side=d["side"], quantity_lots=d["quantity"], entry=d["entry"], exit=d["target"],
                               contract=ContractSpec(**CONTRACT))["net_pnl"]
            ambiguous_cost += alt - d["net_pnl"]
        for key in ("entry_side_ok", "entry_price_rule_ok", "exit_price_rule_ok", "exit_side_rule_ok", "pnl_formula_ok", "sizing_ok"):
            pass
    # pending orders: no touch inside the window of a cancelled order; touch at the fill candle otherwise
    pend = {"submitted": 0, "cancelled": 0, "cancel_exact_expiry_candle": 0, "cancelled_without_touch": 0, "filled": 0,
            "filled_on_first_touch": 0}
    if kind in ("stop", "limit"):
        expiry = spec["order"]["expiry_bars"]
        index = m1.index
        submitted = {e["detail"]["signal_at"]: e["detail"] for e in events if e["kind"] == "ORDER_SUBMITTED"}
        pend["submitted"] = len(submitted)

        def touched(order, row):
            P, side = order["planned_price"], order["side"]
            if kind == "stop":
                return row.ask_high >= P if side == "long" else row.bid_low <= P
            return row.ask_low <= P if side == "long" else row.bid_high >= P
        for e in events:
            d = e["detail"]
            if e["kind"] == "ORDER_CANCELLED":
                pend["cancelled"] += 1
                start = index.searchsorted(pd.Timestamp(d["signal_at"]))
                window = index[start:start + expiry]
                pend["cancel_exact_expiry_candle"] += int(index[start + expiry] == pd.Timestamp(e["candle_at"]))
                pend["cancelled_without_touch"] += int(not any(touched(d, m1.loc[t]) for t in window))
            elif e["kind"] == "ORDER_FILLED":
                pend["filled"] += 1
                start = index.searchsorted(pd.Timestamp(d["signal_at"]))
                order = submitted[d["signal_at"]]
                first_touch = next((t for t in index[start:start + expiry] if touched(order, m1.loc[t])), None)
                pend["filled_on_first_touch"] += int(first_touch == pd.Timestamp(e["candle_at"]))
    rejected = [e["detail"].get("reason") for e in events if e["kind"] == "ORDER_REJECTED"]
    return {"case": name, "days": f"{first}..{last}", "order": kind, **counts, "pending": pend,
            "sl_first_counterfactual_cost_if_tp_were_taken": ambiguous_cost, "order_rejections": rejected[:5]}


def spread_and_grid():
    out = {}
    for display in ["XAUUSD", "EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "USDCAD", "BTCUSD", "DE40", "US30", "US500", "USATECHIDXUSD"]:
        path = CACHE / f"{display}_M1_bidask.parquet"
        if not path.exists():
            out[display] = {"status": "NO_CACHE_FILE"}
            continue
        cols = ["bid_open", "ask_open", "bid_close", "ask_close", "bid_high", "ask_high", "bid_low", "ask_low"]
        f = pd.read_parquet(path, columns=cols)
        both = f.dropna(subset=["bid_open", "ask_open"])
        spread = both.ask_open - both.bid_open
        day = both.index.normalize()
        neg = (both.ask_open < both.bid_open) | (both.ask_close.fillna(1e18) < both.bid_close.fillna(-1e18))
        px = both.bid_close.dropna().iloc[-2_000_000:].to_numpy()
        grid = None
        for decimals in range(0, 8):
            if np.allclose(px * 10 ** decimals, np.round(px * 10 ** decimals), rtol=0, atol=1e-6 * 10 ** 0):
                grid = 10.0 ** -decimals
                break
        out[display] = {"rows": int(len(f)), "rows_with_bid_and_ask": int(len(both)),
                        "spread_open_mean": float(spread.mean()), "spread_open_median": float(spread.median()),
                        "spread_open_p99": float(spread.quantile(0.99)), "spread_open_max": float(spread.max()),
                        "rows_negative_open_or_close_spread": int(neg.sum()),
                        "days_refused_by_daily_store_for_negative_spread": int(day[neg.to_numpy()].nunique()),
                        "days_refused_since_2025": int(day[neg.to_numpy()][day[neg.to_numpy()] >= pd.Timestamp("2025-01-01", tz="UTC")].nunique()),
                        "last_refused_day": str(day[neg.to_numpy()].max().date()) if neg.any() else None,
                        "rows_zero_open_spread": int((spread == 0).sum()),
                        "observed_price_grid_last_2m_rows": grid,
                        "assumed_default_contract": _DEFAULT_CONTRACTS.get(display if display != "USATECHIDXUSD" else "USTEC")}
        print(display, out[display], flush=True)
    return out


QUOTE_CCY = {"XAUUSD": "USD", "EURUSD": "USD", "GBPUSD": "USD", "AUDUSD": "USD", "USDJPY": "JPY", "USDCAD": "CAD",
             "BTCUSD": "USD", "DE40": "EUR", "US30": "USD", "US500": "USD", "USATECHIDXUSD": "USD"}


def contract_table(stats):
    rows = {}
    for symbol, info in stats.items():
        default = info.get("assumed_default_contract")
        grid = info.get("observed_price_grid_last_2m_rows")
        row = {"quote_currency": QUOTE_CCY[symbol],
               "default_contract_in_backtest_py": default,
               "contract_size": "ASSUMED (no broker/symbol-spec source in repo or data)" if default else "NO_DEFAULT (backtest falls back to size 1)",
               "volume_step": "ASSUMED",
               "tick_size": (("MATCHES_DATA_PRICE_GRID " + str(grid) + " (necessary, not sufficient: no broker spec)")
                             if default and grid is not None and abs(default[1] - grid) < 1e-12 else
                             ("NOT_VERIFIED: data price grid " + str(grid) + " differs from default tick " + str(default[1])
                              if default else "NO_DEFAULT")),
               "quote_to_account": ("ASSUMED 1.0 (valid only for USD-quoted symbols on a USD account)" if QUOTE_CCY[symbol] == "USD"
                                    else f"WRONG_IF_USED: quote currency {QUOTE_CCY[symbol]} vs USD account, default 1.0"),
               "commission_and_slippage": "USER INPUT (never from broker)"}
        rows[symbol] = row
    return rows


if __name__ == "__main__":
    payload = {"slippage_points": CONTRACT["slippage_points"], "commission_per_lot_side": COMM,
               "contract_assumed_not_broker_verified": CONTRACT, "fill_checks": {}}
    for name in ("rich_m1_market", "rich_m1_stop", "rich_m1_limit"):
        payload["fill_checks"][name] = verify_case(name)
        print(payload["fill_checks"][name], flush=True)
    payload["real_spread_and_price_grid"] = spread_and_grid()
    payload["contract_verification"] = contract_table(payload["real_spread_and_price_grid"])
    payload["rules"] = {
        "sl_tp_same_candle": "stop loss wins (conservative_ohlc_exit); flagged ambiguous=True on the trade",
        "tp_on_stop_or_limit_fill_candle": "ignored (intrabar path unknown), SL still active",
        "gap": "stop exit fills at min(open, stop) long / max(open, stop) short; target at max(open,target) long / min(open,target) short",
        "slippage": "adverse on market entry, stop-order fills, STOP_LOSS and TIME_EXIT; none on TAKE_PROFIT and limit fills",
        "commission": "2 x lots x commission_per_lot_side per round trip",
    }
    print(save("check3_execution.json", payload))
