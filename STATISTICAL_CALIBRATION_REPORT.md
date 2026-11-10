# Statistical calibration report (V10.1, 2026-11-10)

Script: `evidence/v101/stats/calibration.py` (results `calibration_results.json`, all tables
`calibration_tables.md`, printer `summarize.py`). Synthetic returns only; seeds fixed per cell (CRC32 of the cell
parameters), rerunning reproduces every number. Compute: REF 17 s, S1 253 s, S2 71 s, S3 25 s on 24 cores
(single process). The factory pipeline-level campaign of V10 (`evidence/v10/stats/benchmark.py`, scenarios A-I,
results `benchmark_results.json`) is reused, not rerun: no-edge pipeline FPR 0/100 [0, 3.7%]; injected trend
edge detected 0/20 (3 y), 16/20 (6 y), 20/20 (10 y); costs turn gross edge into net loss 0/10 passes.

Statistic under test: "DSR >= 0.95" for the tested (selected) series. Rates are shown as
`rate [Wilson 95% lo, hi]`. Null cells 400 replications, edge cells 200 (S1 many-optimisation cells 100).
"edge" = annualised Sharpe of ONE skilled trial (others skill-less); "best SR ann" = median observed annualised
Sharpe of the selected trial. NC = Null-calibrated DSR (gate), paper = BLdP 2014 definition, dep = paper V with
effective N.

## 1. Reference computations

| check | result |
|---|---|
| BLdP 2014 worked example (SR 2.5 ann, T 1250, skew -3, kurt 10, N 100, V 0.5/250) | SR0 0.11317 (paper 0.1132), DSR 0.90040 (paper 0.9004) |
| PSR vs `scipy.stats.norm.cdf` (3 cases incl. skew -1 / kurt 8) | identical to 1e-15 |
| E[max SR] closed form vs Monte Carlo, N 10 / 100 / 1000, T 750 / 250, normal | +3.4% / +0.4% / +1.3% |
| same, t3 innovations (N 10 / 100) | +2.0% / +1.2% |
| same, AR(0.2), closed form with T vs with T_eff (N 10 / 100) | -18.2% vs -3.4% / -18.0% vs -3.2% |
| correlated trials rho 0.5 / 0.9 (N 100): E[max - mean] MC vs closed form with raw N and V = (1-rho)/T | 0.06529 vs 0.06534 / 0.02920 vs 0.02922 |
| same, NC form (effective N 51 / 11, V = 1/T) | 0.0834 / 0.0592 (conservative: includes the common factor) |

## 2. S1 - best of N trials on one period (DSR-native setting)

False positives (edge 0), selection from 10 / 100 / 500 trials:

| T (days) | N | best SR ann | paper | NC (gate) | dep |
|---|---|---|---|---|---|
| 250 | 10 | 1.57 | 0.000 [0, 0.010] | 0.007 [0.003, 0.022] | 0.000 [0, 0.010] |
| 750 | 10 | 0.87 | 0.000 [0, 0.010] | 0.007 [0.003, 0.022] | 0.000 [0, 0.010] |
| 1500 | 10 | 0.61 | 0.000 [0, 0.010] | 0.003 [0, 0.014] | 0.000 [0, 0.010] |
| 250 | 100 | 2.47 | 0.003 [0, 0.014] | 0.005 [0.001, 0.018] | 0.003 [0, 0.014] |
| 750 / 1500 | 100 | 1.46 / 1.02 | 0.000 | 0.000 | 0.000 |
| 250 / 750 / 1500 | 500 | 3.04 / 1.71 / 1.22 | 0.000 | 0.000 | 0.000 |

Stress nulls (N 100, T 750 unless noted) - every variant <= 1.3%:

| scenario | paper | NC (gate) | dep |
|---|---|---|---|
| t3 heavy tails / t5 | 0.000 / 0.000 | 0.003 / 0.000 | 0.000 / 0.000 |
| AR(0.2) serial dependence (T_eff ~540) | 0.000 | 0.000 | 0.000 |
| GARCH(1,1) a 0.08 b 0.90 | 0.000 | 0.000 | 0.000 |
| volatility regimes (x2 in alternate years) | 0.000 | 0.000 | 0.000 |
| low trade frequency (20% active days) | 0.000 | 0.003 | 0.000 |
| correlated trials rho 0.7 (N_eff 31) | 0.003 | 0.003 | 0.003 |
| 10 families, rho 0.8 inside (N_eff 33) | 0.000 | 0.000 | 0.000 |
| costs: true SR of trials ~ -|N(0, 1.5 ann)| | 0.000 | 0.000 | 0.000 |
| 1000 optimisation variants rho 0.9 (N_eff ~102) | 0.000 [0, 0.037] | 0.000 | 0.000 |
| multi-market selection, 8 markets rho 0.2, T 1500 | 0.007 [0.003, 0.022] | 0.013 [0.005, 0.029] | 0.010 |

Power (one skilled trial):

