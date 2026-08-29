# BigDataProject

A local analytics pipeline for monitoring how Large Language Models (LLMs) are used across conversations.

## Project purpose

The project collects conversation metadata and token-usage details from an upstream API, normalizes them, and stores them in PostgreSQL. Grafana then turns that data into a ready-to-use dashboard for studying relationships among:

- input and output tokens;
- estimated or reported cost;
- call duration and number of calls;
- conversation status, users, and models.

Its main purpose is to make LLM usage patterns, costs, performance, correlations, and unusual conversations easier to identify. The complete stack runs locally and can also generate deterministic demo data, so the dashboard remains useful when the upstream API is unavailable or does not yet contain data.

The stack contains four services:

- **PostgreSQL** stores conversations, individual LLM calls, and token-usage history;
- **the Python collector** fetches and incrementally updates data from the upstream API;
- **Ofelia** schedules periodic synchronizations;
- **Grafana** provides the provisioned analytics dashboard.

## Dependencies

The recommended setup only requires:

- [Docker](https://docs.docker.com/get-docker/) with Docker Compose v2;
- free local ports `3000` for Grafana and `5432` for PostgreSQL, unless they are changed in `.env`;

Docker Desktop includes Docker Compose. On Linux, install Docker Engine and the Compose plugin by following the [official installation guide](https://docs.docker.com/engine/install/). Verify the installation with:

```bash
docker --version
docker compose version
```

Python, PostgreSQL, Grafana, and the Python packages do not need to be installed on the host for the normal containerized setup. Docker builds the collector image and installs the packages from `requirements.txt` automatically.

For optional local Python development, use Python 3.11 or later and install the packages in a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The Python dependencies are SQLAlchemy, the PostgreSQL driver `psycopg2`, and Requests. Running the collector directly also requires a reachable PostgreSQL instance and a valid `DATABASE_URL` environment variable.

## Start up

1. Clone the repository and enter its directory:

   ```bash
   git clone https://github.com/ChriBonsi/BigDataProject.git
   cd BigDataProject
   ```

2. Create the local environment file:

   ```bash
   cp .env.example .env
   ```

   Review `.env` before starting. In particular, set secure PostgreSQL and Grafana passwords and change `API_BASE_URL` if a different upstream API must be used.

3. Build and start the complete stack:

   ```bash
   docker compose up --build -d
   ```

4. Check that the services are running:

   ```bash
   docker compose ps
   ```

Grafana is available at [http://localhost:3000](http://localhost:3000). The default development credentials are `admin` / `change_me`.
Change `GRAFANA_ADMIN_PASSWORD` in `.env` before using the project outside a local development environment.

On the first startup, the stack:

1. creates or upgrades the PostgreSQL schema;
2. inserts 40 demo conversations only when the database is empty and `SEED_DEMO_DATA=true`;
3. immediately runs an API synchronization;
4. repeats the synchronization every 30 minutes through Ofelia.

The **Conversation Correlation Analytics** dashboard and PostgreSQL data source are loaded automatically, so Grafana requires no manual setup.

To stop the services without deleting the persisted PostgreSQL and Grafana data, run:

```bash
docker compose down
```

## Upstream API contract

The collector reads conversation metadata from:

```text
GET /api/v1/conversations?limit=50&offset=0
```

For each new or changed conversation, it reads token metrics from the dedicated endpoint:

```text
GET /api/v1/conversation/{conversation_id}/token-usage
```

The `updated_at` value returned by the list endpoint is stored alongside the token-usage snapshot. The dedicated endpoint is called again only when that value changes. A conversation without `updated_at` is fetched once; subsequent runs reuse its stored snapshot because the API provides no version marker to compare it.

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
