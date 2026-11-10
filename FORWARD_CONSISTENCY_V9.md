# Forward-consistentie V9

Datum: 2026-11-09. Alle prijzen komen uit `C:\AI\Dukascopy_data` (alleen gelezen, via `DataManager`; de dagpartities zijn per test in een tijdelijke SQLite-store gezet). Er is niets live of papier gehandeld. Evidence-scripts: `evidence/v9/consistency/` (JSON naast elk script). Regressietests: `strategy_lab/tests/test_v9_consistency.py` (29, synthetisch) en `strategy_lab/tests/test_v9_consistency_real.py` (7, kleine echte slices, samen ca. 11 s).

**Eindoordeel: PARTIAL.** Vier van de vijf controles zijn PASS of PASS met gemeten, financieel verwaarloosbare afwijkingen. Twee echte fouten zijn gevonden en opgelost (scale-in dat in de forwardrun stil werd genegeerd; DE40/US500 konden niet door het forwardpad). De Jesse-forward is niet bedrag-op-bedrag vergelijkbaar met de Jesse-tester (andere prijsmodellen); alleen de signaal- en entrytijdlijn is vergelijkbaar. Contractspecificaties (contractgrootte, lotstap, valutaconversie) zijn niet bronverifieerd en blijven aannames.

## Samenvatting per controle

| # | Controle | Status | Kerncijfers |
|---|---|---|---|
| 1a | Geen look-ahead (prefix-test) | PASS | 53 spec x timeframe-combinaties (5 templates + 3 tot 4 stressspecs, M1 t/m D1, XAUUSD), 4.492 afgekapte herberekeningen, 0 verschillen |
| 1b | Causale aggregatie vanuit M1 | PASS | Labels = openingstijd op UTC-veelvoud, 0,0 verschil met onafhankelijke herberekening, 75 afkappunten: gesloten bars veranderen nooit, forward-aggregator = research-aggregator |
| 2a | Backtest vs forward, M1 | PASS | 893 trades (market, stop, limit; ATR-stop, TP, break-even, trailing, tijd-exit, cooldown): 893 identiek, netto verschil 0,00 |
| 2b | Backtest vs forward, M5 t/m D1 | PASS (afwijkingen gemeten) | 11 cases, 491 trades 1:1 gematcht; 3 intrabar-afwijkingen, netto -0,134 USD (som van absolute verschillen 0,34 USD); 392 trades alleen ander tijdstempel |
| 2c | Scale-in in forward | BUG, opgelost | Forward negeerde `scale_in`: 204 backtest-trades tegen 194, verschil -588,82 USD in 3 dagen; nu geweigerd bij bevriezen |
| 3a | Bid/Ask-kant, slippage, commissie, SL/TP, pending | PASS | 893 echte trades, 100% op alle 8 regels; 182 pending-annuleringen exact op de verwachte candle, 0 ten onrechte |
| 3b | Contractspecificaties | PARTIAL | Alleen tick size is (deels) tegen data gecontroleerd; de rest is aanname; zie tabel 3b |
| 4 | Herstart-idempotentie | PASS | 2 cases x 6 scenario's: herstart per dag, zelfde dag twee keer, batch, crash midden in commit, 3 gelijktijdige processen, latere prijsrevisie: telkens identiek aan de continue run, 0 dubbele trades |
| 5 | Jesse-forward vs Jesse-tester | PARTIAL | Signaal/entry: 226 van 230 Jesse-entries op dezelfde candle (maand), 2 extra forward-entries. Bedragen en exits niet vergelijkbaar (zie 5) |

## 1. Look-ahead en aggregatie

Methode (`check1_lookahead.py`): per timeframe de laatste 1.500 tot 3.000 echte Bid-bars (XAUUSD tot 2026-10-12). De regelengine evalueert de volle reeks; daarna wordt elke gesampelde bar (alle signalen tot 50 per spec, 40 willekeurige, de laatste bars) herberekend op `data[:i+1]`. Vereist: gelijke 3-waardige entrytoestand voor long en short en gelijke exit-snapshots (ATR-stop, ATR-target, trailing-ATR, orderoffset).

