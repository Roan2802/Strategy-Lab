import json
import pandas as pd
import pytest

from strategy_lab.events import StrategyDebugCandle, candle_event, lifecycle_event
from strategy_lab.execution import ContractSpec, conservative_ohlc_exit, position_pnl, simulate_bidask
from strategy_lab.replay import event_links, recorded_state_snapshot, replay_slice, reexecute_labbreakout


def contract(**changes):
    values = {"account_currency": "USD", "contract_size": 10, "tick_size": 0.01,
              "quote_to_account": 1, "volume_step": 0.1,
              "commission_per_lot_side": 0.5, "slippage_points": 0}
    values.update(changes)
    return ContractSpec(**values)


def quote_candle(timestamp_ms, **changes):
    row = {"timestamp_ms": timestamp_ms, "bid_open": 100.0, "bid_high": 101.0,
           "bid_low": 99.0, "bid_close": 100.0, "ask_open": 100.2, "ask_high": 101.2,
           "ask_low": 99.2, "ask_close": 100.2}
    row.update(changes)
    return row


def test_event_schema_and_nonfinite_cleanup():
    event = candle_event(run_id="r1", symbol="XAUUSD", timeframe="M1", timestamp_ms=1000,
                         strategy="Probe", parameters={}, checks=[{"value": float("nan"), "result": "SKIP"}],
                         decision="SKIP", reason="WARMUP")
    assert event["schema_version"] == "strategy-lab.events.v1"
    assert event["checks"][0]["value"] is None
    assert event["event_id"] == "r1:XAUUSD:M1:1000:decision"
    assert lifecycle_event(run_id="r1", event_type="POSITION_CLOSE", timestamp_ms=1000,
                           symbol="XAUUSD", timeframe="M1", trade_id="t1")["trade_id"] == "t1"


def test_generic_debug_contract_records_values_groups_short_circuit_and_metadata():
    class StrategyProbe:
        symbol = "XAUUSD"
        timeframe = "M1"
        current_candle = [60_000, 10, 11, 12, 9, 1]
        hp = {"period": 5}
        def __init__(self): self.logged = []
        def log(self, line): self.logged.append(line)

    probe = StrategyProbe()
    uncalled = []
    with StrategyDebugCandle(probe, {"session": "London"}) as debug:
        debug.indicator("RSI 14", 42.5, timeframe="M5", unit="index")
        ok = debug.evaluate(debug.group("entry", "all", [
            debug.condition("session open", True, "==", True),
            debug.condition("RSI oversold", lambda: uncalled.append(True) or 42.5, "<", 30,
                            timeframe="M5"),
            debug.condition("zone", lambda: uncalled.append("zone") or {"previous": 11, "current": 12}, "CROSSES_ABOVE",
                            {"previous": 12, "current": 11}),
        ]))
        debug.signal("BUY", "RSI breakout", depends_on=["entry"])
        debug.state("pending_order_ids", ["pending-1"])
    event = json.loads(probe.logged[0].split("STRATEGY_LAB_EVENT ", 1)[1])
    assert ok is False and uncalled == [True]
    assert event["decision"] == "FAIL" and event["reason"] == "ENTRY_CONDITION_FAILED"
    assert [c["result"] for c in event["checks"]] == ["PASS", "FAIL", "SKIP", "FAIL"]
    assert event["checks"][2]["blocked_by"] == "entry"
    assert event["indicators"] == [{"name": "RSI 14", "value": 42.5,
                                     "timeframe": "M5", "unit": "index", "source": None}]
    assert event["state"] == {"pending_order_ids": ["pending-1"]}
    assert event["context"] == {"session": "London"}
    assert event["signal"]["side"] == "NONE"
    assert event["signal"]["attempted_side"] == "BUY"


