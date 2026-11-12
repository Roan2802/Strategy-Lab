# V10.2 — Implementatierapport (2026-11-11)

## Kort

V10.2 is gebouwd en getest: **886 pytest geslaagd**, browser-E2E **0 fouten** in 8 runs (Chrome en Edge, 1280–2560 px, dark/light, 0 JS-fouten).

- **Geldbedragen:** worden nu per candle met echte historische FX omgerekend. Ontbreekt een koers, dan worden geldresultaten en promotie geblokkeerd (`FX_RATE_UNAVAILABLE`). Er bestaat geen 1.0-fallback meer.
- **Nieuw:**
  - een Shadow Research-portfolio dat strikt gescheiden is van Gevalideerd;
  - een uitvoeringsmodel met margin, lots, swap, handelsuren, orderafwijzing en stress;
  - partial exits die in backtest en forward identiek werken;
  - portfolio-accounting;
  - een statistische selectie-audit.
- **Externe blokkades:**
  - De Dukascopy-provider is wisselvallig: 4/9 markten zijn actueel.
  - Er is geen echte brokerexport, dus geldbedragen blijven PROVISIONAL.
  - De shadowportfolio heeft nog 0 prospectieve dagen.

Er is niets gecommit, er zijn geen orders geplaatst en `C:\AI\Dukascopy_data` is niet aangeraakt. De verzegelde periode 2024+ is niet gebruikt en Optuna is niet geïnstalleerd.

## Status per onderdeel

| Onderdeel | Status | Bewijs |
|---|---|---|
| Historische FX-service | PASS | `FX_CONVERSION_VALIDATION.md`. Synthetisch getest, plus echt historisch: DE40, 744 trades, max verschil 0,0 met M1 EURUSD |
| Instrumentregister + brokerprofielen (handmatig / MT5-import) | PASS (synthetisch) | `BROKER_EXECUTION_VALIDATION.md`. Per veld VERIFIED/DERIVED/ASSUMED/UNAVAILABLE; geen echte export beschikbaar |
| Uitvoeringsmodel: margin, lot min/max/stap, swap, uren, afwijzing, stop-gap, stress | PASS (research-backtest) | Idem. Standaard uit, waardoor oude resultaten identiek blijven. Forward modelleert alleen partial exits |
| Partial exits backtest = forward | PASS | Paritytest: identieke trades, fills en eindsaldo |
| Dukascopy-herstel 9 markten | PARTIAL | `DUKASCOPY_DATA_RECOVERY.md`. Provider gaf 503/timeouts, geen omzeiling. Data-integriteit schoon voor alle 9 |
| Scheduler-controle | PASS | Taak actief, inhaalrun na slaapstand. Statusveld `scheduler_status` |
| Kalenders | PARTIAL | US30/US500/USTEC zijn VERIFIED_PROVIDER, de rest DERIVED_FROM_DATA |
| Actualiteit in de UI | PASS | Home toont actueel/vertraagd/gesloten plus de laatste succesvolle sync; de Data-pagina heeft "Nu synchroniseren" |
| Shadow Research-forward | PASS (synthetisch + echte registratie) | `SHADOW_FORWARD_DESIGN.md`, `SHADOW_FORWARD_VALIDATION.md`. Zie de toelichting hieronder |
| Statistische selectie-audit | PASS (PBO over alle L4-configs) | `STATISTICAL_SELECTION_AUDIT.md`, `FACTORY_PROTOCOL.md` §9. Zie de toelichting hieronder |
| Portfolio-accounting | PASS | `PORTFOLIO_ACCOUNTING_VALIDATION.md`. Gedeeld kapitaal, margin (aangenomen hefboom), groepsrisico, max posities, prioriteit binnen dezelfde minuut, accounting-endpoint, 9 stressscenario's |
| UI-keuze Gevalideerd / Shadow Research | PASS | E2E-logs in `evidence/v102/ui/`. V10-navigatie behouden |
| Tests + browser-E2E | PASS | `V10_2_TEST_RESULTS.md` |

**Shadow Research-forward:**
- Eigen opslag en een eigen `/api/shadow/*`.
- `live_eligible=0` wordt afgedwongen met een CHECK-constraint, en promote geeft altijd 409.
- Bevroren registratie met hash. Alleen candles van na de registratie tellen mee, en `received_at` wordt opgeslagen. Geen duplicaten, crash-veilig.
- Drie echte factory-strategieën zijn geregistreerd.

**Statistische selectie-audit:**
- **DSR (NC/paper/afhankelijkheid):** correct.
- **Twee technische fouten hersteld:** DSR/PSR telden dubbel in L5, en ontbrekend bewijs werd als afwijzing geteld. Er is daarbij geen drempel gewijzigd.
- **Kalibratie:** 0 valse positieven in 3.900 nulzoektochten, met een bovengrens van 1,3% per cel.
- **Power:** 68% bij een netto Sharpe van 3, en 0% bij een Sharpe van 1 of lager.

## Gewijzigde code (hoofdlijnen)

- **FX/uitvoering:**
  - `research/fx.py` en `research/broker_profiles.py` (beide nieuw);
  - `research/instruments.py`, `backtest.py`, `multimarket.py`;
  - `runtime/strategy_lab/execution.py`, `rule_engine.py`;
  - `strategy_lab/forward_rules.py`.
- **Data:** `data_supervisor.py` en `daily_sync.local.json` (backup in `evidence/v102/data/`).
- **Shadow/portfolio:** `shadow_forward.py` (nieuw), `forward_engine.py`, `forward_performance.py`, `portfolio.py` en `server.py` (alleen toegevoegd).
- **Statistiek:** `research/factory.py` (`l5_decision`) en `research/validation.py` (`deferred_checks`).
- **UI:** `static/workflow.js`, `factory.js`, `shell.css` en `index.html`; daarnaast `evidence/scripts/ux_e2e.py`.

## Rapporten

| Rapport | Inhoud |
|---|---|
| `FX_CONVERSION_VALIDATION.md` | FX-service |
| `BROKER_EXECUTION_VALIDATION.md` | Brokerprofielen en uitvoeringsmodel |
| `DUKASCOPY_DATA_RECOVERY.md` | Dataherstel |
| `SHADOW_FORWARD_DESIGN.md` | Ontwerp shadowportfolio |
| `SHADOW_FORWARD_VALIDATION.md` | Validatie shadowportfolio |
| `PORTFOLIO_ACCOUNTING_VALIDATION.md` | Portfolio-accounting |
| `STATISTICAL_SELECTION_AUDIT.md` | Statistische selectie-audit |
| `V10_2_REMAINING_ISSUES.md` | Resterende problemen |
| `V10_2_TEST_RESULTS.md` | Testresultaten |
| dit rapport | Overzicht |

Ook bijgewerkt: `UPDATED_FEATURE_MATRIX.csv`, `REMAINING_ISSUES.md` en `FACTORY_PROTOCOL.md`.

## PBO-besluit (doorgevoerd 2026-11-11)

Op jouw keuze berekent L5 de PBO nu over alle L4-configuraties (24 in "uitgebreid") in plaats van over de top-4; de drempel 0,5 blijft. Simulatie: 0 valse positieven in 6 nulscenario's (0/300 elk), power bij Sharpe 2 van 19% naar 38% en bij Sharpe 3 van 68% naar 95%. `STATISTICAL_SELECTION_AUDIT.md` §6a. pytest: 887 geslaagd.
