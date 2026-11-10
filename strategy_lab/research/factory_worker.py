"""Factory supervisor and worker processes.

Research never runs inside the FastAPI process. The API starts ONE detached supervisor process
(``python -m strategy_lab.research.factory_worker``) which drives every active run through its phases and executes
candidate tasks in a pool of worker processes (BELOW_NORMAL priority, max workers per run, RAM cap, stuck-task
detection). All state lives in sqlite (``data_cache/factory.sqlite3``): a crash or restart resumes at the last completed
candidate-stage; optimisation trials have idempotent keys so nothing is counted twice.
"""
from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd

from . import factory, protocol
from .jobs import JobCancelled

ROOT = factory.LAB.parent
HEARTBEAT_S = 2.0


class Superseded(Exception):
    pass
SUPERVISOR_STALE_S = 20.0
IDLE_EXIT_S = 120.0
CONTROL_POLL_S = 1.5


def _now():
    return datetime.now(timezone.utc).isoformat()


def lower_priority() -> str:
    """BELOW_NORMAL on Windows (psutil, else ctypes), nice +10 elsewhere."""
    try:
        import psutil
        p = psutil.Process()
        if os.name == "nt":
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            p.nice(10)
        return "psutil"
    except Exception:
        pass
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
            return "ctypes"
        os.nice(10)
        return "os.nice"
    except Exception:
        return "none"


# ================================================================================================ data preparation
def default_loader(symbol, timeframe):
    from ..data_manager import DataManager
    from . import backtest
    dm = DataManager()
    bid, ask, manifest = backtest.load_bid_ask(dm, symbol, timeframe, None, None)
    return bid, ask, manifest


def resolve_loader(spec: str | None):
    if not spec:
        return default_loader
    mod, _, fn = spec.partition(":")
    if not mod.startswith(("strategy_lab.tests.", "tests.")):
        raise ValueError("alleen test-loaders uit strategy_lab.tests zijn toegestaan")
    return getattr(importlib.import_module(mod), fn)


def prepare_run(store: factory.FactoryStore, run: dict, loader=None, frames_dir=None) -> dict:
    """Measure availability, build per-market ranges, write one frames file per market (dataset id = file sha) and
    pick representative development windows. Stored in runs.protocol."""
    cfg = run["config"]
    loader = loader or resolve_loader((cfg.get("advanced") or {}).get("loader") or cfg.get("loader"))
    frames_dir = Path(frames_dir or cfg.get("frames_dir") or factory.FRAMES_DIR)
    prof = cfg["profile_settings"]
    markets = {}
    for sym in cfg["markets"]:
        for tf in cfg["timeframes"]:
            key = f"{sym}:{tf}"
            store.log(run["run_id"], "info", f"Data voorbereiden {key}")
            try:
                bid, ask, manifest = loader(sym, tf)
            except Exception as exc:
                markets[key] = {"symbol": sym, "timeframe": tf, "error": f"{type(exc).__name__}: {exc}",
                                "ranges": {"status": "INSUFFICIENT_SUPPORT", "notes_nl": [f"data laden mislukt: {exc}"],
                                           "sealed_status": "INSUFFICIENT_SUPPORT"}, "windows": []}
                store.log(run["run_id"], "warning", f"{key}: data laden mislukt: {exc}")
                continue
            avail = protocol.data_availability(bid, ask, tf)
            avail["measured"] = True
            ranges = protocol.build_ranges(avail, dev_start=cfg["dev_start"], development_end=cfg["development_end"],
                                           sealed_start=cfg["sealed_start"])
            src = {k: (manifest or {}).get(k) for k in ("bid_source", "ask_source", "daily_partitions", "last_utc",
                                                        "first_utc", "synthetic")}
            tag = protocol.sha({"src": src, "tf": tf, "rows": len(bid), "last": str(bid.index[-1]) if len(bid) else None})[:12]
            path = frames_dir / f"{sym}_{tf}_{tag}.parquet"
            if path.is_file():
                dataset_sha = protocol.file_sha256(path)
            else:
                dataset_sha = protocol.frames_to_file(bid, ask, path)
            windows = []
            if ranges.get("development"):
                d = ranges["development"]
                windows = factory.representative_windows(bid, d["start"], d["end"], int(prof["n_windows"]),
                                                         int(prof["window_months"]))
            markets[key] = {"symbol": sym, "timeframe": tf, "availability": avail, "ranges": ranges,
                            "frames_path": str(path), "dataset_sha256": dataset_sha, "windows": windows,
                            "cost_model": factory.cost_model_for(sym, cfg["capital"], cfg["risk_pct"]),
                            "source": {k: v for k, v in src.items() if k != "daily_partitions"},
                            "daily_partitions": len((manifest or {}).get("daily_partitions") or [])}
            del bid, ask
    proto = {"version": protocol.PROTOCOL_VERSION, "dev_start": cfg["dev_start"],
             "development_end": cfg["development_end"], "sealed_start": cfg["sealed_start"],
             "embargo_days": protocol.EMBARGO_DAYS, "markets": markets,
             "rules_nl": ["Ontwikkeling (generatie, snelle tests, optimalisatie): tot de ontwikkelgrens.",
                          "Validatie (walk-forward rolling en expanding, robuustheid, modelselectie): ontwikkelgrens "
                          "tot de verzegelde start.",
                          "Verzegeld: alleen via final_test() met een onveranderlijk bevroren record; elke toegang "
                          "wordt gelogd; een lijn die al getest is wordt REUSED_HOLDOUT.",
                          "Zoek- en optimalisatiecode krijgt fysiek geen bars vanaf de verzegelde start.",
                          "Trades die aan het einde van een periode nog open staan worden gemarkeerd en niet geteld."]}
    store.update_run(run["run_id"], protocol=proto)
    _freeze_registration(store, run)
    _register(store.run(run["run_id"]), markets)
    return proto


