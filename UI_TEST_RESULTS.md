# UI-testresultaten — 2026-11-05

| Test | Resultaat | Bewijs |
|---|---|---|
| Dashboard health en prijsbron | PASS | UI toont alleen `C:\AI\Dukascopy_data` |
| Dataset en M1-chart | PASS | XAUUSD, 1.380 candles voor 2026-09-14, chart in browser zichtbaar |
| Parameter metadata | PASS | Labels/uitleg/grenzen zichtbaar voor LabBreakout; ordermodus 0/1/2 toont naam, wanneer, gedrag en beperking; geselecteerde enginewaarde bleef exact `0`/`1`/`2` |
| Interne parameterwaarden | PASS | Laatste run bewaart `order_mode=0`; servercast blijft gebaseerd op de originele definitie |
| Decimale komma | PASS | UI-backtest met `0,25` leverde manifestwaarde `stop_offset=0.25` |
| Backtestresultaten | PASS | Run `7a8daf7e-e78f-4373-afd8-3b8b32c6f93f`: 13 trades, 1.432 events, alleen Duka XAUUSD M1 |
| Layout save, herladen en gedeeld profiel | PASS | `Research layout` verschijnt na serverherstart en in tweede tab; server JSON-opslag gebruikt local API |
| Verbergen/herstellen paneel | PASS | Inspector verborgen en hersteld via editor |
| Paneelherschikking | PASS | Sleepgreep verplaatst chart; positie bleef na profielselectie behouden |
| Eenvoudig/Geavanceerd | PASS na fix | Start nu standaard eenvoudig; fee/rekenmodel/leverage/warm-up, optimizer en strategiecode zitten in ingeklapte Geavanceerd-groepen. Modus en geopende groepen bleven na herladen behouden. |
| Schermbreedtes | PASS | 1280×720, 1440×900, 1920×1080, 2560×1440; geen horizontale overflow |
| Python tests | PASS | `python -m pytest -q strategy_lab/tests` — 14 passed |
| Python compile / JS syntax | PASS | `python -m compileall -q strategy_lab`; `node --check strategy_lab/static/app.js` |
| Chrome/Edge vergelijking | BLOCKED | Geen Chrome-/Edge-browseroppervlak beschikbaar |
| Screenshotbestand per UI-state | BLOCKED | Screenshot bekeken in browsercontrole, maar export naar auditbestanden niet beschikbaar via huidige browserinterface |

Geen live- of papertrade uitgevoerd. Prijshistorie uitsluitend uit `C:\AI\Dukascopy_data`.


