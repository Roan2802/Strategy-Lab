# V10.1 functie-audit (2026-11-10)

Samengesteld uit de bestaande rapporten (alleen gelezen, niets opnieuw gedraaid). Geen code gewijzigd. Per rij staat de status en welk soort bewijs bestaat.

## Leeswijzer

Statuslabels: PASS, PARTIAL, FAIL, BLOCKED, NOT TESTED, INSUFFICIENT_EVIDENCE.

Bewijstypen (kolommen), van zwak naar sterk:

| Code | Betekenis |
|---|---|
| U | unit-test (logica, handberekende getallen) |
| I | integratietest (meerdere onderdelen, tijdelijke kluis/database) |
| S | synthetische marktdata (fixture, zaagtand, gesimuleerde rendementen, mock-transport, virtuele klok) |
| H | echte historische Dukascopy-data (ontwikkel-/validatieperiode, `C:\AI\Dukascopy_data`) |
| E | echte externe dienst of echte machine (Dukascopy-CDN, Windows Task Scheduler) |
| F | echt prospectief forward (kandidaten die na registratie nieuwe, nog niet bestaande data verwerken) |

`x` = aanwezig, `-` = niet aanwezig.

## Kernconclusie

**Synthetische tests bewijzen alleen dat de mechaniek klopt (rekenregels, bescherming tegen look-ahead, onveranderbaarheid, herstel na crash). Ze zijn geen bewijs voor marktrendement van enige strategie en worden zo ook nergens gepresenteerd.**

Harde feiten over de marktbewijzen:

- Echt prospectief forwardbewijs (F): **geen**. Er bestaan 0 forwardportefeuilles met echte kandidaten en 0 echte prospectieve waarnemingen.
- Realtime paper trading (`REALTIME_PAPER`): **NOT IMPLEMENTED** (geen live feed, geen streaming, geen orderroutering). Live orders zijn uitgeschakeld.
- Geen enkele strategie haalde walk-forward (L5) in een echte run: V9 240 gegenereerd (0 L5), V10 419 gegenereerd (0 L5), V10.1 370 gegenereerd in run 2 (0 L5-geslaagd). Aantal PASSED-kandidaten: **0**. De afgeschermde periode 2024+ is niet aangeraakt (`final_test: false`; 0 HOLDOUT-ledgerregels), dus "goedgekeurd" (L7/L8) is 0 per constructie.
- Het positieve pad L6-L8 (bevriezen, eindtest, forwardregistratie van een echte kandidaat) is alleen met fixtures bewezen.
- Optuna/TPE: niet geïnstalleerd, `optuna_status() = NOT_AVAILABLE`, niet getest (NOT TESTED). Er is niets geïnstalleerd.
- AI-assistent (lokale LLM): niet gebouwd (NOT BUILT, optioneel; de fabriek werkt zonder).
- Alle geldbedragen: PROVISIONAL (contractspecificaties zijn aannames, geen brokerbevestiging).
- Regressie: totaal pytest- en E2E-aantallen: pytest 802 geslaagd; browser-E2E Chrome 1600x900 53/53 en Edge 1920x1080 53/53 (volledig), snel 31/31 op Chrome 1280x720/1600x900/2560x1440 en Edge 1920x1080, dark+light, 0 JS-fouten (evidence/v101/ui, evidence/v101/pytest_final.log)

## 1. V8 doelen (UX, validatie, forward-basis, plugins, bibliotheek)

