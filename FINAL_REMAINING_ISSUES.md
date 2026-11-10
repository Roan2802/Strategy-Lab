# Definitieve open punten (V10.1, 2026-11-10)

Geconsolideerd uit DSR_FINAL_METHODOLOGY.md, STATISTICAL_CALIBRATION_REPORT.md, FACTORY_FINAL_VALIDATION.md, END_TO_END_FORWARD_VALIDATION.md, PORTFOLIO_RISK_ACCEPTANCE.md, DUKASCOPY_SYNC_ACCEPTANCE.md, MULTI_MARKET_JESSE_PLUGIN_VALIDATION.md en BROKER_ASSUMPTIONS_REGISTER.md. Zie V10_1_FEATURE_AUDIT.md voor het bewijsniveau per functie. Geen code gewijzigd bij het opstellen.

Prioriteit: P1 = blokkeert marktbewijs of geld/live, P2 = beperkt betrouwbaarheid, P3 = kleiner of cosmetisch.

## A. Marktbewijs

| # | Punt | Label | Prio | Toelichting |
|---|---|---|---|---|
| A1 | Geen echt prospectief forwardbewijs | NOT TESTED | P1 | 0 forwardportefeuilles met echte kandidaten, 0 echte waarnemingen. Elke rendementsuitspraak is INSUFFICIENT_EVIDENCE |
| A2 | Realtime paper trading | NOT IMPLEMENTED | P1 | geen live feed, streaming of orderroutering; `REALTIME_PAPER` niet beschikbaar; live gateway UITGESCHAKELD |
| A3 | Geen kandidaat haalde walk-forward in een echte run | FAIL (resultaat) | P1 | V9 240, V10 419, V10.1 370 gegenereerd; 0 PASSED; hoogste NC-DSR 0,0057 |
| A4 | Positief pad L6-L8 alleen met fixtures | PARTIAL | P1 | bevriezen, eindtest en forwardregistratie van een echte kandidaat nooit uitgevoerd; afgeschermde periode 2024+ niet aangeraakt, dus 0 goedgekeurd |
| A5 | Validatieperiode 2018-2023 is hergebruikt | INSUFFICIENT_EVIDENCE | P2 | alle L5-records `REUSED_FOR_SELECTION`; handmatige iteratie V1-V9 staat niet in het ledger, dus N wordt daar onderschat |
| A6 | Zwakke edges (<= 1 Sharpe jaarlijks) en korte steekproeven zijn niet detecteerbaar | INSUFFICIENT_EVIDENCE | P2 | met <= 6 jaar en >= 100 trials; zulke strategieën eindigen als INSUFFICIENT_EVIDENCE, niet als PASSED |
| A7 | Optuna/TPE ontbreekt | NOT TESTED | P3 | niet geïnstalleerd (`NOT_AVAILABLE`); alleen random search + successive halving; installatie alleen met akkoord |
| A8 | AI-assistent | NOT BUILT | P3 | optioneel |
| A9 | GoldReaper: externe trials onbekend, holdout 7x eerder bekeken | INSUFFICIENT_EVIDENCE | P2 | blijft OOS_PASSED, niet FORWARD_ELIGIBLE; plugin-forwardregistratie bestaat niet |
| A10 | Fabrieksrun 1 (V10.1) onvolledig | - | P3 | supervisor-bug (opgelost); alleen run 2 telt |

## B. Statistiek

