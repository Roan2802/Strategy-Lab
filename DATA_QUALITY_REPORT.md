# Dukascopy data quality

## Scope

Read-only scan of paired M1 Bid/Ask CSV files discovered exclusively under `C:\AI\Dukascopy_data`. Counts and hashes are in `evidence/test_outputs/data_quality_symbols.json`; XAUUSD detail is in `data_quality_detail.json`. Pair caches, manifests, and test copies are stored in the audit project. No original market-data file was written.

| Symbol | Union rows | Bid without Ask | Ask without Bid | Gap intervals (daily / weekend / unclassified*) | Duplicate / invalid-DST | Missing / negative volume |
|---|---:|---:|---:|---:|---:|---:|
| XAUUSD | 8,244,053 | 2 | 0 | 4,032 (2,775 / 1,146 / 111) | 0 / 0 | 0 / 0 |
| USDJPY | 8,530,555 | 0 | 0 | 1,207 (0 / 1,111 / 96) | 0 / 0 | 0 / 0 |
| BTCUSD | 4,920,508 | 2 | 0 | 6 (0 / 0 / 6) | 0 / 0 | 0 / 0 |
| DE40 | 4,416,821 | 2 | 0 | 3,316 (2,643 / 654 / 19) | 0 / 0 | 0 / 0 |
| US30 | 4,415,140 | 0 | 0 | 3,316 (2,603 / 643 / 70) | 0 / 0 | 0 / 0 |
| US500 | 5,098,720 | 0 | 0 | 3,828 (3,013 / 745 / 70) | 0 / 0 | 0 / 0 |
| EURUSD | 8,530,555 | 0 | 0 | 1,207 (0 / 1,111 / 96) | 0 / 0 | 0 / 0 |
| GBPUSD | 8,530,555 | 0 | 0 | 1,207 (0 / 1,111 / 96) | 0 / 0 | 0 / 0 |
| AUDUSD | 8,530,555 | 0 | 0 | 1,207 (0 / 1,111 / 96) | 0 / 0 | 0 / 0 |
| USATECHIDXUSD | 5,094,715 | 0 | 2,670 | 3,823 (3,009 / 744 / 70) | 0 / 0 | 2,670 / 0 |
| USDCAD | 8,530,555 | 0 | 0 | 1,207 (0 / 1,111 / 96) | 0 / 0 | 0 / 0 |

*Daily/weekend classification uses an Europe/Helsinki wall-clock heuristic. Those classes are likely closure windows, not verified exchange calendars; all other gaps remain unclassified. Missing-minute counts are recorded in the JSON rather than inferred from row totals.

## Integrity and limitations

The XAUUSD source SHA-256 is Bid `1ddb8c54ff351c534673aaede9f66ae32a5981c7af88c159d304e58ea0ce9c16`, Ask `2c9f7ac0c9fe5afe835d42dca56b2903b753fdcb110ce579be72f43994539d2b`. Its scan reports 8,244,053 union timestamps, 4,032 gap intervals / 3,741,513 missing minutes, and no duplicates or invalid/DST rows. Dukascopy tick/volume columns are preserved as supplied and are not real traded volume. USATECHIDXUSD has 2,670 Ask-only timestamps with missing paired Bid/volume; execution replay skips incomplete quotes and returns a provisional status. Gap-class labels are estimates and should not be used as official session calendars.

All 11 discovered instruments have a parsed cache manifest. A first full-file parse is memory-intensive (about 4.3 GB peak was observed for XAUUSD in the earlier audit); repeated runs use the local cache.

## Updated first-import resource measurement — 2026-11-06

The earlier approximately 4.3 GB observation predates the final full-import benchmark. The updated Dukascopy-only Strategy Lab XAUUSD `ensure()` test completed in 28.202 seconds at 2.82 GB peak process RSS, with exact output equality to the existing 8,244,053-row Parquet cache and unchanged source fingerprints. This measurement is for the local Strategy Lab loader, not Jesse's native custom-data importer and not a simultaneous 11-symbol import. Full evidence: `evidence/test_outputs/chunked_dukascopy_parse_20261106.json`.

## Inbox-intake van JForex-exports — 2026-11-09

De inbox-modus van `daily_sync.py` valideert elke dag vóór opslag: negatieve spread, dubbele of niet-monotone tijdstempels, prijzen <= 0 en high < low leiden tot afwijzing van die dag met vastgelegde reden. Een onvolledige laatste dag wordt als INCOMPLETE gelogd en niet gepubliceerd. De tijdzone-interpretatie van `Time (EET)` (Europe/Helsinki, dubbele herfstuur in bronvolgorde) is getest tegen `DataManager._read_side`. Beperkingen: de sessiekalenders in `daily_sync.example.json` zijn bij benadering en onbevestigd; automatisch downloaden blijft geblokkeerd (HTTP 429 / WAF), dus de data-aanvoer blijft afhankelijk van handmatige exports. Gedekt door fixturetests in `strategy_lab/tests/test_daily_sync_inbox.py`; er is niet getest met echte Dukascopy-exports.
