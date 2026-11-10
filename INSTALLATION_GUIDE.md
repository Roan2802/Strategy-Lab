# Jesse-Audit install and startup guide

## Installed components

- Jesse `v3.6.0` source commit `8665b253d43af7b06a88c6ebcda7a0b6393ec937`, MIT; Docker image `salehmir/jesse:latest` (recorded digest `sha256:53dced6824be8d20de18afc5b1057b327f6c4c8a82c560a37b12e5aa4585a3dd`).
- Docker Desktop `4.98.0.245422`, Engine `29.9.2`; Compose project `runtime\docker`.
- Jesse dashboard `http://127.0.0.1:9000`; local Dukascopy Strategy Lab `http://127.0.0.1:8767`.
- Strategy Lab host Python uses the existing Windows Python environment; validated with Python 3.11.9. No global packages were installed in this validation pass.

## Start

For normal use, double-click the Windows desktop shortcut **Dukascopy Strategy Lab**. It starts Docker Desktop/WSL2 when needed, waits for Jesse and PostgreSQL, starts or reuses Strategy Lab on port 8767, and opens the Edge app window. No terminal or admin rights are required. Closing that window leaves background services and active research jobs running. Startup logs are in `%LOCALAPPDATA%\DukascopyStrategyLab\logs`.

The legacy project launchers `start.bat` and `start-strategy-lab.bat` call the same hidden launcher. If Jesse is already running, start only the lab from PowerShell:

```powershell
Set-Location C:\AI\Jesse\Jesse-Audit
python -m strategy_lab.server
```

Or start only Jesse:

```powershell
Set-Location C:\AI\Jesse\Jesse-Audit\runtime\docker
docker compose up -d
docker compose ps
```

Open the local dashboard or lab URLs above. Stop Strategy Lab with Ctrl+C. Stop Jesse without deleting its database:

```powershell
Set-Location C:\AI\Jesse\Jesse-Audit\runtime\docker
docker compose stop
```

Do not use `docker compose down -v`; that would delete persistent database volumes. Dashboard credentials remain in ignored `runtime\.env` and are not included here.

## Dukascopy-only source rule

The Strategy Lab reads source M1 CSV files only from `C:\AI\Dukascopy_data`, and only symbol pairs with both Dukascopy Bid and Ask files. It ignores tick files and other folders/formats. Raw files are never modified. Derived Parquet caches live in `strategy_lab\data_cache`; Jesse requests, run manifests and reports live in this audit project. It does not read MT5 history or any other market feed.

To run the interface locally, select a pair discovered from that directory, symbol/timeframe, date range and strategy; load the chart; then run a backtest. A date-only end field is inclusive through that UTC calendar day. Treat Jesse's net P&L as provisional until broker-specific accounting is validated.

## Reproduce current tests

```powershell
Set-Location C:\AI\Jesse\Jesse-Audit
python -m compileall -q strategy_lab
python -m pytest -q strategy_lab/tests
```

Relevant Jesse core tests run inside a disposable Docker container using a read-only bind mount of the vendored `jesse` source and a cache under `/tmp`; exact command and 34-pass result are in `TEST_RESULTS.md`.

## First-start notes and known issues

Initial Docker image setup may need internet; the desktop launcher uses `--pull never` and a cold Engine restart succeeded with the installed local image. At the earlier checkpoint, a full controlled network-off test had not been done; see the later scoped offline results below. Docker Desktop 4.53.0 previously crashed while binding its `dockerInference` local endpoint; it was updated with user approval. The current Docker Desktop 4.98.0 cold-start passed. No Docker volumes were factory-reset or deleted by this audit. Compose reports an obsolete top-level `version` warning; it does not stop services.

`python -m pytest -q strategy_lab/tests` currently passes 32 tests (see `evidence/test_outputs/strategy_lab_pytest_20261106_final_continuation.txt`). Compileall and `node --check` also pass. The Jesse core subset previously passed 34 tests in the Docker image; it was not rerun in this latest continuation. No global Python install was made.

## Machine and storage notes

Windows 11 / WSL2 were available. Earlier inventory found MT5 history files, but this audit did not read them as price input. Strategy market data came only from the Dukascopy folder, as required. For source hashes, Duka file inventory, exact app run and test evidence see `JESSE_AUDIT_REPORT.md`, `P0_VALIDATION_REPORT.md`, and `evidence/test_outputs`.

## Desktop shortcut location verified — 2026-11-06

The configured Windows Desktop known folder currently resolves to `C:\Users\Roanv\Desktop`. Launch `Dukascopy Strategy Lab.lnk` there. Windows shortcut metadata points to the hidden launcher in this project and the custom project icon.


## Offline readiness (verified scope)

The browser UI and a disposable PostgreSQL + Jesse stack completed local Dukascopy research while Docker egress was disabled. The actual Windows launcher/production Compose startup and Windows reboot under network denial remain unverified. See `OFFLINE_TEST_RESULTS.md`; do not treat the ordinary Docker cold-start as an offline restart.


An isolated Docker test confirmed that the cached Jesse image, a temporary PostgreSQL database, and actual Dukascopy-only backtest/optimizer computations run with Docker network mode `none`. The user's Windows launcher and production PostgreSQL volume were not restarted for this test. Full Windows offline cold-start status remains open; evidence is in `OFFLINE_TEST_RESULTS.md`.
