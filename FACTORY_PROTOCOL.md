# Strategy Discovery Factory: protocol, stages and first real run (2026-11-10, V10-update)

Code: `strategy_lab/research/factory.py` (stages, profielen, store, views), `protocol.py` (tijdsprotocol, databewaking,
bevroren records, `final_test`, audit, meervoudig testen), `distribution.py` (winstverdeling), `factory_worker.py`
(supervisor en werkprocessen), `strategy_lab/factory_api.py` (`/api/factory/*`). Tests: `strategy_lab/tests/test_factory.py`,
`test_factory_safety.py`, `test_factory_worker.py`. Generator: `strategy_lab/research/generator.py` (55 families); als dat
bestand ontbreekt of niet importeert, valt de factory terug op `discovery.generate_hypotheses` (shim). De generator wordt per
run vastgelegd: een run blijft de generator gebruiken waarmee hij is gestart.

`discovery.run_discovery` (`/api/discovery/run`) blijft bestaan als snelle variant met één splitsing (geen optimalisatie,
walk-forward of verzegelde test). Beide gebruiken dezelfde Bid/Ask-backtestengine, hetzelfde onderzoekslogboek en dezelfde
telling van trials. De factory is de volledige pijplijn.

## 1. Tijdsprotocol

| Periode | Standaard | Gebruik |
|---|---|---|
| Ontwikkeling | 2004-01-01 tot 2018-01-01 | L1–L4: generatie, snelle tests, volledige backtest, optimalisatie |
| Validatie | 2018-01-01 tot 2024-01-01 | L5: walk-forward rolling én expanding, robuustheid, modelselectie (L6) |
| Verzegeld | 2024-01-01 tot en met de laatste volledig complete dag | L7: één eindtest per bevroren kandidaat |

- **Per markt gemeten** (`protocol.data_availability`) en niets opgevuld: eerste betrouwbare candle (eerste reeks van 5 werkdagen
  met ≥ 50% van het mediane aantal bars), laatste complete dag (de lopende dag telt nooit mee, een onvolledige laatste dag
  wordt overgeslagen), werkdagen zonder candles, ontbrekende Ask en spreadstatistiek per jaar.
- **Aanpassen** (`build_ranges`): de ontwikkelperiode begint op de eerste betrouwbare candle plus minimaal 30 dagen warm-up.
  US30 en DE40 beginnen daardoor op 2014-01-31, XAUUSD op 2004-01-31. Ontwikkeling korter dan 2 jaar of validatie korter dan
  3 jaar geeft `INSUFFICIENT_SUPPORT`. Een verzegelde periode korter dan 180 dagen betekent: geen eindtest.
- **Warm-up en embargo**: elk venster krijgt een causale warm-up van max(30 dagen, look-back van de spec). Bars voor de start
  dienen alleen voor indicatoren. Trades die aan het einde van een zoek- of validatievenster nog open staan (`END_OF_DATA`)
  worden gemarkeerd en niet meegeteld. In de verzegelde test worden ze gemarkeerd (`boundary_trades_flagged`) en wel
  meegeteld.
- **Databewaking**: zoek- en optimalisatiecode krijgt alleen `SearchData`. Die leest de parquet met een filter
  `time_utc < verzegelde start` en weigert elk verzoek dat die grens raakt met `SealedAccessError`. Dat staat in de code én is
  getest, ook voor L4 met een gemanipuleerde periode.

## 2. Bevroren record, `final_test` en audit

- `SealedVault.freeze(record)` vereist spec, parameters, kostenmodel, risicomodel, selectiecriteria, code-sha, dataset-sha
  (sha256 van het parquetbestand), onderzoeksversie, seed, signature, familie en verzegelde periode. Het id is de hash van het
  record. De tabellen `frozen`, `sealed_results` en `sealed_audit` staan in `data_cache/factory.sqlite3` en zijn append-only:
  triggers weigeren UPDATE en DELETE.
- `final_test(frozen_id)` is de enige code die verzegelde bars leest. Hij controleert of het bestand dezelfde sha heeft als
  het bevroren record en schrijft elke toegang naar de audit met wie, wanneer, wat en het resultaat. Mogelijke acties:
  `ACCESS_START`/`ACCESS_DONE`, `REPEAT_READ` (eerder resultaat teruggegeven, niets opnieuw gedraaid), `BLOCKED_REUSED_HOLDOUT`,
  `REFUSED` en `ERROR`.