| Resultaattabs | PASS | Overzicht, Trades, Optimalisatie, Vergelijking en Logs wisselen zelfstandig en toegankelijk |
| Chart SL/TP | PASS | Geselecteerde run toont entries/exits en horizontale stop-loss-/take-profittraces |
| Chartfocus | PASS | Overlay opent; Escape sluit; viewport blijft binnen breedte |
| Mobiel 390×844 | PASS | Flexlayout; documentbreedte 375 px bij viewport 390 px (geen horizontale overflow) |
| Optimizer UI | PASS | Spot-short trial meldde `InvalidStrategy`; futures 1/1 trial COMPLETED (score 0) en resultaat stond in Optimalisatie-tab |
| Optimizerparameter terugtest | PASS | Run `40a6fd7e-2cf0-4853-94a9-5b7bce633ceb`: geselecteerd `Short stop onder candle-low` is manifest `order_mode=2`; XAUUSD, Duka-only, 7 trades / 1.449 events |
| Results pane resize | PASS | Paneel resized from 360 to 292 px; height 292 persisted in `Research layout` JSON and reapplied |
| Recovery factor | PASS | UI derives provisional net-profit / cash-drawdown from Jesse's percent drawdown and starting balance |
| Persisted layout on narrow viewport | PASS after fix | At 730 CSS px, inline saved widths had held settings/Inspector at 290/330 px. Responsive CSS now overrides them; settings, chart and Inspector each measure 714 px and document scroll width remains 730 px. |
| LabBreakout order mode at narrow viewport | PASS after fix | ChatGPT IAB shows Dutch labels with internal values `0`, `1`, `2`; parameter grid columns measure 339 px. |
| Fee meaning | PASS after UI clarification | Jesse source computes `fee_rate × quantity × (entry + exit)`. UI says fraction per fill and `0,0006 = 0,06% per side`; runner forwards the rate unchanged. |
| Chart vertical splitter | PASS | ChatGPT IAB: pointer drag increased panel 540→592 px and Plotly chart 330.8→369.3 px; ArrowDown increased 540→552 px; repeated PageDown clamped at 1200 px; document had 0 px horizontal overflow. Reload restored 540 px and Dukascopy XAUUSD. |
| UI na Compose-herstart | PASS | Docker Desktop herstart; Jesse v3.6.0 + PostgreSQL healthy; dashboard en IAB hersteld. Health meldt uitsluitend `C:\AI\Dukascopy_data`. |
| Eenvoudige modus regressiebacktest | PASS | Run `1d35af9c-9908-4dff-943a-78ac18c45a67`: 13 trades/1.432 events; manifest behield `order_mode=0`, alle negen LabBreakout-parameters, fee `0.0006`, balance `10000`, warm-up `210`, source root uitsluitend Duka. |
| Gedeelde layout in tweede IAB-tab | PASS, zelfde browser | `Research layout` werd door de tweede ChatGPT-IAB-tab van lokale API geladen; volgorde en panelhoogten kwamen overeen. Dit is geen Chrome/Edge-vergelijking. |
| Replay Inspector synchronisatie | PASS | Na replay start/volgende/vorige wees de Inspector steeds naar exact dezelfde Dukascopy-candle als de cursor (00:00 → 00:01 → 00:00); alleen events op die timestamp werden getoond. Dit is cursorfiltering, geen heruitvoering van willekeurige strategiestatus. |
| Volledige app-screenshot export | BLOCKED | ChatGPT in-app browser liet de dashboard-screenshot bekijken, maar bood geen ondersteunde opslag naar het auditproject. Een data-URL export werd door de browser-URL-beveiliging geweigerd; geen omweg gebruikt. |
| Laatste regressieset | PASS | Strategy Lab 18 passed; Jesse core subset 34 passed; compileall en `node --check` slaagden. |
| Trade → signaalcandle | PASS | ChatGPT-browser: eerste trade uit Duka-run via entry-order-ID geopend; replay landde op 2026-09-14 01:00 UTC, de gekoppelde signaaltimestamp. Inspector toonde 1 submission en 1 position-open event; geen dubbele events. |

## 2026-11-06 event/Inspector regression update

- Fixed event linking and trade jumping with the real Jesse entry order ID. On the Dukascopy run, 13/13 trades linked to their unique submitted signal, position open and trade close; 0 ambiguous links.
- Fixed duplicate Inspector event cards: `/api/inspect` now selects events by timestamp OR signal timestamp in one pass. Regression fixture asserts unique events even when both timestamps match.
- ChatGPT in-app browser verified **Spring** on the first trade opened replay at the exact signal timestamp `2026-09-14T01:00:00Z`; Inspector showed the expected candle and only one `ORDER_SUBMITTED` plus one `POSITION_OPEN` card.
- Strategy Lab tests: 18 passed after the P1 lifecycle/timestamp fixes; Jesse core subset: 34 passed. The server was restarted on localhost to load the verified patch. Only Dukascopy prices were used.

## P1 cancellation IDs and fill-time replay — 2026-11-06

The Dukascopy XAUUSD M1 regression `dac05432-695b-4393-b0a0-6e43ac739c44` produced 7 trades. Jesse's order IDs now match 7/7 open and 7/7 close events to actual `executed_at` timestamps. For 13 expiry cancellations, all 13 request order IDs match stable cancelled events. One additional ID-bearing cancel at the final data timestamp has no request event and is called out as unmatched.

ChatGPT IAB replay proved signal/fill separation: cursor 07:36Z showed ORDER_SUBMITTED but not POSITION_OPEN; cursor 07:52Z showed POSITION_OPEN. Future fill information was excluded from the signal-candle Inspector. See `evidence/test_outputs/dukascopy_lifecycle_audit_20261106.json`.

## Lifecycle and causal replay — 2026-11-06

ChatGPT in-app browser on Duka run `dac05432-695b-4393-b0a0-6e43ac739c44`: **Spring** opened the selected trade at signal 07:36Z; Inspector showed ORDER_SUBMITTED without POSITION_OPEN. Advancing to the actual Jesse execution timestamp 07:52Z showed POSITION_OPEN. The API returned exact-timestamp events only, excluding future fill information from the signal candle. This verified causal display, not arbitrary strategy-state re-execution. Full-app screenshot-to-file export remains blocked; no browser-policy workaround was used.