def test_generic_debug_any_group_and_dependencies_record_real_skip():
    class StrategyProbe:
        symbol="XAUUSD"; timeframe="M1"; current_candle=[60_000]; hp={}
        def __init__(self): self.logged=[]
        def log(self,line): self.logged.append(line)
    probe=StrategyProbe(); calls=[]
    with StrategyDebugCandle(probe) as debug:
        expression=debug.group("entry","any",[
            debug.group("trend","all",[
                debug.condition("trend up",False,"==",True),
                debug.condition("trend strength",lambda: calls.append(1) or 2,">",1),
            ]),
            debug.condition("breakout",True,"==",True),
            debug.condition("dependent",lambda: calls.append(2) or True,"==",True,
                            depends_on=["trend up"]),
        ])
        assert debug.evaluate(expression) is True
    event=json.loads(probe.logged[0].split("STRATEGY_LAB_EVENT ",1)[1])
    assert calls == []
    assert event["decision"] == "PASS"
    assert {c["name"]:c["result"] for c in event["checks"]} == {
        "trend up":"FAIL", "trend strength":"SKIP", "trend":"FAIL",
        "breakout":"PASS", "dependent":"SKIP", "entry":"PASS"}


def test_generic_debug_missing_indicator_value_is_skip_not_strategy_exception():
    class StrategyProbe:
        symbol="XAUUSD"; timeframe="M1"; current_candle=[60_000]; hp={}
        def __init__(self): self.logged=[]
        def log(self,line): self.logged.append(line)
    probe=StrategyProbe()
    with StrategyDebugCandle(probe) as debug:
        assert debug.evaluate(debug.condition("RSI ready", None, "<=", 30)) is None
    event=json.loads(probe.logged[0].split("STRATEGY_LAB_EVENT ",1)[1])
    check=event["checks"][0]
    assert event["decision"] == "SKIP"
    assert check["result"] == "SKIP" and check["evaluated"] is False
    assert check["skip_reason"] == "MISSING_COMPARISON_VALUE"


def test_pnl_and_quote_sides():
    result = position_pnl(side="long", quantity_lots=1, entry=101, exit=102, contract=contract())
    assert result["gross_pnl"] == 10
    assert result["commission"] == 1
    assert result["net_pnl"] == 9
    assert result["tick_value"] == pytest.approx(0.1)
    assert position_pnl(side="short", quantity_lots=1, entry=102, exit=101,
                         contract=contract())["net_pnl"] == 9


def test_same_bar_sl_tp_uses_conservative_stop():
    result = conservative_ohlc_exit(side="long", bid={"open": 100, "high": 103, "low": 98},
        ask={"open": 100.2, "high": 103.2, "low": 98.2}, stop=99, target=102)
    assert result == {"reason": "STOP_LOSS", "price": 99, "ambiguous": True}


def test_bidask_simulation_market_next_bar_and_ambiguous_exit():
    candles = [
        {"timestamp_ms": 60_000, "bid_open": 100, "bid_high": 101, "bid_low": 99, "bid_close": 100,
         "ask_open": 100.2, "ask_high": 101.2, "ask_low": 99.2, "ask_close": 100.2},
        {"timestamp_ms": 120_000, "bid_open": 100, "bid_high": 103, "bid_low": 98, "bid_close": 101,
         "ask_open": 100.2, "ask_high": 103.2, "ask_low": 98.2, "ask_close": 101.2},
    ]
    events = [{"event_type": "ORDER_SUBMITTED", "timestamp_ms": 60_000, "signal_timestamp_ms": 60_000,
               "side": "BUY", "order_type": "MARKET", "quantity": 1}]
    result = simulate_bidask(candles, events, contract(), {"stop_distance": 1, "target_distance": 1}, 10000)
    trade = result["trades"][0]
    assert trade["entry_timestamp_ms"] == 120_000
    assert trade["entry_price"] == pytest.approx(100.2)
    assert trade["exit_quote"] == "BID"
    assert trade["exit_reason"] == "STOP_LOSS"
    assert trade["intrabar_ambiguous"] is True
    assert result["status"] == "DUKASCOPY_BIDASK_REPLAY"
    assert result["quote_quality"] == "COMPLETE_QUOTES"
    assert result["strategy_signals_recomputed"] is False
    assert result["broker_contract_verified"] is False
    assert result["tick_order_known"] is False


