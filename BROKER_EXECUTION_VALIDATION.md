# Broker- en uitvoeringsvalidatie (V102)

Code: `strategy_lab/research/broker_profiles.py`, `strategy_lab/research/instruments.py`, `strategy_lab/research/backtest.py`,
`runtime/strategy_lab/execution.py`, `runtime/strategy_lab/rule_engine.py` (`partial_exits`), `strategy_lab/forward_rules.py`.
Tests: `test_v102_execution.py` (18), `test_v102_execution_partial_parity.py` (1), `test_v102_fx_broker_profiles.py` (8).
Alle bewijs hieronder is **synthetisch** (met de hand uitgerekende getallen) tenzij "echt" staat; er is geen brokerdata,
geen live verbinding, geen order en geen accountwijziging. Dukascopy-data is alleen gelezen.

Labels: PASS = getest en bewezen op de beschreven fixtures; PARTIAL = deels; NOT_SUPPORTED = bewust niet gemodelleerd;
NOT_TESTED = niet uitgevoerd; BLOCKED = wacht op gebruikersdata.

## 1. Brokerprofielen en instrumentvelden

| Onderdeel | Label | Toelichting |
| :--- | :--- | :--- |
| Meerdere benoemde profielen (JSON in `strategy_lab/data_cache/broker_profiles/`) | PASS | `create_profile`, `load_profile`, `list_profiles` |
| Handmatige invoer `set_field(profile, symbol, field, value, source)` | PASS | zonder `source` wordt niets opgeslagen; onbekend veld/ongeldige waarde/status wordt geweigerd |
| Velden per symbool | PASS | contractgrootte, tick size/value, quote-/profit-/margin-valuta, lotstap, min/max lot, hefboom, margin (initieel/onderhoud/stop-out), sessies, spread, commissie, slippage, swap long/short/modus/triple-dag/rolloveruur |
| Contractwijzigingen in de tijd (`effective_from`) | PASS | `value_at(profile, symbol, field, ts)`; zelfde datum vervangt, andere datum voegt toe; vóór de ingangsdatum geldt weer de lab-default |
| Status per veld: VERIFIED / DERIVED / ASSUMED / UNAVAILABLE | PASS | elk registerveld heeft `status` (bestaand) en `verification` (nieuw); afwezig = UNAVAILABLE |
| Onbevestigde waarde nooit als feit | PASS | ASSUMED-invoer blijft ASSUMED, een samengesteld veld (swap, margin) is zo sterk als zijn zwakste onderdeel, de `money_gate` blijft dicht |
| Import MT5-symboolspecificatie (CSV `;`/`,`/tab of JSON) | PASS | geen verbinding; bron = `mt5_export:<bestand>:sha256=<12>`; onleesbare cellen worden gerapporteerd, nooit geraden |
| Echte brokerwaarden aanwezig | BLOCKED | geen profiel met echte waarden: alle geldbedragen blijven PROVISIONAL (E1) |

Verwachte MT5-kolommen (hoofdletterongevoelig, willekeurige deelverzameling): `Symbol, Digits, ContractSize, TickSize, TickValue,
VolumeMin, VolumeMax, VolumeStep, CurrencyBase, CurrencyProfit, CurrencyMargin, SwapMode` (0-9 of naam, bijv.
`SYMBOL_SWAP_MODE_POINTS`), `SwapLong, SwapShort, Swap3Day` (MT5 0=zondag..6=zaterdag, opgeslagen als weekdag 0=maandag),
`MarginInitial, MarginMaintenance, Spread, StopsLevel, Leverage, CommissionPerLot`. Waarde 0 voor `MarginInitial`/`Leverage`
betekent "door de broker berekend" en wordt niet als feit opgeslagen. `symbol_map` koppelt brokernamen aan labsymbolen (`GER40.cash` -> `DE40`).

