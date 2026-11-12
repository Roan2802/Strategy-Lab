# V10.2b — PBO-besluit, FX-fallbacks en Shadow-route (2026-11-12)

## Kort

- **PBO-waarschuwing:** PBO wordt nu berekend over de volledige set geschikte L4-configuraties (24 in profiel "uitgebreid"), met afgedwongen CSCV-voorwaarden. Als PBO > 0,5 het enige open punt is, wordt de status **REVIEW_REQUIRED** in plaats van REJECTED. Automatische promotie blijft geblokkeerd tot een mens dat beoordeelt.
- **FX-fallbacks:** de overgebleven paden die 1.0 of een bevroren koers gebruikten, zijn gedicht. Op één punt na: zie "Onzekerheden".
- **Shadow-route:** "Volg in Shadow Research" is in de browser van begin tot eind getest. Daarbij kwamen 2 echte bugs aan het licht, die zijn opgelost.
- **Tests:** pytest geeft 920 geslaagd en 1 overgeslagen (de live providerprobe; de provider was niet bereikbaar).
- **Veiligheid:** niets is gecommit en er zijn geen orders geplaatst. De afgeschermde periode vanaf 2024 en `C:\AI\Dukascopy_data` zijn niet aangeraakt.

## Wat er gewijzigd is

**PBO-poort** (`research/factory.py`, `research/validation.py`, `server.py`, `static/factory.js`):

- **CSCV-voorwaarden** (`pbo_over_l4`):
  - Alle configuraties krijgen dezelfde tijdas.
  - Kosten en kapitaal moeten overeenkomen (vingerafdruk).
  - De set bevat de L4-beste en de top-K.
  - Uitsluiten mag alleen om technische redenen: een backtestfout of 0 validatietrades. Uitsluiten op prestatie gebeurt nooit.
  - Is een voorwaarde geschonden, dan is de uitkomst INSUFFICIENT_EVIDENCE en wordt er geen PBO-getal berekend.
  - Wat bewust afwijkt: CSCV rekent met de Sharpe van dagrendementen, terwijl L4 selecteert op een Pareto-rangorde. Dat staat gedocumenteerd in `STATISTICAL_SELECTION_AUDIT.md` §6b.
- **Registratie:** elke PBO-configuratie wordt opgeslagen als `L5_PBO`-trial, met parameters, hash, validatiemetrics en een verwijzing naar de bestaande L4-ledgerrij. Ze tellen niet extra mee in N van de DSR, omdat het dezelfde configuraties zijn.
- **REVIEW_REQUIRED:** een kandidaat met deze status gaat niet naar L6 en wordt niet bevroren. Hij krijgt ook geen eindtest, geen FORWARD_ELIGIBLE en geen forward-registratie.
- **Beoordeling vastleggen:** dat gaat via `POST /api/factory/candidates/{id}/pbo-review` en wordt opgeslagen in een append-only tabel `pbo_reviews` plus het beslissingslogboek.
  - Accepteren zet de kandidaat terug op WF_PASSED. Daarna moet hij nog steeds door de verplichte DSR-poort in L6.
  - Afwijzen maakt de kandidaat REJECTED; die beslissing is definitief.
  - Faalt naast PBO ook een andere controle, dan blijft de kandidaat REJECTED zoals voorheen.
- **Ongewijzigd:** DSR, walk-forward, robuustheidstests, kostenmodellen en de afgeschermde periode.

**FX-fallbacks:**

- **`instruments.contract_for`:** geeft zonder tijdstip voor een EUR/JPY/CAD-quote nu `FX_RATE_UNAVAILABLE` in plaats van 1.0. Wil een aanroeper toch een plaatshouder gebruiken, dan moet dat expliciet met `conversion_series=True`. De aanroepers zijn aangepast.
- **`portfolio.py`:** bij een open positie zonder actuele koers zijn de geldvelden leeg, met de status FX_RATE_UNAVAILABLE. De stressscenario's die van FX afhangen, staan dan ook op FX_RATE_UNAVAILABLE.
- **`forward_performance.py`:** MAE/MFE gebruikt de echte koers bij sluiting en is FX_RATE_UNAVAILABLE als die koers ontbreekt. Open P&L wordt `None` zonder live koers.
- **Ledger:** `instruments.validate_ledger_contract` is aangeroepen in `server.py` (`bidask_ledger`). Een claim van 1.0 voor een EUR-quote geeft nu 422. Een vaste koers die je zelf invoert, krijgt het label `FIXED_USER_RATE`.
- **`forward_rules._contract`:** weigert onbekende instrumenten en quotes zonder conversiepaar.

**Shadow-route:**

- **Opgelost — blokkerend:** beide knoppen stuurden `source.kind = "candidate"`, wat de backend weigert. Volgen vanuit de fabriek werkte daardoor nooit.
- **Opgelost — dubbele registratie:** twee keer klikken maakte stil versie 2 aan met dezelfde inhoud. Dat geeft nu 422 DUPLICATE_REGISTRATION.
- **Opgelost — meldingen:** weigeringsmeldingen tonen de reden nu in het Nederlands in plaats van als JSON.
- **Nieuw:** het detailscherm heeft een blok "Registratie (bevroren)".
- **Testopslag:** de route wordt getest op een aparte server (poort 8768) met een tijdelijke shadow-opslag, via de omgevingsvariabele `STRATEGY_LAB_SHADOW_DB`. De echte portfolio "Schaduw research 2026-11" hield 3 strategieën.

