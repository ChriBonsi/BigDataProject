"""Database schema and persistence helpers for conversation analytics."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import Engine, create_engine, text

from pricing import estimate_call_cost


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS api_sessions (
        conv_id VARCHAR(255) PRIMARY KEY,
        user_id VARCHAR(255),
        status VARCHAR(100),
        created_at TIMESTAMP,
        total_calls BIGINT NOT NULL DEFAULT 0,
        total_input_tokens BIGINT NOT NULL DEFAULT 0,
        total_output_tokens BIGINT NOT NULL DEFAULT 0,
        total_duration_ms BIGINT NOT NULL DEFAULT 0,
        models_used TEXT NOT NULL DEFAULT '',
        total_cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
        updated_at TIMESTAMP,
        token_usage_updated_at TIMESTAMPTZ,
        token_usage_fetched_at TIMESTAMPTZ,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        synced_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS token_usage_history (
        id BIGSERIAL PRIMARY KEY,
        conv_id VARCHAR(255) NOT NULL,
        timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        incremental_input_tokens BIGINT NOT NULL DEFAULT 0,
        incremental_output_tokens BIGINT NOT NULL DEFAULT 0,
        source_updated_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_calls (
        call_id VARCHAR(255) PRIMARY KEY,
        conv_id VARCHAR(255) NOT NULL,
        call_type VARCHAR(255),
        model VARCHAR(255),
        input_tokens BIGINT NOT NULL DEFAULT 0,
        output_tokens BIGINT NOT NULL DEFAULT 0,
        cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
        duration_ms BIGINT,
        called_at TIMESTAMP
    )
    """,
    "ALTER TABLE api_sessions ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP",
    "ALTER TABLE api_sessions ADD COLUMN IF NOT EXISTS synced_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP",
    "ALTER TABLE api_sessions ADD COLUMN IF NOT EXISTS status VARCHAR(100)",
    "ALTER TABLE api_sessions ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
    "ALTER TABLE api_sessions ADD COLUMN IF NOT EXISTS token_usage_updated_at TIMESTAMPTZ",
    "ALTER TABLE api_sessions ADD COLUMN IF NOT EXISTS token_usage_fetched_at TIMESTAMPTZ",
    "ALTER TABLE token_usage_history ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ",
    "ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS duration_ms BIGINT",
    # Older versions used an integer call id. Text also supports UUID-like ids.
    "ALTER TABLE llm_calls ALTER COLUMN call_id TYPE VARCHAR(255) USING call_id::VARCHAR",
    "CREATE INDEX IF NOT EXISTS idx_api_sessions_updated_at ON api_sessions (updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_api_sessions_user_id ON api_sessions (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_api_sessions_status ON api_sessions (status)",
    "CREATE INDEX IF NOT EXISTS idx_token_history_timestamp ON token_usage_history (timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_token_history_conv_id ON token_usage_history (conv_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_token_history_source_snapshot ON token_usage_history (conv_id, source_updated_at) WHERE source_updated_at IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_llm_calls_conv_id ON llm_calls (conv_id)",
    "CREATE INDEX IF NOT EXISTS idx_llm_calls_called_at ON llm_calls (called_at)",
    "CREATE INDEX IF NOT EXISTS idx_llm_calls_model ON llm_calls (model)",
    """
    CREATE OR REPLACE VIEW conversation_metrics AS
    SELECT
        conv_id,
        user_id,
        total_calls,
        total_input_tokens,
        total_output_tokens,
        total_input_tokens + total_output_tokens AS total_tokens,
        total_duration_ms,
        models_used,
        total_cost_usd,
        updated_at,
        first_seen_at,
        synced_at,
        total_output_tokens::DOUBLE PRECISION
            / NULLIF(total_input_tokens, 0) AS output_input_ratio,
        (total_input_tokens + total_output_tokens)::DOUBLE PRECISION
            / NULLIF(total_calls, 0) AS tokens_per_call,
        total_duration_ms::DOUBLE PRECISION
            / NULLIF(total_calls, 0) AS avg_duration_ms_per_call,
        total_cost_usd::DOUBLE PRECISION * 1000
            / NULLIF(total_input_tokens + total_output_tokens, 0) AS cost_per_1k_tokens,
        status,
        created_at,
        token_usage_updated_at,
        token_usage_fetched_at
    FROM api_sessions
    """,
    """
    CREATE OR REPLACE VIEW model_call_metrics AS
    SELECT
        COALESCE(model, 'unknown') AS model,
        COUNT(*) AS calls,
        COUNT(DISTINCT conv_id) AS conversations,
        SUM(input_tokens) AS input_tokens,
        SUM(output_tokens) AS output_tokens,
        SUM(input_tokens + output_tokens) AS total_tokens,
        SUM(cost_usd) AS total_cost_usd,
        AVG(duration_ms) AS avg_duration_ms
    FROM llm_calls
    GROUP BY COALESCE(model, 'unknown')
    """,
)


