# Research Ledger

Het Research Ledger (`strategy_lab/research/ledger.py`, standaard `strategy_lab/data_cache/research_ledger.sqlite3`) legt **elke** geteste hypothese vast, ook de mislukte. Zonder dat overzicht zijn de statistieken over "de beste" strategie waardeloos: wie honderd varianten test, vindt altijd een mooie.

## Schema (tabel `entries`)

| Veld | Betekenis |
| :--- | :--- |
| `id` | uuid4 hex |
| `created_at` | UTC ISO-8601 |
| `kind` | `BACKTEST`, `VALIDATION`, `DISCOVERY`, `HOLDOUT` |
| `hypothesis_id`, `parent_id`, `family` | groepering; `parent_id` verwijst naar de entry die deze corrigeert of verfijnt |
| `symbol`, `timeframe`, `start`, `end` | geteste data (periode is `[start, end)`) |
| `spec_json`, `spec_sha256`, `params_json`, `mutations_json` | strategiedefinitie en parameters |
| `code_sha256`, `dataset_sha256` | exacte code en dataset (reproduceerbaarheid) |
| `cost_model_json`, `data_requirements_json` | kostenmodel en datavereisten |
| `status` | `TESTED`, `CANDIDATE`, `REJECTED`, `ERROR` |
| `rejection_reason`, `verdict`, `score` | uitkomst |
| `metrics_json`, `oos_metrics_json` | in-sample en out-of-sample metrics (`trades`, `net_profit`, `profit_factor`, `sharpe_daily`, ...) |
| `statistics_json` | bv. DSR, PBO, gebruikte `n_trials` |
| `trials_json` | alle optimizer-trials |

## Append-only garantie

`UPDATE` en `DELETE` op `entries` worden door sqlite-triggers geblokkeerd (`RAISE(ABORT)`). Een correctie is altijd een **nieuwe rij** met `parent_id` naar het origineel. De API (`record`) kent geen wijzig- of verwijderfunctie.

## API

`ResearchLedger(path)`: `record(**velden) -> id`, `get(id)`, `list(status, kind, family, limit)` (metrics `trades`, `net_profit`, `profit_factor`, `oos_net_profit` zijn naar boven gehaald), `summary()`, `n_trials(family, symbol, timeframe)`, `sharpe_variance(family)`, `data_exposure(symbol, start, end)`.

## n_trials en de Deflated Sharpe Ratio

`n_trials()` telt het aantal **verschillende hypotheses** (`COUNT(DISTINCT COALESCE(hypothesis_id, id))`) binnen een familie, symbool of timeframe. `sharpe_variance()` geeft de variantie van `sharpe_daily` over alle vastgelegde tests (`None` bij minder dan 2).

De Deflated Sharpe Ratio corrigeert voor selectiebias: de verwachte maximale Sharpe onder `N` onafhankelijke pogingen is ruwweg `sqrt(V) * ((1-g) * Phi^-1(1-1/N) + g * Phi^-1(1-1/(N*e)))`, met `V` = `sharpe_variance` en `N` = `n_trials`. De DSR is de kans dat de waargenomen Sharpe boven die drempel ligt. Hoe meer hypotheses in het ledger, hoe hoger de lat. Daarom mag een hypothese nooit uit het ledger verdwijnen.

## Data-blootstelling (data exposure)

`data_exposure(symbol, start, end)` geeft alle eerdere entries terug waarvan `[start, end)` overlapt met de gevraagde periode. Een holdout-periode die al in eerdere tests voorkwam, is niet meer "ongezien"; de UI toont dat zodat een holdout-resultaat niet als onafhankelijk bewijs wordt gepresenteerd.
