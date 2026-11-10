from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
import lzma
import sqlite3
import struct
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from strategy_lab.daily_data import DailyPartitionStore
from strategy_lab.daily_sync import (
    ChainProvider, GatedProvider, Inbox, MarketCalendar, SymbolPlan, SyncConfig, load_sync_config, reference_close,
    run_sync, synchronize,
)
from strategy_lab.data_manager import DataError, DataManager
from strategy_lab.dukascopy_datafeed import (
    MIN_INTERVAL_SECONDS, POINT_FACTORS, USER_AGENT, DatafeedProvider, DatafeedTicksProvider, FeedClient, FetchState,
    RateLimiter, Response, backoff_delay, decode_bi5, decode_ticks, feed_url, tick_url, urllib_transport,
)

DAY = date(2026, 11, 6)
NOW = datetime(2026, 11, 9, 8, tzinfo=timezone.utc)
OPEN_CALENDAR = MarketCalendar(ZoneInfo("UTC"), {day: [["10:00", "10:05"]] for day in range(7)}, {})
CLOSED_CALENDAR = MarketCalendar(ZoneInfo("UTC"), {day: [] for day in range(7)}, {})
SESSION_START = 10 * 3600


def bi5(records, *, fmt=lzma.FORMAT_ALONE):
    """Records are (seconds, open, close, low, high, volume) in the provider's integer scale."""
    return lzma.compress(b"".join(struct.pack(">5if", *record) for record in records), format=fmt)


def session_records(base=4_085_000, volume=1.5, count=5):
    return [(SESSION_START + 60 * i, base + i, base + i + 5, base + i - 3, base + i + 9, volume) for i in range(count)]


class FakeFeed:
    def __init__(self, files=None, default=None):
        self.files, self.default, self.urls = files if files is not None else {}, default, []

    def __call__(self, url):
        self.urls.append(url)
        reply = self.files.get(url, self.default)
        assert reply is not None, f"unexpected request {url}"
        return reply(url) if callable(reply) else reply


def ok(body):
    return Response(200, {}, body)


def feed_for(symbol, day, bid, ask):
    return {feed_url(symbol, day, "BID"): ok(bid), feed_url(symbol, day, "ASK"): ok(ask)}


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def provider(feed, *, reference=4000.0, factors=None, clock=None):
    clock = clock or Clock()
    return DatafeedProvider(transport=feed, limiter=RateLimiter(clock=clock.monotonic, sleep=clock.sleep),
                            reference=lambda symbol: reference, today=lambda: NOW.date(),
                            factors=factors or {"XAUUSD": 1000, "EURUSD": 100000}, clock=lambda: NOW)


@pytest.fixture
def store(tmp_path):
    return DailyPartitionStore(tmp_path / "daily.sqlite")


def settle(store, feed_provider, *, calendar=OPEN_CALENDAR, first=DAY, now=NOW, max_days=5, symbol="XAUUSD"):
    return synchronize(store, feed_provider, symbol, calendar, first_day=first, now=now, max_days=max_days,
                       clock=lambda: now)


def test_url_uses_zero_based_month():
    assert feed_url("XAUUSD", date(2026, 11, 6), "BID").endswith("/datafeed/XAUUSD/2026/10/06/BID_candles_min_1.bi5")
    assert feed_url("EURUSD", date(2026, 1, 31), "ASK").endswith("/EURUSD/2026/00/31/ASK_candles_min_1.bi5")


def test_decode_scaling_column_order_and_utc_index():
    frame = decode_bi5(bi5([(SESSION_START, 4085470, 4085500, 4085400, 4085600, 1.5)]), DAY, 1000)
    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
    row = frame.iloc[0]
    assert (row.open, row.high, row.low, row.close, row.volume) == (4085.47, 4085.6, 4085.4, 4085.5, 1.5)
    assert frame.index[0] == pd.Timestamp("2026-11-06T10:00Z")
    assert decode_bi5(bi5([(0, 112046, 112045, 112039, 112050, 72.82)]), DAY, 100000).iloc[0].open == 1.12046


