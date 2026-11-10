"""Strategy adapters: one ``backtest_fn(params, start, end, **cost_kwargs)`` contract for Builder rule specs,
Jesse strategies (real Jesse backtests through the Docker runner) and Python plugins.

Every adapter returns normalized results: ``trades`` with ``pnl`` (net of costs), ``entry_time``/``exit_time``
(ISO UTC) and ``side``; ``metrics`` from :func:`validation.trade_metrics` plus the engine's own numbers under
``engine_metrics``. Dates are half-open ``[start, end)`` calendar days, like the validation engine.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from . import validation
from .ledger import hypothesis_id

KINDS = ("builder", "jesse", "plugin")
JESSE_SPREAD_REASON_NL = ("Jesse rekent met Bid-candles en een fee-fractie; spread-/slippagestress vereist de "
                          "Bid/Ask-engine")
# Sizing/cost inputs are not strategy hypotheses: they are kept fixed in grids and stability checks.
FIXED_PARAM_RE = re.compile(r"(commission|position_size|^lots?$|_lots?$|^qty$|quantity|contract_size)", re.I)
HERE = Path(__file__).resolve().parent
ENGINE_FILES = (HERE / "backtest.py", HERE.parents[1] / "runtime" / "strategy_lab" / "rule_engine.py",
                HERE.parents[1] / "runtime" / "strategy_lab" / "execution.py")


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _files_sha(paths) -> str:
    digest = hashlib.sha256()
    for path in paths:
        if Path(path).is_file():
            digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def cast_param(spec: dict | None, value):
    if spec is None or value is None:
        return value
    kind = spec.get("type")
    if kind == "int":
        return int(round(float(value)))
    if kind == "float":
        return float(value)
    if kind == "categorical":
        return next((o for o in spec.get("options", []) if str(o) == str(value)), value)
    return value


def is_fixed_param(name: str, spec: dict) -> bool:
    return spec.get("optimize") is False or bool(FIXED_PARAM_RE.search(name))


def numeric_specs(param_specs: dict) -> dict:
    return {n: s for n, s in param_specs.items()
            if s.get("type") in ("int", "float") and s.get("min") is not None and s.get("max") is not None}


def param_grid(param_specs: dict, defaults: dict, per_param: int = 4) -> dict:
    """Value lists per optimizable parameter: ``per_param`` evenly spaced points in [min, max] plus the default;
    categorical parameters use their options."""
    grid = {}
    for name, spec in param_specs.items():
        if is_fixed_param(name, spec):
            continue
        if spec.get("type") == "categorical" and spec.get("options"):
            grid[name] = list(spec["options"])
            continue
        lo, hi = spec.get("min"), spec.get("max")
        if spec.get("type") not in ("int", "float") or lo is None or hi is None:
            continue
        values = {cast_param(spec, defaults[name])} if defaults.get(name) is not None else set()
        for i in range(per_param):
            v = lo + (hi - lo) * i / max(1, per_param - 1)
            values.add(int(round(v)) if spec["type"] == "int" else round(float(v), 6))
        grid[name] = sorted(values)
    return grid


def sample_candidates(grid: dict, defaults: dict, max_combos: int, seed: int = 0) -> list[dict]:
    """Defaults first, then distinct seeded random points from the grid (full product when it is small)."""
    base = {k: defaults.get(k) for k in grid}
    out, seen = [base], {_sha(base)}
    if not grid or max_combos <= 1:
        return out[:max(1, max_combos)]
    keys = list(grid)
    size = int(np.prod([len(grid[k]) for k in keys], dtype=float))
    if size <= 5000:
        rng = np.random.default_rng(seed)
        import itertools
        combos = [dict(zip(keys, v)) for v in itertools.product(*[grid[k] for k in keys])]
        for i in rng.permutation(len(combos)):
            if len(out) >= max_combos:
                break
            if _sha(combos[i]) not in seen:
                seen.add(_sha(combos[i]))
                out.append(combos[i])
        return out
    rng = np.random.default_rng(seed)
    attempts = 0
    while len(out) < max_combos and attempts < max_combos * 50:
        attempts += 1
        combo = {k: grid[k][int(rng.integers(len(grid[k])))] for k in keys}
        if _sha(combo) not in seen:
            seen.add(_sha(combo))
            out.append(combo)
    return out


def normalize_metrics(trades: list, capital: float, engine_metrics: dict | None = None,
                      engine_dd_pct: float | None = None) -> dict:
    m = validation.trade_metrics(trades, capital)
    m["trades"] = m["n_trades"]
    engine = {k: v for k, v in (engine_metrics or {}).items()
              if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if engine_dd_pct is not None:
        m["max_drawdown_pct"] = max(m["max_drawdown_pct"], abs(float(engine_dd_pct)))
    m["sharpe_daily"] = engine.get("sharpe_daily", m["sharpe"])
    m["engine_metrics"] = engine
    return m


def _iso_ms(value) -> str:
    return pd.Timestamp(float(value), unit="ms", tz="UTC").isoformat()


def normalize_jesse_trades(rows: list) -> list[dict]:
    """Jesse closed trades -> Strategy Lab contract. Jesse's PNL already includes its fee on both fills."""
    out = []
    for t in rows or []:
        if t.get("opened_at") is None or t.get("closed_at") is None or t.get("PNL") is None:
            continue
        out.append({"entry_time": _iso_ms(t["opened_at"]), "exit_time": _iso_ms(t["closed_at"]),
                    "side": "short" if str(t.get("type")).lower() == "short" else "long",
                    "entry_price": t.get("entry_price"), "exit_price": t.get("exit_price"), "qty": t.get("qty"),
                    "pnl": float(t["PNL"]), "costs": float(t.get("fee") or 0.0)})
    return sorted(out, key=lambda r: r["exit_time"])


