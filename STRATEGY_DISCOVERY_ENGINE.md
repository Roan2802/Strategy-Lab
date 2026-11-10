# Strategy Discovery Engine

Module: `strategy_lab/research/discovery.py`. Tests: `strategy_lab/tests/test_discovery.py`.

De engine genereert een **begrensde, gecontroleerde** set strategie-hypotheses uit expliciete bouwblokken, laat elke
hypothese door dezelfde stages lopen en schrijft **alles** (ook fouten en afgewezen hypotheses) naar het
research-ledger. Het is geen zoekmachine naar "de beste strategie": het is een gecontroleerde manier om veel
hypotheses te testen en bij te houden hoeveel er zijn geprobeerd, zodat selectiebias zichtbaar blijft.

## Families

| id | Beschrijving |
| :-- | :-- |
| `trend_following` | EMA-kruising met ADX-trendfilter |
| `breakout` | Donchian-kanaaldoorbraak (template `donchian_breakout`) |
| `volatility_expansion` | Donchian-breakout terwijl ATR of realized vol oploopt |
| `volatility_contraction` | NR4/NR7 + inside bar, stop-order (template `nr4_inside_bar_breakout`) |
| `mean_reversion` | Bollinger-terugkeer met lage ADX, optioneel RSI-bevestiging (template `bollinger_mean_reversion`) |
| `vwap_pullback` | Koers herneemt de VWAP in de richting van de EMA-trend |
| `session_breakout` | Opening-range breakout New York of Londen (template `opening_range_breakout`) |
| `liquidity_sweep_proxy` | **PROXY**: patroon `liquidity_sweep_*` op OHLC; geen orderflow |
| `price_action_reversal` | Engulfing bij vorige daghoogte/-laagte |
| `time_of_day` | Zelfde momentumregel in verschillende sessies |
| `regime_filtered_momentum` | ROC-kruising met ADX-filter en optioneel HTF-EMA |

Elke familie heeft `id`, `name`, `description` (Nederlands), `default`, `base_template` (sleutel uit
`codegen.TEMPLATES` of een inline spec) en `mutations`. Alle basis-specs en elke afzonderlijke mutatiewaarde worden
in de tests tegen `rule_engine.validate_spec` gevalideerd.

## Bouwblokken (mutaties)

Een mutatie is `sleutel -> lijst kandidaatwaarden`. De sleutel is een van:

- een **parameternaam** uit `spec.params` (zet de default; min/max worden zo nodig verbreed), bv. `channel`, `ema_fast`;
- een **spec-pad** met punten, bv. `stop.mult`, `target.value`, `trailing`, `session`, `order.expiry_bars`, `time_exit_bars`;
- een **benoemd blok**: `htf_filter` (HTF-EMA(50) trendfilter aan/uit; H4 voor M15/M30/H1, H1 voor M1/M5, D1 voor H4, niets voor D1),
  `rsi_confirm`, `nr_pattern` (nr4/nr7), `vol_measure` (atr/realized_vol).

Elke hypothese bewaart precies wat er t.o.v. de ouder (`parent`) is veranderd in `mutations`.

## Hypothese-generatie

`generate_hypotheses(families, symbols, timeframes, max_candidates, seed)` is deterministisch (zelfde invoer + seed =
zelfde lijst). Per familie komt eerst de baseline, daarna de mutatiecombinaties in een met `seed` geschudde volgorde.
Round-robin over families x symbolen x timeframes, uniek (een `hypothesis_id` komt maar een keer voor) en
afgekapt op `max_candidates`. `hypothesis_id` = eerste 16 hex van sha256 over de canonieke spec (zonder `name` en
`description`) + symbool + timeframe.

## Pipeline per hypothese

Elke stage wordt met status, detail en duur vastgelegd in `statistics.stages`.

1. **Spec-validatie** (`validate_spec`); ongeldig = `ERROR`.
2. **Codegeneratie + `compile()`** van de Jesse-code. De code wordt **nooit uitgevoerd** (geen `exec`/`import`); alleen de sha256 wordt bewaard.
3. **Smoke-backtest** op de eerste 10% van de in-sample periode (minimaal 200 bars). Een exceptie geeft `ERROR`.
4. **Datasufficientie**: minimaal `criteria.min_bars` in-sample bars en maximaal 5% ontbrekende Ask-candles.
5. **Volledige in-sample backtest** (eerste `1 - oos_fraction`, standaard 70%). Criteria: `min_trades`, netto > 0, `min_profit_factor`, `max_drawdown_pct`.
6. **Kostenstress** op in-sample: spread x1,5 en entry 1 bar later.
7. **OOS-backtest** op de laatste `oos_fraction` (standaard 30%), met warm-up-bars uit in-sample voor de indicatoren; alleen trades die na de splitsing openen tellen mee.
8. **Scoring** (zie hieronder).
9. **Beslissing**.

