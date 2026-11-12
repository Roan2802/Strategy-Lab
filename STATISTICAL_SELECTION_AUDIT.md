# Statistische selectie-audit (V10.2, 2026-11-11)

Labels: **[GEMETEN]** simulatie of code, reproduceerbaar; **[AFGELEID]** volgt uit gemeten getallen; **[AANNAME]** modelkeuze;
**[BESLUIT]** genomen en gedocumenteerd; **[OPEN]** besluit voor de eigenaar. Alleen synthetische rendementen; de verzegelde
periode (2024+) is niet gebruikt, er is niets geinstalleerd (geen Optuna). Eerdere documenten: `DSR_FINAL_METHODOLOGY.md`,
`STATISTICAL_CALIBRATION_REPORT.md`, `FACTORY_FINAL_VALIDATION.md`, `FACTORY_PROTOCOL.md` (sectie 9 is nieuw).

Code en bewijs: `evidence/v101/stats/calibration.py` (uitgebreid: S4, VREC, `--out`), `evidence/v102/stats/pipeline_sim.py`
(simulatie van de hele promotieladder), `pipeline_analyze.py`, `pipeline_results.json`, `pipeline_summary.json`,
`pipeline_tables.md`, `calibration_results.json`, `preregistration.json`. Tests: `strategy_lab/tests/test_v102_stats.py`,
`test_v102_stats_sim.py`.

## 1. Conclusie

1. **DSR-berekening klopt** [GEMETEN]: paper-DSR, NC-DSR en afhankelijkheidsbewuste DSR zijn formeel correct, de volledige
   zoekgeschiedenis (alle families, markten, timeframes, rondes, runs in het logboek) zit in N, gecorreleerde trials en
   cross-markt-selectie worden via de effectieve N gedekt (SR0 van de code >= Monte-Carlo-verwachting van het beste trial in
   4 van 4 structuren).
2. **Twee technische fouten gevonden en hersteld** [BESLUIT], zonder drempel te wijzigen en zonder dat een strategie
   hierdoor kan slagen die eerder faalde: (a) DSR/PSR werden twee keer als harde poort toegepast (L5-scorecard en L6) en een
   DSR-tekort met positieve Sharpe werd in L5 als REJECTED gelogd in plaats van INSUFFICIENT_EVIDENCE; (b) een scorecard met
   alleen ontbrekend bewijs (NEEDS_MORE_DATA zonder FAIL) werd als afwijzing geteld.
3. **Geen enkele andere poort is een aangetoonde dubbele straf** volgens de vooraf vastgelegde regels. Alle andere
   L5-poorten zijn *redundant ten opzichte van de DSR* (zij verwerpen 0% van de sterke edges die de DSR laat slagen), maar
   zij kosten dus ook geen power. **[OPEN]** De enige uitzondering is PBO: zij blokkeert 34% van de sterke edges die de DSR
   laat slagen (zie sectie 6).
4. **Pipeline-niveau** [GEMETEN]: valse-positieven-percentage 0 van 300 zoektochten (Wilson bovengrens 1,3%) in elk van 13
   nulcellen (3.900 zoektochten; 7.210 kandidaten bereikten L5). Power: 0% bij netto Sharpe <= 1,0, 1,3% bij 1,5, 19% bij 2,0, 68% bij
   3,0 (6 jaar validatie, zoek-N_eff ~700). **De pijplijn verwerpt dus alle zwakke echte edges** (netto Sharpe <= 1,5 jaarlijks),
   zoals bedoeld; sterke edges (>= 3) worden pas met redelijke kans gevonden. Dit is inclusief de selectiestap op ontwikkeldata.

## 2. Vooraf vastgelegde beslisregels [BESLUIT]

Vastgelegd in `evidence/v102/stats/preregistration.json` (sha256 begint met `df0158b8`) vooraf aan de volledige campagne.
Eerlijkheid: een pilot van 60 zoektochten (cellen edge 0, edge 2, regimewisseling) ging eraan vooraf; die vond een bug in de
simulator (volatiliteitsvermenigvuldiger ook zonder regime) die voor de campagne is hersteld; pilotresultaten zijn niet gebruikt.

* **R1 technische fout**: uitkomst die haar eigen gedocumenteerde beleid tegenspreekt, aangetoond met een test -> herstel.
* **R2 duplicaat**: zelfde statistiek en drempel op twee plaatsen -> alleen gedocumenteerd (OF over identieke gebeurtenissen
  verandert de beslissing niet).
* **R3 niet-onderscheidende poort**: afwijzingspercentage voor sterke echte edges binnen 10 procentpunt van dat voor nullen.
* **R4 dubbele straf** van poort G t.o.v. H: (a) zelfde risicocategorie, (b) de afwijzingen van G zijn al grotendeels door H
  gedekt, (c) nul-FPR zonder G <= 2% (Wilson-bovengrens) en niet hoger dan met G, (d) zonder G >= 5 pp meer power bij Sharpe >= 1.
* **R5 nooit**: geen drempel versoepeld, geen poort weggehaald omdat strategieen falen, FPR op zoekniveau <= 2%.

Zwakte in de regels, achteraf vastgesteld [AFGELEID]: R4(b) is in het bestand als "P(G verwerpt | H verwerpt) >= 0,8"
geschreven, bedoeld is het omgekeerde (de afwijzingen van G zitten in die van H). Bovendien is (b) triviaal met de DSR als H:
de DSR verwerpt ~100% van de nullen, dus elke poort lijkt "gedekt". Het beslissende criterium is daarom (c)+(d), het
marginale effect. Op basis hiervan is geen poort verwijderd of gedemoteerd.

## 3. DSR-verificatie

| onderdeel | resultaat | label |
|---|---|---|
| Formules (PSR, SR0 = sqrt(V)((1-g)Z^-1(1-1/N)+g Z^-1(1-1/(Ne))), DSR = PSR(SR0)) | werkvoorbeeld BLdP 2014 gereproduceerd (SR0 0,1132, DSR 0,9004); PSR = scipy tot 1e-15; DSR(N=1) = PSR(0); DSR dalend in N; NC-DSR = PSR met V = 1/T_eff (test `test_dsr_closed_forms`) | GEMETEN |
| Paper-V (methode van momenten, herschaald naar toetshorizon) | herstelt Var(ware SR) + 1/T_test: relatieve fout +10,6% (geen spreiding, ondergrens), -1,4% (0,5 ann), -0,8% (1,0 ann), 600 herhalingen (VREC) | GEMETEN |
| Volledige zoekgeschiedenis | `ledger_trials(ledger)` zonder familie-/periodefilter, L6 en L5 gebruiken het hele logboek; hypothese-id = hash(signatuur, markt, timeframe, parameters): dezelfde configuratie in L2-L5 telt een keer, verschillende markten/timeframes apart | GEMETEN (code) |
| Zoekniveau-N bevat ontwikkeltrials | ja, bewust (aanpassende zoektocht, zie `DSR_FINAL_METHODOLOGY.md` sectie 3); bij niet-aanpassende zoektocht is dit een bekende dubbele telling (S2: power 0,49 tegen 0,92) | AFGELEID |
| Gecorreleerde trials / families / cross-markt (S4, 200 zoektochten per structuur, T 1250, via `search_effective_n` met echte `rep_series`) | zie tabel hieronder | GEMETEN |
| Onderschatting die bestaat | spec-varianten van het L5-nested-WF (zonder sessiefilter) krijgen dezelfde hypothese-id als de basis en tellen niet apart: hooguit factor 2 op 4 configuraties van een N_eff van duizenden (< 0,1%); onderzoek buiten het logboek (V1-V9) is niet geteld; bij `ledger=None` (fixtures) telt alleen de eigen run | AFGELEID |

