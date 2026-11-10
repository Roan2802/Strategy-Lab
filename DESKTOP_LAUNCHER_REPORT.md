# Desktop launcher report — 2026-11-06

## Installed entry point

- Windows desktop shortcut: `C:\Users\Roanv\Desktop\Dukascopy Strategy Lab.lnk` (resolved with the configured Desktop known folder; this machine does not redirect Desktop to OneDrive).
- Shortcut target: `C:\Windows\System32\wscript.exe`, calling `C:\AI\Jesse\Jesse-Audit\launch-hidden.vbs`.
- Shortcut icon: `assets\dukascopy-strategy-lab.ico`; its presence and shortcut properties were read back from Windows Explorer/COM. A desktop screenshot is saved as `evidence\screenshots\desktop-shortcut-custom-icon.jpg`.
- App window: Microsoft Edge `--app` mode, exact title `Dukascopy Strategy Lab`, maximized, no address bar. A native Windows window capture is in `evidence\screenshots\desktop-app-window-native.png`.

## Startup behavior

`launch-hidden.vbs` starts `desktop-launcher.ps1` without a console. The launcher serializes starts with a per-user mutex, checks the Dukascopy source root and compose file, checks Docker Engine health, starts Docker Desktop if needed, starts only missing Jesse Compose services with `--pull never`, waits for PostgreSQL health and Jesse HTTP 200, then starts/health-checks the local Strategy Lab server on 8767. A server on 8767 that does not answer the expected Duka-only health response is left untouched and reported as a conflict. Hidden Python stdout/stderr and per-start logs are kept under `%LOCALAPPDATA%\DukascopyStrategyLab\logs`.

Second launch is serialized; existing servers and services are reused and an existing exact-title app window is focused. Closing the UI does not stop Docker, Jesse, the database, or a running research request. No live or paper trading is invoked.

## Fix found by the cold-start test

The first actual Docker Desktop cold-start exposed PowerShell scalar-string indexing in executable-path resolution: a one-item candidate path was reduced to its first character (`C`). The startup error dialog correctly displayed the log path. Candidate selection now takes the first pipeline result, and browser candidates are explicitly arrays. Retest from Docker Desktop stopped succeeded: Docker Engine 29.9.2 responded, PostgreSQL became healthy, the missing Jesse service started, Jesse returned HTTP 200, the existing local server reported `C:\AI\Dukascopy_data`, and Edge app mode opened/focused.

## Boundaries still open

- At initial setup, the native icon was not checked; the later Windows pass verified the refreshed app icon and pinned taskbar entry below. The desktop shortcut icon is verified.
- Windows virtual-desktop placement is left to normal Windows behavior: a desktop shortcut is shared and the app launches on the desktop from which it is opened. No undocumented virtual-desktop pinning is attempted.
- Port-conflict and missing-Dukascopy-folder UI failure cases were not induced. The code path checks them and logs/shows an error; they are not claimed as runtime-tested.
- The complete Windows reboot test was not run.

## Single-window and background Docker follow-up — 2026-11-06

- The launcher now enumerates all visible top-level windows with the exact Strategy Lab title. On repeat launch it focuses and refreshes the existing app window, then closes any additional exact-title dashboard windows.
- After Docker Engine readiness, it hides visible top-level windows owned by Docker Desktop processes. It does not stop the Engine, WSL2, containers, Jesse, or PostgreSQL.
- Runtime check found two exact-title Strategy Lab app windows. Running the launcher again refreshed one and closed the other. A second run found one window and refreshed it.
- Docker Desktop was still running with Jesse/PostgreSQL healthy; the window inventory no longer showed a Docker Desktop UI window after the launcher completed.
- The extra local Strategy Lab test listener on 8768 (PID 16276, confirmed command line explicitly set `STRATEGY_LAB_PORT=8768`) was stopped. Port 8767 remains healthy with source `C:\AI\Dukascopy_data`.
- PowerShell parser passed and `python -m pytest strategy_lab/tests -q` passed: 47 tests.
- Evidence/limits: one single-window cleanup was exercised; concurrent double-click stress is not rerun here. Windows reboot, forced port collision, and unavailable-data-folder tests remain untested. This does not close the open quantitative issues listed in `REMAINING_ISSUES.md`.

