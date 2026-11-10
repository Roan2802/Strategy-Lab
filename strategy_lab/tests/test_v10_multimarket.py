"""V10 cross-market: currency conversion, sealed guard, frozen params, ledger accounting, no cherry-picking."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from strategy_lab.research import backtest, instruments, ledger as ledger_mod, multimarket, protocol

SPEC_PATH = Path(__file__).resolve().parents[1] / "data_cache" / "builder_specs" / "EmaRsiPullback.json"


class Ctx:
    def check(self): pass
    def progress(self, f, m=""): pass


class FakeDM:
    """Deterministic random-walk H1 Bid/Ask data to 2025-06, spread proportional to price."""
    def __init__(self):
        self.frames = {}
        idx = pd.date_range("2011-01-03", "2025-06-01", freq="1h", tz="UTC")
        idx = idx[idx.dayofweek < 5]
        base = {"XAUUSD": 1500.0, "EURUSD": 1.2, "DE40": 8000.0, "USDJPY": 100.0}
        for k, (sym, p0) in enumerate(base.items()):
            rng = np.random.default_rng(k + 1)
            close = p0 * np.exp(np.cumsum(rng.normal(0, 0.002, len(idx))))
            opn = np.concatenate([[p0], close[:-1]])
            hi, lo = np.maximum(opn, close) * 1.001, np.minimum(opn, close) * 0.999
            sp = close * 0.0001
            f = pd.DataFrame(index=idx)
            for side, off in (("bid", 0.0), ("ask", 1.0)):
                for c, v in (("open", opn), ("high", hi), ("low", lo), ("close", close)):
                    f[f"{side}_{c}"] = np.round(v + off * sp, 5)
                f[f"{side}_volume"] = 1.0
            self.frames[sym] = f

    def candles(self, symbol, timeframe, start=None, end=None):
        f = self.frames[symbol]
        if start:
            f = f[f.index >= pd.Timestamp(start, tz="UTC")]
        if end:
            f = f[f.index < pd.Timestamp(end, tz="UTC")]
        return f, {"symbol": symbol}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(instruments, "CACHE", tmp_path / "stats.json")
    return FakeDM(), ledger_mod.ResearchLedger(tmp_path / "l.sqlite3")


@pytest.fixture()
def strategy():
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    return {"kind": "builder", "label": "EmaRsiPullback", "spec": spec, "params": None}


def run(env, strategy, markets, **kw):
    dm, led = env
    kw.setdefault("stability", False)
    return multimarket.run_cross_market(Ctx(), dm, strategy, markets, ["H1"], ledger=led, **kw)


def test_conversion_series_is_causal_mid_and_inverse(env):
    dm, _ = env
    eur = instruments.conversion_series(dm, "DE40", "H1", None, None)
    jpy = instruments.conversion_series(dm, "USDJPY", "H1", None, None)
    f, g = dm.frames["EURUSD"], dm.frames["USDJPY"]
    assert np.allclose(eur.to_numpy(), ((f.bid_open + f.ask_open) / 2).to_numpy())  # bar OPEN, never close
    assert np.allclose(jpy.to_numpy(), (1.0 / ((g.bid_open + g.ask_open) / 2)).to_numpy())
    assert instruments.conversion_series(dm, "XAUUSD", "H1", None, None).empty  # USD quote: none needed
    assert instruments.conversion_info("USDCAD")["pair"] == "USDCAD"


def test_backtest_converts_pnl_with_rate_and_flags_unconverted(env, strategy):
    dm, _ = env
    spec = {**strategy["spec"], "symbol": "DE40", "timeframe": "H1"}
    bid, ask, _ = backtest.load_bid_ask(dm, "DE40", "H1", "2012-01-01", "2014-01-01")
    contract = instruments.contract_for("DE40", dm)
    conv = pd.Series(1.25, index=bid.index)
    raw = backtest.run_rules_backtest(spec, bid, ask, None, contract=contract)
    cv = backtest.run_rules_backtest(spec, bid, ask, None, contract=contract, conversion=conv)
    assert raw["diagnostics"]["currency_status"] == "QUOTE_CURRENCY_UNCONVERTED"
    assert cv["diagnostics"]["currency_status"] == "CONVERTED_CAUSAL"
    assert raw["trades"] and len(raw["trades"]) == len(cv["trades"])
    for t in cv["trades"]:  # net pnl = price move * qty * contract * rate
        d = 1 if t["side"] == "long" else -1
        assert t["pnl"] == pytest.approx((t["exit_price"] - t["entry_price"]) * d * t["qty"] * contract.contract_size * 1.25
                                         - 2 * t["qty"] * contract.commission_per_lot_side, rel=1e-6)
    assert sum(t["qty"] for t in cv["trades"]) < sum(t["qty"] for t in raw["trades"])  # USD risk -> smaller lots


def test_sealed_period_refused(env, strategy):
    with pytest.raises(protocol.SealedAccessError):
        run(env, strategy, ["XAUUSD"], end="2024-06-01")
    with pytest.raises(protocol.SealedAccessError):
        run(env, strategy, ["XAUUSD"], start="2024-02-01", end="2025-01-01")
    out = run(env, strategy, ["XAUUSD"])
    assert out["config"]["end"] == protocol.SEALED_START
    assert out["results"][0]["end"] <= protocol.SEALED_START


def test_frozen_params_never_changed(env, strategy, monkeypatch):
    seen = []
    orig = backtest.run_rules_backtest

    def spy(spec, b, a, params=None, **kw):
        seen.append(dict(params))
        return orig(spec, b, a, params, **kw)

    monkeypatch.setattr(backtest, "run_rules_backtest", spy)
    out = run(env, strategy, ["XAUUSD", "EURUSD"], stability=False)
    expect = out["config"]["params_frozen"]
    assert len(seen) == 4 and all(p == expect for p in seen)  # base + spread stress per cell, never re-optimised


def test_every_cell_is_a_ledger_trial(env, strategy):
    dm, led = env
    before = len(led.list(limit=1000))
    out = run(env, strategy, ["XAUUSD", "EURUSD", "DE40"])
    cells = [c for c in out["results"] if c["status"] == "OK"]
    assert len(cells) == 3 and len(out["ledger_ids"]) == 3
    assert len(led.list(limit=1000)) == before + 3
    assert led.n_trials(family=out["config"]["family"]) == 3
    rec = led.get(out["ledger_ids"][0])
    assert rec["statistics"]["multimarket"]["selection_experiment"] is True


def test_single_market_cannot_beat_broad_class(env, strategy):
    one = run(env, strategy, ["XAUUSD"])
    assert one["generalisation"]["class"] in ("MARKET_SPECIFIC", "NO_EDGE")
    cells = [{"symbol": s, "validation_status": "PASS", "edge_positive": True} for s in ("A", "B", "C", "D", "E")]
    comb = {"net_return_pct": 5.0}
    assert multimarket.classify(cells, comb, 5, 4.0)[0] == "BROAD_GENERALISATION"
    assert multimarket.classify(cells[:1], comb, 1, 1.0)[0] == "MARKET_SPECIFIC"  # cherry-picked best market
    assert multimarket.classify(cells[:3], comb, 3, 3.0)[0] == "PARTIAL_GENERALISATION"  # <4 markets never BROAD
    assert multimarket.classify(cells, comb, 5, 1.2)[0] == "MARKET_SPECIFIC"  # all markets move as one
    neg = [{**c, "edge_positive": False} for c in cells]
    assert multimarket.classify(neg, {"net_return_pct": -3}, 5, 0)[0] == "NO_EDGE"
    assert one["generalisation"]["selection"]["evidence_class"] == "EXPLORATORY_SELECTION"


def test_registry_marks_provenance_and_effective_markets(env):
    dm, _ = env
    r = instruments.spec_for("DE40", dm)
    assert r["fields"]["contract_size"]["status"] == "ASSUMED"
    assert r["fields"]["account_conversion"]["value"]["pair"] == "EURUSD"
    assert r["money_status"] == "PROVISIONAL"
    assert instruments.spec_for("XAUUSD", dm)["fields"]["data_range"]["status"] == "VERIFIED_FROM_DATA"
    assert multimarket.effective_markets(np.eye(4)) == pytest.approx(4.0)
    assert multimarket.effective_markets(np.ones((4, 4))) == pytest.approx(1.0)


def test_reoptimise_is_separate_new_hypothesis(env, strategy):
    out = run(env, strategy, ["XAUUSD"], reoptimise=True, reopt_candidates=3)
    assert out["config"]["family"].endswith("|reopt") and out["config"]["new_hypothesis"] is True
    assert out["generalisation"]["class"] == "NOT_COMPARABLE"
    assert "NEW_HYPOTHESIS" in out["results"][0]["flags"]