def _freeze_registration(store: factory.FactoryStore, run: dict) -> None:
    """The protocol is frozen when the run starts (here, before any candidate is researched). A configuration that
    was edited between creation and start gets a new registration; the old hash is kept as history. After this
    point every stage verifies the hash and refuses a changed protocol."""
    cfg = dict(run["config"])
    now = protocol.preregistration(cfg)
    old = cfg.get("preregistration") or {}
    if old.get("sha256") != now["sha256"]:
        now["superseded_before_start"] = old.get("sha256")
        cfg["preregistration"] = now
        store.update_run(run["run_id"], config=cfg)
        store.log(run["run_id"], "warning", "onderzoeksprotocol gewijzigd voor de start: nieuwe registratie "
                                            f"{now['sha256'][:12]} (was {str(old.get('sha256'))[:12]})")


def _register(run: dict, markets: dict) -> None:
    """Pre-registration and data-usage history in the ledger (append-only)."""
    cfg = run["config"]
    led = _ledger(run)
    if led is None or not hasattr(led, "preregister"):
        return
    reg = cfg.get("preregistration") or protocol.preregistration(cfg)
    led.preregister(reg["sha256"], reg["protocol"], run_id=run["run_id"])
    for key, mk in markets.items():
        r = mk.get("ranges") or {}
        for part, purpose in (("development", "SEARCH"), ("validation", "VALIDATION_SELECTION")):
            if r.get(part):
                led.record_data_usage(run_id=run["run_id"], symbol=mk["symbol"], timeframe=mk["timeframe"],
                                      start=r[part]["start"], end=r[part]["end"], purpose=purpose,
                                      detail={"preregistration": reg["sha256"]})
        led.record_data_usage(run_id=run["run_id"], symbol=mk["symbol"], timeframe=mk["timeframe"],
                              start=(r.get("development") or {}).get("start"), end=(r.get("validation") or {}).get("end"),
                              purpose="SELECTION_EXPERIMENT",
                              detail={"note": "markt x timeframe is een selectie-experiment (telt in de globale N)"})


