# Strategy Builder – gebruikershandleiding

De Visual Strategy Builder beschrijft een strategie als JSON-regelspecificatie (`strategy_lab.rules/1`).
Dezelfde spec wordt gebruikt voor (1) de Bid/Ask-backtester, (2) de Inspector-uitleg per candle en
(3) de gegenereerde Jesse-strategie. Alles draait op één regelmotor: `runtime/strategy_lab/rule_engine.py`
(alleen numpy + standaardbibliotheek).

Bestanden: `runtime/strategy_lab/rule_engine.py`, `strategy_lab/research/backtest.py`,
`strategy_lab/research/codegen.py`, tests in `strategy_lab/tests/test_rule_engine.py`.

## 1. Spec-formaat

```json
{"schema":"strategy_lab.rules/1","name":"EmaRsiPullback","symbol":"XAUUSD","timeframe":"M15",
 "direction":"long|short|both",
 "entry":{"long":COND,"short":COND},
 "order":{"type":"market|stop|limit","offset_atr":0.0,"atr_period":14,"expiry_bars":3},
 "stop":{"type":"atr","mult":1.5,"period":14} | {"type":"points","value":2.0} | {"type":"swing","lookback":10},
 "target":{"type":"r_multiple","value":2.0} | {"type":"atr","mult":3,"period":14} | null,
 "trailing":{"activate_r":1.0,"atr_mult":1.0,"period":14} | null,
 "break_even":{"activate_r":1.0} | null,
 "time_exit_bars":null, "cooldown_bars":0, "max_trades_per_day":null,
 "session":{"timezone":"America/New_York","start":"09:30","end":"16:00"} | null,
 "risk":{"percent":0.5},
 "params":[{"name":"ema_fast","type":"int","min":5,"max":50,"step":1,"default":20}]}
```

- Tijdframes: `M1 M5 M15 M30 H1 H4 D1`. Optioneel veld `description`.
- `order`, `target`, `trailing`, `break_even`, `time_exit_bars`, `cooldown_bars`, `max_trades_per_day`, `session` zijn optioneel.
- Overal waar een getal staat mag `{"param":"naam"}` staan; de waarde komt uit `params` (default of override).
  `type` is `int` of `float`; waarden buiten `min`/`max` worden geweigerd.
- `validate_spec(spec)` geeft een lijst foutmeldingen (onbekende sleutels/operatoren/indicatoren, ongeldige
  periodes of bereiken, niet-bestaande parameters, onbekende tijdzone). Lege lijst = geldig.
- `resolve(spec, params)` vult parameters in; `describe(spec)` geeft de Nederlandse regeltekst
  (WANNEER / ENTRY / ORDER / STOP LOSS / TAKE PROFIT / RISK / EXIT).

### Condities

`{"all":[…]}` (EN), `{"any":[…]}` (OF), `{"not":COND}`, vergelijking
`{"left":OPERAND,"op":OP,"right":OPERAND}` of patroon `{"pattern":"…","lookback":N}`.

Operatoren: `>`, `<`, `>=`, `<=`, `crosses_above` (vorige bar `left <= right`, nu `left > right`),
`crosses_below` (vorige bar `left >= right`, nu `left < right`).

Tri-state: elke conditie is PASS (1), FAIL (0) of SKIP (-1). Een operand met NaN (warm-up) geeft SKIP en
nooit een signaal. `all`: FAIL wint van SKIP; `any`: PASS wint van SKIP; `not` laat SKIP ongemoeid.

### Operands

- `{"indicator":"ema","period":20,"source":"close","timeframe":"H1","shift":0}`
- `{"price":"open|high|low|close","shift":1}`
- `{"value":40}`
- `{"level":"prev_day_high","minutes":30}` (`minutes` alleen voor opening range)

`shift` >= 0 (n bars terug); negatieve shift is verboden (zou vooruitkijken zijn).
`source`: `open high low close volume hl2 hlc3 ohlc4` (standaard `close`).

## 2. Indicatoren (exacte definities)

