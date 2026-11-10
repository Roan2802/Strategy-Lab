"""Strict time protocol for the Strategy Discovery Factory.

Periods (defaults, per symbol adjusted to the real data, never invented):

* development  2004-01-01 .. 2018-01-01  - generation, quick tests, full dev backtests, parameter optimisation
* validation   2018-01-01 .. 2024-01-01  - rolling/expanding walk-forward, robustness, model selection
* sealed       2024-01-01 .. last fully complete day - ONE final test per frozen candidate, via ``final_test`` only

``dev`` = development + validation. Search/optimisation code only ever receives :class:`SearchData`, which physically
holds no bar at or after the sealed start and raises :class:`SealedAccessError` for any request that reaches it.
Sealed bars are loaded exclusively inside :meth:`SealedVault.final_test`, which requires an immutable, append-only
frozen record and writes every access (also refusals) to an append-only audit log.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from runtime.strategy_lab import rule_engine as engine

from . import backtest, validation

DEV_START = "2004-01-01"
DEVELOPMENT_END = "2018-01-01"
SEALED_START = "2024-01-01"
MIN_DEVELOPMENT_DAYS = 2 * 365
MIN_VALIDATION_DAYS = 3 * 365
MIN_SEALED_DAYS = 180
EMBARGO_DAYS = 2
MIN_WARMUP_DAYS = 30
RELIABLE_RUN_DAYS = 5
EVIDENCE_CLASSES = ("HISTORICAL_OOS", "REUSED_HOLDOUT", "PROSPECTIVE_FORWARD")
_BAR_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
PROTOCOL_VERSION = "FACTORY_PROTOCOL_V1"


class SealedAccessError(PermissionError):
    """Raised when search/optimisation code asks for any bar at or after the sealed start."""


class SealedReuseError(PermissionError):
    """Raised when a candidate (or its signature/parent/family lineage) already had a final test."""


def utc(value) -> pd.Timestamp:
    t = pd.Timestamp(value)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _day(ts) -> str:
    return utc(ts).strftime("%Y-%m-%d")


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha(obj) -> str:
    return hashlib.sha256(canon(obj).encode()).hexdigest()


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ------------------------------------------------------------------------------------------------ availability
def data_availability(bid: pd.DataFrame, ask: pd.DataFrame, timeframe: str, *, today=None) -> dict:
    """Measured facts about one symbol/timeframe: first reliable candle, last complete day, gaps, spreads, Bid/Ask
    presence. Nothing is filled in: a day without candles is a gap, not an interpolated day."""
    out = {"timeframe": timeframe, "rows": int(len(bid)), "first_candle": None, "first_reliable": None,
           "last_candle": None, "last_complete_day": None, "bid_present": bool(len(bid)),
           "ask_present": bool(len(ask)), "missing_ask_fraction": 1.0, "gaps": {}, "spread": {}, "notes_nl": []}
    if bid.empty:
        out["notes_nl"].append("Geen Bid-candles beschikbaar.")
        return out
    idx = bid.index
    out["first_candle"], out["last_candle"] = idx[0].isoformat(), idx[-1].isoformat()
    per_day = pd.Series(1, index=idx).groupby(idx.tz_convert("UTC").normalize()).sum()
    weekdays = per_day[per_day.index.dayofweek < 5]
    med = float(weekdays.median()) if len(weekdays) else float(per_day.median())
    # first reliable: start of the first run of RELIABLE_RUN_DAYS weekdays each with >= 50% of the median bar count
    good = (weekdays >= 0.5 * med).to_numpy()
    first_rel = None
    for i in range(0, max(0, len(good) - RELIABLE_RUN_DAYS + 1)):
        if good[i:i + RELIABLE_RUN_DAYS].all():
            first_rel = weekdays.index[i]
            break
    out["first_reliable"] = (first_rel or per_day.index[0]).isoformat()
    # last complete day: the last day must look like its predecessors (>= 80% of the median of the previous 20 days)
    # and must be strictly before "today" (UTC) - the running day is never complete.
    today_ts = utc(today or datetime.now(timezone.utc)).normalize()
    days = per_day[per_day.index < today_ts]
    last_complete = None
    if len(days):
        last_day = days.index[-1]
        ref = days.iloc[-21:-1]
        ref = ref[ref.index.dayofweek < 5] if len(ref[ref.index.dayofweek < 5]) else ref
        if len(ref) and days.iloc[-1] < 0.8 * float(ref.median()) and last_day.dayofweek < 5:
            out["notes_nl"].append(f"Laatste dag {last_day.date()} is onvolledig ({int(days.iloc[-1])} candles) en "
                                   "telt niet mee.")
            last_day = days.index[-2] if len(days) > 1 else None
        last_complete = last_day
    out["last_complete_day"] = last_complete.strftime("%Y-%m-%d") if last_complete is not None else None
    # gaps: weekdays between first and last candle without any candle
    all_wd = pd.bdate_range(per_day.index[0].tz_localize(None), per_day.index[-1].tz_localize(None))
    have = set(per_day.index.tz_localize(None))
    missing = [d for d in all_wd if d not in have]
    big = []
    run_start, prev = None, None
    for d in missing:
        if prev is not None and (d - prev).days <= 3:
            prev = d
            continue
        if run_start is not None and (prev - run_start).days >= 4:
            big.append([run_start.strftime("%Y-%m-%d"), prev.strftime("%Y-%m-%d")])
        run_start = prev = d
    if run_start is not None and (prev - run_start).days >= 4:
        big.append([run_start.strftime("%Y-%m-%d"), prev.strftime("%Y-%m-%d")])
    out["gaps"] = {"missing_weekdays": len(missing), "weekdays": len(all_wd),
                   "missing_weekday_fraction": round(len(missing) / max(len(all_wd), 1), 5),
                   "multi_day_gaps": big[:50], "median_bars_per_weekday": med,
                   "definition_nl": "Werkdag zonder enkele candle (ook feestdagen tellen mee; niet opgevuld)."}
    ask_close = ask["close"].reindex(idx) if len(ask) else pd.Series(np.nan, index=idx)
    out["missing_ask_fraction"] = float(ask_close.isna().mean())
    spread = (ask_close - bid["close"]).astype(float)
    rel = (spread / bid["close"].astype(float)).replace([np.inf, -np.inf], np.nan).dropna()
    if len(rel):
        by_year = rel.groupby(rel.index.year).median()
        out["spread"] = {"median_rel": float(rel.median()), "p95_rel": float(rel.quantile(0.95)),
                         "median_abs": float(spread.dropna().median()),
                         "non_positive_fraction": float((spread.dropna() <= 0).mean()),
                         "median_rel_by_year": {str(k): float(v) for k, v in by_year.items()}}
    return out


def _clip_range(start, end, lo, hi):
    s, e = max(utc(start), utc(lo)), min(utc(end), utc(hi))
    return (s, e) if e > s else (s, s)


def build_ranges(avail: dict, *, dev_start: str = DEV_START, development_end: str = DEVELOPMENT_END,
                 sealed_start: str = SEALED_START, warmup_days: int = MIN_WARMUP_DAYS) -> dict:
    """Per-symbol ranges from the protocol defaults and the measured availability. Adjustments are listed in
    ``notes_nl``; too little support gives status INSUFFICIENT_SUPPORT (never invented candles)."""
    notes, status = [], "OK"
    if not avail.get("first_reliable") or not avail.get("last_complete_day"):
        return {"status": "INSUFFICIENT_SUPPORT", "development": None, "validation": None, "dev": None,
                "sealed": None, "notes_nl": ["Geen bruikbare candles."], "embargo_days": EMBARGO_DAYS}
    first = utc(avail["first_reliable"]) + pd.Timedelta(days=warmup_days)
    last_excl = utc(avail["last_complete_day"]) + pd.Timedelta(days=1)
    d0 = max(utc(dev_start), first.ceil("D"))
    if d0 > utc(dev_start):
        notes.append(f"Ontwikkelperiode start op {_day(d0)} i.p.v. {dev_start}: eerste betrouwbare candle "
                     f"{avail['first_reliable'][:10]} + {warmup_days} dagen warm-up.")
    dev_end = utc(development_end)
    seal = utc(sealed_start)
    development = (d0, min(dev_end, last_excl))
    validation_r = (max(dev_end, d0), min(seal, last_excl))
    sealed = (seal, last_excl) if last_excl > seal else (seal, seal)
    dev_days = (development[1] - development[0]).days
    val_days = (validation_r[1] - validation_r[0]).days
    seal_days = (sealed[1] - sealed[0]).days
    if dev_days < MIN_DEVELOPMENT_DAYS:
        status = "INSUFFICIENT_SUPPORT"
        notes.append(f"Ontwikkelperiode {max(dev_days, 0)} dagen < {MIN_DEVELOPMENT_DAYS}.")
    if val_days < MIN_VALIDATION_DAYS:
        status = "INSUFFICIENT_SUPPORT"
        notes.append(f"Validatieperiode {max(val_days, 0)} dagen < {MIN_VALIDATION_DAYS}.")
    sealed_status = "OK"
    if seal_days < MIN_SEALED_DAYS:
        sealed_status = "INSUFFICIENT_SUPPORT"
        notes.append(f"Verzegelde periode {max(seal_days, 0)} dagen < {MIN_SEALED_DAYS}: geen eindtest mogelijk.")
    if status == "OK" and notes:
        status = "ADJUSTED"
    fmt = lambda r: {"start": _day(r[0]), "end": _day(r[1]), "days": max((r[1] - r[0]).days, 0)}
    return {"status": status, "sealed_status": sealed_status, "development": fmt(development),
            "validation": fmt(validation_r), "dev": fmt((development[0], validation_r[1])), "sealed": fmt(sealed),
            "sealed_end_rule_nl": "t/m de laatste volledig complete dag (de lopende dag telt nooit mee)",
            "embargo_days": EMBARGO_DAYS, "warmup_days_min": warmup_days, "notes_nl": notes,
            "protocol_version": PROTOCOL_VERSION}


def warmup_days_for(spec: dict, timeframe: str, params: dict | None = None) -> int:
    """Calendar days of causal warm-up needed by the spec's longest look-back (x2 for weekends), min 30."""
    try:
        bars = engine.lookback_bars(engine.resolve(spec, params))
    except Exception:
        bars = 500
    minutes = bars * _BAR_MINUTES.get(timeframe, 60)
    return int(max(MIN_WARMUP_DAYS, math.ceil(minutes / 1440 * 2) + 2))


