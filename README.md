# BigDataProject

A local pipeline that collects LLM conversation statistics, stores them in PostgreSQL, and uses Grafana to analyze relationships among tokens, cost, duration, calls, users, and models.

## Quick start

```bash
cp .env.example .env
docker compose up --build -d
```

Grafana is available at [http://localhost:3000](http://localhost:3000). The default development credentials are `admin` / `admin`; set `GRAFANA_ADMIN_PASSWORD` in `.env` for shared environments.

On the first startup, the service:

1. creates or upgrades the PostgreSQL schema;
2. inserts 40 demo conversations only when the database is empty and `SEED_DEMO_DATA=true`;
3. immediately runs an API synchronization;
4. repeats the synchronization every 30 minutes through Ofelia.

The **Conversation Correlation Analytics** dashboard and PostgreSQL data source are loaded automatically, so Grafana requires no manual setup.

## Upstream API contract

The collector reads conversation metadata from:

```text
GET /api/v1/conversations?limit=50&offset=0
```

For each new or changed conversation, it reads token metrics from the dedicated endpoint:

```text
GET /api/v1/conversation/{conversation_id}/token-usage
```

The `updated_at` value returned by the list endpoint is stored alongside the token-usage snapshot. The dedicated endpoint is called again only when that value changes. A conversation without `updated_at` is fetched once; subsequent runs reuse its stored snapshot because the API provides no version marker to compare.

The list endpoint's `status` and `created_at` fields are merged with `llm_statistics` and `llm_calls` from the token-usage response before persistence.

## Available analytics

The dashboard includes:

- KPIs for conversations, calls, tokens, cost, duration, and the output/input ratio;
- incremental token usage over time;
- cost by model and latency by call type;
- input/output and token/cost scatter plots;
- Pearson correlation coefficients for tokens, cost, duration, and call count;
- an outlier table sorted by cost per 1,000 tokens;
- global filters for conversation status, model, and time range.

PostgreSQL also exposes two reusable views:

- `conversation_metrics`, with derived metrics for each conversation;
- `model_call_metrics`, with aggregates for each model.

## Grafana persistence

The `grafana_data` Docker volume preserves users, preferences, dashboards created in the UI, and saved changes. The main dashboard is also versioned in `grafana/dashboards/conversation-correlations.json`, allowing it to be recreated automatically in a new installation.

Provisioning allows changes from the UI (`allowUiUpdates: true`). To include a change in the project's shared configuration, export the updated JSON and replace the versioned file.

## Configuration

The main options are documented in `.env.example`:

- `API_BASE_URL`: upstream API base endpoint;
- `SYNC_SCHEDULE`: Ofelia schedule, defaulting to `@every 30m`;
- `SYNC_PAGE_SIZE` and `SYNC_MAX_PAGES`: pagination limits;
- `REQUEST_TIMEOUT_SECONDS`: HTTP timeout;
- `SEED_DEMO_DATA`: enables demo data only when the database is empty;
- `POSTGRES_*` and `GRAFANA_*`: ports and credentials.

To run a synchronization manually:

```bash
docker compose exec api_fetcher python fetch_and_update.py
```

To inspect status and logs:

```bash
docker compose ps
docker compose logs -f api_fetcher grafana
```

## Data quality

The synchronization processes every available conversation page, uses HTTP retries and timeouts, updates calls idempotently, and records only the difference from the previous snapshot in the history table. A source timestamp and unique index prevent the same update from being counted twice.

When the API does not return `cost_usd`, the collector estimates the cost using `model_pricing.json`, including support for version-suffixed model names. Costs supplied directly by the API always take precedence.

Call IDs are stored as text, supporting both numeric IDs and UUIDs. Individual call duration is stored when the API exposes `duration_ms` or `total_duration_ms`.

## Tests and validation

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
docker compose config --quiet
```