- **Lijn**: signature, ouder en familie, telkens per symbool+timeframe. Heeft iets uit dezelfde lijn al een eindtest op een
  overlappende verzegelde periode, dan wordt de nieuwe test geweigerd (`SealedReuseError`). Met expliciete `allow_reuse` mag hij
  draaien, maar dan als `REUSED_HOLDOUT`. In L1 krijgt zo'n kandidaat meteen `REUSED_HOLDOUT` en wordt hij niet opnieuw
  geoptimaliseerd. Dit is bewust streng: per verzegelde periode krijgt elke familie per markt één schone eindtest.
- **Bewijsklassen**: `HISTORICAL_OOS` (schone verzegelde test), `REUSED_HOLDOUT`, `PROSPECTIVE_FORWARD` (na forwardregistratie).
- **Meervoudig testen** (`SealedVault.multiple_testing`): per eindtest een eenzijdige p-waarde uit een stationaire
  block-bootstrap van dagrendementen. Daarop Holm en Benjamini-Hochberg over **alle** kandidaten die ooit op de verzegelde
  periode zijn getest. De DSR gebruikt N = alle eindtests + alle zoek-trials in het onderzoekslogboek.

## 3. Stages per kandidaat

Elke stage wordt opgeslagen (append-only `stage_results`) en in het onderzoekslogboek gezet. Een afgewezen kandidaat houdt
zijn record en reden.

| Stage | Wat | Afwijzen als (kwaliteitsprofiel q) |
|---|---|---|
| L1 | `generator.level1_check` (schema, data, look-ahead via afkap- en mutatietest, reproduceerbaarheid, tegenstrijdigheden, duplicaat), daarnaast `rule_engine.validate_spec`, Jesse-codegen + compile (nooit uitgevoerd), eigen causaliteitstest, Ask-dekking, lijncontrole tegen de kluis, dedup tegen signatures in het logboek | een check faalt |
| L2 | standaardparameters op 3–4 representatieve ontwikkelvensters (jaren met hoge/lage volatiliteit, sterke trend, zijwaarts) | geen trades; < 0,4·q trades/week; PF < 0,90; DD in een venster > 2·q.max_dd; > 50% van de winst uit één trade of één dag |
| L3 | volledige ontwikkelperiode + uitsplitsing per jaar, maand, sessie en regime | < 0,5·q trades/week; PF < 0,95 |
| L4 | random search (standaardwaarden eerst, met seed) + successive halving (η = 3: vensters → tweede helft → volledig), multi-objective Pareto (haalbaarheid frequentie, RF, PF, jaarstabiliteit); elke trial in `trials` (idempotente sleutel) én in het logboek | netto ≤ 0; PF < 1,05; < 0,75·q trades/week; DD > 1,5·q.max_dd; < 30 trades |
| L5 (V10) | **genest walk-forward** (`nested_wf.py`): binnen elk buitenste trainvenster kiest de binnenlus parameters op inner-train en de variant (basis / zonder sessiefilter) op inner-validatie; buiten rolling (3 jaar) en expanding, testvensters van 182 dagen over 2018–2023; purge/embargo uit de echte afhankelijkheid (max houdduur, max look-back). Robuustheid (`robustness.py`): block- en stationaire bootstrap, kosten-Monte-Carlo met echte spread, timing-MC, adverse fill, uitvoeringsscenario's (entry/exit 1 bar later, beide, spread ×1,5, slippage +2), eenheidsbewuste parameteromgeving + heatmap (plateau/piek), causale regimes, concentratie, willekeurige-instap-nulhypothese, baselines + eenvoudigere varianten; PBO over alle geneste configuraties; DSR-records familie + globaal (voorlopig) | een WF-check FAIL (incl. afhankelijkheid van één venster), scorecard niet OOS_PASSED/FORWARD_ELIGIBLE, verslaat de baseline niet, of bewijsklasse niet SUPPORTED_UNDER_ASSUMPTIONS. Te weinig effectieve waarnemingen geeft `INSUFFICIENT_EVIDENCE` |
| L6 (V10) | **eindpoort meervoudig testen**: globale DSR (zoekniveau, alle rondes, effectieve N) opnieuw berekend na afloop van de hele zoektocht; < min_dsr → `REJECTED`. Daarna selectie: rangorde globale DSR → PF → RF, `max_frozen`, `max_per_family`, gelijkenis (rendement-corr > 0,7, instap-overlap > 0,6, zelfde architectuur+markt) → bevriezen van de laatst gekozen WF-configuratie (variant + parameters) | anders `NOT_SELECTED` met reden |
| L7 | `final_test` + Holm/BH | — |
| L8 | `OOS_APPROVED` als verzegeld: trades ≥ max(30, 0,75·q·weken), netto > 0, PF ≥ q, DD ≤ q, Holm-p ≤ α, klasse HISTORICAL_OOS. `FORWARD_ELIGIBLE` als daarnaast de L5-scorecard FORWARD_ELIGIBLE is en het forwardmodel de spec ondersteunt. Registreren gebeurt alleen op verzoek (`POST /api/factory/candidates/{id}/forward`, anders 409) | — |

