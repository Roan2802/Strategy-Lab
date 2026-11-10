# Broker assumptions register (V101)

Code: `strategy_lab/research/instruments.py` (`spec_for`, `money_gate`, `money_approval`). API: `GET /api/multimarket/markets`.
Tests: `strategy_lab/tests/test_v101_multimarket_instruments.py`.

No broker specification exists in the repository or the data. Every field below carries a `value`, a `status` and a
`source`. Statuses: `VERIFIED_FROM_DATA` (measured from Dukascopy candles), `ASSUMED` (lab default/convention),
`UNKNOWN` (no value, nothing invented), `BROKER_VERIFIED` (entered by the user with a source).

## Fields per market (all 11 markets)

| Field | Default status | Source | Note |
| :--- | :--- | :--- | :--- |
| contract_size | ASSUMED | convention | FX 100000, XAUUSD 100, indices/crypto 1 |
| tick_size | VERIFIED_FROM_DATA | dukascopy_data | smallest price step in quoted decimals; broker tick may be larger |
| tick_value | ASSUMED (derived) | derived | tick x contract size in quote currency; account value x FX rate at that time |
| pip_value | ASSUMED (FX) / UNKNOWN (others) | derived / n/a | pip 0.0001 (0.01 JPY); not defined for CFDs |
| currency | ASSUMED | dukascopy convention | quote currency + account currency |
| lot_step, min_lot | ASSUMED | convention | 0.01 |
| max_lot | UNKNOWN | none | not modelled |
| margin | UNKNOWN | none | no margin check in any engine |
| trading_hours | VERIFIED_FROM_DATA | dukascopy_data | observed H1 presence; holidays/DST not modelled |
| commission_per_lot_side | UNKNOWN | none | stress assumption only |
| typical_spread | VERIFIED_FROM_DATA | dukascopy_data | ask_close - bid_close (median, p90, last 2y) |
| slippage | ASSUMED | lab_default | 0 points unless stressed; no measured fill slippage |
| swap | UNKNOWN | none | overnight financing is not modelled |
| account_conversion | VERIFIED_FROM_DATA | dukascopy_data | real EURUSD/USDJPY/USDCAD/GBPUSD/AUDUSD series, causal |

## Money gate

Money-based approval requires `VERIFIED_FROM_DATA` or `BROKER_VERIFIED` on: contract_size, tick_size, lot_step, min_lot,
commission_per_lot_side, margin, swap, slippage, typical_spread, account_conversion. Today the blocking fields on every
market are contract_size, lot_step, min_lot, commission_per_lot_side, margin, swap, slippage. Result:
`money_status = PROVISIONAL`, `money_gate.money_approval_allowed = false`; multi-market runs return `money_approval`
(aggregate: one blocked market blocks the whole result). R-multiples, percentages and trade counts remain usable;
amounts in currency, lot sizes and margin conclusions are PROVISIONAL.

## Entering broker facts

Create `evidence/v101/multimarket/broker_overrides.json`:

```json
{"DE40": {"contract_size": {"value": 1, "source": "<broker spec sheet / date>", "status": "BROKER_VERIFIED"}}}
```

Entries without a `source`, without a value, or with another status are ignored. Opening the gate needs all seven
blocking fields per market.

## Not modelled anywhere (documented gaps)

Swap/overnight financing, margin/leverage limits and stop-out, max lot, partial exits, holiday calendars, requotes and
rejections, variable slippage. See the execution audit in `MULTI_MARKET_JESSE_PLUGIN_VALIDATION.md`.