## Simulatie (pijplijnniveau)

Opzet:

- 300 herhalingen per nulcel en 150 per edgecel.
- Gepaarde seeds en Wilson-intervallen van 95%.
- "Auto" betekent automatische promotie.
- "Bovengrens" betekent promotie als elke REVIEW_REQUIRED zou worden geaccepteerd.

| Scenario | Top-4: auto / review / bovengrens | Alle 24: auto / review / bovengrens |
|---|---|---|
| Nul, nul + regime, nul + correlatie 0,6 | 0 [0; 1,3%] / 0 / 0 | 0 [0; 1,3%] / 0 / 0 |
| Sharpe 0,5 / 1,0 | 0 / ≤11% / 0 | 0 / ≤4% / 0 |
| Sharpe 1,5 | 2,0% / 33% / 3,3% | 3,3% / 3,3% / 3,3% |
| Sharpe 2 | 27% [21; 35] / 31% / 41% | 41% [34; 49] / 0 / 41% |
| Sharpe 3 | 56% [48; 64] / 43% / 98% | 98% [94; 99] / 0 / 98% |
| Plateau, Sharpe 2 | 23% / 52% / 42% | 24% [18; 31] / 45% / 42% |
| Adaptief, 5 rondes, nul | 0/300 | 0/300 |

**Wat dit laat zien:**

- **Geen versoepeling:** de automatische promotie volgt exact de vorige regel. Dat is bit-identiek gecontroleerd met oude seeds (`pbo_review_repro.py`).
- **Valse positieven:** zonder echte edge is er 0 valse promotie, ook in de bovengrens waarin elke review wordt geaccepteerd.
- **Detectiekracht:** de grotere PBO-set vindt meer sterke edges: bij Sharpe 3 98% in plaats van 56%.
- **Zwakke edges** (Sharpe 1,5 of lager) slagen nog steeds vrijwel nooit.
- **Plateaus:** bij een echte plateau-edge is PBO per definitie ongeveer 0,5. Daardoor gaat zo'n 45% naar handmatige beoordeling.

**Echte kandidaten:**

- 234 kandidaten haalden L5, en ze zijn allemaal REJECTED.
- Bij geen van hen was PBO de enige reden. Er wordt dus geen enkele kandidaat REVIEW_REQUIRED.
- De PBO over de volledige set is voor deze records niet opnieuw berekend; daarvoor moeten de backtests opnieuw draaien.

## Werkelijk uitgevoerde tests

- **pytest:** 920 geslaagd, 1 overgeslagen (log `evidence/v102/pytest_v102b.log`).
  - Nieuw: `test_v102_pbo_review.py` (21 tests) en `test_v102_fx_fallbacks.py` (11 tests).
  - Ook nieuw: een API-test die controleert dat de ledger 422 geeft voor een EUR-quote met koers 1.0.
- **Browser, Shadow-route:** 27/27 geslaagd in Chrome 1600×900 en 27/27 in Edge 1920×1080, met 0 JS-fouten (`evidence/v102/ui/shadow_route/`).
- **Volledige browsermatrix na de wijzigingen:** alle runs 0 mislukt (65/65 volledig, 43/43 snel). Eén JS-fout in de eerste volledige Edge-run; herhaling schoon, oorzaak niet vastgesteld. Zie `V10_2_TEST_RESULTS.md`.

## Onzekerheden die overblijven

- **Adaptief scenario:** dit onderscheidt niets. De andere L5-poorten blokkeren alle ruis al voordat PBO aan bod komt. PBO kan hergebruik tussen rondes in principe niet zien.
- **Beoordeling na afloop van een run:** zo'n beoordeling bevriest niet automatisch. L6 moet daarvoor opnieuw draaien.
- **Pre-registratie:** een deel van de nulcampagne liep al toen de pre-registratie werd geschreven. Dat staat vermeld in de audit.
- **Simulatie:** die gebruikt vereenvoudigde vervangers voor de parameteromgeving, de kosten en de random-entry-nul, en normale of t3-rendementen.
- **FX — MAE/MFE:** gebruikt één koers (die bij sluiting) voor de hele uitschieter, in plaats van een koers per bar.
- **FX — registratie:** een bevroren koers die niet 1.0 is, wordt voor EUR/JPY/CAD nog geaccepteerd. Hij wordt tijdens het draaien genegeerd, omdat de engine per candle omrekent.
- **Shadow-UI:**
  - De UI kiest stil de eerste shadow-portfolio; er is geen keuzelijst. Met één echte portfolio is dat onschadelijk.
  - "Verwerken" (advance) is niet getest in de route.
  - De Shadow-groep is alleen in het donkere thema getest.
  - Niet-gekoppelde weigeringscodes worden nog in het Engels getoond.
- **REVIEW_REQUIRED in Shadow:** zo'n kandidaat mag in Shadow Research gevolgd worden. Dat is bedoeld: Shadow is onderzoek en kan nooit naar live.
