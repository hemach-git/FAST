"""Token price table (USD per 1M tokens: input, output). Estimates only; check provider pricing."""
from __future__ import annotations

PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00), "gpt-4.1-mini": (0.40, 1.60)}


def cost(model: str | None, tokens_in: int, tokens_out: int) -> float:
    for k in sorted(PRICES, key=len, reverse=True):
        if model and model.startswith(k):
            pi, po = PRICES[k]
            return round((tokens_in * pi + tokens_out * po) / 1_000_000, 6)
    return 0.0