def generate_candidates(store: factory.FactoryStore, run: dict, ledger=None) -> int:
    cfg = run["config"]
    gen = factory.get_generator()
    want = int(cfg["max_candidates"])
    gcfg = {"markets": cfg["markets"], "symbols": cfg["markets"], "timeframes": cfg["timeframes"],
            "families": cfg["families"], "max_candidates": want * 3, "profile": cfg["profile"],
            "quality": cfg["quality"], "seed": cfg["seed"]}
    raw = list(gen.generate(gcfg, cfg["seed"]) or [])
    for c in raw:
        c.setdefault("group", "overig")
        c.setdefault("name", c.get("id"))
        c.setdefault("signature", protocol.sha(c.get("spec"))[:20])
        c.setdefault("parent", (c.get("passport") or {}).get("base_template") or (c.get("passport") or {}).get("parent"))
        c["id"] = str(c["id"])
    existing = set()
    if ledger is not None and cfg.get("dedup", "ledger") == "ledger":
        existing = {k.split("|")[0] for k in ledger.factory_signatures()}
    kept, dropped = gen.dedup(raw, existing)
    by_id = {c["id"]: c for c in raw}
    ledger_dups = [by_id[d["id"]] for d in dropped if isinstance(d, dict) and d.get("id") in by_id
                   and (d.get("signature") or by_id[d["id"]]["signature"]) in existing]
    # balance markets: round-robin over (symbol, timeframe)
    by_market = {}
    for c in kept:
        by_market.setdefault((c["symbol"], c["timeframe"]), []).append(c)
    chosen = []
    while len(chosen) < want and any(by_market.values()):
        for k in list(by_market):
            if by_market[k] and len(chosen) < want:
                chosen.append(by_market[k].pop(0))
    ids = set()
    unique = []
    for c in chosen:
        if c["id"] in ids:
            c["id"] = f"{c['id']}_{len(ids)}"
        ids.add(c["id"])
        unique.append(c)
    store.add_candidates(run["run_id"], unique)
    dup_rows = [c for c in ledger_dups if c["id"] not in ids][: want]
    if dup_rows:
        store.add_candidates(run["run_id"], dup_rows, status="REJECTED", stage="L1",
                             reasons=["duplicaat: deze signatuur is al eerder getest (onderzoekslogboek)"])
    for c in unique:
        store.add_task(run["run_id"], c["id"], "research")
    store.log(run["run_id"], "info", f"{len(unique)} kandidaten gegenereerd ({gen.name}); {len(dup_rows)} duplicaten "
                                     f"van eerder geteste hypotheses afgewezen")
    store.update_run(run["run_id"], generator=getattr(gen, "name", "generator"))
    return len(unique)


# ================================================================================================ worker side
_PROC_CACHE: dict = {}


def _init_worker():
    lower_priority()


def _ledger(run):
    from .ledger import ResearchLedger
    path = run["config"].get("ledger_path") or factory.DEFAULT_LEDGER
    return ResearchLedger(path)


def _search_data(run, market) -> tuple:
    key = (market["frames_path"], run["config"]["sealed_start"])
    hit = _PROC_CACHE.get(key)
    if hit is None:
        data = protocol.SearchData.from_file(market["frames_path"], run["config"]["sealed_start"],
                                             symbol=market["symbol"], timeframe=market["timeframe"],
                                             dataset_sha256=market["dataset_sha256"])
        regimes = factory.distribution.regime_labels(data.bid)
        if len(_PROC_CACHE) > 4:
            _PROC_CACHE.clear()
        hit = _PROC_CACHE[key] = (data, regimes)
    return hit


class Control:
    """Worker-side pause/stop polling and task heartbeat."""

    def __init__(self, store, run_id, cand_id, kind):
        self.store, self.run_id, self.cand_id, self.kind = store, run_id, cand_id, kind
        self._t = 0.0
        self._hb = 0.0

    def beat(self):
        now = time.time()
        if now - self._hb >= HEARTBEAT_S:
            self._hb = now
            self.store.heartbeat(self.run_id, self.cand_id, self.kind)

    def check(self):
        now = time.time()
        if now - self._t < CONTROL_POLL_S:
            return
        self._t = now
        while True:
            ctl = self.store.control(self.run_id)
            if ctl == "stop":
                raise JobCancelled()
            if ctl != "pause":
                return
            self._hb = 0.0
            self.beat()
            time.sleep(1.0)


