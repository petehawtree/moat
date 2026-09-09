"""Pricing snapshot and cost helpers for W3 (Sprint 3).

Prices change; --dry-run reports the snapshot date so a stale constant is visible.
Batch API is 50% off every token.
"""
from __future__ import annotations

# Snapshot date: update whenever prices are re-verified against console.anthropic.com/settings/billing
PRICING_SNAPSHOT_DATE = "2026-09-04"

# Per-model price table (USD per million tokens)
_PRICES: dict[str, dict[str, float]] = {
    "claude-opus-4-8": {
        "input":        15.00,
        "output":       75.00,
        "cache_read":    1.50,   # 0.1× input
        "cache_write":  18.75,   # 1.25× input
    },
    "claude-sonnet-4-6": {
        "input":         3.00,
        "output":       15.00,
        "cache_read":    0.30,
        "cache_write":   3.75,
    },
    "claude-haiku-4-5-20251001": {
        "input":         0.80,
        "output":        4.00,
        "cache_read":    0.08,
        "cache_write":   1.00,
    },
}

BATCH_DISCOUNT = 0.50   # Batch API is 50% off all token types
PILOT_CAP_USD = 15.00
PRODUCTION_CAP_USD = 35.00

DEFAULT_MODEL = "claude-sonnet-4-6"


def get_prices(model_id: str) -> dict[str, float]:
    if model_id not in _PRICES:
        raise ValueError(
            f"No pricing data for model '{model_id}'. "
            f"Known models: {list(_PRICES)}"
        )
    return _PRICES[model_id]


def estimate_cost(usage: dict, model_id: str, is_batch: bool = False) -> float:
    """Estimate USD cost from an API usage dict.

    usage keys: input_tokens, output_tokens,
                cache_creation_input_tokens, cache_read_input_tokens
    """
    prices = get_prices(model_id)
    discount = BATCH_DISCOUNT if is_batch else 1.0

    input_tok  = usage.get("input_tokens", 0)
    output_tok = usage.get("output_tokens", 0)
    cache_write = usage.get("cache_creation_input_tokens", 0)
    cache_read  = usage.get("cache_read_input_tokens", 0)

    cost = (
        input_tok   * prices["input"]        / 1_000_000
        + output_tok  * prices["output"]       / 1_000_000
        + cache_write * prices["cache_write"]  / 1_000_000
        + cache_read  * prices["cache_read"]   / 1_000_000
    ) * discount
    return cost


# Assumed output tokens (4 analyses × ~2k; thinking not included) for
# projecting cost from a free token count alone, before any generation call.
ASSUMED_OUTPUT_TOKENS = 8_000


def estimate_projected_cost(
    input_tokens: int,
    model_id: str,
    is_batch: bool,
    assumed_output: int = ASSUMED_OUTPUT_TOKENS,
) -> float:
    """Project cost from an input token count alone (no generation call made).

    Used by --dry-run's report and, since Sprint 3.1, by the batch path's
    pre-submission cap check — submit_batch() has no way to know true cost
    until after the batch resolves, so the cap has to be enforced against a
    projection before submitting, not the actual cost after the fact.
    """
    prices = get_prices(model_id)
    discount = BATCH_DISCOUNT if is_batch else 1.0
    return (
        input_tokens    * prices["input"]  / 1_000_000
        + assumed_output * prices["output"] / 1_000_000
    ) * discount


def format_dry_run_report(
    ticker: str,
    model_id: str,
    input_tokens: int,
    is_batch: bool,
) -> str:
    """Human-readable --dry-run summary including pricing snapshot and caps."""
    prices = get_prices(model_id)
    discount = BATCH_DISCOUNT if is_batch else 1.0
    mode = "batch" if is_batch else "sync"

    assumed_output = ASSUMED_OUTPUT_TOKENS
    estimated_cost = estimate_projected_cost(input_tokens, model_id, is_batch, assumed_output)

    lines = [
        f"DRY RUN — {ticker}",
        f"  model           : {model_id}",
        f"  mode            : {mode}",
        f"  input tokens    : {input_tokens:,}",
        f"  assumed output  : ~{assumed_output:,}  (4 analyses × ~2k; thinking not included)",
        f"  estimated cost  : ${estimated_cost:.4f}",
        f"  pricing snapshot: {PRICING_SNAPSHOT_DATE}",
        f"    input  ${prices['input']:.2f}/MTok × {discount:.0%} → ${prices['input']*discount:.3f}",
        f"    output ${prices['output']:.2f}/MTok × {discount:.0%} → ${prices['output']*discount:.3f}",
        f"  pilot cap       : ${PILOT_CAP_USD:.2f}",
        f"  production cap  : ${PRODUCTION_CAP_USD:.2f}",
    ]
    return "\n".join(lines)
