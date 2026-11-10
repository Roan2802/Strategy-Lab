# V10 UI redesign (Dukascopy Strategy Lab frontend)

Scope: `strategy_lab/static/*` only (index.html, app.js, shell.js, factory.js, workflow.js, shell.css, workflow.js tail). Dutch UI, existing dark and light design tokens, no new main sections. No backend file was changed.

## Navigation

| Section | Subtabs |
| :--- | :--- |
| Home | Overzicht |
| Strategieën | Mijn strategieën, Zelf bouwen, Automatisch ontdekken, Bibliotheek |
| Onderzoeken | Testen, Analyse |
| Portfolio | Forwardtest, Portfolio en risico |
| Data | (one page) |

Removed subtabs: Backtest, Grafiek, Trades, Inspector, Optimalisatie, Validatie, Journaal, Zelf bouwen and Automatische strategieën under Onderzoeken. Every old hash still works through `LEGACY` in `shell.js` (for example `#onderzoeken/validatie` opens Analyse with the validation section open, `#onderzoeken/optimalisatie` opens Testen with the optimizer drawer open, `#onderzoeken/automatisch` opens `#strategieen/automatisch`, bare `#validate`, `#optimize`, `#discover`, `#backtest`, `#builder`, `#strategies`). A stored `labLastView` with an old route is redirected the same way.

The 7-step route stepper is no longer permanently visible. It sits behind the small "Onderzoeksroute" toggle on the Onderzoeken subnav (state in `localStorage.labRouteOpen`). The interactive onboarding stays under the Help (?) menu.

## Strategieën

- Mijn strategieën: one compact, searchable, sortable, filterable table. Sources: `/api/lifecycle`, `/api/strategies` (version = sha256, 8 characters), `/api/builder/specs` (market, timeframe), `/api/plugins`, and non-rejected or frozen factory candidates from `/api/factory/runs/*/candidates` (only runs whose counts show surviving candidates are fetched). Columns: Naam, Type / familie, Markt / TF, Versie, Onderzoeksstatus chip. Row buttons: "Onderzoeken" (Jesse and builder strategies open Testen with strategy, market and timeframe preselected; plugins open the plugin panel; candidates open their report in Automatisch ontdekken) and "Test op andere markten". Lifecycle history and the status-transition form moved to a per-row "Historie" link.
- Zelf bouwen: the existing builder view (renamed). Advanced JSON and code were already folded.
- Automatisch ontdekken: `factory.js` mounted here. Default view now shows markets, timeframes, strategy kind, depth, plan and one start button; trade frequency, risk and drawdown moved under Geavanceerd. While running: phase, progress, counters, pause and stop. After the run: compact candidate list first (with an "Andere markten" button), the funnel, family chart and settings folded under "Hoe is dit gefilterd?".
- Candidate report (drawer) has four sections with a jump bar: Prestaties, Stabiliteit, Statistische betrouwbaarheid, Eindbeoordeling.
  - Statistische betrouwbaarheid reads `statistics.dsr` (observed SR, SR0, raw N from `n_trials`, effective N when present, V = `sr_variance_across_trials`, T = `n`, skew and kurtosis when present), `statistics.psr`, PBO (`pbo_result` or `robustness.pbo`), `trade_pnl_ci` and `multiple_testing`. Rows without data are omitted and the section says so; nothing is computed in the browser.
  - Eindbeoordeling groups `quality_checks` into Sterk (PASS), Zwak (FAIL) and Ontbrekend bewijs (INSUFFICIENT_EVIDENCE) and states why or why not forward.
- Bibliotheek (IQ dossiers) is the fourth Strategieën subtab.

## Onderzoeken

