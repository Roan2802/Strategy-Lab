"""Shadow Research Portfolio: experimental prospective paper simulation, strictly apart from the validated forward portfolio.

Separation is structural, not cosmetic: shadow portfolios live in their own SQLite file (never in the validated forward
store), carry ``kind = SHADOW_RESEARCH`` and ``live_eligible = 0`` under CHECK constraints, and have their own API
namespace (``/api/shadow``). Processing reuses the audited forward engine (same quote-side fills, hash-chained append-only
ledger, exactly-once days) on candles that were published after the registration instant; the reference data store is only
read. See SHADOW_FORWARD_DESIGN.md.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
import dataclasses
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from . import forward_rules
from .daily_data import DailyPartitionStore, _utc
from .data_manager import DEFAULT_DAILY_STORE, DataError
from .forward_engine import ForwardEngine
from .forward_performance import _performance
from .forward_registry import ForwardRegistry
from .portfolio import fx_check, forward_correlations, portfolio_accounting, stress_test

KIND = "SHADOW_RESEARCH"
LABEL_NL = ("EXPERIMENTEEL schaduwportfolio: onderzoek zonder validatie. Nooit live-geschikt, geen bewijs dat een "
            "strategie werkt, geen orders naar een broker.")
DEFAULT_SHADOW_DB = Path(os.environ.get("STRATEGY_LAB_SHADOW_DB") or Path(__file__).resolve().parent / "data_cache" / "shadow_forward.sqlite3")
MIN_COMPARE_TRADES = 10
MIN_DRIFT_TRADES = 30
DEFAULT_LIMITS = {"max_daily_loss": 0.05, "max_drawdown": 0.25, "symbol_risk": 0.02, "group_risk": 0.04,
                  "max_positions": 5}
# Retail leverage notes from research/instruments.py; used only when instruments.margin_leverage() is not provided.
ASSUMED_LEVERAGE = {"fx": 30.0, "metal": 20.0, "index": 20.0, "crypto": 2.0}
EVIDENCE_CLASSES = {
    "HISTORICAL_BACKTEST": {"implemented": True, "prospective": False,
                            "note": "Research backtest before registration; reference only."},
    "HISTORICAL_OOS": {"implemented": True, "prospective": False,
                       "note": "Out-of-sample data that existed before registration; never counted as forward."},
    "DAILY_DELAYED_FORWARD": {"implemented": True, "prospective": True,
                              "note": "Only candles published after registration; whole UTC days; Dukascopy daily "
                                      "update = delayed forward."},
    "REALTIME_PAPER_FORWARD": {"implemented": False, "prospective": True,
                               "note": "NOT IMPLEMENTED: no live feed, no streaming ticks, no order routing."},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_portfolios (
    id TEXT PRIMARY KEY REFERENCES forward_portfolios(id),
    kind TEXT NOT NULL CHECK (kind = 'SHADOW_RESEARCH'),
    experimental INTEGER NOT NULL CHECK (experimental = 1),
    live_eligible INTEGER NOT NULL CHECK (live_eligible = 0),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_strategies (
    registration_id TEXT PRIMARY KEY REFERENCES frozen_strategies(id),
    portfolio_id TEXT NOT NULL REFERENCES shadow_portfolios(id),
    source_kind TEXT NOT NULL, source_ref TEXT NOT NULL, version INTEGER NOT NULL, supersedes TEXT,
    registered_at TEXT NOT NULL, reason_nl TEXT NOT NULL, record TEXT NOT NULL, record_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_advance_log (
    id INTEGER PRIMARY KEY, portfolio_id TEXT NOT NULL, at TEXT NOT NULL, result TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS shadow_portfolios_no_update BEFORE UPDATE ON shadow_portfolios
BEGIN SELECT RAISE(ABORT, 'shadow portfolios are immutable'); END;
CREATE TRIGGER IF NOT EXISTS shadow_portfolios_no_delete BEFORE DELETE ON shadow_portfolios
BEGIN SELECT RAISE(ABORT, 'shadow portfolios are immutable'); END;
CREATE TRIGGER IF NOT EXISTS shadow_strategies_no_update BEFORE UPDATE ON shadow_strategies
BEGIN SELECT RAISE(ABORT, 'shadow registrations are append-only; register a new version'); END;
CREATE TRIGGER IF NOT EXISTS shadow_strategies_no_delete BEFORE DELETE ON shadow_strategies
BEGIN SELECT RAISE(ABORT, 'shadow registrations are append-only; register a new version'); END;
CREATE TRIGGER IF NOT EXISTS shadow_log_no_update BEFORE UPDATE ON shadow_advance_log
BEGIN SELECT RAISE(ABORT, 'shadow log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS shadow_log_no_delete BEFORE DELETE ON shadow_advance_log
BEGIN SELECT RAISE(ABORT, 'shadow log is append-only'); END;
"""


