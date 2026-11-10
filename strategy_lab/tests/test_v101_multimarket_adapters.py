"""V101: Jesse + plugin strategies through the multi-market runner (instrument binding, ledger, FX, sealed guard)."""
import hashlib

import numpy as np
import pandas as pd
import pytest

from strategy_lab.research import adapters, instruments, ledger as ledger_mod, multimarket, protocol


class Ctx:
    def check(self): pass
    def progress(self, f, m=""): pass


class FakeDM:
    """H1 Bid/Ask 2018-2024 with a deterministic EURUSD ramp so FX conversion is exactly checkable."""
    def __init__(self):
        idx = pd.date_range("2018-01-01", "2023-12-31", freq="1h", tz="UTC")
        idx = idx[idx.dayofweek < 5]
        base = {"XAUUSD": 1500.0, "EURUSD": 1.1, "DE40": 8000.0}
        self.frames = {}
        for k, (sym, p0) in enumerate(base.items()):
            close = p0 * (1 + 0.00001 * np.arange(len(idx))) if sym == "EURUSD" else p0 * np.ones(len(idx))
            f = pd.DataFrame(index=idx)
            for side, off in (("bid", 0.0), ("ask", p0 * 1e-4)):
                for c in ("open", "high", "low", "close"):
                    f[f"{side}_{c}"] = close + off
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
    monkeypatch.setattr(instruments, "BROKER_OVERRIDES", tmp_path / "broker.json")
    return FakeDM(), ledger_mod.ResearchLedger(tmp_path / "l.sqlite3"), tmp_path


GENERIC = "from jesse.strategies import Strategy\n\nclass Generic(Strategy):\n    def should_long(self):\n        return self.close > self.ema\n"
BOUND_SYMBOL = "class Gold:\n    def go(self):\n        return self.symbol == 'XAUUSD'\n"
BOUND_PRICE = "class P:\n    def should_long(self):\n        return self.price > 1800.5\n"


def jesse_dir(tmp_path, name, code):
    d = tmp_path / "src" / name
    d.mkdir(parents=True)
    (d / "__init__.py").write_text(code, encoding="utf-8")
    return d


def fake_runner_factory(calls):
    def make(kind, ref, symbol, timeframe, start, end, capital, check=None, **opts):
        class Ad:
            code_sha256, dataset_sha256 = "c" * 64, "d" * 64
            engine_nl = "fake"
            supported_cost_scenarios = {"baseline": {}}

            def merge(self, p):
                return {"fast": 5, **(p or {})}

            def fn(self, params, s, e, **cost):
                calls.append((symbol, timeframe, s, e))
                # 40 trades of +10 (quote ccy), fee 1; exits spread through 2022 so the EURUSD ramp changes
                days = pd.date_range("2022-03-01", periods=40, freq="3D", tz="UTC")
                return {"trades": [{"entry_time": (d - pd.Timedelta(hours=2)).isoformat(), "exit_time": d.isoformat(),
                                    "side": "long", "pnl": 10.0, "costs": 1.0, "qty": 1.0} for d in days]}
        return Ad()
    return make


def run(env, kind, name, markets, tmp_path, code=GENERIC, **kw):
    dm, led, _ = env
    d = jesse_dir(tmp_path, f"S{abs(hash(code)) % 9999}", code) if kind == "jesse" else None
    opts = {"strategies": [{"name": d.name, "sha256": hashlib.sha256(code.encode()).hexdigest(), "parameters": [
        {"name": "stop_distance", "type": "float", "default": 1.0}]}], "jesse_source_dir": d.parent} if d else {}
    strat = {"kind": kind, "label": d.name if d else name, "name": d.name if d else name, "params": None}
    calls = []
    out = multimarket.run_cross_market_adapter(Ctx(), dm, strat, markets, ["M15"], "2022-01-01", "2023-01-01",
                                               ledger=led, adapter_options=opts, min_days=200,
                                               make=fake_runner_factory(calls), **kw)
    return out, calls, led


