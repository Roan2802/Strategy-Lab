# Portfolio accounting validation (v102)

Applies to both the validated forward portfolio and the shadow portfolio (same engine). Synthetic fixtures; mechanics only.
Tests: `strategy_lab/tests/test_v102_portfolio_accounting.py`, plus the v101 acceptance tests (updated for FX).

## FX conversion

* Non-USD quotes (EUR index, JPY and CAD pairs) are converted per candle with `research/fx.py`
  (`FxService.rate(quote, account, candle_ts, "mid")`). The rate is the open of the last M1 bar at or before the candle; never 1.0
  unless both currencies are equal. Events record `fx_quote`, `fx_rate`, `fx_side`, `fx_source`, `fx_data_version`.
* The frozen `quote_to_account` is ignored for those markets. `PLACEHOLDER_RATE` and the static `WARNING` status are gone;
  `fx_check` now answers `OK`, `DYNAMIC_FX`, `UNKNOWN_INSTRUMENT` or `BLOCKED` (non-USD account, no pair, or no FX service).
* Missing rate: the day rolls back and `advance` returns `PAUSED / FX_RATE_UNAVAILABLE`; no event, flag or balance change
  is stored; the same day commits once the rate exists (test: missing rate then fixed rate).
* The last rate per member is stored in member state (`fx_rate`) so a restart marks open positions with it.
* Limits: P&L converts at the exit-time rate, margin and risk at the entry-time rate; broker mark-ups are not modelled.

## Accounting (`portfolio.portfolio_accounting`, `GET .../accounting`)

Shared capital (starting, balance, equity, allocated, unallocated), per-member allocation and realised P&L, per-trade and
portfolio risk fractions, open stop risk against `max_open_risk = risk_limit * min(balance, equity)`, risk by symbol and by
group, margin (open, free, margin level, reservation rule), gross exposure by symbol (no netting), daily-loss usage, drawdown
against the limit, rejected hypothetical orders by reason with the last 50, the priority rule and the limits.

**Margin reservation:** `margin = notional / leverage`, checked at fill time against `min(balance, equity)` and the member's own
budget. The leverage is the frozen one; for shadow registrations it is an assumption by asset class (fx 30, metal 20, index 20,
crypto 2) unless `research/instruments.margin_leverage(symbol)` exists, then that value is used and labelled `INSTRUMENT_PROFILE`.
`instruments.py` does not provide it today, so every margin figure carries `status: ASSUMED`.

## New portfolio limits (set before the first day, like the v101 ones)

| Limit | Effect | Event |
| :--- | :--- | :--- |
| `group_risk` (fraction of equity) | caps gross open stop risk of all members whose instrument shares an asset class (fx, index, metal, crypto); the class is an assumption, not a measured correlation | `ORDER_REJECTED` reason `GROUP_RISK_LIMIT` |
| `max_positions` | no new entry while that many positions are open | `PORTFOLIO_HALT`, halt `MAX_POSITIONS` |

When both the symbol and the group cap exist, the tighter room decides and names the reason.

## Combined trades and priority

Opposite or identical signals on one market remain two gross positions, each paying its own spread side. Same-minute orders are
evaluated in enrolment order (`joined_at`, then registration id): the earlier member consumes margin and risk budget first.
Rejected orders are logged with `priority_rank` and appear as `hypothetical: true` in the accounting.

## Correlations

`forward_correlations` is unchanged: aligned daily returns, `HISTORICAL_BACKTEST`, `HISTORICAL_OOS` and `DAILY_DELAYED_FORWARD`
kept apart, `INSUFFICIENT_EVIDENCE` below `min_obs`. Correlation never changes order acceptance; `group_risk` is the only
correlated-exposure control and it uses asset classes.

## Stress tests (`portfolio.stress_test`, `GET .../stress`)

Assumption-driven what-ifs on open positions and configured capacity, never forecasts. Each scenario gives loss, equity after,
margin level after and `breaches` (`DAILY_LOSS_LIMIT`, `MAX_DRAWDOWN`, `PORTFOLIO_RISK_BUDGET_EXCEEDED`, `MARGIN_CALL_ASSUMED`
below margin level 0.5 (assumed, no broker stop-out schedule), `EQUITY_EXHAUSTED`).

| Scenario | Assumption |
| :--- | :--- |
| `CORRELATED_SAME_DIRECTION` | every position of the worst asset-class group stops out together |
| `CORRELATIONS_GO_TO_ONE` | all groups lose their full stop risk at once |
| `GAP_THROUGH_STOP` | stops fill 2% of entry beyond the level |
| `VOLATILITY_SHOCK` | realised loss is 2x planned risk |
| `POOR_LIQUIDITY` | exit slippage is 5x the frozen slippage |
| `HIGHER_SPREADS` | exit spread is 3x the spread at entry |
| `MANY_SIMULTANEOUS_POSITIONS` | every member holds one position at its per-trade risk; the risk cap and `max_positions` bind (`cap_binds`) |
| `INSUFFICIENT_MARGIN` | all stops hit against the open margin |
| `DAILY_LOSS_LIMIT` | all open risk realised in one day against the limit (`headroom`) |

With no open positions the position-based losses are 0 with `note: "no open positions"`; only the capacity scenario is non-zero.

## Test results

All mechanics above are asserted in `test_v102_portfolio_accounting.py` (FX pause and resume, FX not used for USD quotes,
group cap, position cap and priority, accounting identities, nine stress scenarios, margin-call flag, empty portfolio) and
`test_v102_shadow_forward.py`. The full suite result is in `SHADOW_FORWARD_VALIDATION.md`.

## Not validated

Broker margin schedules, broker FX mark-ups, swap, intrabar tick order and real correlations. These are labelled assumptions.