| Functie | Status | U | I | S | H | E | F | Bron / opmerking |
|---|---|---|---|---|---|---|---|---|
| Workflow-shell, tooltips, evidence-ladder, geen overflow 1366-1920 | PASS | - | x | - | - | - | - | UX_V8_REPORT.md; Playwright Chrome/Edge (UI-gedrag, geen marktbewijs). Verouderd: schermnamen, zie sectie 6 |
| Backtest-tester Jesse (LabBreakout XAUUSD M15) | PASS | x | x | - | x | - | - | UX_V8_REPORT.md; 9 trades, alleen demonstratie van de keten |
| Interpretatielaag, beslislogboek, trade-in-grafiek met SL/TP en "Waarom" | PASS | x | x | - | x | - | - | UX_V8_REPORT.md |
| Run-vergelijking, sessieherstel na heropenen browser | PASS | - | x | - | x | - | - | UX_V8_REPORT.md |
| Statistische validatie Builder en Python-plugins (WF, MC, bootstrap, holdout) | PASS | x | x | - | x | - | - | V8.3 in matrix; voor handgeschreven Jesse-strategieën NOT SUPPORTED in V8 (zie multimarket V10.1) |
| Validatie van EmaRsiPullback (echt verdict REJECT) | PASS | - | x | - | x | - | - | UX_V8_REPORT.md; V9_REAL_MARKET_VALIDATION.md |
| Forwardstap geblokkeerd bij verdict niet FORWARD_ELIGIBLE | PASS | x | x | - | - | - | - | UX_V8_REPORT.md |
| Visual Strategy Builder + Jesse-codegen (zelfde rule_engine) | PASS | x | x | - | x | - | - | V8.2 in matrix |
| IQ Capital-bibliotheek (45 dossiers, 186 componenten) | PASS | x | - | - | - | - | - | IQ_CAPITAL_IMPORT_REPORT.md; nooit exacte reconstructie |
| Python-plugin-contract + GoldReaper-adapter | PASS | x | x | - | x | - | - | V8.2; GoldReaper simuleert zelf de tickvolgorde in M1 (niet door het lab te verifiëren) |
| Append-only onderzoeksledger | PASS | x | x | - | - | - | - | RESEARCH_LEDGER.md; triggers blokkeren UPDATE/DELETE |
| Levenscyclus 13 statussen, poorten A-E | PASS | x | x | - | - | - | - | LIVE_READINESS_GATES.md; poort D geblokkeerd (geen realtime feed) |
| Forward-alerts (verouderde data, drawdown, risico, CUSUM) | PARTIAL | x | - | x | - | - | - | alleen stub-engine, geen echte forward-data |
| Winner-only scale-in (risico <= 1R) | PASS | x | - | x | - | - | - | gaps voorbij de stop kunnen > 1R |
| Realtime paper trading | NOT IMPLEMENTED | - | - | - | - | - | - | niet gebouwd |
| Economische kalender / news-filter | BLOCKED | - | - | - | - | - | - | DATA_REQUIRED, geen point-in-time kalender |
| Orderflow, L2, volumeprofiel | BLOCKED | - | - | - | - | - | - | DATA_REQUIRED, niet afleidbaar uit M1 OHLC |
| ML-onderzoeksmodule | NOT TESTED | - | - | - | - | - | - | DEFERRED, niet gebouwd |

## 2. V9 fabriek (generator, L1-L8, protocol)

