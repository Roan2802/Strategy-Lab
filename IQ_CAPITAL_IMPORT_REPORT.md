# IQ Capital strategiebibliotheek - importrapport

Bron (alleen gelezen, niet gewijzigd): `C:\Users\Roanv\Downloads\IQCapital_Strategie_Bibliotheek_AUDIT2_2026-11-05`
Code: `strategy_lab/library.py` - tests: `strategy_lab/tests/test_library.py` (8 passed)
Resultaat: `strategy_lab/data_cache/library/catalogue.json` + `strategy_lab/data_cache/library/dossiers/<id>/*.md` (kopieen).

## Wat is geimporteerd

| Item | Status | Toelichting |
| :--- | :--- | :--- |
| 45 dossiers (strategy.json + data_readiness.json) | PASS | 0 importfouten, per dossier sha256 van alle bronbestanden vastgelegd |
| Markdown (build_spec, EVIDENCE_MATRIX, REFERENCES, test_checklist) | PASS | gekopieerd, niet verplaatst |
| Bronlinks (video's, artikelen, specs) | PASS | in `sources` (video- plus artikel/spec-links); 6 dossiers hebben geen `source_urls` maar wel video-referenties |
| Onbekende regels (`unknown_rules`) | PASS | alle 45 behouden zoals in de bron |
| Import idempotent, bronmap ongewijzigd | PASS | getest met hash van catalogus en `strategies_index.csv` |
| Transcripten | DATA_REQUIRED | zitten niet in de bibliotheek (`full_text_included: false`); niet geimporteerd |

## Evidence-grades (werkelijke aantallen)

A = 7 (5x "A:", 2x "A-"), B = 27, C = 11. Dit komt overeen met de verwachting. Let op: 6 dossiers (40-45) hebben een afwijkend schema met een expliciet `grade`-veld; de overige 39 geven de grade alleen als prefix van `status`.

A/B/C beschrijft de **volledigheid van de regels**, niet winstgevendheid.

## OHLC-geschiktheid (Dukascopy)

Afgeleid uitsluitend uit `data_readiness.json` (mapping staat in de docstring van `library.py`).

| Klasse | Aantal | Betekenis |
| :--- | :--- | :--- |
| OHLC_READY | 19 | kandidaat op OHLC; exacte reproductie staat in de bron op "NOT YET VERIFIED" |
| OHLC_PROXY_ONLY | 17 | OHLC-versie bestaat alleen als andere strategie (proxy) |
| NEEDS_EXTERNAL_DATA | 6 | orderflow, prijs-per-volume of echte tick-/macrodata nodig |
| UNKNOWN | 3 | regels onvolledig (35, 37, 39); geen replicatie claimbaar |

OHLC_READY is geen bewijs dat de strategie op Dukascopy-CFD's werkt: het origineel draait op futures (meestal NQ/MNQ) en overdracht naar een ander instrument is een nieuwe hypothese.

## De 8 prioriteitsprototypes

| Dossier | Grade | Data-status | Ontbrekende / onduidelijke regels | Datavereisten | Label |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 03_matteo_drift_vwap | A | OHLC_READY | state-reset na verlies M15-regime; timing M15-snapshot vs M5-trigger; exacte sluitsessie en max dagverlies | futures OHLCV + sessie-VWAP vanaf 09:30 NY; M5+M15 gesynchroniseerd | PARTIAL |
| 04_matteo_vault_break | A | OHLC_READY | VWAP-reset vs midnight-open; sessie-ATR en sessie-einde; re-entry volgende bar of verse crossing | RTH/Globex data, 00:00 Chicago open, ATR van 15 sessies, sessie-VWAP | PARTIAL |
| 05_matteo_new_vwap_adx | A | OHLC_READY | 15:55-zone dubbelzinnig; ADX eenmalig vs hernieuwde VWAP-close; max entries per dag | M1 OHLCV, VWAP, Wilder-ADX, Opening Range 08:30-09:00 Chicago | PARTIAL |
| 06_matteo_overnight_bias_orb | A | OHLC_READY | ATR-definitie, exacte exit 14:30 CT; ADX-periode (14 is hypothese) | overnight high/low 23:00-08:30 CT, RTH-open, ADX M15, ATR 15 sessies | PARTIAL |
| 18_mike_crt_3candle | A | OHLC_READY | minimum sweep, body-inside definitie, HTF-bias, stop-ordertype | OHLC per timeframe, NY-sessie, economische agenda | PARTIAL |
| 19_mnq_atr_extreme_long | A | OHLC_READY | ATR-lengte; markturen openingscandle | M5 OHLC, ATR zonder lekkage, intrabar fill-volgorde | PARTIAL |
| 29_hannes_orb | B | OHLC_READY | referentiestart 15-minutencandle; touch vs close; sessie-exit | M15 OHLC, Duitse/US openingsuren en DST, tick size | PARTIAL |
| 35_trader_x_nr4_breakout | B | UNKNOWN | timeframe en NR4-definitie; 4-bar vs enkele smalste bar; richting, offset, expiry; scale-out en trailing | tijdgestempelde NQ OHLCV, ex-ante volatiliteit, realistische fills | DATA_REQUIRED |

Geen van de 8 is een exacte reproductie. Elk prototype moet als eigen, testbare onderzoeksvariant worden gebouwd (`translation_status = RESEARCH_VARIANT_REQUIRED`).

## Component-concepten

`component_concepts()` levert 16 bouwstenen (VWAP, ORB, ADX, ATR, ranges/drie aanrakingen, liquidity sweep, FVG, autocorrelatie, Hurst, HTF-regime, opening range, winner-only add, balans vs trend, context/scenario, NR4, effort-vs-result). `SOURCE_RULE` alleen als de dossiertekst de regel noemt; anders `OWN_TESTABLE_TRANSLATION`. Keywordmatch op dossiertekst is indicatief, geen inhoudelijke audit. Dossier-ids komen uit de catalogus.

## Eerlijke kanttekeningen

- Er zijn geen bewezen winstgevende algoritmen geimporteerd; backtestcijfers uit de bron zijn niet onafhankelijk geverifieerd.
- Originele transcripts ontbreken; regels komen uit publieke beschrijvingen, artikelen en samenvattingen.
- A/B/C = volledigheid van regels, niet kwaliteit of rendement.
- Geen record is "EXACT_ORIGINAL_STRATEGY"; alles is `RESEARCH_VARIANT_REQUIRED`.
- Veel regels hangen af van futures-sessiedata (RTH, VWAP-reset, volume); Dukascopy-tickvolume is geen futuresvolume.
- Bron-readiness-flags zijn overgenomen, niet zelf gevalideerd; mappingkeuzes (o.a. OHLC_PROXY_ONLY bij `price_only_proxy_is_distinct_strategy = true`) staan in `library.py`.