# ------------------------------------------------------------------------------------------------ data guard
def assert_search_range(start, end, sealed_start) -> None:
    s, e, seal = utc(start), utc(end), utc(sealed_start)
    if s >= seal or e > seal:
        raise SealedAccessError(f"verzegelde periode geweigerd: gevraagd {_day(s)}..{_day(e)}, verzegeld vanaf "
                                f"{_day(seal)}; alleen final_test() mag die bars lezen")


def frames_to_file(bid: pd.DataFrame, ask: pd.DataFrame, path) -> str:
    """One parquet with bid_*/ask_* columns and a time_utc column; returns its sha256 (the dataset id)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = bid.add_prefix("bid_").join(ask.add_prefix("ask_"), how="left")
    frame.index.name = "time_utc"
    tmp = path.with_suffix(".tmp")
    frame.reset_index().to_parquet(tmp, index=False, compression="zstd")
    tmp.replace(path)
    return file_sha256(path)


def _split(frame: pd.DataFrame):
    frame = frame.set_index("time_utc")
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    bid = frame[[c for c in frame.columns if c.startswith("bid_")]].copy()
    ask = frame[[c for c in frame.columns if c.startswith("ask_")]].copy()
    bid.columns = [c[4:] for c in bid.columns]
    ask.columns = [c[4:] for c in ask.columns]
    ask = ask.dropna(how="all")
    return bid, ask


def load_search_frames(path, sealed_start):
    """Bars strictly before the sealed start; the sealed bars are filtered out at read time."""
    seal = utc(sealed_start)
    frame = pd.read_parquet(path, filters=[("time_utc", "<", seal)])
    bid, ask = _split(frame)
    return bid[bid.index < seal], ask[ask.index < seal]


class SearchData:
    """The only data object search/optimisation stages receive. Holds no bar >= sealed_start and refuses requests
    that reach it. ``backtest`` adds a causal warm-up prefix before ``start``; only trades opened in [start, end)
    count, and positions still open at ``end`` (closed by END_OF_DATA) are flagged and dropped by default."""

    def __init__(self, bid, ask, sealed_start, *, symbol="", timeframe="", dataset_sha256=None):
        self.sealed_start = utc(sealed_start)
        self.bid = bid[bid.index < self.sealed_start]
        self.ask = ask[ask.index < self.sealed_start]
        self.symbol, self.timeframe, self.dataset_sha256 = symbol, timeframe, dataset_sha256
        self.calls = 0

    @classmethod
    def from_file(cls, path, sealed_start, **kw):
        bid, ask = load_search_frames(path, sealed_start)
        return cls(bid, ask, sealed_start, **kw)

    def slice(self, start, end, warmup_days=0):
        assert_search_range(start, end, self.sealed_start)
        s, e = utc(start) - pd.Timedelta(days=warmup_days), utc(end)
        b = self.bid.iloc[self.bid.index.searchsorted(s):self.bid.index.searchsorted(e)]
        a = self.ask.iloc[self.ask.index.searchsorted(s):self.ask.index.searchsorted(e)]
        return b, a

    def backtest(self, spec, params, start, end, *, capital=10000.0, commission_per_lot=0.0, warmup_days=None,
                 drop_boundary=True, **stress) -> dict:
        assert_search_range(start, end, self.sealed_start)
        self.calls += 1
        wd = warmup_days if warmup_days is not None else warmup_days_for(spec, self.timeframe, params)
        b, a = self.slice(start, end, wd)
        r = run_window(spec, params, b, a, start, capital=capital, commission_per_lot=commission_per_lot, **stress)
        return mark_boundary(r, drop_boundary)


def run_window(spec, params, bid, ask, trade_from, **kw) -> dict:
    if len(bid) < 3:
        return {"trades": [], "equity": [], "metrics": {}, "params": params or {}, "diagnostics": {"bars": len(bid)}}
    return backtest.run_rules_backtest(spec, bid, ask, params, trade_from=utc(trade_from), **kw)


def mark_boundary(result: dict, drop: bool = True) -> dict:
    """Trades still open at the window end were force-closed (END_OF_DATA): they cross a protocol boundary."""
    crossing = [t for t in result.get("trades", []) if t.get("exit_reason") == "END_OF_DATA"]
    for t in crossing:
        t["boundary_crossing"] = True
    if drop and crossing:
        result["trades"] = [t for t in result["trades"] if t.get("exit_reason") != "END_OF_DATA"]
    result["boundary_trades_dropped" if drop else "boundary_trades_flagged"] = len(crossing)
    return result


# ------------------------------------------------------------------------------------------------ sealed vault
VAULT_SCHEMA = """
CREATE TABLE IF NOT EXISTS frozen (
    frozen_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, run_id TEXT, candidate_id TEXT, symbol TEXT,
    timeframe TEXT, signature TEXT, lineage_json TEXT NOT NULL, record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sealed_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, who TEXT NOT NULL, frozen_id TEXT, action TEXT NOT NULL,
    evidence_class TEXT, detail_json TEXT
);
CREATE TABLE IF NOT EXISTS sealed_results (
    frozen_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, symbol TEXT, timeframe TEXT, sealed_start TEXT,
    sealed_end TEXT, evidence_class TEXT NOT NULL, lineage_json TEXT NOT NULL, p_value REAL, sharpe_obs REAL,
    n_obs INTEGER, result_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS frozen_no_update BEFORE UPDATE ON frozen
BEGIN SELECT RAISE(ABORT, 'frozen records are immutable'); END;
CREATE TRIGGER IF NOT EXISTS frozen_no_delete BEFORE DELETE ON frozen
BEGIN SELECT RAISE(ABORT, 'frozen records are immutable'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON sealed_audit
BEGIN SELECT RAISE(ABORT, 'sealed audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON sealed_audit
BEGIN SELECT RAISE(ABORT, 'sealed audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS sealed_no_update BEFORE UPDATE ON sealed_results
BEGIN SELECT RAISE(ABORT, 'sealed results are append-only'); END;
CREATE TRIGGER IF NOT EXISTS sealed_no_delete BEFORE DELETE ON sealed_results
BEGIN SELECT RAISE(ABORT, 'sealed results are append-only'); END;
"""

FROZEN_REQUIRED = ("spec", "params", "cost_model", "risk_model", "selection_criteria", "code_sha256",
                   "dataset_sha256", "research_version", "seed", "symbol", "timeframe", "frames_path",
                   "sealed_start", "sealed_end", "signature", "family")


def lineage_keys(record: dict) -> list[str]:
    """Signature, parent and family lineage, each scoped to symbol+timeframe."""
    scope = f"{record.get('symbol')}:{record.get('timeframe')}"
    keys = [f"sig:{record.get('signature')}:{scope}", f"family:{record.get('family')}:{scope}"]
    for parent in record.get("lineage") or []:
        keys.append(f"parent:{parent}:{scope}")
    keys.append(f"params:{record.get('signature')}:{sha(record.get('params') or {})[:16]}:{scope}")
    return sorted(set(keys))


def _now():
    return datetime.now(timezone.utc).isoformat()


def block_bootstrap_pvalue(daily_returns, n: int = 2000, seed: int = 0) -> dict:
    """One-sided p-value for mean daily return > 0: stationary block bootstrap of the centred series (null of zero
    mean), p = P(mean* >= observed mean). Also a percentile CI of the mean."""
    x = np.asarray(daily_returns, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < validation.MIN_OBS or np.all(x == x[0]):
        return {"p_value": None, "n": int(len(x)), "status": validation.INSUFFICIENT}
    rng = np.random.default_rng(seed)
    b = validation._auto_block(len(x))
    idx = validation._resample_indices(len(x), n, b, rng, "stationary")
    centred = x - x.mean()
    null = centred[idx].mean(axis=1)
    boot = x[idx].mean(axis=1)
    p = float((np.sum(null >= x.mean()) + 1) / (n + 1))
    return {"p_value": p, "n": int(len(x)), "block": int(b), "mean": float(x.mean()),
            "ci_low": float(np.quantile(boot, 0.025)), "ci_high": float(np.quantile(boot, 0.975)), "status": "OK"}


class SealedVault:
    """Append-only store of frozen records, sealed results and the sealed-access audit log."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._db()) as db, db:
            db.executescript(VAULT_SCHEMA)

    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    # -- frozen records
    def freeze(self, record: dict) -> str:
        missing = [k for k in FROZEN_REQUIRED if k not in record]
        if missing:
            raise ValueError(f"bevroren record onvolledig: {missing}")
        body = {k: record[k] for k in sorted(record)}
        frozen_id = "frz_" + sha(body)[:24]
        lineage = lineage_keys(record)
        with closing(self._db()) as db, db:
            db.execute("INSERT OR IGNORE INTO frozen VALUES (?,?,?,?,?,?,?,?,?)",
                       (frozen_id, _now(), record.get("run_id"), record.get("candidate_id"), record["symbol"],
                        record["timeframe"], record["signature"], canon(lineage), canon(body)))
        return frozen_id

    def frozen(self, frozen_id: str) -> dict | None:
        with closing(self._db()) as db:
            row = db.execute("SELECT * FROM frozen WHERE frozen_id=?", (frozen_id,)).fetchone()
        if row is None:
            return None
        return {"frozen_id": row["frozen_id"], "created_at": row["created_at"], "lineage": json.loads(row["lineage_json"]),
                "record": json.loads(row["record_json"])}

    def _audit(self, db, who, frozen_id, action, evidence_class=None, detail=None):
        db.execute("INSERT INTO sealed_audit (at, who, frozen_id, action, evidence_class, detail_json) VALUES "
                   "(?,?,?,?,?,?)", (_now(), who, frozen_id, action, evidence_class, canon(detail or {})))
        return db.execute("SELECT last_insert_rowid()").fetchone()[0]

    def audit_log(self, limit: int = 500) -> list[dict]:
        with closing(self._db()) as db:
            rows = db.execute("SELECT * FROM sealed_audit ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [{**{k: r[k] for k in r.keys() if k != "detail_json"}, "detail": json.loads(r["detail_json"] or "{}")}
                for r in rows]

    def tested_lineage(self, keys, sealed_start=SEALED_START) -> list[dict]:
        """Earlier sealed results sharing any lineage key on an overlapping sealed period."""
        keys = set(keys)
        with closing(self._db()) as db:
            rows = db.execute("SELECT frozen_id, lineage_json, sealed_end, evidence_class FROM sealed_results").fetchall()
        hits = []
        for r in rows:
            shared = keys & set(json.loads(r["lineage_json"]))
            if shared and (r["sealed_end"] or "") > str(sealed_start):
                hits.append({"frozen_id": r["frozen_id"], "shared": sorted(shared), "evidence_class": r["evidence_class"]})
        return hits

    def result(self, frozen_id: str) -> dict | None:
        with closing(self._db()) as db:
            row = db.execute("SELECT result_json FROM sealed_results WHERE frozen_id=?", (frozen_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def results(self) -> list[dict]:
        with closing(self._db()) as db:
            rows = db.execute("SELECT frozen_id, evidence_class, p_value, sharpe_obs, n_obs, symbol, timeframe, "
                              "created_at FROM sealed_results ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    # -- the ONLY sealed access path
    def final_test(self, frozen_id: str, *, who: str = "factory", allow_reuse: bool = False,
                   search_trials: int = 0, sr_variance: float | None = None) -> dict:
        """Run the frozen candidate once on the sealed period.

        * unknown/incomplete frozen record -> refused (audit REFUSED)
        * this frozen record was already tested -> stored result is returned, nothing re-run (audit REPEAT_READ)
        * another record of the same signature/parent/family lineage was tested on an overlapping sealed period
          -> REUSED_HOLDOUT; refused (SealedReuseError) unless ``allow_reuse`` (then run but classified REUSED_HOLDOUT)
        """
        with closing(self._db()) as db, db:
            frz = db.execute("SELECT * FROM frozen WHERE frozen_id=?", (frozen_id,)).fetchone()
            if frz is None:
                self._audit(db, who, frozen_id, "REFUSED", None, {"reason": "geen bevroren record"})
        if frz is None:  # raised after the audit row is committed
            raise PermissionError(f"final_test geweigerd: {frozen_id} is niet bevroren")
        with closing(self._db()) as db, db:
            prior = db.execute("SELECT result_json FROM sealed_results WHERE frozen_id=?", (frozen_id,)).fetchone()
            if prior is not None:
                res = json.loads(prior[0])
                aid = self._audit(db, who, frozen_id, "REPEAT_READ", res.get("evidence_class"),
                                  {"note": "eerder resultaat teruggegeven, niets opnieuw gedraaid"})
                return {**res, "audit_id": aid, "repeat_read": True}
        record = json.loads(frz["record_json"])
        lineage = json.loads(frz["lineage_json"])
        hits = self.tested_lineage(lineage, record["sealed_start"])
        evidence = "REUSED_HOLDOUT" if hits else "HISTORICAL_OOS"
        if hits and not allow_reuse:
            with closing(self._db()) as db, db:
                self._audit(db, who, frozen_id, "BLOCKED_REUSED_HOLDOUT", "REUSED_HOLDOUT", {"prior": hits})
            raise SealedReuseError("verzegelde periode is al gebruikt door dezelfde lijn: "
                                   + ", ".join(h["frozen_id"] for h in hits))
        with closing(self._db()) as db, db:
            self._audit(db, who, frozen_id, "ACCESS_START", evidence,
                        {"sealed": [record["sealed_start"], record["sealed_end"]], "prior": hits})
        try:
            res = self._run_sealed(record)
        except Exception as exc:
            with closing(self._db()) as db, db:
                self._audit(db, who, frozen_id, "ERROR", evidence, {"error": f"{type(exc).__name__}: {exc}"})
            raise
        boot = block_bootstrap_pvalue(res.pop("_daily_returns"), seed=int(record.get("seed") or 0))
        res.update(frozen_id=frozen_id, evidence_class=evidence, bootstrap=boot, prior_lineage_tests=hits,
                   tested_at=_now(), who=who, protocol_version=PROTOCOL_VERSION)
        with closing(self._db()) as db, db:
            db.execute("INSERT INTO sealed_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (frozen_id, _now(), record["symbol"], record["timeframe"], record["sealed_start"],
                        record["sealed_end"], evidence, canon(lineage), boot.get("p_value"), res.get("sharpe_obs"),
                        boot.get("n"), canon(res)))
            aid = self._audit(db, who, frozen_id, "ACCESS_DONE", evidence,
                              {"trades": res["metrics"].get("trades"), "net_profit": res["metrics"].get("net_profit"),
                               "p_value": boot.get("p_value")})
        res["audit_id"] = aid
        return res

    @staticmethod
    def _run_sealed(record: dict) -> dict:
        path = Path(record["frames_path"])
        if file_sha256(path) != record["dataset_sha256"]:
            raise ValueError("dataset-sha klopt niet meer met het bevroren record: data is veranderd")
        seal, end = utc(record["sealed_start"]), utc(record["sealed_end"])
        wd = warmup_days_for(record["spec"], record["timeframe"], record["params"])
        frame = pd.read_parquet(path, filters=[("time_utc", ">=", seal - pd.Timedelta(days=wd)),
                                               ("time_utc", "<", end)])
        bid, ask = _split(frame)
        cm = record["cost_model"]
        r = run_window(record["spec"], record["params"], bid, ask, seal, capital=float(cm.get("capital", 10000)),
                       commission_per_lot=float(cm.get("commission_per_lot", 0.0)),
                       slippage_points=float(cm.get("slippage_points", 0.0)))
        r = mark_boundary(r, drop=False)
        trades = r["trades"]
        capital = float(cm.get("capital", 10000))
        tm = validation.trade_metrics(trades, capital)
        daily = validation.daily_returns_from_trades(trades, capital)
        sr = float(daily.mean() / daily.std(ddof=1)) if len(daily) > 2 and daily.std(ddof=1) > 0 else None
        weeks = max((end - seal).days / 7.0, 1e-9)
        metrics = {"trades": tm["n_trades"], "net_profit": tm["net_profit"],
                   "net_return_pct": tm["net_profit"] / capital * 100.0, "profit_factor": tm["profit_factor"],
                   "max_drawdown_pct": tm["max_drawdown_pct"], "recovery_factor": tm["recovery_factor"],
                   "win_rate": tm["win_rate"], "sharpe_daily": tm["sharpe"],
                   "expectancy": tm["net_profit"] / tm["n_trades"] if tm["n_trades"] else 0.0,
                   "trades_per_week": tm["n_trades"] / weeks,
                   "boundary_trades_flagged": r.get("boundary_trades_flagged", 0)}
        eq = r.get("equity") or []
        step = max(1, len(eq) // 300)
        return {"metrics": metrics, "sealed": [record["sealed_start"], record["sealed_end"]],
                "equity": [{"time": p["time"], "equity": p["equity"]} for p in eq[::step]],
                "trades": trades, "sharpe_obs": sr, "_daily_returns": daily.to_numpy().tolist()}

    # -- multiple testing over everything that ever touched the sealed period
    def multiple_testing(self, *, search_trials: int = 0, sr_variance: float | None = None, alpha: float = 0.05) -> dict:
        rows = self.results()
        tested = [r for r in rows if r["p_value"] is not None]
        p = [r["p_value"] for r in tested]
        holm = validation.multiple_testing(p, "holm", alpha) if p else {"p_adjusted": [], "status": "NO_TESTS"}
        bh = validation.multiple_testing(p, "bh", alpha) if p else {"p_adjusted": [], "status": "NO_TESTS"}
        srs = [r["sharpe_obs"] for r in rows if r["sharpe_obs"] is not None]
        # The sealed period is fresh data: the only selection performed ON it is the set of sealed tests. Search trials
        # were selected on other data and are already penalised by the walk-forward DSR gate (no double penalty).
        var = float(np.var(srs, ddof=1)) if len(srs) >= 2 else None
        n_total = len(rows)
        per = {}
        for i, r in enumerate(tested):
            per[r["frozen_id"]] = {"p_raw": r["p_value"], "p_holm": holm["p_adjusted"][i], "p_bh": bh["p_adjusted"][i]}
        for r in rows:
            res = self.result(r["frozen_id"]) or {}
            daily = None
            if r["sharpe_obs"] is not None and r["n_obs"]:
                floor = 1.0 / max(r["n_obs"], 1)
                v_use = max(var or 0.0, floor)
                sr0 = validation.expected_max_sr(max(n_total, 1), v_use) if n_total >= 2 else 0.0
                dsr = validation.psr_from_moments(r["sharpe_obs"], int(r["n_obs"]), 0.0, 3.0, sr0)
                daily = {"dsr": dsr, "sr0": sr0, "n": n_total, "V": v_use, "scope": SEALED_DSR_SCOPE}
            per.setdefault(r["frozen_id"], {}).update(evidence_class=r["evidence_class"], dsr=daily,
                                                      net_profit=(res.get("metrics") or {}).get("net_profit"))
        return {"n_final_tested": len(rows), "n_with_p_value": len(tested), "search_trials": int(search_trials),
                "dsr_n_trials": len(rows) + int(search_trials), "sealed_dsr_n": n_total,
                "dsr_n_trials_note": "historisch veld (eindtests + zoek-trials), alleen gerapporteerd; de sealed-DSR "
                                     "gebruikt sealed_dsr_n",
                "sr_variance": var, "alpha": alpha, "dsr_scope": SEALED_DSR_SCOPE,
                "search_trials_used_in_sealed_dsr": False, "legacy_sr_variance_arg_ignored": sr_variance is not None,
                "holm_rejections": holm.get("n_reject", 0), "bh_rejections": bh.get("n_reject", 0),
                "per_candidate": per,
                "definition_nl": ("p-waarde per eindtest uit een stationaire block-bootstrap van dagrendementen "
                                  "(nulhypothese gemiddelde 0, eenzijdig). Holm (familiegewijs) en Benjamini-Hochberg "
                                  "(FDR) over ALLE kandidaten die ooit op de verzegelde periode zijn getest (dit is de poort). DSR "
                                  "(informatief) met N = aantal verzegelde tests: de zoek-trials zijn al in de "
                                  "walk-forward-DSR verrekend, opnieuw tellen zou dubbel straffen.")}


# ------------------------------------------------------------------------------------------------ pre-registration
PREREG_VERSION = "PREREG_V1"
SEALED_DSR_SCOPE = "sealed_tests_only"


def research_protocol(cfg: dict) -> dict:
    """Everything that defines the hypothesis being tested by a factory run, fixed BEFORE the run starts. Any change
    to it is a different hypothesis (different hash), recorded as a new registration."""
    p = cfg.get("profile_settings") or {}
    return {
        "version": PREREG_VERSION, "research_version": cfg.get("research_version"),
        "code_sha256": cfg.get("code_sha256"),
        "budget": {"max_candidates": cfg.get("max_candidates"), "trials_per_candidate": cfg.get("trials"),
                   "max_minutes": cfg.get("max_minutes"), "max_backtests": cfg.get("max_backtests"),
                   "seed": cfg.get("seed")},
        "search_space": {"families": cfg.get("families"), "markets": cfg.get("markets"),
                         "timeframes": cfg.get("timeframes"), "profile": cfg.get("profile"),
                         "profile_settings": p, "selection_experiments": len(cfg.get("markets") or []) *
                         len(cfg.get("timeframes") or [])},
        "objectives": {"optimiser": "successive halving, Pareto (haalbaarheid, recovery, profit factor, stabiliteit)",
                       "wf_objective": "sharpe (binnenste lus)"},
        "quality": {"profile": cfg.get("quality"), "thresholds": cfg.get("quality_thresholds")},
        "costs": {"commission_per_lot_side": "per activaklasse (factory.COMMISSION_PER_LOT_SIDE)",
                  "spread": "echte Dukascopy bid/ask", "risk_pct": cfg.get("risk_pct"), "capital": cfg.get("capital")},
        "time_protocol": {"dev_start": cfg.get("dev_start"), "development_end": cfg.get("development_end"),
                          "sealed_start": cfg.get("sealed_start")},
        "walk_forward": {"procedure": "nested (binnen: parameters op inner-train, variant op inner-validatie; buiten: "
                                      "rolling en expanding)", "test_days": 182, "train_days_rolling": "min(3 jaar, "
                                      "ontwikkelperiode)", "purge_embargo": "uit max look-back en max houdduur"},
        "corrections": {"dsr": "effective_trials.DSR_V10, gate = globale DSR (zoekniveau, alle rondes)",
                        "family_dsr": "informatief", "sealed": "Holm over alle verzegelde tests ooit; sealed-DSR met "
                                                              "N = aantal verzegelde tests"},
        "final_test": {"procedure": "bevroren record, eenmalige toegang via final_test(), lijn-hergebruik = "
                                    "REUSED_HOLDOUT", "enabled": p.get("final_test")},
    }


def preregistration(cfg: dict) -> dict:
    proto = research_protocol(cfg)
    return {"sha256": sha(proto), "protocol": proto}


def verify_preregistration(cfg: dict) -> None:
    """Refuse to continue a run whose configuration no longer matches its registered protocol."""
    reg = cfg.get("preregistration")
    if not reg:
        return  # runs created before V10 have no registration
    now = sha(research_protocol(cfg))
    if now != reg.get("sha256"):
        raise PermissionError(f"onderzoeksprotocol gewijzigd na registratie ({reg.get('sha256', '')[:12]} -> "
                              f"{now[:12]}): dit is een nieuwe hypothese, start een nieuwe run")
