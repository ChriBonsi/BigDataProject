"""Fallback cost estimation based on the repository model pricing catalog."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_PRICING_FILE = Path(__file__).with_name("model_pricing.json")


def _catalog_entries(payload: dict[str, Any]):
    """Yield model entries from every pricing category."""
    models = payload.get("models") or {}
    yield from models.get("standard_tier") or []
    specialized = models.get("specialized_and_tools") or {}
    yield from specialized.get("deep_research") or []


@lru_cache(maxsize=1)
def load_model_rates() -> dict[str, tuple[float, float]]:
    """Load input/output USD rates per million tokens."""
    path = Path(os.getenv("MODEL_PRICING_FILE", DEFAULT_PRICING_FILE))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}

    rates: dict[str, tuple[float, float]] = {}
    for entry in _catalog_entries(payload):
        if not isinstance(entry, dict) or not entry.get("model"):
            continue
        name = str(entry["model"]).split(" (", 1)[0]
        try:
            rates[name] = (float(entry["input"]), float(entry["output"]))
        except (KeyError, TypeError, ValueError):
            continue
    return rates


def model_rates(model: str | None) -> tuple[float, float] | None:
    """Match exact model names and dated variants such as gpt-4.1-2025-04-14."""
    if not model:
        return None
    model = str(model)
    rates = load_model_rates()
    if model in rates:
        return rates[model]

    candidates = [
        name for name in rates if model.startswith(f"{name}-")
    ]
    if not candidates:
        return None
    return rates[max(candidates, key=len)]


def estimate_call_cost(
    model: str | None, input_tokens: int, output_tokens: int
) -> float:
    """Estimate USD cost, returning zero when the model is not in the catalog."""
    rates = model_rates(model)
    if rates is None:
        return 0.0
    input_rate, output_rate = rates
    return (
        max(int(input_tokens or 0), 0) * input_rate
        + max(int(output_tokens or 0), 0) * output_rate
    ) / 1_000_000