def test_decode_accepts_xz_container_and_rejects_garbage():
    assert len(decode_bi5(bi5(session_records(), fmt=lzma.FORMAT_XZ), DAY, 1000)) == 5
    for body in (b"not lzma at all", lzma.compress(b"123", format=lzma.FORMAT_ALONE)):
        with pytest.raises(DataError):
            decode_bi5(body, DAY, 1000)
    with pytest.raises(DataError, match="non-increasing"):
        decode_bi5(bi5([(60, 1, 1, 1, 1, 0.0), (60, 1, 1, 1, 1, 0.0)]), DAY, 1000)
    with pytest.raises(DataError, match="off-minute"):
        decode_bi5(bi5([(61, 1, 1, 1, 1, 0.0)]), DAY, 1000)
    assert decode_bi5(b"", DAY, 1000).empty


def test_all_nine_symbols_have_a_point_factor():
    assert set(POINT_FACTORS) == {"XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "USA30IDXUSD", "USA500IDXUSD",
                                  "USATECHIDXUSD", "DEUIDXEUR", "BTCUSD"}


def test_fetch_returns_ingest_ready_frames_and_sends_honest_requests(store):
    feed = FakeFeed(feed_for("XAUUSD", DAY, bi5(session_records()), bi5(session_records(base=4_085_200))))
    reports = settle(store, provider(feed), max_days=1)
    assert reports[0]["status"] == "UP TO DATE" and reports[0]["rows"] == 5
    assert feed.urls == [feed_url("XAUUSD", DAY, "BID"), feed_url("XAUUSD", DAY, "ASK")]
    frame, _ = store.read("XAUUSD", DAY, DAY, as_of=NOW)
    assert frame.bid_open.iloc[0] == 4085.0 and frame.ask_open.iloc[0] == 4085.2


def test_user_agent_is_fixed_and_honest(monkeypatch):
    seen = {}

    class Reply:
        status, headers, closed = 200, {}, False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=-1):
            return b""

    class Opener:
        def open(self, request, timeout):
            seen.update(request.header_items(), timeout=timeout)
            return Reply()

    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: Opener())
    urllib_transport("https://datafeed.dukascopy.com/x")
    assert seen["User-agent"] == USER_AGENT == "DukascopyStrategyLab/1.0 (local research)" and seen["timeout"] > 0


def test_price_scale_outside_thirty_percent_rejects_day_without_ingest(store):
    wrong = bi5(session_records(base=40_850_000))
    feed = FakeFeed(feed_for("XAUUSD", DAY, wrong, wrong))
    report = settle(store, provider(feed), max_days=1)[0]
    assert report["status"] == "ERROR" and "wrong point factor" in json.dumps(report)
    assert not store.read("XAUUSD", DAY, DAY, as_of=NOW)[1]


def test_missing_reference_close_blocks_ingest_instead_of_guessing(store):
    feed = FakeFeed(default=ok(bi5(session_records())))
    report = settle(store, provider(feed, reference=None), max_days=1)[0]
    assert report["status"] == "ERROR" and not feed.urls


def chain(feed, *, clock=None, reference=4000.0, ticks=True):
    clock = clock or Clock()
    client = FeedClient(feed, RateLimiter(clock=clock.monotonic, sleep=clock.sleep), lambda: NOW)
    options = dict(reference=lambda symbol: reference, today=lambda: NOW.date(), clock=lambda: NOW, client=client,
                   factors={"XAUUSD": 1000, "EURUSD": 100000})
    methods = [("datafeed", DatafeedProvider(**options))]
    if ticks:
        methods.append(("datafeed_ticks", DatafeedTicksProvider(**options)))
    return ChainProvider(methods)