| Timeframe | Specs getest | Signalen (long+short, som) | Verschillen |
|---|---|---|---|
| M1 | 9 | 6.900 | 0 |
| M5 | 9 | 6.868 | 0 |
| M15 | 9 | 6.636 | 0 |
| H1 | 9 | 7.120 | 0 |
| H4 | 9 | 6.393 | 0 |
| D1 | 8 | 3.046 | 0 |

De specs (templates uit `research/codegen.py` plus stressspecs) dekken EMA/RSI/ATR/ADX/MACD/Bollinger/Donchian/ROC/VWAP, patronen (engulfing, inside bar, NR4, liquidity sweep, FVG), niveaus (vorige dag hoog/laag, sessie-open, opening range), sessies met DST (America/New_York) en indicatoren op een hoger timeframe (H1 op M1/M5, H4 op M15/H1, D1 op H4). Vier combinaties leveren nul signalen en zijn dus niet inhoudelijk bewezen: `opening_range_breakout` op H1/H4/D1 (de opening range van 30 minuten is korter dan één bar, het template kan daar nooit triggeren: functionele beperking, geen look-ahead) en de niveau-spec op D1. Evidence: `check1_lookahead.json`.

Aggregatie: bars worden gelabeld met hun openingstijd (UTC-veelvoud), een bar sluit pas als de bucket voorbij is. Een afgekapte M1-reeks geeft alleen in de laatste (deel)bucket een afwijkende bar; `forward_rules.aggregate_bid(until=...)` laat die weg en geeft voor alle overige bars exact dezelfde OHLCV als `DataManager._aggregate`. Let op (ontwerp, geen fout): H4 en D1 zijn UTC-buckets, geen broker-dagen (17:00 New York); door de Dukascopy-pauze 22:00-23:00 UTC en het weekend zijn 36 van 180 H4-bars en alle 35 D1-bars in de steekproef korter dan nominaal (bijvoorbeeld 1.380 van 1.440 minuten).

## 2. Backtest tegenover forward (dag voor dag)

Methode (`check2_parity.py`, `parity_lib.py`): echte M1-dagen worden als gesloten dagpartities gepubliceerd (weekenden en feestdagen als `MARKET CLOSED`). De regelstrategie wordt bevroren op de eerste dag (warm-up uit de store, standaard buffer 2N), ingeschreven en met één `advance` per UTC-dag verwerkt. Daartegenover staat één `run_rules_backtest` over hetzelfde bereik met dezelfde parameters, kosten en kapitaal, op `DataManager`-bars: (A) gelijke warm-upstart, (B) 45 dagen eerder gestart (volledige historie, dus met andere indicator-seeding). Contract in beide: grootte 100, tick 0,01, lotstap 0,01, commissie 3,50 per lot per kant, slippage 3 punten (aannames, zie 3b).

