# Multi-market validation (V10)

Code: `strategy_lab/research/instruments.py`, `research/multimarket.py`, `strategy_lab/multimarket_api.py`,
contract handling in `research/backtest.py`. Evidence: `evidence/v10/multimarket/real_run.json` (+ `real_run.py`).
Tests: `strategy_lab/tests/test_v10_multimarket.py`.

## Rules of the test
- One strategy with **frozen parameters** (no re-optimisation) on every market x timeframe.
- **Development data only**: range is `max(2004-01-01, first_bar + warm-up) .. 2024-01-01`. Anything reaching the sealed
  period raises `SealedAccessError` (API: HTTP 422). The frames are additionally asserted to end before the seal.
- Every market x timeframe cell is a ledger `BACKTEST` entry (family `multimarket:<spec sha>`, statistics
  `multimarket.selection_experiment=true`). The generalisation class is computed over ALL requested cells; picking the
  best market afterwards does not improve evidence (a single market is capped at `MARKET_SPECIFIC`; fewer than 4 markets
  are capped at `PARTIAL_GENERALISATION`). `generalisation.selection` also reports whether the best cell beats the
  expected maximum Sharpe by chance given the number of cells.
- Parameters that were found on one market (factory candidate) are flagged `ORIGIN_MARKET_IN_SAMPLE` on that market:
  its result is not generalisation evidence.

## Instrument registry (`GET /api/multimarket/markets`)
Per symbol, each field has `value`, `status` (`VERIFIED_FROM_DATA` | `ASSUMED` | `UNKNOWN`) and `note`.
- VERIFIED_FROM_DATA: tick size (quoted decimals; a broker tick may be coarser), data first/last, typical spread
  (median/p90/last 2 years, in price and ticks), observed active UTC hours/weekdays, conversion pair availability.
- ASSUMED: quote currency, contract size, lot step, asset class. UNKNOWN: commission, margin.
- Money results are therefore always `PROVISIONAL` (R-multiples and percentages are more robust than amounts).

## Currency conversion
Account currency is USD. EUR-quoted (DE40), JPY-quoted (USDJPY) and CAD-quoted (USDCAD) instruments are converted with
the real Dukascopy series: DE40 x EURUSD mid, USDJPY x 1/USDJPY mid, USDCAD x 1/USDCAD mid. The rate used is the mid
of the conversion pair at the **open of the bar** being processed (causal): it sizes the position, marks equity and
settles each trade (`run_rules_backtest(..., conversion=series)`). Without a series the run reports
`currency_status=QUOTE_CURRENCY_UNCONVERTED` (amounts in quote currency; cell flagged `PROVISIONAL_CURRENCY`).

DE40 before/after (XAUUSD breakout_prev_day candidate on DE40 H1, 2014-03..2024-01, same signals): net profit
-1157.17 (EUR labelled USD) vs -1159.75 (converted); max drawdown 1828.45 vs 1812.02; mean lot size 0.368 vs 0.318.
Because sizing is percent-of-equity, percentages barely move; the bug distorted lot sizes (about 16% too large),
the per-lot commission weight and every absolute amount, and would have been far larger had a fixed lot been used.
EURUSD open ranged 0.954..1.396 (mean 1.144) over that window.

## Risk normalisation (`risk_model`)
- `native` (default): the spec's own scale-free stop (ATR / swing / percent) is kept; sizing is `risk.percent` of equity
  per trade (the backtester's native percent-of-equity model). A `points` stop is rescaled by relative price level
  (`distance * median_price(target) / median_price(origin)`); raw fixed distances are not comparable across instruments.
- `pct_equity_atr`: replace the stop with `atr_mult x ATR(atr_period)` (default 2 x ATR(14)). This changes the strategy.
- `pct_stop`: stop = `percent_stop`% of price.
- `fixed_lot`: NOT_SUPPORTED (the rule backtester sizes by risk); refused rather than silently substituted.
- Jesse strategies: NOT_SUPPORTED (no cheap adapter into the rule backtester).

## API
- `GET /api/multimarket/markets[?refresh=true]` -> `{account_currency, markets:[registry...], legend}`.
- `POST /api/multimarket/runs` `{strategy:{kind:'builder'|'factory'|'jesse', name|candidate_id|spec, params?},
  markets:[...]|'all', timeframes:[], start?, end?, reoptimise:false, risk_model, risk_percent?, capital, stability}`
  -> 202 `{run_id, job_id}`.
- `GET /api/multimarket/runs/{id}` -> `{status, progress, config, results:[{symbol,timeframe,key,metrics,cost_stress,
  stability,validation_status,edge_positive,provisional,flags,...}], combined:{equity,by_timeframe,...},
  equities:{"SYM|TF":[{time,equity}]}, generalisation:{class,explanation_nl,n_markets_positive,effective_markets,
  correlation_matrix,regime_dependence,selection}, ledger_ids}`; partial results while running; finished runs persist in
  `strategy_lab/data_cache/multimarket_runs/`.
- `GET /api/multimarket/runs` (list), `POST /api/multimarket/runs/{id}/cancel`.

## Classification
`NO_EDGE` (no market positive) / `MARKET_SPECIFIC` (<=1 positive market, <25% positive, or effective positive markets
< 1.5) / `PARTIAL_GENERALISATION` / `BROAD_GENERALISATION` (>=4 markets tested, >=60% and >=4 positive, effective
positive markets >= 3, positive combined curve). A cell has a positive edge with >= 30 trades, net > 0, PF > 1.05 and
net > 0 at spread +50%. Effective markets = participation ratio of the correlation-matrix eigenvalues of daily
mark-to-market returns (sparse trading days lower measured correlation, so this is an upper estimate).
`validation_status` per cell is a light check (spread +50%, +-1 step parameter neighbours), not factory validation.
Combined equity = equal capital split over all cells (cells without data count as cash).

## Re-optimisation (`reoptimise=true`)
A separate run with its own id and ledger family suffix `|reopt`, flagged `NEW_HYPOTHESIS`: random parameter combos on the
first 60% of each market's dev window, best by net profit (>= 30 trades), reported on the untouched last 40%. Every combo is
a ledger trial. No generalisation class (`NOT_COMPARABLE`).

## Real run (XAUUSD breakout_prev_day candidate e0a55476747937e4, frozen params, 11 markets x M15+H1, 2004..2024-01)
Class **MARKET_SPECIFIC**: only XAUUSD (the origin market, in-sample) is positive after cost stress; all other 10 markets
fail or are weak (spread +50% turns them negative); effective markets about 9.6 of 11 (daily mark-to-market
correlations are low because the strategy trades about once a week). See `evidence/v10/multimarket/real_run.json`.
