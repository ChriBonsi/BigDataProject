"""Initialize the schema and optionally add deterministic demo conversations."""

from __future__ import annotations

import logging
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from database import create_db_engine, initialize_database, save_conversation


LOGGER = logging.getLogger("database_seed")
MODEL_PRICING_PER_MILLION = {
    "gpt-4.1-2025-04-14": (2.0, 8.0),
    "gpt-5.2-2025-12-11": (1.75, 14.0),
    "gpt-4o-mini": (0.15, 0.60),
}


def _enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def generate_fake_data(
    num_sessions: int = 40,
    *,
    random_seed: int = 42,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Create reproducible, time-distributed data with meaningful correlations."""
    rng = random.Random(random_seed)
    reference_time = now or datetime.now(timezone.utc)
    users = [f"demo_user_{index}" for index in range(1, 7)]
    call_types = (
        "question_generation",
        "report_generation",
        "topic_importance_extraction",
        "idq_c2_coherence",
    )
    models = tuple(MODEL_PRICING_PER_MILLION)
    conversations: list[dict[str, Any]] = []

    for session_number in range(1, num_sessions + 1):
        conv_id = f"demo-conv-{session_number:04d}"
        user_id = rng.choice(users)
        updated_at = reference_time - timedelta(
            days=rng.uniform(0, 29), hours=rng.uniform(0, 20)
        )
        created_at = updated_at - timedelta(minutes=rng.randint(5, 240))
        status = rng.choices(
            ("completed", "topic_exploration", "awaiting_confirmation"),
            weights=(6, 2, 2),
            k=1,
        )[0]
        num_calls = rng.randint(3, 16)
        total_input = total_output = total_duration = 0
        total_cost = 0.0
        models_used: set[str] = set()
        calls: list[dict[str, Any]] = []

        for call_number in range(1, num_calls + 1):
            model = rng.choices(models, weights=(4, 3, 2), k=1)[0]
            call_type = rng.choice(call_types)
            input_tokens = rng.randint(450, 5_500)
            output_tokens = max(
                20, int(input_tokens * rng.uniform(0.08, 0.38) + rng.randint(0, 250))
            )
            model_latency = 1.35 if model.startswith("gpt-5") else 1.0
            duration_ms = int(
                (500 + (input_tokens + output_tokens) * rng.uniform(0.28, 0.75))
                * model_latency
            )
            input_rate, output_rate = MODEL_PRICING_PER_MILLION[model]
            cost_usd = (
                input_tokens * input_rate + output_tokens * output_rate
            ) / 1_000_000
            called_at = updated_at - timedelta(
                minutes=(num_calls - call_number) * rng.randint(1, 8)
            )

            calls.append(
                {
                    "id": f"demo-call-{session_number:04d}-{call_number:03d}",
                    "call_type": call_type,
                    "llm_model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": cost_usd,
                    "duration_ms": duration_ms,
                    "called_at": called_at.isoformat(),
                }
            )
            models_used.add(model)
            total_input += input_tokens
            total_output += output_tokens
            total_cost += cost_usd
            total_duration += duration_ms

        conversations.append(
            {
                "conversation_id": conv_id,
                "user_id": user_id,
                "status": status,
                "created_at": created_at.isoformat(),
                "updated_at": updated_at.isoformat(),
                "llm_statistics": {
                    "total_calls": num_calls,
                    "total_input_tokens": total_input,
                    "total_output_tokens": total_output,
                    "total_duration_ms": total_duration,
                    "models_used": sorted(models_used),
                    "total_cost_usd": total_cost,
                },
                "llm_calls": calls,
            }
        )

    return conversations


def run_seed() -> int:
    engine = create_db_engine()
    initialize_database(engine)

    if not _enabled("SEED_DEMO_DATA", "true"):
        LOGGER.info("Schema initialized; demo data disabled")
        engine.dispose()
        return 0

    with engine.connect() as connection:
        existing_rows = connection.execute(text("SELECT COUNT(*) FROM api_sessions")).scalar_one()

    force_seed = _enabled("FORCE_SEED_DEMO_DATA")
    if existing_rows and not force_seed:
        LOGGER.info("Schema initialized; skipped demo data because the database is not empty")
        engine.dispose()
        return 0

    count = max(int(os.getenv("DEMO_SESSION_COUNT", "40")), 1)
    conversations = generate_fake_data(count)
    try:
        for conversation in conversations:
            save_conversation(
                engine,
                conversation,
                updated_at=conversation["updated_at"],
                user_id=conversation["user_id"],
            )
    finally:
        engine.dispose()

    LOGGER.info("Inserted %s demo conversations", len(conversations))
    return len(conversations)


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_seed()
