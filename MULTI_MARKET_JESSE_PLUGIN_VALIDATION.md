# Multi-market for Jesse strategies and plugins, plus execution audit (V101)

Code: `research/multimarket.py` (`run_cross_market_adapter`), `research/adapters.py` (`instrument_binding`,
`scan_source`, `market_block_reason`, `strategy_identity`), `research/instruments.py`, `multimarket_api.py`.
Tests: `test_v101_multimarket_adapters.py`, `test_v101_multimarket_instruments.py`, `test_v101_execution_reference.py`.
Evidence: `evidence/v101/multimarket/real_run.py`, `real_run_jesse.json`, `real_run_plugin.json`, `real_run_jesse_de40.json`.

## 1. How it works

`POST /api/multimarket/runs` accepts `strategy.kind` = `jesse` or `plugin` (name required, optional `origin_symbol`,
`params`). The same frozen config runs on every market x timeframe through `adapters.make_adapter`; development data only
(sealed 2024+ raises `SealedAccessError`, HTTP 422). `reoptimise` and any `risk_model` other than `native` are refused
(NOT_SUPPORTED): the strategy keeps its own sizing, so `risk_normalisation = NOT_APPLICABLE_OWN_SIZING`; compare R/%,
costs and trade counts, not lots.

- **Instrument binding (static scan, no translation).** Plugin: `PLUGIN['symbols']` restriction (or a source scan when
  absent). Jesse: AST scan for hardcoded symbol strings (restrict to those symbols) and price constants combined with
  price-like operands (`price > 1800.5`; restrict to `origin_symbol`, or all markets if the origin is unknown). Ratios,
  `len()`, 0/1 and docstrings are ignored. Bound markets are reported per cell as `status NOT_SUPPORTED`,
  `validation_status INSTRUMENT_BOUND` with the reason; they are not evidence (excluded from the generalisation class;
  all-bound gives `NOT_TESTABLE`). Another market needs a new version/hypothesis.
- **Price-scale warning.** Absolute price-distance hyperparameters (stop/target/offset/range) are not scale-free;
  cells carry `PRICE_SCALE_PARAMS` and `PRICE_SCALE_EXTREME` (distance > 2% or < 0.002% of median price) with the
  percentage per parameter.
- **Plugins run on their own base timeframe** (gold_reaper: M1); requested timeframes are recorded in
  `config.timeframes_requested`.
- **Cost normalisation.** Costs total and % of gross per cell. Jesse: fee x1.5 stress computed post-hoc (fee is linear in
  notional; no spread/slippage/swap in Jesse). Plugin: spread +50% re-run when the plugin supports it. A cell is only
  `edge_positive` with >= 30 trades, net > 0, PF > 1.05 and surviving stress.
- **Currency.** P/L of non-account quote currencies is converted at the rate known at each trade's exit bar open
  (`instruments.rate_at`: last value at or before the time; refuses rather than back-fill; never the current rate).
  Account currency is selectable (`account_currency` USD/EUR/...); cross rates combine two USD legs.
- **Ledger.** Every market attempt is a ledger row in family `multimarket:<kind>:<sha>`: run cells as TESTED, bound
  cells as REJECTED (`INSTRUMENT_BOUND`, reason), errors as ERROR. Not-run rows count in `n_trials` (conservative).
- **Money gate.** Results carry `money_approval` (see `BROKER_ASSUMPTIONS_REGISTER.md`): PROVISIONAL, blocked.

## 2. Real checks (dev data, M15, runtime ~22 s and ~13 s)

LabBreakout (Jesse via Docker runner), frozen defaults, 2023-06-01..2023-09-01 (`min_days=60`, flagged `SHORT_PERIOD`):

| Market | Result |
| :--- | :--- |
| XAUUSD (origin) | 53 trades, net -122.77 USD, costs 123.32, FAILS fee x1.5; flags ORIGIN_MARKET_IN_SAMPLE, PRICE_SCALE_PARAMS |
| EURUSD | 1 trade (INSUFFICIENT_TRADES): stop/target 1.0 = 83% of the 1.20 price (PRICE_SCALE_EXTREME) |
| DE40 | ERROR: Jesse spot account "Not enough balance ... 10000 EUR ... spend 15842" (frozen size 1 unit at price ~15.8k). New runs tag this `FROZEN_SIZE_EXCEEDS_BALANCE` |
| DE40, sensitivity (only start balance 200000) | 48 trades, net -1061.13 USD converted at exit-time EURUSD, `CONVERTED_CAUSAL_AT_EXIT`, FAILS stress |

Class NO_EDGE; LabBreakout source is not symbol-bound (no hardcoded symbol/price constants), but its distance
parameters are gold-scaled; results on EURUSD/DE40 mainly show that scale mismatch, not market behaviour. It is a
probe strategy, so the negative results are expected (fees exceed the 1.0-point edge).

