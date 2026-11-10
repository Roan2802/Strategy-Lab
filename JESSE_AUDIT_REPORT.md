# Jesse + Dukascopy Strategy Lab audit

## Laatste validatie-update (2026-11-06)

De lokale Strategy Lab draait op `http://127.0.0.1:8767/` met `C:\AI\Dukascopy_data` als enige prijsbron. De nieuwste Duka-validatie voegde een beperkte `LabBreakout` quote-side signaaladapter toe: 1.379/1.380 opgeslagen beslissingen kwamen overeen en één Ask-EMA signaal week af. Generieke strategie-heruitvoering en broker-equivalente accounting zijn nog niet bewezen. De definitieve featurematrix staat in `FEATURE_MATRIX.csv`; bewijs en beperkingen staan in `P0_VALIDATION_REPORT.md`, `TEST_RESULTS.md` en `REMAINING_ISSUES.md`.



**Bijgewerkt: 6 november 2026**  
**Eindoordeel: CONDITIONAL GO** voor een lokaal Python researchlab met Dukascopy M1-data. **NO-GO** voor claim van MT5 CFD-accountingpariteit totdat contractinstellingen en fillgedrag apart zijn gevalideerd.

## Reikwijdte en gegevensbeleid

Jesse draait lokaal via Docker. De Strategy Lab-browserapp op `http://127.0.0.1:8767` leest uitsluitend Bid/Ask CSV-paren uit `C:\AI\Dukascopy_data`. De data-manager weigert ontbrekende Bid/Ask-paren, negeert ticks en andere bestandsvormen, en schrijft afgeleide Parquet-cache en testresultaten alleen in dit project. Bronbestanden zijn alleen gelezen; geen MT5-prijshistorie, andere feed of live/paper trade is gebruikt. De run is in de ChatGPT-browser uitgevoerd.

Voor de laatste XAUUSD M1-invoer zijn 8.244.053 tijdstempels gevonden, van 2004-01-01 01:00 UTC tot 2026-10-15 08:25 UTC. Er zijn 2 Bid-tijdstempels zonder Ask, 4.032 gaten (3.741.513 ontbrekende minuten), 0 duplicaten, 0 ongeldige/DST-tijdstempels, 0 missende volumevelden en 0 negatieve volumevelden. Dukascopy-volume is tickvolume/bronvolume en geen verhandeld volume. Bestands- en datasethashes staan in runmanifest `strategy_lab/runs/85808ac8-84eb-478e-a405-7ba41a11f56d/manifest.json`.

## A. Installatie

- Jesse `v3.6.0`, source commit `8665b253d43af7b06a88c6ebcda7a0b6393ec937`, MIT; image `salehmir/jesse:latest`, eerder vastgelegde digest `sha256:53dced6824be8d20de18afc5b1057b327f6c4c8a82c560a37b12e5aa4585a3dd`.
- Compose-services Jesse en PostgreSQL/TimescaleDB waren actief; database health check healthy. Dashboard `http://127.0.0.1:9000` geeft HTTP 200; bijgewerkte Strategy Lab `http://127.0.0.1:8767` geeft `/api/health` status `ok`, `mode=local-only`, bronroot Dukascopy. Oude Strategy Lab-listeners blijven actief op 8765 en 8766.
- Start: `C:\AI\Jesse\Jesse-Audit\start.bat`; alleen lab starten met Jesse reeds actief: `python -m strategy_lab.server`. Zie `INSTALLATION_GUIDE.md`.
- Docker Desktop-crash is eerder hersteld met de door gebruiker goedgekeurde update. Geen Docker-volumes gewist.

## B. Praktisch bevestigd

