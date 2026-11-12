"""V102: FxService contract (causal, inverse, JPY, crosses, staleness, invalid prices, no 1.0 fallback). Synthetic data."""
import math
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from strategy_lab.research import fx
from strategy_lab.research.fx import Conversion, FxRateUnavailable, FxService

UTC = "UTC"


class FakeM1:
    """M1 frames with an optional gap (no bars) and optional invalid bars; mimics DataManager.candles."""

    def __init__(self, pairs=None, gap=None, bad=None):
        self.calls = 0
        self.frames = {}
        for pair, (p0, drift, spread) in (pairs or {"EURUSD": (1.10, 1e-6, 0.00008), "USDJPY": (150.0, 1e-4, 0.006)}).items():
            idx = pd.date_range("2024-03-01", "2024-04-15", freq="1min", tz=UTC)
            idx = idx[idx.dayofweek < 5]
            if gap:
                idx = idx[(idx < pd.Timestamp(gap[0], tz=UTC)) | (idx >= pd.Timestamp(gap[1], tz=UTC))]
            mid = p0 + drift * np.arange(len(idx))
            f = pd.DataFrame({"bid_open": mid - spread / 2, "ask_open": mid + spread / 2}, index=idx)
            f["bid_close"], f["ask_close"] = f["bid_open"], f["ask_open"]
            self.frames[pair] = f
        for ts, col, val in (bad or []):
            self.frames[ts[0]].loc[pd.Timestamp(ts[1], tz=UTC), col] = val

    def candles(self, symbol, timeframe, start=None, end=None):
        self.calls += 1
        if symbol not in self.frames:
            raise KeyError(f"no data for {symbol}")
        f = self.frames[symbol]
        utc = lambda x: pd.Timestamp(x).tz_localize(UTC) if pd.Timestamp(x).tzinfo is None else pd.Timestamp(x)
        if start:
            f = f[f.index >= utc(start)]
        if end:
            f = f[f.index < utc(end)]
        return f, {"symbol": symbol, "bid_source": {"sha256": "aa" + symbol}, "ask_source": {"sha256": "bb" + symbol}}


def mid(dm, pair, ts):
    f = dm.frames[pair]
    row = f.loc[pd.Timestamp(ts, tz=UTC)]
    return (row.bid_open + row.ask_open) / 2, row


def test_eur_to_usd_uses_eurusd_mid_at_bar_open_and_reports_provenance():
    dm = FakeM1()
    svc = FxService(dm)
    c = svc.rate("EUR", "USD", "2024-03-13T10:30:00Z")
    expected, row = mid(dm, "EURUSD", "2024-03-13T10:30:00Z")
    assert isinstance(c, Conversion) and c.status == "OK"
    assert c.rate == pytest.approx(expected) and c.side == "mid" and c.source == "DUKASCOPY_M1:EURUSD"
    assert c.uncertainty == pytest.approx((row.ask_open - row.bid_open) / 2 / expected)
    assert len(c.data_version) == 64 and c.ts == pd.Timestamp("2024-03-13T10:30:00Z")
    amount, c2 = svc.convert(200.0, "EUR", "USD", "2024-03-13T10:30:00Z")
    assert amount == pytest.approx(200.0 * expected) and c2 == c


def test_usd_to_eur_is_the_inverse_of_eurusd_and_sides_follow_the_settlement_rule():
    dm = FakeM1()
    svc = FxService(dm)
    ts = "2024-03-13T10:30:00Z"
    m, row = mid(dm, "EURUSD", ts)
    assert svc.rate("USD", "EUR", ts).rate == pytest.approx(1.0 / m)
    assert svc.rate("USD", "EUR", ts, "ask").rate == pytest.approx(1.0 / row.ask_open)
    # conservative: a gain converts to fewer account units (EUR->USD at Bid, USD->EUR at 1/Ask); a loss to more
    assert svc.rate("EUR", "USD", ts, "conservative").rate == pytest.approx(row.bid_open)
    assert svc.rate("USD", "EUR", ts, "conservative").rate == pytest.approx(1.0 / row.ask_open)
    gain, cg = svc.convert(100.0, "EUR", "USD", ts, "conservative")
    loss, cl = svc.convert(-100.0, "EUR", "USD", ts, "conservative")
    assert cg.side == "bid" and cl.side == "ask" and gain == pytest.approx(100 * row.bid_open)
    assert loss == pytest.approx(-100 * row.ask_open) and abs(loss) > gain


