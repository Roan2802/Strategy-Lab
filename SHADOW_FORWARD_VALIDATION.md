# Shadow Research Portfolio: validation (v102)

**EXPERIMENTAL / TECHNICAL PROOF.** Tests use synthetic candles and prove mechanics only. The real registration below has
zero prospective days, so there is no forward result about any strategy.

Tests: `strategy_lab/tests/test_v102_shadow_forward.py` (10 tests), `test_v102_portfolio_accounting.py` (9 tests; see
PORTFOLIO_ACCOUNTING_VALIDATION.md). Design: SHADOW_FORWARD_DESIGN.md.

## What the tests assert

| Area | Assertion |
| :--- | :--- |
| Separation | shadow store is another file; the validated store lists no shadow portfolio; `promote` raises; a direct `live_eligible=1` insert or update fails on the CHECK/trigger; a plain forward portfolio inside the shadow file is refused by `advance`; the API namespaces do not mix |
| Eligibility | allowed: INSUFFICIENT_EVIDENCE, TECHNICALLY_VALID, statistically REJECTED (L3+). Refused: look-ahead/technical rejection, TECHNICAL_BLOCKED, missing-data reasons, duplicates, passed, running, failed technical check, ERROR. Jesse/plugin and `scale_in` give NOT_SUPPORTED; invalid spec gives INVALID_SPEC |
| Registration | record holds code/spec hash, parameters, indicators, symbol, timeframe, cost, execution and risk model, data snapshot sha, exact UTC start, information cutoff <= start, research period, reason; a changed spec is version 2 with `supersedes`, version 1 unchanged; UPDATE/DELETE on registrations fail |
| Prospective only | 0 days processed right after registration reported as `AWAITING_FIRST_FORWARD_DAY`; candles dated before the registration but downloaded afterwards create no event |
| Audit timestamps | each trade has `signal_ts <= candle_ts <= exit_at < received_at`; `received_at` equals the partition ingest time; `evidence_class DAILY_DELAYED_FORWARD`; spread, commission and fill assumption recorded |
| Safety | identical re-download plus engine restart: same events and ids, ledger head unchanged, one fill; append-only triggers hold; missing day gives `WAITING_FOR_DATA` and a warning; missing FX rate gives `PAUSED / FX_RATE_UNAVAILABLE` with no events, then the same day commits once a rate exists and the fill carries `fx_rate` |
| API | all `/api/shadow` endpoints and field names listed in SHADOW_FORWARD_DESIGN.md section 6 |

## Real registration (this machine, 2026-11-11)

Script: `evidence/v102/shadow/register_real.py` (`PYTHONPATH=. C:/Python311/python.exe evidence/v102/shadow/register_real.py`);
output `evidence/v102/shadow/real_registration.json`. Store `strategy_lab/data_cache/shadow_forward.sqlite3`.

Portfolio "Schaduw research 2026-11" (USD 30,000, risk limit 5%, default shadow limits). Eligible pool in the real factory
database at that moment: 343 candidates. Registered (all `live_eligible: false`):

| Strategy | Symbol / timeframe | Source evidence |
| :--- | :--- | :--- |
| Gen vwap_pullback EURUSD H1 48d05e | EURUSD H1 | TECHNICALLY_VALID_UNTESTED |
| Gen trend_adx_strength XAUUSD H4 c6440c | XAUUSD H4 | TECHNICALLY_VALID_UNTESTED |
| Gen mr_vwap_deviation US30 M15 01c639 | USA30IDXUSD M15 | REJECTED (statistical, L3+) |

`advance` was run once: `UP TO DATE`, 0 committed days, 0 trades, ledger `OK` with 0 sealed days. This is the honest result:
the daily partitions for the registration day are published only after that UTC day ends. The validated forward store
(`/api/forward/portfolios`) stays empty.

## Limitations

* No prospective result exists yet. Tests do not show that any strategy works.
* Costs (commission 0, slippage 0), the margin leverage and the contract specification are assumptions.
* Historical/OOS correlation classes need frozen pre-registration daily returns, which are not stored yet.
* Jesse and plugin strategies are NOT_SUPPORTED; real-time paper forward is not implemented.
* Changing `forward_engine.py` changed `MODEL_SHA256`: forward portfolios created by an older build refuse to continue. The real
  validated store held no forward portfolio at the time.