- Pair-detectie toont alleen Dukascopy M1 Bid/Ask paren. Symbolen zijn dynamisch ontdekt; bekende indices krijgen Jesse-aliasen.
- V2-cache verwerkt volledige XAUUSD Bid/Ask-bestanden, telt gaten/duplicaten/volumeproblemen, bewaart bronhashes en maakt timeframeweergaven. Dagselectie `2026-09-14` levert inclusief einddag exact 1.380 XAUUSD M1-candles.
- Browserrun `LabBreakout`, XAUUSD M1, 2026-09-14, 210 warm-upcandles, cache v2: 13 trades, 1.432 events, Jesse net P&L `-70.2518`, fee `67.2536` (run `85808ac8-84eb-478e-a405-7ba41a11f56d`). Dit is een workflowtest, geen strategieprestatieclaim.
- Een eerder browserrun had 12 trades/1.428 events. Trade selecteren/naar trade springen en chartklik op candle toonden in Entry Inspector candledata, PASS-condities en event lifecycle. Selecteerbare candle-inspectie is dus aangetoond; niet ieder plotspoor was bij de tweede run klikbaar.
- Causale replay getest: bij 00:00 UTC was 1 candle zichtbaar; stap vooruit gaf 00:01 en 2 candles; stap terug keerde naar 00:00 en 1 candle. API/UI meldde expliciet dat toekomstige data niet was meegestuurd.
- Dashboard geeft Run A/B-selectie, metrics en trade-overzicht. Nieuwe run selecteren na verversen is bevestigd in de tweede browserrun.
- Lab-tests: 14 geslaagd na de responsive en parametermetadatawijzigingen. Jesse kernregressietests in Docker: opnieuw 34 geslaagd; ruwe uitvoer staat in `evidence/test_outputs/`.
- De Jesse upstream-import/backtest/optimizer-tests en beperkte optimalisatierun uit de oorspronkelijke audit staan apart in `TEST_RESULTS.md`; die gebruiken geen Duka prijsdata en tellen niet mee als Duka-strategievalidatie.

## C. Grenzen en blockers

- Jesse MCP was in deze omgeving niet beschikbaar. De lokale Jesse `research.backtest()` API via bestaande Docker-service werkte; alleen de MCP-specifieke integratie is **BLOCKED / NOT TESTED**.
- Volledige Jesse-testsuite op Windows-host verzamelt niet: `ModuleNotFoundError: arrow`. Kernsubset draait wel in Jesse-image (`arrow 1.2.3`), 34 passed. Hostfout staat in `evidence/test_outputs/jesse_host_collection_failure.txt`.
- De browserassets, bestaande runweergave en geïsoleerde Jesse/PostgreSQL offline-run zijn inmiddels onder netwerkblokkade getest. De Windows-launcher/production-Compose-herstart en Windows reboot zonder externe netwerktoegang blijven **NOT TESTED**.
- De Plotly-chart is vanuit de ChatGPT-browser geëxporteerd naar `evidence/screenshots/dukascopy-xauusd-m1-backtest.png`; een volledige app-viewport-screenshot is niet door de CUA-connector opgeslagen.
- Geen tickdata getest. M1-OHLC kan de volgorde van intrabar-events, gelijktijdig geraakte SL/TP en werkelijke fillprijs niet vaststellen.

## D. MT5-compatibiliteit

Alle strategieprijzen kwamen uit Dukascopy. MT5-historische bestanden zijn geen invoer voor deze tests. Jesse's spot/futures model is niet gelijk aan brokerinstrumenten: contract size, tick value, lot step, accountvaluta, spreadkant, swap, margin en exacte slippage zijn niet automatisch equivalent. Bid/Ask-overlay vraagt expliciete contractspecificaties en is niet als gevalideerde MT5-accounting beschouwd. Financiële pariteit is niet bewezen.

## E. Chart en Trade Inspector

Bevestigd in de Strategy Lab: prijs- en equityplots, EMA-overlay, entry/exitmarkeringen waar geladen, trade lijst, trade springfunctie, chart-candleselectie en PASS/FAIL/SKIP condities bij de geselecteerde signalen. Event logging scheidt signaal, order en positie lifecycle. Replay kapt candles en events af op cursor. Dit is werkende eerste prototypefunctionaliteit, geen volledige Jesse-native review-ervaring.

Onvolledig: afgewezen setup-controles bestaan alleen waar een strategie ze logt; algemene automatische reconstructie van iedere Python-voorwaarde kan niet. Indicatorstate bij elk replayframe wordt niet opnieuw uitgevoerd door Jesse. De Duka-regressie van 6 november koppelde 7/7 openingen en 7/7 sluitingen aan de exacte Jesse-order-ID en uitvoeringstijd; 13/13 expiry-annuleringen zijn gekoppeld. De extra annulering op de laatste candle is als Jesse backtest-teardown geclassificeerd. Dit bewijst geen algemene lifecyclegarantie voor willekeurige strategieën. Er is geen bewijs dat alle indicatorpanelen, zones, MAE/MFE of willekeurige getekende objecten volledig zijn. Zie `TRADE_INSPECTOR_TESTS.md` en `evidence/test_outputs/dukascopy_lifecycle_teardown_classification_20261106.json`.

