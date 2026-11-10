# Dagelijks forwardportfolio

1. Open **Forwardportfolio** en maak een portfolio met gedeeld startkapitaal, rekeningvaluta en maximaal open risico.
2. Selecteer in Strategy Lab **LabBreakout** op **M1**. Stel de parameters in. Vul in de Inspector de contractgrootte, tick size, omrekening, lotstap, rekeningvaluta, commissie en slippage in.
3. Open het portfolio opnieuw. Vul onder **Strategie toevoegen** de kapitaaltoewijzing en het risicopercentage per trade in. Bevries de instellingen en voeg de bevroren versie toe.
4. Klik **Verwerk beschikbare dagen**. Het overzicht toont de eerste ontbrekende dag wanneer een benodigd symbool nog niet beschikbaar is.

Voor de dagelijkse worker zet je de portefeuille-ID’s in `forward_portfolio_ids` in het configuratiebestand voor `strategy_lab.daily_sync`. De worker verwerkt maximaal `max_forward_days` dagen per portfolio per uitvoering, standaard drie. De lokale API kan portfolio’s, registraties en dagrapporten ook afzonderlijk lezen of aanmaken onder `/api/forward`.

De eerste adapter gebruikt EMA die vanaf de eerste toegelaten candle wordt opgebouwd. Een strategieversie, het prijsmodel of de uitvoercode wijzigen vereist een nieuwe registratie of modelmigratie. De getoonde cijfers volgen het vertraagde Bid/Ask OHLC-model met de opgegeven contractgegevens. Intrabar tickvolgorde is onbekend.

## Prestaties en risico

Het rapport gebruikt de vastgelegde forwarddagen en gesloten trades. Het portfolio toont equity op UTC-dageinde, inclusief de waardering van open posities, en de drawdown op die meetpunten. Per strategie worden drawdown en rendement berekend op gerealiseerd dagsaldo. De duur onder water loopt van het eerste waargenomen dageinde onder de piek tot herstel of de laatste verwerkte dag.

Sharpe gebruikt het gemiddelde dagelijkse rendement tegenover 0% gedeeld door de steekproefstandaardafwijking. Sortino gebruikt het daggemiddelde tegenover 0% gedeeld door de neerwaartse afwijking over alle waarnemingen. Beide ratio’s blijven leeg bij minder dan twee observaties of een ongeldige noemer en worden zonder annualisering getoond. Profit factor blijft leeg zonder gerealiseerd verlies. Het gemiddelde rendement/risico gebruikt de geplande koersafstanden tussen entry, stop en doel. [Sharpe](https://web.stanford.edu/~wfsharpe/art/sr/sr.htm) · [CFA Institute](https://rpc.cfainstitute.org/-/media/documents/code/gips/the-sortino-ratio.pdf)

Het open stoprisico gebruikt de opgeslagen schatting bij opening. De risicolimiet op portfolioniveau gebruikt het laagste van saldo en equity. De simulator controleert daarnaast de strengere limieten van de betreffende strategie bij elke nieuwe fill.

MAE en MFE zijn de grootste ongunstige en gunstige beweging vanaf de gesimuleerde entryprijs. Het rapport rekent met Bid voor longs en Ask voor shorts en zet de prijsafstand bruto om naar rekeningvaluta met het bevroren contract. De exitcandle en de fillcandle van een stoporder vallen buiten de meting. Bij een marktfill op de open telt die candle mee als de positie die candle overleeft.

De meting gebruikt de originele dagpartities uit het forwardlogboek en controleert de hashes en waarnemingstijden. Een later gepubliceerde revisie verandert de cijfers niet. Ontbrekende waarneembare candles of een niet-verifieerbare partitie geven een lege waarde. De dekking toont het aantal meetbare trades ten opzichte van alle gesloten trades.

## Historische referentie

Open **Forwardportfolio → Historische referentie**, kies een bevroren strategie en selecteer één tot 31 UTC-dagen die vóór de bevriezingsdag eindigen. De app controleert de hashes van de oorspronkelijke Dukascopy Bid/Ask-bestanden en gebruikt dezelfde bevroren parameters, contractinstellingen, strategiecode en uitvoerengine.

De referentiestrategie draait zelfstandig met dezelfde kapitaaltoewijzing en portfoliolimieten. De vergelijking toont gerealiseerde opbrengst als aandeel van de toewijzing, opbrengst per gesloten trade, trades en drawdown. Een positie die op de einddatum nog open is, krijgt een aparte vermelding. Minuten zonder quotes staan als aantal in het rapport. Dit aantal kan zowel marktsluitingen als datagaten omvatten.

De app bewaart de referentie apart als `HISTORICAL_BACKTEST` met periode, snapshot-, engine-, bron- en dagpartitiehashes. Een herhaalde aanvraag voor dezelfde strategie en periode geeft het oorspronkelijke resultaat terug. De historische run start met lege strategiestate op de eerste gekozen dag en heeft geen invloed op het forwardlogboek. OOS-validatie en vergelijking met trainingsresultaten blijven afzonderlijke onderzoeksstappen.

## Regelstrategieën uit de Strategy Builder

Sla de strategie op in de **Strategy Builder**. Bevries hem daarna met `POST /api/forward/rule-registrations` (body: `spec_name` of `spec`, optioneel `parameters`, `contract` met alle velden, `starting_capital`, `trade_risk`, `portfolio_risk_limit`, optioneel `leverage`). Het antwoord bevat het `registration_id`. `GET /api/forward/rule-registrations/preview?spec_name=...` toont de configuratie die zou worden bevroren, zonder te bevriezen. Voeg de registratie toe via het bestaande ledenpunt van het portfolio (`/api/forward/portfolios/{id}/members`) en verwerk de dagen zoals bij LabBreakout.

De registratie legt de spec, de parameters, de codeversies en een warm-up van gesloten Bid-bars vast die vóór het bevriezen zijn waargenomen. Elke wijziging van spec of parameters vraagt een nieuwe registratie. Het nieuwsfilter moet uit staan. De positiegrootte gebruikt het laagste van `risk.percent` in de spec en `trade_risk`. De portfoliolimiet blokkeert een nieuwe entry als het gedeelde open risico die zou overschrijden; dat staat als `ORDER_REJECTED` in het logboek. Dit blijft `DAILY_DELAYED_FORWARD`: data wordt pas verwerkt nadat de dagpartitie is gepubliceerd, dus dit is geen realtime papertrading. Een bar telt pas mee wanneer de volgende bar begint, en orders worden per minuut gevuld met Ask voor longs en Bid voor shorts. De volledige semantiek en de verschillen met de backtest staan in `FORWARD_ENGINE_VALIDATION.md`.