| Case | TF | Order | Periode | Trades | Identiek | Netto verschil (USD) | Oorzaak van rest |
|---|---|---|---|---|---|---|---|
| rich_m1_market | M1 | market | 5 dagen | 357 | 357 | 0,000 | - |
| rich_m1_stop | M1 | stop | 5 dagen | 326 | 326 | 0,000 | - |
| rich_m1_limit | M1 | limit | 5 dagen | 210 | 210 | 0,000 | - |
| ema_rsi_m5 | M5 | market | 26 dagen | 54 | 13 | 0,000 | 41x tijdstempel |
| ema_rsi_m15 | M15 | market | 26 dagen | 22 | 4 | 0,000 | 18x tijdstempel |
| orb_m5 (sessie, max 1/dag) | M5 | market | 26 dagen | 20 | 4 | 0,000 | 16x tijdstempel |
| levels_session_m5 (prev-day, trailing) | M5 | market | 26 dagen | 20 | 3 | 0,000 | 17x tijdstempel |
| swing_atr_target_m5 (swing-stop, ATR-target, break-even) | M5 | market | 26 dagen | 219 | 56 | +0,104 | 162x tijdstempel, 1x M1-gap |
| bollinger_m15 | M15 | market | 26 dagen | 54 | 6 | -0,066 | 47x tijdstempel, 1x M1-gap |
| htf_h4_filter_limit_m15 (H4-EMA-filter) | M15 | limit | 26 dagen | 52 | 1 | -0,172 | 50x tijdstempel, 1x M1-gap |
| nr4_h1_stop | H1 | stop | 26 dagen | 19 | 1 | 0,000 | 18x tijdstempel |
| donchian_h1_trailing | H1 | market | 26 dagen | 12 | 1 | -0,000 | 11x tijdstempel |
| donchian_h4_trailing | H4 | market | 8 weken | 8 | 0 | +0,000 | 8x tijdstempel |
| simple_d1 (trailing, tijd-exit) | D1 | market | 4 maanden | 11 | 7 | 0,000 | 4x tijdstempel |
| **Totaal** | | | | **1.384** | **989** | **-0,134** | |

Afwijkingen en oorzaken (alle 395 niet-identieke trades, details per trade in `check2_parity.json`):

| Oorzaak | Trades | Financieel effect | Toelichting |
|---|---|---|---|
| Tijdstempel-granulariteit | 392 | 0,00 | De backtest stempelt in- en uitstap met het bar-openingslabel (bij D1 00:00 ook als de eerste candle om 22:00 valt); forward stempelt de M1-candle van de aanraking. Prijs, richting, reden en grootte zijn gelijk. |
| SL/TP binnen de samengestelde bar, opgelost op M1 | 3 | -0,134 totaal (-0,066, +0,104, -0,172), 0,05 tot 0,17 per trade | Forward test stops per M1-candle; een M1-gap door de stop vult op de gap-open, de backtest kijkt naar de bar (vult op het stoplevel). Gedocumenteerd verschil (1) in `FORWARD_ENGINE_VALIDATION.md`. |
| Entry-prijs, grootte, gemiste of extra trades | 0 | 0,00 | Geen enkele trade ontbreekt aan een kant. |

Indicator-seeding: de backtest die 45 dagen eerder start dan de bevroren warm-up geeft in alle 14 cases dezelfde trades als de backtest met de warm-upstart (0 gemiste of extra trades, netto verschil kleiner dan 0,0001 USD), en dus dezelfde als forward. De zorg dat de afgekapte forwardbuffer (2N) Wilder-indicatoren (RSI/ATR) verschuift, is in deze steekproef niet zichtbaar, terwijl de buffer ruim is doorgeschoven (bijvoorbeeld EMA/RSI op M15: buffer van 516 bars, ongeveer 5,4 dagen, tegen een venster van 26 dagen).

Bug 2c (opgelost): `scale_in` valideert als geldige regelspec en de backtest voegt toe aan winnende posities, maar `forward_rules` kent geen scale-in. Een bevroren strategie met scale-in liep dus stil een ander model. Op dezelfde drie echte dagen: backtest 204 trades en -2.906,60 USD (126 toevoegingen), forward 194 trades en -2.317,77 USD, verschil 588,82 USD, 74 trades met andere entryprijs. Nu weigert `build_configuration`/`validate_registration` zo'n spec met een duidelijke fout (`scale_in is not supported by the forward model`). Evidence: `check2b_scale_in_divergence.json`.

Niet getest: instap halverwege een bucket op echte data (wel in `test_forward_rules.py`, fixtures); minuten zonder Ask-quote (de store laat zulke minuten weg, de backtest houdt de bar voor signalen maar vult er niet; geen voorkomen in de gekozen vensters); portfoliobrede margin- en risicolimieten (bestaan alleen forward; `SHARED_MARGIN` en `RISK_LIMIT` zijn in `test_forward_rules.py` gedekt).

## 3. Bid/Ask-uitvoering

### 3a. Regels, gemeten op echte quotes (`check3_execution.py`)

