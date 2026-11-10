# Strategy Lab UI-redesign — 2026-11-05

## Uitgevoerd

- De vaste driekolomsdashboardlayout is vervangen door een brede werkruimte met instellingen, chart, Inspector en resultatenrij.
- De bestaande backtest-, optimizer-, replay-, runselectie-, tradejump-, preset-, code-editor- en Bid/Ask-ledgeracties zijn behouden.
- Layout-editor toegevoegd: panelen verslepen, verbergen/herstellen, standaard terugzetten, annuleren, named profiles opslaan en breedte/hoogte bewaren in gedeelde lokale JSON.
- Splitters ondersteunen slepen en pijltjestoetsen; chart kan verticaal worden vergroot en fullscreen worden geopend.
- Eenvoudige/geavanceerde instellingenmodus en herstel van parameterdefaults toegevoegd.
- `LabBreakout` toont Nederlandstalige labels, uitleg, eenheden en grenzen. `order_mode` toont tekst maar houdt de interne categoriewaarden 0/1/2 ongewijzigd.
- Decimale komma en punt worden voor numerieke parameters geaccepteerd. Financiële resultaten dragen het label voorlopig.
- Eén vaste standaardpoort is 8767; launcher, serverdefault en installatiegids zijn gelijkgetrokken.

## Verificatie

- App geopend in de ChatGPT in-app browser op `http://127.0.0.1:8767/`; de healthmelding noemt uitsluitend `C:\AI\Dukascopy_data`.
- Met XAUUSD M1, 2026-09-14 en `LabBreakout` is een lokale backtest uitgevoerd. Run `7a8daf7e-e78f-4373-afd8-3b8b32c6f93f` bevat `stop_offset: 0.25`, `order_mode: 0` en `source_root: C:\AI\Dukascopy_data`.
- Layoutprofiel opslaan/herladen (ook in een tweede tab), sleepgreep-herschikking, paneel verbergen/herstellen, Eenvoudig/Geavanceerd en vier viewportmaten zijn gecontroleerd.
- Python compileall, JavaScript syntaxcheck en Strategy Lab tests: 14 tests geslaagd.

## Nog niet geverifieerd

- Chrome en Edge zijn niet beschikbaar als geautomatiseerde browsers in deze sessie; cross-browservergelijking is **BLOCKED**.
- De volledige checklist met full-app screenshots voor dashboard, grootte, Edit Mode, optimizer en Inspector is nog niet als bestanden vastgelegd. De PNG-bestanden in `evidence/screenshots` zijn chartexports, geen captures van de volledige applicatie.
- Dynamische parameteruitleg is alleen gevalideerd voor `LabBreakout`; strategieën zonder sidecar tonen een onbekend-metadata-indicator.
- Resultaten gebruiken tabbladen Overzicht, Trades, Optimalisatie, Vergelijking en Logs; de resultaatnavigatie is in de browser geverifieerd.

## P0 vervolg — 2026-11-06

- Docker Desktop stond uit en daardoor waren de dashboardservices en Strategy Lab niet bereikbaar. De bestaande image-digest is gelijk aan de eerder gevalideerde Jesse v3.6.0-image; Compose-services zijn met `--pull never` gestart, zonder volumes te verwijderen.
- Eenvoudige modus start nu standaard actief. Fee, rekenmodel, leverage, warm-up, optimizer en strategiecode zijn naar inklapbare geavanceerde groepen verplaatst. De geselecteerde modus en open/dicht-status van groepen worden in de browser bewaard.
- ChatGPT IAB controleerde de zichtbaarheid en persistentie na reload. Een echte Duka-backtest na de UI-wijziging behield alle parameterwaarden in manifest `1d35af9c-9908-4dff-943a-78ac18c45a67`.
- Layoutprofielen zijn opnieuw in een tweede ChatGPT-IAB-tab geladen. Dit bewijst gedeelde lokale opslag tussen tabs van dezelfde browser, niet tussen verschillende browsers.