# ------------------------------------------------------------------ static binding scan
def test_scan_flags_symbol_literals_and_price_constants_only():
    assert adapters.scan_source(GENERIC)["symbols"] == [] and adapters.scan_source(GENERIC)["price_constants"] == []
    assert adapters.scan_source(BOUND_SYMBOL)["symbols"][0]["value"] == "XAUUSD"
    assert adapters.scan_source(BOUND_PRICE)["price_constants"][0]["value"] == 1800.5
    # scale-free ratios, len() comparisons, 0/1 and docstrings are not instrument-bound
    ok = '"""XAUUSD notes"""\ndef f(self):\n    a = self.price * 1.01\n    return len(self.candles) > 50 and self.close > 0\n'
    res = adapters.scan_source(ok)
    assert res["symbols"] == [] and res["price_constants"] == []
    assert adapters.scan_source("x = 'BTC-USDT'")["symbols"][0]["value"] == "BTC-USDT"


def test_binding_restrictions(tmp_path):
    d = jesse_dir(tmp_path, "A", BOUND_SYMBOL)
    b = adapters.instrument_binding("jesse", "A", source_dir=d)
    assert b["restricted_to"] == ["XAUUSD"]
    assert adapters.market_block_reason(b, "XAUUSD") is None
    assert "INSTRUMENT_BOUND" in adapters.market_block_reason(b, "DE40")
    d = jesse_dir(tmp_path, "B", BOUND_PRICE)
    assert adapters.instrument_binding("jesse", "B", source_dir=d)["restricted_to"] == []  # origin unknown: all blocked
    assert adapters.instrument_binding("jesse", "B", source_dir=d, origin_symbol="ustec")["restricted_to"] == ["USATECHIDXUSD"]
    gold = adapters.instrument_binding("plugin", "gold_reaper")
    assert gold["restricted_to"] == ["XAUUSD"] and gold["source"] == "PLUGIN['symbols']"
    d = jesse_dir(tmp_path, "C", GENERIC)
    assert adapters.instrument_binding("jesse", "C", source_dir=d)["restricted_to"] is None


def test_real_labbreakout_source_is_not_symbol_bound_but_has_price_scale_params():
    from pathlib import Path
    root = Path(adapters.__file__).resolve().parents[2] / "runtime" / "strategies" / "LabBreakout"
    defs = [{"name": "stop_distance", "type": "float", "default": 1.0}, {"name": "ema_period", "type": "int", "default": 20},
            {"name": "max_bar_range", "type": "float", "default": 25.0}]
    b = adapters.instrument_binding("jesse", "LabBreakout", definitions=defs, source_dir=root)
    assert b["restricted_to"] is None
    assert {w["name"] for w in b["price_param_warnings"]} == {"stop_distance", "max_bar_range"}


# ------------------------------------------------------------------ runner
def test_jesse_three_markets_same_frozen_config_ledger_and_fx(env, tmp_path):
    out, calls, led = run(env, "jesse", None, ["XAUUSD", "EURUSD", "DE40"], tmp_path)
    assert [c["symbol"] for c in out["results"]] == ["XAUUSD", "EURUSD", "DE40"]
    assert len({(c["start"], c["end"]) for c in out["results"]}) == 1  # same period everywhere
    assert {c["key"]: c["params"] for c in out["results"]}["DE40|M15"] == {"fast": 5}  # frozen, never changed
    assert all(c["status"] == "OK" and c["risk_normalisation"] == "NOT_APPLICABLE_OWN_SIZING" for c in out["results"])
    de, gold = out["results"][2], out["results"][0]
    assert gold["currency_conversion"] == "ACCOUNT_CURRENCY_QUOTE"
    assert gold["metrics"]["net_profit"] == pytest.approx(400.0)  # USD quote: unchanged
    assert de["currency_conversion"] == "CONVERTED_CAUSAL_AT_EXIT"
    # DE40 is EUR quoted: each trade converted at the EURUSD rate known at ITS exit time, not one flat/current rate
    eur = instruments.conversion_series(env[0], "DE40", "M15", None, None)
    days = pd.date_range("2022-03-01", periods=40, freq="3D", tz="UTC")
    expected = sum(10.0 * instruments.rate_at(eur, d) for d in days)
    assert de["metrics"]["net_profit"] == pytest.approx(expected)
    assert expected != pytest.approx(400.0 * float(eur.iloc[-1]), rel=1e-4)  # not the latest rate
    # every market attempt is a ledger row in the multimarket family
    assert led.n_trials(family=out["config"]["family"]) == 3
    assert out["money_approval"]["money_approval_allowed"] is False
    assert out["generalisation"]["n_markets_tested"] == 3


