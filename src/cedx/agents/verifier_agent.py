"""Verifier Agent — independently checks Worker Agent output.

Checks:
  - AGENT_HALLUCINATION: content in delivered_fields not traceable to source
  - AGENT_MALFORMED: missing required fields, invalid types, nulls where not expected

Pattern: agent-checks-agent (independent verification).
Each record is verified independently; failed records are routed to exception queue.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from cedx.agents.base import BaseAgent, AgentContext, AgentContract
from cedx.agents.llm_client import LLMClient, LLMInput, LLMOutput
from cedx.agents.model_metadata import CHEAP_MODELS, STRONG_MODELS, models_for_provider
from cedx.agents.cost_tracker import format_cost_usd
from cedx.agents.transcript_recorder import TranscriptRecorder
from cedx.models.record import Record, ReasonCode, ReasonClass, RecordStatus
from cedx.utils.hashing import sha


VERIFIER_PROMPT_VERSION = "0.1.0"


VERIFY_SYSTEM_PROMPT = """You are an independent quality verifier for CEDX Tiny Kit.
Your job is to verify the output produced by the Worker agent.

You will receive:
  1. The original record data (source truth)
  2. The Worker's draft output (delivered_fields)

Check for these issues:
  1. AGENT_HALLUCINATION — content in delivered_fields that has no basis in the source record.
     Example: Worker adds a name or value not present in the original data.
  2. AGENT_MALFORMED — delivered_fields is missing required fields, has invalid types,
     or contains null where a valid value was available in source data.

Required fields check: owner, deadline, category, amount, notes — all must be present.
owner, category must be non-null strings. amount must be numeric or null (not string, not missing).

Respond with valid JSON exactly matching this schema:
{
  "verdict": "pass" or "fail",
  "issues": [
    {
      "type": "AGENT_HALLUCINATION" or "AGENT_MALFORMED",
      "field": "field_name",
      "detail": "description of the issue"
    }
  ],
  "confidence": 0.95
}

Rules:
- If NO issues found, verdict must be "pass" with empty issues list.
- If ANY issue found, verdict must be "fail".
- Be thorough but fair. Null is acceptable for deadline if not in source.
- An amount that was changed from a valid value to null IS AGENT_MALFORMED."""


VERIFY_USER_PROMPT_TEMPLATE = """Verify the Worker's draft for this record:

=== SOURCE RECORD ===
Record ID: {record_id}
Owner: {owner}
Deadline: {deadline}
Category: {category}
Amount: {amount}
Notes: {notes}
Raw fields: {raw_fields}

=== WORKER DRAFT ===
Delivered fields: {delivered_fields}"""


class VerifierAgent(BaseAgent):
    """Verifier agent — independently checks Worker output.

    Each record verified independently; failed records go to exception queue.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        transcript_recorder: Optional[TranscriptRecorder] = None,
        models: Optional[list[str]] = None,
    ):
        contract = AgentContract(
            prompt_version=VERIFIER_PROMPT_VERSION,
            can_call=[],
        )

        # Model selection: try most capable first (quality), fall back to
        # cheapest (rate-limit resilience on free tier).
        base_url = getattr(llm_client, "base_url", "") or ""
        cheap, strong = models_for_provider(base_url)
        all_models = strong + cheap if strong else cheap
        if models:
            self._primary_model = models[0]
            self._fallback_model = models[-1] if len(models) > 1 else models[0]
        elif all_models:
            self._primary_model = all_models[-1].model_name  # most capable (last)
            self._fallback_model = all_models[0].model_name  # cheapest (first)
        else:
            self._primary_model = "gpt-4o-mini"
            self._fallback_model = "gpt-4o-mini"

        super().__init__(
            name="verifier_agent",
            role="verifier",
            contract=contract,
            models=[self._primary_model, self._fallback_model],
        )
        self.llm = llm_client
        self.transcript_recorder = transcript_recorder or TranscriptRecorder()

    async def process(
        self,
        context: AgentContext,
        record: Record,
        **kwargs: Any,
    ) -> AgentContext:
        """Verify a single record's delivered_fields.

        Returns updated context with verification verdict and trace spans.
        """
        span = self.start_trace_span(context.record_id)

        if not record.delivered_fields:
            # Nothing to verify — Worker didn't produce output
            trace = self.complete_trace_span(
                span,
                status="rejected",
                verdict="fail",
            )
            trace["error"] = "no_delivered_fields_to_verify"
            record.agent_trace.append(trace)
            record.reason_code = ReasonCode.AGENT_MALFORMED
            record.reason_class = ReasonClass.A
            record.status = RecordStatus.EXCEPTION
            context.trace_spans.append(trace)
            return context

        # Tag LLM for transcript tracking
        self.llm._calling_agent = self.name
        self.llm._prompt_version = VERIFIER_PROMPT_VERSION

        user_prompt = VERIFY_USER_PROMPT_TEMPLATE.format(
            record_id=record.id,
            owner=record.owner or "null",
            deadline=record.deadline or "null",
            category=record.category or "null",
            amount=record.amount if record.amount is not None else "null",
            notes=record.notes or "null",
            raw_fields=json.dumps(record.raw_fields, default=str),
            delivered_fields=json.dumps(record.delivered_fields, default=str),
        )

        # Try primary (most capable) model first; fall back to cheapest on error.
        models_to_try = [self._primary_model, self._fallback_model]
        if self._primary_model == self._fallback_model:
            models_to_try = [self._primary_model]

        result = None
        for model_name in models_to_try:
            inp = LLMInput(
                system_prompt=VERIFY_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                record_id=record.id,
                model_name=model_name,
                max_tokens=1024,
                temperature=0.0,
            )
            result = await self.llm.generate(inp)
            if result.error is None and (result.structured_output or result.content):
                break

        # Parse the verification result
        verdict = "fail"
        issues = []
        confidence = 0.0

        if result is None:
            result = LLMOutput(error="all_models_failed")

        if result.structured_output:
            verdict = result.structured_output.get("verdict", "fail")
            issues = result.structured_output.get("issues", [])
            confidence = result.structured_output.get("confidence", 0.0)
        elif result.content:
            # Fallback: try to parse JSON from content
            try:
                parsed = json.loads(result.content)
                verdict = parsed.get("verdict", "fail")
                issues = parsed.get("issues", [])
                confidence = parsed.get("confidence", 0.0)
            except (json.JSONDecodeError, TypeError):
                pass

        # If verdict not "pass", route to exception queue
        # Map issue types to appropriate reason codes
        if verdict != "pass":
            issue_types = {i.get("type", "") for i in issues}
            if "AGENT_HALLUCINATION" in issue_types:
                record.reason_code = ReasonCode.AGENT_HALLUCINATION
            elif "AGENT_MALFORMED" in issue_types:
                record.reason_code = ReasonCode.AGENT_MALFORMED
            else:
                record.reason_code = ReasonCode.UNVERIFIED_ANOMALY
            record.reason_class = ReasonClass.A
            record.status = RecordStatus.EXCEPTION

        trace = self.complete_trace_span(
            span,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
            status="ok" if verdict == "pass" else "rejected",
            verdict=verdict,
        )
        trace["issues"] = issues
        trace["confidence"] = confidence
        trace["model_used"] = result.model_name
        if result.error:
            trace["error"] = result.error
        record.agent_trace.append(trace)

        context.trace_spans.append(trace)
        context.accumulated_cost += result.cost_usd

        return context