def run_task(db_path: str, run_id: str, cand_id: str, kind: str) -> str:
    store = factory.FactoryStore(db_path)
    store.set_task(run_id, cand_id, kind, state="running", worker_pid=os.getpid(), heartbeat_at=time.time(),
                   started_at=_now())
    run = store.run(run_id)
    ctl = Control(store, run_id, cand_id, kind)
    try:
        ledger = _ledger(run)
        vault = protocol.SealedVault(db_path)
        if kind == "research":
            row = store.candidate(run_id, cand_id)
            market = run["protocol"]["markets"][f"{row['symbol']}:{row['timeframe']}"]
            if "frames_path" not in market:
                status = "INSUFFICIENT_EVIDENCE"
                store.put_stage(run_id, cand_id, "L1", status, 0.0, market["ranges"]["notes_nl"], None, None, [])
                store.set_candidate(run_id, cand_id, stage="L1", status=status, reasons=market["ranges"]["notes_nl"])
            else:
                data, regimes = _search_data(run, market)
                env = factory.Env(store, run, data, ledger=ledger, vault=vault, check=ctl.check, beat=ctl.beat,
                                  regimes=regimes)
                status = factory.CandidateRunner(env, row).run()
        elif kind == "final":
            res = factory.final_test_candidate(store, run, cand_id, vault, ledger)
            status = res["status"] if res else "ERROR"
        else:
            raise ValueError(f"onbekend taaktype {kind}")
        store.set_task(run_id, cand_id, kind, state="done", finished_at=_now())
        return status
    except JobCancelled:
        store.set_task(run_id, cand_id, kind, state="cancelled", finished_at=_now())
        return "CANCELLED"
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        store.set_task(run_id, cand_id, kind, state="error", finished_at=_now(), error=msg[:2000])
        store.set_candidate(run_id, cand_id, status="ERROR", reasons=[msg[:500]])
        store.log(run_id, "error", f"{cand_id} ({kind}): {msg}\n{traceback.format_exc(limit=6)}")
        return "ERROR"