def test_http_429_is_delayed_honours_retry_after_and_holds_every_symbol(store):
    feed = FakeFeed(default=Response(429, {"retry-after": "7200"}, b""))
    feed_provider = chain(feed)
    report = settle(store, feed_provider)[0]
    assert report["status"] == "DELAYED" and "retry_after_s=7200" in report["detail"]
    assert feed.urls == [feed_url("XAUUSD", DAY, "BID")]
    assert not store.read("XAUUSD", DAY, DAY, as_of=NOW)[1]
    held = settle(store, feed_provider, symbol="EURUSD")[0]
    assert held["status"] == "DELAYED" and "no request sent" in held["detail"] and len(feed.urls) == 1
    later = NOW + timedelta(minutes=90)
    assert "no request sent" in settle(store, chain(feed), now=later)[0]["detail"] and len(feed.urls) == 1
    settle(store, chain(feed), now=NOW + timedelta(hours=2, minutes=1))
    assert len(feed.urls) == 2


def test_http_429_without_retry_after_backs_off_for_the_default_period(store):
    feed = FakeFeed(default=Response(429, {}, b""))
    assert "retry_after_s=3600" in settle(store, chain(feed))[0]["detail"]
    assert FetchState(store.path).hold("XAUUSD", DAY, NOW + timedelta(minutes=59)) is not None
    assert FetchState(store.path).hold("XAUUSD", DAY, NOW + timedelta(minutes=61)) is None


def test_backoff_schedule_doubles_to_a_six_hour_ceiling():
    assert [backoff_delay(n) for n in range(1, 7)] == [
        timedelta(minutes=2), timedelta(minutes=5), timedelta(minutes=15), timedelta(minutes=30), timedelta(hours=1),
        timedelta(hours=1)]


def test_repeated_missing_day_backs_off_further_and_success_clears_it(store):
    files = {}
    feed = FakeFeed(files, default=Response(404, {}, b""))
    state = FetchState(store.path)
    clock = NOW
    for expected_delay in (2, 5, 15):
        feed_provider = chain(feed, ticks=False)  # every scheduled run is a new process with fresh file budgets
        report = settle(store, feed_provider, now=clock, max_days=1)[0]
        assert report["status"] == "DELAYED" and "datafeed: not available" in report["detail"]
        assert state.hold("XAUUSD", DAY, clock + timedelta(minutes=expected_delay - 1)) is not None
        assert state.hold("XAUUSD", DAY, clock + timedelta(minutes=expected_delay + 1)) is None
        requests = len(feed.urls)
        assert "no request sent" in settle(store, feed_provider, now=clock + timedelta(minutes=expected_delay - 1))[0]["detail"]
        assert len(feed.urls) == requests
        clock += timedelta(minutes=expected_delay + 1)
    files.update(feed_for("XAUUSD", DAY, bi5(session_records()), bi5(session_records())))
    done = settle(store, chain(feed, ticks=False), now=clock, max_days=1)[0]
    assert done["status"] == "UP TO DATE" and done["method"] == "datafeed"
    assert state.hold("XAUUSD", DAY, clock) is None
    assert state.methods("XAUUSD", DAY)[0]["method"] == "datafeed"


def test_waf_challenge_stops_the_whole_run_with_evidence_and_cools_down(store, tmp_path):
    challenge = Response(202, {"x-amzn-waf-action": "challenge"}, b"")
    feed = FakeFeed(default=challenge)
    plans = [SymbolPlan("XAUUSD", DAY, 0, OPEN_CALENDAR), SymbolPlan("EURUSD", DAY, 0, OPEN_CALENDAR)]

    def run(at):
        return run_sync(store, SyncConfig(plans, chain(feed), 5, [], 3, None, tmp_path), manual=True,
                        clock=lambda: at)

    result = run(NOW)
    blocked = [item for item in result["intake"] if item["status"] == "BLOCKED"]
    assert result["status"] == "RETRY" and len(blocked) == 1 and len(result["intake"]) == 1
    assert blocked[0]["evidence"]["headers"] == {"x-amzn-waf-action": "challenge"} and blocked[0]["evidence"]["status"] == 202
    assert len(feed.urls) == 1
    assert run(NOW + timedelta(minutes=30))["intake"][0]["status"] == "BLOCKED" and len(feed.urls) == 1
    assert run(NOW + timedelta(hours=7))["intake"][0]["status"] == "BLOCKED" and len(feed.urls) == 2


