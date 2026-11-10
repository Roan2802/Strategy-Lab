# Overfitting-analyse

Functies: `pbo_cscv`, `deflated_sharpe_ratio`, `haircut_sharpe`, `multiple_testing`, `scorecard`.

## Definities

- **PBO via CSCV** (Bailey, Borwein, Lopez de Prado & Zhu, 2015, "The Probability of Backtest Overfitting", J. of Computational Finance 20(4)): matrix T x N (rijen = periodes met rendementen, kolommen = geprobeerde configuraties). De rijen worden in `n_splits` (even, standaard 8) aaneengesloten blokken gesneden. Voor elk van de C(S, S/2) keuzes van de helft als in-sample (IS) wordt de beste IS-trial `n*` gekozen (Sharpe of gemiddelde); zijn relatieve OOS-rang `w = rang/(N+1)` geeft `logit = ln(w/(1-w))`. **PBO = aandeel combinaties met logit <= 0.** Boven 5000 combinaties wordt gesampled.
- **DSR**: zie STATISTICAL_VALIDATION_REPORT.md; corrigeert Sharpe voor het aantal geprobeerde configuraties (Bailey & Lopez de Prado, 2014).
- **Haircut / Holm / BH**: Bonferroni/Holm-haircut op de Sharpe (Harvey & Liu 2015), Holm (1979) en Benjamini-Hochberg (1995).

## Interpretatie en aannames

- PBO ~ 0 betekent dat de IS-winnaar ook OOS wint; PBO ~ 0,5 betekent dat selectie geen informatie bevat; PBO richting 1 betekent dat IS-winnaars systematisch OOS falen.
- Zuivere ruis geeft gemiddeld PBO ~ 0,5 (gemeten over 20 draws, spreiding groot per draw), **niet** 1. Een waarde dicht bij 1 ontstaat bij anti-persistente prestaties (bijv. kolommen met gelijke totaalprestatie).
- PBO hangt af van een volledige en eerlijke lijst geprobeerde configuraties. Niet-geregistreerde proeven maken PBO en DSR te optimistisch.
- Blokken moeten voldoende waarnemingen bevatten; de Sharpe per blok is ruis bij weinig rijen.
- Trades zijn niet iid: gebruik dagrendementen per configuratie als matrix.

## Wat betekent INSUFFICIENT_EVIDENCE?

PBO: N < 2 trials, T < `n_splits`, oneven `n_splits` of geen 2D-matrix. Haircut: n_obs < 30. DSR: < 30 waarnemingen of nul variantie. Nooit een pass.

## Scorecard-gedrag bij overfitting

Hoog IS-resultaat met `n_trials = 500`, lage DSR, PBO 0,8 en negatieve OOS krijgt `REJECT`; ook zonder DSR-waarde zorgen PBO + WFE + OOS-falen (>= 2 FAILs) voor `REJECT`.

## Testbewijs

| Onderdeel | Test | Status |
|---|---|---|
| PBO ruis ~ 0,5 en anti-persistent ~ 1 | `test_pbo_noise_is_about_half_and_antipersistent_near_one` (geslaagd) | PASS |
| PBO dominante trial ~ 0 | `test_pbo_dominant_trial_near_zero` (geslaagd) | PASS |
| PBO INSUFFICIENT | `test_pbo_insufficient` (geslaagd) | PASS |
| DSR daalt met aantal trials | `test_dsr_worked_example` (geslaagd) | PASS |
| Haircut en multiple testing | `test_haircut_sharpe_grows_with_trials`, `test_multiple_testing_holm_bh` (geslaagd) | PASS |
| Overfit-scenario niet kandidaat | `test_scorecard_overfitted_case_not_candidate` (geslaagd) | PASS |
| PBO op echte strategie-trials uit de optimizer | niet uitgevoerd | NOT TESTED |
| Automatische registratie van `n_trials` vanuit de optimizer | buiten dit werkpakket | NOT TESTED |
