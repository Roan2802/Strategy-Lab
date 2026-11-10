"""Cross-market generalisation API (``/api/multimarket/*``). Runs as research jobs (progress + cancel)."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import research_api
from .research import adapters, factory, instruments, multimarket, protocol

HERE = Path(__file__).resolve().parent
RUNS_DIR = HERE / "data_cache" / "multimarket_runs"
router = APIRouter()
_live: dict = {}
_index: dict = {}
_lock = threading.Lock()


class StrategyRef(BaseModel):
    kind: str = Field(pattern="^(builder|factory|jesse|plugin)$")
    name: str | None = None
    origin_symbol: str | None = None
    candidate_id: str | None = None
    spec: dict | None = None
    params: dict | None = None


class RunBody(BaseModel):
    strategy: StrategyRef
    markets: list[str] | str = "all"
    timeframes: list[str] = Field(default_factory=lambda: ["M15", "H1"])
    start: str | None = None
    end: str | None = None
    reoptimise: bool = False
    risk_model: str = "native"
    risk_percent: float | None = Field(None, gt=0, le=5)
    capital: float = Field(10000, gt=0)
    stability: bool = True
    min_days: int | None = Field(None, ge=7)
    account_currency: str = Field("USD", pattern="^(USD|EUR|GBP|JPY|CAD|AUD)$")


def resolve_strategy(ref: StrategyRef) -> dict:
    if ref.kind in multimarket.ADAPTER_KINDS:
        if not ref.name:
            raise HTTPException(422, f"{ref.kind}: name vereist")
        try:
            ident = adapters.strategy_identity(ref.kind, ref.name)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"kind": ref.kind, "label": ref.name, "name": ref.name, "params": ref.params,
                "origin_symbol": ref.origin_symbol, "code_sha256": ident["sha256"]}
    if ref.spec is not None:
        return {"kind": ref.kind, "label": ref.name or ref.spec.get("name", "inline"), "spec": ref.spec,
                "params": ref.params}
    if ref.kind == "builder":
        if not ref.name:
            raise HTTPException(422, "builder: name of spec vereist")
        path = research_api._spec_path(ref.name)
        if not path.is_file():
            raise HTTPException(404, "Builder-specificatie niet gevonden")
        spec = json.loads(path.read_text(encoding="utf-8"))
        return {"kind": "builder", "label": ref.name, "spec": spec, "params": ref.params}
    if not ref.candidate_id:
        raise HTTPException(422, "factory: candidate_id of spec vereist")
    from . import factory_api
    det = factory.candidate_detail(factory_api.store(), ref.candidate_id, None, factory_api.vault())
    if det is None:
        raise HTTPException(404, "Kandidaat niet gevonden")
    cand = det["candidate"]
    return {"kind": "factory", "label": cand.get("name") or ref.candidate_id, "spec": cand["spec"],
            "params": ref.params or cand.get("params"), "candidate_id": ref.candidate_id}


def _persist(run_id: str, payload: dict) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = RUNS_DIR / f"{run_id}.json.tmp"
    tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
    tmp.replace(RUNS_DIR / f"{run_id}.json")


def _job(ctx, run_id: str, strategy: dict, body: dict):
    live = _live.setdefault(run_id, {})
    out = multimarket.run_cross_market(ctx, research_api._data_manager, strategy, body["markets"], body["timeframes"],
                                       body["start"], body["end"], ledger=research_api.ledger(),
                                       reoptimise=body["reoptimise"], risk_model=body["risk_model"],
                                       risk_percent=body["risk_percent"], capital=body["capital"],
                                       stability=body["stability"], run_id=run_id, live=live,
                                       min_days=body.get("min_days"),
                                       account_currency=body.get("account_currency", "USD"))
    _persist(run_id, {**out, "run_id": run_id, "status": "done", "progress": 1.0})
    return {"run_id": run_id}


@router.get("/api/multimarket/markets")
def markets(refresh: bool = False):
    dm = research_api._data_manager
    return {"account_currency": instruments.ACCOUNT_CURRENCY, "markets": instruments.registry(dm, refresh),
            "money_approval": instruments.money_approval(instruments.SYMBOLS, dm),
            "legend": {"VERIFIED_FROM_DATA": "gemeten uit Dukascopy-data", "ASSUMED": "aanname, niet broker-bevestigd",
                       "UNKNOWN": "onbekend", "BROKER_VERIFIED": "door de gebruiker met bron bevestigd brokerfeit"}}


@router.post("/api/multimarket/runs", status_code=202)
def start(body: RunBody):
    strategy = resolve_strategy(body.strategy)
    try:
        protocol.assert_search_range(body.start or protocol.DEV_START, body.end or protocol.SEALED_START,
                                     protocol.SEALED_START)
    except protocol.SealedAccessError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.risk_model == "fixed_lot":
        raise HTTPException(422, "NOT_SUPPORTED: fixed_lot; gebruik native, pct_equity_atr of pct_stop")
    if strategy["kind"] in multimarket.ADAPTER_KINDS and (body.reoptimise or body.risk_model != "native"):
        raise HTTPException(422, f"NOT_SUPPORTED: {strategy['kind']} ondersteunt alleen risk_model=native zonder "
                                 "reoptimise (eigen positiegrootte; nieuwe hypothese = nieuwe versie)")
    run_id = f"mm_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
    dump = body.model_dump()
    job_id = research_api.JOBS.submit("multimarket", f"Cross-market {strategy['label']}", _job, run_id, strategy, dump)
    with _lock:
        _index[run_id] = {"job_id": job_id, "label": strategy["label"],
                          "created_at": datetime.now(timezone.utc).isoformat(), "reoptimise": body.reoptimise}
    return {"run_id": run_id, "job_id": job_id, "status": "queued"}


def _stored(run_id: str):
    path = RUNS_DIR / f"{run_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


@router.get("/api/multimarket/runs/{run_id}")
def run(run_id: str):
    done = _stored(run_id)
    if done:
        return done
    meta = _index.get(run_id)
    if not meta:
        raise HTTPException(404, "Run niet gevonden")
    job = research_api.JOBS.get(meta["job_id"]) or {}
    live = _live.get(run_id, {})
    return {"run_id": run_id, "status": job.get("status", "unknown"), "progress": job.get("progress", 0.0),
            "message": job.get("message", ""), "error": job.get("error"), "results": list(live.get("results", [])),
            "combined": None, "equities": live.get("equities", {}), "generalisation": None,
            "config": live.get("config"), "ledger_ids": []}


@router.post("/api/multimarket/runs/{run_id}/cancel")
def cancel(run_id: str):
    meta = _index.get(run_id)
    if not meta:
        raise HTTPException(404, "Run niet gevonden")
    return {"cancelled": research_api.JOBS.cancel(meta["job_id"])}


@router.get("/api/multimarket/runs")
def runs(limit: int = 50):
    out = {}
    if RUNS_DIR.is_dir():
        for p in sorted(RUNS_DIR.glob("mm_*.json"), reverse=True)[:limit]:
            d = json.loads(p.read_text(encoding="utf-8"))
            g, c = d.get("generalisation") or {}, d.get("config") or {}
            out[d["run_id"]] = {"run_id": d["run_id"], "status": "done", "label": (c.get("strategy") or {}).get("label"),
                                "class": g.get("class"), "n_markets_positive": g.get("n_markets_positive"),
                                "n_markets_tested": g.get("n_markets_tested"), "reoptimise": c.get("reoptimise")}
    for rid, meta in _index.items():
        if rid not in out:
            job = research_api.JOBS.get(meta["job_id"]) or {}
            out[rid] = {"run_id": rid, "status": job.get("status", "unknown"), "progress": job.get("progress", 0.0),
                        "label": meta["label"], "reoptimise": meta["reoptimise"]}
    return {"runs": list(out.values())[:limit]}