@pytest.mark.parametrize("reply", [Response(403, {}, b""), Response(200, {"x-amzn-waf-action": "captcha"}, b"x")])
def test_forbidden_or_waf_header_on_any_status_is_blocked(store, reply):
    feed = FakeFeed(default=reply)
    assert settle(store, chain(feed))[0]["status"] == "BLOCKED"
    assert not store.read("XAUUSD", DAY, DAY, as_of=NOW)[1]


def test_404_on_open_day_is_delayed_not_closed_and_blocks_later_days(store):
    feed = FakeFeed(default=Response(404, {}, b""))
    reports = settle(store, provider(feed), max_days=3)
    assert [r["status"] for r in reports] == ["DELAYED"] and len(feed.urls) == 1
    assert store.settled_days("XAUUSD", DAY) == set()


def test_empty_file_and_missing_ask_are_not_complete(store):
    feed = FakeFeed({feed_url("XAUUSD", DAY, "BID"): ok(bi5(session_records())),
                     feed_url("XAUUSD", DAY, "ASK"): Response(404, {}, b"")})
    assert settle(store, provider(feed), max_days=1)[0]["status"] == "DELAYED"
    feed = FakeFeed(default=ok(b""))
    assert settle(store, provider(feed), max_days=1)[0]["status"] == "DELAYED"


def test_closed_day_per_calendar_is_a_closure_without_any_request(store):
    feed = FakeFeed()
    reports = settle(store, provider(feed), calendar=CLOSED_CALENDAR, max_days=2)
    assert [r["status"] for r in reports] == ["MARKET CLOSED", "MARKET CLOSED"] and not feed.urls


def test_server_error_and_network_failure_are_delayed(store):
    assert settle(store, provider(FakeFeed(default=Response(503, {}, b""))))[0]["status"] == "DELAYED"

    def broken(url):
        from strategy_lab.dukascopy_datafeed import DatafeedDelayed
        raise DatafeedDelayed("Dukascopy datafeed unreachable: TimeoutError")

    assert settle(store, provider(broken))[0]["status"] == "DELAYED"


def test_today_and_future_days_are_never_requested():
    feed = FakeFeed()
    feed_provider = provider(feed)
    for day in (NOW.date(), NOW.date() + timedelta(days=1)):
        with pytest.raises(DataError, match="completed UTC day"):
            feed_provider.fetch("XAUUSD", day)
    assert not feed.urls


def test_request_budget_is_two_files_per_symbol_day():
    feed = FakeFeed(default=Response(404, {}, b""))
    feed_provider = provider(feed)
    feed_provider._files[("XAUUSD", DAY)] = 2
    with pytest.raises(DataError, match="budget"):
        feed_provider.fetch("XAUUSD", DAY)
    assert not feed.urls


def test_rate_limiter_spaces_requests_at_least_one_second():
    clock = Clock()
    limiter = RateLimiter(0.0, clock=clock.monotonic, sleep=clock.sleep)
    assert limiter.interval == MIN_INTERVAL_SECONDS == 1.0
    limiter.wait()
    clock.now += 0.25
    limiter.wait()
    limiter.wait()
    assert clock.sleeps == pytest.approx([0.75, 1.0])


def test_provider_sleeps_between_the_two_files_of_a_day(store):
    clock = Clock()
    feed = FakeFeed(feed_for("XAUUSD", DAY, bi5(session_records()), bi5(session_records())))
    settle(store, provider(feed, clock=clock), max_days=1)
    assert clock.sleeps == [1.0]