S4 (markten x families x configuraties; factoren gemeenschappelijk/markt/familie): E[beste SR] jaarlijks door Monte Carlo,
SR0 van de code, SR0 met ruwe N, en de FPR van DSR >= 0,95 met de code-N.

| structuur | ruwe N | N_eff (mediaan) | MC E[max] | SR0 code | SR0 ruwe N | FPR code [bovengrens] |
|---|---|---|---|---|---|---|
| 5 markten x 6 families x 8, rho (0,1; 0,2; 0,3) | 240 | 100 | 1,15 | 1,15 | 1,29 | 1/200 [2,8%] |
| 3 x 4 x 8, rho (0,3; 0,2; 0,2) | 96 | 35 | 0,85 | 0,97 | 1,15 | 0/200 [1,9%] |
| 8 markten x 3 x 6, rho (0; 0,3; 0,4) | 144 | 69 | 1,09 | 1,09 | 1,21 | 1/200 [2,8%] |
| onafhankelijk 5 x 6 x 8 | 240 | 240 | 1,27 | 1,28 | 1,28 | 2/200 [3,6%] |

De code-N dekt de multipliciteit van het beste trial (SR0 >= MC, tot 15% conservatief bij sterke correlatie); met
onafhankelijke trials is N_eff = N en de FPR 1,0% (nominaal 5%, conservatief omdat SR0 een verwachting is, geen kwantiel).

## 4. Poortenmatrix: welk risico meet welke poort

Poorten zoals in `factory.l5` / `quality_checks` / `validation.scorecard` / `robustness.verdict` / L6. "Duplicaat" = zelfde
statistiek en drempel op een tweede plaats (R2).

| poort (groep) | gemeten risico | ook gemeten door (duplicaat of overlap) |
|---|---|---|
| NC-DSR, zoekniveau N_eff, 0,95 (L6) | selectie-bias + steekproefruis | impliceert PSR; overlapt met alle ruis-CI's |
| PBO > 0,5 (overfit-vlag + scorecard) | selectie-bias / overfit (rangstabiliteit van de top-K) | dubbel in `quality_checks` en scorecard |
| WFE < 0,5 | overfit | dubbel in `quality_checks` en scorecard |
| expanding walk-forward <= 0 | overfit + regime | - |
| dual-positief (dev netto > 0, PF > 1 en OOS netto > 0) | overfit van de ontwikkelleg + ruis | scorecard `net_after_costs`, robuustheid `oos_net` |
| expectancy-CI, daggemiddelde-CI, PF-CI/RF-CI, random-entry | steekproefruis | PSR/DSR |
| PF/RF-ondergrens, drawdown, frequentie, aantal trades | prestatie-eis, steekproefgrootte | scorecard (PF, drawdown, sample_size) |
| +50% spread, uitvoeringsscenario's, cost-MC | kostenfragiliteit | `spread_stress` en scorecard `cost_stress` zijn hetzelfde getal |
| buurparameters (>= 70% winstgevend), neighbourhood-klasse | parameterfragiliteit | drie plaatsen, zelfde `frac_positive` |
| concentratie, drop-best, enkel-venster | steekproefruis (weinig dagen/vensters) | drie plaatsen |
| regimestabiliteit, jaarlijkse consistentie, OOS-vensters, inactiviteit | regime-afhankelijkheid | - |

Aantal plaatsen met identieke statistiek (R2): kostenstress, parameterstabiliteit, WFE, PBO, drawdown, PF, trades; dit zijn
dubbele *redenen* maar geen dubbele *straf*, de beslissing is een OF over gelijke gebeurtenissen.

## 5. Empirische overlap, gemeten

Simulatie: per zoektocht G = 120 lijnen (kandidaten), C = 24 configuraties per geoptimaliseerde lijn (rho 0,85), L2/L3-scherm
Sharpe >= 0,8, L4-scherm >= 1,0, top-4 naar L5 (K12-cellen: 12), 3,0 jaar training, 182 dagen testvensters, rolling en expanding
nested walk-forward met de echte `nested_wf`-functie (snelle I/O, gelijkheid bewezen in `test_v102_stats_sim`), alle poorten met
de echte factory-functies, geschiedenisfactor H = 5 (N-teller x5 voor eerdere rondes). Een kandidaat is een L5-kandidaat; de
bekwame lijn (echte edge) wordt altijd tot L5 doorgelaten zodat de poorten geisoleerd worden gemeten (power "gegeven L4");
de eerste kolom "bereikt L4" meet het ontwikkelscherm. Flag-niveau uitkomst = factory-uitkomst voor 20.899 van 20.899 kandidaten.

**Conditionele afwijzing** (nulkandidaten met positieve OOS-netto, n = 3.417; P(kolom verwerpt | rij verwerpt), uit `pipeline_tables.md`):

* Voor elke rij is P(NC-DSR verwerpt | rij) = 1,00; de DSR dekt de afwijzingen van elke andere poort.
* Omgekeerd, P(PBO | DSR) = 0,46, P(WFE | DSR) = 0,39, P(expanding | DSR) = 0,10; P(expectancy-CI | DSR) = 0,93; P(daggemiddelde-CI
  | DSR) = 0,94; P(PF-ondergrens | DSR) = 0,92; P(buurparameters | DSR) = 0,96. De ruis-CI's, PF en parameterstabiliteit
  overlappen onderling >= 0,92-1,00: dezelfde onzekerheid, meerdere keren gemeten.
* PBO overlapt matig met de ruis-gates (P(expectancy-CI | PBO) = 0,94) maar is niet gedekt door WFE (0,42) of expanding (0,11).

**Wat kost elke poort?** De "negeer-een-poort"-analyse (`pipeline_tables.md`, sectie Leave-one-out) toont dat het negeren van
*elke* afzonderlijke poort de FPR op 0 laat (nul-FPR op zoekniveau 0 van 3.600 zoektochten, bovengrens <= 0,2%, voor elke rij) en dat
slechts drie poorten het power veranderen:

