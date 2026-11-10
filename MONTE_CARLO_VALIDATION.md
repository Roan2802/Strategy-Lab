# Monte Carlo validatie

Functies: `monte_carlo`, `bootstrap_confidence_interval` in `strategy_lab/research/validation.py`.

## Methode

`monte_carlo(trades, capital, n, method, seed, cost_shock_per_trade, ruin_drawdown_pct)` herbemonstert de reeks trade-P&L (chronologisch op exit-tijd) en bouwt `n` equity-curves vanaf `capital`.

| Methode | Beschrijving | Seriele afhankelijkheid |
|---|---|---|
| `block_bootstrap` (standaard) | stationaire blokbootstrap (Politis & Romano 1994), gemiddelde blokduur n_trades^(1/3) bij `block="auto"` | behouden op korte termijn |
| `bootstrap` | iid trekking met teruglegging | genegeerd |
| `shuffle` | willekeurige permutatie | **genegeerd**: eindequity is in elke run gelijk, alleen het pad verschilt; onderschat waarschijnlijk drawdown-risico |

Uitvoer: verdelingen (gemiddelde, std, min, max, P5/P25/P50/P75/P95) van eindequity, max drawdown (% en absoluut), kans op verlies, equity-banden (P5-P95, gesubsampled op maximaal 50 punten), en **risk of ruin** = aandeel paden met max drawdown >= `ruin_drawdown_pct` (standaard 50%) of equity <= 0. `cost_shock_per_trade` trekt een extra kost van elke trade af.

Bootstrap-CI voor willekeurige statistieken: iid, stationair of moving block (`block=None|"auto"|int`, `method="stationary"|"moving"`).

## Aannames en beperkingen

- Het herbemonstert alleen de historie: nieuwe regimes, grotere verliezen dan ooit gezien of liquiditeitsschokken zitten er niet in.
- Risk of ruin is een eigenschap van de herbemonsterde historie, **geen voorspelling** en geen kans op toekomstige winst of verlies.
- Trades die tegelijk open stonden worden als sequentieel behandeld.

## Wat betekent INSUFFICIENT_EVIDENCE?

Minder dan 30 trades (Monte Carlo) of minder dan 30 waarnemingen / nul variantie (bootstrap-CI): `status = INSUFFICIENT_EVIDENCE`, geen verdelingen.

## Testbewijs

| Onderdeel | Test | Status |
|---|---|---|
| Reproduceerbaar met seed, percentielen geordend, banden geordend | `test_monte_carlo_reproducible_and_percentiles_ordered` (geslaagd) | PASS |
| Shuffle behoudt eindequity, ruin-drempel en kostenshock werken, waarschuwing seriele afhankelijkheid | `test_monte_carlo_shuffle_keeps_final_equity_and_ruin_responds_to_threshold` (geslaagd) | PASS |
| Kleine steekproef | `test_monte_carlo_small_sample_insufficient` (geslaagd) | PASS |
| CI dekt ware gemiddelde (iid, 40 herhalingen, dekking >= 85%) | `test_bootstrap_ci_covers_true_mean_on_iid` (geslaagd) | PASS |
| Blokbootstrap breder dan iid op AR(1) | `test_block_bootstrap_wider_than_iid_on_ar1` (geslaagd) | PASS |
| Monte Carlo met echte tradelijsten van de backtest-engine | niet uitgevoerd | NOT TESTED |
| Overlappende/gecorreleerde posities in het pad | niet gemodelleerd | NOT TESTED |
