#!/usr/bin/env python3
"""CLI to run Stage 1-5: Intake → Orchestration → Agents → Approval → Audit."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cedx.intake import run_intake, RecordStore
from cedx.orchestration import run_orchestration
from cedx.agents.llm_client import LLMClient
from cedx.agents.transcript_recorder import TranscriptRecorder
from cedx.agents.worker_agent import WorkerAgent
from cedx.agents.verifier_agent import VerifierAgent
from cedx.approval.approval_agent import ApprovalAgent
from cedx.agents.pipeline_agent import PipelineAgent, AgentContext


def main() -> int:
    seed_dir = os.environ.get("SEED_DIR", "seed")
    store_path = os.environ.get("STORE_PATH", "out/records.db")
    aliases_path = os.environ.get("ALIASES_PATH")
    eq_path = os.environ.get("EXCEPTION_QUEUE_PATH", "out/exception_queue.json")
    case_id = os.environ.get("CASE_ID", "CEDX-0000")
    replay = os.environ.get("REPLAY_LLM", "true").lower() == "true"

    print("=" * 60)
    print("CEDX Tiny Agent Fleet — Pipeline v0.1.0")
    print("=" * 60)
    print(f"SEED_DIR: {seed_dir}")
    print(f"Store:    {store_path}")
    print(f"CASE_ID:  {case_id}")

    # Print amendment from CASE_ID
    from cedx.approval.derivation import derive_amendment
    amd_role, amd_threshold = derive_amendment(case_id)
    print(f"AMENDMENT: role={amd_role} threshold=${amd_threshold:.2f}")
    print()

    # Inject agent-failure sample in replay mode (dev seed sample transcript)
    if replay:
        import subprocess
        try:
            subprocess.run(
                [sys.executable, "scripts/inject_agent_failure.py", "--silent"],
                cwd=Path.cwd(), capture_output=True,
            )
        except Exception:
            pass

    # Shared store instance
    store = RecordStore(store_path)

    # Stage 1 — Intake
    print("[Stage 1] Intake...")
    intake_result = run_intake(
        seed_dir=seed_dir,
        store_path=store_path,
        aliases_path=aliases_path,
    )
    print(f"  Stored {intake_result['records_stored']} records "
          f"(feed={intake_result['by_format']['feed']}, "
          f"eml={intake_result['by_format']['eml']}, "
          f"pdf={intake_result['by_format']['pdf']})")

    # Stage 2 — Orchestration
    print("[Stage 2] Orchestration (Normalize + Exception Queue)...")
    orchestration_result = run_orchestration(
        seed_dir=seed_dir,
        store_path=store_path,
        aliases_path=aliases_path,
        exception_queue_path=eq_path,
    )
    print(f"  Clean for assembly:       {orchestration_result.get('clean_for_assembly', '?')}")
    print(f"  Blocking (Class A):       {orchestration_result['exceptions']['blocking_class_a']}")
    print(f"  Logged (Class B):         {orchestration_result['exceptions']['logged_class_b']}")
    print(f"  Outlier thresholds: lower={orchestration_result['outlier_thresholds']['lower']:.1f}, "
          f"upper={orchestration_result['outlier_thresholds']['upper']:.1f}")
    print(f"  Exception queue:    {orchestration_result['exception_queue']['path']}")

    # Stage 3-5 — Agent Fleet + Approval + Audit
    print("[Stage 3-5] Agent Fleet (Worker + Verifier + Approval + Audit)...")

    replay = os.environ.get("REPLAY_LLM", "true").lower() in ("1", "true", "yes")
    transcript_recorder = TranscriptRecorder("transcripts")
    llm_client = LLMClient(
        transcript_recorder=transcript_recorder,
        replay=replay,
    )

    worker = WorkerAgent(
        llm_client=llm_client,
        transcript_recorder=transcript_recorder,
    )
    verifier = VerifierAgent(
        llm_client=llm_client,
        transcript_recorder=transcript_recorder,
    )
    approval = ApprovalAgent(case_id=case_id)

    orchestrator_agent = PipelineAgent(
        store=store,
        llm_client=llm_client,
        transcript_recorder=transcript_recorder,
        exception_queue_path=eq_path,
        case_id=case_id,
    )
    orchestrator_agent.register_agent(worker)
    orchestrator_agent.register_agent(verifier)
    orchestrator_agent.register_agent(approval)

    agent_context = AgentContext()
    agent_context = asyncio.run(orchestrator_agent.process(agent_context))
    s3 = agent_context.pipeline_state.get("stage3_summary", {})
    print(f"  Processed: {s3.get('records_processed', 0)}, "
          f"Delivered: {s3.get('records_delivered', 0)}, "
          f"Passed: {s3.get('records_passed', 0)}, "
          f"Failed: {s3.get('records_failed', 0)}, "
          f"Cost: ${s3.get('total_cost', 0.0):.6f}")

    print()
    print("Pipeline complete. See: out/audit.json, out/package/, out/exception_queue.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