Voor elke afgesloten trade van drie echte cases (893 trades, 2026-09-21 t/m 09-25) is de fill en exit teruggezocht in de echte M1-quote van die minuut en gecontroleerd. Resultaat: 893 van 893 voor elke regel.

| Regel | Gemeten | Status |
|---|---|---|
| Long entry op Ask, short op Bid (market: `ask_open` + slippage, `bid_open` - slippage) | 893/893 | PASS |
| Exit op de tegenovergestelde kant: long test SL/TP op Bid, short op Ask | 893/893 | PASS |
| Take profit: `max(open, target)` long, `min(open, target)` short, zonder slippage | binnen exit-prijsregel, 893/893 | PASS |
| Stop loss: `min(open, stop)` long, `max(open, stop)` short, plus slippage (gap vult op de open) | binnen exit-prijsregel, 893/893 | PASS |
| SL en TP in dezelfde candle: stop loss wint, trade krijgt `ambiguous=True` | 6 gevallen, 6x stop loss | PASS (regel gedocumenteerd) |
| TP op de candle waarin een stop- of limitorder gevuld wordt: genegeerd (SL blijft actief) | zelfde code in backtest en forward, parity 100% | PASS |
| Slippage alleen op market-entry, stop-fill, STOP_LOSS en TIME_EXIT; niet op TP en limit | via prijsregels, 893/893 | PASS |
| Commissie = 2 x lots x commissie per lot per kant; `net_pnl = bruto - commissie` | 893/893 | PASS |
| Spread op de fill (`spread_open`) = `ask_open - bid_open` van de echte candle | 893/893 | PASS |
| Lotstap en risicogebaseerde grootte (`risk %` van gerealiseerd saldo / stopafstand x contractgrootte, naar beneden afgerond) | 893/893 | PASS |
| Pending-order: gevuld op de eerste aanraking binnen `expiry_bars` | 536/536 | PASS |
| Pending-order: annulering precies op de candle na het venster, zonder aanraking in het venster | 182/182 | PASS |

Financieel gevolg van de stop-eerst-regel: had bij de 6 dubbel-geraakte candles de take profit gegolden, dan was het resultaat samen 329,62 USD hoger geweest (151,32 + 86,61 + 91,69, in cases met netto -4.544,72, -4.306,67 en -2.906,64 USD). Dat is de bewust conservatieve onzekerheidsmarge; M1-OHLC bewijst de volgorde binnen een minuut niet.

Spreads uit de echte data (hele historie, `real_spread_and_price_grid` in `check3_execution.json`): mediane open-spread XAUUSD 0,374 (p99 0,92), EURUSD 0,00004, DE40 2,0, US30 2,86, US500 0,50. De Jesse-run met 1,0 punt stop op XAUUSD heeft dus gemiddeld 0,63 spread (63% van de stopafstand) die Jesse niet kent.

Datakwaliteit die de forwardstore raakt: `DailyPartitionStore.ingest` weigert dagen met een negatieve open- of closespread (fail-closed, dag blijft `WAITING_FOR_DATA`). Historisch gaat het om AUDUSD 774 dagen (laatste 2010-10-29), USATECHIDXUSD 111 (2012-12-16), BTCUSD 30 (2017-12-28), US500 3 (2012-10-10). In recente data staat 1 dag: XAUUSD 2026-10-12 (1 minuut met ask < bid). Een forwardportfolio op die dag blijft staan tot iemand ingrijpt; dit is geen bug maar een operationeel risico dat bekend moet zijn.

### 3b. Contractspecificaties: geverifieerd of aangenomen

Er is nergens in de repository of data een broker-symboolspecificatie. Daarom geldt voor **elke** contractgrootte, lotstap, commissie, slippage en valutaconversie: aangenomen of door de gebruiker ingevoerd. Er is niets verzonnen; de tabel toont wat de code aanneemt en wat de data kan tegenspreken.

