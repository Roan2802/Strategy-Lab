# Anti-overfitting validation: V10 benchmark campaign (2026-11-10)

What was tested: does the V10 pipeline (nested walk-forward, robustness, multiple-testing gate) reject strategies
without an edge, find a known edge, and expose overfitting, concentration, cost fragility, correlated variants,
cherry-picking and leakage? Method details are in `DSR_METHODOLOGY_AUDIT.md` and `WALK_FORWARD_VALIDATION.md`.

* Script: `evidence/v10/stats/benchmark.py` (results: `benchmark_results.json`).
* Scenarios A-I run on **synthetic** data with the same statistical modules and thresholds as factory L5/L6
  (`nested_wf`, `robustness`, `effective_trials`, `validation.pbo_cscv`, quality profile *regelmatig*). Only the rule
  engine is replaced by a small vectorised engine (MA cross, breakout, mean reversion on synthetic hourly bid/ask
  bars with Dukascopy-like session/volatility spreads), so hundreds of experiments fit in about 30 minutes. These
  results calibrate the pipeline. They say nothing about any real strategy.
* Scenario J is the real factory run on Dukascopy data through the API (`real_run_summary.json`).
* `final_pass` = every gate a factory candidate needs: walk-forward checks AND evidence verdict
  `SUPPORTED_UNDER_ASSUMPTIONS` AND global DSR >= 0.95.

## Results

| Scen. | Setup | Result |
|---|---|---|
| **A no edge** | 100 independent random walks (8 years hourly, 1.5 bp base spread); per experiment a full search over 3 families and 23 configurations, nested WF picks the best | **false-positive rate 0/100** (Wilson 95% upper bound 3.7%). Each layer on its own: WF checks 2/100, robustness verdict 2/100 (the same 2 experiments, OOS Sharpe 1.40 and 1.06), naive PSR >= 0.95 10/100, global DSR gate 0/100 (highest 0.77). Verdicts: 67 NO_EVIDENCE, 31 POSITIVE_UNCERTAIN, 2 SUPPORTED_UNDER_ASSUMPTIONS |
| **B known edge** | persistent drift (trend strength 0.35), MA cross + breakout search, 20 experiments per length | **detection 0/20 at 3 years, 16/20 at 6 years, 20/20 at 10 years** (median OOS Sharpe 3.5 / 3.2 / 9.3). The 3-year cells fail on the walk-forward checks (too few OOS trades, profit concentrated in one window), not on DSR |
| **C overfit** | best of 200 random MA configurations chosen in-sample on noise (low spread so IS looks good), 20 experiments | median best IS Sharpe 0.54 becomes 0.08 OOS; median PBO 0.51; naive single-test PSR >= 0.95 in 40% of cases, DSR with N = 200 in 0% |
| **D few days** | losing noise strategy plus 3 outsized winning days | flags `profit_in_few_days` (net without the 32 best days = -4,363 for +1,832 total), `unstable_rolling`; verdict POSITIVE_UNCERTAIN, never supported |
| **E costs** | weak drift traded by high-turnover MA crosses at 4 bp spread, 10 experiments | gross (zero spread) OOS positive in 10/10, the same configuration net of costs negative in 9/10; **0/10 pass** (3 NO_EVIDENCE, 7 POSITIVE_UNCERTAIN) |
| **F correlated variants** | 50 near-identical MA variants vs 50 independent random strategies | effective N 9.0 of 50 (mean abs corr 0.70) vs 50.0 of 50 (0.03); SR0 0.93 -> 0.62 annualised (T = 1,500) |
| **G many families** | 10 families x 50 trials; one strong candidate (Sharpe 3.1) and one moderate one (1.8) | strong: family DSR 1.00, global 1.00, PASS. Moderate: **family 0.97 but global 0.88, gate FAIL**: the global scope is the gate |
| **H leakage** | search data with sealed start 2024-01-01 | 3/3 requests that touch the sealed period refused (`SealedAccessError`); search data ends 2023-12-29 23:00; purge drops the train trade whose exit overlaps the test window (1 of 2) |
| **I cross-market cherry-pick** | one strategy on 6 synthetic markets, the best is picked | 6 selection experiments recorded in `data_usage`; the pick's DSR (N = 6) 0.04 < naive PSR 0.30: picking does not improve the evidence |

Freeze, final test and forward paths are tested separately with labelled fixtures, not as performance evidence:
`test_l6_final_global_dsr_gate_freeze_final_and_forward_paths`. The sealed period is never read by search or
validation: `test_sealed_period_never_read_by_search_and_validation_stages` spies on every range the stages request,
for real candidates through L1-L5, including a forced nested-WF L5.

## What the benchmark changed (reported rather than hidden)