| Functie | Status | U | I | S | H | E | F | Bron / opmerking |
|---|---|---|---|---|---|---|---|---|
| Declaratieve generator, 55 families in 7 groepen | PASS | x | x | - | x | - | - | V9_DISCOVERY_FACTORY_REPORT.md |
| Fabriek L1-L5 op echte data (XAUUSD/US30/DE40, M15+H1) | PASS (uitgevoerd) | - | x | - | x | - | - | 240 gegenereerd -> 231 L1 -> 52 L2 -> 24 L3/L4 -> **0 L5** |
| Fabriek L6-L8 (bevriezen, eindtest, forwardkoppeling) | PARTIAL | x | x | x | - | - | - | alleen fixtures; geen echte kandidaat haalde L5 |
| Tijdprotocol: ontwikkeling 2004-2017, validatie 2018-2023, afgeschermd >= 2024, embargo 2 dagen | PASS | x | x | x | x | - | - | FACTORY_PROTOCOL.md; zoekcode krijgt fysiek geen bars >= 2024 (test) |
| Auditlog per toegang afgeschermde periode | PASS | x | x | x | - | - | - | log is leeg in alle echte runs |
| Veiligheidstests met opzettelijk slechte strategieën | PASS | x | x | x | - | - | - | test_factory_safety.py; synthetisch, bewijst alleen afwijzing van bekende fouten |
| Kwaliteitsprofielen en winst-/verliesverdeling | PASS | x | - | - | x | - | - | distribution.py |
| Conservatieve SL/TP-volgorde binnen een candle (SL eerst) | PASS | x | - | x | - | - | - | per kandidaat vermeld |
| Workers, CPU/RAM-grenzen, pauze/hervat/stop, crashherstel | PASS | x | x | - | x | - | - | hervat zonder dubbele trials na harde kill |
| Fabrieks-dashboard (voortgang, trechter, kandidaten, detail) | PASS | - | x | - | x | - | - | browser: Chrome/Edge, 0 JS-fouten |
| Forward-koppeling `POST /api/factory/candidates/{id}/forward` | PARTIAL | x | x | x | - | - | - | alleen FORWARD_ELIGIBLE; geen echte kandidaat beschikbaar |
| Echt-data-validatie van drie strategietypen (EmaRsiPullback, LabBreakout, GoldReaper v2) | PASS (uitgevoerd) | - | x | - | x | - | - | V9_REAL_MARKET_VALIDATION.md; twee REJECT, GoldReaper OOS_PASSED maar niet FORWARD_ELIGIBLE (onbekend aantal externe trials, holdout 7x eerder gezien) |
| Optuna/TPE | NOT TESTED | - | - | - | - | - | - | niet geïnstalleerd; random search + successive halving |
| AI-assistent | NOT BUILT | - | - | - | - | - | - | optioneel |

## 3. V10 (UI, multimarket, data-supervisor, anti-overfitting)

| Functie | Status | U | I | S | H | E | F | Bron / opmerking |
|---|---|---|---|---|---|---|---|---|
| UI-herontwerp (Strategieën/Onderzoeken, geen versleepbare widgets) | PASS | - | x | - | - | - | - | V10_UI_REDESIGN.md; Chrome 49/49, Edge 49/49, snel 27/27 (dark+light), 0 JS-fouten |
| Testen op meerdere markten voor Builder- en fabrieksstrategieën | PASS | x | x | - | x | - | - | MULTI_MARKET_VALIDATION.md; breakout_prev_day = MARKET_SPECIFIC; bedragen PROVISIONAL |
| Instrumentregister + valutaomrekening (DE40-fix) | PASS | x | x | - | x | - | - | EUR-quote op USD-rekening; zie V10.1 voor causale as-of-conversie |
| DE40-sessiekalender hersteld uit echte data | PASS | - | - | - | x | - | - | ma-do 00:00-22:15, vr 00:00-22:00 Berlijn |
| Data-supervisor, Windows-taak, statuspagina | PASS (scheduler/backoff echt) / PARTIAL (slaapstand, lange storing) | x | x | x | - | x | - | DUKASCOPY_MONITOR_VALIDATION.md; zie V10.1 sectie 4 |
| DSR-formule-audit, effectief N, globale vs familie-DSR | PASS | x | - | x | - | - | - | DSR_METHODOLOGY_AUDIT.md, **gate-beschrijving achterhaald**, zie sectie 6 |
| Nested walk-forward met purge/embargo, robuustheid, concentratie, baselines | PASS | x | - | x | - | - | - | WALK_FORWARD_VALIDATION.md, ANTI_OVERFITTING_VALIDATION.md |
| Fabriek met statusladder, pre-registratie, ledger van zoekbeslissingen | PASS | x | x | - | x | - | - | FACTORY_PROTOCOL.md |
| Benchmark A-I (synthetisch) | PASS | - | - | x | - | - | - | A: 0/100 vals-positief; B: 0/20 (3 j), 16/20 (6 j), 20/20 (10 j). Alleen methodebewijs |
| Scenario J: echte Dukascopy-run (4 markten, M15+H1) | PASS (uitgevoerd), 0 kandidaten | - | x | - | x | - | - | 419 -> 307 L1 -> 80 L2 -> 45 L3 -> 30 L4 -> **0 L5** |
| Bevriezen / eindtest / forwardregistratie echte kandidaat | PARTIAL | x | x | x | - | - | - | alleen fixtures |
| Multimarket voor Jesse en plugins | NOT SUPPORTED in V10 | - | - | - | - | - | - | opgevolgd door V10.1, sectie 4 |

