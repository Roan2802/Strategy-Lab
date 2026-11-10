# V8 UX & Workflow Overhaul — rapport (2026-11-09)

## 1. Audit vóór de wijzigingen

Screenshots van de oude situatie: `evidence/ux_v8/audit/` (11 schermen, Chrome 1600×900).

| Onderdeel | Status vóór | Bevinding |
|---|---|---|
| Navigatie | gedeeltelijk | 11 losse menu-items in 4 groepen; Backtest/Optimaliseren waren dezelfde pagina met een ander layoutprofiel |
| Dashboard datafeeds | **kapot** | toonde altijd "laatste candle —" (frontend las `last_candle`, API levert `last_candle_at`); lange symboolnamen overlapten |
| Strategy Tester M1 | bewezen werkend | backtest, trades, inspector, replay |
| Strategy Tester M5–D1 | **kapot** | elke run op een hoger tijdframe gaf 0 trades en 0 events: de server stuurde al-geaggregeerde candles naar Jesse, dat ze als 1-minuutcandles las |
| Optimalisatie | gedeeltelijk | standaard werd ook positiegrootte geoptimaliseerd ⇒ alle pogingen faalden op "Not enough balance" |
| Resultaten | gedeeltelijk | alleen ruwe statistieken; geen uitleg, geen onderscheid tussen bewijssoorten; onder de vouw |
| Run vergelijken | gedeeltelijk | toonde ruwe JSON |
| Validatie | bewezen werkend voor Builder-specs en plugins | niet bruikbaar voor Python-strategieën uit de tester; dat stond nergens |
| Instellingen | gedeeltelijk | technische labels (Trials, Seed, Doel "sharpe", Leverage, Warm-up); contractinstellingen verstopt in de Inspector |
| Persistentie | gedeeltelijk | layout en thema bleven bewaard; strategie, markt, periode, parameters en laatste run niet |
| `/api/runs` | traag | 3–11 s per aanroep (alle result.json-bestanden telkens opnieuw geparsed) |
| Forwardtest, Data Manager, Bibliotheek, Builder, Ontdekken | bewezen werkend | functioneel; alleen herplaatst |
| Realtime papertrading, ML-module, news/orderflow/L2 | alleen gepland | niet gebouwd (zie §5) |

## 2. Wat is aangepast

**Navigatie (fase 2)** — vijf hoofddelen in de zijbalk met subtabs bovenaan (`shell.js`: `SECTIONS`, `resolveRoute`, `window.labGo`):
- Home
- Strategieën: Overzicht · Bouwen · Bibliotheek
- Onderzoeken: Backtest · Grafiek · Trades · Inspector · Optimalisatie · Validatie · Journaal · Automatisch onderzoek
- Portfolio: Forwardtest · Portfolio en risico (incl. "Backtests combineren")
- Data

Oude links (`#backtest`, `#validate` …) blijven werken. Elke subtab onthoudt waar je was; de laatst bezochte route komt terug na herstart.

**Begeleide workflow (fase 3)** — `workflow.js`: een stappenbalk boven Onderzoeken met 7 stappen (strategie → data → backtest → trades → optimaliseren → valideren → forward). Elke stap toont klaar / volgende / beschikbaar / geblokkeerd, op basis van echte API-data. Een klik op een stap opent een uitleg: wat je doet, welke instellingen nodig zijn, waar het resultaat staat, wat de volgende stap is, en waarom iets geblokkeerd is. Voorbeelden: "Python-strategie zonder regelspecificatie" of "verdict REJECT, gefaalde controles: …".

**Instellingen (fase 4)**:
- `labels.js` bevat 50 parameter- en 19 instellingslabels in het Nederlands, elk met eenheid, uitleg, voorstel en effect. Alle betekenissen zijn tegen de code gecontroleerd; alleen `min_hold` is nergens in gebruik en gemarkeerd als niet geverifieerd.
- In Geavanceerd zie je ook de technische naam.
- Eenvoudig/Geavanceerd bestond al en bleef behouden.
- De presets staan nu bovenaan.
- Contractinstellingen zijn verplaatst naar Resultaten → Bid/Ask-herwaardering.