| Indicator | Definitie | Eerste geldige index |
|---|---|---|
| `sma` | gemiddelde van de laatste `period` waarden (incl. huidige) | period-1 |
| `ema` | alpha = 2/(n+1); start = SMA van de eerste n waarden; daarna `ema += alpha*(x-ema)` | n-1 |
| `rsi` | Wilder: gemiddelde winst/verlies over de eerste n veranderingen, daarna `(prev*(n-1)+x)/n`; RSI = 100-100/(1+RS); verlies 0 -> 100 (50 bij ook geen winst) | n |
| `atr` | Wilder over True Range `max(H-L,|H-Cprev|,|L-Cprev|)`; TR[0] bestaat niet; start = gemiddelde TR[1..n] | n |
| `adx` | Wilder +DM/-DM en ATR -> +DI/-DI -> DX = 100*abs(+DI - -DI)/(+DI + -DI) -> ADX = Wilder-gemiddelde van DX | 2n-1 |
| `macd`, `macd_signal`, `macd_hist` | EMA(fast)-EMA(slow) (standaard 12/26, via `fast`/`slow`); signal = EMA(`signal`=9) van de MACD-lijn; hist = macd - signal | 25 / 33 |
| `bb_upper/mid/lower` | SMA(period) +/- `mult` (standaard 2) x populatie-standaardafwijking (ddof 0) | period-1 |
| `donchian_high/low` | hoogste high / laagste low van de laatste `period` bars **inclusief de huidige** (gebruik `shift:1` voor "voorgaande n bars") | period-1 |
| `roc` | (x / x[n bars terug] - 1) x 100 | n |
| `vwap` | sessie-geankerd per UTC-dag: cumulatief (typische prijs (H+L+C)/3 x volume) / cumulatief volume. Is het volume overal 0, dan gelijk gewogen; dit staat in `notes` van het resultaat | 0 |
| `realized_vol` | standaardafwijking (ddof 1) van de laatste `period` log-rendementen, niet geannualiseerd | n |

### Niveaus

Dag = kalenderdag in de sessie-tijdzone (UTC zonder sessie); week = maandag-zondag in diezelfde zone.

- `prev_day_high/low`, `prev_week_high/low`: extreem van de vorige (afgesloten) dag/week met data.
- `session_open`: open van de eerste candle van de sessie (zonder sessie: eerste candle van de dag).
- `opening_range_high/low` (`minutes`): extreem van de candles waarvan de openingstijd in
  `[sessiestart, sessiestart+minutes)` valt. Pas zichtbaar vanaf de eerste candle waarvan de open >= einde range.

### Patronen (bar i = laatste afgesloten candle)

- `inside_bar`: `high[i] < high[i-1]` en `low[i] > low[i-1]`.
- `nr4` / `nr7`: range `high-low` van bar i is strikt kleiner dan de range van de 3 / 6 voorgaande bars.
- `bullish_engulfing`: vorige bar bearish, huidige bullish, `open[i] < close[i-1]` en `close[i] > open[i-1]`.
  `bearish_engulfing` is het spiegelbeeld.
- `breakout_high` / `breakout_low` (`lookback` N, standaard 20): `close[i]` boven de hoogste high / onder de
  laagste low van bars i-N..i-1.
- `liquidity_sweep_high` / `_low` (N, standaard 20): `high[i]` boven het hoogste high van i-N..i-1 maar
  `close[i]` er weer op of onder (spiegel voor low).
- `fvg_bull` / `fvg_bear` (N, standaard 1): `low[j] > high[j-2]` (bull) resp. `high[j] < low[j-2]` (bear) voor
  minstens een bar j in de laatste N bars. Het vullen van de gap wordt niet gevolgd.

Patronen met te weinig historie geven SKIP.

## 3. Causaliteit (geen vooruitkijken)

- Signalen worden geevalueerd op de **slotkoers van bar i** met uitsluitend data <= bar i.
  `evaluate()` mag bars na i wijzigen zonder dat checks of signalen op index <= i veranderen (getest voor elke template).
