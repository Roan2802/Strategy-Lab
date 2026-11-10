# Startup test results — 2026-11-06

All price data used by the Strategy Lab remains rooted at `C:\AI\Dukascopy_data`. No live/paper trading was used.

| Scenario | Result | Evidence |
|---|---|---|
| Actual desktop shortcut properties/icon | PASS | `%USERPROFILE%\Desktop\Dukascopy Strategy Lab.lnk`; `evidence/screenshots/desktop-shortcut-custom-icon.jpg` |
| Docker Desktop stopped, then launcher invoked | PASS after fix | `startup-20261106-121842-15352.log`: Docker Engine 29.9.2, Jesse started, PostgreSQL healthy, backend HTTP 200, app window focused |
| First cold-start attempt | FAIL caught and repaired | `startup-20261106-121610-20604.log`: executable path resolved to `C`; visible error named the log. Fixed scalar indexing, then cold-start retested successfully |
| Jesse stopped while Docker remained available | PASS | `startup-20261106-120838-34484.log`; Jesse and DB readiness checks passed; no other Compose project was stopped |
| Python server unavailable | PASS recovery | Server PID was stopped after verifying the exact `python -m strategy_lab.server` command; launcher restarted one server and `/api/health` returned `ok` with the Duka root (see 11:xx startup logs) |
| Two rapid launcher invocations | PASS | One invocation logged mutex coalescing; one server process (PID 20344 at the time) and one exact-title app window (PID 25896) remained |
| All services already running | PASS | `startup-20261106-141913-11124.log`: Jesse/PostgreSQL reused; server reused on 8767; app mode opened/focused |
| Duplicate server prevention | PASS | Before/after process count remained 1; listener stayed on 8767 |
| No console window | PASS | VBS hidden launch and Python `Start-Process -WindowStyle Hidden`; logs persisted under local app data |
| App close/reopen | PASS, partial evidence | A prior exact-title app window was closed; relaunch opened the dedicated Edge app window. Native capture: `evidence/screenshots/desktop-app-window-native.png` |
| Port 8767 occupied by unrelated service | NOT TESTED | Not induced to avoid disrupting a possibly active local service |
| Dukascopy root unavailable | NOT TESTED | Original data folder was left untouched |
| Docker Desktop completely stopped and restarted | PASS | Docker Desktop stop reported success; it restarted via the launcher and Docker Engine became reachable in about 10 seconds |
| Windows reboot | NOT TESTED | No reboot performed |

The cold-start test confirmed the Compose invocation did not pull an image (`--pull never`). PostgreSQL was healthy before Jesse was accepted, and the server health response reported `source_root=C:\AI\Dukascopy_data`, `source_exists=true`, `mode=local-only`.

## Desktop launcher relaunch — 2026-11-06

After a research run was complete and no server job remained active, I relaunched the API with the existing desktop shortcut orchestrator (`desktop-launcher.ps1`) to apply local code updates. Exit code 0; port 8767 `/api/health` returned `ok`, `local-only`, and `C:\AI\Dukascopy_data`. This is a same-state relaunch check, not a new Docker-off cold-start or reboot test. The custom favicon endpoint also returned HTTP 200. See `evidence/test_outputs/dashboard_favicon_route_20261106.json`.

## Desktop shortcut path correction — 2026-11-06

The Windows known-folder API resolves the active Desktop to `C:\Users\Roanv\Desktop` (not OneDrive). The real shortcut exists there and its resolved target, hidden VBS arguments, working directory and project icon were checked with Windows Script Host COM. Existing cold-start evidence is unchanged; this metadata recheck did not repeat the cold-start scenario.


## Isolated launcher failure branches — 2026-11-06

The production `desktop-launcher.ps1` was copied into a temporary test directory. Only the test copy used port 18767, a temporary state/log directory and a no-op Docker-window handler. Its Docker CLI was a local stub that answered readiness checks; the real backend readiness URL was read-only.

- Missing Dukascopy source path: copy exited 1, logged the explicit source-unavailable error, and exited before invoking Docker. The real `C:\AI\Dukascopy_data` folder was untouched.
- Port conflict: a local listener owned by the test process held 18767. Copy exited 1, logged the conflict and stated that the listener would not be terminated. The listener remained active through the assertion; production port 8767 was not touched.

Machine-readable results: `evidence/test_outputs/launcher_failure_scenarios_20261106.json`; reproducible harness: `evidence/test_outputs/launcher_failure_scenarios_20261106.ps1`. These are isolated branch tests, not tests against an unrelated service on the production port, and they do not verify the native message box rendering. Windows reboot remains untested.