def test_lab_breakout_recomputes_long_signal_on_ask_and_does_not_use_saved_intent():
    candles = [
        quote_candle(0, bid_close=100, bid_high=101, bid_low=99,
                     ask_close=110, ask_high=111, ask_low=109),
        quote_candle(60_000, bid_close=105, bid_high=106, bid_low=104,
                     ask_close=105.5, ask_high=106, ask_low=105),
    ]
    saved = [{"event_type": "ORDER_SUBMITTED", "timestamp_ms": 60_000,
              "signal_timestamp_ms": 60_000, "side": "BUY", "order_type": "MARKET",
              "quantity": 1}]
    result = simulate_bidask(candles, saved, contract(),
        {"order_mode": 0, "ema_period": 2, "entry_interval": 1, "max_bar_range": 25},
        10_000, strategy_name="LabBreakout", trade_start_ms=0)

    assert result["strategy_signals_recomputed"] is True
    assert result["signal_decision_counts"] == {"PASS": 0, "FAIL": 1, "SKIP": 1}
    assert result["lifecycle"] == []
    assert result["signal_model"].startswith("LabBreakout quote-side signals")


def test_lab_breakout_recomputed_market_order_fills_next_bar_on_ask():
    candles = [
        quote_candle(0, bid_close=100, ask_close=100),
        quote_candle(60_000, bid_close=102, ask_close=102),
        quote_candle(120_000, bid_open=102.1, bid_close=102.2,
                     ask_open=102.3, ask_close=102.4),
    ]
    result = simulate_bidask(candles, [], contract(),
        {"order_mode": 0, "ema_period": 2, "entry_interval": 1,
         "max_bar_range": 25, "stop_distance": 0, "target_distance": 0},
        10_000, strategy_name="LabBreakout", trade_start_ms=0)

    submitted = next(x for x in result["lifecycle"] if x["event_type"] == "ORDER_SUBMITTED")
    filled = next(x for x in result["lifecycle"] if x["event_type"] == "ORDER_FILLED")
    assert submitted["timestamp_ms"] == 60_000
    assert filled["timestamp_ms"] == 120_000
    assert filled["quote"] == "ASK" and filled["fill_price"] == pytest.approx(102.3)
    assert result["strategy_signals_recomputed"] is True


@pytest.mark.parametrize(
    ("mode", "rows", "expected_quote", "expected_price"),
    [
        pytest.param(1, [
            quote_candle(0, bid_close=100, ask_close=100),
            quote_candle(60_000, bid_close=102, ask_close=102,
                         ask_high=102.2, ask_low=101.8),
            quote_candle(120_000, ask_open=102.0, ask_high=102.5),
        ], "ASK", 102.3, id="long-stop-ask-source-and-fill"),
        pytest.param(2, [
            quote_candle(0, bid_close=110, bid_high=110.5, bid_low=109.5),
            quote_candle(60_000, bid_close=100, bid_high=100.2, bid_low=99.5),
            quote_candle(120_000, bid_open=100, bid_low=99.0),
        ], "BID", 99.4, id="short-stop-bid-source-and-fill"),
    ],
)
def test_lab_breakout_quote_side_stop_signals_fill_on_executable_side(
        mode, rows, expected_quote, expected_price):
    result = simulate_bidask(rows, [], contract(),
        {"order_mode": mode, "ema_period": 2, "entry_interval": 1,
         "max_bar_range": 25, "stop_offset": 0.1,
         "stop_distance": 0, "target_distance": 0},
        10_000, strategy_name="LabBreakout", trade_start_ms=0)

    submitted = next(x for x in result["lifecycle"] if x["event_type"] == "ORDER_SUBMITTED")
    filled = next(x for x in result["lifecycle"] if x["event_type"] == "ORDER_FILLED")
    assert submitted["order_type"] == "STOP" and submitted["timestamp_ms"] == 60_000
    assert filled["quote"] == expected_quote
    assert filled["fill_price"] == pytest.approx(expected_price)


def test_unsupported_strategy_keeps_saved_order_replay_and_marks_signal_reexecution_blocked():
    candles = [quote_candle(0), quote_candle(60_000)]
    saved = [{"event_type": "ORDER_SUBMITTED", "timestamp_ms": 0,
              "signal_timestamp_ms": 0, "side": "BUY", "order_type": "MARKET",
              "quantity": 0.1}]
    result = simulate_bidask(candles, saved, contract(), {}, 10_000,
                             strategy_name="ExampleStrategy", trade_start_ms=0)

    assert result["strategy_signals_recomputed"] is False
    assert result["lifecycle"][0]["event_type"] == "ORDER_SUBMITTED"
    assert result["lifecycle"][1]["event_type"] == "ORDER_FILLED"
    assert "Only LabBreakout" in result["warnings"][-1]