| # | Punt | Label | Prio | Toelichting |
|---|---|---|---|---|
| B1 | NC-DSR straft zoekopdrachten die nooit op validatie "kijken" te zwaar (dubbele penalty) | PARTIAL | P2 | in non-adaptief scenario S2 tot ~2x minder power (0,485 vs 0,915); geaccepteerd omdat het ledger niet-adaptiviteit niet kan bewijzen |
| B2 | Dev-only trials tellen mee in N | PARTIAL | P3 | bekende resterende dubbele penalty (DSR_FINAL_METHODOLOGY.md sectie 5) |
| B3 | Onderzoek buiten het ledger telt niet mee | INSUFFICIENT_EVIDENCE | P2 | handmatige V1-V9-iteratie; onderschatting van multipliciteit |
| B4 | Kalibratie alleen synthetisch en beperkt | PARTIAL | P2 | Gauss/t/AR/GARCH, geen sprongen of structurele breuken (buiten 2 volatiliteitsregimes), één bekwame trial per zoektocht, adaptiviteit modelleert alleen mutatie van de beste; power voor regime-only edges ~0 |
| B5 | NC-DSR is niet de paper-DSR | - | P3 | gate wijkt bewust af (V = 1/T_eff); beslissing onderbouwd, maar gebruikers moeten het label lezen; drempel 0,95 ongewijzigd |
| B6 | Contractonzekerheid wordt niet automatisch als beslissende factor herkend | PARTIAL | P2 | staat als waarschuwing op elke kandidaat; ongeldige instrumentdata in L1 is wel TECHNICAL_BLOCKED |
| B7 | Parameterbuurt: INSUFFICIENT_EVIDENCE bij 1-parameterruimte | PARTIAL | P3 | geen plateauclaim mogelijk |
| B8 | Oude ledgerregels zonder rendementsreeksen: effectief N = ruw N | PARTIAL | P3 | uit V10, niet opgelost |
| B9 | Volume in Dukascopy-data constant (proxy 1) | PARTIAL | P3 | volumeproxy's niet informatief; VWAP is feitelijk TWAP; 4 kandidaten TECHNICAL_BLOCKED |

## C. Forward en portefeuille

| # | Punt | Label | Prio | Toelichting |
|---|---|---|---|---|
| C1 | Bestaande forwardportefeuilles moeten opnieuw aangemaakt/geregistreerd worden | BLOCKED (voor die portefeuilles) | P1 | `MODEL_SHA256` gewijzigd; oude portefeuilles weigeren voortzetting; oudere dagen krijgen `LEGACY_UNSEALED_DAYS` |
| C2 | Forward-FX is een statisch bevroren tarief, geen causale reeks | PARTIAL | P1 | P&L, margin en risico wijken af van echte conversie; causale `conversion_series` alleen in research-backtests |
| C3 | PLACEHOLDER_RATE wordt gemarkeerd, niet geblokkeerd | PARTIAL | P1 | tarief 1,0 (DE40, USDJPY) misstelt P&L/margin/risico; harde blokkade is één regel in `enroll` |
| C4 | Eigen limieten (dagverlies, drawdown, symboolrisico) niet standaard actief | PARTIAL | P2 | optioneel, vast vóór eerste gecommitte dag; alleen `max_jump` en `gap_minutes` hebben default |
| C5 | Pending orders reserveren geen margin | PARTIAL | P3 | controle bij fill |
| C6 | Geen tickvolgorde binnen een minuut | PARTIAL | P2 | alleen quote-side OHLC; conservatief (stop wint bij beide) |
| C7 | Correlaties voor oude registraties en forward-correlaties | INSUFFICIENT_EVIDENCE | P3 | oude registraties missen bevroren historische rendementen; forward-paren alleen op fixtures |
| C8 | Forward-E2E gebruikt synthetische zaagtand; kluistijd vs synthetische klok | - | P3 | "geregistreerd na bevriezing" bewezen via kluisinhoud, niet via klokvergelijking |
| C9 | Forwardregistratie voor plugins | BLOCKED | P2 | bestaat niet; ook optimizer en beslislogboek voor plugins |

## D. Data en Dukascopy-sync

| # | Punt | Label | Prio | Toelichting |
|---|---|---|---|---|
| D1 | 6 van 9 markten lopen achter; provider geeft intermitterend HTTP 503/URLError | PARTIAL | P1 | status blijft PARTIAL; retries lopen via taak en in-app worker; USDJPY 6, US30 22, US500 24, USTEC 24, DE40 22, BTCUSD 23 dagen achter (laatste meting) |
| D2 | Kalenders zijn DERIVED_FROM_DATA, niet door de provider geverifieerd | PARTIAL | P2 | geen `VERIFIED_PROVIDER`; feestdagen alleen waar patroon in twee jaar gelijk is (EURUSD 25/31 dec en latere US-feestdagen niet gedekt) |
| D3 | Originele CSV's niet read-only beschermd | PARTIAL | P2 | grootte/mtime ongewijzigd, maar schrijfrechten en geen ACL of read-only attribuut |
| D4 | Task Scheduler-geschiedenislog is uitgeschakeld | NOT TESTED | P3 | systeeminstelling, niet gewijzigd; bewijs = LastRunTime/LastTaskResult en SQLite-rijen |
| D5 | Echte herstart en slaapstand/wake niet uitgevoerd | NOT TESTED | P2 | alleen gesimuleerd (virtuele klok); logon-trigger niet echt geobserveerd |
| D6 | Schade aan het SQLite-bestand zelf (buiten partitie-payload) niet gedekt | NOT TESTED | P3 | |
| D7 | DE40-statustekst toont verouderde JForex-fout tot de volgende stap | PARTIAL | P3 | cosmetisch |
| D8 | US500/DE40 bi5-puntfactor niet live bevestigd | PARTIAL | P2 | uit V8 UX-rapport; niet opnieuw geverifieerd in V10.1 |