| genegeerde poort | power bij 1,0 | power bij 2,0 | power bij 3,0 |
|---|---|---|---|
| geen (volledige pijplijn) | 0,000 [0, 0,025] | 0,193 [0,138, 0,264] | 0,680 [0,602, 0,749] |
| NC-DSR | 0,193 [0,138, 0,264] | 0,553 [0,473, 0,631] | 0,700 [0,622, 0,768] |
| PBO | 0,000 | 0,300 [0,232, 0,378] | 0,973 [0,933, 0,990] |
| buurparameters | 0,007 | 0,247 [0,185, 0,321] | 0,680 |
| elke andere poort afzonderlijk | 0,000 | 0,193 | 0,680 |

Van de sterke echte edges (netto Sharpe 2 en 3, K 4, n = 1.350) laat de DSR er 518 slagen. Van die 518 verwerpen
daarna: PBO 33,8%, parameterstabiliteit 5,4%, uitvoeringsscenario 0,2%, **elke andere poort 0%**. De overige L5-poorten
(ruis-CI's, PF/RF, kosten, concentratie, regime, jaren, enkel-venster) zijn dus impliciet in de DSR en voegen geen enkele
afwijzing toe: geen dubbele straf in de zin van extra afwijzingen, wel redundantie. Zij beschermen de FPR evenmin
afzonderlijk (in deze niet-aanpassende nullen houdt elke subset de FPR op 0); hun waarde is robuustheid tegen modelfouten
(bijv. een DSR die door te weinig geregistreerde trials te mild is), niet marginale bescherming. [AFGELEID]

**Wat de bindende poort is.** Bij het bekwame trial blokkeert de DSR 100% (Sharpe 0,5), 99% (1,0, 19% als enige poort),
64% (2,0, 36% enige poort) en 3% (3,0). SR0 jaarlijks: 1,12 (H = 1, N_eff 136), 1,33 (H = 5, N_eff 685), 1,49 (H = 20,
N_eff 2.720). Het ledger van de echte run (N_eff ~5.600, SR0 ~1,5) ligt daar bovenop. Het power daalt dus met de
geschiedenis: bij Sharpe 2,0 0,193 (H = 5) tegen 0,133 (H = 20), een eerlijke prijs van meervoudig testen (de eerdere
zoektochten zijn echte trials) en bij niet-aanpassend zoeken een dubbele telling (S2).

## 6. Beslissingen

| nr | onderwerp | besluit | label |
|---|---|---|---|
| 1 | DSR/PSR in L5-scorecard en in L6 | **hersteld (R1).** `deferred_checks = ["dsr", "psr"]` in de L5-scorecard: berekend en getoond, niet beslissend; L6 blijft de enige poort. Test `test_scorecard_defers_dsr_psr_and_never_passes_without_them`: zonder uitstel REJECT, met uitstel OOS_PASSED met 0 FAILs, L6-poort verwerpt nog steeds, een uitgestelde tekortkoming blokkeert FORWARD_ELIGIBLE. Geen strategie slaagt hierdoor, een DSR-tekort wordt INSUFFICIENT_EVIDENCE (positieve Sharpe) of REJECTED (Sharpe <= 0) zoals `DSR_FINAL_METHODOLOGY.md` sectie 4 al zei | BESLUIT |
| 2 | NEEDS_MORE_DATA zonder FAIL als afwijzing | **hersteld (R1).** `factory.l5_decision` (pure functie): alleen echte FAILs zijn redenen; ontbrekend bewijs is INSUFFICIENT_EVIDENCE. Test `test_l5_decision_...` | BESLUIT |
| 3 | PSR | impliciet in de DSR (SR0 >= 0, T_eff <= T); samen met de DSR uitgesteld, geen effect op wie slaagt | AFGELEID |
| 4 | dubbele redenen (kosten, parameters, WFE, PBO, drawdown, PF) | R2: gedocumenteerd, niet gewijzigd | BESLUIT |
| 5 | ruis-CI's, PF/RF, concentratie, regime, jaren, enkel-venster | marginaal 0 (zie sectie 5): behouden, geen wijziging | BESLUIT |
| 6 | N-teller met ontwikkeltrials en geschiedenis | behouden (aanpassende zoektocht niet uit te sluiten, S3); kost power, geen FPR-fout | BESLUIT |
| 7 | **PBO > 0,5 als harde vlag** | **niet gewijzigd** (R3 niet voldaan, R4 niet vastgesteld). Feiten: bij K = 4 vlagt PBO 51% van de nullen, 47% bij Sharpe 0,5, 35-37% bij Sharpe 1-2, 30% bij Sharpe 3; mediane PBO 0,51 tegen 0,36. Bij K = 12 onderscheidt PBO beter: 48% (nul) tegen 17% (Sharpe 2). PBO is de enige poort die na de DSR nog sterke edges blokkeert (34%, voor Sharpe 3,0 de enige blokkade bij 29% van de kandidaten). Verklaring: de top-K configuraties van een echte, brede plateau-edge zijn gelijkwaardig, de rangorde IS naar OOS is dan toeval (PBO ~ 0,5), terwijl `param_stability` juist een plateau eist | OPEN |

**Aanbeveling bij 7 (besluit eigenaar, niet doorgevoerd omdat dit een versoepeling is):** PBO berekenen over de volledige L4-trialset
(minstens 24 configuraties in plaats van de 4 top-K) of alleen ter informatie tonen. De simulatie laat zien dat de
nul-FPR zonder PBO 0,000 [0, 0,001] blijft en het power bij Sharpe 3,0 van 0,68 naar 0,97 gaat, maar de simulatie bevat geen
aanpassende hergebruikscenario's op pipeline-niveau (alleen op return-niveau, S3), waar PBO extra bescherming zou kunnen bieden.

## 6a. Uitvoering besluit 7: PBO over alle L4-configuraties (2026-11-11) [BESLUIT eigenaar]

Gekozen optie: PBO over de volledige L4-trialset. Code: `factory.pbo_over_l4` (eerste rung = alle gesamplede configuraties,
duplicaten verwijderd, elk gebacktest op de validatieperiode), test `test_pbo_over_l4_uses_every_first_rung_configuration`.
Drempel `max_pbo = 0,5` ongewijzigd. Simulatie: `pipeline_sim.py` optie `pbo_all`, resultaten `pipeline_results_pboall.json`,
vergelijking `pboall_compare.py` -> `pboall_comparison.json` [GEMETEN].

| cel | top-K (4): slaagt [Wilson 95%] / PBO-vlag | alle 24: slaagt [Wilson 95%] / PBO-vlag |
|---|---|---|
| nul (edge 0) | 0 [0; 0,013] / 55% | 0 [0; 0,013] / 49% |
| nul, gecorreleerde markten rho 0,6 | 0 [0; 0,013] / 49% | 0 [0; 0,013] / 48% |
| nul, t3-staarten | 0 [0; 0,013] / 54% | 0 [0; 0,013] / 51% |
| nul, regimewisseling | 0 [0; 0,013] / 52% | 0 [0; 0,013] / 54% |
| nul, geschiedenis x20 | 0 [0; 0,013] / 48% | 0 [0; 0,013] / 52% |
| nul, lage frequentie | 0 [0; 0,013] / 52% | 0 [0; 0,013] / 46% |
| Sharpe 0,5 | 0 / 47% | 0 / 35% |
| Sharpe 1,0 | 0 / 35% | 0 / 8% |
| Sharpe 1,5 | 0,013 / 41% | 0,013 / 3% |
| Sharpe 2,0 | 0,193 [0,138; 0,264] / 37% | **0,380** [0,306; 0,460] / 1% |
| Sharpe 3,0 | 0,680 [0,602; 0,749] / 30% | **0,947** [0,898; 0,973] / 0% |

Conclusie: over de volledige trialset doet PBO wat CSCV belooft (ruis ~0,5, echte edges laag), de nul-FPR blijft 0 en zwakke
edges (Sharpe <= 1,5) slagen nog steeds niet. Beperking blijft: geen aanpassend hergebruik op pipeline-niveau gesimuleerd.
Bij profiel "snel" (6 trials) is de PBO-set 6 configuraties; minder dan 2 of < 8 dagen = INSUFFICIENT_EVIDENCE.

## 6b. CSCV-voorwaarden, REVIEW_REQUIRED en simulatie (2026-11-12) [BESLUIT eigenaar]

Besluit eigenaar: (1) de CSCV-aannames expliciet auditen en in de code afdwingen, (2) elke PBO-configuratie registreren, (3) PBO > `max_pbo`
als enige reden is geen REJECTED maar `REVIEW_REQUIRED` (menselijke beoordeling, geen automatische promotie), (4) simulatie op pipeline-niveau
van top-4 tegen de volledige set. Code: `factory.pbo_over_l4` (voorwaarden), `factory.pbo_check`, `factory.l5_decision` (veld `review_required`),
`factory.review_pbo` / `pbo_review_record` / `l5_cleared`, `validation.scorecard` (drempel `pbo_review`), endpoint
`POST /api/factory/candidates/{id}/pbo-review`, tests `strategy_lab/tests/test_v102_pbo_review.py`.

### 6b.1 Voorwaardenaudit CSCV (Bailey et al. 2015) tegen de implementatie

| voorwaarde | wat CSCV aanneemt | hoe de code het afdwingt | status |
|---|---|---|---|
| gelijke tijdas | alle configuraties over dezelfde T rijen | alle configuraties draaien op dezelfde validatieperiode; series worden op de **unie** van handelsdagen gezet, dagen zonder trade = 0 (een configuratie die niet handelt verdient 0, het is geen ontbrekende waarde; weglaten zou haar Sharpe vertekenen en de kolommen ongelijk uitlijnen). Controle: elke trade moet in `[val.start, val.end]` vallen (einddag inclusief), anders `time_index = FAIL` | PASS / FAIL, afgedwongen |
| gelijk kostenmodel | zelfde kosten, kapitaal en spreadbron als de gekozen configuratie | PBO-backtests lopen via dezelfde `bt_spec` (commissie per lot, kapitaal, bid/ask-data) als de gekozen configuratie; vergelijking van een vingerafdruk (`cost_fp` tegen `chosen_cost_fp`) | PASS / FAIL, afgedwongen; in L5 identiek **per constructie** [AFGELEID] |
| vergelijkbare maatstaf | selectie en evaluatie op dezelfde prestatiemaat | **niet gelijk, gedocumenteerd.** CSCV kiest de IS-winnaar op de Sharpe van dagrendementen (validatieperiode); L4 kiest op Pareto-rangorde (frequentie, recovery factor, profit factor, stabiliteit; ontwikkelperiode); L5 kiest met een geneste walk-forward op Sharpe. PBO meet dus de kans dat de *Sharpe-selectie* overfit, niet exact de Pareto-selectie | NOTE |
| juiste weergave van de selectie | de set is precies wat de zoektocht overwoog | de set is de volledige eerste L4-rung (alle gesamplede configuraties, geen top-K); `best` en `top_k` van L4 moeten erin zitten (anders `selection_set = FAIL`). **Niet in de set:** de variant zonder sessiefilter van de geneste walk-forward en de neighbour-trials van de stabiliteitstoets (beide tellen als trials, zitten niet in de PBO-matrix) | PASS / FAIL, afgedwongen; variantdimensie NOTE |
| uitsluitingen | uitsluitingen mogen de selectie niet kleuren | alleen technisch: backtestfout (`TECHNICAL_ERROR`) of geen enkele trade op de validatieperiode (`ZERO_TRADES_VALIDATION`), elk met reden teruggegeven; nooit op prestatie. Annuleren (`JobCancelled`) wordt nooit als uitsluiting weggeslikt. Meer dan 50% uitgesloten = `exclusion_policy = FAIL` (de set vertegenwoordigt de zoektocht niet meer) [AANNAME: 50%] | PASS / FAIL, afgedwongen |
| voldoende data | >= 2 configuraties en >= 8 rijen voor 8 blokken | te weinig: PBO niet van toepassing (`applicable = False`, geen getal, geen blokkade zoals voorheen) | NOTE |
| onafhankelijke trials | CSCV neemt een vaste, vooraf bepaalde set aan | de set is door selectie op de ontwikkelperiode ontstaan en de configuraties zijn sterk gecorreleerd (rho_c 0,85 in de simulatie). PBO is daardoor een *indicatie van rangorde-instabiliteit*, geen kans in strikte zin | NOTE [AANNAME] |

Retour in het PBO-dict: `config_set` ("L4_all"), `n_configs`, `n_eligible`, `excluded` (lijst met hash, params, reden), `configs` (hash, params,
validatiemetrics), `preconditions` (check: PASS / FAIL / NOTE), `precondition_notes`, `precondition_failed`, `applicable`. **Een FAIL geeft
`status = INSUFFICIENT_EVIDENCE` en `pbo = None`**; de L5-check `pbo` wordt dan `INSUFFICIENT_EVIDENCE` (de kandidaat eindigt als
INSUFFICIENT_EVIDENCE, nooit met een PBO-getal).

### 6b.2 Registratie en N voor de DSR

Elke PBO-configuratie wordt opgeslagen als `trials`-record met stage `L5_PBO` (parameters, kanonieke sha256 van de parameters, validatiemetrics:
trades, netto, profit factor, Sharpe), idempotent per (run, kandidaat, hash), en staat in de L5-payload (`robustness.pbo.configs`).
**Het zijn dezelfde configuraties als de L4-trials** die de zoektocht al telde: `FactoryStore.count_trials` en `_store_rows_for` sluiten stage
`L5_PBO` uit en er komt geen ledger-rij bij, dus N van de DSR groeit niet (test `test_pbo_configs_are_registered_but_never_counted_as_new_trials`).
Bewust niet gedaan: een nieuwe trial per PBO-backtest; dat zou N kunstmatig opblazen zonder nieuwe hypothese.

### 6b.3 REVIEW_REQUIRED

* `pbo` is nu een eigen L5-check: PASS, `REVIEW_REQUIRED` (PBO > max_pbo) of `INSUFFICIENT_EVIDENCE` (voorwaarde geschonden). PBO zit niet meer in
  `overfit_flags` (alleen WFE en expanding) en in de scorecard geldt `pbo_review`: geen FAIL, wel nooit PASS (dus geen FORWARD_ELIGIBLE).
* `l5_decision`: alleen als **PBO de enige** niet-PASS-reden is (alle andere L5-checks, dubbel-positief, baseline, scorecard en robuustheid slagen)
  wordt de uitkomst `REVIEW_REQUIRED`. Faalt iets anders, dan is het REJECTED (of INSUFFICIENT_EVIDENCE) zoals voorheen, met de PBO-waarschuwing
  tussen de redenen.
* Een `REVIEW_REQUIRED`-kandidaat krijgt L5-stagestatus en kandidaatstatus `REVIEW_REQUIRED`, valt buiten `WF_PASSED` en dus buiten
  `select_and_freeze` (L6), de verzegelde eindtest (L7), FORWARD_ELIGIBLE en forward-registratie (die eisen `FORWARD_ELIGIBLE`). Een stop van de
  run overschrijft de status niet (`TERMINAL_STATUSES`).
* Beoordeling: `factory.pbo_review_record` (pure functie: valideert, bepaalt record, nieuwe status en reden), `factory.review_pbo` (zoekt, schrijft,
  logt in het beslissingslogboek van het ledger) en tabel `pbo_reviews` (append-only: triggers weigeren UPDATE en DELETE; beoordelaar,
  toelichting, tijdstempel, PBO, drempel, sha256 van het L5-bewijs). Statuswissel en logregel gebeuren in een transactie; een beoordeling is
  definitief (tweede poging = 409). **Accepteren** zet de kandidaat op `WF_PASSED`: L6 (selectie met de verplichte globale DSR-poort), eindtest
  en forward-poorten gelden onverkort (test: een geaccepteerde zwakke kandidaat wordt in L6 alsnog door de DSR afgewezen). **Afwijzen** = REJECTED.
* Beperking [OPEN]: `select_and_freeze` draait een keer aan het einde van een run. Een beoordeling na afloop zet de kandidaat op `WF_PASSED`, maar
  bevriezing gebeurt pas bij een volgende aanroep van L6 (nieuwe run of handmatige selectie); er is geen automatische herstart. Diversiteitslimieten
  in L6 zien eerder bevroren kandidaten dan niet.
* UI: `Beoordeling nodig — PBO-waarschuwing` in `factory.js` (alleen label). Uitkomst `REVIEW_REQUIRED` staat in `factory.OUTCOMES` en
  `GET /api/factory/outcomes`. Het schaduw-forwardportfolio (`shadow_forward.eligibility`) behandelt REVIEW_REQUIRED niet apart (research-sandbox,
  geen bewijsklasse); dat bestand viel buiten deze opdracht [OPEN].

### 6b.4 Voorregistratie van de simulatie (vastgelegd voordat de adaptieve cellen draaiden)

Doel: top-4-PBO (K = 4) tegen de volledige L4-set (C = 24) onder de nieuwe regel. Scenario's en het adaptieve proces staan in `pipeline_sim.py`
(`review_scenarios`, `ADAPTIVE_ROUNDS`); beide methoden gebruiken per scenario **dezelfde zaden** (gepaard). Eerlijkheid: deze tekst is geschreven nadat
de eerste niet-adaptieve cellen (nul) al draaiden; de adaptieve cellen draaiden er nog niet, de scenariolijst stond al in de code.

* Scenario's: nul (300), nul met regimewisseling (300), nul met gecorreleerde markten rho 0,6 (300); zwakke edge Sharpe 0,5 / 1,0 / 1,5 (150), sterke edge
  2 / 3 (150); **parameterplateau** (`plateau`: alle 24 configuraties van de bekwame lijn hebben dezelfde ware edge, geen verval; Sharpe 0,5 / 1,0 / 2,0; 150);
  regimewisseling met edge (0,5 / 1,0 / 2,0; 150).
* **Adaptief proces (pipeline-niveau)**: een onderzoeker die de validatieperiode hergebruikt. Hij draait een zoekronde (volledige `run_search`, 120
  lijnen, L1-L6), kijkt naar de validatieresultaten (L5-uitkomst) en **start na elke ronde zonder promotie een nieuwe ronde** (nieuwe lijnen, zelfde
  validatieperiode), tot maximaal R = 5. Hij bewaart alles wat in enige ronde slaagt (ruim: "beste van R"). Nul-edge (alle lijnen ruis), 300
  herhalingen per methode, zelfde zaden. Twee varianten voor N in de DSR-poort: (a) **ledger telt alle rondes** (N = som van de rondes: zo werkt het lab,
  elke ronde staat in het ledger); (b) **alleen eigen ronde** (hergebruik onzichtbaar voor de DSR: worstcase als een onderzoeker buiten het ledger
  regenereert). Gerapporteerd na 1, 3 en 5 rondes. [AANNAME] rondes zijn onafhankelijke zoektochten; het proces modelleert selectie-op-validatie over
  rondes, geen gerichte verfijning rond eerdere validatiewinnaars. Omdat PBO alleen binnen de L4-set van een kandidaat kijkt, is vooraf te verwachten
  dat PBO tegen hergebruik tussen rondes weinig extra beschermt (de beschermer is de N-teller van de DSR); de simulatie toetst dat.
* Definities: **auto-promotie** = slaagt L5 zonder REVIEW_REQUIRED en slaagt de L6-DSR-poort (identiek aan de vorige regel "PBO > 0,5 = harde FAIL";
  gecontroleerd met `pbo_review_repro.py`: 36 zoektochten in 3 cellen bit-identiek aan de opgeslagen resultaten). **REVIEW_REQUIRED-aandeel** = de
  eenheid belandt bij L5 in REVIEW_REQUIRED. **Bovengrens** = promotie als elke REVIEW_REQUIRED werd geaccepteerd (L5 schoon en L6 slaagt). Eenheid =
  zoektocht (nulcellen) of de bekwame kandidaat (edge-cellen); Wilson 95%.
* Criterium (vooraf): de auto-promotie-FPR mag niet hoger zijn dan onder de vorige regel (per constructie gelijk); als de bovengrens de FPR
  verhoogt, wordt dat gemeld.

### 6b.5 Resultaten van de simulatie [GEMETEN]

Campagne `pipeline_sim.py --campaign review --procs 16`: 300 zoektochten per nulcel, 150 per edge-cel, 300 per adaptieve cel (5 rondes = 1.500 zoektochten per
methode); gepaarde zaden; 0 gefaalde replicaties; in alle gesimuleerde kandidaten slagen alle CSCV-voorwaarden. Reproductie en tabellen:
`pbo_review_results.json`, `pbo_review_analyze.py` -> `pbo_review_tables.md` / `.json`, `pbo_review_repro.py`. Eenheid per cel: de zoektocht (nul) of de bekwame
kandidaat (edge). Wilson 95%. **Auto-promotie** = slaagt L5 zonder REVIEW_REQUIRED en de L6-DSR-poort; **bovengrens** = promotie als elke REVIEW_REQUIRED was geaccepteerd.

| scenario | reps | methode | PBO-kolommen | auto-promotie [Wilson 95%] | REVIEW_REQUIRED-aandeel [95%] | bovengrens: alles geaccepteerd [95%] | L5-kandidaten / L5 schoon |
|---|---|---|---|---|---|---|---|
| null | 300 | top-4 | 4 | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 689 / 0 |
| null | 300 | alle 24 | 24 | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 689 / 0 |
| null_regime | 300 | top-4 | 4 | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 653 / 0 |
| null_regime | 300 | alle 24 | 24 | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 653 / 0 |
| null_corr0.6 | 300 | top-4 | 4 | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 465 / 0 |
| null_corr0.6 | 300 | alle 24 | 24 | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 465 / 0 |
| edge0.5 | 150 | top-4 | 4 | 0.000 [0.000; 0.025] | 0.000 [0.000; 0.025] | 0.000 [0.000; 0.025] | 150 / 0 |
| edge0.5 | 150 | alle 24 | 24 | 0.000 [0.000; 0.025] | 0.000 [0.000; 0.025] | 0.000 [0.000; 0.025] | 150 / 0 |
| edge1.0 | 150 | top-4 | 4 | 0.000 [0.000; 0.025] | 0.107 [0.067; 0.166] | 0.000 [0.000; 0.025] | 150 / 47 |
| edge1.0 | 150 | alle 24 | 24 | 0.000 [0.000; 0.025] | 0.040 [0.018; 0.085] | 0.000 [0.000; 0.025] | 150 / 47 |
| edge1.5 | 150 | top-4 | 4 | 0.020 [0.007; 0.057] | 0.327 [0.257; 0.405] | 0.033 [0.014; 0.076] | 150 / 122 |
| edge1.5 | 150 | alle 24 | 24 | 0.033 [0.014; 0.076] | 0.033 [0.014; 0.076] | 0.033 [0.014; 0.076] | 150 / 122 |
| edge2.0 | 150 | top-4 | 4 | 0.273 [0.208; 0.350] | 0.307 [0.238; 0.385] | 0.413 [0.338; 0.493] | 150 / 145 |
| edge2.0 | 150 | alle 24 | 24 | 0.413 [0.338; 0.493] | 0.000 [0.000; 0.025] | 0.413 [0.338; 0.493] | 150 / 145 |
| edge3.0 | 150 | top-4 | 4 | 0.560 [0.480; 0.637] | 0.427 [0.350; 0.507] | 0.980 [0.943; 0.993] | 150 / 150 |
| edge3.0 | 150 | alle 24 | 24 | 0.980 [0.943; 0.993] | 0.000 [0.000; 0.025] | 0.980 [0.943; 0.993] | 150 / 150 |
| plateau0.5 | 150 | top-4 | 4 | 0.000 [0.000; 0.025] | 0.000 [0.000; 0.025] | 0.000 [0.000; 0.025] | 150 / 2 |
| plateau0.5 | 150 | alle 24 | 24 | 0.000 [0.000; 0.025] | 0.007 [0.001; 0.037] | 0.000 [0.000; 0.025] | 150 / 2 |
| regime0.5 | 150 | top-4 | 4 | 0.000 [0.000; 0.025] | 0.000 [0.000; 0.025] | 0.000 [0.000; 0.025] | 150 / 0 |
| regime0.5 | 150 | alle 24 | 24 | 0.000 [0.000; 0.025] | 0.000 [0.000; 0.025] | 0.000 [0.000; 0.025] | 150 / 0 |
| plateau1.0 | 150 | top-4 | 4 | 0.000 [0.000; 0.025] | 0.153 [0.104; 0.220] | 0.000 [0.000; 0.025] | 150 / 51 |
| plateau1.0 | 150 | alle 24 | 24 | 0.000 [0.000; 0.025] | 0.193 [0.138; 0.264] | 0.000 [0.000; 0.025] | 150 / 51 |
| regime1.0 | 150 | top-4 | 4 | 0.000 [0.000; 0.025] | 0.013 [0.004; 0.047] | 0.000 [0.000; 0.025] | 150 / 5 |
| regime1.0 | 150 | alle 24 | 24 | 0.000 [0.000; 0.025] | 0.013 [0.004; 0.047] | 0.000 [0.000; 0.025] | 150 / 5 |
| plateau2.0 | 150 | top-4 | 4 | 0.227 [0.167; 0.300] | 0.520 [0.441; 0.598] | 0.420 [0.344; 0.500] | 150 / 142 |
| plateau2.0 | 150 | alle 24 | 24 | 0.240 [0.179; 0.314] | 0.453 [0.376; 0.533] | 0.420 [0.344; 0.500] | 150 / 142 |
| regime2.0 | 150 | top-4 | 4 | 0.000 [0.000; 0.025] | 0.080 [0.046; 0.135] | 0.000 [0.000; 0.025] | 150 / 37 |
| regime2.0 | 150 | alle 24 | 24 | 0.000 [0.000; 0.025] | 0.080 [0.046; 0.135] | 0.000 [0.000; 0.025] | 150 / 37 |

Adaptief (nul-edge, 300 herhalingen per methode, rondes = zoekrondes op dezelfde validatieperiode):

| methode | rondes | N-teller | auto-promotie [95%] | REVIEW_REQUIRED [95%] | bovengrens [95%] |
|---|---|---|---|---|---|
| top-4 | kandidaten 3328 | L5 schoon 0 | waarvan REVIEW_REQUIRED 0 | | |
| top-4 | 1 | alleen eigen ronde | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] |
| top-4 | 1 | cumulatief (ledger telt alle rondes) | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] |
| top-4 | 3 | alleen eigen ronde | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] |
| top-4 | 3 | cumulatief (ledger telt alle rondes) | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] |
| top-4 | 5 | alleen eigen ronde | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] |
| top-4 | 5 | cumulatief (ledger telt alle rondes) | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] |
| alle 24 | kandidaten 3328 | L5 schoon 0 | waarvan REVIEW_REQUIRED 0 | | |
| alle 24 | 1 | alleen eigen ronde | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] |
| alle 24 | 1 | cumulatief (ledger telt alle rondes) | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] |
| alle 24 | 3 | alleen eigen ronde | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] |
| alle 24 | 3 | cumulatief (ledger telt alle rondes) | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] |
| alle 24 | 5 | alleen eigen ronde | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] |
| alle 24 | 5 | cumulatief (ledger telt alle rondes) | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] | 0.000 [0.000; 0.013] |

