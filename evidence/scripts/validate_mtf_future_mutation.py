"""Check MTF replay invariance after mutating post-cursor Duka-derived M1 rows."""
import csv
import argparse
import hashlib
import json
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "02cff6de-ef4a-4f64-a1ae-a4213e826375"
CURSOR_MS = 1789398000000  # 2026-09-14 15:00 UTC
APPROVED_DATA = Path(r"C:\AI\Dukascopy_data")
REQUESTS = ROOT / "runtime" / "strategy_lab" / "requests"
RESULTS = ROOT / "runtime" / "strategy_lab" / "results"
RUNS = ROOT / "strategy_lab" / "runs"
EVIDENCE_ROOT = ROOT / "evidence" / "test_outputs"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize(event: dict) -> dict:
    event = dict(event)
    event.pop("event_id", None)
    event.pop("run_id", None)
    return event


def run(request_id: str, payload: dict) -> Path:
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=RUN_ID)
    parser.add_argument("--cursor-ms", type=int, default=CURSOR_MS)
    args = parser.parse_args()
    run_id = args.run_id
    cursor_ms = args.cursor_ms
    manifest_path = RUNS / run_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    seed = RUNS / run_id / "input_candles.csv"
    if Path(manifest["source_root"]).resolve() != APPROVED_DATA.resolve():
        raise RuntimeError("Seed run does not use the approved Dukascopy source")
    if not all(Path(manifest["dataset_manifest"][side]["path"]).resolve().is_relative_to(APPROVED_DATA.resolve())
               for side in ("bid_source", "ask_source")):
        raise RuntimeError("Seed manifest contains a price file outside Dukascopy_data")
    routes = manifest.get("data_routes", [])
    if len(routes) != 1 or routes[0].get("symbol") != manifest.get("jesse_symbol"):
        raise RuntimeError("Seed run must declare one same-symbol supplemental timeframe feed")
    timeframe = str(manifest["parameters"].get("data_timeframe", "")).upper()
    indicator_name = f"{timeframe} EMA"
    if not timeframe or not routes[0].get("timeframe"):
        raise RuntimeError("Seed run is missing its declared supplemental timeframe")
    original_hash = sha256(seed)
    with seed.open("r", newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    future = [row for row in rows if int(float(row["timestamp"])) > cursor_ms]
    if not future:
        raise RuntimeError("Seed input has no future rows to mutate")

    saved_events_path = RESULTS / run_id / "events.jsonl"
    with saved_events_path.open("r", encoding="utf-8") as stream:
        saved_events = [json.loads(line) for line in stream if line.strip()]
    expected_decisions = sum(1 for event in saved_events
                             if event.get("event_type") == "CANDLE_DECISION"
                             and int(event.get("timestamp_ms", -1)) <= cursor_ms)
    if not expected_decisions:
        raise RuntimeError("Seed run has no saved candle-decision trace through the selected cursor")

    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    csv_paths = [REQUESTS / f"{request_id}_mtf_future_mutation.csv" for request_id in ids]
    result_paths = [RESULTS / request_id for request_id in ids]
    try:
        for index, request_id in enumerate(ids):
            copied = [dict(row) for row in rows]
            if index:
                for row in copied:
                    if int(float(row["timestamp"])) > cursor_ms:
                        for key in ("open", "high", "low", "close"):
                            row[key] = str(float(row[key]) + 10000.0)
            with csv_paths[index].open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(copied[0]))
                writer.writeheader()
                writer.writerows(copied)
            payload = {
                "action": "backtest", "run_id": request_id, "strategy": manifest["strategy"],
                "strategy_sha256": manifest["strategy_sha256"], "dataset_sha256": manifest["dataset_sha256"],
                "candles_file": csv_paths[index].name,
                "start_ms": int(datetime.strptime(manifest["start_date"], "%Y-%m-%d")
                                .replace(tzinfo=timezone.utc).timestamp() * 1000),
                "end_ms": cursor_ms + 60000,
                "warmup_candles": manifest["warmup_candles"],
                "parameters": manifest["parameters"], "symbol": manifest["symbol"],
                "jesse_symbol": manifest["jesse_symbol"], "timeframe": manifest["timeframe"],
                "starting_balance": manifest["starting_balance"], "fee": manifest["fee"],
                "account_type": manifest["account_type"], "leverage": manifest["leverage"],
            }
            run(request_id, payload)

        event_sets = []
        for result_dir in result_paths:
            with (result_dir / "events.jsonl").open("r", encoding="utf-8") as stream:
                events = [json.loads(line) for line in stream if line.strip()]
            if any(int(event.get("timestamp_ms", -1)) > cursor_ms for event in events):
                raise RuntimeError("Runner emitted an event after the requested cursor")
            event_sets.append([normalize(event) for event in events if event.get("event_type") == "CANDLE_DECISION"])
        if event_sets[0] != event_sets[1]:
            raise RuntimeError("MTF decisions changed after mutating only post-cursor M1 rows")
        if len(event_sets[0]) != expected_decisions:
            raise RuntimeError(f"Expected {expected_decisions} saved decisions through the cursor, found {len(event_sets[0])}")
        if sha256(seed) != original_hash:
            raise RuntimeError("Saved Duka-derived input copy changed during the test")
        indicator = next((item for item in event_sets[0][-1].get("indicators", [])
                          if item.get("name") == indicator_name), None)
        if not indicator or indicator.get("timeframe") != timeframe or indicator.get("value") is None:
            raise RuntimeError(f"The selected MTF decision did not contain a computed {timeframe} indicator")
        evidence_path = EVIDENCE_ROOT / f"generic_replay_mtf_{timeframe.lower()}_future_mutation_20261107.json"
        evidence = {
            "status": "PASS", "strategy": manifest["strategy"], "seed_run_id": run_id,
            "cursor_ms": cursor_ms, "data_timeframe": timeframe, "data_routes": routes,
            "future_rows_mutated": len(future),
            "mutation": "Added 10000 to Open/High/Low/Close only after the cursor in a temporary CSV copy.",
            "warmup_candles": manifest["warmup_candles"],
            "prefix_decisions_compared": len(event_sets[0]), "expected_saved_decisions": expected_decisions,
            "prefix_traces_equal": True, "last_higher_timeframe_indicator": indicator,
            "post_cursor_events_emitted": [0, 0], "future_candles_supplied_to_jesse": False,
            "source_root": str(APPROVED_DATA), "seed_input_sha256_unchanged": sha256(seed) == original_hash,
            "run_ids": ids, "evidence_path": str(evidence_path.relative_to(ROOT)),
        }
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps(evidence, indent=2))
    finally:
        for path in csv_paths:
            path.unlink(missing_ok=True)
        for request_id in ids:
            (REQUESTS / f"{request_id}.json").unlink(missing_ok=True)
            result_dir = RESULTS / request_id
            if result_dir.exists() and result_dir.resolve().parent == RESULTS.resolve():
                shutil.rmtree(result_dir)


if __name__ == "__main__":
    main()
