# Dukascopy-monitor: validatie (data supervisor)

**Status PARTIAL.** Alle scenario's draaien met nagebootste HTTP en een virtuele klok (FIXTURE, 30 tests in `strategy_lab/tests/test_v10_supervisor.py`, suite 651 tests groen). De installatie-audit en een echte supervisorstatus zijn op deze machine gemeten (REAL). De echte provider gaf tijdens de audit eerst HTTP 503 (echte storing, backoff werd gehonoreerd) en herstelde om 13:57 UTC; het inhalen loopt nog (GBPUSD 2026-10-29 echt geimporteerd). DE40 blijft ERROR door een kalenderafwijking die handmatige controle vraagt. Niets is gecommit.

## Wat het doet

`strategy_lab/data_supervisor.py` leest alleen: partitiestore, backoff-tabel, Windows-taak, worker-thread en de laatste regels van de originele CSV's. Het stuurt zelf nooit een providerverzoek; `check_sync` gebruikt `run_sync` (dezelfde vergrendeling, backoff en 1 verzoek/s). Status, gebeurtenissen en kwaliteitscache staan in `strategy_lab/data_cache/data_supervisor.sqlite3`.

- Verwachte laatste complete UTC-dag per symbool uit de marktkalender; gesloten markt (weekend, feestdag, sessie) telt niet als achterstand; gisteren telt pas na de dagelijkse updatetijd.
- Per symbool: laatste candle, achterstand in handelsdagen, laatste controle/succes, volgende poging, backoff (aantal fouten, geblokkeerd), reden in het Nederlands.
- Gezondheid: planner (geinstalleerd/ingeschakeld/laatste resultaat/oud), worker (thread leeft, hartslag), updater (vergrendeling vast of RUNNING-rij zonder proces).
- Inhalen na slaap/herstart: de worker slaapt in plakken op de wandklok; een sprong > 2 minuten (slaap/hibernate) wekt hem direct en logt `RESUME`; de gestopte backoff blijft gelden. Geplande runs loggen een hartslag; een gat > 3x interval telt ook als hervatting.
- Kwaliteit per symbool (gecachet op store-handtekening): bid&ask aanwezig, UTC, duplicaten, negatieve spread, prijsschaal t.o.v. laatste bekende koers, onvolledige dagen, gaten, providerrevisies.
- `dataset_snapshot(symbol)` (ook `Supervisor.dataset_snapshot(symbol, now=...)`): sha256 over identiteit van de originele CSV's (grootte, mtime, sha uit DataManager-manifest) en de nieuwste revisiehash per dagpartitie; `version` = `SYMBOOL@laatste-dag#sha10`. Met `now` is een eerder snapshot reproduceerbaar.

## API

`GET /api/data/supervisor` geeft `overall` (`UP_TO_DATE|PARTIAL|ERROR`), `overall_nl`, `last_check`, `last_success`, `scheduler{installed, enabled, state, last_run, last_run_age_s, last_result, last_result_class, last_result_nl, next_run, catch_up, wake_to_run, missed_runs, stale}`, `worker{alive, state, heartbeat_age_s, stuck, requests, filled, last_error, resumed_at}`, `updater{running, lock_held, stuck, run_age_s}`, `issues[{code, nl}]`, `events`, `job`, en `symbols[{symbol, last_candle, expected_day, lag_days, first_missing_day, market_closed, status, reason_nl, last_check, last_success, next_attempt, backoff_until, backoff, quality{bid_ask, duplicates, negative_spread, scale_ok, incomplete_days, gaps, revisions, utc}, snapshot_sha}]`. Symboolstatus: `UP_TO_DATE`, `MARKET_CLOSED`, `CATCHING_UP`, `DELAYED`, `BLOCKED`, `ERROR`. `overall` is ERROR als geen instrument actueel is, de updater vastzit of niemand (planner uit en worker dood) de achterstand kan oppakken; PARTIAL als sommige achterlopen.

`POST /api/data/supervisor/check-sync` (JSON, lokale origin) start op de achtergrond een controle plus synchronisatie en antwoordt `{job:{id,state,...}, poll}`. Een lopende job wordt hergebruikt; de status staat onder `job` in de GET. Jobstatus: `RUNNING|DONE|BUSY|ERROR`; `result` toont geimporteerd, door backoff vastgehouden en mislukte dagen.

CLI: `python -m strategy_lab.data_check [--compact] [--sync] [--scheduled]`.

## Installatie op deze machine