## 4. V10.1 (statistiek, fabriek, forward, portefeuille, data, multimarket, broker)

| Functie | Status | U | I | S | H | E | F | Bron / opmerking |
|---|---|---|---|---|---|---|---|---|
| DSR-eindmethodologie: drie varianten naast elkaar (paper, NC-DSR, dep-DSR), gate = NC-DSR met zoekniveau-effectief N, drempel 0,95 | PASS (formules en kalibratie) | x | - | x | - | - | - | DSR_FINAL_METHODOLOGY.md. NC-DSR is niet de paper-DSR; label zegt dat. Paper-voorbeeld exact gereproduceerd (SR0 0,11317; DSR 0,90040) |
| Statistische kalibratie: vals-positief <= 1,3% in alle nulcellen, power per variant, adaptieve hergebruikscenario's (S1-S3) | PASS (alleen synthetisch) | - | - | x | - | - | - | STATISTICAL_CALIBRATION_REPORT.md. Alleen Gauss/t/AR/GARCH-generatoren, geen sprongen of structurele breuken. Zegt niets over echte markten |
| Betrouwbaarheidscontrole DSR (T_eff < 60, < 30 trades, extreme scheefheid): INSUFFICIENT_EVIDENCE in plaats van FAIL | PASS | x | - | x | - | - | - | DSR_FINAL_METHODOLOGY.md sectie 4 |
| Uitkomstmapping L6: DSR < 0,95 met SR > 0 -> INSUFFICIENT_EVIDENCE, SR <= 0 -> REJECTED; oude L6-afwijzingen getoond als INSUFFICIENT_EVIDENCE | PASS | x | - | x | - | - | - | `stored_status` bewaart origineel |
| Gecontroleerde-signaaltests (ruis niet gepromoot, ingebouwd voordeel gevonden, zwak signaal = onzekerheid, kleine steekproef = INSUFFICIENT_EVIDENCE, dual-positive regel) | PASS | x | x | x | - | - | - | FACTORY_FINAL_VALIDATION.md sectie A; 35 tests in test_v101_stats.py. Synthetisch |
| Echte Dukascopy-fabrieksrun 2 (5 markten, M15/H1/H4): 370 gegenereerd, 130 duplicaten, 10 in L5, **0 geslaagd** | PASS (uitgevoerd), 0 kandidaten | - | x | - | x | - | - | FACTORY_FINAL_VALIDATION.md sectie B; `final_test: false`; hoogste NC-DSR 0,0057. Run 1 (336, supervisor-bug) onvolledig, niet gebruikt voor conclusies |
| Supervisor-bug in run 1 (geen heartbeat bij lange stap, dubbele supervisors, 7 kandidaten blijven IN_PROGRESS) en fix | PASS (opgelost, test `test_superseded_supervisor_stops`) | x | x | - | x | - | - | alleen run 2 na de fix is volledig |
| Integriteit: ledger compleet, herhaalde rondes herkend (REUSED_FOR_SELECTION), bevroren record onveranderlijk (content-addressed), geen toekomstige candles | PASS | x | x | - | x | - | - | FACTORY_FINAL_VALIDATION.md sectie C |
| Optimizer: random search + successive halving, seeded | PASS (unit) | x | - | - | - | - | - | Optuna NOT TESTED |
| Forward end-to-end pijplijn in geïsoleerde kluis (ontwikkelen -> bevriezen -> eindtest -> registratie -> 5 UTC-dagen -> herstart/crash) | PASS (alleen technisch bewijs) | x | x | x | - | - | - | END_TO_END_FORWARD_VALIDATION.md. Zaagtand met ingebouwd voordeel; zegt niets over echte strategieën |
| Forward-engine audit: alleen prospectief, first publication wins, exactly-once, deterministische order-/positie-ID's, append-only triggers + hashketen (`verify_ledger`) | PASS | x | x | x | - | - | - | zelfde rapport; oude portefeuilles weigeren voortzetting (MODEL_SHA256) |
| Evidenceklassen: HISTORICAL_BACKTEST, HISTORICAL_OOS, DAILY_DELAYED_FORWARD geïmplementeerd; REALTIME_PAPER | PASS (3 klassen) / NOT IMPLEMENTED (REALTIME_PAPER) | x | x | x | - | - | - | **0 echte DAILY_DELAYED_FORWARD-waarnemingen** |
| Portefeuille: gedeeld saldo, allocatie per lid, gedeeld marginbudget, gross posities, limieten (dagverlies, drawdown, symboolrisico, prijssprong, datagat) | PASS (mechaniek, synthetisch) | x | x | x | - | - | - | PORTFOLIO_RISK_ACCEPTANCE.md; geen bewijs over strategieën |
| Valutacontrole forward (`fx_check`): BLOCKED / PLACEHOLDER_RATE / WARNING / UNKNOWN_INSTRUMENT | PARTIAL | x | x | x | - | - | - | PLACEHOLDER_RATE wordt gemarkeerd, niet geblokkeerd; forward gebruikt statisch tarief |
| Correlatie-API (klassen gescheiden, `min_obs`, INSUFFICIENT_EVIDENCE bij te weinig dagen) | PASS | x | x | x | - | - | - | forward-correlaties alleen op fixtures |
| Dukascopy-taak in Windows Task Scheduler: geïnstalleerd, 30 min + bij aanmelden, StartWhenAvailable | PASS (echt) | - | - | - | - | x | - | DUKASCOPY_SYNC_ACCEPTANCE.md; runs op 16:30, 17:00, 20:30 lokaal, resultaat 0; app mag dicht |
| Achterstand inhalen (9 markten) | PARTIAL | x | x | x | x | x | - | 3 van 9 actueel, 6 lopen achter; echte HTTP 503 en URLError afgehandeld met backoff; geen 429/WAF |
| Kalenders + herkomst (`calendar_source`) | PASS als DERIVED_FROM_DATA; geen VERIFIED_PROVIDER | - | x | - | x | - | - | alle 9 markten DERIVED_FROM_DATA; geen sessietabel van Dukascopy geraadpleegd |
| Datavalidatie: sha256 per partitie, quarantaine + herdownload corrupte dagen, calendar-defect US30/US500/USTEC gerepareerd | PASS | x | x | x | x | x | - | origineel ongewijzigd (grootte/mtime), maar niet schrijfbeveiligd |
| Overallstatus nooit groen bij achterstand | PASS | x | x | - | - | x | - | test + echte status "PARTIAL, 3 van 9 actueel" |
| Herstelscenario's (503, 429, WAF, sleep/wake, herstart, netwerkverlies, dubbele trigger, corrupt) | PASS (grotendeels S) | x | x | x | - | x | - | echt: 503, URLError, BUSY-vergrendeling; sleep/wake en Windows-herstart alleen gesimuleerd |
| Multimarket voor Jesse-strategieën en plugins (instrumentbinding, prijsschaalwaarschuwing, kostennormalisatie, ledger per markt) | PASS (mechaniek) | x | x | - | x | - | - | MULTI_MARKET_JESSE_PLUGIN_VALIDATION.md; LabBreakout NO_EDGE (probe, kort venster, `SHORT_PERIOD`); gold_reaper op EURUSD/DE40 INSTRUMENT_BOUND; korte vensters zijn geen generalisatiebewijs |
| Causale valutaconversie op exit-bar (DE40, EUR/USD-rekening) | PASS (research-backtests) | x | x | - | x | - | - | zelfde rapport sectie 3; niet in forward |
| Uitvoeringsrealisme-audit Bid/Ask-engine (fills, SL/TP, gaps, slippage, commissie, lotafronding) | PASS | x | - | x | - | - | - | handberekende referentietests |
| Partial exits, margin/max lot/stop-out, overnight swap | NOT MODELLED | - | - | - | - | - | - | gedocumenteerd, geen implementatie |
| Jesse vs Bid/Ask reconciliatie bedrag-voor-bedrag | NOT TESTED | - | - | - | - | - | - | alleen de verschilformule spread x qty x contractgrootte voor een referentietrade |
| Brokerveronderstellingenregister + geldpoort | PASS (poort sluit correct) | x | - | - | - | - | - | BROKER_ASSUMPTIONS_REGISTER.md; 7 blokkerende velden per markt onbevestigd, `money_status = PROVISIONAL` |
| Brokergeverifieerde contractspecificaties | BLOCKED | - | - | - | - | - | - | geen brokergegevens beschikbaar |
| Live-gereedheid | FAIL (niet gereed) | - | - | - | - | - | - | live gateway UITGESCHAKELD, geen router; checklist grotendeels open |

