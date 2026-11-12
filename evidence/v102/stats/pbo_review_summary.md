# PBO-review: CSCV-voorwaarden, REVIEW_REQUIRED en simulatie (2026-11-12)

Volledig verslag: `STATISTICAL_SELECTION_AUDIT.md` sectie 6b (voorwaardenaudit, voorregistratie, resultaten). Labels: [GEMETEN] uit simulatie of store, [AFGELEID] uit ontwerp, [AANNAME] niet getoetst.

## Wat er veranderd is
- `factory.pbo_over_l4` controleert CSCV-voorwaarden (gelijke tijdas, kostenvingerafdruk, set = eerste L4-rung incl. best/top-K, uitsluiting alleen technisch) en geeft `config_set`, `n_configs`, `n_eligible`, `excluded`, `configs`, `preconditions`. Geschonden voorwaarde = `INSUFFICIENT_EVIDENCE`, geen PBO-getal.
- Elke PBO-configuratie staat als `L5_PBO`-trial in de store (params, hash, validatiemetrics, verwijzing naar de L4-ledgerrij) en telt niet mee in N van de DSR [AFGELEID, getest].
- Nieuwe status `REVIEW_REQUIRED` (alleen als PBO > 0,5 de enige niet-PASS-reden is); anders REJECTED zoals voorheen. Geblokkeerd voor L6, bevriezing, eindtest, FORWARD_ELIGIBLE en forward-registratie tot een beoordeling is vastgelegd. Accepteren = terug naar `WF_PASSED` met de verplichte L6-DSR-poort; afwijzen = REJECTED. Append-only tabel `pbo_reviews` + beslissingslogboek. Endpoint `POST /api/factory/candidates/{id}/pbo-review` (404/409/422/200). Label in `factory.js`.
- `validation.scorecard`: drempel `pbo_review` (PBO boven limiet = REVIEW_REQUIRED, geen FAIL); standaardgedrag ongewijzigd.

## Simulatie (pipeline-niveau, gepaard, Wilson 95%) [GEMETEN]
Zie `pbo_review_tables.md`. Samenvatting: nul-FPR (auto-promotie en bovengrens "alles geaccepteerd") 0/300 [0; 1,3%] in alle nulcellen en 0/300 in het adaptieve scenario, bij top-4 en bij alle 24. Auto-promotie is per constructie gelijk aan de vorige regel (bit-identiek gecontroleerd). Volledige set: Sharpe 2 auto 41% (top-4 27%), Sharpe 3 98% (56%), REVIEW_REQUIRED 0% (31% en 43% bij top-4). Plateau-edge: REVIEW_REQUIRED 45% (Sharpe 2), auto 24% tegen bovengrens 42%.

## Echte kandidaten [GEMETEN]
234 kandidaten bereikten L5 in `factory.sqlite3`, alle REJECTED, geen enkele met PBO als enige reden: 0 worden REVIEW_REQUIRED. Geen echte kandidaat heeft een L5-PBO onder de nieuwe code.

## Open onzekerheden
- Het adaptieve scenario is niet onderscheidend: 0 van 3.328 ruiskandidaten haalt L5-schoon; de L5-poorten blokkeren alles. Het bewijst geen PBO-bescherming; PBO ziet hergebruik tussen rondes in principe niet [AFGELEID].
- Simulatie gebruikt surrogaten (parameterbuur, kostenstress, random-entry-nul, geen baselines) en normale of t3-rendementen [AANNAME].
- Metriekverschil: CSCV op Sharpe van dagrendementen, L4-selectie op Pareto; variant zonder sessiefilter zit niet in de PBO-set [NOTE].
- Een beoordeling na afloop van een run bevriest niet automatisch; L6 moet opnieuw draaien [OPEN].
- `shadow_forward.eligibility` behandelt REVIEW_REQUIRED niet apart (buiten de opdracht) [OPEN].
- PBO-herberekening over de volledige set voor de 234 bestaande L5-records is niet gedaan (vereist heruitvoeren van backtests); de uitkomst verandert niet omdat PBO bij geen enkele de enige reden is [AFGELEID].
