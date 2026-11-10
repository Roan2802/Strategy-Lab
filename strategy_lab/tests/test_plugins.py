import numpy as np
import pandas as pd
import pytest

from strategy_lab import plugins


def _frames(days=20):
    idx = pd.date_range("2026-03-02", periods=days * 1440, freq="1min", tz="UTC")
    rng = np.random.default_rng(1)
    close = 2000 + np.cumsum(rng.normal(0, 0.3, len(idx))) + 5 * np.sin(np.arange(len(idx)) / 3000)
    bid = pd.DataFrame({"open": close, "high": close + 0.2, "low": close - 0.2, "close": close, "volume": 1.0}, index=idx)
    ask = bid + 0.25
    ask["volume"] = 1.0
    return bid, ask


def test_available_lists_example_and_goldreaper():
    ids = {p["id"] for p in plugins.available()}
    assert {"voorbeeld_ma_cross", "gold_reaper"} <= ids


def test_example_plugin_respects_window_and_contract():
    bid, ask = _frames()
    r = plugins.run("voorbeeld_ma_cross", bid, ask, None, capital=10000, start="2026-03-10", end="2026-03-20")
    assert r["trades"], "example should trade on the oscillating fixture"
    assert all(pd.Timestamp("2026-03-10", tz="UTC") <= pd.Timestamp(t["entry_time"]) < pd.Timestamp("2026-03-20", tz="UTC")
               for t in r["trades"])
    assert r["engine"] == "PLUGIN:voorbeeld_ma_cross" and len(r["plugin_sha256"]) == 64


def test_contract_violation_is_refused():
    bad = [{"entry_time": "2026-01-01T00:00:00", "exit_time": "2026-01-01T01:00:00+00:00", "side": "buy",
            "entry_price": 1, "exit_price": 2, "qty": 1, "pnl": 1}]
    problems = plugins.check_trades(bad, "2026-01-01", "2026-02-01")
    assert any("UTC" in p for p in problems) and any("side" in p for p in problems)


def test_goldreaper_adapter_runs_on_fixture_and_converts_server_time():
    bid, ask = _frames(days=10)
    r = plugins.run("gold_reaper", bid, ask, None, capital=10000, start="2026-03-04", end="2026-03-12")
    for t in r["trades"]:
        assert pd.Timestamp(t["entry_time"]).tzinfo is not None
        assert t["side"] in ("long", "short")
    assert "summary" in r


def test_unknown_plugin():
    with pytest.raises(KeyError):
        plugins.load("bestaat_niet")
