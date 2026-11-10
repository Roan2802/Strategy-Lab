# Walk-forward validatie

Functies: `walk_forward`, `parameter_stability`, `cost_stress`, `drop_best` in `strategy_lab/research/validation.py`.

## Methode

Walk-forward (Pardo): optimaliseer op een train-venster, evalueer de gekozen parameters op het direct volgende, ongeziene test-venster, schuif daarna `step_days` op. Modus `rolling` (vaste trainlengte) of `expanding` (train start altijd bij `start`). Vensters zijn kalenderdagen over `[start, end)`; het test-venster overlapt nooit met de train van hetzelfde venster. Objectief: `sharpe`, `net_profit` of `profit_factor`. Kandidaten: grid (dict van lijsten), lijst van dicts of `sampler(n, rng)`, begrensd tot `max_combos` (seeded willekeurige subset).

**Walk-Forward Efficiency (expliciete definitie)** per venster met positieve in-sample winst:
`WFE_w = (OOS netto winst / test_dagen) / (IS netto winst / train_dagen)`; `wfe` = gemiddelde over vensters met status OK. Annualiseren van beide snelheden met dezelfde factor laat de ratio ongewijzigd. WFE rond 1 = OOS houdt tempo met IS; veel lager of negatief wijst op overfitting.

Per venster: train- en testrange, gekozen parameters, train- en testmetrics (netto winst, PF, recovery, Sharpe/Sortino, max DD, aantal trades) en de samengevoegde OOS-trades.

`parameter_stability`: elke parameter apart +/- `rel_step` (gehele parameters minimaal 1); rapporteert aandeel naburige punten met positieve winst en relatieve verandering van het objectief.

`cost_stress`: baseline, spread +25%, spread +50%, commissie x2, slippage +1 punt, entry 1 bar later, exit 1 bar later (configureerbaar), met degradatie t.o.v. baseline. `drop_best`: effect van verwijderen van de beste 1/3/5 trades en dagen.

## Aannames

- De aangeleverde `backtest_fn(params, start, end, **stress)` is deterministisch en gebruikt alleen data binnen `[start, end)`; lookahead in de backtest zelf wordt hier niet gedetecteerd.
- "Commissie x2" gebruikt `commission_per_lot = 2 * base_commission_per_lot` (standaard `params["commission_per_lot"]`, anders 7,0); stel de echte commissie in.
- Vensters met te weinig trades zijn geen bewijs, geen mislukking.

## Wat betekent INSUFFICIENT_EVIDENCE / INSUFFICIENT?

Venster-status `INSUFFICIENT`: geen parametercombinatie haalde `min_trades` in train, of het test-venster heeft minder dan `min_trades` trades. Zulke vensters tellen niet mee in WFE. Als geen enkel venster OK is, is de totaalstatus `INSUFFICIENT_EVIDENCE` en is WFE `None` (scorecard: ONVOLDOENDE BEWIJS).

## Testbewijs

| Onderdeel | Test | Status |
|---|---|---|
| Walk-forward rolling en expanding, vensterranges, WFE-formule, OOS-trades | `test_walk_forward_fake_backtest_rolling_and_expanding` (geslaagd) | PASS |
| INSUFFICIENT-vensters | `test_walk_forward_marks_insufficient_windows` (geslaagd) | PASS |
| max_combos en sampler | `test_walk_forward_caps_combos_and_accepts_sampler` (geslaagd) | PASS |
| Kostenstress | `test_cost_stress_fake_backtest` (geslaagd) | PASS |
| Drop-best | `test_drop_best` (geslaagd) | PASS |
| Parameterstabiliteit | `test_parameter_stability_fake` (geslaagd) | PASS |
| Scorecard gebruikt walk-forward/kostenstress | `test_scorecard_unwraps_engine_dicts` (geslaagd) | PASS |
| Walk-forward op echte Dukascopy-data | niet uitgevoerd (nep-backtest zonder marktdata) | NOT TESTED |
| Aparte holdout-functie | niet geimplementeerd; holdout-OOS kan via de `oos_*` bewijssleutels in het scorecard worden aangeleverd | PARTIAL |


## V10: genest walk-forward met purge/embargo (`strategy_lab/research/nested_wf.py`)

De factory (L5) gebruikt sinds V10 `nested_walk_forward`; `validation.walk_forward` blijft bestaan voor de losse
validatie-runner.

- **Buitenste lus:** rolling of expanding trainvensters, gevolgd door ongeziene testvensters (configureerbare lengtes,
  `step_days`). De gepoolde testvensters vormen het OOS-bewijs.
- **Binnenste lus (alleen binnen het buitenste trainvenster):** niveau 1 kiest parameters op inner-train, niveau 2 kiest
  de architectuur-/filtervariant op inner-validatie (laatste blok, of `inner="kfold"`: geblokte k-fold met purge en
  embargo). De gekozen variant krijgt daarna opnieuw parameters op het volledige trainvenster en wordt één keer op het
  testvenster geëvalueerd.
- **Purge/embargo uit de echte afhankelijkheid** (`dependency_gap`): purge = langste houdduur (`time_exit_bars`, anders
  1,5 × de langste waargenomen houdduur); embargo = houdduur + langste look-back (`rule_engine.lookback_bars`). Bars →
  kalenderdagen ×7/5 (weekenden), naar boven afgerond, +1. Het trainvenster eindigt `purge` dagen voor de test; train-trades
  waarvan de uitkomst in het gat/de test valt, worden verwijderd.
- **Evaluatie:** standaard één causale backtest per configuratie over het hele bereik, daarna per venster gesneden; elke
  trade herschaald naar vers vensterkapitaal (`pnl × kapitaal / equity vóór de trade`). `evaluate="window"` draait per venster.
- **Uitvoer per venster:** train- vs OOS-metrics (trades, netto, PF, winrate, verwachting, max DD, Sharpe), gekozen variant en
  parameters, `params_changed`, binnenste scores, regimetabel. **Samenvatting:** winstgevende OOS-vensters, slechtste
  venster-DD en gepoolde OOS-DD, WFE (gemiddeld, mediaan, gepoold = gem. OOS-rate / gem. IS-rate), degradatie (Sharpe- en
  verwachtingsratio OOS/IS), parameterwissel-ratio, OOS-equity, **afhankelijkheid van één venster** (beste venster > 50%
  van de OOS-winst, of OOS-winst zonder beste venster ≤ 0 → FAIL in L5).

| Onderdeel | Test | Status |
|---|---|---|
| purge/embargo uit spec en trades | `test_dependency_gap_from_spec_and_trades` | PASS |
| vensters gepurged, nooit buiten bereik gelezen, rolling + expanding | `test_nested_wf_windows_purged_and_never_read_outside_range` | PASS |
| één backtest per configuratie, purge van overlappende trades | `test_nested_wf_slice_mode_one_backtest_per_config_and_purges_overlapping_trades` | PASS |
| vlag afhankelijkheid van één venster | `test_single_window_dependence_flag` | PASS |
| L5 genest WF op factory-data, verzegelde periode nooit gelezen | `test_sealed_period_never_read_by_search_and_validation_stages` | PASS |
