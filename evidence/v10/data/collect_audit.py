"""Collect install_audit.json + live_check.json (read-only against the real installation)."""
import json, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from strategy_lab import data_supervisor
from strategy_lab.data_manager import DEFAULT_SOURCE

out = Path(__file__).parent
def stat():
    return {p.name: [p.stat().st_size, p.stat().st_mtime_ns] for p in sorted(DEFAULT_SOURCE.glob("*.csv"))}
before = stat()
data_supervisor._PROBE_CACHE.update(at=0.0, value=None)
sup = data_supervisor.Supervisor()
status = sup.status()
after = stat()
(out / "live_check.json").write_text(json.dumps({"collected_at": datetime.now(timezone.utc).isoformat(), "supervisor": status,
    "original_csv_count": len(before), "original_csvs_unchanged_size_mtime": before == after,
    "note": "49 GB of originals: size+mtime compared (DataManager manifests hold their sha256); supervisor only reads file tails."}, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: status[k] for k in ("overall", "overall_nl", "scheduler", "worker", "updater", "issues")}, indent=1, default=str))
print("originals unchanged:", before == after, len(before))
