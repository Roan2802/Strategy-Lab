"""Command line for the data supervisor.

  python -m strategy_lab.data_check                 print the supervisor status as JSON (no provider request)
  python -m strategy_lab.data_check --scheduled     what the scheduled task runs: heartbeat, then a sync only when
                                                    a complete session day is missing and no backoff covers it
  python -m strategy_lab.data_check --sync          status, then one sync that respects every stored backoff
Scheduled runs exit 0 when they finished (even with days still waiting); a non-zero exit means a real failure.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .daily_sync import DEFAULT_SYNC_CONFIG, SyncBusy, run_sync
from .data_manager import DEFAULT_DAILY_STORE
from .data_supervisor import SCHEDULER_INTERVAL_S, Supervisor


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_SYNC_CONFIG)
    parser.add_argument("--store", type=Path, default=DEFAULT_DAILY_STORE)
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--compact", action="store_true", help="Print only the overall state and per-symbol lines")
    args = parser.parse_args(argv)
    supervisor = Supervisor(args.config, args.store)
    if args.scheduled:
        resumed = supervisor.tick(SCHEDULER_INTERVAL_S)
        repaired = supervisor.repair()
        gate = supervisor.needs_fetch()
        outcome = {"resumed": resumed, "repaired": len(repaired["quarantined"]), **gate}
        if gate["fetch"]:
            try:
                result = run_sync(supervisor.store(), supervisor.config(), manual=False)
                outcome["sync"] = result["status"]
            except SyncBusy:
                outcome["sync"] = "BUSY"
        supervisor.put("last_check_at", supervisor.clock().isoformat(timespec="seconds"))
        supervisor.put("last_scheduled", outcome)
        print(json.dumps(outcome))
        return 0
    if args.sync:
        supervisor.repair()
        supervisor.check_sync(wait=True)
    data = supervisor.status()
    if args.compact:
        print(data["overall"], "-", data["overall_nl"])
        for s in data["symbols"]:
            print(f"{s['symbol']:14} {s['status']:13} lag={s['lag_days']:<3} last={s['last_candle']} next={s['next_attempt']} {s['reason_nl']}")
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