## 2026-11-06 responsive layout regression

| Check | Result | Evidence |
|---|---|---|
| ChatGPT IAB narrow desktop layout (~888 px) | PASS | Settings and chart remain side by side; Inspector uses the full row when stacked. The selected shared `Lab widescreen` profile rendered with its saved panel order after reload. |
| Responsive panel order | PASS | Reordered Inspector above the settings/chart pair using the layout editor at ~888 px; the layout reflowed without clipping. |
| Wide layout (~1265 px) | PASS | Three-column order changed from settings/Inspector/chart to settings/chart/Inspector; saved as `Lab widescreen`. |
| Shared profile in another IAB tab | PASS | After reload the second ChatGPT IAB tab listed and selected `Lab widescreen`; profile data is shared through the local API. |
| Drag gesture reorder | NOT VERIFIED | CUA drag gestures did not visibly reorder panels; use the verified accessible arrow controls. HTML5 drag path remains unverified. |
| Chrome/Edge | BLOCKED | User requested ChatGPT in-app browser and no Chrome/Edge surface was available for comparison. |
| Dashboard screenshot export | BLOCKED | Visual state was inspected in the ChatGPT IAB; supported file export into the audit folder was unavailable. |

No live or paper trading. Market data source remains exclusively `C:\AI\Dukascopy_data`.

## Layout-drag and toolbar regression — 2026-11-06

- Pointer-based drag from the visible Settings handle to Chart reordered them in ChatGPT IAB (~903 CSS px viewport). The live DOM order changed from `settings,chart,inspector,results` to `chart,settings,inspector,results`; the screenshot showed Chart first and Settings second. **Annuleren** restored the pre-edit order. Only Settings, Chart and Inspector expose drag handles; Results stays in the bottom row.
- The dashboard toolbar now contains the existing run selector, Runs verversen, Backtest uitvoeren and Optimaliseren. At the narrow IAB viewport, controls wrap cleanly and `documentElement.scrollWidth` was 888 px against `innerWidth` 903 px (no horizontal overflow).
- Clicking the top **Backtest uitvoeren** ran the real Duka-only XAUUSD M1 LabBreakout backtest for 2026-09-14: 13 trades / 1,432 events. Run `c2656509-0bfe-4e62-b60d-911e5bbd2e26`; manifest source root and BID/ASK paths are exclusively `C:\AI\Dukascopy_data`. Manifest retained `order_mode=0`, `entry_interval=60`, and other LabBreakout values.
- The saved `Lab widescreen` profile was still present and loaded via the profile menu after the drag test. This is a second tab of the same IAB, not Chrome/Edge validation.

## Toolbar optimizer regression — 2026-11-06

Top-bar **Optimaliseren** ran Duka-only run `24c9e4d5-eeb9-4f33-930a-75a65a29e686`: 5/5 trials settled, 1 valid score-0 result, 4 errors. Browser DOM confirmed status `Optimalisatie afgerond · 5/5 trials · 1 geldige resultaten · 4 fouten.` and the Optimization tab selected. One failure was a short order mode on Spot; three exceeded the available balance. All 5 trial records remained visible. The latest status UI fix is loaded from `/assets/app.js?v=20261106.7`.

## Replay look-ahead unit test

Synthetic test `test_replay_future_candle_changes_do_not_affect_visible_prefix_or_events` now verifies future candle edits cannot alter the cursor-visible candle prefix or timestamp-filtered stored events. Strategy-state re-execution remains outside the test scope.

## Screenshot file capture attempt — 2026-11-06

The ChatGPT IAB screenshot remained available for visual browser checks, but no supported project-file export was exposed. Windows Snipping Tool captured only the native ChatGPT window; the embedded IAB page was not composited into that OS capture. No full-dashboard screenshot file was saved. Screenshot acceptance remains BLOCKED.

## Optimizer result wrapping and fresh browser run — 2026-11-06

