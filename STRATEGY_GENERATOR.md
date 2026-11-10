# Strategy Generator (`strategy_lab/research/generator.py`)

Bounded, diverse rule-spec candidate generator for the Strategy Discovery Factory. Candidates are plain
`strategy_lab.rules/1` specs, so the Bid/Ask backtester, codegen (Jesse) and the forward engine run them unchanged.

## Contract
```python
families_catalog() -> list[dict]            # id, group, label_nl, description_nl, mechanisms, suitable_symbols, iq_sources, default_timeframes
generate(config, seed) -> list[candidate]   # config: symbols, timeframes, families ('all'|ids), max_candidates, diversity, allow_scale_in
level1_check(candidate, bid, ask, *, existing_signatures=(), run_backtest=True) -> {ok, reasons, checks, warnings, stats}
dedup(candidates, existing_signatures=set()) -> (kept, dropped_with_reason)
baselines(symbol, timeframe, exits_from=None) -> list[candidate]   # buy&hold proxy, random entry with the same exits, EMA 20/50 cross
run_smoke(out_path, ...)                    # python -m strategy_lab.research.generator <out.json>
```
Candidate fields: `id` (sha of family+spec), `name`, `symbol`, `timeframe`, `family`, `group`, `spec`, `param_space`,
`blocks`, `iq_sources` [{dossier, component, provenance, note_nl}], `passport` {base_template, changes[{what, from, to}],
exit_profile}, `hypothesis_nl`, `rules_nl` (`rule_engine.describe`), `complexity`, `signature`, `proxies_nl`, `allow_scale_in`.

## Families (55, 7 groups)
Each family is its own entry mechanism with its own bounded parameters (not a parameter tweak of one algorithm;
`test_families_are_distinct_mechanisms` enforces distinct entry structures).

| group | families |
|---|---|
| trend (7) | MA cross, MA slope (linreg), HH/HL swing structure, ADX strength, pullback to mean, MTF trend filter, momentum persistence |
| breakout (10) | ORB (home session), Asian range, London (pre-London range), NY (London range broken after NY open), prev day/week H/L, Donchian, consolidation box, NR4/NR7 (stop order), ATR expansion, breakout + retest |
| mean_reversion (7) | Bollinger re-entry, RSI extremes, VWAP deviation fade, ATR-extreme band, z-score, failed breakout, return to range mid |
| price_action (11) | liquidity sweep, MSS, swing breakout, FVG retest, inside bar, engulfing at location, pin rejection, range highs/lows, three-touch reclaim, CRT 3-candle, reversal at prev-day level |
| statistical (8) | autocorrelation regime, Hurst + ACF breakout, hour of day, day of week, volatility clustering, trend/range switch (efficiency ratio), momentum exhaustion, large-move (return z-score) reversal |
| vwap_volume (6) | VWAP pullback, VWAP breakout, VWAP deviation momentum, volume contraction proxy, volume expansion proxy, effort vs result proxy |
| hybrid (6) | ORB + ADX + ATR stop, VWAP drift pullback, sweep + MSS, range break + realized-vol contraction, momentum + regime filter, stat MR + session |

Exit architecture is generated separately: ATR / swing / fixed-% stop; R-multiple, ATR, level (opposite band, range
mid, VWAP, C1 high) or no target + trailing; break-even; time exit; session close (approximated with
`time_exit_bars` = session length, the backtester has no clock exit); overlays cooldown, max trades/day, HTF EMA
filter, session window. Sizing is always `risk.percent` (0.5%) of equity. Grid/martingale/averaging down are never
generated; winner-only `scale_in` only with `allow_scale_in=True`.

## Diversity and dedup
Round robin over groups, then families, rotating symbol x timeframe; per-family cap = ceil(1.5 x budget / families).
`signature` = family + symbol + timeframe + spec structure with every number and param value removed, so parameter
tweaks of the same structure are duplicates. Near-duplicates: same structure across families with all resolved numbers
within 10%. Complexity (entry leaves + params + exit features) is reported but never rewarded.

## Level 1 gate
`schema` (validate_spec, opt-in scale_in), `data_available` (>=300 bars, Ask coverage, volume-proxy informative),
`indicators_defined` (every condition/indicator/exit array defined somewhere in the slice), `no_lookahead` (prefix
truncation + future mutation at 4 cut points must leave the past identical), `reproducible` (two evaluations and two
backtests identical, id matches spec hash), `no_contradiction` (long and short on one bar, identical sides, entry true on
>60% of tradable bars unless capped/baseline, level target never on the profit side), `not_duplicate`.

## rule_engine extensions (backward compatible, causal)
Indicators: `zscore`, `linreg_slope`, `hurst` (aggregated-variance PROXY), `autocorr` (`lag`), `efficiency_ratio`,
`ret_zscore`, `volume_ratio`, `range_atr`, `range_width_atr`, `swing_high`/`swing_low` (pivots confirmed after k bars),
`range_mid`, `vwap_dev_atr`, `atr_band_lower`/`upper`. Levels: `prev_day_close`, `day_open`, `window_high/low/mid`
(start/end/timezone, exposed only after the window closes). Patterns: `pin_bull/bear`, `crt_bull/bear`,
`fvg_bull/bear_retest`, `range_touch_high/low` (touches, tolerance_atr), `swing_break_*`, `mss_*`, `structure_up/down`,
`random_entry` (seeded hash of bar time, baseline only). Conditions: `calendar` hour/weekday. Stop `percent`; target
`level` (operand; ignored when on the wrong side of entry). All existing specs evaluate identically (golden check over
codegen templates + 120 discovery hypotheses); every new feature has truncation + mutation tests on synthetic and real
XAUUSD data (`strategy_lab/tests/test_generator_rule_features.py`).

## IQ Capital components
`strategy_lab/research/components/iq_components.json`: 45 dossiers, 186 components (60 SOURCE_RULE, 68
RESEARCH_VARIANT, 24 PROXY, 34 NOT_IMPLEMENTABLE, each PROXY/NOT_IMPLEMENTABLE with a reason). Grade A/B/C = rule
completeness only. All transfers to Dukascopy CFD/FX are NEW hypotheses. Candidates using components are described as
"geinspireerd op ..., eigen onderzoeksvariant; GEEN exacte reconstructie".

## Data caveat
Dukascopy volume in this dataset is constant 1 per M1 bar: volume-proxy families are rejected by level 1
(`data_available`) and VWAP is effectively a TWAP (labelled PROXY).

## Smoke (evidence/v9/factory/generator_smoke.json)
200 candidates, XAUUSD/US30/DE40 x M15/H1, seed 7, dev slice 2019-01-01..2019-03-01: 195 level-1 OK, 5 rejected
(all `data_available`, volume proxy uninformative); 18/18 baselines OK. Level 1 says nothing about profitability.
