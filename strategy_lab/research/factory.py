"""Automatic Strategy Discovery Factory: multi-stage, budgeted, checkpointed research of generated rule strategies.

Stages per candidate (each with early rejection, every result checkpointed and recorded in the research ledger):

  L1 technical      generator.level1_check + own causality check + data support + lineage (sealed reuse) check
  L2 quick          default params on 3-4 representative development windows (different regimes)
  L3 full dev       default params on the whole development period + year/month/session/regime breakdowns
  L4 optimisation   random search + successive halving, multi-objective (Pareto), development period only
  L5 validation     rolling AND expanding walk-forward over the validation period, perturbation, cost stress,
                    Monte Carlo, PBO, PSR/DSR (N = all trials in the ledger), distribution, quality profile, scorecard
  L6 freeze         cross-candidate selection on dev information only, diversity, baselines -> frozen record
  L7 sealed test    protocol.SealedVault.final_test (the ONLY sealed access) + multiple-testing correction
  L8 forward hook   FORWARD_ELIGIBLE only when everything passed; registration is an explicit user action

``discovery.run_discovery`` is the quick, single-split special case of this pipeline (same families, backtest engine,
ledger and trial accounting); the factory is the full multi-stage protocol.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import math
import os
import random
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from runtime.strategy_lab import rule_engine as engine

from . import codegen, discovery, distribution, effective_trials, nested_wf, protocol, robustness, validation
from .jobs import JobCancelled

HERE = Path(__file__).resolve().parent
LAB = HERE.parent
DEFAULT_DB = LAB / "data_cache" / "factory.sqlite3"
DEFAULT_LEDGER = LAB / "data_cache" / "research_ledger.sqlite3"
FRAMES_DIR = LAB / "data_cache" / "factory_frames"
RESEARCH_VERSION = "FACTORY_V10"
PBO_TRIAL_STAGE = "L5_PBO"
STAGES = ("L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8")
STAGE_NL = {"L1": "Technische controle", "L2": "Snelle test (representatieve vensters)",
            "L3": "Volledige ontwikkelbacktest", "L4": "Parameteroptimalisatie (alleen ontwikkelperiode)",
            "L5": "Walk-forward + robuustheid (validatieperiode)", "L6": "Selectie en bevriezing",
            "L7": "Verzegelde eindtest", "L8": "Forward-geschiktheid"}
PHASE_NL = {"queued": "In de wachtrij", "preparing": "Data en tijdsprotocol voorbereiden",
            "generating": "Kandidaten genereren", "researching": "Kandidaten onderzoeken (L1-L5)",
            "selecting": "Selectie en bevriezing (L6)", "final_test": "Verzegelde eindtest (L7)",
            "forward": "Forward-geschiktheid (L8)", "done": "Klaar", "stopped": "Gestopt", "error": "Fout",
            "paused": "Gepauzeerd"}
TERMINAL_STATUSES = {"REJECTED", "ERROR", "REUSED_HOLDOUT", "INSUFFICIENT_EVIDENCE", "TECHNICAL_BLOCKED",
                     "REVIEW_REQUIRED"}     # REVIEW_REQUIRED: no automatic work; only a recorded review moves it on
DATA_REQUIREMENTS = ["Dukascopy M1 Bid/Ask OHLC (geaggregeerd)"]
# commission per lot per side assumed per asset class (spread comes from the real Bid/Ask)
COMMISSION_PER_LOT_SIDE = {"fx": 3.5, "metal": 0.0, "index": 0.0, "crypto": 0.0}
_FX = {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"}

PROFILES = {
    "snel": {
        "label_nl": "Snel", "description_nl": ("Weinig families, korte representatieve tests, weinig trials. Geen "
                                               "verzegelde eindtest: snel onderzochte kandidaten zijn daar nog niet rijp voor."),
        "defaults": {"max_families": 4, "candidates_per_market": 4, "window_months": 3, "n_windows": 3,
                     "trials": 6, "eta": 3, "wf_top_k": 2, "monte_carlo_n": 300, "final_test": False,
                     "max_frozen": 0, "max_per_family": 1, "correlation": False, "max_minutes": 20,
                     "strict": False, "optimizer": "random", "nested_filter_variants": True}},
    "uitgebreid": {
        "label_nl": "Uitgebreid", "description_nl": ("Veel families, meerjarige tests, optimalisatie, walk-forward, "
                                                     "kosten- en robuustheidstests; eindtest van voorgeselecteerde kandidaten."),
        "defaults": {"max_families": None, "candidates_per_market": 16, "window_months": 6, "n_windows": 4,
                     "trials": 24, "eta": 3, "wf_top_k": 4, "monte_carlo_n": 1000, "final_test": True,
                     "max_frozen": 6, "max_per_family": 1, "correlation": False, "max_minutes": 90,
                     "strict": False, "optimizer": "random", "nested_filter_variants": True}},
    "diepgaand": {
        "label_nl": "Diepgaand", "description_nl": ("Grotere begrensde zoekruimte, meer markten/timeframes, uitgebreide "
                                                    "statistiek, portefeuille-/correlatieanalyse en strengere promotie."),
        "defaults": {"max_families": None, "candidates_per_market": 40, "window_months": 12, "n_windows": 4,
                     "trials": 48, "eta": 3, "wf_top_k": 6, "monte_carlo_n": 2000, "final_test": True,
                     "max_frozen": 10, "max_per_family": 2, "correlation": True, "max_minutes": 240,
                     "strict": True, "optimizer": "random", "nested_filter_variants": True}},
}

QUALITY_PROFILES = {
    "regelmatig": {"label_nl": "Regelmatig", "min_trades_per_week": 1.0, "min_oos_trades": 150,
                   "min_pf_oos": 1.20, "min_rf_oos": 1.5, "max_dd_pct": 15.0, "max_top3_day_share": 0.5,
                   "max_inactive_days": 60, "min_param_stability": 0.7, "max_pbo": 0.5, "min_wfe": 0.5,
                   "min_dsr": 0.95, "min_psr": 0.95, "alpha": 0.05},
    "zeer_actief": {"label_nl": "Zeer actief", "min_trades_per_week": 5.0, "min_oos_trades": 500,
                    "min_pf_oos": 1.15, "min_rf_oos": 2.0, "max_dd_pct": 15.0, "max_top3_day_share": 0.4,
                    "max_inactive_days": 21, "min_param_stability": 0.7, "max_pbo": 0.5, "min_wfe": 0.5,
                    "min_dsr": 0.95, "min_psr": 0.95, "alpha": 0.05},
    "laag_frequent": {"label_nl": "Laag frequent", "min_trades_per_week": 0.25, "min_oos_trades": 50,
                      "min_pf_oos": 1.30, "min_rf_oos": 1.5, "max_dd_pct": 20.0, "max_top3_day_share": 0.5,
                      "max_inactive_days": 180, "min_param_stability": 0.7, "max_pbo": 0.5, "min_wfe": 0.5,
                      "min_dsr": 0.95, "min_psr": 0.95, "alpha": 0.05},
}
QUALITY_PROFILES["custom"] = {**QUALITY_PROFILES["regelmatig"], "label_nl": "Aangepast"}

SUPPORTED_TIMEFRAMES = ("M15", "M30", "H1", "H4")
DEFAULT_MARKETS = ("XAUUSD", "US30", "DE40")
CPU_COUNT = os.cpu_count() or 4
# Up to all-but-two cores; the scheduler only starts a new task while machine CPU and RAM stay under
# MACHINE_LIMIT_PCT, so the effective worker count adapts to what else runs on the PC.
DEFAULT_WORKERS = max(1, CPU_COUNT - 2)
MACHINE_LIMIT_PCT = 80.0


def _default_ram_mb() -> float:
    try:
        import psutil
        return round(psutil.virtual_memory().total / 2**20 * MACHINE_LIMIT_PCT / 100)
    except ImportError:
        return 8000.0
MAX_CANDIDATES = 2000
MAX_BACKTESTS = 250_000
# measured on this machine: ~1 s per 14 years of M15 bars (run_rules_backtest); scaled by bars per year
SECONDS_PER_YEAR = {"M1": 1.0, "M5": 0.2, "M15": 0.07, "M30": 0.04, "H1": 0.025, "H4": 0.01, "D1": 0.005}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def asset_class(symbol: str) -> str:
    s = symbol.upper()
    if s in _FX:
        return "fx"
    if s.startswith(("XAU", "XAG")):
        return "metal"
    if s.startswith("BTC"):
        return "crypto"
    return "index"


def cost_model_for(symbol: str, capital: float, risk_pct: float) -> dict:
    return {"capital": float(capital), "commission_per_lot": COMMISSION_PER_LOT_SIDE[asset_class(symbol)],
            "slippage_points": 0.0, "spread": "echte Dukascopy Bid/Ask per bar (long koopt Ask, verkoopt Bid)",
            "stress": {"spread": [1.25, 1.5], "commission": "x2", "slippage_points": 2, "delay_bars": 1},
            "risk_pct": float(risk_pct),
            "contract": "aanname, niet door broker bevestigd (backtest.default_contract)"}


def code_sha256() -> str:
    digest = hashlib.sha256()
    for path in (HERE / "factory.py", HERE / "protocol.py", HERE / "backtest.py", HERE / "distribution.py",
                 HERE / "generator.py", LAB.parent / "runtime" / "strategy_lab" / "rule_engine.py"):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


# ================================================================================================ generator
class GeneratorShim:
    """Thin adapter over discovery.generate_hypotheses with the generator.py contract (used until it exists)."""

    name = "discovery-shim"
    GROUPS = {"trend_following": "trend", "regime_filtered_momentum": "trend", "breakout": "breakout",
              "volatility_expansion": "volatiliteit", "volatility_contraction": "volatiliteit",
              "session_breakout": "sessie", "time_of_day": "sessie", "mean_reversion": "mean_reversion",
              "vwap_pullback": "pullback", "liquidity_sweep_proxy": "price_action", "price_action_reversal": "price_action"}

    def families_catalog(self):
        return [{"id": f["id"], "name": f["name"], "group": self.GROUPS.get(f["id"], "overig"),
                 "description_nl": f["description"], "default": bool(f.get("default")),
                 "proxy": "PROXY" in f["description"] or "proxy" in f["id"]} for f in discovery.FAMILIES]

    @staticmethod
    def _signature(spec, symbol, timeframe, family=""):
        body = {k: v for k, v in spec.items() if k not in ("name", "description", "params", "symbol", "timeframe")}
        return hashlib.sha256(protocol.canon({"b": body, "s": symbol, "t": timeframe, "f": family}).encode()).hexdigest()[:20]

    def generate(self, config, seed):
        fams = config.get("families")
        fams = None if fams in (None, "all") else list(fams)
        hyps = discovery.generate_hypotheses(fams, config["markets"], config["timeframes"],
                                             int(config.get("max_candidates", 50)), int(seed))
        out = []
        for h in hyps:
            spec = h["spec"]
            fam = discovery.family_by_id(h["family"])
            space = {p["name"]: {"type": p["type"], "min": p["min"], "max": p["max"], "step": p.get("step"),
                                 "default": p["default"]} for p in spec.get("params") or []}
            out.append({"id": h["hypothesis_id"], "name": spec["name"], "symbol": h["symbol"],
                        "timeframe": h["timeframe"], "family": h["family"],
                        "group": self.GROUPS.get(h["family"], "overig"), "spec": spec, "param_space": space,
                        "blocks": sorted(h["mutations"]), "iq_sources": [],
                        "passport": {"generator": self.name, "parent": h["parent"], "mutations": h["mutations"]},
                        "hypothesis_nl": fam["description"], "rules_nl": engine.describe(spec),
                        "complexity": len(json.dumps(spec["entry"])) // 40,
                        "signature": self._signature(spec, h["symbol"], h["timeframe"], h["family"]),
                        "proxies_nl": ["Liquidity sweep is een OHLC-proxy, geen orderflow."]
                        if "sweep" in h["family"] else [], "parent": h["parent"]})
        return out

    def level1_check(self, candidate, bid, ask):
        errs = engine.validate_spec(candidate["spec"])
        checks = [{"name": "spec_valid", "ok": not errs, "detail": "; ".join(errs[:3])}]
        return {"ok": not errs, "reasons": [f"ongeldige spec: {e}" for e in errs[:3]], "checks": checks}

    def dedup(self, cands, existing=frozenset()):
        seen, kept, dropped = set(existing or ()), [], []
        for c in cands:
            if c["signature"] in seen:
                dropped.append({"id": c["id"], "family": c["family"], "signature": c["signature"],
                                "reason": "duplicaat: identieke structuur (signature)"})
            else:
                seen.add(c["signature"])
                kept.append(c)
        return kept, dropped

    def baselines(self, symbol, timeframe):
        spec = discovery.base_spec(discovery.family_by_id("trend_following"))
        spec.update(symbol=symbol, timeframe=timeframe, name="Baseline EMA-kruising",
                    description="Naive referentie: EMA(20/50)-kruising met ADX-filter, standaardparameters.")
        return [{"id": "baseline_ema_cross", "name": spec["name"], "spec": spec}]


def dedup_key(c) -> str:
    return f"{c.get('signature')}|{c.get('symbol')}|{c.get('timeframe')}"


def generator_for(run: dict):
    """The generator that produced this run's candidates (fixed per run, also after generator.py appears)."""
    if (run or {}).get("generator") == GeneratorShim.name:
        return GeneratorShim()
    return get_generator()


def get_generator():
    """The real strategy_lab.research.generator when present, else the discovery shim."""
    if (HERE / "generator.py").is_file():
        try:
            mod = importlib.import_module("strategy_lab.research.generator")
            if all(hasattr(mod, n) for n in ("families_catalog", "generate", "level1_check", "dedup", "baselines")):
                mod.name = getattr(mod, "name", "generator.py")
                return mod
        except Exception as exc:  # a half-written generator must not take the factory down
            print(f"[factory] generator.py niet bruikbaar ({type(exc).__name__}: {exc}); shim gebruikt", flush=True)
    return GeneratorShim()


def families_catalog() -> list[dict]:
    try:
        return list(get_generator().families_catalog())
    except Exception:
        return GeneratorShim().families_catalog()


# ================================================================================================ config / plan
def normalize_config(body: dict) -> dict:
    """Validate and complete a run/plan body. Raises ValueError for unknown or unbounded input."""
    body = dict(body or {})
    profile = body.get("profile") or "uitgebreid"
    if profile not in PROFILES:
        raise ValueError(f"onbekend onderzoeksprofiel {profile!r}")
    quality = body.get("quality") or "regelmatig"
    if quality not in QUALITY_PROFILES:
        raise ValueError(f"onbekend kwaliteitsprofiel {quality!r}")
    adv = dict(body.get("advanced") or {})
    prof = {**PROFILES[profile]["defaults"], **{k: v for k, v in adv.items() if k in PROFILES[profile]["defaults"]}}
    q = {k: v for k, v in QUALITY_PROFILES[quality].items() if k != "label_nl"}
    if body.get("min_trades_per_week") is not None:
        q["min_trades_per_week"] = float(body["min_trades_per_week"])
    if body.get("max_dd_pct") is not None:
        q["max_dd_pct"] = float(body["max_dd_pct"])
    for k, v in (adv.get("quality") or {}).items():
        if k in q:
            q[k] = float(v)
    if prof["strict"]:
        q["max_pbo"] = min(q["max_pbo"], 0.3)
    markets = [str(m).upper() for m in (body.get("markets") or DEFAULT_MARKETS)]
    tfs = [str(t).upper() for t in (body.get("timeframes") or ["M15", "H1"])]
    bad = [t for t in tfs if t not in SUPPORTED_TIMEFRAMES]
    if bad:
        raise ValueError(f"niet-ondersteunde timeframes: {bad}")
    if not markets or not tfs:
        raise ValueError("kies minstens een markt en een timeframe")
    fam_ids = [f["id"] for f in families_catalog()]
    families = body.get("families") or "all"
    if families != "all":
        families = [f for f in families if f]
        unknown = [f for f in families if f not in fam_ids]
        if unknown:
            raise ValueError(f"onbekende families: {unknown}")
    elif prof.get("max_families"):
        # spread a limited family budget across strategy groups (not the first N in catalogue order = all trend)
        by_group: dict = {}
        for f in families_catalog():
            by_group.setdefault(f.get("group", "other"), []).append(f["id"])
        rng = random.Random(int(adv.get("seed") or 0))
        pools = [rng.sample(v, len(v)) for _, v in sorted(by_group.items())]
        rng.shuffle(pools)
        families, i = [], 0
        while len(families) < int(prof["max_families"]) and any(pools):
            if pools[i % len(pools)]:
                families.append(pools[i % len(pools)].pop())
            i += 1
    n_fam = len(fam_ids) if families == "all" else len(families)
    max_candidates = int(adv.get("max_candidates") or prof["candidates_per_market"] * len(markets) * len(tfs))
    if max_candidates < 1 or max_candidates > MAX_CANDIDATES:
        raise ValueError(f"max_candidates moet tussen 1 en {MAX_CANDIDATES} liggen (begrensd onderzoek)")
    trials = int(adv.get("max_trials_per_candidate") or prof["trials"])
    if trials < 1 or trials > 500:
        raise ValueError("max_trials_per_candidate moet tussen 1 en 500 liggen")
    workers = int(adv.get("max_workers") or DEFAULT_WORKERS)
    workers = max(1, min(workers, max(1, CPU_COUNT - 2)))
    max_minutes = float(adv.get("max_minutes") or prof["max_minutes"])
    if not 1 <= max_minutes <= 24 * 60:
        raise ValueError("max_minutes moet tussen 1 en 1440 liggen")
    risk_pct = float(body.get("risk_pct") or 0.5)
    if not 0.01 <= risk_pct <= 5:
        raise ValueError("risk_pct moet tussen 0,01 en 5 liggen")
    cfg = {"markets": markets, "timeframes": tfs, "families": families, "n_families": n_fam,
            "profile": profile, "quality": quality, "quality_thresholds": q, "profile_settings": prof,
            "max_candidates": max_candidates, "trials": trials, "workers": workers, "max_minutes": max_minutes,
            "max_ram_mb": float(adv.get("max_ram_mb") or _default_ram_mb()), "max_machine_cpu_pct": float(adv.get("max_machine_cpu_pct") or MACHINE_LIMIT_PCT), "max_machine_ram_pct": float(adv.get("max_machine_ram_pct") or MACHINE_LIMIT_PCT), "risk_pct": risk_pct,
            "capital": float(adv.get("capital") or 10000), "seed": int(adv.get("seed") or body.get("seed") or 0),
            "stuck_timeout_s": float(adv.get("stuck_timeout_s") or 900),
            "max_backtests": int(adv.get("max_backtests") or MAX_BACKTESTS),
            "ledger_path": adv.get("ledger_path"), "dedup": adv.get("dedup", "ledger"),
            "sealed_start": adv.get("sealed_start") or protocol.SEALED_START,
            "development_end": adv.get("development_end") or protocol.DEVELOPMENT_END,
            "dev_start": adv.get("dev_start") or protocol.DEV_START, "auto_forward": False,
            "loader": adv.get("loader"), "frames_dir": adv.get("frames_dir"), "code_sha256": code_sha256(),
            "research_version": RESEARCH_VERSION}
    cfg["preregistration"] = protocol.preregistration(cfg)
    return cfg