Gebruik in de engine: `instruments.spec_for(symbol, dm, profile="naam", as_of=ts)` en
`instruments.contract_for(symbol, dm, account, ts=..., profile=...)`. `broker_profiles.swap_execution_config` geeft alleen een
swap-blok terug uit VERIFIED data (MT5-modus 5 = depositovaluta per lot; modus 1 = punten, omgerekend met verified
TickValue/TickSize/Digits); anders `(None, reden)`. `margin_execution_config` geeft `CONTRACTUAL` alleen bij een VERIFIED hefboom of margin-rate.

## 2. Uitvoeringsmodel (`execution=` in `run_rules_backtest`)

Standaard UIT (`execution=None`): oude specs en oude resultaten zijn identiek (bestaande 802 tests onaangeroerd groen). Elk blok
draagt een label; ongelabelde swap of margin wordt geweigerd.

| Onderdeel | Label | Gedrag en bewijs |
| :--- | :--- | :--- |
| Initiële margin, gebruikte/vrije margin, margin level, portefeuillehefboom | PASS (SIMULATION_ASSUMPTION tenzij `status: CONTRACTUAL`) | margin = lots x contract x prijs x koers x rate (rate = 1/hefboom of expliciet); diag: `min_margin_level_pct`, `max_used_margin`, `max_portfolio_leverage` |
| Onderhoudsmargin en stop-out | PASS (SIMULATION_ASSUMPTION) | stop-out beoordeeld op het ongunstigste punt van de bar (conservatief), sluit tegen dat punt, `exit_reason = STOP_OUT`; test: 1.98 lot, exit 60.0, pnl met de hand |
| Afwijzing bij te weinig margin | PASS | `on_insufficient: "reject"` -> `rejected_insufficient_margin`, geen trade |
| Sizing op risico EN beschikbare margin | PASS | `cap`: lots = min(risicolots, marginlots) op de stap; `size_limits` toont theoretische vs uitvoerbare lots (2.5 -> 0.99) |
| Min/max lot en lotstap | PASS | afronden omlaag op de stap; boven `max_lot` afgekapt (`MAX_LOT`), onder `min_lot` geweigerd, nooit omhoog afgerond |
| Swap/financiering (long/short, per nacht, triple-dag, weekend) | PASS (VERIFIED of SCENARIO, verplicht label) | per rollover-instant (`rollover_hour_utc`, standaard 22 UTC = ASSUMED) bij elke bar-open; za/zo geen rollover; `triple_weekday` telt 3 (woensdag FX; index vaak vrijdag); test: 5 nachten x 0.5 lot x -7.0 = -17.5 |
| Swap uit echte brokerdata | BLOCKED | zonder VERIFIED profiel alleen scenario; verlies per nacht is dan een aanname |
| Granulariteit swap | PARTIAL | rollover binnen een H4-bar wordt bij de volgende bar-open geboekt |
| Partial exits (meerdere TP-niveaus, fracties, nieuwe SL voor restant) | PASS | spec-sleutel `partial_exits: [{r, fraction, stop_r}]`; hieronder |
| Handelsuren (instap buiten sessie) | PASS | `trading_hours`: instap buiten UTC-uren/weekdagen geweigerd (`rejected_outside_hours`); uitgang (SL/TP) wordt niet geblokkeerd |
| Orderafwijzing | PASS | `reject_every_n` deterministisch (elke n-de poging) |
| Gap-stress op stops | PASS | `stop_gap_points` extra ongunstige fill op STOP_LOSS |
| Pending orders | PASS (bestaand) | stop/limit binnen `expiry_bars`, gap vult op de open; `unfilled_orders` |
| SL en TP in dezelfde candle | PASS (bestaand) | stop eerst, `ambiguous_bars`; bij partial exits wordt het niveau dan niet gecrediteerd |
| Gedeeltelijke fills van één order | NOT_SUPPORTED | geen dieptemodel op OHLC; fills zijn alles-of-niets (`diagnostics.partial_fills_model`) |
| Intrabar-volgorde binnen M1/H1-bar | NOT_SUPPORTED | onbekend; conservatief (stop eerst). Geen precisie geclaimd die OHLC niet geeft |
| Rollover, requotes, holidays, variabele slippage | NOT_TESTED | blijft E11 |