- ChatGPT IAB reran Random optimization on Dukascopy XAUUSD M1, 2026-09-14. The dashboard settled 5/5 trials and reported 1 valid score-0 candidate plus 4 errors (one Spot/short mismatch and three insufficient-balance trials). This is a second UI smoke check, not evidence of a profitable candidate.
- Fixed the Optimization tab's unbroken JSON parameter strings: each result card now wraps long values within the results panel. Visual IAB inspection confirmed the horizontal scrollbar from the prior screenshot is gone; vertical scrolling remains available.
- `python -m pytest -q strategy_lab/tests` — 30 passed; `python -m compileall -q strategy_lab` and `node --check strategy_lab/static/app.js` — PASS; Jesse Docker core subset — 34 passed. API health reports `status=ok`, local-only mode, and source root exactly `C:\AI\Dukascopy_data`.
- Ledger was run with the documented illustrative contract assumptions against the selected 09:33 run. It returned 0 trades, so no result from that interaction is used as accounting evidence. Full-app screenshot file capture and Chrome/Edge comparison remain BLOCKED.

## Parameter help disclosure — 2026-11-06

- In the ChatGPT IAB on the selected Dukascopy XAUUSD dataset, opened the `Entry-interval` help control. It displayed the verified explanation, `candles` unit, 10–240 range, and standard value 60; the input still showed current value 60.
- Description controls are buttons with `aria-expanded` state, so they can be opened with keyboard focus. Parameter metadata remains presentation-only; the stored numeric enum values are unchanged.

## Simple/advanced parameter grouping — 2026-11-06

- ChatGPT IAB showed the four `simple` LabBreakout controls in the main panel and five additional parameters in a collapsed disclosure. Its summary reflected current values and units; expanding exposed all five editable inputs.
- Opened the Entry-interval help button: description, `candles`, range 10–240 and standard 60 appeared; current input remained 60.
- Ran the dashboard backtest using the advanced stop distance 2.5: `e3d00425-02c6-4dcd-b66f-5e9429afced0`, XAUUSD M1, 2026-09-14, 13 trades / 1,432 events. `strategy_lab/runs/e3d00425-02c6-4dcd-b66f-5e9429afced0/manifest.json` confirms all nine parameter values and identifies Bid/Ask inputs only under `C:\AI\Dukascopy_data`.
- The same browser view showed the dataset restricted to the Dukascopy directory and the currently selected Duka run. This was an offline Jesse backtest; no live or paper orders were submitted.

## Optimizer search-space summary — 2026-11-06

- In ChatGPT IAB, expanded Advanced → Optimization. The live summary showed all nine LabBreakout parameters, categorical labels, bounds, units and method-specific step rules, plus trial count, objective, seed, date interval and Jesse's 80/20 candle-count split.
- Random reports no finite combination count because several parameters are continuous, and warns repeated draws are possible. Switching to Grid showed 768 possible parameter tuples and accurately described the first five as the current requested trials. Grid's runner uses endpoint values where no explicit step exists.
- Summary remained usable in the narrow settings pane after changing from a four-column table to wrapped parameter rows; no dashboard-wide horizontal scroll was introduced. No optimizer trial was started during this UI check.

## Lifecycle teardown evidence correction — 2026-11-06

The one ID-bearing cancellation without a strategy expiry request is no longer treated as unresolved. `python evidence/test_outputs/verify_lifecycle_teardown.py` verified that the final candle still had the pending order, Jesse logged its end-of-run “cancel all remaining orders” step immediately before the cancellation event, and both timestamps match. The run manifest confirms the source root and Bid/Ask inputs are exclusively under `C:\AI\Dukascopy_data`. Output: `evidence/test_outputs/dukascopy_lifecycle_teardown_classification_20261106.json` (`EXPECTED_JESSE_BACKTEST_TEARDOWN`).

## Chrome/Edge and menu regression — 2026-11-06

`evidence/test_outputs/browser_compatibility_matrix_20261106.py` ran against installed Chrome 157 and Edge 156. On both browsers, 1280×720, 1920×1080 and 2560×1440 rendered without horizontal overflow or console/page errors. The test exercised the nested right-opening theme menus, all light palettes and persistence, hover-only parameter help, Inspector hide/restore, and opening/canceling Edit Mode. Paired dashboard and Edit Mode screenshots are in `evidence/screenshots/`; the complete measurements are in `evidence/test_outputs/browser_compatibility_matrix_20261106.json`. This browser matrix was headless and does not verify Windows scaling; a separate live Windows check confirmed the Edge PWA icon and taskbar pin.