def test_flat_zero_volume_minutes_outside_session_are_dropped_inside_session_kept(store):
    placeholders = [(0, 4085000, 4085000, 4085000, 4085000, 0.0), (SESSION_START - 60, 1, 1, 1, 1, 0.0)]
    flat_in_session = [(SESSION_START + 120, 4085002, 4085002, 4085002, 4085002, 0.0)]
    records = placeholders[:1] + session_records()[:2] + flat_in_session + session_records()[3:]
    feed = FakeFeed(feed_for("XAUUSD", DAY, bi5(records), bi5(records)))
    report = settle(store, provider(feed), max_days=1)[0]
    assert report["status"] == "UP TO DATE" and report["rows"] == 5
    frame, _ = store.read("XAUUSD", DAY, DAY, as_of=NOW)
    assert frame.bid_volume.tolist() == [1.5, 1.5, 0.0, 1.5, 1.5]


def test_ticks_outside_session_are_not_hidden_and_missing_minutes_are_not_invented(store):
    stray = session_records()[:5] + [(SESSION_START + 600, 4085000, 4085005, 4084997, 4085009, 3.0)]
    feed = FakeFeed(feed_for("XAUUSD", DAY, bi5(stray), bi5(stray)))
    report = settle(store, provider(feed), max_days=1)[0]
    assert report["status"] == "ERROR" and "outside configured session" in report["detail"]
    short = session_records()[:4]
    feed = FakeFeed(feed_for("XAUUSD", date(2026, 11, 7), bi5(short), bi5(short)))
    report = settle(store, provider(feed), first=date(2026, 11, 7), max_days=1)[0]
    assert report["status"] == "INCOMPLETE" and report["missing_bid"] == 1


def test_rerun_is_idempotent_and_never_requests_a_settled_day_again(store):
    feed = FakeFeed(feed_for("XAUUSD", DAY, bi5(session_records()), bi5(session_records())),
                    default=Response(404, {}, b""))
    feed_provider = provider(feed)
    assert settle(store, feed_provider, max_days=1)[0]["status"] == "UP TO DATE"
    again = settle(store, feed_provider)
    assert [r["day"] for r in again] == ["2026-11-07"] and again[0]["status"] == "DELAYED"
    assert feed.urls == [feed_url("XAUUSD", DAY, "BID"), feed_url("XAUUSD", DAY, "ASK"),
                         feed_url("XAUUSD", date(2026, 11, 7), "BID")]
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT COUNT(*) FROM daily_partitions WHERE day='2026-11-06'").fetchone()[0] == 1


def write_history(source, day="2026-11-05"):
    """Original-style CSVs (EET wall time) ending on `day`, closing at 4000.5 / 4000.7."""
    source.mkdir(parents=True, exist_ok=True)
    for side, close in (("Bid", 4000.5), ("Ask", 4000.7)):
        rows = [f"{day.replace('-', '.')} 12:{i:02d}:00,{close},{close + 1},{close - 1},{close},1" for i in range(5)]
        (source / f"XAUUSD_1 Min_{side}_2026.01.01_{day.replace('-', '.')}.csv").write_text(
            "Time (EET),Open,High,Low,Close,Volume\n" + "\n".join(rows) + "\n", encoding="utf-8")


