# Roadmap: Jesse met lokale Dukascopy Strategy Lab

De bestaande prototypefunctionaliteit staat in `strategy_lab/` en `runtime/strategy_lab/`. Dit document markeert wat bewezen is en wat nog openstaat; het vraagt geen toegang tot andere prijsdata. Alleen prijsbron is `C:\AI\Dukascopy_data`.

## Validatiestatus — 2026-11-06

De lokale 8-symbol × 7-timeframe route is voor één UTC-dag uitgevoerd en alle 56 gevallen zijn geslaagd na correctie van Jesse's dashed-pair routes. `ExampleStrategy` is in de Inspector getest. Een beperkte `LabBreakout` quote-side adapter is nu op Duka XAUUSD M1 uitgevoerd: 1.379/1.380 beslissingen kwamen overeen met de opgeslagen Jesse-events; één Ask-EMA longsignaal week af. Complete date-range/resamplingvalidatie en generieke arbitrary-strategy state/signal replay blijven werk voor de roadmap. Zie `evidence/test_outputs/bidask_signal_reexecution_dukascopy_20261106.json`.

## P0 — betrouwbaarheid en accounting

1. **Datasetvalidatie uitbreiden** voor alle ontdekte Duka-symbolen en M1 tijdsperiodes. Acceptatie: hashes, paarintegriteit, timestamps/gaten/duplicates/volume, cache hergebruik, timeframe-aantallen en onveranderde originele bronbestanden in manifesten.
2. **Instrumentaccounting specificeren** zonder externe prijsbronnen: per instrument contract size, tick size, lotstep, valuta, kosten en conversieregels via expliciete testinstellingen. Acceptatie: handmatig narekenbare Bid/Ask voorbeelden en verklaring van fee/slippage/currency semantics. Nog niet door Jesse-equivalentie bewezen.
3. **OHLC ambiguity documenteren**: gelijktijdig geraakt SL/TP, openingsgaten en intrabar-volgorde. Acceptatie: uitvoeringspolicy expliciet en conservatief, met waarschuwing dat M1 OHLC de echte tickvolgorde niet kent.
4. **MCP-beperking registreren**: Jesse MCP ontbreekt in deze sessie; local research API werkt. Geen MCP-resultaten simuleren.

## P1 — runs en strategiebeheer

5. Verifieer preset save/load en herlaad oude run, nieuwe run, vergelijking en herhaalbaarheid via echte Duka runvensters. Acceptatie: code-, data- en parameterhash terug te herleiden.
6. Breid de geslaagde one-day browser-/API-matrix uit naar representatieve Duka-datums en vergelijk resampled OHLC met onafhankelijke bronaggregaten; test ook foutmeldingen en incomplete quotes. Alleen Duka-mapgegevens.

## P2 — Inspector en replay

7. Standaardiseer logs voor elke setup, ook wanneer geen order ontstaat: candle, exact check/waarde, state, reden, signaal, order, fill, SL/TP. Acceptatie: aantoonbaar onderscheid tussen afgewezen signaal, ingediende order en fill.
8. Replay test nu future cutoff en UI-step heen/terug. Volgende acceptatie: indicatorwaarden en strategie-state per cursor opnieuw causaal afgeleid; voeg look-ahead test toe met echte Duka sample windows.
9. Onderzoek chart zones/labels via huidige Plotly/strategy hooks. Een aparte chart-engine pas overwegen als selecteerbare prijsserie, trade markers en replay hiermee niet betrouwbaar werken.

## P3 — analyse en optimalisatie

10. Herhaal kleine optimalisatie met alleen Duka-trainingwindows en een onaangeroerd latere holdoutwindow uit dezelfde Duka bron. Meet tijd/geheugen en sla parametercombinaties op. Niet optimaliseren op volledige data zonder aparte holdout.
11. Vergelijk Jesse's random sampling met mogelijke grid/TPE externe aanpak; test hervatten, stabiliteit en walk-forward alleen met begrensde workloads.
12. Bereken ontbrekende metrics extern uit bewaarde trades: profit factor, recovery, drawdown, MAE/MFE, maand/sessie/weekday, trade-overlap en cross-symbol-correlation. Blijf voor alle historische quotes binnen Duka-map.

## Architectuuradvies

Behoud Jesse als lokale Python signaal- en backtestengine en houd deze kleine Dukascopy-only wrapper ernaast. Een volledige Jesse fork is nog niet gerechtvaardigd. Voor MT5 financieel equivalente CFD-resultaten is er nog geen GO: bewijs daarvoor moet later met dezelfde Duka-prijzen plus volledig gespecificeerd instrumentmodel en expliciet toegestane onafhankelijke execution vergelijking worden geleverd. Jesse MCP is niet nodig voor lokale browser/backtestpaden die hier al werken.

## Afhankelijkheden en blockers

- Instrument-/accountparameters ontbreken: Bid/Ask P&L-ledger is daarom niet geverifieerd.
- Jesse MCP tool ontbreekt: alleen MCP-functionaliteit is geblokkeerd, lokale API niet.
- Geen gecontroleerde netwerk-offline test; geen app screenshotbestand opgeslagen.
- Full Windows upstream pytest collection mist `arrow`; relevante kernsubset is in de Jesse-container geslaagd.

## Continuation delta — 2026-11-06

Fixed saved-run selection so chart/replay context follows the selected run; the ChatGPT IAB verified Spring at a full-month run's first signal and the next-candle fill cutoff. The September research result was fully negative and remains provisional. Remaining work: arbitrary strategy-state/signal replay, per-strategy quote-side adapters beyond LabBreakout, broker-verified contract specifications, custom optimizer feasibility/TPE checkpoints, performance benchmarking and controlled Windows offline/reboot tests.


## Update — 2026-11-06

Optimizer UI follow-up is implemented and verified: sortable saved trials, actual-evaluated-parameter heatmap, sensitivity scatter plot, and JSON run export. Remaining optimizer extension work is limited to generic margin-aware candidate feasibility and resumable TPE sampler checkpoints. The Dukascopy import performance work is also complete for XAUUSD and benchmarked at 43.6% faster / 47.9% lower peak RSS with exact output equality; benchmark other symbols only if their first-import footprint becomes a real bottleneck.


## Offline-stack test update — 2026-11-06

Core Jesse + temporary PostgreSQL startup, the Strategy Lab runner, one Duka backtest and one Duka optimization now pass inside a disposable Docker namespace without a default route, with exact result reproduction. The local UI also works when browser requests outside loopback are blocked. Do not remove the remaining operational acceptance item: the real Windows launcher and production Compose cold-start/reboot under offline conditions remain unverified.
