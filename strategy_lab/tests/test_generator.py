"""Strategy generator: families, generation/diversity/dedup, level-1 gate, baselines and IQ components."""
import copy
import json
import random
from collections import Counter

import numpy as np
import pandas as pd
import pytest

from runtime.strategy_lab import rule_engine as engine
from strategy_lab.research import generator as gen
from strategy_lab.tests.test_rule_engine import synthetic_arrays

CANDIDATE_KEYS = {"id", "name", "symbol", "timeframe", "family", "group", "spec", "param_space", "blocks", "iq_sources",
                  "passport", "hypothesis_nl", "rules_nl", "complexity", "signature", "proxies_nl"}
CONFIG = {"symbols": ["XAUUSD", "US30", "DE40"], "timeframes": ["M15", "H1"], "families": "all", "max_candidates": 120}


def frames(n=3000, seed=7, step_ms=900_000, volume=True):
    arr = synthetic_arrays(n, seed=seed, step_ms=step_ms)
    idx = pd.to_datetime(arr["time"], unit="ms", utc=True)
    bid = pd.DataFrame({k: arr[k] for k in ("open", "high", "low", "close", "volume")}, index=idx)
    if not volume:
        bid["volume"] = 15.0
    ask = bid.copy()
    for k in ("open", "high", "low", "close"):
        ask[k] = bid[k] + 0.2
    return bid, ask


# ------------------------------------------------------------------ catalogue

def test_families_catalog_shape_and_groups():
    cat = gen.families_catalog()
    assert len(cat) >= 50
    assert {f["group"] for f in cat} == set(gen.GROUPS)
    assert len({f["id"] for f in cat}) == len(cat)
    dossiers = {d["dossier"] for d in gen.load_components()["dossiers"]}
    for f in cat:
        assert set(f) == {"id", "group", "label_nl", "description_nl", "mechanisms", "suitable_symbols", "iq_sources",
                          "default_timeframes"}
        assert f["suitable_symbols"] == "all" or isinstance(f["suitable_symbols"], list)
        assert set(f["iq_sources"]) <= dossiers
    counts = Counter(f["group"] for f in cat)
    assert counts["trend"] >= 7 and counts["breakout"] >= 10 and counts["mean_reversion"] >= 7
    assert counts["price_action"] >= 11 and counts["statistical"] >= 8 and counts["vwap_volume"] >= 6
    assert counts["hybrid"] >= 6


def test_families_are_distinct_mechanisms():
    """Different families must not collapse onto the same entry structure (they are not parameter tweaks)."""
    structures = {}
    for f in gen.FAMILIES:
        c = gen.build_candidate(f["id"], "XAUUSD", "M15", random.Random(1))
        structures.setdefault(json.dumps(gen._strip(c["spec"]["entry"]), sort_keys=True), []).append(f["id"])
    collisions = [v for v in structures.values() if len(v) > 1]
    assert not collisions, collisions


@pytest.mark.parametrize("fid", [f["id"] for f in gen.FAMILIES])
def test_every_family_yields_valid_evaluable_specs(fid):
    arr15 = synthetic_arrays(2500)
    arr60 = synthetic_arrays(2500, step_ms=3_600_000)
    for sym, tf, arr in (("XAUUSD", "M15", arr15), ("US30", "H1", arr60), ("DE40", "M15", arr15)):
        if not gen._suitable(gen._BY_ID[fid], sym, tf):
            continue
        for seed in range(4):
            c = gen.build_candidate(fid, sym, tf, random.Random(seed))
            assert CANDIDATE_KEYS <= set(c)
            assert engine.validate_spec(c["spec"]) == [], (sym, tf, seed)
            res = engine.evaluate(c["spec"], arr)
            assert res["n"] == len(arr["time"])
            assert c["group"] in gen.GROUPS and c["family"] == fid
            assert c["param_space"].keys() == {p["name"] for p in c["spec"]["params"]}
            assert c["spec"]["risk"]["percent"] == gen.RISK_PERCENT
            assert "scale_in" not in c["spec"]
            assert isinstance(c["complexity"], int) and c["complexity"] > 0
            assert c["signature"] == gen.signature(c)
            assert c["id"] == gen.candidate_id(c["spec"], fid)
            assert engine.describe(c["spec"]) == c["rules_nl"]


# ------------------------------------------------------------------ generate