De OOS-periode wordt alleen aangeraakt voor hypotheses die de in-sample criteria en de kostenstress overleven en
dan precies een keer. Bij een eerdere afwijzing staat `oos_metrics` op leeg en is de stage `SKIP`.

## Statuslabels

- `CANDIDATE`: in-sample criteria gehaald, kostenstress netto > 0 en OOS netto > 0. **Dit is geen gevalideerde strategie**, alleen een kandidaat voor volledige validatie.
- `REJECTED`: met concrete reden, bv. `te weinig trades (12 < 30)`, `in-sample verliesgevend`, `profit factor te laag`, `drawdown te hoog`, `kostengevoelig: verlies bij spread +50%`, `OOS verliesgevend`, `onvoldoende data`.
- `ERROR`: spec ongeldig, exceptie in een stage, data laden mislukt, of geannuleerd tijdens verwerking.

## Budgetbeheer

`budget = {max_candidates, max_minutes, max_workers, max_ram_mb}`

- `max_candidates`: hard plafond op het aantal gegenereerde/gelanceerde hypotheses.
- `max_minutes`: na dit tijdsbudget worden geen nieuwe hypotheses meer gestart (lopende worden afgemaakt).
- `max_workers`: `ThreadPoolExecutor`; frames worden per (symbool, timeframe) een keer geladen en read-only gedeeld.
- `max_ram_mb`: met `psutil` wordt het RSS van het proces gecontroleerd voor elke nieuwe hypothese; boven de limiet wordt niet gelanceerd (`ram_pauses` in de samenvatting) en bij geen lopend werk stopt de run met reden "geheugenlimiet". Zonder `psutil` wordt de limiet **niet afgedwongen** (`ram_note` meldt dat).
- Annulering: `ctx.check()` tussen alle stages; de run stopt met `stopped_reason = "geannuleerd"`, onderbroken hypotheses worden als `ERROR` ("geannuleerd tijdens verwerking") vastgelegd.

## Ledger

Elke hypothese wordt met `ledger.record(kind="DISCOVERY", ...)` vastgelegd: `hypothesis_id`, `family`, `symbol`,
`timeframe`, `start`, `end`, `spec`, `spec_sha256`, `params`, `mutations`, `code_sha256`, `dataset_sha256` (manifest +
timeframe + venster), `cost_model`, `data_requirements` (`["Dukascopy M1 Bid/Ask OHLC"]`), `status`,
`rejection_reason`, `metrics` (in-sample), `oos_metrics`, `score` en `statistics` = `{n_trials_in_run, psr, dsr,
stages, components}`. Test: aantal ledger-entries == `tested`.

## Score

```
score = sum(w_i * c_i) / sum(w_i)      c_i in [0, 1] (geknipt; ontbrekend = 0), w_i >= 0
```

De score **rangschikt** hypotheses onderling en is **geen kans op winst of succes**. Gewichten staan in
`DEFAULT_WEIGHTS`, zijn per run te overschrijven (`config["weights"]`) en worden in de samenvatting teruggegeven.

| Component | Gewicht | Definitie (0..1) |
| :-- | --: | :-- |
| `risk_adjusted_return` | 0,15 | in-sample dagelijkse Sharpe / 3 |
| `drawdown` | 0,10 | 1 - max drawdown% / `max_drawdown_pct` |
| `recovery` | 0,08 | recovery factor / 5 |
| `profit_factor` | 0,10 | (PF - 1) / 1,5 |
| `sample_size` | 0,08 | trades / (5 x `min_trades`) |
| `yearly_stability` | 0,08 | aandeel winstgevende perioden (kalenderjaren bij >= 2 jaar data, anders kwartalen, anders maanden; < 2 perioden = 0) |
| `oos_performance` | 0,15 | 0 als OOS netto <= 0, anders 0,5 + 0,5 x (OOS-PF - 1) / 1,5 |
| `cost_sensitivity` | 0,08 | netto onder stress / netto basis |
| `parameter_stability` | 0,08 | eerste parameter +/-20% op in-sample; gemiddelde van netto-buur / netto-basis (geknipt) |
| `regime_robustness` | 0,05 | aandeel van de twee realized-vol-helften (mediaansplitsing) met positief netto |
| `dependency` | 0,05 | 1 - hoogste positieve correlatie van dagelijkse P&L met reeds geaccepteerde kandidaten in deze run |

