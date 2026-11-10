"""Strategy Discovery Factory API (``/api/factory/*``). Research runs in a separate supervisor process."""
from __future__ import annotations

import dataclasses
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .research import effective_trials, factory, factory_worker, protocol

HERE = Path(__file__).resolve().parent
router = APIRouter()
DB_PATH = factory.DEFAULT_DB
_store: factory.FactoryStore | None = None
_vault: protocol.SealedVault | None = None


def store() -> factory.FactoryStore:
    global _store
    if _store is None or _store.path != Path(DB_PATH):
        _store = factory.FactoryStore(DB_PATH)
    return _store


def vault() -> protocol.SealedVault:
    global _vault
    if _vault is None or _vault.path != Path(DB_PATH):
        _vault = protocol.SealedVault(DB_PATH)
    return _vault


class FactoryBody(BaseModel):
    markets: list[str] | None = None
    timeframes: list[str] | None = None
    families: str | list[str] = "all"
    profile: str = "uitgebreid"
    quality: str = "regelmatig"
    min_trades_per_week: float | None = Field(None, gt=0, le=100)
    max_dd_pct: float | None = Field(None, gt=0, le=100)
    risk_pct: float | None = Field(None, gt=0, le=5)
    advanced: dict = Field(default_factory=dict)


def _data_manager():
    from . import research_api
    return research_api._data_manager


def _market_availability() -> dict:
    """Cheap per-symbol availability: measured values from the newest run protocol when present, else the M1
    manifest (first candle) and the daily partition store (last day)."""
    out = {}
    cache = HERE / "data_cache"
    dm = _data_manager()
    symbols = [s["symbol"] for s in dm.available_symbols()] if dm else list(factory.DEFAULT_MARKETS)
    daily_last = {}
    daily = cache / "daily_dukascopy.sqlite3"
    if daily.is_file():
        try:
            with closing(sqlite3.connect(daily, timeout=5)) as db:
                daily_last = {r[0]: r[1] for r in db.execute("SELECT symbol, MAX(day) FROM daily_partitions GROUP BY symbol")}
        except sqlite3.Error:
            daily_last = {}
    from .data_manager import SYMBOLS
    for sym in symbols:
        man = cache / f"{sym}_M1_bidask_manifest.json"
        first = last = None
        if man.is_file():
            try:
                m = json.loads(man.read_text(encoding="utf-8"))
                first, last = m.get("first_utc"), m.get("last_utc")
            except ValueError:
                pass
        d = daily_last.get(SYMBOLS.get(sym, sym))
        if d and (last is None or d > last[:10]):
            last = d
        out[sym] = {"first": first, "last": last, "measured": False}
    for run in store().runs(limit=10):
        for key, mk in ((run.get("protocol") or {}).get("markets") or {}).items():
            av = mk.get("availability") or {}
            sym = mk.get("symbol")
            if sym in out and av.get("first_reliable") and not out[sym].get("measured"):
                out[sym].update(first_reliable=av["first_reliable"], last_complete_day=av["last_complete_day"],
                                measured=True, measured_in_run=run["run_id"])
    return out