class StrategyAdapter:
    """Uniform wrapper; ``fn`` is the validation engine's backtest_fn contract."""

    def __init__(self, *, kind, name, ref, symbol, timeframe, start, end, capital, run: Callable, defaults: dict,
                 param_specs: dict, family: str, hyp_key: str, code_sha256: str, engine_nl: str,
                 capabilities: dict, limitations_nl: list, supported_cost_scenarios: dict,
                 unsupported_cost_scenarios: list, dataset_sha256: str | None = None, spec: dict | None = None,
                 spec_sha256: str | None = None, jesse_strategy: str | None = None, check: Callable | None = None,
                 extra: dict | None = None):
        self.kind, self.name, self.ref = kind, name, ref
        self.symbol, self.timeframe, self.start, self.end, self.capital = symbol, timeframe, start, end, capital
        self._run, self.defaults, self.param_specs = run, dict(defaults), dict(param_specs)
        self.family, self.hyp_key, self.code_sha256 = family, hyp_key, code_sha256
        self.engine_nl, self.capabilities, self.limitations_nl = engine_nl, capabilities, list(limitations_nl)
        self.supported_cost_scenarios = supported_cost_scenarios
        self.unsupported_cost_scenarios = unsupported_cost_scenarios
        self.dataset_sha256, self.spec, self.spec_sha256 = dataset_sha256, spec, spec_sha256
        self.jesse_strategy, self.check, self.extra = jesse_strategy, check, dict(extra or {})
        self.calls = 0

    def merge(self, params: dict | None) -> dict:
        merged = dict(self.defaults)
        for k, v in (params or {}).items():
            merged[k] = cast_param(self.param_specs.get(k), v)
        return merged

    def hypothesis_id(self, params: dict | None) -> str:
        return hypothesis_id(self.hyp_key, self.merge(params))

    def fn(self, params: dict, start: str, end: str, **cost_kwargs) -> dict:
        if self.check:
            self.check()
        self.calls += 1
        return self._run(self.merge(params), start, end, **cost_kwargs)

    @property
    def optimizable_specs(self) -> dict:
        return {n: s for n, s in self.param_specs.items() if not is_fixed_param(n, s)}

    @property
    def fixed_params(self) -> list[str]:
        return [n for n, s in self.param_specs.items() if is_fixed_param(n, s)]

    def grid(self, per_param: int = 4) -> dict:
        return param_grid(self.param_specs, self.defaults, per_param)

    def candidates(self, max_combos: int, seed: int = 0, base: dict | None = None) -> list[dict]:
        merged = self.merge(base)
        return [self.merge(c) for c in sample_candidates(self.grid(), merged, max_combos, seed)]

    def rerun_reason(self) -> str | None:
        """Non-empty when results of this validation were produced under different engine settings and the pipeline
        must be repeated once (Jesse: spot results before the automatic switch to futures)."""
        return None

    def stability_specs(self, params: dict) -> dict:
        specs = numeric_specs(self.optimizable_specs)
        return {n: s for n, s in specs.items() if isinstance(params.get(n), (int, float))}

    def describe(self) -> dict:
        return {"kind": self.kind, "name": self.name, "family": self.family, "symbol": self.symbol,
                "timeframe": self.timeframe, "engine_nl": self.engine_nl, "code_sha256": self.code_sha256,
                "spec_sha256": self.spec_sha256, "dataset_sha256": self.dataset_sha256,
                "defaults": self.defaults, "param_specs": self.param_specs, "fixed_params": self.fixed_params,
                "grid": self.grid(), "capabilities": self.capabilities, "limitations_nl": self.limitations_nl,
                "supported_cost_scenarios": self.supported_cost_scenarios,
                "unsupported_cost_scenarios": self.unsupported_cost_scenarios, **self.extra}