def test_integration_run_sync_and_data_manager_reads_one_continuous_series(tmp_path):
    source = tmp_path / "source"
    write_history(source)
    store = DailyPartitionStore(tmp_path / "daily.sqlite")
    reference = lambda symbol: reference_close(store, source, symbol, NOW)
    assert reference("XAUUSD") == 4000.5
    days = [date(2026, 11, 6), date(2026, 11, 7)]
    files = {}
    for index, day in enumerate(days):
        files.update(feed_for("XAUUSD", day, bi5(session_records(base=4_001_000 + 1000 * index)),
                              bi5(session_records(base=4_001_200 + 1000 * index))))
    feed = FakeFeed(files, default=Response(404, {}, b""))
    feed_provider = provider(feed)
    feed_provider.reference = reference
    config = SyncConfig([SymbolPlan("XAUUSD", date(2026, 11, 6), 0, OPEN_CALENDAR)], feed_provider, 5, [], 3, None, source)
    result = run_sync(store, config, manual=True, clock=lambda: NOW)
    assert [(r["day"], r["status"]) for r in result["intake"]] == [
        ("2026-11-06", "UP TO DATE"), ("2026-11-07", "UP TO DATE"), ("2026-11-08", "DELAYED")]
    assert result["status"] == "RETRY"
    assert reference("XAUUSD") != 4000.5
    manager = DataManager(source, tmp_path / "cache", store.path)
    frame, manifest = manager.candles("XAUUSD", "M1", end="2027-01-01")
    assert frame.index.is_monotonic_increasing and not frame.index.has_duplicates
    assert frame.index[0] == pd.Timestamp("2026-11-05T10:00Z")
    assert pd.Timestamp("2026-11-06T10:00Z") in frame.index and pd.Timestamp("2026-11-07T10:04Z") in frame.index
    assert frame.loc["2026-11-05T10:04Z", "bid_close"] == 4000.5 and frame.loc["2026-11-06T10:00Z", "bid_open"] == 4001.0
    assert manifest["daily_partitions"]


def tick_file(minutes):
    """minutes: {minute_of_hour: [(second, ask, bid), ...]} in the provider's integer scale."""
    records = [((minute * 60 + second) * 1000, ask, bid, 1.0, 2.0)
               for minute, ticks in sorted(minutes.items()) for second, ask, bid in ticks]
    return lzma.compress(b"".join(struct.pack(">3i2f", *record) for record in records), format=lzma.FORMAT_ALONE)


TICKS = {0: [(1, 4_085_300, 4_085_100), (30, 4_085_700, 4_085_500), (59, 4_085_400, 4_085_200)],
         1: [(5, 4_085_450, 4_085_250)], 3: [(10, 4_085_500, 4_085_300), (40, 4_085_200, 4_085_000)],
         4: [(0, 4_085_350, 4_085_150)]}


def hourly(symbol, day, body):
    return {tick_url(symbol, day, 10): ok(body)}


def test_tick_url_and_decoding():
    assert tick_url("XAUUSD", date(2026, 11, 6), 7).endswith("/XAUUSD/2026/10/06/07h_ticks.bi5")
    frame = decode_ticks(tick_file({0: [(1, 4_085_300, 4_085_100)]}), DAY, 10, 1000)
    assert frame.iloc[0].ask == 4085.3 and frame.iloc[0].bid == 4085.1
    assert frame.index[0] == pd.Timestamp("2026-11-06T10:00:01Z")
    assert decode_ticks(b"", DAY, 10, 1000).empty
    with pytest.raises(DataError):
        decode_ticks(lzma.compress(struct.pack(">3i2f", 3_600_000, 1, 1, 1.0, 1.0), format=lzma.FORMAT_ALONE), DAY, 10, 1000)


def test_missing_candles_fall_back_to_hourly_ticks_and_record_the_method(store):
    files = {feed_url("XAUUSD", DAY, "BID"): Response(404, {}, b""), **hourly("XAUUSD", DAY, tick_file(TICKS))}
    feed = FakeFeed(files)
    clock = Clock()
    report = settle(store, chain(feed, clock=clock), max_days=1)[0]
    assert report["status"] == "UP TO DATE" and report["method"] == "datafeed_ticks"
    assert feed.urls == [feed_url("XAUUSD", DAY, "BID"), tick_url("XAUUSD", DAY, 10)]
    assert report["method_detail"] == {"files": 1, "ticks": 7, "filled_flat_minutes": 1}
    frame, _ = store.read("XAUUSD", DAY, DAY, as_of=NOW)
    assert frame.bid_open.tolist() == [4085.1, 4085.25, 4085.25, 4085.3, 4085.15]
    assert (frame.bid_high.iloc[0], frame.bid_low.iloc[0], frame.bid_close.iloc[0]) == (4085.5, 4085.1, 4085.2)
    assert (frame.ask_high.iloc[0], frame.ask_close.iloc[0]) == (4085.7, 4085.4)
    assert frame.bid_volume.tolist() == [3.0, 1.0, 0.0, 2.0, 1.0] and frame.ask_volume.tolist() == frame.bid_volume.tolist()
    assert frame.bid_close.iloc[2] == 4085.25 == frame.bid_open.iloc[2]
    assert FetchState(store.path).methods("XAUUSD", DAY)[0]["method"] == "datafeed_ticks"
    assert clock.sleeps == [1.0]