1. **Gate V.** With the BLdP cross-trial variance V_paper as gate, scenario B passed the DSR in only 8/20, 8/20 and
   12/20 cells, although the OOS Sharpe was 3-9. V_paper there measures real differences between skilled and
   unskilled variants, not luck. The gate now uses V_null = 1/T_eff (the dispersion of a skill-less trial) and keeps
   N_eff for the multiplicity. Scenario A: the false-positive rate is 0/100 under both choices.
2. **Synthetic slippage** was in price units (0.2% per fill) instead of ticks as in the real engine. Fixed in the
   benchmark engine.
3. **Loss clustering** (runs test) is now informational. Serial dependence is already priced in through T_eff and the
   block bootstrap, and its cost through the drawdown check.
4. **Regime stability**: profit concentrated in one label only counts as fragile when that label is rare (< 30% of
   trades). A trend strategy that earns on trend days shows its mechanism.

B before fixes 2 and 3 (same seeds): detection 0/20, 8/20, 7/20 (`B_before_flag_fixes`). Scenario A ran with the final
code.

## J: real factory run (Dukascopy, via the API)

Run `fr_20261110_141731_16a4ea`. Markets XAUUSD, US30, DE40, EURUSD; M15 + H1; profile *uitgebreid*, quality
*regelmatig*; 320 candidates, 32 trials per candidate, 4 workers, budget 55 min. Actual run time **9 min 51 s**
(14:17:32 to 14:27:23 UTC), no NOT_RUN, 0 errors. Pre-registration sha256 `6778921d51fc…`.

| Stage | In | Pass | Main rejection reasons |
|---|---|---|---|
| generation | 419 | 320 | 99 duplicates of hypotheses tested in earlier rounds |
| L1 technical | 320 | 307 | 9 volume proxy not informative (`data_available`), 4 contradiction (level target never reachable) |
| L2 quick test | 307 | 80 | 177 no preliminary edge after costs, 39 too few trades, 34 result hangs on one trade, 5 no trades |
| L3 development | 80 | 45 | 35 loss-making over the development period after costs |
| L4 optimisation | 45 | 30 | 7 PF too low after optimisation, 7 frequency too low, other |
| L5 nested WF + robustness | 30 | **0** | (several per candidate) scorecard not passed 30, OOS PF < 1.2 29, OOS recovery factor < 1.5 26, OOS result depends on one window 26, overfit signals (WFE / expanding / PBO) 25, losing at +50% spread 18, does not beat the baseline 15, trade-mean bootstrap CI includes 0 14, neighbourhood < 70% profitable 14, profit in a few days 11 |
| L6-L8 | 0 | 0 | - |

L5 detail (30 candidates): 0 passed the walk-forward checks and 0 the robustness verdict; verdicts 24 NO_EVIDENCE and
6 POSITIVE_UNCERTAIN. Best OOS Sharpe was 0.65 annualised (EURUSD H1 `stat_hour_of_day`, 1,241 trades, PF 1.12):
global DSR 0.031, family DSR 0.20, raw N 3,877, effective N 1,882, SR0 1.40 annualised. 28/30 carry evidence class
`REUSED_FOR_SELECTION` (the validation period was already used to select in earlier rounds). Purge per candidate
ranged from 2 to 50 days, derived from holding period and look-back. Promotion: 389 EXPERIMENT and 30
DEVELOPMENT_PASS as the highest rung reached; all 419 end as REJECTED.

Conclusion J: no candidate shows evidence of an edge after costs on 2018-2023 out-of-sample data. That matches V9
(0 of 24). The difference with V9 is that the rejections now name causes (costs, concentration, single window,
WFE) and that the DSR is an informative probability instead of a numerical zero.

## Old vs new DSR (V9 run)

`evidence/v10/stats/v9_dsr_compare.py` / `.json`: the same 24 V9 L5 candidates, ledger as it was at the end of that run.
Median SR0 4.70 -> 1.35 annualised. Best candidate DE40 M15 `hybrid_orb_adx_atr` (OOS Sharpe 0.90): DSR 1.0e-18 ->
0.117 (family 0.51). No candidate reaches 0.95 under either version. Explanation in `DSR_METHODOLOGY_AUDIT.md` §2.

## Open points

* B covers one edge strength. The synthetic drift edge is strong (Sharpe 3-9); detection of realistic edges
  (Sharpe 1-1.5) over 6 years is expected to be low, because SR0 at search-level N is ~1.35-1.4 annualised.
* The 3-year B cells are rejected by the single-window rule and the trade minimum. With 2-4 OOS windows that is
  arguably INSUFFICIENT_EVIDENCE rather than FAIL. Left unchanged.
* In A the robustness layer alone let 2/100 through. The DSR gate is the layer that stops them, so it must stay.
* Optuna is not installed: TPE vs random NOT_AVAILABLE, not compared.
* The synthetic engine fills at bar opens and does not model stops intrabar. The real engine is used in J and in the
  integration tests.
