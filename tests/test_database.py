import unittest
from contextlib import contextmanager
from datetime import datetime, timezone

from database import (
    parse_datetime,
    parse_optional_datetime,
    save_conversation,
    token_increment,
)
from pricing import estimate_call_cost, model_rates
from seed_db import generate_fake_data


def pearson(left, right):
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right)
    )
    left_variance = sum((value - left_mean) ** 2 for value in left)
    right_variance = sum((value - right_mean) ** 2 for value in right)
    return numerator / (left_variance * right_variance) ** 0.5


class FakeResult:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, previous=None):
        self.previous = previous
        self.statements = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if "SELECT total_input_tokens" in sql:
            return FakeResult(self.previous)
        self.statements.append((sql, parameters or {}))
        return FakeResult()


class FakeEngine:
    def __init__(self, previous=None):
        self.connection = FakeConnection(previous)

    @contextmanager
    def begin(self):
        yield self.connection


class DatabaseHelpersTest(unittest.TestCase):
    def test_parse_datetime_normalizes_to_utc(self):
        parsed = parse_datetime("2026-04-14T10:00:00+02:00")
        self.assertEqual(parsed, datetime(2026, 4, 14, 8, 0, tzinfo=timezone.utc))
        self.assertIsNone(parse_optional_datetime(None))
        self.assertIsNone(parse_optional_datetime(""))

    def test_token_increment_handles_first_snapshot_growth_and_reset(self):
        self.assertEqual(token_increment(100, None), 100)
        self.assertEqual(token_increment(140, 100), 40)
        self.assertEqual(token_increment(20, 140), 20)
        self.assertEqual(token_increment(-10, 5), 0)

    def test_pricing_catalog_matches_dated_model_names(self):
        self.assertEqual(model_rates("gpt-4.1-2025-04-14"), (2.0, 8.0))
        self.assertAlmostEqual(
            estimate_call_cost("gpt-4.1-2025-04-14", 1_000, 100),
            0.0028,
        )

    def test_demo_data_is_reproducible_and_correlated(self):
        now = datetime(2026, 4, 14, tzinfo=timezone.utc)
        first = generate_fake_data(40, now=now)
        second = generate_fake_data(40, now=now)
        self.assertEqual(first, second)
        self.assertEqual(len({item["conversation_id"] for item in first}), 40)

        token_totals = [
            item["llm_statistics"]["total_input_tokens"]
            + item["llm_statistics"]["total_output_tokens"]
            for item in first
        ]
        costs = [item["llm_statistics"]["total_cost_usd"] for item in first]
        self.assertGreater(pearson(token_totals, costs), 0.70)

        for item in first:
            stats = item["llm_statistics"]
            self.assertEqual(stats["total_calls"], len(item["llm_calls"]))
            self.assertTrue(all("duration_ms" in call for call in item["llm_calls"]))

    def test_save_conversation_keeps_call_and_conversation_ids_distinct(self):
        engine = FakeEngine(previous=(80, 20))
        saved = save_conversation(
            engine,
            {
                "conversation_id": "conv-123",
                "status": "completed",
                "created_at": "2026-04-14T09:00:00Z",
                "updated_at": "2026-04-14T10:00:00Z",
                "llm_statistics": {
                    "total_calls": 1,
                    "total_input_tokens": 100,
                    "total_output_tokens": 25,
                    "total_duration_ms": 900,
                    "models_used": ["gpt-test"],
                    "total_cost_usd": 0.01,
                },
                "llm_calls": [
                    {
                        "id": 987,
                        "llm_model": "gpt-test",
                        "input_tokens": 100,
                        "output_tokens": 25,
                    }
                ],
            },
        )

        self.assertTrue(saved)
        call_parameters = next(
            parameters
            for sql, parameters in engine.connection.statements
            if "INSERT INTO llm_calls" in sql
        )
        self.assertEqual(call_parameters["call_id"], "987")
        self.assertEqual(call_parameters["conv_id"], "conv-123")

        session_parameters = next(
            parameters
            for sql, parameters in engine.connection.statements
            if "INSERT INTO api_sessions" in sql
        )
        self.assertEqual(session_parameters["status"], "completed")
        self.assertEqual(
            session_parameters["token_usage_updated_at"],
            datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc),
        )

        history_parameters = next(
            parameters
            for sql, parameters in engine.connection.statements
            if "INSERT INTO token_usage_history" in sql
        )
        self.assertEqual(history_parameters["input_delta"], 20)
        self.assertEqual(history_parameters["output_delta"], 5)


if __name__ == "__main__":
    unittest.main()
