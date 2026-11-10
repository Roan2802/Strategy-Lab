# Optimizer diagnostics (Dukascopy only)

## Finding

The earlier Jesse `research.optimize()` adapter interpreted `trials` per hyperparameter. The UI request for five trials across nine parameters therefore ran 45 combinations. Jesse also returned only positive candidates, hiding completed negative trials. On XAUUSD M1 Dukascopy data for Sep 2026, all 45 monthly trials completed but `best_trials` was empty. That means the first run did not provide a usable winning configuration.

The adapter now treats `trials` as the total requested count, persists every trial including negative scores and errors, records train/test metrics, uses a deterministic seed, exposes random/grid/TPE, and resumes random/grid runs from saved trial records. More than one worker is rejected because Jesse's backtest state is process-global. TPE resume is rejected because the Optuna sampler state is not persisted.

## Duka UI evidence

All runs below use the same selected XAUUSD M1 window on 2026-09-14 and the source root in their manifests is `C:\AI\Dukascopy_data`.

| Method | Run | Requested / completed | Outcome |
|---|---|---:|---|
| Random, seed 73 | `214fa0af-45f4-4951-bbb9-2b7d0c15c3d4` | 3 / 3 | Completed; all scores retained, including negative scores |
| Random repeat, seed 73 | `8fff04ec-c920-462b-85e4-165b858073da` | 3 / 3 | Same parameters and scores as the first run |
| TPE, seed 73 | `cd9f015a-521d-4ea2-988e-fc0d63838022` | 1 / 1 | Completed and recorded |
| Grid, seed 73 | `17a8ec11-9739-46a2-b5f8-c660be5a2f25` | 2 / 2 | Completed; both tested configurations recorded |
| Resume random checkpoint | `6b388cd5-97b2-4acb-bcb3-89f87fd63d09` | 5 total / 5 | Continued from `8fff04ec-c920-462b-85e4-165b858073da` |

Every recorded trial score in these smoke runs was negative. The controls prove bounded execution, score/error visibility, deterministic random sampling, TPE/grid dispatch, and supported resume. They do **not** establish profitable parameters or out-of-sample quality. The dashboard's full September 45-trial run remains a valid negative result, not a successful optimization result.

Input validation was exercised in the UI: 101 requested trials and `ema_period=500` were rejected; valid runs still worked afterwards. A saved preset survived reload and restored its parameter values. A selected trial's parameters could be loaded back into the controls.

The highest-scoring trial in the three-trial Random run (score `-6.5916192585`; still negative) was submitted as an ordinary backtest with the same Sep 1–30 Duka window and optimizer account settings (USD 100,000 starting balance, Futures, leverage 1, fee 0.0006, warm-up 210). Run `f9778be2-2f89-4aaf-a916-cb9c06c67b29` used the exact saved parameter dictionary and produced 28 trades, 30,546 events, net `-573.7105581521494`. An identical repeat, `bc3cede8-dc2d-42bc-b762-339f45f9fe9a`, reproduced the parameters, trade/event counts and net result exactly. Both manifests identify the Dukascopy source root. Reproducibility assertions are saved in `evidence/test_outputs/optimizer_trial_rerun_validation.json`. A mismatched Spot/10,000-balance attempt was correctly rejected (shorts unsupported in Spot; then Futures margin insufficient); using the optimizer's matching account configuration passed.

## Remaining limits

No parallel-worker scaling, TPE resume, multi-objective search, walk-forward evaluation, multi-symbol portfolio search, or holdout performance claim was established. All optimization prices were Duka-only; no synthetic performance dataset was used.

## UI-redesign note (2026-11-05)

The optimizer controls remain wired to the existing API. The Simple/Advanced toggle hides advanced optimizer controls in Simple mode. Financial metrics in the result cards are explicitly labeled **voorlopig**. Full browser optimizer interaction and optimizer-results screenshots were not captured in this pass.

## Redesign UI optimizer smoke test (2026-11-05)