def test_generate_deterministic_bounded_and_diverse():
    a = gen.generate(CONFIG, 7)
    b = gen.generate(CONFIG, 7)
    c = gen.generate(CONFIG, 8)
    assert [x["id"] for x in a] == [x["id"] for x in b]
    assert [x["id"] for x in a] != [x["id"] for x in c]
    assert len(a) == 120
    assert len({x["signature"] for x in a}) == len(a) and len({x["id"] for x in a}) == len(a)
    assert {x["group"] for x in a} == set(gen.GROUPS)
    per_fam = Counter(x["family"] for x in a)
    assert max(per_fam.values()) <= int(np.ceil(120 / len(gen.FAMILIES) * 1.5))
    assert len(per_fam) >= 50
    assert {x["symbol"] for x in a} == {"XAUUSD", "US30", "DE40"} and {x["timeframe"] for x in a} == {"M15", "H1"}
    assert all(engine.validate_spec(x["spec"]) == [] for x in a)
    assert not any("scale_in" in x["spec"] for x in a)


def test_generate_family_subset_and_no_diversity():
    out = gen.generate({**CONFIG, "families": ["breakout_orb", "pa_crt"], "max_candidates": 10, "diversity": False}, 1)
    assert {x["family"] for x in out} <= {"breakout_orb", "pa_crt"}
    assert len(out) == 10
    assert out[0]["family"] == "breakout_orb"


def test_complexity_is_not_rewarded_in_selection():
    """Generation order/selection does not depend on complexity: low and high complexity both appear."""
    cs = [x["complexity"] for x in gen.generate(CONFIG, 3)]
    assert min(cs) < np.median(cs) < max(cs)


def test_scale_in_only_with_explicit_opt_in_and_winners_only():
    out = gen.generate({**CONFIG, "allow_scale_in": True, "max_candidates": 200}, 5)
    scaled = [x for x in out if "scale_in" in x["spec"]]
    assert scaled, "opt-in should produce some winner-only scale-in candidates"
    for x in scaled:
        assert x["allow_scale_in"] is True
        assert set(x["spec"]["scale_in"]) == {"max_adds", "add_every_r", "add_size_fraction"}
        assert any(s["component"] == "lb_winner_adds" for s in x["iq_sources"])


def test_iq_inspired_candidates_are_labelled_research_variants():
    out = gen.generate(CONFIG, 7)
    inspired = [x for x in out if x["iq_sources"]]
    assert inspired
    for x in inspired:
        assert "eigen onderzoeksvariant" in x["hypothesis_nl"]
        assert "GEEN exacte reconstructie" in x["spec"]["description"]
        assert gen.CFD_NOTE in x["proxies_nl"]
        for s in x["iq_sources"]:
            assert s["provenance"] in ("SOURCE_RULE", "RESEARCH_VARIANT", "PROXY", "NOT_IMPLEMENTABLE")
            assert s["note_nl"].startswith("Geïnspireerd op")


def test_volume_and_vwap_candidates_carry_proxy_labels():
    out = gen.generate({**CONFIG, "families": ["volume_contraction", "volume_effort_result", "vwap_pullback"],
                        "max_candidates": 9}, 2)
    for x in out:
        text = " ".join(x["proxies_nl"])
        assert "PROXY" in text or "TWAP" in text
        assert "echt futuresvolume" not in text.replace("GEEN echt futuresvolume", "").replace(
            "geen echt futuresvolume", "")


# ------------------------------------------------------------------ dedup

def test_dedup_exact_near_and_existing():
    out = gen.generate({**CONFIG, "max_candidates": 20}, 4)
    dup = copy.deepcopy(out[3])
    kept, dropped = gen.dedup(out + [dup])
    assert len(kept) == 20 and len(dropped) == 1 and "signature" in dropped[0]["reason"]
    # near duplicate: same structure under another family label, every value within 10%
    near = copy.deepcopy(out[0])
    near["family"] = "relabelled"
    near["id"] = "x" * 16
    for p in near["spec"]["params"]:
        if p["type"] == "float":
            p["default"] = min(p["max"], p["default"] * 1.01)
    near["signature"] = gen.signature(near)
    kept, dropped = gen.dedup([out[0], near])
    assert len(kept) == 1 and dropped[0]["duplicate_of"] == out[0]["id"] and "bijna" in dropped[0]["reason"]
    kept, dropped = gen.dedup(out, existing_signatures={out[5]["signature"]})
    assert len(kept) == 19 and dropped[0]["id"] == out[5]["id"]


