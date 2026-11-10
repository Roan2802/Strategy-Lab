# Factory final validation (V10.1, 2026-11-10)

Evidence: `evidence/v101/stats/` (`real_run1_*`, `real_run2_*`, `real_run_summary.py`, calibration files).
Tests: `strategy_lab/tests/test_v101_stats.py` (35 tests). Methodology: `DSR_FINAL_METHODOLOGY.md`,
`STATISTICAL_CALIBRATION_REPORT.md`, `FACTORY_PROTOCOL.md` section 8.

## A. Controlled-signal tests (synthetic, isolated tmp vaults/ledgers; never the real sealed data)

| requirement | test | result |
|---|---|---|
| meaningless signals rejected / not promoted | `test_meaningless_signals_are_not_promoted` (20 searches of 50 noise trials) + calibration S1/S2/S3 nulls | 0/20 pass; FPR <= 1.3% in all calibration nulls with the gate |
| known injected edge detected with enough data | `test_known_injected_edge_detected_with_enough_data` (SR 3 ann, 6 y, N 50) | >= 8/10 (passes); calibration: SR 2 ann, 6 y, N 10: 94.5% |
| weak signal -> uncertainty, not REJECTED | `test_weak_signal_is_uncertainty_not_rejection` | PF 1.10 with CI [0.95, 1.30], RF and expectancy CI incl. 0 -> INSUFFICIENT_EVIDENCE; PF CI entirely < 1.2 -> FAIL; DSR < 0.95 with SR > 0 -> INSUFFICIENT_EVIDENCE |
| small sample -> INSUFFICIENT_EVIDENCE, not loss | `test_small_sample_is_insufficient_not_loss` | 12 losing trades -> INSUFFICIENT_EVIDENCE; 80 losing trades -> FAIL |
| single-window rule | `test_single_window_rule_needs_enough_windows` | < 4 windows -> INSUFFICIENT_EVIDENCE, >= 4 -> FAIL |
| no double correction | `test_no_double_correction_gate_uses_global_record_only` | gate value = global record; family value reported only |
| reproducible with seeds | `test_seeded_results_are_reproducible`, calibration seeds = CRC32(cell) | identical records |
| dual-positive rule | `test_dual_positive_gate` (6 cases), `test_l6_refuses_to_freeze_when_development_negative` | one positive + one negative never PASS; L6 does not freeze, outcome REJECTED, `is_candidate` false |
| outcome mapping old/new records | `test_outcome_mapping_old_and_new_records` (10 cases), duplicates | ERROR -> TECHNICAL_BLOCKED, data gaps -> TECHNICAL_BLOCKED, look-ahead -> REJECTED/technisch, old L6 DSR rejections -> INSUFFICIENT_EVIDENCE |

## B. Real Dukascopy development-data runs via the API

Request (both runs): `POST /api/factory/runs` markets XAUUSD, US30, DE40, EURUSD, USDJPY; M15/H1/H4; profile
`uitgebreid`; quality `regelmatig`; `advanced = {max_minutes: 45, final_test: false, seed}`; workers adaptive
(plan: 22). **`final_test: false`**: the sealed 2024+ period was not touched (0 HOLDOUT ledger rows); the final test
of a validation exercise would consume the holdout. Therefore "approved" (L7/L8) is 0 by construction.

**Run 1 (`fr_20261110_192949_b91ae5`, seed 101) exposed a pipeline bug**: data preparation of 15 markets took
longer than the 20 s supervisor staleness limit without a heartbeat, the API started a second (and third)
supervisor, the old one kept running, and a supervisor whose own pool was idle declared the run done while 7
candidates were still in L1/L5 ("Klaar" logged three times). Fixed in `factory_worker.py`: heartbeat thread for
long steps, a supervisor that lost its claim raises `Superseded`, kills its pool and exits (test
`test_superseded_supervisor_stops`), and `execute` never finishes while tasks are still running in a live worker
of another process. Run 1 results are kept as evidence but are incomplete.

**Run 2 (`fr_20261110_193539_c1af37`, seed 102, after the fix)**: 19:35:40-19:38:49 UTC (3.1 min), heartbeat fresh
throughout, 0 errors, 0 candidates left in progress.

| funnel | run 2 | run 1 (incomplete) |
|---|---|---|
| generated | 370 | 336 |
| duplicates (signature already in the ledger: earlier runs/rounds) | 130 | 96 |
| L1 technical check: pass / rejected | 233 / 7 | 238 / 2 |
| - of which TECHNICAL_BLOCKED (volume proxy constant in Dukascopy data) | 4 | 1 |
| - of which REJECTED/technisch (long+short on the same bar, unreachable target) | 3 | 1 |
| L2 quick test: pass / rejected | 44 / 189 | 40 / 198 |
| L3 development backtest: pass / rejected | 22 / 22 | 19 / 21 |
| L4 optimisation (random search + successive halving): pass / rejected | 10 / 12 | 13 / 6 |
| L5 nested walk-forward + cost/stability/robustness + DSR: pass / rejected | 0 / 10 | 0 / 12 |
| L6 frozen / L7-L8 approved | 0 / 0 (final test off) | 0 / 0 |
| outcomes | REJECTED 366 (statistisch 233, technisch 133 incl. 130 duplicates), TECHNICAL_BLOCKED 4, INSUFFICIENT_EVIDENCE 0, PASSED 0 | REJECTED 329, IN_PROGRESS 7 (bug) |