`dependency` hangt af van de volgorde van acceptatie; bij `max_workers > 1` is die volgorde niet deterministisch.

## Selectiebias en statistiek

Wie de beste uit veel hypotheses kiest, overschat de werkelijke prestatie. Daarom worden PSR en DSR berekend op de
dagelijkse in-sample rendementen. De DSR gebruikt `n_trials` = aantal hypotheses in deze run + `ledger.n_trials()`
voor de run, en de variantie van de Sharpe over eerdere ledger-entries (geschaald naar per-observatie; zonder
voorgaande entries `1/n`). Een lage DSR betekent dat het resultaat door toeval verklaard kan worden. Ook een enkele OOS-test
is een steekproef: bij veel hypotheses slagen er toevallig enkele. Een `CANDIDATE` moet daarna nog door de volledige
validatie (walk-forward, Monte Carlo, parameterstabiliteit, drop-best-trades, aparte holdout).

## Beperkingen

- Alleen OHLC Bid/Ask-candles; **geen orderflow, orderboek of tick-volgorde**. `liquidity_sweep_proxy` is een prijspatroon-proxy, geen meting van liquiditeit.
- Intrabar-volgorde is onbekend; SL en TP in dezelfde bar = SL eerst (conservatief).
- Kosten: alleen spread (Bid/Ask), optioneel commissie; slippage is 0 tenzij de backtest anders is ingesteld.
- Vaste contractspecificaties per symbool; onbekende symbolen krijgen een standaardspecificatie.
- Het aantal mutaties is bewust klein; hogere aantallen vergroten de selectiebias en moeten in `n_trials` terugkomen.
- `max_ram_mb` werkt alleen met `psutil`.

## Gebruik

```python
from strategy_lab.research import discovery
summary = discovery.run_discovery(ctx, config, load_frames, ledger)
```

`load_frames(symbol, tf, start, end)` geeft `(bid_df, ask_df, manifest)` (zoals `backtest.load_bid_ask`).
De samenvatting bevat `tested, candidates, rejected, errors, cancelled, stopped_reason, top (max. 10; kandidaten eerst, dan op score), weights, summary_nl`.

## Eén-klik discovery over meerdere paren (historische OOS)

Config accepteert `in_sample: {start, end}` en `out_of_sample: {start, end}` (ISO-datums, samen op te geven; OOS mag
niet overlappen met IS). Zonder die vensters geldt het oude gedrag (`start`/`end` + `oos_fraction`). De API
(`POST /api/discovery/run`) gebruikt standaard IS 2004-01-01..2024-01-01 en OOS 2024-01-01..vandaag, timeframes
`M15,H1` en alle beschikbare datasets uit XAUUSD, EURUSD, GBPUSD, USDJPY, US30, US500, USATECHIDXUSD, DE40, BTCUSD.
`max_candidates` 1..500, `max_minutes` 1..240, `max_workers` standaard 3.

- Per run wordt elk `(symbol, timeframe)`-frame **één keer** geladen (IS-start min 30 dagen warm-up tot OOS-einde) en
  per stage als slice (view) gebruikt.
- Per symbool wordt geklemd op beschikbare data: IS-start = max(gevraagd, eerste candle + 30 dagen). Minder dan 1 jaar IS
  of 60 dagen OOS (of geen candles) = elke hypothese van dat symbool `REJECTED` met reden `onvoldoende data voor <symbool>`.
- OOS draait met de in-sample gekozen, ongewijzigde spec/params, op een prefix van 30 dagen voor de OOS-start
  (`trade_from` = OOS-start; geen IS-trades tellen mee) en wordt per hypothese maar één keer geëvalueerd.
- Hypotheses worden round-robin over (symbool x timeframe) verdeeld; `max_candidates` is het totaalbudget.
- Resultaat: `top` (max. 25, kandidaten eerst, dan score; met `spec`, `metrics`, `oos_metrics`, `rejection_reason`,
  `equity_is`/`equity_oos` max. 300 punten `{time, equity}`), `by_symbol` (getest/kandidaten/afgewezen/fouten),
  `periods` (effectieve IS/OOS per symbool en timeframe) en `validation: "HISTORICAL_OOS"`.
- Let op: dit is een **historische** out-of-sample test, geen prospectieve forward test. Selectie uit veel hypotheses
  blaast resultaten op (DSR wordt berekend); kandidaten moeten nog Valideren en een bevroren forward test doorstaan.
- Meting (XAUUSD M15, 2004-2024 IS + 2024-heden OOS, echte data): laden 2,3 s eenmalig; een volledige hypothese
  (smoke, IS, kostenstress, parameterbuurtschap) kost circa 7 s CPU; geen extra optimalisatie nodig.
