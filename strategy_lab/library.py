"""IQ Capital strategy research library: import, catalogue, search.

Source dossiers are read-only data. Nothing in them is executed.

dukascopy_ohlc_suitability mapping (strictly from data_readiness.json):
  OHLC_READY            data_class == "price/volume OHLC strategy candidate", or
                        ohlc_m1_only_sufficient_for_exact_reproduction is True.
                        Candidate only: exact reproduction is "NOT YET VERIFIED".
  UNKNOWN               data_class == "rules incomplete: cannot claim replication",
                        or no usable readiness information.
  OHLC_PROXY_ONLY       data needs beyond OHLC, but price_only_proxy_is_distinct_strategy
                        is True: an OHLC version exists only as a different strategy.
  NEEDS_EXTERNAL_DATA   orderflow / price-at-volume / true tick data classes, or
                        ohlc_m1_only_sufficient_for_exact_reproduction is False.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
DEFAULT_DEST = _HERE / "data_cache" / "library"
DEFAULT_CATALOGUE = DEFAULT_DEST / "catalogue.json"

PRIORITY_IDS = (
    "03_matteo_drift_vwap", "04_matteo_vault_break", "05_matteo_new_vwap_adx",
    "06_matteo_overnight_bias_orb", "18_mike_crt_3candle", "19_mnq_atr_extreme_long",
    "29_hannes_orb", "35_trader_x_nr4_breakout",
)
MD_FILES = ("build_spec.md", "EVIDENCE_MATRIX.md", "REFERENCES.md", "test_checklist.md", "README.md")
GRADE_RE = re.compile(r"^\s*([ABC])\s*[:\-]")
TRANSFER_NOTE = (
    "Regels zijn onderzocht op het originele instrument; toepassing op een ander instrument "
    "(bv. Dukascopy CFD/FX/index in plaats van NQ futures) vereist een nieuwe hypothese en eigen validatie."
)
CAVEATS = [
    "Evidence-grade A/B/C beschrijft volledigheid van de regels, niet winstgevendheid.",
    "Originele transcripts zijn niet meegeleverd; regels komen uit publieke beschrijvingen en samenvattingen.",
    "Niet bewezen winstgevend; resultaten van de bron zijn niet onafhankelijk geverifieerd.",
]
_REQUIRES_KEYS = (
    ("futures volume", r"futures|volume|ohlcv|vwap"),
    ("volume profile", r"volume.?profile|price-at-volume|vpoc|value area|single.?print"),
    ("orderflow", r"order.?flow|footprint|delta|absorption|big trades|cvd"),
    ("L2", r"\bl2\b|\bdom\b|bookmap|depth|order book|heatmap"),
    ("news/macro", r"news|macro|pmi|gdp|\bcot\b|centrale|central.?bank|economic"),
    ("tick data", r"\btick|bid/ask|executable"),
    ("options", r"option|\bput\b|\biv\b"),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _as_list(v):
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _grade(status, explicit):
    if explicit in ("A", "B", "C"):
        return explicit
    m = GRADE_RE.match(status or "")
    return m.group(1) if m else None


def suitability(readiness) -> str:
    r = readiness or {}
    dc = r.get("data_class")
    if dc == "price/volume OHLC strategy candidate":
        return "OHLC_READY"
    if dc == "rules incomplete: cannot claim replication":
        return "UNKNOWN"
    if dc is not None:
        if r.get("price_only_proxy_is_distinct_strategy") is True:
            return "OHLC_PROXY_ONLY"
        return "NEEDS_EXTERNAL_DATA"
    flag = r.get("ohlc_m1_only_sufficient_for_exact_reproduction")
    if flag is True:
        return "OHLC_READY"
    if flag is False:
        return "NEEDS_EXTERNAL_DATA"
    return "UNKNOWN"


def _source_type(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "video"
    if "github.com" in u:
        return "spec"
    if "iqcapital.io" in u:
        return "official_article"
    if "transcript" in u:
        return "transcript_viewer"
    return "web"


def _sources(strat: dict, url_titles: dict) -> list:
    out, seen = [], set()
    for v in _as_list(strat.get("video_references")):
        url = v.get("youtube_url")
        if url and url not in seen:
            seen.add(url)
            out.append({"title": v.get("title"), "url": url, "type": "video"})
    for url in _as_list(strat.get("source_urls")):
        if url and url not in seen:
            seen.add(url)
            out.append({"title": url_titles.get(url), "url": url, "type": _source_type(url)})
    return out


def _requires(readiness: dict, strat: dict) -> list:
    text = " ".join(_as_list(readiness.get("mandatory_data")) + _as_list(readiness.get("required_data"))
                    + _as_list(strat.get("requirements"))).lower()
    return [name for name, pat in _REQUIRES_KEYS if re.search(pat, text)]


def _record(sid: str, sdir: Path, url_titles: dict) -> dict:
    strat = _load_json(sdir / "strategy.json")
    rp = sdir / "data_readiness.json"
    readiness = _load_json(rp) if rp.exists() else {}
    status = strat.get("status")
    shas = {p.name: _sha(p) for p in sorted(sdir.iterdir()) if p.is_file()}
    market = strat.get("market")
    tf = strat.get("timeframes")
    return {
        "id": sid,
        "strategy": strat.get("name"),
        "title": strat.get("name"),
        "trader": strat.get("trader"),
        "kind": strat.get("kind"),
        "sources": _sources(strat, url_titles),
        "markets": [market] if market else [],
        "timeframes": [tf] if tf else [],
        "mechanism": strat.get("mechanism"),
        "context_rules": _as_list(strat.get("context")),
        "entry_rules": _as_list(strat.get("entry")),
        "exit_rules": _as_list(strat.get("exit")),
        "risk_management": _as_list(strat.get("risk")),
        "parameters": strat.get("parameters") or {},
        "data_requirements": _as_list(readiness.get("mandatory_data") or readiness.get("required_data")
                                      or strat.get("requirements")),
        "evidence_grade": _grade(status, strat.get("grade")),
        "evidence_status": status,
        "implementation_readiness": strat.get("implementation_gate")
        or readiness.get("gate") or readiness.get("exact_reproduction_on_ordinary_mt5_m1_ohlc"),
        "unknown_rules": _as_list(strat.get("unknown")),
        "experiments": _as_list(strat.get("experiments")),
        "dukascopy_ohlc_suitability": suitability(readiness),
        "data_class": readiness.get("data_class"),
        "requires": _requires(readiness, strat),
        "translation_status": "RESEARCH_VARIANT_REQUIRED",
        "caveats": list(CAVEATS),
        "priority_prototype": any(sid.startswith(p) for p in PRIORITY_IDS),
        "original_instrument": market,
        "instrument_transfer_note": TRANSFER_NOTE,
        "markdown_files": [n for n in MD_FILES if (sdir / n).exists()],
        "source_sha256": shas,
    }


def import_library(source, dest=None) -> dict:
    source = Path(source)
    dest = Path(dest) if dest else DEFAULT_DEST
    if dest.suffix == ".json":
        cat_path, dest_dir = dest, dest.parent
    else:
        cat_path, dest_dir = dest / "catalogue.json", dest
    errors: list = []
    sroot = source / "strategies"
    url_titles: dict = {}
    sj = source / "sources.json"
    if sj.exists():
        try:
            url_titles = {v: k for k, v in _load_json(sj).items() if isinstance(v, str)}
        except Exception as e:  # noqa: BLE001
            errors.append(f"sources.json: {e}")
    records = []
    dirs = sorted(p for p in sroot.iterdir() if p.is_dir()) if sroot.is_dir() else []
    for sdir in dirs:
        try:
            rec = _record(sdir.name, sdir, url_titles)
            out = dest_dir / "dossiers" / sdir.name
            out.mkdir(parents=True, exist_ok=True)
            for n in rec["markdown_files"]:
                shutil.copy2(sdir / n, out / n)
            records.append(rec)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{sdir.name}: {e}")
    found = {r["id"] for r in records}
    for p in PRIORITY_IDS:
        if p not in found:
            errors.append(f"priority prototype missing: {p}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest_dir, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, cat_path)
    return {
        "count": len(records),
        "grades": {g: sum(1 for r in records if r["evidence_grade"] == g) for g in "ABC"},
        "ohlc_ready_count": sum(1 for r in records if r["dukascopy_ohlc_suitability"] == "OHLC_READY"),
        "suitability": {k: sum(1 for r in records if r["dukascopy_ohlc_suitability"] == k)
                        for k in ("OHLC_READY", "OHLC_PROXY_ONLY", "NEEDS_EXTERNAL_DATA", "UNKNOWN")},
        "catalogue": str(cat_path),
        "errors": errors,
    }


def load_catalogue(path=None) -> list:
    p = Path(path) if path else DEFAULT_CATALOGUE
    return json.loads(p.read_text(encoding="utf-8"))


def _blob(r: dict) -> str:
    parts = [r.get("id"), r.get("title"), r.get("trader"), r.get("kind"), r.get("mechanism")]
    for k in ("entry_rules", "exit_rules", "context_rules", "risk_management", "unknown_rules", "markets"):
        parts += r.get(k) or []
    return " ".join(str(x) for x in parts if x).lower()


def search(catalogue, q=None, grade=None, market=None, timeframe=None, ohlc_ready=None, trader=None) -> list:
    out = []
    for r in catalogue:
        if q and not all(t in _blob(r) for t in q.lower().split()):
            continue
        if grade and r.get("evidence_grade") != grade.upper():
            continue
        if market and not any(market.lower() in m.lower() for m in r.get("markets") or []):
            continue
        if timeframe and not any(timeframe.lower() in t.lower() for t in r.get("timeframes") or []):
            continue
        if ohlc_ready is not None and (r.get("dukascopy_ohlc_suitability") == "OHLC_READY") != bool(ohlc_ready):
            continue
        if trader and trader.lower() not in (r.get("trader") or "").lower():
            continue
        out.append(r)
    return out


def get_dossier(catalogue, id, base_dir=None) -> dict:
    rec = next((r for r in catalogue if r["id"] == id), None)
    if rec is None:
        raise KeyError(id)
    base = Path(base_dir) if base_dir else DEFAULT_DEST
    texts = {}
    for n in rec.get("markdown_files", []):
        p = base / "dossiers" / id / n
        texts[n] = p.read_text(encoding="utf-8") if p.exists() else None
    return {**rec, "markdown": texts}


# (concept, trader, id tokens, evidence keywords, ohlc_feasible, feature_hint)
_CONCEPTS = (
    ("Effort-vs-result / value zones", "Gianluca Bruni", ("bruni",), ("value area", "effort", "vah"), False, "value_area"),
    ("Session VWAP regime / pullback", "Matteo Conti", ("matteo",), ("vwap",), True, "vwap"),
    ("Opening range breakout (ORB)", "Matteo Conti", ("matteo",), ("orb", "opening range"), True, "opening_range"),
    ("ADX trend-strength filter", "Matteo Conti", ("matteo",), ("adx",), True, "adx"),
    ("Volatility / ATR filter", "Matteo Conti", ("matteo",), ("atr", "volatil"), True, "atr"),
    ("Ranges and three touches", "Patrick Nill", ("patrick",), ("3 touch", "three touch", "drie", "range"), True, "range_touches"),
    ("Liquidity sweep", "Candace", ("candace",), ("sweep", "liquidity", "turtle"), True, "liquidity_sweep"),
    ("Fair value gap (FVG)", "Candace", ("candace", "fvg"), ("fvg", "fair value"), True, "fvg"),
    ("Autocorrelation", "Trader Math", ("math",), ("autocorr",), True, "autocorrelation"),
    ("Hurst exponent regime", "Trader Math", ("math",), ("hurst",), True, "hurst"),
    ("Higher-timeframe regime", "Imre Gams", ("imre",), ("htf", "hogere", "one time framing", "timeframe"), True, "htf_regime"),
    ("Opening range (cash open)", "Hannes", ("hannes",), ("opening range", "orb", "15-minute", "open"), True, "opening_range"),
    ("Winner-only add (pyramiding)", "Marco", ("marco",), ("add", "winner", "winst", "loading"), True, "winner_add"),
    ("Balance vs trend day", "Tom", ("tom",), ("balance", "trend"), True, "balance_vs_trend"),
    ("Context / scenario planning", "Chris Creamer", ("chris_creamer", "chris_premarket"), ("scenario", "context", "layer", "laag"), True, "context_scenario"),
    ("NR4 narrow-range breakout", "Trader X", ("nr4",), ("nr4",), True, "nr4"),
)


def _rule_text(r: dict) -> str:
    return " ".join(r["context_rules"] + r["entry_rules"] + r["exit_rules"] + [r.get("mechanism") or ""]).lower()


def component_concepts(catalogue=None) -> list:
    try:
        cat = catalogue if catalogue is not None else load_catalogue()
    except FileNotFoundError:
        cat = []
    out = []
    for concept, trader, tokens, kws, feasible, hint in _CONCEPTS:
        pool = [r for r in cat if any(re.search(rf"(^|_){t}(_|$)", r["id"]) for t in tokens)]
        hits = [r for r in pool if any(k in _rule_text(r) for k in kws)]
        recs = hits or pool
        explicit = bool(hits)
        out.append({
            "concept": concept, "trader": trader,
            "dossier_ids": [r["id"] for r in recs],
            "provenance": "SOURCE_RULE" if explicit else "OWN_TESTABLE_TRANSLATION",
            "ohlc_feasible": feasible, "feature_hint": hint,
        })
    return out