| cell | paper | NC (gate) | dep |
|---|---|---|---|
| N 10, T 1500, edge 0.5 / 1.0 / 2.0 | 0.010 / 0.055 / 0.765 | 0.030 / 0.205 / 0.945 | 0.005 / 0.050 / 0.750 |
| N 10, T 750, edge 2.0 | 0.240 | 0.530 | 0.205 |
| N 100, T 1500, edge 1.0 / 2.0 | 0.045 / 0.660 | 0.050 / 0.730 | 0.040 / 0.660 |
| N 100, T 750, edge 2.0 | 0.150 | 0.185 | 0.135 |
| N 500, T 1500, edge 2.0 | 0.530 | 0.545 | 0.535 |
| N 100, T 250, edge 2.0 | 0.010 | 0.010 | 0.010 |
| t3 / AR(0.2) / GARCH, edge 1.0 | 0.005 / 0.005 / 0.005 | 0.015 / 0.010 / 0.010 | 0.005 |
| regime-only edge 1.0 | 0.000 | 0.000 | 0.000 |
| 1000 variants rho 0.9, edge 1.5 | 0.020 | 0.040 | 0.050 |
| 8 markets, T 1500, edge 1.0 | 0.110 | 0.315 | 0.130 |

Effect of T and N: power rises steeply with T (N 10, edge 2.0: 0.095 at 1 y, 0.53 at 3 y, 0.945 at 6 y, NC) and
falls with N (T 1500, edge 2.0: 0.945 / 0.73 / 0.545 for N 10 / 100 / 500). An edge of 0.5-1.0 annualised
Sharpe is not detectable with <= 6 years and >= 100 trials by any variant: such strategies must end as
INSUFFICIENT_EVIDENCE, not as REJECTED and not as PASSED.

## 3. S2 - two-stage search (dev search, top K to OOS, best OOS tested; non-adaptive)

| N_dev / K, T_oos | edge | NC search N (gate) | NC OOS N | paper OOS N | paper search N | dep OOS N |
|---|---|---|---|---|---|---|
| 200 / 10, 750 | 0 | 0.000 [0, 0.010] | 0.005 [0.001, 0.018] | 0.000 | 0.000 | 0.000 |
| 200 / 10, 1500 | 0 | 0.000 | 0.007 [0.003, 0.022] | 0.000 | 0.000 | 0.000 |
| 1000 / 20, 750 / 1500 | 0 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 200 / 10, 750 | 2.0 | 0.160 | 0.595 | 0.220 | 0.000 | 0.210 |
| 200 / 10, 1500 | 1.0 / 2.0 | 0.015 / 0.670 | 0.160 / 0.925 | 0.040 / 0.745 | 0.000 / 0.000 | 0.040 / 0.735 |
| 1000 / 20, 1500 | 1.0 / 2.0 | 0.000 / 0.485 | 0.075 / 0.915 | 0.025 / 0.705 | 0.000 / 0.000 | 0.025 / 0.695 |

Under a non-adaptive search the dev-only trials cannot bias the OOS Sharpe; counting them is a double penalty
(power 0.485 vs 0.915). Paper V with search-level N has zero power (the skilled trial dominates V).

## 4. S3 - repeated rounds on the same validation period (adaptive)

5 rounds x 100 dev trials, top 10 to OOS (T_oos 1000); later rounds generate trials correlated (rho_mut) with the
best OOS series so far - the way repeated factory runs and mutation from survivors reuse validation information.

| rho_mut | edge | NC last-round N (10) | NC validation-ledger N (50) | NC search N (550, gate) |
|---|---|---|---|---|
| 0.0 | 0 | 0.015 [0.007, 0.032] | 0.000 [0, 0.010] | 0.000 [0, 0.010] |
| 0.5 | 0 | **0.515** [0.466, 0.564] | **0.120** [0.092, 0.156] | 0.010 [0.004, 0.025] |
| 0.8 | 0 | **0.515** [0.466, 0.564] | **0.147** [0.116, 0.186] | 0.013 [0.005, 0.029] |
| 0.0 / 0.5 / 0.8 | 2.0 | 0.745 / 0.955 / 0.990 | 0.450 / 0.750 / 0.915 | 0.165 / 0.325 / 0.635 |

Counting only evaluations on the validation period underestimates multiplicity once generation is informed by
validation results (FPR 12-15%, nominal 5%). The search-level N keeps FPR <= 1.3%.

## 5. Conclusions used by the factory

* Gate: NC-DSR, search-level effective N, global scope, 0.95 (unchanged threshold). FPR-controlled in every
  scenario including adaptive reuse; more power than the paper definition everywhere. See
  `DSR_FINAL_METHODOLOGY.md` section 3.
* Accepted cost: double penalty under a genuinely non-adaptive search (S2), up to ~2x lower power.
* DSR >= 0.95 is conservative (observed FPR 0-1.3% vs nominal 5%) because SR0 is an expectation, not a quantile.
* Weak edges (<= 1 Sharpe ann) and short samples (1 year) are not detectable: classified INSUFFICIENT_EVIDENCE.
* T_eff is required under serial dependence (closed form error -18% with T, -3% with T_eff).
* Limitations: Gaussian/t/AR/GARCH generators only (no jumps or structural breaks beyond 2 volatility regimes);
  one skilled trial per search; the adaptive model in S3 is one mechanism (mutation of the best), not every form
  of researcher degrees of freedom; Monte Carlo errors are given by the Wilson intervals.
