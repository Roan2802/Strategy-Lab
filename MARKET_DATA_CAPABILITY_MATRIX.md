# Marktdata — capability matrix

Dukascopy M1 Bid/Ask is de primaire, zelfstandig werkende basis. Alles daarbuiten is optioneel en wordt pas ingeschakeld met echte data. Er wordt **geen** fictieve orderflow-, volume-at-price- of orderboekdata uit candles afgeleid.

| # | Datatype | Status | Bron / toelichting |
| :--- | :--- | :--- | :--- |
| 1 | Dukascopy M1 Bid/Ask OHLC (historisch) | PASS | `C:\AI\Dukascopy_data`, read-only; cache in `strategy_lab/data_cache` |
| 2 | Dagelijkse nieuwe Dukascopy-dagen | PARTIAL | Automatisch zonder account: geordende terugvalketen `datafeed` (publieke BID/ASK_candles_min_1.bi5) -> `datafeed_ticks` (M1 uit uurlijkse ticks) -> `jforex` (alleen met credentialsbestand) -> `inbox`. Max. 1 verzoek/s, eerlijke User-Agent, 429 -> DELAYED met backoff (30 min tot 6 u), 202/403/WAF-header -> BLOCKED zonder omzeiling. Code en tests (nagebootste HTTP) PASS; livebewijs: XAUUSD-probe 2026-11-06 HTTP 200, daarna op 2026-11-09 host onbereikbaar/503, dus puntfactoren van 8 symbolen nog niet live bevestigd |
| 3 | Dukascopy quote-/tickvolume | PARTIAL | Alleen als expliciet benoemde proxy; Dukascopy-volume is geen beursvolume |
| 4 | Werkelijk verhandeld futuresvolume | DATA_REQUIRED | CME DataMine, Databento e.d. (betaald) |
| 5 | Aggressor buy/sell-volume | DATA_REQUIRED | Tick-by-tick trades met aggressorvlag |
| 6 | Delta / cumulatieve delta | DATA_REQUIRED | Afgeleid van (5) |
| 7 | Volume-at-price, VPOC, VAH/VAL, single prints | DATA_REQUIRED | Trade prints nodig; OHLC-benadering zou misleidend zijn |
| 8 | L2-orderboek (MBP) / Bookmap-heatmap, resting liquidity, absorption | DATA_REQUIRED | Historische MBP-10/MBO-feeds (bijv. Databento, Rithmic-exports) |
| 9 | L3-orderboek (MBO) | DATA_REQUIRED | Zie (8) |
| 10 | Economische kalender | DATA_UNAVAILABLE | Zie `NEWS_DATA_REQUIREMENTS.md` |
| 11 | Realtime quotefeed | DATA_REQUIRED | Nodig voor Gate D (paper); dagelijkse download is geen realtime data |
| 12 | Brokerverbinding / orderrouting | DISABLED | Bewust uit; aparte opdracht + expliciete toestemming vereist |

## Adapterarchitectuur voor toekomstige orderflow/L2-data

Een toekomstige adapter levert per instrument een tijdgeordende stroom met expliciete `available_at`-tijdstempels:

```text
OrderflowAdapter
  ├── trades(symbol, start, end) -> [ts_exchange, ts_available, price, size, aggressor_side]
  ├── book_updates(symbol, start, end, depth) -> [ts_available, side, level, price, size, action]
  ├── capabilities() -> {"trades": bool, "aggressor": bool, "mbp_depth": int, "mbo": bool}
  └── provenance() -> {vendor, licence, instrument_mapping, timezone, gaps}
```

Afgeleide features (delta, volume profile, VPOC, absorption) worden alleen berekend wanneer `capabilities()` de benodigde velden bevestigt; anders retourneert de feature `DATA_REQUIRED`. Futures (bijv. NQ) en Dukascopy-CFD's (USATECHIDXUSD) zijn verschillende instrumenten; een mapping tussen beide is een nieuwe onderzoekshypothese, geen gelijkstelling.

## In de app

Data Manager → "Beschikbare databronnen" toont deze matrix live via `GET /api/capabilities`.

## Dagelijkse Dukascopy-download (toelichting op rij 2)

De worker haalt per symbool 1 UTC-dag op uit `datafeed.dukascopy.com` en voegt die als gevalideerde dagpartitie toe aan de bestaande historie. Een tijdelijke fout laat de dag openstaan; de geplande taak probeert opnieuw met backoff. Details, toegangsregels, het besluit over vlakke nulvolume-minuten en de puntfactoren staan in `DAILY_DUKASCOPY_PIPELINE.md`.
