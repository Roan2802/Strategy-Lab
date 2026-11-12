# FX-omrekening validatie (V102)

Code: `strategy_lab/research/fx.py` (`FxService`, `FxRateUnavailable`, `Conversion`, `convert_trades`, `route`),
aangesloten in `instruments.py` (`conversion_series`, `convert_trades`, `contract_for`), `backtest.py` en `multimarket.py`.
Tests: `strategy_lab/tests/test_v102_fx.py` (19), `test_v102_execution.py` (FX-deel), `test_v102_fx_multimarket.py` (2).
Echt-data-check: `evidence/v102/fx/real_de40.py` -> `real_de40.json`. Labels: PASS / PARTIAL / NOT_TESTED / BLOCKED;
bewijs is steeds als **synthetisch** of **echt** gemarkeerd. Dukascopy-data is alleen gelezen.

## 1. Contract van `FxService`

- `rate(from, to, ts, side="mid") -> Conversion`: causaal, de OPEN van de laatste M1-bar met openingstijd <= `ts` (nooit een latere bar).
  Naïeve tijden gelden als UTC; andere tijdzones worden naar UTC omgezet (DST-grens getest).
- Route: direct paar, omgekeerd paar (USD->EUR = 1/EURUSD), JPY (USDJPY), anders via USD (JPY->EUR = 1/USDJPY x 1/EURUSD, bron `CROSS:USDJPY*EURUSD`).
  Bekende paren: EURUSD, GBPUSD, AUDUSD, NZDUSD, USDJPY, USDCAD, USDCHF.
- Nooit 1.0 behalve bij dezelfde valuta (`source = IDENTITY`). Ontbrekende data, geen route, bar ouder dan `max_staleness` (standaard 72 uur),
  prijs <= 0 of NaN, of ask < bid -> `FxRateUnavailable`. Een ongeldige laatste bar valt niet terug op een oudere prijs.
- `convert(amount, from, to, ts, side) -> (waarde, Conversion)`; `try_convert` geeft `(None, Conversion(status="FX_RATE_UNAVAILABLE", rate=NaN))`.
- `Conversion`: `from_ccy, to_ccy, ts, rate, side, source ('DUKASCOPY_M1:<paar>' | 'IDENTITY' | 'CROSS:<p1>*<p2>'), data_version
  (sha256 van bid- en ask-bronbestand uit het manifest, 'UNVERSIONED' zonder manifest), uncertainty (som van spread/2 relatief per been), status`.
- Extra (additief): `side="conservative"`, `convert_portfolio(balances, to, ts)` (totaal of None, nooit een deelsom), `series(...)` voor backtests.

### Bid/Ask-regel bij het omrekenen van winst

`side` benoemt de prijs van het geciteerde paar die wordt gebruikt (`mid` standaard voor onderzoek; broker-opslag op conversie is
UNAVAILABLE en niet gemodelleerd). `side="conservative"` kiest per been en per teken van het bedrag de realistische
afrekenzijde: een winst levert **minder** rekeningvaluta op (basis verkopen tegen Bid, basis kopen tegen Ask = 1/Ask), een verlies **meer**.
Voorbeeld (synthetisch): +100 EUR -> USD tegen Bid, -100 EUR -> USD tegen Ask. De `uncertainty` geeft de orde van het verschil.

## 2. Waar de vaste 1.0 / statische omrekening is vervangen

| Plek | Was | Nu | Label |
| :--- | :--- | :--- | :--- |
| `backtest.run_rules_backtest` sluiting zonder koers | laatst bekende koers of `quote_to_account` (1.0) | trade krijgt `fx_status=FX_RATE_UNAVAILABLE`, `pnl=None`, R op prijsbasis (`r_basis=PRICE_ONLY`); run: geldmetrics geblokkeerd | PASS (synthetisch) |
| idem, equity-mark en scale-in zonder koers | `Q(j) or q2a` | equity wordt voortgedragen, scale-in overgeslagen; nooit 1.0 | PASS |
| idem, koerslookup | `ffill` zonder limiet; ongeldige print werd overbrugd | laatste koers op of vóór de bar-open, max `conversion_max_staleness` (72 u), ongeldige print = niet beschikbaar | PASS |
| idem, geen conversiereeks bij EUR/JPY/CAD-quote | bedragen in quotevaluta getoond als USD | `fx_status=FX_RATE_UNAVAILABLE`, `metrics.money_blocked`, geldvelden `None`; quotevaluta-cijfers apart en gelabeld (`quote_currency_metrics`, EUR) | PASS |
| `instruments.conversion_series` | eigen USD-been-tabel | `fx.FxService.series` (zelfde uitkomst, test ongewijzigd groen); `None` = niet beschikbaar | PASS |
| `instruments.convert_trades` | `rate_at` (ValueError) | `fx.convert_trades` (`FxRateUnavailable`, optionele staleness) | PASS |
| `instruments.contract_for` | altijd 1.0 | echte `FxService`-koers bij `ts=...`, anders expliciet PLACEHOLDER (zie 4); met `conversion=` genegeerd | PARTIAL |
| `multimarket._run_cell` (regel-engine) | zonder reeks: doorrekenen als `UNCONVERTED` | cel `status=FX_RATE_UNAVAILABLE`, `money_blocked`, geen `metrics` | PASS |
| `multimarket` (Jesse/plugin-adapters) | reeks ontbrak: `UNCONVERTED`, bedragen doorgerekend | idem geblokkeerd; staleness 72 u bij conversie op exit-tijd | PASS |

