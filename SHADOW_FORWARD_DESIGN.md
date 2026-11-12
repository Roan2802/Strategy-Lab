# Shadow Research Portfolio: design (v102)

**EXPERIMENTAL.** A shadow portfolio is a research sandbox that follows frozen rules on data published after registration.
It is not validation, it never becomes `LIVE_ELIGIBLE`, and it places no orders anywhere (no broker connection exists).

## 1. Separation from the Validated Forward Portfolio

| Aspect | Validated forward | Shadow research |
| :--- | :--- | :--- |
| Store | `data_cache/daily_dukascopy.sqlite3` | own file `data_cache/shadow_forward.sqlite3` |
| Portfolio kind | forward portfolio | `SHADOW_RESEARCH`, `experimental=1`, `live_eligible=0` under CHECK constraints; the rows are immutable (triggers) |
| API | `/api/forward/*` | `/api/shadow/*` |
| Promotion | via validation gates | `POST /api/shadow/portfolios/{id}/promote` always answers 409 `NEVER_LIVE_ELIGIBLE` |
| Labelling | | every response carries `kind`, `experimental`, `live_eligible:false`, `label_nl` |

The shadow engine (`strategy_lab/shadow_forward.py`, `ShadowEngine`) subclasses the audited `ForwardEngine`: same quote-side fills
(ASK long / BID short), same exactly-once UTC days, same hash-chained append-only ledger. The candle store is only read; the
registry, members, events, flags and ledger of shadow portfolios live in the shadow file. A forward portfolio that was not
created through `create_shadow` is refused by `advance`, and `create` raises.

## 2. Eligibility (`shadow_forward.eligibility`)

Allowed: technically valid candidates that are `INSUFFICIENT_EVIDENCE`, `TECHNICALLY_VALID` (untested beyond the technical
checks) or `REJECTED` at stage L2 or later for statistical or performance reasons.

Refused, with the reason listed in the 422 body (`code: NOT_ELIGIBLE`, `reasons`): look-ahead or leakage, invalid spec or
execution (`rejection_kind = technisch`, a failed technical check), missing instrument or market data, corrupt data,
`TECHNICAL_BLOCKED`/`ERROR`, duplicates, candidates still running, candidates that already passed (they belong in the
validated portfolio). Also refused: instruments absent from the registry and an FX route that is not available.

Sources: factory candidates (`kind: factory`, `candidate_id`, optional `run_id`; spec from `GET /api/factory/candidates/{id}`),
builder specs (`kind: builder`, `spec_name`; unvalidated, evidence `UNVALIDATED_BUILDER_SPEC`). Jesse and plugin strategies
(`kind: jesse|plugin`) answer 422 `code: NOT_SUPPORTED`: the forward engine only simulates rule specs (`RULES_BIDASK_V1`).
Specs with `scale_in` are NOT_SUPPORTED too (the forward model never adds to positions).

## 3. Prospective registration

`register` freezes, in `frozen_strategies` (checksummed) and in the append-only `shadow_strategies` record
(`record_sha256`):

code hash and spec hash, parameters (all explicit), indicators, symbol, timeframe, cost model (quotes, commission, slippage,
contract; contract defaults are an assumption), execution model, risk model (allocation, per-trade risk, portfolio risk,
leverage plus margin source), data snapshot sha256 and data version, warm-up sha256, the exact UTC start, the
historical-information cutoff (`info_cutoff_utc`, the warm-up `as_of`, never after the start), the research period used
(from the factory development range, or `UNKNOWN`), the reason (`reason_nl`, required) and the historical backtest reference.

Rows cannot be updated or deleted. Changing a spec, parameter or cost produces a new registration: `version` n+1 with
`supersedes` pointing at the previous one. Version 1 keeps running unchanged; nothing is edited silently.

## 4. Processing

Only candles with an open time at or after the registration instant, inside a daily partition whose first publication was
observed after the registration, count. Candles dated before the registration that are downloaded later are filtered out
(`frame.index >= joined`), and later provider revisions never rewrite history (first publication wins).

Every event carries: `candle_at` (candle_ts), `received_at` (the partition's ingest time, `observed_at` from the partition
store, identical to `quote_observed_at`), `signal_at`, the order id and submit candle, `fill_assumption`, spread, slippage,
commission, the position and the result, plus the partition sha256. `GET /api/shadow/strategies/{id}` joins them into one row
per hypothetical trade.