**Kwaliteitsprofielen** (configureerbaar via `advanced.quality`, en `min_trades_per_week`/`max_dd_pct` in de body):

| | trades/week | OOS-trades | PF OOS | RF OOS | max DD | top-3-dagen | max dagen zonder trade |
|---|---|---|---|---|---|---|---|
| regelmatig | 1 | 150 | 1,20 | 1,5 | 15% | ≤ 50% | 60 |
| zeer_actief | 5 | 500 | 1,15 | 2,0 | 15% | ≤ 40% | 21 |
| laag_frequent | 0,25 | 50 | 1,30 | 1,5 | 20% | ≤ 50% | 180 |

Voor alle profielen gelden verder: parameterstabiliteit ≥ 0,7, PBO ≤ 0,5 (diepgaand ≤ 0,3), WFE ≥ 0,5, expanding-OOS > 0,
spread +50% > 0, DSR ≥ 0,95 en PSR ≥ 0,95 (scorecard), α = 0,05. De verwachtingswaarde telt alleen als PASS wanneer het
95%-block-bootstrap-interval van de gemiddelde trade boven nul ligt.

**Onderzoeksprofielen**: `snel` (max. 4 families, vensters van 3 maanden, 6 trials, **geen eindtest**), `uitgebreid` (alle
families, vensters van 6 maanden, 24 trials, top-4 walk-forward, eindtest voor maximaal 6), `diepgaand` (40 kandidaten per markt,
48 trials, top-6, correlatieanalyse, strengere PBO, eindtest voor maximaal 10). `POST /api/factory/plan` schat kandidaten,
backtests (verwacht en maximaal), trials, minuten en workers. Meer dan 2000 kandidaten, meer dan 500 trials of meer dan
250.000 backtests wordt geweigerd (422).

## 4. Werkprocessen, wachtrij en herstel

- De API start één losgekoppelde supervisor (`python -m strategy_lab.research.factory_worker`) met prioriteit BELOW_NORMAL.
  Er draait geen onderzoek in het FastAPI-proces. De supervisor gebruikt een `ProcessPoolExecutor` met standaard
  min(4, kernen/4) workers. Elke worker zet zichzelf via psutil op BELOW_NORMAL.
- Een RAM-plafond per run (standaard 8000 MB, RSS van supervisor en workers samen): daarboven start er geen nieuwe taak.
  Na `max_minutes` worden er geen nieuwe kandidaten meer gestart (`NOT_RUN`); L6–L8 lopen daarna nog wel.
- **Pause**: er worden geen nieuwe taken uitgedeeld en lopende workers wachten bij de volgende controle. **Resume**: ze gaan
  verder. **Stop**: de pool wordt gedood, open taken krijgen `cancelled` en kandidaten die nog liepen `STOPPED`.
