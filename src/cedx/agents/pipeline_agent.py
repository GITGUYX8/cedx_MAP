"""Top-level orchestrator agent — runs Worker → Verifier → Approval pipeline.

Coordinates the full agent fleet:
  1. Load clean records from store (post-Stage 2)
  2. Route each record through Worker Agent (LLM draft)
  3. Route each Worker output through Verifier Agent (independent check)
  4. Route passed records through Approval Agent (state machine)
  5. Handle failures: update exception queue with agent-layer issues
  6. Write append-only audit + branded output package
  7. Update store with outcomes, traces, costs

Pattern adapted from KiwiQ's workflow orchestration (graph/builder.py).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from cedx.agents.base import BaseAgent, AgentContext, AgentContract
from cedx.agents.worker_agent import WorkerAgent
from cedx.agents.verifier_agent import VerifierAgent
from cedx.agents.llm_client import LLMClient
from cedx.agents.transcript_recorder import TranscriptRecorder
from cedx.agents.model_metadata import CHEAP_MODELS
from cedx.approval.approval_agent import ApprovalAgent
from cedx.audit.events import EventLog
from cedx.audit.builder import AuditBuilder
from cedx.intake.store import RecordStore
from cedx.models.record import Record, RecordStatus, ReasonCode
from cedx.utils.hashing import sha


PIPELINE_AGENT_VERSION = "0.1.0"

# Budget ceiling — per-record cost and step limits.
MAX_COST_USD_PER_RECORD = float(os.environ.get("MAX_COST_USD_PER_RECORD", "0.05"))
MAX_STEPS_PER_RECORD = int(os.environ.get("MAX_STEPS_PER_RECORD", "10"))


class PipelineAgent(BaseAgent):
    """Top-level orchestrator for the agent fleet.

    Implements the assembly line:
      Orchestrator -> [Worker Agent -> Verifier Agent -> Approval Agent] per record
    """

    def __init__(
        self,
        store: RecordStore,
        llm_client: LLMClient,
        transcript_recorder: Optional[TranscriptRecorder] = None,
        exception_queue_path: str | Path = "out/exception_queue.json",
        case_id: str = "CEDX-0000",
    ):
        contract = AgentContract(
            prompt_version=PIPELINE_AGENT_VERSION,
            can_call=["worker_agent", "verifier_agent", "approval_agent"],
        )
        super().__init__(
            name="orchestrator_agent",
            role="orchestrator",
            contract=contract,
            models=[m.model_name for m in CHEAP_MODELS],
        )
        self.store = store
        self.transcript_recorder = transcript_recorder or TranscriptRecorder()
        self.exception_queue_path = Path(exception_queue_path)
        self.exception_queue_path.parent.mkdir(parents=True, exist_ok=True)
        self.case_id = case_id
        self.sub_agents: dict[str, BaseAgent] = {}
        self.event_log = EventLog()

    def register_agent(self, agent: BaseAgent) -> None:
        """Register a sub-agent (Worker, Verifier, Approval)."""
        self.sub_agents[agent.name] = agent

    async def process(
        self,
        context: AgentContext,
        **kwargs: Any,
    ) -> AgentContext:
        """Run the full assembly pipeline across all records.

        Loads records from store, routes through Worker -> Verifier -> Approval,
        writes exception queue, audit, and output package.
        """
        records = self.store.get_all()
        clean_records = [r for r in records if r.status in (
            RecordStatus.PENDING, RecordStatus.DRAFT,
        )]
        worker = self.sub_agents.get("worker_agent")
        verifier = self.sub_agents.get("verifier_agent")
        approval = self.sub_agents.get("approval_agent", ApprovalAgent(case_id=self.case_id))

        summary = {
            "records_loaded": len(records),
            "records_processed": 0,
            "records_passed": 0,
            "records_failed": 0,
            "records_delivered": 0,
            "total_cost": 0.0,
        }

        for record in clean_records:
            if record.status == RecordStatus.EXCEPTION:
                continue

            record.status = RecordStatus.DRAFT
            record_ctx = AgentContext(record_id=record.id)

            # Budget check: enforce per-record cost + step ceilings.
            steps = 0

            # Step A: Worker Agent drafts
            if worker:
                record_ctx = await worker.process(record_ctx, record=record)
                steps += 1

            if record_ctx.accumulated_cost > MAX_COST_USD_PER_RECORD:
                record.reason_code = ReasonCode.BUDGET_EXCEEDED
                record.reason_class = ReasonClass.A
                record.status = RecordStatus.EXCEPTION
                self.event_log.append(
                    actor="orchestrator_agent",
                    action="budget_exceeded",
                    record_id=record.id,
                )
                self.store.upsert(record)
                summary["records_failed"] += 1
                summary["records_processed"] += 1
                summary["total_cost"] += record_ctx.accumulated_cost
                continue

            # Step B: Verifier Agent checks (only if Worker succeeded)
            if verifier and record.status != RecordStatus.EXCEPTION:
                record_ctx = await verifier.process(record_ctx, record=record)
                steps += 1

            if steps > MAX_STEPS_PER_RECORD:
                record.reason_code = ReasonCode.AGENT_LOOP
                record.reason_class = ReasonClass.A
                record.status = RecordStatus.EXCEPTION
                self.event_log.append(
                    actor="orchestrator_agent",
                    action="loop_killed",
                    record_id=record.id,
                )
                self.store.upsert(record)
                summary["records_failed"] += 1
                summary["records_processed"] += 1
                summary["total_cost"] += record_ctx.accumulated_cost
                continue

            # Step C: Approval Agent (only if passed verification)
            if approval and record.status not in (
                RecordStatus.EXCEPTION, RecordStatus.BLOCKED,
            ):
                record_ctx = await approval.process(record_ctx, record=record)

            # Update summary
            if record.status == RecordStatus.DELIVERED:
                summary["records_delivered"] += 1
                summary["records_passed"] += 1
                self.event_log.append(
                    actor="orchestrator_agent",
                    action="deliver",
                    record_id=record.id,
                )
            elif record.status == RecordStatus.EXCEPTION:
                summary["records_failed"] += 1
                self.event_log.append(
                    actor="orchestrator_agent",
                    action="exception",
                    record_id=record.id,
                )
            else:
                summary["records_passed"] += 1
            summary["records_processed"] += 1
            summary["total_cost"] += record_ctx.accumulated_cost

            self.store.upsert(record)

        # Ensure ALL records have an agent_trace (even Stage-2-blocked ones)
        for record in records:
            if not record.agent_trace:
                record.agent_trace.append({
                    "agent": "orchestrator_agent",
                    "status": "routed",
                    "verdict": "blocked",
                    "tokens_in": None,
                    "tokens_out": None,
                    "cost_usd": None,
                    "latency_ms": None,
                    "retries": 0,
                })
                self.store.upsert(record)

        self._write_exception_queue(records)

        # Write audit + output package
        self._build_audit(records)

        context.pipeline_state["stage3_summary"] = summary
        context.pipeline_state["agent_roster"] = self._build_roster()
        return context

    def _build_audit(self, records: list[Record]) -> None:
        """Build and write the audit bundle + branded output package."""
        # Collect all agents
        all_agents = [self]
        for agent in self.sub_agents.values():
            all_agents.append(agent)

        # Write output package
        pkg_path = Path("out/package")
        self._write_output_package(records, pkg_path)

        # Build audit
        seed_dir = os.environ.get("SEED_DIR", "seed")
        pipeline_now = os.environ.get("PIPELINE_NOW", "2026-06-26")
        builder = AuditBuilder(
            store=self.store,
            event_log=self.event_log,
            case_id=self.case_id,
            seed_dir=seed_dir,
            pipeline_now=pipeline_now,
        )
        builder.write(
            agents=all_agents,
            output_path="out/audit.json",
            output_package_path=str(pkg_path),
        )

    def _write_output_package(
        self, records: list[Record], pkg_path: Path,
    ) -> None:
        """Write branded output package to out/package/."""
        pkg_path.mkdir(parents=True, exist_ok=True)
        delivered = [r for r in records if r.status == RecordStatus.DELIVERED]
        exceptions = [r for r in records if r.status == RecordStatus.EXCEPTION]

        output = {
            "case_id": self.case_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "stats": {
                "total_records": len(records),
                "delivered": len(delivered),
                "exceptions": len(exceptions),
            },
            "delivered": [
                {
                    "id": r.id,
                    "version": r.version,
                    "source_format": r.source_format,
                    "delivered_fields": r.delivered_fields,
                    "delivered_fields_hash": r.delivered_fields_hash,
                    "transcript_hash": r.transcript_hash,
                }
                for r in delivered
            ],
            "exceptions": [
                {
                    "id": r.id,
                    "version": r.version,
                    "status": r.status,
                    "reason_code": r.reason_code,
                    "reason_class": r.reason_class,
                }
                for r in exceptions
            ],
        }
        (pkg_path / "package.json").write_text(
            json.dumps(output, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )

    def _write_exception_queue(self, records: list[Record]) -> None:
        """Write updated exception queue with agent-layer failures."""
        exceptions = []
        for r in records:
            if r.reason_code:
                exceptions.append({
                    "id": r.id,
                    "version": r.version,
                    "source_format": r.source_format,
                    "status": r.status,
                    "reason_code": r.reason_code,
                    "reason_class": r.reason_class,
                    "notes": r.notes,
                    "amount": r.amount,
                    "deadline": r.deadline,
                    "category": r.category,
                })

        with open(self.exception_queue_path, "w", encoding="utf-8") as f:
            json.dump(exceptions, f, indent=2, default=str)

    def _build_roster(self) -> list[dict[str, Any]]:
        """Build the agents roster for audit."""
        roster = [self.roster_entry()]
        for agent in self.sub_agents.values():
            roster.append(agent.roster_entry())
        return roster