**Strategy Tester (fase 5)**:
- Indeling: links Instellingen, midden een grote grafiek met equity en replay, rechts Resultaten en interpretatie, onder een paneel met tabs Trades · Beslislogboek · Optimalisatie · Analyse · Vergelijken · Journaal.
- Slepen, formaat aanpassen, Edit Mode en opgeslagen layouts zijn behouden. Elke weergave (Standaard, Grafiek, Optimalisatie) bewaart nu zijn eigen layout.
- Een geselecteerde trade wordt in de grafiek ingezoomd, met de entrylijn, stop loss en take profit als stippellijn en een label.
- Een detailkaart toont entry, exit, exitreden, SL (ook als die is verplaatst), TP, resultaat en kosten. Onder "Waarom" staan de opgeslagen controles van de signaalcandle, bijvoorbeeld "EMA direction 4329.695 > 4327.42 PASS".
- Als een strategie geen beslisdata opslaat, staat dat er letterlijk; er wordt niets verzonnen.

**Interpretatielaag (fase 6)**:
- Nieuw endpoint `GET /api/runs/{id}/interpretation` (`strategy_lab/interpretation.py`).
- De kopregel geeft in gewone taal het resultaat, het risico, de zwakke periodes, de hoeveelheid bewijs, de kostengevoeligheid (wat er gebeurt bij verdubbelde kosten), de gefaalde controles en de ontbrekende onderzoeken. Daarna volgen de volgende stappen.
- Een bewijsbanner onderscheidt historische backtest, historische OOS, dagelijks vertraagde forwardtest en realtime papertrading. Die laatste is niet beschikbaar, en dat staat er ook.
- Bij optimalisatie wordt gewaarschuwd voor de selectiebias van "de beste van N pogingen".

**Onboarding (fase 7)**:
- `window.labOnboarding` doet een echte mini-studie in 8 stappen op je eigen XAUUSD M15-data:
  - voorbeeldstrategie klaarzetten, data kiezen, backtest, trades bekijken;
  - optimaliseren, valideren via `/api/validate`, de forward-geschiktheid beoordelen en afronden.
- Je kunt hem overslaan (Esc), pauzeren ("Later verdergaan") en opnieuw openen via ? → "Interactieve introductie" of de Home-kaart "Introductie".
- Op Home staan nu vier startacties.

**Gerepareerde fouten (backend)**:
- Hogere tijdframes: de server voert nu 1-minuutcandles aan Jesse, en de runner rekent de opwarmperiode om naar minuten (`server.py`, `runtime/strategy_lab/jesse_runner.py`). Regressietest: `test_higher_timeframe_run_feeds_one_minute_candles_to_jesse`.
- `/api/runs` gebruikt nu een cache per run-map en reageert in 0,28 s in plaats van 5 s.
- De Jesse-fout bij shorten op spot wordt een duidelijke Nederlandse melding met de knop "Futures instellen en opnieuw starten".
- Nieuwe frontendbestanden staan in de whitelist en vallen onder de automatische cache-busting.

**Gerepareerde fouten (frontend)**:
- Datafeeddatums op Home werken weer.
- Labels zijn vertaald (ACHTER/FOUT/let op/klaar).
- Run vergelijken toont een tabel.
- De sessie wordt bewaard en hersteld: strategie, markt, tijdframe, periode, parameters, engine-instellingen en de laatste run.
- Plotly-fouten bij verborgen grafieken zijn weg.
- Positiegrootte wordt standaard niet geoptimaliseerd.
- De doelopties van de optimizer hebben vaste waarden, omdat de vertaling de waarde overschreef.

## 3. Wat is getest

