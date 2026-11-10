# Live-readiness poorten en strategie-levenscyclus

**Er bestaat geen live order-router.** Deze app plaatst geen echte orders en kan dat niet. De status `LIVE_ELIGIBLE` betekent uitsluitend: *in aanmerking voor menselijke beoordeling*. Een toekomstige live-gateway is **UITGESCHAKELD** totdat daar een aparte, expliciete opdracht voor komt.

## Statussen

`IDEA`, `PROTOTYPE`, `BACKTESTING`, `OPTIMIZING`, `VALIDATION_FAILED`, `BACKTEST_VALIDATED`, `OOS_VALIDATED`, `FORWARD_TESTING`, `FORWARD_FAILED`, `PAPER_ELIGIBLE`, `LIVE_REVIEW_REQUIRED`, `LIVE_ELIGIBLE`, `RETIRED`.

## Toegestane overgangen

| Van | Naar |
| :--- | :--- |
| IDEA | PROTOTYPE |
| PROTOTYPE | BACKTESTING |
| BACKTESTING | OPTIMIZING, VALIDATION_FAILED, BACKTEST_VALIDATED |
| OPTIMIZING | VALIDATION_FAILED, BACKTEST_VALIDATED |
| BACKTEST_VALIDATED | OOS_VALIDATED, VALIDATION_FAILED |
| OOS_VALIDATED | FORWARD_TESTING |
| FORWARD_TESTING | FORWARD_FAILED, PAPER_ELIGIBLE |
| PAPER_ELIGIBLE | LIVE_REVIEW_REQUIRED, FORWARD_FAILED |
| LIVE_REVIEW_REQUIRED | LIVE_ELIGIBLE, FORWARD_FAILED |
| VALIDATION_FAILED, FORWARD_FAILED | PROTOTYPE (nieuwe versie) |
| elke status | RETIRED |

Alles anders wordt geweigerd met een Nederlandse foutmelding. Registratie en historie staan in `strategy_lab/data_cache/lifecycle.sqlite3`; de tabel `history` is append-only (triggers blokkeren `UPDATE`/`DELETE`).

## Bewijsvereisten per overgang

Overgangen naar `BACKTEST_VALIDATED`, `OOS_VALIDATED`, `FORWARD_TESTING`, `PAPER_ELIGIBLE`, `LIVE_REVIEW_REQUIRED` en `LIVE_ELIGIBLE` vereisen een `evidence_ref` (ledger-id of forward-registratie-id) en een `evidence`-object.

| Naar | Exacte criteria |
| :--- | :--- |
| BACKTEST_VALIDATED | `verdict` in {RESEARCH_CANDIDATE, OOS_PASSED, FORWARD_ELIGIBLE} |
| OOS_VALIDATED | `verdict` in {OOS_PASSED, FORWARD_ELIGIBLE} |
| FORWARD_TESTING | `verdict` FORWARD_ELIGIBLE of OOS_PASSED, plus bevroren `registration_id` |
| PAPER_ELIGIBLE | `forward_trades` >= 30, `forward_days` >= 60, `gate_c` = "PASS" |
| LIVE_REVIEW_REQUIRED | `gate_d` = "PASS"; wordt nu altijd geweigerd met `REALTIME_PAPER_FEED_UNAVAILABLE` |
| LIVE_ELIGIBLE | expliciete `human_reviewer` (naam) en `gate_e` = "PASS" |

## Poorten A tot en met E

| Poort | Naam | Slaagt als | Status nu |
| :--- | :--- | :--- | :--- |
| A | Research | backtest gevalideerd met ledger-bewijs (BACKTEST_VALIDATED bereikt) | afgeleid uit historie |
| B | Robustness | out-of-sample en robuustheid aangetoond (OOS_VALIDATED bereikt) | afgeleid uit historie |
| C | Forward | minimaal 30 forward-trades over minimaal 60 dagen, gate_c PASS (PAPER_ELIGIBLE bereikt) | afgeleid uit historie |
| D | Paper | realtime paper-trading gehaald | **BLOCKED**: `REALTIME_PAPER_FEED_UNAVAILABLE`, er is geen realtime feed |
| E | Live review | menselijke reviewer heeft expliciet akkoord gegeven | BLOCKED zolang D niet gehaald is |

Statuswaarden per poort: `PASS`, `FAIL`, `INSUFFICIENT_EVIDENCE`, `BLOCKED`, elk met een Nederlandse uitleg (`explanation_nl`) afgeleid uit het opgeslagen bewijs.

## Bewaking van forward-tests

`strategy_lab/research/monitoring.py` geeft waarschuwingen (`DATA_STALE`, `NO_TRADES`, `DRAWDOWN_LIMIT`, `RISK_LIMIT`, `FORWARD_BELOW_EXPECTATION`, `CONFIG_CHANGED`, `REVIEW_REQUIRED`). Statistische alerts (onder verwachting, CUSUM-breuk bij h = 5 sigma) verschijnen pas vanaf 20 forward-trades en vereisen opgeslagen backtestverwachtingen (`configuration.expectations`: `mean_r`, `std_r`).

## Slotopmerking

Geen enkele status of poort schakelt handel in. `LIVE_ELIGIBLE` is een signaal voor een mens om te beoordelen, nooit een toestemming voor automatische uitvoering.
