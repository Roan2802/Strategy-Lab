# Backtest settings validation — 2026-11-07

**Result: PASS for exercised local settings-to-run wiring.** The tester separates market/backtest settings from strategy inputs. Browser navigation was checked across Overzicht, Invoerparameters, Optimalisatie, Resultaten, Trades, Grafiek, Inspector, and Journaal; `Markt` and `Strategie` remain separate settings tabs.

Existing Duka-only runs verify selected symbol, timeframe, dates, account model, balance, fees, warm-up, and persisted strategy parameters in run manifests. Representative evidence includes backtest `85808ac8-84eb-478e-a405-7ba41a11f56d` (XAUUSD M1, 2026-09-14; 1,380 candles, 13 trades, 1,432 events) and UI-triggered backtest `e3d00425-02c6-4dcd-b66f-5e9429afced0`. Decimal-comma parsing and the Spot/Futures optimizer scenarios are recorded in `TEST_RESULTS.md` and `OPTIMIZER_DIAGNOSTICS.md`.

The current UI-only check started no new run. All market-price inputs for the audit are restricted to `C:\AI\Dukascopy_data`; source files were not edited. Settings are Jesse research-engine inputs, not verified broker/MT5 contract settings. Bid/Ask M1 bars do not resolve intrabar tick ordering, and commission, margin, swaps, slippage, and fills are not proven to match a broker.