def test_signature_ignores_parameter_values_but_not_structure():
    c = gen.build_candidate("trend_ma_cross", "XAUUSD", "M15", random.Random(1))
    tweaked = copy.deepcopy(c)
    for p in tweaked["spec"]["params"]:
        p["default"] = p["min"]
    assert gen.signature(tweaked) == c["signature"]
    other_tf = copy.deepcopy(c)
    other_tf["spec"]["timeframe"] = "H1"
    assert gen.signature(other_tf) != c["signature"]
    assert gen.signature(c["spec"], "breakout_donchian") != c["signature"]


# ------------------------------------------------------------------ level 1

def _cand(fid="trend_ma_cross", seed=1, tf="M15"):
    return gen.build_candidate(fid, "XAUUSD", tf, random.Random(seed))


def test_level1_passes_clean_candidate_on_synthetic():
    bid, ask = frames()
    r = gen.level1_check(_cand(), bid, ask)
    assert r["ok"], r["reasons"]
    assert set(r["checks"]) == {"schema", "data_available", "indicators_defined", "no_lookahead", "reproducible",
                                "no_contradiction", "not_duplicate"}
    assert all(v["ok"] for v in r["checks"].values())


def test_level1_catches_injected_lookahead_feature(monkeypatch):
    real = engine.compute_indicator

    def leaky(name, a, period=None, **kw):
        if name == "leak_future_close":  # deliberately uses the NEXT bar: test-only feature
            c = np.asarray(a["close"], dtype=float)
            return np.concatenate((c[1:], [np.nan]))
        return real(name, a, period, **kw)

    monkeypatch.setattr(engine, "INDICATORS", engine.INDICATORS + ("leak_future_close",))
    monkeypatch.setattr(engine, "compute_indicator", leaky)
    cand = _cand()
    spec = cand["spec"]
    spec["entry"] = {"long": gen.cmp(gen.ind("leak_future_close", 1), ">", gen.CLOSE),
                     "short": gen.cmp(gen.ind("leak_future_close", 1), "<", gen.CLOSE)}
    cand["id"] = gen.candidate_id(spec, cand["family"])
    cand["signature"] = gen.signature(cand)
    assert engine.validate_spec(spec) == []
    bid, ask = frames()
    r = gen.level1_check(cand, bid, ask, run_backtest=False)
    assert not r["ok"]
    assert not r["checks"]["no_lookahead"]["ok"]
    assert "verschilt" in r["checks"]["no_lookahead"]["detail"]


def test_level1_catches_contradiction_and_degenerate_entry():
    cand = _cand()
    same = gen.cmp(gen.CLOSE, ">", gen.val(0))
    cand["spec"]["entry"] = {"long": same, "short": copy.deepcopy(same)}
    cand["spec"].pop("max_trades_per_day", None)
    cand["id"] = gen.candidate_id(cand["spec"], cand["family"])
    cand["signature"] = gen.signature(cand)
    bid, ask = frames()
    r = gen.level1_check(cand, bid, ask, run_backtest=False)
    detail = r["checks"]["no_contradiction"]["detail"]
    assert not r["checks"]["no_contradiction"]["ok"]
    assert "dezelfde bar" in detail and "identiek" in detail and "degeneratie" in detail


def test_level1_catches_duplicates_and_tampering():
    bid, ask = frames()
    cand = _cand()
    r = gen.level1_check(cand, bid, ask, existing_signatures={cand["signature"]}, run_backtest=False)
    assert not r["checks"]["not_duplicate"]["ok"]
    tampered = copy.deepcopy(cand)
    tampered["spec"]["direction"] = "long"
    r = gen.level1_check(tampered, bid, ask, run_backtest=False)
    assert not r["checks"]["not_duplicate"]["ok"]      # signature no longer matches the spec
    assert not r["checks"]["reproducible"]["ok"]       # id no longer matches the spec hash


def test_level1_rejects_scale_in_without_opt_in_and_invalid_spec():
    bid, ask = frames()
    cand = _cand()
    cand["spec"]["scale_in"] = {"max_adds": 1, "add_every_r": 1.0, "add_size_fraction": 0.5}
    r = gen.level1_check(cand, bid, ask, run_backtest=False)
    assert not r["checks"]["schema"]["ok"] and "allow_scale_in" in r["checks"]["schema"]["detail"]
    bad = _cand()
    bad["spec"]["stop"] = {"type": "nope"}
    r = gen.level1_check(bad, bid, ask)
    assert not r["ok"] and all(not v["ok"] for k, v in r["checks"].items())


