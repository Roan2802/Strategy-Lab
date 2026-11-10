# DSR / PSR / PBO / WFE / bootstrap / multiple-testing audit (V10, 2026-11-10)

Scope: `strategy_lab/research/validation.py` (formulas), `factory.py` L5-L8 (how they were fed), `protocol.py`
(sealed multiple testing). New code: `research/effective_trials.py`, `research/nested_wf.py`, `research/robustness.py`.
Evidence: `evidence/v10/stats/` (benchmark, V9 comparison). Tests: `strategy_lab/tests/test_v10_stats*.py`.

## 1. Verdict per statistic

| Statistic | Reference | Formula in code | Inputs in V9 factory | Status |
|---|---|---|---|---|
| PSR | Bailey & Lopez de Prado 2012, eq. 9 | correct: per-observation SR, `sqrt(n-1)`, non-excess kurtosis | daily OOS returns | formula OK; now also with `T_eff` (serial correlation) |
| MinTRL | BLdP 2012, eq. 11 | correct | - | OK (closed-form test) |
| E[max SR] / SR0 | BLdP 2014, eq. 4 | correct | **wrong V, raw N** | formula OK; inputs replaced |
| DSR | BLdP 2014 | `PSR(SR0)` correct | V = variance of annualised `sharpe_daily` of ALL ledger rows / 252; N = all distinct ledger hypotheses | **inputs wrong -> DSR ~ 0 for everything** |
| PBO / CSCV | Bailey, Borwein, LdP & Zhu 2017 | correct (rank `w = rank/(N+1)`, logit, share <= 0) | top-k = 2..4 configurations only | formula OK; low power with 2-4 columns -> V10 uses every nested-WF configuration (top-k x variants) |
| WFE | Pardo | per-window ratio of per-day net rates, mean | as defined | OK but unstable when IS profit is tiny -> V10 adds `wfe_pooled` and `wfe_median` |
| Bootstrap | Politis-Romano 1994, Kunsch 1989 | stationary + moving block, block ~ n^(1/3) | trade P&L (block) | OK; V10 adds daily mean / daily Sharpe CIs with both schemes |
| Holm / BH | Holm 1979, BH 1995 | correct | sealed p-values (block bootstrap) | OK |
| Haircut Sharpe | Harvey & Liu 2015 | correct, assumes iid | not in gate | OK (informational) |
| Sealed DSR | - | `N = sealed tests + ALL search trials` | - | **double penalty** -> V10: N = sealed tests only; Holm stays the gate |

All paper formulas were reproduced independently in tests: BLdP (2014) worked example (annual SR 2.5, T = 1250,
skew -3, kurtosis 10, N = 100, V = 0.5/250 gives SR0 = 0.1132 and DSR = 0.90), PSR against a `scipy.stats`
reference, E[max] against a Monte Carlo of independent normals (N = 10, 100, 1000; within 3%), MinTRL closed form,
PBO on a dominant column (< 0.1) and on pure noise (~0.5).

## 2. Why V9 reported DSR = 0.00 for every candidate

Measured on the V9 run (`evidence/v10/stats/v9_dsr_compare.json`, ledger restricted to the 2,354 rows that existed
when that run finished):

* V9 used V = variance of the annualised Sharpe of every ledger row (3-month window rungs, full-period rungs,
  holdout rows; annualised range -16 .. +4.4) divided by 252, and N = 647-1,783 distinct hypotheses.
  Median SR0 = **4.7 annualised**. No real strategy clears that; the best V9 OOS Sharpe was 0.90.
* The per-day units were in fact consistent (annualised / 252). The real fault was what V measured: the
  dispersion of very different trials (different horizons, different periods, cost-dominated losers). Short
  windows add sampling noise (~1/T_i with T_i ~ 60 days); the full-period trials add genuine dispersion of TRUE
  Sharpe ratios. Neither is the luck of a skill-less trial on the 6-year validation horizon.

## 3. V10 design (`effective_trials.py`)

**Units.** Every Sharpe is per observation (daily); annualised values are reported next to it.

**T and T_eff.** `T` = number of daily returns of the tested walk-forward OOS series. `T_eff` = T divided by the
Bartlett-weighted autocorrelation inflation (Newey-West lag rule, capped at T). PSR/DSR use `T_eff`.

**N raw.** Distinct hypothesis ids in the scope (ledger rows, all rounds).

**N effective.** Two levels, because not every historical trial has a stored return series:
1. within a candidate: daily P&L of every configuration on the first successive-halving rung (same windows);
   effective N = max(eigenvalue estimate of Li & Ji 2005, ONC-style cluster count of Lopez de Prado 2019), never
   more than the raw count; stored as `n_eff_within` in the ledger row of L4;
2. between candidates: one weekly representative series per candidate (L3, development period, default params),
   stored compactly in the L3 ledger row; ratio n_eff/n_raw over those series.
`N_eff = ratio_between * sum_c within_c`; candidates without a measurement use the median measured within-ratio
(raw count when nothing was measured, i.e. conservative for pre-V10 rows).

