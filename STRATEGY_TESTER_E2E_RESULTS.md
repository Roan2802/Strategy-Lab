# Strategy Tester end-to-end — 2026-11-07

## Verified flow

A real dashboard backtest completed and its saved manifest/result were read back through the local API. Source root was exclusively `C:\AI\Dukascopy_data`.

- Run: `c2239cb4-4ed9-4e54-a356-8121d850ebff`
- Instrument/timeframe/window: XAUUSD M1, 2026-09-14
- Strategy: LabBreakout; parameters are saved in `strategy_lab/runs/c2239cb4-4ed9-4e54-a356-8121d850ebff/manifest.json`.
- Result: 23 trades, 1472 events, MFE and MAE present for 23/23 trades.
- Jesse research-model net result: -118.0489; this is a plumbing result, not evidence of profitability or MT5 parity.
- Dataset identity: `b681c7c9346663669edc253f897fa4324d5a87517418955b15b301e099b7a179`.

The user-facing dashboard start action and result rendering were separately exercised in the browser; menu backtest and optimize actions were also checked in the earlier UI evidence. The optimizer's 1-trial check completed as an explicit insufficient-balance error, retained in its result table.

Tests on the current build: `python -m pytest strategy_lab/tests -q` — 76 passed; `node --check strategy_lab/static/app.js` — passed; `python -m compileall -q strategy_lab runtime/strategy_lab` — passed.

Evidence: `evidence/test_outputs/strategy_tester_e2e_20261107.json`, `evidence/test_outputs/current_build_validation_20261107.json`. No live/paper trades; no source or original MT5 data changed.
