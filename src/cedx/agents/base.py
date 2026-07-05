"""Agent base class with typed contracts — adapted from KiwiQ's BaseNode pattern.

Each agent has:
  - Declared input/output schemas (typed contracts)
  - A `can_call` list (which agents it may invoke)
  - A `process()` method that implements its business logic
  - A `name` and `role` for the agents roster
  - A `prompt_version` for traceability

Pattern from KiwiQ at
supp_repo/kiwiq/services/workflow_service/registry/nodes/core/base.py.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentContext:
    """Context passed through the agent call chain.

    This replaces KiwiQ's LangGraph state/config system with a simpler
    dictionary that accumulates traces, costs, and results.
    """
    record_id: str = ""
    pipeline_state: dict[str, Any] = field(default_factory=dict)
    trace_spans: list[dict[str, Any]] = field(default_factory=list)
    accumulated_cost: float = 0.0
    agent_name: str = ""


@dataclass
class AgentContract:
    """Typed contract for an agent — input/output schema + call permissions.

    This is the CEDX equivalent of KiwiQ's BaseNode input/output/config schemas.
    """
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    can_call: list[str] = field(default_factory=list)
    prompt_version: str = "0.1.0"


class BaseAgent(ABC):
    """Abstract base agent — adapted from KiwiQ's BaseNode.

    Key differences from KiwiQ:
      - No LangGraph state machine (simpler pipeline context)
      - Direct HTTP-based LLM calls (no LangChain)
      - Built-in cost + latency tracking per agent call
    """

    def __init__(
        self,
        name: str,
        role: str,
        contract: AgentContract,
        models: list[str],
    ):
        self.name = name
        self.role = role
        self.contract = contract
        self.models = models

    @abstractmethod
    async def process(
        self,
        context: AgentContext,
        **kwargs: Any,
    ) -> AgentContext:
        """Process a record/request within the pipeline.

        Args:
            context: The current pipeline context (record state, traces, costs).
            **kwargs: Additional typed inputs per the contract's input_schema.

        Returns:
            Updated context with results, trace spans, and costs appended.
        """
        ...

    def roster_entry(self) -> dict[str, Any]:
        """Return the agent roster entry for the audit."""
        return {
            "name": self.name,
            "role": self.role,
            "models": self.models,
            "prompt_version": self.contract.prompt_version,
            "can_call": self.contract.can_call,
        }

    def start_trace_span(self, record_id: str) -> dict[str, Any]:
        """Start a trace span (observability)."""
        return {
            "agent": self.name,
            "model": self.models[0] if self.models else None,
            "prompt_version": self.contract.prompt_version,
            "tokens_in": None,
            "tokens_out": None,
            "cost_usd": None,
            "latency_ms": None,
            "retries": 0,
            "status": "ok",
            "verdict": None,
            "started_at": time.time(),
        }

    def complete_trace_span(
        self,
        span: dict[str, Any],
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
        latency_ms: float = 0.0,
        status: str = "ok",
        verdict: Optional[str] = None,
        retries: int = 0,
    ) -> dict[str, Any]:
        """Complete a trace span with final metrics."""
        span["tokens_in"] = tokens_in
        span["tokens_out"] = tokens_out
        span["cost_usd"] = cost_usd
        span["latency_ms"] = latency_ms
        span["retries"] = retries
        span["status"] = status
        span["verdict"] = verdict
        span.pop("started_at", None)
        return span
