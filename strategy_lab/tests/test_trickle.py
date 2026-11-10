import sqlite3
from datetime import date, datetime, timezone
from types import SimpleNamespace

from strategy_lab import trickle


def _setup(monkeypatch, tmp_path, outcomes):
    """outcomes: symbol -> list of reports returned by successive synchronize() calls."""
    plans = [SimpleNamespace(symbol=s, calendar=None, first_day=date(2026, 10, 1)) for s in outcomes]
    config = SimpleNamespace(fetch=True, plans=plans, provider=object(), source_directory=tmp_path,
                             portfolio_ids=[], max_forward_days=3)
    calls = []

    def fake_sync(store, provider, symbol, calendar, *, first_day, now, max_days, clock):
        assert max_days == 1
        calls.append(symbol)
        queue = outcomes[symbol]
        return [queue.pop(0)] if queue else []

    monkeypatch.setattr(trickle, "load_sync_config", lambda path: config)
    monkeypatch.setattr(trickle, "synchronize", fake_sync)
    monkeypatch.setattr(trickle, "_advance_forward", lambda store, config, now: [])
    t = trickle.Trickle(tmp_path / "cfg.json", tmp_path / "store.sqlite3",
                        clock=lambda: datetime(2026, 11, 9, 12, tzinfo=timezone.utc))
    return t, calls


def ok(symbol, day):
    return {"symbol": symbol, "day": day, "status": "UP TO DATE", "detail": "ingested"}


def test_one_day_per_step_round_robin(monkeypatch, tmp_path):
    t, calls = _setup(monkeypatch, tmp_path, {"XAUUSD": [ok("XAUUSD", "d1"), ok("XAUUSD", "d2")],
                                              "EURUSD": [ok("EURUSD", "d1")]})
    assert t.step()["report"]["symbol"] == "XAUUSD"
    assert t.step()["report"]["symbol"] == "EURUSD"
    assert t.step()["report"]["day"] == "d2"
    assert t.step()["state"] == "UP TO DATE"


def test_symbol_in_backoff_does_not_block_others(monkeypatch, tmp_path):
    held = {"symbol": "XAUUSD", "day": "d1", "status": "DELAYED",
            "detail": "Backoff until later (503); no request sent"}
    t, calls = _setup(monkeypatch, tmp_path, {"XAUUSD": [held, held], "EURUSD": [ok("EURUSD", "d1")]})
    r = t.step()
    assert r["state"] == "FILLING" and r["report"]["symbol"] == "EURUSD"
    assert t.step()["state"] == "BACKOFF"


def test_skips_while_daily_task_holds_the_lock(monkeypatch, tmp_path):
    t, calls = _setup(monkeypatch, tmp_path, {"XAUUSD": [ok("XAUUSD", "d1")]})
    lock = sqlite3.connect(tmp_path / "store.sync-lock.sqlite3")
    lock.execute("BEGIN EXCLUSIVE")
    try:
        assert t.step()["state"] == "BUSY" and calls == []
    finally:
        lock.close()