# ------------------------------------------------------------------ capability descriptors
def _has_ranges(param_specs: dict) -> bool:
    return any(not is_fixed_param(n, s) and (s.get("type") == "categorical" and s.get("options") or
                                             (s.get("min") is not None and s.get("max") is not None))
               for n, s in param_specs.items())


def builder_param_specs(spec: dict) -> dict:
    return {p["name"]: {k: p.get(k) for k in ("type", "min", "max", "options", "default") if p.get(k) is not None}
            | {"type": p.get("type", "float")} for p in spec.get("params", [])}


def builder_capabilities(spec: dict, jesse_twin: str | None = None) -> tuple[dict, list[str]]:
    ranges = _has_ranges(builder_param_specs(spec))
    caps = {"backtest": True, "optimize": bool(jesse_twin), "inspect": bool(jesse_twin), "validate": True,
            "walk_forward": ranges, "stability": bool(numeric_specs(builder_param_specs(spec))),
            "cost_stress_spread": True, "forward": True}
    lim = ["Forward test is dagelijks vertraagd op nieuwe Dukascopy-data (geen realtime papertrading).", "Contractspecificaties (contractgrootte, lotstap, commissie) zijn aannames, niet door een broker bevestigd; bedragen zijn onderzoeksschattingen."]
    if not jesse_twin:
        lim.append("Optimizer en Inspector werken via de Jesse-tester; sla de regels op om een Jesse-kopie te maken.")
    if not ranges:
        lim.append("Geen parameters met min/max: walk-forward en parameterstabiliteit zijn niet mogelijk.")
    return caps, lim


def jesse_forward_supported(name: str, sha256: str | None) -> tuple[bool, str]:
    from ..forward_engine import LAB_SHA256
    if name == "LabBreakout" and sha256 == LAB_SHA256:
        return True, ("Forward alleen via de geauditeerde LabBreakout-snapshot op M1 (eigen Bid/Ask-replica van de "
                      "regels, niet de Jesse-engine zelf).")
    if name == "LabBreakout":
        return False, "LabBreakout-code wijkt af van de geauditeerde snapshot; forward-registratie wordt geweigerd."
    return False, ("De forward-engine ondersteunt alleen de geauditeerde LabBreakout-snapshot en Builder-regels; "
                   "voor deze Jesse-strategie is geen bevroren forward test beschikbaar.")


def jesse_param_specs(definitions: list[dict]) -> dict:
    out = {}
    for d in definitions:
        kind = d.get("type")
        if kind in ("int", "float"):
            out[d["name"]] = {"type": kind, "min": d.get("min"), "max": d.get("max"), "default": d.get("default")}
        elif kind == "categorical":
            out[d["name"]] = {"type": "categorical", "options": list(d.get("options") or []),
                              "default": d.get("default")}
        else:
            out[d["name"]] = {"type": kind, "default": d.get("default")}
    return out


def jesse_capabilities(name: str, definitions: list[dict], sha256: str | None) -> tuple[dict, list[str]]:
    specs = jesse_param_specs(definitions)
    forward, forward_nl = jesse_forward_supported(name, sha256)
    caps = {"backtest": True, "optimize": bool(_has_ranges(specs)), "inspect": True, "validate": True,
            "walk_forward": _has_ranges(specs), "stability": bool(numeric_specs(
                {n: s for n, s in specs.items() if not is_fixed_param(n, s)})),
            "cost_stress_spread": False, "forward": forward}
    lim = [JESSE_SPREAD_REASON_NL + "; alleen fee-stress (×1,5 en ×2) wordt getoetst.",
           "Elke validatiestap draait echte Jesse-backtests in Docker (ca. 10–20 s per backtest); houd max_combos klein.",
           forward_nl]
    if not _has_ranges(specs):
        lim.append("Geen parameters met min/max of opties: walk-forward en parameterstabiliteit zijn niet mogelijk.")
    return caps, lim


def plugin_stress_support(module) -> set[str]:
    try:
        text = Path(module.__file__).read_text(encoding="utf-8")
    except (OSError, TypeError, AttributeError):
        text = ""
    declared = module.PLUGIN.get("stress")
    if isinstance(declared, (list, tuple)):
        return set(declared)
    return {k for k in ("spread_multiplier", "commission_per_lot", "slippage_points", "entry_delay_bars",
                        "exit_delay_bars") if k in text}


def plugin_param_specs(module) -> dict:
    out = {}
    for p in module.PLUGIN.get("params", []):
        spec = {k: p.get(k) for k in ("type", "min", "max", "options", "default", "optimize") if k in p}
        spec.setdefault("type", "float")
        out[p["name"]] = spec
    return out