def _stage_backtests(cfg: dict, n_params: int = 3) -> dict:
    p = cfg["profile_settings"]
    trials = cfg["trials"]
    eta = max(2, int(p["eta"]))
    sh = trials + math.ceil(trials / eta) + math.ceil(trials / eta ** 2)
    wf_windows = 12  # 6 validation years / 182-day test windows
    k = max(1, int(p["wf_top_k"]))
    return {"L2": p["n_windows"], "L3": 1, "L4": sh, "L5": 2 * wf_windows * (k + 1) + 2 * n_params + 7 + k + 2,
            "L7": 1}


def plan(body: dict, availability: dict | None = None) -> dict:
    """Bounded estimate of the work for a configuration (refuses unbounded configurations)."""
    cfg = normalize_config(body)
    availability = availability or {}
    p = cfg["profile_settings"]
    per = _stage_backtests(cfg)
    n = cfg["max_candidates"]
    # survival assumptions (from earlier discovery runs: most naive rule strategies fail the quick test)
    surv = {"L2": 1.0, "L3": 0.35, "L4": 0.25, "L5": 0.15}
    backtests_exp = n * (per["L2"] + surv["L3"] * per["L3"] + surv["L4"] * per["L4"] + surv["L5"] * per["L5"])
    backtests_max = n * (per["L2"] + per["L3"] + per["L4"] + per["L5"]) + p["max_frozen"]
    trials_est = int(round(n * surv["L4"] * per["L4"]))
    ranges, warnings = {}, []
    sec_per_bt = []
    for sym in cfg["markets"]:
        av = availability.get(sym) or {}
        first = (av.get("first_reliable") or av.get("first") or protocol.DEV_START)[:10]
        last = (av.get("last_complete_day") or av.get("last") or datetime.now(timezone.utc).date().isoformat())[:10]
        r = protocol.build_ranges({"first_reliable": first, "last_complete_day": last},
                                  dev_start=cfg["dev_start"], development_end=cfg["development_end"],
                                  sealed_start=cfg["sealed_start"])
        ranges[sym] = {"dev": r["dev"], "development": r["development"], "validation": r["validation"],
                       "sealed": r["sealed"], "status": r["status"], "notes_nl": r["notes_nl"],
                       "estimated": not av.get("measured")}
        if r["status"] == "INSUFFICIENT_SUPPORT":
            warnings.append(f"{sym}: onvoldoende data voor het tijdsprotocol ({'; '.join(r['notes_nl'])}).")
        elif r["notes_nl"]:
            warnings.append(f"{sym}: " + " ".join(r["notes_nl"]))
        years = (r["development"] or {}).get("days", 14 * 365) / 365.0
        for tf in cfg["timeframes"]:
            sec_per_bt.append(SECONDS_PER_YEAR.get(tf, 0.05) * max(years, 1.0) * 0.6)
    avg_bt = float(np.mean(sec_per_bt)) if sec_per_bt else 0.5
    workers = cfg["workers"]
    est_minutes = backtests_exp * avg_bt / workers / 60.0 * 1.3 + 2.0
    est_max = backtests_max * avg_bt / workers / 60.0 * 1.3 + 2.0
    if backtests_max > cfg["max_backtests"]:
        raise ValueError(f"onderzoek te groot: tot {int(backtests_max)} backtests > limiet {cfg['max_backtests']}; "
                         "verklein markten, families of trials")
    if est_minutes > cfg["max_minutes"]:
        warnings.append(f"Geschatte duur {est_minutes:.0f} min > tijdsbudget {cfg['max_minutes']:.0f} min: na het "
                        "budget worden geen nieuwe kandidaten meer gestart.")
    if not p["final_test"]:
        warnings.append("Profiel 'snel' doet GEEN verzegelde eindtest: kandidaten blijven onderzoekskandidaat.")
    cpu_note = (f"{workers} werkprocessen op {CPU_COUNT} kernen, prioriteit 'lager dan normaal'; de app blijft "
                f"bruikbaar. Maximaal {cfg['max_ram_mb']:.0f} MB RAM voor deze run.")
    return {"candidates": n, "backtests_estimate": int(round(backtests_exp)), "backtests_max": int(backtests_max),
            "trials_estimate": trials_est, "est_minutes": round(est_minutes, 1), "est_minutes_max": round(est_max, 1),
            "workers": workers, "cpu_note_nl": cpu_note, "ranges": ranges, "warnings": warnings,
            "per_candidate_backtests": per, "survival_assumption": surv, "config": cfg}


