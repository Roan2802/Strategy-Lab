"""Strategy Lab Python plugins: own strategies with their own engine, run on Dukascopy Bid/Ask M1.

A plugin is a folder ``strategy_lab/plugins/<id>/`` with a ``plugin.py`` that defines

    PLUGIN = {"name": str, "version": str, "description": str, "symbols": [..], "params": [..]}
    def backtest(bid, ask, params, *, capital, start, end, stress=None, progress=None) -> dict

``bid``/``ask`` are UTC-indexed DataFrames (open, high, low, close, volume) that include a warm-up
prefix before ``start``; only trades entered in [start, end) may be returned. The result holds
``trades`` in the Strategy Lab trade contract (see STRATEGY_PLUGIN_GUIDE.md), optional ``equity``,
``summary`` and ``notes``. Plugins are local, user-owned code: they are imported, not sandboxed.
"""
from __future__ import annotations

import hashlib
import importlib.util
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
REQUIRED_TRADE_KEYS = ("entry_time", "exit_time", "side", "entry_price", "exit_price", "qty", "pnl")
_cache: dict = {}


def _folder_sha(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*.py")):
        digest.update(path.relative_to(folder).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def load(plugin_id: str):
    folder = ROOT / plugin_id
    if not plugin_id.isidentifier() or not (folder / "plugin.py").is_file():
        raise KeyError(f"Onbekende plugin: {plugin_id}")
    sha = _folder_sha(folder)
    hit = _cache.get(plugin_id)
    if hit and hit[0] == sha:
        return hit[1]
    spec = importlib.util.spec_from_file_location(f"strategy_lab_plugin_{plugin_id}", folder / "plugin.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    meta = getattr(module, "PLUGIN", None)
    if not isinstance(meta, dict) or not callable(getattr(module, "backtest", None)):
        raise ValueError(f"Plugin {plugin_id} mist PLUGIN of backtest()")
    module.PLUGIN_SHA256 = sha
    _cache[plugin_id] = (sha, module)
    return module


def available() -> list[dict]:
    out = []
    for folder in sorted(ROOT.iterdir()):
        if not (folder / "plugin.py").is_file():
            continue
        try:
            module = load(folder.name)
            out.append({"id": folder.name, **module.PLUGIN, "sha256": module.PLUGIN_SHA256})
        except Exception as exc:  # show a broken plugin instead of hiding it
            out.append({"id": folder.name, "name": folder.name, "error": str(exc)})
    return out


def defaults(module) -> dict:
    return {p["name"]: p.get("default") for p in module.PLUGIN.get("params", [])}


def check_trades(trades: list, start, end) -> list[str]:
    """Contract check for plugin output; returns problems (empty = valid)."""
    problems = []
    s, e = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    for i, t in enumerate(trades):
        missing = [k for k in REQUIRED_TRADE_KEYS if k not in t]
        if missing:
            problems.append(f"trade {i}: ontbrekende velden {missing}")
            continue
        entry, exit_ = pd.Timestamp(t["entry_time"]), pd.Timestamp(t["exit_time"])
        if entry.tzinfo is None or exit_.tzinfo is None:
            problems.append(f"trade {i}: tijden moeten UTC met tijdzone zijn")
        elif not (s <= entry < e) or exit_ < entry:
            problems.append(f"trade {i}: entry {entry} buiten [{s}, {e}) of exit vóór entry")
        if t["side"] not in ("long", "short"):
            problems.append(f"trade {i}: side moet long of short zijn")
        if not math.isfinite(float(t["pnl"])):
            problems.append(f"trade {i}: pnl is geen getal")
    return problems


def run(plugin_id: str, bid, ask, params: dict | None, *, capital: float, start: str, end: str,
        stress: dict | None = None, progress=None) -> dict:
    module = load(plugin_id)
    merged = {**defaults(module), **(params or {})}
    result = module.backtest(bid, ask, merged, capital=capital, start=start, end=end, stress=stress or {},
                             progress=progress)
    trades = result.get("trades", [])
    problems = check_trades(trades, start, end)
    if problems:
        raise ValueError("Plugin-uitvoer voldoet niet aan het contract: " + "; ".join(problems[:5]))
    return {**result, "engine": f"PLUGIN:{plugin_id}", "plugin_sha256": module.PLUGIN_SHA256, "params": merged}