Evidence classes (also in every response): `HISTORICAL_BACKTEST`, `HISTORICAL_OOS` (neither is prospective),
`DAILY_DELAYED_FORWARD` (the only class produced here; the Dukascopy daily update is delayed forward) and
`REALTIME_PAPER_FORWARD` (NOT IMPLEMENTED). Different classes are shown side by side and never pooled.

Statuses per strategy: `AWAITING_FIRST_FORWARD_DAY` (0 prospective days: honest empty result),
`DAILY_DELAYED_FORWARD_INSUFFICIENT` (< 30 forward trades), `DAILY_DELAYED_FORWARD_PRELIMINARY`. There is no "validated" status.

## 5. Safety

* No duplicate trades on resync or restart: primary key per portfolio day, one transaction per day, deterministic order and position ids.
* Append-only tables and hash chain (`forward_ledger`, `verify_ledger`), plus shadow tables with their own triggers.
* No silent parameter change (see section 3), no future data (section 4).
* FX: P&L, margin and risk of non-USD quotes use `research/fx.py` per candle (`FxService.rate(quote, account, ts, "mid")`).
  The frozen static `quote_to_account` is ignored for those markets and `PLACEHOLDER_RATE` no longer exists. A candle without a
  rate raises `FxPause`: the whole day rolls back, `advance` answers `status: PAUSED, reason: FX_RATE_UNAVAILABLE`, nothing is
  filled. If the FX service is not installed, enrolment of such a market is blocked. Broker conversion mark-ups are not modelled.
* Missing day data: `WAITING_FOR_DATA`, nothing guessed. The last non-trivial advance status is stored in `shadow_advance_log` and shown as a warning.
* Crash recovery: a crash inside a day leaves no row for that day; the retry reproduces identical events.

## 6. API as implemented

| Method and path | Result |
| :--- | :--- |
| `GET /api/shadow/portfolios` | `{portfolios:[{id,name,kind,experimental,live_eligible,currency,starting_capital,balance,risk_limit,last_day,strategies,prospective_days,created_at,label_nl}], evidence_classes, label_nl}` |
| `POST /api/shadow/portfolios` | body `{name, currency="USD", starting_capital=10000, risk_limit=0.05, limits?}` returns 201 `{id, kind, experimental, live_eligible:false, limits}`; default limits: daily loss 5%, drawdown 25%, symbol risk 2%, group risk 4%, max 5 positions |
| `POST /api/shadow/portfolios/{id}/register` | body `{source:{kind, candidate_id?, run_id?, spec_name?}, reason_nl, allocation?, trade_risk?}` returns 201 `{strategy_id, version, start_utc, snapshot_sha256, evidence_status}`; 422 `NOT_ELIGIBLE` / `NOT_SUPPORTED`; allocation defaults to one third of starting capital |
| `GET /api/shadow/portfolios/{id}` | `{portfolio, strategies:[{id,name,symbol,timeframe,start_utc,trades,net,max_dd_pct,pf,last_update,evidence_status,warnings,...}], equity:[{day,equity,balance}], daily:[{day,pnl,trades_closed,orders_rejected}], limits, accounting, stress, correlations, ledger, flags, last_advance, evidence_classes, prospective_days, warnings}` |
| `GET /api/shadow/strategies/{id}` | `{strategy, equity, daily, trades, clustering, backtest_vs_forward, drift, warnings, record, record_sha256, evidence_classes}` |
| `POST /api/shadow/portfolios/{id}/advance` | body `{max_days}`: processes available UTC days; returns `committed_days`, `status` (`UP TO DATE`, `WAITING_FOR_DATA`, `PAUSED`, `BATCH_LIMIT`) |
| `POST /api/shadow/portfolios/{id}/promote` | always 409 `NEVER_LIVE_ELIGIBLE` |

Additive on the validated side: `GET /api/forward/portfolios/{id}/accounting` and `.../stress`.

`backtest_vs_forward` keeps `historical_backtest` and `forward` apart (status `NO_BACKTEST_REFERENCE`, `INSUFFICIENT_FORWARD_DATA`
below 10 forward trades, else `COMPARABLE`). `drift` stays `INSUFFICIENT_EVIDENCE` below 30 forward trades.
`clustering`: trades per day, loss streak, entry-hour histogram, share of profit in the top three trades.

## 7. Known limits

* Historical and OOS correlation classes stay `INSUFFICIENT_EVIDENCE` for shadow strategies: daily pre-registration returns are not frozen yet.
* Contract defaults (commission 0, slippage 0) and the retail leverage used for margin are labelled assumptions, not broker data.
* Jesse and plugin strategies cannot be simulated. No real-time paper feed exists.
* A new strategy can only be enrolled on a UTC day after the portfolio's last committed day.
