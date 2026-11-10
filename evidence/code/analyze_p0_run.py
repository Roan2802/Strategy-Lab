import csv
import json
import re
from collections import Counter
from pathlib import Path

root = Path(r"C:\AI\Jesse\Jesse-Audit\evidence")
source = root / "results" / "P0_XAUUSD_backtest_logs.txt"
out = root / "results" / "P0_XAUUSD_inspector_events.jsonl"
trades_path = root / "results" / "P0_XAUUSD_trades.csv"
events = []
for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
    marker = "P0_INSPECTOR_JSON "
    if marker not in line:
        continue
    payload = line.split(marker, 1)[1]
    try:
        item = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in structured log: {exc}")
    events.append(item)
with out.open("w", encoding="utf-8", newline="\n") as f:
    for item in events:
        f.write(json.dumps(item, separators=(",", ":"), allow_nan=False) + "\n")
counts = Counter(x.get("event", "CANDLE") for x in events)
decisions = Counter(x["decision"] for x in events if "decision" in x)
reasons = Counter(x["reason"] for x in events if "reason" in x)
trades = list(csv.DictReader(trades_path.open(encoding="utf-8-sig", newline="")))
summary = {
    "log_lines": len(source.read_text(encoding="utf-8", errors="replace").splitlines()),
    "structured_events": len(events),
    "event_types": dict(counts),
    "candle_decisions": dict(decisions),
    "candle_reasons": dict(reasons),
    "trade_rows": len(trades),
    "trade_rows_with_entry_timestamp": sum(bool(x.get("opened_at")) for x in trades),
    "structured_events_jsonl": out.name,
    "sample_trade": trades[0] if trades else None,
    "sample_position_open": next((x for x in events if x.get("event") == "POSITION_OPEN"), None),
    "sample_position_close": next((x for x in events if x.get("event") == "POSITION_CLOSE"), None),
}
(root / "results" / "P0_XAUUSD_run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps(summary, indent=2))
