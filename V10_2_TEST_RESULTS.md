# V10.2 — Testresultaten (2026-11-11)

Eindregressie na afronding van alle onderdelen, server herstart met de definitieve backend, Docker (`jesse`, `postgres`) draaiend.

## pytest

`C:/Python311/python.exe -m pytest strategy_lab/tests -q -p no:cacheprovider` → **886 passed** in 222 s (V10.1: 802). Log: `evidence/v102/pytest_final.log`. Na PBO-wijziging: **887 passed** (`evidence/v102/pytest_pboall.log`).

Nieuwe testbestanden V10.2:

| Bestand | Onderwerp |
|---|---|
| test_v102_fx.py, test_v102_fx_multimarket.py, test_v102_fx_broker_profiles.py | FX-service, geen 1.0-fallback, FX_RATE_UNAVAILABLE, brokerprofielen + MT5-import |
| test_v102_execution.py, test_v102_execution_partial_parity.py | margin/lot/swap/uren/afwijzing/stress; partial exits backtest = forward |
| test_v102_data_recovery.py | supervisor-tellingen, verouderde fout na succes; live provider-probe: geslaagd in de eindrun (provider antwoordde), SKIPPED bij herhaling 5 min later (timeout) — provider wisselvallig |
| test_v102_shadow_forward.py (10) | scheiding, bevriezing, alleen post-registratie candles, received_at, geen duplicaten, crash-veilig, promote 409 |
| test_v102_portfolio_accounting.py (9) | gedeeld kapitaal, margin, groepsrisico, max posities, stress |
| test_v102_stats.py, test_v102_stats_sim.py | L5-beslisfunctie, deferred DSR/PSR, simulatiecontroles |

## Browser-E2E (`evidence/v102/run_all.sh`, logs `evidence/v102/ui/`)

| Run | Geslaagd | Mislukt | JS-fouten |
|---|---|---|---|
| Chrome 1600×900 volledig | 65 | 0 | 0 |
| Edge 1920×1080 volledig | 65 | 0 | 0 |
| Chrome 1280×720 snel, dark / light | 43 / 43 | 0 | 0 |
| Chrome 2560×1440 snel, dark / light | 43 / 43 | 0 | 0 |
| Edge 1920×1080 snel, dark / light | 43 / 43 | 0 | 0 |

Inclusief: keuze Gevalideerd/Shadow Research, experimenteel-label + bewijsklasse, shadow-detail (met de echte shadowportfolio van 3 strategieën), Home-datatellingen, Data-pagina "Nu synchroniseren", herstart behoudt route en instellingen.

## Bewijs per soort

| Soort | Wat |
|---|---|
| Synthetisch | FX-randgevallen, uitvoeringsmodel, partial-parity, shadow/portfolio-regels, statistische pijplijn (3.900 nulzoektochten + powercellen) |
| Echt historisch | DE40 2014-03 → 2024-01 (744 trades): FX per exit vs M1 EURUSD, max verschil 0,0 (`evidence/v102/fx/real_de40.json`) |
| Provider | Dukascopy wisselvallig (503/timeouts); agentrun herstelde 1 dag, geplande taak daarna bij tot 4/9 actueel (16:30 UTC); kalenders US30/US500/USTEC tegen providerpagina geverifieerd |
| Prospectief | Shadowportfolio geregistreerd, 0 prospectieve dagen → INSUFFICIENT_EVIDENCE |

## Niet getest

- "Volg in Shadow Research" end-to-end vanuit de UI (registratie gebeurde via script `evidence/v102/shadow/register_real.py`; knop en foutafhandeling wel getest).
- Echte MT5-export-import (geen bestand beschikbaar).
- Adaptief hergebruik validatieperiode op pijplijnniveau.

## V10.2b (2026-11-12): PBO-review, FX-fallbacks, Shadow-route

- pytest: **920 passed, 1 skipped** (live provider-probe, provider onbereikbaar). Log `evidence/v102/pytest_v102b.log`.
- Browsermatrix na herstart server met definitieve code: Chrome 1600×900 volledig 65/65; Edge 1920×1080 volledig 65/65 maar **1 JS-fout**; alle snelle runs (Chrome 1280/2560, Edge 1920, dark/light) 43/43 met 0 JS-fouten. De JSON met de foutdetails werd overschreven door de snelle Edge dark-run (zelfde tag), dus de foutmelding zelf is niet bewaard. Herhaalde Edge volledige run: 65/65, 0 JS-fouten (`log-edge-1920x1080-full-rerun.txt`). Status: niet gereproduceerd, oorzaak onbekend.
- Shadow-route ("Volg in Shadow Research", geïsoleerde server poort 8768): Chrome 1600×900 27/27, Edge 1920×1080 27/27, 0 JS-fouten (`evidence/v102/ui/shadow_route/`).