A single random trial on the Dukascopy XAUUSD M1 day completed under the futures account model with score 0. A spot-account trial whose sampled `order_mode` was short failed with `InvalidStrategy: should_short cannot be True if the exchange type is "spot".` The completed trial appeared in the new Optimization tab. Applying its descriptive short-mode option and rerunning produced `order_mode=2` in the saved Jesse manifest. No profitable result is claimed.

## Toolbar optimizer usability regression — 2026-11-06

Toolbar optimization run `24c9e4d5-eeb9-4f33-930a-75a65a29e686` exercised the simple-mode action on Dukascopy XAUUSD M1, 2026-09-14. The requested and settled count was 5; one valid trial completed with score 0 and four trials failed (one short `order_mode=2` under Spot, three `InsufficientBalance` candidates). All trial records were retained and visible in the Optimization tab. The run manifest identifies only `C:\AI\Dukascopy_data` Bid/Ask files.

The toolbar status now distinguishes settled trials, valid results and errors (`5/5 trials · 1 geldige resultaten · 4 fouten`). This makes the failed batch visible without treating it as five successful candidates. The underlying issue remains: random search can sample strategy/account-incompatible and balance-infeasible inputs; the UI does not yet constrain those combinations. No profitable result is claimed.

## Zoekruimteweergave — 2026-11-06

The advanced optimizer settings now summarize the selected strategy's actual hyperparameter domains, including categorical choices, min/max, units and effective step behavior, plus requested trial count, method, objective, seed, selected date interval and Jesse's 80/20 candle-count train/test split. The test slice is not a separately selected calendar interval. Random/TPE have no finite combination count for the current continuous float parameters; repeat samples are possible. Grid uses declared steps or parameter endpoints when no step is declared. LabBreakout's full Futures grid is 768 combinations; the Spot-feasible grid is 512 after excluding short mode 2. The UI warns when the requested trial count exceeds the current effective grid. This is a display of the current runner behavior.

## Account-aware categorical feasibility — 2026-11-06

LabBreakout parameter metadata now declares allowed `order_mode` categories by account. The UI removes Spot-incompatible short mode 2 from the displayed/estimated search domain. The Jesse runner applies the same allowed-category filter before Random, Grid, or TPE samples a trial and records excluded choices in `optimization.preflight_excluded_options`. Unknown strategies without declared constraints retain their declared domains. This deliberately does not guess affordability or margin from a `position_size` parameter: Jesse quantity, symbol contract, leverage, and historical equity make a generic safe static limit unsupported without explicit contract metadata.

Actual Duka XAUUSD M1 Grid test, 2026-09-14, Spot, seed 7332, 2 trials, recovery-factor objective: run `1f235b89-b3c7-4f16-9223-41ac8ba2e2b5`. The preflight record excluded only `order_mode=2`; both executed trials used legal long modes, completed, and retained negative training P&L (`-0.3163031291`) and negative scores (`-1.0000000000`). Evidence: `evidence/test_outputs/optimizer_spot_recovery_feasibility_20261106.json`. Both trials and their exact parameters remain in the result. A selected trial from the preceding 2-trial Spot Grid run was re-run as a backtest with exact parameter dictionary and matching Dukascopy dataset digest; evidence: `evidence/test_outputs/optimizer_selected_trial_rerun_20261106.json`.

A separate Duka XAUUSD M1 Spot TPE smoke run (seed 7333, one trial) also recorded `preflight_excluded_options.order_mode.excluded=[2]`; the sampled legal mode 1 completed with a negative score. Evidence: `evidence/test_outputs/optimizer_spot_tpe_feasibility_20261106.json`. Random uses the same prefiltered specifications but was not rerun after this metadata addition.

Selecting a saved optimizer run after page reload previously showed an empty Optimization tab. `loadSelectedRun()` now restores stored trial cards and activates that tab; a browser reload/selection verified both saved trials and their negative results. Screenshot: `evidence/screenshots/optimizer-negative-trials.png`.

Still open: margin-aware sampling, sortable trial table, sensitivity/heatmap views, and TPE sampler-state resume. Insufficient margin remains a runtime trial error when no explicit supported margin model is available.

