import builtins
import sys
import threading
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.strategy_lab import rule_engine as engine  # noqa: E402
from strategy_lab.research import backtest, discovery  # noqa: E402
from strategy_lab.research.jobs import JobCancelled  # noqa: E402
from strategy_lab.research.ledger import ResearchLedger  # noqa: E402


def make_frames(n=4000, seed=7, freq="15min", start="2024-01-01"):
    """Random walk with a weak AR(1) trend in the exact column format backtest.load_bid_ask returns."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 0.0012, n)
    ret = np.zeros(n)
    for i in range(1, n):
        ret[i] = 0.15 * ret[i - 1] + noise[i] + 0.00002
    close = 2000.0 * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[2000.0], close[:-1]])
    span = np.abs(rng.normal(0, 0.0008, n)) * close
    high, low = np.maximum(open_, close) + span, np.minimum(open_, close) - span
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    bid = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                        "volume": rng.integers(50, 500, n).astype(float)}, index=idx)
    ask = bid.copy()
    for c in ("open", "high", "low", "close"):
        ask[c] = bid[c] + 0.30
    return bid, ask, {"source": "synthetic", "sources": [{"sha256": "abc"}]}


@pytest.fixture(scope="module")
def frames():
    return make_frames()


def loader_for(frames):
    calls = []

    def load(symbol, tf, start, end):
        calls.append((symbol, tf))
        return frames

    load.calls = calls
    return load


class FakeCtx:
    def __init__(self, cancel_after_checks=None):
        self.checks, self.cancel_after, self.progress_calls = 0, cancel_after_checks, []
        self.lock = threading.Lock()

    @property
    def cancelled(self):
        return self.cancel_after is not None and self.checks > self.cancel_after

    def check(self):
        with self.lock:
            self.checks += 1
        if self.cancelled:
            raise JobCancelled()

    def progress(self, fraction, message=""):
        self.progress_calls.append((fraction, message))


def config(**over):
    cfg = {"families": ["trend_following", "breakout", "mean_reversion"], "symbols": ["XAUUSD"],
           "timeframes": ["M15"], "start": "2024-01-01", "end": "2024-02-12",
           "budget": {"max_candidates": 6, "max_minutes": 5, "max_workers": 2},
           "criteria": {"min_trades": 5, "min_profit_factor": 1.0, "max_drawdown_pct": 60, "min_bars": 300},
           "capital": 10000, "commission_per_lot": 0.0, "oos_fraction": 0.3}
    cfg.update(over)
    return cfg


# ------------------------------------------------------------------ families / generation
def test_family_catalogue_complete():
    ids = {f["id"] for f in discovery.FAMILIES}
    assert {"trend_following", "breakout", "volatility_expansion", "volatility_contraction", "mean_reversion",
            "vwap_pullback", "session_breakout", "liquidity_sweep_proxy", "price_action_reversal", "time_of_day",
            "regime_filtered_momentum"} <= ids
    for f in discovery.FAMILIES:
        assert f["name"] and f["description"] and isinstance(f["default"], bool) and f["mutations"]
    assert "PROXY" in discovery.family_by_id("liquidity_sweep_proxy")["description"]


@pytest.mark.parametrize("tf", ["M5", "H1"])
def test_all_family_specs_and_every_mutation_value_valid(tf):
    for fam in discovery.FAMILIES:
        base = discovery.base_spec(fam)
        base["timeframe"] = tf
        assert engine.validate_spec(base) == [], fam["id"]
        for key, values in fam["mutations"].items():
            for v in values:
                spec = discovery.apply_mutations(base, {key: v})
                assert engine.validate_spec(spec) == [], (fam["id"], key, v)


def test_generation_deterministic_unique_capped_traceable():
    a = discovery.generate_hypotheses(None, ["XAUUSD", "EURUSD"], ["M15", "H1"], 40, seed=3)
    b = discovery.generate_hypotheses(None, ["XAUUSD", "EURUSD"], ["M15", "H1"], 40, seed=3)
    assert [h["hypothesis_id"] for h in a] == [h["hypothesis_id"] for h in b]
    wide = [discovery.generate_hypotheses(None, ["XAUUSD"], ["M15"], 60, seed=s) for s in (3, 4)]
    assert [h["hypothesis_id"] for h in wide[0]] != [h["hypothesis_id"] for h in wide[1]]
    assert len(a) == 40 and len({h["hypothesis_id"] for h in a}) == 40
    assert len(discovery.generate_hypotheses(None, ["XAUUSD"], ["M15"], 5, seed=1)) == 5
    # round robin: first pass touches every family/symbol/timeframe slot
    first = {(h["family"], h["symbol"], h["timeframe"]) for h in a}
    assert len(first) == 40 and len({f for f, _, _ in first}) == 11  # 4 markets, 11 families, markets rotate the start family
    for h in a:
        assert len(h["hypothesis_id"]) == 16 and engine.validate_spec(h["spec"]) == []
        assert h["spec"]["symbol"] == h["symbol"] and h["spec"]["timeframe"] == h["timeframe"]
        fam = discovery.family_by_id(h["family"])
        assert h["parent"] and set(h["mutations"]) <= set(fam["mutations"])
        for k, v in h["mutations"].items():
            assert v in fam["mutations"][k]
        rebuilt = discovery.apply_mutations(
            {**discovery.base_spec(fam), "symbol": h["symbol"], "timeframe": h["timeframe"]}, h["mutations"])
        assert discovery.hypothesis_id(rebuilt, h["symbol"], h["timeframe"]) == h["hypothesis_id"]
    assert a[0]["mutations"] == {}  # baseline first


def test_generation_stops_when_space_exhausted():
    fam = {"id": "tiny", "name": "x", "description": "x", "default": False, "base_template": "donchian_breakout",
           "mutations": {"channel": [20, 40]}}
    out = discovery.generate_hypotheses([fam], ["XAUUSD"], ["H1"], 99, seed=0)
    assert len(out) == 2  # baseline (channel 20) duplicates the first mutation -> deduplicated


# ------------------------------------------------------------------ scoring
def test_score_weights_configurable_and_monotonic():
    comps = {k: 0.5 for k in discovery.DEFAULT_WEIGHTS}
    base = discovery.score(comps)
    assert base == pytest.approx(0.5)
    better = dict(comps, oos_performance=0.9)
    assert discovery.score(better) > base
    assert discovery.score(dict(comps, drawdown=0.1)) < base
    only_oos = {k: 0.0 for k in discovery.DEFAULT_WEIGHTS} | {"oos_performance": 1.0}
    assert discovery.score(only_oos, {"oos_performance": 0.0}) < discovery.score(only_oos)
    assert discovery.score({"risk_adjusted_return": 1.0}, {k: 0.0 for k in discovery.DEFAULT_WEIGHTS}
                           | {"risk_adjusted_return": 3.0}) == pytest.approx(1.0)
    assert discovery.score({k: 7.0 for k in discovery.DEFAULT_WEIGHTS}) == pytest.approx(1.0)  # clipped
    assert discovery.score({}, {k: 0.0 for k in discovery.DEFAULT_WEIGHTS}) == 0.0


# ------------------------------------------------------------------ run
def test_bounded_run_records_every_hypothesis(tmp_path, frames):
    ledger = ResearchLedger(tmp_path / "l.sqlite3")
    load = loader_for(frames)
    ctx = FakeCtx()
    out = discovery.run_discovery(ctx, config(), load, ledger)
    assert out["tested"] == 6 == ledger.summary()["total"]
    assert out["candidates"] + out["rejected"] + out["errors"] == out["tested"]
    assert len(load.calls) == 1  # frames loaded once per symbol/tf
    assert set(out["weights"]) == set(discovery.DEFAULT_WEIGHTS)
    assert "DSR" in out["summary_nl"] and "Valideren" in out["summary_nl"] and "HISTORICAL_OOS" in out["summary_nl"] and "forward" in out["summary_nl"]
    assert ctx.progress_calls and ctx.progress_calls[-1][0] == 1.0
    rows = ledger.list(kind="DISCOVERY", limit=50)
    assert len(rows) == 6
    for r in rows:
        full = ledger.get(r["id"])
        assert full["spec_sha256"] and full["dataset_sha256"] and full["hypothesis_id"]
        assert full["data_requirements"] == ["Dukascopy M1 Bid/Ask OHLC"]
        assert full["cost_model"]["stress"]["spread_multiplier"] == 1.5
        assert full["statistics"]["n_trials_in_run"] == 6 + 0
        assert [s["stage"] for s in full["statistics"]["stages"]][:3] == [1, 2, 3]
        if full["status"] == "REJECTED":
            assert full["rejection_reason"]
        if full["status"] != "ERROR":
            assert full["code_sha256"] and full["metrics"]["trades"] >= 0
    top = out["top"]
    assert len(top) <= 10 and [t["score"] for t in top if t["status"] == "CANDIDATE"] == sorted(
        [t["score"] for t in top if t["status"] == "CANDIDATE"], reverse=True)


def test_rejection_reasons_and_oos_only_for_survivors(tmp_path, frames):
    ledger = ResearchLedger(tmp_path / "l.sqlite3")
    cfg = config(criteria={"min_trades": 100000, "min_profit_factor": 1.0, "max_drawdown_pct": 60, "min_bars": 300})
    out = discovery.run_discovery(FakeCtx(), cfg, loader_for(frames), ledger)
    assert out["candidates"] == 0 and out["rejected"] == out["tested"]
    for r in ledger.list(limit=50):
        assert r["rejection_reason"].startswith("te weinig trades (") and "< 100000" in r["rejection_reason"]
        assert r["oos_metrics"] is None  # holdout untouched for in-sample failures


def test_candidate_decision_requires_oos_and_cost_stress(tmp_path, frames, monkeypatch):
    real = backtest.run_rules_backtest
    ledger = ResearchLedger(tmp_path / "l.sqlite3")
    cfg = config(budget={"max_candidates": 4, "max_minutes": 5, "max_workers": 1},
                 criteria={"min_trades": 1, "min_profit_factor": 0.0, "max_drawdown_pct": 100, "min_bars": 300})

    def profitable_in_sample_losing_oos(spec, bid, ask, params=None, **kw):
        out = real(spec, bid, ask, params, **kw)
        for t in out["trades"]:
            in_sample = pd.Timestamp(t["entry_time"]) < pd.Timestamp("2024-02-01", tz="UTC")
            stressed = kw.get("spread_multiplier", 1.0) != 1.0
            t["pnl"] = abs(t["pnl"]) + 1 if in_sample and not stressed else -abs(t["pnl"]) - 1
        return out

    monkeypatch.setattr(backtest, "run_rules_backtest", profitable_in_sample_losing_oos)
    out = discovery.run_discovery(FakeCtx(), cfg, loader_for(frames), ledger)
    reasons = {r["rejection_reason"] for r in ledger.list(limit=50)}
    assert out["candidates"] == 0
    assert any(x and x.startswith("kostengevoelig") for x in reasons)

    def all_profitable_except_oos(spec, bid, ask, params=None, **kw):
        out = real(spec, bid, ask, params, **kw)
        for t in out["trades"]:
            late = pd.Timestamp(t["entry_time"]) >= pd.Timestamp("2024-01-30 04:00", tz="UTC")
            t["pnl"] = -abs(t["pnl"]) - 1 if late else abs(t["pnl"]) + 1
        return out

    monkeypatch.setattr(backtest, "run_rules_backtest", all_profitable_except_oos)
    ledger2 = ResearchLedger(tmp_path / "l2.sqlite3")
    out2 = discovery.run_discovery(FakeCtx(), cfg, loader_for(frames), ledger2)
    rows = ledger2.list(limit=50)
    assert len(rows) == out2["tested"]
    assert "OOS verliesgevend" in {r["rejection_reason"] for r in rows}
    assert out2["candidates"] == 0


def test_candidate_status_when_everything_passes(tmp_path, frames, monkeypatch):
    real = backtest.run_rules_backtest

    def all_profitable(spec, bid, ask, params=None, **kw):
        out = real(spec, bid, ask, params, **kw)
        for t in out["trades"]:
            t["pnl"] = abs(t["pnl"]) + 1.0
        return out

    monkeypatch.setattr(backtest, "run_rules_backtest", all_profitable)
    ledger = ResearchLedger(tmp_path / "l.sqlite3")
    cfg = config(budget={"max_candidates": 3, "max_minutes": 5, "max_workers": 1},
                 criteria={"min_trades": 1, "min_profit_factor": 0.0, "max_drawdown_pct": 100, "min_bars": 300})
    out = discovery.run_discovery(FakeCtx(), cfg, loader_for(frames), ledger)
    cands = [r for r in ledger.list(status="CANDIDATE", limit=50)]
    assert out["candidates"] == len(cands) >= 1
    full = ledger.get(cands[0]["id"])
    assert full["score"] is not None and 0 <= full["score"] <= 1
    assert full["oos_metrics"]["net_profit"] > 0
    assert set(full["statistics"]["components"]) == set(discovery.DEFAULT_WEIGHTS)
    assert full["statistics"]["stages"][-1]["stage"] == 9


def test_budget_max_candidates_respected(tmp_path, frames):
    ledger = ResearchLedger(tmp_path / "l.sqlite3")
    cfg = config(budget={"max_candidates": 2, "max_minutes": 5, "max_workers": 2})
    out = discovery.run_discovery(FakeCtx(), cfg, loader_for(frames), ledger)
    assert out["tested"] == 2 == ledger.summary()["total"]
    assert out["stopped_reason"] == "max_candidates bereikt"


def test_time_budget_stops_launching(tmp_path, frames):
    ledger = ResearchLedger(tmp_path / "l.sqlite3")
    cfg = config(budget={"max_candidates": 6, "max_minutes": 0, "max_workers": 1})
    out = discovery.run_discovery(FakeCtx(), cfg, loader_for(frames), ledger)
    assert out["tested"] == 0 and "tijdbudget" in out["stopped_reason"]


def test_ram_limit_pauses_new_work(tmp_path, frames, monkeypatch):
    pytest.importorskip("psutil")
    ledger = ResearchLedger(tmp_path / "l.sqlite3")
    cfg = config(budget={"max_candidates": 3, "max_minutes": 5, "max_workers": 1, "max_ram_mb": 1})
    out = discovery.run_discovery(FakeCtx(), cfg, loader_for(frames), ledger)
    assert out["tested"] == 0 and out["stopped_reason"].startswith("geheugenlimiet") and out["ram_pauses"] >= 1


def test_cancellation_stops_early_and_records_everything(tmp_path, frames):
    ledger = ResearchLedger(tmp_path / "l.sqlite3")
    cfg = config(budget={"max_candidates": 6, "max_minutes": 5, "max_workers": 1})
    out = discovery.run_discovery(FakeCtx(cancel_after_checks=12), cfg, loader_for(frames), ledger)
    assert out["stopped_reason"] == "geannuleerd"
    assert out["tested"] < 6
    assert ledger.summary()["total"] == out["tested"]


def test_failing_hypothesis_recorded_as_error(tmp_path, frames, monkeypatch):
    real = backtest.run_rules_backtest
    state = {"n": 0}

    def flaky(spec, bid, ask, params=None, **kw):
        state["n"] += 1
        if "breakout" in spec["name"]:
            raise RuntimeError("boom in backtest")
        return real(spec, bid, ask, params, **kw)

    monkeypatch.setattr(backtest, "run_rules_backtest", flaky)
    ledger = ResearchLedger(tmp_path / "l.sqlite3")
    cfg = config(families=["breakout", "trend_following"], budget={"max_candidates": 4, "max_minutes": 5,
                                                                    "max_workers": 1})
    out = discovery.run_discovery(FakeCtx(), cfg, loader_for(frames), ledger)
    assert out["errors"] == 2 and out["tested"] == 4 == ledger.summary()["total"]
    errs = ledger.list(status="ERROR", limit=10)
    assert len(errs) == 2 and all("boom in backtest" in e["rejection_reason"] and "stage 3" in e["rejection_reason"]
                                  for e in errs)
    assert all(e["family"] == "breakout" for e in errs)


def test_data_load_failure_recorded(tmp_path):
    ledger = ResearchLedger(tmp_path / "l.sqlite3")

    def bad(symbol, tf, start, end):
        raise OSError("geen data")

    out = discovery.run_discovery(FakeCtx(), config(budget={"max_candidates": 3, "max_minutes": 5,
                                                              "max_workers": 1}), bad, ledger)
    assert out["errors"] == 3 and ledger.summary()["errors"] == 3


def test_generated_code_never_executed(tmp_path, frames, monkeypatch):
    def no_exec(*a, **k):
        raise AssertionError("exec() must never be called on generated code")

    monkeypatch.setattr(builtins, "exec", no_exec)
    ledger = ResearchLedger(tmp_path / "l.sqlite3")
    out = discovery.run_discovery(FakeCtx(), config(budget={"max_candidates": 3, "max_minutes": 5,
                                                              "max_workers": 1}), loader_for(frames), ledger)
    assert out["tested"] == 3 and out["errors"] == 0
    assert all(ledger.get(r["id"])["code_sha256"] for r in ledger.list(limit=10))


def test_insufficient_data_rejected(tmp_path):
    ledger = ResearchLedger(tmp_path / "l.sqlite3")
    small = make_frames(n=250)
    out = discovery.run_discovery(FakeCtx(), config(budget={"max_candidates": 2, "max_minutes": 5, "max_workers": 1}),
                                  loader_for(small), ledger)
    assert out["rejected"] == 2
    assert all("onvoldoende data" in r["rejection_reason"] for r in ledger.list(limit=10))


# ------------------------------------------------------------------ explicit IS/OOS windows (historical OOS)
WIN = {"in_sample": {"start": "2020-01-01", "end": "2021-09-01"},
       "out_of_sample": {"start": "2021-09-01", "end": "2022-03-01"}}
LOOSE = {"min_trades": 1, "min_profit_factor": 0.0, "max_drawdown_pct": 100, "min_bars": 300}


@pytest.fixture(scope="module")
def long_frames():
    return make_frames(n=20000, freq="1h", start="2020-01-01")


def win_config(**over):
    cfg = config(timeframes=["H1"], criteria=LOOSE, budget={"max_candidates": 4, "max_minutes": 5, "max_workers": 1},
                 **WIN)
    cfg.pop("start"), cfg.pop("end")
    cfg.update(over)
    return cfg


def all_profitable_recorder(calls):
    real = backtest.run_rules_backtest

    def wrapper(spec, bid, ask, params=None, **kw):
        out = real(spec, bid, ask, params, **kw)
        for t in out["trades"]:
            t["pnl"] = abs(t["pnl"]) + 1.0
        calls.append({"spec": spec, "params": params, "kw": kw, "first": bid.index[0], "last": bid.index[-1],
                      "trades": out["trades"]})
        return out

    return wrapper


def test_explicit_windows_respected_and_oos_uses_frozen_spec(tmp_path, long_frames, monkeypatch):
    calls: list = []
    monkeypatch.setattr(backtest, "run_rules_backtest", all_profitable_recorder(calls))
    ledger = ResearchLedger(tmp_path / "l.sqlite3")
    out = discovery.run_discovery(FakeCtx(), win_config(), loader_for(long_frames), ledger)
    assert out["candidates"] >= 1
    is_end = oos_start = pd.Timestamp("2021-09-01", tz="UTC")
    oos_calls = [c for c in calls if c["kw"].get("trade_from") is not None]
    is_calls = [c for c in calls if c["kw"].get("trade_from") is None]
    assert oos_calls and is_calls
    assert len(oos_calls) >= out["candidates"]
    for c in oos_calls:
        assert pd.Timestamp(c["kw"]["trade_from"]) == oos_start
        assert c["first"] < oos_start  # warm-up prefix before the OOS start
        assert c["first"] >= oos_start - pd.Timedelta(days=discovery.WARMUP_DAYS)
        assert c["last"] < pd.Timestamp("2022-03-01", tz="UTC")
        assert c["params"] is None  # frozen: no re-optimisation
        assert all(pd.Timestamp(t["entry_time"]) >= oos_start for t in c["trades"])  # no IS trade counted
    for c in is_calls:
        assert c["last"] < is_end
        assert all(pd.Timestamp(t["exit_time"]) <= is_end for t in c["trades"])  # no IS trade after IS end
        assert c["first"] >= pd.Timestamp("2020-01-31", tz="UTC")  # first available + 30 warm-up days
    for r in ledger.list(status="CANDIDATE", limit=10):
        full = ledger.get(r["id"])
        assert any(discovery._canon(c["spec"]) == discovery._canon(full["spec"]) for c in oos_calls)
        assert full["statistics"]["validation"] == "HISTORICAL_OOS"
        assert full["statistics"]["periods"]["out_of_sample"]["start"].startswith("2021-09-01")
    p = out["periods"][0]
    assert p["sufficient"] and p["in_sample"]["start"].startswith("2020-01-31")


def test_insufficient_data_symbol_rejected_not_crash(tmp_path, long_frames):
    short = make_frames(n=24 * 100, freq="1h", start="2021-12-01")  # ~100 days, no usable IS
    empty = tuple(f.iloc[0:0] if hasattr(f, "iloc") else f for f in long_frames)

    def load(symbol, tf, start, end):
        return {"XAUUSD": long_frames, "BTCUSD": short, "DE40": empty}[symbol]

    ledger = ResearchLedger(tmp_path / "l.sqlite3")
    cfg = win_config(symbols=["XAUUSD", "BTCUSD", "DE40"], budget={"max_candidates": 9, "max_minutes": 5,
                                                                   "max_workers": 1})
    out = discovery.run_discovery(FakeCtx(), cfg, load, ledger)
    assert out["tested"] == 9 and out["errors"] == 0
    for sym in ("BTCUSD", "DE40"):
        rows = [r for r in ledger.list(limit=50) if r["symbol"] == sym]
        assert len(rows) == 3
        assert all(r["status"] == "REJECTED" and r["rejection_reason"].startswith(f"onvoldoende data voor {sym}")
                   for r in rows)
    by = {row["symbol"]: row for row in out["by_symbol"]}
    assert set(by) == {"XAUUSD", "BTCUSD", "DE40"} and all(row["tested"] == 3 for row in by.values())
    assert by["BTCUSD"]["candidates"] == 0
    periods = {p["symbol"]: p for p in out["periods"]}
    assert periods["XAUUSD"]["sufficient"] and not periods["BTCUSD"]["sufficient"]


def test_frames_loaded_once_per_symbol_timeframe(tmp_path, long_frames):
    load = loader_for(long_frames)
    ledger = ResearchLedger(tmp_path / "l.sqlite3")
    cfg = win_config(symbols=["XAUUSD", "EURUSD"], timeframes=["M15", "H1"],
                     budget={"max_candidates": 16, "max_minutes": 5, "max_workers": 2})
    out = discovery.run_discovery(FakeCtx(), cfg, load, ledger)
    assert out["tested"] == 16
    assert sorted(load.calls) == sorted({("XAUUSD", "M15"), ("XAUUSD", "H1"), ("EURUSD", "M15"), ("EURUSD", "H1")})


def test_load_window_covers_warmup_to_oos_end(tmp_path, long_frames):
    seen = []

    def load(symbol, tf, start, end):
        seen.append((start, end))
        return long_frames

    discovery.run_discovery(FakeCtx(), win_config(budget={"max_candidates": 1, "max_minutes": 5, "max_workers": 1}),
                            load, ResearchLedger(tmp_path / "l.sqlite3"))
    assert seen == [("2019-12-02", "2022-03-01")]


def test_round_robin_gives_every_symbol_a_fair_share():
    symbols = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "US30", "US500", "USATECHIDXUSD", "DE40", "BTCUSD"]
    one = discovery.generate_hypotheses(None, symbols, ["M15"], 9, seed=1)
    assert sorted(h["symbol"] for h in one) == sorted(symbols)
    two = discovery.generate_hypotheses(None, symbols, ["M15", "H1"], 18, seed=1)
    assert len({(h["symbol"], h["timeframe"]) for h in two}) == 18
    many = discovery.generate_hypotheses(None, symbols, ["M15", "H1"], 100, seed=1)
    per = {s: sum(1 for h in many if h["symbol"] == s) for s in symbols}
    assert max(per.values()) - min(per.values()) <= 2


def test_top_has_capped_equity_and_context(tmp_path, long_frames, monkeypatch):
    monkeypatch.setattr(backtest, "run_rules_backtest", all_profitable_recorder([]))
    cfg = win_config(budget={"max_candidates": 30, "max_minutes": 5, "max_workers": 2}, families=None)
    out = discovery.run_discovery(FakeCtx(), cfg, loader_for(long_frames), ResearchLedger(tmp_path / "l.sqlite3"))
    top = out["top"]
    assert 0 < len(top) <= 25
    cands = [t for t in top if t["status"] == "CANDIDATE"]
    assert cands and top[: len(cands)] == cands  # candidates first
    for t in top:
        for key in ("hypothesis_id", "family", "symbol", "timeframe", "status", "rejection_reason", "score",
                    "metrics", "oos_metrics", "spec", "equity_is", "equity_oos"):
            assert key in t
        assert 0 < len(t["equity_is"]) <= 300
        assert set(t["equity_is"][0]) == {"time", "equity"}
    assert all(0 < len(t["equity_oos"]) <= 300 for t in cands)
    assert "HISTORICAL_OOS" in out["summary_nl"] and "Valideren" in out["summary_nl"] and "DSR" in out["summary_nl"]
    assert out["validation"] == "HISTORICAL_OOS"


def test_downsample_caps_and_keeps_endpoints():
    eq = [{"time": str(i), "equity": float(i)} for i in range(5000)]
    ds = discovery._downsample(eq)
    assert len(ds) <= 300 and ds[0]["time"] == "0" and ds[-1]["time"] == "4999"
    assert discovery._downsample(eq[:10]) == eq[:10] and discovery._downsample(None) == []


def test_windows_validation():
    with pytest.raises(ValueError):
        discovery._parse_windows({"in_sample": {"start": "2020-01-01", "end": "2021-01-01"}})
    with pytest.raises(ValueError):
        discovery._parse_windows({"in_sample": {"start": "2020-01-01", "end": "2021-06-01"},
                                  "out_of_sample": {"start": "2021-01-01", "end": "2022-01-01"}})
    assert discovery._parse_windows({}) is None
