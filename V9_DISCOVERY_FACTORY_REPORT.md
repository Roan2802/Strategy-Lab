# V9 — Automatische strategiefabriek en vernieuwde Portfolio (2026-11-10)

## Kort

De fabriek werkt en is op echte Dukascopy-data gedraaid. Je vindt hem onder **Onderzoeken → Automatische strategieën**, naast **Zelf bouwen**.

Eerste uitgebreide onderzoek: XAUUSD, US30 en DE40 op M15 en H1, alle families, kwaliteitsprofiel "regelmatig".

| Stap | Over |
|---|---|
| Gegenereerd | 240 |
| L1 technisch geldig | 231 |
| L2 snelle ontwikkeltest | 52 |
| L3/L4 volledige ontwikkeltest + optimalisatie | 24 |
| L5 walk-forward + robuustheid (2018–2023) | **0** |
| Bevroren / afgeschermde eindtest / forward | 0 / 0 / 0 |

Er is geen strategie gevonden die streng genoeg is. De drempels zijn niet versoepeld. De eindperiode 2024–2026 is niet aangeraakt; het toegangslog is leeg.

Bijna-kandidaten:
- breakout_prev_day XAUUSD M15: OOS PF 1,32, maar 0,92 trades per week en 78 dagen zonder trade.
- hybrid_orb_adx_atr DE40 M15: OOS PF 1,19, net onder 1,20.

Gegevens van de run:
- Looptijd 9,4 minuten.
- 1.644 trials vastgelegd.
- Maximaal 16,5% CPU (4 workers, prioriteit "lager dan normaal") en 3,0 GB RAM.

Bewijs: `evidence/v9/factory/real_run_*.json`.

## Status per onderdeel