def test_level1_flags_uninformative_volume_proxy():
    cand = gen.build_candidate("volume_expansion", "XAUUSD", "M15", random.Random(2))
    bid, ask = frames(volume=False)
    r = gen.level1_check(cand, bid, ask, run_backtest=False)
    assert not r["checks"]["data_available"]["ok"]
    assert "volume-proxy niet informatief" in r["checks"]["data_available"]["detail"]
    bid, ask = frames(volume=True)
    assert gen.level1_check(cand, bid, ask, run_backtest=False)["checks"]["data_available"]["ok"]


def test_level1_on_real_xauusd_slice():
    from strategy_lab.data_manager import DataManager
    from strategy_lab.research.backtest import load_bid_ask
    try:
        bid, ask, _ = load_bid_ask(DataManager(), "XAUUSD", "M15", "2019-01-01", "2019-03-01")
    except Exception as exc:
        pytest.skip(f"Dukascopy dataset unavailable: {exc}")
    ok = 0
    for fid in ("trend_ma_cross", "pa_crt", "mr_bollinger", "stat_hurst_regime", "breakout_asian_range"):
        r = gen.level1_check(gen.build_candidate(fid, "XAUUSD", "M15", random.Random(3)), bid, ask)
        assert r["checks"]["no_lookahead"]["ok"], (fid, r["reasons"])
        assert r["checks"]["reproducible"]["ok"], (fid, r["reasons"])
        ok += r["ok"]
    assert ok >= 4


# ------------------------------------------------------------------ baselines

def test_baselines_valid_and_random_uses_same_exits():
    cand = gen.build_candidate("breakout_donchian", "US30", "H1", random.Random(4))
    bl = gen.baselines("US30", "H1", exits_from=cand)
    assert [b["family"] for b in bl] == ["baseline_buy_hold", "baseline_random_entry", "baseline_ma_cross"]
    for b in bl:
        assert CANDIDATE_KEYS <= set(b) and b["group"] == "baseline"
        assert engine.validate_spec(b["spec"]) == []
    rnd = bl[1]["spec"]
    rs = engine.resolve(cand["spec"])
    assert rnd["stop"] == rs["stop"] and rnd.get("target") == rs.get("target")
    bid, ask = frames(step_ms=3_600_000)
    for b in bl:
        r = gen.level1_check(b, bid, ask, run_backtest=False)
        assert r["ok"], (b["family"], r["reasons"])
    default = gen.baselines("XAUUSD", "M15")
    assert default[1]["spec"]["stop"] == {"type": "atr", "mult": 2.0, "period": 14}


# ------------------------------------------------------------------ IQ components

def test_iq_components_cover_all_dossiers_with_valid_provenance():
    doc = gen.load_components()
    cat = json.loads((gen.COMPONENTS_PATH.parents[2] / "data_cache" / "library" / "catalogue.json")
                     .read_text(encoding="utf-8"))
    assert {d["dossier"] for d in doc["dossiers"]} == {r["id"] for r in cat}
    totals = Counter()
    for d in doc["dossiers"]:
        assert d["components"], d["dossier"]
        assert d["transfer_to_dukascopy_cfd_fx"] == "NEW_HYPOTHESIS"
        for c in d["components"]:
            assert c["provenance"] in doc["provenance_legend"]
            assert c["kind"] in ("entry", "exit", "filter", "risk", "context")
            if c["provenance"] in ("PROXY", "NOT_IMPLEMENTABLE"):
                assert c.get("reason_nl"), (d["dossier"], c["id"])
            totals[c["provenance"]] += 1
        assert d["counts"] == {k: sum(c["provenance"] == k for c in d["components"]) for k in d["counts"]}
    assert dict(totals) == {k: v for k, v in doc["totals"].items() if v}
    traders = " ".join(d["trader"] for d in doc["dossiers"])
    for name in ("Matteo", "Bruni", "Patrick Nill", "Candace", "Trader Math", "Hannes", "Imre", "Trader X", "Mike",
                 "Marco"):
        assert name in traders
    marco = next(d for d in doc["dossiers"] if d["dossier"] == "45_marco_loading_the_boat")
    assert any("allow_scale_in" in (c["engine_mapping"] or "") for c in marco["components"])
    assert "volledigheid" in doc["grade_note_nl"]


def test_all_family_iq_references_resolve():
    for f in gen.FAMILIES:
        for dossier, cid in f["iq"]:
            d, c = gen._component(dossier, cid)
            assert c["provenance"] != "NOT_IMPLEMENTABLE", (f["id"], cid)
