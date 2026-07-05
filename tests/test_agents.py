"""Unit tests for Stage 3 — Agent Fleet."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from cedx.agents.model_metadata import (
    ALL_MODELS, CHEAP_MODELS, STRONG_MODELS,
    get_model, pick_cheapest, ModelSpec,
)
from cedx.agents.cost_tracker import calculate_cost, format_cost_usd
from cedx.agents.transcript_recorder import TranscriptRecorder
from cedx.agents.llm_client import LLMClient, LLMInput, LLMOutput
from cedx.agents.base import BaseAgent, AgentContext, AgentContract
from cedx.agents.worker_agent import WorkerAgent
from cedx.agents.verifier_agent import VerifierAgent
from cedx.agents.pipeline_agent import PipelineAgent
from cedx.models.record import Record, ReasonCode
from cedx.intake.store import RecordStore


# ── model_metadata ──────────────────────────────────────────────────────────


def test_all_models_defined():
    assert len(ALL_MODELS) >= 4  # at least 4 models
    assert "gpt-4o-mini" in ALL_MODELS
    assert "claude-3-5-haiku-latest" in ALL_MODELS
    assert "gpt-4o" in ALL_MODELS


def test_cheap_models_nonempty():
    assert len(CHEAP_MODELS) >= 1
    for m in CHEAP_MODELS:
        assert m.is_cheap
        assert m.model_name in ALL_MODELS


def test_get_model():
    spec = get_model("gpt-4o-mini")
    assert spec.provider.value == "openai"
    assert spec.pricing.input_per_1m == 0.15


def test_get_model_unknown():
    try:
        get_model("nonexistent")
        assert False, "should raise"
    except ValueError:
        pass


def test_pick_cheapest():
    cheapest = pick_cheapest()
    assert cheapest.pricing.input_per_1m == 0.0  # gemini is free


# ── cost_tracker ────────────────────────────────────────────────────────────


def test_calculate_cost_gpt4o_mini():
    cost = calculate_cost("gpt-4o-mini", input_tokens=1000, output_tokens=500)
    # (1000/1M)*0.15 + (500/1M)*0.60 = 0.00015 + 0.0003 = 0.00045
    assert abs(cost - 0.00045) < 1e-7


def test_calculate_cost_unknown_model():
    cost = calculate_cost("unknown-model", input_tokens=1000, output_tokens=500)
    assert cost == 0.0


def test_format_cost_usd():
    assert format_cost_usd(0.001234) == "$0.001234"


# ── transcript_recorder ─────────────────────────────────────────────────────


def test_record_and_load_transcript():
    with tempfile.TemporaryDirectory() as tmp:
        tr = TranscriptRecorder(Path(tmp))
        h = tr.record(
            agent="test_agent",
            model_spec=get_model("gpt-4o-mini"),
            prompt_version="0.1.0",
            request={"test": True},
            response="hello world",
            tokens_in=10,
            tokens_out=5,
            cost_usd=0.001,
            latency_ms=100,
            record_id="REC-TEST",
        )
        assert h.startswith("sha256:")

        loaded = tr.load(h)
        assert loaded is not None
        assert loaded["response"] == "hello world"
        assert loaded["record_id"] == "REC-TEST"


def test_transcript_not_found():
    with tempfile.TemporaryDirectory() as tmp:
        tr = TranscriptRecorder(Path(tmp))
        loaded = tr.load("sha256:nonexistent")
        assert loaded is None


# ── llm_client (mock mode) ──────────────────────────────────────────────────


def test_llm_mock_worker():
    with tempfile.TemporaryDirectory() as tmp:
        tr = TranscriptRecorder(Path(tmp) / "transcripts")
        client = LLMClient(transcript_recorder=tr, replay=True)
        client._calling_agent = "worker_agent"
        inp = LLMInput(
            user_prompt="Draft branded output for work request:\n\nRecord ID: REC-001\nOwner: a.shah\nDeadline: 2026-07-15\nCategory: ONBOARDING\nAmount: 4800\nNotes: Standard new-client setup.\nVersion: 1\n",
            record_id="REC-001",
            model_name="gpt-4o-mini",
        )
        result = client._replay(inp)
        assert result.structured_output is not None
        df = result.structured_output.get("delivered_fields", {})
        assert df.get("owner") == "a.shah"
        assert df.get("category") == "ONBOARDING"
        assert df.get("amount") == 4800


def test_llm_mock_verifier():
    with tempfile.TemporaryDirectory() as tmp:
        tr = TranscriptRecorder(Path(tmp) / "transcripts")
        client = LLMClient(transcript_recorder=tr, replay=True)
        client._calling_agent = "verifier_agent"
        inp = LLMInput(
            user_prompt="=== SOURCE RECORD ===\nRecord ID: REC-001\n=== WORKER DRAFT ===\nDelivered fields: ...",
            record_id="REC-001",
            model_name="gpt-4o-mini",
        )
        result = client._replay(inp)
        assert result.structured_output is not None
        assert result.structured_output.get("verdict") == "pass"


# ── worker_agent ────────────────────────────────────────────────────────────


def _run_async(coro):
    import asyncio
    return asyncio.run(coro)


def test_worker_drafts_delivered_fields():
    with tempfile.TemporaryDirectory() as tmp:
        tr = TranscriptRecorder(Path(tmp) / "transcripts")
        store = RecordStore(Path(tmp) / "records.db")
        client = LLMClient(transcript_recorder=tr, replay=True)
        worker = WorkerAgent(llm_client=client, transcript_recorder=tr)

        record = Record(
            id="REC-001",
            owner="a.shah",
            deadline="2026-07-15",
            category="ONBOARDING",
            amount=4800,
            notes="Standard setup.",
            source_format="feed",
            raw_fields={"name": "a.shah", "budget": 4800},
        )
        store.upsert(record)

        ctx = AgentContext(record_id="REC-001")
        ctx = _run_async(worker.process(ctx, record=record))

        assert record.delivered_fields is not None
        assert record.delivered_fields.get("owner") == "a.shah"
        assert record.delivered_fields.get("amount") == 4800
        assert record.transcript_hash is not None  # mock now records transcript
        assert len(record.agent_trace) == 1
        assert record.agent_trace[0]["verdict"] == "pass"


def test_worker_abstains_empty_record():
    """Worker abstains (LOW_CONFIDENCE) for empty records with no owner."""
    with tempfile.TemporaryDirectory() as tmp:
        tr = TranscriptRecorder(Path(tmp) / "transcripts")
        client = LLMClient(transcript_recorder=tr, replay=True)
        worker = WorkerAgent(llm_client=client, transcript_recorder=tr)

        record = Record(
            id="REC-EMPTY",
            owner=None,
            deadline=None,
            category=None,
            amount=None,
            notes="",
            source_format="feed",
            raw_fields={},
        )

        ctx = AgentContext(record_id="REC-EMPTY")
        ctx = _run_async(worker.process(ctx, record=record))

        assert record.delivered_fields is None  # abstain → no output
        assert record.reason_code == "LOW_CONFIDENCE"
        assert record.status == "exception"
        assert len(record.agent_trace) == 1
        assert record.agent_trace[0]["verdict"] == "abstain"


# ── verifier_agent ──────────────────────────────────────────────────────────


def test_verifier_passes_ok():
    with tempfile.TemporaryDirectory() as tmp:
        tr = TranscriptRecorder(Path(tmp) / "transcripts")
        store = RecordStore(Path(tmp) / "records.db")
        client = LLMClient(transcript_recorder=tr, replay=True)
        verifier = VerifierAgent(llm_client=client, transcript_recorder=tr)

        record = Record(
            id="REC-001",
            owner="a.shah",
            deadline="2026-07-15",
            category="ONBOARDING",
            amount=4800,
            notes="Test note.",
            source_format="feed",
            delivered_fields={
                "owner": "a.shah",
                "deadline": None,
                "category": "ONBOARDING",
                "amount": 4800,
                "notes": None,
            },
        )

        ctx = AgentContext(record_id="REC-001")
        ctx = _run_async(verifier.process(ctx, record=record))

        assert record.reason_code is None  # pass = no exception
        assert len(record.agent_trace) == 1
        assert record.agent_trace[0]["verdict"] == "pass"


def test_verifier_fails_no_fields():
    with tempfile.TemporaryDirectory() as tmp:
        tr = TranscriptRecorder(Path(tmp) / "transcripts")
        client = LLMClient(transcript_recorder=tr, replay=True)
        verifier = VerifierAgent(llm_client=client, transcript_recorder=tr)

        record = Record(
            id="REC-NO", owner=None, deadline=None, category=None,
            amount=None, notes=None,
            source_format="feed", delivered_fields=None,
        )

        ctx = AgentContext(record_id="REC-NO")
        ctx = _run_async(verifier.process(ctx, record=record))

        assert record.reason_code == "AGENT_MALFORMED"
        assert record.status == "exception"


# ── pipeline_agent ──────────────────────────────────────────────────────────


def test_pipeline_agent_processes_clean_records():
    with tempfile.TemporaryDirectory() as tmp:
        tdir = Path(tmp)
        store = RecordStore(tdir / "records.db")
        tr = TranscriptRecorder(tdir / "transcripts")
        client = LLMClient(transcript_recorder=tr, replay=True)

        worker = WorkerAgent(llm_client=client, transcript_recorder=tr)
        verifier = VerifierAgent(llm_client=client, transcript_recorder=tr)

        pipeline = PipelineAgent(
            store=store,
            llm_client=client,
            transcript_recorder=tr,
            exception_queue_path=tdir / "exception_queue.json",
        )
        pipeline.register_agent(worker)
        pipeline.register_agent(verifier)

        # Insert two clean records with unique raw_fields so hashes differ
        for i in range(1, 3):
            rec = Record(
                id=f"REC-00{i}",
                owner=f"user{i}",
                deadline="2026-07-15",
                category="REVIEW",
                amount=5000 + i * 100,
                notes=f"Review {i}",
                source_format="feed",
                raw_fields={"id": f"REC-00{i}", "notes": f"Review {i}", "budget": 5000 + i * 100},
            )
            rec.source_version_hash = rec.compute_source_version_hash()
            store.upsert(rec)

        ctx = AgentContext()
        import asyncio
        ctx = asyncio.run(pipeline.process(ctx))

        s3 = ctx.pipeline_state.get("stage3_summary", {})
        assert s3["records_processed"] == 2
        assert s3["records_passed"] == 2
        assert s3["records_failed"] == 0

        # Verify records persisted
        updated = store.get_all()
        for r in updated:
            assert r.delivered_fields is not None
            assert len(r.agent_trace) == 3  # worker + verifier + approval


def test_pipeline_roster():
    with tempfile.TemporaryDirectory() as tmp:
        store = RecordStore(Path(tmp) / "records.db")
        client = LLMClient(replay=True)
        worker = WorkerAgent(llm_client=client)
        verifier = VerifierAgent(llm_client=client)
        pipeline = PipelineAgent(store=store, llm_client=client)
        pipeline.register_agent(worker)
        pipeline.register_agent(verifier)

        roster = pipeline._build_roster()
        assert len(roster) == 3  # orchestrator + worker + verifier
        names = [e["name"] for e in roster]
        assert "orchestrator_agent" in names
        assert "worker_agent" in names
        assert "verifier_agent" in names


if __name__ == "__main__":
    test_all_models_defined()
    test_cheap_models_nonempty()
    test_get_model()
    test_get_model_unknown()
    test_pick_cheapest()
    test_calculate_cost_gpt4o_mini()
    test_calculate_cost_unknown_model()
    test_format_cost_usd()
    test_record_and_load_transcript()
    test_transcript_not_found()
    test_llm_mock_worker()
    test_llm_mock_verifier()
    test_worker_drafts_delivered_fields()
    test_worker_abstains_empty_record()
    test_verifier_passes_ok()
    test_verifier_fails_no_fields()
    test_pipeline_agent_processes_clean_records()
    test_pipeline_roster()
    print("All agent tests passed.")