- **pytest:** 331 tests slagen (`python -m pytest strategy_lab/tests -q`), waaronder 5 voor de interpretatielaag en 1 regressietest voor de tijdframe-fix.
- **Browser-E2E** (`evidence/scripts/ux_e2e.py`, Playwright met de echte Chrome en Edge, tegen de productieserver op 8767). Getest:
  1. alle secties en subtabs zonder JS-fouten;
  2. backtest LabBreakout XAUUSD M15 op bestaande data: 9 trades;
  3. interpretatielaag;
  4. trade in de grafiek met entry, SL en TP plus de "Waarom"-checks;
  5. het beslislogboek;
  6. optimalisatie met 4/4 geldige pogingen;
  7. run-vergelijking;
  8. validatie van EmaRsiPullback: echt verdict REJECT;
  9. forwardstap geblokkeerd omdat het verdict niet FORWARD_ELIGIBLE is;
  10. browser sluiten en heropenen, waarbij route, strategie, markt, tijdframe, periode en laatste run terugkomen;
  11. panelen overlappen niet en er is geen horizontale scroll.

  Resultaten staan in `evidence/ux_v8/e2e/e2e-*.json` en de logs.
- **Eindresultaat:** alle runs 0 gefaalde stappen en 0 JavaScript-fouten (volledig: Chrome 19/19, Edge 19/19; snel: 7/7 per formaat).
- **Formaten:** Chrome 1600×900 en 1366×768 (volledig of snel); Edge 1920×1080 (volledig), 1280×720 en 2560×1440.
- **Desktop-snelkoppeling:** "Dukascopy Strategy Lab.lnk" → `launch-hidden.vbs` → Edge-appvenster. Startlog: "Appvenster geopend en gefocust … Klaar".
- **Echte onboarding-run** door de subagent: alle 8 stappen met echte resultaten (screenshots in `evidence/ux_v8/agent/`).

## 4. Screenshots van het nieuwe ontwerp

`evidence/ux_v8/final/`:
- `home.png`, `home-light.png`
- `tester-dark.png`, `tester-light.png`, `grafiek-dark.png`
- `onboarding-1.png`, `workflow-guide.png`
- `strategieen-overzicht.png`, `onderzoeken-validatie.png`, `portfolio-risico.png`, `data.png`

De E2E-stappen per browser en formaat staan in `evidence/ux_v8/e2e/*.png`.

## 5. Problemen die nog bestaan

- Statistische validatie (walk-forward, Monte Carlo, holdout) werkt alleen voor Builder-strategieën en Python-plugins, niet voor de handgeschreven Jesse-strategieën (LabBreakout e.a.). De UI zegt dit nu eerlijk, maar ondersteuning vraagt een Jesse-gebaseerde walk-forward.
- Plugins (GoldReaper) zijn niet selecteerbaar in de tester-werkruimte. Ze hebben hun eigen Backtest- en Valideren-knoppen in Strategieën → Overzicht.
- Afgewezen signalen in de grafiek: alleen opgeslagen beslisevents worden getoond (via het beslislogboek). Er is geen aparte overlay met alle SKIP/FAIL-candles.
- US500 en DE40: de dagelijkse aanvulling meldt "No verified point factor". Hun bi5-puntfactor is nog niet live bevestigd, dus er worden geen nieuwe dagen toegevoegd. De bestaande data is bruikbaar.
- Dukascopy-CDN geeft vandaag veel 503-fouten, waardoor het aanvullen traag gaat.
- Een tester-run (Jesse) kan niet halverwege worden geannuleerd; validatie- en ontdekkingsjobs wel.
- MFE/MAE worden na de tijdframe-fix berekend op 1-minuutcandles. Dat is nauwkeuriger, maar wijkt af van oudere runs.
- De oude gedetailleerde tour (24 stappen) bestaat nog als "Rondleiding langs alle schermen" en verwijst intern naar de oude schermnamen. Hij werkt via de omzettabel, maar enkele teksten noemen nog "Dashboard" en "Data Manager".
- Realtime papertrading, de ML-module en news/orderflow/L2 zijn niet gebouwd (DATA_REQUIRED).