def test_ticks_are_only_requested_for_session_hours_and_stay_within_budget(store):
    calendar = MarketCalendar(ZoneInfo("UTC"), {day: [["10:00", "10:05"], ["12:00", "12:02"]] for day in range(7)}, {})
    feed = FakeFeed({feed_url("XAUUSD", DAY, "BID"): Response(404, {}, b""), tick_url("XAUUSD", DAY, 10): ok(tick_file(TICKS)),
                     tick_url("XAUUSD", DAY, 12): Response(404, {}, b"")})
    report = settle(store, chain(feed), calendar=calendar, max_days=1)[0]
    assert report["status"] == "DELAYED" and "datafeed_ticks: not available" in report["detail"]
    assert [u for u in feed.urls if "ticks" in u] == [tick_url("XAUUSD", DAY, 10), tick_url("XAUUSD", DAY, 12)]
    with pytest.raises(DataError, match="budget of 24"):
        client = FeedClient(FakeFeed(default=ok(b"")), RateLimiter(clock=Clock().monotonic, sleep=Clock().sleep), lambda: NOW)
        for hour in range(25):
            client.get(tick_url("XAUUSD", DAY, hour % 24), ("XAUUSD", DAY, "ticks"), 24, 1000)


def test_429_during_candles_does_not_fall_back_to_ticks(store):
    feed = FakeFeed(default=Response(429, {"retry-after": "60"}, b""))
    assert settle(store, chain(feed))[0]["status"] == "DELAYED" and len(feed.urls) == 1


def test_tick_prices_are_scale_checked_too(store):
    wrong = {0: [(1, 40_853_000, 40_851_000)]}
    feed = FakeFeed({feed_url("XAUUSD", DAY, "BID"): Response(404, {}, b""), **hourly("XAUUSD", DAY, tick_file(wrong))})
    assert settle(store, chain(feed), max_days=1)[0]["status"] == "ERROR"


def test_jforex_is_skipped_without_credentials_and_used_with_them(store):
    class Bridge:
        def __init__(self):
            self.calls = 0

        def fetch(self, symbol, day):
            self.calls += 1
            frame = pd.DataFrame({"open": 4085.0, "high": 4086.0, "low": 4084.0, "close": 4085.5, "volume": 1.0},
                                 index=pd.date_range("2026-11-06T10:00Z", periods=5, freq="min"))
            return frame, frame + 0.2

    bridge, allowed = Bridge(), [False]
    feed = FakeFeed(default=Response(404, {}, b""))
    gated = GatedProvider(bridge, lambda: allowed[0], "no jforex_credentials.local.json")
    one = chain(feed, ticks=False)
    one.methods.append(("jforex", gated))
    report = settle(store, one, max_days=1)[0]
    assert report["status"] == "DELAYED" and "jforex: skipped (no jforex_credentials.local.json)" in report["detail"]
    assert bridge.calls == 0
    allowed[0] = True
    report = settle(store, one, now=NOW + timedelta(hours=1), max_days=1)[0]
    assert report["status"] == "UP TO DATE" and report["method"] == "jforex" and bridge.calls == 1


