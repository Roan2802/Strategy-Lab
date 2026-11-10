# Portfolio analysis status

Updated 2026-11-07. Status: **IMPLEMENTED AND TESTED — provisional Dukascopy research-model portfolio only.**

## Available and verified

- Results → Portfolio selects 2–12 saved Dukascopy Bid/Ask runs, uses each run's original starting balance, and shows combined equity, net P&L, drawdown, concurrent losing trades, trade count, and run/strategy/instrument/month/correlation tables.
- Compatibility gates block combinations with incompatible currencies, execution assumptions, or signal-replay modes. Blocked output deliberately has no combined financial metrics.
- Browser automation selected two saved StatefulRsiPending Duka runs and rendered the portfolio dashboard: 19 trades, $20,000 starting capital, provisional net P&L -$34.649 USD, ending equity $19,965.351, and max drawdown $34.649 (0.17%).
- A live API test with mixed signal-recomputed and saved-intent runs returned `COMPATIBILITY_BLOCKED` / `SIGNAL_MODEL_MISMATCH`, `combined_financials_available=false`, and no `net_pnl` field.
- Live API test combining two compatible saved LabBreakout runs returned a provisional result: 20 trades, $20,000 starting capital, net P&L -$999.163786 USD, ending equity $19,000.836214, and max drawdown $999.163786 (4.9958%).
- Automated coverage includes same-currency/mode calculation, mismatch blocking, normalized daily-return correlation, and run discovery metadata. Full Strategy Lab suite: 93 passed.

Evidence: `evidence/test_outputs/portfolio_analysis_dukascopy_20261107.json`, `evidence/test_outputs/portfolio_analysis_compatibility_blocked_20261107.json`, `evidence/test_outputs/portfolio_analysis_browser_20261107.json`, `strategy_lab/tests/test_portfolio.py`.

## Limits

All amounts are provisional Dukascopy Bid/Ask research-model results. Contract settings are user-supplied and not broker-verified; OHLC does not establish tick ordering or broker accounting. A blocked mix is not aggregated. This feature does not claim MT5 CFD parity or profitability.