| Symbool | Standaard in `research/backtest.py` (grootte, tick, lotstap) | Tick tegen data | Contractgrootte, lotstap | Quote-valuta / conversie |
|---|---|---|---|---|
| EURUSD, GBPUSD, AUDUSD | 100.000, 0,00001, 0,01 | Komt overeen met het prijsraster van de data (0,00001); noodzakelijk, niet voldoende | AANGENOMEN | USD, conversie 1,0 correct |
| XAUUSD | 100, 0,01, 0,01 | NIET GEVERIFIEERD: data heeft 3 decimalen (0,001) | AANGENOMEN | USD, 1,0 correct |
| BTCUSD | 1, 0,01, 0,01 | NIET GEVERIFIEERD: raster 0,1 | AANGENOMEN | USD, 1,0 correct |
| US500, USTEC | 1, 0,1, 0,01 | NIET GEVERIFIEERD: raster 0,001 | AANGENOMEN | USD, 1,0 correct |
| DE40 | 1, 0,1, 0,01 | NIET GEVERIFIEERD: raster 0,001 | AANGENOMEN | EUR-genoteerd, standaardconversie 1,0 is FOUT voor een USD-rekening |
| US30 | geen standaard (valt terug op 1, 0,01, 0,01) | geen | niet beschikbaar | USD |
| USDJPY, USDCAD | geen standaard (terugval 1, 0,01, 0,01) | geen | niet beschikbaar | JPY/CAD, conversie 1,0 zou FOUT zijn |
| XAGUSD | 5.000, 0,001, 0,01 | geen data in `C:\AI\Dukascopy_data` | AANGENOMEN | USD |

Forward-bevriezing vraagt alle contractvelden expliciet (geen standaardwaarden), zodat daar de verantwoordelijkheid bij de gebruiker ligt. De backtest-standaarden uit `research/backtest.py` (niet van mij; geadviseerd aan de eigenaar): US30, USDJPY, USDCAD toevoegen of weigeren in plaats van een stille terugval, en DE40 een omrekenkoers eisen. De tickwaarde (`tick_size x contractgrootte x conversie`) en slippage-in-punten zijn getest in `test_contract_value_maths_and_slippage_points`.

## 4. Herstart-idempotentie

Methode (`check4_idempotence.py`): bevroren echte strategie (`rich_m1_stop`, 5 dagen, 326 trades, en `ema_rsi_m15`, 10 dagen, 11 trades), vergeleken met een continue run op volledige gebeurtenissen, lidstatus, portfoliorij en dagrijen.

| Scenario | rich_m1_stop | ema_rsi_m15 |
|---|---|---|
| Nieuwe engine (herstart) vóór elke dag | identiek | identiek |
| Dezelfde dag twee keer (en herstart ertussen) | identiek, tweede aanroep bevestigt 0 dagen | identiek, 0 dagen |
| Alle dagen in één `advance` | identiek | identiek |
| Crash tijdens het committen van dag k (database-trigger), daarna hervatten | niets bewaard door de crash; na hervatten identiek | idem |
| 3 gelijktijdige processen op dezelfde store | 5 dagen exact één keer, 0 fouten, identiek | 10 dagen, identiek |
| Latere revisie van een al verwerkte dag | genegeerd (eerste publicatie), identiek | idem |
| Dubbele trades | 0 | 0 |

Evidence: `check4_idempotence.json`; tests: `test_restart_double_processing_batch_crash_and_threads_all_equal_the_continuous_run`, `test_a_later_revision_of_a_processed_day_never_changes_the_forward_ledger`, `test_real_restart_and_repeated_days_leave_the_ledger_unchanged`.

## 5. Jesse-forward (bevroren LabBreakout) tegenover de Jesse-tester

Technisch haalbaar op signaalniveau: de opgeslagen Docker-Jesse-runs (`strategy_lab/runs/<id>/trades.csv`) zijn van exact dezelfde `LabBreakout`-bytes (sha256 `b5d77bbb...`, gelijk aan `forward_engine.LAB_SHA256`) op dezelfde Dukascopy-dagen. Er is geen nieuwe Jesse-run gestart; de forwardengine draaide de dagen opnieuw met nul commissie en nul slippage en dezelfde parameters (`check5_jesse_forward.py`).

