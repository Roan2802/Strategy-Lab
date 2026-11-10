"""V8 research endpoints: library, builder, validation, discovery, ledger, lifecycle, monitoring."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from runtime.strategy_lab import rule_engine
from . import library
from .research import backtest as rbt
from .research import codegen, validation
from .research.jobs import JobManager
from .research.ledger import ResearchLedger
from .research.lifecycle import Lifecycle

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STRATEGY_ROOT = ROOT / "runtime" / "strategies"
SPEC_ROOT = HERE / "data_cache" / "builder_specs"
router = APIRouter()
JOBS = JobManager(max_concurrent=2)
_ledger: ResearchLedger | None = None
_lifecycle: Lifecycle | None = None
_catalogue: list | None = None
_data_manager = None
_frames_lock = threading.Lock()
_frames: dict = {}


def configure(data_manager) -> None:
    global _data_manager
    _data_manager = data_manager


def ledger() -> ResearchLedger:
    global _ledger
    if _ledger is None:
        _ledger = ResearchLedger(HERE / "data_cache" / "research_ledger.sqlite3")
    return _ledger


def lifecycle() -> Lifecycle:
    global _lifecycle
    if _lifecycle is None:
        _lifecycle = Lifecycle(HERE / "data_cache" / "lifecycle.sqlite3")
    return _lifecycle


def catalogue() -> list:
    global _catalogue
    if _catalogue is None:
        try:
            _catalogue = library.load_catalogue()
        except FileNotFoundError as exc:
            raise HTTPException(404, "Researchbibliotheek is nog niet geïmporteerd") from exc
    return _catalogue


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _strategy_spec(spec: dict) -> dict:
    """Spec without UI/research metadata, as accepted by the rule engine."""
    return {k: v for k, v in spec.items() if k != "meta"}


def _validated(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise HTTPException(422, "spec must be an object")
    errors = rule_engine.validate_spec(_strategy_spec(spec))
    if errors:
        raise HTTPException(422, "; ".join(errors))
    return spec


def _defaults(spec: dict) -> dict:
    return {p["name"]: p.get("default") for p in spec.get("params", [])}


# ---------------------------------------------------------------- data
def _frames_for(symbol: str, timeframe: str, start: str, end: str, warmup_days: int = 30):
    """Bid/Ask frames covering [start - warmup, end). Cached per request window."""
    if _data_manager is None:
        raise HTTPException(503, "Data manager not configured")
    begin = (pd.Timestamp(start) - pd.Timedelta(days=warmup_days)).strftime("%Y-%m-%d")
    key = (symbol.upper(), timeframe.upper(), begin, end)
    with _frames_lock:
        hit = _frames.get(key)
    if hit is not None:
        return hit
    try:
        bid, ask, manifest = rbt.load_bid_ask(_data_manager, symbol, timeframe, begin, end)
    except Exception as exc:  # DataError and friends: show to user
        raise HTTPException(422, f"Data niet beschikbaar: {exc}") from exc
    if bid.empty:
        raise HTTPException(422, f"Geen Dukascopy-candles voor {symbol} {timeframe} {start}–{end}")
    with _frames_lock:
        if len(_frames) > 6:
            _frames.pop(next(iter(_frames)))
        _frames[key] = (bid, ask, manifest)
    return bid, ask, manifest


def _dataset_sha(manifest: dict, symbol: str, timeframe: str, start: str, end: str) -> str:
    keep = {k: manifest.get(k) for k in ("bid_source", "ask_source", "daily_partitions")}
    return _sha({"m": keep, "s": symbol, "tf": timeframe, "a": start, "b": end})


def _make_backtest_fn(spec: dict, bid, ask, *, capital: float, commission_per_lot: float = 0.0):
    """backtest_fn(params, start, end, **stress) contract used by the validation engine.

    Indicators are computed with a causal warm-up prefix before ``start``; only trades whose entry
    lies inside [start, end) are counted."""
    clean = _strategy_spec(spec)

    def run(params: dict, start: str, end: str, **stress) -> dict:
        s, e = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
        prefix = s - pd.Timedelta(days=30)
        b = bid[(bid.index >= prefix) & (bid.index < e)]
        a = ask[(ask.index >= prefix) & (ask.index < e)]
        kwargs = {"capital": capital, "commission_per_lot": commission_per_lot, "trade_from": s, **stress}
        return rbt.run_rules_backtest(clean, b, a, {**_defaults(clean), **(params or {})}, **kwargs)
    return run


# ---------------------------------------------------------------- library
@router.get("/api/library")
def library_list(q: str | None = None, grade: str | None = None, suitability: str | None = None,
                 priority: bool = False, market: str | None = None, trader: str | None = None):
    cat = catalogue()
    items = library.search(cat, q=q, grade=grade, market=market, trader=trader)
    if suitability:
        items = [r for r in items if r.get("dukascopy_ohlc_suitability") == suitability]
    if priority:
        items = [r for r in items if r.get("priority_prototype")]
    grades = {g: sum(1 for r in cat if r.get("evidence_grade") == g) for g in "ABC"}
    slim = [{k: r.get(k) for k in ("id", "title", "strategy", "trader", "markets", "market", "timeframes", "timeframe",
                                   "mechanism", "evidence_grade", "dukascopy_ohlc_suitability", "priority_prototype",
                                   "unknown_rules", "translation_status")} for r in items]
    for row in slim:
        row["title"] = row.get("title") or row.get("strategy") or row["id"]
        if isinstance(row.get("mechanism"), list):
            row["mechanism"] = " ".join(map(str, row["mechanism"]))
    return {"items": slim, "summary": {"count": len(cat), "grades": grades}}


@router.get("/api/library/components")
def library_components():
    return {"components": library.component_concepts(catalogue())}


@router.get("/api/library/{dossier_id}")
def library_dossier(dossier_id: str):
    try:
        d = library.get_dossier(catalogue(), dossier_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(404, "Dossier niet gevonden") from exc
    d = dict(d)
    d["title"] = d.get("title") or d.get("strategy") or d.get("id")
    if "markdown" not in d:
        d["markdown"] = {k: v for k, v in d.items() if k.endswith(".md")}
    return {"dossier": d}


_VARIANT_HINTS = [
    (("nr4", "nr7", "inside"), "nr4_inside_bar_breakout"),
    (("orb", "opening range", "opening-range", "opening_range"), "opening_range_breakout"),
    (("donchian", "breakout", "vault"), "donchian_breakout"),
    (("bollinger", "mean reversion", "mean-reversion"), "bollinger_mean_reversion"),
]


@router.post("/api/library/{dossier_id}/variant")
def library_variant(dossier_id: str):
    d = library_dossier(dossier_id)["dossier"]
    text = json.dumps(d, ensure_ascii=False).casefold()
    template = next((t for keys, t in _VARIANT_HINTS if any(k in text for k in keys)), "ema_rsi_pullback")
    spec = json.loads(json.dumps(codegen.TEMPLATES[template]))
    spec["name"] = re.sub(r"[^A-Za-z0-9]", "", f"RV{dossier_id.split('_')[0]}{template.title()}")[:40]
    spec["meta"] = {
        "translation_status": "RESEARCH_VARIANT",
        "source_dossier": dossier_id,
        "starting_template": template,
        "original_instrument": d.get("original_instrument"),
        "hypothesis": (f"Toetsbare vertaling van dossier {dossier_id} (graad {d.get('evidence_grade')}) "
                       f"op {spec.get('symbol')} {spec.get('timeframe')}. Niet het exacte origineel; "
                       f"onbekende regels: {len(d.get('unknown_rules') or [])}. Pas regels aan op het dossier en "
                       "leg een nieuwe hypothese vast voor een ander instrument dan het origineel."),
        "unknown_rules": d.get("unknown_rules") or [],
    }
    return {"spec": spec}


# ---------------------------------------------------------------- builder
class SpecBody(BaseModel):
    spec: dict
    overwrite: bool = False


class BuilderBacktestBody(BaseModel):
    spec: dict
    params: dict = Field(default_factory=dict)
    symbol: str | None = None
    timeframe: str | None = None
    start: str
    end: str
    capital: float = Field(10000, gt=0)
    commission_per_lot: float = Field(0.0, ge=0)
    slippage_points: float = Field(0.0, ge=0)
    spread_multiplier: float = Field(1.0, ge=1.0, le=5.0)


@router.get("/api/builder/templates")
def builder_templates():
    return {"templates": codegen.TEMPLATES}


@router.post("/api/builder/validate")
def builder_validate(body: SpecBody):
    clean = _strategy_spec(body.spec)
    try:
        errors = rule_engine.validate_spec(clean)
    except Exception as exc:  # malformed spec structure
        errors = [f"Ongeldige specificatie: {exc}"]
    if errors:
        return {"errors": errors, "description": "", "code": ""}
    return {"errors": [], "description": rule_engine.describe(clean), "code": codegen.generate_jesse_strategy(clean),
            "spec_sha256": _sha(clean)}


@router.get("/api/builder/specs")
def builder_specs():
    SPEC_ROOT.mkdir(parents=True, exist_ok=True)
    out = []
    for path in sorted(SPEC_ROOT.glob("*.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
            out.append({"name": spec.get("name", path.stem), "symbol": spec.get("symbol"), "timeframe": spec.get("timeframe")})
        except (OSError, json.JSONDecodeError):
            continue
    return {"specs": out}


def _spec_path(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name or ""):
        raise HTTPException(422, "Ongeldige strategienaam (letters, cijfers, _)")
    return SPEC_ROOT / f"{name}.json"


@router.get("/api/builder/specs/{name}")
def builder_spec(name: str):
    path = _spec_path(name)
    if not path.is_file():
        raise HTTPException(404, "Specificatie niet gevonden")
    return {"spec": json.loads(path.read_text(encoding="utf-8"))}


@router.post("/api/builder/save")
def builder_save(body: SpecBody):
    spec = _validated(body.spec)
    name = spec.get("name", "")
    path = _spec_path(name)
    try:
        written = codegen.write_strategy(_strategy_spec(spec), STRATEGY_ROOT, overwrite=body.overwrite)
    except (FileExistsError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    SPEC_ROOT.mkdir(parents=True, exist_ok=True)
    if path.exists():
        history = SPEC_ROOT / "history"
        history.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        (history / f"{name}.{stamp}.json").write_bytes(path.read_bytes())
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    origin = "library" if spec.get("meta", {}).get("source_dossier") else spec.get("meta", {}).get("origin", "builder")
    lc = lifecycle()
    if not lc.get(name):
        lc.register(name, origin, spec=spec, description=spec.get("meta", {}).get("hypothesis"))
        lc.transition(name, "PROTOTYPE", "Regels opgeslagen en Jesse-code gegenereerd", actor="builder")
    return {"strategy": name, "path": str(written.relative_to(ROOT)) if written.is_relative_to(ROOT) else str(written),
            "spec_sha256": _sha(_strategy_spec(spec))}


@router.post("/api/builder/backtest")
def builder_backtest(body: BuilderBacktestBody):
    spec = _validated(body.spec)
    symbol = body.symbol or spec.get("symbol") or "XAUUSD"
    timeframe = body.timeframe or spec.get("timeframe") or "M15"
    if pd.Timestamp(body.end) <= pd.Timestamp(body.start):
        raise HTTPException(422, "Einddatum moet na de startdatum liggen")
    if pd.Timestamp(body.end) - pd.Timestamp(body.start) > pd.Timedelta(days=3700):
        raise HTTPException(422, "Periode te lang (max ~10 jaar per researchbacktest)")
    spec = {**spec, "symbol": symbol, "timeframe": timeframe}
    bid, ask, manifest = _frames_for(symbol, timeframe, body.start, body.end)
    fn = _make_backtest_fn(spec, bid, ask, capital=body.capital, commission_per_lot=body.commission_per_lot)
    result = fn(body.params, body.start, body.end, slippage_points=body.slippage_points,
                spread_multiplier=body.spread_multiplier)
    m = result["metrics"]
    ledger_id = ledger().record(
        kind="BACKTEST", family=spec.get("meta", {}).get("family", "builder"), symbol=symbol, timeframe=timeframe,
        start=body.start, end=body.end, spec=spec, spec_sha256=_sha(_strategy_spec(spec)),
        params={**_defaults(spec), **body.params},
        dataset_sha256=_dataset_sha(manifest, symbol, timeframe, body.start, body.end),
        cost_model={"commission_per_lot": body.commission_per_lot, "slippage_points": body.slippage_points,
                         "spread_multiplier": body.spread_multiplier, "spread": "Dukascopy Bid/Ask"},
        status="TESTED", metrics=m)
    result["ledger_id"] = ledger_id
    result["equity"] = result.get("equity", [])[-4000:]
    return result


# ---------------------------------------------------------------- validation
class ValidateBody(BaseModel):
    spec: dict | None = None
    strategy: dict | None = None
    symbol: str | None = None
    timeframe: str | None = None
    start: str
    end: str
    capital: float = Field(10000, gt=0)
    commission_per_lot: float = Field(0.0, ge=0)
    fee: float = Field(0.0006, ge=0, le=0.05)
    account_type: str | None = None
    holdout_percent: float = Field(20, ge=0, le=50)
    walk_forward: dict = Field(default_factory=dict)
    stability: dict = Field(default_factory=dict)
    pbo: dict = Field(default_factory=dict)
    monte_carlo: dict = Field(default_factory=dict)
    cost_stress: dict = Field(default_factory=dict)
    params: dict = Field(default_factory=dict)
    thresholds: dict = Field(default_factory=dict)


def _param_grid(spec: dict, per_param: int = 4) -> dict:
    grid = {}
    for p in spec.get("params", []):
        lo, hi, default = p.get("min"), p.get("max"), p.get("default")
        if lo is None or hi is None:
            continue
        values = {default} if default is not None else set()
        for i in range(per_param):
            v = lo + (hi - lo) * i / max(1, per_param - 1)
            values.add(int(round(v)) if p.get("type") == "int" else round(v, 6))
        grid[p["name"]] = sorted(values)
    return grid


def _strategy_ref(body: dict) -> tuple[str, object]:
    """(kind, ref) from a validate body: legacy ``spec`` or ``strategy: {kind, name?, spec?, template?}``."""
    strategy = body.get("strategy") or {}
    if body.get("spec") is not None and not strategy:
        return "builder", {"spec": body["spec"]}
    kind = strategy.get("kind")
    if kind == "builder":
        if isinstance(strategy.get("spec"), dict):
            return kind, {"spec": strategy["spec"]}
        if strategy.get("template"):
            return kind, {"template": strategy["template"]}
        return kind, {"name": strategy.get("name")}
    if kind in ("jesse", "plugin"):
        if not strategy.get("name"):
            raise HTTPException(422, f"Geef de naam van de {kind}-strategie op")
        return kind, strategy["name"]
    raise HTTPException(422, "Geef spec, of strategy met kind builder, jesse of plugin")


def _validation_adapter(ctx, kind: str, ref, body: dict):
    from .research import adapters
    ctx.progress(0.01, "Strategie en Dukascopy-data laden")
    try:
        return adapters.make_adapter(
            kind, ref, body.get("symbol"), body.get("timeframe"), body["start"], body["end"],
            float(body.get("capital", 10000)), commission_per_lot=float(body.get("commission_per_lot") or 0),
            fee=float(body.get("fee", 0.0006)), account_type=body.get("account_type"),
            check=ctx.check, is_cancelled=lambda: ctx.cancelled)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _optimizer_ids(adapter) -> set:
    from .research import adapters
    if not adapter.jesse_strategy:
        return set()
    try:
        return adapters.optimizer_trial_ids(adapter.jesse_strategy, adapter.symbol)
    except Exception:  # trial accounting is best effort for unreadable run folders
        return set()


def _run_validation(ctx, body: dict) -> dict:
    from .research import validation_runner
    kind, ref = _strategy_ref(body)
    if kind == "builder" and isinstance(ref, dict) and isinstance(ref.get("spec"), dict):
        spec = ref["spec"]
        body = {**body, "symbol": body.get("symbol") or spec.get("symbol"),
                "timeframe": body.get("timeframe") or spec.get("timeframe")}
    adapter = _validation_adapter(ctx, kind, ref, body)
    result = validation_runner.run_validation(ctx, adapter, body, ledger=ledger(), lifecycle=lifecycle(),
                                              optimizer_ids=_optimizer_ids(adapter))
    result["strategy"] = {"kind": kind, "name": adapter.name}
    return result


def _check_strategy_exists(kind: str, ref) -> str:
    if kind == "builder":
        if isinstance(ref, dict) and isinstance(ref.get("spec"), dict):
            return _validated(ref["spec"]).get("name") or "inline"
        if isinstance(ref, dict) and ref.get("template"):
            if ref["template"] not in codegen.TEMPLATES:
                raise HTTPException(404, "Sjabloon niet gevonden")
            return ref["template"]
        path = _spec_path((ref or {}).get("name") or "")
        if not path.is_file():
            raise HTTPException(404, "Specificatie niet gevonden")
        return ref["name"]
    if kind == "plugin":
        from . import plugins
        try:
            plugins.load(str(ref))
        except (KeyError, ValueError) as exc:
            raise HTTPException(404, str(exc)) from exc
        return str(ref)
    from . import server
    if ref not in {x["name"] for x in server._strategies()}:
        raise HTTPException(404, f"Jesse-strategie {ref} niet gevonden")
    return str(ref)


@router.post("/api/validate")
def validate_start(body: ValidateBody):
    payload = body.model_dump()
    if body.spec is not None and not body.strategy:
        payload["spec"] = _validated(body.spec)
    kind, ref = _strategy_ref(payload)
    name = _check_strategy_exists(kind, ref)
    hours = {"builder": 1, "plugin": 8, "jesse": 8}[kind]
    job_id = JOBS.submit("validation", f"Validatie {name} ({kind})", _run_validation, payload, timeout_s=hours * 3600)
    return {"job_id": job_id}


@router.get("/api/research/strategies")
def research_strategies():
    """Every validatable strategy with honest capabilities per engine."""
    from . import plugins, server
    from .research import adapters
    out = []
    jesse = {x["name"]: x for x in server._strategies()}
    twins = set()
    SPEC_ROOT.mkdir(parents=True, exist_ok=True)
    for path in sorted(SPEC_ROOT.glob("*.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = spec.get("name", path.stem)
        twin = adapters.jesse_twin_of(name) if name in jesse else None
        if twin:
            twins.add(twin)
        caps, lim = adapters.builder_capabilities(spec, twin)
        out.append({"kind": "builder", "id": name, "name": name, "label_nl": f"{name} (Builder-regels)",
                    "symbols": [spec.get("symbol")], "timeframes": [spec.get("timeframe")],
                    "params": adapters.builder_param_specs(spec), "capabilities": caps, "limitations_nl": lim,
                    "jesse_twin": twin})
    for name, info in jesse.items():
        if name in twins or (STRATEGY_ROOT / name / "spec.json").is_file():
            continue
        caps, lim = adapters.jesse_capabilities(name, info["parameters"], info.get("sha256"))
        out.append({"kind": "jesse", "id": name, "name": name, "label_nl": f"{name} (Jesse-strategie)",
                    "params": adapters.jesse_param_specs(info["parameters"]), "capabilities": caps,
                    "limitations_nl": lim, "sha256": info.get("sha256")})
    for item in plugins.available():
        if item.get("error"):
            out.append({"kind": "plugin", "id": item["id"], "name": item.get("name"), "label_nl": item["id"],
                        "params": {}, "capabilities": {k: False for k in (
                            "backtest", "optimize", "inspect", "validate", "walk_forward", "stability",
                            "cost_stress_spread", "forward")},
                        "limitations_nl": [f"Plugin laadt niet: {item['error']}"]})
            continue
        module = plugins.load(item["id"])
        caps, lim = adapters.plugin_capabilities(module)
        out.append({"kind": "plugin", "id": item["id"], "name": item.get("name"),
                    "label_nl": f"{item.get('name', item['id'])} (Python-plugin)", "symbols": item.get("symbols"),
                    "timeframes": [item.get("base_timeframe", "M1")], "params": adapters.plugin_param_specs(module),
                    "capabilities": caps, "limitations_nl": lim, "sha256": item.get("sha256")})
    return {"strategies": out}


# ---------------------------------------------------------------- discovery
@router.get("/api/discovery/families")
def discovery_families():
    from .research import discovery
    return {"families": [{"id": f["id"], "name": f.get("name"), "description": f.get("description"),
                          "default": bool(f.get("default"))} for f in discovery.FAMILIES],
            "weights": discovery.DEFAULT_WEIGHTS}


DISCOVERY_DEFAULT_SYMBOLS = ("XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "US30", "US500", "USATECHIDXUSD", "DE40", "BTCUSD")
DISCOVERY_DEFAULT_TIMEFRAMES = ("M15", "H1")
DISCOVERY_DEFAULT_IN_SAMPLE = {"start": "2004-01-01", "end": "2024-01-01"}


class DiscoveryBody(BaseModel):
    families: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    timeframes: list[str] = Field(default_factory=list)
    start: str | None = None
    end: str | None = None
    in_sample: dict | None = None
    out_of_sample: dict | None = None
    budget: dict = Field(default_factory=dict)
    criteria: dict = Field(default_factory=dict)
    weights: dict = Field(default_factory=dict)
    capital: float = Field(10000, gt=0)


def _discovery_default_symbols() -> list[str]:
    available = {item["symbol"].upper() for item in _data_manager.available_symbols()} if _data_manager else set()
    return [s for s in DISCOVERY_DEFAULT_SYMBOLS if s in available]


def _discovery_windows(body: DiscoveryBody) -> dict:
    """Explicit in_sample/out_of_sample, or defaults 2004-01-01..2024-01-01 / 2024-01-01..today; legacy start/end only
    when given without windows (then oos_fraction splitting applies)."""
    if body.in_sample or body.out_of_sample:
        ins, oos = body.in_sample, body.out_of_sample
        if ins and not oos:
            oos = {"start": ins["end"], "end": date.today().isoformat()}
        elif oos and not ins:
            ins = {"start": DISCOVERY_DEFAULT_IN_SAMPLE["start"], "end": oos["start"]}
        return {"in_sample": ins, "out_of_sample": oos}
    if body.start and body.end:
        return {"start": body.start, "end": body.end}
    return {"in_sample": dict(DISCOVERY_DEFAULT_IN_SAMPLE),
            "out_of_sample": {"start": DISCOVERY_DEFAULT_IN_SAMPLE["end"], "end": date.today().isoformat()}}


@router.post("/api/discovery/run")
def discovery_run(body: DiscoveryBody):
    from .research import discovery
    symbols = [s.upper() for s in body.symbols] or _discovery_default_symbols()
    timeframes = [t.upper() for t in body.timeframes] or list(DISCOVERY_DEFAULT_TIMEFRAMES)
    if not symbols or not timeframes:
        raise HTTPException(422, "Kies minstens één markt en één timeframe")
    budget = {**body.budget}
    budget["max_candidates"] = max(1, min(int(budget.get("max_candidates", 24)), 500))
    budget["max_minutes"] = max(1, min(float(budget.get("max_minutes", 10)), 240))
    budget["max_workers"] = max(1, min(int(budget.get("max_workers", 3)), 8))
    config = {**body.model_dump(exclude={"start", "end", "in_sample", "out_of_sample"}), **_discovery_windows(body),
              "symbols": symbols, "timeframes": timeframes, "budget": budget}

    def load(symbol, timeframe, start, end):
        try:
            return _frames_for(symbol, timeframe, start, end, warmup_days=0)
        except HTTPException as exc:
            if exc.status_code != 422:
                raise
            empty = pd.DataFrame(columns=list(rbt._COLS), index=pd.DatetimeIndex([], tz="UTC"))
            return empty, empty.copy(), {"load_error": str(exc.detail)}

    try:
        discovery._parse_windows(config)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, f"Ongeldige in_sample/out_of_sample: {exc}") from exc
    job_id = JOBS.submit("discovery", f"Ontdekken ({len(body.families) or 'standaard'} families, {len(symbols)} markten)",
                         discovery.run_discovery, config, load, ledger(), timeout_s=int(budget["max_minutes"] * 60 + 120))
    return {"job_id": job_id}


# ---------------------------------------------------------------- python plugins
class PluginRunBody(BaseModel):
    symbol: str = "XAUUSD"
    start: str
    end: str
    params: dict = Field(default_factory=dict)
    capital: float = Field(10000, gt=0)
    holdout_percent: float = Field(20, ge=0, le=50)
    monte_carlo_n: int = Field(1000, ge=100, le=10000)
    walk_forward: dict = Field(default_factory=dict)
    stability: dict = Field(default_factory=dict)
    pbo: dict = Field(default_factory=dict)
    cost_stress: dict = Field(default_factory=dict)


@router.get("/api/plugins")
def plugins_list():
    from . import plugins
    return {"plugins": plugins.available()}


def _plugin_fn(plugin_id: str, bid, ask, capital: float, progress=None):
    from . import plugins
    warmup = int(plugins.load(plugin_id).PLUGIN.get("warmup_days", 14))

    def run(params: dict, start: str, end: str, **stress) -> dict:
        s = pd.Timestamp(start, tz="UTC") - pd.Timedelta(days=warmup)
        e = pd.Timestamp(end, tz="UTC")
        b, a = bid[(bid.index >= s) & (bid.index < e)], ask[(ask.index >= s) & (ask.index < e)]
        r = plugins.run(plugin_id, b, a, params, capital=capital, start=start, end=end, stress=stress, progress=progress)
        r["metrics"] = validation.trade_metrics(r["trades"], capital)
        return r
    return run


def _plugin_frames(plugin_id: str, body: dict):
    from . import plugins
    module = plugins.load(plugin_id)
    allowed = [x.upper() for x in module.PLUGIN.get("symbols", [body["symbol"]])]
    if body["symbol"].upper() not in allowed:
        raise ValueError(f"Plugin ondersteunt alleen {module.PLUGIN.get('symbols')}")
    frames = _frames_for(body["symbol"], module.PLUGIN.get("base_timeframe", "M1"), body["start"], body["end"],
                         warmup_days=int(module.PLUGIN.get("warmup_days", 14)) + 1)
    return frames, module


def _run_plugin_backtest(ctx, plugin_id: str, body: dict) -> dict:
    ctx.progress(0.02, "Dukascopy Bid/Ask laden")
    (bid, ask, manifest), module = _plugin_frames(plugin_id, body)

    def progress(fraction, message):
        ctx.check()
        ctx.progress(0.05 + 0.9 * fraction, message)

    fn = _plugin_fn(plugin_id, bid, ask, body["capital"], progress=progress)
    r = fn(body.get("params") or {}, body["start"], body["end"])
    r["ledger_id"] = ledger().record(
        kind="BACKTEST", family=f"plugin:{plugin_id}", symbol=body["symbol"],
        timeframe=module.PLUGIN.get("base_timeframe", "M1"), start=body["start"], end=body["end"],
        params=r["params"], code_sha256=r["plugin_sha256"],
        dataset_sha256=_dataset_sha(manifest, body["symbol"], "M1", body["start"], body["end"]),
        cost_model={"spread": "Dukascopy Bid/Ask per minuut"}, status="TESTED", metrics=r["metrics"])
    r["equity"] = r.get("equity", [])[-4000:]
    return r


def _run_plugin_validation(ctx, plugin_id: str, body: dict) -> dict:
    cfg = {**body, "strategy": {"kind": "plugin", "name": plugin_id},
           "monte_carlo": {"n": int(body.get("monte_carlo_n", 1000)), **(body.get("monte_carlo") or {})}}
    return _run_validation(ctx, cfg)


def _plugin_job(plugin_id: str, kind: str, title: str, fn, body: "PluginRunBody", timeout: int):
    from . import plugins
    try:
        plugins.load(plugin_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"job_id": JOBS.submit(kind, title, fn, plugin_id, body.model_dump(), timeout_s=timeout)}


@router.post("/api/plugins/{plugin_id}/backtest")
def plugin_backtest(plugin_id: str, body: PluginRunBody):
    return _plugin_job(plugin_id, "plugin", f"Backtest {plugin_id}", _run_plugin_backtest, body, 4 * 3600)


@router.post("/api/plugins/{plugin_id}/validate")
def plugin_validate(plugin_id: str, body: PluginRunBody):
    return _plugin_job(plugin_id, "validation", f"Validatie {plugin_id}", _run_plugin_validation, body, 8 * 3600)


# ---------------------------------------------------------------- jobs
@router.get("/api/jobs")
def jobs_list():
    return {"jobs": JOBS.list()}


@router.get("/api/jobs/{job_id}")
def job_get(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Taak niet gevonden")
    return job


@router.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str):
    JOBS.cancel(job_id)
    return JOBS.get(job_id) or {}


# ---------------------------------------------------------------- ledger / lifecycle
@router.get("/api/ledger")
def ledger_list(status: str | None = None, kind: str | None = None, family: str | None = None,
                limit: int = Query(200, ge=1, le=2000)):
    led = ledger()
    return {"entries": led.list(status=status, kind=kind, family=family, limit=limit), "summary": led.summary()}


@router.get("/api/ledger/summary")
def ledger_summary():
    return ledger().summary()


@router.get("/api/ledger/{entry_id}")
def ledger_entry(entry_id: str):
    entry = ledger().get(entry_id)
    if not entry:
        raise HTTPException(404, "Logboekregel niet gevonden")
    if isinstance(entry.get("spec_json"), str):
        entry["spec"] = json.loads(entry["spec_json"])
    else:
        entry["spec"] = entry.get("spec_json") or entry.get("spec")
    return {"entry": entry}


@router.get("/api/lifecycle")
def lifecycle_list():
    lc = lifecycle()
    known = {r["name"]: r for r in lc.list()}
    rows = []
    for path in sorted(STRATEGY_ROOT.glob("*/__init__.py")):
        name = path.parent.name
        r = dict(known.pop(name, {"name": name, "status": "PROTOTYPE", "origin": "python"}))
        r["spec"] = bool(r.get("spec")) or (SPEC_ROOT / f"{name}.json").is_file()
        rows.append(r)
    for r in known.values():
        rows.append({**r, "spec": bool(r.get("spec"))})
    return {"strategies": rows}


@router.get("/api/lifecycle/{name}")
def lifecycle_get(name: str):
    try:
        r = lifecycle().get(name)
    except (KeyError, ValueError):
        r = None
    if not r:
        return {"name": name, "status": "PROTOTYPE", "history": [], "gates": []}
    try:
        r["gates"] = lifecycle().gates(name)
    except Exception:  # gates are informative only
        r["gates"] = []
    return r


class TransitionBody(BaseModel):
    to_status: str
    reason: str = Field(min_length=3, max_length=500)
    evidence_ref: str | None = None
    human_reviewer: str | None = None


@router.post("/api/lifecycle/{name}/transition")
def lifecycle_transition(name: str, body: TransitionBody):
    lc = lifecycle()
    evidence = None
    if body.evidence_ref:
        entry = ledger().get(body.evidence_ref)
        if entry:
            evidence = {"verdict": entry.get("verdict"), "ledger_id": body.evidence_ref}
    if body.human_reviewer:
        evidence = {**(evidence or {}), "human_reviewer": body.human_reviewer}
    try:
        if not lc.get(name):
            lc.register(name, "python")
        return lc.transition(name, body.to_status, body.reason, evidence_ref=body.evidence_ref, evidence=evidence)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


# ---------------------------------------------------------------- capabilities
@router.get("/api/capabilities")
def capabilities():
    return {"capabilities": [
        {"name": "Dukascopy M1 Bid/Ask OHLC (historisch)", "status": "UP TO DATE",
         "note": "Primaire prijsbron, lokaal in C:\\AI\\Dukascopy_data; originele bestanden worden nooit gewijzigd."},
        {"name": "Dagelijkse nieuwe Dukascopy-data", "status": "PARTIAL",
         "note": "Automatisch: de app vult ontbrekende dagen één voor één aan via de Dukascopy-datafeed (BID/ASK M1), met backoff bij storingen. Reserve: tickbestanden en de inbox-map."},
        {"name": "Dukascopy tickvolume / quotevolume", "status": "PARTIAL",
         "note": "Alleen als expliciete proxy; dit is geen verhandeld futuresvolume."},
        {"name": "Werkelijk futuresvolume (CME)", "status": "DATA_REQUIRED", "note": "Externe betaalde bron nodig (bijv. CME DataMine, Databento)."},
        {"name": "Aggressor buy/sell volume, delta", "status": "DATA_REQUIRED", "note": "Vereist tick-by-tick trades met aggressorvlag; niet afleidbaar uit M1-OHLC."},
        {"name": "Volume-at-price / VPOC / VAH / VAL", "status": "DATA_REQUIRED", "note": "Vereist trade prints; een OHLC-benadering zou fictief zijn en wordt niet gebouwd."},
        {"name": "L2/L3-orderboek, Bookmap-heatmap", "status": "DATA_REQUIRED", "note": "Historische orderboekdata (MBO/MBP) nodig; adapterinterface gedocumenteerd, geen simulatie."},
        {"name": "Economische kalender / nieuwsfilter", "status": "DATA_UNAVAILABLE", "note": "Geen point-in-time kalender met publicatietijdstempels aanwezig; nieuwsfilters blijven uit tot een betrouwbare bron is gekoppeld."},
        {"name": "Realtime quotefeed (papertrading)", "status": "DATA_REQUIRED", "note": "Gate D (paper) kan pas worden getoetst met een realtime feed; de dagelijkse download is geen realtime data."},
        {"name": "Live brokerkoppeling", "status": "DISABLED", "note": "Bewust uitgeschakeld. Vereist een aparte opdracht en expliciete toestemming."},
    ]}


# ---------------------------------------------------------------- forward rule registrations
import dataclasses  # noqa: E402
from . import forward_rules  # noqa: E402
from .daily_data import DailyPartitionStore, provider_symbol  # noqa: E402
from .data_manager import DEFAULT_DAILY_STORE, DataError  # noqa: E402
from .forward_registry import ForwardRegistry  # noqa: E402


class RuleRegistrationBody(BaseModel):
    spec_name: str | None = None
    spec: dict | None = None
    parameters: dict | None = None
    contract: dict
    starting_capital: float = Field(gt=0)
    trade_risk: float = Field(gt=0, le=1)
    portfolio_risk_limit: float = Field(gt=0, le=1)
    leverage: float = Field(default=1, ge=1, le=1000)


def _rule_spec(spec_name: str | None, spec: dict | None) -> dict:
    if spec is None:
        path = _spec_path(spec_name or "")
        if not path.is_file():
            raise HTTPException(404, "Specificatie niet gevonden")
        spec = json.loads(path.read_text(encoding="utf-8"))
    return _strategy_spec(_validated(spec))


def _rule_configuration(spec: dict, parameters: dict | None, contract: dict, *, capital: float, trade_risk: float,
                        portfolio_limit: float, leverage: float, as_of: datetime) -> dict:
    """Warm-up comes from DataManager candles strictly before ``as_of`` (the freeze time)."""
    if _data_manager is None:
        raise HTTPException(503, "Data manager not configured")
    seen: dict = {}

    def fetch(start, end):
        frame, manifest = _data_manager.candles(spec["symbol"], "M1", start.isoformat(), end.isoformat())
        seen.update(manifest)
        return frame, manifest.get("daily_partitions") or []

    warmup = forward_rules.warmup_from_fetch(fetch, spec, parameters, as_of=as_of)
    store = DailyPartitionStore(DEFAULT_DAILY_STORE)
    version = {"legacy_bid_sha256": (seen.get("bid_source") or {}).get("sha256"),
               "legacy_ask_sha256": (seen.get("ask_source") or {}).get("sha256"),
               "daily_watermark": store.watermark(provider_symbol(spec["symbol"]), as_of=as_of)}
    return forward_rules.build_configuration(
        spec, parameters, contract=contract, starting_capital=capital, trade_risk=trade_risk,
        portfolio_risk_limit=portfolio_limit, leverage=leverage, warmup=warmup, data_version=version)


@router.post("/api/forward/rule-registrations", status_code=201)
def freeze_rule_registration(body: RuleRegistrationBody):
    if body.spec is None and not body.spec_name:
        raise HTTPException(422, "Geef spec_name of spec op")
    spec = _rule_spec(body.spec_name, body.spec)
    frozen_at = datetime.now(timezone.utc)
    try:
        config = _rule_configuration(spec, body.parameters, body.contract, capital=body.starting_capital,
                                     trade_risk=body.trade_risk, portfolio_limit=body.portfolio_risk_limit,
                                     leverage=body.leverage, as_of=frozen_at)
        frozen = forward_rules.freeze(ForwardRegistry(DailyPartitionStore(DEFAULT_DAILY_STORE)), config,
                                      frozen_at=frozen_at)
    except (DataError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"registration_id": frozen["id"], "frozen_at": frozen["frozen_at"],
            "snapshot_sha256": frozen["snapshot_sha256"], "status": "FROZEN", "mode": "DAILY_DELAYED_FORWARD"}


@router.get("/api/forward/rule-registrations/preview")
def preview_rule_registration(spec_name: str, starting_capital: float = Query(10000, gt=0),
                              trade_risk: float | None = Query(None, gt=0, le=1),
                              portfolio_risk_limit: float = Query(0.05, gt=0, le=1),
                              leverage: float = Query(1, ge=1, le=1000)):
    spec = _rule_spec(spec_name, None)
    contract = rbt.default_contract(spec["symbol"])
    risk = trade_risk if trade_risk is not None else spec["risk"]["percent"] / 100.0
    as_of = datetime.now(timezone.utc)
    try:
        config = _rule_configuration(spec, None, dataclasses.asdict(contract), capital=starting_capital,
                                     trade_risk=risk, portfolio_limit=portfolio_risk_limit,
                                     leverage=leverage, as_of=as_of)
        forward_rules.validate_registration({"configuration": config, "sources": forward_rules.current_sources(),
                                             "frozen_at": as_of.isoformat(timespec="microseconds")})
    except (DataError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    warm = config["warmup"]
    summary = {**{k: v for k, v in warm.items() if k != "bars"}, "bars_omitted": True,
               "first_bar_at": forward_rules._iso_ms(warm["bars"][0][0])}
    return {"status": "PREVIEW_NOT_FROZEN", "mode": "DAILY_DELAYED_FORWARD",
            "configuration": {**config, "warmup": summary},
            "source_sha256": {name: hashlib.sha256(data).hexdigest()
                              for name, data in forward_rules.current_sources().items()},
            "defaults": {"contract": "default_contract(symbol)", "trade_risk": "spec risk.percent / 100"}}