**V.** Two quantities are computed and both reported:
* `V_paper` (the BLdP definition, variance of trial Sharpe estimates): taken from other OOS evaluations on the
  validation period (L5 rows of all rounds: same period, same horizon, same units), rebuilt for the tested horizon by
  method of moments `V = max(0, Var(SR_i) - mean((1 + SR_i^2/2)/T_i)) + 1/T_test`; fallback: all search trials with a
  known horizon >= 120 days; last resort the null floor.
* `V_null = 1/T_eff`: the sampling variance of a skill-less trial's Sharpe on this horizon (Mertens 2002 at SR = 0).

**The gate uses V_null.** SR0 is, by derivation, the expected maximum of N trials *without skill*. `V_paper` equals
that only when the trials have no skill. When the trials genuinely differ (cost-dominated losers, or several skilled
variants of a real effect) `V_paper` measures real differences, not luck, and SR0 then punishes the best strategy
for the existence of the others. Benchmark B shows it: injected edge, OOS Sharpe 2.7-3.4 annualised over ~5 years,
17 trials: `value_paper_v` 0.0001-0.48 versus gate DSR 1.00 (see `ANTI_OVERFITTING_VALIDATION.md`). The
multiplicity of the search is carried in full by N, which counts every trial of every round. `value_paper_v` is
always stored next to the gate value. This choice was made after the benchmark exposed the problem; it is documented
here rather than hidden, and the no-edge false-positive rate is reported under both choices.

**Scopes.** `family` = all trials of the family (all markets, timeframes, rounds); `global` = every trial in the
ledger (search level). Global contains family, so it is the stricter one. **Gate = global DSR >= min_dsr (0.95)**;
family DSR is reported only. Taking both as AND would add nothing (nested condition); multiplying them would be a
double penalty.

**Where the gate runs.** L5 computes a preliminary record (snapshot of the ledger while the run is still
searching). L6 recomputes the global record for every L5 survivor after the whole search of the run has finished,
with the final ledger, and rejects (`REJECTED`, reason "meervoudig testen: globale DSR ...") below the threshold.

**Every DSR record exposes**: observed SR (per day and annualised), SR0, raw N, effective N (with the two-level
detail), V (gate), V_null, V_paper (with source and sensitivities), T, T_eff, skew, kurtosis, method `DSR_V10`,
`value`, `value_raw_n`, `value_iid_T`, `value_paper_v`, limitations.

## 4. No double penalty: which correction covers which uncertainty

| Data slice | Selection performed on it | Correction (gate) | Not applied again |
|---|---|---|---|
| development (<= 2018) | generation, L2-L4 optimisation | none needed: no evidence is claimed from IS | - |
| validation (2018-2023) | nested-WF parameter/variant choice per window, L5 filter, L6 ranking, all earlier rounds | global DSR with search-level N_eff (L6) | family DSR (reported), PSR (implied by DSR) |
| sealed (>= 2024) | only the set of sealed tests ever run | Holm over all sealed p-values ever (L8) | search trials (they were selected on other data); sealed DSR uses N = sealed tests and is informational |

Other L5 checks (bootstrap CI of the trade mean, random-entry null, cost / execution stress, neighbourhood,
concentration) test *different* failure modes on the same OOS series. They are a conjunction, not stacked
multiplicity corrections; none of them deflates the Sharpe a second time. The N counting no longer adds
`validation_configs` on top of distinct hypotheses (each configuration counts once).

## 5. Evidence class of the validation period

The validation period has been used to select strategies in V1-V9. V10 records data usage per run
(`ledger.data_usage`: SEARCH, VALIDATION_SELECTION, SELECTION_EXPERIMENT, SEALED_FINAL) and marks the L5 evidence
`REUSED_FOR_SELECTION` when an earlier run (or a legacy ledger row) already selected on that symbol and period,
otherwise `FIRST_USE_SELECTION`. Either way it is selection evidence; only the sealed test is clean OOS
(`HISTORICAL_OOS`), and only forward data is prospective.

## 6. Old vs new DSR on the 24 V9 L5 candidates

| | V9 | V10 gate | V10 family (info) |
|---|---|---|---|
| SR0 annualised (median) | 4.70 | 1.35 | lower |
| best candidate (DE40 M15 hybrid_orb_adx_atr, OOS Sharpe 0.90) | 1.0e-18 | 0.117 | 0.514 |
| XAUUSD M15 breakout_prev_day (0.84) | 1.1e-20 | 0.085 | 0.355 |
| candidates >= 0.95 | 0 | 0 | 0 |

The new values are informative probabilities instead of numerical zeros, and they still reject every V9 candidate:
an OOS Sharpe below 1 over six years after ~1,500 search trials is not distinguishable from the best of many
skill-less trials. Between-candidate redundancy among the 24 L5 series is small (effective N 22 of 24, mean |rho|
0.06). Pre-V10 rows have no within-candidate series, so for the V9 comparison N_eff = N raw (conservative).

## 7. Limitations

* Effective N between candidates is only measured for candidates that reached L3 in V10 runs; others inherit a ratio.
* `V_null` assumes the selected series' sampling variance; regime changes are not modelled.
* Trials run outside the app (manual research) are not in the ledger and are not counted.
* Optuna is not installed: no TPE-vs-random comparison was made (`NOT_AVAILABLE`); the optimiser is successive halving
  over seeded random samples.
