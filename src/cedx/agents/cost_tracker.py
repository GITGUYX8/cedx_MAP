"""Cost calculation per LLM call — adapted from KiwiQ's _calculate_actual_cost().

Computes actual cost based on token usage and model pricing metadata.
Does NOT depend on the `tokencost` library (unlike KiwiQ).
"""
from __future__ import annotations

from typing import Optional

from cedx.agents.model_metadata import ModelSpec, ALL_MODELS


def calculate_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> float:
    """Calculate cost of an LLM call using model pricing metadata.

    Args:
        model_name: The model identifier (e.g. 'gpt-4o-mini').
        input_tokens: Number of input (prompt) tokens.
        output_tokens: Number of output (completion) tokens.
        cached_input_tokens: Number of cached input tokens (Anthropic cache read).

    Returns:
        Cost in USD.

    Adapted from KiwiQ's _calculate_actual_cost() at
    supp_repo/kiwiq/services/workflow_service/registry/nodes/llm/llm_node.py:3232.
    """
    spec = ALL_MODELS.get(model_name)
    if spec is None:
        return 0.0  # unknown model -> assume free

    p = spec.pricing
    input_cost = (input_tokens / 1_000_000) * p.input_per_1m
    output_cost = (output_tokens / 1_000_000) * p.output_per_1m
    cached_cost = (cached_input_tokens / 1_000_000) * p.cached_input_per_1m

    return round(input_cost + output_cost + cached_cost, 8)


def calculate_estimated_cost(
    model_name: str,
    estimated_input_tokens: int = 500,
    estimated_output_tokens: int = 500,
) -> float:
    """Estimate cost before making a call (for budget decisions).

    KiwiQ uses two-pass cost calculation:
      1. Estimated (pre-call, for budget checking)
      2. Actual (post-call, for billing)

    This is the estimation pass.
    """
    return calculate_cost(model_name, estimated_input_tokens, estimated_output_tokens)


def format_cost_usd(cost: float) -> str:
    """Format cost for display."""
    return f"${cost:.6f}"