Zie `evidence/v10/data/install_audit.json`. Bestaande taak "Dukascopy Strategy Lab Daily Sync" was geregistreerd en gezond (herhaling elke 30 min + bij aanmelden, StartWhenAvailable aan, geen wake, IgnoreNew). Laatste resultaat 1 betekent "klaar met openstaande dagen", geen crash. Aangepast: `install-daily-sync.ps1` is idempotent (vast anker 00:00:15, bewaart oude definitie in `evidence/v10/data/scheduled_task_previous.xml`), de taak draait nu `data_check --scheduled`: hartslag, en alleen een synchronisatie als er een complete sessiedag mist, de updatetijd voorbij is en geen backoff geldt (of er inbox-bestanden liggen). Eindigt met exit 0; alleen een echte crash is niet-nul. Handmatig gestart: resultaat 0. De taak draait met de app dicht, niet als de Windows-gebruiker is afgemeld en wekt de pc niet. Frequenties ongewijzigd (app-worker druk 20 s, rustig 15 min).

## Scenario's

| Scenario | Bewijs | Resultaat |
| --- | --- | --- |
| Normale dagelijkse sync, vandaag nooit opgevraagd | FIXTURE | PASS |
| Geen nieuwe data: geen verzoek, partities en snapshot ongewijzigd | FIXTURE | PASS |
| Markt gesloten (weekend) is geen vertraging, geen verzoek voor gesloten dagen | FIXTURE | PASS |
| Eerste dag is gesloten dag: geen achterstand | FIXTURE | PASS |
| Dag voor de updatetijd nog niet verwacht | FIXTURE | PASS |
| Tijdelijke 503: backoff, geen verzoek binnen backoff, herstel erna | FIXTURE | PASS |
| Echte 503 op de provider (13:30-13:55 UTC): backoff gehonoreerd, geen verzoeken tijdens wachttijd, herstel 13:57 | REAL | PASS |
| Lange storing: backoff stijgt naar plafond van 1 u, status blijft zichtbaar | FIXTURE | PASS |
| HTTP 429: Retry-After gehonoreerd | FIXTURE | PASS |
| WAF/403: BLOCKED, 6 u afkoeling, niet omzeild | FIXTURE | PASS |
| Slaap/hervatting: kloksprong wekt worker en wordt gelogd; geplande hartslaggat | FIXTURE | PASS |
| Planner gestopt, ontbreekt, verouderd, laatste resultaat mislukt | FIXTURE | PASS |
| Planner van de echte machine gelezen (ingeschakeld, catch-up, volgende run) | REAL | PASS |
| Worker hartslag vast, updater vast (RUNNING zonder slot) | FIXTURE | PASS |
| Een downloader per symbool-dag: tweede sync krijgt BUSY | FIXTURE | PASS |
| App-herstart: status, snapshot en backoff blijven; geen herdownload | FIXTURE | PASS |
| Gemiste dagen in een run ingehaald; budget over meerdere runs | FIXTURE | PASS |
| Zelfde dag twee keer: geen duplicaat, snapshot gelijk | FIXTURE | PASS |
| Providerrevisie: nieuw snapshot, geteld, eerder snapshot reproduceerbaar | FIXTURE | PASS |
| Onvolledige bid/ask geweigerd en gemeld | FIXTURE | PASS |
| Verkeerde puntfactor geweigerd voor opslag | FIXTURE | PASS |
| Negatieve spread in opslag gemeld als kwaliteitsfout | FIXTURE | PASS |
| Check+sync-job op de achtergrond, API-vorm, beide endpoints | FIXTURE + REAL server | PASS |
| Echte inhaalimport via provider (GBPUSD 2026-10-29, 1440 rijen) | REAL | PARTIAL: loopt nog, 7 symbolen achter |
| DE40 sessiekalender komt niet overeen met de aanbieder | REAL | BLOCKED: kalender in `daily_sync.local.json` moet door de gebruiker bevestigd worden |
| Originele CSV's onaangetast | FIXTURE sha256 voor/na elk scenario; REAL 24 bestanden grootte+mtime voor/na | PASS (49 GB: echte bestanden niet volledig gehasht) |

## Beperkingen

- Planner werkt alleen terwijl de Windows-gebruiker is aangemeld (Interactive); afgemeld of uit draait niets. Wijzigen vraagt een opgeslagen wachtwoord of SYSTEM-account en is niet gedaan.
- `lag_days` telt handelsdagen volgens de kalenders uit `daily_sync.local.json`; die zijn bij benadering (zie DAILY_DUKASCOPY_PIPELINE.md).
- Het FeedClient-bestandsbudget (2 per symbool-dag) geldt per proces; elke run en elke trickle-stap laadt een nieuwe config, dus een eerdere fout verbruikt het budget van de volgende poging niet.