- **Checkpoints** in `factory.sqlite3`: runs, candidates, `stage_results` (append-only, idempotent per kandidaat en stage),
  `trials` (append-only, sleutel = sha van run, kandidaat, rung en parameters), tasks met heartbeat, en een log.
  Na een crash of herstart worden taken van dode workers opnieuw in de wachtrij gezet en gaan ze verder vanaf de laatste
  afgeronde stage. Al berekende trials worden hergebruikt. Een gestorven worker (`BrokenProcessPool`) leidt tot een nieuwe pool
  en een nieuwe wachtrijplaats (maximaal 3 pogingen). Een taak zonder heartbeat binnen `stuck_timeout_s` (900 s) wordt gedood
  en opnieuw in de wachtrij gezet. Is de supervisor dood (heartbeat ouder dan 20 s), dan start de API hem opnieuw bij de
  volgende `/api/factory/runs`-aanvraag.

## 5. Eerste echte run (bewijs in `evidence/v9/factory/`)

Run `fr_20261110_094132_eafc58` via `POST /api/factory/runs`: XAUUSD, US30, DE40 × M15, H1, alle families, uitgebreid,
regelmatig, 240 kandidaten, 32 trials per kandidaat, 4 workers, budget 80 minuten. Generator: `generator.py`.

| | aantal |
|---|---|
| gegenereerd | 240 (40 per markt/timeframe) |
| technisch geldig (L1) | 231 |
| snelle test geslaagd (L2) | 52 |
| ontwikkeling gevalideerd (L4) | 24 |
| walk-forward geslaagd (L5) | **0** |
| bevroren / eindtest / OOS goedgekeurd / forward | 0 / 0 / 0 / 0 |

- Waar ze vielen: L1 9 (onder meer een volume-proxy zonder informatie), L2 179 (125 zonder voorlopige edge na kosten, 29 te
  weinig trades, 15 afhankelijk van één trade of dag, 9 zonder trades), L3 21, L4 7, L5 24.
- In L5 hadden de beste kandidaten een OOS-PF van 1,32 (XAUUSD M15 breakout_prev_day: te weinig trades per week en 78 dagen
  zonder trade), 1,19 (DE40 M15 hybrid_orb_adx_atr: PF net onder 1,20) en 1,12. **Alle 24 hadden DSR 0,00** bij N ≈ 1500
  trials. Er is niets versoepeld.
- Duur 9,4 minuten (geschat 11,9, maximaal 53). 1644 trials geregistreerd. CPU van de procesboom maximaal 16,5% van de
  machine (≈ 4 kernen), RAM maximaal 3,0 GB, prioriteitsklasse 16384 (BELOW_NORMAL). Er zijn geen verzegelde bars aangeraakt;
  de audit is leeg.
- **Herstarttest** (`real_run_restart_test.json`), run `fr_20261110_124557_9f2851` (XAUUSD/DE40, 48 kandidaten, seed 7):
  supervisor en workers gedood na 43 trials, midden in L1–L5. De API startte bij de volgende aanvraag een nieuwe supervisor.
  Vier onderbroken taken gingen verder vanaf hun laatste stage en de run werd afgerond. 87 trialrijen, 0 dubbel, het aantal
  trials per kandidaat klopt met de L4-payload, 0 dubbele L4-rijen in het logboek. 8 van de 56 kandidaten waren duplicaten van
  run 1 en zijn afgewezen.

## 6. Bekende beperkingen

- **De DSR is in de praktijk een harde blokkade.** De variantie van de Sharpe ratio over de trials (het maximum van deze
  kandidaat en het hele logboek, zoals in `validation_runner`) is groot, omdat de meeste trials sterk verliezen. Daardoor ligt
  SR0 hoog en is de DSR voor alles ≈ 0. Dat is conform de definitie, maar het betekent dat de DSR-check vrijwel elke kandidaat
  tegenhoudt. Een smallere variantiebron (alleen trials die de vroege filters doorstaan) zou een methodologische keuze zijn en
  is niet doorgevoerd.
- De L6–L8-paden (bevriezen, eindtest, Holm/BH, L8) zijn alleen met synthetische data getest. In de echte run haalde geen
  kandidaat L5.
- De lijnregel (familie per markt) is streng. Nieuwe runs na een eindtest blokkeren die familie op die markt voor de huidige
  verzegelde periode.
- De dedup tegen het logboek betekent dat een tweede run met dezelfde seed grotendeels uit duplicaten bestaat. Gebruik een
  andere seed.