## F. Optimalisatie

De oorspronkelijke septemberrun voltooide 45 trials maar leverde geen positieve beste kandidaat. Daarna zijn Random (3), seeded herhaling (3), Grid (2), TPE (1) en hervatten van 3 naar 5 totaal getest op Dukascopy XAUUSD M1. Alle optimizer-scores bleven negatief. De hoogste trialscore (-6.5916) is met exacte parameters tweemaal opnieuw gebacktest over dezelfde maand en configuratie: beide runs gaven 28 trades, 30.546 events en net -573.7105581521494. Zie OPTIMIZER_DIAGNOSTICS.md voor runidentifiers en grenzen.

## G. Offline en agent

Browser-UI en een disposable Jesse + PostgreSQL-stack met Dukascopy-backtest en optimalisatie zijn getest zonder externe netwerkroute. De echte Windows-launcher/production-Compose cold-start onder netwerkblokkade blijft open. Jesse MCP ontbreekt; de lokale REST/research-brug is getest.

## H–J. Uitbreidingen, architectuur en volgende stappen

Aanbeveling: Jesse als research/backtest engine met de bestaande dunne lokale Dukascopy Strategy Lab. Houd instrumentaccounting nadrukkelijk apart en als voorlopig gemarkeerd. Fork Jesse pas als reproduceerbare tests aantonen dat bestaande hooks het noodzakelijke gedrag niet kunnen bieden.

1. **P0:** formeel accountingcontract per instrument specificeren; side-aware Bid/Ask, fees, ticksize, contract size, margin en currency conversion met alleen Dukascopy-prijzen valideren. MT5 parity blijft apart en momenteel geblokkeerd door de eis geen MT5-prijsdata te gebruiken.
2. **P1:** nieuwe runs, opgeslagen run laden/vergelijking en parameters reproduceren; browserregressietest toevoegen.
3. **P1:** dataset-integriteit/gaten, DST-fold, volume en cache invalidatie blijven testen tegen Duka-bestanden; origineel onveranderd houden.
4. **P2:** ontbrekende setup-logs standaardiseren en replay indicators/state causaal herberekenen.
5. **P3:** offline herstart via de echte Windows-launcher/production-Compose; uitgebreidere Duka-optimalisatie en stabiliteitsanalyse.

`FEATURE_MATRIX.csv` bevat de volledige lijst van onderzochte gevraagde functies met verificatiestatus. `EXTENSION_ROADMAP.md`, `P0_VALIDATION_REPORT.md` en `TEST_RESULTS.md` bevatten acceptatiecriteria en reproduceerbaar bewijs.

## Primaire bronnen