# ================================================================================================ supervisor
class Supervisor:
    def __init__(self, db_path, *, loader=None, frames_dir=None, idle_exit_s=IDLE_EXIT_S):
        self.db_path = str(db_path)
        self.store = factory.FactoryStore(db_path)
        self.loader, self.frames_dir = loader, frames_dir
        self.idle_exit_s = idle_exit_s
        self.pool = None
        self.pool_workers = 0
        self.futures = {}
        self.ram_pauses = 0
        self._last_res = 0.0
        self._last_hb = 0.0
        self.priority = lower_priority()
        self.superseded = False
        self._hb_stop = threading.Event()

    # ---- singleton claim
    def claim(self) -> bool:
        import psutil
        with factory.closing(self.store.db()) as db, db:
            row = db.execute("SELECT pid, heartbeat_at FROM supervisor WHERE id=1").fetchone()
            if row and row["pid"] != os.getpid() and row["heartbeat_at"] and time.time() - row["heartbeat_at"] < \
                    SUPERVISOR_STALE_S and psutil.pid_exists(row["pid"]):
                return False
            db.execute("INSERT OR REPLACE INTO supervisor (id, pid, heartbeat_at, started_at) VALUES (1,?,?,?)",
                       (os.getpid(), time.time(), _now()))
        return True

    def heartbeat(self, force=False):
        now = time.time()
        if force or now - self._last_hb >= HEARTBEAT_S:
            self._last_hb = now
            with factory.closing(self.store.db()) as db, db:
                cur = db.execute("UPDATE supervisor SET heartbeat_at=? WHERE id=1 AND pid=?", (now, os.getpid()))
                if cur.rowcount == 0:
                    self.superseded = True
        if self.superseded:
            raise Superseded("een andere supervisor heeft de factory overgenomen")

    def _beat_forever(self):
        """Keeps the claim fresh while the main thread is busy in long steps (data preparation, generation, L6),
        otherwise the API would see a stale heartbeat and start a second supervisor."""
        while not self._hb_stop.wait(HEARTBEAT_S):
            try:
                with factory.closing(self.store.db()) as db, db:
                    cur = db.execute("UPDATE supervisor SET heartbeat_at=? WHERE id=1 AND pid=?",
                                     (time.time(), os.getpid()))
                    if cur.rowcount == 0:
                        self.superseded = True
                        return
            except Exception:
                pass

    # ---- pool
    def ensure_pool(self, workers):
        if self.pool is not None and self.pool_workers == workers:
            return
        self.shutdown_pool()
        # one compute thread per worker: BLAS/numexpr pools otherwise spawn ~64 threads each and overshoot the CPU limit
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            os.environ[var] = "1"
        self.pool = ProcessPoolExecutor(max_workers=workers, initializer=_init_worker)
        self.pool_workers = workers

    def shutdown_pool(self, kill=False):
        if self.pool is None:
            return
        procs = list(getattr(self.pool, "_processes", {}).values())
        if kill:
            for p in procs:
                try:
                    p.kill()
                except Exception:
                    pass
        try:
            self.pool.shutdown(wait=not kill, cancel_futures=True)
        except Exception:
            pass
        self.pool, self.pool_workers, self.futures = None, 0, {}

    def worker_pids(self) -> list[int]:
        if self.pool is None:
            return []
        return [p.pid for p in getattr(self.pool, "_processes", {}).values() if p.pid]

    def resources(self, run_id) -> dict:
        try:
            import psutil
        except Exception:
            return {"cpu_pct": None, "ram_mb": None, "workers": len(self.futures)}
        cpu, ram = 0.0, 0.0
        cache = self.__dict__.setdefault("_procs", {})
        pids = [os.getpid()] + self.worker_pids()
        for pid in list(cache):
            if pid not in pids:
                cache.pop(pid)
        procs = []
        for pid in pids:
            try:
                procs.append(cache.setdefault(pid, psutil.Process(pid)))  # cpu_percent needs the same object
            except Exception:
                pass
        for p in procs:
            try:
                cpu += p.cpu_percent(interval=None)
                ram += p.memory_info().rss / 1048576
            except Exception:
                pass
        return {"cpu_pct": round(cpu / (os.cpu_count() or 1), 1), "cpu_pct_of_one_core": round(cpu, 1),
                "ram_mb": round(ram, 1), "workers": len(self.futures), "pool_size": self.pool_workers,
                "machine_cpu_pct": psutil.cpu_percent(interval=None), "ram_pauses": self.ram_pauses,
                "priority": self.priority, "supervisor_pid": os.getpid(), "worker_pids": self.worker_pids()}

    # ---- main loop
    def serve(self):
        if not self.claim():
            return "another supervisor is alive"
        threading.Thread(target=self._beat_forever, name="supervisor-heartbeat", daemon=True).start()
        try:
            return self._serve()
        except Superseded:
            self.shutdown_pool(kill=True)
            return "superseded by another supervisor"
        finally:
            self._hb_stop.set()

    def _serve(self):
        idle_since = time.time()
        while True:
            self.heartbeat()
            run = self.next_run()
            if run is None:
                self.shutdown_pool()
                if time.time() - idle_since > self.idle_exit_s:
                    return "idle"
                time.sleep(1.0)
                continue
            idle_since = time.time()
            try:
                self.drive(run)
            except Superseded:
                raise
            except Exception as exc:
                self.store.log(run["run_id"], "error", f"supervisor: {type(exc).__name__}: {exc}\n"
                                                       f"{traceback.format_exc(limit=8)}")
                self.store.update_run(run["run_id"], status="error", phase="error", error=f"{type(exc).__name__}: {exc}",
                                      finished_at=_now())
                self.shutdown_pool(kill=True)

    def next_run(self):
        with factory.closing(self.store.db()) as db:
            row = db.execute("SELECT run_id FROM runs WHERE status IN ('queued','running','paused') "
                             "ORDER BY created_at LIMIT 1").fetchone()
        return self.store.run(row[0]) if row else None

    def drive(self, run):
        rid = run["run_id"]
        if run["control"] == "stop":
            return self.stop(rid)
        if run["status"] == "queued":
            self.store.update_run(rid, status="running", started_at=run.get("started_at") or _now())
        phase = run["phase"]
        if phase in ("queued", "preparing"):
            self.store.update_run(rid, phase="preparing")
            prepare_run(self.store, self.store.run(rid), self.loader, self.frames_dir)
            deadline = time.time() + float(run["config"]["max_minutes"]) * 60.0
            self.store.update_run(rid, phase="generating", deadline_at=str(deadline))
            phase = "generating"
        if phase == "generating":
            if not self.store.candidates(rid):
                generate_candidates(self.store, self.store.run(rid), _ledger(run))
            self.store.update_run(rid, phase="researching")
            phase = "researching"
        if phase == "researching":
            if not self.execute(rid, "research"):
                return
            self.store.update_run(rid, phase="selecting")
            phase = "selecting"
        run = self.store.run(rid)
        vault = protocol.SealedVault(self.db_path)
        if phase == "selecting":
            sel = factory.select_and_freeze(self.store, run, vault, _ledger(run))
            frozen = [s["cand_id"] for s in sel if s["selected"]]
            for cid in frozen:
                self.store.add_task(rid, cid, "final")
            self.store.log(rid, "info", f"L6: {len(frozen)} kandidaten bevroren, {len(sel) - len(frozen)} niet "
                                        "geselecteerd")
            self.store.update_run(rid, phase="final_test")
            phase = "final_test"
        if phase == "final_test":
            if not self.execute(rid, "final"):
                return
            self.store.update_run(rid, phase="forward")
            phase = "forward"
        if phase == "forward":
            mt = factory.finalize_run(self.store, self.store.run(rid), vault, _ledger(run))
            c = factory.counts(self.store, rid)
            self.store.update_run(rid, status="done", phase="done", progress=1.0, counts=c, finished_at=_now(),
                                  resources=self.resources(rid))
            self.store.log(rid, "info", f"Klaar: {c}. Meervoudig testen: {mt['n_final_tested']} eindtests ooit, "
                                        f"DSR-N {mt['dsr_n_trials']}")
            self.shutdown_pool()

    def stop(self, rid):
        self.shutdown_pool(kill=True)
        for t in self.store.tasks(rid):
            if t["state"] in ("queued", "running"):
                self.store.set_task(rid, t["cand_id"], t["kind"], state="cancelled", finished_at=_now())
                cand = self.store.candidate(rid, t["cand_id"])
                if cand and cand["status"] not in factory.TERMINAL_STATUSES:
                    self.store.set_candidate(rid, t["cand_id"], status="STOPPED",
                                             reasons=(cand["reasons"] or []) + ["run gestopt voordat deze kandidaat klaar was"])
        self.store.update_run(rid, status="stopped", phase="stopped", finished_at=_now(),
                              counts=factory.counts(self.store, rid))
        self.store.log(rid, "info", "Run gestopt")

    def execute(self, rid, kind) -> bool:
        """Dispatch tasks of ``kind`` until all are finished. Returns False when the run was stopped/paused-exited."""
        run = self.store.run(rid)
        cfg = run["config"]
        workers = int(cfg["workers"])
        deadline = float(run.get("deadline_at") or 0) or None
        # requeue tasks orphaned by a previous supervisor/worker crash: their completed stages are kept
        for t in self.store.tasks(rid, kind, "running"):
            if not self._alive(t["worker_pid"]) or t["worker_pid"] not in self.worker_pids():
                self.store.set_task(rid, t["cand_id"], kind, state="queued")
                self.store.log(rid, "warning", f"{t['cand_id']}: taak hervat na onderbreking (vanaf laatste stage)")
        self.ensure_pool(workers)
        stuck_s = float(cfg.get("stuck_timeout_s") or 900)
        while True:
            self.heartbeat()
            ctl = self.store.control(rid)
            if ctl == "stop":
                self.stop(rid)
                return False
            paused = ctl == "pause"
            status = self.store.run(rid)["status"]
            if paused and status != "paused":
                self.store.update_run(rid, status="paused")
                self.store.log(rid, "info", "Run gepauzeerd")
            elif not paused and status == "paused":
                self.store.update_run(rid, status="running")
                self.store.log(rid, "info", "Run hervat")
            self._collect(rid, kind, timeout=0.5)
            queued = self.store.tasks(rid, kind, "queued")
            running = len(self.futures)
            if not queued and not running:
                foreign = [t for t in self.store.tasks(rid, kind, "running") if self._alive(t["worker_pid"])
                           and t["worker_pid"] not in self.worker_pids()]
                if not foreign:
                    return True
                time.sleep(1.0)
                continue
            res = self._resources_tick(rid, kind)
            over_ram = res and res.get("ram_mb") and res["ram_mb"] > float(cfg["max_ram_mb"])
            past_deadline = kind == "research" and deadline and time.time() > deadline
            if past_deadline and queued:
                for t in queued:
                    self.store.set_task(rid, t["cand_id"], kind, state="skipped", finished_at=_now())
                    self.store.set_candidate(rid, t["cand_id"], status="NOT_RUN",
                                             reasons=["tijdsbudget (max_minutes) bereikt voordat deze kandidaat startte"])
                self.store.log(rid, "warning", f"Tijdsbudget bereikt: {len(queued)} kandidaten niet gestart")
                queued = []
            if over_ram:
                self.ram_pauses += 1
            if not paused and not over_ram:
                # adaptive: start at most one extra task per tick, only while the whole machine is under its limits
                free = max(0, workers - running)
                if free and running and not self._machine_has_room(cfg):
                    free = 0
                for t in queued[: min(free, 1 if running else free)]:
                    self._submit(rid, t["cand_id"], kind, t["attempts"])
            self._check_stuck(rid, kind, stuck_s)
            time.sleep(0.2)

    def _machine_has_room(self, cfg) -> bool:
        try:
            import psutil
        except ImportError:
            return True
        cpu_lim = float(cfg.get("max_machine_cpu_pct") or 80.0)
        ram_lim = float(cfg.get("max_machine_ram_pct") or 80.0)
        return psutil.cpu_percent(interval=None) < cpu_lim and psutil.virtual_memory().percent < ram_lim

    def _submit(self, rid, cand_id, kind, attempts):
        try:
            fut = self.pool.submit(run_task, self.db_path, rid, cand_id, kind)
        except BrokenProcessPool:
            self._recover(rid, kind)
            return
        self.store.set_task(rid, cand_id, kind, state="running", attempts=(attempts or 0) + 1,
                            heartbeat_at=time.time(), worker_pid=None)
        self.futures[fut] = (cand_id, kind)

    def _collect(self, rid, kind, timeout):
        if not self.futures:
            return
        done, _ = wait(list(self.futures), timeout=timeout, return_when=FIRST_COMPLETED)
        for fut in done:
            cand_id, k = self.futures.pop(fut)
            try:
                fut.result()
            except BrokenProcessPool:
                self.futures[fut] = (cand_id, k)
                self._recover(rid, kind)
                return
            except Exception as exc:
                self.store.set_task(rid, cand_id, k, state="error", error=f"{type(exc).__name__}: {exc}")
                self.store.set_candidate(rid, cand_id, status="ERROR", reasons=[f"{type(exc).__name__}: {exc}"])
        self._progress(rid)

    def _recover(self, rid, kind):
        """A worker process died (killed, crashed): rebuild the pool, requeue its in-flight tasks."""
        lost = list(self.futures.values())
        self.shutdown_pool(kill=True)
        for cand_id, k in lost:
            t = next((x for x in self.store.tasks(rid, k) if x["cand_id"] == cand_id), None)
            if t and t["state"] in ("running", "queued"):
                if (t["attempts"] or 0) >= 3:
                    self.store.set_task(rid, cand_id, k, state="error", error="worker herhaaldelijk afgebroken")
                    self.store.set_candidate(rid, cand_id, status="ERROR", reasons=["worker herhaaldelijk afgebroken"])
                else:
                    self.store.set_task(rid, cand_id, k, state="queued")
        self.store.log(rid, "warning", f"Workerproces afgebroken; {len(lost)} taken opnieuw in de wachtrij "
                                       "(hervat vanaf laatste stage)")
        self.ensure_pool(int(self.store.run(rid)["config"]["workers"]))

    def _check_stuck(self, rid, kind, stuck_s):
        now = time.time()
        for t in self.store.tasks(rid, kind, "running"):
            if t["heartbeat_at"] and now - float(t["heartbeat_at"]) > stuck_s:
                self.store.log(rid, "warning", f"{t['cand_id']}: geen heartbeat sinds {now - t['heartbeat_at']:.0f}s; "
                                               "worker wordt herstart")
                if t["worker_pid"]:
                    try:
                        import psutil
                        psutil.Process(int(t["worker_pid"])).kill()
                    except Exception:
                        pass
                self._recover(rid, kind)
                return

    @staticmethod
    def _alive(pid):
        if not pid:
            return False
        try:
            import psutil
            return psutil.pid_exists(int(pid))
        except Exception:
            return False

    def _resources_tick(self, rid, kind):
        now = time.time()
        if now - self._last_res < 2.0:
            return None
        self._last_res = now
        res = self.resources(rid)
        self.store.update_run(rid, resources=res, counts=factory.counts(self.store, rid))
        return res

    def _progress(self, rid):
        tasks = self.store.tasks(rid)
        if not tasks:
            return
        fin = sum(1 for t in tasks if t["state"] in ("done", "error", "cancelled", "skipped"))
        self.store.update_run(rid, progress=round(0.05 + 0.9 * fin / len(tasks), 4))