- De commissie is per activaklasse aangenomen (FX 3,5 per lot per kant, metalen/indexen 0, alleen spread). Contractspecificaties
  zijn niet door de broker bevestigd; DE40 rekent in EUR alsof het USD is. OHLC zonder tickvolgorde: SL en TP in dezelfde bar
  geeft SL eerst.
- Sessies in de verdeling zijn UTC-uurblokken. Regimes komen uit de dagelijkse ADX(14) ≥ 25 en een mediaansplitsing van de
  gerealiseerde volatiliteit.
- In de eerste run meldde de supervisor `cpu_pct` = 0 (psutil-meetfout, daarna hersteld; de herstart-run meldt waarden). De
  CPU-cijfers hierboven komen van een externe monitor.


## 7. V10: pre-registratie, promotieladder, logboek (2026-11-10)

Zie ook `DSR_METHODOLOGY_AUDIT.md` en `ANTI_OVERFITTING_VALIDATION.md`.

- **Pre-registratie.** `normalize_config` bevriest het volledige onderzoeksprotocol (`protocol.research_protocol`: budget,
  families, markten, timeframes, doelfuncties, kwaliteitsdrempels, kosten, WF-procedure, correcties, eindtestprocedure,
  code-sha) met sha256 in `config.preregistration`. Bij de start (`prepare_run`) wordt het opnieuw berekend: een wijziging
  tussen aanmaken en starten geeft een nieuwe registratie (`superseded_before_start`). Daarna controleert elke kandidaat-
  stage de hash (`verify_preregistration`) en weigert een gewijzigd protocol: dat is een nieuwe hypothese = nieuwe run.
  Registraties staan append-only in de tabel `preregistrations` van het logboek (zelfde hash = zelfde hypothese).
- **Logboek (additief, append-only):** `search_decisions` (successive-halving rungs: wat door/afvalt; WF-keuze per venster),
  `data_usage` (SEARCH = ontwikkeling, VALIDATION_SELECTION = validatie, SELECTION_EXPERIMENT per markt × timeframe,
  SEALED_FINAL bij de eindtest). L3-rijen bewaren een wekelijkse representatieve reeks, L4-rijen `n_eff_within`, alle rijen
  `n_days`: invoer voor de effectieve N in latere rondes.
- **Bewijsklasse validatie:** `REUSED_FOR_SELECTION` als de validatieperiode van dat symbool al eerder voor selectie is
  gebruikt (2018–2023 is in V1–V9 gebruikt), anders `FIRST_USE_SELECTION`. Alleen de verzegelde test is HISTORICAL_OOS.
- **Promotieladder** (`promotion_status`, ook voor oude runs via mapping van de oude statussen): EXPERIMENT →
  DEVELOPMENT_PASS (L4) → WALK_FORWARD_PASS → ROBUSTNESS_PASS (L5) → FROZEN_CANDIDATE (L6) → HISTORICAL_OOS_PASS (L8) →
  FORWARD_ELIGIBLE → PROSPECTIVE_FORWARD; eindstatussen REJECTED en INSUFFICIENT_EVIDENCE. Elke kandidaat krijgt
  `promotion_status` en `highest_promotion` (hoe ver een afgewezen kandidaat kwam); `counts.promotion` en
  `counts.highest_promotion` in de API (velden toegevoegd, bestaande velden ongewijzigd).
- **Verzegeld meervoudig testen:** Holm over alle verzegelde p-waarden ooit blijft de poort. De sealed-DSR gebruikt nu
  N = aantal verzegelde tests (`sealed_dsr_n`); zoek-trials tellen daar niet meer mee (die zijn al in de globale DSR van L6
  verrekend). `dsr_n_trials` blijft als historisch veld bestaan.
- **Optimiser:** Optuna is niet geïnstalleerd → `optuna_status() = NOT_AVAILABLE`; TPE wordt alleen gebruikt als
  `profile_settings.optimizer = "tpe"` én `import optuna` lukt. Standaard: seeded random + successive halving. Er is geen
  TPE-versus-random-vergelijking gemaakt.

## 8. V10.1: uitkomsten, dubbel-positieve regel, DSR-varianten (2026-11-10)

**Uitkomst per kandidaat** (`factory.classify_outcome`, afgeleid bij het lezen; oude records worden niet herschreven,
`stored_status` bewaart de opgeslagen status):