## E. Multimarket, uitvoering en broker

| # | Punt | Label | Prio | Toelichting |
|---|---|---|---|---|
| E1 | Contractspecificaties niet broker-geverifieerd, geldbedragen PROVISIONAL | BLOCKED | P1 | 7 blokkerende velden per markt (contract_size, lot_step, min_lot, commission_per_lot_side, margin, swap, slippage); R-multiples, procenten en trade-aantallen bruikbaar |
| E2 | Partial exits niet gemodelleerd | NOT SUPPORTED | P2 | spec weigert de sleutel |
| E3 | Margin, leverage, stop-out en max lot niet gemodelleerd | NOT TESTED | P1 | 100 lots (~100x leverage) wordt geaccepteerd |
| E4 | Overnight swap/financiering niet gemodelleerd | NOT TESTED | P1 | positie van 3 dagen betaalt niets |
| E5 | Commissie en slippage zijn aannames/stress, geen gemeten fills | PARTIAL | P2 | |
| E6 | Jesse zonder spread, slippage, swap; Jesse vs Bid/Ask niet bedrag-voor-bedrag gereconcilieerd | NOT TESTED | P2 | `JESSE_RESEARCH_PROVISIONAL`; `spread_trigger_replayed = false`; ~1,7% entries flippen bij Ask-/Bid-signalen |
| E7 | Prijsschaal-gebonden parameters (afstanden) op andere markten | PARTIAL | P2 | `PRICE_SCALE_PARAMS`/`PRICE_SCALE_EXTREME`; resultaten tonen vooral schaalmismatch |
| E8 | Jesse-spot kan niet groter dan saldo kopen (DE40-fout `FROZEN_SIZE_EXCEEDS_BALANCE`) | PARTIAL | P3 | |
| E9 | Multimarket-checks zijn korte vensters (2-3 maanden) | INSUFFICIENT_EVIDENCE | P2 | geen generalisatiebewijs; alleen lichte kostenstress, geen per-markt walk-forward/DSR |
| E10 | Reoptimise en afwijkend risicomodel voor Jesse/plugins | NOT SUPPORTED | P3 | `risk_normalisation = NOT_APPLICABLE_OWN_SIZING` |
| E11 | Rollover, requotes, afwijzingen, variabele slippage, holidays voor indices/FX | NOT TESTED | P2 | |
| E12 | PBO bij weinig combinaties (4) is zwak bewijs | INSUFFICIENT_EVIDENCE | P3 | uit V9.1 |

## F. UI en overig

| # | Punt | Label | Prio | Toelichting |
|---|---|---|---|---|
| F1 | Nieuwe CSS/JS in shell.css/workflow.js in plaats van aparte v10-bestanden | - | P3 | whitelist in `server.py` |
| F2 | Oude rondleiding noemt nog "Dashboard"/"Data Manager" | PARTIAL | P3 | uit V8; niet opnieuw bevestigd |
| F3 | Jesse-tester-run niet halverwege annuleerbaar | PARTIAL | P3 | uit V8; niet opnieuw bevestigd |
| F4 | News/orderflow/L2 en ML-module | BLOCKED / NOT TESTED | P3 | geen data resp. niet gebouwd |

## Beslissingen die bij de gebruiker liggen

1. Optuna installeren (ja/nee).
2. PLACEHOLDER_RATE in `enroll` omzetten naar harde blokkade.
3. Brokergegevens aanleveren via `evidence/v101/multimarket/broker_overrides.json` voor opening van de geldpoort.
4. Of de afgeschermde 2024+-periode voor één bevroren kandidaat wordt gebruikt (verbruikt de holdout); nu geen kandidaat beschikbaar.