# ================================================================================================ launching
def supervisor_alive(db_path) -> dict:
    store = factory.FactoryStore(db_path)
    with factory.closing(store.db()) as db:
        row = db.execute("SELECT pid, heartbeat_at, started_at FROM supervisor WHERE id=1").fetchone()
    if not row:
        return {"alive": False}
    try:
        import psutil
        exists = psutil.pid_exists(row["pid"])
    except Exception:
        exists = True
    fresh = row["heartbeat_at"] and time.time() - row["heartbeat_at"] < SUPERVISOR_STALE_S
    return {"alive": bool(exists and fresh), "pid": row["pid"], "heartbeat_age_s":
            round(time.time() - row["heartbeat_at"], 1) if row["heartbeat_at"] else None, "started_at": row["started_at"]}


def ensure_supervisor(db_path=None, *, python=None) -> dict:
    """Start the detached supervisor process when none is alive (survives FastAPI reloads)."""
    db_path = str(db_path or factory.DEFAULT_DB)
    state = supervisor_alive(db_path)
    if state["alive"]:
        return state
    log_dir = Path(db_path).parent
    log = open(log_dir / "factory_supervisor.log", "ab")
    exe = python or sys.executable
    if exe.lower().endswith("pythonw.exe"):
        alt = exe[:-len("pythonw.exe")] + "python.exe"
        exe = alt if Path(alt).is_file() else exe
    flags = 0
    if os.name == "nt":
        # NO_WINDOW gives the supervisor a hidden console that its worker-pool children inherit; DETACHED_PROCESS
        # would leave it console-less, so every spawned python.exe worker opened its own visible terminal window.
        flags = 0x00000200 | 0x00004000 | 0x08000000  # NEW_GROUP | BELOW_NORMAL | NO_WINDOW
    proc = subprocess.Popen([exe, "-m", "strategy_lab.research.factory_worker", "--db", db_path], cwd=str(ROOT),
                            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags,
                            close_fds=True)
    for _ in range(50):
        time.sleep(0.1)
        state = supervisor_alive(db_path)
        if state["alive"]:
            break
    return {**state, "spawned_pid": proc.pid}


def new_run_id() -> str:
    return "fr_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:6]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(factory.DEFAULT_DB))
    ap.add_argument("--idle-exit", type=float, default=IDLE_EXIT_S)
    ap.add_argument("--frames-dir", default=None)
    args = ap.parse_args(argv)
    sup = Supervisor(args.db, frames_dir=args.frames_dir, idle_exit_s=args.idle_exit)
    print(f"[factory] supervisor {os.getpid()} start ({_now()}), priority={sup.priority}", flush=True)
    out = sup.serve()
    print(f"[factory] supervisor {os.getpid()} exit: {out}", flush=True)


if __name__ == "__main__":
    main()
