# Statistische validatie - rapport

Module: `strategy_lab/research/validation.py`. Tests: `strategy_lab/tests/test_validation.py` (32 geslaagd, `pytest -q`).

## Definities en formules

Alle Sharpe-ratio's in PSR/DSR/MinTRL zijn **per waarneming** (niet geannualiseerd). `g3` = scheefheid, `g4` = niet-excess kurtosis.

- **PSR** (Bailey & Lopez de Prado, 2012, "The Sharpe Ratio Efficient Frontier", J. of Risk 15(2)):
  `PSR(SR*) = Phi( (SR - SR*) * sqrt(n-1) / sqrt(1 - g3*SR + (g4-1)/4 * SR^2) )`.
- **DSR** (Bailey & Lopez de Prado, 2014, "The Deflated Sharpe Ratio", J. of Portfolio Management 40(5)):
  `DSR = PSR(SR0)`, `SR0 = sqrt(V) * ( (1-gamma)*Phi^-1(1-1/N) + gamma*Phi^-1(1-1/(N*e)) )`, gamma = Euler-Mascheroni (0,5772...), N = aantal trials, V = variantie van de SR over de trials.
- **MinTRL** (idem 2012): `1 + (1 - g3*SR + (g4-1)/4*SR^2) * (z_alpha/(SR-SR*))^2`.
- **Haircut-Sharpe** (Bonferroni/Holm, vgl. Harvey & Liu 2015): `p = 1-Phi(SR*sqrt(n))`, `p_adj = min(1, N*p)`, `SR_adj = Phi^-1(1-p_adj)/sqrt(n)`.
- **Multiple testing**: Holm (1979) step-down en Benjamini-Hochberg (1995) FDR.
- **Bootstrap-CI**: percentiel-interval met stationaire (Politis & Romano 1994) of moving-block bootstrap (Kunsch 1989); blokduur automatisch ~ n^(1/3).

Uitgewerkte voorbeelden (in de tests): SR=0,1, n=250, g3=-0,5, g4=5 geeft PSR = 0,9373; met N=100 en V=0,01 is SR0 = 0,2531 en DSR = 0,0095; MinTRL = 1 + 1,06*(1,6449/0,1)^2 = 287,8 waarnemingen.

## Aannames

- Trades zijn **niet iid** (overlap, clustering, regimes). P-waarden en betrouwbaarheidsintervallen worden daarom op **dagrendementen** met **blokbootstrap** berekend (`daily_returns_from_trades`: gerealiseerde P&L per UTC-exitdag / equity aan het begin van de dag).
- PSR/DSR gebruiken scheefheid en kurtosis maar nemen onafhankelijke waarnemingen aan; bij seriele correlatie zijn ze optimistisch.
- Het aantal geprobeerde hypotheses (`n_trials`) en de variantie van de SR over trials moeten eerlijk worden aangeleverd; een te laag aantal maakt de DSR te mild.
- Geen enkele functie geeft een kans op toekomstige winst.

## Wat betekent INSUFFICIENT_EVIDENCE?

Er zijn minder dan 30 waarnemingen of de variantie is nul. Het resultaat is dan niet berekend (`value = None`) en telt **nooit** als geslaagd. In het scorecard leidt dit tot verdict `NEEDS_MORE_DATA`.

## Scorecard

Verdicten: `REJECT`, `NEEDS_MORE_DATA`, `RESEARCH_CANDIDATE`, `OOS_PASSED`, `FORWARD_ELIGIBLE`. Checks (PASS/FAIL/INSUFFICIENT_EVIDENCE/NOT_TESTED): sample size, netto na kosten, PF, max DD, PSR, DSR, PBO, WFE, OOS-winst, kostenstress (+50% spread), parameterstabiliteit, drop-best-trades, jaarlijkse consistentie. Drempels in `DEFAULT_THRESHOLDS`, aanpasbaar. Regels: ontbrekend bewijs = NOT_TESTED en verdict maximaal RESEARCH_CANDIDATE; harde FAIL op netto of DSR (of >= 2 FAILs) = REJECT; OOS_PASSED vereist WFE en OOS-winst PASS; FORWARD_ELIGIBLE vereist alle checks PASS.

## Testbewijs

| Onderdeel | Test | Resultaat | Status |
|---|---|---|---|
| Normale CDF/PPF | `test_normal_cdf_ppf_roundtrip` | geslaagd | PASS |
| PSR | `test_psr_worked_example`, `test_psr_from_returns_normal_case_matches_closed_form` | geslaagd | PASS |
| DSR | `test_dsr_worked_example` | geslaagd | PASS |
| MinTRL | `test_min_track_record_length_worked_example` | geslaagd | PASS |
| INSUFFICIENT_EVIDENCE | `test_sr_insufficient_evidence`, `test_bootstrap_small_sample_insufficient` | geslaagd | PASS |
| Bootstrap-CI | `test_bootstrap_ci_covers_true_mean_on_iid`, `test_block_bootstrap_wider_than_iid_on_ar1` | geslaagd | PASS |
| Multiple testing / haircut | `test_multiple_testing_holm_bh`, `test_haircut_sharpe_grows_with_trials` | geslaagd | PASS |
| Benchmark-effect | `test_effect_vs_benchmark` | geslaagd | PASS |
| Breakdowns | `test_breakdowns_and_daily_returns` | geslaagd | PASS |
| Scorecard | `test_scorecard_all_pass_is_forward_eligible`, `test_scorecard_overfitted_case_not_candidate`, `test_scorecard_tiny_sample_needs_more_data`, `test_scorecard_missing_oos_capped_at_research_candidate`, `test_scorecard_hard_fail_net_or_dsr_rejects_and_thresholds_configurable`, `test_scorecard_unwraps_engine_dicts` | geslaagd | PASS |
| Nederlands rapport | `test_validation_report_nl_sections` | geslaagd | PASS |
| Validatie op echte marktdata | niet uitgevoerd (tests gebruiken synthetische data en een nep-backtest) | - | NOT TESTED |
| Dukascopy-backtest-integratie (`research/backtest.py`) | niet in dit werkpakket | - | NOT TESTED |