def test_inbox_is_the_last_resort_in_the_chain(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    stamps = [f"2026.11.06 10:0{i}:00" for i in range(5)] + ["2026.11.07 10:00:00"]
    for side, shift in (("Bid", 0.0), ("Ask", 0.2)):
        rows = [f"{t},{4085 + shift},{4086 + shift},{4084 + shift},{4085.5 + shift},5" for t in stamps]
        (inbox / f"XAUUSD_1 Min_{side}_2026.11.06_2026.11.07.csv").write_text(
            "Time (UTC),Open,High,Low,Close,Volume\n" + "\n".join(rows) + "\n", encoding="utf-8")
    store = DailyPartitionStore(tmp_path / "daily.sqlite")
    feed = FakeFeed(default=Response(404, {}, b""))
    config = SyncConfig([SymbolPlan("XAUUSD", DAY, 0, OPEN_CALENDAR)], chain(feed, ticks=False), 5, [], 3,
                        Inbox(inbox, tmp_path / "source"), tmp_path / "source", True)
    result = run_sync(store, config, manual=True, clock=lambda: NOW)
    statuses = [(r["day"], r["status"]) for r in result["intake"]]
    assert statuses[0] == ("2026-11-06", "DELAYED") and ("2026-11-06", "UP TO DATE") in statuses
    assert store.read("XAUUSD", DAY, DAY, as_of=NOW)[0].shape[0] == 5
    assert [e["status"] for e in result["inbox"]] == ["PROCESSED"] or result["inbox"][0]["status"] == "KEPT"


def sync_config(tmp_path, **extra):
    path = tmp_path / f"c{len(list(tmp_path.glob('c*.json')))}.json"
    path.write_text(json.dumps({
        "symbols": {"XAUUSD": {
            "first_day": "2026-11-06", "update_at": "01:30", "timezone": "UTC",
            "weekly": {str(day): [["10:00", "10:05"]] for day in range(7)}}}, **extra}), encoding="utf-8")
    return load_sync_config(path)


def test_load_sync_config_provider_selection(tmp_path):
    default = sync_config(tmp_path)
    assert isinstance(default.provider, ChainProvider) and default.provider.names == ["datafeed"]
    assert default.inbox is None and default.fetch
    both = sync_config(tmp_path, provider="datafeed", jforex_command=["x"], inbox_directory="in")
    assert both.provider.names == ["datafeed"] and both.inbox is None
    legacy = sync_config(tmp_path, jforex_command=["cmd"], export_directory="export")
    assert legacy.provider.command == ["cmd"] and legacy.inbox is None and legacy.fetch
    assert sync_config(tmp_path, export_directory="export").provider.command is None
    only_inbox = sync_config(tmp_path, inbox_directory="in", jforex_command=["cmd"])
    assert only_inbox.inbox is not None and not only_inbox.fetch
    assert not sync_config(tmp_path, provider="inbox", inbox_directory="in").fetch
    with pytest.raises(DataError, match="providers must be"):
        sync_config(tmp_path, provider="ftp")
    with pytest.raises(DataError, match="providers must be"):
        sync_config(tmp_path, providers=["datafeed", "datafeed"])
    with pytest.raises(DataError, match="inbox_directory"):
        sync_config(tmp_path, provider="inbox")
    with pytest.raises(DataError, match="jforex_command"):
        sync_config(tmp_path, providers=["datafeed", "jforex"])


def test_providers_list_builds_the_ordered_fallback_chain(tmp_path):
    loaded = sync_config(tmp_path, providers=["datafeed", "datafeed_ticks", "jforex", "inbox"], jforex_command=["cmd"],
                         inbox_directory="in", export_directory="stage")
    assert loaded.provider.names == ["datafeed", "datafeed_ticks", "jforex"]
    assert isinstance(loaded.provider.methods[2][1], GatedProvider)
    assert loaded.inbox is not None and loaded.fetch
    clients = {id(method.client) for name, method in loaded.provider.methods if name != "jforex"}
    assert len(clients) == 1


def test_production_config_loads_with_the_full_fallback_chain():
    from strategy_lab.daily_sync import DEFAULT_SYNC_CONFIG

    loaded = load_sync_config(DEFAULT_SYNC_CONFIG)
    assert loaded.provider.names == ["datafeed", "datafeed_ticks", "jforex"] and loaded.inbox is not None
    assert loaded.max_days == 31 and len(loaded.plans) == 9