def test_generic_quote_signal_contract_uses_causal_ask_history_and_ignores_saved_orders():
    candles = [
        quote_candle(0),
        quote_candle(60_000, ask_high=100.8),
        quote_candle(120_000, ask_open=100.7, ask_high=101.2),
    ]
    seen = []

    def evaluator(context):
        seen.append(context)
        next_state = {"evaluations": context["state"].get("evaluations", 0) + 1}
        if context["index"] == 1:
            return {"decision": "PASS", "reason": "QUOTE_TRIGGER",
                    "signal_quote": "ASK", "signal": {"side": "BUY", "order_type": "STOP",
                    "planned_price": 100.9, "quantity": 0.1}, "state": next_state}
        return {"decision": "SKIP", "reason": "NO_ENTRY", "state": next_state}

    saved = [{"event_type": "ORDER_SUBMITTED", "timestamp_ms": 60_000,
              "side": "SELL", "order_type": "MARKET", "quantity": 9}]
    result = simulate_bidask(candles, saved, contract(), {}, 10_000,
        strategy_name="QuoteContractProbe", trade_start_ms=0, signal_evaluator=evaluator)

    submitted = next(x for x in result["lifecycle"] if x["event_type"] == "ORDER_SUBMITTED")
    filled = next(x for x in result["lifecycle"] if x["event_type"] == "ORDER_FILLED")
    assert submitted["side"] == "long" and submitted["order_type"] == "STOP"
    assert filled["quote"] == "ASK" and filled["fill_price"] == pytest.approx(100.9)
    assert result["strategy_signals_recomputed"] is True
    assert result["signal_model"].startswith("QuoteContractProbe strategy_lab_quote_signal")
    assert len(seen[1]["history"]) == 2 and seen[1]["history"][-1]["timestamp_ms"] == 60_000
    assert seen[0]["state"] == {} and seen[1]["state"] == {"evaluations": 1}
    seen[1]["history"][-1]["ask_high"] = -1
    assert candles[1]["ask_high"] == 100.8


def test_generic_quote_signal_index_starts_at_trade_range_and_close_state_uses_same_index():
    candles = [
        quote_candle(0),
        quote_candle(60_000),
        quote_candle(120_000),
        quote_candle(180_000, bid_open=100.2, ask_open=100.4,
                     bid_low=99.5, ask_low=99.7),
        quote_candle(240_000, bid_low=98.0, ask_low=98.2),
        quote_candle(300_000),
    ]
    seen = []

    def evaluator(context):
        seen.append({"index": context["index"], "history_index": context["history_index"],
                     "last_close_index": context["last_close_index"]})
        if context["index"] == 0:
            return {"decision": "PASS", "reason": "FIRST_TRADE_CANDLE",
                    "signal": {"side": "BUY", "order_type": "MARKET", "quantity": 0.1}}
        return {"decision": "SKIP", "reason": "NO_ENTRY"}

    simulate_bidask(candles, [], contract(), {"stop_distance": 1}, 10_000,
                    strategy_name="QuoteContractProbe", trade_start_ms=120_000,
                    signal_evaluator=evaluator)

    assert seen[0] == {"index": 0, "history_index": 2, "last_close_index": None}
    assert seen[1] == {"index": 1, "history_index": 3, "last_close_index": None}
    assert seen[2] == {"index": 2, "history_index": 4, "last_close_index": 2}
    assert seen[3] == {"index": 3, "history_index": 5, "last_close_index": 2}


@pytest.mark.parametrize(("signal", "message"), [
    ({"side": "BUY", "order_type": "STOP", "quantity": 0.1}, "requires a planned_price"),
    ({"side": "BUY", "order_type": "TRAILING_STOP", "planned_price": 101,
      "quantity": 0.1}, "order_type must be MARKET, STOP, or LIMIT"),
])
def test_generic_quote_signal_rejects_underspecified_or_unsupported_order(signal, message):
    def evaluator(_context):
        return {"decision": "PASS", "reason": "INVALID_TEST_ORDER", "signal": signal}

    with pytest.raises(ValueError, match=message):
        simulate_bidask([quote_candle(0)], [], contract(), {}, 10_000,
                        strategy_name="QuoteContractProbe", trade_start_ms=0,
                        signal_evaluator=evaluator)


