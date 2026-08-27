import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import fetch_and_update


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return FakeResponse(next(self.pages))


class PaginationTest(unittest.TestCase):
    def test_iter_conversations_reads_until_short_page(self):
        session = FakeSession(
            [
                {"conversations": [{"conversation_id": "a"}, {"conversation_id": "b"}]},
                {"conversations": [{"conversation_id": "c"}]},
            ]
        )
        with patch.object(fetch_and_update, "PAGE_SIZE", 2), patch.object(
            fetch_and_update, "MAX_PAGES", 10
        ):
            result = list(fetch_and_update.iter_conversations(session))

        self.assertEqual([row["conversation_id"] for row in result], ["a", "b", "c"])
        self.assertEqual(session.requests[0][1]["params"], {"limit": 2, "offset": 0})
        self.assertEqual(session.requests[1][1]["params"], {"limit": 2, "offset": 2})

    def test_iter_conversations_rejects_invalid_shape(self):
        session = FakeSession([{"conversations": "not-a-list"}])
        with self.assertRaises(ValueError):
            list(fetch_and_update.iter_conversations(session))

    def test_iter_conversations_honors_total_when_server_caps_page_size(self):
        session = FakeSession(
            [
                {"total": 2, "conversations": [{"conversation_id": "a"}]},
                {"total": 2, "conversations": [{"conversation_id": "b"}]},
            ]
        )
        with patch.object(fetch_and_update, "PAGE_SIZE", 5), patch.object(
            fetch_and_update, "MAX_PAGES", 10
        ):
            result = list(fetch_and_update.iter_conversations(session))

        self.assertEqual([row["conversation_id"] for row in result], ["a", "b"])
        self.assertEqual(session.requests[1][1]["params"]["offset"], 1)


class TokenUsageContractTest(unittest.TestCase):
    def test_fetch_token_usage_uses_singular_conversation_endpoint(self):
        session = FakeSession(
            [
                {
                    "conversation_id": "conv/example",
                    "llm_statistics": {"total_calls": 0},
                    "llm_calls": [],
                }
            ]
        )

        payload = fetch_and_update.fetch_token_usage(session, "conv/example")

        self.assertEqual(payload["conversation_id"], "conv/example")
        self.assertEqual(
            session.requests[0][0],
            f"{fetch_and_update.API_BASE_URL}/conversation/conv%2Fexample/token-usage",
        )

    def test_fetch_token_usage_rejects_mismatched_conversation(self):
        session = FakeSession([{"conversation_id": "another-conversation"}])
        with self.assertRaises(ValueError):
            fetch_and_update.fetch_token_usage(session, "expected-conversation")

    def test_new_or_changed_conversations_require_token_usage(self):
        fetched_at = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        stored_updated_at = datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc)

        self.assertTrue(
            fetch_and_update.token_usage_needs_refresh(
                None, "2026-04-01T09:00:00+00:00"
            )
        )
        self.assertFalse(
            fetch_and_update.token_usage_needs_refresh(
                (stored_updated_at, fetched_at), "2026-04-01T09:00:00+00:00"
            )
        )
        self.assertTrue(
            fetch_and_update.token_usage_needs_refresh(
                (stored_updated_at, fetched_at), "2026-04-01T09:01:00+00:00"
            )
        )

    def test_missing_updated_at_is_fetched_once(self):
        fetched_at = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(fetch_and_update.token_usage_needs_refresh(None, None))
        self.assertFalse(
            fetch_and_update.token_usage_needs_refresh((None, fetched_at), None)
        )
        self.assertTrue(
            fetch_and_update.token_usage_needs_refresh((None, None), None)
        )


if __name__ == "__main__":
    unittest.main()
