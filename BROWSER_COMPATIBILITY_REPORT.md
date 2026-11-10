# Browsercompatibiliteit

## Beschikbaarheid

De eerdere audit gebruikte de **ChatGPT in-app browser**. In de V4-continuatie is ook de lokale Windows **Edge app-mode** geopend en in een native venster vastgelegd. Een gepaarde renderingvergelijking met een normale Edge-tab en Chrome op identieke viewport/zoom is nog niet uitgevoerd; die vergelijking staat als **NOT TESTED**, niet als een foutieve compatibiliteitsconclusie.

## Viewportmetingen (ChatGPT in-app browser)

| Viewport | Chartbreedte (CSS px) | Documentbreedte | Bevinding |
|---|---:|---:|---|
| 1280 × 720 | 537 | 1265 | Geen horizontale overflow; desktopkolommen |
| 1440 × 900 | 697 | 1425 | Geen horizontale overflow; desktopkolommen |
| 1920 × 1080 | 1177 | 1905 | Geen horizontale overflow; chart groeit mee |
| 2560 × 1440 | 1817 | 2545 | Geen horizontale overflow; chart groeit mee |

De 15 px verschil komt van de browserdocumentviewportrapportage. De screenshots gebruikten de in-app browser en kunnen niet als equivalent van Chrome/Edge worden geïnterpreteerd. Zoomvergelijking en cross-browserpixelvergelijking blijven open.

| Mobiel 390 × 844 (ChatGPT IAB) | 322 | 375 | PASS; verticale flexlayout, geen horizontale overflow |

Chartfocus-overlay en Escape zijn in de ChatGPT-browser geverifieerd. Instellingen voor browserzoom en Windows-schaal zijn niet aangepast; Chrome en Edge blijven niet beschikbaar.

De laatste layout en resultaatnavigatie bevatten geen browser-specifieke CSS-hacks. In-app browsermetingen zijn opnieuw op een viewport van 1440×900 gedraaid; de cross-browservalidatie wacht op Chrome en Edge.

## 2026-11-06 vervolg — desktopapp en screenshots

- De Windows launcher opende Microsoft Edge app mode met exact de titel `Dukascopy Strategy Lab`, zonder adresbalk, en bracht het venster naar voren. Native Windows capture: `evidence/screenshots/desktop-app-window-native.png` (1295×687 capture; venster op Windows-display, niet viewport-emulatie).
- Browserautomatisering legde de dashboardpagina vast op emulated viewports 1280×720, 1920×1080 en 2560×1440. Gemeten documentbreedtes waren respectievelijk 1265, 1905 en 2545 CSS px; geen horizontale documentoverflow. Dit bewijst responsive gedrag in de gebruikte Chromium/CDP-surface, niet gelijke rendering tussen Chrome en Edge.
- Vastgelegde bestanden: standaarddashboard, grote viewports, Edit Mode, aangepaste Inspector-layout, geavanceerde strategie-instellingen, replay op één candle en optimizerresultaten onder `evidence/screenshots/`.
- Spot-feasibility werd zichtbaar in het optimizerplan: voor LabBreakout worden bij Spot alleen ordermodi 0 en 1 getoond; de shortmodus 2 staat als uitgesloten vermeld.
- **Nog niet getest:** dezelfde matrix in afzonderlijke gewone Chrome- en Edge-vensters, Windows-zoom/scaling op meerdere waarden en detail-Inspector met geselecteerde trade. De native capture toont de app en chart; het Inspector-detailbeeld blijft beperkt tot het lege selectiebeeld.

## Responsive regressie opgelost — 2026-11-05

Een actuele smalle IAB-view had `innerWidth=745`, documentbreedte 730 CSS px en DPR 1.5. Opgeslagen `style.width`-waarden bleven de mobiele flexlayout beperken tot 290 px voor Instellingen en 330 px voor Inspector, met overlappende parametertitels. De breakpointregel overschrijft die inline breedtes nu met 100% en een minimum van 0. Na herladen maten Instellingen, chart en Inspector ieder 714 px; document scrollWidth bleef 730 px. LabBreakout toont twee leesbare kolommen van 339 px, Nederlandse parameters en ordermoduslabels.

Deze correctie raakt alleen viewports tot en met 760 px. De eerder gemeten desktopbreedtes blijven ongewijzigd. Full-app screenshotbestanden en Chrome/Edge-vergelijking ontbreken nog; de bestaande PNG's zijn chartexports, geen dashboardcaptures.

## Charthoogte-splitter — 2026-11-05

In de ChatGPT in-app browser veranderde een muissleep de chartpanelhoogte van 540 naar 592 CSS px; de Plotly-chart volgde van 330,8 naar 369,3 px. ArrowDown vergrootte 540 naar 552 px. PageDown stopte bij de ingestelde maximumhoogte 1200 px. Bij elk meetpunt bleef horizontale overflow 0 px. Herladen herstelde de standaardhoogte van 540 px.

