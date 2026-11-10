# V9.1 — Validatie op echte marktdata en één onderzoeksomgeving (2026-11-10)

Alle resultaten hieronder zijn berekend op de bestaande Dukascopy-bestanden in `C:\AI\Dukascopy_data`, aangevuld met de dagelijkse partitie-store. Er zijn geen synthetische trades als bewijs gebruikt; synthetische fixtures komen alleen voor in unittests van logica. Er zijn geen orders geplaatst.

## Samenvatting

| Strategie | Type en engine | Oordeel | Waarom |
|---|---|---|---|
| EmaRsiPullback, XAUUSD M15, 2025-01-01 t/m 2026-10-01 | Builder, Bid/Ask-regelengine | **REJECT** | Netto −1.882 na kosten, PF 0,81, DSR 0,00 bij N = 260, kostenstress negatief, 8% van de buurparameters winstgevend, geen enkel winstgevend jaar |
| LabBreakout, XAUUSD M15, 2026-01-01 t/m 2026-10-01 | Handgeschreven Jesse, echte Jesse-backtests in Docker | **REJECT** | Netto −627, alle trades verlies (PF 0), walk-forward heeft 1 van 7 bruikbare vensters, OOS −274, DSR 0,00 bij N = 291 |
| GoldReaper v2, XAUUSD M1, 2025-07-01 t/m 2026-10-01 | Python-plugin met eigen engine | **OOS_PASSED**, niet FORWARD_ELIGIBLE | Alle prestatiechecks geslaagd: 590 trades, PF 3,57, WFE 0,88, OOS +3.018, spread +50% nog +3.462, DSR 0,954 bij N = 62. Begrensd door de herkomst van het bewijs: (1) aantal buiten het lab geprobeerde varianten onbekend (de naam zegt "Pass3436"); (2) holdout al 7 keer eerder bekeken |

Een verliesgevende strategie wordt gewoon afgewezen. De drempels van het scorecard zijn niet versoepeld. De twee nieuwe herkomstchecks maken het oordeel alleen strenger: ze kunnen FORWARD_ELIGIBLE begrenzen tot OOS_PASSED, maar nooit een afwijzing opheffen.

## Wat GoldReaper nog nodig heeft voor een forwardtest

1. In `strategy_lab/plugins/gold_reaper/plugin.py` → `PLUGIN["research_history"]["external_trials"]` het werkelijke aantal geteste varianten invullen en de periode waarop geoptimaliseerd is. Dat getal telt mee in N voor DSR.
2. Een holdout die nog niet is ingezien, bijvoorbeeld de nieuwste dagen die de dagelijkse aanvulling binnenhaalt.
3. Forwardregistratie voor plugins bestaat nog niet. De forward-engine kent alleen Builder-regels en de geaudite LabBreakout-snapshot. Dit staat als beperking in de app.

## 1. Statistische engine op echte data

Bewijs:
- `evidence/v9/validation/*.json`: API-runs met de volledige request, voortgangslog, timing en resultaat.
- `evidence/v9/browser/`: dezelfde route via de echte browser (screenshots plus `proof-chrome.json`).

| Onderdeel | Status | Bewijs |
|---|---|---|
| Walk-forward rolling | PASS | Builder: 13 vensters, WFE 0,75, OOS +831 over 159 trades. Jesse: 7 vensters, 59 echte Jesse-backtests. Plugin: 9 vensters, WFE 0,88 |
| Walk-forward expanding | PASS | Builder expanding: 13 vensters, WFE 0,07, OOS −365 (`builder_EmaRsiPullback_expanding.json`) |
| Monte Carlo op echte backtesttrades | PASS | Block bootstrap, 1.000 simulaties op de echte tradelijst. Plugin: DD P5/P50/P95 0,9/1,7/3,0%, ruïnerisico 0%. LabBreakout: DD P50 6,3% |
| Parameterstabiliteit | PASS | Builder 8%, LabBreakout 0%, GoldReaper 100% winstgevende buurwaarden |
| Kostenstress | PASS (Builder, plugin); PARTIAL (Jesse) | Builder en plugin: spread +25%/+50% en commissie ×2. Jesse: alleen fee ×1,5 en ×2 (−939 / −1.250). Spread- en slippagestress staat als NOT_TESTED met de reden dat Jesse met Bid-candles en een fee-fractie rekent |
| Train / validatie / OOS (holdout) | PASS | Holdout wordt eenmalig getest en vastgelegd in het logboek, met het aantal eerdere inzages (plugin 7, LabBreakout 20, Builder 46) |
| PBO (CSCV) | PASS | Builder 0,23 (16 combinaties), Jesse 0,06, plugin 0,00 (4 combinaties, laag aantal) |
| Deflated Sharpe | PASS | Gecorrigeerd met N; zie hieronder |
| Overfittingdetectie | PASS | PBO, DSR, PSR, MinTRL, resultaat zonder de beste trades, consistentie per jaar |
| Volledige scorecard | PASS | 15 checks inclusief herkomst; alle drie de strategieën kregen een volledig oordeel |