| Onderdeel | Status | Toelichting / bewijs |
|---|---|---|
| Subtab "Automatische strategieën" + "Zelf bouwen" | PASS | `static/factory.js`, browser-screenshots `evidence/v9/factory_browser/` |
| Eenvoudige instellingen + "Geavanceerd" + onderzoeksprofielen (snel / uitgebreid / diepgaand) | PASS | Planvoorbeeld vooraf: aantal strategieën, backtests, optimalisatiepogingen, minuten, workers. Het onderzoek is begrensd (max_candidates, maximale tijd) |
| Modulaire generator, declaratief (geen vrije Python-code) | PASS | `research/generator.py`: 55 families in 7 groepen (trend, uitbraak, mean reversion, price action, statistisch, VWAP/volume, hybride). Exits en risico worden apart gegenereerd. Geen grid of martingale; toevoegen aan winnaars alleen na opt-in |
| IQ Capital-integratie | PASS | 45 dossiers gelezen; 186 componenten (60 bronregel, 68 eigen variant, 24 proxy, 34 niet implementeerbaar met reden). Nooit gepresenteerd als exacte reconstructie; overzetten naar CFD/FX = nieuwe hypothese (`research/components/iq_components.json`) |
| Meertrapsfabriek L1–L8 met vroege afwijzing | PASS (L1–L5 echte data), PARTIAL (L6–L8) | L6–L8 (bevriezen, afgeschermde eindtest, forward) zijn alleen met synthetische tests bewezen, omdat geen echte kandidaat L5 haalde |
| Tijdprotocol + afgeschermde eindperiode | PASS | Ontwikkeling 2004–2017, validatie 2018–2023, afgeschermd vanaf 2024-01-01. De zoekcode krijgt fysiek geen bars van na 2024 (test). Embargo van 2 dagen. HISTORICAL_OOS / REUSED_HOLDOUT / PROSPECTIVE_FORWARD. Auditlog per toegang (`FACTORY_PROTOCOL.md`) |
| Databeschikbaarheid per symbool | PASS | US30 en DE40 beginnen pas in 2014; dat wordt zichtbaar gemeld en de ontwikkelperiode wordt aangepast |
| Kwaliteitsprofielen + verdeling van winst/verlies | PASS | `research/distribution.py`: winstgevende dagen, aandeel van de beste 3/5/10 dagen, reeksen, langste drawdown en tijd onder de top, rolling 20/50/100 trades, sessies, regimes, resultaat zonder topdagen, waarschuwing bij concentratie |
| Statistiek (WF rolling/expanding, MC, bootstrap-CI, perturbatie, kosten/slippage, DSR/PSR, PBO, Holm/BH) | PASS | Hergebruik van de bestaande validatie-engine; N = alle trials |
| Conservatieve uitvoering binnen een candle | PASS | SL en TP in dezelfde candle telt als SL eerst; staat bij elke kandidaat vermeld |
| Ledger van alles, ook verliezers | PASS | Elke kandidaat, stap en trial in het onderzoekslogboek; afgewezen records blijven bewaard |
| Workers, CPU/RAM-grenzen, pauze/hervat/stop, checkpoints, crashherstel | PASS | Hard afschieten midden in een run: hervat zonder dubbele trials (`real_run_restart_test.json`) |
| Dashboard: voortgang, trechter, kandidaten, families, detail | PASS | Detail toont equity (ontwikkeling + walk-forward OOS), trades, regels, parameters, herkomst, jaren, winst-/verliesreeksen, kostenstress, WF-vensters, redenen en "Meer analyse" |
| Veiligheidstests met opzettelijk slechte strategieën | PASS (synthetisch) | `tests/test_factory_safety.py`: geweldig op train maar verlies OOS, weinig trades, alle winst op één dag, verlies bij hogere spread, toekomstige candles, pure overfit, aanpassen na de eindtest, optimizer die afgeschermde bars opvraagt |
| Portfolio: themafix + vereenvoudiging | PASS | Oorzaak van de witte rijen: lichte kleurvariabelen bleven inline actief in dark mode. Voor en na: `evidence/v9/portfolio/before` en `after` |
| Forwardportfolio-koppeling | PARTIAL | Endpoint `POST /api/factory/candidates/{id}/forward` (alleen FORWARD_ELIGIBLE) is getest met synthetische data; geen echte kandidaat beschikbaar |
| AI-assistent (lokale LLM) | NOT BUILT | Optioneel; de fabriek werkt volledig zonder |
| Optuna/TPE | NOT USED | Niet geïnstalleerd; gebruikt wordt random search met successive halving. Installatie alleen na jouw akkoord |

## Browser- en regressietests

- **Fabrieksroute via de browser** (starten, volgen, kandidaat openen), telkens 0 JS-fouten en geen horizontale overflow:
  - Chrome 1600×900 dark
  - Edge 1920×1080 light
  - Chrome 1280×720 dark en light
  - Edge 2560×1440 dark
- **pytest:** 621 geslaagd.
- **V8-regressie:** Chrome 1600×900 19/19 en Edge 1920×1080 19/19, 0 JS-fouten (`evidence/v9/e2e/v9final-*.log`). Een eerdere poging faalde omdat Docker Desktop was gestopt. De app meldde dat correct ("Docker of de Jesse-container draait niet"). Na herstart via de desktop-snelkoppeling is opnieuw getest.

## Belangrijke beperkingen

- **Volume:** Dukascopy-volume is in deze dataset altijd 1 per M1-bar. Volumeproxy's zijn daardoor niet informatief en worden afgewezen, en VWAP is in feite een TWAP. Dat staat bij elke kandidaat.
- **Contractspecificaties:** aannames, niet door een broker bevestigd. DE40-bedragen zijn in EUR.
- **DSR:** de variantie over alle trials in het logboek is groot, waardoor de DSR voor vrijwel elke kandidaat 0 is. Dat is methodologisch verdedigbaar, maar zeer streng. Of N en V per familie of alleen over overlevers berekend moeten worden, is een keuze voor jou.
- **Sessie-close:** benaderd als een tijdsexit (geen klokexit in de backtester).
- **Afgewezen structuren worden niet opnieuw gegenereerd:** latere runs met dezelfde seed geven vooral duplicaten. Kies dus een andere seed of andere markten.