## User-defined search space — 2026-11-06

The advanced dashboard now exposes an enabled toggle for each parameter, numeric min/max/step inputs, and per-category checkboxes. Disabling a parameter fixes it to the current strategy value. Numeric domains are narrowed inside declared strategy metadata; unsupported bounds, fractional integer controls, empty category sets, unknown or duplicate names, and Spot-incompatible fixed categories fail at API preflight before data is loaded. Effective domains and account-excluded categories are persisted in both run manifest and optimizer result. LabBreakout Spot mode 2 is never sampled.

ChatGPT IAB used the dashboard on isolated local port 8768 for Duka XAUUSD M1 on 2026-09-14, Random/seed 42/one trial. The user-set domain was `entry_interval=60..120 step 60`; `order_mode=0`; seven remaining inputs were fixed, including `position_size=0.01`. Run `f06fb1b8-e636-4ed2-8968-c4d3ba36e828` completed 1/1 with zero errors; the manifest records all controls and normalized specs, and its trial parameter dictionary stayed within them. Spot short mode 2 was excluded. Training net was `-0.5177578460`; test net was `-0.0415438282`; the Sharpe objective was null and therefore stored score 0. This is a negative research result, not a profitability claim. Evidence: `evidence/test_outputs/optimizer_user_search_space_20261106.json`.

The first UI probe (`f1478661-8e05-42e0-9bd4-7cc1951b670f`) reached the pre-existing API process on port 8767, whose in-memory code predated this patch. Its null-space manifest and out-of-domain sample are invalidated and excluded from acceptance evidence. After restarting the app through `desktop-launcher.ps1`, port 8767 returned healthy Duka-only status and rejected an expanded entry-interval domain with HTTP 422 before data loading. `FEATURE_MATRIX.csv` documents this process-restart caveat.

## Configurable chronological split — 2026-11-06

The Training candles (%) control configures a 50–90% chronological split. ChatGPT IAB used the current-code local server on port 8768 for XAUUSD M1, 2026-09-14. Bounded Random run `34cedb29-a799-4ddd-afb2-a51810027b96` completed 1/1 with 70% training / 30% holdout, 966/414 candles, and only `entry_interval=60..120 step 60` enabled; all other parameters were fixed to displayed values. It recorded 8 losing training trades and 0 holdout trades, so no holdout-performance claim is made. UI, manifest and result metadata agree.

The preceding broad-space run `523a4a90-3ba4-4180-965c-f0cf392241a3` used the same split but sampled an unaffordable position size and retained an `InsufficientBalance` error. This remains evidence of unresolved generic margin feasibility. The earlier stale-server run `a4ae3f07-d946-429c-b5e1-8a17d9ac9a07` used 80/20 despite the browser plan displaying 70/30; it is invalid for validating the requested split. Evidence: `evidence/test_outputs/optimizer_train_holdout_20261106.json`.

## Insufficient balance error retention — 2026-11-06

A current-code Duka-only Spot trial (`2762d81c-b923-498b-b210-337a1f646b08`) deliberately sampled an allowed `position_size` domain of 9–10 with USD 10,000 balance. The sampled candidate required `39,180.0716` USD and Jesse raised `InsufficientBalance`. The trial remains visible with exact parameters and full error cause (`status=ERROR`); the optimizer does not convert it to a completed losing strategy or silently drop it. Spot short mode was still prefiltered. This validates runtime classification and auditability, not the missing preflight margin estimator. Evidence: `evidence/test_outputs/optimizer_insufficient_balance_20261106.json`.


## Optimizer UI regression — 2026-11-06