# ================================================================================================ store
STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, status TEXT, phase TEXT, control TEXT,
    progress REAL DEFAULT 0, config_json TEXT, protocol_json TEXT, plan_json TEXT, counts_json TEXT,
    resources_json TEXT, multiple_testing_json TEXT, error TEXT, started_at TEXT, finished_at TEXT, deadline_at TEXT,
    generator TEXT
);
CREATE TABLE IF NOT EXISTS candidates (
    run_id TEXT, cand_id TEXT, created_at TEXT, updated_at TEXT, symbol TEXT, timeframe TEXT, family TEXT,
    grp TEXT, name TEXT, stage TEXT, status TEXT, evidence_class TEXT, reasons_json TEXT, summary_json TEXT,
    cand_json TEXT, frozen_id TEXT, PRIMARY KEY (run_id, cand_id)
);
CREATE TABLE IF NOT EXISTS stage_results (
    run_id TEXT, cand_id TEXT, stage TEXT, created_at TEXT, status TEXT, seconds REAL, reasons_json TEXT,
    metrics_json TEXT, payload_json TEXT, ledger_ids_json TEXT, PRIMARY KEY (run_id, cand_id, stage)
);
CREATE TABLE IF NOT EXISTS trials (
    trial_key TEXT PRIMARY KEY, run_id TEXT, cand_id TEXT, stage TEXT, rung TEXT, params_json TEXT,
    metrics_json TEXT, created_at TEXT, ledger_id TEXT
);
CREATE INDEX IF NOT EXISTS trials_cand ON trials(run_id, cand_id);
CREATE TABLE IF NOT EXISTS tasks (
    run_id TEXT, cand_id TEXT, kind TEXT, state TEXT, attempts INTEGER DEFAULT 0, worker_pid INTEGER,
    heartbeat_at REAL, started_at TEXT, finished_at TEXT, error TEXT, PRIMARY KEY (run_id, cand_id, kind)
);
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, at TEXT, level TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS pbo_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, cand_id TEXT, at TEXT, decision TEXT, reviewer TEXT, note TEXT,
    pbo REAL, max_pbo REAL, l5_sha256 TEXT
);
CREATE TABLE IF NOT EXISTS supervisor (
    id INTEGER PRIMARY KEY CHECK (id = 1), pid INTEGER, heartbeat_at REAL, started_at TEXT
);
CREATE TRIGGER IF NOT EXISTS stage_results_no_update BEFORE UPDATE ON stage_results
BEGIN SELECT RAISE(ABORT, 'stage results are append-only'); END;
CREATE TRIGGER IF NOT EXISTS stage_results_no_delete BEFORE DELETE ON stage_results
BEGIN SELECT RAISE(ABORT, 'stage results are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trials_no_update BEFORE UPDATE ON trials
BEGIN SELECT RAISE(ABORT, 'trial records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trials_no_delete BEFORE DELETE ON trials
BEGIN SELECT RAISE(ABORT, 'trial records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS pbo_reviews_no_update BEFORE UPDATE ON pbo_reviews
BEGIN SELECT RAISE(ABORT, 'PBO reviews are append-only'); END;
CREATE TRIGGER IF NOT EXISTS pbo_reviews_no_delete BEFORE DELETE ON pbo_reviews
BEGIN SELECT RAISE(ABORT, 'PBO reviews are append-only'); END;
CREATE TRIGGER IF NOT EXISTS candidates_no_delete BEFORE DELETE ON candidates
BEGIN SELECT RAISE(ABORT, 'candidate records are never deleted'); END;
"""


def _dumps(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), default=_json_default)


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return str(o)


def _clean(obj):
    """JSON-safe copy (NaN/inf -> None)."""
    def fix(x):
        if isinstance(x, float):
            return x if math.isfinite(x) else None
        if isinstance(x, dict):
            return {str(k): fix(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [fix(v) for v in x]
        return x
    return fix(json.loads(_dumps(obj)))


def _loads(t, default=None):
    if t is None:
        return default
    try:
        return json.loads(t)
    except ValueError:
        return default


class FactoryStore:
    """Checkpoint store (sqlite, WAL): runs, candidates, append-only stage results and trials, tasks, log."""

    def __init__(self, path=None):
        self.path = Path(path or DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.db()) as db, db:
            db.executescript(STORE_SCHEMA)

    def db(self):
        db = sqlite3.connect(self.path, timeout=60)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        return db

    # ---- runs
    def create_run(self, run_id, cfg, plan_out, generator_name):
        with closing(self.db()) as db, db:
            db.execute("INSERT INTO runs (run_id, created_at, updated_at, status, phase, control, config_json, "
                       "plan_json, counts_json, generator) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (run_id, _now(), _now(), "queued", "queued", None, _dumps(cfg), _dumps(plan_out),
                        _dumps({}), generator_name))
        self.log(run_id, "info", f"Run aangemaakt ({cfg['profile']}, {cfg['quality']}, {cfg['max_candidates']} "
                                 f"kandidaten max, {cfg['workers']} workers)")

    def update_run(self, run_id, **fields):
        cols, vals = [], []
        for k, v in fields.items():
            if k.endswith("_json") or k in ("config", "protocol", "plan", "counts", "resources", "multiple_testing"):
                k = k if k.endswith("_json") else f"{k}_json"
                v = _dumps(v)
            cols.append(f"{k}=?")
            vals.append(v)
        cols.append("updated_at=?")
        vals.append(_now())
        with closing(self.db()) as db, db:
            db.execute(f"UPDATE runs SET {','.join(cols)} WHERE run_id=?", (*vals, run_id))

    def run(self, run_id) -> dict | None:
        with closing(self.db()) as db:
            row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return self._run_row(row) if row else None

    @staticmethod
    def _run_row(row):
        d = dict(row)
        for k in ("config", "protocol", "plan", "counts", "resources", "multiple_testing"):
            d[k] = _loads(d.pop(f"{k}_json"), {})
        return d

    def runs(self, limit=50) -> list[dict]:
        with closing(self.db()) as db:
            rows = db.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (int(limit),)).fetchall()
        return [self._run_row(r) for r in rows]

    def control(self, run_id) -> str | None:
        with closing(self.db()) as db:
            row = db.execute("SELECT control FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return row[0] if row else None

    def log(self, run_id, level, message):
        with closing(self.db()) as db, db:
            db.execute("INSERT INTO log (run_id, at, level, message) VALUES (?,?,?,?)", (run_id, _now(), level,
                                                                                         str(message)[:2000]))

    def log_tail(self, run_id, n=40) -> list[dict]:
        with closing(self.db()) as db:
            rows = db.execute("SELECT at, level, message FROM log WHERE run_id=? ORDER BY id DESC LIMIT ?",
                              (run_id, int(n))).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ---- candidates
    def add_candidates(self, run_id, cands, status="GENERATED", stage=None, reasons=None):
        with closing(self.db()) as db, db:
            for c in cands:
                db.execute("INSERT OR IGNORE INTO candidates (run_id, cand_id, created_at, updated_at, symbol, "
                           "timeframe, family, grp, name, stage, status, evidence_class, reasons_json, summary_json, "
                           "cand_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (run_id, c["id"], _now(), _now(), c["symbol"], c["timeframe"], c["family"],
                            c.get("group"), c.get("name"), stage, status, None, _dumps(reasons or []), _dumps({}),
                            _dumps(_clean(c))))

    def set_candidate(self, run_id, cand_id, **fields):
        cols, vals = [], []
        for k, v in fields.items():
            if k in ("reasons", "summary"):
                k, v = f"{k}_json", _dumps(_clean(v))
            cols.append(f"{k}=?")
            vals.append(v)
        cols.append("updated_at=?")
        vals.append(_now())
        with closing(self.db()) as db, db:
            db.execute(f"UPDATE candidates SET {','.join(cols)} WHERE run_id=? AND cand_id=?", (*vals, run_id, cand_id))

    def candidate(self, run_id, cand_id) -> dict | None:
        with closing(self.db()) as db:
            row = db.execute("SELECT * FROM candidates WHERE run_id=? AND cand_id=?", (run_id, cand_id)).fetchone()
        return self._cand_row(row) if row else None

    def find_candidate(self, cand_id) -> dict | None:
        with closing(self.db()) as db:
            row = db.execute("SELECT * FROM candidates WHERE cand_id=? ORDER BY created_at DESC LIMIT 1",
                             (cand_id,)).fetchone()
        return self._cand_row(row) if row else None

    @staticmethod
    def _cand_row(row):
        d = dict(row)
        d["reasons"] = _loads(d.pop("reasons_json"), [])
        d["summary"] = _loads(d.pop("summary_json"), {})
        d["candidate"] = _loads(d.pop("cand_json"), {})
        return d

    def candidates(self, run_id, stage=None, status=None) -> list[dict]:
        sql, args = "SELECT * FROM candidates WHERE run_id=?", [run_id]
        if stage:
            sql += " AND stage=?"
            args.append(stage)
        if status:
            sql += " AND status=?"
            args.append(status)
        with closing(self.db()) as db:
            rows = db.execute(sql + " ORDER BY created_at, rowid", args).fetchall()
        return [self._cand_row(r) for r in rows]

    # ---- stage results (append-only, idempotent)
    def stage_result(self, run_id, cand_id, stage) -> dict | None:
        with closing(self.db()) as db:
            row = db.execute("SELECT * FROM stage_results WHERE run_id=? AND cand_id=? AND stage=?",
                             (run_id, cand_id, stage)).fetchone()
        if row is None:
            return None
        d = dict(row)
        for k in ("reasons", "metrics", "payload", "ledger_ids"):
            d[k] = _loads(d.pop(f"{k}_json"), None)
        return d

    def stage_results(self, run_id, cand_id) -> dict:
        with closing(self.db()) as db:
            rows = db.execute("SELECT stage FROM stage_results WHERE run_id=? AND cand_id=?", (run_id, cand_id)).fetchall()
        return {r[0]: self.stage_result(run_id, cand_id, r[0]) for r in rows}

    def put_stage(self, run_id, cand_id, stage, status, seconds, reasons, metrics, payload, ledger_ids) -> bool:
        with closing(self.db()) as db, db:
            cur = db.execute("INSERT OR IGNORE INTO stage_results VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (run_id, cand_id, stage, _now(), status, round(seconds, 3), _dumps(_clean(reasons)),
                              _dumps(_clean(metrics)), _dumps(_clean(payload)), _dumps(ledger_ids or [])))
            return cur.rowcount == 1

    # ---- trials (append-only, idempotent keys)
    def trial(self, key) -> dict | None:
        with closing(self.db()) as db:
            row = db.execute("SELECT * FROM trials WHERE trial_key=?", (key,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["params"], d["metrics"] = _loads(d.pop("params_json"), {}), _loads(d.pop("metrics_json"), {})
        return d

    def put_trial(self, key, run_id, cand_id, stage, rung, params, metrics, ledger_id) -> bool:
        with closing(self.db()) as db, db:
            cur = db.execute("INSERT OR IGNORE INTO trials VALUES (?,?,?,?,?,?,?,?,?)",
                             (key, run_id, cand_id, stage, str(rung), _dumps(params), _dumps(_clean(metrics)),
                              _now(), ledger_id))
            return cur.rowcount == 1

    def trials(self, run_id=None, cand_id=None) -> list[dict]:
        sql, args = "SELECT * FROM trials", []
        if run_id:
            sql += " WHERE run_id=?"
            args.append(run_id)
            if cand_id:
                sql += " AND cand_id=?"
                args.append(cand_id)
        with closing(self.db()) as db:
            rows = db.execute(sql + " ORDER BY created_at, rowid", args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["params"], d["metrics"] = _loads(d.pop("params_json"), {}), _loads(d.pop("metrics_json"), {})
            out.append(d)
        return out

    def count_trials(self, run_id=None) -> int:
        """Search trials (N of the multiple-testing correction). L5_PBO records re-list L4 configurations for
        auditability; they are the same trials and never count again."""
        with closing(self.db()) as db:
            if run_id:
                return db.execute("SELECT COUNT(*) FROM trials WHERE run_id=? AND stage!=?",
                                  (run_id, PBO_TRIAL_STAGE)).fetchone()[0]
            return db.execute("SELECT COUNT(*) FROM trials WHERE stage!=?", (PBO_TRIAL_STAGE,)).fetchone()[0]

    # ---- PBO reviews (append-only; the candidate status change happens in the same transaction)
    def pbo_reviews(self, run_id, cand_id) -> list[dict]:
        with closing(self.db()) as db:
            rows = db.execute("SELECT * FROM pbo_reviews WHERE run_id=? AND cand_id=? ORDER BY id",
                              (run_id, cand_id)).fetchall()
        return [dict(r) for r in rows]

    def add_pbo_review(self, run_id, cand_id, rec: dict, new_status: str, reasons: list) -> bool:
        """Append the review and move the candidate out of REVIEW_REQUIRED atomically; False when the candidate is
        no longer REVIEW_REQUIRED (a review is final)."""
        with closing(self.db()) as db, db:
            cur = db.execute("UPDATE candidates SET status=?, reasons_json=?, updated_at=? WHERE run_id=? AND cand_id=? "
                             "AND status='REVIEW_REQUIRED'", (new_status, _dumps(_clean(reasons)), _now(), run_id, cand_id))
            if cur.rowcount != 1:
                return False
            db.execute("INSERT INTO pbo_reviews (run_id, cand_id, at, decision, reviewer, note, pbo, max_pbo, l5_sha256) "
                       "VALUES (?,?,?,?,?,?,?,?,?)", (run_id, cand_id, rec["at"], rec["decision"], rec["reviewer"],
                                                      rec["note"], rec["pbo"], rec["max_pbo"], rec["l5_sha256"]))
        return True

    # ---- tasks
    def add_task(self, run_id, cand_id, kind):
        with closing(self.db()) as db, db:
            db.execute("INSERT OR IGNORE INTO tasks (run_id, cand_id, kind, state) VALUES (?,?,?,?)",
                       (run_id, cand_id, kind, "queued"))

    def set_task(self, run_id, cand_id, kind, **fields):
        cols = [f"{k}=?" for k in fields]
        with closing(self.db()) as db, db:
            db.execute(f"UPDATE tasks SET {','.join(cols)} WHERE run_id=? AND cand_id=? AND kind=?",
                       (*fields.values(), run_id, cand_id, kind))

    def tasks(self, run_id, kind=None, state=None) -> list[dict]:
        sql, args = "SELECT * FROM tasks WHERE run_id=?", [run_id]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if state:
            sql += " AND state=?"
            args.append(state)
        with closing(self.db()) as db:
            return [dict(r) for r in db.execute(sql + " ORDER BY rowid", args).fetchall()]

    def heartbeat(self, run_id, cand_id, kind):
        try:
            self.set_task(run_id, cand_id, kind, heartbeat_at=time.time())
        except sqlite3.OperationalError:
            pass


# ================================================================================================ helpers
def weeks_between(start, end) -> float:
    """Trading weeks: calendar weeks minus ~2 holiday weeks per year."""
    days = (protocol.utc(end) - protocol.utc(start)).days
    return max(days / 7.0 * 50.0 / 52.0, 1e-9)


def metrics_of(trades, capital, start, end) -> dict:
    tm = validation.trade_metrics(trades, capital)
    n = tm["n_trades"]
    r = [t.get("r_multiple", 0.0) for t in trades]
    return {"trades": n, "net_profit": tm["net_profit"], "net_return_pct": tm["net_profit"] / capital * 100.0,
            "profit_factor": tm["profit_factor"], "max_drawdown_pct": tm["max_drawdown_pct"],
            "recovery_factor": tm["recovery_factor"], "win_rate": tm["win_rate"], "sharpe_daily": tm["sharpe"],
            "expectancy": tm["net_profit"] / n if n else 0.0, "avg_r": float(np.mean(r)) if r else 0.0,
            "trades_per_week": n / weeks_between(start, end),
            "n_days": int(len(validation.daily_pnl_from_trades(trades))) if n else 0}


def _downsample(equity, limit=300):
    if not equity:
        return []
    step = max(1, len(equity) // limit)
    pts = equity[::step]
    if pts[-1] is not equity[-1]:
        pts = list(pts) + [equity[-1]]
    return [{"time": p["time"], "equity": p["equity"]} for p in pts]


def equity_from_trades(trades, capital, limit=300):
    if not trades:
        return []
    eq = capital
    pts = []
    for t in sorted(trades, key=lambda x: x["exit_time"]):
        eq += t["pnl"]
        pts.append({"time": t["exit_time"], "equity": eq})
    return _downsample(pts, limit)


def _trade_sample(trades, n=200):
    keep = ("entry_time", "exit_time", "side", "entry_price", "exit_price", "qty", "pnl", "r_multiple", "exit_reason",
            "bars_held")
    step = max(1, len(trades) // n)
    return [{k: t.get(k) for k in keep} for t in trades[::step]][:n]


def param_space(cand: dict) -> dict:
    """name -> {type, min, max, step, default} or {choices} restricted to the spec's engine params."""
    spec_params = {p["name"]: p for p in cand["spec"].get("params") or []}
    raw = cand.get("param_space") or {}
    if isinstance(raw, list):
        raw = {p.get("name"): p for p in raw if isinstance(p, dict) and p.get("name")}
    out = {}
    for name, sp in spec_params.items():
        r = raw.get(name, sp) if isinstance(raw, dict) else sp
        if isinstance(r, (list, tuple)):
            out[name] = {"choices": list(r), "type": sp["type"], "default": sp["default"]}
        elif isinstance(r, dict) and r.get("choices"):
            out[name] = {"choices": list(r["choices"]), "type": sp["type"], "default": sp["default"]}
        elif isinstance(r, dict):
            out[name] = {"type": r.get("type", sp["type"]), "min": r.get("min", sp["min"]),
                         "max": r.get("max", sp["max"]), "step": r.get("step", sp.get("step")),
                         "default": sp["default"]}
    return out


def sample_params(space: dict, n: int, seed) -> list[dict]:
    """Defaults first, then seeded random configurations (unique)."""
    rng = random.Random(str(seed))
    out = [{k: v["default"] for k, v in space.items()}]
    seen = {protocol.canon(out[0])}
    attempts = 0
    while len(out) < n and attempts < n * 50:
        attempts += 1
        p = {}
        for k, v in space.items():
            if "choices" in v:
                p[k] = rng.choice(v["choices"])
            elif v["type"] == "int":
                step = int(v.get("step") or 1)
                lo, hi = int(v["min"]), int(v["max"])
                p[k] = lo + step * rng.randrange(0, max(1, (hi - lo) // step + 1))
            else:
                step = v.get("step")
                x = rng.uniform(float(v["min"]), float(v["max"]))
                p[k] = round(round(x / step) * step, 10) if step else round(x, 6)
        key = protocol.canon(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def optuna_status() -> dict:
    """Optional TPE optimiser: used only when ``import optuna`` works; never installed by the app."""
    try:
        import optuna  # noqa: F401
        return {"status": "AVAILABLE", "version": getattr(optuna, "__version__", None)}
    except Exception as exc:
        return {"status": "NOT_AVAILABLE", "reason": f"{type(exc).__name__}: {exc}"}


def tpe_params(space: dict, n: int, seed, objective_fn) -> list[dict] | None:
    """Optuna TPE proposals for ``n`` trials (``objective_fn(params) -> float`` is maximised). Returns None when
    Optuna is not available, so the caller falls back to seeded random sampling."""
    if optuna_status()["status"] != "AVAILABLE":
        return None
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize",
                                sampler=optuna.samplers.TPESampler(seed=int(hashlib.sha256(str(seed).encode())
                                                                           .hexdigest()[:8], 16)))
    out = []

    def obj(trial):
        p = {}
        for k, v in space.items():
            if "choices" in v:
                p[k] = trial.suggest_categorical(k, v["choices"])
            elif v["type"] == "int":
                p[k] = trial.suggest_int(k, int(v["min"]), int(v["max"]), step=int(v.get("step") or 1))
            else:
                p[k] = trial.suggest_float(k, float(v["min"]), float(v["max"]), step=v.get("step") or None)
        out.append(p)
        return float(objective_fn(p))

    study.optimize(obj, n_trials=n)
    return out


def pareto_ranks(points: list[tuple]) -> list[int]:
    """Non-dominated sorting (maximise every objective); rank 1 = Pareto front."""
    n = len(points)
    ranks = [0] * n
    remaining = set(range(n))
    r = 1
    while remaining:
        front = []
        for i in remaining:
            dominated = False
            for j in remaining:
                if j != i and all(a >= b for a, b in zip(points[j], points[i])) and any(
                        a > b for a, b in zip(points[j], points[i])):
                    dominated = True
                    break
            if not dominated:
                front.append(i)
        for i in front:
            ranks[i] = r
            remaining.discard(i)
        r += 1
    return ranks


def objectives(m: dict, q: dict) -> tuple:
    """(recovery, profit factor, year/window stability, feasibility) - all maximised."""
    feasible = 1.0 if m.get("trades_per_week", 0) >= 0.75 * q["min_trades_per_week"] and m.get("trades", 0) >= 20 else 0.0
    rf = m.get("recovery_factor") or 0.0
    rf = rf if m.get("net_profit", 0) > 0 else min(rf, 0.0) + m.get("net_profit", 0) / 1e6
    return (feasible, float(min(rf, 50.0)), float(min(m.get("profit_factor") or 0.0, 10.0)),
            float(m.get("stability") or 0.0))


def rank_trials(rows: list[dict], q: dict) -> list[dict]:
    pts = [objectives(r["metrics"], q) for r in rows]
    ranks = pareto_ranks(pts)
    # tie-break inside a front: mean of normalised objective ranks
    arr = np.array(pts, dtype=float)
    norm = np.zeros(len(rows))
    for j in range(arr.shape[1]):
        order = arr[:, j].argsort().argsort()
        norm += order / max(len(rows) - 1, 1)
    for r, pr, sc in zip(rows, ranks, norm):
        r["pareto_rank"], r["scalar"] = int(pr), float(sc)
    return sorted(rows, key=lambda r: (r["pareto_rank"], -r["scalar"]))


def representative_windows(bid: pd.DataFrame, start, end, n_windows: int, months: int) -> list[dict]:
    """n windows of ``months`` inside [start, end): the most distinct years by realized volatility and trend
    strength (|yearly return| / vol), measured on development data only. Deterministic."""
    s, e = protocol.utc(start), protocol.utc(end)
    d = bid["close"][(bid.index >= s) & (bid.index < e)].resample("1D").last().dropna()
    years = sorted(set(d.index.year))
    stats = []
    for y in years:
        x = d[d.index.year == y]
        if len(x) < 120:
            continue
        r = np.log(x).diff().dropna()
        vol = float(r.std() * math.sqrt(252))
        trend = float(abs(math.log(x.iloc[-1] / x.iloc[0])) / max(vol, 1e-9))
        stats.append({"year": int(y), "vol": vol, "trend": trend})
    if not stats:
        return []
    picks = []
    label = {}
    for key, rev, name in (("vol", True, "hoge volatiliteit"), ("vol", False, "lage volatiliteit"),
                           ("trend", True, "sterke trend"), ("trend", False, "zijwaarts")):
        for row in sorted(stats, key=lambda r: r[key], reverse=rev):
            if row["year"] not in label:
                label[row["year"]] = name
                picks.append(row)
                break
        if len(picks) >= n_windows:
            break
    for row in stats:  # fill when fewer distinct years than windows
        if len(picks) >= n_windows:
            break
        if row["year"] not in label:
            label[row["year"]] = "aanvullend"
            picks.append(row)
    out = []
    for row in sorted(picks, key=lambda r: r["year"]):
        ws = max(protocol.utc(f"{row['year']}-01-01"), s)
        we = min(ws + pd.DateOffset(months=months), e)
        out.append({"start": ws.strftime("%Y-%m-%d"), "end": we.strftime("%Y-%m-%d"), "year": row["year"],
                    "regime_nl": label[row["year"]], "vol": row["vol"], "trend": row["trend"]})
    return out


def _signals(spec, bid, params=None):
    t_ms = ((bid.index - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta("1ms")).to_numpy("int64")
    arrays = {"time": t_ms, **{c: bid[c].to_numpy(float) for c in ("open", "high", "low", "close", "volume")}}
    sig = engine.evaluate(spec, arrays, params)
    return np.asarray(sig["long_signal"], bool), np.asarray(sig["short_signal"], bool)


def causality_check(spec, bid, params=None, signal_fn=None, cuts=(0.5, 0.75, 0.9)) -> dict:
    """Signals computed on a truncated history must equal the full-history signals on the shared bars; any
    difference means the rule looks at future candles."""
    fn = signal_fn or _signals
    if len(bid) < 200:
        return {"ok": True, "detail": "te weinig bars voor causaliteitstest", "checked": 0}
    full_l, full_s = fn(spec, bid, params)
    for c in cuts:
        k = int(len(bid) * c)
        part_l, part_s = fn(spec, bid.iloc[:k], params)
        if not (np.array_equal(part_l[:k], full_l[:k]) and np.array_equal(part_s[:k], full_s[:k])):
            diff = int((part_l[:k] != full_l[:k]).sum() + (part_s[:k] != full_s[:k]).sum())
            return {"ok": False, "detail": f"{diff} signalen veranderen als toekomstige candles worden weggelaten "
                                           f"(afkap op {c:.0%})", "checked": len(cuts)}
    return {"ok": True, "detail": f"signalen gelijk bij {len(cuts)} afkappunten", "checked": len(cuts),
            "signals": int(full_l.sum() + full_s.sum())}


# ================================================================================================ stage runner
class StageReject(Exception):
    def __init__(self, reasons, status="REJECTED"):
        super().__init__("; ".join(reasons))
        self.reasons, self.status = list(reasons), status


class Env:
    """Everything a worker needs to run stages for one candidate."""

    def __init__(self, store: FactoryStore, run: dict, data: protocol.SearchData, *, ledger=None, vault=None,
                 generator=None, check=None, regimes=None, beat=None):
        self.store, self.run, self.data = store, run, data
        self.cfg, self.proto = run["config"], run["protocol"]
        self.q = self.cfg["quality_thresholds"]
        self.prof = self.cfg["profile_settings"]
        self.ledger, self.vault = ledger, vault
        self.gen = generator or generator_for(run)
        self._check = check or (lambda: None)
        self.regimes = regimes or {}
        self.beat = beat or (lambda: None)
        self.code_sha = self.cfg.get("code_sha256") or code_sha256()

    def check(self):
        self._check()
        self.beat()

    def market(self, cand) -> dict:
        return self.proto["markets"][f"{cand['symbol']}:{cand['timeframe']}"]


def _ledger_status(status):
    return {"PASS": "TESTED", "REJECTED": "REJECTED", "ERROR": "ERROR", "INSUFFICIENT_EVIDENCE": "REJECTED",
            "REUSED_HOLDOUT": "REJECTED", "TECHNICAL_BLOCKED": "ERROR"}.get(status, "TESTED")


def ledger_row(env: Env, cand, stage, status, *, params=None, metrics=None, reason=None, start=None, end=None,
               extra=None, oos_access=False) -> dict:
    mk = env.market(cand)
    key = f"factory|{cand['signature']}|{cand['symbol']}|{cand['timeframe']}"
    from .ledger import hypothesis_id
    p = params if params is not None else {x["name"]: x["default"] for x in cand["spec"].get("params") or []}
    stats = {"factory": {"run_id": env.run["run_id"], "candidate_id": cand["id"], "stage": stage,
                         "signature": cand["signature"], "blocks": cand.get("blocks"),
                         "iq_sources": cand.get("iq_sources"), "seed": env.cfg["seed"], "oos_access": oos_access,
                         "research_version": RESEARCH_VERSION, "profile": env.cfg["profile"],
                         "quality": env.cfg["quality"], "generator": env.run.get("generator"),
                         **(extra or {})}}
    return {"kind": "HOLDOUT" if oos_access else "DISCOVERY", "hypothesis_id": hypothesis_id(key, p),
            "parent_id": cand.get("parent"), "family": f"factory:{cand['family']}", "symbol": cand["symbol"],
            "timeframe": cand["timeframe"], "start": start, "end": end, "spec": cand["spec"], "params": p,
            "mutations": cand.get("passport", {}).get("mutations"), "code_sha256": env.code_sha,
            "dataset_sha256": mk.get("dataset_sha256"), "cost_model": mk.get("cost_model"),
            "data_requirements": DATA_REQUIREMENTS, "status": _ledger_status(status), "rejection_reason": reason,
            "metrics": _clean(metrics) if metrics else None, "statistics": _clean(stats)}


def _record(env: Env, row) -> str | None:
    if env.ledger is None:
        return None
    return env.ledger.record(**row)


def _decision(env: Env, cand, stage, decision, detail=None) -> None:
    if env.ledger is not None and hasattr(env.ledger, "record_decision"):
        env.ledger.record_decision(run_id=env.run["run_id"], candidate_id=cand["id"], stage=stage, decision=decision,
                                   detail=_clean(detail))


def _store_rows(env: Env) -> list[dict]:
    return _store_rows_for(env.store, env.run["run_id"])


def _store_rows_for(store, run_id) -> list[dict]:
    """Multiple-testing rows from the factory store when no ledger is configured (same shape as ledger rows)."""
    out = []
    for t in store.trials(run_id):
        if t.get("stage") == PBO_TRIAL_STAGE:        # same configurations as the L4 trials: not new trials
            continue
        m = t.get("metrics") or {}
        sr = m.get("sharpe_daily")
        out.append({"hypothesis_id": t.get("key"), "family": None, "group": t.get("cand_id"), "kind": "DISCOVERY",
                    "sr": sr / math.sqrt(252) if isinstance(sr, (int, float)) else None, "T": m.get("n_days"),
                    "stage": t.get("stage"), "n_eff_within": None, "rep_series": None})
    return out


class CandidateRunner:
    """Runs stages L1..L5 for one candidate; completed stages are loaded, never recomputed (idempotent resume)."""

    def __init__(self, env: Env, cand_row: dict):
        self.env = env
        self.row = cand_row
        self.cand = cand_row["candidate"]
        self.rid = env.run["run_id"]
        self.cid = self.cand["id"]
        self.state: dict = {}
        self._series: dict = {}
        protocol.verify_preregistration(env.cfg)

    def _done(self, stage):
        return self.env.store.stage_result(self.rid, self.cid, stage)

    def run(self, stages=("L1", "L2", "L3", "L4", "L5")) -> str:
        fns = {"L1": self.l1, "L2": self.l2, "L3": self.l3, "L4": self.l4, "L5": self.l5}
        status = "PASS"
        for st in stages:
            prev = self._done(st)
            if prev is not None:
                self.state[st] = prev
                if prev["status"] != "PASS":
                    return prev["status"]
                continue
            self.env.check()
            self.env.store.set_candidate(self.rid, self.cid, stage=st, status="IN_PROGRESS")
            t0 = time.monotonic()
            ledger_ids = []
            try:
                metrics, payload, ledger_ids = fns[st]()
                status, reasons = "PASS", []
            except StageReject as rej:
                status, reasons = rej.status, rej.reasons
                metrics, payload = self.state.get(f"{st}_metrics"), self.state.get(f"{st}_payload")
                ledger_ids = self.state.get(f"{st}_ledger", [])
            except (JobCancelled, KeyboardInterrupt):
                raise
            except protocol.SealedAccessError:
                raise
            except Exception as exc:
                status, reasons = "ERROR", [f"fout in {st}: {type(exc).__name__}: {exc}"]
                metrics, payload = None, None
            if not ledger_ids:
                ledger_ids = [_record(self.env, ledger_row(
                    self.env, self.cand, st, status, metrics=metrics, reason="; ".join(reasons) or None,
                    params=self.state.get(f"{st}_params"), extra=self.state.get(f"{st}_extra"),
                    start=(self.state.get(f"{st}_range") or [None, None])[0],
                    end=(self.state.get(f"{st}_range") or [None, None])[1]))]
            self.env.store.put_stage(self.rid, self.cid, st, status, time.monotonic() - t0, reasons, metrics,
                                     payload, ledger_ids)
            self.state[st] = self.env.store.stage_result(self.rid, self.cid, st)
            summary = _summary_from(metrics) if metrics else None
            if status != "PASS":
                self.env.store.set_candidate(self.rid, self.cid, stage=st, status=status, reasons=reasons,
                                             **({"summary": summary} if summary else {}))
                return status
            self.env.store.set_candidate(self.rid, self.cid, stage=st, status=STAGE_PASS_STATUS[st],
                                         **({"summary": summary} if summary else {}))
        return status

    # -------------------------------------------------------------- helpers
    def _bt(self, params, start, end, **kw):
        cm = self.env.market(self.cand)["cost_model"]
        kw.setdefault("commission_per_lot", cm["commission_per_lot"])
        return self.env.data.backtest(self.cand["spec"], self._full_params(params), start, end,
                                      capital=cm["capital"], **kw)

    def _full_params(self, params):
        names = {p["name"] for p in self.cand["spec"].get("params") or []}
        base = {p["name"]: p["default"] for p in self.cand["spec"].get("params") or []}
        base.update({k: v for k, v in (params or {}).items() if k in names})
        return base

    @property
    def capital(self):
        return self.env.market(self.cand)["cost_model"]["capital"]

    def _ranges(self):
        mk = self.env.market(self.cand)["ranges"]
        return mk["development"], mk["validation"]

    # -------------------------------------------------------------- L1
    def l1(self):
        env, cand = self.env, self.cand
        mk = env.market(cand)
        reasons, checks = [], []
        if mk["ranges"]["status"] == "INSUFFICIENT_SUPPORT":
            raise StageReject(["INSUFFICIENT_SUPPORT: " + "; ".join(mk["ranges"]["notes_nl"])],
                              "INSUFFICIENT_EVIDENCE")
        if env.vault is not None:
            rec = {"signature": cand["signature"], "family": cand["family"], "symbol": cand["symbol"],
                   "timeframe": cand["timeframe"], "lineage": [cand.get("parent")] if cand.get("parent") else [],
                   "params": {}}
            hits = env.vault.tested_lineage(protocol.lineage_keys(rec), env.cfg["sealed_start"])
            if hits:
                self.env.store.set_candidate(self.rid, self.cid, evidence_class="REUSED_HOLDOUT")
                raise StageReject([f"REUSED_HOLDOUT: deze lijn (signatuur/ouder/familie op {cand['symbol']} "
                                   f"{cand['timeframe']}) had al een verzegelde eindtest ({hits[0]['frozen_id']}); "
                                   "opnieuw optimaliseren of testen op dezelfde verzegelde periode is geblokkeerd"],
                                  "REUSED_HOLDOUT")
        dev, _ = self._ranges()
        b, a = env.data.slice(dev["start"], dev["end"])
        sample_b = b.iloc[-6000:] if len(b) > 6000 else b
        sample_a = a[a.index >= sample_b.index[0]] if len(sample_b) else a
        try:
            g = env.gen.level1_check(cand, sample_b, sample_a) or {}
        except Exception as exc:
            g = {"ok": False, "reasons": [f"level1_check fout: {type(exc).__name__}: {exc}"], "checks": []}
        gchecks = g.get("checks") or []
        if isinstance(gchecks, dict):
            gchecks = [{"name": k, **(v if isinstance(v, dict) else {"ok": bool(v)})} for k, v in gchecks.items()]
        checks.extend({**c, "source": "generator"} for c in gchecks)
        if g.get("warnings"):
            checks.append({"name": "generator_warnings", "ok": True, "detail": "; ".join(g["warnings"])[:500]})
        if not g.get("ok", False):
            reasons.extend(g.get("reasons") or ["generator level1_check niet geslaagd"])
        errs = engine.validate_spec(cand["spec"])
        checks.append({"name": "rule_engine_validate", "ok": not errs, "detail": "; ".join(errs[:3])})
        if errs:
            reasons.append("ongeldige spec: " + "; ".join(errs[:3]))
        else:
            try:
                code = codegen.generate_jesse_strategy(cand["spec"])
                compile(code, f"<{cand['id']}>", "exec")
                checks.append({"name": "codegen_compile", "ok": True, "detail": f"{len(code)} tekens, niet uitgevoerd",
                               "code_sha256": hashlib.sha256(code.encode()).hexdigest()})
            except Exception as exc:
                checks.append({"name": "codegen_compile", "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
                reasons.append(f"Jesse-codegeneratie/compile mislukt: {type(exc).__name__}: {exc}")
            cz = causality_check(cand["spec"], sample_b)
            checks.append({"name": "causality", **cz})
            if not cz["ok"]:
                reasons.append("gebruikt toekomstige candles (look-ahead): " + cz["detail"])
        miss = float(a["close"].reindex(b.index).isna().mean()) if len(b) else 1.0
        checks.append({"name": "ask_coverage", "ok": miss <= 0.05, "detail": f"{miss:.2%} ontbrekende Ask"})
        data_reasons = []
        if miss > 0.05:
            data_reasons.append(f"onvoldoende Ask-dekking ({miss:.1%} ontbreekt)")
        if len(b) < 2000:
            data_reasons.append(f"te weinig bars in ontwikkelperiode ({len(b)})")
        self.state["L1_payload"] = {"checks": checks}
        self.state["L1_range"] = [dev["start"], dev["end"]]
        if reasons or data_reasons:
            # a defective strategy is rejected; missing market data blocks the test without judging the strategy
            raise StageReject(reasons + data_reasons, "REJECTED" if reasons else "TECHNICAL_BLOCKED")
        return {"bars_development": len(b), "missing_ask": miss}, {"checks": checks}, []

    # -------------------------------------------------------------- L2
    def l2(self):
        env, q = self.env, self.env.q
        windows = env.market(self.cand)["windows"]
        if not windows:
            raise StageReject(["geen representatieve ontwikkelvensters beschikbaar"], "INSUFFICIENT_EVIDENCE")
        rows, all_trades, weeks = [], [], 0.0
        for w in windows:
            env.check()
            r = self._bt(None, w["start"], w["end"])
            m = metrics_of(r["trades"], self.capital, w["start"], w["end"])
            rows.append({**w, "metrics": m})
            all_trades.extend(r["trades"])
            weeks += weeks_between(w["start"], w["end"])
        tm = validation.trade_metrics(all_trades, self.capital)
        agg = {"trades": tm["n_trades"], "net_profit": tm["net_profit"], "profit_factor": tm["profit_factor"],
               "trades_per_week": tm["n_trades"] / max(weeks, 1e-9),
               "max_window_dd_pct": max((x["metrics"]["max_drawdown_pct"] for x in rows), default=0.0),
               "net_return_pct": tm["net_profit"] / self.capital * 100.0,
               "max_drawdown_pct": max((x["metrics"]["max_drawdown_pct"] for x in rows), default=0.0),
               "recovery_factor": tm["recovery_factor"]}
        reasons = []
        min_tpw = 0.4 * q["min_trades_per_week"]
        if tm["n_trades"] == 0:
            reasons.append("geen trades in de representatieve vensters")
        elif agg["trades_per_week"] < min_tpw:
            reasons.append(f"te weinig trades voor dit profiel ({agg['trades_per_week']:.2f}/week < {min_tpw:.2f})")
        if tm["n_trades"] and tm["profit_factor"] < 0.9:
            reasons.append(f"geen voorlopige edge na kosten (PF {tm['profit_factor']:.2f} < 0,90)")
        if agg["max_window_dd_pct"] > 2 * q["max_dd_pct"]:
            reasons.append(f"drawdown in een venster {agg['max_window_dd_pct']:.1f}% > {2 * q['max_dd_pct']:.0f}%")
        if tm["net_profit"] > 0 and all_trades:
            best_trade = max(t["pnl"] for t in all_trades)
            daily = validation.daily_pnl_from_trades(all_trades)
            best_day = float(daily.max()) if len(daily) else 0.0
            if best_trade > 0.5 * tm["net_profit"]:
                reasons.append("resultaat hangt aan een enkele trade (> 50% van de winst)")
            elif best_day > 0.5 * tm["net_profit"]:
                reasons.append("resultaat hangt aan een enkele dag (> 50% van de winst)")
        payload = {"windows": rows}
        self.state.update(L2_metrics=agg, L2_payload=payload, L2_range=[windows[0]["start"], windows[-1]["end"]])
        if reasons:
            raise StageReject(reasons)
        return agg, payload, []

    # -------------------------------------------------------------- L3
    def l3(self):
        env, q = self.env, self.env.q
        dev, _ = self._ranges()
        r = self._bt(None, dev["start"], dev["end"])
        trades = r["trades"]
        m = metrics_of(trades, self.capital, dev["start"], dev["end"])
        yb = validation.yearly_breakdown(trades)
        m["stability"] = yb.get("frac_positive") or 0.0
        dist = distribution.analyse(trades, self.capital, regimes=env.regimes)
        payload = {"equity": equity_from_trades(trades, self.capital), "yearly": yb,
                   "monthly": dist.get("monthly"), "sessions": dist.get("sessions"), "regimes": dist.get("regimes"),
                   "boundary_trades_dropped": r.get("boundary_trades_dropped", 0),
                   "range": [dev["start"], dev["end"]], "params": self._full_params(None)}
        reasons = []
        if m["trades_per_week"] < 0.5 * q["min_trades_per_week"]:
            reasons.append(f"te weinig trades over de ontwikkelperiode ({m['trades_per_week']:.2f}/week)")
        if m["trades"] and m["profit_factor"] < 0.95:
            reasons.append(f"ontwikkelperiode verliesgevend na kosten (PF {m['profit_factor']:.2f} < 0,95)")
        payload["max_bars_held"] = max((int(t.get("bars_held") or 0) for t in trades), default=None)
        self.state.update(L3_metrics=m, L3_payload=payload, L3_range=[dev["start"], dev["end"]],
                          L3_extra={"rep_series": effective_trials.compact_weekly(
                              validation.daily_pnl_from_trades(trades), self.capital)})
        if reasons:
            raise StageReject(reasons)
        return m, payload, []

    # -------------------------------------------------------------- L4
    def _trial(self, params, rung, start, end, extra_windows=None):
        """One optimisation trial, idempotent by key; recorded in the trials table AND the ledger."""
        env = self.env
        key = hashlib.sha256(protocol.canon([self.rid, self.cid, "L4", rung, params]).encode()).hexdigest()
        prev = env.store.trial(key)
        if prev is not None:
            return {"params": prev["params"], "metrics": prev["metrics"], "key": key, "reused": True}
        env.check()
        if extra_windows:
            trades, weeks, wins = [], 0.0, []
            for w in extra_windows:
                r = self._bt(params, w["start"], w["end"])
                trades.extend(r["trades"])
                weeks += weeks_between(w["start"], w["end"])
                wins.append(sum(t["pnl"] for t in r["trades"]) > 0)
            m = metrics_of(trades, self.capital, extra_windows[0]["start"], extra_windows[-1]["end"])
            m["trades_per_week"] = m["trades"] / max(weeks, 1e-9)
            m["stability"] = float(np.mean(wins)) if wins else 0.0
        else:
            r = self._bt(params, start, end)
            trades = r["trades"]
            m = metrics_of(trades, self.capital, start, end)
            m["stability"] = validation.yearly_breakdown(trades).get("frac_positive") or 0.0
        self._series[(rung, protocol.canon(params))] = validation.daily_pnl_from_trades(trades)
        lid = _record(env, ledger_row(env, self.cand, "L4", "PASS", params=self._full_params(params), metrics=m,
                                      start=start, end=end, extra={"trial_rung": rung}))
        env.store.put_trial(key, self.rid, self.cid, "L4", rung, params, m, lid)
        return {"params": params, "metrics": m, "key": key, "reused": False, "ledger_id": lid}

    def l4(self):
        env, q, prof = self.env, self.env.q, self.env.prof
        dev, _ = self._ranges()
        space = param_space(self.cand)
        n = max(1, int(env.cfg["trials"]))
        eta = max(2, int(prof["eta"]))
        configs = sample_params(space, n, f"{env.cfg['seed']}:{self.cid}") if space else [{}]
        opt = {"requested": prof.get("optimizer", "random"), "used": "random_successive_halving",
               "optuna": optuna_status()}
        if opt["requested"] == "tpe" and space and opt["optuna"]["status"] == "AVAILABLE":
            wins_ = env.market(self.cand)["windows"]
            tp = tpe_params(space, n, f"{env.cfg['seed']}:{self.cid}", lambda p: objectives(
                self._trial(p, "tpe_vensters", wins_[0]["start"] if wins_ else dev["start"],
                            wins_[-1]["end"] if wins_ else dev["end"], wins_ or None)["metrics"], q)[1])
            if tp:
                configs, opt["used"] = list({protocol.canon(p): p for p in tp}.values()), "optuna_tpe"
        windows = env.market(self.cand)["windows"]
        mid = protocol.utc(dev["start"]) + (protocol.utc(dev["end"]) - protocol.utc(dev["start"])) / 2
        rungs = [("r0_vensters", None, None, windows),
                 ("r1_tweede_helft", mid.strftime("%Y-%m-%d"), dev["end"], None),
                 ("r2_volledig", dev["start"], dev["end"], None)]
        if len(configs) <= 3:
            rungs = rungs[-1:]
        alive = configs
        history = []
        ranked = []
        for i, (name, s, e, wins) in enumerate(rungs):
            rows = [self._trial(p, name, s or windows[0]["start"], e or windows[-1]["end"], wins) for p in alive]
            ranked = rank_trials(rows, q)
            history.append({"rung": name, "n": len(rows),
                            "trials": [{"params": r["params"], "metrics": _slim(r["metrics"]),
                                        "pareto_rank": r["pareto_rank"]} for r in ranked]})
            if i < len(rungs) - 1:
                keep = max(1, math.ceil(len(ranked) / eta))
                alive = [r["params"] for r in ranked[:keep]]
                _decision(env, self.cand, "L4", f"rung {name}: {len(rows)} configuraties, {keep} door",
                          {"kept": alive, "dropped": [r["params"] for r in ranked[keep:]], "eta": eta})
        best = ranked[0]
        within = self._within_effective_n(rungs[0][0], len(configs))
        top_k = [r["params"] for r in ranked[: max(1, int(prof["wf_top_k"]))]]
        m = dict(best["metrics"])
        n_trials = sum(h["n"] for h in history)
        payload = {"objective": "Pareto (haalbaarheid frequentie, recovery factor, profit factor, stabiliteit per "
                                "jaar); binnen een front gemiddelde rangorde", "successive_halving": {"eta": eta},
                   "rungs": history, "best": {"params": best["params"], "metrics": m}, "top_k": top_k,
                   "n_trials": n_trials, "param_space": space, "range": [dev["start"], dev["end"]],
                   "effective_trials": within, "optimizer": opt}
        reasons = []
        if m["net_profit"] <= 0:
            reasons.append(f"ook na optimalisatie verliesgevend in de ontwikkelperiode (netto {m['net_profit']:.0f})")
        if m["profit_factor"] < 1.05:
            reasons.append(f"profit factor na optimalisatie te laag ({m['profit_factor']:.2f} < 1,05)")
        if m["trades_per_week"] < 0.75 * q["min_trades_per_week"]:
            reasons.append(f"frequentie na optimalisatie te laag ({m['trades_per_week']:.2f}/week)")
        if m["max_drawdown_pct"] > 1.5 * q["max_dd_pct"]:
            reasons.append(f"drawdown ontwikkelperiode {m['max_drawdown_pct']:.1f}% > {1.5 * q['max_dd_pct']:.1f}%")
        if m["trades"] < 30:
            reasons.append(f"te weinig trades in de ontwikkelperiode ({m['trades']})")
        self.state.update(L4_metrics=m, L4_payload=payload, L4_range=[dev["start"], dev["end"]],
                          L4_params=self._full_params(best["params"]),
                          L4_extra={"n_eff_within": within.get("n_eff_within"), "n_configs": len(configs)})
        if reasons:
            raise StageReject(reasons)
        return m, payload, []

    def _within_effective_n(self, rung: str, n_configs: int) -> dict:
        """Effective number of independent configurations of this candidate, from the daily P&L of the first rung
        (all configurations on the same windows). Missing series (resumed run) -> not measured (raw count used)."""
        ser = {k[1]: v for k, v in self._series.items() if k[0] == rung and len(v)}
        if len(ser) < 3 or n_configs < 3:
            return {"n_eff_within": None, "n_configs": n_configs, "measured_series": len(ser),
                    "note": "te weinig reeksen: ruwe telling"}
        frame = pd.concat(ser, axis=1).fillna(0.0)
        frame = frame.loc[:, frame.std() > 0]
        if frame.shape[1] < 2:
            return {"n_eff_within": 1.0, "n_configs": n_configs, "measured_series": len(ser),
                    "note": "alle configuraties identiek of zonder trades"}
        en = effective_trials.effective_n(frame, seed=int(self.env.cfg["seed"]))
        ratio = en["n_eff"] / max(en["n_raw"], 1)
        return {"n_eff_within": float(max(1.0, ratio * n_configs)), "n_configs": n_configs,
                "measured_series": len(ser), **{k: en[k] for k in ("eigen", "onc_k", "mean_abs_corr", "method")}}

    # -------------------------------------------------------------- L5
    def l5(self):
        """Nested walk-forward on the validation period (rolling + expanding, purged), robustness and the
        preliminary multiple-testing record. The final multiplicity gate runs at L6 over the complete search."""
        env, q, prof = self.env, self.env.q, self.env.prof
        dev, val = self._ranges()
        l4 = self.state.get("L4") or self._done("L4")
        best = l4["payload"]["best"]["params"]
        top_k = l4["payload"]["top_k"] or [best]
        space = param_space(self.cand)
        capital = self.capital
        cm = env.market(self.cand)["cost_model"]
        spec = self.cand["spec"]
        variants = {"basis": spec}
        if spec.get("session") and prof.get("nested_filter_variants", True):
            variants.update({n: s for n, s in robustness.spec_variants(spec) if n == "zonder_sessiefilter"})
        configs = [{"variant": v, "params": self._full_params(p)} for v in variants for p in top_k]
        memo: dict = {}

        def bt_spec(vspec, params, start, end, **kw):
            env.check()
            kw.setdefault("commission_per_lot", cm["commission_per_lot"])
            return env.data.backtest(vspec, self._full_params(params), start, end, capital=cm["capital"], **kw)

        def full(cfg):
            key = protocol.canon(cfg)
            if key not in memo:
                memo[key] = bt_spec(variants[cfg["variant"]], cfg["params"], dev["start"], val["end"])["trades"]
            return memo[key]

        def nested_fn(cfg, start, end):
            s0, e0 = protocol.utc(start), protocol.utc(end)
            return {"trades": [t for t in full(cfg) if s0 <= protocol.utc(t["entry_time"]) < e0]}

        v0 = protocol.utc(val["start"])
        d0 = protocol.utc(dev["start"])
        train_roll = min(3 * 365, (v0 - d0).days)
        test_days = 182
        min_tr = max(5, int(q["min_trades_per_week"] * 26 * 0.4))
        gap = nested_wf.dependency_gap(spec, self.cand["timeframe"], self._full_params(best), trades=full(configs[0]))
        common = dict(test_days=test_days, gap=gap, objective="sharpe", min_trades=min_tr, capital=capital)
        roll = nested_wf.nested_walk_forward(nested_fn, configs, (v0 - pd.Timedelta(days=train_roll)).strftime(
            "%Y-%m-%d"), val["end"], train_days=train_roll, mode="rolling", **common)
        expd = nested_wf.nested_walk_forward(nested_fn, configs, dev["start"], val["end"], train_days=(v0 - d0).days,
                                             mode="expanding", **common)
        oos_trades = roll.pop("oos_trades", [])
        exp_trades = expd.pop("oos_trades", [])
        roll.pop("oos_equity", None)
        expd.pop("oos_equity", None)
        for w in roll["windows"]:
            _decision(env, self.cand, "L5", f"WF venster {w['index']} ({w['test'][0]}): {w.get('variant')}",
                      {"params": w.get("params"), "inner": w.get("inner"), "status": w["status"]})
        oos_start = roll["windows"][0]["test"][0] if roll["windows"] else val["start"]
        oos_end = roll["windows"][-1]["test"][1] if roll["windows"] else val["end"]
        om = metrics_of(oos_trades, capital, oos_start, oos_end)
        em = metrics_of(exp_trades, capital, oos_start, oos_end)
        last_ok = next((w for w in reversed(roll["windows"]) if w["status"] == "OK"), None)
        chosen = (last_ok or {}).get("config") or configs[0]
        fspec, fparams = variants[chosen["variant"]], self._full_params(chosen["params"])

        def fn(params, start, end, **kw):
            return bt_spec(fspec, params, start, end, **kw)

        # robustness on the validation period for the configuration a walk-forward deployment would freeze
        neigh = robustness.parameter_neighbourhood(fn, fparams, space, val["start"], val["end"], capital)
        stability = {"status": neigh.get("status"), "frac_positive": neigh.get("frac_positive"),
                     "classification": neigh.get("classification"),
                     "neighbours": [{"param": n["param"], "value": n["value"], "net_profit": n["objective"],
                                     "n_trades": None, "unit": n["unit"]} for n in neigh.get("neighbours", [])]}
        scen = validation.default_cost_scenarios(cm["commission_per_lot"], 2.0)
        cost = validation.cost_stress(fn, fparams, val["start"], val["end"], scenarios=scen, capital=capital)
        execution = robustness.execution_stress(fn, fparams, oos_start, oos_end, capital, cm["commission_per_lot"])
        mc = validation.monte_carlo(oos_trades, capital, n=int(prof["monte_carlo_n"]), seed=env.cfg["seed"])
        mc.pop("equity_bands", None)
        bid, ask = env.data.slice(oos_start, oos_end, warmup_days=400)
        bid_oos = bid[bid.index >= protocol.utc(oos_start)]
        ask_oos = ask[ask.index >= protocol.utc(oos_start)]
        entry_ts = (pd.to_datetime([t["entry_time"] for t in oos_trades], utc=True).tz_localize(None)
                    if oos_trades else pd.DatetimeIndex([]))
        spread_pts = robustness.spread_at(bid_oos, ask_oos, entry_ts) if len(entry_ts) and len(bid_oos) else None
        cost_mc = robustness.cost_monte_carlo(oos_trades, spread_points=spread_pts, n=int(prof["monte_carlo_n"]),
                                              seed=env.cfg["seed"])
        timing = robustness.timing_monte_carlo(oos_trades, bid_oos, ask_oos, n=min(300, int(prof["monte_carlo_n"])),
                                               seed=env.cfg["seed"])
        adverse = robustness.adverse_fill(oos_trades, bid_oos, ask_oos, 0.25)
        null = robustness.random_entry_null(oos_trades, bid_oos, ask_oos, n=300, seed=env.cfg["seed"])
        labels = robustness.causal_regimes(bid, ask)
        regimes = robustness.regime_stability(oos_trades, labels)
        conc = robustness.concentration(oos_trades, capital, oos_start, oos_end)
        env.check()
        cost_fp = hashlib.sha256(protocol.canon({"capital": cm["capital"], "commission_per_lot": cm["commission_per_lot"],
                                                 "data": "same SearchData bid/ask"}).encode()).hexdigest()
        pbo = pbo_over_l4(l4["payload"], lambda p: bt_spec(spec, p, val["start"], val["end"])["trades"], capital,
                          val_range=(val["start"], val["end"]), cost_fp=cost_fp, chosen_cost_fp=cost_fp,
                          max_pbo=q["max_pbo"])
        n_pbo_reg = register_pbo_configs(env, self.rid, self.cid, pbo)
        _decision(env, self.cand, "L5", f"PBO-set: {pbo['n_eligible']}/{pbo['n_configs']} L4-configuraties "
                  f"(status {pbo['status']})", {"preconditions": pbo["preconditions"], "excluded": pbo["excluded"],
                                               "registered_new": n_pbo_reg, "pbo": pbo.get("pbo")})
        daily = validation.daily_returns_from_trades(oos_trades, capital)
        cis = robustness.bootstrap_cis(daily.to_numpy(), [t["pnl"] for t in oos_trades], n=1000,
                                       seed=env.cfg["seed"])
        eff_obs = robustness.effective_independent_obs(daily.to_numpy(), oos_trades)
        mt = effective_trials.multiple_testing_records(
            daily.to_numpy(), env.ledger, family=f"factory:{self.cand['family']}",
            extra_trials=None if env.ledger is not None else _store_rows(env), seed=int(env.cfg["seed"]))
        dsr = mt["global"]
        n_trials = dsr["raw_n"]
        psr = validation.probabilistic_sharpe_ratio(daily)
        db = validation.drop_best(oos_trades)
        yb = validation.yearly_breakdown(oos_trades)
        dist = distribution.analyse(oos_trades, capital, regimes=env.regimes)
        boot = validation.bootstrap_confidence_interval([t["pnl"] for t in oos_trades], np.mean, n=1000, block="auto",
                                                        seed=env.cfg["seed"])
        perf_ci = performance_cis([t["pnl"] for t in oos_trades], seed=int(env.cfg["seed"]))
        d0_, d1_ = protocol.utc(dev["start"]), protocol.utc(dev["end"])
        dev_m = metrics_of([t for t in full(chosen) if d0_ <= protocol.utc(t["entry_time"]) < d1_], capital,
                           dev["start"], dev["end"])
        dual = dual_positive_gate(dev_m, om)
        reuse = (env.ledger.validation_reuse(self.cand["symbol"], val["start"], val["end"], exclude_run=self.rid)
                 if env.ledger is not None else {"evidence_class": "FIRST_USE_SELECTION", "reused": False})
        evidence = {"n_trades": om["trades"], "net_profit": om["net_profit"], "profit_factor": om["profit_factor"],
                    "max_drawdown_pct": om["max_drawdown_pct"], "psr": psr, "dsr": dsr, "n_trials": n_trials,
                    "pbo_result": pbo, "walk_forward": roll, "cost_stress": cost,
                    "parameter_stability": stability, "drop_best": db, "yearly": yb}
        th = {"deferred_checks": ["dsr", "psr"],
              "min_trades": q["min_oos_trades"], "min_profit_factor": q["min_pf_oos"],
              "max_drawdown_pct": q["max_dd_pct"], "max_pbo": q["max_pbo"], "min_wfe": q["min_wfe"],
              "min_dsr": q["min_dsr"], "min_psr": q["min_psr"], "min_param_stability": q["min_param_stability"],
              "min_oos_trades": q["min_oos_trades"], "pbo_review": True}
        sc = validation.scorecard(evidence, th)
        checks = quality_checks(om, q, dist=dist, cost=cost, stability=stability, pbo=pbo, roll=roll, expd=em,
                                boot=boot, oos_start=oos_start, oos_end=oos_end,
                                pf_ci=perf_ci["profit_factor"], rf_ci=perf_ci["recovery_factor"])
        checks.append(single_window_check(roll["single_window_dependence"]))
        checks.append(_chk("dual_positive", dual["status"], dual["dev_net"], 0.0,
                           "Ontwikkeling en walk-forward OOS beide positief." if dual["status"] == "PASS" else
                           "; ".join(dual["reasons_nl"]) + "."))
        baseline = self._baselines(val, oos_start, oos_end, om)
        simpler = {}
        for name, vs in robustness.spec_variants(fspec):
            if engine.validate_spec(vs):
                continue
            r = bt_spec(vs, fparams, oos_start, oos_end)
            simpler[name] = _slim(metrics_of(r["trades"], capital, oos_start, oos_end))
        baseline["simpler_variants"] = simpler
        ev = robustness.verdict(oos_net=om["net_profit"], eff_obs=eff_obs, cis=cis, null=null,
                                baselines_beaten=baseline.get("beats_baseline"), cost_mc=cost_mc, execution=execution,
                                neighbourhood=neigh, conc=conc, regimes=regimes, simpler_variants=simpler)
        mt_gate = effective_trials.gate(mt, q["min_dsr"])
        decision = l5_decision(checks, sc, baseline.get("beats_baseline"), ev)
        wf_fail, insufficient = decision["wf_fail"], decision["insufficient"]
        promotion = {"walk_forward_pass": not wf_fail and not insufficient,
                     "robustness_pass": ev["verdict"] == "SUPPORTED_UNDER_ASSUMPTIONS",
                     "multiple_testing_preliminary": mt_gate["status"]}
        payload = {
            "walk_forward": {"rolling": _wf_slim(roll), "expanding": _wf_slim(expd),
                             "oos_equity": equity_from_trades(oos_trades, capital),
                             "expanding_oos_equity": equity_from_trades(exp_trades, capital),
                             "oos_range": [oos_start, oos_end], "oos_metrics": om, "expanding_oos_metrics": em,
                             "gap": gap, "variants": list(variants), "nested": True},
            "robustness": {"cost_stress": cost, "perturbation": stability, "monte_carlo": mc, "pbo": pbo,
                           "neighbourhood": neigh, "execution_stress": execution, "cost_monte_carlo": cost_mc,
                           "timing_monte_carlo": timing, "adverse_fill": adverse, "random_entry_null": null,
                           "regime_stability": regimes, "concentration": conc, "bootstrap": cis,
                           "effective_observations": eff_obs},
            "statistics": {"psr": psr, "dsr": dsr, "n_trials": n_trials, "sr_variance": dsr.get("V"),
                           "dsr_records": mt, "multiple_testing_gate": mt_gate,
                           "trade_pnl_ci": boot, "drop_best": db, "yearly": yb, "configs_evaluated": len(configs)},
            "distribution": dist, "scorecard": sc, "quality_checks": checks, "baseline_comparison": baseline,
            "evidence_verdict": ev, "evidence_class_validation": reuse, "promotion": promotion,
            "dual_positive": dual, "performance_cis": perf_ci, "dev_metrics_chosen": _slim(dev_m),
            "pbo_review": {"required": bool(decision["review_required"]), "pbo": pbo.get("pbo"),
                           "max_pbo": q["max_pbo"], "state": "PENDING" if decision["review_required"] else None,
                           "config_set": pbo["config_set"], "n_configs": pbo["n_configs"],
                           "n_eligible": pbo["n_eligible"]},
            "trades_sample": _trade_sample(oos_trades), "params": fparams, "variant": chosen["variant"],
            "spec": fspec if chosen["variant"] != "basis" else None,
            "entry_days": sorted({str(t["entry_time"])[:10] for t in oos_trades}),
            "daily_returns": {k.strftime("%Y-%m-%d"): float(v) for k, v in daily.items()},
        }
        self._record_neighbour_trials(stability, val, fparams)
        reasons, insufficient = decision["reasons"], decision["insufficient"]
        metrics = {**om, "wfe": roll.get("wfe"), "wfe_pooled": roll.get("wfe_pooled"),
                   "expanding_net": em["net_profit"], "dsr": dsr.get("value"), "dsr_family": mt["family"].get("value"),
                   "psr": psr.get("value"), "pbo": pbo.get("pbo"), "pbo_status": pbo.get("status"),
                   "pbo_review_required": decision["review_required"], "verdict": sc["verdict"],
                   "evidence_verdict": ev["verdict"], "effective_n": dsr.get("effective_n"), "raw_n": dsr.get("raw_n"),
                   "walk_forward_pass": promotion["walk_forward_pass"], "dual_positive": dual["status"],
                   "dev_net_chosen": dev_m["net_profit"], "dev_pf_chosen": dev_m["profit_factor"],
                   "robustness_pass": promotion["robustness_pass"],
                   "spread50_net": (cost.get("scenarios", {}).get("spread_+50%") or {}).get("net_profit")}
        self.state.update(L5_metrics=metrics, L5_payload=payload, L5_range=[oos_start, oos_end],
                          L5_params=fparams, L5_extra={"evidence_verdict": ev["verdict"], "promotion": promotion})
        if reasons:
            raise StageReject(reasons)
        if insufficient:
            raise StageReject(insufficient, "INSUFFICIENT_EVIDENCE")
        if decision["review_required"]:
            raise StageReject(decision["review"], "REVIEW_REQUIRED")
        return metrics, payload, []

    def _record_neighbour_trials(self, stability, val, base_params=None):
        """Perturbation neighbours are configurations that were looked at: they count as trials (ledger + store)."""
        env = self.env
        best = dict(base_params) if base_params else None
        for nb in (stability or {}).get("neighbours") or []:
            if best is None:
                l4 = self.state.get("L4") or self._done("L4")
                best = dict(l4["payload"]["best"]["params"])
            params = {**best, nb["param"]: nb["value"]}
            key = hashlib.sha256(protocol.canon([self.rid, self.cid, "L5", "perturbation", params]).encode()).hexdigest()
            if env.store.trial(key) is not None:
                continue
            m = {"net_profit": nb.get("net_profit"), "trades": nb.get("n_trades")}
            lid = _record(env, ledger_row(env, self.cand, "L5", "PASS", params=self._full_params(params), metrics=m,
                                          start=val["start"], end=val["end"], extra={"trial_rung": "L5_perturbation"}))
            env.store.put_trial(key, self.rid, self.cid, "L5", "perturbation", params, m, lid)

    def _baselines(self, val, oos_start, oos_end, om) -> dict:
        env = self.env
        out = {"baselines": [], "beats_baseline": None}
        b, _ = env.data.slice(oos_start, oos_end)
        if len(b) > 2:
            ret = float(b["close"].iloc[-1] / b["close"].iloc[0] - 1.0) * 100.0
            out["buy_and_hold_return_pct"] = ret
        try:
            bases = env.gen.baselines(self.cand["symbol"], self.cand["timeframe"]) or []
        except Exception as exc:
            bases = []
            out["error"] = f"{type(exc).__name__}: {exc}"
        best_net = None
        for base in bases[:3]:
            spec = base.get("spec") if isinstance(base, dict) else None
            if not spec or engine.validate_spec(spec):
                continue
            cm = env.market(self.cand)["cost_model"]
            r = env.data.backtest(spec, None, oos_start, oos_end, capital=cm["capital"],
                                  commission_per_lot=cm["commission_per_lot"])
            m = metrics_of(r["trades"], self.capital, oos_start, oos_end)
            out["baselines"].append({"id": base.get("id"), "name": base.get("name"), "metrics": _slim(m)})
            best_net = m["net_profit"] if best_net is None else max(best_net, m["net_profit"])
        if best_net is not None:
            out["beats_baseline"] = bool(om["net_profit"] > max(best_net, 0.0))
        out["note_nl"] = ("Baselines over dezelfde walk-forward OOS-periode met standaardparameters. Buy-and-hold "
                          "wordt getoond als marktreferentie (geen gelijke risico-inzet, dus geen harde eis).")
        return out


STAGE_PASS_STATUS = {"L1": "TECHNICALLY_VALID", "L2": "QUICK_PASSED", "L3": "DEV_TESTED", "L4": "DEV_VALIDATED",
                     "L5": "WF_PASSED"}


def _slim(m):
    keys = ("trades", "net_profit", "net_return_pct", "profit_factor", "max_drawdown_pct", "recovery_factor",
            "trades_per_week", "stability", "sharpe_daily", "win_rate")
    return {k: m.get(k) for k in keys if k in m}


def _wf_slim(wf):
    out = {k: v for k, v in wf.items() if k not in ("oos_trades",)}
    out["windows"] = [{k: (_slim(v) if k in ("train_metrics", "test_metrics") and isinstance(v, dict) else v)
                       for k, v in w.items()} for w in wf.get("windows", [])]
    om = out.get("oos_metrics")
    if isinstance(om, dict):
        out["oos_metrics"] = om
    return out


def _summary_from(m):
    return {"trades": m.get("trades"), "net_return_pct": m.get("net_return_pct"),
            "max_dd_pct": m.get("max_drawdown_pct", m.get("max_window_dd_pct")),
            "profit_factor": m.get("profit_factor"), "recovery_factor": m.get("recovery_factor"),
            "trades_per_week": m.get("trades_per_week")}


def _chk(name, status, value, threshold, text):
    return {"name": name, "status": status, "value": value, "threshold": threshold, "explanation_nl": text}


MIN_TRADES_TO_REJECT = 30    # below this a loss or a shortfall is not evidence of failure


def _pf_stat(x):
    x = np.asarray(x, dtype=float)
    loss = -x[x < 0].sum()
    return float(min(x[x > 0].sum() / loss, validation.PF_CAP)) if loss > 0 else float(validation.PF_CAP)


def _rf_stat(x):
    eq = np.concatenate([[0.0], np.cumsum(np.asarray(x, dtype=float))])
    dd = float((np.maximum.accumulate(eq) - eq).max())
    net = float(eq[-1])
    return float(min(net / dd, validation.PF_CAP)) if dd > 0 else (float(validation.PF_CAP) if net > 0 else 0.0)


def performance_cis(pnls, n: int = 1000, seed: int = 0) -> dict:
    """Block-bootstrap 95% CIs of OOS profit factor and recovery factor (trade order kept within blocks)."""
    return {"profit_factor": validation.bootstrap_confidence_interval(pnls, _pf_stat, n=n, block="auto", seed=seed),
            "recovery_factor": validation.bootstrap_confidence_interval(pnls, _rf_stat, n=n, block="auto", seed=seed)}


def dual_positive_gate(dev: dict | None, oos: dict | None) -> dict:
    """User requirement for automatically discovered strategies: promotion (and being shown as a candidate) needs
    BOTH a positive development backtest after costs (net > 0 and PF > 1) AND a positive walk-forward OOS result
    (net > 0 and expectancy > 0). One positive and one negative is never promoted. A negative OOS on fewer than
    MIN_TRADES_TO_REJECT trades is INSUFFICIENT_EVIDENCE (not promoted, not called a loss)."""
    dev, oos = dev or {}, oos or {}
    d_net, d_pf = dev.get("net_profit"), dev.get("profit_factor")
    o_net, o_n = oos.get("net_profit"), oos.get("trades") or 0
    o_exp = oos.get("expectancy")
    if o_exp is None and o_net is not None and o_n:
        o_exp = o_net / o_n
    dev_ok = d_net is not None and d_pf is not None and d_net > 0 and d_pf > 1.0
    oos_ok = o_net is not None and o_net > 0 and (o_exp is None or o_exp > 0)
    reasons = []
    if not dev_ok:
        reasons.append("ontwikkelbacktest niet positief na kosten" +
                       (f" (netto {d_net:.0f}, PF {d_pf:.2f})" if d_net is not None and d_pf is not None
                        else " (niet beschikbaar)"))
    if not oos_ok:
        reasons.append("walk-forward OOS niet positief" +
                       (f" (netto {o_net:.0f} over {o_n} trades)" if o_net is not None else " (niet beschikbaar)"))
    if dev_ok and oos_ok:
        status = "PASS"
    elif d_net is None or o_net is None or (dev_ok and o_n < MIN_TRADES_TO_REJECT):
        status = "INSUFFICIENT_EVIDENCE"
    else:
        status = "FAIL"
    return {"status": status, "dev_positive": dev_ok, "oos_positive": oos_ok, "dev_net": d_net, "dev_pf": d_pf,
            "oos_net": o_net, "oos_trades": o_n, "reasons_nl": reasons,
            "rule_nl": "ontwikkeling (netto > 0, PF > 1) EN walk-forward OOS (netto > 0, verwachting > 0) "
                       "moeten beide positief zijn"}


def l5_decision(checks: list, sc: dict, beats_baseline, ev: dict) -> dict:
    """L5 pass/fail logic, pure (shared with the V10.2 pipeline simulation). ``reasons`` = evidence of failure
    (stage REJECTED), ``insufficient`` = missing evidence (INSUFFICIENT_EVIDENCE). PSR/DSR are deferred to the single
    L6 multiplicity gate (``scorecard`` threshold ``deferred_checks``): a second, preliminary DSR gate would be the
    same correction applied twice and would call a positive-Sharpe DSR shortfall a rejection. A scorecard that is only
    NEEDS_MORE_DATA through INSUFFICIENT_EVIDENCE checks (no FAIL) is missing evidence, not a failure."""
    wf_fail = [c["explanation_nl"] for c in checks if c["status"] == "FAIL"]
    insufficient = [c["explanation_nl"] for c in checks if c["status"] == "INSUFFICIENT_EVIDENCE"]
    review = [c["explanation_nl"] for c in checks if c["status"] == "REVIEW_REQUIRED"]
    reasons = list(wf_fail)
    if sc["verdict"] not in ("OOS_PASSED", "FORWARD_ELIGIBLE"):
        text = f"scorecard {sc['verdict']}: " + "; ".join(sc.get("notes_nl") or [])
        if sc["verdict"] == "NEEDS_MORE_DATA" and not sc["counts"]["FAIL"]:
            insufficient.append(text)
        else:
            reasons.append(text)
    if beats_baseline is False:
        reasons.append("verslaat de naive baseline niet over dezelfde OOS-periode")
    if not reasons and not insufficient and ev["verdict"] != "SUPPORTED_UNDER_ASSUMPTIONS":
        if ev["verdict"] == validation.INSUFFICIENT:
            insufficient.extend(ev["reasons"])
        else:
            reasons.append(f"robuustheid {ev['verdict']}: " + "; ".join(ev["reasons"]))
    # PBO above the limit is the only reason to ask for a review: any other failure or missing evidence decides first
    # (REJECTED / INSUFFICIENT_EVIDENCE), with the PBO warning kept among the reasons
    review_required = bool(review) and not reasons and not insufficient
    if review and not review_required:
        (reasons if reasons else insufficient).extend(review)
    return {"wf_fail": wf_fail, "reasons": reasons, "insufficient": insufficient, "review": review if review_required else [],
            "review_required": review_required}


def single_window_check(sw: dict) -> dict:
    """Single-window dependence is only decidable with enough windows; with fewer it is INSUFFICIENT_EVIDENCE."""
    if not sw.get("flag"):
        return _chk("single_window_dependence", "PASS", sw.get("best_window_share"), 0.5,
                    "OOS-resultaat niet afhankelijk van een enkel venster.")
    n, need = sw.get("n_windows"), sw.get("min_windows", 4)
    if n is not None and n < need:
        return _chk("single_window_dependence", "INSUFFICIENT_EVIDENCE", sw.get("best_window_share"), 0.5,
                    f"Slechts {n} walk-forward-vensters (minimaal {need}): afhankelijkheid van een enkel venster "
                    "is niet te beoordelen.")
    return _chk("single_window_dependence", "FAIL", sw.get("best_window_share"), 0.5,
                "OOS-resultaat leunt op een enkel venster.")


def _threshold_status(value, threshold, ci, enough: bool, losing: bool, n: int) -> str:
    """PASS when the estimate meets the threshold. FAIL needs evidence: a bootstrap CI entirely below the
    threshold, or (without a CI) enough trades, or a structural loss on at least MIN_TRADES_TO_REJECT trades.
    Otherwise the shortfall is not distinguishable from noise: INSUFFICIENT_EVIDENCE."""
    if value >= threshold:
        return "PASS"
    if losing and n >= MIN_TRADES_TO_REJECT:
        return "FAIL"
    if ci is not None and ci.get("status") == "OK" and ci.get("ci_high") is not None:
        return "FAIL" if ci["ci_high"] < threshold else "INSUFFICIENT_EVIDENCE"
    return "FAIL" if enough else "INSUFFICIENT_EVIDENCE"


def config_hash(params: dict) -> str:
    return hashlib.sha256(protocol.canon(params).encode()).hexdigest()


def pbo_over_l4(l4_payload: dict, trades_fn, capital: float, *, val_range=None, cost_fp=None, chosen_cost_fp=None,
                max_pbo: float | None = None) -> dict:
    """PBO (CSCV) over every configuration of the L4 search (first rung = all sampled configurations), each
    backtested on the validation period. Top-K alone ranks near-equivalent plateau configurations (PBO ~ 0.5 by
    construction); the full trial set is what CSCV assumes (owner decision 2026-11-11, STATISTICAL_SELECTION_AUDIT 6.7).

    CSCV preconditions (STATISTICAL_SELECTION_AUDIT 6b) are checked here; a FAIL gives ``status`` INSUFFICIENT_EVIDENCE
    and ``pbo`` None, never a number. Configurations are excluded only for technical reasons (backtest error, no trade
    on the validation period), never for their performance; every exclusion is returned with its reason.
    ``val_range`` = (start, end) of the validation period; ``cost_fp`` / ``chosen_cost_fp`` = fingerprints of the cost
    model / capital used for the PBO backtests and for the chosen configuration."""
    rungs = (l4_payload or {}).get("rungs") or []
    params = list({protocol.canon(t["params"]): t["params"] for t in (rungs[0]["trials"] if rungs else [])}.values())
    pre: dict = {}
    notes: dict = {}

    def mark(name, status, text):
        pre[name], notes[name] = status, text

    out = {"status": validation.INSUFFICIENT, "pbo": None, "config_set": "L4_all", "n_configs": len(params),
           "n_eligible": 0, "excluded": [], "configs": [], "preconditions": pre, "precondition_notes": notes,
           "precondition_failed": False, "applicable": True, "max_pbo": max_pbo}
    wanted = [(l4_payload or {}).get("best", {}).get("params")] + list((l4_payload or {}).get("top_k") or [])
    in_set = {protocol.canon(p) for p in params}
    missing = [w for w in wanted if w is not None and protocol.canon(w) not in in_set]
    if missing:
        mark("selection_set", "FAIL", f"{len(missing)} configuratie(s) die L4 koos (best/top-K) zitten niet in de PBO-set")
    else:
        mark("selection_set", "PASS", "de PBO-set is de volledige eerste L4-rung (alle gesamplede configuraties) en bevat "
                                      "de gekozen best/top-K")
    mark("metric", "NOTE", "CSCV selecteert op de Sharpe van dagrendementen (validatieperiode); L4 selecteert op "
                           "Pareto-rangorde (ontwikkelperiode). PBO meet daarom de Sharpe-selectie, niet de Pareto-selectie")
    if len(params) < 2:
        out["applicable"] = False
        mark("enough_configs", "NOTE", f"{len(params)} configuratie(s): PBO niet van toepassing")
        return out
    cols, out_of_range, no_cost_mix = [], 0, False
    lo, hi = (protocol.utc(val_range[0]), protocol.utc(val_range[1])) if val_range else (None, None)
    for p in params:
        h = config_hash(p)
        try:
            trades = trades_fn(p)
        except (JobCancelled, KeyboardInterrupt, protocol.SealedAccessError):
            raise
        except Exception as exc:                    # technical: this configuration cannot be backtested
            out["excluded"].append({"hash": h, "params": p, "reason": f"TECHNICAL_ERROR: {type(exc).__name__}: {exc}"})
            continue
        if not trades:
            out["excluded"].append({"hash": h, "params": p, "reason": "ZERO_TRADES_VALIDATION"})
            continue
        if lo is not None:
            ts = pd.to_datetime([t["entry_time"] for t in trades], utc=True)
            out_of_range += int(((ts < lo) | (ts >= hi + pd.Timedelta(days=1))).sum())     # end day inclusive
        tm = validation.trade_metrics(trades, capital)
        out["configs"].append({"hash": h, "params": p, "n_trades": tm["n_trades"], "net_profit": tm["net_profit"],
                               "profit_factor": tm["profit_factor"], "sharpe_daily": tm["sharpe"]})
        cols.append(validation.daily_returns_from_trades(trades, capital).rename(h))
    out["n_eligible"] = len(cols)
    if val_range is None:
        mark("time_index", "NOTE", "validatieperiode niet opgegeven: gelijke tijdas niet controleerbaar")
    elif out_of_range:
        mark("time_index", "FAIL", f"{out_of_range} trade(s) buiten de validatieperiode {val_range[0]} .. {val_range[1]}")
    else:
        mark("time_index", "PASS", "alle trades vallen in de validatieperiode; series op de unie van handelsdagen, "
                                   "dagen zonder trade = 0")
    if cost_fp is not None and chosen_cost_fp is not None:
        ok = cost_fp == chosen_cost_fp
        mark("cost_model", "PASS" if ok else "FAIL", "zelfde kostenmodel, kapitaal en spreadbron als de gekozen configuratie"
             if ok else "kostenmodel/kapitaal wijkt af van de gekozen configuratie")
    else:
        mark("cost_model", "NOTE", "kostenvingerafdruk niet opgegeven: gelijke kosten niet controleerbaar")
    if out["excluded"] and len(cols) * 2 < len(params):
        mark("exclusion_policy", "FAIL", f"{len(out['excluded'])} van {len(params)} configuraties technisch uitgesloten "
                                         "(> 50%): de set vertegenwoordigt de zoektocht niet meer")
    else:
        mark("exclusion_policy", "PASS", f"{len(out['excluded'])} technisch uitgesloten (fout of geen trade), "
                                         "geen op prestatie")
    failed = [k for k, v in pre.items() if v == "FAIL"]
    if failed:
        out["precondition_failed"] = True
        return out
    if len(cols) < 2:
        out["applicable"] = False
        mark("enough_configs", "NOTE", f"{len(cols)} bruikbare configuratie(s): PBO niet van toepassing")
        return out
    mat = pd.concat(cols, axis=1).fillna(0.0)
    if len(mat) < 8:
        out["applicable"] = False
        mark("enough_configs", "NOTE", f"{len(mat)} handelsdagen (< 8): PBO niet van toepassing")
        return out
    mark("enough_configs", "PASS", f"{len(cols)} configuraties x {len(mat)} dagen")
    out.update(validation.pbo_cscv(mat.to_numpy(), n_splits=8))
    out.pop("logits", None)
    return out


def register_pbo_configs(env, rid, cid, pbo: dict) -> int:
    """Store every PBO configuration (params, canonical hash, validation metrics) as an auditable L5_PBO record that
    references the configuration's existing L4 ledger row. These are the L4 configurations again: the stage is excluded
    from every trial count and no ledger row is added (N of the DSR does not grow)."""
    l4_ledger: dict = {}
    for t in env.store.trials(rid, cid):             # creation order: the first L4 record of a configuration wins
        if t["stage"] == "L4":
            l4_ledger.setdefault(config_hash(t["params"]), t.get("ledger_id"))
    n = 0
    for c in pbo.get("configs") or []:
        key = hashlib.sha256(protocol.canon([rid, cid, PBO_TRIAL_STAGE, c["hash"]]).encode()).hexdigest()
        n += bool(env.store.put_trial(key, rid, cid, PBO_TRIAL_STAGE, "pbo_validation", c["params"],
                                      {**{k: c[k] for k in ("n_trades", "net_profit", "profit_factor", "sharpe_daily")},
                                       "config_hash": c["hash"]}, l4_ledger.get(c["hash"])))   # points at the L4 ledger row
    return n


def pbo_check(pbo: dict, max_pbo: float) -> dict:
    """PBO is a review trigger, not a rejection: PBO > max_pbo is REVIEW_REQUIRED. A failed CSCV precondition is
    INSUFFICIENT_EVIDENCE (no number); a search with too few configurations for CSCV is not applicable (PASS, note)."""
    v = pbo.get("pbo")
    if pbo.get("precondition_failed"):
        bad = [f"{k}: {t}" for k, t in (pbo.get("precondition_notes") or {}).items()
               if (pbo.get("preconditions") or {}).get(k) == "FAIL"]
        return _chk("pbo", "INSUFFICIENT_EVIDENCE", None, max_pbo,
                    "PBO niet te bepalen, CSCV-voorwaarde geschonden: " + "; ".join(bad) + ".")
    if v is None:
        return _chk("pbo", "PASS", None, max_pbo, "PBO niet van toepassing (te weinig configuraties of dagen voor CSCV).")
    if v > max_pbo:
        return _chk("pbo", "REVIEW_REQUIRED", v, max_pbo,
                    f"PBO {v:.2f} > {max_pbo}: handmatige beoordeling nodig voordat de kandidaat verder mag.")
    return _chk("pbo", "PASS", v, max_pbo, f"PBO {v:.2f} (maximaal {max_pbo}).")


def quality_checks(om, q, *, dist=None, cost=None, stability=None, pbo=None,roll=None, expd=None, boot=None,
                   oos_start=None, oos_end=None, pf_ci=None, rf_ci=None) -> list[dict]:
    """Quality-profile checks on the walk-forward OOS evidence, with uncertainty: too few observations give
    INSUFFICIENT_EVIDENCE (never a FAIL), an edge whose bootstrap CI includes zero is not counted as a pass, and a
    PF / RF below the threshold is a FAIL only when the evidence says so (see :func:`_threshold_status`)."""
    out = []
    n = om["trades"]
    enough = n >= q["min_oos_trades"]
    losing = om["net_profit"] <= 0
    if not enough:
        out.append(_chk("oos_trades", "INSUFFICIENT_EVIDENCE", n, q["min_oos_trades"],
                        f"{n} OOS-trades; minimaal {q['min_oos_trades']:.0f} nodig voor een oordeel."))
    else:
        out.append(_chk("oos_trades", "PASS", n, q["min_oos_trades"], f"{n} OOS-trades."))
    tpw = om["trades_per_week"]
    out.append(_chk("frequency", "PASS" if tpw >= q["min_trades_per_week"] else "FAIL", tpw, q["min_trades_per_week"],
                    f"{tpw:.2f} trades per handelsweek (minimaal {q['min_trades_per_week']})."))
    pf = om["profit_factor"]
    st = _threshold_status(pf, q["min_pf_oos"], pf_ci, enough, losing, n)
    out.append(_chk("profit_factor_oos", st, pf, q["min_pf_oos"],
                    f"OOS profit factor {pf:.2f} (minimaal {q['min_pf_oos']})" +
                    (f"; 95%-CI [{pf_ci['ci_low']:.2f}, {pf_ci['ci_high']:.2f}]" if pf_ci and pf_ci.get("status") == "OK"
                     else "") + ("; tekort niet van ruis te onderscheiden." if st == "INSUFFICIENT_EVIDENCE" else ".")))
    rf = om["recovery_factor"]
    st = _threshold_status(rf, q["min_rf_oos"], rf_ci, enough, losing, n)
    out.append(_chk("recovery_factor_oos", st, rf, q["min_rf_oos"],
                    f"OOS recovery factor {rf:.2f} (minimaal {q['min_rf_oos']})" +
                    ("; tekort niet van ruis te onderscheiden." if st == "INSUFFICIENT_EVIDENCE" else ".")))
    dd = om["max_drawdown_pct"]
    out.append(_chk("max_dd_oos", "PASS" if dd <= q["max_dd_pct"] else "FAIL", dd, q["max_dd_pct"],
                    f"Maximale OOS-drawdown {dd:.1f}% (maximaal {q['max_dd_pct']}%)."))
    exp = om["expectancy"]
    if boot and boot.get("status") == "OK":
        lo = boot["ci_low"]
        st = ("PASS" if exp > 0 and lo > 0 else
              "FAIL" if exp <= 0 and n >= MIN_TRADES_TO_REJECT else "INSUFFICIENT_EVIDENCE")
        out.append(_chk("expectancy", st, exp, 0.0, f"Gemiddelde trade {exp:.2f}; 95%-block-bootstrap-CI "
                                                    f"[{lo:.2f}, {boot['ci_high']:.2f}]" +
                        ("" if st == "PASS" else " - de edge is niet van nul te onderscheiden" if exp > 0 else "")))
    else:
        out.append(_chk("expectancy", "FAIL" if exp <= 0 and n >= MIN_TRADES_TO_REJECT else "INSUFFICIENT_EVIDENCE",
                        exp, 0.0,
                        "Te weinig trades voor een betrouwbaarheidsinterval van de gemiddelde trade."))
    if dist is not None and dist.get("n_trades"):
        share = dist.get("profit_share_best_3_days")
        bad = "PROFIT_CONCENTRATION" in dist.get("flags", []) or (share is not None and share > q["max_top3_day_share"])
        out.append(_chk("concentration", "FAIL" if bad else "PASS", share,
                        q["max_top3_day_share"], " ".join(dist.get("warnings_nl") or []) or
                        "Winst niet geconcentreerd in een paar dagen."))
        inactive = dist.get("longest_inactive_days")
        out.append(_chk("inactivity", "PASS" if inactive is not None and inactive <= q["max_inactive_days"] else "FAIL",
                        inactive, q["max_inactive_days"], f"Langste periode zonder trade: "
                                                          f"{(inactive or 0):.0f} dagen (maximaal {q['max_inactive_days']:.0f})."))
    if cost is not None:
        s50 = (cost.get("scenarios", {}).get("spread_+50%") or {}).get("net_profit")
        out.append(_chk("spread_stress", "PASS" if s50 is not None and s50 > 0 else "FAIL", s50, 0.0,
                        "Blijft winstgevend bij +50% spread." if s50 and s50 > 0 else "Verliesgevend bij +50% spread."))
    if stability is not None:
        fp = stability.get("frac_positive")
        if fp is None:
            out.append(_chk("param_stability", "INSUFFICIENT_EVIDENCE", None, q["min_param_stability"],
                            "Parameterstabiliteit niet te bepalen."))
        else:
            out.append(_chk("param_stability", "PASS" if fp >= q["min_param_stability"] else "FAIL", fp,
                            q["min_param_stability"], f"{fp:.0%} van de buurparameters winstgevend."))
    flags = []
    if pbo is not None:
        out.append(pbo_check(pbo, q["max_pbo"]))
    if roll is not None:
        if roll.get("wfe") is None:
            flags.append("WFE niet berekenbaar")
        elif roll["wfe"] < q["min_wfe"]:
            flags.append(f"WFE {roll['wfe']:.2f} < {q['min_wfe']}")
    if expd is not None and expd.get("net_profit", 0) <= 0:
        flags.append("expanding walk-forward OOS niet winstgevend")
    out.append(_chk("overfit_flags", "FAIL" if flags else "PASS", len(flags), 0,
                    "Overfit-signalen: " + "; ".join(flags) if flags else "Geen overfit-signalen (WFE, expanding)."))
    return out


# ================================================================================================ L6 / L7 / L8
def dual_from_stages(l4: dict | None, l5: dict | None) -> dict:
    """Dual-positive gate for a stored candidate. V10.1 L5 records carry it; older records are evaluated from the
    L4 (development, optimised parameters) and L5 (walk-forward OOS) metrics. When an older record lacks those
    metrics the stage gates that it passed already imply it (L4: dev net > 0 and PF >= 1.05; L5: OOS PF >= min)."""
    p5 = (l5 or {}).get("payload") or {}
    if p5.get("dual_positive"):
        return p5["dual_positive"]
    m5 = (l5 or {}).get("metrics") or {}
    dev = (l4 or {}).get("metrics")
    if dev is None or m5.get("net_profit") is None:
        return {"status": "PASS", "implied_by_stage_gates": True, "dev_positive": None, "oos_positive": None,
                "reasons_nl": [], "note_nl": "ouder record zonder metrics: afgeleid uit de doorstane L4/L5-poorten"}
    return dual_positive_gate(dev, m5)


# ------------------------------------------------------------------------------------------------ PBO review
PBO_REVIEW_DECISIONS = ("accept", "reject")


class ReviewStateError(Exception):
    """The candidate is not (or no longer) REVIEW_REQUIRED."""


def pbo_review_record(status: str | None, decision: str, reviewer: str, note: str, *, pbo=None, max_pbo=None,
                      l5_sha256=None, at: str | None = None) -> dict:
    """Pure: validate a PBO review and derive the record, the new candidate status and its reasons. Accept moves the
    candidate to WF_PASSED, i.e. back into the normal flow: L6 (selection with the mandatory DSR gate), the sealed test
    and the forward gates all still apply. Reject is a final REJECTED."""
    if status != "REVIEW_REQUIRED":
        raise ReviewStateError(f"kandidaat is geen REVIEW_REQUIRED (status {status})")
    decision = str(decision or "").strip().lower()
    if decision not in PBO_REVIEW_DECISIONS:
        raise ValueError(f"beslissing moet {' of '.join(PBO_REVIEW_DECISIONS)} zijn")
    reviewer, note = str(reviewer or "").strip(), str(note or "").strip()
    if not reviewer or not note:
        raise ValueError("beoordelaar en toelichting zijn verplicht")
    at = at or _now()
    rec = {"at": at, "decision": decision, "reviewer": reviewer, "note": note, "pbo": pbo, "max_pbo": max_pbo,
           "l5_sha256": l5_sha256}
    if decision == "accept":
        new_status = "WF_PASSED"
        reasons = [f"PBO-waarschuwing ({pbo if pbo is None else round(pbo, 3)} > {max_pbo}) handmatig geaccepteerd door "
                   f"{reviewer} op {at}: {note}. De overige poorten (L6 DSR e.d.) gelden onverkort."]
    else:
        new_status = "REJECTED"
        reasons = [f"PBO-waarschuwing ({pbo if pbo is None else round(pbo, 3)} > {max_pbo}) handmatig afgewezen door "
                   f"{reviewer} op {at}: {note}"]
    return {"record": rec, "new_status": new_status, "reasons": reasons}


def l5_cleared(store: FactoryStore, rid: str, cand_id: str, l5: dict | None = None) -> bool:
    """L5 passed, or L5 required a PBO review and the latest recorded review accepted."""
    l5 = l5 or store.stage_result(rid, cand_id, "L5")
    if l5 is None:
        return False
    if l5["status"] == "PASS":
        return True
    if l5["status"] == "REVIEW_REQUIRED":
        rv = store.pbo_reviews(rid, cand_id)
        return bool(rv) and rv[-1]["decision"] == "accept"
    return False


def review_pbo(store: FactoryStore, cand_id: str, decision: str, reviewer: str, note: str, *, run_id: str | None = None,
               ledger=None, at: str | None = None) -> dict:
    """Record a human PBO review (append-only table + decision log) and move the candidate on. Raises LookupError
    (unknown candidate), ReviewStateError (not REVIEW_REQUIRED, or reviewed concurrently) or ValueError (bad input)."""
    c = store.candidate(run_id, cand_id) if run_id else store.find_candidate(cand_id)
    if c is None:
        raise LookupError(cand_id)
    rid = c["run_id"]
    l5 = store.stage_result(rid, cand_id, "L5") if c["status"] == "REVIEW_REQUIRED" else None
    info = ((l5 or {}).get("payload") or {}).get("pbo_review") or {}
    digest = hashlib.sha256(protocol.canon({"metrics": (l5 or {}).get("metrics"), "pbo_review": info}).encode()).hexdigest()
    res = pbo_review_record(c["status"], decision, reviewer, note, pbo=info.get("pbo"), max_pbo=info.get("max_pbo"),
                            l5_sha256=digest, at=at)
    if not store.add_pbo_review(rid, cand_id, res["record"], res["new_status"], res["reasons"]):
        raise ReviewStateError("kandidaat is intussen al beoordeeld")
    if ledger is not None and hasattr(ledger, "record_decision"):
        ledger.record_decision(run_id=rid, candidate_id=cand_id, stage="L5", decision=f"PBO-beoordeling: {res['record']['decision']}",
                               detail=_clean(res["record"]))
    store.log(rid, "info", f"{cand_id}: PBO-beoordeling {res['record']['decision']} door {res['record']['reviewer']}")
    return {"cand_id": cand_id, "run_id": rid, "status": res["new_status"], "review": res["record"]}


def select_and_freeze(store: FactoryStore, run: dict, vault: protocol.SealedVault, ledger=None) -> list[dict]:
    """L6: choose on dev information only (walk-forward OOS 2018-2023 is part of dev), with diversity across
    families and markets, a correlation limit and the baseline requirement; freeze the chosen ones."""
    cfg, prof = run["config"], run["config"]["profile_settings"]
    q = cfg["quality_thresholds"]
    rid = run["run_id"]
    passed = store.candidates(rid, status="WF_PASSED")
    # final multiplicity gate over the COMPLETE search (all rounds, this run finished): global DSR
    mt_rows = effective_trials.ledger_trials(ledger) if ledger is not None else _store_rows_for(store, rid)
    rows = []
    for c in passed:
        l5 = store.stage_result(rid, c["cand_id"], "L5")
        if l5 is None or not l5_cleared(store, rid, c["cand_id"], l5):
            continue
        m = l5["metrics"]
        daily = pd.Series(l5["payload"].get("daily_returns") or {}, dtype=float)
        if len(daily):
            daily.index = pd.to_datetime(daily.index)
            mt = effective_trials.multiple_testing_records(daily.to_numpy(), None, family=f"factory:{c['family']}",
                                                           rows=mt_rows, seed=int(cfg["seed"]))
            gate = effective_trials.gate(mt, q["min_dsr"])
        else:  # runs/fixtures without stored daily returns: the L5 value is the only record
            mt, gate = None, {"status": "PASS" if (m.get("dsr") or 0) >= q["min_dsr"] else "FAIL", "scope": "legacy_L5",
                              "value": m.get("dsr"), "threshold": q["min_dsr"]}
        rows.append((c, l5, (gate.get("value") or 0.0, m.get("profit_factor") or 0.0, m.get("recovery_factor") or 0.0),
                     mt, gate, daily))
    rows.sort(key=lambda x: x[2], reverse=True)
    chosen, per_family, series, sim_info = [], {}, {}, {}
    out = []
    for c, l5, key, mt, gate, daily in rows:
        cand = c["candidate"]
        reasons = []
        dual = dual_from_stages(store.stage_result(rid, c["cand_id"], "L4"), l5)
        if dual["status"] != "PASS":
            reasons = ["ontwikkeling en walk-forward OOS niet beide positief: " + "; ".join(dual["reasons_nl"])]
            st_ = "REJECTED" if dual["status"] == "FAIL" else "INSUFFICIENT_EVIDENCE"
            store.put_stage(rid, c["cand_id"], "L6", st_, 0.0, reasons, {"rank_key": key},
                            {"dual_positive": dual, "multiple_testing": mt, "gate": gate}, [])
            store.set_candidate(rid, c["cand_id"], stage="L6", status=st_, reasons=reasons)
            out.append({"cand_id": c["cand_id"], "selected": False, "reasons": reasons, "dual_positive": dual})
            continue
        if gate["status"] != "PASS":
            obs = ((mt or {}).get("global") or {}).get("observed_sr")
            if obs is None and len(daily) > 1 and daily.std() > 0:
                obs = float(daily.mean() / daily.std())
            # DSR below the threshold with a positive Sharpe: performance not distinguishable from the best of N
            # skill-less trials. That is missing evidence, not evidence of failure (see DSR_FINAL_METHODOLOGY.md).
            st_ = "INSUFFICIENT_EVIDENCE" if (obs is not None and obs > 0) else "REJECTED"
            reasons.append(f"meervoudig testen: globale DSR {gate.get('value') if gate.get('value') is None else round(gate['value'], 3)}"
                           f" < {q['min_dsr']} over de volledige zoektocht (alle rondes)" +
                           (": niet te onderscheiden van het beste van N toevalstrials" if st_ != "REJECTED" else
                            ": Sharpe niet positief"))
            store.put_stage(rid, c["cand_id"], "L6", st_, 0.0, reasons, {"rank_key": key},
                            {"multiple_testing": mt, "gate": gate}, [])
            store.set_candidate(rid, c["cand_id"], stage="L6", status=st_, reasons=reasons)
            out.append({"cand_id": c["cand_id"], "selected": False, "reasons": reasons, "gate": gate})
            continue
        if not prof["final_test"]:
            reasons.append("profiel 'snel': geen verzegelde eindtest voor onvoldoende onderzochte kandidaten")
        if len(chosen) >= int(prof["max_frozen"]):
            reasons.append(f"maximaal {prof['max_frozen']} kandidaten per run naar de eindtest")
        fam_key = cand["family"]
        if per_family.get(fam_key, 0) >= int(prof["max_per_family"]):
            reasons.append(f"diversiteit: al {per_family[fam_key]} kandidaat uit familie {fam_key}")
        me = {"daily": daily, "entry_days": l5["payload"].get("entry_days"), "blocks": cand.get("blocks"),
              "market": f"{cand['symbol']}:{cand['timeframe']}"}
        for other_id, other in series.items():
            sim = robustness.similarity(me, other)
            if sim["near_identical"]:
                reasons.append(f"bijna identiek aan gekozen kandidaat {other_id} (rendement-corr "
                               f"{sim.get('returns_corr')}, instap-overlap {sim.get('entries_jaccard')})")
                sim_info[c["cand_id"]] = {"other": other_id, **sim}
                break
        mk = run["protocol"]["markets"][f"{cand['symbol']}:{cand['timeframe']}"]
        if mk["ranges"].get("sealed_status") != "OK":
            reasons.append("verzegelde periode te kort voor deze markt")
        if reasons:
            store.put_stage(rid, c["cand_id"], "L6", "NOT_SELECTED", 0.0, reasons, None,
                            {"rank_key": key, "multiple_testing": mt, "gate": gate,
                             "similarity": sim_info.get(c["cand_id"])}, [])
            store.set_candidate(rid, c["cand_id"], stage="L6", status="NOT_SELECTED", reasons=reasons)
            out.append({"cand_id": c["cand_id"], "selected": False, "reasons": reasons})
            continue
        l4 = store.stage_result(rid, c["cand_id"], "L4")
        record = {"run_id": rid, "candidate_id": c["cand_id"], "spec": l5["payload"].get("spec") or cand["spec"],
                  "variant": l5["payload"].get("variant") or "basis",
                  "params": l5["payload"]["params"], "cost_model": mk["cost_model"],
                  "risk_model": {"risk_pct_per_trade": cand["spec"].get("risk", {}).get("percent"),
                                 "position_sizing": "risk % van gerealiseerd kapitaal / stopafstand"},
                  "selection_criteria": {"quality": cfg["quality"], "thresholds": cfg["quality_thresholds"],
                                         "rank_key": ["dsr_global", "profit_factor_oos", "recovery_factor_oos"],
                                         "multiple_testing_gate": gate,
                                         "preregistration_sha256": (cfg.get("preregistration") or {}).get("sha256"),
                                         "based_on": "alleen ontwikkel- en validatie-informatie (2004-2023)"},
                  "code_sha256": cfg.get("code_sha256") or code_sha256(), "dataset_sha256": mk["dataset_sha256"],
                  "research_version": RESEARCH_VERSION, "seed": cfg["seed"], "symbol": cand["symbol"],
                  "timeframe": cand["timeframe"], "frames_path": mk["frames_path"],
                  "sealed_start": mk["ranges"]["sealed"]["start"], "sealed_end": mk["ranges"]["sealed"]["end"],
                  "signature": cand["signature"], "family": cand["family"],
                  "lineage": [cand["parent"]] if cand.get("parent") else [],
                  "n_trials_candidate": (l4 or {}).get("payload", {}).get("n_trials")}
        frozen_id = vault.freeze(record)
        chosen.append(c["cand_id"])
        per_family[fam_key] = per_family.get(fam_key, 0) + 1
        series[c["cand_id"]] = me
        store.put_stage(rid, c["cand_id"], "L6", "PASS", 0.0, [], {"rank_key": key, "dsr_global": gate.get("value")},
                        {"frozen_id": frozen_id, "record": record, "multiple_testing": mt, "gate": gate}, [])
        store.set_candidate(rid, c["cand_id"], stage="L6", status="FROZEN", frozen_id=frozen_id)
        out.append({"cand_id": c["cand_id"], "selected": True, "frozen_id": frozen_id})
    return out


def final_test_candidate(store: FactoryStore, run: dict, cand_id: str, vault: protocol.SealedVault, ledger=None) -> dict:
    """L7 for one frozen candidate (idempotent: an existing L7 result is returned)."""
    rid = run["run_id"]
    prev = store.stage_result(rid, cand_id, "L7")
    if prev is not None:
        return prev
    c = store.candidate(rid, cand_id)
    t0 = time.monotonic()
    try:
        res = vault.final_test(c["frozen_id"], who=f"factory:{rid}",
                               search_trials=ledger.n_trials() if ledger is not None else store.count_trials())
        status = "PASS"
        reasons = []
    except protocol.SealedReuseError as exc:
        store.put_stage(rid, cand_id, "L7", "REUSED_HOLDOUT", time.monotonic() - t0, [str(exc)], None, None, [])
        store.set_candidate(rid, cand_id, stage="L7", status="REUSED_HOLDOUT", evidence_class="REUSED_HOLDOUT",
                            reasons=[str(exc)])
        return store.stage_result(rid, cand_id, "L7")
    trades = res.pop("trades", [])
    res["trades_sample"] = _trade_sample(trades)
    cand = c["candidate"]
    lid = None
    if ledger is not None:
        env = Env(store, run, None, ledger=ledger)
        lid = ledger.record(**ledger_row(env, cand, "L7", "PASS", params=store.stage_result(rid, cand_id, "L5")
                                         ["payload"]["params"], metrics=res["metrics"], start=res["sealed"][0],
                                         end=res["sealed"][1], oos_access=True,
                                         extra={"frozen_id": c["frozen_id"], "evidence_class": res["evidence_class"],
                                                "audit_id": res.get("audit_id")}))
    if ledger is not None and hasattr(ledger, "record_data_usage"):
        ledger.record_data_usage(run_id=rid, symbol=cand["symbol"], timeframe=cand["timeframe"], start=res["sealed"][0],
                                 end=res["sealed"][1], purpose="SEALED_FINAL",
                                 detail={"frozen_id": c["frozen_id"], "evidence_class": res["evidence_class"]})
    store.put_stage(rid, cand_id, "L7", status, time.monotonic() - t0, reasons, res["metrics"], res,
                    [lid] if lid else [])
    store.set_candidate(rid, cand_id, stage="L7", status="FINAL_TESTED", evidence_class=res["evidence_class"],
                        summary=_summary_from(res["metrics"]))
    return store.stage_result(rid, cand_id, "L7")


def sealed_checks(m: dict, q: dict, mt: dict | None, sealed_days: int) -> list[dict]:
    weeks = max(sealed_days / 7.0 * 50 / 52, 1e-9)
    need = q["min_trades_per_week"] * weeks * 0.75
    out = []
    n = m.get("trades", 0)
    out.append(_chk("sealed_trades", "PASS" if n >= need and n >= 30 else ("INSUFFICIENT_EVIDENCE" if n < 30 else "FAIL"),
                    n, round(need, 1), f"{n} trades in de verzegelde periode (minimaal {max(need, 30):.0f})."))
    out.append(_chk("sealed_net", "PASS" if m.get("net_profit", 0) > 0 else "FAIL", m.get("net_profit"), 0.0,
                    "Netto positief na kosten in de verzegelde periode." if m.get("net_profit", 0) > 0 else
                    "Niet winstgevend in de verzegelde periode."))
    pf = m.get("profit_factor") or 0.0
    out.append(_chk("sealed_pf", "PASS" if pf >= q["min_pf_oos"] else "FAIL", pf, q["min_pf_oos"],
                    f"Profit factor {pf:.2f} (minimaal {q['min_pf_oos']})."))
    dd = m.get("max_drawdown_pct") or 0.0
    out.append(_chk("sealed_dd", "PASS" if dd <= q["max_dd_pct"] else "FAIL", dd, q["max_dd_pct"],
                    f"Drawdown {dd:.1f}% (maximaal {q['max_dd_pct']}%)."))
    if mt is None or mt.get("p_holm") is None:
        out.append(_chk("multiple_testing", "INSUFFICIENT_EVIDENCE", None, q["alpha"],
                        "Geen p-waarde (te weinig dagen met resultaat)."))
    else:
        ok = mt["p_holm"] <= q["alpha"]
        out.append(_chk("multiple_testing", "PASS" if ok else "FAIL", mt["p_holm"], q["alpha"],
                        f"Holm-gecorrigeerde p = {mt['p_holm']:.3f} over alle verzegelde tests (ruw {mt['p_raw']:.3f}, "
                        f"BH {mt['p_bh']:.3f})."))
    return out


def finalize_run(store: FactoryStore, run: dict, vault: protocol.SealedVault, ledger=None) -> dict:
    """Multiple-testing correction over every sealed test, OOS approval (L7 verdict) and L8 forward eligibility."""
    rid = run["run_id"]
    q = run["config"]["quality_thresholds"]
    search_trials = ledger.n_trials() if ledger is not None else store.count_trials()
    led_var = ledger.sharpe_variance() if ledger is not None else None
    mt = vault.multiple_testing(search_trials=search_trials, sr_variance=(led_var / 252.0) if led_var else None,
                                alpha=q["alpha"])
    for c in store.candidates(rid, status="FINAL_TESTED"):
        l7 = store.stage_result(rid, c["cand_id"], "L7")
        if l7 is None or l7["status"] != "PASS":
            continue
        per = mt["per_candidate"].get(c["frozen_id"])
        mk = run["protocol"]["markets"][f"{c['symbol']}:{c['timeframe']}"]
        checks = sealed_checks(l7["metrics"], q, per, mk["ranges"]["sealed"]["days"])
        ev = l7["payload"].get("evidence_class")
        approved = all(x["status"] == "PASS" for x in checks) and ev == "HISTORICAL_OOS"
        reasons = [x["explanation_nl"] for x in checks if x["status"] != "PASS"]
        if ev != "HISTORICAL_OOS":
            reasons.append(f"bewijsklasse {ev}: geen schone verzegelde test")
        if not store.stage_result(rid, c["cand_id"], "L8"):
            l5 = store.stage_result(rid, c["cand_id"], "L5")
            fwd_ok, fwd_reason = forward_supported(c["candidate"]["spec"])
            verdict = (l5 or {}).get("metrics", {}).get("verdict")
            eligible = approved and fwd_ok and verdict == "FORWARD_ELIGIBLE"
            if approved and not fwd_ok:
                reasons.append(fwd_reason)
            if approved and verdict != "FORWARD_ELIGIBLE":
                reasons.append(f"scorecard in de validatieperiode is {verdict}, niet FORWARD_ELIGIBLE")
            store.put_stage(rid, c["cand_id"], "L8", "PASS" if eligible else ("OOS_APPROVED" if approved else "FAIL"),
                            0.0, reasons, {"sealed_checks": checks, "multiple_testing": per},
                            {"sealed_checks": checks, "multiple_testing": per, "forward_supported": fwd_ok}, [])
            status = "FORWARD_ELIGIBLE" if eligible else ("OOS_APPROVED" if approved else "FINAL_FAILED")
            store.set_candidate(rid, c["cand_id"], stage="L8", status=status, reasons=reasons)
    store.update_run(rid, multiple_testing=mt)
    return mt


def forward_supported(spec: dict) -> tuple[bool, str]:
    try:
        from .. import forward_rules
        forward_rules._reject_unsupported(spec)
        return True, ""
    except Exception as exc:
        return False, f"forwardmodel ondersteunt deze spec niet: {exc}"


# ================================================================================================ views (API)
COUNT_KEYS = ("generated", "technically_valid", "rejected", "quick_passed", "dev_validated", "wf_passed", "frozen",
              "final_tested", "oos_approved", "forward_ready")
_STAGE_ORDER = {s: i for i, s in enumerate((None, "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"))}


def counts(store: FactoryStore, rid: str) -> dict:
    cs = store.candidates(rid)
    out = {k: 0 for k in COUNT_KEYS}
    out["generated"] = len(cs)
    with closing(store.db()) as db:
        rows = db.execute("SELECT cand_id, stage, status FROM stage_results WHERE run_id=?", (rid,)).fetchall()
    passed = {}
    for r in rows:
        passed.setdefault(r["stage"], set())
        if r["status"] == "PASS" or (r["stage"] == "L8" and r["status"] == "OOS_APPROVED"):
            passed[r["stage"]].add(r["cand_id"])
    out["technically_valid"] = len(passed.get("L1", ()))
    out["quick_passed"] = len(passed.get("L2", ()))
    out["dev_validated"] = len(passed.get("L4", ()))
    out["wf_passed"] = len(passed.get("L5", ()))
    out["frozen"] = len(passed.get("L6", ()))
    out["final_tested"] = len(passed.get("L7", ()))
    out["oos_approved"] = len(passed.get("L8", ()))
    out["forward_ready"] = sum(1 for c in cs if c["status"] == "FORWARD_ELIGIBLE")
    out["rejected"] = sum(1 for c in cs if c["status"] in ("REJECTED", "REUSED_HOLDOUT", "INSUFFICIENT_EVIDENCE",
                                                            "NOT_SELECTED", "FINAL_FAILED", "TECHNICAL_BLOCKED"))
    out["errors"] = sum(1 for c in cs if c["status"] == "ERROR")
    out["outcomes"] = {k: 0 for k in OUTCOMES}
    for c in cs:
        out["outcomes"][classify_outcome(c, None)["outcome"]] += 1
    promo = {k: 0 for k in PROMOTION_STATUSES}
    highest = {k: 0 for k in PROMOTION_STATUSES[:8]}
    for c in cs:
        pr = promotion_status(c["status"], c["stage"])
        promo[pr["promotion_status"]] += 1
        if pr["highest_promotion"] in highest:
            highest[pr["highest_promotion"]] += 1
    out["promotion"] = promo
    out["highest_promotion"] = highest
    out["in_progress"] = sum(1 for c in cs if c["status"] in ("IN_PROGRESS", "GENERATED") or
                             c["status"] in STAGE_PASS_STATUS.values() and c["status"] != "WF_PASSED")
    return out


def families_view(store: FactoryStore, rid: str) -> list[dict]:
    out = {}
    for c in store.candidates(rid):
        row = out.setdefault(c["family"], {"family": c["family"], "group": c["grp"], "tested": 0, "survived": 0,
                                           "best_stage": None})
        row["tested"] += 1
        if c["status"] not in TERMINAL_STATUSES and c["status"] not in ("NOT_SELECTED", "FINAL_FAILED"):
            row["survived"] += 1
        reached = c["stage"]
        if c["status"] in TERMINAL_STATUSES and c["status"] != "REVIEW_REQUIRED":
            idx = _STAGE_ORDER.get(reached, 0) - 1
            reached = (None, "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8")[max(idx, 0)]
        if _STAGE_ORDER.get(reached, 0) > _STAGE_ORDER.get(row["best_stage"], 0):
            row["best_stage"] = reached
    return sorted(out.values(), key=lambda r: r["family"])


PROMOTION_STATUSES = ("EXPERIMENT", "DEVELOPMENT_PASS", "WALK_FORWARD_PASS", "ROBUSTNESS_PASS", "FROZEN_CANDIDATE",
                      "HISTORICAL_OOS_PASS", "FORWARD_ELIGIBLE", "PROSPECTIVE_FORWARD", "REJECTED",
                      "INSUFFICIENT_EVIDENCE", "TECHNICAL_BLOCKED")
_PROMO_RANK = {s: i for i, s in enumerate(PROMOTION_STATUSES[:8])}
# legacy candidate status -> (current promotion status, highest promotion reached)
_LEGACY_PROMOTION = {
    "GENERATED": ("EXPERIMENT", "EXPERIMENT"), "IN_PROGRESS": ("EXPERIMENT", "EXPERIMENT"),
    "TECHNICALLY_VALID": ("EXPERIMENT", "EXPERIMENT"), "QUICK_PASSED": ("EXPERIMENT", "EXPERIMENT"),
    "DEV_TESTED": ("EXPERIMENT", "EXPERIMENT"), "NOT_RUN": ("EXPERIMENT", "EXPERIMENT"),
    "STOPPED": ("EXPERIMENT", "EXPERIMENT"), "DEV_VALIDATED": ("DEVELOPMENT_PASS", "DEVELOPMENT_PASS"),
    "WF_PASSED": ("ROBUSTNESS_PASS", "ROBUSTNESS_PASS"), "NOT_SELECTED": ("ROBUSTNESS_PASS", "ROBUSTNESS_PASS"),
    "FROZEN": ("FROZEN_CANDIDATE", "FROZEN_CANDIDATE"), "FINAL_TESTED": ("FROZEN_CANDIDATE", "FROZEN_CANDIDATE"),
    "OOS_APPROVED": ("HISTORICAL_OOS_PASS", "HISTORICAL_OOS_PASS"),
    "FORWARD_ELIGIBLE": ("FORWARD_ELIGIBLE", "FORWARD_ELIGIBLE"),
    "PROSPECTIVE_FORWARD": ("PROSPECTIVE_FORWARD", "PROSPECTIVE_FORWARD"),
    "FINAL_FAILED": ("REJECTED", "FROZEN_CANDIDATE"), "REUSED_HOLDOUT": ("INSUFFICIENT_EVIDENCE", "FROZEN_CANDIDATE"),
    "INSUFFICIENT_EVIDENCE": ("INSUFFICIENT_EVIDENCE", None), "REJECTED": ("REJECTED", None),
    "REVIEW_REQUIRED": ("DEVELOPMENT_PASS", "DEVELOPMENT_PASS"),
    "ERROR": ("TECHNICAL_BLOCKED", None), "TECHNICAL_BLOCKED": ("TECHNICAL_BLOCKED", None)}
_STAGE_HIGHEST = {"L1": "EXPERIMENT", "L2": "EXPERIMENT", "L3": "EXPERIMENT", "L4": "EXPERIMENT",
                  "L5": "DEVELOPMENT_PASS", "L6": "ROBUSTNESS_PASS", "L7": "FROZEN_CANDIDATE",
                  "L8": "FROZEN_CANDIDATE"}


def promotion_status(status: str | None, stage: str | None, l5: dict | None = None) -> dict:
    """V10 promotion ladder for a candidate (works for runs made before V10: their statuses are mapped).
    ``highest`` is the last rung passed; for rejected candidates it says how far they got."""
    cur, hi = _LEGACY_PROMOTION.get(status or "GENERATED", ("EXPERIMENT", "EXPERIMENT"))
    if hi is None:  # terminal: highest = rung before the stage that rejected it
        hi = _STAGE_HIGHEST.get(stage or "L1", "EXPERIMENT")
        promo = ((l5 or {}).get("metrics") or {})
        if stage == "L5" and promo.get("walk_forward_pass"):
            hi = "WALK_FORWARD_PASS"
    if status == "NOT_SELECTED" and l5 is not None:
        m = l5.get("metrics") or {}
        if m.get("robustness_pass") is False:
            cur = hi = "WALK_FORWARD_PASS"
    return {"promotion_status": cur, "highest_promotion": hi}


# ------------------------------------------------------------------------------------------------ outcomes (V10.1)
OUTCOMES = ("PASSED", "REJECTED", "INSUFFICIENT_EVIDENCE", "TECHNICAL_BLOCKED", "REVIEW_REQUIRED", "IN_PROGRESS")
REJECTION_KIND = {"REJECTED": "statistisch", "INSUFFICIENT_EVIDENCE": "onvoldoende bewijs",
                  "TECHNICAL_BLOCKED": "technisch"}
_PASSED = {"WF_PASSED", "NOT_SELECTED", "FROZEN", "FINAL_TESTED", "OOS_APPROVED", "FORWARD_ELIGIBLE",
           "PROSPECTIVE_FORWARD"}
_DATA_TXT = ("Ask-dekking", "data_available", "te weinig bars", "level1_check fout", "INSUFFICIENT_SUPPORT", "ontbrekende data",
             "data ontbreekt", "instrument")
_DEFECT_TXT = ("ongeldige spec", "codegeneratie", "compile", "look-ahead", "toekomstige candles", "shift")
_SAMPLE_TXT = ("OOS-trades; minimaal", "vensters (minimaal", "niet van ruis te onderscheiden",
               "niet van nul te onderscheiden", "Te weinig trades voor een betrouwbaarheidsinterval",
               "NEEDS_MORE_DATA", "toevalstrials", "niet te beoordelen")


def _has(texts, needles) -> bool:
    return any(n.lower() in t.lower() for t in texts for n in needles)


def classify_outcome(c: dict, stages: dict | None = None) -> dict:
    """Final outcome of a candidate, derived at read time from the stored status, stage and reasons, so records of
    older runs are mapped without rewriting them (``stored_status`` keeps the original).

    PASSED                 all gates passed so far (WF_PASSED and later; NOT_SELECTED = passed, not chosen)
    REJECTED               evidence of failure: losing after costs, negative OOS on enough trades, overfit flags,
                           drawdown, pre-registered criterion failed with enough data, defective strategy (look-ahead,
                           invalid spec: rejection_kind 'technisch')
    INSUFFICIENT_EVIDENCE  too few trades/windows, short history, DSR not distinguishable from selection luck,
                           holdout already used, test not computable
    TECHNICAL_BLOCKED      missing/invalid data or instrument information, pipeline error"""
    status, stage = c.get("status") or "GENERATED", c.get("stage")
    reasons = list(c.get("reasons") or [])
    kind_override = None
    if status in _PASSED:
        out = "PASSED"
        if status == "NOT_SELECTED" and _has(reasons, ("verzegelde periode te kort",)):
            out = "INSUFFICIENT_EVIDENCE"
    elif status == "REVIEW_REQUIRED":
        out = "REVIEW_REQUIRED"
    elif status in ("ERROR", "TECHNICAL_BLOCKED"):
        out = "TECHNICAL_BLOCKED"
    elif status in ("INSUFFICIENT_EVIDENCE", "REUSED_HOLDOUT"):
        out = "INSUFFICIENT_EVIDENCE"
    elif status in ("REJECTED", "FINAL_FAILED"):
        if _has(reasons, ("duplicaat",)):
            out, kind_override = "REJECTED", "technisch"     # not re-tested; the earlier test is the evidence
        elif stage == "L1" and _has(reasons, _DATA_TXT) and not _has(reasons, _DEFECT_TXT):
            out = "TECHNICAL_BLOCKED"
        elif stage == "L1":
            out, kind_override = "REJECTED", "technisch"
        elif stage == "L6" and _has(reasons, ("globale DSR",)) and not _has(reasons, ("Sharpe niet positief",)):
            out = "INSUFFICIENT_EVIDENCE"     # pre-V10.1 L6 rejections: positive OOS, DSR below the threshold
        elif reasons and all(_has([r], _SAMPLE_TXT) for r in reasons):
            out = "INSUFFICIENT_EVIDENCE"
        else:
            out = "REJECTED"
    else:
        out = "IN_PROGRESS"
    kind = kind_override or REJECTION_KIND.get(out)
    missing, nxt = _missing_and_next(out, status, stage, reasons, stages)
    return {"outcome": out, "rejection_kind": kind, "stored_status": status, "reasons_nl": reasons,
            "duplicate": _has(reasons, ("duplicaat",)),
            "missing_nl": missing, "next_test_nl": nxt}


def _missing_and_next(out, status, stage, reasons, stages) -> tuple[list, list]:
    missing, nxt = [], []
    if out == "TECHNICAL_BLOCKED":
        missing.append("bruikbare marktdata of instrumentgegevens (zie redenen)" if status != "ERROR"
                       else "een foutloze pijplijnrun (zie foutmelding)")
        nxt.append("data aanvullen of instrumentgegevens corrigeren, daarna de kandidaat opnieuw vanaf L1 testen")
    elif out == "INSUFFICIENT_EVIDENCE":
        if _has(reasons, ("OOS-trades", "trades <", "Te weinig trades")):
            missing.append("meer onafhankelijke out-of-sample trades")
            nxt.append("langere validatiehistorie of dezelfde regels (bevroren) op een extra markt/timeframe")
        if _has(reasons, ("vensters",)):
            missing.append("meer walk-forward-vensters (minimaal 4)")
            nxt.append("langere validatieperiode of kortere testvensters")
        if _has(reasons, ("DSR", "toevalstrials")):
            missing.append("bewijs dat de Sharpe boven het verwachte maximum van N toevalstrials ligt")
            nxt.append("prospectieve forward-/papertest van de bevroren regels (geen nieuwe trials toevoegen)")
        if _has(reasons, ("ruis",)):
            missing.append("nauwkeuriger schatting van profit/recovery factor (betrouwbaarheidsinterval te breed)")
            nxt.append("meer trades: langere periode of forward-test")
        if status == "REUSED_HOLDOUT":
            missing.append("schone out-of-sample data: de verzegelde periode is al gebruikt voor deze lijn")
            nxt.append("alleen nog prospectieve forward-data")
        if _has(reasons, ("INSUFFICIENT_SUPPORT", "verzegelde periode te kort")):
            missing.append("voldoende historie voor ontwikkel-, validatie- en verzegelde periode")
            nxt.append("wachten op meer data of een markt met langere historie")
        if not missing:
            missing.append("voldoende bewijs voor een oordeel (zie redenen)")
            nxt.append("meer data verzamelen (langere periode of forward-test) en opnieuw beoordelen")
    elif out == "REVIEW_REQUIRED":
        missing.append("een vastgelegde menselijke beoordeling van de PBO-waarschuwing")
        nxt.append("beoordeling vastleggen (accepteren of afwijzen); bij accepteren volgen de overige poorten, "
                   "waaronder de verplichte DSR-poort van L6")
    elif out == "REJECTED" and _has(reasons, ("duplicaat",)):
        nxt.append("geen: dezelfde signatuur is eerder getest; zie dat resultaat in het onderzoekslog")
    elif out == "REJECTED":
        nxt.append("geen vervolgtest van deze lijn; een nieuwe hypothese moet als nieuwe trial worden geteld")
    elif out == "PASSED":
        nxt.append({"WF_PASSED": "selectie en bevriezing (L6) met de globale meervoudig-testen-poort",
                    "NOT_SELECTED": "niet gekozen (capaciteit/diversiteit); kan in een volgende run bevroren worden",
                    "FROZEN": "verzegelde eindtest (L7)", "FINAL_TESTED": "beoordeling verzegelde eindtest (L8)",
                    "OOS_APPROVED": "forward-geschiktheid controleren",
                    "FORWARD_ELIGIBLE": "prospectieve forward-test (paper)",
                    "PROSPECTIVE_FORWARD": "forward-resultaten blijven volgen"}.get(status, "volgende fase"))
    else:
        nxt.append(f"onderzoek loopt ({stage or 'wachtrij'})")
    return missing, nxt


def candidate_rows(store: FactoryStore, rid: str, stage: str | None = None) -> list[dict]:
    rows = []
    for c in store.candidates(rid):
        if stage and c["stage"] != stage:
            continue
        s = c["summary"] or {}
        rows.append({"id": c["cand_id"], "name": c["name"], "symbol": c["symbol"], "timeframe": c["timeframe"],
                     "family": c["family"], "group": c["grp"], "stage": c["stage"], "status": c["status"],
                     "evidence_class": c["evidence_class"], "trades": s.get("trades"),
                     "net_return_pct": s.get("net_return_pct"), "max_dd_pct": s.get("max_dd_pct"),
                     "profit_factor": s.get("profit_factor"), "recovery_factor": s.get("recovery_factor"),
                     "reasons_nl": c["reasons"] or [], "run_id": rid, "frozen_id": c["frozen_id"],
                     **promotion_status(c["status"], c["stage"]), **_outcome_fields(c)})
    return rows


def _outcome_fields(c: dict, stages: dict | None = None) -> dict:
    o = classify_outcome(c, stages)
    return {"outcome": o["outcome"], "rejection_kind": o["rejection_kind"], "missing_nl": o["missing_nl"],
            "next_test_nl": o["next_test_nl"], "stored_status": o["stored_status"], "duplicate": o["duplicate"],
            "is_candidate": o["outcome"] == "PASSED"}


def candidate_detail(store: FactoryStore, cand_id: str, run_id: str | None = None, vault=None) -> dict | None:
    c = store.candidate(run_id, cand_id) if run_id else store.find_candidate(cand_id)
    if c is None:
        return None
    rid = c["run_id"]
    st = store.stage_results(rid, cand_id)
    cand = c["candidate"]
    l3, l4, l5, l7, l8 = (st.get(k) for k in ("L3", "L4", "L5", "L7", "L8"))
    p5 = (l5 or {}).get("payload") or {}
    params = p5.get("params") or ((l4 or {}).get("payload") or {}).get("best", {}).get("params") or \
        {p["name"]: p["default"] for p in cand.get("spec", {}).get("params") or []}
    dev = None
    if l3:
        p3 = l3["payload"] or {}
        dev = {"metrics": l3["metrics"], "equity": p3.get("equity"), "yearly": p3.get("yearly"),
               "monthly": p3.get("monthly"), "sessions": p3.get("sessions"), "regimes": p3.get("regimes"),
               "range": p3.get("range")}
    opt = None
    if l4 and l4.get("payload"):
        p4 = l4["payload"]
        opt = {"trials": [t for r in p4.get("rungs", []) for t in r["trials"]], "rungs": p4.get("rungs"),
               "best": p4.get("best"), "objective": p4.get("objective"), "n_trials": p4.get("n_trials"),
               "param_space": p4.get("param_space")}
    final = None
    if l7 and l7.get("payload"):
        p7 = l7["payload"]
        final = {"evidence_class": p7.get("evidence_class"), "metrics": p7.get("metrics"), "equity": p7.get("equity"),
                 "audit_id": p7.get("audit_id"), "bootstrap": p7.get("bootstrap"), "sealed": p7.get("sealed"),
                 "frozen_id": p7.get("frozen_id"), "trades_sample": p7.get("trades_sample"),
                 "sealed_checks": ((l8 or {}).get("payload") or {}).get("sealed_checks"),
                 "multiple_testing": ((l8 or {}).get("payload") or {}).get("multiple_testing")}
    elif l7:
        final = {"evidence_class": "REUSED_HOLDOUT", "metrics": None, "equity": [], "audit_id": None,
                 "reasons_nl": l7.get("reasons")}
    warnings = []
    dist = p5.get("distribution") or {}
    warnings.extend(dist.get("warnings_nl") or [])
    if cand.get("proxies_nl"):
        warnings.extend(cand["proxies_nl"])
    warnings.append("Contractspecificaties zijn aannames (niet door broker bevestigd); OHLC zonder tick-volgorde: "
                    "SL en TP in dezelfde bar = SL eerst.")
    ledger_ids = [lid for r in st.values() for lid in (r.get("ledger_ids") or []) if lid]
    return {"id": cand_id, "run_id": rid, "status": c["status"], "stage": c["stage"],
            **promotion_status(c["status"], c["stage"], l5),
            "evidence_class": c["evidence_class"], "frozen_id": c["frozen_id"],
            "evidence_verdict": p5.get("evidence_verdict"), "evidence_class_validation": p5.get("evidence_class_validation"),
            "multiple_testing_final": ((st.get("L6") or {}).get("payload") or {}).get("gate"),
            "candidate": {"spec": cand.get("spec"), "rules_nl": cand.get("rules_nl"), "params": params,
                          "passport": cand.get("passport"), "iq_sources": cand.get("iq_sources"),
                          "proxies_nl": cand.get("proxies_nl"), "hypothesis_nl": cand.get("hypothesis_nl"),
                          "name": cand.get("name"), "symbol": cand.get("symbol"), "timeframe": cand.get("timeframe"),
                          "family": cand.get("family"), "group": cand.get("group"), "blocks": cand.get("blocks"),
                          "signature": cand.get("signature"), "complexity": cand.get("complexity")},
            "stages": {k: {"status": v["status"], "reasons_nl": v["reasons"], "seconds": v["seconds"],
                           "metrics": v["metrics"] if k not in ("L5",) else _slim(v["metrics"] or {}) | {
                               x: (v["metrics"] or {}).get(x) for x in ("wfe", "dsr", "psr", "pbo", "verdict")}}
                       for k, v in st.items()},
            "quick": ((st.get("L2") or {}).get("payload") or {}).get("windows"),
            "technical": ((st.get("L1") or {}).get("payload") or {}).get("checks"),
            "dev": dev, "optimisation": opt,
            "walk_forward": p5.get("walk_forward"), "robustness": p5.get("robustness"),
            "statistics": p5.get("statistics"), "distribution": dist or None, "final_test": final,
            "scorecard": p5.get("scorecard"), "quality_checks": p5.get("quality_checks"),
            "warnings_nl": warnings, "reasons_nl": c["reasons"] or [],
            "baseline_comparison": p5.get("baseline_comparison"), "ledger_ids": ledger_ids,
            **_outcome_fields(c, st), "dual_positive": dual_from_stages(l4, l5) if l5 else None,
            "pbo_reviews": store.pbo_reviews(rid, cand_id), "pbo_review": p5.get("pbo_review"),
            "trades_sample": p5.get("trades_sample") or ((l3 or {}).get("payload") or {}).get("trades_sample") or []}
