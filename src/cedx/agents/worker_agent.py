"""Worker Agent — drafts branded output for each clean record.

Model routing:
  - First attempt: cheap model (gpt-4o-mini, claude-3-5-haiku, gemini-2.5-flash)
  - Retry on error/low-confidence: strong model (gpt-4o)
  - Max retries before abstain: records routed to exception queue with LOW_CONFIDENCE

Pattern adapted from KiwiQ's LLMNode retry/repair/abstain logic.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from cedx.agents.base import BaseAgent, AgentContext, AgentContract
from cedx.agents.llm_client import LLMClient, LLMInput, LLMOutput
from cedx.agents.model_metadata import CHEAP_MODELS, STRONG_MODELS, get_model, models_for_provider
from cedx.agents.cost_tracker import format_cost_usd
from cedx.agents.transcript_recorder import TranscriptRecorder
from cedx.models.record import Record, ReasonCode, ReasonClass, RecordStatus
from cedx.utils.hashing import sha


WORKER_PROMPT_VERSION = "0.1.0"


DRAFT_SYSTEM_PROMPT = """You are a professional content brander for CEDX Tiny Kit.
Your job is to take raw work-request data and produce polished, standardized output.

Given a record with these fields:
  - id: unique identifier
  - owner: the requester's name
  - deadline: ISO-8601 date
  - category: work category
  - amount: budget/value
  - notes: free-text notes

Produce a professional branded output with:
  1. A professional summary of the work request (2-3 sentences)
  2. Standardized deliverable format with all fields normalised
  3. A confidence score (0.0-1.0) - lower if you had to guess or data is missing
  4. The delivered_fields object with all canonical fields populated

Respond with valid JSON exactly matching this schema:
{{
  "summary": "professional summary of the work",
  "delivered_fields": {{
    "owner": "owner name",
    "deadline": "ISO date or null",
    "category": "category",
    "amount": numeric or null,
    "notes": "polished notes"
  }},
  "confidence": 0.95
}}

Rules:
- NEVER fabricate information. If a field is missing, use the raw data or set to null.
- Keep confidence high (>0.8) for complete records, moderate (0.5-0.8) for partial, low (<0.5) for very incomplete.
- The delivered_fields must be valid for JSON serialization.
- If the amount is missing, set to null (not 0).
- Do not include any explanation outside the JSON."""


DRAFT_USER_PROMPT_TEMPLATE = """Draft branded output for work request:

Record ID: {record_id}
Owner: {owner}
Deadline: {deadline}
Category: {category}
Amount: {amount}
Notes: {notes}
Version: {version}