Chrome browser automation exercised the actual dashboard renderer against existing local run `6b388cd5-97b2-4acb-bcb3-89f87fd63d09`; its manifest and export identify only `C:\AI\Dukascopy_data`. The five saved trials sorted numerically by descending training score. The heatmap updated its X/Y axis labels after changing both parameter selectors; the sensitivity plot showed five evaluated points with labeled axes. The UI downloaded a JSON export with run ID, dataset SHA-256, and all five trials. There were no browser console/page errors and no new backtest or optimizer run. Full machine-readable evidence: `evidence/test_outputs/optimizer_analysis_ui_20261106.json`; screenshot: `evidence/screenshots/optimizer-analysis-20261106.png`.

This browser check exposed and fixed Plotly axis titles that had been passed in an unsupported string form. It does not implement TPE sampler-state resume or generic margin preflight. Scores are training-set diagnostics only; no positive profitability conclusion is made.

## TPE resume guard regression — 2026-11-06

Added `test_tpe_resume_is_rejected_before_creating_a_new_run`. It verifies the API returns HTTP 422 with the explicit “Optuna study is not persisted” reason before writing new request artifacts or invoking the Jesse runner. The test uses synthetic fixture candles only to reach the guard; it does not run a market backtest. Evidence: `evidence/test_outputs/tpe_resume_guard_20261106.json`. This confirms the limitation remains explicitly unsupported; it does not make TPE resume available.


## Trial outcomes and margin-model boundary — 2026-11-06

Current runner evidence:

- Spot, run `b6e7265c-57d8-45e5-b44f-3aabbb3ef0f9`: one XAUUSD M1 trial with `position_size=9.0`, USD 10,000, Spot. Jesse failed the first historical order during TRAINING: `InsufficientBalance`, requested USD 39,112.092. Stored as `INSUFFICIENT_SPOT_BALANCE_AT_ORDER` with exact parameters, phase and error; excluded Spot short mode is recorded.
- Futures, run `041e502b-7a2e-4c74-b6a1-665c39096c2c`: one XAUUSD M1 trial with `position_size=10.0`, USD 10,000, leverage 1. Jesse failed during TRAINING with `InsufficientMargin`, order value USD 43,458 and available margin USD 10,000. Stored as `INSUFFICIENT_FUTURES_MARGIN_AT_ORDER` with exact parameters, phase and error.
- Valid but losing Spot trial, run `8097663c-0a21-4452-bdc1-4d9189bef6ac`: one legal mode-0 trial, 51 losing training trades, net -2.7276252189768; 10 losing holdout trades, net -0.49562355451359996; recovery-factor score -0.9999999999959528. Kept as `COMPLETED_NEGATIVE` and included in trial results.
- Companion Sharpe trial `525b7e0d-483c-48b2-ba9e-bdb3f3264dbc` has negative train/holdout P&L while Sharpe is null and its score is 0; the outcome still correctly follows net P&L, rather than calling it a positive result.

Jesse source was inspected read-only inside the existing container. Spot debits `abs(qty) * order.price` from settlement balance; Futures compares `abs(qty * price) / leverage` to current available margin. `LabBreakout.position_size` is an uncalibrated Jesse quantity, not a broker lot. Available funds also vary with historical orders and realized P&L. Therefore a general static affordability ceiling based only on starting balance or presumed CFD contract size could reject a combination that later becomes affordable. No such guessed filter was added. The preflight still removes only strategy-declared account-incompatible categories (Spot short mode 2); historic insufficiency is classified and retained at the exact failure phase. This remains a gap against the request for a broader safe margin feasibility filter and broker-specific contract support.

Browser-use verified the result labels for Spot balance failure, Futures margin failure and a negative trial at 1905×1080. It also opened Beeld → Kleurthema by hover; Donker/Licht appeared, then Lichtgroen/Lichtroze/Lichtrood under Licht. Selection updated the active theme state. Screenshot: `evidence/screenshots/optimizer-outcome-classification-20261106.png`. Full run/source summary: `evidence/test_outputs/optimizer_outcome_classification_20261106.json`; model source excerpts: `evidence/test_outputs/jesse_margin_model_source_20261106.json`.

Regression: `python -m pytest -q strategy_lab/tests` — 70 passed; JS syntax and Python compile checks passed. No live/paper trades; all historical price inputs were Dukascopy only.
