"""Append-only research ledger: every tested hypothesis is recorded, nothing is ever edited or removed."""
from __future__ import annotations

import json
import sqlite3
import hashlib
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from statistics import variance
from uuid import uuid4

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data_cache" / "research_ledger.sqlite3"

KINDS = {"BACKTEST", "VALIDATION", "DISCOVERY", "HOLDOUT"}
STATUSES = {"TESTED", "CANDIDATE", "REJECTED", "ERROR"}
JSON_FIELDS = ("spec", "params", "mutations", "cost_model", "data_requirements",
               "metrics", "oos_metrics", "statistics", "trials")
TEXT_FIELDS = ("hypothesis_id", "parent_id", "family", "symbol", "timeframe", "start", "end",
               "spec_sha256", "code_sha256", "dataset_sha256", "rejection_reason", "verdict")
COLUMNS = ("id", "created_at", "kind", "status", "score",
           *TEXT_FIELDS, *(f"{n}_json" for n in JSON_FIELDS))

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('BACKTEST','VALIDATION','DISCOVERY','HOLDOUT')),
    hypothesis_id TEXT, parent_id TEXT, family TEXT, symbol TEXT, timeframe TEXT, start TEXT, end TEXT,
    spec_json TEXT, spec_sha256 TEXT, params_json TEXT, mutations_json TEXT, code_sha256 TEXT,
    dataset_sha256 TEXT, cost_model_json TEXT, data_requirements_json TEXT,
    status TEXT NOT NULL CHECK (status IN ('TESTED','CANDIDATE','REJECTED','ERROR')),
    rejection_reason TEXT, metrics_json TEXT, oos_metrics_json TEXT, score REAL, verdict TEXT,
    statistics_json TEXT, trials_json TEXT
);
CREATE INDEX IF NOT EXISTS entries_family ON entries(family, symbol, timeframe);
CREATE INDEX IF NOT EXISTS entries_symbol_period ON entries(symbol, start, end);
CREATE TRIGGER IF NOT EXISTS entries_no_update BEFORE UPDATE ON entries
BEGIN SELECT RAISE(ABORT, 'research ledger is append-only: UPDATE not allowed'); END;
CREATE TRIGGER IF NOT EXISTS entries_no_delete BEFORE DELETE ON entries
BEGIN SELECT RAISE(ABORT, 'research ledger is append-only: DELETE not allowed'); END;
CREATE TABLE IF NOT EXISTS preregistrations (
    sha256 TEXT PRIMARY KEY, created_at TEXT NOT NULL, run_id TEXT, protocol_json TEXT NOT NULL,
    previous_sha256 TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS search_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, run_id TEXT, candidate_id TEXT,
    stage TEXT, decision TEXT NOT NULL, detail_json TEXT
);
CREATE TABLE IF NOT EXISTS data_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, run_id TEXT, symbol TEXT, timeframe TEXT,
    start TEXT, end TEXT, purpose TEXT NOT NULL, detail_json TEXT
);
CREATE TRIGGER IF NOT EXISTS prereg_no_update BEFORE UPDATE ON preregistrations
BEGIN SELECT RAISE(ABORT, 'preregistrations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS prereg_no_delete BEFORE DELETE ON preregistrations
BEGIN SELECT RAISE(ABORT, 'preregistrations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS decisions_no_update BEFORE UPDATE ON search_decisions
BEGIN SELECT RAISE(ABORT, 'search decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS decisions_no_delete BEFORE DELETE ON search_decisions
BEGIN SELECT RAISE(ABORT, 'search decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS usage_no_update BEFORE UPDATE ON data_usage
BEGIN SELECT RAISE(ABORT, 'data usage is append-only'); END;
CREATE TRIGGER IF NOT EXISTS usage_no_delete BEFORE DELETE ON data_usage
BEGIN SELECT RAISE(ABORT, 'data usage is append-only'); END;
"""
DATA_PURPOSES = {"SEARCH", "VALIDATION_SELECTION", "SEALED_FINAL", "SELECTION_EXPERIMENT", "FORWARD"}


def _dumps(value):
    return None if value is None else json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _loads(text):
    try:
        return None if text is None else json.loads(text)
    except ValueError:
        return None


def hypothesis_id(key: str, params: dict | None) -> str:
    """Stable id of one tested configuration: sha256 over a strategy key plus its sorted parameters."""
    norm = {k: int(v) if isinstance(v, float) and v.is_integer() else v for k, v in (params or {}).items()}
    return hashlib.sha256(f"{key}|{_dumps(norm)}".encode()).hexdigest()


def _num(mapping, *keys):
    if isinstance(mapping, dict):
        for key in keys:
            if isinstance(mapping.get(key), (int, float)) and not isinstance(mapping.get(key), bool):
                return mapping[key]
    return None


class ResearchLedger:
    def __init__(self, path=None):
        self.path = Path(path or DEFAULT_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript(SCHEMA)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def record(self, **fields) -> str:
        unknown = set(fields) - set(TEXT_FIELDS) - set(JSON_FIELDS) - {"kind", "status", "score"}
        if unknown:
            raise ValueError(f"Onbekende ledger-velden: {sorted(unknown)}")
        kind = fields.get("kind", "BACKTEST")
        status = fields.get("status", "TESTED")
        if kind not in KINDS or status not in STATUSES:
            raise ValueError("Ongeldige kind of status voor ledger-entry")
        if fields.get("spec") is not None and not fields.get("spec_sha256"):
            fields["spec_sha256"] = hashlib.sha256(_dumps(fields["spec"]).encode()).hexdigest()
        score = fields.get("score")
        entry_id = uuid4().hex
        row = {"id": entry_id, "created_at": datetime.now(timezone.utc).isoformat(),
               "kind": kind, "status": status, "score": None if score is None else float(score)}
        for name in TEXT_FIELDS:
            value = fields.get(name)
            row[name] = None if value is None else str(value)
        for name in JSON_FIELDS:
            row[f"{name}_json"] = _dumps(fields.get(name))
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(f"INSERT INTO entries ({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})",
                       [row[c] for c in COLUMNS])
        return entry_id

    @staticmethod
    def _flatten(row, *, full):
        item = dict(row)
        parsed = {name: _loads(item.pop(f"{name}_json")) for name in JSON_FIELDS}
        metrics, oos = parsed["metrics"], parsed["oos_metrics"]
        item.update(
            trades=_num(metrics, "trades", "trade_count"), net_profit=_num(metrics, "net_profit"),
            profit_factor=_num(metrics, "profit_factor"),
            oos_net_profit=_num(oos, "net_profit"), oos_trades=_num(oos, "trades", "trade_count"))
        if full:
            item.update(parsed)
        else:
            item.update(metrics=metrics, oos_metrics=oos, statistics=parsed["statistics"])
        return item

    def get(self, entry_id: str) -> dict | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        return self._flatten(row, full=True) if row else None

    def list(self, status=None, kind=None, family=None, limit=200) -> list[dict]:
        where, args = [], []
        for column, value in (("status", status), ("kind", kind), ("family", family)):
            if value is not None:
                where.append(f"{column}=?")
                args.append(value)
        sql = "SELECT * FROM entries" + (" WHERE " + " AND ".join(where) if where else "")
        with closing(self._connect()) as db:
            rows = db.execute(sql + " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                              (*args, max(1, int(limit)))).fetchall()
        return [self._flatten(row, full=False) for row in rows]

    def summary(self) -> dict:
        with closing(self._connect()) as db:
            def counts(column):
                return {r[0] or "": r[1] for r in db.execute(
                    f"SELECT {column}, COUNT(*) FROM entries GROUP BY {column}")}
            by_status = counts("status")
            return {"total": sum(by_status.values()), "candidates": by_status.get("CANDIDATE", 0),
                    "rejected": by_status.get("REJECTED", 0), "errors": by_status.get("ERROR", 0),
                    "by_family": counts("family"), "by_kind": counts("kind")}

    @staticmethod
    def _where(family=None, symbol=None, timeframe=None):
        where, args = [], []
        for column, value in (("family", family), ("symbol", symbol), ("timeframe", timeframe)):
            if value is not None:
                where.append(f"{column}=?")
                args.append(value)
        return (" WHERE " + " AND ".join(where) if where else ""), args

    def n_trials(self, family=None, symbol=None, timeframe=None, include_validation_configs: bool = False) -> int:
        """Distinct hypotheses. With ``include_validation_configs`` the ``configs_evaluated`` of earlier
        VALIDATION entries (walk-forward candidates, PBO combinations, stability neighbours that are not
        recorded as separate rows) are added on top."""
        where, args = self._where(family, symbol, timeframe)
        with closing(self._connect()) as db:
            count = db.execute("SELECT COUNT(DISTINCT COALESCE(hypothesis_id, id)) FROM entries" + where,
                               args).fetchone()[0]
        return count + (self.validation_configs(family, symbol, timeframe) if include_validation_configs else 0)

    def validation_configs(self, family=None, symbol=None, timeframe=None) -> int:
        where, args = self._where(family, symbol, timeframe)
        sql = "SELECT statistics_json FROM entries" + (where + " AND " if where else " WHERE ") + "kind='VALIDATION'"
        with closing(self._connect()) as db:
            rows = db.execute(sql, args).fetchall()
        return int(sum(_num(_loads(r[0]), "configs_evaluated") or 0 for r in rows))

    def hypothesis_ids(self, family=None, symbol=None, timeframe=None) -> set[str]:
        where, args = self._where(family, symbol, timeframe)
        sql = ("SELECT DISTINCT hypothesis_id FROM entries" + (where + " AND " if where else " WHERE ")
               + "hypothesis_id IS NOT NULL")
        with closing(self._connect()) as db:
            return {r[0] for r in db.execute(sql, args).fetchall()}

    def sharpe_variance(self, family=None) -> float | None:
        sql, args = "SELECT metrics_json FROM entries WHERE metrics_json IS NOT NULL", []
        if family is not None:
            sql, args = sql + " AND family=?", [family]
        with closing(self._connect()) as db:
            rows = db.execute(sql, args).fetchall()
        values = [v for r in rows if (v := _num(_loads(r[0]), "sharpe_daily")) is not None]
        return variance(values) if len(values) >= 2 else None

    def data_exposure(self, symbol, start, end) -> list[dict]:
        """Prior entries whose [start, end) overlaps the given period: that data was already looked at."""
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM entries WHERE symbol=? AND start IS NOT NULL AND end IS NOT NULL "
                "AND start < ? AND end > ? ORDER BY created_at, rowid", (symbol, str(end), str(start))).fetchall()
        return [self._flatten(row, full=False) for row in rows]

    def factory_signatures(self) -> set[str]:
        """``signature|symbol|timeframe`` of every factory candidate ever recorded (for dedup across runs)."""
        sql = ("SELECT DISTINCT json_extract(statistics_json, '$.factory.signature'), symbol, timeframe FROM entries "
               "WHERE statistics_json LIKE '%\"factory\"%'")
        with closing(self._connect()) as db:
            rows = db.execute(sql).fetchall()
        return {f"{r[0]}|{r[1]}|{r[2]}" for r in rows if r[0]}

    # ------------------------------------------------------------------ V10 additive: protocol, decisions, data use
    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def preregister(self, sha256: str, protocol: dict, *, run_id=None, note=None) -> dict:
        """Record a frozen research protocol. The same hash again is the same hypothesis; any change gives a new
        hash, recorded as a NEW registration that points to the most recent earlier one."""
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT sha256, created_at, run_id FROM preregistrations WHERE sha256=?",
                             (sha256,)).fetchone()
            if row:
                return {"sha256": sha256, "new": False, "first_run_id": row["run_id"], "created_at": row["created_at"]}
            prev = db.execute("SELECT sha256 FROM preregistrations ORDER BY created_at DESC, rowid DESC LIMIT 1"
                              ).fetchone()
            db.execute("INSERT INTO preregistrations VALUES (?,?,?,?,?,?)",
                       (sha256, self._now(), run_id, _dumps(protocol), prev[0] if prev else None, note))
        return {"sha256": sha256, "new": True, "previous_sha256": prev[0] if prev else None}

    def preregistrations(self) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM preregistrations ORDER BY created_at, rowid").fetchall()
        return [{**dict(r), "protocol": _loads(r["protocol_json"])} for r in rows]

    def record_decision(self, *, run_id, candidate_id, stage, decision, detail=None) -> None:
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO search_decisions (created_at, run_id, candidate_id, stage, decision, detail_json) "
                       "VALUES (?,?,?,?,?,?)", (self._now(), run_id, candidate_id, stage, decision, _dumps(detail)))

    def decisions(self, run_id=None, candidate_id=None) -> list[dict]:
        sql, args = "SELECT * FROM search_decisions", []
        where = [(c, v) for c, v in (("run_id", run_id), ("candidate_id", candidate_id)) if v is not None]
        if where:
            sql += " WHERE " + " AND ".join(f"{c}=?" for c, _ in where)
            args = [v for _, v in where]
        with closing(self._connect()) as db:
            rows = db.execute(sql + " ORDER BY id", args).fetchall()
        return [{**dict(r), "detail": _loads(r["detail_json"])} for r in rows]

    def record_data_usage(self, *, run_id, symbol, timeframe, start, end, purpose, detail=None) -> None:
        if purpose not in DATA_PURPOSES:
            raise ValueError(f"onbekend datadoel {purpose}")
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO data_usage (created_at, run_id, symbol, timeframe, start, end, purpose, detail_json)"
                       " VALUES (?,?,?,?,?,?,?,?)", (self._now(), run_id, symbol, timeframe, str(start), str(end),
                                                     purpose, _dumps(detail)))

    def data_usage(self, symbol=None, purpose=None) -> list[dict]:
        sql, args = "SELECT * FROM data_usage", []
        where = [(c, v) for c, v in (("symbol", symbol), ("purpose", purpose)) if v is not None]
        if where:
            sql += " WHERE " + " AND ".join(f"{c}=?" for c, _ in where)
            args = [v for _, v in where]
        with closing(self._connect()) as db:
            rows = db.execute(sql + " ORDER BY id", args).fetchall()
        return [{**dict(r), "detail": _loads(r["detail_json"])} for r in rows]

    def validation_reuse(self, symbol, start, end, exclude_run=None) -> dict:
        """Was [start, end) of this symbol already used to SELECT strategies (earlier runs, any round)? Uses the
        data_usage history plus older ledger rows (walk-forward / validation stages before V10 recorded usage)."""
        runs = set()
        for u in self.data_usage(symbol=symbol):
            if u["purpose"] in ("VALIDATION_SELECTION", "SELECTION_EXPERIMENT") and u["run_id"] != exclude_run                     and str(u["start"]) < str(end) and str(u["end"]) > str(start):
                runs.add(u["run_id"])
        legacy = 0
        for r in self.data_exposure(symbol, start, end):
            fac = ((r.get("statistics") or {}).get("factory") or {})
            if r["kind"] == "VALIDATION" or (fac.get("stage") == "L5" and fac.get("run_id") != exclude_run):
                legacy += 1
                if fac.get("run_id"):
                    runs.add(fac["run_id"])
        return {"reused": bool(runs or legacy), "prior_runs": sorted(r for r in runs if r), "legacy_rows": legacy,
                "evidence_class": "REUSED_FOR_SELECTION" if (runs or legacy) else "FIRST_USE_SELECTION"}
