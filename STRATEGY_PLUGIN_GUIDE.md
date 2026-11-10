# Je eigen strategie in Strategy Lab zetten

Er zijn drie vormen. Kies de eenvoudigste die je strategie volledig kan uitdrukken.

| Vorm | Wanneer | Wat werkt er dan |
| :--- | :--- | :--- |
| **Builder-regels** (Strategy Builder) | Indicatoren, prijspatronen, sessies, SL/TP/trailing — alles wat met keuzelijsten past | Backtest, valideren (alle toetsen), automatisch onderzoek, forward test, Jesse-code |
| **Jesse-strategie** (`runtime/strategies/<Naam>/__init__.py`) | Je wilt de Jesse-engine, Inspector en causale replay | Strategy Tester, optimizer, Inspector |
| **Python-plugin** (`strategy_lab/plugins/<id>/plugin.py`) | Je strategie heeft een eigen engine of logica die niet in regels past (zoals GoldReaper) | Backtest en valideren op Dukascopy Bid/Ask M1, researchlogboek |

## Python-plugin in 5 stappen

1. **Kopieer** de map `strategy_lab/plugins/voorbeeld_ma_cross` naar `strategy_lab/plugins/mijn_strategie` (alleen letters, cijfers en `_`).
2. **Pas `PLUGIN` aan**: naam, beschrijving, symbolen, en de parameters die je in de app wilt kunnen instellen.
3. **Schrijf `backtest()`** — of laat die je bestaande code aanroepen (zie GoldReaper hieronder).
4. **Controleer** met `python -m pytest -q strategy_lab/tests/test_plugins.py` of maak een eigen test naar dat voorbeeld.
5. **Open de app** → Strategieën. De plugin verschijnt vanzelf; de server laadt nieuwe code automatisch.

### Het contract

```python
PLUGIN = {
    "name": "Mijn strategie",
    "version": "1.0",
    "description": "Eén zin wat hij doet.",
    "symbols": ["XAUUSD"],          # app-symbolen waarop hij mag draaien
    "base_timeframe": "M1",         # de app levert altijd M1 Bid + Ask
    "warmup_days": 14,              # zoveel dagen historie vóór de startdatum
    "params": [                     # instelbaar in de app
        {"name": "stop_factor", "type": "float", "min": 0.5, "max": 3, "default": 1.0},
    ],
}

def backtest(bid, ask, params, *, capital, start, end, stress=None, progress=None) -> dict:
    ...
    return {"trades": trades, "equity": equity, "summary": {...}, "notes": [...]}
```

**Wat je krijgt**

- `bid`, `ask`: pandas DataFrames met UTC-index en kolommen `open, high, low, close, volume`, inclusief `warmup_days` vóór `start`. Echte Dukascopy-prijzen; ontbrekende minuten zijn niet ingevuld.
- `params`: de defaults uit `PLUGIN`, overschreven door wat de gebruiker instelt.
- `stress`: tijdens validatie bijvoorbeeld `{"spread_multiplier": 1.5}` of `{"commission_per_lot": 7}`. Verwerk ze als je strategie spread/commissie gebruikt; anders telt de kostentoets niet mee.
- `progress(fraction, bericht)`: optioneel aanroepen voor de voortgangsbalk.

**Wat je teruggeeft — iedere trade**

| Veld | Verplicht | Betekenis |
| :--- | :--- | :--- |
| `entry_time`, `exit_time` | ja | ISO-tijd **met tijdzone, in UTC** |
| `side` | ja | `long` of `short` |
| `entry_price`, `exit_price`, `qty` | ja | Uitgevoerde prijzen en lots |
| `pnl` | ja | Netto resultaat in accountvaluta, na kosten |
| `costs`, `r_multiple`, `exit_reason` | nee | Aanbevolen voor betere analyse |

Alleen trades met een entry in `[start, end)` mogen terugkomen; de app weigert anders de uitvoer.

### Spelregels (de app controleert wat hij kan)

- **Geen vooruitkijken**: beslis op een gesloten candle, voer uit op een latere prijs.
- **Kopen op Ask, verkopen op Bid.**
- **Geen verzonnen candles** of nieuwsdata die destijds niet bekend was.
- **Deterministisch**: dezelfde invoer geeft dezelfde trades (geen willekeur zonder vaste seed).
- Plugins zijn je eigen lokale code en worden geïmporteerd, niet gesandboxed. Zet er geen code van onbekende herkomst in.

## Voorbeeld: GoldReaper

`strategy_lab/plugins/gold_reaper/` bevat een **ongewijzigde kopie** van `gold_reaper.py` (SHA-256 `1d990bf9…6c82`) plus `plugin.py`, dat:

- Dukascopy UTC-tijd omzet naar brokerservertijd (UTC+2 winter / UTC+3 zomer, met de Amerikaanse DST-regel uit het script zelf);
- per minuut de echte Ask−Bid-spread doorgeeft;
- de engine draait via `run_m1_fast` (de eigen synthetische 4-punts tickvolgorde van het script);
- de trades terugvertaalt naar UTC en het contract hierboven.

Gemeten op XAUUSD augustus 2026: 34 trades, ±2 seconden per maand data. Beperkingen: M1-OHLC heeft geen echte tickvolgorde; de nieuwsfilters gebruiken de vaste regels uit het script, geen point-in-time kalender. Valideren loopt via dezelfde pipeline als Builder en Jesse (`research/validation_runner.py`): walk-forward, parameterstabiliteit en PBO draaien wanneer de plugin parameters met `min`/`max` declareert (kleine seeded grid, standaard max. 4 combinaties); commissie- en sizingparameters (of `"optimize": False`) blijven vast. Kostenstress telt alleen voor stress die `plugin.py` echt verwerkt (`spread_multiplier`, `commission_per_lot`, ...); de rest wordt NIET GETEST met reden gerapporteerd.

## Builder of Jesse in plaats van plugin?

Kan je strategie met de Builder? Dan krijg je meer: automatisch onderzoek, walk-forward, parameterstabiliteit en een bevroren forward test (zie `STRATEGY_BUILDER_GUIDE.md`). Een Jesse-strategie schrijf je volgens `STRATEGY_DEVELOPER_GUIDE.md`.

**Status:** plugin-contract, loader, GoldReaper-adapter en voorbeeldplugin — PASS (5 tests, echte Dukascopy-run). Forward test voor plugins — DEFERRED.