Lezing:

1. **Nul-FPR niet hoger.** Alle nulcellen (gewoon, regimewisseling, gecorreleerde markten rho 0,6): auto-promotie 0/300 [0; 1,3%] bij beide methoden, en ook de
   bovengrens (alles geaccepteerd) 0/300. Reden: van 1.807 nul-kandidaten haalt er geen enkele alle L5-poorten behalve PBO, dus REVIEW_REQUIRED treedt bij ruis niet
   op (aandeel 0%). Per kandidaat is de kans op REVIEW_REQUIRED bij ruis < 0,2% (0/1.807, Wilson), samen met het adaptieve scenario < 0,08% (0/5.135). Dat is een
   bovengrens uit een simulatie met surrogaten, geen bewijs dat het in echte data nul is.
2. **Auto-promotie <= de vorige regel (gelijk, per constructie).** Automatisch gepromoveerd wordt precies wie voorheen met "PBO > 0,5 = harde FAIL" slaagde; de
   REVIEW_REQUIRED-kandidaten zijn precies de eerder door PBO afgewezen kandidaten die verder alles halen. `pbo_review_repro.py`: 36 zoektochten in 3
   oude cellen geven bit-identieke `pass`, PBO en DSR (nieuwe code, oude zaden).
3. **Top-4 tegen de volledige set.** Bij brede edges met verval (Sharpe 2 en 3) maakt de volledige set PBO laag: REVIEW_REQUIRED 0% tegen 31% en 43% bij top-4,
   auto-promotie 41% tegen 27% (Sharpe 2) en 98% tegen 56% (Sharpe 3); de bovengrens is gelijk (41% en 98%), de volledige set haalt die dus zonder menselijke
   stap. Zwakke edges (Sharpe <= 1,5) slagen nog steeds vrijwel niet (power 0-3%), REVIEW_REQUIRED-aandeel top-4 11% en 33% tegen 4% en 3% bij alle 24.
