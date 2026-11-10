# Nieuwsfilters — datavereisten

**Status: DATA_UNAVAILABLE.** Strategy Lab past op dit moment géén historische nieuwsfilter toe. Er is lokaal geen point-in-time economische kalender aanwezig, en een kalender zonder publicatietijdstempels zou toekomstkennis in backtests lekken. De filter blijft uit totdat een betrouwbare bron is gekoppeld; de Dukascopy-prijsbron blijft daarbij altijd de primaire bron.

## Wat een bruikbare kalender minimaal moet bevatten

| Veld | Waarom |
| :--- | :--- |
| Geplande releasetijd (UTC) | Venster "geen entry X minuten vóór/na" bepalen |
| Werkelijke publicatietijd (UTC) | Vertraagde of vervroegde publicaties correct behandelen |
| Land / valuta | Koppelen aan instrumenten (USD → XAUUSD, US30, EURUSD …) |
| Impactniveau | Filter op hoog/middel/laag |
| Oorspronkelijk gepubliceerde waarde | Geen herziene cijfers als historische kennis gebruiken |
| Vorige waarde en consensus, met tijdstempel van bekendwording | Forecast alleen gebruiken als die vóór de release bekend was |
| Revisiehistorie | Herzieningen apart bewaren |

## Causaliteitsregels voor de toekomstige module

1. Een eventwaarde mag pas worden gebruikt vanaf de tijdstempel waarop die bekend werd.
2. Herziene cijfers vervangen nooit de oorspronkelijke publicatie in een backtest.
3. Het filter blokkeert alleen nieuwe entries in het ingestelde venster; wat er met open posities gebeurt (behouden/sluiten) is een expliciete regel per strategie.
4. Instelbaar: valuta's, impactniveau, minuten vóór/na, uitzonderingen per strategie, tijdzone, eventcategorie (CPI, NFP, FOMC, ECB …).

## Mogelijke bronnen (nog niet gekoppeld)

| Bron | Point-in-time? | Kosten / account | Opmerking |
| :--- | :--- | :--- | :--- |
| ALFRED (St. Louis Fed, vintages) | Ja, voor macroreeksen | Gratis API-sleutel | Uitstekend voor oorspronkelijke waarden; geen intraday releasetijden voor alle events |
| Officiële agentschappen (BLS, BEA, Fed, ECB) | Releaseschema's publiek | Gratis | Schema's moeten per agentschap worden verzameld |
| Commerciële kalenders (bijv. Trading Economics, Refinitiv, Bloomberg) | Afhankelijk van licentie | Betaald | Vereist toestemming van de gebruiker vóór aanschaf |
| Gescrapete websitekalenders | Meestal niet point-in-time | — | Niet gebruiken: geen publicatietijdstempels, voorwaarden onduidelijk |

Voor iedere nieuwe externe, betaalde bron of account is eerst toestemming van de gebruiker nodig.

## Huidige gedrag in de app

- Data Manager → "Beschikbare databronnen" toont de nieuwskalender als `DATA_UNAVAILABLE`.
- Bevroren forwardregistraties leggen `news_filter: {"enabled": false}` vast; de LabBreakout-forwardadapter weigert een ingeschakelde nieuwsfilter.
- Regimeclassificatie "pre-news/post-news" is om dezelfde reden niet beschikbaar.
