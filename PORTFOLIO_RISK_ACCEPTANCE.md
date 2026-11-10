# Portfolio risk acceptance (v101)

Synthetic fixtures; mechanics only, not evidence about any strategy.
Tests: `strategy_lab/tests/test_v101_portfolio_acceptance.py`.

## Model

* One shared balance. Each member has a frozen allocation (the sum may not exceed starting capital, checked at
  enrolment). A member can never use more margin than `allocation + its own realised P&L`, even when portfolio capital is idle.
* Shared budget per minute: `min(balance, mark-to-market equity)`. Total open margin must fit it; total open stop risk must
  fit `portfolio risk fraction * equity` (the smaller of portfolio and strategy limits). Pending orders do not reserve
  margin; the check runs at fill time, so two pending orders cannot both fill past the budget.
* Opposite or identical signals on one market are two gross virtual positions: no netting, both reserve margin and risk,
  each pays its own spread side.
* Equity is one number: realised balance plus marked open P&L, stored per committed day.

## Portfolio limits (`ForwardEngine.set_limits`, optional, fixed before the first committed day)

| Limit | Effect | Event |
| :--- | :--- | :--- |
| `max_daily_loss` (fraction of day-start equity) | no new entries for the rest of the UTC day | `ORDER_REJECTED` reason `PORTFOLIO_HALT`, halt `DAILY_LOSS_LIMIT`, flag `DAILY_LOSS_HALT` |
| `max_drawdown` (fraction of peak equity) | no new entries while drawdown is at or beyond the limit (spans days) | `PORTFOLIO_HALT`, halt `MAX_DRAWDOWN` |
| `symbol_risk` (fraction of equity) | caps summed open stop risk on one symbol | `SYMBOL_RISK_LIMIT` |
| `max_jump` (default 0.2) | a quote-to-quote move above this blocks entries on that candle, exits still process | flag `PRICE_JUMP`, `PORTFOLIO_HALT` halt `PRICE_JUMP` |
| `gap_minutes` (default 180) | flag only | flag `DATA_GAP` |

Flags live in `forward_flags` (append-only, inside the day seal), not in the event stream.

## Currency and missing data

`portfolio.fx_check` runs at enrolment (blocking) and in every report (`member.fx`). It reuses `research/instruments.py`
conversion info.

* BLOCKED at enrolment: non-USD account currency (no causal source), or a non-USD quote without a conversion pair.
* PLACEHOLDER_RATE: conversion needed but the frozen rate is 1.0 (for example DE40, USDJPY). Not blocked, because existing
  registrations and tests use it, but flagged in every report; P&L, margin and risk are misstated until a real rate is frozen.
  Switching this to a hard block is a one-line change in `enroll`.
* WARNING: a rate was frozen but it is static, so P&L drifts from the real conversion.
* UNKNOWN_INSTRUMENT: flagged.
* A missing UTC day makes `advance` return `WAITING_FOR_DATA`; nothing is guessed.

**Limitation:** forward conversion is a frozen static rate, not a causal series. The causal
`instruments.conversion_series` is used by research backtests only.

## Correlation API

`GET /api/forward/portfolios/{id}/correlations?min_obs=30&threshold=0.7`. Daily returns (closed P&L per UTC day over
member capital at day start, zero on quiet days), aligned on common days only, Pearson. Three classes are kept apart and
never pooled: `HISTORICAL_BACKTEST` and `HISTORICAL_OOS` (from `configuration.pre_registration_returns`, frozen with the
registration), `DAILY_DELAYED_FORWARD` (from the ledger); `REALTIME_PAPER` is `NOT_IMPLEMENTED`. Each pair carries `n_obs`,
`min_obs`, `same_symbol`, `correlation`, `status`. Fewer than `min_obs` aligned days, or a zero-variance series, gives
`INSUFFICIENT_EVIDENCE` with `correlation: null` and the reason. Pairs at or above the threshold (or on one symbol) are
listed in `correlation_risk_pairs`; correlation is reported and does not change order acceptance. Registrations made
before v101 carry no frozen historical returns, so those two classes read `INSUFFICIENT_EVIDENCE` for them.

## Acceptance results

| Scenario | Result |
| :--- | :--- |
| Two strategies open simultaneously | both fill the same candle, margin and risk sums match, allocations within capital |
| Insufficient margin | rejected `SHARED_MARGIN`, balance and equity unchanged |
| Strategy exceeds own budget (10,000 shared, 500 allocated) | rejected `SHARED_MARGIN`; 9,500 stays unallocated |
| Allocations beyond capital | refused at enrolment |
| Same market, opposite signals | two gross positions, ASK and BID fills, margin not netted, both settle |
| Correlated markets | forward pairs +1.0 and -1.0 at 12 days, flagged; `min_obs=30` gives `INSUFFICIENT_EVIDENCE` |
| Daily risk limit hit | second entry the same day rejected; next UTC day trades again |
| Max drawdown | next-day entry rejected while the drawdown persists |
| Per-symbol risk | second stacked position rejected `SYMBOL_RISK_LIMIT`; control without the cap stacks |
| Restart with open positions | carried margin and risk still bind a new entry after restart |
| New data after a gap | stop filled at the gap open (97.99, worse than the stop), `DATA_GAP` flagged |
| Missing day | `WAITING_FOR_DATA`, nothing committed |
| Large price jump (30%) | `PRICE_JUMP` flagged, entry on that candle rejected |
| Missing FX or instrument data | EUR account blocked; USDJPY/DE40 at 1.0 flagged PLACEHOLDER_RATE; static rate WARNING; UNKNOWN_INSTRUMENT flagged |
