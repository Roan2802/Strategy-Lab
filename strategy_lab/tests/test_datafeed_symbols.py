from datetime import date

from strategy_lab import dukascopy_datafeed as feed


def test_lab_symbols_map_to_dukascopy_instruments():
    assert "/USA500IDXUSD/2026/09/12/" in feed.feed_url("US500", date(2026, 10, 12), "BID")
    assert "/DEUIDXEUR/" in feed.tick_url("DE40", date(2026, 10, 12), 9)
    assert "/XAUUSD/" in feed.feed_url("XAUUSD", date(2026, 10, 12), "ASK")


def test_index_aliases_have_a_point_factor():
    provider = feed.DatafeedProvider(reference=lambda s: 6000.0, today=lambda: date(2026, 11, 9))
    for symbol in ("US500", "DE40", "US30"):
        assert provider._preflight(symbol, date(2026, 10, 12)) == 6000.0


def test_provider_symbol_is_idempotent_for_provider_names():
    from strategy_lab.daily_data import provider_symbol
    for lab, provider in (("DE40", "DEUIDXEUR"), ("US500", "USA500IDXUSD"), ("US30", "USA30IDXUSD")):
        assert provider_symbol(lab) == provider == provider_symbol(provider)