| uitkomst | wanneer | `rejection_kind` |
|---|---|---|
| `PASSED` | WF_PASSED en verder (NOT_SELECTED = geslaagd, niet gekozen) | - |
| `REJECTED` | voldoende bewijs van falen: verlies na kosten, negatieve OOS-verwachting op >= 30 trades, overfit-signalen (PBO, WFE, expanding), drawdown, vooraf vastgelegd criterium gefaald met voldoende data, eindtest gefaald | `statistisch`; defecte strategie (look-ahead, ongeldige spec, codegen) `technisch` |
| `INSUFFICIENT_EVIDENCE` | te weinig OOS-trades, < 4 walk-forward-vensters voor de enkel-venster-regel, PF/RF-tekort binnen het bootstrap-CI, verwachting niet van nul te onderscheiden, DSR < 0,95 bij positieve Sharpe, korte historie, holdout al gebruikt | `onvoldoende bewijs` |
| `TECHNICAL_BLOCKED` | ontbrekende Ask/bars, ongeldige instrumentgegevens, pijplijnfout (ERROR) | `technisch` |
| `REVIEW_REQUIRED` (V10.2) | PBO > `max_pbo` is de **enige** niet-PASS-reden van L5 (alle andere checks, dubbel-positief, baseline en robuustheid slagen). Geen automatische promotie: geen L6-selectie, bevriezing, eindtest, FORWARD_ELIGIBLE of forward-registratie tot een menselijke beoordeling is vastgelegd | - |

Elke kandidaat in `/api/factory/runs/{id}/candidates` en `/api/factory/candidates/{id}` heeft (additief) `outcome`,
`rejection_kind`, `reasons_nl`, `missing_nl` (welke informatie ontbreekt), `next_test_nl` (zinvolle vervolgtest),
`stored_status`, `is_candidate`; de detailweergave ook `dual_positive`. `counts.outcomes` telt de uitkomsten.
Legenda: `GET /api/factory/outcomes`. Nieuwe opgeslagen status `TECHNICAL_BLOCKED` (L1 bij ontbrekende data).

**Enkel-venster-regel**: met minder dan 4 OK-vensters is "leunt op een enkel venster" niet te beoordelen ->
INSUFFICIENT_EVIDENCE (was FAIL).

**Kwaliteitscontroles**: te weinig OOS-trades is nooit meer een FAIL. PF en RF onder de drempel zijn FAIL alleen als
het block-bootstrap-95%-CI geheel onder de drempel ligt of bij structureel verlies op >= 30 trades; anders
INSUFFICIENT_EVIDENCE. Negatieve verwachting op < 30 trades: INSUFFICIENT_EVIDENCE.

**Dubbel-positieve regel (gebruikerseis)**: automatisch gevonden strategieen worden alleen gepromoveerd en als
kandidaat getoond als de ontwikkelbacktest van de te bevriezen configuratie (netto > 0 en PF > 1, na kosten) EN de
walk-forward OOS (netto > 0, verwachting > 0) positief zijn (`factory.dual_positive_gate`). Gecontroleerd in L5
(check `dual_positive`) en opnieuw in L6 voor het bevriezen (`dual_from_stages`; oudere records vanuit L4/L5).
Een positief en een negatief resultaat wordt nooit gepromoveerd.

**DSR**: elk record bevat `variants` (paper-DSR, Null-calibrated DSR = poort, afhankelijkheidsbewuste DSR) en
`reliability`. Poort ongewijzigd (NC-DSR, zoekniveau effectieve N, 0,95), nu onderbouwd met kalibratie:
`DSR_FINAL_METHODOLOGY.md`, `STATISTICAL_CALIBRATION_REPORT.md`. L6: DSR < 0,95 met positieve Sharpe ->
INSUFFICIENT_EVIDENCE, Sharpe <= 0 -> REJECTED.

## 9. V10.2: poortenmatrix en dubbele correcties (2026-11-11)

Onderbouwing, simulaties en beslisregels: `STATISTICAL_SELECTION_AUDIT.md`, bewijs `evidence/v102/stats/`.

**Twee technische fouten hersteld (geen drempel gewijzigd, geen poort verwijderd):**

