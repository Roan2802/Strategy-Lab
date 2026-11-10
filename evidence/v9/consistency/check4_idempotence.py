"""Check 4: restart idempotence of the forward engine on real data (crash, restart, same day twice, concurrency)."""
import json
import shutil
import sqlite3
import tempfile
import threading
from contextlib import closing

from check2_parity import CASES
from parity_lib import *
from strategy_lab.forward_engine import ForwardEngine
from strategy_lab.forward_registry import ForwardRegistry


def signature(engine_, portfolio):
    report = engine_.report(portfolio)
    return {"events": [(e["kind"], e["day"], e["candle_at"], e["registration_id"], e["detail"]) for e in report["events"]],
            "members": [(m["registration_id"], m["state"], m["allocation"], m["joined_at"]) for m in report["members"]],
            "portfolio": {k: v for k, v in report["portfolio"].items()},
            "days": [(d["day"], d["balance"], d["equity"], d["publications"]) for d in report["days"]]}


def trade_keys(sig):
    return [(e[4]["entry_at"], e[4]["side"]) for e in sig["events"] if e[0] == "POSITION_CLOSED"]


def fresh(path):
    return lambda: ForwardEngine(ForwardRegistry(DailyPartitionStore(path)))


def scenarios(name):
    spec, params, first, last, *_ = CASES[name]
    base_dir = Path(tempfile.mkdtemp(prefix=f"idem_{name}_"))
    store, eng, portfolio, registration, stats = build_forward(base_dir / "base.sqlite", spec, params, first, last)
    del store, eng
    days = [(first + timedelta(days=i)) for i in range((last - first).days + 1)]
    results = {}

    def clone(label):
        path = base_dir / f"{label}.sqlite"
        shutil.copy(base_dir / "base.sqlite", path)
        return path

    # S0 continuous
    p0 = clone("continuous")
    e0 = fresh(p0)()
    advance_daily(e0, portfolio, first, last)
    ref = signature(e0, portfolio)
    results["reference"] = {"events": len(ref["events"]), "trades": len(trade_keys(ref)), "final_balance": ref["portfolio"]["balance"]}
    assert len(trade_keys(ref)) >= 1

    def compare(label, sig, extra=None):
        results[label] = {"identical_to_continuous": sig == ref,
                          "duplicate_trades": len(trade_keys(sig)) - len(set(trade_keys(sig))),
                          "events": len(sig["events"]), "trades": len(trade_keys(sig)), **(extra or {})}

    # S1 a new engine over a re-opened store file before every day (process restart)
    p1 = clone("restart_each_day")
    advance_daily(None, portfolio, first, last, fresh_engine=fresh(p1))
    compare("restart_before_every_day", signature(fresh(p1)(), portfolio))

    # S2 the same day processed twice (and a restart in between)
    p2 = clone("twice")
    e2 = fresh(p2)()
    repeats = []
    for d in days:
        cutoff = observed_after(d)
        first_call = e2.advance(portfolio, as_of=cutoff)
        before = signature(e2, portfolio)
        again = fresh(p2)().advance(portfolio, as_of=cutoff)
        e2 = fresh(p2)()
        repeats.append((first_call["committed_days"], again["committed_days"], signature(e2, portfolio) == before))
    compare("same_day_twice", signature(e2, portfolio),
            {"second_call_committed_days": [r[1] for r in repeats if r[1]], "state_unchanged_after_repeat": all(r[2] for r in repeats)})

    # S3 all days in one call (max_days) on one cutoff
    p3 = clone("batch")
    e3 = fresh(p3)()
    out = e3.advance(portfolio, as_of=observed_after(last), max_days=31)
    sig3 = signature(e3, portfolio)
    sig3_cmp = {**sig3, "days": [(d[0], d[1], d[2], d[3]) for d in sig3["days"]]}
    results["single_batch_call"] = {"committed_days": len(out["committed_days"]),
                                    "events_identical": sig3["events"] == ref["events"],
                                    "members_identical": sig3["members"] == ref["members"],
                                    "balance_identical": sig3["portfolio"]["balance"] == ref["portfolio"]["balance"]}

    # S4 crash while committing day k: nothing persists, a retry completes with the reference result
    p4 = clone("crash")
    e4 = fresh(p4)()
    k = len(days) // 2
    for d in days[:k]:
        e4.advance(portfolio, as_of=observed_after(d))
    before = signature(e4, portfolio)
    with closing(sqlite3.connect(p4)) as db, db:
        db.execute("CREATE TRIGGER crash BEFORE INSERT ON forward_days BEGIN SELECT RAISE(ABORT, 'simulated crash'); END")
    crashed = False
    try:
        e4.advance(portfolio, as_of=observed_after(days[k]))
    except sqlite3.IntegrityError:
        crashed = True
    after_crash = signature(fresh(p4)(), portfolio)
    with closing(sqlite3.connect(p4)) as db, db:
        db.execute("DROP TRIGGER crash")
    e4 = fresh(p4)()
    for d in days[k:]:
        e4.advance(portfolio, as_of=observed_after(d))
    compare("crash_mid_commit_then_resume", signature(e4, portfolio),
            {"crash_raised": crashed, "state_unchanged_by_crash": after_crash == before})

    # S5 two processes advancing the same portfolio concurrently
    p5 = clone("concurrent")
    committed, errors = [], []

    def worker():
        try:
            committed.append(fresh(p5)().advance(portfolio, as_of=observed_after(last), max_days=31)["committed_days"])
        except Exception as exc:  # noqa: BLE001 - recorded as evidence
            errors.append(repr(exc))
    threads = [threading.Thread(target=worker) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    flat = [d for c in committed for d in c]
    sig5 = signature(fresh(p5)(), portfolio)
    results["concurrent_advance_3_threads"] = {
        "days_committed_total": len(flat), "days_committed_unique": len(set(flat)), "worker_errors": errors,
        "events_identical_to_continuous": sig5["events"] == ref["events"], "members_identical": sig5["members"] == ref["members"],
        "duplicate_trades": len(trade_keys(sig5)) - len(set(trade_keys(sig5)))}

    # S6 a later revision of an already processed day never replaces the first publication
    p6 = clone("revision")
    store6 = DailyPartitionStore(p6)
    d2 = days[1]
    frame, manifests = store6.read(registration["configuration"]["symbol"], d2, d2, as_of=observed_after(last))
    bid = frame[[f"bid_{c}" for c in FIELDS]].rename(columns=lambda c: c[4:]) + 0.5
    ask = frame[[f"ask_{c}" for c in FIELDS]].rename(columns=lambda c: c[4:]) + 0.5
    revised = store6.ingest("XAUUSD", d2, bid, ask, expected_minutes=bid.index,
                            observed_at=observed_after(d2) + timedelta(hours=6))
    e6 = fresh(p6)()
    late_cutoff = observed_after(last) + timedelta(days=1)
    out6 = e6.advance(portfolio, as_of=late_cutoff, max_days=31)
    sig6 = signature(e6, portfolio)
    results["late_revision_ignored"] = {"revision_detected": bool(revised.get("revision_detected")),
                                        "events_identical_to_continuous": sig6["events"] == ref["events"],
                                        "members_identical": sig6["members"] == ref["members"]}
    results["setup"] = {"case": name, "days": f"{first}..{last}", "ingest": stats}
    return results


if __name__ == "__main__":
    payload = {"scenarios": {}}
    for name in ("rich_m1_stop", "ema_rsi_m15"):
        spec, params, first, last, *_ = CASES[name]
        if name == "ema_rsi_m15":
            CASES[name] = (spec, params, first, first + timedelta(days=9))
        payload["scenarios"][name] = scenarios(name)
        print(name, json.dumps({k: v for k, v in payload["scenarios"][name].items() if k != "setup"}, default=str, indent=1), flush=True)
    print(save("check4_idempotence.json", payload))