### Partial exits

Spec: `"partial_exits": [{"r": 1.0, "fraction": 0.5, "stop_r": 0.0}, {"r": 1.5, "fraction": 0.25}]` (max 5 niveaus, `r` oplopend,
som fracties <= 1, `stop_r` < `r`, niet combineerbaar met `scale_in`). Semantiek (identiek in backtest en forward):
- Fractie van de INITIELE grootte, afgerond omlaag op de lotstap; niveau onder min-lot overgeslagen; een rest onder min-lot gaat mee met het laatste niveau.
- Niveau = instap + r x risicoafstand. Fill op de uitvoerbare zijde tegen het betere van niveau en bar-open (gap = beter).
- Stop en niveau in dezelfde bar: stop wint, het niveau telt niet (`ambiguous_bars`).
- `stop_r` zet de stop van het restant op instap + stop_r x R (0 = break-even), geldig vanaf de volgende bar/candle; de finale target/trailing/BE blijven gelden voor het restant.
- Commissie per partiële fill (som = 2 x totale lots x tarief); gerealiseerd P&L direct, niet-gerealiseerd op het restant.
- Trade-record: totale `pnl`, `qty` = initiële grootte, `partial_fills` met tijd/prijs/qty/net_pnl/commissie. Met `exit_delay_bars > 0`: NOT_SUPPORTED (ValueError).
- Forward (`forward_rules.py`): zelfde functies (`partial_plan`, `partial_bar_events`); event `POSITION_PARTIAL_CLOSE` per fill; `POSITION_CLOSED` bevat het totaal (zodat bestaande consumenten kloppen) en de balans krijgt partiële P&L niet dubbel.
  Verschil: de forward houdt de gereserveerde margin vast tot de volledige sluiting (conservatief); het V102-`execution`-model (margin/swap/uren/afwijzing) bestaat alleen in de research-backtest.

Pariteit (PASS, synthetisch random-walk, M1, 3 dagen, 8+ gesloten trades en 37 partiële fills): backtest en forward geven dezelfde instap-/uittijd,
prijzen, richting, reden, totale hoeveelheid, totale P&L, dezelfde partiële fills (aantal, tijd, qty, P&L) en dezelfde eindbalans.
Voor TF > M1 geldt hetzelfde verschil als voor het bestaande model (forward beheert exits per M1-candle, de backtest per TF-bar).

### Stresstests (`backtest.run_execution_stress`)

Scenario's: spread x2/x3, extra slippage 5/20 punten, stop-gap 20 punten, orderafwijzing elke 2e, onvoldoende margin (hefboom 2),
instap buiten 08-16 UTC. Elk scenario verandert één ding t.o.v. de baseline en rapporteert delta-netto-winst, geweigerde
orders, stop-outs en `fx_status`. Pending orders en SL+TP-zelfde-candle worden uit de diagnostiek gerapporteerd; gedeeltelijke fills staan als NOT_SUPPORTED.
Dit zijn gevoeligheidsanalyses, geen voorspellingen.

## 3. Wat dit niet bewijst

- Geen enkele brokerwaarde is geverifieerd: margin-, swap- en commissiebedragen zijn aannames of scenario's (E1, E3-E5 blijven PARTIAL/BLOCKED voor geldconclusies).
- Het margin-model is één positie tegelijk per run (de backtester heeft één positie); portefeuille-margin over meerdere runs zit in de forward-portefeuille, niet hier.
- Swap per nacht is in depositovaluta per lot (geen FX-omrekening van swap); tripledag en rolloveruur zijn per broker anders en daarom parameters, niet feiten.
- Stop-out op het ongunstigste punt van de bar is een bovengrens voor de schade, geen replay van het brokerproces.
- Er is niets live gedaan; live-orders blijven uitgeschakeld (`LIVE_READINESS_GATES.md`).
