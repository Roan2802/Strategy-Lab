# Bid/Ask execution validation

Updated 2026-11-07. Status: **PARTIAL — quote-side stored-intent replay and two explicit signal adapters are tested; arbitrary strategy coverage and broker accounting are not validated.** Historical prices in these checks came only from `C:\AI\Dukascopy_data`. No live or paper orders were sent.

## Tested

- Independent deterministic tests cover side-aware market/limit/stop fills, long/short exit quote sides, fees per side, tick/contract conversion, lot-step rejection, incomplete Dukascopy quote rows and conservative M1 ambiguity handling. See `EXECUTION_VALIDATION.md` and `evidence/test_outputs/dukascopy_execution_validation.json`.
- The Dashboard distinguishes `JESSE_RESEARCH_PROVISIONAL` from `DUKASCOPY_BIDASK_REPLAY`, verifies quote completeness and warns when contract assumptions fail. UI evidence is `evidence/test_outputs/dukascopy_execution_ui_safety_20261106.json`.
- Current bid and ask source hashes are captured in each run manifest. The XAUUSD manifest identifies the source CSVs under the approved root.
- `StatefulRsiPending` now declares a causal Ask-close RSI input and reuses Jesse's sequential RSI kernel over one precomputed series. A rerun of Duka XAUUSD M1 run `894a8f38-7d56-4056-b7bc-b1ddccd31b0b` evaluated 1,380 trading candles after 210 warmup candles: 1,378/1,380 saved decisions matched, with 2 quote-side differences and 10 modeled trades. The decision counts and trade count match the earlier adapter run. Evidence: `evidence/test_outputs/stateful_bidask_rsi_precompute_dukascopy_20261107.json`.
- The generic callback now receives a lazy, read-only causal history prefix. Declared RSI indicators are materialized once, and the callback sees only the value for the current candle; this removes repeated full-prefix copying and RSI recomputation for the tested strategy.

## Not validated

- Quote-side signal recomputation is available for `LabBreakout` and `StatefulRsiPending`. Arbitrary Python strategies without an explicit `strategy_lab_quote_signal` callback remain on saved-order replay; their quote-side signal behavior is **NOT TESTED**.
- Contract size, tick value, volume step, commission, slippage, conversion and margin are user-declared research inputs, not verified broker specifications.
- OHLC cannot prove intrabar event order or tick-level fills; gap policy is a model. Broker/MT5 parity is unproven.
- Partial closes, break-even and trailing-stop transitions are not established for arbitrary strategies in the generic quote-side engine.

Do not interpret this report as an instrument's broker-accurate profit estimate. Additional strategy adapters and broker/accounting validation remain open.

## Generic callback index and current-code Duka run — 2026-11-07

The first current-code live Bid/Ask replay exposed that `strategy_lab_quote_signal` received the absolute candle-array index, including warm-up candles. Jesse's strategy index starts at zero on the first trading candle, so scheduled entries/cooldowns could shift. The callback now receives `index` relative to the trading range and a separate `history_index` for its warm-up-inclusive causal history; close state uses the same strategy index. Regression tests check the warm-up offset, last-close state, market orders without a planned price, and invalid order declarations.

The current Strategy Lab run `fa440326-5b10-4617-ad67-e104aba936ce` was replayed again through the live API: 1,380 trading candles after 210 warm-up candles, 1,379 saved decisions matched and 1 differed. The remaining difference is the strategy's explicit Ask-close RSI evaluation crossing its threshold while the original Jesse run used Bid candles. The quote model produced 9 trades; all contract values were illustrative and unverified, and M1 tick order remains unknown. Bid and Ask source SHA-256 values were read back unchanged. Evidence: `evidence/test_outputs/stateful_bidask_quote_index_regression_20261107.json`.
