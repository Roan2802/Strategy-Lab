# Financial metrics validation — 2026-11-07

**Result: PARTIAL.** Metrics are calculated and displayed for research runs, but are not financially validated against MT5 or a broker. All tested market data came from `C:\AI\Dukascopy_data`; no live/paper trades were submitted.

## Available metrics and evidence

The results UI includes trade count, net result, profit factor, recovery factor, maximum drawdown, win rate, Calmar, Sortino, Sharpe, annualized return, average win/loss, payoff ratio, expectancy, largest win/loss, winning/losing streak, trade frequency, underwater period, and positive/negative months. Hover help marks simulated/accounting-dependent ratios as provisional. Selectable close-to-close analytics include Ulcer/Pain indexes, drawdown durations/frequency, time underwater, rolling 12-month return, and worst month/year. MFE/MAE are raw price excursions derived from saved Duka candles for supported market fills; they are not account-currency P&L.

Formula-level code checks include profit factor = gross winning P&L / absolute gross losing P&L, recovery factor from net return and maximum drawdown, and expectancy = mean closed-trade net P&L. The quote-side replay returns these figures under `DUKASCOPY_BIDASK_REPLAY` with `broker_contract_verified=false` and `tick_order_known=false`. The latest Trade MAE/MFE API evidence is `evidence/test_outputs/mae_mfe_dukascopy_20261107.json`; browser evidence for the metric cards and selectable analytics is linked from `UI_TEST_RESULTS.md`.

## Not validated / unavailable

The Jesse results remain `JESSE_RESEARCH_PROVISIONAL`; quote-side values remain a declared model, not broker accounting. No exact MT5 CFD contract, broker fill/fee/swap parity, tick-level sequence, or independent platform reconciliation was established. Out-of-sample profitability, walk-forward, Monte Carlo, CVaR, risk of ruin, and parameter stability were not validated. Some derived close-to-close statistics omit floating equity and must not be read as intrabar/broker drawdown.

The stale-process caveat above is resolved for the current process: the 2026-11-07 dashboard run `5e51591d-e963-44e4-9fe0-38b7fa36cb8f` was read back from the live API with the `trade_excursions` field present for supported market fills (10 available, 1 unavailable). Runtime/build evidence is `evidence/test_outputs/v7_current_runtime_check_20261107.json` and the Computer Use run evidence is `evidence/test_outputs/cua_dashboard_replay_20261107.json`. These excursions remain raw Dukascopy Bid price units, not account-currency P&L.