4. **Parameterplateau (alle 24 configuraties dezelfde ware edge): de beoordeling kost echt iets.** CSCV kan geen winnaar onder gelijken kiezen, PBO is daar ~0,5
   per constructie, ook over de volledige set: REVIEW_REQUIRED 45% (Sharpe 2) en 19% (Sharpe 1); auto-promotie 24% tegen een bovengrens van 42%. De helft van de
   L5-schone plateau-edges gaat dus naar een mens. Dat is het ontwerp (de poort `param_stability` eist juist een plateau) en tegelijk de kostenpost van het besluit.
5. **Regimewisseling met edge:** bijna niets slaagt L5 (Sharpe 1 en 2: auto 0%, REVIEW_REQUIRED 1% en 8%, bovengrens 0%); niet door PBO. Zelfde uitkomst voor top-4 en alle 24.
6. **Adaptief proces (hergebruik van de validatieperiode, 5 rondes):** 0/300 [0; 1,3%] voor beide methoden en beide N-varianten (alleen eigen ronde, en
   cumulatief over rondes) na 1, 3 en 5 rondes, bovengrens eveneens 0/300. **Maar het scenario onderscheidt de methoden niet:** van 3.328 ruiskandidaten haalt er
   geen enkele alle L5-poorten (ook niet met REVIEW_REQUIRED); de L5-kwaliteitspoorten blokkeren alles voordat PBO of DSR iets doen, ook zonder N-accumulatie.
   Het toont geen PBO-beschermingswaarde. Wel valt uit het ontwerp af te leiden dat PBO alleen de L4-set binnen een kandidaat ziet en hergebruik tussen rondes niet
   kan zien; die bescherming zit in de N-teller van de DSR en de L5-poorten.
