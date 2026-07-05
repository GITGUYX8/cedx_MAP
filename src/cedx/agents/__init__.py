"""Multi-agent fleet for CEDX Tiny Agent Fleet.

Pattern adapted from KiwiQ's BaseNode + LLMNode architecture.

Components:
  - model_metadata: Model pricing/specs (from KiwiQ config.py)
  - cost_tracker: Per-call cost calculation (from KiwiQ _calculate_actual_cost)
  - transcript_recorder: Record + replay LLM call transcripts
  - llm_client: Lightweight multi-provider LLM client (from KiwiQ LLMNode)
  - base: Agent base class with typed contracts (from KiwiQ BaseNode)
  - worker_agent: Drafts branded output via model router
  - verifier_agent: Independent output verification
  - pipeline_agent: Orchestrator that runs Worker -> Verifier pipeline
"""
from .model_metadata import (
    ModelSpec, ModelPricing, LLMProvider,
    ALL_MODELS, CHEAP_MODELS, STRONG_MODELS,
    get_model, pick_cheapest,
)
from .cost_tracker import calculate_cost, calculate_estimated_cost, format_cost_usd
from .transcript_recorder import TranscriptRecorder
from .llm_client import LLMClient, LLMInput, LLMOutput
from .base import BaseAgent, AgentContext, AgentContract
from .worker_agent import WorkerAgent
from .verifier_agent import VerifierAgent
from .pipeline_agent import PipelineAgent

__all__ = [
    "ModelSpec", "ModelPricing", "LLMProvider",
    "ALL_MODELS", "CHEAP_MODELS", "STRONG_MODELS",
    "get_model", "pick_cheapest",
    "calculate_cost", "calculate_estimated_cost", "format_cost_usd",
    "TranscriptRecorder",
    "LLMClient", "LLMInput", "LLMOutput",
    "BaseAgent", "AgentContext", "AgentContract",
    "WorkerAgent",
    "VerifierAgent",
    "PipelineAgent",
]
