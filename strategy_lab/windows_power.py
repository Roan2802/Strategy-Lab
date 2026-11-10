"""Read recent power-state events for the local synchronization monitor."""
from __future__ import annotations

import json
import os
import subprocess


EVENTS = {
    ("Microsoft-Windows-Kernel-General", 12): "STARTED",
    ("Microsoft-Windows-Kernel-General", 13): "SHUTDOWN",
    ("Microsoft-Windows-Kernel-Power", 41): "UNCLEAN_RESTART",
    ("Microsoft-Windows-Kernel-Power", 42): "SLEEP",
    ("Microsoft-Windows-Power-Troubleshooter", 1): "RESUMED",
}

SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$query = "*[System[TimeCreated[timediff(@SystemTime) <= 604800000] and (" +
    "(Provider[@Name='Microsoft-Windows-Kernel-General'] and (EventID=12 or EventID=13)) or " +
    "(Provider[@Name='Microsoft-Windows-Kernel-Power'] and (EventID=41 or EventID=42)) or " +
    "(Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1))]]"
try {
    $events = @(Get-WinEvent -LogName System -FilterXPath $query -MaxEvents 40 | ForEach-Object {
        @{ provider=$_.ProviderName; id=$_.Id; at=$_.TimeCreated.ToUniversalTime().ToString('o') }
    })
} catch {
    if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { throw }
    $events = @()
}
ConvertTo-Json -InputObject $events -Compress
"""


def recent_power_events() -> dict:
    if os.name != "nt":
        return {"available": False, "events": []}
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", SCRIPT],
            capture_output=True, encoding="utf-8-sig", timeout=5, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        records = json.loads(result.stdout)
        events = [{"kind": EVENTS[(row["provider"], row["id"])], "at": row["at"]} for row in records]
        return {"available": True, "events": events}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return {"available": False, "events": []}
