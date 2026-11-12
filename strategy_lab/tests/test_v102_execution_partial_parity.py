"""V102: partial exits behave identically in the research backtest and the frozen forward model (M1 fixtures)."""
import pandas as pd
import pytest

import test_forward_rules as tfr   # tests dir is on sys.path (rootdir import mode); reuses its random-walk fixture

LEVELS = [{"r": 0.6, "fraction": 0.5, "stop_r": 0.0}, {"r": 1.0, "fraction": 0.25, "stop_r": 0.3}]


@pytest.fixture()
def partial_rich_spec(monkeypatch):
    original = tfr.rich_spec
    monkeypatch.setattr(tfr, "rich_spec", lambda order: {**original(order), "partial_exits": LEVELS})


def test_forward_partial_exits_equal_the_backtest_trade_for_trade(tmp_path, partial_rich_spec):
    engine, portfolio, expected = tfr.run_forward_vs_backtest(tmp_path, {"type": "market"}, days=3, minutes=240)
    events = engine.report(portfolio)["events"]
    actual = [e["detail"] for e in events if e["kind"] == "POSITION_CLOSED"]
    entries = {a["entry_at"] for a in actual}                       # a position still open at the end is not in `expected`
    partials = [e["detail"] for e in events if e["kind"] == "POSITION_PARTIAL_CLOSE" and e["detail"]["entry_at"] in entries]
    assert len(expected) >= 8 and len(actual) == len(expected)
    n_partials = sum(len(t["partial_fills"]) for t in expected)
    assert n_partials >= 3 and len(partials) == n_partials          # the fixture really exercises partial fills
    for ours, theirs in zip(actual, expected):
        assert pd.Timestamp(ours["entry_at"]) == pd.Timestamp(theirs["entry_time"])
        assert pd.Timestamp(ours["exit_at"]) == pd.Timestamp(theirs["exit_time"])
        assert ours["side"] == theirs["side"] and ours["exit_reason"] == theirs["exit_reason"]
        assert ours["exit"] == pytest.approx(theirs["exit_price"])
        assert ours["quantity"] == pytest.approx(theirs["qty"])      # whole trade, partial quantities included
        assert ours["net_pnl"] == pytest.approx(theirs["pnl"])       # realised on partials + remainder
    flat = [(p["exit_at"], p["quantity"], p["partial_net_pnl"]) for p in partials]
    mine = [(pd.Timestamp(f["time"]), f["qty"], f["net_pnl"]) for t in expected for f in t["partial_fills"]]
    for (at, qty, net), (t2, q2, n2) in zip(flat, mine):
        assert pd.Timestamp(at) == t2 and qty == pytest.approx(q2) and net == pytest.approx(n2)
    balance = engine.report(portfolio)["portfolio"]["balance"]
    open_partials = sum(e["detail"]["partial_net_pnl"] for e in events
                        if e["kind"] == "POSITION_PARTIAL_CLOSE" and e["detail"]["entry_at"] not in entries)
    # realised partials of a still-open position are already in the balance; closed trades are counted once
    assert balance == pytest.approx(10000 + sum(t["pnl"] for t in expected) + open_partials)
