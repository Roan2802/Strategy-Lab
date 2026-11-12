# V10.2 — Resterende problemen (2026-11-11)

Labels: BLOCKED (extern), OPEN (besluit eigenaar), PARTIAL, NOT TESTED.

## Extern geblokkeerd

| Onderwerp | Status | Toelichting |
|---|---|---|
| Dukascopy-data actueel | PARTIAL (provider wisselvallig) | Provider gaf HTTP 503 / URLError na de eerste poging. Alleen USDJPY 2026-11-06 hersteld. Op 2026-11-11 15:51 UTC 0/9 actueel (achterstand 1–25 dagen); na automatische inhaalruns om ~16:30 UTC 4/9 actueel, 5 achter (status PARTIAL). Geen 429, geen omzeiling; geplande taak probeert verder met backoff. `DUKASCOPY_DATA_RECOVERY.md` |
| Echte brokerdata | BLOCKED | Geen MT5-export of brokerbevestiging aangeleverd. Profielen en import werken (synthetisch getest), maar alle geldbedragen blijven PROVISIONAL / ASSUMED |
| Shadow forward-bewijs | BLOCKED (tijd) | Drie echte strategieën geregistreerd; 0 prospectieve dagen. Eerste dagpartitie komt pas na afloop van de registratiedag |

## Besluit voor jou

| Onderwerp | Toelichting |
|---|---|
| PBO-poort | **Besloten 2026-11-11:** PBO over alle L4-configuraties (drempel 0,5 ongewijzigd). Nul-FPR 0, power Sharpe 3: 95%. `STATISTICAL_SELECTION_AUDIT.md` §6a |
| Optuna | Niet geïnstalleerd; random search + successive halving blijft |

## Gedeeltelijk / beperkingen

- **FX:** `contract_for(symbol, dm)` zonder `ts` levert intern nog 1.0 voor EUR/JPY/CAD-quotes (ContractSpec eist positief getal); zo'n run wordt gemarkeerd en geldmetrics worden geblokkeerd. `portfolio.py` open-positie-overzicht valt terug op bevroren `quote_to_account` als `fx_rate` ontbreekt (alleen relevant vóór de eerste fill); `forward_performance.py` MAE/MFE gebruikt contract-rate van de sluiting. `server.py` `LedgerRequest.quote_to_account` controleert de quotevaluta niet. Hulpscripts in `evidence/v9/consistency/` gebruiken hardcoded 1 (alleen USD-quotes).
- **Uitvoering:** margin/swap/handelsuren/orderafwijzing alleen in de research-backtest; forward modelleert alleen partial exits en eigen margincheck. Gedeeltelijke vulling van één order: NOT_SUPPORTED.
- **Margin portfolio:** aangenomen hefboom per activaklasse (label ASSUMED); commissie/slippage standaard 0.
- **Correlaties shadow:** historisch/OOS = INSUFFICIENT_EVIDENCE (pre-registratie dagrendementen niet bevroren).
- **Kalenders:** US30/US500/USTEC VERIFIED_PROVIDER; DE40 (15 min verschil winter-close) en FX/goud/BTC DERIVED_FROM_DATA; feestdagen afgeleid.
- **REALTIME_PAPER_FORWARD:** niet gebouwd; alleen DAILY_DELAYED_FORWARD.
- **UI:** correlatiematrix, margin- en statistiekdetails niet achter "Details" verplaatst.
- **Statistiek:** adaptief hergebruik van de validatieperiode op pijplijnniveau niet gesimuleerd (alleen rendementsniveau S3). L5-specvarianten delen hypothese-id (<0,1% van N_eff).
- **Scheduler-historie:** Windows Task Scheduler-historie uit; gat 11-10 21:00 → 11-11 15:50 UTC vermoedelijk slaapstand (afgeleid).

## V10.2b (2026-11-12)

Zie `V10_2B_PBO_FX_SHADOW_REPORT.md` → "Onzekerheden die overblijven". Kort: REVIEW_REQUIRED na afloop van een run vereist L6-herrun; adaptief scenario niet onderscheidend; MAE/MFE met sluitkoers; Shadow-UI kiest eerste portfolio zonder keuzelijst; één niet-gereproduceerde JS-fout in Edge.