- **Hogere tijdframes**: base-bars worden in UTC-geankerde buckets `floor(open_ms / htf_ms)` samengevoegd
  (open eerste, high max, low min, close laatste, volume som). Een HTF-bar is pas zichtbaar op base-bar i als
  `htf_einde <= open[i] + base_duur`, dus alleen als de HTF-bar volledig is afgesloten. Daarvoor zie je de
  vorige afgesloten HTF-waarde. `shift` op een HTF-operand telt HTF-bars. De tijdframe moet een veelvoud van het basistijdframe zijn.
- Sessiefilter: lokale tijd via `zoneinfo` (zomer-/wintertijd correct). Een candle telt mee als de **openingstijd**
  in `[start, end)` valt (nachtsessies met start > end lopen over middernacht).
- Warm-up (NaN) = SKIP, nooit PASS.

## 4. Fill-aannames van de backtester

`run_rules_backtest(spec, bid, ask, params=None, *, capital, contract, commission_per_lot, slippage_points, spread_multiplier, entry_delay_bars, exit_delay_bars)`
gebruikt Dukascopy Bid **en** Ask. Signalen worden op de Bid-candles berekend.

- Marktentry: open van bar i+1+`entry_delay_bars`; long koopt op Ask-open, short verkoopt op Bid-open.
- `stop`-order: long triggert als Ask-high >= prijs (fill `max(open,prijs)`), short als Bid-low <= prijs.
  `limit`-order: long triggert als Ask-low <= prijs, short als Bid-high >= prijs. Orderprijs = slotkoers signaalbar
  (+/- `offset_atr` x ATR). Order vervalt na `expiry_bars`. In een bar waarin een stop/limit gevuld wordt geldt alleen
  de stop; de take profit wordt in die bar genegeerd (intrabar-pad onbekend).
- Exits: long op Bid, short op Ask. Stop en target in dezelfde bar -> **stop eerst** (`ambiguous_bars` telt dit).
  Gaps: opent de bar voorbij de stop, dan vult de stop op die open.
- Stop-afstand: `atr` = mult x ATR(period) op de signaalbar vanaf de werkelijke instap; `points` = vaste afstand;
  `swing` = laagste low (long) / hoogste high (short) van de laatste `lookback` bars (Bid). Target: `r_multiple` x
  stopafstand, of `atr`. Ongeldige stop (aan de verkeerde kant) = trade overgeslagen (`rejected_invalid_stop`).
- Break-even en trailing worden na de bar bijgewerkt en gelden vanaf de volgende bar. Trailing = beste prijs sinds instap
  -/+ `atr_mult` x ATR, alleen in gunstige richting. `time_exit_bars`: exit op de open van de bar na N gehouden bars.
- `exit_delay_bars`: een geraakte stop/target/tijd-exit wordt gevuld op de open van de N-de volgende bar.
- Positiegrootte: `risk.percent` van de actuele (gerealiseerde) equity / (stopafstand x contractgrootte x quote-naar-rekening),
  naar beneden afgerond op `volume_step` (via `ContractSpec`). Te klein = overgeslagen (`rejected_min_lot`).
- `commission_per_lot` is per lot per kant; `slippage_points` werkt nadelig op stop/market-fills (niet op TP/limit).
- `spread_multiplier` schaalt de Ask-Bid-afstand rond mid voor fills (stresstest); signalen blijven ongewijzigd.
- Een positie tegelijk; cooldown = minimaal `cooldown_bars` bars na de exit-bar; `max_trades_per_day` telt per lokale dag.
- Geen verzonnen candles: bars zonder Bid worden verwijderd (`missing_bid_bars`); bars zonder Ask blijven voor
  signalen maar er wordt niet op gevuld (`missing_ask_bars`, `skipped_missing_quote`).
