"""v10.2 data recovery: API summary fields, superseded errors, verified calendars, originals untouched, live provider probe.
Fixture tests use mocked transport and a virtual clock; the live probe and the originals check read the real machine
(labelled OBSERVED) and skip when the provider or files are not available."""
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest

from strategy_lab.daily_sync import load_sync_config
from strategy_lab.dukascopy_datafeed import feed_url
from test_v10_supervisor import Env, env, isolate_default  # noqa: F401  (fixtures)

LAB = Path(__file__).resolve().parents[1]
LOCAL_CONFIG = LAB / "daily_sync.local.json"


def test_status_exposes_counts_scheduler_and_last_success_for_the_ui(env):
    env.feed.publish(date(2026, 11, 5))
    env.sync()
    status = env.status()
    assert status["counts"] == {"current": 0, "closed": 0, "delayed": 1, "total": 1}
    assert status["last_success_sync"] and status["last_success_sync"] == status["last_success"]
    assert status["scheduler_status"] in {"ACTIVE", "MISSING", "DISABLED", "STALE", "FAILED"}
    row = env.row()
    for key in ("last_candle", "last_check", "last_success", "lag_days", "next_attempt", "reason_nl"):
        assert key in row
    env.feed.publish_range(date(2026, 11, 6), date(2026, 11, 8))
    env.now += timedelta(minutes=5)
    env.sync()
    assert env.status()["counts"]["current"] == 1 and env.status()["counts"]["delayed"] == 0


def test_stale_error_older_than_a_later_success_does_not_stay_red(env):
    env.feed.publish(date(2026, 11, 5))
    env.sync()
    env.feed.fail = 503
    env.now += timedelta(minutes=5)
    env.sync()
    assert env.row()["status"] == "DELAYED"
    env.feed.fail = None
    env.feed.publish(date(2026, 11, 6))
    env.now += timedelta(hours=7)  # backoff over
    env.sync()  # 11-06 stored after the old failure; 11-07 not published yet
    row = env.row()
    assert row["status"] != "ERROR" and row["lag_days"] == 2


def test_real_config_verified_calendars_only_where_the_provider_publishes_matching_hours():
    plans = {p.symbol: p for p in load_sync_config(LOCAL_CONFIG).plans}
    verified = {s for s, p in plans.items() if p.calendar_source == "VERIFIED_PROVIDER"}
    assert len(verified) == 3 and not verified & {"XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "DE40", "DEUIDXEUR"}
    # DE40 winter close differs from the provider table (21:00 GMT vs derived 22:15 Berlin): stays derived
    assert next(p for s, p in plans.items() if s in {"DE40", "DEUIDXEUR"}).calendar_source == "DERIVED_FROM_DATA"
    raw = json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))["symbols"]
    for name in ("US30", "US500", "USTEC"):
        weekly = raw[name]["weekly"]
        assert weekly["0"] == [["00:00", "16:15"], ["18:00", "24:00"]] and weekly["4"] == [["00:00", "16:15"]]
        assert weekly["5"] == [] and weekly["6"] == [["18:00", "24:00"]]


def test_originals_unchanged_size_mtime_and_sample_hash():
    caches = sorted(LAB.glob("data_cache/*_M1_bidask_manifest.json"))
    checked = []
    for path in caches:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for side in ("bid_source", "ask_source"):
            entry = manifest.get(side) or {}
            original = Path(entry.get("path", ""))
            if original.is_file():
                stat = original.stat()
                assert (stat.st_size, stat.st_mtime_ns) == (entry["bytes"], entry["mtime_ns"]), original
                checked.append((stat.st_size, original, entry["sha256"]))
    if not checked:
        pytest.skip("original CSV files not present on this machine")
    size, original, sha = min(checked)
    digest = hashlib.sha256()
    with original.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    assert digest.hexdigest() == sha


def test_live_provider_probe_observed():
    """OBSERVED: one honest GET of a published old day file. Skips when the provider is unreachable or failing."""
    url = feed_url("EURUSD", date(2026, 11, 5), "BID")
    request = Request(url, headers={"User-Agent": "StrategyLab-DataCheck/10.2 (personal research; contact local user)"})
    try:
        with urlopen(request, timeout=20) as response:
            status, size = response.status, len(response.read())
    except URLError as exc:
        code = getattr(exc, "code", None)
        pytest.skip(f"provider not available right now: {code or exc.reason}")
    assert status == 200 and size > 0