class ShadowNotSupported(DataError):
    """The source cannot be simulated by the forward engine (reason code NOT_SUPPORTED)."""
    code = "NOT_SUPPORTED"


class ShadowIneligible(DataError):
    """The source may not enter the shadow portfolio (reason code NOT_ELIGIBLE); ``reasons`` lists why."""
    code = "NOT_ELIGIBLE"

    def __init__(self, message, reasons):
        super().__init__(message)
        self.reasons = reasons


class _ShadowData:
    """Registry/engine data handle: ``path`` is the shadow database, every read goes to the reference store."""

    def __init__(self, reference: DailyPartitionStore, path: Path):
        self._reference = reference
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __getattr__(self, name):
        return getattr(self._reference, name)


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _indicators(spec) -> list[str]:
    found = set()

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("indicator"), str):
                found.add(node["indicator"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(spec)
    return sorted(found)


def margin_leverage(symbol: str) -> tuple[float, dict]:
    """Leverage for the margin reservation: instrument profile when instruments.py provides one, else an assumption."""
    from .research import instruments

    hook = getattr(instruments, "margin_leverage", None)
    value = hook(symbol) if callable(hook) else None
    if isinstance(value, (int, float)) and math.isfinite(value) and value >= 1:
        return float(value), {"source": "INSTRUMENT_PROFILE", "status": "PROFILE"}
    name = instruments.canonical(symbol)
    group = instruments._STATIC[name][0] if name in instruments._STATIC else "index"
    return ASSUMED_LEVERAGE.get(group, 20.0), {
        "source": "ASSUMED_RETAIL_LEVERAGE", "status": "ASSUMED",
        "note": f"{group} leverage {ASSUMED_LEVERAGE.get(group, 20.0)}:1 is a labelled assumption, not a broker schedule"}


# ----------------------------------------------------------------------------- eligibility

def eligibility(detail: dict) -> dict:
    """May this factory candidate enter the shadow portfolio? ``detail`` is factory.candidate_detail output.

    Allowed: technically valid candidates whose evidence is INSUFFICIENT_EVIDENCE, are untested beyond the technical
    checks (TECHNICALLY_VALID), or were REJECTED on performance/statistics (stage L2 or later) for reasons that are not
    technical. Never: look-ahead or leakage, invalid spec or execution, missing instrument/market data, corrupt data,
    TECHNICAL_BLOCKED, duplicates, candidates still running, and candidates that already passed (those belong in the
    validated portfolio, not in a research sandbox)."""
    from .research import factory

    outcome, status, stage = detail.get("outcome"), detail.get("stored_status"), detail.get("stage")
    reasons = list(detail.get("reasons_nl") or [])
    problems = []
    if outcome == "TECHNICAL_BLOCKED" or status in ("ERROR", "TECHNICAL_BLOCKED"):
        problems.append("TECHNICAL_BLOCKED: missing or invalid data or a pipeline error")
    failed = [c.get("name") for c in detail.get("technical") or [] if c.get("ok") is False]
    if failed:
        problems.append("TECHNICAL_CHECK_FAILED: " + ", ".join(map(str, failed)))
    if detail.get("rejection_kind") == "technisch" or (outcome == "REJECTED" and stage == "L1"):
        problems.append("TECHNICAL_REJECTION: defective strategy (look-ahead, invalid spec or execution)")
    if factory._has(reasons, factory._DEFECT_TXT):
        problems.append("LOOKAHEAD_OR_INVALID_EXECUTION")
    if factory._has(reasons, factory._DATA_TXT):
        problems.append("MISSING_SAFETY_CRITICAL_DATA")
    if detail.get("duplicate"):
        problems.append("DUPLICATE")
    if outcome == "PASSED":
        problems.append("ALREADY_PASSED: use the validated forward portfolio")
    if outcome == "IN_PROGRESS" and status != "TECHNICALLY_VALID":
        problems.append("NOT_FINISHED")
    evidence = "TECHNICALLY_VALID_UNTESTED" if status == "TECHNICALLY_VALID" else outcome
    return {"eligible": not problems, "problems": problems, "evidence_status": evidence,
            "outcome": outcome, "stored_status": status, "stage": stage, "reasons_nl": reasons}


def _reference(detail: dict | None) -> dict:
    """Frozen historical numbers kept apart from forward results (class HISTORICAL_BACKTEST, never pooled)."""
    dev = (detail or {}).get("dev") or {}
    metrics = dev.get("metrics")
    if not metrics:
        return {"evidence_class": "HISTORICAL_BACKTEST", "status": "NO_BACKTEST_REFERENCE", "metrics": None,
                "range": dev.get("range")}
    return {"evidence_class": "HISTORICAL_BACKTEST", "status": "AVAILABLE", "range": dev.get("range"),
            "metrics": {key: metrics.get(key) for key in (
                "trades", "net_return_pct", "profit_factor", "max_dd_pct", "win_rate", "expectancy")}}


def _default_configuration(spec, params, contract, **kw):
    from . import research_api

    return research_api._rule_configuration(spec, params, contract, **kw)


def _resolve_source(source: dict) -> tuple[dict, dict | None, dict | None, dict]:
    """(spec, params, factory detail, eligibility) for a registration source, or raise."""
    kind = source.get("kind")
    if kind in ("jesse", "plugin"):
        raise ShadowNotSupported("The forward engine simulates rule specs only (RULES_BIDASK_V1); Jesse and plugin "
                                 "strategies cannot be shadow-registered")
    if kind == "factory":
        from . import factory_api
        from .research import factory

        candidate_id = source.get("candidate_id")
        if not candidate_id:
            raise DataError("source.candidate_id is required for factory candidates")
        detail = factory.candidate_detail(factory_api.store(), candidate_id, source.get("run_id"), factory_api.vault())
        if detail is None:
            raise DataError("Unknown factory candidate")
        check = eligibility(detail)
        spec = detail["candidate"]["spec"]
        return spec, detail["candidate"].get("params"), detail, check
    if kind in ("builder", "spec"):
        spec = source.get("spec")
        if spec is None:
            from . import research_api

            path = research_api._spec_path(source.get("spec_name") or "")
            if not path.is_file():
                raise DataError("Unknown builder specification")
            spec = json.loads(path.read_text(encoding="utf-8"))
        spec = {key: value for key, value in spec.items() if key != "meta"}
        errors = forward_rules.engine.validate_spec(spec)
        if errors:
            return spec, None, None, {"eligible": False, "problems": ["INVALID_SPEC: " + "; ".join(errors)],
                                      "evidence_status": "UNVALIDATED_BUILDER_SPEC", "reasons_nl": []}
        try:
            forward_rules._reject_unsupported(spec)
        except DataError as exc:
            raise ShadowNotSupported(str(exc)) from exc
        return spec, None, None, {"eligible": True, "problems": [], "evidence_status": "UNVALIDATED_BUILDER_SPEC",
                                  "reasons_nl": []}
    raise DataError("source.kind must be factory, builder, jesse or plugin")


# ----------------------------------------------------------------------------- engine

class ShadowEngine(ForwardEngine):
    def __init__(self, registry: ForwardRegistry, fx=None):
        super().__init__(registry, fx=fx)
        with closing(self._connect()) as db, db:
            db.executescript(SCHEMA)

    # -- guards
    def _require_shadow(self, portfolio_id: str) -> None:
        with closing(self._connect()) as db:
            if db.execute("SELECT 1 FROM shadow_portfolios WHERE id=?", (portfolio_id,)).fetchone() is None:
                raise DataError("Unknown shadow portfolio")

    def promote(self, portfolio_id: str, *_, **__):
        self._require_shadow(portfolio_id)
        raise DataError("Shadow portfolios can never become LIVE_ELIGIBLE; register the frozen rules in the validated "
                        "forward portfolio after the validation gates instead")

    # -- portfolios
    def create_shadow(self, name: str, *, currency: str = "USD", starting_capital: float, risk_limit: float,
                      limits: dict | None = None, now: datetime | None = None) -> str:
        identifier = ForwardEngine.create(self, name, currency=currency, starting_capital=starting_capital,
                                          risk_limit=risk_limit)
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO shadow_portfolios VALUES (?, ?, 1, 0, ?)",
                       (identifier, KIND, _utc(now or datetime.now(timezone.utc)).isoformat(timespec="microseconds")))
        self.set_limits(identifier, **{**DEFAULT_LIMITS, **(limits or {})})
        return identifier

    def create(self, *args, **kwargs):
        raise DataError("Use create_shadow: every portfolio in the shadow store is a shadow portfolio")

    def shadow_portfolios(self) -> list[dict]:
        with closing(self._connect()) as db:
            rows = [dict(row) for row in db.execute("""
                SELECT p.id, p.name, p.currency, p.starting_capital, p.balance, p.risk_limit, p.last_day,
                       s.kind, s.created_at,
                       (SELECT COUNT(*) FROM shadow_strategies x WHERE x.portfolio_id=p.id) AS strategies,
                       (SELECT COUNT(*) FROM forward_days d WHERE d.portfolio_id=p.id) AS prospective_days
                FROM forward_portfolios p JOIN shadow_portfolios s ON s.id=p.id ORDER BY s.created_at, p.id""")]
        return [{**row, "experimental": True, "live_eligible": False, "label_nl": LABEL_NL} for row in rows]

    # -- registration
    def register_strategy(self, portfolio_id: str, *, source: dict, reason_nl: str, allocation: float | None = None,
                          trade_risk: float | None = None, now: datetime | None = None,
                          configuration_builder=None) -> dict:
        self._require_shadow(portfolio_id)
        reason = (reason_nl or "").strip()
        if len(reason) < 3:
            raise DataError("reason_nl is required: why is this strategy registered experimentally?")
        spec, params, detail, check = _resolve_source(source)
        if not check["eligible"]:
            raise ShadowIneligible("Not eligible for the shadow portfolio: " + "; ".join(check["problems"]),
                                   check["problems"])
        from .research import backtest as rbt, instruments

        symbol = spec["symbol"]
        if instruments.canonical(symbol) not in instruments.SYMBOLS:
            raise ShadowIneligible("Missing safety-critical instrument data",
                                   ["MISSING_SAFETY_CRITICAL_DATA: instrument not in the registry"])
        contract = dataclasses.asdict(rbt.default_contract(symbol))
        fx = fx_check(symbol, rbt.default_contract(symbol), fx_available=self.fx_service() is not None)
        if fx["status"] == "BLOCKED":
            raise ShadowIneligible("FX conversion unavailable", [f"FX_RATE_UNAVAILABLE: {fx['reason']}"])
        with closing(self._connect()) as db:
            portfolio = self._portfolio(db, portfolio_id)
            used = db.execute("SELECT COALESCE(SUM(allocation),0) FROM forward_members WHERE portfolio_id=?",
                              (portfolio_id,)).fetchone()[0]
            previous = db.execute("SELECT registration_id FROM shadow_strategies WHERE portfolio_id=? AND "
                                  "source_kind=? AND source_ref=? ORDER BY version DESC LIMIT 1",
                                  (portfolio_id, source["kind"], _ref(source))).fetchone()
            version = 1 + db.execute("SELECT COUNT(*) FROM shadow_strategies WHERE portfolio_id=? AND source_kind=? "
                                     "AND source_ref=?", (portfolio_id, source["kind"], _ref(source))).fetchone()[0]
        capital = allocation if allocation is not None else portfolio["starting_capital"] / 3
        if used + capital > portfolio["starting_capital"]:
            raise DataError("Shadow allocations exceed the shared starting capital")
        risk = trade_risk if trade_risk is not None else spec["risk"]["percent"] / 100.0
        leverage, margin = margin_leverage(symbol)
        frozen_at = _utc(now or datetime.now(timezone.utc))
        build = configuration_builder or _default_configuration
        config = build(spec, params, contract, capital=capital, trade_risk=risk,
                       portfolio_limit=portfolio["risk_limit"], leverage=leverage, as_of=frozen_at)
        config["margin_model"] = margin
        if previous:
            before = self._shadow_rows(registration_id=previous[0])[0]["record"]
            if (before["spec_sha256"], before["parameters"], before["risk_model"]["allocation"],
                    before["risk_model"]["trade_risk"]) == (config["spec_sha256"], config["parameters"], capital, risk):
                raise ShadowIneligible("Already registered in this shadow portfolio with identical specification, "
                                       "parameters and risk (version %d)" % before["version"],
                                       ["DUPLICATE_REGISTRATION: identical to version %d" % before["version"]])
        frozen = forward_rules.freeze(self.registry, config, frozen_at=frozen_at)
        self.enroll(portfolio_id, frozen["id"], joined_at=datetime.fromisoformat(frozen["frozen_at"]))
        record = {
            "kind": KIND, "experimental": True, "live_eligible": False, "version": version,
            "supersedes": previous[0] if previous else None,
            "source": {"kind": source["kind"], "ref": _ref(source), "evidence_status": check["evidence_status"],
                       "outcome": check.get("outcome"), "stage": check.get("stage"),
                       "reasons_nl": check["reasons_nl"]},
            "spec_sha256": config["spec_sha256"], "code_sha256": frozen["source_sha256"],
            "parameters": config["parameters"], "indicators": _indicators(spec), "symbol": config["symbol"],
            "timeframe": config["timeframe"],
            "cost_model": {"quotes": "Dukascopy Bid/Ask M1 (ASK for longs, BID for shorts)",
                           "commission_per_lot_side": contract["commission_per_lot_side"],
                           "slippage_points": contract["slippage_points"], "contract": config["contract"],
                           "assumption": "contract defaults from research/backtest.default_contract; not broker-verified"},
            "execution_model": config["execution_model"],
            "risk_model": {"allocation": capital, "trade_risk": risk, "portfolio_risk_limit": config[
                "portfolio_risk_limit"], "leverage": leverage, "margin_model": margin, "fx": fx},
            "data_snapshot_sha256": frozen["snapshot_sha256"], "data_version": config.get("data_version"),
            "warmup_sha256": config["warmup"]["sha256"], "start_utc": frozen["frozen_at"],
            "info_cutoff_utc": config["warmup"]["as_of"],
            "research_period_used": (_reference(detail).get("range") if detail else None) or "UNKNOWN",
            "backtest_reference": _reference(detail), "reason_nl": reason,
        }
        encoded = _canonical(record)
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO shadow_strategies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (frozen["id"], portfolio_id, source["kind"], _ref(source), version, record["supersedes"],
                        frozen["frozen_at"], reason, encoded, hashlib.sha256(encoded.encode()).hexdigest()))
        return {"strategy_id": frozen["id"], "portfolio_id": portfolio_id, "version": version,
                "start_utc": frozen["frozen_at"], "snapshot_sha256": frozen["snapshot_sha256"],
                "evidence_status": "AWAITING_FIRST_FORWARD_DAY", "kind": KIND, "experimental": True,
                "live_eligible": False, "label_nl": LABEL_NL}

    # -- processing
    def advance(self, portfolio_id: str, **kwargs) -> dict:
        self._require_shadow(portfolio_id)
        result = ForwardEngine.advance(self, portfolio_id, **kwargs)
        result = {**result, "evidence_class": "DAILY_DELAYED_FORWARD", "kind": KIND, "label_nl": LABEL_NL}
        with closing(self._connect()) as db, db:
            last = db.execute("SELECT result FROM shadow_advance_log WHERE portfolio_id=? ORDER BY id DESC LIMIT 1",
                              (portfolio_id,)).fetchone()
            if result["committed_days"] or last is None or json.loads(last[0]).get("status") != result["status"]:
                db.execute("INSERT INTO shadow_advance_log(portfolio_id, at, result) VALUES (?, ?, ?)",
                           (portfolio_id, _utc(datetime.now(timezone.utc)).isoformat(timespec="microseconds"),
                            _canonical(result)))
        return result

    def last_advance(self, portfolio_id: str) -> dict | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT at, result FROM shadow_advance_log WHERE portfolio_id=? ORDER BY id DESC LIMIT 1",
                             (portfolio_id,)).fetchone()
        return {"at": row["at"], **json.loads(row["result"])} if row else None

    # -- views
    def _shadow_rows(self, portfolio_id: str | None = None, registration_id: str | None = None) -> list[dict]:
        with closing(self._connect()) as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM shadow_strategies WHERE (? IS NULL OR portfolio_id=?) AND (? IS NULL OR "
                "registration_id=?) ORDER BY registered_at, registration_id",
                (portfolio_id, portfolio_id, registration_id, registration_id))]
        for row in rows:
            row["record"] = json.loads(row["record"])
        return rows

    def portfolio_view(self, portfolio_id: str) -> dict:
        self._require_shadow(portfolio_id)
        report = self.report(portfolio_id)
        rows = {row["registration_id"]: row for row in self._shadow_rows(portfolio_id)}
        days, portfolio = report["days"], report["portfolio"]
        previous = portfolio["starting_capital"]
        equity, daily = [], []
        closed_by_day, rejected_by_day = Counter(), Counter()
        for event in report["events"]:
            if event["kind"] == "POSITION_CLOSED":
                closed_by_day[event["day"]] += 1
            elif event["kind"] == "ORDER_REJECTED":
                rejected_by_day[event["day"]] += 1
        for day in days:
            equity.append({"day": day["day"], "equity": day["equity"], "balance": day["balance"]})
            daily.append({"day": day["day"], "pnl": day["equity"] - previous, "trades_closed": closed_by_day[day["day"]],
                          "orders_rejected": rejected_by_day[day["day"]], "processed_at": day["processed_at"]})
            previous = day["equity"]
        strategies = [self._summary(report, member, rows[member["registration_id"]])
                      for member in report["members"] if member["registration_id"] in rows]
        stored = next((row for row in self.shadow_portfolios() if row["id"] == portfolio_id))
        paused = self.last_advance(portfolio_id)
        warnings = [LABEL_NL]
        if not days:
            warnings.append("Nog geen prospectieve dag verwerkt: er is geen forward-resultaat, alleen een registratie.")
        if paused and paused.get("status") in ("PAUSED", "WAITING_FOR_DATA"):
            warnings.append(f"Verwerking gepauzeerd: {paused.get('reason') or paused['status']}"
                            f" ({paused.get('detail') or paused.get('day') or ''}).")
        return {
            "portfolio": {**stored, "equity": days[-1]["equity"] if days else portfolio["balance"],
                          "label_nl": LABEL_NL},
            "kind": KIND, "experimental": True, "live_eligible": False, "label_nl": LABEL_NL,
            "strategies": strategies, "equity": equity, "daily": daily, "limits": report["limits"],
            "accounting": portfolio_accounting(report), "stress": stress_test(report),
            "correlations": forward_correlations(report), "ledger": self.verify_ledger(portfolio_id),
            "flags": report["flags"][-100:], "last_advance": paused, "evidence_classes": EVIDENCE_CLASSES,
            "prospective_days": len(days), "warnings": warnings,
        }

    def strategy_view(self, registration_id: str) -> dict:
        rows = self._shadow_rows(registration_id=registration_id)
        if not rows:
            raise DataError("Unknown shadow strategy")
        row = rows[0]
        report = self.report(row["portfolio_id"])
        member = next(m for m in report["members"] if m["registration_id"] == registration_id)
        summary = self._summary(report, member, row)
        trades = _trades(report, registration_id)
        closed = [t for t in trades if t["status"] == "CLOSED"]
        equity, daily = _member_series(report, member)
        return {
            "strategy": summary, "equity": equity, "daily": daily, "trades": trades,
            "clustering": _clustering(closed), "backtest_vs_forward": _compare(row["record"], summary),
            "drift": _drift(row["record"], summary), "warnings": summary["warnings"], "record": row["record"],
            "record_sha256": row["record_sha256"], "evidence_classes": EVIDENCE_CLASSES, "kind": KIND,
            "experimental": True, "live_eligible": False, "label_nl": LABEL_NL,
            "evidence_class": "DAILY_DELAYED_FORWARD",
        }

    def _summary(self, report, member, row) -> dict:
        registration = member["registration_id"]
        trades = [t for t in _trades(report, registration) if t["status"] == "CLOSED"]
        equity, _ = _member_series(report, member)
        metrics, _curve = _performance(member["allocation"], [(p["day"], p["equity"]) for p in equity],
                                       [{**t, "net_pnl": t["net_pnl"]} for t in trades])
        record, days = row["record"], len(report["days"])
        member_days = sum(1 for d in report["days"] if d["day"] >= member["joined_at"][:10])
        if not member_days:
            status = "AWAITING_FIRST_FORWARD_DAY"
        elif len(trades) < MIN_DRIFT_TRADES:
            status = "DAILY_DELAYED_FORWARD_INSUFFICIENT"
        else:
            status = "DAILY_DELAYED_FORWARD_PRELIMINARY"
        warnings = [f"Experimenteel: bronbewijs {record['source']['evidence_status']}."]
        if not member_days:
            warnings.append("Nog geen prospectieve dag verwerkt; er zijn geen forward-trades.")
        elif len(trades) < MIN_DRIFT_TRADES:
            warnings.append(f"{len(trades)} forward-trades; onder {MIN_DRIFT_TRADES} is geen conclusie mogelijk.")
        cost = record["cost_model"]
        if not cost["commission_per_lot_side"] and not cost["slippage_points"]:
            warnings.append("Kostenmodel: alleen spread uit Bid/Ask; commissie en slippage staan op 0 (aanname).")
        if record["risk_model"]["margin_model"]["status"] == "ASSUMED":
            warnings.append("Margin gebaseerd op een aangenomen hefboom, niet op een brokerprofiel.")
        if member.get("fx", {}).get("status") == "DYNAMIC_FX":
            warnings.append("P&L wordt per candle omgerekend met het causale FX-model; bij ontbrekende koers pauzeert de verwerking.")
        return {
            "id": registration, "portfolio_id": row["portfolio_id"], "name": member["configuration"]["strategy"],
            "symbol": member["configuration"]["symbol"], "timeframe": member["configuration"]["timeframe"],
            "start_utc": row["registered_at"], "version": row["version"], "trades": len(trades),
            "net": metrics["net_profit"], "max_dd_pct": metrics["max_drawdown_percent"],
            "pf": metrics["profit_factor"], "winrate_percent": metrics["winrate_percent"],
            "last_update": report["days"][-1]["processed_at"] if member_days else None,
            "evidence_status": status, "source_evidence_status": record["source"]["evidence_status"],
            "prospective_days": member_days, "allocation": member["allocation"],
            "open_position": member["state"].get("position") is not None, "warnings": warnings,
            "experimental": True, "live_eligible": False,
        }


