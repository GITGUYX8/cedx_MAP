"""Stage 3 entry point — run the agent fleet (Worker + Verifier).

Usage:
    python scripts/run_agents.py [--store out/records.db] [--transcripts transcripts/]
        [--replay] [--case-id CEDX-XXXX]

Environment variables:
    REPLAY_LLM=true|false   (default: true — replay committed transcripts)
    LLM_API_KEY             (required when REPLAY_LLM=false)
    LLM_MODEL               (default: gpt-4o-mini)
    LLM_BASE_URL            (optional — for proxies/non-OpenAI endpoints)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Ensure the package root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cedx.agents.llm_client import LLMClient
from cedx.agents.transcript_recorder import TranscriptRecorder
from cedx.agents.worker_agent import WorkerAgent
from cedx.agents.verifier_agent import VerifierAgent
from cedx.agents.pipeline_agent import PipelineAgent, AgentContext
from cedx.intake.store import RecordStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CEDX Stage 3 — Agent Fleet Assembly")
    parser.add_argument("--store", default="out/records.db", help="Path to SQLite record store")
    parser.add_argument("--transcripts", default="transcripts", help="Transcripts directory")
    parser.add_argument("--replay", action="store_true", default=True, help="Replay transcripts (default)")
    parser.add_argument("--no-replay", action="store_false", dest="replay", help="Make real LLM calls")
    parser.add_argument("--case-id", default="CEDX-0000", help="Case ID for amendment")
    parser.add_argument("--exception-queue", default="out/exception_queue.json", help="Exception queue path")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    # Resolve replay mode (CLI arg wins, then env var, then default true)
    replay = args.replay
    if "REPLAY_LLM" in os.environ:
        replay = os.environ["REPLAY_LLM"].lower() in ("1", "true", "yes")

    store = RecordStore(args.store)
    transcript_recorder = TranscriptRecorder(args.transcripts)
    llm_client = LLMClient(
        transcript_recorder=transcript_recorder,
        replay=replay,
    )

    # Build agents
    worker = WorkerAgent(
        llm_client=llm_client,
        transcript_recorder=transcript_recorder,
    )
    verifier = VerifierAgent(
        llm_client=llm_client,
        transcript_recorder=transcript_recorder,
    )

    # Build orchestrator and register sub-agents
    orchestrator = PipelineAgent(
        store=store,
        llm_client=llm_client,
        transcript_recorder=transcript_recorder,
        exception_queue_path=args.exception_queue,
        case_id=args.case_id,
    )
    orchestrator.register_agent(worker)
    orchestrator.register_agent(verifier)

    # Run the pipeline
    context = AgentContext()
    context = await orchestrator.process(context)

    summary = context.pipeline_state.get("stage3_summary", {})
    print(f"Stage 3 complete — {summary.get('records_processed', 0)} processed, "
          f"{summary.get('records_passed', 0)} passed, "
          f"{summary.get('records_failed', 0)} failed, "
          f"total cost ${summary.get('total_cost', 0.0):.6f}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