| Run | Jesse-trades | Forward | Entries zelfde candle | Gemist door forward | Extra forward | Gemiddelde spread (forward) |
|---|---|---|---|---|---|---|
| XAUUSD 2026-09-14 standaard | 13 | 12 | 12 | 1 | 0 | 0,63 |
| XAUUSD interval 30, EMA 34 | 23 | 23 | 23 | 0 | 0 | 0,64 |
| XAUUSD short stoporders (positiegrootte afgerond op lotstap) | 7 | 7 | 7 (Jesse stempelt 1 minuut later) | 0 | 0 | 0,49 |
| DE40, US500, US30 (1 dag) | 11 elk | 11 elk | 33 van 33 | 0 | 0 | 1,9 / 0,5 / 1,8 |
| XAUUSD hele september | 230 | 228 | 226 | 4 | 2 | 0,68 |
| XAUUSD strakke parameters (`pending_expiry_bars` = 1) | 9 | geweigerd | - | - | - | - |

Waarom bedragen niet vergelijkbaar zijn (en wat wel):

1. Prijsmodel. Jesse draait een spotmodel op één prijsreeks (Bid-candles) met 0,06% fee; forward koopt op Ask, verkoopt op Bid en rekent een contractmodel met commissie per lot. Bij een stopafstand van 1,0 en een spread van 0,63 draait een groot deel van de Jesse-winsten in forward om: over de 226 gematchte septembertrades is de som van (forward-beweging - Jesse-beweging) -158,19 prijspunten (gemiddeld -0,70 per trade, gelijk aan de gemiddelde spread). In USD zijn de runs niet gelijk te stellen (positiegrootte 1,0 spotstuk tegen 1,0 lot x 100).
2. Exit-tijdstip. Forward test stop en target al in de candle van de entry; In 212 van 226 septembertrades eindigt forward eerder dan Jesse (156 precies één minuut), in 13 gelijk en in 1 later. Jesse stempelt gevulde pending-orders een minuut later dan de aanrakingscandle (7 van 7). Of dat candle-sluitstempel of een candle verwerkingsvertraging is, is niet afgeleid uit Jesse's broncode.
3. Signaalkant. Forward berekent het signaal op de uitvoerbare kant (Ask voor long, Bid voor short), Jesse op de Bid-candles. Alle 6 afwijkende septembersignalen (4 gemist, 2 extra) hebben `close > EMA` dat omklapt tussen Bid en Ask (bijvoorbeeld 2026-09-14 19:00: Ask 4307,68 onder de EMA 4307,78, Bid 4307,39 erboven); `explanations_*` in `check5_jesse_forward.json`. Dat is 1,7% van de trades.
4. Registratie. Forward weigert wat Jesse's optimizer mag kiezen: `pending_expiry_bars` < 2 en positiegroottes die geen veelvoud van de lotstap zijn (0,2598574... uit een optimizer-run). Dit is een expliciete grens (registratie mislukt zichtbaar), geen stille afwijking.
5. EMA-warm-up. Forward start de EMA bij het aanmelden op de eerste close; Jesse gebruikt 210 warm-upcandles. Na 20 tot 60 candles is het effect verwaarloosbaar; de gemeten signaalverschillen zijn hierboven al volledig door de quotekant verklaard.

Status PARTIAL: signaaltijdlijn gelijk op 98,3% van de entries; trade-uitkomsten en bedragen NIET vergelijkbaar. Een bedrag-op-bedrag vergelijking zou een Jesse-run met Bid/Ask-fills vereisen (bestaat niet) en valt buiten dit onderzoek.

## Gevonden en opgeloste fouten (bestanden die ik beheer)