### Telling van geprobeerde configuraties

N voor DSR, Holm en haircut is de som van:
- de onderscheiden hypotheses in het logboek voor deze markt en dit tijdframe (inclusief configuraties die eerdere validaties al doorrekenden);
- dezelfde strategiefamilie op andere markten;
- optimizer-trials uit opgeslagen Jesse-runs die nog niet in het logboek staan;
- nieuwe configuraties uit deze validatie (walk-forward, stabiliteit, PBO);
- het opgegeven aantal externe trials (plugins).

Gecontroleerd:
- **Tester in het logboek:** iedere backtest en optimizer-trial uit de tester komt nu in het logboek, met een hypothesis_id op basis van strategie en parameters. Identieke parameters tellen één keer.
- **Herhaling verhoogt N:** een validatie opnieuw draaien verhoogt N, omdat herhaald kijken ook selectie is. Gemeten bij Builder: 106 bij de eerste run, 132 bij de tweede, 181 bij de derde, 260 bij de browserrun.
- **Variantie voor de DSR:** de grootste van twee bronnen: de kandidaten in deze run, en eerdere trials van dezelfde strategiefamilie in het logboek (omgerekend van jaar- naar dagbasis). Een handvol kandidaten onderschat de spreiding; andere families zijn een andere selectie.
- **Tests:** `test_validation_pipeline.py` bewijst dat N stijgt met herhaling en met optimizer-trials, en dat identieke parameters niet dubbel tellen. `test_validation.py::test_scorecard_provenance_caps_forward_eligible` dekt de herkomstbegrenzing.

## 2. Drie strategietypen, één onderzoeksomgeving

Adapters (`strategy_lab/research/adapters.py`) geven iedere strategie dezelfde backtestfunctie voor de validatiepipeline (`research/validation_runner.py`). De engines worden niet gelijkgetrokken.

| Functie | Builder | Jesse (handgeschreven) | Python-plugin |
|---|---|---|---|
| Backtest | PASS (Bid/Ask-engine, en via de Jesse-kopie in de tester) | PASS (Docker-runner) | PASS (eigen engine, Strategieën → Overzicht) |
| Optimalisatie | PASS (via de Jesse-kopie) | PASS (Jesse-optimizer) | BLOCKED: geen optimizer voor plugins; walk-forward- en PBO-raster werken wel |
| Inspectie (beslislogboek) | PASS | PASS | BLOCKED: plugins leveren geen beslisevents |
| Statistische validatie | PASS | PASS (echte Jesse-backtests per combinatie) | PASS |
| Forwardregistratie | PASS (regelregistratie) | PARTIAL: alleen de geaudite LabBreakout-snapshot op M1 | BLOCKED: wordt als beperking getoond |

De validatiekiezer toont per strategie de mogelijkheden en de beperkingen voordat je start (`GET /api/research/strategies`). Contractspecificaties staan overal zichtbaar als "aanname, niet door broker bevestigd".

## 3. Consistentie tussen backtest en forward

Volledig rapport: `FORWARD_CONSISTENCY_V9.md`, bewijs in `evidence/v9/consistency/`. Kort:

| Check | Status |
|---|---|
| Geen vooruitkijken, M1–D1 (4.492 afgekapte herberekeningen) | PASS |
| Causale aggregatie van hogere tijdframes | PASS |
| Backtest = forward op M1 (893 trades identiek) | PASS |
| Backtest tegenover forward op M5–D1 | PASS met gemeten verschillen: alleen exit-tijdstempels, en 3 fills in M1-gaten (−0,13 USD) |
| Scale-in in forward | Fout gevonden en hersteld: wordt nu geweigerd bij bevriezen (was 588,82 USD verschil) |
| Bid/Ask-zijden, slippage, commissie, SL/TP, pending orders | PASS |
| Contractspecificaties | PARTIAL: alleen tickgrootte EURUSD/GBPUSD/AUDUSD is uit data afgeleid; de rest is aanname en zichtbaar gemarkeerd |
| Herstart zonder dubbele trades (6 scenario's) | PASS |
| Jesse-forward tegenover Jesse-tester | PARTIAL: 226 van 230 entries op dezelfde candle; geldbedragen niet vergelijkbaar (Bid/Ask tegenover één prijsreeks met een fee-fractie) |

Onderweg hersteld:
- De forward-engine crashte voor DE40 en US500.
- De dagelijkse aanvulling voor US500 en DE40 faalde, omdat de app-naam niet werd vertaald naar de Dukascopy-instrumentnaam.
- `provider_symbol` accepteerde zijn eigen uitvoer niet.

## 4. Dagelijkse bediening

| Functie | Status | Bewijs |
|---|---|---|
| Lopende Jesse-backtest annuleren | PASS | Echte browsertest: 6,2 s na de klik is de run gestopt, de knoppen werken weer en er is geen actieve run meer (`evidence/v9/browser/cancel.json`). Werkt ook tijdens het laden van de data (eerst een fout, nu hersteld) |
| Voortgang van lange onderzoeken | PASS | Stap en backtestnummer ("Walk-forward venster 3/7, combinatie 2/4"), verstreken tijd, geschatte rekentijd vooraf |
| Automatisch opslaan en hervatten | PASS | De lopende validatie-job hangt na herladen weer aan; de laatste 5 resultaten zijn kiesbaar. De tester-sessie wordt hersteld (V8-test 19/19) |
| Duidelijke foutdiagnoses | PASS | Runnerfouten worden Nederlandse actiemeldingen (Docker uit, geen candles, parameter buiten bereik, spot-short, time-out) |
| Reproduceerbare configuraties | PASS | Iedere validatie bevat de volledige configuratie (data-bereiken, dataset- en code-sha, parameters, seeds, drempels), plus kopiëren, downloaden en laden |
| Strategieversies vergelijken | PASS | Vergelijken van 2–4 runs met sha- en parameterverschillen |

## 5. Regressie

- **pytest:** zie `TEST_RESULTS.md`. Dit omvat 17 tests voor de pipeline, 36 voor consistentie (waarvan 7 op echte data), 1 voor de herkomstbegrenzing en 3 voor symboolmapping.
- **V8 browser-E2E** op productie: Chrome 19/19 met 0 JS-fouten (`evidence/v9/e2e/`), Edge 1920×1080 19/19 met 0 JS-fouten. pytest: 388 geslaagd.

## Niet getest of nog open

- **Spread- en slippagestress voor Jesse-strategieën:** NOT TESTED, omdat de engine dat niet modelleert.
- **Plugins:** forwardregistratie, optimizer en beslislogboek zijn BLOCKED.
- **Brokerbevestigde contractspecificaties:** niet beschikbaar. Alle geldbedragen zijn onderzoeksschattingen.
- **PBO met weinig combinaties:** bij 4 combinaties (Jesse, plugin) is de PBO zwak bewijs. Meer combinaties kosten per combinatie een volledige backtest.
- **GoldReaper simuleert de tickvolgorde binnen een M1-candle:** de plugin-engine verzint die volgorde zelf, omdat er geen ticks zijn. Dit is een eigenschap van de plugin, niet door het lab te verifiëren.
- **Open-range-breakout-sjabloon:** geeft op H1 en hoger geen signalen, dus daar niet bewezen.