def test_precomputed_quote_rsi_is_causal_and_history_is_lazy_prefix_only():
    candles = [quote_candle(i * 60_000, ask_close=100 + (i % 9) * 0.2,
                            ask_open=100 + (i % 9) * 0.2,
                            ask_high=101 + (i % 9) * 0.2,
                            ask_low=99 + (i % 9) * 0.2) for i in range(80)]

    def execute(rows):
        observed = []

        def evaluator(context):
            history = context["history"]
            assert len(history) == context["index"] + 1
            assert history[-1]["timestamp_ms"] == context["candle"]["timestamp_ms"]
            observed.append(context["indicators"]["rsi"])
            # Reading/mutating one history row must not expose the engine's candle.
            history[-1]["ask_close"] = -1
            return {"decision": "SKIP", "reason": "INDICATOR_PROBE"}

        simulate_bidask(rows, [], contract(), {}, 10_000, strategy_name="RsiProbe",
                        trade_start_ms=0, signal_evaluator=evaluator,
                        indicator_specs={"rsi": {"type": "rsi", "source": "ask_close", "period": 5}})
        return observed

    original = execute(candles)
    altered = [dict(row) for row in candles]
    for row in altered[40:]:
        row["ask_close"] += 1000
    changed = execute(altered)
    assert original[:40] == changed[:40]
    assert original[40:] != changed[40:]
    assert candles[10]["ask_close"] > 0


def test_lab_breakout_signal_prefix_is_unchanged_by_future_quote_changes():
    candles = [quote_candle(x * 60_000, bid_close=100 + x, ask_close=100 + x,
                            bid_open=100 + x, ask_open=100.2 + x,
                            bid_high=101 + x, ask_high=101.2 + x,
                            bid_low=99 + x, ask_low=99.2 + x) for x in range(5)]
    changed = [dict(row) for row in candles]
    changed[-1].update(ask_close=5000, ask_high=5000, bid_close=5000, bid_high=5000)
    params = {"order_mode": 0, "ema_period": 2, "entry_interval": 1,
              "max_bar_range": 25, "stop_distance": 0, "target_distance": 0}
    original = simulate_bidask(candles, [], contract(), params, 10_000,
                               strategy_name="LabBreakout", trade_start_ms=0)
    altered = simulate_bidask(changed, [], contract(), params, 10_000,
                              strategy_name="LabBreakout", trade_start_ms=0)
    cutoff = 180_000
    prefix_original = [x for x in original["lifecycle"] if x["timestamp_ms"] <= cutoff]
    prefix_altered = [x for x in altered["lifecycle"] if x["timestamp_ms"] <= cutoff]
    assert prefix_original == prefix_altered


