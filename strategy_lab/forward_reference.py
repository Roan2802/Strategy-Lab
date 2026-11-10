"""Historical research references using the frozen delayed-forward model."""
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .daily_data import DailyPartitionStore, FIELDS
from .data_manager import DataError
from .forward_engine import ForwardEngine, MODEL, MODEL_SHA256, validate_registration
from .forward_performance import summarize_forward
from .forward_registry import ForwardRegistry, data_symbol


SCHEMA = """
CREATE TABLE IF NOT EXISTS forward_historical_references (
    portfolio_id TEXT NOT NULL, registration_id TEXT NOT NULL,
    start_day TEXT NOT NULL, end_day TEXT NOT NULL, summary TEXT NOT NULL,
    PRIMARY KEY (portfolio_id, registration_id, start_day, end_day)
)
"""


def _utc_start(day):
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


class _HistoricalStore(DailyPartitionStore):
    """Move the isolated replay clock through days with no recorded quotes."""

    def __init__(self, path):
        super().__init__(path)
        self.unobserved_days = set()

    def closure(self, symbol, day, *, as_of):
        if day in self.unobserved_days:
            return {"status": "HISTORICAL_NO_OBSERVATION", "observed_at": as_of.isoformat()}
        return super().closure(symbol, day, as_of=as_of)


def list_references(store, portfolio_id):
    with closing(store._connect()) as db, db:
        db.execute(SCHEMA)
        return [json.loads(row["summary"]) for row in db.execute(
            "SELECT summary FROM forward_historical_references WHERE portfolio_id=? ORDER BY rowid DESC",
            (portfolio_id,),
        )]


def create_reference(engine, portfolio_id, registration_id, start_day, end_day, datasets):
    days = (end_day - start_day).days + 1
    if not 1 <= days <= 31:
        raise DataError("Kies een historische periode van 1 t/m 31 UTC-dagen.")
    report = engine.report(portfolio_id)
    member = next((item for item in report["members"] if item["registration_id"] == registration_id), None)
    if member is None:
        raise DataError("Deze strategie hoort niet bij het gekozen forwardportfolio.")
    portfolio = report["portfolio"]
    store = engine.registry.data
    key = (portfolio_id, registration_id, start_day.isoformat(), end_day.isoformat())
    with closing(store._connect()) as db, db:
        db.execute(SCHEMA)
        existing = db.execute("SELECT summary FROM forward_historical_references "
                              "WHERE portfolio_id=? AND registration_id=? AND start_day=? AND end_day=?", key).fetchone()
    if existing is not None:
        return json.loads(existing["summary"])
    if portfolio["model_sha256"] != MODEL_SHA256:
        raise DataError("De bevroren engineversie is niet beschikbaar voor een vergelijkbare backtest.")

    registration = engine.registry.get(registration_id)
    config, _ = validate_registration(registration)
    if config["execution_model"] != MODEL:
        raise DataError("Een historische referentie is nog niet beschikbaar voor regelstrategieën.")
    if end_day >= datetime.fromisoformat(registration["frozen_at"]).date():
        raise DataError("De historische periode moet vóór de bevriezingsdag eindigen.")
    version = config["data_version"]
    if not isinstance(version, dict):
        raise DataError("De bevroren strategie bevat geen historische Dukascopy-bronhashes.")
    data = datasets.ensure(data_symbol(config["symbol"]))
    if (data.manifest["bid_source"]["sha256"] != version.get("legacy_bid_sha256")
            or data.manifest["ask_source"]["sha256"] != version.get("legacy_ask_sha256")):
        raise DataError("De bronbestanden komen niet overeen met de bevroren Dukascopy-versie.")

    frame = data.frame
    if frame.empty or start_day < frame.index[0].date() or end_day > frame.index[-1].date():
        raise DataError("De gekozen periode valt buiten de oorspronkelijke Dukascopy-historie.")
    start, stop = _utc_start(start_day), _utc_start(end_day + timedelta(days=1))
    window = frame.iloc[frame.index.searchsorted(start):frame.index.searchsorted(stop)]
    if window.empty:
        raise DataError("De gekozen periode bevat geen historische quotes.")

    replay = _replay_reference(registration, portfolio, window, start, stop, days)
    result = {
        **replay, "mode": "HISTORICAL_BACKTEST", "registration_id": registration_id,
        "start_day": key[2], "end_day": key[3], "snapshot_sha256": registration["snapshot_sha256"],
        "model_sha256": MODEL_SHA256, "recorded_at": datetime.now(timezone.utc).isoformat(),
        "bid_sha256": version["legacy_bid_sha256"], "ask_sha256": version["legacy_ask_sha256"],
        "allocation": member["allocation"], "minutes_without_quotes": days * 1440 - len(window),
        "first_quote_at": window.index[0].isoformat(), "last_quote_at": window.index[-1].isoformat(),
    }
    with closing(store._connect()) as db, db:
        db.execute("INSERT OR IGNORE INTO forward_historical_references VALUES (?, ?, ?, ?, ?)",
                   (*key, json.dumps(result, allow_nan=False)))
        row = db.execute("SELECT summary FROM forward_historical_references "
                         "WHERE portfolio_id=? AND registration_id=? AND start_day=? AND end_day=?", key).fetchone()
    return json.loads(row["summary"])


def _replay_reference(registration, portfolio, window, start, stop, days):
    with TemporaryDirectory(prefix="strategy-lab-reference-") as scratch:
        store = _HistoricalStore(Path(scratch) / "reference.sqlite")
        registry = ForwardRegistry(store)
        engine = ForwardEngine(registry)
        frozen = registry.freeze(registration["configuration"], registration["sources"], frozen_at=start)
        portfolio_id = engine.create("Historical research reference", currency=portfolio["currency"],
                                     starting_capital=portfolio["starting_capital"], risk_limit=portfolio["risk_limit"])
        engine.enroll(portfolio_id, frozen["id"], joined_at=start)
        symbol = data_symbol(registration["configuration"]["symbol"])
        groups = {day.date(): candles for day, candles in window.groupby(window.index.normalize())}
        partitions = {}
        for offset in range(days):
            day = start.date() + timedelta(days=offset)
            candles = groups.get(day)
            if candles is None:
                store.unobserved_days.add(day)
                continue
            sides = [candles[[f"{side}_{field}" for field in FIELDS]].set_axis(FIELDS, axis="columns")
                     for side in ("bid", "ask")]
            ingested = store.ingest(symbol, day, *sides, expected_minutes=candles.index,
                                    observed_at=_utc_start(day + timedelta(days=1)))
            if ingested["status"] != "UP TO DATE":
                raise DataError(f"Historische Bid/Ask-quotes onbruikbaar op {day}: "
                                f"{ingested.get('detail', ingested['status'])}")
            partitions[day.isoformat()] = ingested["sha256"]
        advanced = engine.advance(portfolio_id, as_of=stop, max_days=days)
        if len(advanced["committed_days"]) != days:
            raise DataError("Historische referentie kon niet volledig worden doorgerekend.")
        report = engine.report(portfolio_id)
        performance = summarize_forward(report, model_current=True, data=store)["members"][frozen["id"]]
        return {
            "metrics": performance, "partition_sha256": partitions, "quote_count": len(window),
            "days_without_quotes": sorted(day.isoformat() for day in store.unobserved_days),
            "open_position_at_end": bool(report["members"][0]["state"]["position"]),
        }
