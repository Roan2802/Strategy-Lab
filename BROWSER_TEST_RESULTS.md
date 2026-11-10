# Strategy Lab browser regression

**Browser:** ChatGPT in-app browser. **URL:** `http://127.0.0.1:8767/`. **Date:** 2026-11-05.

The dashboard loaded with the visible banner `Alleen C:\AI\Dukascopy_data`. The current API health response is `status=ok`, `mode=local-only`, and reports that the source directory exists. Current run `85808ac8-84eb-478e-a405-7ba41a11f56d` is visible with 13 trades / 1,432 events and 1,380 XAUUSD M1 candles.

| Check | Result | Evidence |
|---|---|---|
| Symbol/timeframe/strategy selectors | PASS | 11 Duka symbols, M1–D1 controls, LabBreakout and audit strategies visible |
| Inclusive end date and chart load | PASS | 2026-09-14 returned 1,380 candles |
| Backtest and run persistence | PASS | 13 trade rows and run selectable after refresh |
| Trade jump / inspector | PASS | Spring selection moved replay to trade timestamp; chart candle populated Duka quote and conditions |
| Replay cutoff | PASS | 00:00 → 00:01 → 00:00; candle count 1 → 2 → 1; future data excluded |
| Optimization | PASS (execution); no positive result | Random 3, grid 2, TPE 1 trials completed; deterministic rerun matched; random resume reached 5 total |
| Trial parameters reused in normal backtest | PASS | Best-of-three negative-score trial reran twice with exact parameters and identical metrics on its Duka month; see OPTIMIZER_DIAGNOSTICS.md |
| Preset and comparison | PASS | Preset restored after reload; A/B comparison rendered metrics |
| Input validation | PASS | 101 trials and out-of-range EMA were rejected |
| Bid/Ask ledger | PASS (declared assumptions) | UI returned Jesse timestamp repricing and independent Duka OHLC ledger |

The prior probe established the ledger numbers under explicit illustrative assumptions: Jesse repricing `-799.30` and independent OHLC ledger `-791.00`, 13 trades, 3 ambiguous; this is not broker accounting. New screenshot: `evidence/screenshots/dukascopy-xauusd-m1-final-regression.png`.

The in-app browser is left on the live local dashboard. Jesse MCP is not available in this session; the local dashboard/research path was tested instead. No browser action placed live or paper trades.

## 2026-11-06 responsive layout update

In the ChatGPT in-app browser, the ~888 px layout now keeps Settings and Chart side by side and gives the stacked Inspector the full row. Panel ordering was checked in both Inspector-first and `Lab widescreen` layouts; hiding a panel uses a vertical responsive stack. At ~1265 px, all three columns and the saved order were checked. The profile was saved through the local API and appeared in a second IAB tab after reload. Arrow-based reorder is verified; CUA drag-and-drop did not visibly reorder and remains unverified. Chrome/Edge comparison and supported full-dashboard screenshot export remain blocked.

`strategy_lab/static/app.css` cache key: `20261106.5`; `app.js`: `20261106.4`. Data source health remains Dukascopy-only.

## Global toolbar and drag-and-drop follow-up — 2026-11-06

Moved the run selector and refresh action into the global toolbar and added simple-mode Backtest uitvoeren and Optimaliseren actions. The toolbar wraps at a ~903 px IAB viewport without document overflow; CSS and JS asset keys are `20261106.8` and `20261106.7`. The previous fixed-height regression in toolbar `<select>` elements was caught visually and corrected before acceptance.

Replaced unverified native HTML drag with pointer-based drag from dedicated handles. In the live ChatGPT browser, Settings→Chart drag changed panel order visibly; Cancel restored the original order. Results remains anchored below the workspace. The toolbar Backtest button completed Duka run `c2656509-0bfe-4e62-b60d-911e5bbd2e26` (XAUUSD M1, one day, 13 trades / 1,432 events), with a Duka-only source manifest. Full browser matrix in Chrome/Edge, saved full-dashboard screenshot files and Windows scaling tests remain open.

## Optimizer toolbar follow-up — 2026-11-06

The ChatGPT IAB top-bar Optimiseren action completed Duka run `24c9e4d5-eeb9-4f33-930a-75a65a29e686` with 5/5 trials settled, 1 valid score-0 result, and 4 errors. The Optimization tab selected automatically and status clearly distinguished the valid result count from failures. The latest browser-visible status text was `Optimalisatie afgerond · 5/5 trials · 1 geldige resultaten · 4 fouten.` The optimizer sampled a Spot-incompatible short order mode and three sizes above available balance; this sampling issue remains documented in `OPTIMIZER_DIAGNOSTICS.md`.
