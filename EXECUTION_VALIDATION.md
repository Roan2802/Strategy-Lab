# Execution validation (Dukascopy only)

## Scope and inputs

Every strategy-price input in this report came from `C:\AI\Dukascopy_data`. No MT5 history or other feed was read for these checks. The reproducible probe is `evidence/test_outputs/dukascopy_execution_validation.py`; its output records the selected source paths and SHA-256 hashes. The XAUUSD M1 day (2026-09-14 UTC) contains 1,380 paired quote candles and reuses Jesse run `85808ac8-84eb-478e-a405-7ba41a11f56d`.

The independent ledger run uses explicit test assumptions, not broker specifications: USD account, contract size 100, tick size 0.01, quote-to-account conversion 1, lot step 0.01, commission USD 3.50 per lot per side, and zero slippage. Treat its monetary output as a model demonstration only.

## Results

- Jesse research backtest completed on the Duka candles: 13 trades, 1,432 events, net Jesse spot-model result `-70.2517536194`, fees `67.2535536194`.
- Independent Bid/Ask ledger replay produced 13 trades, net `-791.00` under the declared assumptions, profit factor `0.26075`, and max drawdown `0.0791` of the starting balance. It is deliberately not equated with Jesse P&L or MT5.
- Side-aware quote selection passed: long LIMIT fill uses Ask; short LIMIT fill uses Bid. The companion assertion checks the fill timestamp against the Duka candle used.
- Incomplete quotes are rejected from execution: the Duka USATECHIDXUSD probe returns `PROVISIONAL_INCOMPLETE_BIDASK_QUOTES` and reports the skipped row.
- On bars touching both stop and target, the independent OHLC replay takes stop first. M1 OHLC does not establish intrabar event order.

## Limits

Signals and order intent come from the persisted Jesse event stream. This ledger reprices/replays those event timestamps; it does not recalculate spread-sensitive strategy triggers. The Duka sample's resulting Jesse trades are long; the probe asserts a short LIMIT fill but does not validate a completed short position's full P&L lifecycle. Contract size, tick value, swap, margin, currency conversion, and broker fees remain user-configured assumptions. No MT5 parity claim follows from this test.

## Reproduction

From `C:\AI\Jesse\Jesse-Audit` run `python evidence\test_outputs\dukascopy_execution_validation.py`. Expected summary: `rows=1380`, `trades=13`, `net_profit=-791.0`, long quote `ASK`, short quote `BID`, incomplete-quote status `PROVISIONAL_INCOMPLETE_BIDASK_QUOTES`.

## Entry-side and accounting regression — 2026-11-06

`python -m pytest -q strategy_lab/tests/test_events_execution_replay.py` passes 19 tests. Ten focused synthetic quote cases cover long/short market fills, long stop on Ask, short stop on Bid, long/short limit non-fills when only the non-executable quote crosses, and four opening-gap fills. Separate assertions check contract conversion, commission per side, tick value, and rejection of a quantity that violates the configured lot step. These are execution unit fixtures, not historical market data or profitability evidence. All historical market-price runs remain sourced only from `C:\AI\Dukascopy_data`.

The tests validate order replay of stored Jesse intents against quote sides. They do not re-run strategy signals against spread-adjusted quotes; trigger-signal recomputation remains open. The ledger UI run against the currently selected 2026-11-06 09:33 run returned zero trades, so that UI interaction is not used as P&L validation.

## UI-redesign regression (2026-11-05)

- ChatGPT in-app browser `http://127.0.0.1:8767/`; health confirms source root `C:\AI\Dukascopy_data`.
- New UI run `7a8daf7e-e78f-4373-afd8-3b8b32c6f93f` used XAUUSD M1 on 2026-09-14, 13 trades / 1,432 events. Request parameter `stop_offset` entered as `0,25`; persisted request/manifest value is numeric `0.25`, while categorical `order_mode` remains integer 0.
- This confirms UI parsing and server casting. It does not establish broker execution parity; metrics stay provisional.

The optimizer-selected short mode was also reapplied through the UI and backtested: run `40a6fd7e-2cf0-4853-94a9-5b7bce633ceb` persisted `order_mode=2`, with XAUUSD and source root `C:\AI\Dukascopy_data`.

## Execution-status integrity regression — 2026-11-06

The live local API was restarted and queried against existing run `85808ac8-84eb-478e-a405-7ba41a11f56d`; its manifest pins both historical input files to `C:\AI\Dukascopy_data`. The API returned `JESSE_RESEARCH_PROVISIONAL` for Jesse timestamp repricing and `DUKASCOPY_BIDASK_REPLAY` for the separate stored-intent quote replay, with `COMPLETE_QUOTES` and 13 trades. Contract specs are marked `USER_SUPPLIED_NOT_BROKER_VERIFIED`; signal recomputation and broker/tick parity are explicitly not claimed. Machine-readable response summary: `evidence/test_outputs/dukascopy_execution_statuses_20261106.json`.

This corrects current API result labels and scope reporting. It does not revise archived run outputs or convert user-supplied assumptions into verified broker specifications.

## Live dashboard execution-status check — 2026-11-06

In the ChatGPT in-app browser, selected Dukascopy XAUUSD M1 run `40a6fd7e-2cf0-4853-94a9-5b7bce633ceb` (7 Jesse trades) and ran the Bid/Ask overlay with explicitly entered illustrative settings: USD, contract size 100, tick 0.01, conversion 1, lot step 0.01, commission 3.50 per lot per side, slippage 0. The page showed `JESSE_RESEARCH_PROVISIONAL` (-238.73 USD) and `DUKASCOPY_BIDASK_REPLAY` (-208.16 USD), both 7 trades, `COMPLETE_QUOTES`, and explicitly “Contract gecontroleerd: nee” / “Signalen opnieuw berekend: nee”. All 7 trade volumes fail the declared 0.01 lot-step check, so those figures are a behavior check of the warning and calculation path, not a usable instrument P&L estimate. Run manifest confirms the only historical source is `C:\AI\Dukascopy_data`. Machine-readable evidence: `evidence/test_outputs/dukascopy_execution_ui_status_20261106.json`.

## UI hides unvalidated quote-ledger money — 2026-11-06

A live ChatGPT-browser regression exercised the Bid/Ask overlay against the same Dukascopy 7-trade run. Because none of the trades met the configured 0.01 lot step and the model is not `EXECUTION_VALIDATED` with broker-verified specs and recomputed signals, the overlay now hides aggregate monetary totals and per-trade P&L fields. It shows the execution status, quote quality, trade count, contract/signal caveats, and an explicit 7/7 lot-step warning. Evidence summary: `evidence/test_outputs/dukascopy_execution_ui_safety_20261106.json`.