## Hidden launcher target reuse — 2026-11-06

Invoked the exact shortcut target through `wscript.exe` and `launch-hidden.vbs`. The launcher reused Jesse/PostgreSQL and the Strategy Lab server on 8767, reported healthy PostgreSQL and Jesse HTTP 200, and refreshed/focused one exact-title app window. `/api/health` returned `ok`, `local-only`, and `C:\AI\Dukascopy_data`; current listener PID was 18492. Raw startup log: `evidence/logs/startup-20261106-185924-32168.log`; structured result: `evidence/test_outputs/launcher_reuse_20261106.json`. This verifies the hidden launcher target and reuse path, not an Explorer double-click of the `.lnk`. No backtest or trade was started.

## Actual Desktop shortcut activation — 2026-11-06

The real `C:\Users\Roanv\Desktop\Dukascopy Strategy Lab.lnk` was opened from the active Desktop folder in Explorer. The launcher startup log `C:\Users\Roanv\AppData\Local\DukascopyStrategyLab\logs\startup-20261106-195804-35944.log` confirms Docker Engine 29.9.2, healthy PostgreSQL, Jesse HTTP 200, Strategy Lab health `ok`, source root `C:\AI\Dukascopy_data`, reuse of the existing port-8767 server and refresh/focus of exactly one titled app window. Current readback found one listener on 8767, one Strategy Lab app window, no visible Docker Desktop main window and no visible PowerShell/CMD window. The service had already been running, so this is actual-shortcut existing-service reuse, not a cold start. The UI automation's mouse double-click did not activate the link reliably; opening the selected link with Enter did. A literal human mouse double-click and Windows reboot remain unverified. Machine-readable evidence: `evidence/test_outputs/desktop_shortcut_live_acceptance_20261106.json`.

## Isolated offline launcher handoff — 2026-11-06

A disposable copy of the Windows launcher started PostgreSQL and Jesse on a Docker internal network, with a loopback proxy for local dashboard access. Jesse HTTP 200, PostgreSQL healthy, direct outbound TCP errno 101, hidden Strategy Lab HTTP 200 on 18767, and Edge app window open/focus were verified. The window was confirmed in CUA. Cleanup removed only project `dsltest-dd1c74ae` and its networks (no volumes), stopped the copied server, and left production port 8767 listening. Full production cold start under host-wide network denial and Windows reboot remain untested. Evidence: `evidence/test_outputs/offline_windows_launcher_acceptance_20261106.json` and `evidence/logs/offline_launcher_windows_20261106_success.log`.

## PostgreSQL unhealthy branch — isolated copy — 2026-11-06

PASS: a disposable copy of the launcher received a Docker shim reporting Engine ready and services running while `postgres` health stayed `unhealthy`. It exited 1 with the explicit logged timeout in 6.69 s after the test copy's timeout was reduced to 5 s. Production Compose and port 8767 were untouched. Evidence: `evidence/test_outputs/launcher_postgres_failure_20261106.json`; log: `evidence/logs/launcher-postgres-unhealthy-20261106.log`.

## Docker completely unavailable — isolated launcher copy — 2026-11-07

PASS: a local CLI shim failed every Docker command and the test copy was forced to report Docker Desktop unavailable. The launcher stopped in 0.52 s with the explicit Docker-unavailable error. The real Docker CLI/Desktop, production Compose, port 8767 and market data were untouched. Evidence: `evidence/test_outputs/launcher_docker_unavailable_20261107.json`; log: `evidence/logs/launcher-docker-unavailable-20261107.log`.

## Jesse Compose startup failure — isolated launcher copy — 2026-11-07

PASS: a Docker CLI shim reported Engine ready and no running services, then returned exit code 7 for `compose up`. The launcher returned exit code 1 and logged the clear missing-service startup failure. No real Docker command, production Compose file, port 8767 or market data was used. Evidence: `evidence/test_outputs/launcher_jesse_start_failure_20261107.json`; log: `evidence/logs/launcher-jesse-start-failure-20261107.log`.

## Desktop shortcut state readback — 2026-11-07

The configured desktop shortcut exists at `C:\Users\Roanv\Desktop\Dukascopy Strategy Lab.lnk`; its target is hidden `wscript.exe` launch of `launch-hidden.vbs` with the project icon. Current-state checks found one app server/listener on 8767, one titled Edge app window, no visible Docker Desktop window, and healthy local-only API using `C:\AI\Dukascopy_data`. This confirms presence and live reuse only; it does not replace the documented cold-start/reboot gaps. Evidence: `evidence/test_outputs/desktop_shortcut_current_state_20261107.json`.
