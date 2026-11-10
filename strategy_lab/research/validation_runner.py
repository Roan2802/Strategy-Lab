"""Generic validation pipeline for any :class:`adapters.StrategyAdapter` (Builder, Jesse, plugin).

Steps: base development backtest, cost stress, walk-forward (rolling/expanding, stitched OOS equity), parameter
stability, PBO/CSCV, Monte Carlo + drop-best + yearly on real trades, PSR/DSR/MinTRL/haircut with honest trial
accounting, a one-time holdout, scorecard and a Dutch report. Every step can be switched off; a skipped or
unsupported step is reported NOT_TESTED with its reason, never filled in.
"""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict

import numpy as np
import pandas as pd

from . import validation
from .jobs import JobCancelled

KIND_DEFAULTS = {
    "builder": {"walk_forward": {"max_combos": 12}, "pbo": {"max_combos": 12}},
    "jesse": {"walk_forward": {"max_combos": 4}, "pbo": {"max_combos": 4}},
    "plugin": {"walk_forward": {"max_combos": 4}, "pbo": {"max_combos": 4}},
}
CHECK_STEP = {"walk_forward_efficiency": "walk_forward", "oos_profitability": "walk_forward",
              "parameter_stability": "parameter_stability", "pbo": "pbo", "cost_stress": "cost_stress",
              "psr": "statistics", "dsr": "statistics", "drop_best_trades": "monte_carlo",
              "yearly_consistency": "monte_carlo"}
GAP_NL = {
    "sample_size": "Meer trades nodig voor betekenisvolle statistiek",
    "net_after_costs": "Netto resultaat na kosten moet positief zijn",
    "profit_factor": "Profit factor moet de drempel halen",
    "max_drawdown": "Maximale drawdown moet binnen de limiet blijven",
    "psr": "PSR (kans dat de echte Sharpe > 0) moet de drempel halen",
    "dsr": "DSR (Sharpe gecorrigeerd voor alle geprobeerde configuraties) moet de drempel halen",
    "pbo": "Kans op backtest-overfitting (PBO) moet laag genoeg zijn",
    "walk_forward_efficiency": "Walk-forward efficiency moet aantonen dat out-of-sample het tempo bijhoudt",
    "oos_profitability": "Out-of-sample resultaat moet positief en consistent over vensters zijn",
    "cost_stress": "Resultaat moet positief blijven bij +50% spread",
    "parameter_stability": "Naburige parameterwaarden moeten ook winstgevend zijn",
    "drop_best_trades": "Resultaat moet positief blijven zonder de beste trades",
    "yearly_consistency": "Voldoende jaren moeten winstgevend zijn",
}


def _cfg(cfg: dict, kind: str, key: str) -> dict:
    return {**KIND_DEFAULTS.get(kind, {}).get(key, {}), **(cfg.get(key) or {})}