| Fout | Gevolg zonder fix | Fix | Test |
|---|---|---|---|
| `scale_in`-specs werden bevroren maar door forward niet uitgevoerd | Stil ander model dan de backtest (-588,82 USD op 3 dagen, 10 trades verschil) | `forward_rules._reject_unsupported` in `build_configuration` en `validate_registration` | `test_scale_in_specs_are_refused_by_the_forward_model` |
| `provider_symbol()` accepteert zijn eigen uitvoer niet voor DE40 en US500 (`DEUIDXEUR`, `USA500IDXUSD`) terwijl de bevroren configuratie de providernaam bewaart | `advance`, `eligible_data` en historische referentie falen met "Unknown Dukascopy instrument"; `summarize_forward` slikte de `DataError` en gaf stil `PARTITION_UNVERIFIED` (excursies nooit beschikbaar) | `forward_registry.data_symbol()` (via `DISPLAY_ALIASES`) in `forward_engine._load_day`, `forward_registry.freeze/eligible_data`, `forward_performance.partition`, `forward_reference` | `test_every_display_symbol_survives_freeze_and_advance[DE40,US500,US30,USTEC]` (faalt zonder de fix voor DE40 en US500) |

`MODEL_SHA256` en de bevroren `forward_rules.py` zijn door deze wijzigingen veranderd. De lokale store (`strategy_lab/data_cache/daily_dukascopy.sqlite3`) bevat 0 bevroren strategieen en 0 forwardportfolio's, dus er is niets dat `MODEL_MISMATCH` krijgt.

## Bevindingen buiten mijn bestanden (niet aangepast)

1. `strategy_lab/daily_data.py::provider_symbol` hoort idempotent te zijn (providernaam moet geaccepteerd worden). Mijn workaround staat in de forwardbestanden; ook `research_api.py:871` (`store.watermark(provider_symbol(spec["symbol"]))`) en `server.py:1635` geven een providernaam door aan `read`/`watermark` en zijn voor DE40/US500 niet door mij getest.
2. `research/backtest.py::_DEFAULT_CONTRACTS`: geen US30, USDJPY, USDCAD; DE40 met omrekenkoers 1,0 voor een EUR-instrument; geen waarschuwing behalve de terugvalnotitie (zie 3b).
3. Het `opening_range_breakout`-template geeft op H1 en hoger nul signalen (opening range korter dan een bar); de Visual Strategy Builder zou daar moeten waarschuwen.
4. De backtest lost SL/TP op de samengestelde bar op, forward op M1 (gedocumenteerd, 3 trades, 0,13 USD in 1.384 trades). Een M1-uitvoeringsstap in de backtest zou dit sluiten.
5. `test_server_api.py::test_runtime_build_endpoint_and_index_asset_versions_match_disk` faalde eenmalig tijdens mijn volledige run (statische bestanden werden op dat moment door de andere agent gewijzigd) en slaagde direct daarna apart.

## Evidencebestanden

| Bestand | Inhoud |
|---|---|
| `evidence/v9/consistency/check1_lookahead.{py,json}` | Prefix-test en aggregatie |
| `evidence/v9/consistency/check2_parity.{py,json}`, `parity_lib.py` | 14 parity-cases met alle afwijkende trades |
| `evidence/v9/consistency/check2b_scale_in_divergence.{py,json}` | Scale-in-afwijking en weigering |
| `evidence/v9/consistency/check3_execution.{py,json}` | Fillregels, spreads, prijsraster, contracttabel |
| `evidence/v9/consistency/check4_idempotence.{py,json}` | Herstart-, crash-, gelijktijdigheids- en revisiescenario's |
| `evidence/v9/consistency/check5_jesse_forward.{py,json}` | Vergelijking met opgeslagen Jesse-runs, verklaringen per afwijking |
| `strategy_lab/tests/test_v9_consistency.py`, `test_v9_consistency_real.py` | 29 + 7 regressietests |

Volledige suite: `C:/Python311/python.exe -m pytest strategy_lab/tests -q` geeft 386 geslaagd (333 vooraf, 36 nieuw van mij, de rest van de andere agent).