## 3. Testmatrix

| Eis | Resultaat | Soort |
| :--- | :--- | :--- |
| EUR->USD (mid, Bid, Ask, conservative) | PASS | synthetisch |
| USD->EUR (inverse) | PASS | synthetisch |
| JPY->USD en USD->JPY | PASS | synthetisch |
| Kruis via USD (JPY->EUR), bron `CROSS:`, onzekerheid groter dan enkel been | PASS | synthetisch |
| Verschillende dagen, causaliteit (tussen twee minuten = eerdere bar; vóór eerste bar = onbeschikbaar) | PASS | synthetisch |
| Ontbrekend paar, geen route, geen datamanager, `try_convert` status | PASS | synthetisch |
| Ongeldige prijs (0, negatief, NaN, ask<bid) -> raise, volgende geldige bar werkt | PASS | synthetisch |
| Datagaten: binnen 72 u gebruikt de laatste bar, 108 u = stale, ruimere staleness expliciet | PASS | synthetisch |
| Weekend binnen staleness | PASS | synthetisch |
| DST-grenzen (Europa/Amsterdam 2024-03-31, New York 2024-03-10), naïef = UTC | PASS | synthetisch |
| Multi-valuta portefeuille (helper), één onbeschikbaar been -> totaal `None` | PASS | synthetisch |
| Geen 1.0-terugval (FxService, backtest bij ontbrekende/verlopen/ongeldige koers, multimarket-cel) | PASS | synthetisch |
| Chunk-cache: weinig datamanager-aanroepen | PASS | synthetisch |
| DE40-trades met echte EURUSD op handelstijd (ontwikkelperiode) | PASS | **echt** |

## 4. Echt-data-check: DE40, EMA/RSI-pullback (H1), 2014-03-01 tot 2024-01-01 (vóór de verzegelde periode)

Bron: echte Dukascopy DE40 H1 en EURUSD (M1 voor `FxService`, H1 voor de backtestreeks); `evidence/v102/fx/real_de40.json`.

- 744 trades. Voor elke trade is de koers op de exit-tijd onafhankelijk opgevraagd via `FxService` op **M1**-candles en vergeleken met de
  **H1**-reeks die de backtest gebruikt: maximaal verschil **0.0** (de H1-open is dezelfde minuut-open). Instapkoersen zijn eveneens opgehaald.
- EURUSD op exit-tijd liep van 0.9623 tot 1.3922; gemiddelde verhouding USD-winst / EUR-winst per trade 1.139 (de omrekening is dus niet te verwaarlozen per trade).
- Mediane relatieve onzekerheid (spread/2) op exit-tijd: 1.3e-5.
- Resultaat met omrekening (USD-rekening): netto -3916.56, profit factor 0.828, max. drawdown 4343.23 (41.8%), `fx_status=OK`.
- Dezelfde run **zonder** conversiereeks is nu geblokkeerd: `fx_status=FX_RATE_UNAVAILABLE`, `net_profit=None`. De quotevaluta-cijfers (EUR, niet
  rekeningvaluta) staan apart: netto -3919.95, PF 0.828. Het totaalverschil is hier klein omdat de strategie verliesgevend is en de koers rond 1.1-1.3 schommelt; de
  per-trade bedragen en de lotgrootte (risico van 1% in USD) verschillen wel.
- De sealed periode (>= 2024-01-01) is niet gelezen (`assert bid.index.max() < SEALED_START`).
- Dit is een boekhoudcontrole van de omrekening, geen edge-bewijs voor de strategie.

## 5. Open punten en niet-gevonden zaken

