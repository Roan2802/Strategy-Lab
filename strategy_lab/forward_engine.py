"""Persistent daily forward simulation for the audited LabBreakout quote rules."""
from __future__ import annotations

from contextlib import closing
from dataclasses import fields, replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from uuid import uuid4

from runtime.strategy_lab import execution, rule_engine
from runtime.strategy_lab.execution import ContractSpec

from . import forward_rules
from .daily_data import _utc
from .data_manager import DataError
from .forward_registry import ForwardRegistry, data_symbol
from .portfolio import FxRateUnavailable, fx_check, fx_quote_currency, load_fx, risk_group


MODEL = "LAB_BREAKOUT_BIDASK_M1_V1"
LAB_SOURCE = "LabBreakout/__init__.py"
LAB_SHA256 = "b5d77bbbb026cb4ac2a27999a4cf532eddc8d5bce8cac8ceb649482b2a8ee472"
MODEL_SHA256 = hashlib.sha256(Path(__file__).read_bytes() + Path(execution.__file__).read_bytes()
                              + Path(forward_rules.__file__).read_bytes()
                              + Path(rule_engine.__file__).read_bytes()).hexdigest()

EVIDENCE_CLASSES = {
    "HISTORICAL_BACKTEST": {"implemented": True, "prospective": False,
                            "note": "Frozen model replayed over old quotes (forward_reference); research reference only."},
    "HISTORICAL_OOS": {"implemented": True, "prospective": False,
                       "note": "One sealed final test per frozen candidate (research/protocol.py); data existed at freeze time."},
    "DAILY_DELAYED_FORWARD": {"implemented": True, "prospective": True,
                              "note": "Only candles published after registration; whole UTC days committed after publication."},
    "REALTIME_PAPER": {"implemented": False, "prospective": True,
                       "note": "NOT IMPLEMENTED: no live feed, no streaming ticks, no order routing of any kind."},
}
DEFAULT_LIMITS = {"max_daily_loss": None, "max_drawdown": None, "symbol_risk": None,
                  "max_jump": 0.2, "gap_minutes": 180, "group_risk": None, "max_positions": None}
_LIMIT_FIELDS = tuple(DEFAULT_LIMITS)

SCHEMA = """
CREATE TABLE IF NOT EXISTS forward_portfolios (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, currency TEXT NOT NULL,
    starting_capital REAL NOT NULL, balance REAL NOT NULL, risk_limit REAL NOT NULL,
    model_sha256 TEXT NOT NULL, last_day TEXT
);
CREATE TABLE IF NOT EXISTS forward_members (
    portfolio_id TEXT NOT NULL REFERENCES forward_portfolios(id),
    registration_id TEXT NOT NULL UNIQUE REFERENCES frozen_strategies(id),
    joined_at TEXT NOT NULL, allocation REAL NOT NULL, state TEXT NOT NULL,
    PRIMARY KEY (portfolio_id, registration_id)
);
CREATE TABLE IF NOT EXISTS forward_days (
    portfolio_id TEXT NOT NULL REFERENCES forward_portfolios(id),
    day TEXT NOT NULL, processed_at TEXT NOT NULL, publications TEXT NOT NULL,
    balance REAL NOT NULL, equity REAL NOT NULL,
    PRIMARY KEY (portfolio_id, day)
);
CREATE TABLE IF NOT EXISTS forward_events (
    id INTEGER PRIMARY KEY, portfolio_id TEXT NOT NULL REFERENCES forward_portfolios(id),
    registration_id TEXT NOT NULL REFERENCES frozen_strategies(id),
    day TEXT NOT NULL, candle_at TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL,
    FOREIGN KEY (portfolio_id, day) REFERENCES forward_days(portfolio_id, day)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX IF NOT EXISTS forward_event_lookup ON forward_events(portfolio_id, day, id);
CREATE TABLE IF NOT EXISTS forward_limits (
    portfolio_id TEXT PRIMARY KEY REFERENCES forward_portfolios(id),
    max_daily_loss REAL, max_drawdown REAL, symbol_risk REAL, max_jump REAL, gap_minutes REAL,
    group_risk REAL, max_positions REAL
);
CREATE TABLE IF NOT EXISTS forward_marks (
    portfolio_id TEXT NOT NULL, symbol TEXT NOT NULL, candle_at TEXT NOT NULL, bid_close REAL NOT NULL,
    PRIMARY KEY (portfolio_id, symbol)
);
CREATE TABLE IF NOT EXISTS forward_flags (
    portfolio_id TEXT NOT NULL, candle_at TEXT NOT NULL, symbol TEXT NOT NULL, kind TEXT NOT NULL,
    day TEXT NOT NULL, detail TEXT NOT NULL,
    PRIMARY KEY (portfolio_id, candle_at, symbol, kind)
);
CREATE TABLE IF NOT EXISTS forward_ledger (
    portfolio_id TEXT NOT NULL, day TEXT NOT NULL, n_events INTEGER NOT NULL, events_sha256 TEXT NOT NULL,
    prev_sha256 TEXT NOT NULL, chain_sha256 TEXT NOT NULL,
    PRIMARY KEY (portfolio_id, day)
);
CREATE TRIGGER IF NOT EXISTS forward_events_no_update BEFORE UPDATE ON forward_events
BEGIN SELECT RAISE(ABORT, 'forward events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_events_no_delete BEFORE DELETE ON forward_events
BEGIN SELECT RAISE(ABORT, 'forward events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_days_no_update BEFORE UPDATE ON forward_days
BEGIN SELECT RAISE(ABORT, 'forward days are append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_days_no_delete BEFORE DELETE ON forward_days
BEGIN SELECT RAISE(ABORT, 'forward days are append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_flags_no_update BEFORE UPDATE ON forward_flags
BEGIN SELECT RAISE(ABORT, 'forward flags are append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_flags_no_delete BEFORE DELETE ON forward_flags
BEGIN SELECT RAISE(ABORT, 'forward flags are append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_ledger_no_update BEFORE UPDATE ON forward_ledger
BEGIN SELECT RAISE(ABORT, 'forward ledger seals are append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_ledger_no_delete BEFORE DELETE ON forward_ledger
BEGIN SELECT RAISE(ABORT, 'forward ledger seals are append-only'); END;
"""


