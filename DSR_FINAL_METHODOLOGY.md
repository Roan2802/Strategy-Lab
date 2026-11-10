# DSR - final methodology (V10.1, 2026-11-10)

Code: `strategy_lab/research/effective_trials.py` (`dsr_variants`, `reliability`, `dsr_record`, `gate`),
`strategy_lab/research/factory.py` (L5 preliminary record, L6 final gate). Calibration evidence:
`STATISTICAL_CALIBRATION_REPORT.md`, `evidence/v101/stats/`. Tests: `strategy_lab/tests/test_v101_stats.py`.
Supersedes the gate description in `DSR_METHODOLOGY_AUDIT.md` (V10); that file remains the formula audit.

## 1. Audit against the papers

| Paper | What it defines | Code | Verdict |
|---|---|---|---|
| Bailey & Lopez de Prado 2012, PSR | `PSR(SR*) = Phi((SR - SR*) sqrt(T-1) / sqrt(1 - g3 SR + (g4-1)/4 SR^2))`, per-observation SR, non-excess kurtosis | `effective_trials.psr`, `validation.psr_from_moments` | correct; equals `scipy.stats.norm.cdf` of the same z to 1e-15 (3 cases) |
| BLdP 2014, DSR | `SR0 = sqrt(V) ((1-g) Z^-1(1-1/N) + g Z^-1(1-1/(N e)))`, `V` = variance of the trials' SR estimates, same units, `DSR = PSR(SR0)` | `expected_max_sr`, `dsr_variants["paper_dsr"]` | correct; worked example reproduced: SR0 0.11317 (paper 0.1132), DSR 0.90040 (paper 0.9004) |
| BLdP 2014 "Pseudo-mathematics and financial charlatanism" | E[max] of N skill-less backtests grows like `sqrt(2 ln N)`; with enough trials any IS Sharpe is reachable; minimum backtest length | E[max] formula, MinTRL | E[max] vs Monte Carlo (1,500 reps): within 0.4-3.4% (N 10/100/1000, normal and t3); with AR(0.2) returns the formula needs T_eff (error -18% with T, -3.2/-3.4% with T_eff) |
| Bailey, Borwein, LdP & Zhu 2017, PBO/CSCV | rank logit over combinatorial splits | `validation.pbo_cscv` | correct (V10 tests); PBO is an L5 overfit flag, not part of DSR |

The formulas were already correct in V10. What changes in V10.1 is (a) the variants are computed side by side and
named, (b) the gate choice is justified by calibration instead of by argument, (c) DSR failures are classified as
INSUFFICIENT_EVIDENCE vs REJECTED, (d) an explicit reliability check.

## 2. Three definitions, side by side (every DSR record)

`dsr_record(...)["variants"]` holds, for the same SR, skewness and kurtosis of the tested series:

| key | name in the UI | V | N | T | role |
|---|---|---|---|---|---|
| `paper_dsr` | Paper-DSR (BLdP 2014) | cross-trial variance of SR estimates in the units of the tested SR, horizon-rescaled by method of moments: `max(0, Var(SR_i) - mean((1+SR_i^2/2)/T_i)) + 1/T` | raw N | T | reported |
| `nc_dsr` | **Null-calibrated DSR (NC-DSR)** | `V_null = 1/T_eff` (sampling variance of a skill-less trial's SR on this horizon) | effective N (search level) | T_eff | **gate** |
| `dep_dsr` | Dependence-aware DSR | cross-trial V (as paper) | effective N | T_eff | reported |

NC-DSR is **not** the paper's DSR and is no longer presented as such: the field `value` of the record is the
NC-DSR (gate), `gate_variant = "nc_dsr"`, and the labels say so. `value_paper_v` (V10 field, `max(V_paper, V_null)`)
is kept for old runs.

Each variant reports `V, N, T_used, sr0, sr0_annualized, value, se_sr` (Mertens/Lo standard error
`sqrt((1 - g3 SR + (g4-1)/4 SR^2)/T)`) and `sr_ci95` (per observation and annualised). The record also reports
observed SR, raw N, effective N with its two-level detail (within-candidate configurations x between-candidate
correlation; eigenvalue Li-Ji 2005 and ONC clusters LdP 2019, the larger is used), `T`, `T_eff` (Bartlett-weighted
autocorrelation, Lo 2002 / Newey-West; never more than T), autocorrelation inflation, skewness, kurtosis, V source
(validation OOS evaluations / search trials rescaled / null floor) and sensitivities.

Scopes: `global` = every trial in the ledger (all families, markets, timeframes, rounds, runs) and `family` = the
trials of the family across all markets/timeframes/rounds. Gate = global only (it contains the family scope;
using both would be a nested condition, multiplying them a double penalty). Family value is reported.

## 3. Gate decision (from calibration, not pass counts)

Calibration (`STATISTICAL_CALIBRATION_REPORT.md`, 400 null / 200 edge replications per cell, Wilson 95% CIs):

1. **False-positive rate.** All three variants have FPR <= 1.3% (largest Wilson upper bound 2.9%) in every null
   cell: N 10-1000, T 250-1500, t3/t5 tails, AR(0.2), GARCH(1,1), volatility regimes, 20% activity, correlated
   trials (rho 0.7, 10 families rho 0.8), cost-induced negative true SR, 1000 correlated optimisation variants,
   8-market selection. DSR >= 0.95 is conservative by construction (SR0 is the *expected* maximum, not a quantile),
   so the nominal 5% is not reached. No variant is anti-conservative.
2. **Power.** NC-DSR >= paper DSR in every edge cell; the gap is largest when one trial genuinely differs (the
   skilled trial inflates the paper V): N 10, T 1500, edge 1.0 ann: 0.205 vs 0.055; 8 markets, edge 1.0: 0.315 vs
   0.110; N 10, T 750, edge 2.0: 0.53 vs 0.24. The paper's V measures real dispersion (losers after costs,
   variants of a real effect), not luck, and then punishes the best strategy for the existence of the others.