@pytest.mark.parametrize(
    ("side", "order_type", "planned", "trigger_bar", "expected_quote", "expected_fill"),
    [
        pytest.param("BUY", "MARKET", None, {"bid_open": 100.2, "ask_open": 100.4}, "ASK", 100.4, id="long-market-ask-next-open"),
        pytest.param("SELL", "MARKET", None, {"bid_open": 100.2, "ask_open": 100.4}, "BID", 100.2, id="short-market-bid-next-open"),
        pytest.param("BUY", "STOP", 100.9, {"bid_high": 100.8, "ask_high": 101.0}, "ASK", 100.9, id="long-stop-ask-only-crosses"),
        pytest.param("SELL", "STOP", 99.1, {"bid_low": 99.0, "ask_low": 99.2}, "BID", 99.1, id="short-stop-bid-only-crosses"),
        pytest.param("BUY", "LIMIT", 99.1, {"bid_low": 98.9, "ask_low": 99.2}, None, None, id="long-limit-bid-only-crosses-no-fill"),
        pytest.param("SELL", "LIMIT", 101.1, {"bid_high": 101.0, "ask_high": 101.2}, None, None, id="short-limit-ask-only-crosses-no-fill"),
        pytest.param("BUY", "STOP", 101.0, {"bid_open": 101.0, "ask_open": 101.2}, "ASK", 101.2, id="long-stop-gap-up-fills-open"),
        pytest.param("SELL", "STOP", 99.0, {"bid_open": 98.8, "ask_open": 99.0}, "BID", 98.8, id="short-stop-gap-down-fills-open"),
        pytest.param("BUY", "LIMIT", 99.0, {"bid_open": 97.8, "ask_open": 98.0, "bid_low": 97.5, "ask_low": 97.7}, "ASK", 98.0, id="long-limit-gap-down-fills-open"),
        pytest.param("SELL", "LIMIT", 101.0, {"bid_open": 101.2, "ask_open": 101.4, "bid_high": 101.6, "ask_high": 101.8}, "BID", 101.2, id="short-limit-gap-up-fills-open"),
    ],
)
def test_bidask_entry_triggers_use_executable_quote_side_and_gap_price(
        side, order_type, planned, trigger_bar, expected_quote, expected_fill):
    first, next_bar = quote_candle(0), quote_candle(60_000, **trigger_bar)
    event = {"event_type": "ORDER_SUBMITTED", "timestamp_ms": 0, "signal_timestamp_ms": 0,
             "side": side, "order_type": order_type, "quantity": 0.1}
    if planned is not None:
        event["planned_price"] = planned

    result = simulate_bidask([first, next_bar], [event], contract(), {}, 10_000)
    fills = [row for row in result["lifecycle"] if row["event_type"] == "ORDER_FILLED"]

    if expected_quote is None:
        assert fills == []
        assert result["lifecycle"][-1]["event_type"] == "ORDER_SUBMITTED"
    else:
        assert len(fills) == 1
        assert fills[0]["quote"] == expected_quote
        assert fills[0]["fill_price"] == pytest.approx(expected_fill)
        assert fills[0]["timestamp_ms"] == 60_000


def test_bidask_pending_order_expires_before_trigger_at_configured_bar_limit():
    candles = [
        quote_candle(0),
        quote_candle(60_000, ask_high=100.8),
        quote_candle(120_000, ask_high=101.0),
    ]
    event = {"event_type": "ORDER_SUBMITTED", "timestamp_ms": 0,
             "signal_timestamp_ms": 0, "side": "BUY", "order_type": "STOP",
             "planned_price": 100.9, "quantity": 0.1}

    result = simulate_bidask(candles, [event], contract(),
                             {"pending_expiry_bars": 2}, 10_000)

    assert not [row for row in result["lifecycle"] if row["event_type"] == "ORDER_FILLED"]
    cancelled = [row for row in result["lifecycle"] if row["event_type"] == "ORDER_CANCELLED"]
    assert cancelled == [{"event_type": "ORDER_CANCELLED", "timestamp_ms": 120_000,
                          "reason": "PENDING_EXPIRY", "side": "long"}]


def test_account_conversion_commission_and_volume_step_validation():
    spec = contract(contract_size=100, tick_size=0.01, quote_to_account=1.25,
                    volume_step=0.1, commission_per_lot_side=3.5)
    pnl = position_pnl(side="long", quantity_lots=0.02, entry=2000, exit=2001, contract=spec)
    assert pnl["gross_pnl"] == pytest.approx(2.5)
    assert pnl["commission"] == pytest.approx(0.14)
    assert pnl["net_pnl"] == pytest.approx(2.36)
    assert pnl["tick_value"] == pytest.approx(1.25)

    bars = [quote_candle(0), quote_candle(60_000, bid_low=98.0)]
    event = {"event_type": "ORDER_SUBMITTED", "timestamp_ms": 0, "side": "BUY",
             "order_type": "MARKET", "quantity": 0.15}
    closed = simulate_bidask(bars, [event], spec, {"stop_distance": 1}, 10_000)["trades"]
    assert len(closed) == 1 and closed[0]["volume_step_valid"] is False