def plugin_capabilities(module) -> tuple[dict, list[str]]:
    specs = plugin_param_specs(module)
    stress = plugin_stress_support(module)
    ranges = _has_ranges(specs)
    caps = {"backtest": True, "optimize": False, "inspect": False, "validate": True, "walk_forward": ranges,
            "stability": bool(numeric_specs({n: s for n, s in specs.items() if not is_fixed_param(n, s)})),
            "cost_stress_spread": "spread_multiplier" in stress, "forward": False}
    lim = ["Contractspecificaties (contractgrootte, lotstap, commissie) zijn aannames, niet door een broker bevestigd; bedragen zijn onderzoeksschattingen.", "Forward test voor plugins is nog niet beschikbaar: de forward-engine kent alleen Builder-regels en de "
           "geauditeerde LabBreakout-snapshot.",
           "Geen optimizer of Inspector voor plugins; walk-forward kiest uit een kleine seeded parametergrid."]
    if not ranges:
        lim.append("De plugin declareert geen parameters met min/max: walk-forward en stabiliteit zijn niet mogelijk.")
    if "spread_multiplier" not in stress:
        lim.append("De plugin verwerkt stress['spread_multiplier'] niet: spreadstress is niet toetsbaar.")
    return caps, lim


# ------------------------------------------------------------------ builders
def _resolve_builder_spec(ref) -> dict:
    from .. import research_api as ra
    from . import codegen
    if isinstance(ref, dict) and isinstance(ref.get("spec"), dict):
        return ref["spec"]
    if isinstance(ref, dict) and ref.get("template"):
        if ref["template"] not in codegen.TEMPLATES:
            raise ValueError(f"Onbekend sjabloon: {ref['template']}")
        return json.loads(json.dumps(codegen.TEMPLATES[ref["template"]]))
    name = ref.get("name") if isinstance(ref, dict) else ref
    path = ra._spec_path(str(name or ""))
    if not path.is_file():
        raise ValueError(f"Builder-specificatie niet gevonden: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def jesse_twin_of(spec_name: str | None) -> str | None:
    from .. import research_api as ra
    if spec_name and (ra.STRATEGY_ROOT / spec_name / "spec.json").is_file():
        return spec_name
    return None


def _builder_adapter(ref, symbol, timeframe, start, end, capital, *, commission_per_lot=0.0, check=None, **_):
    from .. import research_api as ra
    spec = ra._validated(_resolve_builder_spec(ref))
    symbol = (symbol or spec.get("symbol") or "XAUUSD").upper()
    timeframe = (timeframe or spec.get("timeframe") or "M15").upper()
    spec = {**spec, "symbol": symbol, "timeframe": timeframe}
    bid, ask, manifest = ra._frames_for(symbol, timeframe, start, end)
    inner = ra._make_backtest_fn(spec, bid, ask, capital=capital, commission_per_lot=commission_per_lot)

    def run(params, s, e, **cost):
        r = inner(params, s, e, **cost)
        em = r.get("metrics") or {}
        return {"trades": r["trades"], "metrics": normalize_metrics(r["trades"], capital, em,
                                                                    em.get("max_drawdown_pct"))}

    name = spec.get("name") or "inline"
    family = (spec.get("meta") or {}).get("family") or f"builder:{name}"
    spec_sha = ra._sha(ra._strategy_spec(spec))
    twin = jesse_twin_of(spec.get("name"))
    caps, lim = builder_capabilities(spec, twin)
    scenarios = validation.default_cost_scenarios(float(commission_per_lot or 0) or 7.0)
    return StrategyAdapter(
        kind="builder", name=name, ref=ref, symbol=symbol, timeframe=timeframe, start=start, end=end,
        capital=capital, run=run, defaults=ra._defaults(spec), param_specs=builder_param_specs(spec), family=family,
        hyp_key=f"builder:{spec_sha}", code_sha256=_files_sha(ENGINE_FILES),
        engine_nl="Builder-regels op de Dukascopy Bid/Ask-engine (kopen op Ask, verkopen op Bid, echte spread).",
        capabilities=caps, limitations_nl=lim, supported_cost_scenarios=scenarios, unsupported_cost_scenarios=[],
        dataset_sha256=ra._dataset_sha(manifest, symbol, timeframe, start, end), spec=spec, spec_sha256=spec_sha,
        jesse_strategy=twin, check=check,
        extra={"commission_per_lot": commission_per_lot, "legacy_families": ["builder"]})


def _plugin_adapter(ref, symbol, timeframe, start, end, capital, *, check=None, **_):
    from .. import plugins
    from .. import research_api as ra
    plugin_id = ref.get("name") if isinstance(ref, dict) else ref
    try:
        module = plugins.load(str(plugin_id))
    except (KeyError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    meta = module.PLUGIN
    symbol = (symbol or (meta.get("symbols") or ["XAUUSD"])[0]).upper()
    if symbol not in [x.upper() for x in meta.get("symbols", [symbol])]:
        raise ValueError(f"Plugin ondersteunt alleen {meta.get('symbols')}")
    tf = meta.get("base_timeframe", "M1")
    bid, ask, manifest = ra._frames_for(symbol, tf, start, end, warmup_days=int(meta.get("warmup_days", 14)) + 1)
    inner = ra._plugin_fn(plugin_id, bid, ask, capital, progress=lambda f, m: check() if check else None)
    stress = plugin_stress_support(module)

    def run(params, s, e, **cost):
        r = inner(params, s, e, **cost)
        return {"trades": r["trades"], "metrics": normalize_metrics(r["trades"], capital, r.get("summary"))}

    specs = plugin_param_specs(module)
    defaults = plugins.defaults(module)
    scenarios, unsupported = {"baseline": {}}, []
    reason = "De plugin-engine verwerkt deze stress niet (niet gevonden in plugin.py)."
    if "spread_multiplier" in stress:
        scenarios.update({"spread_+25%": {"spread_multiplier": 1.25}, "spread_+50%": {"spread_multiplier": 1.5}})
    else:
        unsupported += [{"name": n, "reason_nl": reason} for n in ("spread_+25%", "spread_+50%")]
    if "commission_per_lot" in stress:
        base = max(float(defaults.get("commission_per_lot_side") or 0), 3.5)
        scenarios["commission_x2"] = {"commission_per_lot": 2 * base}
    else:
        unsupported.append({"name": "commission_x2", "reason_nl": reason})
    for n, kw in (("slippage_+1pt", "slippage_points"), ("entry_delay_1bar", "entry_delay_bars"),
                  ("exit_delay_1bar", "exit_delay_bars")):
        if kw in stress:
            scenarios[n] = {kw: 1 if "delay" in kw else 1.0}
        else:
            unsupported.append({"name": n, "reason_nl": reason})
    caps, lim = plugin_capabilities(module)
    return StrategyAdapter(
        kind="plugin", name=str(plugin_id), ref=ref, symbol=symbol, timeframe=tf, start=start, end=end,
        capital=capital, run=run, defaults=defaults, param_specs=specs, family=f"plugin:{plugin_id}",
        hyp_key=f"plugin:{plugin_id}", code_sha256=module.PLUGIN_SHA256,
        engine_nl=f"Python-plugin {meta.get('name', plugin_id)} met eigen engine op Dukascopy Bid/Ask {tf}.",
        capabilities=caps, limitations_nl=lim, supported_cost_scenarios=scenarios,
        unsupported_cost_scenarios=unsupported,
        dataset_sha256=ra._dataset_sha(manifest, symbol, tf, start, end), check=check,
        extra={"research_history": meta.get("research_history") or {"external_trials": None, "external_periods": None,
               "note_nl": "Geen ontwikkelgeschiedenis opgegeven in PLUGIN['research_history']."}})


def _day_before(end: str) -> str:
    return (pd.Timestamp(end) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def _jesse_adapter(ref, symbol, timeframe, start, end, capital, *, check=None, is_cancelled=None, fee=0.0006,
                   account_type=None, runner=None, strategies=None, dataset_sha256=None, warmup_candles=210,
                   timeout=None, **_):
    from .. import server
    name = ref.get("name") if isinstance(ref, dict) else ref
    available = {s["name"]: s for s in (strategies if strategies is not None else server._strategies())}
    if name not in available:
        raise ValueError(f"Onbekende Jesse-strategie: {name}")
    definition = available[name]
    specs = jesse_param_specs(definition["parameters"])
    defaults = {n: s.get("default") for n, s in specs.items() if s.get("default") is not None}
    runner = runner or server.run_jesse_backtest
    symbol, timeframe = (symbol or "XAUUSD").upper(), (timeframe or "M15").upper()
    state = {"account_type": account_type or "spot", "auto": account_type is None, "switched": None,
             "runs": 0, "cache_hits": 0, "seconds": 0.0, "spot_results": 0, "rerun_done": False}
    cache: dict = {}

    def run(params, s, e, fee_multiplier: float = 1.0, **unsupported):
        if unsupported:
            raise ValueError(f"Jesse ondersteunt deze kostenstress niet: {sorted(unsupported)}")
        last = _day_before(e)
        if pd.Timestamp(last) < pd.Timestamp(s):
            return {"trades": [], "metrics": normalize_metrics([], capital)}
        key = (_sha(params), s, e, float(fee_multiplier))
        hit = cache.get(key + (state["account_type"],))
        if hit is not None:
            state["cache_hits"] += 1
            return hit
        t0 = pd.Timestamp.now()
        while True:
            try:
                out = runner(name, symbol, timeframe, s, last, params, fee=fee * float(fee_multiplier),
                             account_type=state["account_type"], starting_balance=capital,
                             is_cancelled=is_cancelled, warmup_candles=warmup_candles, timeout=timeout)
                break
            except server.RunCancelled:
                if check:
                    check()
                raise RuntimeError("Jesse-backtest is geannuleerd via Annuleren in de tester.")
            except server.JesseRunError as exc:
                if exc.code == "spot_short" and state["auto"] and state["account_type"] == "spot":
                    state["account_type"] = "futures"
                    state["switched"] = (f"Rekenmodel automatisch van spot naar futures gezet bij {s}–{last} "
                                         f"(params {params}): de strategie opent shortposities.")
                    continue
                raise RuntimeError(f"Jesse-backtest mislukt ({s}–{last}): {exc.message_nl}") from exc
        state["runs"] += 1
        state["spot_results"] += state["account_type"] == "spot"
        state["seconds"] += (pd.Timestamp.now() - t0).total_seconds()
        result = out["result"]
        trades = normalize_jesse_trades(result.get("trades"))
        em = result.get("metrics") or {}
        dd = em.get("max_drawdown")
        r = {"trades": trades, "metrics": normalize_metrics(trades, capital, em, dd if isinstance(dd, (int, float)) else None),
             "account_type": state["account_type"]}
        cache[key + (state["account_type"],)] = r
        return r

    if dataset_sha256 is None:
        try:
            from ..data_manager import dataset_digest
            begin = (pd.Timestamp(start) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
            _, manifest = server.DATA.candles(symbol, timeframe, begin, end)
            dataset_sha256 = dataset_digest(manifest, timeframe, start, _day_before(end))
        except Exception as exc:  # data problems surface on the first real backtest with a Dutch message
            dataset_sha256 = f"onbekend: {exc}"
    caps, lim = jesse_capabilities(name, definition["parameters"], definition.get("sha256"))
    scenarios = {"baseline": {}, "fee ×1,5": {"fee_multiplier": 1.5}, "fee ×2": {"fee_multiplier": 2.0}}
    unsupported = [{"name": n, "reason_nl": JESSE_SPREAD_REASON_NL}
                   for n in ("spread_+25%", "spread_+50%", "slippage_+1pt", "entry_delay_1bar", "exit_delay_1bar")]
    adapter = StrategyAdapter(
        kind="jesse", name=name, ref=ref, symbol=symbol, timeframe=timeframe, start=start, end=end, capital=capital,
        run=run, defaults=defaults, param_specs=specs, family=f"jesse:{name}", hyp_key=f"jesse:{name}",
        code_sha256=definition.get("sha256"),
        engine_nl=(f"Echte Jesse-backtest via de Docker-runner op Dukascopy Bid-candles (route {timeframe}, "
                   f"opgebouwd uit M1), fee {fee:g} per fill."),
        capabilities=caps, limitations_nl=lim, supported_cost_scenarios=scenarios,
        unsupported_cost_scenarios=unsupported, dataset_sha256=dataset_sha256, jesse_strategy=name, check=check,
        extra={"fee": fee, "warmup_candles": warmup_candles})
    adapter.jesse_state = state

    def rerun_reason():
        if state["switched"] and state["spot_results"] and not state["rerun_done"]:
            state["rerun_done"] = True
            return (f"{state['spot_results']} backtest(s) liepen nog op spot voordat de strategie shortposities "
                    "opende; alle stappen worden opnieuw uitgevoerd met futures zodat elke vergelijking op hetzelfde "
                    "rekenmodel berust.")
        return None
    adapter.rerun_reason = rerun_reason
    adapter.extra_state = lambda: {"account_type": state["account_type"], "account_type_note": state["switched"],
                                   "jesse_backtests": state["runs"], "cache_hits": state["cache_hits"],
                                   "jesse_seconds": round(state["seconds"], 1)}
    return adapter


def make_adapter(kind: str, ref, symbol: str | None, timeframe: str | None, start: str, end: str,
                 capital: float = 10000, **options) -> StrategyAdapter:
    """``kind``: builder | jesse | plugin. ``ref``: name string, or {"name"}/{"spec"}/{"template"} for builder.
    Options: commission_per_lot (builder), fee/account_type/is_cancelled/runner (jesse), check (all)."""
    factory = {"builder": _builder_adapter, "jesse": _jesse_adapter, "plugin": _plugin_adapter}.get(kind)
    if factory is None:
        raise ValueError(f"Onbekend strategietype: {kind} (kies builder, jesse of plugin)")
    return factory(ref, symbol, timeframe, start, end, float(capital), **options)


# ------------------------------------------------------------------ optimizer trials on disk
def optimizer_trial_ids(strategy: str, symbol: str, runs_root: Path | None = None,
                        definitions: list[dict] | None = None) -> set[str]:
    """Hypothesis ids of every distinct parameter set tried by saved Jesse optimize runs (strategy+symbol)."""
    if runs_root is None or definitions is None:
        from .. import server
        runs_root = runs_root or server.RUNS_ROOT
        if definitions is None:
            definitions = next((s["parameters"] for s in server._strategies() if s["name"] == strategy), [])
    defaults = {d["name"]: d.get("default") for d in definitions if "default" in d}
    ids: set[str] = set()
    for manifest_path in Path(runs_root).glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (manifest.get("action") != "optimize" or manifest.get("strategy") != strategy
                    or str(manifest.get("symbol", "")).upper() != symbol.upper()):
                continue
            result = json.loads((manifest_path.parent / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for trial in result.get("all_trials") or []:
            params = {**defaults, **(manifest.get("parameters") or {}), **(trial.get("params") or {})}
            ids.add(hypothesis_id(f"jesse:{strategy}", params))
    return ids


# ------------------------------------------------------------------ instrument binding (static scan)
_PRICE_NAMES = frozenset({"price", "close", "high", "low", "open", "entry_price", "avg_entry_price",
                          "current_candle", "candles", "ask", "bid"})
_CCY = frozenset({"EUR", "USD", "GBP", "JPY", "CAD", "AUD", "CHF", "NZD", "XAU", "XAG", "BTC", "ETH", "USDT"})
_PRICE_PARAM_RE = re.compile(r"(stop|target|distance|offset|range|points?|pips?|tick|spread|slippage)", re.I)
_SCALE_FREE_RE = re.compile(r"(pct|percent|ratio|mult|factor|atr|period|bars|interval|expiry|count|size)", re.I)
# a distance parameter this large/small relative to the market's median price is not a plausible intraday design
PRICE_SCALE_MAX_PCT, PRICE_SCALE_MIN_PCT = 2.0, 0.002


def _known_symbols() -> set[str]:
    from . import instruments
    return set(instruments.SYMBOLS) | set(instruments.ALIASES) | {"XAGUSD"}


def _symbol_literal(value: str) -> bool:
    v = value.strip().upper()
    if v in _known_symbols():
        return True
    if re.fullmatch(r"[A-Z0-9]{2,6}-[A-Z0-9]{2,6}", v):
        return v.split("-")[0] in _CCY or v.split("-")[1] in _CCY
    return len(v) == 6 and v[:3] in _CCY and v[3:] in _CCY


def _price_like(node) -> bool:
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "len":
            continue
        if (isinstance(n, ast.Attribute) and n.attr in _PRICE_NAMES) or (isinstance(n, ast.Name) and n.id in _PRICE_NAMES):
            return True
        stack.extend(ast.iter_child_nodes(n))
    return False


def _num_const(node):
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        node = node.operand
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return abs(float(node.value))
    return None


def scan_source(text: str, filename: str = "<src>") -> dict:
    """Static scan of Python source for instrument-bound constructs: symbol string literals and numeric price
    constants combined (+/-/compare) with price-like operands. Scale-free ratios (``*``/``/``) are ignored."""
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return {"symbols": [], "price_constants": [], "error": f"{filename}: {exc}"}
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body
                  and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    symbols, consts = [], []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings \
                and _symbol_literal(n.value):
            symbols.append({"file": filename, "line": n.lineno, "value": n.value.strip().upper()})
        pairs = []
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub)):
            pairs = [(n.left, n.right), (n.right, n.left)]
        elif isinstance(n, ast.Compare):
            seq = [n.left, *n.comparators]
            pairs = [(a, b) for a, b in zip(seq, seq[1:])] + [(b, a) for a, b in zip(seq, seq[1:])]
        for const_side, other in pairs:
            v = _num_const(const_side)
            if v is not None and v not in (0.0, 1.0) and _price_like(other):
                consts.append({"file": filename, "line": n.lineno, "value": v})
    return {"symbols": symbols, "price_constants": consts}


def _scan_dir(folder: Path) -> dict:
    out = {"symbols": [], "price_constants": []}
    for path in sorted(Path(folder).rglob("*.py")):
        res = scan_source(path.read_text(encoding="utf-8", errors="replace"), path.name)
        out["symbols"] += res["symbols"]
        out["price_constants"] += res["price_constants"]
    return out


def jesse_source_dir(name: str, root: Path | None = None) -> Path:
    if root is None:
        from .. import research_api as ra
        root = ra.STRATEGY_ROOT
    return Path(root) / name


def price_scale_params(definitions: list[dict] | None, params: dict | None = None) -> list[dict]:
    """Hyperparameters that look like absolute price distances (not scale-free): they keep their value on every
    market, so the effective distance differs wildly between e.g. gold and EURUSD."""
    out = []
    for d in definitions or []:
        n = d.get("name", "")
        if d.get("type") in ("int", "float", int, float) and _PRICE_PARAM_RE.search(n) and not _SCALE_FREE_RE.search(n):
            value = (params or {}).get(n, d.get("default"))
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out.append({"name": n, "value": float(value)})
    return out


def instrument_binding(kind: str, ref, *, definitions: list[dict] | None = None, source_dir: Path | None = None,
                       module=None, origin_symbol: str | None = None) -> dict:
    """Is the strategy tied to one instrument? ``restricted_to`` = allowed canonical symbols (None = unrestricted).

    * plugin: ``PLUGIN['symbols']`` is an explicit restriction; without it the plugin source is scanned.
    * jesse: static scan of the strategy folder for hardcoded symbol strings and price constants. Symbol
      literals restrict to those symbols; bare price constants restrict to ``origin_symbol`` (all markets when
      the origin is unknown). Nothing is translated automatically: another market needs a new version/hypothesis."""
    from . import instruments
    name = ref.get("name") if isinstance(ref, dict) else ref
    canon = instruments.canonical
    if kind == "plugin":
        if module is None:
            from .. import plugins
            module = plugins.load(str(name))
        declared = [canon(s) for s in module.PLUGIN.get("symbols") or []]
        if declared:
            return {"kind": kind, "source": "PLUGIN['symbols']", "restricted_to": declared, "findings": [],
                    "n_findings": 0, "price_param_warnings": []}
        scan = _scan_dir(Path(module.__file__).parent)
    else:
        scan = _scan_dir(source_dir or jesse_source_dir(str(name)))
    lits = sorted({canon(s["value"]) for s in scan["symbols"] if s["value"].upper() in _known_symbols()})
    findings = [{"type": "SYMBOL_LITERAL", **s} for s in scan["symbols"]] + \
               [{"type": "PRICE_CONSTANT", **c} for c in scan["price_constants"]]
    if lits:
        restricted, src = lits, "hardcoded symbol string(s) in source"
    elif scan["symbols"]:  # symbol-shaped strings that are not Lab markets (e.g. BTC-USDT): bound to an unknown market
        restricted, src = [], "hardcoded non-Lab symbol string(s) in source"
    elif scan["price_constants"]:
        restricted, src = ([canon(origin_symbol)] if origin_symbol else []), "hardcoded price constants in source"
    else:
        restricted, src = None, "no instrument-bound construct found"
    return {"kind": kind, "source": src, "restricted_to": restricted, "findings": findings[:20],
            "n_findings": len(findings), "price_param_warnings": price_scale_params(definitions)}


def market_block_reason(binding: dict, symbol: str) -> str | None:
    """None if the market may run, else the INSTRUMENT_BOUND reason."""
    from . import instruments
    allowed = binding.get("restricted_to")
    if allowed is None or instruments.canonical(symbol) in allowed:
        return None
    where = ", ".join(allowed) if allowed else "an unknown origin instrument"
    return (f"INSTRUMENT_BOUND: {binding['source']} -> only valid for {where}; {instruments.canonical(symbol)} would "
            "need a new strategy version / hypothesis (no automatic translation)")


def strategy_identity(kind: str, ref, *, strategies: list[dict] | None = None, source_dir: Path | None = None) -> dict:
    """{name, sha256} of a Jesse strategy source / plugin folder; ValueError when unknown."""
    name = str(ref.get("name") if isinstance(ref, dict) else ref)
    if kind == "plugin":
        from .. import plugins
        try:
            return {"name": name, "sha256": plugins.load(name).PLUGIN_SHA256}
        except (KeyError, ValueError) as exc:
            raise ValueError(str(exc)) from exc
    if kind != "jesse":
        raise ValueError(f"strategy_identity: alleen jesse of plugin, niet {kind}")
    if strategies is not None:
        hit = next((s for s in strategies if s["name"] == name), None)
        if hit is None:
            raise ValueError(f"Onbekende Jesse-strategie: {name}")
        return {"name": name, "sha256": hit.get("sha256")}
    path = (source_dir or jesse_source_dir(name)) / "__init__.py"
    if not path.is_file():
        raise ValueError(f"Onbekende Jesse-strategie: {name}")
    return {"name": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