- Brokeropslag op valutaconversie en de werkelijke afrekenkoers van de broker zijn onbekend (UNAVAILABLE); `mid` en `conservative` zijn onderzoeksbenaderingen.
- Conversiebars: M1 voor `FxService`, H1/TF-bars voor de backtestreeks. Op het tijdstip van de bar-open is dat gelijk; binnen een H1-bar wisselt de koers (onbekend voor de backtest).
- `contract_for(symbol, dm)` zonder `ts` levert voor EUR/JPY/CAD-quotes nog een 1.0-**placeholder** in het `ContractSpec` (het veld kan niet leeg). Elke backtest zonder `conversion=` markeert dat als FX_RATE_UNAVAILABLE en blokkeert de geldcijfers; met `ts=` komt er een echte koers (of `FxRateUnavailable`).
- 1.0-/vaste-koers-plekken buiten deze bestanden, niet aangepast:
  - `strategy_lab/portfolio.py` (`PLACEHOLDER_RATE`-controle; regel ~397 `member["state"].get("fx_rate") or contract["quote_to_account"]`) en `strategy_lab/forward_engine.py` / `forward_performance.py` (bevroren `quote_to_account`, per-candle koers is daar in aanpassing door de andere agent).
  - `strategy_lab/server.py:199` (`quote_to_account: float` zonder validatie tegen quotevaluta).
  - `strategy_lab/forward_rules.py` `_contract` accepteert elke bevroren `quote_to_account` (ook 1 voor een EUR-quote); de portefeuille/forward-engine moet de echte koers per candle zetten.
  - Evidence-scripts met vaste 1: `evidence/v9/consistency/check3_execution.py`, `check5_jesse_forward.py`, `parity_lib.py`.
  - `strategy_lab/research/adapters.py` / Jesse-runner: geen eigen omrekening; Jesse-resultaten worden achteraf omgerekend via `convert_trades` (exit-tijd).

## V10.2b FX-fallbacks

Remaining fixed-rate / 1.0 paths for non-account quote currencies (EUR/JPY/CAD on a USD account) are closed. Same-currency quotes stay 1.0.

- `instruments.contract_for(symbol, dm)`: a conversion-needing symbol without `ts` now raises `fx.FxRateUnavailable` ("FX_RATE_UNAVAILABLE"). Callers that pass a `conversion=` series to `run_rules_backtest` must say so with `conversion_series=True` (the 1.0 is then an explicit placeholder that the backtester ignores, or flags FX_RATE_UNAVAILABLE with blocked money metrics). Updated: `research/multimarket.py`, `evidence/v10/multimarket/real_run.py`, `evidence/v102/fx/real_de40.py`.
- `portfolio._open_positions`: no `fx_rate` in the member state for a non-account quote gives `status=FX_RATE_UNAVAILABLE` and `multiplier/notional/slippage_cost/spread_cost = None` (the frozen `quote_to_account` is no longer used). `stress_test` marks the FX-dependent scenarios (gap, liquidity, spreads) `FX_RATE_UNAVAILABLE`; exposure lists the blocked registrations. `risk` and `margin` were fixed in account currency at fill time and stay.
- `forward_performance`: MAE/MFE for non-account quotes uses the `fx_rate` carried by the POSITION_CLOSED event (FxService rate of the closing candle, written by the forward engine); without it the excursion is `FX_RATE_UNAVAILABLE`. Open P&L / exposure without a live `fx_rate` are `None` (portfolio totals `None` if any member is unavailable). Remaining approximation: one rate (at close) for the whole excursion, not a per-bar rate.
- `forward_rules._contract(raw, symbol)`: a non-account-quote symbol is accepted only as a DYNAMIC_FX placeholder (the engine converts per candle and ignores the frozen `quote_to_account`; `enroll` blocks without an FX service). Registration now rejects, with FX_RATE_UNAVAILABLE, an instrument outside the registry (quote currency unverifiable) or a quote with no conversion pair. Rejecting every frozen 1.0 for EUR/JPY/CAD was not done: factory_api, research_api and shadow_forward freeze `default_contract` (1.0) for those symbols by design.
- Ledger: `instruments.validate_ledger_contract(symbol, contract)` rejects `quote_to_account == 1.0` for a conversion-needing symbol and a non-USD account; other rates return `FIXED_USER_RATE` (constant user rate, not historical). `server.py` `bidask_ledger` still has to call it (see hand-off).
- `evidence/v9/consistency/parity_lib.py` and `check5_jesse_forward.py` assert the symbol needs no conversion; `check3_execution.py` only labels the 1.0 per quote currency (no money computed), unchanged.

Tests: `strategy_lab/tests/test_v102_fx_fallbacks.py`.
