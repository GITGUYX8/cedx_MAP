"""Model metadata and pricing — adapted from KiwiQ's config.py.

Contains the pricing data for all supported LLM providers and models,
used by the cost tracker and model router.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class LLMProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "google_vertexai"
    GROQ = "groq"


@dataclass
class ModelPricing:
    """Pricing per 1M tokens for a model. Adapted from KiwiQ ModelMetadata."""
    input_per_1m: float = 0.0
    output_per_1m: float = 0.0
    cached_input_per_1m: float = 0.0


@dataclass
class ModelSpec:
    """Complete model specification with pricing and metadata."""
    provider: LLMProvider
    model_name: str
    display_name: str = ""
    context_limit: int = 128000
    pricing: ModelPricing = field(default_factory=ModelPricing)
    supports_structured_output: bool = True
    is_cheap: bool = False  # True = use as default cheap model


# ── Pricing for gpt-4o-mini (default cheap model from task spec) ──────────
# From KiwiQ config.py: GPT_4o_mini = "gpt-4o-mini"
GPT_4O_MINI = ModelSpec(
    provider=LLMProvider.OPENAI,
    model_name="gpt-4o-mini",
    display_name="GPT-4o Mini",
    pricing=ModelPricing(input_per_1m=0.15, output_per_1m=0.60),
    is_cheap=True,
)

# ── Pricing for claude-3-5-haiku (task-specified cheap alternative) ────────
# From KiwiQ config.py: CLAUDE_3_5_HAIKU = "claude-3-5-haiku-latest"
CLAUDE_3_5_HAIKU = ModelSpec(
    provider=LLMProvider.ANTHROPIC,
    model_name="claude-3-5-haiku-latest",
    display_name="Claude 3.5 Haiku",
    pricing=ModelPricing(input_per_1m=0.80, output_per_1m=4.0, cached_input_per_1m=0.08),
    is_cheap=True,
)

# ── Pricing for gemini-2.5-flash (task-specified free/cheap alternative) ───
# From KiwiQ config.py: GEMINI_2_5_FLASH = "gemini-2.5-flash-preview-05-20"
GEMINI_2_5_FLASH = ModelSpec(
    provider=LLMProvider.GEMINI,
    model_name="gemini-2.5-flash-preview-05-20",
    display_name="Gemini 2.5 Flash",
    pricing=ModelPricing(input_per_1m=0.0, output_per_1m=0.0),  # free tier
    is_cheap=True,
)

# ── Groq models (free via groq.com API, OpenAI-compatible) ──────────────────
# Free tier: 30 RPM, 6000 RPD for Llama 3.1 8B. No credit card needed.
GROQ_LLAMA_8B = ModelSpec(
    provider=LLMProvider.GROQ,
    model_name="llama-3.1-8b-instant",
    display_name="Groq Llama 3.1 8B",
    pricing=ModelPricing(input_per_1m=0.0, output_per_1m=0.0),
    is_cheap=True,
    context_limit=8192,
)

GROQ_LLAMA_70B = ModelSpec(
    provider=LLMProvider.GROQ,
    model_name="llama-3.3-70b-versatile",
    display_name="Groq Llama 3.3 70B",
    pricing=ModelPricing(input_per_1m=0.0, output_per_1m=0.0),
    is_cheap=True,
    context_limit=32768,
)

GROQ_QWEN_32B = ModelSpec(
    provider=LLMProvider.GROQ,
    model_name="qwen/qwen3-32b",
    display_name="Groq Qwen 3 32B",
    pricing=ModelPricing(input_per_1m=0.0, output_per_1m=0.0),
    is_cheap=True,
    context_limit=32768,
)

GROQ_LLAMA_4_SCOUT = ModelSpec(
    provider=LLMProvider.GROQ,
    model_name="meta-llama/llama-4-scout-17b-16e-instruct",
    display_name="Groq Llama 4 Scout 17B",
    pricing=ModelPricing(input_per_1m=0.0, output_per_1m=0.0),
    is_cheap=True,
    context_limit=16384,
)

# ── Pricing for gpt-4o (strong/fallback model) ─────────────────────────────
# From KiwiQ config.py: GPT_4o = "gpt-4o"
GPT_4O = ModelSpec(
    provider=LLMProvider.OPENAI,
    model_name="gpt-4o",
    display_name="GPT-4o",
    pricing=ModelPricing(input_per_1m=2.50, output_per_1m=10.0),
)

# ── Pricing for claude-sonnet-4 (strong Anthropic model) ───────────────────
# From KiwiQ config.py: CLAUDE_SONNET_4 = "claude-sonnet-4-20250514"
CLAUDE_SONNET_4 = ModelSpec(
    provider=LLMProvider.ANTHROPIC,
    model_name="claude-sonnet-4-20250514",
    display_name="Claude Sonnet 4",
    pricing=ModelPricing(input_per_1m=3.0, output_per_1m=15.0, cached_input_per_1m=0.3),
)

# ── Master registry ────────────────────────────────────────────────────────
ALL_MODELS: dict[str, ModelSpec] = {
    s.model_name: s for s in [
        GROQ_LLAMA_8B, GROQ_LLAMA_70B, GROQ_QWEN_32B, GROQ_LLAMA_4_SCOUT,
        GPT_4O_MINI, CLAUDE_3_5_HAIKU, GEMINI_2_5_FLASH,
        GPT_4O, CLAUDE_SONNET_4,
    ]
}

CHEAP_MODELS: list[ModelSpec] = [s for s in ALL_MODELS.values() if s.is_cheap]
STRONG_MODELS: list[ModelSpec] = [s for s in ALL_MODELS.values() if not s.is_cheap]

# Groq models indexed for quick lookup (ordered cheap → capable)
GROQ_MODELS: list[ModelSpec] = [
    GROQ_LLAMA_8B, GROQ_QWEN_32B, GROQ_LLAMA_4_SCOUT, GROQ_LLAMA_70B,
]


def get_model(model_name: str) -> ModelSpec:
    """Look up a model spec by name."""
    if model_name not in ALL_MODELS:
        raise ValueError(f"Unknown model: {model_name!r}; known: {list(ALL_MODELS)}")
    return ALL_MODELS[model_name]


def pick_cheapest() -> ModelSpec:
    """Pick the cheapest available model (by input price)."""
    return min(ALL_MODELS.values(), key=lambda m: m.pricing.input_per_1m)


def models_for_provider(base_url: str = "") -> tuple[list[ModelSpec], list[ModelSpec]]:
    """Return (cheap_models, strong_models) appropriate for the given base URL.

    Auto-detects Groq from the base URL and returns only Groq-compatible models.
    Falls back to non-Groq models for other providers (or default if no hint).
    """
    if "groq" in base_url.lower():
        return GROQ_MODELS, []
    # Exclude Groq models when using a non-Groq provider (they'd waste API calls).
    filtered_cheap = [m for m in CHEAP_MODELS if m.provider != LLMProvider.GROQ]
    filtered_strong = [m for m in STRONG_MODELS if m.provider != LLMProvider.GROQ]
    return filtered_cheap, filtered_strong