- Resultaatnavigatie toegevoegd voor Overzicht, Trades, Optimalisatie, Vergelijking en Logs. Charttekens bevatten entry/exit en SL/TP-traces; EMA-periode volgt de actieve parameter.
- Chartfocus-overlay werkt in de in-app browser; Escape sluit deze. Mobiele viewport 390×844 heeft geen horizontale overflow.
- Een futuresoptimizertrial is als COMPLETED weergegeven; resultaat terugzetten en opnieuw backtesten behield `order_mode=2`.
- Resultaatmetrics tonen PROVISIONAL; Recovery Factor wordt afgeleid uit nettoresultaat en de gerapporteerde procentuele drawdown.
- Resultatenpaneel met pointer van 360 px naar 292 px versleept; de custom layout bewaart en herstelt die hoogte.
- Een smalle viewport hield eerder vast aan opgeslagen inline paneelbreedtes. De mobiele breakpoint overschrijft die nu; op 730 px breed zijn instellingen/chart/Inspector elk 714 px en is er geen horizontale overflow.
- De kosteninstelling legt nu de Jesse-fee-rate uit: een fractie per fill, ongewijzigd doorgestuurd; `0,0006` is `0,06%` per zijde.

## 2026-11-06 continuation

Rechecked the local-only dashboard in the ChatGPT in-app browser. Replay Start/Next/Previous kept the Inspector on the cursor candle (00:00, 00:01, 00:00) and displayed exact-timestamp events only. Simple-mode Duka regression remained green (run `1d35af9c-9908-4dff-943a-78ac18c45a67`, 13 trades / 1,432 events). Strategy Lab regression suite passed 14 tests and Jesse Docker core subset passed 34 tests. Health endpoint still reports `C:\AI\Dukascopy_data` as the only source root.

Full-app screenshot files remain unavailable from this browser surface: its screenshot could be viewed in memory, but project-file export was unsupported and browser policy refused data-URL navigation. No policy workaround was used; existing chart PNG evidence is retained.

2026-11-06 follow-up: regression coverage now verifies unique signal→entry→close links (13/13 trades, 0 ambiguous) and no duplicate events in `/api/inspect`. ChatGPT IAB **Spring** control was checked against the first real Duka trade and landed on its exact signal candle, showing a single submission and position-open card. Strategy Lab suite: 17 passed.

## Lifecycle audit update — 2026-11-06

Dukascopy-only run `dac05432-695b-4393-b0a0-6e43ac739c44` now has exact order-ID/execution-time reconciliation for all 7 opens and 7 closes. Thirteen pending expiry cancellations carry stable matching IDs; one additional end-of-data cancel remains unmatched to a request event. The Inspector no longer exposes the future fill on the signal candle. Verification: Strategy Lab 18 passed; Jesse core Docker subset 34 passed. Full-app screenshot file export remains a browser-surface limitation.

## Responsive layout patch verification — 2026-11-06

The 761–1250 px layout now preserves a side-by-side Settings/Chart pair, moves Inspector according to the saved order at full row width, and uses a full-width vertical stack when any panel is hidden. Desktop retains its three-column grid. Latest asset versions are CSS `20261106.13` and JS `20261106.11`. ChatGPT IAB visual checks passed around 888 px and 1265 px; shared profile load in a second IAB tab passed after reload. A later pointer-driven CUA drag reordered Settings and Chart visually; Cancel restored the prior layout. See `UI_TEST_RESULTS.md` for remaining browser-specific blocks.

## Optimizer plan view — 2026-11-06

Advanced → Optimization now displays the full strategy search domains and live trial/method/objective/seed/date/train-test summary. ChatGPT IAB verified Random's continuous-domain warning and Grid's 768 LabBreakout combinations / first-five-trials notice. The parameter list wraps within the narrow Settings pane. No sampling semantics were changed, and no trial was submitted during this UI check.

## Lifecycle audit correction — 2026-11-06

The previously noted unmatched final-time cancellation is classified as expected Jesse backtest teardown: final-candle event, cleanup log, and cancel event are ordered and timestamp-aligned. Reproducible verification: `evidence/test_outputs/verify_lifecycle_teardown.py`; result: `evidence/test_outputs/dukascopy_lifecycle_teardown_classification_20261106.json`.

