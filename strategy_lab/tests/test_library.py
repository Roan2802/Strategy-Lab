import hashlib
import json
from pathlib import Path

import pytest

from strategy_lab import library

SOURCE = Path(r"C:\Users\Roanv\Downloads\IQCapital_Strategie_Bibliotheek_AUDIT2_2026-11-05")
pytestmark = pytest.mark.skipif(not SOURCE.is_dir(), reason="IQ Capital source folder missing")


def _h(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def imported(tmp_path_factory):
    dest = tmp_path_factory.mktemp("lib")
    before = _h(SOURCE / "strategies_index.csv")
    summary = library.import_library(SOURCE, dest)
    cat = library.load_catalogue(dest / "catalogue.json")
    return dest, summary, cat, before


def test_counts_and_grades(imported):
    _, summary, cat, _ = imported
    assert summary["count"] == len(cat) == 45
    assert summary["errors"] == []
    # real data: A=7 (5x "A:" + 2x "A-"), B=27, C=11
    assert summary["grades"] == {"A": 7, "B": 27, "C": 11}


def test_translation_status_and_caveats(imported):
    for r in imported[2]:
        assert r["translation_status"] == "RESEARCH_VARIANT_REQUIRED"
        assert any("winstgevendheid" in c for c in r["caveats"])
        assert any("transcripts" in c for c in r["caveats"])
        assert r["instrument_transfer_note"]
        assert r["dukascopy_ohlc_suitability"] in {
            "OHLC_READY", "OHLC_PROXY_ONLY", "NEEDS_EXTERNAL_DATA", "UNKNOWN"}


def test_unknown_rules_and_sources_preserved(imported):
    _, _, cat, _ = imported
    for r in cat:
        raw = json.loads((SOURCE / "strategies" / r["id"] / "strategy.json").read_text(encoding="utf-8-sig"))
        assert r["unknown_rules"] == (raw.get("unknown") or [])
        urls = {s["url"] for s in r["sources"]}
        for u in raw.get("source_urls") or []:
            assert u in urls
    assert sum(1 for r in cat if r["unknown_rules"]) == 45
    assert sum(1 for r in cat if r["sources"]) >= 40


def test_priority_prototypes(imported):
    pri = [r for r in imported[2] if r["priority_prototype"]]
    assert len(pri) == 8
    assert {r["id"] for r in pri} == set(library.PRIORITY_IDS)


def test_search_filters(imported):
    cat = imported[2]
    assert {r["evidence_grade"] for r in library.search(cat, grade="A")} == {"A"}
    assert len(library.search(cat, grade="A")) == 7
    assert all("matteo" in r["trader"].lower() for r in library.search(cat, trader="Matteo"))
    ready = library.search(cat, ohlc_ready=True)
    assert ready and all(r["dukascopy_ohlc_suitability"] == "OHLC_READY" for r in ready)
    assert len(ready) + len(library.search(cat, ohlc_ready=False)) == 45
    assert library.search(cat, q="vwap")
    assert library.search(cat, market="forex")
    assert library.search(cat, timeframe="M5")
    assert library.search(cat, q="zzzz-nonexistent") == []


def test_dossier_markdown(imported):
    dest, _, cat, _ = imported
    d = library.get_dossier(cat, "03_matteo_drift_vwap", base_dir=dest)
    assert d["markdown"]["build_spec.md"]
    with pytest.raises(KeyError):
        library.get_dossier(cat, "nope")


def test_concepts(imported):
    cs = library.component_concepts(imported[2])
    ids = {r["id"] for r in imported[2]}
    assert len(cs) >= 10
    for c in cs:
        assert set(c["dossier_ids"]) <= ids
        assert c["provenance"] in {"SOURCE_RULE", "OWN_TESTABLE_TRANSLATION"}
        assert c["feature_hint"]


def test_idempotent_and_source_untouched(tmp_path, imported):
    first = library.import_library(SOURCE, tmp_path / "a")
    h1 = _h(tmp_path / "a" / "catalogue.json")
    library.import_library(SOURCE, tmp_path / "a")
    assert _h(tmp_path / "a" / "catalogue.json") == h1
    assert first["count"] == 45
    assert _h(SOURCE / "strategies_index.csv") == imported[3]