def _fmt_day(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def wf_windows(start: str, end: str, train_days: int, test_days: int, step_days: int, mode: str) -> list[tuple]:
    """The windows validation.walk_forward will visit: (train_start, train_end, test_end)."""
    s0, e0, cur, out = pd.Timestamp(start), pd.Timestamp(end), pd.Timestamp(start), []
    while True:
        tr_start = s0 if mode == "expanding" else cur
        tr_end = cur + pd.Timedelta(days=train_days)
        te_end = tr_end + pd.Timedelta(days=test_days)
        if te_end > e0:
            return out
        out.append((_fmt_day(tr_start), _fmt_day(tr_end), _fmt_day(te_end)))
        cur += pd.Timedelta(days=step_days)


def _per_obs_sharpe(trades, capital) -> float | None:
    d = validation.daily_returns_from_trades(trades, capital) if trades else pd.Series(dtype=float)
    if len(d) < 2 or d.std(ddof=1) == 0:
        return None
    return float(d.mean() / d.std(ddof=1))


def _clean(obj):
    return json.loads(json.dumps(obj, default=lambda o: None if o != o else str(o)))


class _Tracker:
    """Wraps adapter.fn: cancellation, per-backtest progress, distinct in-run configurations per stage."""

    def __init__(self, ctx, adapter):
        self.ctx, self.adapter = ctx, adapter
        self.configs: dict[str, dict] = {}
        self.by_stage: dict[str, set] = defaultdict(set)
        self.stage, self.lo, self.hi, self.expected, self.done = "", 0.0, 1.0, 1, 0
        self.labeler = None
        self.calls = 0

    def begin(self, stage: str, lo: float, hi: float, expected: int, label=None):
        self.stage, self.lo, self.hi, self.expected, self.done = stage, lo, hi, max(1, expected), 0
        self.labeler = label if callable(label) else (lambda *_a, **_k: label or stage)

    def fn(self, params, start, end, **cost):
        self.ctx.check()
        h = self.adapter.hypothesis_id(params)
        self.configs.setdefault(h, self.adapter.merge(params))
        self.by_stage[self.stage].add(h)
        message = self.labeler(params, start, end, **cost)
        frac = self.lo + (self.hi - self.lo) * min(1.0, self.done / self.expected)
        self.ctx.progress(frac, message)
        self.done += 1
        self.calls += 1
        return self.adapter.fn(params, start, end, **cost)


def trial_accounting(ledger, adapter, configs: dict, optimizer_ids: set | None = None) -> dict:
    """Conservative N for DSR/haircut. Rows already in the ledger are counted once (distinct hypothesis ids, plus
    the configs_evaluated of earlier VALIDATION runs); optimizer trials on disk and in-run configurations count
    only when their hypothesis id is not already in the ledger."""
    sym, tf, fam = adapter.symbol, adapter.timeframe, adapter.family
    ledger_prior = ledger.n_trials(symbol=sym, timeframe=tf, include_validation_configs=True)
    family_prior = ledger.n_trials(family=fam, include_validation_configs=True)
    family_here = ledger.n_trials(family=fam, symbol=sym, timeframe=tf, include_validation_configs=True)
    known = ledger.hypothesis_ids(symbol=sym, timeframe=tf) | ledger.hypothesis_ids(family=fam)
    if adapter.jesse_strategy:
        known |= ledger.hypothesis_ids(family=f"jesse:{adapter.jesse_strategy}")
    opt_all = set(optimizer_ids or ())
    opt_new = opt_all - known
    in_run_new = [h for h in configs if h not in known and h not in opt_new]
    family_elsewhere = max(0, family_prior - family_here)
    total = max(1, ledger_prior + family_elsewhere + len(opt_new) + len(in_run_new))
    return {"ledger_prior": ledger_prior, "family_prior": family_prior, "family_prior_other_markets": family_elsewhere,
            "optimizer_trials": len(opt_all), "optimizer_trials_not_in_ledger": len(opt_new),
            "in_run_configs": len(configs), "in_run_configs_new": len(in_run_new), "total": total,
            "definition_nl": ("N = onderscheiden hypotheses in het logboek voor deze markt+timeframe (incl. eerder "
                              "geëvalueerde validatieconfiguraties) + dezelfde familie op andere markten + "
                              "optimizer-trials uit opgeslagen runs die nog niet in het logboek staan + nieuwe "
                              "configuraties uit deze validatie. Herhaalde validaties tellen opnieuw mee "
                              "(conservatief).")}


def forward_gaps(sc: dict, adapter, holdout: dict | None) -> list[str]:
    gaps = []
    for c in sc["checks"]:
        if c["status"] == "PASS":
            continue
        label = {"FAIL": "gefaald", "INSUFFICIENT_EVIDENCE": "onvoldoende bewijs",
                 "NOT_TESTED": "niet getest"}.get(c["status"], c["status"])
        gaps.append(f"{GAP_NL.get(c['name'], c['name'])} ({c['name']}: {label}). {c['explanation_nl']}")
    if sc["verdict"] != "FORWARD_ELIGIBLE":
        gaps.append(f"Verdict is {sc['verdict']}; alleen FORWARD_ELIGIBLE mag worden bevroren voor een forward test.")
    if holdout and not holdout.get("untouched"):
        gaps.append("De holdoutperiode was al eerder ingezien; een nieuwe, ongeziene periode (of de forward test zelf) "
                    "is nodig als onafhankelijke toets.")
    if not adapter.capabilities.get("forward"):
        reason = next((x for x in adapter.limitations_nl if "orward" in x), "geen forward-ondersteuning")
        gaps.append(f"Geen bevroren forward-registratie mogelijk voor dit type: {reason}")
    gaps.append("Een prospectieve forward test is nog niet gestart: bevries parameters, code-sha en datawatermerk en "
                "volg de strategie op nieuwe, na de bevriezing binnengekomen data.")
    return gaps


def run_validation(ctx, adapter, cfg: dict, *, ledger=None, lifecycle=None, record: bool = True,
                   optimizer_ids: set | None = None) -> dict:
    t_start = time.monotonic()
    kind = adapter.kind
    start, end = cfg.get("start") or adapter.start, cfg.get("end") or adapter.end
    capital = float(cfg.get("capital") or adapter.capital)
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    holdout_pct = float(cfg.get("holdout_percent", 20))
    holdout_days = int((e - s).days * holdout_pct / 100)
    dev_end = (e - pd.Timedelta(days=holdout_days)).strftime("%Y-%m-%d") if holdout_days else end
    base_params = adapter.merge(cfg.get("params") or {})
    wf_cfg = {"enabled": True, "mode": "rolling", "train_days": 120, "test_days": 30, "step_days": 30,
              "objective": "sharpe", "min_trades": 10, "seed": 0, **_cfg(cfg, kind, "walk_forward")}
    st_cfg = {"enabled": True, "rel_step": 0.1, **_cfg(cfg, kind, "stability")}
    pbo_cfg = {"enabled": True, "n_splits": 8, "seed": 0, **_cfg(cfg, kind, "pbo")}
    mc_cfg = {"enabled": True, "n": 1000, "method": "block_bootstrap", "seed": 0, **_cfg(cfg, kind, "monte_carlo")}
    cs_cfg = {"enabled": True, **_cfg(cfg, kind, "cost_stress")}
    tr = _Tracker(ctx, adapter)
    ev: dict = {}
    steps: dict = {}

    def skip(name, status, reason):
        steps[name] = {"status": status, "reason_nl": reason, "seconds": 0.0, "backtests": 0}

    def step(name, lo, hi, expected, label, body):
        t0, calls0 = time.monotonic(), tr.calls
        tr.begin(name, lo, hi, expected, label)
        try:
            body()
            steps[name] = {"status": "DONE", "seconds": round(time.monotonic() - t0, 2), "backtests": tr.calls - calls0}
        except JobCancelled:
            raise
        except Exception as exc:  # a failing optional step becomes NOT_TESTED evidence, not a crash
            ev.pop(name, None)
            steps[name] = {"status": "ERROR", "reason_nl": f"Stap mislukt: {exc}",
                           "seconds": round(time.monotonic() - t0, 2), "backtests": tr.calls - calls0}

    # 1. base
    ctx.check()
    tr.begin("base", 0.02, 0.08, 1, "Basisbacktest op de ontwikkelperiode")
    t0 = time.monotonic()
    base = tr.fn(base_params, start, dev_end)
    steps["base"] = {"status": "DONE", "seconds": round(time.monotonic() - t0, 2), "backtests": 1}
    trades, m = base["trades"], base["metrics"]
    ev.update(n_trades=m.get("n_trades", len(trades)), net_profit=m.get("net_profit"),
              profit_factor=m.get("profit_factor"), max_drawdown_pct=m.get("max_drawdown_pct"))

    # 2. cost stress
    scenarios = adapter.supported_cost_scenarios or {"baseline": {}}
    unsupported = adapter.unsupported_cost_scenarios
    if not cs_cfg["enabled"]:
        skip("cost_stress", "NOT_TESTED", "Kostenstress uitgeschakeld in de configuratie.")
    elif len(scenarios) <= 1:
        skip("cost_stress", "NOT_SUPPORTED", "; ".join(sorted({u["reason_nl"] for u in unsupported}))
             or "Deze engine ondersteunt geen kostenstress.")
    else:
        names = {json.dumps(kw, sort_keys=True): n for n, kw in scenarios.items()}
        order = list(scenarios)

        def label_cost(_p, _s, _e, **kw):
            n = names.get(json.dumps(kw, sort_keys=True), "baseline")
            return f"Kostenstress: scenario {n} ({order.index(n) + 1 if n in order else 1}/{len(order)})"

        def do_cost():
            cs = validation.cost_stress(tr.fn, base_params, start, dev_end, scenarios=scenarios, capital=capital)
            cs["unsupported"] = unsupported
            ev["cost_stress"] = cs
        step("cost_stress", 0.08, 0.16, len(scenarios), label_cost, do_cost)
    if unsupported and "cost_stress" in steps:
        steps["cost_stress"]["unsupported"] = unsupported

    # 3. walk-forward
    wf_cands = adapter.candidates(int(wf_cfg["max_combos"]), int(wf_cfg["seed"]), base_params)
    windows = wf_windows(start, dev_end, int(wf_cfg["train_days"]), int(wf_cfg["test_days"]),
                         int(wf_cfg["step_days"]), wf_cfg["mode"])
    if not wf_cfg["enabled"]:
        skip("walk_forward", "NOT_TESTED", "Walk-forward uitgeschakeld in de configuratie.")
    elif not adapter.capabilities.get("walk_forward") or len(wf_cands) < 2:
        skip("walk_forward", "NOT_SUPPORTED", "Geen optimaliseerbare parameters met bereik (min/max of opties); "
                                              "walk-forward heeft niets te kiezen.")
    elif not windows:
        skip("walk_forward", "NOT_TESTED", f"Ontwikkelperiode {start}–{dev_end} is te kort voor train "
                                           f"{wf_cfg['train_days']} + test {wf_cfg['test_days']} dagen.")
    else:
        pos = {"w": 0, "k": 0}
        train_map = {(w[0], w[1]): i for i, w in enumerate(windows)}
        test_map = {(w[1], w[2]): i for i, w in enumerate(windows)}

        def label_wf(_p, s_, e_, **_kw):
            if (s_, e_) in train_map and train_map[(s_, e_)] >= pos["w"]:
                if train_map[(s_, e_)] != pos["w"]:
                    pos["w"], pos["k"] = train_map[(s_, e_)], 0
                pos["k"] += 1
                return (f"Walk-forward venster {pos['w'] + 1}/{len(windows)}, combinatie "
                        f"{pos['k']}/{len(wf_cands)}")
            if (s_, e_) in test_map:
                return f"Walk-forward venster {test_map[(s_, e_)] + 1}/{len(windows)}, test op ongeziene data"
            return "Walk-forward"

        def do_wf():
            wf = validation.walk_forward(
                tr.fn, wf_cands, start, dev_end, int(wf_cfg["train_days"]), int(wf_cfg["test_days"]),
                int(wf_cfg["step_days"]), mode=wf_cfg["mode"], objective=wf_cfg["objective"],
                min_trades=int(wf_cfg["min_trades"]), max_combos=len(wf_cands), seed=int(wf_cfg["seed"]),
                capital=capital)
            ev["walk_forward"] = wf
        step("walk_forward", 0.16, 0.55, len(windows) * (len(wf_cands) + 1), label_wf, do_wf)

    # 4. parameter stability
    st_specs = adapter.stability_specs(base_params)
    if not st_cfg["enabled"]:
        skip("parameter_stability", "NOT_TESTED", "Parameterstabiliteit uitgeschakeld in de configuratie.")
    elif not st_specs:
        skip("parameter_stability", "NOT_SUPPORTED", "Geen numerieke strategieparameters met min/max om te "
                                                     "verschuiven (vaste of categorische parameters tellen niet).")
    else:
        pos_st = {"k": 0}

        def label_st(p, *_a, **_k):
            pos_st["k"] += 1
            diff = {k: v for k, v in adapter.merge(p).items() if base_params.get(k) != v}
            return f"Parameterstabiliteit {pos_st['k']}/{2 * len(st_specs) + 1}: {diff or 'basis'}"

        def do_st():
            ev["parameter_stability"] = validation.parameter_stability(
                tr.fn, base_params, st_specs, start, dev_end, rel_step=float(st_cfg["rel_step"]), capital=capital)
        step("parameter_stability", 0.55, 0.68, 2 * len(st_specs) + 1, label_st, do_st)

    # 5. PBO / CSCV
    pbo_cands = adapter.candidates(int(pbo_cfg["max_combos"]), int(pbo_cfg["seed"]), base_params)
    sharpe_obs: list[float] = []
    if not pbo_cfg["enabled"]:
        skip("pbo", "NOT_TESTED", "PBO/CSCV uitgeschakeld in de configuratie.")
    elif len(pbo_cands) < 2:
        skip("pbo", "NOT_SUPPORTED", "Minder dan 2 parametercombinaties: PBO vergelijkt geselecteerde met "
                                     "alternatieve configuraties en is hier niet berekenbaar.")
    else:
        pos_p = {"k": 0}

        def label_pbo(*_a, **_k):
            pos_p["k"] += 1
            return f"PBO/CSCV combinatie {pos_p['k']}/{len(pbo_cands)}"

        def do_pbo():
            n_splits = int(pbo_cfg["n_splits"])
            blocks = pd.date_range(s, pd.Timestamp(dev_end), periods=n_splits + 1, tz="UTC")
            matrix = []
            for combo in pbo_cands:
                r = tr.fn(combo, start, dev_end)
                so = _per_obs_sharpe(r["trades"], capital)
                if so is not None:
                    sharpe_obs.append(so)
                pnl = (pd.Series([t["pnl"] for t in r["trades"]],
                                 index=pd.to_datetime([t["exit_time"] for t in r["trades"]], utc=True))
                       if r["trades"] else pd.Series(dtype=float))
                matrix.append([float(pnl[(pnl.index >= a) & (pnl.index < b)].sum()) if len(pnl) else 0.0
                               for a, b in zip(blocks[:-1], blocks[1:])])
            res = validation.pbo_cscv(np.array(matrix).T, n_splits=n_splits, metric="mean")
            res["n_candidates"] = len(pbo_cands)
            ev["pbo_result"] = res
            if res.get("pbo") is not None and res.get("status") != validation.INSUFFICIENT:
                ev["pbo"] = res["pbo"]
        step("pbo", 0.68, 0.8, len(pbo_cands), label_pbo, do_pbo)

    # 6. Monte Carlo / drop-best / yearly on the real base trades
    if not mc_cfg["enabled"]:
        skip("monte_carlo", "NOT_TESTED", "Monte Carlo uitgeschakeld in de configuratie.")
    elif not trades:
        skip("monte_carlo", "NOT_TESTED", "Geen trades in de ontwikkelperiode: niets om te herbemonsteren.")
    else:
        def do_mc():
            ctx.progress(0.81, "Monte Carlo, drop-best en jaaroverzicht op de echte trades")
            ev["monte_carlo"] = validation.monte_carlo(trades, capital, n=int(mc_cfg["n"]),
                                                       method=mc_cfg["method"], seed=int(mc_cfg["seed"]))
            ev["drop_best"] = validation.drop_best(trades)
            ev["yearly"] = validation.yearly_breakdown(trades)
        step("monte_carlo", 0.8, 0.84, 1, "Monte Carlo", do_mc)

    # 7. trial accounting + Sharpe corrections
    ctx.check()
    ctx.progress(0.85, "Geprobeerde configuraties tellen en Sharpe-correcties (PSR/DSR/MinTRL)")
    accounting = trial_accounting(ledger, adapter, tr.configs, optimizer_ids) if ledger is not None else {
        "ledger_prior": 0, "family_prior": 0, "family_prior_other_markets": 0, "optimizer_trials": len(optimizer_ids or ()),
        "optimizer_trials_not_in_ledger": len(optimizer_ids or ()), "in_run_configs": len(tr.configs),
        "in_run_configs_new": len(tr.configs),
        "total": max(1, len(optimizer_ids or ()) + len(tr.configs)), "definition_nl": "Zonder logboek."}
    accounting["stages"] = {k: len(v) for k, v in tr.by_stage.items()}
    # Strategies developed outside the lab (plugins) carry their own selection history; the ledger cannot see it.
    history = dict(adapter.extra.get("research_history") or {}) if adapter.kind == "plugin" else {"in_lab": True}
    if cfg.get("external_trials") is not None:
        history["external_trials"], history["declared_in_request"] = int(cfg["external_trials"]), True
    ext = history.get("external_trials")
    accounting["external_trials"] = int(ext) if isinstance(ext, (int, float)) else None
    if accounting["external_trials"]:
        accounting["total"] += accounting["external_trials"]
    ev["research_history"] = history
    n_trials = accounting["total"]
    ev["n_trials"] = n_trials
    # DSR needs the variance of Sharpe ratios across the trials of THIS selection. A handful of in-run candidates
    # understates it, so also take this strategy family's earlier trials from the ledger (annualised -> per day)
    # and use the larger. Other families are a different selection and would distort V (e.g. -16 Sharpe probes).
    candidates = []
    if len(sharpe_obs) >= 2:
        candidates.append((float(np.var(sharpe_obs, ddof=1)), "pbo_candidates_per_observation"))
    fam_vars = [v for f in {adapter.family, *adapter.extra.get("legacy_families", [])}
                if ledger is not None and (v := ledger.sharpe_variance(family=f))]
    if fam_vars:
        candidates.append((max(fam_vars) / 252.0, "ledger_family_sharpe_daily_annualized_div_252"))
    var, var_source = max(candidates) if candidates else (0.0, "none")
    daily = validation.daily_returns_from_trades(trades, capital) if trades else pd.Series(dtype=float)
    if len(daily) >= 2:
        ev["psr"] = validation.probabilistic_sharpe_ratio(daily.values)
        ev["dsr"] = validation.deflated_sharpe_ratio(daily.values, n_trials, var)
        ev["min_trl"] = validation.min_track_record_length(daily.values)
        sr = ev["psr"].get("sr") if isinstance(ev["psr"], dict) else None
        if sr is None and daily.std(ddof=1) > 0:
            sr = float(daily.mean() / daily.std(ddof=1))
        ev["haircut_sharpe"] = validation.haircut_sharpe(sr, n_trials, len(daily))
        steps["statistics"] = {"status": "DONE", "seconds": 0.0, "backtests": 0}
    else:
        skip("statistics", "NOT_TESTED", "Minder dan 2 handelsdagen met resultaat: PSR/DSR niet berekenbaar.")

    reason = adapter.rerun_reason()
    if reason:  # before any ledger write: repeat the whole pipeline once on consistent engine settings
        ctx.progress(0.86, "Opnieuw met futures-rekenmodel: " + reason)
        again = run_validation(ctx, adapter, cfg, ledger=ledger, lifecycle=lifecycle, record=record,
                               optimizer_ids=optimizer_ids)
        again["rerun_nl"] = reason
        again["report_md"] += f"\n## Herhaling\n- {reason}\n"
        again["timing"]["total_seconds"] = round(time.monotonic() - t_start, 1)
        again["timing"]["first_pass_backtests"] = tr.calls
        return again

    # 8. holdout, exactly once
    holdout, exposure = None, []
    if holdout_days:
        ctx.check()
        tr.begin("holdout", 0.88, 0.93, 1, "Onaangeroerde holdout (eenmalig)")
        if ledger is not None:
            exposure = ledger.data_exposure(adapter.symbol, dev_end, end)
            own = {adapter.family, *adapter.extra.get("legacy_families", [])}
        t0 = time.monotonic()
        h = tr.fn(base_params, dev_end, end)
        steps["holdout"] = {"status": "DONE", "seconds": round(time.monotonic() - t0, 2), "backtests": 1}
        holdout = {"start": dev_end, "end": end, "metrics": h["metrics"], "trades": len(h["trades"]),
                   "previous_exposures": len(exposure), "untouched": not exposure,
                   "same_strategy_exposures": sum(1 for x in exposure if x.get("family") in own) if ledger is not None else 0}
        if ledger is not None and record:
            ledger.record(kind="HOLDOUT", family=adapter.family, symbol=adapter.symbol, timeframe=adapter.timeframe,
                          start=dev_end, end=end, spec=adapter.spec, spec_sha256=adapter.spec_sha256,
                          code_sha256=adapter.code_sha256, params=base_params,
                          dataset_sha256=adapter.dataset_sha256, status="TESTED", metrics=_clean(h["metrics"]))
    else:
        skip("holdout", "NOT_TESTED", "Holdout-percentage is 0: geen ongeziene eindperiode gereserveerd.")
    ev["holdout"] = holdout
    wf = ev.get("walk_forward") or {}
    if holdout and not wf.get("oos_n_trades"):
        ev.update(oos_net_profit=holdout["metrics"].get("net_profit"), oos_n_trades=holdout["trades"])

    # 9. scorecard + report
    ctx.progress(0.95, "Scorecard en rapport")
    sc = validation.scorecard(ev, cfg.get("thresholds") or None)
    for c in sc["checks"]:
        info = steps.get(CHECK_STEP.get(c["name"], ""), {})
        if c["status"] == "NOT_TESTED" and info.get("reason_nl"):
            c["explanation_nl"] = f"Niet getest: {info['reason_nl']}"
        if c["name"] == "cost_stress" and c["status"] == "NOT_TESTED" and unsupported:
            c["explanation_nl"] = "Niet getest: " + "; ".join(sorted({u["reason_nl"] for u in unsupported})) + "."
    oos_equity, eq = [], capital
    for t in sorted(wf.get("oos_trades") or [], key=lambda x: str(x.get("exit_time"))):
        eq += float(t["pnl"])
        oos_equity.append({"time": str(pd.Timestamp(t["exit_time"]).isoformat()), "equity": eq, "pnl": float(t["pnl"])})
    gaps = forward_gaps(sc, adapter, holdout)
    extra_state = adapter.extra_state() if hasattr(adapter, "extra_state") else {}
    config = {
        "strategy": {"kind": kind, "name": adapter.name, "family": adapter.family},
        "symbol": adapter.symbol, "timeframe": adapter.timeframe, "capital": capital,
        "data_ranges": {"full": [start, end], "development": [start, dev_end],
                        "holdout": [dev_end, end] if holdout_days else None, "holdout_percent": holdout_pct,
                        "convention": "halfopen [start, end) kalenderdagen UTC"},
        "dataset_sha256": adapter.dataset_sha256, "code_sha256": adapter.code_sha256,
        "spec_sha256": adapter.spec_sha256, "engine_nl": adapter.engine_nl,
        "params": base_params, "defaults": adapter.defaults, "param_specs": adapter.param_specs,
        "fixed_params": adapter.fixed_params, "grid": adapter.grid(),
        "walk_forward": {**wf_cfg, "candidates": wf_cands, "windows": windows},
        "stability": {**st_cfg, "params": sorted(st_specs)}, "pbo": {**pbo_cfg, "candidates": pbo_cands},
        "monte_carlo": mc_cfg, "cost_stress": {**cs_cfg, "scenarios": scenarios, "unsupported": unsupported},
        "seeds": {"walk_forward": wf_cfg["seed"], "pbo": pbo_cfg["seed"], "monte_carlo": mc_cfg["seed"]},
        "thresholds": sc["thresholds"], "sr_variance": {"value": var, "source": var_source},
        **{k: v for k, v in adapter.extra.items() if k != "legacy_families"}, **extra_state,
    }
    report = validation.validation_report_nl(sc, ev)
    report += _report_extra(adapter, steps, accounting, holdout, gaps, extra_state)

    entry_id = None
    if ledger is not None and record:
        entry_id = ledger.record(
            kind="VALIDATION", family=adapter.family, symbol=adapter.symbol, timeframe=adapter.timeframe,
            start=start, end=end, spec=adapter.spec, spec_sha256=adapter.spec_sha256,
            code_sha256=adapter.code_sha256, params=base_params, dataset_sha256=adapter.dataset_sha256,
            cost_model={"scenarios": scenarios, "unsupported": unsupported},
            status="CANDIDATE" if sc["verdict"] in ("OOS_PASSED", "FORWARD_ELIGIBLE") else
                   "REJECTED" if sc["verdict"] == "REJECT" else "TESTED",
            rejection_reason="; ".join(c["name"] for c in sc["checks"] if c["status"] == "FAIL") or None,
            metrics=_clean({k: v for k, v in m.items() if k != "engine_metrics"}),
            oos_metrics=_clean((holdout or {}).get("metrics")), verdict=sc["verdict"],
            statistics=_clean({"n_trials": n_trials, "configs_evaluated": accounting["in_run_configs_new"],
                               "trial_accounting": accounting, "psr": ev.get("psr"), "dsr": ev.get("dsr"),
                               "pbo": ev.get("pbo"), "wfe": wf.get("wfe"), "engine": kind}))
        if lifecycle is not None:
            try:
                lifecycle.record_verdict(adapter.name, sc["verdict"], entry_id)
            except (KeyError, ValueError, AttributeError):
                pass

    slim_ev = _clean(ev)
    (slim_ev.get("walk_forward") or {}).pop("oos_trades", None)
    for w in (slim_ev.get("walk_forward") or {}).get("windows", []):
        w.pop("trades", None)
    ctx.progress(1.0, "Klaar")
    return {"scorecard": sc, "evidence": slim_ev, "report_md": report, "ledger_id": entry_id, "n_trials": n_trials,
            "trial_accounting": accounting, "config": _clean(config), "capabilities": adapter.capabilities,
            "limitations_nl": adapter.limitations_nl, "forward_gaps_nl": gaps, "oos_equity": oos_equity[-4000:],
            "steps": steps, "timing": {"total_seconds": round(time.monotonic() - t_start, 1),
                                       "backtests": tr.calls, "distinct_configs": len(tr.configs)}}


def _report_extra(adapter, steps, accounting, holdout, gaps, extra_state) -> str:
    L = ["", "## Engine en beperkingen", f"- {adapter.engine_nl}"]
    L += [f"- {x}" for x in adapter.limitations_nl]
    if extra_state.get("account_type_note"):
        L += [f"- {extra_state['account_type_note']}"]
    skipped = {n: st for n, st in steps.items() if st["status"] != "DONE"}
    L += ["", "## Niet uitgevoerde stappen"]
    L += [f"- {n}: {st['status']} - {st.get('reason_nl', '')}" for n, st in skipped.items()] or \
         ["- Alle stappen zijn uitgevoerd."]
    unsupported = (steps.get("cost_stress") or {}).get("unsupported") or []
    L += [f"- Kostenscenario {u['name']}: NIET ONDERSTEUND - {u['reason_nl']}" for u in unsupported]
    L += ["", "## Getelde configuraties (trial accounting)",
          f"- Logboek (deze markt+timeframe): {accounting['ledger_prior']}; zelfde familie elders: "
          f"{accounting.get('family_prior_other_markets', 0)}; optimizer-trials niet in logboek: "
          f"{accounting['optimizer_trials_not_in_ledger']} (van {accounting['optimizer_trials']}); nieuwe "
          f"configuraties in deze validatie: {accounting['in_run_configs_new']} (van {accounting['in_run_configs']}).",
          f"- Totaal N = {accounting['total']}; gebruikt voor DSR en de Sharpe-haircut.",
          f"- {accounting['definition_nl']}"]
    if holdout:
        L += ["", "## Holdout",
              f"- Periode {holdout['start']} – {holdout['end']}: netto {holdout['metrics'].get('net_profit')}, "
              f"{holdout['trades']} trades.",
              "- Deze periode was nog niet eerder ingezien." if holdout["untouched"] else
              f"- Let op: deze periode is al {holdout['previous_exposures']} keer eerder gebruikt in het "
              "researchlogboek en telt daardoor niet meer als volledig onaangeroerd."]
    L += ["", "## Nog nodig vóór een prospectieve forward test"] + [f"- {g}" for g in gaps]
    return "\n".join(L) + "\n"
