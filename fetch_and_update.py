"""Synchronize conversation and LLM usage data from the upstream API."""

from __future__ import annotations

import logging
import os
from typing import Any, Iterator
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from urllib3.util.retry import Retry

from database import (
    create_db_engine,
    initialize_database,
    parse_optional_datetime,
    save_conversation,
)


LOGGER = logging.getLogger("conversation_sync")
API_BASE_URL = os.getenv(
    "API_BASE_URL", "http://34.241.168.124:8000/api/v1"
).rstrip("/")
PAGE_SIZE = max(int(os.getenv("SYNC_PAGE_SIZE", "50")), 1)
MAX_PAGES = max(int(os.getenv("SYNC_MAX_PAGES", "100")), 1)
REQUEST_TIMEOUT_SECONDS = max(float(os.getenv("REQUEST_TIMEOUT_SECONDS", "15")), 1.0)


def build_http_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _get_json(session: requests.Session, url: str, **kwargs: Any) -> dict[str, Any]:
    response = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object from {url}")
    return payload


def iter_conversations(session: requests.Session) -> Iterator[dict[str, Any]]:
    """Read every available page instead of stopping at the first 50 records."""
    offset = 0
    seen_ids: set[str] = set()
    for page_number in range(MAX_PAGES):
        payload = _get_json(
            session,
            f"{API_BASE_URL}/conversations",
            params={"limit": PAGE_SIZE, "offset": offset},
        )
        conversations = payload.get("conversations") or []
        if not isinstance(conversations, list):
            raise ValueError("The conversations field must be a list")

        new_records = 0
        for conversation in conversations:
            conv_id = conversation.get("conversation_id") or conversation.get("conv_id")
            if conv_id is not None and str(conv_id) in seen_ids:
                continue
            if conv_id is not None:
                seen_ids.add(str(conv_id))
            new_records += 1
            yield conversation

        if not conversations:
            break
        if new_records == 0:
            LOGGER.warning("Pagination returned no new conversations at offset=%s", offset)
            break
        offset += len(conversations)

        try:
            total = int(payload["total"]) if payload.get("total") is not None else None
        except (TypeError, ValueError):
            total = None
        if total is not None and offset >= total:
            break
        if payload.get("has_more") is False:
            break
        if total is None and payload.get("has_more") is not True and len(conversations) < PAGE_SIZE:
            break
    else:
        LOGGER.warning("Stopped pagination after SYNC_MAX_PAGES=%s", MAX_PAGES)


def fetch_token_usage(
    session: requests.Session, conversation_id: str
) -> dict[str, Any]:
    safe_id = quote(str(conversation_id), safe="")
    payload = _get_json(
        session, f"{API_BASE_URL}/conversation/{safe_id}/token-usage"
    )
    response_conversation_id = payload.get("conversation_id")
    if response_conversation_id and str(response_conversation_id) != str(conversation_id):
        raise ValueError(
            "Token-usage response conversation_id does not match the requested conversation"
        )
    return payload


def token_usage_needs_refresh(
    stored_snapshot: tuple[Any, Any] | None, api_updated_at: Any
) -> bool:
    """Decide whether the token-usage endpoint must be fetched for a list item."""
    if stored_snapshot is None:
        return True

    stored_updated_at, fetched_at = stored_snapshot
    if fetched_at is None:
        # Rows written by older collector versions have not been fetched from
        # the dedicated token-usage endpoint yet.
        return True

    api_timestamp = parse_optional_datetime(api_updated_at)
    if api_timestamp is None:
        # Without an upstream version marker, fetch once and reuse the snapshot.
        return False

    return parse_optional_datetime(stored_updated_at) != api_timestamp


def sync_conversations() -> tuple[int, int, int]:
    """Synchronize changed records and return (seen, updated, failed)."""
    engine = create_db_engine()
    initialize_database(engine)
    session = build_http_session()

    seen = updated = failed = 0
    try:
        for conversation in iter_conversations(session):
            seen += 1
            conv_id = conversation.get("conversation_id") or conversation.get("conv_id")
            if not conv_id:
                failed += 1
                LOGGER.warning("Skipping a conversation without conversation_id")
                continue

            api_updated_at = conversation.get("updated_at")
            with engine.connect() as connection:
                stored_snapshot = connection.execute(
                    text(
                        """
                        SELECT token_usage_updated_at, token_usage_fetched_at
                        FROM api_sessions
                        WHERE conv_id = :conv_id
                        """
                    ),
                    {"conv_id": str(conv_id)},
                ).fetchone()

            try:
                needs_refresh = token_usage_needs_refresh(
                    tuple(stored_snapshot) if stored_snapshot is not None else None,
                    api_updated_at,
                )
            except (TypeError, ValueError) as exc:
                failed += 1
                LOGGER.error("Invalid updated_at for %s: %s", conv_id, exc)
                continue

            if not needs_refresh:
                continue

            try:
                token_usage = fetch_token_usage(session, str(conv_id))
                token_usage.setdefault("conversation_id", str(conv_id))
                token_usage["status"] = conversation.get("status")
                token_usage["created_at"] = conversation.get("created_at")
                if api_updated_at is not None:
                    token_usage["updated_at"] = api_updated_at
                if save_conversation(
                    engine,
                    token_usage,
                    updated_at=api_updated_at,
                    user_id=conversation.get("user_id"),
                ):
                    updated += 1
            except (requests.RequestException, SQLAlchemyError, ValueError, TypeError) as exc:
                failed += 1
                LOGGER.error("Failed to synchronize %s: %s", conv_id, exc)
    finally:
        session.close()
        engine.dispose()

    LOGGER.info("Sync complete: seen=%s updated=%s failed=%s", seen, updated, failed)
    return seen, updated, failed


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        sync_conversations()
    except (requests.RequestException, SQLAlchemyError, ValueError, RuntimeError) as exc:
        LOGGER.error("Synchronization aborted: %s", exc)
        raise SystemExit(1) from exc