## Theme menu follow-up — 2026-11-06

- Removed the extra neutral-light menu option. The flyout now has Donker and Licht; hovering Licht reveals Lichtgroen, Lichtroze, and Lichtrood. The selected palette is marked, and legacy saved `light` settings migrate to Lichtgroen.
- `node --check strategy_lab/static/app.js` passed; `python -m pytest -q strategy_lab/tests` passed (59 tests). Static structure and theme migration assertions are recorded in `evidence/test_outputs/theme_menu_followup_20261106.json`.
- The current CUA browser surface reported that Computer Use had been stopped with physical Escape, so this final menu adjustment was not rechecked live. The earlier headless Chrome/Edge theme and persistence matrix remains valid for the prior menu version.

## Desktop icon and theme entry — 2026-11-06

- Re-rendered the project SVG to 2048×2048 PNG, 192/512px PWA images, and a Windows ICO with 16, 24, 32, 48, 64, 128, and 256px frames.
- Refreshed `C:\Users\Roanv\Desktop\Dukascopy Strategy Lab.lnk` to use the new cache-busted ICO resource. The launcher target and arguments were preserved.
- All local icon/manifest routes returned HTTP 200. Strategy Lab regression: `python -m pytest -q strategy_lab/tests` — 59 passed. Evidence: `evidence/test_outputs/icon_theme_desktop_refresh_20261106.json`.
- The existing hover hierarchy stays `Beeld → Kleurthema → Donker/Licht`, with Lichtgroen/Lichtroze/Lichtrood under Licht. New native taskbar screenshot not captured.

## High-resolution desktop icon follow-up — 2026-11-06

- Raised the master icon export to 4096×4096 from the SVG source. The standard Windows ICO carries 16–256px frames; PWA PNGs remain 192/512px.
- Updated manifest/favicon cache version to `20261106.6`. Verified all five local icon/manifest routes return HTTP 200 and health remains local-only on `C:\AI\Dukascopy_data`.
- The Windows desktop shortcut now points to `assets/dukascopy-strategy-lab-refresh-4096.ico`; its hidden launcher target and arguments remain intact.
- `node --check strategy_lab/static/app.js` passed; `python -m pytest -q strategy_lab/tests` passed (59 tests). Evidence: `evidence/test_outputs/icon_theme_desktop_refresh_20261106.json`.

## Toolbar alignment regression — 2026-11-06

The ChatGPT in-app browser showed the updated toolbar at approximately 1265×720. The five data/date filters line up in one row; chart/run utility actions stay grouped on the left and the Backtest/Optimize pair aligns on the right. No controls were stranded on a separate left row and no horizontal page overflow was visible. This is a desktop viewport check only; narrow viewport rendering remains open. Evidence: `evidence/test_outputs/toolbar_alignment_regression_20261106.json`.

## 2026-11-06 UI usability follow-up

Headless Chromium at 1440×1100 verified: source path removed from header; Market/Strategy settings tabs; Candle/Accountmodel Inspector tabs; only Donker and Lichtgroen theme choices; custom theme save from `#6c9b78`; nine selectable metric cards with hover help; and `Alles tonen` returning all 1,380 candles in the selected Duka M1 day. No JavaScript errors. Screenshot: `evidence/screenshots/strategy-lab-current-20261106.png`; machine evidence: `evidence/test_outputs/ui_followup_and_p0_browser_20261106.json`.

Replay at five candles per second advanced five minutes with six candles visible and no future candles from the API. Both `Bestand` actions were executed: backtest run `9adfb4a4-1b3c-4bca-b7f1-9cbb01f8f6f4` (13 trades, 0 winners, net Jesse result −70.2518) and 1-trial optimization run `c429a818-fbad-48db-9ea2-53be75365349` (one preserved TRAINING insufficient-balance error). Results remain provisional; these are workflow checks, not financial validation.

Chart range follow-up — 2026-11-06: Chart point limit now defaults to `Alles tonen`; the finite 8,000/20,000/50,000/100,000 options remain available. API `limit=0` returns every available candle in the selected date range without stride thinning. Regression test passes with a synthetic paired Bid/Ask fixture whose selected candle count exceeds 8,000; JS syntax check passes. Evidence: `evidence/test_outputs/chart_unlimited_20261106.json`.


## Live unrestricted chart range — 2026-11-06