def test_instrument_bound_jesse_reports_per_market_and_registers_attempts(env, tmp_path):
    out, calls, led = run(env, "jesse", None, ["XAUUSD", "EURUSD", "DE40"], tmp_path, code=BOUND_SYMBOL)
    st = {c["symbol"]: c for c in out["results"]}
    assert st["XAUUSD"]["status"] == "OK"
    for sym in ("EURUSD", "DE40"):
        assert st[sym]["status"] == "NOT_SUPPORTED" and st[sym]["validation_status"] == "INSTRUMENT_BOUND"
        assert "INSTRUMENT_BOUND" in st[sym]["reason"] and st[sym]["edge_positive"] is False
    assert [x[0] for x in calls] == ["XAUUSD"]  # bound markets never run
    assert led.n_trials(family=out["config"]["family"]) == 3  # attempts registered, incl. the not-run ones
    assert out["generalisation"]["instrument_bound_markets"] == ["DE40", "EURUSD"]
    assert out["generalisation"]["n_markets_tested"] == 1  # bound cells are not evidence


def test_all_bound_is_not_testable(env, tmp_path):
    out, _, _ = run(env, "jesse", None, ["EURUSD", "DE40"], tmp_path, code=BOUND_SYMBOL)
    assert out["generalisation"]["class"] == "NOT_TESTABLE"


def test_plugin_gold_reaper_xauusd_only_others_bound_and_timeframe_collapsed(env, tmp_path):
    out, calls, led = run(env, "plugin", "gold_reaper", ["XAUUSD", "EURUSD", "DE40"], tmp_path)
    st = {c["symbol"]: c for c in out["results"]}
    assert out["config"]["timeframes"] == ["M1"] and out["config"]["timeframes_requested"] == ["M15"]
    assert st["XAUUSD"]["status"] == "OK"
    assert st["EURUSD"]["validation_status"] == st["DE40"]["validation_status"] == "INSTRUMENT_BOUND"
    assert "PLUGIN['symbols']" in st["DE40"]["reason"]
    assert [c[0] for c in calls] == ["XAUUSD"]


def test_sealed_period_refused_and_reoptimise_and_risk_models_not_supported(env, tmp_path):
    dm, led, _ = env
    d = jesse_dir(tmp_path, "G", GENERIC)
    strat = {"kind": "jesse", "label": "G", "name": "G", "params": None}
    opts = {"strategies": [{"name": "G", "sha256": "x", "parameters": []}], "jesse_source_dir": d.parent}
    with pytest.raises(protocol.SealedAccessError):
        multimarket.run_cross_market(Ctx(), dm, strat, ["XAUUSD"], ["M15"], "2023-06-01", "2024-03-01", ledger=led,
                                     adapter_options=opts, make=None)
    with pytest.raises(ValueError, match="NOT_SUPPORTED"):
        multimarket.run_cross_market(Ctx(), dm, strat, ["XAUUSD"], ["M15"], ledger=led, reoptimise=True,
                                     adapter_options=opts)
    with pytest.raises(ValueError, match="NOT_SUPPORTED"):
        multimarket.run_cross_market(Ctx(), dm, strat, ["XAUUSD"], ["M15"], ledger=led, risk_model="pct_equity_atr",
                                     adapter_options=opts)


def test_api_accepts_jesse_and_plugin_kinds():
    from strategy_lab import multimarket_api as api
    assert api.StrategyRef(kind="jesse", name="LabBreakout").kind == "jesse"
    assert api.StrategyRef(kind="plugin", name="gold_reaper").kind == "plugin"
    ref = api.resolve_strategy(api.StrategyRef(kind="plugin", name="gold_reaper"))
    assert ref["kind"] == "plugin" and len(ref["code_sha256"]) == 64
    with pytest.raises(Exception):
        api.StrategyRef(kind="ea")