## 5. Wat per bewijsklasse werkelijk bestaat

| Klasse | Bestaat? |
|---|---|
| U / I | Ja, breed (zie pytest 802 geslaagd; browser-E2E Chrome 1600x900 53/53 en Edge 1920x1080 53/53 (volledig), snel 31/31 op Chrome 1280x720/1600x900/2560x1440 en Edge 1920x1080, dark+light, 0 JS-fouten (evidence/v101/ui, evidence/v101/pytest_final.log)) |
| S | Ja; kalibratie, benchmarks, E2E-forward, portefeuillefixtures. Mechaniek en methodecontrole, geen marktprestatie |
| H | Ja; fabrieksruns zonder geslaagde kandidaat, drie V9-strategie-oordelen (twee REJECT, één OOS_PASSED met herkomstbeperking), multimarket-probes. Ontwikkel- en validatieperiode; validatieperiode is meermaals hergebruikt (`REUSED_FOR_SELECTION`) |
| E | Ja, beperkt; echte Task Scheduler-runs, echte 503/URLError van Dukascopy. Sleep/wake en Windows-herstart niet echt uitgevoerd |
| F | **Nee. 0 forwardportefeuilles, 0 prospectieve waarnemingen.** `REALTIME_PAPER` niet geïmplementeerd |