## Desktop-known-folder recheck — 2026-11-06

Windows reports the configured Desktop known folder as `C:\Users\Roanv\Desktop` on this machine; it is not OneDrive-redirected. The actual `Dukascopy Strategy Lab.lnk` there resolves to `wscript.exe` with `launch-hidden.vbs`, working directory `C:\AI\Jesse\Jesse-Audit`, and project icon `assets\dukascopy-strategy-lab.ico`. API health during the check was `ok`, `local-only`, source root `C:\AI\Dukascopy_data`. This verifies shortcut metadata and current service health, not a fresh cold-start in this continuation.

## API relaunch and page icon check — 2026-11-06

The existing server process retained pre-edit Python code because Uvicorn runs with `reload=False`. After the accepted optimizer run completed, I relaunched via `desktop-launcher.ps1`; the launcher exited 0, the API reported `ok` and the configured Duka root, and `/assets/dukascopy-strategy-lab.ico` returned HTTP 200 (`image/x-icon`, 2,686 bytes). ChatGPT IAB reloaded the default dashboard and showed the icon reference and all nine optimizer rows. This test verified page favicon delivery. The later Windows pass verified the refreshed app icon and pinned taskbar entry below. Evidence: `evidence/test_outputs/dashboard_favicon_route_20261106.json`.

## High-resolution icon refresh — 2026-11-06

The custom app artwork is now a 1254×1254 PNG plus a Windows ICO containing 16, 24, 32, 48, 64, 128 and 256 px images. The desktop shortcut still resolves to `C:\AI\Jesse\Jesse-Audit\assets\dukascopy-strategy-lab.ico,0`; the local page serves that updated ICO with HTTP 200 and 81,411 bytes. Strategy Lab tests: 48 passed; JavaScript syntax check passed. The live Edge window/taskbar icon was not visually rechecked because the user stopped computer control with Escape.


## PWA icon / Start registration follow-up — 2026-11-06

The local PWA manifest is now installed in the user's Edge profile. `Get-StartApps` reports `Dukascopy Strategy Lab` with AppID `127.0.0.1-AF3C0F21_9y8g6evm4b6x0!App`; the launched standalone window title is exactly `Dukascopy Strategy Lab` and its titlebar displays the custom manifest icon. The existing desktop shortcut remains `C:\Users\Roanv\Desktop\Dukascopy Strategy Lab.lnk`, targets `launch-hidden.vbs`, and explicitly uses `assets\dukascopy-strategy-lab.ico`. No second desktop shortcut was created. The install prompt showed taskbar and Start pinning selected; its state was confirmed later from the AppsFolder context verb.

## Current resource and taskbar status supersession — 2026-11-06

The PWA follow-up confirms the custom icon in the installed Strategy Lab app window and confirms Edge app registration. The desktop shortcut uses the high-resolution project ICO. The later live Windows pass confirmed the Edge PWA app icon and taskbar pin through the AppsFolder context verb; see the icon refresh section below.


## Isolated launcher failure branches — 2026-11-06

The production `desktop-launcher.ps1` was copied into a temporary test directory. Only the test copy used port 18767, a temporary state/log directory and a no-op Docker-window handler. Its Docker CLI was a local stub that answered readiness checks; the real backend readiness URL was read-only.

- Missing Dukascopy source path: copy exited 1, logged the explicit source-unavailable error, and exited before invoking Docker. The real `C:\AI\Dukascopy_data` folder was untouched.
- Port conflict: a local listener owned by the test process held 18767. Copy exited 1, logged the conflict and stated that the listener would not be terminated. The listener remained active through the assertion; production port 8767 was not touched.