def test_jpy_to_usd_and_usd_to_jpy():
    dm = FakeM1()
    svc = FxService(dm)
    ts = "2024-03-14T08:00:00Z"
    m, _ = mid(dm, "USDJPY", ts)
    assert svc.rate("JPY", "USD", ts).rate == pytest.approx(1.0 / m)
    assert svc.rate("USD", "JPY", ts).rate == pytest.approx(m)
    value, c = svc.convert(15_000.0, "JPY", "USD", ts)
    assert value == pytest.approx(15_000.0 / m) and c.source == "DUKASCOPY_M1:USDJPY"


def test_cross_through_usd_jpy_to_eur():
    dm = FakeM1()
    svc = FxService(dm)
    ts = "2024-03-14T08:00:00Z"
    eurusd, _ = mid(dm, "EURUSD", ts)
    usdjpy, _ = mid(dm, "USDJPY", ts)
    c = svc.rate("JPY", "EUR", ts)
    assert c.rate == pytest.approx((1.0 / usdjpy) / eurusd) and c.source == "CROSS:USDJPY*EURUSD"
    assert c.uncertainty > svc.rate("JPY", "USD", ts).uncertainty


def test_rates_differ_per_day_and_are_causal_last_bar_at_or_before_ts():
    dm = FakeM1()
    svc = FxService(dm)
    a = svc.rate("EUR", "USD", "2024-03-11T12:00:00Z").rate
    b = svc.rate("EUR", "USD", "2024-03-12T12:00:00Z").rate
    assert a != b
    # between two minute bars the earlier bar is used; never the later one
    c = svc.rate("EUR", "USD", "2024-03-12T12:00:30Z")
    assert c.rate == pytest.approx(b)
    # before the first bar of the dataset: nothing known -> unavailable (no look-ahead)
    with pytest.raises(FxRateUnavailable):
        svc.rate("EUR", "USD", "2024-02-20T12:00:00Z")


def test_missing_pair_missing_manager_and_unroutable_currency_raise_and_try_convert_flags():
    dm = FakeM1()
    svc = FxService(dm)
    with pytest.raises(FxRateUnavailable):
        svc.rate("CHF", "USD", "2024-03-13T10:00:00Z")           # USDCHF known pair, no data
    with pytest.raises(FxRateUnavailable):
        svc.rate("ZAR", "USD", "2024-03-13T10:00:00Z")           # no route at all
    with pytest.raises(FxRateUnavailable):
        FxService(None).rate("EUR", "USD", "2024-03-13T10:00:00Z")
    value, c = svc.try_convert(100.0, "CHF", "USD", "2024-03-13T10:00:00Z")
    assert value is None and c.status == "FX_RATE_UNAVAILABLE" and math.isnan(c.rate) and c.rate != 1.0


@pytest.mark.parametrize("col,val", [("bid_open", 0.0), ("bid_open", -1.0), ("ask_open", float("nan")), ("ask_open", 0.5)])
def test_invalid_price_at_the_last_bar_raises_instead_of_using_an_older_price(col, val):
    dm = FakeM1(bad=[(("EURUSD", "2024-03-13T10:30:00"), col, val)])
    svc = FxService(dm)
    with pytest.raises(FxRateUnavailable, match="invalid"):
        svc.rate("EUR", "USD", "2024-03-13T10:30:20Z")
    assert svc.rate("EUR", "USD", "2024-03-13T10:31:00Z").status == "OK"   # next valid bar works again


def test_data_gap_within_staleness_uses_last_bar_but_longer_gap_is_stale():
    dm = FakeM1(gap=("2024-03-13T00:00", "2024-03-18T00:00"))   # 4+ days without bars
    svc = FxService(dm)
    last = dm.frames["EURUSD"][dm.frames["EURUSD"].index < pd.Timestamp("2024-03-13", tz=UTC)].iloc[-1]
    ok = svc.rate("EUR", "USD", "2024-03-14T12:00:00Z")           # 2024-03-12 23:59 bar is ~36h old: allowed
    assert ok.rate == pytest.approx((last.bid_open + last.ask_open) / 2)
    with pytest.raises(FxRateUnavailable, match="stale"):
        svc.rate("EUR", "USD", "2024-03-17T12:00:00Z")            # ~108h old
    assert FxService(dm, max_staleness=timedelta(hours=200)).rate("EUR", "USD", "2024-03-17T12:00:00Z").status == "OK"


def test_weekend_is_within_default_staleness():
    dm = FakeM1()
    svc = FxService(dm)
    assert svc.rate("EUR", "USD", "2024-03-10T20:00:00Z").status == "OK"   # Sunday, last bar Friday 23:59