def test_replay_hides_future_and_joins_by_ids_not_position_id():
    idx = pd.to_datetime([60_000, 120_000, 180_000], unit="ms", utc=True)
    candles = pd.DataFrame({"close": [10, 11, 12]}, index=idx)
    events = [{"timestamp_ms": x, "event_id": str(x)} for x in (60_000, 120_000, 180_000)]
    visible, visible_events = replay_slice(candles, 120_000, events)
    assert visible["close"].tolist() == [10, 11]
    assert [e["timestamp_ms"] for e in visible_events] == [60_000, 120_000]
    linked = event_links([{"event_type": "POSITION_OPEN", "position_id": "p1", "order_id": "o1"},
                          {"event_type": "POSITION_CLOSE", "trade_id": "t1", "order_id": "o2"}],
                         [{"id": "t1", "orders": [{"id": "o1"}, {"id": "o2"}]}])
    assert linked["trades"][0]["trade_id"] == "t1"
    assert linked["trades"][0]["linked_event_count"] == 2


def test_replay_future_candle_changes_do_not_affect_visible_prefix_or_events():
    idx = pd.to_datetime([60_000, 120_000, 180_000], unit="ms", utc=True)
    candles = pd.DataFrame({"close": [10, 11, 12]}, index=idx)
    altered_future = candles.copy()
    altered_future.loc[idx[2], "close"] = 99_999
    events = [
        {"event_type": "CANDLE_DECISION", "timestamp_ms": 60_000, "decision": "PASS"},
        {"event_type": "CANDLE_DECISION", "timestamp_ms": 120_000, "decision": "FAIL"},
        {"event_type": "CANDLE_DECISION", "timestamp_ms": 180_000, "decision": "PASS"},
    ]

    original_view, original_events = replay_slice(candles, 120_000, events)
    changed_view, changed_events = replay_slice(altered_future, 120_000, events)

    pd.testing.assert_frame_equal(original_view, changed_view)
    assert original_events == changed_events
    assert all(event["timestamp_ms"] <= 120_000 for event in changed_events)


def test_recorded_replay_snapshot_is_exact_candle_and_never_uses_future_state():
    events = [
        {"event_type": "CANDLE_DECISION", "timestamp_ms": 60_000, "symbol": "XAUUSD",
         "timeframe": "M1", "strategy": "LabBreakout", "decision": "PASS",
         "checks": [{"name": "EMA", "result": "PASS"}], "state": {"ema": 10}},
        {"event_type": "CANDLE_DECISION", "timestamp_ms": 120_000, "symbol": "XAUUSD",
         "timeframe": "M1", "strategy": "LabBreakout", "decision": "FAIL",
         "checks": [], "state": {"ema": 12}},
        {"event_type": "CANDLE_DECISION", "timestamp_ms": 180_000, "symbol": "XAUUSD",
         "timeframe": "M1", "strategy": "LabBreakout", "decision": "PASS",
         "checks": [], "state": {"ema": 99}},
    ]
    snapshot = recorded_state_snapshot(events[:2], 120_000, symbol="XAUUSD", timeframe="M1")
    assert snapshot["available"] and snapshot["state_available"]
    assert snapshot["decision"] == "FAIL" and snapshot["state"] == {"ema": 12}
    assert snapshot["timestamp_ms"] == 120_000


def test_recorded_replay_snapshot_matches_jesse_symbol_and_timeframe_aliases():
    events = [{"event_type": "CANDLE_DECISION", "timestamp_ms": 120_000,
               "symbol": "XAU-USD", "timeframe": "1m", "strategy": "LabBreakout",
               "decision": "PASS", "state": {"ema": 10}}]
    snapshot = recorded_state_snapshot(events, 120_000, symbol="XAUUSD", event_symbol="XAU-USD",
                                       timeframe="M1")
    assert snapshot["available"] and snapshot["state"] == {"ema": 10}


def test_recorded_replay_snapshot_explains_missing_or_ambiguous_state():
    missing = recorded_state_snapshot([], 120_000, symbol="XAUUSD", timeframe="M1")
    assert not missing["available"] and "not inferred" in missing["reason"]
    duplicated = [{"event_type": "CANDLE_DECISION", "timestamp_ms": 120_000,
                   "symbol": "XAUUSD", "timeframe": "M1"}] * 2
    ambiguous = recorded_state_snapshot(duplicated, 120_000, symbol="XAUUSD", timeframe="M1")
    assert not ambiguous["available"] and "ambiguous" in ambiguous["reason"]