def _ref(source: dict) -> str:
    return str(source.get("candidate_id") or source.get("spec_name") or source.get("spec", {}).get("name") or "")


def _member_series(report, member):
    """Realised balance of one member per committed day since it joined, plus per-day P&L and trade counts."""
    pnl, count = defaultdict(float), Counter()
    for event in report["events"]:
        if event["registration_id"] == member["registration_id"] and event["kind"] == "POSITION_CLOSED":
            pnl[event["day"]] += event["detail"]["net_pnl"]
            count[event["day"]] += 1
    balance, equity, daily = member["allocation"], [], []
    for day in report["days"]:
        if day["day"] < member["joined_at"][:10]:
            continue
        balance += pnl[day["day"]]
        equity.append({"day": day["day"], "equity": balance})
        daily.append({"day": day["day"], "pnl": pnl[day["day"]], "trades_closed": count[day["day"]]})
    return equity, daily


def _trades(report, registration_id) -> list[dict]:
    """One row per hypothetical trade with every timestamp that matters for the no-future-data audit."""
    submitted, positions, order = {}, {}, []
    for event in report["events"]:
        if event["registration_id"] != registration_id:
            continue
        detail = event["detail"]
        if event["kind"] == "ORDER_SUBMITTED":
            submitted[(detail.get("signal_at"), detail.get("side"))] = event["candle_at"]
        elif event["kind"] == "ORDER_FILLED":
            trade = {
                "position_id": detail.get("position_id"), "order_id": detail.get("order_id"), "side": detail["side"],
                "candle_ts": event["candle_at"], "received_at": detail.get("received_at"),
                "signal_ts": detail.get("signal_at"),
                "order_ts": submitted.get((detail.get("signal_at"), detail["side"]), detail.get("signal_at")),
                "fill_assumption": detail.get("fill_assumption"), "entry_at": detail["entry_at"],
                "entry": detail["entry"], "quantity": detail["quantity"], "stop": detail["stop"],
                "target": detail["target"], "margin": detail["margin"], "risk": detail["risk"],
                "costs": {"spread_open": detail.get("spread_open"), "slippage_points": detail.get("slippage_points"),
                          "quote": detail.get("quote"), "commission": None},
                "fx_rate": detail.get("fx_rate"), "status": "OPEN", "exit_at": None, "exit": None,
                "exit_reason": None, "gross_pnl": None, "net_pnl": None, "hypothetical": True,
                "evidence_class": "DAILY_DELAYED_FORWARD", "day": event["day"]}
            positions[detail["entry_at"]] = trade
            order.append(trade)
        elif event["kind"] == "POSITION_CLOSED":
            trade = positions.get(detail["entry_at"])
            if trade:
                trade.update(status="CLOSED", exit_at=detail["exit_at"], exit=detail["exit"],
                             exit_reason=detail["exit_reason"], gross_pnl=detail["gross_pnl"],
                             net_pnl=detail["net_pnl"], day=event["day"])
                trade["costs"]["commission"] = detail["commission"]
    return order