def _number(value, name, *, minimum=0.0, maximum=math.inf) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise DataError(f"{name} must be a finite number")
    if not minimum <= value <= maximum:
        raise DataError(f"{name} is outside the supported range")
    return float(value)


def _positive(value, name) -> float:
    return _number(value, name, minimum=1e-12)


def _integer(value, name, minimum, maximum) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise DataError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _stamp(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds")


def _sha(*parts) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def _ids(slot: int, symbol: str, detail: dict) -> dict:
    """Deterministic order/position identifiers, unique inside one portfolio ledger.

    ``slot`` is the member's enrollment order, so a crash replay or a rerun in another database reproduces
    the same identifiers (registration UUIDs are random and would differ)."""
    ids = {}
    if "signal_at" in detail and "side" in detail:
        ids["order_id"] = _sha("order", slot, symbol, detail["signal_at"], detail["side"])[:24]
        if "entry_at" in detail:
            ids["position_id"] = _sha("position", slot, symbol, detail["signal_at"], detail["side"],
                                      detail["entry_at"])[:24]
    return ids


FX_SIDE = "mid"


class FxPause(Exception):
    """A needed FX rate is missing: the day is not committed and nothing is filled."""


def validate_registration(registration: dict) -> tuple[dict, execution.ContractSpec]:
    config = registration["configuration"]
    if config.get("execution_model") == forward_rules.MODEL:
        return forward_rules.validate_registration(registration)
    source = registration["sources"].get(LAB_SOURCE, b"")
    if (config["strategy"] != "LabBreakout" or config["execution_model"] != MODEL
            or str(config["timeframe"]).upper() != "M1"
            or hashlib.sha256(source).hexdigest() != LAB_SHA256):
        raise DataError("The forward adapter requires the audited LabBreakout M1 snapshot")
    if config["indicators"] or config["news_filter"] != {"enabled": False}:
        raise DataError("This model uses only the configured EMA and requires the news filter disabled")
    params = config["parameters"]
    if set(params) != {"order_mode", "entry_interval", "position_size", "ema_period", "max_bar_range",
                       "stop_distance", "target_distance", "stop_offset", "pending_expiry_bars"}:
        raise DataError("Freeze all LabBreakout parameters explicitly")
    _integer(params["order_mode"], "order_mode", 0, 2)
    _integer(params["entry_interval"], "entry_interval", 1, 10_000)
    _integer(params["ema_period"], "ema_period", 2, 10_000)
    _integer(params["pending_expiry_bars"], "pending_expiry_bars", 2, 10_000)
    for key in ("position_size", "max_bar_range", "stop_distance", "target_distance", "stop_offset"):
        _positive(params[key], key)
    raw = config["contract"]
    try:
        contract = execution.ContractSpec(**{field.name: raw[field.name] for field in fields(execution.ContractSpec)})
    except (KeyError, TypeError) as exc:
        raise DataError("Freeze every field of the Bid/Ask contract explicitly") from exc
    if not isinstance(contract.account_currency, str) or not contract.account_currency.strip():
        raise DataError("The contract requires an account currency")
    for key in ("contract_size", "tick_size", "quote_to_account", "volume_step"):
        _positive(getattr(contract, key), key)
    for key in ("commission_per_lot_side", "slippage_points"):
        _number(getattr(contract, key), key)
    lots = params["position_size"] / contract.volume_step
    if abs(lots - round(lots)) > 1e-8:
        raise DataError("The position size is off the frozen lot step")
    _positive(config["starting_capital"], "allocation")
    _number(config["trade_risk"], "trade risk fraction", minimum=1e-12, maximum=1)
    _number(config["portfolio_risk_limit"], "portfolio risk fraction", minimum=1e-12, maximum=1)
    _number(config.get("leverage", 1), "leverage", minimum=1, maximum=1000)
    return config, contract


class ForwardEngine:
    def __init__(self, registry: ForwardRegistry, fx=None):
        self.registry = registry
        self._fx = fx
        with closing(self._connect()) as db, db:
            db.executescript(SCHEMA)
            have = {row[1] for row in db.execute("PRAGMA table_info(forward_limits)")}
            for column in _LIMIT_FIELDS:
                if column not in have:
                    db.execute(f"ALTER TABLE forward_limits ADD COLUMN {column} REAL")

    def fx_service(self):
        """Causal FX source (research/fx.py); None when it is not installed."""
        if self._fx is None:
            self._fx = load_fx()
        return self._fx

    def _fx_rate(self, member, timestamp) -> tuple[float, dict]:
        service = self.fx_service()
        quote, account = member["fx_quote"], member["base_contract"].account_currency.upper()
        if service is None:
            raise FxPause(f"no FX service for {quote}/{account}")
        try:
            conversion = service.rate(quote, account, timestamp, FX_SIDE)
        except FxRateUnavailable as exc:
            raise FxPause(f"{quote}/{account} at {_stamp(timestamp)}: {exc}") from exc
        rate = float(getattr(conversion, "rate", conversion))
        if not math.isfinite(rate) or rate <= 0:
            raise FxPause(f"{quote}/{account} at {_stamp(timestamp)}: invalid rate {rate!r}")
        return rate, {"fx_source": getattr(conversion, "source", None),
                      "fx_data_version": getattr(conversion, "data_version", None)}

    def _connect(self):
        db = sqlite3.connect(self.registry.data.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def create(self, name: str, *, currency: str, starting_capital: float, risk_limit: float) -> str:
        if not name.strip() or not currency.strip():
            raise DataError("A forward portfolio needs a name and currency")
        capital = _positive(starting_capital, "starting capital")
        limit = _number(risk_limit, "portfolio risk fraction", minimum=1e-12, maximum=1)
        identifier = str(uuid4())
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO forward_portfolios VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                       (identifier, name.strip(), currency.strip().upper(), capital, capital, limit, MODEL_SHA256))
        return identifier

    @staticmethod
    def _portfolio(db, identifier):
        portfolio = db.execute("SELECT * FROM forward_portfolios WHERE id=?", (identifier,)).fetchone()
        if portfolio is None:
            raise DataError("Unknown forward portfolio")
        return dict(portfolio)

    def enroll(self, portfolio_id: str, registration_id: str, *, joined_at: datetime | None = None) -> None:
        registration = self.registry.get(registration_id)
        config, contract = validate_registration(registration)
        fx = fx_check(config["symbol"], contract, fx_available=self.fx_service() is not None)
        if fx["status"] == "BLOCKED":
            raise DataError(f"Enrollment blocked: {fx['reason']}")
        joined = _stamp(joined_at or datetime.now(timezone.utc))
        if joined < registration["frozen_at"]:
            raise DataError("The enrollment cannot predate the frozen strategy")
        state = (forward_rules.initial_state(config, datetime.fromisoformat(joined))
                 if config["execution_model"] == forward_rules.MODEL else
                 {"index": -1, "ema": None, "pending": None, "position": None, "mark": None})
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            portfolio = self._portfolio(db, portfolio_id)
            allocated = db.execute("SELECT COALESCE(SUM(allocation),0) FROM forward_members WHERE portfolio_id=?",
                                   (portfolio_id,)).fetchone()[0]
            if contract.account_currency.upper() != portfolio["currency"]:
                raise DataError("Forward members must share the portfolio account currency")
            if portfolio["last_day"] and joined[:10] <= portfolio["last_day"]:
                raise DataError("Enrollment cannot change a committed portfolio day")
            if allocated + config["starting_capital"] > portfolio["starting_capital"]:
                raise DataError("Forward allocations exceed shared starting capital")
            try:
                db.execute("INSERT INTO forward_members VALUES (?, ?, ?, ?, ?)",
                           (portfolio_id, registration_id, joined, config["starting_capital"], _json(state)))
            except sqlite3.IntegrityError as exc:
                raise DataError("This registration already belongs to a forward portfolio") from exc

    def set_limits(self, portfolio_id: str, **limits) -> dict:
        """Portfolio-level guards. Fixed once the first day is committed so a run stays reproducible."""
        unknown = set(limits) - set(_LIMIT_FIELDS)
        if unknown:
            raise DataError(f"Unknown portfolio limits: {', '.join(sorted(unknown))}")
        clean = {}
        for key, value in limits.items():
            if value is None:
                clean[key] = None
            elif key == "gap_minutes":
                clean[key] = _number(value, key, minimum=1, maximum=10_000_000)
            elif key == "max_positions":
                clean[key] = _integer(value, key, 1, 10_000)
            else:
                clean[key] = _number(value, key, minimum=1e-12, maximum=1)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            portfolio = self._portfolio(db, portfolio_id)
            if portfolio["last_day"]:
                raise DataError("Portfolio limits cannot change after a day was committed")
            merged = {**self._limits(db, portfolio_id), **clean}
            db.execute(f"INSERT OR REPLACE INTO forward_limits(portfolio_id, {', '.join(_LIMIT_FIELDS)}) "
                       f"VALUES (?{', ?' * len(_LIMIT_FIELDS)})",
                       (portfolio_id, *(merged[key] for key in _LIMIT_FIELDS)))
        return merged

    @staticmethod
    def _limits(db, portfolio_id) -> dict:
        row = db.execute("SELECT * FROM forward_limits WHERE portfolio_id=?", (portfolio_id,)).fetchone()
        return {key: (row[key] if row else DEFAULT_LIMITS[key]) for key in _LIMIT_FIELDS}

    def advance(self, portfolio_id: str, *, as_of: datetime | None = None, max_days: int = 3) -> dict:
        cutoff = _utc(as_of or datetime.now(timezone.utc))
        _integer(max_days, "max_days", 1, 31)
        committed = []
        for _ in range(max_days):
            try:
                outcome = self._advance_day(portfolio_id, cutoff, committed)
            except FxPause as exc:
                return {"committed_days": committed, "status": "PAUSED", "reason": "FX_RATE_UNAVAILABLE",
                        "detail": str(exc)}
            if outcome:
                return outcome
        return {"committed_days": committed, "status": "BATCH_LIMIT"}

    def _advance_day(self, portfolio_id, cutoff, committed):
        """Commit one UTC day (one transaction). Returns a final status dict, or None after a committed day."""
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            portfolio = self._portfolio(db, portfolio_id)
            if portfolio["model_sha256"] != MODEL_SHA256:
                raise DataError("The frozen forward engine version differs from the loaded version")
            members = [dict(row) for row in db.execute(
                "SELECT * FROM forward_members WHERE portfolio_id=? ORDER BY joined_at, registration_id",
                (portfolio_id,),
            )]
            if not members:
                return {"committed_days": committed, "status": "NO_MEMBERS"}
            day = (date.fromisoformat(portfolio["last_day"]) + timedelta(days=1)
                   if portfolio["last_day"] else date.fromisoformat(members[0]["joined_at"][:10]))
            if day >= cutoff.date():
                return {"committed_days": committed, "status": "UP TO DATE"}
            active = [member for member in members if member["joined_at"][:10] <= day.isoformat()]
            for member in active:
                snapshot = self.registry.get(member["registration_id"])
                member["config"], member["contract"] = validate_registration(snapshot)
                member["state"] = json.loads(member["state"])
                member["base_contract"] = member["contract"]
                member["fx_quote"] = fx_quote_currency(member["config"]["symbol"], member["contract"].account_currency)
                if member["fx_quote"] and member["state"].get("fx_rate"):
                    member["contract"] = replace(member["contract"], quote_to_account=member["state"]["fx_rate"])
            publications, missing = self._load_day(active, day, cutoff)
            if missing:
                return {"committed_days": committed, "status": "WAITING_FOR_DATA",
                        "day": day.isoformat(), "missing_symbols": missing}
            self._commit_day(db, portfolio, active, day, cutoff, publications)
        committed.append(day.isoformat())
        return None

    def _load_day(self, members, day, cutoff):
        publications, missing = {}, []
        for symbol in sorted({member["config"]["symbol"] for member in members}):
            store_name = data_symbol(symbol)
            frame, manifests = self.registry.data.read(store_name, day, day, as_of=cutoff, first_publication=True)
            publication = manifests[0] if manifests else self.registry.data.closure(store_name, day, as_of=cutoff)
            if publication is None:
                missing.append(symbol)
            else:
                publications[symbol] = (frame, publication)
        return publications, missing

    def _commit_day(self, db, portfolio, members, day, cutoff, publications):
        limits = self._limits(db, portfolio["id"])
        history = [row[0] for row in db.execute(
            "SELECT equity FROM forward_days WHERE portfolio_id=? ORDER BY day", (portfolio["id"],))]
        day_start = history[-1] if history else portfolio["starting_capital"]
        peak = max([portfolio["starting_capital"], *history])
        marks = {row["symbol"]: (row["candle_at"], row["bid_close"]) for row in db.execute(
            "SELECT * FROM forward_marks WHERE portfolio_id=?", (portfolio["id"],))}
        halt = None

        def flag(timestamp, symbol, kind, **detail):
            db.execute("INSERT OR IGNORE INTO forward_flags VALUES (?, ?, ?, ?, ?, ?)",
                       (portfolio["id"], _stamp(timestamp), symbol, kind, day.isoformat(), _json(detail)))

        timeline = {}
        for member in members:
            frame, publication = publications[member["config"]["symbol"]]
            joined = datetime.fromisoformat(member["joined_at"])
            if datetime.fromisoformat(publication["observed_at"]) <= joined:
                continue
            for timestamp, values in frame[frame.index >= joined].iterrows():
                timeline.setdefault(timestamp, []).append((member, values.to_dict(), publication))
        for timestamp, candles in sorted(timeline.items()):
            positions = [member["state"]["position"] for member in members if member["state"]["position"]]
            # Reserve capital for the whole minute. Intrabar exits in one market
            # cannot finance another market's fill at that minute's open.
            equity_now = self._equity(portfolio, members)
            peak = max(peak, equity_now)
            if (halt is None and limits["max_daily_loss"]
                    and day_start - equity_now >= limits["max_daily_loss"] * day_start):
                halt = "DAILY_LOSS_LIMIT"
                flag(timestamp, "*", "DAILY_LOSS_HALT", day_start_equity=day_start, equity=equity_now)
            drawdown = bool(limits["max_drawdown"] and peak - equity_now >= limits["max_drawdown"] * peak)
            budget = {"equity": min(portfolio["balance"], equity_now),
                      "margin": sum(p["margin"] for p in positions), "risk": sum(p["risk"] for p in positions)}
            for member, candle, publication in candles:
                symbol = member["config"]["symbol"]
                slot = members.index(member)
                block = halt or ("MAX_DRAWDOWN" if drawdown else None)
                if (not block and limits["max_positions"]
                        and sum(1 for m in members if m["state"]["position"]) >= limits["max_positions"]):
                    block = "MAX_POSITIONS"
                if member["fx_quote"]:
                    rate, member["fx_meta"] = self._fx_rate(member, timestamp)
                    member["contract"] = replace(member["base_contract"], quote_to_account=rate)
                    member["state"]["fx_rate"] = rate
                previous = marks.get(symbol)
                if previous:
                    gap = (timestamp - datetime.fromisoformat(previous[0])).total_seconds() / 60
                    if limits["gap_minutes"] and gap > limits["gap_minutes"] + 1:
                        flag(timestamp, symbol, "DATA_GAP", gap_minutes=gap, previous_candle_at=previous[0])
                    move = abs(candle["bid_open"] / previous[1] - 1) if previous[1] > 0 else math.inf
                    if limits["max_jump"] and move > limits["max_jump"]:
                        flag(timestamp, symbol, "PRICE_JUMP", move=move, previous_bid_close=previous[1],
                             bid_open=candle["bid_open"])
                        block = block or "PRICE_JUMP"
                marks[symbol] = (_stamp(timestamp), candle["bid_close"])
                view, extra, cap = dict(budget), 0.0, None
                if block:
                    view["equity"] = 0.0
                elif limits["symbol_risk"] or limits["group_risk"]:
                    total_room = min(portfolio["risk_limit"], member["config"]["portfolio_risk_limit"]) \
                        * budget["equity"] - budget["risk"]
                    rooms = []
                    if limits["symbol_risk"]:
                        rooms.append((limits["symbol_risk"] * budget["equity"] - sum(
                            m["state"]["position"]["risk"] for m in members
                            if m["config"]["symbol"] == symbol and m["state"]["position"]), "SYMBOL_RISK_LIMIT"))
                    if limits["group_risk"]:
                        group = risk_group(symbol)
                        rooms.append((limits["group_risk"] * budget["equity"] - sum(
                            m["state"]["position"]["risk"] for m in members
                            if risk_group(m["config"]["symbol"]) == group and m["state"]["position"]),
                            "GROUP_RISK_LIMIT"))
                    room, cap = min(rooms)
                    extra = max(0.0, total_room - room)
                    view["risk"] += extra
                before = dict(view)

                def emit(kind, *, _member=member, _slot=slot, _pub=publication, _block=block, _extra=extra,
                         _cap=cap, _before=before, _at=timestamp, **detail):
                    if kind == "ORDER_REJECTED" and _block and detail.get("reason") == "SHARED_MARGIN":
                        detail = {**detail, "reason": "PORTFOLIO_HALT", "halt": _block}
                    elif (kind == "ORDER_REJECTED" and _extra and detail.get("reason") == "RISK_LIMIT"
                          and detail["risk"] + _before["risk"] - _extra
                          <= min(portfolio["risk_limit"], _member["config"]["portfolio_risk_limit"])
                          * _before["equity"]):
                        detail = {**detail, "reason": _cap}
                    if kind == "ORDER_REJECTED":
                        detail = {**detail, "priority_rank": _slot + 1}
                    if _member["fx_quote"]:
                        detail = {**detail, "fx_quote": _member["fx_quote"], "fx_side": FX_SIDE,
                                  "fx_rate": _member["contract"].quote_to_account, **_member["fx_meta"]}
                    record = {"quote_observed_at": _pub["observed_at"], "received_at": _pub["observed_at"],
                              "partition_sha256": _pub["sha256"], **detail,
                              **_ids(_slot, symbol, detail)}
                    db.execute("""INSERT INTO forward_events(portfolio_id, registration_id, day, candle_at, kind, detail)
                                  VALUES (?, ?, ?, ?, ?, ?)""",
                               (portfolio["id"], _member["registration_id"], day.isoformat(), _stamp(_at),
                                kind, _json(record)))

                step = forward_rules.step if member["config"]["execution_model"] == forward_rules.MODEL else _step
                step(portfolio, member, candle, _stamp(timestamp), view, emit)
                budget["margin"] += view["margin"] - before["margin"]
                budget["risk"] += view["risk"] - before["risk"]
        for member in members:
            db.execute("UPDATE forward_members SET state=? WHERE registration_id=?",
                       (_json(member["state"]), member["registration_id"]))
        for symbol, (candle_at, bid_close) in marks.items():
            db.execute("INSERT OR REPLACE INTO forward_marks VALUES (?, ?, ?, ?)",
                       (portfolio["id"], symbol, candle_at, bid_close))
        equity = self._equity(portfolio, members)
        db.execute("INSERT INTO forward_days VALUES (?, ?, ?, ?, ?, ?)",
                   (portfolio["id"], day.isoformat(), _stamp(cutoff),
                    _json({symbol: pair[1] for symbol, pair in publications.items()}),
                    portfolio["balance"], equity))
        self._seal_day(db, portfolio["id"], day.isoformat(), portfolio["balance"], equity)
        db.execute("UPDATE forward_portfolios SET balance=?, last_day=? WHERE id=?",
                   (portfolio["balance"], day.isoformat(), portfolio["id"]))

    @staticmethod
    def _day_digest(db, portfolio_id, day) -> tuple[int, str]:
        rows = [(row["id"], row["registration_id"], row["candle_at"], row["kind"], row["detail"])
                for row in db.execute("SELECT * FROM forward_events WHERE portfolio_id=? AND day=? ORDER BY id",
                                      (portfolio_id, day))]
        flags = [tuple(row) for row in db.execute(
            "SELECT candle_at, symbol, kind, detail FROM forward_flags WHERE portfolio_id=? AND day=? "
            "ORDER BY candle_at, symbol, kind", (portfolio_id, day))]
        return len(rows), _sha(_json(rows), _json(flags))

    def _seal_day(self, db, portfolio_id, day, balance, equity) -> None:
        """Hash-chain the committed day so any later edit of an earlier event is detectable."""
        count, digest = self._day_digest(db, portfolio_id, day)
        row = db.execute("SELECT chain_sha256 FROM forward_ledger WHERE portfolio_id=? ORDER BY day DESC LIMIT 1",
                         (portfolio_id,)).fetchone()
        previous = row[0] if row else "GENESIS"
        db.execute("INSERT INTO forward_ledger VALUES (?, ?, ?, ?, ?, ?)",
                   (portfolio_id, day, count, digest, previous, _sha(previous, day, digest, balance, equity)))

    def verify_ledger(self, portfolio_id: str) -> dict:
        """Recompute every day seal; report the first day whose rows or chain no longer match."""
        with closing(self._connect()) as db:
            self._portfolio(db, portfolio_id)
            days = {row["day"]: row for row in db.execute(
                "SELECT * FROM forward_days WHERE portfolio_id=? ORDER BY day", (portfolio_id,))}
            seals = {row["day"]: row for row in db.execute(
                "SELECT * FROM forward_ledger WHERE portfolio_id=? ORDER BY day", (portfolio_id,))}
            previous, sealed, unsealed = "GENESIS", 0, []
            for day, record in days.items():
                seal = seals.get(day)
                if seal is None:
                    unsealed.append(day)
                    continue
                count, digest = self._day_digest(db, portfolio_id, day)
                chain = _sha(seal["prev_sha256"], day, digest, record["balance"], record["equity"])
                if (count != seal["n_events"] or digest != seal["events_sha256"]
                        or seal["prev_sha256"] != previous or chain != seal["chain_sha256"]):
                    return {"status": "BROKEN", "first_bad_day": day, "sealed_days": sealed,
                            "unsealed_days": unsealed}
                previous, sealed = seal["chain_sha256"], sealed + 1
            orphan = {row[0] for row in db.execute(
                "SELECT DISTINCT day FROM forward_events WHERE portfolio_id=?", (portfolio_id,))} - set(days)
            if orphan:
                return {"status": "BROKEN", "first_bad_day": min(orphan), "sealed_days": sealed,
                        "unsealed_days": unsealed, "reason": "EVENTS_WITHOUT_DAY"}
        return {"status": "OK" if not unsealed else "LEGACY_UNSEALED_DAYS", "sealed_days": sealed,
                "unsealed_days": unsealed, "head_sha256": previous}

    @staticmethod
    def _equity(portfolio, members):
        equity = portfolio["balance"]
        for member in members:
            state = member["state"]
            position = state["position"]
            if position and state["mark"] is not None:
                equity += execution.position_pnl(
                    side=position["side"], quantity_lots=position["quantity"], entry=position["entry"],
                    exit=state["mark"], contract=member["contract"],
                )["net_pnl"]
        return equity

    def report(self, portfolio_id: str) -> dict:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN")
            portfolio = self._portfolio(db, portfolio_id)
            days = [dict(row) for row in db.execute(
                "SELECT * FROM forward_days WHERE portfolio_id=? ORDER BY day", (portfolio_id,))]
            members = [dict(row) for row in db.execute("""
                SELECT m.*, f.frozen_at, f.source_sha256, f.snapshot_sha256, f.configuration
                FROM forward_members m JOIN frozen_strategies f ON f.id=m.registration_id
                WHERE m.portfolio_id=? ORDER BY m.joined_at, m.registration_id
            """, (portfolio_id,))]
            events = [dict(row) for row in db.execute(
                "SELECT * FROM forward_events WHERE portfolio_id=? ORDER BY id", (portfolio_id,))]
            flags = [dict(row) for row in db.execute(
                "SELECT * FROM forward_flags WHERE portfolio_id=? ORDER BY candle_at, symbol, kind", (portfolio_id,))]
            limits = self._limits(db, portfolio_id)
        for flag in flags:
            flag["detail"] = json.loads(flag["detail"])
        for day in days:
            day["publications"] = json.loads(day["publications"])
        for member in members:
            member["state"] = json.loads(member["state"])
            member["configuration"] = json.loads(member["configuration"])
            member["fx"] = fx_check(member["configuration"]["symbol"],
                                    ContractSpec(**member["configuration"]["contract"]),
                                    fx_available=self.fx_service() is not None)
        for event in events:
            event["detail"] = json.loads(event["detail"])
        return {"mode": "DAILY_DELAYED_FORWARD", "evidence_classes": EVIDENCE_CLASSES, "portfolio": portfolio,
                "days": days, "members": members, "events": events, "flags": flags, "limits": limits}

    def portfolios(self) -> list[dict]:
        with closing(self._connect()) as db:
            return [dict(row) for row in db.execute("""
                SELECT p.*, (SELECT COUNT(*) FROM forward_members m WHERE m.portfolio_id=p.id) AS members
                FROM forward_portfolios p ORDER BY p.name, p.id
            """)]


def _entry(order, candle, contract):
    side = order["side"]
    if order["type"] == "MARKET":
        return execution.fill_market(side, {"open": candle["bid_open"]}, {"open": candle["ask_open"]},
                                     contract.slippage_points, contract.tick_size)
    price = order["planned_price"]
    if side == "long" and candle["ask_high"] >= price:
        return max(candle["ask_open"], price) + contract.slippage_points * contract.tick_size
    if side == "short" and candle["bid_low"] <= price:
        return min(candle["bid_open"], price) - contract.slippage_points * contract.tick_size
    return None


def _open_position(portfolio, member, order, price, candle, stamp, budget, emit):
    config, contract = member["config"], member["contract"]
    params, state = config["parameters"], member["state"]
    quantity = params["position_size"]
    multiplier = quantity * contract.contract_size * contract.quote_to_account
    margin = price * multiplier / config.get("leverage", 1)
    slippage = contract.slippage_points * contract.tick_size
    risk = (params["stop_distance"] + slippage) * multiplier + 2 * quantity * contract.commission_per_lot_side
    member_equity = min(member["allocation"] + state.get("realized_pnl", 0.0), budget["equity"])
    if price <= 0 or margin > member_equity or margin + budget["margin"] > budget["equity"]:
        emit("ORDER_REJECTED", reason="SHARED_MARGIN", margin=margin, **order)
        return None
    portfolio_limit = min(portfolio["risk_limit"], config["portfolio_risk_limit"])
    if risk > member_equity * config["trade_risk"] or risk + budget["risk"] > budget["equity"] * portfolio_limit:
        emit("ORDER_REJECTED", reason="RISK_LIMIT", risk=risk, **order)
        return None
    direction = 1 if order["side"] == "long" else -1
    stop = price - direction * params["stop_distance"]
    target = price + direction * params["target_distance"]
    if min(stop, target) <= 0:
        emit("ORDER_REJECTED", reason="INVALID_PROTECTIVE_PRICE", **order)
        return None
    position = {"side": order["side"], "signal_at": order["signal_at"], "entry_at": stamp,
                "entry": price, "quantity": quantity, "stop": stop, "target": target,
                "margin": margin, "risk": risk}
    budget["margin"] += margin
    budget["risk"] += risk
    emit("ORDER_FILLED", **position, order_type=order["type"], slippage_points=contract.slippage_points,
         quote="ASK" if direction == 1 else "BID", spread_open=candle["ask_open"] - candle["bid_open"],
         fill_assumption="NEXT_OPEN" if order["type"] == "MARKET" else "QUOTE_SIDE_STOP_OR_WORSE_OPEN")
    return position


def _close_position(portfolio, member, candle, stamp, emit):
    state, contract = member["state"], member["contract"]
    position = state["position"]
    if position is None:
        return False
    side = position["side"]
    bid = {field: candle[f"bid_{field}"] for field in ("open", "high", "low")}
    ask = {field: candle[f"ask_{field}"] for field in ("open", "high", "low")}
    exit_result = execution.conservative_ohlc_exit(side=side, bid=bid, ask=ask,
                                                   stop=position["stop"], target=position["target"])
    if not exit_result:
        return False
    slip = contract.slippage_points * contract.tick_size
    price = exit_result["price"] + (-slip if side == "long" else slip)
    pnl = execution.position_pnl(side=side, quantity_lots=position["quantity"], entry=position["entry"],
                                 exit=price, contract=contract)
    portfolio["balance"] += pnl["net_pnl"]
    state["realized_pnl"] = state.get("realized_pnl", 0.0) + pnl["net_pnl"]
    state["position"] = None
    emit("POSITION_CLOSED", **position, exit_at=stamp, exit=price, exit_reason=exit_result["reason"],
         ambiguous=exit_result["ambiguous"], quote="BID" if side == "long" else "ASK",
         spread_open=candle["ask_open"] - candle["bid_open"], fill_assumption="SL_BEFORE_TP_IF_BOTH",
         **pnl)
    return True


def _step(portfolio, member, candle, stamp, budget, emit):
    config, contract, state = member["config"], member["contract"], member["state"]
    params = config["parameters"]
    state["index"] += 1
    state["last_candle_at"] = stamp
    mode = params["order_mode"]
    quote = "bid" if mode == 2 else "ask"
    close = candle[f"{quote}_close"]
    previous = state["ema"]
    state["ema"] = close if previous is None else previous + 2 / (params["ema_period"] + 1) * (close - previous)

    closed = _close_position(portfolio, member, candle, stamp, emit)
    order = state["pending"]
    if order and state["index"] - order["submitted_index"] >= params["pending_expiry_bars"]:
        emit("ORDER_CANCELLED", reason="PENDING_EXPIRY", **order)
        state["pending"] = order = None
    if order and not closed:
        price = _entry(order, candle, contract)
        if price is not None:
            state["pending"] = None
            state["position"] = _open_position(portfolio, member, order, price, candle, stamp, budget, emit)
            closed = _close_position(portfolio, member, candle, stamp, emit)
    position = state["position"]
    state["mark"] = candle["bid_close" if position and position["side"] == "long" else "ask_close"] if position else None
    decision, reason, signal_quote, _ = execution._lab_breakout_signal(
        candle, ema_value=state["ema"] if state["index"] + 1 >= params["ema_period"] else None,
        parameters=params, index=state["index"], available=not (closed or position or state["pending"]),
    )
    if decision == "PASS":
        signal_at = _stamp(datetime.fromisoformat(stamp) + timedelta(minutes=1))
        planned = (candle["bid_low"] - params["stop_offset"] if mode == 2 else
                   candle["ask_high"] + params["stop_offset"] if mode == 1 else None)
        if planned is not None and planned <= 0:
            emit("SIGNAL_REJECTED", reason="INVALID_STOP_PRICE", signal_at=signal_at)
            return
        state["pending"] = {"type": "STOP" if mode else "MARKET", "side": "short" if mode == 2 else "long",
                            "planned_price": planned, "submitted_index": state["index"], "signal_at": signal_at}
        emit("ORDER_SUBMITTED", **state["pending"], reason=reason, quote=signal_quote)