Queried the running local API for XAUUSD M1, 2026-09-01 through 2026-09-30 with limit=0. Health was ok; the server identified only C:\AI\Dukascopy_data. The endpoint returned all 30,209 available rows, exceeding 8,000 with no thinning. No source files were modified. Evidence: evidence/test_outputs/chart_unlimited_live_api_20261106.json.


Browser continuation loaded XAUUSD M1 for 2026-09-01 through 2026-09-30 in managed Chromium at 1905×1080. The visible selector was Alles tonen; chart status and Plotly DOM both confirmed 30,209/30,209 candles rendered. Screenshot: evidence/screenshots/chart-unlimited-live-20261106.png; structured evidence: evidence/test_outputs/chart_unlimited_browser_20261106.json. The app health endpoint remained local-only with source root C:\AI\Dukascopy_data. No backtest or trading action was started.


## Exact trade-signal Inspector capture — 2026-11-06

The first trade in existing Duka-only run 6428325a-599b-4e94-a459-83d462c6b897 was opened in the Inspector at its verified signal candle, 2026-09-01 02:00 UTC, one minute before the 02:01 UTC fill. The panel showed ENTRY_CONDITIONS_MET, all five PASS checks and the entry-order ORDER_SUBMITTED lifecycle event. Replay was stopped before capture. This uses a previously saved run and started no backtest. Evidence: evidence/test_outputs/trade_inspector_selected_signal_20261106.json; screenshot: evidence/screenshots/trade-inspector-signal-candle-20261106.png.


## Selectable trade-overlap chart — 2026-11-06

The Analyse tab now includes a Trade overlap checkbox and a UTC timeline of simultaneous open trades. It derives only from saved open/close timestamps; exact same-time close/open pairs do not count as simultaneous. Existing Duka-only day run had 13 complete trades with maximum concurrency 1 and 0 overlap minutes. A constructed interval fixture verified concurrency 2 and 2,000 ms overlap. Legacy chart preferences migrate to include the new toggle; hide/show passed, dark card colors passed and browser errors were zero. Evidence: evidence/test_outputs/trade_overlap_analytics_browser_20261106.json; screenshot: evidence/screenshots/trade-overlap-analytics-20261106.png. This does not calculate overlap across separate runs or a portfolio.

The MT5 portable-copy importer attempt is not a pass: the terminal opened without an account, Algo Trading remained unchecked, and the importer inputs dialog appeared, but the CUA screenshot no longer matched the MT5 accessibility state. Imported bars were not verified. The disposable test copy's missing executables were restored after its uninstaller was inadvertently opened; original MT5 installations and data were untouched. Evidence: `evidence/test_outputs/mt5_importer_computer_use_attempt_20261106.json`.

## Strategy Tester navigation — 2026-11-07

Browser automation exercised the eight workspace tabs and verified routing to their matching panels, Market/Strategie settings tabs, and persisted selected view on reload. The Invoerparameters view showed the nine LabBreakout inputs across the basic and advanced groups. Screenshot evidence: `evidence/screenshots/strategy-tester-overview-20261107.png` and `evidence/screenshots/strategy-tester-inputs-20261107.png`; machine record: `evidence/test_outputs/strategy_tester_tabs_browser_20261107.json`. No run was started for this check.

## Computer Use replaycontrole — 2026-11-07

- Microsoft Edge desktop opened the live local dashboard; health status was `ok` and the only configured price root was `C:\AI\Dukascopy_data`.
- UI-triggered `StatefulRsiPending` XAUUSD M1 backtest: run `5e51591d-e963-44e4-9fe0-38b7fa36cb8f`, 1,380 Dukascopy candles, 11 trades, 1,425 events.
- Chart displayed the bullish-entry triangle, TP/SL markers and per-trade dotted connectors; maximum points was `Onbeperkt`.
- Replay dropdown showed 1, 3, 5, 10 and 25 candles/sec. At 25/sec the visible cursor advanced automatically from 1 to 226 candles, then replay stopped successfully.
- Inspector causal-replay button was clicked on the 2026-09-14T03:45:00Z candle. It returned `FULL_CAUSAL_REPLAY`, `beslissing komt overeen`, 436 prefix candles and the expected `SKIP · ENTRY_CONDITION_SKIPPED`; no future candles were supplied.
- Machine-readable evidence: `evidence/test_outputs/cua_dashboard_replay_20261107.json`.

