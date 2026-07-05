"""Approval state machine agent.

State machine: DRAFT → IN_REVIEW → APPROVED → DELIVERED
  (optionally IN_REVIEW → CHANGES_REQUESTED → IN_REVIEW → ...)

The CASE_ID amendment defines the role R that must sign off on
records exceeding the threshold T.

Adapted from KiwiQ's HITLNode / approval pattern.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from cedx.agents.base import BaseAgent, AgentContext, AgentContract
from cedx.approval.derivation import derive_amendment
from cedx.models.record import Record, RecordStatus


class ApprovalAgent(BaseAgent):
    """Manages the approval state machine for delivered records.

    Rules:
      - Records with amount < threshold: auto-approved by orchestrator
      - Records with amount >= threshold: approved by amendment role (R)
      - All records accumulate approval_trail entries
    """

    def __init__(self, case_id: str = "CEDX-0000"):
        contract = AgentContract(
            prompt_version="1.0.0",
            can_call=[],
        )
        super().__init__(
            name="approval_agent",
            role="operator",
            contract=contract,
            models=[],
        )
        self.case_id = case_id
        self.amendment_role, self.amendment_threshold = derive_amendment(case_id)

    async def process(
        self,
        context: AgentContext,
        record: Record,
        **kwargs: Any,
    ) -> AgentContext:
        """Run the approval state machine on a record.

        Returns the context with updated record state and approval trail.
        """
        span = self.start_trace_span(context.record_id)

        if record.status in (RecordStatus.EXCEPTION, RecordStatus.SUPERSEDED, RecordStatus.DELIVERED):
            self.complete_trace_span(span, status="ok", verdict="skipped")
            return context

        now = datetime.now(timezone.utc).isoformat()
        amount = record.amount if record.amount is not None else 0.0
        needs_amendment = amount >= self.amendment_threshold

        # State: draft → in_review
        if record.status in (None, "", RecordStatus.PENDING, RecordStatus.DRAFT):
            record.status = RecordStatus.IN_REVIEW
            record.approval_trail.append({
                "state": "in_review",
                "actor": "orchestrator_agent",
                "ts": now,
                "reason": "entered approval pipeline",
            })

        # If needs amendment role sign-off
        if needs_amendment:
            record.approval_trail.append({
                "state": "in_review",
                "actor": self.amendment_role,
                "ts": now,
                "reason": f"amount {amount} >= threshold {self.amendment_threshold}",
            })

        # Approve
        record.status = RecordStatus.APPROVED
        record.approval_trail.append({
            "state": "approved",
            "actor": self.amendment_role if needs_amendment else "orchestrator_agent",
            "ts": now,
            "reason": f"auto-approved (amount={amount}, threshold={self.amendment_threshold})",
        })

        # Deliver
        record.status = RecordStatus.DELIVERED
        record.approval_trail.append({
            "state": "delivered",
            "actor": "orchestrator_agent",
            "ts": now,
            "reason": "delivered after approval",
        })

        trace = self.complete_trace_span(
            span,
            status="ok",
            verdict="delivered",
        )
        record.agent_trace.append(trace)
        context.trace_spans.append(trace)

        return context