- Testen: fixed responsive layout (no drag, no resize). A compact top bar (strategy, market, timeframe, period, primary "Backtest uitvoeren", secondary "Optimaliseren", text link "Instellingen"), the large chart (entries, exits, SL, TP, indicators, selected trade drawn as before), a narrow results strip beside it (run picker, evidence banner, key metrics, "Volledige analyse" and "Test op andere markten") and the trade history under the chart. Settings, the optimizer (settings, start button and results) and the decision log (opened from a trade or candle click) are side drawers; Escape or the scrim closes them. Below 1100 px the blocks stack in one column.
- Analyse: run picker (mirrors the Testen run list), exactly four KPIs (Netto rendement, Max drawdown, Profit Factor, Recovery Factor), then sections Prestaties (run interpretation, analytics charts including equity, yearly and drawdown, a win/loss histogram), Validatie en robuustheid (the existing validation form, jobs, history and `renderValidation` result with cost stress, walk-forward, Monte Carlo, overfitting and verdict), Vergelijken (existing multi-run compare, overlaid equity curves built from run trades, and the latest multi-market result) and Details (run and error logs; the advanced statistic picker stays in the Testen results strip).

## Draggable widgets removed

`wireLayout`, layout presets, drag handles, resize grips, the layout editor, layout profile select, "Widgets" submenu, "Layout bewerken" and chart-focus are removed from `app.js` and `index.html` (no `data-panel` attributes remain). Old `strategyLabLayout*` localStorage keys are untouched and unused.

## Multi-market drawer

Opened from Mijn strategieën, Testen, Analyse and candidates. Markets from `GET /api/multimarket/markets` (chips, "Alle" and "Geen"; markets without local data are disabled), timeframes M5 to H4, optional period and re-optimise toggle under Geavanceerd. Start: `POST /api/multimarket/runs` with `{strategy, markets ('all' or list), timeframes, start?, end?, reoptimise}`; polls `GET /api/multimarket/runs/{id}` every 2 s. Result: generalisation verdict (class, explanation, positive markets, effective markets, selection note), the table (trades/week, net %, PF, RF, max DD, Sharpe, Sortino, trades, parameter stability, spread +50% stress, verdict and flags), overlaid equity per cell plus combined curve, correlation matrix and a "Voorlopig" money note when cells are flagged provisional. A 404 gives a calm Dutch notice.

Backend contract found in `multimarket_api.py`: strategy kinds are `builder` (name) and `factory` (candidate_id); `jesse` returns 422 NOT_SUPPORTED. The UI therefore enables the button only for zelfgebouwde regelstrategieën and factory candidates and explains why for Python strategies and plugins.

## Data status

Home shows one line "Marktdata: bijgewerkt / gedeeltelijk achter / fout" with a Details link to `#data`. The Data page lists, per symbol, last candle, expected day, lag, status, reason, next attempt (or backoff), quality and snapshot sha from `GET /api/data/supervisor`, shows scheduler and worker state, and has one button "Nu controleren en synchroniseren" (`POST /api/data/supervisor/check-sync`). Data sources moved to a folded section. If the supervisor endpoint returns 404 the previous `/api/data/monitor` page is used unchanged.

## UX rules applied

No explanatory cards on Testen, one primary action per screen, info icons for explanations, single page scroll, no horizontal overflow at 1280 to 2560 px, dark and light tokens only (no hard-coded colours in new CSS).

## Notes

- Two small fixes outside the redesign: `setTheme` no longer relayouts hidden Plotly charts (it marks the main chart stale instead), which removed a Plotly `_rehover` page error that appeared once the analytics charts moved to the Analyse view; Plotly work in the new code is serialised per element for the same reason.

- `server.py` serves only whitelisted assets (`FRONTEND_ASSETS`), so new CSS and JS were appended to `shell.css` and `workflow.js` instead of new files. Adding `v10.css` and `v10.js` to that whitelist would allow splitting them.
- Verification: `evidence/scripts/ux_e2e.py` (routes and selectors updated, adds library, drawers, legacy redirects, no-drag assertion, multi-market run, Analyse KPI checks). Final results (all 0 failures, 0 JS errors): Chrome 1600x900 dark 49/49 full run, Edge 1920x1080 dark 49/49 full run, quick runs 27/27 at Chrome 1280x720, 1600x900 (light), 2560x1440 and Edge 1920x1080, each in dark and light. Per-run JSON and logs: `evidence/v10/ui/after/e2e-*.json`. The full runs include a real backtest, optimisation, validation and a one-market multi-market run (development period 2022-2023, because the backend seals 2024 onwards). Screenshots: `evidence/v10/ui/before` (old UI) and `evidence/v10/ui/after`.