3. **Which N.** Two-stage search (S2, non-adaptive): dev-only trials cannot bias the OOS Sharpe under the null,
   so search-level N is a double penalty there (edge 2.0, 3 y OOS, 200 dev trials / 10 OOS: power 0.16 search-N vs
   0.595 OOS-N, both FPR <= 0.7%). **But** the factory's search is adaptive on the validation period: runs and
   rounds are repeated on the same 2018-2023 validation data and new candidates are derived from what survived.
   Scenario S3 (5 rounds, each mutating the best OOS series so far) shows that counting only validation
   evaluations then **underestimates** multiplicity: FPR 12.0% [9.2, 15.6] (rho 0.5) and 14.7% [11.6, 18.6]
   (rho 0.8); last-round-only N: 51.5%. Search-level N keeps FPR at 1.0-1.3% (upper 2.9%).

**Decision: the gate stays NC-DSR (V = 1/T_eff, T_eff) with the search-level, dependence-aware effective N, global
scope, threshold 0.95.** It is the only variant that is FPR-controlled under adaptive reuse of the validation period
and it has more power than the paper definition under every non-adaptive scenario. The price is a known double
penalty when the search is genuinely non-adaptive (S2); that is accepted because the ledger cannot prove
non-adaptivity (manual iteration V1-V9 on the same validation years is not in the ledger at all). The threshold is
not changed. A switch to a validation-level N would require a protocol in which every round is pre-registered and
generation never reads validation results; not the case today.

## 4. Assumptions and when the DSR is unreliable

Assumptions: trial SR estimates approximately Gaussian with variance (Mertens) `(1 - g3 SR + (g4-1)/4 SR^2)/T_eff`;
trials of one search are exchangeable apart from their correlation; under H0 every true SR <= 0 (NC-DSR uses SR=0,
the least favourable case; trials with negative true SR only lower the true maximum); serial dependence is captured
by T_eff (Bartlett); regime changes are not modelled (calibration with volatility regimes showed no FPR inflation,
but power for regime-only edges is ~0); every trial that saw data is in the ledger.

`reliability` (in every record) marks the DSR unreliable -> **INSUFFICIENT_EVIDENCE, never a FAIL** when:
T_eff < 60; fewer than 30 trades (when supplied); the PSR variance term `1 - g3 SR + (g4-1)/4 SR^2 <= 0.05`
(extreme skew/kurtosis: the asymptotic formula breaks down); paper variants need >= 2 comparable trials (else
`INSUFFICIENT_EVIDENCE` for `paper_dsr`/`dep_dsr`, NC-DSR still computable).

Outcome mapping at L6: DSR >= 0.95 -> continue; DSR < 0.95 with observed SR > 0 -> **INSUFFICIENT_EVIDENCE**
("not distinguishable from the best of N skill-less trials": absence of evidence, not evidence of failure;
next test: prospective forward test of the frozen rules); observed SR <= 0 -> **REJECTED**. Old L6 records that were
stored as REJECTED for the DSR are shown as INSUFFICIENT_EVIDENCE (`stored_status` keeps the original).

## 5. Double penalty vs underestimation (which correction covers what)

| data | selection on it | correction | not applied again |
|---|---|---|---|
| development (<= 2018) | generation, L2-L4 optimisation | dual-positive rule (dev net > 0, PF > 1); no statistical claim | - |
| validation (2018-2023) | nested-WF choice (train windows only), L5 filter, L6 ranking, all rounds/runs | NC-DSR, search-level effective N (counts every trial incl. dev, see 3.3) | family DSR (reported), PSR (implied), sealed Holm |
| sealed (>= 2024) | the set of sealed tests ever run | Holm over all sealed p-values (L8) | search trials |

Other L5 tests (bootstrap CI of the expectancy, PF/RF CIs, random-entry null, cost and execution stress,
neighbourhood, concentration, PBO, WFE) test different failure modes; none deflates the Sharpe a second time.
Known remaining double penalty: dev-only trials in N (accepted, section 3). Known remaining underestimation:
research outside the ledger (manual V1-V9 iteration) is not counted.

## 6. Search-level numbers

Every record exposes raw N, effective N (within ratio, between ratio, number of measured series), the
families/markets/timeframes/rounds it covers (via the ledger grouping `signature|symbol|timeframe`, all runs),
mean |correlation|, T, T_eff, skewness, kurtosis, SE and 95% CI of the SR, SR0 per variant. The real V10.1 run is
summarised in `FACTORY_FINAL_VALIDATION.md` and `evidence/v101/stats/real_run_summary.json`.
