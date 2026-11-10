"""Verify deterministic generic prefix replay under mutated post-cursor Duka rows."""
import csv
import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "d977062a-c73f-46eb-9f48-fb4d9215a8d3"
CURSOR_MS = 1789356900000
APPROVED_DATA = Path(r"C:\AI\Dukascopy_data")
REQUESTS = ROOT / "runtime" / "strategy_lab" / "requests"
RESULTS = ROOT / "runtime" / "strategy_lab" / "results"
EVIDENCE = ROOT / "evidence" / "test_outputs" / "generic_replay_future_mutation_20261107.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def normalized_decision(event: dict) -> dict:
    value = dict(event)
    value.pop("event_id", None)
    value.pop("run_id", None)
    state = dict(value.get("state") or {})
    if "pending_order_ids" in state:
        state["pending_order_count"] = len(state.pop("pending_order_ids") or [])
    value["state"] = state
    return value


def execute(request_id: str, payload: dict) -> Path:
    request_file = REQUESTS / f"{request_id}.json"
    request_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    result = subprocess.run([
        "docker", "compose", "-f", str(ROOT / "runtime" / "docker" / "docker-compose.yml"),
        "exec", "-T", "jesse", "python", "/home/strategy_lab/jesse_runner.py",
        f"/home/strategy_lab/requests/{request_id}.json"],
        cwd=ROOT / "runtime", capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError((result.stdout + "\n" + result.stderr)[-5000:])
    return RESULTS / request_id


def main() -> None:
    REQUESTS.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "strategy_lab" / "runs" / RUN_ID / "manifest.json").read_text(encoding="utf-8"))
    if Path(manifest["source_root"]).resolve() != APPROVED_DATA.resolve():
        raise RuntimeError("Seed run does not use the approved Dukascopy source")
    if not all(Path(manifest["dataset_manifest"][side]["path"]).resolve().is_relative_to(APPROVED_DATA.resolve())
               for side in ("bid_source", "ask_source")):
        raise RuntimeError("Seed manifest contains a price file outside Dukascopy_data")
    seed = ROOT / "strategy_lab" / "runs" / RUN_ID / "input_candles.csv"
    request_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    request_paths = [REQUESTS / f"{request_id}_future_mutation.csv" for request_id in request_ids]
    result_paths = [RESULTS / request_id for request_id in request_ids]
    rows = list(csv.DictReader(seed.open("r", newline="", encoding="utf-8")))
    timestamp_field = "timestamp"
    future = [row for row in rows if int(float(row[timestamp_field])) > CURSOR_MS]
    if not future:
        raise RuntimeError("Seed data has no post-cursor candles to mutate")
    with seed.open("rb") as stream:
        seed_hash = hashlib.sha256(stream.read()).hexdigest()

    try:
        for index, request_id in enumerate(request_ids):
            target = request_paths[index]
            copied = [dict(row) for row in rows]
            if index:
                for row in copied:
                    if int(float(row[timestamp_field])) > CURSOR_MS:
                        for key in ("open", "high", "low", "close"):
                            row[key] = str(float(row[key]) + 10000.0)
            with target.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(copied[0]))
                writer.writeheader()
                writer.writerows(copied)
            payload = {
                "action": "backtest", "run_id": request_id, "strategy": manifest["strategy"],
                "strategy_sha256": manifest["strategy_sha256"], "dataset_sha256": manifest["dataset_sha256"],
                "candles_file": target.name, "start_ms": 1789344000000,
                "end_ms": CURSOR_MS + 60000, "warmup_candles": manifest["warmup_candles"],
                "parameters": manifest["parameters"], "symbol": manifest["symbol"],
                "jesse_symbol": manifest["jesse_symbol"], "timeframe": manifest["timeframe"],
                "starting_balance": manifest["starting_balance"], "fee": manifest["fee"],
                "account_type": manifest["account_type"], "leverage": manifest["leverage"],
            }
            execute(request_id, payload)

        event_sets = []
        for result_dir in result_paths:
            with (result_dir / "events.jsonl").open("r", encoding="utf-8") as stream:
                events = [json.loads(line) for line in stream if line.strip()]
            prefix = [event for event in events if int(event.get("timestamp_ms", -1)) <= CURSOR_MS]
            if any(int(event.get("timestamp_ms", -1)) > CURSOR_MS for event in events):
                raise RuntimeError("Runner emitted an event after the requested cursor")
            event_sets.append(prefix)
        left = [normalized_decision(e) for e in event_sets[0] if e.get("event_type") == "CANDLE_DECISION"]
        right = [normalized_decision(e) for e in event_sets[1] if e.get("event_type") == "CANDLE_DECISION"]
        if left != right:
            raise RuntimeError("Decision trace differs after mutating only post-cursor candle rows")
        if len(left) != 216:
            raise RuntimeError(f"Expected 216 prefix decisions, found {len(left)}")
        if sha256(seed) != seed_hash:
            raise RuntimeError("Saved Dukascopy-derived seed copy changed during the check")
        evidence = {
            "status": "PASS", "strategy": manifest["strategy"], "seed_run_id": RUN_ID,
            "cursor_ms": CURSOR_MS, "future_rows_mutated": len(future),
            "mutation": "Added 10000 to Open/High/Low/Close only after cursor in a temporary copy.",
            "prefix_decisions_compared": len(left), "prefix_traces_equal": True,
            "post_cursor_events_emitted": [sum(1 for e in events if int(e.get("timestamp_ms", -1)) > CURSOR_MS) for events in event_sets],
            "future_candles_supplied_to_jesse": False,
            "source_root": str(APPROVED_DATA), "seed_input_sha256_unchanged": sha256(seed) == seed_hash,
            "run_ids": request_ids,
        }
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps(evidence, indent=2))
    finally:
        for path in request_paths:
            path.unlink(missing_ok=True)
        for request_id in request_ids:
            (REQUESTS / f"{request_id}.json").unlink(missing_ok=True)
            result_dir = RESULTS / request_id
            if result_dir.exists() and result_dir.resolve().parent == RESULTS.resolve():
                shutil.rmtree(result_dir)


if __name__ == "__main__":
    main()
