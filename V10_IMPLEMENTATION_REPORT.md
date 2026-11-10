# V10 — implementatierapport (2026-11-10)

Overzicht; details staan in de deelrapporten.

| Onderdeel | Status | Bewijs |
|---|---|---|
| Automatisch ontdekken verplaatst naar Strategieën (Mijn strategieën / Zelf bouwen / Automatisch ontdekken / Bibliotheek), oude links omgeleid | PASS (echte browser) | V10_UI_REDESIGN.md, evidence/v10/ui/after |
| Onderzoeken = Testen + Analyse (Vergelijken binnen Analyse), geen versleepbare widgets/resize/layout-editor | PASS (e2e controleert dat handles ontbreken) | ux_e2e.py: Chrome 1600×900 49/49, Edge 1920×1080 49/49, snelle runs 27/27 op 1280/1600/2560, dark+light, 0 JS-fouten |
| Testen op meerdere markten, instrumentregister, valutaomrekening (DE40-fix), registratie van selectie-experimenten | PASS (echte data, ontwikkelperiode) | MULTI_MARKET_VALIDATION.md; echte run: breakout_prev_day = MARKET_SPECIFIC; geldbedragen VOORLOPIG |
| Data-supervisor, Windows-taak (catch-up, heartbeat), statuspagina, scenario's | PASS (scheduler/backoff echt) / PARTIAL (slaapstand, langdurige storing: fixtures) | DUKASCOPY_MONITOR_VALIDATION.md, evidence/v10/data |
| DE40-sessiekalender | Hersteld uit echte data (ma–do 00:00–22:15, vr 00:00–22:00 Berlijn) | evidence/v10/data/daily_sync.local.before_de40.json |
| DSR-audit, effectief N, globale vs familie-DSR | PASS (formules tegen paper/scipy) | DSR_METHODOLOGY_AUDIT.md |
| Nested walk-forward met afhankelijkheidsgebaseerde purge/embargo, robuustheid, concentratie, baselines | PASS (unit + benchmark) | WALK_FORWARD_VALIDATION.md, ANTI_OVERFITTING_VALIDATION.md |
| Fabriek gebruikt nieuwe controles, statusladder, pre-registratie, ledger van zoekbeslissingen/datagebruik | PASS | FACTORY_PROTOCOL.md |
| Benchmark A–I (synthetisch) | PASS | A: 0/100 vals-positief; B: 0/20 (3 j), 16/20 (6 j), 20/20 (10 j); F: eff. N 9/50; G: globale gate strenger dan familie |
| Scenario J echte Dukascopy-run (4 markten, M15+H1) | PASS (uitgevoerd) — 0 kandidaten | 419 gegenereerd → 307 L1 → 80 L2 → 45 L3 → 30 L4 → 0 L5 |
| Bevriezen / eindtest / forwardregistratie | PARTIAL — alleen fixtures | geen echte kandidaat haalde L5 |
| Optuna/TPE | NOT TESTED | niet geïnstalleerd; adapter meldt NOT_AVAILABLE |
| pytest | PASS | 686 geslaagd |

## Beslissingen voor jou
1. **DSR-referentievariantie.** De gate gebruikt V_null = 1/T_eff (spreiding van Sharpe zonder vaardigheid); de paper-V wordt ernaast bewaard (`value_paper_v`). Met paper-V als gate faalde een duidelijk ingebouwd voordeel vaak. Eén regel om terug te zetten.
2. **Optuna installeren?** Alleen met jouw akkoord.
3. **Contractspecificaties** blijven aannames; definitieve goedkeuring van geldbedragen vereist brokergegevens.
