/* Nederlandse labels voor strategieparameters en testerinstellingen.
   Alle betekenissen zijn gecontroleerd tegen de code (runtime/strategies/*, runtime/strategy_lab/*,
   strategy_lab/research/*, strategy_lab/plugins/*). Waar de code geen betekenis bewijst staat `unverified: true`. */
(function () {
  'use strict';

  // ---------------------------------------------------------------- strategieparameters
  // Sleutel `<Strategie>.<param>` wint van `<param>` wanneer de betekenis per strategie verschilt.
  var PARAM_LABELS = {
    // --- Gedeeld door de Python-strategieen (LabBreakout, P0AuditProbe, StatefulRsiPending, ExampleStrategy, AuditProbe)
    order_mode: {
      label: 'Ordertype', unit: '', group: 'Instap', simple: true,
      help: 'Bepaalt hoe er wordt ingestapt: direct met een marktorder of met een stoporder boven/onder de signaalcandle. Modus 2 is short; spot ondersteunt geen short.',
      suggest: '0 (long marktorder) is het eenvoudigst',
      example: '1 wacht tot de koers boven de candle-high + offset uitbreekt voordat de trade opent'
    },
    entry_interval: {
      label: 'Entry-interval', unit: 'candles', group: 'Instap', simple: true,
      help: 'Er wordt alleen gezocht naar een instap op elke N-de candle van de test (candle-nummer deelbaar door N). Het is geen tijd in minuten.',
      suggest: '60 (op M15 is dat elk uur)',
      example: 'Hoger = minder instapmomenten; 60 op M15 = elk uur, op H1 = elke 60 uur'
    },
    position_size: {
      label: 'Ordergrootte', unit: 'Jesse-quantity', group: 'Risico', simple: true,
      help: 'Hoeveelheid die rechtstreeks naar Jesse wordt gestuurd. Dit is geen gegarandeerd broker-lot; de werkelijke waarde hangt af van symbool en rekenmodel.',
      suggest: '1 (LabBreakout) of 0,01 (kleine testtrades)',
      example: 'Dubbele grootte = dubbele winst en dubbel verlies per trade'
    },
    ema_period: {
      label: 'EMA-periode (trendfilter)', unit: 'candles', group: 'Filter', simple: true,
      help: 'Aantal candles voor het exponentieel voortschrijdend gemiddelde. Long mag alleen als de slotkoers boven de EMA staat (short: eronder).',
      suggest: '20 (gangbaar voor M15)',
      example: 'Hoger = tragere, rustigere trendfilter met minder signalen'
    },
    max_bar_range: {
      label: 'Maximale candle-range', unit: 'prijspunten', group: 'Filter', simple: false,
      help: 'Een instap wordt geweigerd als de signaalcandle (high min low) groter is dan deze waarde. Uitgedrukt in ruwe prijseenheden van het symbool, niet in pips.',
      suggest: '25 (voor goud; pas aan op het symbool)',
      example: 'Lager = alleen rustige candles, minder instappen na grote uitschieters'
    },
    stop_distance: {
      label: 'Stop-lossafstand', unit: 'prijspunten', group: 'Uitstap', simple: false,
      help: 'Afstand tussen de gevulde instapprijs en de stop-loss, in ruwe prijseenheden van het symbool (geen pips, geen procent).',
      suggest: '1 tot 3 voor goud op M15',
      example: 'Kleinere afstand = kleiner verlies per trade maar vaker uitgestopt'
    },
    target_distance: {
      label: 'Take-profitafstand', unit: 'prijspunten', group: 'Uitstap', simple: false,
      help: 'Afstand tussen de gevulde instapprijs en het winstdoel, in ruwe prijseenheden van het symbool.',
      suggest: 'Minstens gelijk aan de stop-afstand (verhouding 1:1 of beter)',
      example: 'Verder doel = grotere winst per gewonnen trade maar minder vaak bereikt'
    },
    stop_offset: {
      label: 'Stop-entry-offset', unit: 'prijspunten', group: 'Instap', simple: false,
      help: 'Marge boven de candle-high (long) of onder de candle-low (short) waar de stoporder wordt geplaatst. Alleen relevant bij stoporders.',
      suggest: '0,1 (klein ten opzichte van de candle)',
      example: 'Groter = pas instappen na een sterkere doorbraak'
    },
    pending_expiry_bars: {
      label: 'Pending order vervalt na', unit: 'candles', group: 'Instap', simple: false,
      help: 'Een niet-gevulde stoporder wordt geannuleerd zodra er zoveel candles zijn verstreken sinds het plaatsen.',
      suggest: '5',
      example: 'Lager = orders verlopen snel; hoger = ze blijven langer wachten op een doorbraak'
    },
    expiry_bars: {
      label: 'Pending order vervalt na', unit: 'candles', group: 'Instap', simple: false,
      help: 'Een niet-gevulde stoporder wordt geannuleerd zodra er zoveel candles zijn verstreken sinds het plaatsen.',
      suggest: '5',
      example: 'Lager = orders verlopen snel; hoger = ze blijven langer wachten op een doorbraak'
    },
    cooldown_bars: {
      label: 'Wachttijd na sluiten trade', unit: 'candles', group: 'Instap', simple: false,
      help: 'Na het sluiten van een positie mag er pas een nieuwe setup starten als meer dan dit aantal candles is verstreken.',
      suggest: '3',
      example: '0 = direct opnieuw instappen mogelijk; hoger = meer rust tussen trades'
    },
    rsi_period: {
      label: 'RSI-periode', unit: 'candles', group: 'Filter', simple: true,
      help: 'Aantal candles voor de RSI (relative strength index, een momentumindicator van 0 tot 100).',
      suggest: '14 (standaardwaarde van de RSI)',
      example: 'Lager = nerveuzere RSI met meer uitschieters'
    },
    rsi_entry_max: {
      label: 'Maximale RSI voor entry', unit: 'RSI-punten (0-100)', group: 'Filter', simple: true,
      help: 'Een nieuwe long-setup mag alleen doorgaan als de RSI op of onder deze waarde staat (dus als de koers eerder zwak was).',
      suggest: '45',
      example: 'Lager = alleen instappen na diepe zwakte, minder signalen'
    },

    // --- Strategie-specifiek (betekenis verschilt per strategie)
    'AuditProbe.entry_interval': {
      label: 'Entry-interval', unit: 'candles', group: 'Instap', simple: true,
      help: 'Er wordt een long marktorder geplaatst op elke N-de candle (candle-nummer deelbaar door N), zolang er geen positie open is.',
      suggest: '4', example: 'Hoger = minder trades'
    },
    'AuditProbe.target_distance': {
      label: 'Stop- en doelafstand (symmetrisch)', unit: 'prijspunten', group: 'Uitstap', simple: true,
      help: 'Dezelfde afstand wordt gebruikt voor zowel de take-profit (boven) als de stop-loss (onder) vanaf de instapprijs, in ruwe prijseenheden.',
      suggest: '1', example: 'Groter = ruimere stop en ruimer doel tegelijk'
    },
    'ExampleStrategy.entry_interval': {
      label: 'Entry-interval', unit: 'candles', group: 'Instap', simple: true,
      help: 'Elke N-de candle wordt gecontroleerd; er wordt long ingestapt als dan geen positie open is en de candle bullish sluit (slot gelijk of boven open).',
      suggest: '60', example: 'Hoger = minder instapmomenten'
    },
    'ExampleStrategy.position_size': {
      label: 'Ordergrootte', unit: 'Jesse-quantity', group: 'Risico', simple: true,
      help: 'Hoeveelheid van de marktorder. Stop-loss en take-profit staan vast op 3,0 prijspunten van de instap en zijn niet instelbaar.',
      suggest: '0,01', example: 'Dubbele grootte = dubbele winst en verlies'
    },
    'StatefulRsiPending.stop_offset': {
      label: 'Stop-entry-offset', unit: 'prijspunten', group: 'Instap', simple: false,
      help: 'Afstand boven de candle-high van de signaalcandle waarop de long stoporder wordt geplaatst.',
      suggest: '0,2', example: 'Groter = pas instappen na een sterkere opwaartse doorbraak'
    },
    'StatefulRsiPending.stop_distance': {
      label: 'Stop-lossafstand (long)', unit: 'prijspunten', group: 'Uitstap', simple: false,
      help: 'De stop-loss ligt zoveel ruwe prijspunten onder de gevulde instapprijs.',
      suggest: '2', example: 'Kleiner = kleiner verlies maar vaker uitgestopt'
    },
    'StatefulRsiPending.target_distance': {
      label: 'Take-profitafstand (long)', unit: 'prijspunten', group: 'Uitstap', simple: false,
      help: 'Het winstdoel ligt zoveel ruwe prijspunten boven de gevulde instapprijs.',
      suggest: '3', example: 'Verder = grotere winst per gewonnen trade, minder vaak bereikt'
    },
    'MultiTimeframeReplayProbe.data_timeframe': {
      label: 'Hogere timeframe voor de EMA', unit: '', group: 'Filter', simple: true,
      help: 'Kies H1, H4 of D1. Alleen afgesloten candles van die timeframe worden gebruikt. Let op: deze diagnosestrategie plaatst zelf geen orders.',
      suggest: 'H1', example: 'D1 = trage dagtrend, H1 = snellere uurtrend'
    },
    'MultiTimeframeReplayProbe.h1_ema_period': {
      label: 'EMA-periode op gekozen timeframe', unit: 'candles', group: 'Filter', simple: true,
      help: 'Periode van de EMA op de gekozen hogere timeframe (ondanks de naam "h1" volgt hij de keuze H1/H4/D1). Alleen diagnose; er worden geen trades geplaatst.',
      suggest: '5', example: 'Hoger = tragere EMA'
    },

    // --- Strategy Builder / regelspecs (strategy_lab/research/codegen.py, discovery.py, runtime/strategy_lab/rule_engine.py)
    ema_fast: {
      label: 'Snelle EMA-periode', unit: 'candles', group: 'Instap', simple: true,
      help: 'Periode van de snelle exponentieel voortschrijdende gemiddelde. Als deze boven de trage EMA ligt, is er een opwaartse trend (long); eronder een neerwaartse (short). In sommige strategieen telt een kruising.',
      suggest: '20 (gangbaar voor M15)', example: 'Lager = sneller reagerende trendsignalen'
    },
    ema_slow: {
      label: 'Trage EMA-periode', unit: 'candles', group: 'Instap', simple: true,
      help: 'Periode van de trage EMA, de referentie voor de trendrichting. Moet groter zijn dan de snelle periode.',
      suggest: '50', example: 'Hoger = rustigere trend met minder wisselingen'
    },
    rsi_low: {
      label: 'RSI-niveau voor long', unit: 'RSI-punten (0-100)', group: 'Instap', simple: true,
      help: 'Long-instap zodra de RSI van onderen door dit niveau omhoog kruist (herstel na een pullback).',
      suggest: '40', example: 'Hoger = eerder instappen op een herstel'
    },
    rsi_high: {
      label: 'RSI-niveau voor short', unit: 'RSI-punten (0-100)', group: 'Instap', simple: true,
      help: 'Short-instap zodra de RSI van boven door dit niveau omlaag kruist.',
      suggest: '60', example: 'Lager = eerder short instappen'
    },
    stop_mult: {
      label: 'Stop-lossafstand (x ATR)', unit: 'x ATR(14)', group: 'Uitstap', simple: true,
      help: 'De stop-loss ligt dit aantal keer de ATR(14) (gemiddelde candle-beweging) van de instapprijs. Het winstdoel volgt uit de R-multiple van de strategie.',
      suggest: '1,5', example: 'Hoger = ruimere stop, kleinere positie bij dezelfde risicopercentage'
    },
    or_minutes: {
      label: 'Opening range (minuten)', unit: 'minuten', group: 'Instap', simple: true,
      help: 'Lengte van de opening range: de high en low van de eerste N minuten vanaf de sessiestart (09:30 New York). Een slotkoers erbuiten geeft een signaal.',
      suggest: '30', example: 'Langer = bredere range, minder maar groter doorbraken'
    },
    channel: {
      label: 'Kanaalperiode (Donchian)', unit: 'candles', group: 'Instap', simple: true,
      help: 'Hoogste high en laagste low van de voorgaande N candles (zonder de huidige). Een slotkoers daarbuiten is een doorbraak.',
      suggest: '20', example: 'Hoger = alleen grote doorbraken, minder signalen'
    },
    bb_period: {
      label: 'Bollinger-periode', unit: 'candles', group: 'Instap', simple: true,
      help: 'Aantal candles voor het gemiddelde (SMA) en de standaarddeviatie waaruit de Bollinger-banden zijn opgebouwd.',
      suggest: '20', example: 'Hoger = vloeiendere banden'
    },
    bb_mult: {
      label: 'Bandbreedte (x standaarddeviatie)', unit: 'x standaarddeviatie', group: 'Instap', simple: true,
      help: 'Afstand van de Bollinger-banden tot het gemiddelde, in aantal standaarddeviaties.',
      suggest: '2', example: 'Hoger = bredere banden, alleen extremere uitschieters tellen'
    },
    adx_max: {
      label: 'Maximale ADX (rustige markt)', unit: 'ADX-punten', group: 'Filter', simple: true,
      help: 'Instappen mag alleen als de ADX(14) (trendsterkte) onder deze waarde ligt, dus als de markt weinig trend heeft.',
      suggest: '25', example: 'Lager = alleen in zeer rustige markten'
    },
    adx_min: {
      label: 'Minimale ADX (trendsterkte)', unit: 'ADX-punten', group: 'Filter', simple: true,
      help: 'Instappen mag alleen als de ADX(14) boven deze waarde ligt, dus als er een duidelijke trend is.',
      suggest: '20', example: 'Hoger = alleen bij sterke trends'
    },
    trend_ema: {
      label: 'Trend-EMA-periode', unit: 'candles', group: 'Filter', simple: true,
      help: 'Periode van de EMA die de trendrichting bepaalt: long alleen boven, short alleen onder deze EMA.',
      suggest: '50', example: 'Hoger = langere trend als filter'
    },
    expansion_shift: {
      label: 'Volatiliteitsvergelijking', unit: 'candles terug', group: 'Filter', simple: false,
      help: 'Instap alleen als de huidige ATR(14) groter is dan de ATR(14) zoveel candles eerder (volatiliteit neemt toe).',
      suggest: '5', example: 'Hoger = vergelijking met verder verleden'
    },
    lookback: {
      label: 'Candles terugkijken', unit: 'candles', group: 'Instap', simple: true,
      help: 'Aantal voorgaande candles waarvan de hoogste high of laagste low als referentie dient (liquidity sweep: koers prikt erdoorheen en sluit terug erbinnen).',
      suggest: '20', example: 'Hoger = alleen sweeps van grotere extremen'
    },
    ema_n: {
      label: 'EMA-periode', unit: 'candles', group: 'Instap', simple: true,
      help: 'Periode van de EMA die de slotkoers moet kruisen (bevestigd met RSI(14) boven of onder 50).',
      suggest: '30', example: 'Hoger = trager signaal'
    },
    roc_n: {
      label: 'Momentum-periode', unit: 'candles', group: 'Instap', simple: true,
      help: 'Aantal candles voor de rate of change (procentuele koersverandering over N candles). Een kruising door 0 geeft het signaal.',
      suggest: '20', example: 'Lager = nerveuzer momentum'
    },
    min_hold: {
      label: 'Minimale houdduur', unit: 'candles', group: 'Overig', simple: false, unverified: true,
      help: 'Parameter is gedefinieerd in de reversal-strategie maar wordt in de regels niet gebruikt; de werking is niet te verifieren in code.'
    },

    // --- Plugins (strategy_lab/plugins/*)
    fast: {
      label: 'Snelle SMA-periode', unit: 'candles', group: 'Instap', simple: true,
      help: 'Periode van de snelle eenvoudige voortschrijdende gemiddelde (SMA) op de sluitkoersen. Koop als deze boven de trage SMA kruist.',
      suggest: '20', example: 'Lager = meer, snellere signalen'
    },
    slow: {
      label: 'Trage SMA-periode', unit: 'candles', group: 'Instap', simple: true,
      help: 'Periode van de trage SMA, de referentie waar de snelle SMA doorheen kruist.',
      suggest: '50', example: 'Hoger = minder, trager signalen'
    },
    lots: {
      label: 'Lotgrootte', unit: 'lots', group: 'Risico', simple: true,
      help: 'Positiegrootte in lots. Winst en verlies = prijsverschil x lots x contractgrootte.',
      suggest: '0,1', example: 'Dubbel aantal lots = dubbele winst en verlies'
    },
    contract_size: {
      label: 'Contractgrootte (plugin)', unit: 'eenheden per lot', group: 'Risico', simple: false,
      help: 'Aantal eenheden van het onderliggende product in 1 lot; vermenigvuldigt de winst per prijspunt.',
      suggest: '100 (goud)', example: '100 betekent 1 prijspunt = 100 x lots in rekeningvaluta'
    },
    max_dd: {
      label: 'Maximaal drawdown-profiel', unit: '% van saldo', group: 'Risico', simple: true,
      help: 'Doel voor de maximale drawdown van GoldReaper: stuurt de positiegrootte (lots) en de drawdown-bewaker. Waarden worden tussen 1 en 50 begrensd.',
      suggest: '10', example: 'Hoger = grotere posities en grotere mogelijke drawdown'
    },
    entry_offset_factor: {
      label: 'Instap-offsetfactor', unit: 'vermenigvuldiger', group: 'Instap', simple: false,
      help: 'Vermenigvuldigt de instapafstand (offset van pending orders) van alle deelstrategieen. 1 = originele instelling.',
      suggest: '0,7', example: 'Lager = orders dichter bij de koers'
    },
    stop_loss_factor: {
      label: 'Stop-lossfactor', unit: 'vermenigvuldiger', group: 'Uitstap', simple: false,
      help: 'Vermenigvuldigt de stop-loss van alle deelstrategieen. 1 = originele instelling.',
      suggest: '0,75', example: 'Lager = strakkere stops'
    },
    take_profit_factor: {
      label: 'Take-profitfactor', unit: 'vermenigvuldiger', group: 'Uitstap', simple: false,
      help: 'Vermenigvuldigt het winstdoel van alle deelstrategieen. 1 = originele instelling.',
      suggest: '1,5', example: 'Hoger = verdere winstdoelen'
    },
    trail_distance_factor: {
      label: 'Trailing-afstandfactor', unit: 'vermenigvuldiger', group: 'Uitstap', simple: false,
      help: 'Vermenigvuldigt de trailing-stopafstanden van alle deelstrategieen. 1 = originele instelling.',
      suggest: '1,5', example: 'Hoger = ruimere trailing stop'
    },
    be_trigger_factor: {
      label: 'Break-even-triggerfactor', unit: 'vermenigvuldiger', group: 'Uitstap', simple: false,
      help: 'Vermenigvuldigt de winstafstand waarbij de stop naar break-even gaat. 1 = originele instelling.',
      suggest: '0,75', example: 'Lager = eerder break-even'
    },
    commission_per_lot_side: {
      label: 'Commissie per lot per zijde', unit: 'rekeningvaluta per lot', group: 'Risico', simple: false,
      help: 'Commissie die bij openen en bij sluiten per lot wordt afgetrokken (dus twee keer per trade).',
      suggest: '0 of de waarde van je broker', example: '3,5 = 7 per lot per volledige trade'
    }
  };

  // Opties per parameter; sleutel `<Strategie>.<param>` wint.
  var PARAM_OPTION_LABELS = {
    order_mode: { '0': 'Long marktorder', '1': 'Long stoporder boven candle-high', '2': 'Short stoporder onder candle-low' },
    data_timeframe: { H1: '1 uur (H1)', H4: '4 uur (H4)', D1: '1 dag (D1)' }
  };

  // ---------------------------------------------------------------- testerinstellingen (element-id's)
  var SETTING_LABELS = {
    balance: {
      label: 'Startsaldo', unit: 'rekeningvaluta', simple: true,
      help: 'Beginkapitaal van de test (starting_balance in Jesse).',
      suggest: '10.000', example: 'Resultaten in procent hangen hiervan af; ordergrootte niet'
    },
    fee: {
      label: 'Transactiekosten', unit: 'fractie per fill', simple: true,
      help: 'Fractie van de orderwaarde die Jesse per uitgevoerde order aftrekt: 0,0006 is 0,06% per zijde (entry en exit apart).',
      suggest: '0,0006 (0,06%)', example: '0,001 = 0,1% per fill, dus 0,2% voor een volledige trade'
    },
    'account-type': {
      label: 'Rekenmodel', unit: '', simple: true,
      help: 'Spot: je koopt het onderliggende product, geen short en geen hefboom. Futures: short en hefboom mogelijk.',
      suggest: 'Futures voor short-strategieen', example: 'Order modus 2 (short) werkt alleen met Futures'
    },
    leverage: {
      label: 'Hefboom', unit: 'x', simple: true,
      help: 'Hefboomfactor (cross margin). Wordt alleen gebruikt bij het rekenmodel Futures; bij Spot genegeerd.',
      suggest: '1 (geen hefboom)', example: '10 = tien keer zoveel positie per marge, maar ook tien keer het risico'
    },
    warmup: {
      label: 'Warm-up candles', unit: 'candles', simple: false,
      help: 'Aantal candles van voor de startdatum dat alleen wordt ingeladen zodat indicatoren (EMA, RSI) al gevuld zijn. Er wordt niet in gehandeld.',
      suggest: '210 (genoeg voor EMA 200)', example: 'Te laag = indicatoren starten leeg en eerste trades missen'
    },
    trials: {
      label: 'Aantal pogingen (trials)', unit: 'pogingen', simple: true,
      help: 'Aantal parametercombinaties dat de optimalisatie uitprobeert. Bij Grid wordt zoveel combinaties doorlopen, in volgorde.',
      suggest: '20 tot 100', example: 'Meer pogingen = betere kans op goede waarden maar langere looptijd'
    },
    'train-percent': {
      label: 'Trainingsdeel', unit: '% van de candles', simple: true,
      help: 'Eerste deel van de periode waarop wordt geoptimaliseerd; het resterende laatste deel is de holdout waarop de gevonden waarden controle-getest worden. De splitsing volgt de tijdsvolgorde.',
      suggest: '80', example: '80 = eerste 80% trainen, laatste 20% controleren'
    },
    objective: {
      label: 'Optimalisatiedoel', unit: '', simple: true,
      help: 'Maatstaf waarop de beste poging wordt gekozen. De score wordt berekend op de trainingsperiode.',
      suggest: 'Sharpe-ratio', example: 'Calmar kijkt vooral naar de grootste daling'
    },
    'optimizer-method': {
      label: 'Zoekmethode', unit: '', simple: true,
      help: 'Random trekt willekeurige combinaties; Grid loopt alle combinaties op een raster af; TPE leert van eerdere pogingen.',
      suggest: 'TPE of Random', example: 'Grid is volledig maar groeit snel met aantal parameters'
    },
    'optimizer-seed': {
      label: 'Seed (herhaalbaarheid)', unit: 'getal', simple: false,
      help: 'Startwaarde van de toevalsgenerator: dezelfde seed met dezelfde instellingen geeft dezelfde pogingen.',
      suggest: '42', example: 'Andere seed = andere willekeurige combinaties'
    },
    'resume-run': {
      label: 'Hervatten vanaf', unit: '', simple: false,
      help: 'Zet een eerder gestopte optimalisatie voort vanaf het aantal reeds voltooide pogingen in plaats van opnieuw te beginnen.',
      suggest: 'Nieuwe optimalisatie', example: 'Kies een eerdere run om verder te gaan'
    },
    'contract-size': {
      label: 'Contractgrootte', unit: 'eenheden per lot', simple: false,
      help: 'Aantal eenheden van het product in 1 lot. Winst = prijsverschil x lots x contractgrootte x conversiekoers.',
      suggest: '100 (XAUUSD: 100 troy ounce)', example: '1 lot goud, +1 prijspunt = 100 USD'
    },
    'account-currency': {
      label: 'Accountvaluta', unit: 'valutacode', simple: false,
      help: 'Valuta waarin saldo, winst en commissie worden uitgedrukt.',
      suggest: 'USD', example: 'EUR als je rekening in euro is'
    },
    'tick-size': {
      label: 'Tickgrootte', unit: 'prijspunten', simple: false,
      help: 'Kleinste prijsstap van het symbool. Slippage in points wordt hiermee omgerekend naar prijs (points x tickgrootte).',
      suggest: '0,01 (goud)', example: 'Slippage 5 points x 0,01 = 0,05 prijs'
    },
    conversion: {
      label: 'Conversiekoers quote naar account', unit: 'koers', simple: false,
      help: 'Vermenigvuldiger die winst in de quotevaluta van het symbool omrekent naar je accountvaluta.',
      suggest: '1 als beide gelijk zijn (bijv. XAUUSD op USD-rekening)', example: 'USDJPY op USD-rekening: 1 gedeeld door de actuele USDJPY-koers'
    },
    'volume-step': {
      label: 'Lot-stap', unit: 'lots', simple: false,
      help: 'Kleinste toegestane stap in ordergrootte. Orders die geen veelvoud van de stap zijn worden als ongeldig gemarkeerd.',
      suggest: '0,01', example: '0,015 lot bij stap 0,01 is ongeldig'
    },
    commission: {
      label: 'Commissie per lot per zijde', unit: 'rekeningvaluta per lot', simple: false,
      help: 'Commissie per lot bij openen en bij sluiten; per volledige trade betaal je het dus twee keer.',
      suggest: '0 of de waarde van je broker', example: '3,5 per zijde = 7 per lot per trade'
    },
    slippage: {
      label: 'Slippage', unit: 'ticks (points)', simple: false,
      help: 'Extra ongunstige prijsafwijking bij market- en stopfills, in ticks. Prijs = points x tickgrootte; koop vult hoger, verkoop lager.',
      suggest: '0 tot 5', example: '5 points bij tickgrootte 0,01 = 0,05 slechter dan de quote'
    },
    'chart-limit': {
      label: 'Maximum aantal candles in grafiek', unit: 'candles', simple: false,
      help: 'Toont alleen de laatste N candles van de gekozen periode in de grafiek; dit verandert de test of de resultaten niet.',
      suggest: '1.000', example: 'Onbeperkt kan trager zijn bij lange periodes'
    }
  };

  var OPTION_LABELS = {
    objective: {
      sharpe: 'Sharpe-ratio (rendement per eenheid risico)',
      calmar: 'Calmar-ratio (rendement t.o.v. grootste daling)',
      sortino: 'Sortino-ratio (rendement per eenheid neerwaarts risico)',
      omega: 'Omega-ratio (winsten t.o.v. verliezen)',
      recovery_factor: 'Herstelfactor (nettowinst t.o.v. grootste daling in geld)'
    },
    'optimizer-method': {
      random: 'Random (willekeurige combinaties)',
      grid: 'Grid (alle combinaties op een raster)',
      tpe: 'TPE (leert van eerdere pogingen)'
    },
    'account-type': {
      spot: 'Spot (alleen long, geen hefboom)',
      futures: 'Futures (short en hefboom mogelijk)'
    }
  };

  // ---------------------------------------------------------------- helpers
  var WORDS = {
    ema: 'EMA', sma: 'SMA', rsi: 'RSI', atr: 'ATR', adx: 'ADX', bb: 'Bollinger', tp: 'Take-profit', sl: 'Stop-loss',
    bars: 'candles', bar: 'candle', period: 'periode', mult: 'vermenigvuldiger', max: 'maximum', min: 'minimum',
    size: 'grootte', distance: 'afstand', offset: 'afstand', stop: 'stop', target: 'doel', entry: 'instap',
    interval: 'interval', fast: 'snelle', slow: 'trage', low: 'laag', high: 'hoog', pct: '%', percent: '%'
  };

  function humanize(name) {
    var text = String(name).split('.').pop().split('_').map(function (part) {
      return WORDS[part.toLowerCase()] || part;
    }).join(' ').trim();
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function own(object, key) {
    return object && Object.prototype.hasOwnProperty.call(object, key) ? object[key] : undefined;
  }

  // Backend (def.label/description/unit) wint, daarna PARAM_LABELS (strategie-specifiek eerst), daarna een afgeleide naam.
  window.paramLabel = function (name, def, strategyName) {
    def = def || {};
    var known = (strategyName && own(PARAM_LABELS, strategyName + '.' + name)) || own(PARAM_LABELS, name) || null;
    var merged = {};
    if (known) { for (var key in known) merged[key] = known[key]; }
    merged.name = name;
    if (def.label) merged.label = def.label;
    if (def.description) merged.help = def.description;
    if (def.unit) merged.unit = def.unit;
    if (def.simple !== undefined && def.simple !== null) merged.simple = !!def.simple;
    if (!merged.label) merged.label = humanize(name);
    if (!known && !def.description) {
      merged.help = merged.help || 'Geen uitleg beschikbaar; de werking van deze parameter is niet geverifieerd.';
      merged.unverified = true;
    }
    if (merged.unit === undefined) merged.unit = '';
    if (merged.simple === undefined) merged.simple = false;
    if (!merged.group) merged.group = 'Overig';
    return merged;
  };

  window.paramOptionLabel = function (name, value, strategyName) {
    var map = (strategyName && own(PARAM_OPTION_LABELS, strategyName + '.' + name)) || own(PARAM_OPTION_LABELS, name);
    var label = own(map, String(value));
    return label === undefined ? String(value) : label;
  };

  window.PARAM_LABELS = PARAM_LABELS;
  window.PARAM_OPTION_LABELS = PARAM_OPTION_LABELS;
  window.SETTING_LABELS = SETTING_LABELS;
  window.OPTION_LABELS = OPTION_LABELS;
})();