def test_dst_boundary_local_times_map_to_the_right_utc_instant():
    dm = FakeM1()
    svc = FxService(dm)
    # Europe/Amsterdam springs forward on 2024-03-31 02:00 local -> 01:00 UTC; local 03:30 CEST == 01:30 UTC
    local = pd.Timestamp("2024-03-31 03:30", tz="Europe/Amsterdam")
    assert svc.rate("EUR", "USD", local).ts == pd.Timestamp("2024-03-31T01:30:00Z")
    # a Friday instant across the US DST change (2024-03-10 is Sunday) is unambiguous in UTC
    before = pd.Timestamp("2024-03-08 16:00", tz="America/New_York")   # EST, 21:00 UTC
    after = pd.Timestamp("2024-03-15 16:00", tz="America/New_York")    # EDT, 20:00 UTC
    assert svc.rate("EUR", "USD", before).ts == pd.Timestamp("2024-03-08T21:00:00Z")
    assert svc.rate("EUR", "USD", after).ts == pd.Timestamp("2024-03-15T20:00:00Z")
    # naive timestamps are treated as UTC, never as local time
    assert svc.rate("EUR", "USD", "2024-03-15 20:00").ts == after.tz_convert("UTC")


def test_same_currency_is_identity_and_only_then_one():
    svc = FxService(FakeM1())
    c = svc.rate("usd", "USD", "2024-03-13T10:00:00Z")
    assert c.rate == 1.0 and c.source == "IDENTITY" and c.uncertainty == 0.0
    assert svc.rate("EUR", "USD", "2024-03-13T10:00:00Z").rate != 1.0


def test_multi_currency_portfolio_helper_sums_at_one_instant_and_never_partially():
    dm = FakeM1()
    svc = FxService(dm)
    ts = "2024-03-14T08:00:00Z"
    eurusd, _ = mid(dm, "EURUSD", ts)
    usdjpy, _ = mid(dm, "USDJPY", ts)
    total, parts = svc.convert_portfolio({"USD": 1000.0, "EUR": 500.0, "JPY": 30_000.0}, "USD", ts)
    assert total == pytest.approx(1000 + 500 * eurusd + 30_000 / usdjpy) and [p.status for p in parts] == ["OK"] * 3
    total, parts = svc.convert_portfolio({"USD": 1000.0, "CHF": 10.0}, "USD", ts)
    assert total is None and parts[1].status == "FX_RATE_UNAVAILABLE"


def test_series_matches_rate_and_keeps_invalid_prices_nan():
    dm = FakeM1(bad=[(("EURUSD", "2024-03-13T10:30:00"), "bid_open", 0.0)])
    svc = FxService(dm)
    s = svc.series("EUR", "USD", "M1", "2024-03-13", "2024-03-14")
    t = pd.Timestamp("2024-03-13T09:00:00Z")
    assert s.loc[t] == pytest.approx(svc.rate("EUR", "USD", t).rate)
    assert math.isnan(s.loc[pd.Timestamp("2024-03-13T10:30:00Z")])
    inv = svc.series("USD", "EUR", "M1", "2024-03-13", "2024-03-14")
    assert inv.loc[t] == pytest.approx(1.0 / s.loc[t])
    assert svc.series("EUR", "EUR").empty and svc.series("EUR", "CHF", "M1") is None
    assert math.isnan(fx.asof(s, [pd.Timestamp("2024-03-13T10:30:30Z")])[0])   # invalid print never papered over


def test_convert_trades_uses_exit_time_rate_and_raises_when_missing():
    idx = pd.date_range("2024-03-13T00:00Z", periods=4, freq="1h")
    series = pd.Series([1.10, 1.20, np.nan, 1.40], index=idx)
    trades = [{"exit_time": "2024-03-13T01:30:00+00:00", "pnl": 10.0, "costs": 1.0}]
    out = fx.convert_trades(trades, series)
    assert out[0]["pnl"] == pytest.approx(12.0) and out[0]["fx_rate"] == 1.20
    with pytest.raises(FxRateUnavailable):
        fx.convert_trades([{"exit_time": "2024-03-13T02:30:00+00:00", "pnl": 1.0}], series)      # NaN rate
    with pytest.raises(FxRateUnavailable):
        fx.convert_trades([{"exit_time": "2024-03-12T22:00:00+00:00", "pnl": 1.0}], series)      # before first rate
    with pytest.raises(FxRateUnavailable, match="stale"):
        fx.convert_trades([{"exit_time": "2024-03-20T00:00:00+00:00", "pnl": 1.0}], series)
    assert fx.convert_trades(trades, None)[0]["pnl"] == 10.0


def test_chunk_cache_limits_data_manager_calls():
    dm = FakeM1()
    svc = FxService(dm)
    for minute in range(0, 600, 7):
        svc.rate("EUR", "USD", pd.Timestamp("2024-03-13T00:00Z") + timedelta(minutes=minute))
    assert dm.calls <= 2


def test_no_hardcoded_one_fallback_in_fx_module():
    src = open(fx.__file__, encoding="utf-8").read()
    assert "or 1.0" not in src and "else 1.0" not in src