## Responsive settings/chart/Inspector layout — 2026-11-06

A live ChatGPT IAB viewport around 888 px exposed a layout defect: the Inspector stacked at its fixed 330 px width, leaving most of the row blank. The responsive CSS now lets Settings and Chart form a two-column pair while Inspector spans the full row; the responsive reflow also respects the saved panel order and switches to a vertical stack when a panel is hidden. At approximately 888 px the Inspector-first order and the `Lab widescreen` order both rendered without horizontal clipping. At approximately 1265 px, all three columns rendered and their order was changed and saved. A second ChatGPT IAB tab loaded the shared `Lab widescreen` profile after reload.

The CUA drag gesture itself did not visibly reorder panels, so drag-and-drop is **NOT VERIFIED**; accessible arrow controls did reorder them. These are ChatGPT IAB measurements, not Chrome/Edge validation. Screenshot export to an audit PNG remains blocked by the available browser interface.

## Pointer drag and toolbar follow-up — 2026-11-06

The Edit Mode drag path now uses pointer events on explicit handles with a movement threshold and before/after drop indicators. A real CUA drag in ChatGPT IAB reordered Settings and Chart at a ~903 CSS px viewport; the visual order changed and **Annuleren** restored the original layout. Results remains deliberately anchored under the main panels. The layout editor now says which panels can be dragged.

The global toolbar now includes the run selector, run refresh, backtest and optimizer actions. At ~903 px the toolbar wraps over two rows without page overflow (903 px viewport, 888 px document width). The top backtest button completed a Dukascopy-only XAUUSD M1 run, 2026-09-14: 13 trades and 1,432 events; run ID `c2656509-0bfe-4e62-b60d-911e5bbd2e26`. Its manifest names only `C:\AI\Dukascopy_data` BID/ASK source files. Chrome/Edge remain unverified because those browser surfaces are unavailable; no Windows scaling or zoom settings were changed.

Latest follow-up supersedes the earlier drag-unverified note: pointer drag reorder is now verified in the ChatGPT IAB; see the 2026-11-06 toolbar and drag regression entry.

## Screenshot export attempt — 2026-11-06

This earlier attempt could not export a screenshot from the IAB surface and was superseded by later browser/CDP project-file captures plus a native Windows app-window capture, listed below. The detailed Inspector state with an active trade is still missing.

## Saved-run context and replay regression — ChatGPT IAB, 2026-11-06

After the September full-month XAUUSD M1 run, selecting it populated the run's September dates and displayed 30,209 candles. Spring on trade 1 moved to the 2026-09-01 02:00 UTC signal, while the next-candle replay showed the 02:01 UTC fill. The UI was observed at 1265×710 CSS pixels. A screenshot was visible through the IAB capture during testing but could not be exported to a project PNG by the available IAB interface; prior saved captures remain under `evidence/screenshots/`. This is not a Chrome-versus-Edge viewport comparison.

## Optimizer and favicon check — 2026-11-06

The default 8767 dashboard was opened in ChatGPT IAB after launcher restart. It displayed all nine LabBreakout optimizer rows, selected Dukascopy source, and the configured favicon path. The icon returned HTTP 200 (`image/x-icon`, 2,686 bytes); the browser console reported no errors. Evidence: `evidence/test_outputs/dashboard_favicon_route_20261106.json`. Matched ordinary Chrome/Edge rendering and zoom comparisons remain **NOT TESTED** because the current continuation used the requested ChatGPT browser.

## Matched Chrome/Edge verification — 2026-11-06 (supersedes earlier NOT TESTED notes)

- A reproducible Playwright script launched the installed Chrome 157 and Edge 156 executables in isolated headless contexts against `http://127.0.0.1:8767/`; both reported the local-only health banner `Alleen C:\AI\Dukascopy_data`. No backtest or trading action was triggered.
- Both browsers rendered the Plotly chart and passed matched 1280×720, 1920×1080 and 2560×1440 viewport checks with no horizontal document overflow. Chrome/Edge screenshot pairs are saved in `evidence/screenshots/dashboard-{chrome,edge}-{width}x{height}-20261106.png`.
- Both passed the Beeld > Kleurthema hover hierarchy. The right-hand Light submenu contains Neutraal licht, Lichtgroen, Lichtroze and Lichtrood. Each option changed the computed palette and persisted across reload; Dark was restored afterward. Hover help appeared next to the parameter and disappeared after pointer exit. The Inspector widget hid and restored, and Edit Mode opened and canceled without saving test state.
- The browser test reports zero console or page errors. Full result: `evidence/test_outputs/browser_compatibility_matrix_20261106.json`; repeat with `python evidence/test_outputs/browser_compatibility_matrix_20261106.py`.
- This covers installed browser rendering in headless mode at device scale 1. It does not verify Windows display scaling or a native interactive drag gesture in both browsers. The separate live Windows pass verified the Edge PWA app icon and taskbar pin.
