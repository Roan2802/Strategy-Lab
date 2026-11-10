# Strategy Lab V8 implementation plan

## V8.1

1. Keep the original CSV reader and its cache in `strategy_lab/data_manager.py`. Add a project-local ledger of immutable, versioned UTC M1 day partitions. Validate quote pairs, minute coverage and publication bounds before a transaction becomes visible.
2. Add a configured JForex adapter for historical Bid and Ask bars. Separate provider availability from market closures. Keep a retryable per-symbol queue and checkpoint, and run the same worker from Windows scheduling and the dashboard.
3. Freeze source, parameters, warm-up, execution and risk configuration in a durable registry. A forward instance receives only eligible data first observed after its registration. Use the explicit quote strategy contract from the existing runner where it can be made resumable. Keep historical replay separate.
4. Advance the strategy cursor, virtual orders, positions and shared-capital ledger in one transaction. Record feed availability, decisions, fills and costs. Fail closed on missing data or incompatible strategy state. Add a dashboard for synchronization, forward performance and risk.
5. Validate duplicate delivery, interrupted commits, catch-up, closures, restart and forward-only input with controlled fixtures. Independently validate the configured provider against actual paired quotes when access is available.

## Subsequent phases

- V8.2 extends the existing strategy editor, strategy metadata and Inspector with versioned templates, the visual builder and the IQ Capital research library.
- V8.3 extends the existing optimizer and saved-run evidence with independent time windows, robustness tests and statistical validation.
- V8.4 builds a bounded discovery queue on those interfaces and records all attempts and rejections.
- V8.5 adds promotion gates, forward monitoring and portfolio controls. Human review remains required for live eligibility.
- V8.6 adds optional data and ML adapters where their inputs, access and validation are established.

## Provider boundary

Dukascopy documents historical Bid/Ask bar access through [JForex IHistory](https://www.dukascopy.com/wiki/en/development/strategy-api/historical-data/history-bars/) and historical operation through the [JForex SDK](https://www.dukascopy.com/wiki/en/development/get-started-api/use-jforex-sdk/download-jforex-sdk/). The [public historical export](https://www.dukascopy.com/swiss/english/marketwatch/historical/) is a manual fallback. The [website terms](https://www.dukascopy.com/swiss/english/legal-pages/terms-of-use/) restrict automated website access without written consent. Provider access and local pipeline validation are separate acceptance gates.

## Implementatiestatus — 2026-11-09

Bestaande V7-functionaliteit (Strategy Tester, Inspector, causale replay, optimizer, historische portfolioanalyse) is ongewijzigd en draait in de view **Backtest/Optimaliseren**.

```text
strategy_lab/
├── static/shell.js, shell.css      Workflow-navigatie (11 views) bovenop de bestaande app.js
├── research_api.py                 FastAPI-router: library, builder, validate, discovery, ledger, lifecycle, jobs, capabilities
├── library.py                      IQ Capital-import (45 dossiers, A7/B27/C11, RESEARCH_VARIANT)
├── daily_data.py / daily_sync.py   Partitiestore + dagelijkse worker (inbox + JForex-adapterhook)
├── forward_registry.py / forward_engine.py / forward_rules.py   Bevroren registraties, dagelijkse forward, gedeeld kapitaal
├── research/
│   ├── backtest.py                 Bid/Ask researchbacktester op rule-specs (STRATEGY_LAB_RULES_V1)
│   ├── codegen.py                  Spec → Jesse-strategie (zelfde rule_engine), 5 sjablonen
│   ├── validation.py               PSR/DSR/MinTRL, bootstrap, Monte Carlo, kostenstress, walk-forward, PBO/CSCV, scorecard
│   ├── discovery.py                11 families, hypothesegenerator, begrensde queue
│   ├── ledger.py                   Append-only researchlogboek (triggers blokkeren UPDATE/DELETE)
│   ├── lifecycle.py                13 statussen, gates A–E, append-only historie
│   ├── jobs.py                     Achtergrondtaken met voortgang/annuleren/timeout
│   └── monitoring.py               Forward-alerts, rolling metrics, CUSUM
runtime/strategy_lab/rule_engine.py Causale, pure-numpy regelevaluator (host én Jesse-container)
tools/jforex_adapter/               Java-adapter op de lokale JForex4-SDK: 1 dag × 1 symbool per aanroep
```

Dataflow dagelijks: Windows-taak → `daily_sync` → JForex-adapter (of inbox) → gevalideerde dagpartitie → `DataManager` leest historie + partities als één reeks → forward-engine verwerkt alleen na registratie gepubliceerde candles → monitoring-alerts op het dashboard.

Toekomstige live gateway: **DISABLED** — er bestaat geen orderrouter; `LIVE_ELIGIBLE` betekent alleen geschikt voor menselijke beoordeling.