- Open positie aan het einde: gesloten op de laatste slotkoers (`END_OF_DATA`).
- Resultaat: `trades` (incl. `mae_r` <= 0 en `mfe_r` >= 0 in R), `equity` (mark-to-market), `metrics`
  (`win_rate` en `max_drawdown_pct` in procenten; `profit_factor`, `recovery_factor`, `sharpe_daily`,
  `sortino_daily` zijn `null` als ze ongedefinieerd zijn), `diagnostics.assumptions`, `spec_sha256`, `params`.

## 5. Templates (`codegen.TEMPLATES`)

| Naam | Idee |
|---|---|
| `ema_rsi_pullback` | EMA-trend + RSI kruist terug door een drempel; ATR-stop, 2R |
| `opening_range_breakout` | New York 09:30, 30 min opening range, ATR-stop, 2R, max 1 trade per dag |
| `donchian_breakout` | slotkoers door het voorgaande Donchian-kanaal, trailing stop |
| `bollinger_mean_reversion` | terug in de band bij lage ADX, break-even + tijd-exit |
| `nr4_inside_bar_breakout` | NR4 + inside bar in trendrichting, stop-order voorbij de signaalbar |

## 6. Gegenereerde Jesse-code

`generate_jesse_strategy(spec)` geeft een volledige strategieklasse (klassenaam = opgeschoonde `name`);
`write_strategy(spec, strategies_root, overwrite=False)` schrijft `runtime/strategies/<Naam>/__init__.py` plus `spec.json`.
Een bestaande, afwijkende strategie wordt geweigerd tenzij `overwrite=True`; dan blijft een kopie
`<bestand>.bak-<tijdstempel>` naast het origineel staan.

- Het spec staat als JSON-literal (`SPEC_JSON`) met `SPEC_SHA256` in de code; bij import wordt de hash gecontroleerd.
- Spec-parameters worden Jesse-hyperparameters (zelfde naam, type, min, max, default).
- `before()` roept `rule_engine.evaluate` aan op `self.candles` (venster: grootste periode x 4 + 50 bars), schrijft de checks met
  `record_candle` (PASS/FAIL/SKIP voor de Inspector) en bepaalt `should_long`/`should_short` uit de laatste bar.
  `go_long/go_short` gebruiken dezelfde `stop_price`, `target_price` en `order_price` als de backtester; na de fill worden SL/TP
  vanaf de werkelijke instap gezet; break-even, trailing en tijd-exit lopen via `update_position`.
- Docstring van de module = `describe(spec)`. Alleen imports van `jesse`, `numpy`, `json`, `math`, `strategy_lab.*`.

Mapping 1:1: signalen, stops en targets komen uit exact dezelfde functies. Verschillen met de rules-backtester komen uit de
fill-engine (Jesse vult op Bid-candles van de route, de rules-backtester op Bid+Ask) en niet uit de regels.

## 7. Beperkingen

- Het gegenereerde Jesse-venster is eindig: recursieve indicatoren (EMA, RSI, ATR, ADX, MACD) kunnen daardoor minimaal afwijken
  van een berekening over de volledige historie; HTF-buckets aan het begin van het venster kunnen gedeeltelijk zijn.
- OHLC-bars kennen het intrabar-pad niet; SL-eerst is conservatief maar niet altijd juist. Geen tick-replay.
- Swing-stop gebruikt Bid-candles ook voor shorts. `fvg_*` volgt geen gap-vulling. `donchian` bevat de huidige bar.
- Quote-valuta wordt niet omgerekend (`quote_to_account` = 1 in de standaardcontracten; geef een eigen `ContractSpec` mee voor
  niet-USD-quotes). Onbekende symbolen krijgen contractgrootte 1.
- Geen financiele data-uitbreiding: geen swaps, geen margin-controle, geen portfolio-effecten tussen strategieen.
- Jesse: een `stop`-order met `offset_atr` 0 valt samen met de huidige prijs en wordt door Jesse als marktorder behandeld. De
  gegenereerde strategie draait alleen in backtest (niet in live trading).
- Resultaten zijn onderzoek op historische data, geen garantie of beleggingsadvies.
