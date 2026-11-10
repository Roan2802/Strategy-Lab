# V10.1 — Eindrapport (2026-11-10)

## Uitkomst in één alinea
Het platform is betrouwbaarder en strenger gemaakt, maar er is **geen strategie gevonden** die de validatie haalt: in de echte V10.1-fabrieksrun (5 markten × M15/H1/H4, 370 gegenereerd, 130 duplicaten) haalden 10 de optimalisatie en 0 de walk-forward; 366 afgewezen, 4 technisch geblokkeerd, 0 geslaagd. De afgeschermde periode vanaf 2024 is niet aangeraakt. Er zijn geen echte prospectieve forwardgegevens (0 forwardportfolio's) en realtime papertrading bestaat niet.

## Regressie
pytest 802 geslaagd; browser-E2E Chrome 1600x900 53/53 en Edge 1920x1080 53/53 (volledig), snel 31/31 op Chrome 1280x720/1600x900/2560x1440 en Edge 1920x1080, dark+light, 0 JS-fouten (evidence/v101/ui, evidence/v101/pytest_final.log).

## Status per succescriterium
| # | Criterium | Status | Bewijs |
|---|---|---|---|
| 1 | Statistiek correct en gekalibreerd | PASS (synthetisch + referentie) | DSR_FINAL_METHODOLOGY.md, STATISTICAL_CALIBRATION_REPORT.md: paper-voorbeeld gereproduceerd, PSR = scipy, FPR ≤ 1,3% in alle nulscenario's |
| 2 | DSR-methode vastgelegd | PASS | Poort = Null-calibrated DSR (NC-DSR, aparte naam), paper-DSR en afhankelijkheidsbewuste DSR ernaast; onbetrouwbaar → INSUFFICIENT_EVIDENCE |
| 3 | Afwijzing / technisch / onvoldoende bewijs | PASS | PASSED, REJECTED, INSUFFICIENT_EVIDENCE, TECHNICAL_BLOCKED met reden, ontbrekend bewijs en volgende test |
| — | Backtest én validatie positief (jouw eis) | PASS | Gecontroleerd op L5 en L6; in de echte run 2 kandidaten tegengehouden |
| 4 | Keten tot forwardregistratie | PASS (alleen technisch, testkluis) | END_TO_END_FORWARD_VALIDATION.md |
| 5 | Forward-herstart zonder dubbele trades | PASS | append-only + hash-keten, crash-rollback getest |
| 6 | Dukascopy-updater | PARTIAL | Taak draait aantoonbaar elke 30 min; 3/9 markten actueel, rest door providerstoringen achter (DUKASCOPY_SYNC_ACCEPTANCE.md) |
| 7 | Multi-market Jesse/plugins | PASS (echte data, korte periode) | MULTI_MARKET_JESSE_PLUGIN_VALIDATION.md |
| 8 | Instrument-/valutaconversie | PASS | koers op handelsmoment; DE40-regressietests |
| 9 | Onbevestigde brokerdata niet als feit | PASS | BROKER_ASSUMPTIONS_REGISTER.md; geldbedragen VOORLOPIG en geld-goedkeuring geblokkeerd |
| 10 | Portfolio-administratie en risico | PASS (fixtures) | PORTFOLIO_RISK_ACCEPTANCE.md, correlatie-API |
| 11 | Fabriek wijst af én herkent signalen | PASS (synthetisch) / echte run: 0 kandidaten | FACTORY_FINAL_VALIDATION.md |
| 12 | UI overzichtelijk en functioneel | PASS | E2E-matrix hierboven |
| 13 | Tests slagen, bewijsstatus duidelijk | PASS | V10_1_FEATURE_AUDIT.md |

## Niet bewezen / open
Zie FINAL_REMAINING_ISSUES.md. Belangrijkste: geen echte kandidaat ooit door L5; positieve route L6–L8 alleen met fixtures; 6/9 markten achter; handelskalenders afgeleid uit data (geen providerbron); contractspecificaties onbevestigd; gedeeltelijke exits, margin, max lot en swap niet gemodelleerd; forward gebruikt vaste FX-koers; placeholder-FX wordt gemarkeerd, niet geblokkeerd; Optuna niet geïnstalleerd (niet vergeleken); AI-assistent niet gebouwd.

## Documenten
V10_1_FEATURE_AUDIT.md, DSR_FINAL_METHODOLOGY.md, STATISTICAL_CALIBRATION_REPORT.md, END_TO_END_FORWARD_VALIDATION.md, DUKASCOPY_SYNC_ACCEPTANCE.md, MULTI_MARKET_JESSE_PLUGIN_VALIDATION.md, BROKER_ASSUMPTIONS_REGISTER.md, PORTFOLIO_RISK_ACCEPTANCE.md, FACTORY_FINAL_VALIDATION.md, FINAL_REMAINING_ISSUES.md; bijgewerkt: FACTORY_PROTOCOL.md, UPDATED_FEATURE_MATRIX.csv, REMAINING_ISSUES.md, TEST_RESULTS.md.