7. **Geen onbeproefde versoepeling:** de auto-promotie-FPR is gelijk aan de vorige regel (0/300 in alle nulcellen) en de worstcase "alles accepteren" verhoogt de FPR in
   deze simulatie niet (0/300 nul, 0/300 adaptief). Wat wel verandert: (a) het volledige-set-PBO uit besluit 6a (power Sharpe 2 van 27% naar 41%, Sharpe 3 van 56% naar 98%,
   al door de eigenaar besloten), (b) een extra mogelijke promotiepad via een menselijke accept (alleen kandidaten die al alles halen behalve PBO; L6 DSR blijft verplicht).

### 6b.6 Effect op echte kandidaten [GEMETEN]

`strategy_lab/data_cache/factory.sqlite3` (alleen gelezen): 21 runs, 234 kandidaten bereikten L5, alle 234 `REJECTED`; geen enkel L5 PASS. PBO-vlag (oude top-K-PBO,
zonder `config_set`) in 147 van de 234, maar **in geen enkele kandidaat is PBO de enige reden** (alle hebben ook scorecard/enkel venster/baseline/kosten/WFE/expanding-redenen):
0 van 234 zou onder de nieuwe regel REVIEW_REQUIRED worden, alle blijven REJECTED. Ook de 24 bewaarde V9-topkandidaten (`evidence/v9/factory/real_run_top_candidates.json`):
0 met alleen een overfit-vlag. Een herberekening van de PBO over de volledige L4-set voor deze 234 is offline niet gedaan (vereist heruitvoeren van 24 backtests per kandidaat
op de zoekdata); omdat PBO bij alle 234 niet de enige reden is, verandert die herberekening de uitkomst niet. Er is dus nog geen echte kandidaat met een L5-PBO onder de nieuwe code.

