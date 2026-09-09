"""LLM cost tracking utilities.

Provides:
- ``MODEL_PRICING`` — per-1M-token prices for each supported Claude model.
- ``compute_cost()`` — pure function that converts token counts to USD.
- ``CostTracker``   — async Redis-backed tracker for daily per-tenant spend.

All Redis keys use a ``spend:{tenant_id}:{YYYY-MM-DD}`` pattern with a
48-hour TTL so old data is automatically garbage-collected.

Usage::

    from app.observability.cost import CostTracker, compute_cost
    import redis.asyncio as aioredis

    redis_client = aioredis.from_url("redis://localhost:6379/0")
    tracker = CostTracker(redis_client)

    cost = await tracker.record_spend(
        tenant_id="acme",
        model="claude-sonnet-4-6",
        input_tokens=1500,
        output_tokens=300,
    )
    print(f"Today's total: ${cost:.4f}")
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Pricing table  (USD per 1 million tokens)
# ---------------------------------------------------------------------------

MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input_per_1m": 3.0,
        "output_per_1m": 15.0,
    },
    "claude-haiku-4-5": {
        "input_per_1m": 0.80,
        "output_per_1m": 4.0,
    },
    "claude-opus-4-8": {
        "input_per_1m": 15.0,
        "output_per_1m": 75.0,
    },
}

_FALLBACK_MODEL = "claude-sonnet-4-6"

_TTL_SECONDS = 48 * 3600  # 48 hours


# ---------------------------------------------------------------------------
# Pure helper
# ---------------------------------------------------------------------------


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return the USD cost of a single LLM call.

    Args:
        model:         Model identifier string.  Falls back to Sonnet pricing
                       if *model* is not found in :data:`MODEL_PRICING`.
        input_tokens:  Number of input (prompt) tokens consumed.
        output_tokens: Number of output (completion) tokens produced.

    Returns:
        Cost in US dollars as a float.
    """
    pricing = MODEL_PRICING.get(model, MODEL_PRICING[_FALLBACK_MODEL])
    input_cost = (input_tokens / 1_000_000) * pricing["input_per_1m"]
    output_cost = (output_tokens / 1_000_000) * pricing["output_per_1m"]
    return input_cost + output_cost


# ---------------------------------------------------------------------------
# Redis-backed tracker
# ---------------------------------------------------------------------------


class CostTracker:
    """Per-tenant daily spend tracker backed by Redis.

    The tracker stores each day's cumulative spend as a float string in a
    Redis key.  All keys expire after 48 hours to keep memory usage bounded.

    Args:
        redis_client: An async Redis client (e.g. ``redis.asyncio.Redis``).
    """

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _key(tenant_id: str, day: date | None = None) -> str:
        d = day or date.today()
        return f"spend:{tenant_id}:{d.isoformat()}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def record_spend(
        self,
        tenant_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Record a single LLM call's cost and return today's running total.

        Atomically increments the Redis counter for today's date.  The key is
        created with a 48-hour TTL on first write.

        Args:
            tenant_id:     Opaque tenant / workspace identifier.
            model:         LLM model string.
            input_tokens:  Input token count for this call.
            output_tokens: Output token count for this call.

        Returns:
            Today's cumulative spend in USD (including this call).
        """
        cost = compute_cost(model, input_tokens, output_tokens)
        key = self._key(tenant_id)

        # INCRBYFLOAT is atomic; create the key + set TTL in a pipeline.
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incrbyfloat(key, cost)
            pipe.expire(key, _TTL_SECONDS)
            results = await pipe.execute()

        total: float = float(results[0])
        return total

    async def get_today_spend(self, tenant_id: str) -> float:
        """Return today's cumulative spend for *tenant_id* in USD.

        Returns ``0.0`` if no spend has been recorded today.
        """
        key = self._key(tenant_id)
        value = await self._redis.get(key)
        if value is None:
            return 0.0
        return float(value)

    async def get_spend_history(
        self, tenant_id: str, days: int = 30
    ) -> list[dict[str, Any]]:
        """Return daily spend for the last *days* calendar days.

        Reads keys directly (one ``GET`` per day) rather than using SCAN so
        that the result order is deterministic and the operation is O(N) in
        *days* rather than in the total number of Redis keys.

        Args:
            tenant_id: Opaque tenant identifier.
            days:      How many calendar days to look back (inclusive of today).

        Returns:
            List of ``{"date": "YYYY-MM-DD", "spend_usd": float}`` dicts,
            ordered oldest-first.
        """
        today = date.today()
        dates = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]

        keys = [self._key(tenant_id, d) for d in dates]

        # Batch GET with pipeline for efficiency.
        async with self._redis.pipeline(transaction=False) as pipe:
            for key in keys:
                pipe.get(key)
            raw_values = await pipe.execute()

        history: list[dict[str, Any]] = []
        for d, raw in zip(dates, raw_values):
            spend = float(raw) if raw is not None else 0.0
            history.append({"date": d.isoformat(), "spend_usd": spend})

        return history

    async def check_spend_cap(self, tenant_id: str, cap_usd: float) -> bool:
        """Return ``True`` if today's spend is strictly below *cap_usd*.

        Args:
            tenant_id: Opaque tenant identifier.
            cap_usd:   Daily spend ceiling in US dollars.

        Returns:
            ``True``  — spend is within the cap (request may proceed).
            ``False`` — spend has met or exceeded the cap (request should be
                        blocked with HTTP 402).
        """
        today_spend = await self.get_today_spend(tenant_id)
        return today_spend < cap_usd
