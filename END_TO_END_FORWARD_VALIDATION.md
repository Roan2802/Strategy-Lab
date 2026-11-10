# End-to-end forward validation (v101)

**TECHNICAL PROOF ONLY.** All prices are a seeded synthetic sawtooth with a deliberately built-in positive edge.
Nothing here is evidence that any strategy works on real markets. The "sealed" period is a fixture period inside a
temporary vault; the real sealed 2024+ period and `C:\AI\Dukascopy_data` are never read.

Tests: `strategy_lab/tests/test_v101_forward_e2e.py` (pipeline), `test_v101_forward_audit.py` (engine audit),
`test_v101_portfolio_acceptance.py` (portfolio). Last run output: `evidence/v101/forward/e2e_pipeline_report.json`
(written when `V101_WRITE_EVIDENCE=1`).

## 1. Pipeline in an isolated vault

Order, all asserted: develop -> backtest -> optimise (7-point grid) -> nested walk-forward -> robustness (bootstrap CIs,
parameter neighbourhood) -> statistical validation (PSR, DSR with the 7 trials) -> freeze in `SealedVault` (immutable;
record sha256 binds spec, parameters, code sha, dataset sha) -> sealed final test (only after the freeze; refused for an
unfrozen id; audit log ACCESS_START, ACCESS_DONE; a second call is a REPEAT_READ, nothing re-run) -> rerun in a second
vault gives identical frozen id, trades, metrics and bootstrap -> forward registration (`RULES_BIDASK_V1`, parameters and
spec hash equal to the vault record, vault id and record hash stored in `data_version`) -> five UTC days processed one at
a time with a new engine instance per day -> performance stored per day (equity, partition sha256) -> restart is a no-op
-> every earlier ledger row is byte-identical after each new day (row hashes) -> a changed parameter is a new registration
with a new snapshot hash, the old one is unchanged and cannot be re-enrolled -> a crash in the middle of day 2 rolls back
completely and the retry equals a clean run event for event.

Reported honestly from the last run: backtest 69 trades; walk-forward 5/5 windows profitable (by construction);
the parameter-neighbourhood stage returned `INSUFFICIENT_EVIDENCE` (0 neighbours) with this one-parameter space, so no
plateau claim is made; the sealed fixture test had 20 trades and no bootstrap p-value (too short).
Vault timestamps use the wall clock while the forward fixture uses a synthetic 2026-06 clock, so "registered after the
freeze" is proven by the vault holding the sealed result before registration, not by comparing the two clocks.

## 2. Forward engine audit

Evidence classes (`forward_engine.EVIDENCE_CLASSES`, also in every report and API response):

| Class | Status |
| :--- | :--- |
| HISTORICAL_BACKTEST | implemented (`forward_reference`), research reference, not prospective |
| HISTORICAL_OOS | implemented (sealed final test), data existed at freeze time, not prospective |
| DAILY_DELAYED_FORWARD | implemented: only candles published after registration, whole UTC days |
| REALTIME_PAPER | **NOT IMPLEMENTED**: no live feed, no streaming, no order routing |

Audited and covered by tests: prospective-only (candles at or after enrolment, publication observed after enrolment, first
publication wins so later provider revisions never rewrite history; complete older days are never processed); pending
orders and open positions persist in member state across days and restarts; SL/TP resolved conservatively on M1 quote
sides (stop wins when both touch); gaps fill at the gap open, never at the stop; exactly-once (primary key per
portfolio day, one `BEGIN IMMEDIATE` transaction per day, repeated and concurrent `advance` are no-ops); data snapshot
sha256 stored per day and per event.

Added in v101:

* Deterministic order and position IDs on every order/position event (`order_id`, `position_id`): unique within a
  portfolio ledger, identical after a crash replay or a rerun in another database. They derive from the member's
  enrolment slot, symbol and timestamps, not from random UUIDs.
* Append-only enforcement: SQLite triggers forbid UPDATE/DELETE on `forward_events`, `forward_days`, `forward_flags`,
  `forward_ledger`. Each committed day is sealed in a hash chain (`forward_ledger`); `ForwardEngine.verify_ledger` and
  `GET /api/forward/portfolios/{id}/ledger` recompute it. A determined editor who drops a trigger and edits a row is
  caught (`BROKEN`, first bad day). Days committed by older builds report `LEGACY_UNSEALED_DAYS`, never silently OK.
* Portfolio limits, stored per portfolio (`set_limits`, immutable once a day is committed so a run stays reproducible);
  see PORTFOLIO_RISK_ACCEPTANCE.md.
* The engine source hash (`MODEL_SHA256`) covers this change, so portfolios created by an older build refuse to continue
  ("frozen forward engine version differs") rather than mix model versions. Any existing real forward portfolio must be
  recreated or re-registered under the new engine version.

Limitations: position sizing uses the frozen contract and a static `quote_to_account`; no tick order inside a minute
(quote-side OHLC only); pending orders do not reserve margin, the fill-time check is the reservation point.

## 3. Crash and restart semantics

A crash anywhere inside a day leaves no event, flag, state, balance or ledger seal for that day (one transaction). The retry
re-reads the same first-publication partitions and produces identical events and IDs.