## 7. Uitgebreide kalibratie op pipeline-niveau

### 7.1 Vals-positieven (nul-edge), zoekniveau

"Vals-positief" = een kandidaat zonder echte edge passeert L5 en L6 in een zoektocht. 300 zoektochten per cel, Wilson 95%.
In **alle** 13 nulcellen (12 scenario's plus de basiscel zonder edge) is het resultaat 0 van 300, bovengrens 1,3%:
geen edge (netto 0), negatieve netto (-0,5 jaarlijks, kosten), gecorreleerde markten (rho 0,3 en 0,6), t3-staarten,
regimewisseling (edge alleen in rustige blokken, volatiliteit x2), korte validatie (750 dagen), lange validatie (2.500
dagen, 2.000 dagen ontwikkeling), geschiedenisfactor 1 en 20, lage frequentie (0,15 trades per dag, profiel
laag_frequent), hoge frequentie (0,9), K = 12 configuraties. Per cel 1,0-2,2 kandidaten per zoektocht bereiken L5
(0 bij negatieve netto: het ontwikkelscherm laat ze niet door). De 7.210 nulkandidaten (6.568 bij K = 4) halen de pijplijn nooit; de
overgrote meerderheid faalt op de DSR (100%), de ruis-CI's (49-96%), PF-ondergrens (96%) en concentratie (90%).

### 7.2 Power-curve (bekwame lijn; "bereikt L4" = ontwikkelscherm, "power" = hele pijplijn)

Netto Sharpe jaarlijks van de beste parameterset (andere sets 60-100% daarvan); 6 jaar validatie; H = 5; 150 zoektochten.

| netto Sharpe | bereikt L4 | L5 geslaagd | pijplijn geslaagd (power) |
|---|---|---|---|
| 0,25 | 0,093 | 0 | 0 [0, 0,025] |
| 0,5 | 0,173 | 0 | 0 [0, 0,025] |
| 0,75 | 0,393 | 0,080 | 0 [0, 0,025] |
| 1,0 | 0,673 | 0,193 | 0 [0, 0,025] |
| 1,5 | 0,967 | 0,453 | 0,013 [0,004, 0,047] |
| 2,0 | 0,993 | 0,553 | 0,193 [0,138, 0,264] |
| 3,0 | 1,000 | 0,700 | 0,680 [0,602, 0,749] |

### 7.3 Power per scenario (150 zoektochten, pijplijn geslaagd, Wilson 95%)

| scenario | Sharpe 0,5 | Sharpe 1,0 | Sharpe 2,0 |
|---|---|---|---|
| basis (6 jaar, p 0,5, H 5, K 4) | 0 [0, 0,025] | 0 [0, 0,025] | 0,193 [0,138, 0,264] |
| kort (750 dagen) | 0 | 0 | 0,013 [0,004, 0,047] |
| lang (2.500 dagen) | 0 | 0 | 0,420 [0,344, 0,500] |
| gecorreleerde markten rho 0,6 | 0 | 0 | 0,240 [0,179, 0,314] |
| lage frequentie (laag_frequent) | 0 | 0 | 0,213 [0,155, 0,286] |
| hoge frequentie (0,9) | 0 | 0 | 0,267 [0,202, 0,343] |
| regimewisseling (edge alleen in blok A) | 0 | 0 | 0 (bereikt L4: 0,293) |
| geschiedenis x20 | 0 | 0 | 0,133 [0,088, 0,197] |
| K = 12 configuraties | - | 0 | 0,267 [0,202, 0,343] |

Lezing [AFGELEID]: de pijplijn is een filter voor sterke edges. Bij een 1 jaar korter validatiebestand (3 jaar) vervalt het
power van 19% naar 1%, bij 10 jaar stijgt het naar 42%. Edges die alleen in een regime bestaan, worden al bij het
ontwikkelscherm (29% bereikt L4 bij Sharpe 2) en daarna door de poorten niet doorgelaten. **Zwakke echte edges (netto Sharpe <= 1,5)
worden volledig verworpen; ze eindigen als INSUFFICIENT_EVIDENCE (positieve Sharpe, DSR < 0,95) of REJECTED, nooit als
PASSED.** Dit komt overeen met de eerder gemeten return-niveau power (`STATISTICAL_CALIBRATION_REPORT.md`, N 100-500, T 1500:
0,73-0,55 bij Sharpe 2,0), dat hier lager uitkomt door N_eff ~700 uit 120 lijnen x 24 configuraties x H en door de extra
poorten (PBO, parameterstabiliteit).

## 8. Aannames en onzekerheid

* [AANNAME] Rendementen: normaal of t3, een trade per actieve dag, 0,5% risico per trade, 0,10 R kosten per trade (70% spread),
  eenvoudige regime- en correlatiestructuren (een gemeenschappelijke/markt/lijnfactor). Geen sprongen, geen structurele breuken.
* [AANNAME] Ontwikkelscherm als eendrempel op Sharpe (0,8 voor L3, 1,0 voor L4) in plaats van de exacte L2-L4-criteria;
  "bereikt L4" hangt daarvan af, "power gegeven L4" niet.
* [AANNAME] Surrogaten (documenteer bij gebruik): parameterbuur (gecorreleerde reeksen rho 0,8 met geleidelijk lagere ware
  gemiddelde), kosten- en uitvoeringsstress als vaste kostenverschuiving per trade, random-entry-nul als normale benadering
  van de bruto-edge-toets (verwerpt nullen in 0,7%: het toetst bruto-edge, geen netto-edge, en is dus geen bron van
  dubbele straf), geen basislijn- of eenvoudiger-variant-toets, regimelabels als vaste blokken. De bootstrapgrootte is 250
  herhalingen (factory: 1.000).
* [AANNAME] De geschiedenisfactor H herhaalt de meetstructuur van de lijnen; de eerdere rondes zijn onafhankelijk van de
  geteste lijn (niet-aanpassend). Aanpassend hergebruik van de validatieperiode is op pipeline-niveau niet gesimuleerd; op
  return-niveau (S3) liet de zoekniveau-NC-DSR FPR <= 1,3% zien, het alternatief (alleen validatie-evaluaties tellen) 12-15%.
* [GEMETEN] Onzekerheid: Wilson 95%-intervallen in alle tabellen; geen resultaat is gemiddeld over cellen zonder dat dit
  vermeld is. Nul uit 300 geeft een bovengrens van 1,3%, niet "0%". De power-cellen hebben 150 zoektochten (halve breedte tot
  ~7 pp). Zaden zijn vast (crc32 van cel en volgnummer), herhalen reproduceert alles.
* [OPEN] Onderzoek buiten het logboek (V1-V9) en niet-gesimuleerde aanpassende zoektochten kunnen de werkelijke FPR hoger maken
  dan de nul-cellen; de DSR-N bevat ze niet.

## 9. Reproductie

```
C:/Python311/python.exe evidence/v102/stats/pipeline_sim.py --reps 300 --procs 12   # ~60 minuten, hervatbaar
C:/Python311/python.exe evidence/v102/stats/pipeline_analyze.py
C:/Python311/python.exe evidence/v102/stats/pipeline_sim.py --campaign review --procs 16   # PBO-review, ~45 minuten, hervatbaar
C:/Python311/python.exe evidence/v102/stats/pbo_review_analyze.py
C:/Python311/python.exe evidence/v102/stats/pbo_review_repro.py   # nieuwe code, oude zaden: bit-identiek
C:/Python311/python.exe evidence/v101/stats/calibration.py --only S4,VREC --reps 600  # schrijft naar evidence/v102/stats
C:/Python311/python.exe -m pytest strategy_lab/tests -q -p no:cacheprovider
```