def _http(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/api/factory/catalog")
def catalog():
    avail = _market_availability()
    markets = []
    for sym, av in avail.items():
        first = (av.get("first_reliable") or av.get("first") or "")[:10] or None
        last = (av.get("last_complete_day") or av.get("last") or "")[:10] or None
        r = protocol.build_ranges({"first_reliable": first, "last_complete_day": last}) if first and last else None
        notes = list((r or {}).get("notes_nl") or [])
        if not av.get("measured"):
            notes.append("Bereik uit het databestand; exacte eerste betrouwbare en laatste complete dag worden bij de "
                         "start van een run gemeten.")
        markets.append({"symbol": sym, "first": first, "last": last,
                        "supported": bool(r and r["status"] != "INSUFFICIENT_SUPPORT"),
                        "notes": " ".join(notes), "ranges": r})
    return {"families": factory.families_catalog(),
            "profiles": {k: {"label_nl": v["label_nl"], "description_nl": v["description_nl"], "defaults": v["defaults"]}
                         for k, v in factory.PROFILES.items()},
            "quality_profiles": factory.QUALITY_PROFILES, "markets": markets,
            "timeframes": list(factory.SUPPORTED_TIMEFRAMES), "default_markets": list(factory.DEFAULT_MARKETS),
            "default_workers": factory.DEFAULT_WORKERS, "cpu_count": factory.CPU_COUNT,
            "protocol": {"dev_start": protocol.DEV_START, "development_end": protocol.DEVELOPMENT_END,
                         "sealed_start": protocol.SEALED_START, "version": protocol.PROTOCOL_VERSION},
            "generator": getattr(factory.get_generator(), "name", "generator")}


@router.post("/api/factory/plan")
def plan(body: FactoryBody):
    out = _http(factory.plan, body.model_dump(), _market_availability())
    out.pop("config", None)
    return out


@router.post("/api/factory/runs")
def create_run(body: FactoryBody):
    raw = body.model_dump()
    plan_out = _http(factory.plan, raw, _market_availability())
    cfg = plan_out.pop("config")
    cfg["body"] = raw
    run_id = factory_worker.new_run_id()
    gen = factory.get_generator()
    store().create_run(run_id, cfg, plan_out, getattr(gen, "name", "generator"))
    sup = factory_worker.ensure_supervisor(DB_PATH)
    return {"run_id": run_id, "supervisor": sup, "plan": plan_out}


def _run_view(run: dict, full: bool = True) -> dict:
    rid = run["run_id"]
    st = store()
    c = factory.counts(st, rid) if full else (run.get("counts") or {})
    view = {"run_id": rid, "status": run["status"], "phase": run["phase"],
            "phase_nl": factory.PHASE_NL.get(run["status"] if run["status"] in ("paused", "stopped", "error")
                                             else run["phase"], run["phase"]),
            "progress": run.get("progress") or 0.0, "counts": c, "created_at": run["created_at"],
            "started_at": run.get("started_at"), "finished_at": run.get("finished_at"),
            "resources": run.get("resources") or {"cpu_pct": None, "ram_mb": None, "workers": 0},
            "generator": run.get("generator"), "error": run.get("error"),
            "profile": (run.get("config") or {}).get("profile"), "quality": (run.get("config") or {}).get("quality"),
            "markets": (run.get("config") or {}).get("markets"), "timeframes": (run.get("config") or {}).get("timeframes")}
    if full:
        cfg = dict(run.get("config") or {})
        view.update(families=factory.families_view(st, rid), log_tail=st.log_tail(rid, 40), config=cfg,
                    plan=run.get("plan"), protocol=_protocol_view(run.get("protocol") or {}),
                    multiple_testing=run.get("multiple_testing") or vault().multiple_testing(),
                    supervisor=factory_worker.supervisor_alive(DB_PATH),
                    tasks={s: len(st.tasks(rid, state=s)) for s in ("queued", "running", "done", "error", "cancelled",
                                                                     "skipped")})
    return view


def _protocol_view(proto: dict) -> dict:
    out = {k: v for k, v in proto.items() if k != "markets"}
    out["markets"] = {}
    for key, mk in (proto.get("markets") or {}).items():
        av = mk.get("availability") or {}
        out["markets"][key] = {
            "symbol": mk.get("symbol"), "timeframe": mk.get("timeframe"), "ranges": mk.get("ranges"),
            "windows": mk.get("windows"), "dataset_sha256": mk.get("dataset_sha256"), "cost_model": mk.get("cost_model"),
            "availability": {k: av.get(k) for k in ("first_candle", "first_reliable", "last_candle", "last_complete_day",
                                                    "missing_ask_fraction", "spread", "notes_nl", "rows")} | {
                "gaps": {k: (av.get("gaps") or {}).get(k) for k in ("missing_weekdays", "weekdays",
                                                                     "missing_weekday_fraction", "multi_day_gaps")}},
            "error": mk.get("error")}
    return out


def _maybe_restart_supervisor():
    active = [r for r in store().runs(limit=20) if r["status"] in ("queued", "running", "paused")]
    if active and not factory_worker.supervisor_alive(DB_PATH)["alive"]:
        factory_worker.ensure_supervisor(DB_PATH)


@router.get("/api/factory/runs")
def list_runs(limit: int = Query(30, ge=1, le=200)):
    _maybe_restart_supervisor()
    return {"runs": [_run_view(r, full=False) for r in store().runs(limit)]}


def _get_run(run_id: str) -> dict:
    run = store().run(run_id)
    if run is None:
        raise HTTPException(404, "Run niet gevonden")
    return run


@router.get("/api/factory/runs/{run_id}")
def get_run(run_id: str):
    _maybe_restart_supervisor()
    return _run_view(_get_run(run_id))


def _control(run_id: str, value: str | None, allowed: tuple):
    run = _get_run(run_id)
    if run["status"] not in allowed:
        raise HTTPException(409, f"Run heeft status {run['status']}; actie niet mogelijk")
    store().update_run(run_id, control=value)
    store().log(run_id, "info", {"pause": "Pauze gevraagd", "stop": "Stop gevraagd", None: "Hervatten gevraagd"}[value])
    factory_worker.ensure_supervisor(DB_PATH)
    return _run_view(store().run(run_id), full=False)


@router.post("/api/factory/runs/{run_id}/pause")
def pause_run(run_id: str):
    return _control(run_id, "pause", ("queued", "running"))


@router.post("/api/factory/runs/{run_id}/resume")
def resume_run(run_id: str):
    return _control(run_id, None, ("queued", "running", "paused"))


@router.post("/api/factory/runs/{run_id}/stop")
def stop_run(run_id: str):
    return _control(run_id, "stop", ("queued", "running", "paused"))


@router.get("/api/factory/runs/{run_id}/candidates")
def run_candidates(run_id: str, stage: str | None = None):
    _get_run(run_id)
    return factory.candidate_rows(store(), run_id, stage)


@router.get("/api/factory/candidates/{cand_id}")
def candidate(cand_id: str, run_id: str | None = None):
    out = factory.candidate_detail(store(), cand_id, run_id, vault())
    if out is None:
        raise HTTPException(404, "Kandidaat niet gevonden")
    return out


class ForwardBody(BaseModel):
    run_id: str | None = None
    starting_capital: float = Field(10000, gt=0)
    portfolio_risk_limit: float = Field(0.05, gt=0, le=1)
    leverage: float = Field(1, ge=1, le=1000)


@router.post("/api/factory/candidates/{cand_id}/forward", status_code=201)
def forward(cand_id: str, body: ForwardBody | None = None):
    body = body or ForwardBody()
    st = store()
    c = st.candidate(body.run_id, cand_id) if body.run_id else st.find_candidate(cand_id)
    if c is None:
        raise HTTPException(404, "Kandidaat niet gevonden")
    if c["status"] != "FORWARD_ELIGIBLE":
        reasons = c["reasons"] or []
        raise HTTPException(409, f"Alleen FORWARD_ELIGIBLE kandidaten kunnen worden geregistreerd; status is "
                                 f"{c['status']}" + (f": {'; '.join(reasons[:3])}" if reasons else ""))
    from . import forward_rules, research_api
    from .daily_data import DailyPartitionStore
    from .data_manager import DEFAULT_DAILY_STORE, DataError
    from .forward_registry import ForwardRegistry
    from .research import backtest as rbt
    l5 = st.stage_result(c["run_id"], cand_id, "L5")
    spec = c["candidate"]["spec"]
    params = (l5 or {}).get("payload", {}).get("params")
    frozen_at = datetime.now(timezone.utc)
    try:
        config = research_api._rule_configuration(
            spec, params, dataclasses.asdict(rbt.default_contract(spec["symbol"])), capital=body.starting_capital,
            trade_risk=spec["risk"]["percent"] / 100.0, portfolio_limit=body.portfolio_risk_limit,
            leverage=body.leverage, as_of=frozen_at)
        frozen = forward_rules.freeze(ForwardRegistry(DailyPartitionStore(DEFAULT_DAILY_STORE)), config,
                                      frozen_at=frozen_at)
    except (DataError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    st.set_candidate(c["run_id"], cand_id, evidence_class="PROSPECTIVE_FORWARD")
    st.log(c["run_id"], "info", f"{cand_id}: forward geregistreerd ({frozen['id']})")
    return {"registration_id": frozen["id"], "frozen_at": frozen["frozen_at"],
            "snapshot_sha256": frozen["snapshot_sha256"], "status": "FROZEN", "mode": "DAILY_DELAYED_FORWARD",
            "evidence_class": "PROSPECTIVE_FORWARD"}


@router.get("/api/factory/audit")
def audit(limit: int = Query(500, ge=1, le=5000)):
    v = vault()
    return {"audit": v.audit_log(limit), "sealed_results": v.results(),
            "multiple_testing": v.multiple_testing()}


@router.get("/api/factory/outcomes")
def outcomes_legend():
    """Outcome categories of candidates (V10.1); every candidate payload carries outcome, rejection_kind,
    reasons_nl, missing_nl and next_test_nl."""
    return {"outcomes": list(factory.OUTCOMES), "rejection_kind": factory.REJECTION_KIND,
            "dual_positive_rule_nl": "Automatisch gevonden strategieen worden alleen gepromoveerd (en als kandidaat "
                                     "getoond) als zowel de ontwikkelbacktest (netto > 0, PF > 1) als de walk-forward "
                                     "OOS (netto > 0, verwachting > 0) positief zijn.",
            "dsr_gate": {"variant": effective_trials.GATE_VARIANT, "variants": effective_trials.VARIANTS}}