## 6. Verouderde of vervangen documenten en uitspraken

| Document / uitspraak | Status | Vervangen door |
|---|---|---|
| `DSR_METHODOLOGY_AUDIT.md`, de gate-beschrijving (V_null = 1/T_eff, "value_paper_v") | VEROUDERD voor de gate; blijft geldig als formule-audit | `DSR_FINAL_METHODOLOGY.md` |
| V9-uitspraak "DSR is voor vrijwel elke kandidaat 0, keuze N/V per familie of overlevers is aan jou" (V9_DISCOVERY_FACTORY_REPORT.md, Belangrijke beperkingen) | VERVANGEN: beslissing genomen en gekalibreerd, gate = NC-DSR globaal | DSR_FINAL_METHODOLOGY.md sectie 3 |
| V10-uitspraak "DSR-gate gebruikt V_null; keuze ter beoordeling" (V10_IMPLEMENTATION_REPORT.md beslissing 1, REMAINING_ISSUES.md V10) | VERVANGEN: onderbouwd met kalibratie; NC-DSR is niet de paper-DSR en heet niet meer zo | DSR_FINAL_METHODOLOGY.md |
| V10 "DSR-afwijzing = REJECTED" | VERVANGEN: DSR < 0,95 met SR > 0 geeft INSUFFICIENT_EVIDENCE; oude records worden zo getoond | DSR_FINAL_METHODOLOGY.md sectie 4 |
| V10-uitspraak "Single-window-regel geeft FAIL bij korte historie" | VERVANGEN: < 4 vensters = INSUFFICIENT_EVIDENCE | FACTORY_FINAL_VALIDATION.md sectie A |
| V10-uitspraak "Multi-market voor Jesse/plugins: NOT_SUPPORTED" | VERVANGEN voor het draaien (met instrumentbinding); `reoptimise` en afwijkend risicomodel blijven NOT_SUPPORTED | MULTI_MARKET_JESSE_PLUGIN_VALIDATION.md |
| V10-uitspraak "DE40 EUR-conversie default 1.0" (V9.1) en onvolledige DE40-conversie | VERVANGEN voor research-backtests (causaal as-of); voor forward nog PLACEHOLDER_RATE | PORTFOLIO_RISK_ACCEPTANCE.md, MULTI_MARKET_JESSE_PLUGIN_VALIDATION.md |
| V10-uitspraak "Data: 7/9 markten nog aan het inhalen" | VERVANGEN: nu 6 van 9 lopen achter (3 van 9 actueel) | DUKASCOPY_SYNC_ACCEPTANCE.md |
| V9-uitspraak "Forward-engine kent alleen Builder-regels en LabBreakout-snapshot; geen audit" | GEDEELTELIJK VERVANGEN door audit, append-only, hashketen | END_TO_END_FORWARD_VALIDATION.md |
| V9-fabrieksrun (240) en V10-run (419) | Blijven geldig als historische runs; niet vergelijkbaar met V10.1 door andere DSR-gate en andere markten/timeframes | FACTORY_FINAL_VALIDATION.md |
| V10.1 fabrieksrun 1 (`fr_20261110_192949_b91ae5`) | ONVOLLEDIG (supervisor-bug), alleen bewaard als bewijs | run 2 (`fr_20261110_193539_c1af37`) |
| `UX_V8_REPORT.md` sectie 5 (schermnamen Dashboard/Data Manager, "plugins niet selecteerbaar in tester", "validatie niet voor Jesse") | VEROUDERD door V10-navigatie en V10.1-multimarket; niet herlezen of bevestigd in V10.1 | `V10_UI_REDESIGN.md` |
| pytest-aantallen (331, 388, 621, 686, 798) in oudere rapporten | momentopnames, vervangen door het eindcijfer in pytest 802 geslaagd; browser-E2E Chrome 1600x900 53/53 en Edge 1920x1080 53/53 (volledig), snel 31/31 op Chrome 1280x720/1600x900/2560x1440 en Edge 1920x1080, dark+light, 0 JS-fouten (evidence/v101/ui, evidence/v101/pytest_final.log) | zie dat cijfer |
| Alle bedragen uit rapporten V8-V10.1 | PROVISIONAL, niet vervangen: geen brokerbevestiging | BROKER_ASSUMPTIONS_REGISTER.md |

## 7. Eindoordeel

- Methodiek, mechaniek en dataketen: grotendeels PASS met bewijs op unit-, integratie- en synthetisch niveau.
- Marktbewijs: er is **geen** strategie met aangetoond voordeel; 0 PASSED-kandidaten; geen enkele strategie haalde walk-forward in een echte run. De hoogste NC-DSR van V10.1 is 0,0057 tegen drempel 0,95.
- Prospectief bewijs: bestaat niet. Alle uitspraken over rendement blijven INSUFFICIENT_EVIDENCE totdat bevroren regels in een echte forwardperiode zijn gevolgd.
- Geld en live: PROVISIONAL en NOT live-ready.
- Open punten staan geconsolideerd in `FINAL_REMAINING_ISSUES.md`.