1. **DSR/PSR stonden twee keer in de promotieladder.** De L5-scorecard gaf bij DSR < 0,95 (of PSR < 0,95) een harde FAIL
   (`REJECT`) en liet de kandidaat in L5 sneuvelen als REJECTED, terwijl de enige multiplicity-poort L6 is (volledige
   zoektocht, definitieve N) en daar een positieve Sharpe met DSR < 0,95 INSUFFICIENT_EVIDENCE hoort te zijn. Nu geeft L5 de
   scorecard `deferred_checks = ["dsr", "psr"]`: de checks worden berekend en getoond (`deferred: true`) maar beslissen
   niet; het besluit valt in L6 (`effective_trials.gate`). Een kandidaat kan dus nog steeds niet worden bevroren zonder
   DSR >= 0,95; een uitgestelde tekortkoming blokkeert ook FORWARD_ELIGIBLE. PSR(0) is bovendien impliciet in de DSR
   (SR0 >= 0, T_eff <= T).
2. **Onvoldoende bewijs werd REJECTED.** Een scorecard met NEEDS_MORE_DATA zonder enige FAIL (te weinig trades,
   onvoldoende jaren) werd in L5 als afwijzingsreden gelogd. Nu `factory.l5_decision` (pure functie, ook door de simulatie
   gebruikt): alleen echte FAILs zijn redenen voor REJECTED; ontbrekend bewijs is INSUFFICIENT_EVIDENCE.

**Poortenvolgorde ongewijzigd:** dual-positief, kwaliteitscontroles (PF/RF met bootstrap-CI, drawdown, expectancy-CI,
concentratie, inactiviteit, kostenstress, parameterstabiliteit, overfit-vlaggen PBO/WFE/expanding, enkel-venster),
scorecard (zonder DSR/PSR), robuustheidsoordeel, daarna L6 NC-DSR (zoekniveau effectieve N, 0,95). Dubbel geteld in de
*redenenlijst* (zelfde statistiek, zelfde drempel op twee plaatsen: kostenstress +50%, parameterstabiliteit, WFE, PBO,
drawdown, PF) maar niet in de *beslissing*: een OF over identieke gebeurtenissen verandert het resultaat niet.

**PBO (besluit eigenaar 2026-11-11):** PBO wordt in L5 berekend over **alle L4-configuraties** (eerste rung, bv. 24 in
profiel "uitgebreid"), elk gebacktest op de validatieperiode (`factory.pbo_over_l4`), niet meer over de top-K (4). Drempel
ongewijzigd (PBO > 0,5 = vlag). Simulatie: nul-FPR blijft 0/300 in 6 nulscenario's; PBO vlagt ruis ~50% en sterke edges
0-1%. Zie `STATISTICAL_SELECTION_AUDIT.md` sectie 6a.

**PBO-beoordeling (besluit eigenaar 2026-11-12, `STATISTICAL_SELECTION_AUDIT.md` sectie 6b):** `pbo_over_l4` controleert de CSCV-voorwaarden (gelijke
tijdas, gelijk kostenmodel, set = eerste L4-rung incl. best/top-K, uitsluiting alleen om technische reden); een geschonden voorwaarde geeft
`INSUFFICIENT_EVIDENCE` zonder PBO-getal. Elke PBO-configuratie staat als `L5_PBO`-record in de store (parameters, hash, validatiemetrics) en telt
**niet** mee in N van de DSR (dezelfde configuraties als de L4-trials). PBO > `max_pbo` als enige reden geeft kandidaat- en L5-status
`REVIEW_REQUIRED` (niet REJECTED); faalt iets anders, dan blijft het REJECTED met de PBO-waarschuwing tussen de redenen. Beoordelen:
`POST /api/factory/candidates/{id}/pbo-review` met `decision` (`accept` of `reject`), `reviewer`, `note`, optioneel `run_id`; 404 onbekende
kandidaat, 409 geen REVIEW_REQUIRED of al beoordeeld, 422 ongeldige invoer. De beoordeling staat append-only in tabel `pbo_reviews` en in het
beslissingslogboek van het ledger. `accept` zet de kandidaat op `WF_PASSED`: L6 met de verplichte globale DSR-poort, eindtest en forward-poorten gelden
onverkort; `reject` is REJECTED. Een beoordeling is definitief.