- [Jesse broncode](https://github.com/jesse-ai/jesse), [project template](https://github.com/jesse-ai/project-template)
- [Custom Data-import](https://docs.jesse.trade/docs/traditional-markets/importing-data), [interactive charts](https://docs.jesse.trade/docs/charts/interactive-charts)
- [Research backtest](https://docs.jesse.trade/docs/research/backtest), [research optimize](https://docs.jesse.trade/docs/research/optimize), [MCP setup](https://docs.jesse.trade/docs/mcp/setup)


## P1 lifecycle regression update — 2026-11-06

The Dukascopy-only replay regression `dac05432-695b-4393-b0a0-6e43ac739c44` confirms exact Jesse order execution timestamps for 7/7 opens and 7/7 closes. Thirteen pending-expiry order IDs matched cancellation events. One additional cancellation ID at end of data has no request event and remains unresolved. ChatGPT IAB verified the signal at 07:36Z has a submission but no position-open event; the fill is shown at 07:52Z. This improves lifecycle observability but does not reconstruct arbitrary strategy state. Strategy Lab tests: 18 passed; Jesse core Docker subset: 34 passed. Evidence: `evidence/test_outputs/dukascopy_lifecycle_audit_20261106.json`.


## Eindcontrole — 2026-11-06

De hoofdoplevering bevat de vijf gevraagde bestanden (`JESSE_AUDIT_REPORT.md`, `FEATURE_MATRIX.csv`, `INSTALLATION_GUIDE.md`, `TEST_RESULTS.md`, `EXTENSION_ROADMAP.md`). De matrix is bijgewerkt met 154 feature-rijen en 11 kolommen. Actuele verificatie: Strategy Lab-tests 30/30 geslaagd; `compileall`, `node --check` en lokale health-check geslaagd. Health meldt alleen `C:\AI\Dukascopy_data`. De full-app screenshot en Chrome/Edge-vergelijking waren op dat moment nog niet geleverd; zie de V4-continuatie hieronder voor de nu opgeslagen screenshots en de nog niet uitgevoerde gepaarde browservergelijking.

**Correctie op de historische lifecycle-notitie hierboven (2026-11-06):** de destijds als onopgelost omschreven extra eind-annulering is later geclassificeerd als `EXPECTED_JESSE_BACKTEST_TEARDOWN`. De latere reproduceerbare classificatie in `evidence/test_outputs/dukascopy_lifecycle_teardown_classification_20261106.json` supersedeert alleen die conclusie; 13/13 strategy-expiry-annuleringen waren al gekoppeld. Er is geen algemene lifecyclegarantie voor andere strategieën vastgesteld.

## V4 continuation — 2026-11-06

The actual Windows Desktop shortcut and hidden launcher are now covered by `DESKTOP_LAUNCHER_REPORT.md` and `STARTUP_TEST_RESULTS.md`. A Docker Desktop cold-stop/start found a path-resolution bug; the fix was retested successfully, including healthy PostgreSQL, Jesse HTTP 200, Duka-only Strategy Lab health, and Edge app mode. Dashboard and UI-state images plus a native app-window capture are saved under `evidence/screenshots/`.

Optimizer feasibility metadata excludes the Spot-incompatible LabBreakout short order mode before Random/Grid/TPE sampling. Duka Grid (2 trials) and TPE (1 trial) smoke runs recorded the exclusion; completed negative trial scores remain visible. Saved optimizer results repopulate the Optimization tab after reload, and the selected-trial exact-parameter run is verified. At this checkpoint, import performance and optimizer analysis views were not yet measured; both have since been addressed in the latest continuation below. Full Windows launcher/production Compose restart under network isolation remains open; spread-sensitive signal re-execution and arbitrary strategy-state replay also remain open.

## Dashboard execution-status UI regression — 2026-11-06

ChatGPT in-app browser exercised the Bid/Ask ledger against Dukascopy-only run `40a6fd7e-2cf0-4853-94a9-5b7bce633ceb`: the UI presented both intended status labels, complete-quote quality, 7 trades, and clearly stated that contract details are unverified and strategy signals are not recomputed. It also flagged all 7 quantities as inconsistent with the user-entered 0.01 volume step. Calculated USD figures are provisional only. Evidence: `evidence/test_outputs/dukascopy_execution_ui_status_20261106.json`.


## Symbol-route correction and matrix — 2026-11-06

A real 56-case matrix found Jesse rejects unseparated route symbols like `USDJPY` and `BTCUSD`; after mapping those markets to Jesse's required dashed symbols, the 28 failed cases all reran successfully. Combined with the 28 unaffected initial passes, all 8 Dukascopy symbols × 7 timeframes completed for one selected UTC date. Initial failures and successful retests are retained in `evidence/test_outputs/dukascopy_symbol_timeframe_matrix_20261106/matrix_final.json`; source-root and Bid/Ask paths from all 56 final run manifests were independently verified in `evidence/test_outputs/dukascopy_matrix_manifest_source_verification_20261106.json`. This does not validate multi-year performance, MT5 parity, or trade logic where a run produced zero trades.


## P0-continuatie — 2026-11-06

De ChatGPT-browsercheck van `ExampleStrategy` is geslaagd: **Spring** selecteerde de signaalcandle van de eerste trade op 2026-09-14 01:00 UTC en de Inspector toonde de Duka Bid/Ask-candle en de gelogde PASS-controles. Het run bevat 1.380 beslissingen en 11 submit/open/close-events. Hiermee is een tweede instrumented strategy getest; automatische inspectie van willekeurige Python-logica of replay van ongeregistreerde strategiestatus is niet bewezen. De 56-case matrix (8 symbolen × 7 timeframes) voltooide de lokale route voor één UTC-dag, uitsluitend met `C:\AI\Dukascopy_data`; brede resamplefideliteit blijft open. De bijgewerkte vijf opleverbestanden en uitvoer staan in de auditmap en `evidence/test_outputs/`.

## Full September dashboard run and replay correction — 2026-11-06

The ChatGPT in-app dashboard ran XAUUSD M1 `LabBreakout` for 2026-09-01–2026-09-30 using only Dukascopy Bid/Ask source files. Run `6428325a-599b-4e94-a459-83d462c6b897` completed with 30,209 candles, 230 trades, and net Jesse P&L `-1,197.5674` (`-11.9757%`); all 230 trades lost. This remains `JESSE_RESEARCH_PROVISIONAL`, not broker-validated execution. Both 475 MB source files' current SHA-256 hashes matched the run manifest. During this validation, selecting a saved full-month run failed to update the old replay date bounds; the run selector now applies the run's symbol/timeframe/strategy/parameters/account/date context before loading its chart. ChatGPT IAB re-test: Spring on trade 1 showed the 2026-09-01 02:00 UTC signal candle and `ORDER_SUBMITTED`, with no future fill; one M1 step later showed the 02:01 UTC `POSITION_OPEN`. Evidence: `evidence/test_outputs/dukascopy_ui_september_2026_full_month_20261106.json` and `dukascopy_ui_fullmonth_spring_replay_fix_20261106.json`. Strategy Lab suite: 32 passed; `node --check` and `compileall` passed.

## Editable optimizer search space — 2026-11-06

Added and tested per-parameter include/fix controls, numeric bounds/steps and category subsets. ChatGPT IAB plus Jesse completed Duka XAUUSD M1 Spot optimization `f06fb1b8-e636-4ed2-8968-c4d3ba36e828` with a user-set entry-interval domain and seven fixed parameters. The manifest and trial result preserve those constraints; Spot short mode is excluded. Trial training/test net P&L were negative. The initial stale-process probe is invalidated and not counted. After `desktop-launcher.ps1` relaunched the API on port 8767, health returned `ok` for `C:\AI\Dukascopy_data` and an expanded bound was rejected with HTTP 422 before loading candles. The broken favicon route was repaired and rechecked at HTTP 200. Strategy Lab suite: 40 passed; compileall, JS syntax and evidence JSON validation passed. Evidence: `evidence/test_outputs/optimizer_user_search_space_20261106.json` and `dashboard_favicon_route_20261106.json`.
The configurable 50–90% candle-order split is now also browser-verified at 70/30: run `34cedb29-a799-4ddd-afb2-a51810027b96` completed with 966 training and 414 holdout candles, matching its manifest and result. The holdout had no trades and supports no performance claim. A broad-space trial retained an `InsufficientBalance` error, so generic margin feasibility remains open. Details: `OPTIMIZER_DIAGNOSTICS.md`.


## Latest continuation — 2026-11-06

The Dukascopy XAUUSD M1 full import now matches the established cache exactly across 8,244,053 joined rows, timestamps and values. The optimized import reduced elapsed time 43.6% (50.038 s → 28.202 s) and peak RSS 47.9% (5.42 GB → 2.82 GB); original CSVs remained read-only.

The optimizer UI's trial sorting, heatmap, sensitivity chart and JSON export passed Chrome automation against a saved Dukascopy-only run. No new backtest/optimization was started. The regression also fixed Plotly axis titles. Strategy Lab tests: 49 passed; Python compile, JS syntax and evidence JSON checks passed. Evidence is linked in `DATA_PERFORMANCE_REPORT.md`, `OPTIMIZER_DIAGNOSTICS.md`, and `TEST_RESULTS.md`.

The audit conclusion remains **CONDITIONAL GO** for offline-oriented Dukascopy research and **NO-GO** for MT5-equivalent CFD accounting. Network-isolated offline restart, exact spread-sensitive strategy replay, arbitrary-strategy state reconstruction, Jesse MCP calls and broker-verified contract behavior remain unverified or unavailable.

## Replay cutoff and desktop UI confirmation — 2026-11-06

The live `/api/replay` route was checked against Dukascopy XAUUSD M1 and saved run `85808ac8-84eb-478e-a405-7ba41a11f56d` at 2026-09-14 09:33 UTC. It returned 574 candles and 594 stored events through the selected cursor, with `future_candles_returned=false`. Source inspection confirms it filters timestamped stored candles/events only; it does not re-run LabBreakout, recalculate EMA or reconstruct arbitrary Python strategy state. This feature is **Missing** for generic causal replay, despite the future-data cutoff passing. Evidence: `evidence/test_outputs/dukascopy_replay_cutoff_20261106.json`.

The high-resolution project icon and light/dark theme menu are already implemented. Chrome/Edge browser evidence covers the right-opening `Beeld → Kleurthema` menu, neutral/green/pink/red light palettes, persistence, and zero console errors. The Windows shortcut, Edge app titlebar and taskbar use the custom logo. The exact hidden WScript/VBS launcher target also passed an already-running reuse check; this was not an Explorer double-click or reboot test. See `DESKTOP_LAUNCHER_REPORT.md`, `STARTUP_TEST_RESULTS.md`, and `evidence/test_outputs/launcher_reuse_20261106.json`.


## Offline verification update — 2026-11-06

A browser request-deny test verified that the localhost dashboard can display local assets, historical Dukascopy runs, charts, optimizer results and saved layout profiles without any browser request leaving loopback. At this intermediate checkpoint, Jesse image import and `jesse install-live --no-strict` passed on Docker `--network=none`; subsequent testing also started disposable PostgreSQL + Jesse and ran new offline backtest/optimization computations, recorded below. The Windows launcher restart remains untested.


A further isolated offline test started Jesse with temporary PostgreSQL on a Docker `network=none` namespace, then ran a new Duka-only XAUUSD M1 backtest and one-trial optimization through the copied current Strategy Lab runner. Both results exactly matched saved baselines; no live/paper trading occurred and the disposable containers were removed. The Strategy Lab localhost browser also worked with all non-loopback browser requests blocked. **CONDITIONAL GO** for offline Jesse research computation is now supported in an isolated Docker setup. Full Windows launcher/production Compose cold start and Windows reboot with external access denied remain unverified. See `OFFLINE_TEST_RESULTS.md`.


## Launcher recovery/error paths — 2026-11-06

Isolated tests of a temporary launcher copy confirmed it fails closed for a missing data source and an occupied test port, logs actionable errors, and preserves the port listener. Production port, Docker windows/services and original data were not touched. This complements earlier cold-start/reuse/deduplication tests; native popup rendering, real production-port conflict and Windows reboot remain unverified. See `STARTUP_TEST_RESULTS.md`.

## Actual desktop shortcut use — 2026-11-06

The real Windows Desktop shortcut has been opened from Explorer; the latest startup log confirms reuse rather than duplicate startup. The startup and health checks pass with only `C:\AI\Dukascopy_data`. This confirms the `.lnk` target and existing-service path, but not a literal mouse double-click, cold start from a fresh Windows boot or offline full-stack restart. Evidence: `evidence/test_outputs/desktop_shortcut_live_acceptance_20261106.json`.

## P0 validation continuation — desktop icon and offline launcher — 2026-11-06

The desktop/PWA logo was refined in its SVG source and exported at 4096×4096, with Windows ICO frames through 256px and PWA 192/512px images. The real desktop shortcut now targets a cache-busted ICO path. Live asset routes and health passed; health remains local-only with `C:\AI\Dukascopy_data`. Strategy Lab tests: 59 passed; JS syntax check passed. The existing Edge app window could not be refreshed in computer control, so its new visible taskbar artwork is not visually verified.

A disposable Windows launcher/Compose acceptance now passed the hidden-server and Edge app-window handoff with Jesse egress blocked. The test stack and ports were cleaned up without touching volumes; the production 8767 listener remains. Production offline cold start, host-wide network denial and Windows reboot remain unverified. Feature matrix and detailed evidence were updated. No live/paper trades or changes to original market data.

## Optimizer support boundary test — 2026-11-06

The Strategy Lab API now has an explicit regression for unsupported TPE resume: it returns HTTP 422 before writing a request or invoking Jesse when the Optuna study is not persisted. `python -m pytest -q strategy_lab/tests` passed 60 tests after adding the guard. This keeps TPE resume labeled unsupported rather than suggesting it is resumable. Evidence: `evidence/test_outputs/tpe_resume_guard_20261106.json`.


## 2026-11-06 — Optimizer outcome follow-up

The optimizer now preserves and labels distinct completed, no-trade, Spot-balance, Futures-margin, invalid-combination and technical-error outcomes. It retains partial training metrics and exact failure phase when holdout execution fails. Real Jesse research runs on Dukascopy XAUUSD M1 verified Spot `InsufficientBalance`, Futures `InsufficientMargin`, and a completed negative trial; negative scores remain visible. The full Strategy Lab suite passes 70 tests. A generic safe margin preflight is still open because historical equity changes during the run and Jesse quantities are not broker-verified lots. Details and evidence: `OPTIMIZER_DIAGNOSTICS.md` and `evidence/test_outputs/optimizer_outcome_classification_20261106.json`.


## Final audit reconciliation — 2026-11-07

**Conclusion: CONDITIONAL GO** for local, offline-capable Dukascopy research and LabBreakout debugging in the Strategy Lab. **NO-GO** for claiming validated MT5 CFD financial parity. The current local build matches its on-disk backend and served frontend; the live API is restricted to `C:\AI\Dukascopy_data`. The current Strategy Lab suite passes 76 tests, and the dashboard backtest/readback, trade excursions, replay entry/exit markers, dotted per-trade connectors, replay cursor cutoff, and 1/3/5/10/25 candles-per-second auto-advance have direct evidence.

The five required deliverables remain `JESSE_AUDIT_REPORT.md`, `FEATURE_MATRIX.csv`, `INSTALLATION_GUIDE.md`, `TEST_RESULTS.md`, and `EXTENSION_ROADMAP.md`. Current P0, build, replay, E2E and blocker detail is in `P0_VALIDATION_REPORT.md`, `CURRENT_BUILD_VALIDATION.md`, `REPLAY_VALIDATION.md`, `STRATEGY_TESTER_E2E_RESULTS.md`, and `REMAINING_ISSUES.md`.

Residual individual blocks: Jesse MCP-specific calls (connector unavailable); generic arbitrary-strategy causal re-execution; MT5 importer execution (both importer and minimal print-only script fail before `OnStart` in the isolated terminal); exact MT5 CFD parity; production-wide offline restart and Windows reboot. Those do not block completion of the locally executable audit, and are not marked passed. No live/paper trade was run; original MT5 and Dukascopy files were not modified.

## Current replay status supersession — 2026-11-07

The current build is PID 31472 with matching backend/frontend build IDs, health `ok`, local-only mode, and source root exactly `C:\AI\Dukascopy_data`. Strategy Lab suite is 85/85. Generic causal prefix checkpoints now pass on StatefulRsiPending at signal, fill, TP and pending expiry; a cursor accounting defect where Jesse prefix-finalization was miscounted as a completed trade has been corrected and covered by a regression. Chart entry/exit markers and dotted per-trade connectors pass 23 automated assertions and have prior visual Computer Use evidence. Current artifacts: `GENERIC_REPLAY_VALIDATION.md`, `evidence/test_outputs/stateful_replay_lifecycle_checkpoints_20261107.json`.

This does not complete the entire V7 development objective. The latest requested deliverables are listed in the supplied V7 specification; portfolio aggregation, broader quote-side execution, broker/accounting parity and the other residuals remain tracked in `REMAINING_ISSUES.md`. Jesse MCP-specific calls remain blocked at connector availability. MT5 importer work remains OPTIONAL / DEFERRED and must not be resumed.

## Portfolio dashboard and end-of-turn runtime check — 2026-11-07

Portfolio analysis is now implemented and tested; the earlier partial/not-implemented statements in this report are superseded. The current local service (PID 15800) reports health `ok`, `local-only`, backend current, served frontend current, 0 active runs, and source root exactly `C:\AI\Dukascopy_data`. Docker Jesse is running and PostgreSQL healthy. Full Strategy Lab regression: 93 passed; `node --check strategy_lab/static/app.js`, 23 replay overlay assertions, and Python compileall passed.

Computer Use opened Results → Portfolio, selected two saved Duka StatefulRsiPending runs, and visibly rendered aggregate equity, drawdown, strategy/instrument/month and correlation tables. Compatible saved LabBreakout runs also passed the live API calculation. An incompatible mixed signal-mode request returned `COMPATIBILITY_BLOCKED` and no combined net P&L. See `PORTFOLIO_ANALYSIS_STATUS.md` and the three `evidence/test_outputs/portfolio_analysis_*_20261107.json` files.

The audit deliverables are reconciled for all locally executable P0 scope. Remaining isolated items are explicitly blocked/limited: Jesse MCP connector unavailable; quote-side adapters for strategies that do not implement the explicit callback contract (unsupported by design; only MCP-only work is blocked); broker-verified MT5 CFD parity (cannot be established from OHLC alone without broker contract/tick ground truth); production-wide offline reboot/restart (deferred; not required for this development round); and OPTIONAL / DEFERRED MT5 importer (isolated terminal failed to initialize even a minimal script; per scope, no further attempts). No live/paper trades were executed and the original Dukascopy and MT5 data were not changed.