def create_db_engine(database_url: str | None = None) -> Engine:
    """Create the SQLAlchemy engine, failing with an actionable message."""
    url = database_url or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    return create_engine(url, pool_pre_ping=True)


def initialize_database(engine: Engine) -> None:
    """Create or migrate analytics tables, indexes and reusable SQL views."""
    with engine.begin() as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(text(statement))


def parse_datetime(value: Any) -> datetime:
    """Normalize API timestamps to timezone-aware UTC datetimes."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        return datetime.now(timezone.utc)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_optional_datetime(value: Any) -> datetime | None:
    """Normalize a timestamp while preserving a missing source value as None."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return parse_datetime(value)


def token_increment(current: int, previous: int | None) -> int:
    """Return a non-negative delta, accounting for an upstream counter reset."""
    current = max(int(current or 0), 0)
    if previous is None:
        return current
    previous = max(int(previous or 0), 0)
    return current - previous if current >= previous else current


def _as_non_negative_int(value: Any) -> int:
    """Convert a value to a non-negative integer."""
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _as_non_negative_float(value: Any) -> float:
    """Convert a value to a non-negative float."""
    try:
        return max(float(value or 0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _call_cost(call: Mapping[str, Any]) -> float:
    """Return the reported or estimated cost of a call."""
    if call.get("cost_usd") is not None:
        return _as_non_negative_float(call.get("cost_usd"))
    return estimate_call_cost(
        call.get("llm_model") or call.get("model"),
        _as_non_negative_int(call.get("input_tokens")),
        _as_non_negative_int(call.get("output_tokens")),
    )


def save_conversation(
    engine: Engine,
    data: Mapping[str, Any],
    updated_at: Any = None,
    user_id: str | None = None,
) -> bool:
    """Upsert one conversation, its calls and a de-duplicated usage delta."""
    conv_id = data.get("conversation_id") or data.get("conv_id")
    if not conv_id:
        return False

    conv_id = str(conv_id)
    stats = data.get("llm_statistics") or data.get("statistics") or {}
    if not isinstance(stats, Mapping):
        stats = {}
    calls = data.get("llm_calls") or []
    if not isinstance(calls, list):
        calls = []
    calls = [call for call in calls if isinstance(call, Mapping)]
    models = stats.get("models_used") or []
    if isinstance(models, str):
        models = [models]

    if not models:
        models = [
            call.get("llm_model") or call.get("model") or "unknown" for call in calls
        ]

    input_tokens = (
        _as_non_negative_int(stats.get("total_input_tokens"))
        if stats.get("total_input_tokens") is not None
        else sum(_as_non_negative_int(call.get("input_tokens")) for call in calls)
    )
    output_tokens = (
        _as_non_negative_int(stats.get("total_output_tokens"))
        if stats.get("total_output_tokens") is not None
        else sum(_as_non_negative_int(call.get("output_tokens")) for call in calls)
    )
    total_calls = (
        _as_non_negative_int(stats.get("total_calls"))
        if stats.get("total_calls") is not None
        else len(calls)
    )
    duration_ms = (
        _as_non_negative_int(stats.get("total_duration_ms"))
        if stats.get("total_duration_ms") is not None
        else sum(
            _as_non_negative_int(
                call.get("duration_ms") or call.get("total_duration_ms")
            )
            for call in calls
        )
    )
    total_cost = (
        _as_non_negative_float(stats.get("total_cost_usd"))
        if stats.get("total_cost_usd") is not None
        else sum(_call_cost(call) for call in calls)
    )
    source_updated_at = parse_datetime(
        updated_at or data.get("updated_at") or data.get("created_at")
    )
    token_usage_updated_at = parse_optional_datetime(
        updated_at or data.get("updated_at")
    )
    created_at = parse_optional_datetime(data.get("created_at"))
    status = data.get("status")
    if status is not None:
        status = str(status)
    effective_user_id = str(user_id or data.get("user_id") or "unknown")

    with engine.begin() as connection:
        previous = connection.execute(
            text(
                """
                SELECT total_input_tokens, total_output_tokens
                FROM api_sessions
                WHERE conv_id = :conv_id
                FOR UPDATE
                """
            ),
            {"conv_id": conv_id},
        ).fetchone()

        previous_input = previous[0] if previous else None
        previous_output = previous[1] if previous else None
        input_delta = token_increment(input_tokens, previous_input)
        output_delta = token_increment(output_tokens, previous_output)

        connection.execute(
            text(
                """
                INSERT INTO api_sessions (
                    conv_id, user_id, status, created_at,
                    total_calls, total_input_tokens,
                    total_output_tokens, total_duration_ms, models_used,
                    total_cost_usd, updated_at, token_usage_updated_at,
                    token_usage_fetched_at, synced_at
                ) VALUES (
                    :conv_id, :user_id, :status, :created_at,
                    :total_calls, :input_tokens,
                    :output_tokens, :duration_ms, :models_used,
                    :total_cost, :updated_at, :token_usage_updated_at,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (conv_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    status = COALESCE(EXCLUDED.status, api_sessions.status),
                    created_at = COALESCE(api_sessions.created_at, EXCLUDED.created_at),
                    total_calls = EXCLUDED.total_calls,
                    total_input_tokens = EXCLUDED.total_input_tokens,
                    total_output_tokens = EXCLUDED.total_output_tokens,
                    total_duration_ms = EXCLUDED.total_duration_ms,
                    models_used = EXCLUDED.models_used,
                    total_cost_usd = EXCLUDED.total_cost_usd,
                    updated_at = EXCLUDED.updated_at,
                    token_usage_updated_at = EXCLUDED.token_usage_updated_at,
                    token_usage_fetched_at = CURRENT_TIMESTAMP,
                    synced_at = CURRENT_TIMESTAMP
                """
            ),
            {
                "conv_id": conv_id,
                "user_id": effective_user_id,
                "status": status,
                "created_at": created_at,
                "total_calls": total_calls,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "duration_ms": duration_ms,
                "models_used": ",".join(sorted({str(model) for model in models})),
                "total_cost": total_cost,
                "updated_at": source_updated_at,
                "token_usage_updated_at": token_usage_updated_at,
            },
        )

        if input_delta or output_delta:
            connection.execute(
                text(
                    """
                    INSERT INTO token_usage_history (
                        conv_id, timestamp, incremental_input_tokens,
                        incremental_output_tokens, source_updated_at
                    ) VALUES (
                        :conv_id, :timestamp, :input_delta,
                        :output_delta, :source_updated_at
                    )
                    ON CONFLICT (conv_id, source_updated_at)
                    WHERE source_updated_at IS NOT NULL DO NOTHING
                    """
                ),
                {
                    "conv_id": conv_id,
                    "timestamp": source_updated_at,
                    "input_delta": input_delta,
                    "output_delta": output_delta,
                    "source_updated_at": source_updated_at,
                },
            )

        for position, call in enumerate(calls, start=1):
            raw_call_id = call.get("id") or call.get("call_id")
            call_id = str(raw_call_id or f"{conv_id}:{position}")
            call_duration = call.get("duration_ms")
            if call_duration is None:
                call_duration = call.get("total_duration_ms")

            connection.execute(
                text(
                    """
                    INSERT INTO llm_calls (
                        call_id, conv_id, call_type, model, input_tokens,
                        output_tokens, cost_usd, duration_ms, called_at
                    ) VALUES (
                        :call_id, :conv_id, :call_type, :model, :input_tokens,
                        :output_tokens, :cost_usd, :duration_ms, :called_at
                    )
                    ON CONFLICT (call_id) DO UPDATE SET
                        conv_id = EXCLUDED.conv_id,
                        call_type = EXCLUDED.call_type,
                        model = EXCLUDED.model,
                        input_tokens = EXCLUDED.input_tokens,
                        output_tokens = EXCLUDED.output_tokens,
                        cost_usd = EXCLUDED.cost_usd,
                        duration_ms = EXCLUDED.duration_ms,
                        called_at = EXCLUDED.called_at
                    """
                ),
                {
                    "call_id": call_id,
                    "conv_id": conv_id,
                    "call_type": call.get("call_type") or "unknown",
                    "model": call.get("llm_model") or call.get("model") or "unknown",
                    "input_tokens": _as_non_negative_int(call.get("input_tokens")),
                    "output_tokens": _as_non_negative_int(call.get("output_tokens")),
                    "cost_usd": _call_cost(call),
                    "duration_ms": (
                        _as_non_negative_int(call_duration)
                        if call_duration is not None
                        else None
                    ),
                    "called_at": parse_datetime(
                        call.get("called_at") or source_updated_at
                    ),
                },
            )

    return True