def test_labbreakout_causal_reexecution_matches_recorded_prefix_and_ignores_future():
    index = pd.to_datetime([60_000, 120_000, 180_000, 240_000], unit="ms", utc=True)
    candles = pd.DataFrame({"open": [9.0, 19.0, 29.0, 1000.0],
                            "high": [11.0, 21.0, 31.0, 1100.0],
                            "low": [9.0, 19.0, 29.0, 900.0],
                            "close": [10.0, 20.0, 30.0, 1000.0]}, index=index)
    period = 2
    ema = 10.0 + 2.0 / 3.0 * (20.0 - 10.0)
    ema = ema + 2.0 / 3.0 * (30.0 - ema)
    snapshot = {"available": True, "state_available": True, "timestamp_ms": 180_000,
                "decision": "PASS", "reason": "ENTRY_CONDITIONS_MET", "state": {"index": 1, "position_open": False,
                "pending_order_ids": [], "ema": ema}}
    result = reexecute_labbreakout(candles, 180_000, start_ms=120_000,
                                   warmup_candles=1,
                                   parameters={"ema_period": period, "entry_interval": 1,
                                               "max_bar_range": 5, "order_mode": 0},
                                   recorded_snapshot=snapshot)
    assert result["available"] and result["decision"] == "PASS"
    assert result["matches_recorded"] and result["ema_matches_recorded"]
    changed_future = candles.copy()
    changed_future.loc[index[-1], ["open", "high", "low", "close"]] = [2, 3, 1, 2]
    after_future_edit = reexecute_labbreakout(changed_future, 180_000, start_ms=120_000,
                                               warmup_candles=1,
                                               parameters={"ema_period": period, "entry_interval": 1,
                                                           "max_bar_range": 5, "order_mode": 0},
                                               recorded_snapshot=snapshot)
    assert after_future_edit["decision"] == result["decision"]
    assert after_future_edit["ema"] == result["ema"]


def test_labbreakout_reexecution_refuses_unrecorded_position_state():
    index = pd.to_datetime([60_000], unit="ms", utc=True)
    candles = pd.DataFrame({"open": [1], "high": [2], "low": [0], "close": [1]}, index=index)
    snapshot = {"available": True, "state_available": False, "state": {}}
    result = reexecute_labbreakout(candles, 60_000, start_ms=60_000, warmup_candles=0,
                                   parameters={}, recorded_snapshot=snapshot)
    assert not result["available"] and "exact recorded strategy state" in result["reason"]


def test_event_links_bridges_unique_signal_to_actual_entry_order():
    events = [
        {"event_type": "ORDER_SUBMITTED", "event_id": "submit-1", "symbol": "XAU-USD",
         "signal_timestamp_ms": 1000, "order_id": None},
        {"event_type": "POSITION_OPEN", "event_id": "open-1", "symbol": "XAU-USD",
         "signal_timestamp_ms": 1000, "order_id": "entry-1"},
        {"event_type": "POSITION_CLOSE", "event_id": "close-1", "trade_id": "trade-1",
         "order_id": "exit-1"},
    ]
    trades = [{"id": "trade-1", "symbol": "XAU-USD",
               "orders": [{"id": "entry-1"}, {"id": "exit-1"}]}]
    linked = event_links(events, trades)["trades"][0]
    assert linked["event_ids"] == ["close-1", "open-1", "submit-1"]
    assert linked["ambiguous_signal_submit_count"] == 0


def test_event_links_does_not_guess_when_signal_submit_is_ambiguous():
    events = [
        {"event_type": "ORDER_SUBMITTED", "event_id": "submit-a", "symbol": "XAU-USD",
         "signal_timestamp_ms": 1000},
        {"event_type": "ORDER_SUBMITTED", "event_id": "submit-b", "symbol": "XAU-USD",
         "signal_timestamp_ms": 1000},
        {"event_type": "POSITION_OPEN", "event_id": "open-1", "symbol": "XAU-USD",
         "signal_timestamp_ms": 1000, "order_id": "entry-1"},
    ]
    trades = [{"id": "trade-1", "symbol": "XAU-USD", "orders": [{"id": "entry-1"}]}]
    linked = event_links(events, trades)["trades"][0]
    assert linked["event_ids"] == ["open-1"]
    assert linked["ambiguous_signal_submit_count"] == 1