gold_reaper (plugin, 2023-06-01..2023-08-01, M1): XAUUSD runs (100 trades, net -328.84, FAILS spread +50%);
EURUSD and DE40 are `INSTRUMENT_BOUND` ("PLUGIN['symbols'] -> only valid for XAUUSD"), both ledgered as REJECTED.
Class NO_EDGE (1 market tested, 2 bound).

All amounts PROVISIONAL (contract specs assumed); short windows are not generalisation evidence.

## 3. DE40 regression (EUR and USD account)

Tests cover: USD account converts with EURUSD mid at bar open; EUR account needs no conversion and USD instruments use
1/EURUSD; JPY->EUR cross; as-of rate semantics (held until next bar, never later/current, refusal before first
rate); `convert_trades` at exit time; lot sizing and risk % (1% of 10000, stop 50 points: 1.81 lots at rate 1.10 =
99.55 USD risk, 2.00 lots on EUR account); exposure and portfolio open risk with each position's own entry rate;
backtester P/L equals hand-calculated gross at the exit-bar rate and lots equal the hand-calculated floor at the
entry-bar rate; a flat "current" rate gives a different result; unconverted EUR quote on a USD account is flagged
`QUOTE_CURRENCY_UNCONVERTED`. Fix made in `backtest.py`: `currency_status` is now relative to the contract's account
currency (`ACCOUNT_CURRENCY_QUOTE`), where it previously assumed USD.

## 4. Execution realism audit

Engines: Bid/Ask rule backtester (`research/backtest.py` + `runtime/strategy_lab/execution.py`), Jesse (Docker),
plugins (own engine). Verdict per feature (reference tests hand-calculate every number):

| Feature | Bid/Ask engine | Evidence |
| :--- | :--- | :--- |
| Bid/ask fills | Long buys Ask, sells Bid; short mirrored | tests: entry 100.9 Ask open, TP on Bid, short entry on Bid |
| Market / stop / limit | Market at next-bar open; stop/limit trigger on executable side; gap fills at the open | pending-stop test (fill at P, gap) |
| SL / TP | SL at min(open, stop) (gap = worse than -1R, -145 vs -100); TP at max(open, target) | tests |
| Same-bar SL+TP | Stop wins, `ambiguous_bars` counted | -100 = -1R |
| Short stop vs spread | Short stop triggers on Ask high, so spread shortens it | Bid high 101.2 < 101.6 stop, stopped out |
| TP in stop/limit fill bar | Ignored (path unknown) | pending-stop test |
| Break-even | Entry price, from next bar, no spread/commission offset | pnl 0.0 |
| Trailing | best Bid/Ask price -/+ ATR mult, from next bar | +75.0 |
| Partial exits | NOT SUPPORTED (spec rejects the key); only winner-only scale-in | test |
| Slippage | Adverse on market entry, stop fills, SL, time exit; not on TP/limit | -105.0 / +200 |
| Commission | Per lot per side | 196.5 |
| Lot rounding / min lot | Floor to step; below min lot rejected, never rounded up | 0.53 lot; rejected_min_lot |
| Margin / max lot | NOT MODELLED: 100 lots (~100x leverage) accepted | test |
| Overnight swap | NOT MODELLED: 3-day hold pays nothing | test |
| Intrabar ambiguity | Conservative (stop first); M1 minute order unknown | FORWARD_CONSISTENCY_V9 3a: 6 cases, 329.62 USD margin |

Jesse vs Bid/Ask engine (prior: `FORWARD_CONSISTENCY_V9.md` sec. 3 and 5): Jesse fills both sides on one Bid series with
a 0.06% fee fraction, fixed quantity, no spread/slippage/swap; the Bid/Ask engine pays the spread each round trip.
`apply_ledger` reprices Jesse trades at Ask/Bid opens: for the reference trade the difference is exactly
spread x qty x contract size (25.0 USD) and is marked `JESSE_RESEARCH_PROVISIONAL`, `spread_trigger_replayed = false`
(it does not replay spread-sensitive triggers). Other known differences: Jesse stamps stop fills one minute later;
signals on Bid versus the executable side flip ~1.7% of entries; Jesse needs a spot->futures switch for shorts and cannot
size above the balance (DE40 error above).

## 5. Live-readiness checklist (live orders stay disabled; no router exists)

- [ ] Broker contract data BROKER_VERIFIED for all 7 blocking fields per market (register).
- [ ] Swap/financing modelled or bounded; margin, leverage, max lot and stop-out modelled.
- [ ] Commission and measured slippage from real fills; compare backtest vs paper fills.
- [ ] Partial exits modelled if a strategy uses them.
- [ ] Holiday/session calendar and rollover for indices/FX.
- [ ] Jesse vs Bid/Ask reconciliation on the same trades acceptable (no amount-for-amount comparison exists yet).
- [ ] Per-market validation (walk-forward, DSR) beyond the light cost-stress check done here.
- [ ] Human review gate per `LIVE_READINESS_GATES.md`; live gateway remains UITGESCHAKELD.

Status today: NOT live-ready; all money conclusions PROVISIONAL.