def _clustering(closed: list[dict]) -> dict:
    if not closed:
        return {"status": "NO_TRADES", "reason": "no closed forward trades yet"}
    by_day = Counter(t["exit_at"][:10] for t in closed)
    by_hour = Counter(t["entry_at"][11:13] for t in closed)
    streak = longest = 0
    for trade in sorted(closed, key=lambda t: t["exit_at"]):
        streak = streak + 1 if trade["net_pnl"] < 0 else 0
        longest = max(longest, streak)
    wins = sorted((t["net_pnl"] for t in closed if t["net_pnl"] > 0), reverse=True)
    return {"status": "OK", "trades": len(closed), "trades_per_day_max": max(by_day.values()),
            "days_with_trades": len(by_day), "longest_loss_streak": longest,
            "entry_hour_utc": dict(sorted(by_hour.items())),
            "top3_profit_share": sum(wins[:3]) / sum(wins) if wins else None}


def _compare(record: dict, summary: dict) -> dict:
    forward = {"evidence_class": "DAILY_DELAYED_FORWARD", "trades": summary["trades"], "net": summary["net"],
               "profit_factor": summary["pf"], "max_dd_pct": summary["max_dd_pct"],
               "winrate_percent": summary["winrate_percent"]}
    reference = record["backtest_reference"]
    if reference["status"] != "AVAILABLE":
        status = "NO_BACKTEST_REFERENCE"
    elif summary["trades"] < MIN_COMPARE_TRADES:
        status = "INSUFFICIENT_FORWARD_DATA"
    else:
        status = "COMPARABLE"
    return {"status": status, "min_forward_trades": MIN_COMPARE_TRADES, "historical_backtest": reference,
            "forward": forward, "note": "Different evidence classes are shown side by side and never pooled."}


def _drift(record: dict, summary: dict) -> dict:
    n = summary["trades"]
    out = {"status": "INSUFFICIENT_EVIDENCE", "n_forward_trades": n, "min_trades": MIN_DRIFT_TRADES,
           "profit_factor_ratio": None, "reason": f"{n} forward trades < {MIN_DRIFT_TRADES}"}
    metrics = (record["backtest_reference"] or {}).get("metrics") or {}
    if n >= MIN_DRIFT_TRADES and metrics.get("profit_factor") and summary["pf"] is not None:
        ratio = summary["pf"] / metrics["profit_factor"]
        out.update(status="OK", profit_factor_ratio=ratio, reason=None,
                   drifting=bool(ratio < 0.5))
    elif n >= MIN_DRIFT_TRADES:
        out["reason"] = "no comparable backtest profit factor"
    return out


def shadow_engine(path: Path | None = None, reference_path: Path | None = None, fx=None) -> ShadowEngine:
    """The shadow store (own file) reading candles from the reference daily store."""
    reference = DailyPartitionStore(reference_path or DEFAULT_DAILY_STORE)
    return ShadowEngine(ForwardRegistry(_ShadowData(reference, path or DEFAULT_SHADOW_DB)), fx=fx)
