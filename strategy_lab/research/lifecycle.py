"""Strategy lifecycle registry with evidence-gated transitions.

Nothing in here enables trading. LIVE_ELIGIBLE means: eligible for human review only.

Allowed transitions (anything may also go to RETIRED):
    IDEA -> PROTOTYPE
    PROTOTYPE -> BACKTESTING
    BACKTESTING -> OPTIMIZING | VALIDATION_FAILED | BACKTEST_VALIDATED
    OPTIMIZING -> VALIDATION_FAILED | BACKTEST_VALIDATED
    BACKTEST_VALIDATED -> OOS_VALIDATED | VALIDATION_FAILED
    OOS_VALIDATED -> FORWARD_TESTING
    FORWARD_TESTING -> FORWARD_FAILED | PAPER_ELIGIBLE
    PAPER_ELIGIBLE -> LIVE_REVIEW_REQUIRED | FORWARD_FAILED
    LIVE_REVIEW_REQUIRED -> LIVE_ELIGIBLE | FORWARD_FAILED
    VALIDATION_FAILED | FORWARD_FAILED -> PROTOTYPE (new version)
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data_cache" / "lifecycle.sqlite3"

STATUSES = ("IDEA", "PROTOTYPE", "BACKTESTING", "OPTIMIZING", "VALIDATION_FAILED", "BACKTEST_VALIDATED",
            "OOS_VALIDATED", "FORWARD_TESTING", "FORWARD_FAILED", "PAPER_ELIGIBLE",
            "LIVE_REVIEW_REQUIRED", "LIVE_ELIGIBLE", "RETIRED")
ORIGINS = ("python", "builder", "ai", "discovery", "library")

TRANSITIONS = {
    "IDEA": {"PROTOTYPE"},
    "PROTOTYPE": {"BACKTESTING"},
    "BACKTESTING": {"OPTIMIZING", "VALIDATION_FAILED", "BACKTEST_VALIDATED"},
    "OPTIMIZING": {"VALIDATION_FAILED", "BACKTEST_VALIDATED"},
    "VALIDATION_FAILED": {"PROTOTYPE"},
    "BACKTEST_VALIDATED": {"OOS_VALIDATED", "VALIDATION_FAILED"},
    "OOS_VALIDATED": {"FORWARD_TESTING"},
    "FORWARD_TESTING": {"FORWARD_FAILED", "PAPER_ELIGIBLE"},
    "FORWARD_FAILED": {"PROTOTYPE"},
    "PAPER_ELIGIBLE": {"LIVE_REVIEW_REQUIRED", "FORWARD_FAILED"},
    "LIVE_REVIEW_REQUIRED": {"LIVE_ELIGIBLE", "FORWARD_FAILED"},
    "LIVE_ELIGIBLE": set(),
    "RETIRED": set(),
}
for _source in STATUSES:
    if _source != "RETIRED":
        TRANSITIONS[_source] = TRANSITIONS[_source] | {"RETIRED"}

EVIDENCE_GATED = {"BACKTEST_VALIDATED", "OOS_VALIDATED", "FORWARD_TESTING", "PAPER_ELIGIBLE",
                  "LIVE_REVIEW_REQUIRED", "LIVE_ELIGIBLE"}
BACKTEST_VERDICTS = {"RESEARCH_CANDIDATE", "OOS_PASSED", "FORWARD_ELIGIBLE"}
OOS_VERDICTS = {"OOS_PASSED", "FORWARD_ELIGIBLE"}
# No realtime paper feed exists; gate D can therefore never pass.
REALTIME_PAPER_AVAILABLE = False
REFUSAL_REALTIME = "REALTIME_PAPER_FEED_UNAVAILABLE"

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
    name TEXT PRIMARY KEY, origin TEXT NOT NULL, description TEXT, spec_json TEXT,
    status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    last_verdict TEXT, last_ledger_id TEXT
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL REFERENCES strategies(name),
    from_status TEXT, to_status TEXT NOT NULL, reason TEXT, evidence_ref TEXT, evidence_json TEXT,
    actor TEXT, at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS history_no_update BEFORE UPDATE ON history
BEGIN SELECT RAISE(ABORT, 'lifecycle history is append-only: UPDATE not allowed'); END;
CREATE TRIGGER IF NOT EXISTS history_no_delete BEFORE DELETE ON history
BEGIN SELECT RAISE(ABORT, 'lifecycle history is append-only: DELETE not allowed'); END;
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


class Lifecycle:
    def __init__(self, path=None, *, min_forward_trades=30, min_forward_days=60):
        self.path = Path(path or DEFAULT_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.min_forward_trades = min_forward_trades
        self.min_forward_days = min_forward_days
        with closing(self._connect()) as db, db:
            db.executescript(SCHEMA)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def register(self, name, origin, spec=None, description=None) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("Een strategie heeft een naam nodig")
        if origin not in ORIGINS:
            raise ValueError(f"Onbekende herkomst '{origin}'; kies uit {', '.join(ORIGINS)}")
        now = _now()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM strategies WHERE name=?", (name,)).fetchone():
                raise ValueError(f"Strategie '{name}' bestaat al")
            db.execute("INSERT INTO strategies VALUES (?,?,?,?,?,?,?,NULL,NULL)",
                       (name, origin, description, None if spec is None else json.dumps(spec, default=str),
                        "IDEA", now, now))
            db.execute("INSERT INTO history (name, from_status, to_status, reason, actor, at) "
                       "VALUES (?,?,?,?,?,?)", (name, None, "IDEA", "Geregistreerd", "system", now))
        return self.get(name)

    def _check_evidence(self, to_status, evidence_ref, evidence):
        if to_status == "LIVE_REVIEW_REQUIRED":
            if not REALTIME_PAPER_AVAILABLE:
                raise ValueError(f"Geweigerd: {REFUSAL_REALTIME}. Er is geen realtime paper-feed; "
                                 "poort D (paper trading) kan niet worden gehaald.")
        if to_status not in EVIDENCE_GATED:
            return
        evidence = evidence or {}
        if not evidence_ref:
            raise ValueError(f"Overgang naar {to_status} vereist een evidence_ref (ledger- of registratie-id)")
        if not isinstance(evidence, dict) or not evidence:
            raise ValueError(f"Overgang naar {to_status} vereist bewijs (evidence)")
        verdict = evidence.get("verdict")
        if to_status == "BACKTEST_VALIDATED" and verdict not in BACKTEST_VERDICTS:
            raise ValueError("BACKTEST_VALIDATED vereist verdict RESEARCH_CANDIDATE, OOS_PASSED of FORWARD_ELIGIBLE")
        if to_status == "OOS_VALIDATED" and verdict not in OOS_VERDICTS:
            raise ValueError("OOS_VALIDATED vereist verdict OOS_PASSED of FORWARD_ELIGIBLE")
        if to_status == "FORWARD_TESTING":
            if verdict not in OOS_VERDICTS:
                raise ValueError("FORWARD_TESTING vereist verdict FORWARD_ELIGIBLE of OOS_PASSED")
            if not evidence.get("registration_id"):
                raise ValueError("FORWARD_TESTING vereist een bevroren registratie-id (registration_id)")
        if to_status == "PAPER_ELIGIBLE":
            trades, days = evidence.get("forward_trades"), evidence.get("forward_days")
            if not isinstance(trades, (int, float)) or trades < self.min_forward_trades:
                raise ValueError(f"PAPER_ELIGIBLE vereist minimaal {self.min_forward_trades} forward-trades")
            if not isinstance(days, (int, float)) or days < self.min_forward_days:
                raise ValueError(f"PAPER_ELIGIBLE vereist minimaal {self.min_forward_days} forward-dagen")
            if evidence.get("gate_c") != "PASS":
                raise ValueError("PAPER_ELIGIBLE vereist gate_c PASS (forward-poort)")
        if to_status == "LIVE_REVIEW_REQUIRED" and evidence.get("gate_d") != "PASS":
            raise ValueError("LIVE_REVIEW_REQUIRED vereist gate_d PASS (realtime paper)")
        if to_status == "LIVE_ELIGIBLE":
            if not str(evidence.get("human_reviewer") or "").strip():
                raise ValueError("LIVE_ELIGIBLE vereist een expliciete human_reviewer (naam)")
            if evidence.get("gate_e") != "PASS":
                raise ValueError("LIVE_ELIGIBLE vereist gate_e PASS (menselijke review)")

    def transition(self, name, to_status, reason, evidence_ref=None, evidence=None, actor="user") -> dict:
        if to_status not in STATUSES:
            raise ValueError(f"Onbekende status '{to_status}'")
        if not (reason or "").strip():
            raise ValueError("Een overgang vereist een reden")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM strategies WHERE name=?", (name,)).fetchone()
            if row is None:
                raise ValueError(f"Onbekende strategie '{name}'")
            current = row["status"]
            if to_status not in TRANSITIONS[current]:
                allowed = ", ".join(sorted(TRANSITIONS[current])) or "geen"
                raise ValueError(f"Ongeldige overgang {current} -> {to_status}; toegestaan: {allowed}")
            self._check_evidence(to_status, evidence_ref, evidence)
            now = _now()
            db.execute("UPDATE strategies SET status=?, updated_at=? WHERE name=?", (to_status, now, name))
            db.execute("INSERT INTO history (name, from_status, to_status, reason, evidence_ref, evidence_json, "
                       "actor, at) VALUES (?,?,?,?,?,?,?,?)",
                       (name, current, to_status, reason, evidence_ref,
                        None if evidence is None else json.dumps(evidence, default=str), actor, now))
        return self.get(name)

    def record_verdict(self, name, verdict, ledger_id):
        with closing(self._connect()) as db, db:
            cursor = db.execute("UPDATE strategies SET last_verdict=?, last_ledger_id=?, updated_at=? WHERE name=?",
                                (verdict, ledger_id, _now(), name))
            if cursor.rowcount == 0:
                raise ValueError(f"Onbekende strategie '{name}'")

    @staticmethod
    def _item(row):
        item = dict(row)
        item["spec"] = json.loads(item.pop("spec_json")) if item.get("spec_json") else None
        return item

    def get(self, name) -> dict | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM strategies WHERE name=?", (name,)).fetchone()
            if row is None:
                return None
            history = [dict(r) for r in db.execute("SELECT * FROM history WHERE name=? ORDER BY id", (name,))]
        for entry in history:
            text = entry.pop("evidence_json")
            entry["evidence"] = json.loads(text) if text else None
        return {**self._item(row), "history": history}

    def list(self) -> list[dict]:
        with closing(self._connect()) as db:
            return [self._item(r) for r in db.execute("SELECT * FROM strategies ORDER BY name")]

    def gates(self, name) -> list[dict]:
        strategy = self.get(name)
        if strategy is None:
            raise ValueError(f"Onbekende strategie '{name}'")
        status, history = strategy["status"], strategy["history"]
        reached = {h["to_status"]: h for h in history}

        def gate(key, label, state, text):
            return {"gate": key, "name": label, "status": state, "explanation_nl": text}

        failed_validation = status == "VALIDATION_FAILED"
        failed_forward = status == "FORWARD_FAILED"
        a = (gate("A", "Research", "PASS", "Backtest gevalideerd met onderbouwing in het ledger.")
             if "BACKTEST_VALIDATED" in reached else
             gate("A", "Research", "FAIL", "Validatie mislukt; zie geschiedenis voor de reden.")
             if failed_validation else
             gate("A", "Research", "INSUFFICIENT_EVIDENCE",
                  "Nog geen backtest-validatie met bewijs vastgelegd"
                  + (f" (laatste verdict: {strategy['last_verdict']})." if strategy["last_verdict"] else ".")))
        b = (gate("B", "Robustness", "PASS", "Out-of-sample en robuustheidstoetsen gehaald.")
             if "OOS_VALIDATED" in reached else
             gate("B", "Robustness", "FAIL", "Validatie mislukt voordat robuustheid is aangetoond.")
             if failed_validation else
             gate("B", "Robustness", "INSUFFICIENT_EVIDENCE", "Out-of-sample validatie ontbreekt."))
        paper = reached.get("PAPER_ELIGIBLE")
        if paper:
            proof = paper["evidence"] or {}
            c = gate("C", "Forward", "PASS",
                     f"Forward-test gehaald: {proof.get('forward_trades')} trades over {proof.get('forward_days')} dagen.")
        elif failed_forward:
            c = gate("C", "Forward", "FAIL", "Forward-test mislukt; zie geschiedenis voor de reden.")
        else:
            c = gate("C", "Forward", "INSUFFICIENT_EVIDENCE",
                     f"Minimaal {self.min_forward_trades} forward-trades en {self.min_forward_days} dagen vereist.")
        if "LIVE_REVIEW_REQUIRED" in reached:
            d = gate("D", "Paper", "PASS", "Realtime paper-validatie gehaald.")
        else:
            d = gate("D", "Paper", "BLOCKED",
                     f"{REFUSAL_REALTIME}: er is geen realtime paper-feed; deze poort kan niet worden gehaald.")
        if "LIVE_ELIGIBLE" in reached:
            e = gate("E", "Live review", "PASS",
                     f"Menselijke review afgerond door {(reached['LIVE_ELIGIBLE']['evidence'] or {}).get('human_reviewer')}. "
                     "Dit schakelt geen handel in.")
        else:
            e = gate("E", "Live review", "BLOCKED", "Wacht op poort D en een expliciete menselijke review.")
        return [a, b, c, d, e]
