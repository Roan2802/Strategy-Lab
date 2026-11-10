# Implementatieplan P0

## P0 — afronden valide financiële vergelijking

- Voeg de al gecompileerde `P0DukaCustomSymbolImporter` alleen op een afgescheiden custom symbol toe, voer hem uit op de Duka-afgeleide CSV en controleer logregel, barcount en eerste/laatste UTC.
- Bouw een MT5 test-only Expert Advisor met harde `MQL_TESTER` guard; run korte Duka-periode op custom symbol. Geen bestaande instrumenten of MT5-history gebruiken.
- Houd Jesse en MT5-signalen, orders en fills apart. Vergelijk timestamps en prijzen op dezelfde Duka bars. Controleer contract size, tick size/value, volume min/step, spread, fee/commission en P&L.
- Test vooraf gedefinieerde synthetische SL-only, TP-only, gap en beide-in-één-candle scenario's. Markeer intrabarvolgorde onbepaald op M1 OHLC.
- Acceptatie: alle invoerbestanden aantoonbaar afgeleid uit `C:\AI\Dukascopy_data`; identieke bar timestamps/OHLC; rapport toont verschillen per lifecycle-stap, zonder live orders.

## P1 — betrouwbare Inspector data

- Versiebeheer event schema en schrijf aparte events voor candle decision, submit, cancel request/complete, fill/open en close.
- Voeg strategy hash, dataset hash, timeframe en stabiele event IDs toe; valideer order/trade joins automatisch.
- Bewaar ook FAIL en SKIP redenen plus precieze indicatorwaardes en state snapshots.
- Acceptatie: fixture met bekende beslissingen produceert reproduceerbare JSONL; alle trade records linken naar beslissings-, submit-, fill- en close-events.

## P1 — Inspector interface

- Maak een lokale Python chart/paneel dat Duka-candles, Jesse trade-CSV en JSONL combineert.
- Selectie van candle toont PASS/FAIL/SKIP checks; trade jump heeft aparte signal/fill/exit sprongen.
- Voeg zoom, scroll, filters op reden, ordermarkers, SL/TP en indicator overlays toe. Replay verbergt toekomstige candles.
- Acceptatie: gebruiker kan met één klik een entry of afgewezen setup reproduceren en candledata/indicatorwaarden inspecteren.

## P2 — chart uitbreiding

- Bevestig gekozen chart renderer en een stabiele marker/shape API; voeg zone/range shapes en export pas daarna toe.
- Acceptatie: zones volgen UTC tijd/prijs, blijven correct bij zoom/scroll, en exporteren zonder verborgen toekomstige bars in replay.

## Technische grens

Dukascopy bevat M1-OHLC, geen volledige intrabar tickvolgorde voor deze validatie. Spread kan uit Bid/Ask worden berekend, maar contract/commissie/tickwaarden zijn geen prijsdata en moeten als aparte expliciete testconfiguratie uit een door gebruiker gekozen contractmodel komen. Niet afleiden uit account- of MT5-historie.