Raw fields: {raw_fields}"""


class WorkerAgent(BaseAgent):
    """Worker agent that drafts branded output.

    Uses model routing: cheap → strong → abstain.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        transcript_recorder: Optional[TranscriptRecorder] = None,
        models: Optional[list[str]] = None,
    ):
        contract = AgentContract(
            prompt_version=WORKER_PROMPT_VERSION,
            can_call=["verifier_agent"],
        )

        # Auto-detect model list from the LLM client's base URL (e.g. Groq).
        base_url = getattr(llm_client, "base_url", "") or ""
        cheap, strong = models_for_provider(base_url)
        self.cheap_models = models or [m.model_name for m in cheap]
        self.strong_models = [m.model_name for m in strong]

        super().__init__(
            name="worker_agent",
            role="worker",
            contract=contract,
            models=self.cheap_models + self.strong_models,
        )
        self.llm = llm_client
        self.transcript_recorder = transcript_recorder or TranscriptRecorder()

    async def process(
        self,
        context: AgentContext,
        record: Record,
        **kwargs: Any,
    ) -> AgentContext:
        """Draft branded output for a single record.

        Returns updated context with delivered_fields and trace spans.
        """
        span = self.start_trace_span(context.record_id)

        # Model routing: try cheap first, fall back to strong, then abstain
        for attempt, model_name in enumerate(self._route_models()):
            result = await self._attempt_draft(record, model_name, attempt > 0)

            if result.error is None and result.structured_output is not None:
                return self._handle_success(
                    context, record, result, span, attempt,
                )

            # Failed — retry with next model or abstain
            error_msg = result.error or "no_structured_output"
            if attempt == len(self._route_models()) - 1:
                return self._handle_abstain(
                    context, record, span, attempt, error_msg,
                )

        return context

    def _route_models(self) -> list[str]:
        """Return the ordered list of models to try (cheap then strong)."""
        return [
            *self.cheap_models,
            *self.strong_models,
        ]

    async def _attempt_draft(
        self,
        record: Record,
        model_name: str,
        is_retry: bool,
    ) -> LLMOutput:
        """Make a single LLM draft attempt."""
        # Tag the LLM client so transcripts know who produced them
        self.llm._calling_agent = self.name
        self.llm._prompt_version = WORKER_PROMPT_VERSION

        user_prompt = DRAFT_USER_PROMPT_TEMPLATE.format(
            record_id=record.id,
            owner=record.owner or "null",
            deadline=record.deadline or "null",
            category=record.category or "null",
            amount=record.amount if record.amount is not None else "null",
            notes=record.notes or "null",
            version=record.version,
            raw_fields=json.dumps(record.raw_fields, default=str),
        )

        inp = LLMInput(
            system_prompt=DRAFT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            record_id=record.id,
            model_name=model_name,
            max_tokens=2048,
            temperature=0.2 if is_retry else 0.0,
        )

        return await self.llm.generate(inp)

    def _handle_success(
        self,
        context: AgentContext,
        record: Record,
        result: LLMOutput,
        span: dict[str, Any],
        attempt: int,
    ) -> AgentContext:
        """Handle a successful draft."""
        structured = result.structured_output
        confidence = structured.get("confidence", 0.0)
        delivered_fields = structured.get("delivered_fields", {})

        # Rule-based LOW_CONFIDENCE check: even if LLM produced output,
        # records with truly ambiguous inputs should not proceed.
        if not record.owner or (record.category and record.category.strip() == "?"):
            return self._handle_abstain(
                context, record, span, attempt,
                f"low_confidence_input: owner={record.owner!r} category={record.category!r}",
            )

        # Set delivered_fields on the record
        record.delivered_fields = delivered_fields
        df_hash = sha(delivered_fields)
        record.delivered_fields_hash = df_hash
        record.transcript_hash = result.transcript_hash

        # Update the stored transcript with delivered_fields_hash
        if result.transcript_hash:
            th = result.transcript_hash.split(":")[-1] if ":" in result.transcript_hash else result.transcript_hash
            tpath = self.transcript_recorder.dir / f"{th}.json"
            if tpath.exists():
                try:
                    import json
                    tdata = json.loads(tpath.read_text(encoding="utf-8"))
                    tdata["delivered_fields_hash"] = df_hash
                    tpath.write_text(json.dumps(tdata, indent=2), encoding="utf-8")
                except Exception:
                    pass

        # Agent trace
        trace = self.complete_trace_span(
            span,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
            status="ok",
            verdict="pass" if confidence >= 0.5 else "low_confidence",
            retries=attempt,
        )
        trace["model_used"] = result.model_name
        trace["confidence"] = confidence
        record.agent_trace.append(trace)

        context.trace_spans.append(trace)
        context.accumulated_cost += result.cost_usd

        return context

    def _handle_abstain(
        self,
        context: AgentContext,
        record: Record,
        span: dict[str, Any],
        attempt: int,
        error_msg: str,
    ) -> AgentContext:
        """Handle abstention — route to exception queue."""
        trace = self.complete_trace_span(
            span,
            status="abstained",
            verdict="abstain",
            retries=attempt,
        )
        trace["error"] = error_msg
        record.agent_trace.append(trace)
        record.reason_code = ReasonCode.LOW_CONFIDENCE
        record.reason_class = ReasonClass.A
        record.status = RecordStatus.EXCEPTION

        context.trace_spans.append(trace)
        return context