Regression checks on 2026-11-06: `python -m compileall -q strategy_lab`, `node --check strategy_lab/static/app.js`, and `python -m pytest -q strategy_lab/tests` all passed (18 tests). Jesse core Docker suite passed (34 tests) with plugin autoload disabled, avoiding an incompatible globally installed `anyio` pytest plugin in the container. Health remained `status=ok`, `mode=local-only`, source root `C:\AI\Dukascopy_data`.

## Drag-and-drop and global toolbar — 2026-11-06

Edit Mode now performs pointer-driven reorder from dedicated Settings/Chart/Inspector handles. A live ChatGPT IAB drag reordered Settings and Chart at ~903 px; visual order changed, and Cancel restored the original order. The fixed Results row is not draggable. The global toolbar now exposes run selection, refresh, Backtest and Optimize actions in simple mode; at ~903 px it wraps without horizontal page overflow. The top Backtest action completed a Dukascopy-only run with 13 trades / 1,432 events (run `c2656509-0bfe-4e62-b60d-911e5bbd2e26`). See `UI_TEST_RESULTS.md`. Chrome/Edge comparisons and full-app screenshot files remain unverified.

## Menubalk, thema's en app-icoon — 2026-11-06

- Beeld > Kleurthema toont Donker en Licht; Licht opent alleen de drie paletten Lichtgroen, Lichtroze en Lichtrood. De ongevraagde neutrale extra keuze is verwijderd. Hover opent de flyouts, klikken/keyboard-focus blijven bruikbaar, Escape sluit de menu's en de actieve kleur krijgt een vinkje. Een oude opgeslagen waarde `light` wordt voor compatibiliteit naar Lichtgroen omgezet.
- De eerdere logo-export was al 2048×2048; de vectorbron is opgeschoond en nu op 4096×4096 gerenderd voor betere herkenning en extra resolutiereserve. De Windows-ICO bevat 16, 24, 32, 48, 64, 128 en 256 px varianten; de PWA gebruikt 192- en 512px-iconen. De bureaubladsnelkoppeling is naar een nieuw ICO-pad gezet om de Windows-iconcache te verversen.
- `python -m pytest strategy_lab/tests -q`: 48 geslaagd. `node --check strategy_lab/static/app.js`: geslaagd. Lokale favicon-route gaf HTTP 200, `image/x-icon`, 81.411 bytes.
- De daadwerkelijke app-venster-/taakbalkweergave kon in deze controle niet visueel worden afgelezen. Computerbediening was door fysieke Escape gestopt; er is geen verdere browserbediening uitgevoerd.
- Vervolgpatch: `node --check strategy_lab/static/app.js` en `python -m pytest -q strategy_lab/tests` — 59 geslaagd. Exacte menuopties en legacy-thema-migratie statisch gecontroleerd in `evidence/test_outputs/theme_menu_followup_20261106.json`; live hover na deze laatste patch niet opnieuw gecontroleerd doordat Computer Use aangaf door fysieke Escape gestopt te zijn.

## Icoonvernieuwing — 2026-11-06

- Het bestaande grafieklogo is als vector opgeschoond en opnieuw gerenderd naar 4096×4096 PNG, PWA-iconen van 192/512 px en een Windows-ICO van 16–256 px. De desktopshortcut wijst naar het cache-vernieuwde ICO-pad; target en verborgen launcherargumenten zijn behouden.
- De PWA-manifest- en faviconversies zijn verhoogd naar `20261106.6`. Alle vijf lokale icon-/manifestroutes antwoordden HTTP 200; de lokale healthcheck meldt uitsluitend `C:\AI\Dukascopy_data`.
- `python -m pytest -q strategy_lab/tests`: 59 geslaagd. De `Beeld > Kleurthema` hovermenu-indeling blijft Donker/Licht, met drie lichte paletten in de Licht-flyout. Detail: `evidence/test_outputs/icon_theme_desktop_refresh_20261106.json`.
- De Explorer-shortcutresource is geverifieerd; een nieuwe screenshot van de Edge-PWA-taakbalkpin is niet gemaakt.