Main rejection reasons run 2: L2 no preliminary edge after costs 99, too few trades for the profile 53, no trades in
the representative windows 50, result depends on one trade 33; L3 losing after costs 19; L4 PF / frequency too low
6 / 6; L5 single-window dependence 7 (all with 11-12 windows, so decidable), +50% spread loss 6, overfit flags 5,
scorecard REJECT 4, neighbourhood 0% profitable 3, development not positive 2.

L5 candidates run 2 (OOS = rolling nested WF 2018-2023, all `REUSED_FOR_SELECTION`: the validation period was used
by earlier runs incl. run 1):

| market | family | OOS trades | OOS PF | OOS net | dev net (frozen config) | dual | OOS SR ann | NC-DSR | paper DSR | SR0 ann NC / paper |
|---|---|---|---|---|---|---|---|---|---|---|
| DE40 M15 | hybrid_range_break_volc | 853 | 1.13 | 3,046 | -1,105 | FAIL | 0.51 | 0.0057 | 0.0023 | 1.49 / 1.61 |
| DE40 H1 | breakout_donchian | 955 | 1.05 | 1,596 | 1,637 | PASS | 0.30 | 0.0012 | 0.0005 | 1.57 / 1.60 |
| XAUUSD M15 | trend_momentum_persistence | 421 | 1.03 | 386 | -5,566 | FAIL | 0.12 | 0.0003 | 0.0001 | 1.50 / 1.61 |
| XAUUSD H1 | stat_autocorr_regime | 334 | 1.01 | 138 | 6,385 | PASS | 0.07 | 0.0002 | 0.0001 | 1.53 / 1.61 |
| US30 H1 | stat_autocorr_regime | 365 | 1.00 | 3 | 829 | PASS | 0.03 | 0.0001 | 0.0000 | 1.49 / 1.61 |
| 5 others | - | 797-1441 | 0.88-0.99 | negative | - | FAIL | < 0 | 0 | 0 | ~1.5 / 1.6 |

Search-level multiple testing at L5: raw N 18,111-18,707 trials (all runs and rounds in the ledger), effective N
5,556-5,718, T 1,552-1,595 days, T_eff 1,409-1,595. The dual-positive rule fired on two OOS-positive candidates
whose frozen configuration lost money on the development period (DE40 M15, XAUUSD M15). No candidate reached the
DSR gate at L6; the highest NC-DSR (0.0057) is far from 0.95 and, with a positive Sharpe, would have been
INSUFFICIENT_EVIDENCE at L6 had it passed L5. Every L5 rejection here has >= 334 trades and >= 11 windows, so
REJECTED (not INSUFFICIENT_EVIDENCE) is the correct class.

## C. Integrity

| check | evidence |
|---|---|
| ledger completeness | run 2: 1,467 ledger rows (every stage of every candidate + every optimisation trial + perturbation neighbours), 918 trials in the store, 549 stage results; run 1: 1,607 / 844 / 549 |
| repeated rounds traceable | 130 duplicates in run 2 recognised from the ledger (signatures tested in earlier runs); L5 `evidence_class_validation = REUSED_FOR_SELECTION` with `prior_runs` listing run 1 and older runs; test `test_repeated_rounds_on_validation_period_are_recognised` |
| holdout reuse recognised | existing `test_retest_after_sealed_is_blocked_and_reused_holdout` (REUSED_HOLDOUT; outcome INSUFFICIENT_EVIDENCE, missing "schone out-of-sample data") |
| frozen strategy immutable | content-addressed `frz_` id; refreeze of the same record gives the same id, a changed parameter a new id, stored record unchanged (`test_frozen_record_is_content_addressed_and_immutable`); vault is append-only |
| no future candles | L1 causality check (truncation test) and look-ahead specs rejected (`test_leaky_spec_rejected_at_l1`, `test_leaky_signal_function_rejected_at_l1`); search data never holds sealed bars (`test_search_data_never_holds_sealed_bars`, `test_sealed_period_never_read_by_search_and_validation_stages`) |

## D. Optimiser

Optuna: **not installed** (`optuna_status() = NOT_AVAILABLE`); no TPE comparison was made and nothing was
installed. The optimiser stays seeded random search + successive halving. Verified:
`test_random_search_is_seeded_unique_and_inside_space` (same seed -> same configurations, defaults first, unique,
inside bounds, other seed differs) and `test_successive_halving_keeps_top_ceil_n_over_eta` (each rung keeps exactly
ceil(n/eta) configurations, exactly the best ones of the previous rung, the reported best is the top of the last
rung, optimizer `random_successive_halving`).

## E. Limitations

* Both real runs found no promotable strategy; the positive path L6-L8 is exercised only with fixtures (V10 tests).
* Run 1 is incomplete (supervisor bug, fixed); its counts are reported but not used for conclusions.
* Uncertain contract specifications are not yet detected automatically as a decisive factor (they are listed as a
  warning on every candidate); invalid instrument data at L1 is TECHNICAL_BLOCKED.