Machine-readable results: `evidence/test_outputs/launcher_failure_scenarios_20261106.json`; reproducible harness: `evidence/test_outputs/launcher_failure_scenarios_20261106.ps1`. These are isolated branch tests, not tests against an unrelated service on the production port, and they do not verify the native message box rendering. Windows reboot remains untested.

## Icon and taskbar refresh — 2026-11-06

- Updated the editable SVG mark and regenerated the desktop PNG at 2048×2048, the 192/512 PWA icons, and a multi-size ICO from 16 through 256 px. The same artwork is used by the desktop shortcut and PWA manifest.
- Versioned the favicon and PWA manifest/icon URLs, mirrored the ICO into the app static assets, and refreshed the existing desktop shortcut icon. Relaunching the shortcut opened one `Dukascopy Strategy Lab` window; the existing Edge taskbar pin remains installed.
- Simplified `Beeld → Kleurthema`: its choices are `Donker` and `Licht`; hovering/opening `Licht` reveals `Lichtgroen`, `Lichtroze` and `Lichtrood` in the adjacent flyout. ChatGPT IAB showed the flyouts, applied the green theme, then restored dark.
- Verification: all icon and manifest routes returned HTTP 200; the 2048 PNG and ICO sizes were inspected; `python -m pytest strategy_lab/tests -q` passed 53 tests; `node --check strategy_lab/static/app.js` and `python -m compileall -q strategy_lab` passed. Evidence: `evidence/test_outputs/ui_icon_theme_refresh_20261106.json`.
- No Dukascopy or MT5 source data was changed and no trade was run.

## Hidden launcher target reuse — 2026-11-06

The exact `wscript.exe` → `launch-hidden.vbs` shortcut target was invoked again. It reused the healthy Docker/Jesse/PostgreSQL services and the existing local server (port 8767), refreshed/focused one exact-title Strategy Lab app window, and left one server listener. Health returned `ok`, `local-only`, and `C:\AI\Dukascopy_data`; current server PID was 18492. Evidence: `evidence/test_outputs/launcher_reuse_20261106.json` and `evidence/logs/startup-20261106-185924-32168.log`. This was not an Explorer double-click test of the `.lnk` itself. No backtest or trade was started.

## Current Desktop shortcut acceptance — 2026-11-06

Actual Desktop-folder activation of the existing `.lnk` is now confirmed via Explorer. The logged launch reused the healthy Docker/Jesse/PostgreSQL/8767 stack and one existing Edge app window; no duplicate Strategy Lab server or visible Docker/terminal window appeared. The launcher log names only `C:\AI\Dukascopy_data`. This was a reuse run, not a cold start. The UI automation could not reliably issue a literal mouse double-click; it opened the selected `.lnk` with Enter. Windows reboot and offline cold start remain open. See `evidence/test_outputs/desktop_shortcut_live_acceptance_20261106.json`.

## Asset refresh — 2026-11-06

The project SVG was simplified and rendered to a 4096×4096 master PNG, a standard Windows ICO with 16–256px frames, and 192/512px PWA icons. The real Desktop `.lnk` now points to `assets/dukascopy-strategy-lab-refresh-4096.ico`; its hidden WScript/VBS launcher target and arguments are unchanged. The live 8767 service serves favicon and manifest cache version `20261106.6`. The native Edge app could not be refreshed through computer control in this continuation, so the new taskbar art has not been visually reconfirmed. Evidence: `evidence/test_outputs/icon_theme_desktop_refresh_20261106.json`.

## Disposable offline handoff — 2026-11-06

A copied launcher and Compose project opened the hidden local server and a focused Edge app window while the Jesse container had no outbound route. The test-only containers/network and server were stopped without volume deletion; production 8767 stayed live. This is not a production Compose offline restart or Windows reboot test. Evidence: `evidence/test_outputs/offline_windows_launcher_acceptance_20261106.json`.
