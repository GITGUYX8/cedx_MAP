#!/usr/bin/env python3
"""Inject a sample agent-failure record into the dev seed by writing
hallucinated Worker + failing Verifier transcripts for REC-006.

The Worker transcript contains delivered_fields with an extra "priority"
field not present in the source record — a clear AGENT_HALLUCINATION.
The Verifier transcript returns "fail" with an AGENT_HALLUCINATION issue.

Run BEFORE the pipeline (make demo). The pipeline replays these transcripts
and routes REC-006 to the exception queue as AGENT_HALLUCINATION.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from cedx.utils.hashing import sha
from cedx.agents.model_metadata import get_model


TRANSCRIPTS_DIR = Path("transcripts")

# ── Hallucinated Worker response ─────────────────────────────────────
# The Worker invents a "priority" field that has no basis in source data.

WORKER_HALLUCINATED = {
    "summary": "Renewal request REC-006: RENEWAL for f.haddad.",
    "delivered_fields": {
        "owner": "f.haddad",
        "deadline": "2026-07-18",
        "category": "RENEWAL",
        "amount": 5300,
        "notes": "Renewal with minor scope bump.",
        "priority": "HIGH",          # ← hallucinated! Not in source.
        "assigned_team": "Renewals",  # ← hallucinated! Not in source.
    },
    "confidence": 0.95,
}

WORKER_RESPONSE = json.dumps(WORKER_HALLUCINATED, indent=2)

# ── Failing Verifier response ────────────────────────────────────────
# The Verifier catches the hallucination and returns "fail".

VERIFIER_RESPONSE = json.dumps({
    "verdict": "fail",
    "issues": [
        {
            "type": "AGENT_HALLUCINATION",
            "field": "priority",
            "detail": "Worker added 'priority' field not present in source record data",
        },
        {
            "type": "AGENT_HALLUCINATION",
            "field": "assigned_team",
            "detail": "Worker added 'assigned_team' field not present in source record data",
        },
    ],
    "confidence": 0.95,
}, indent=2)


def write_transcript(
    agent: str,
    record_id: str,
    response: str,
    model_name: str = "llama-3.1-8b-instant",
) -> str:
    """Write a transcript file and return its response_hash."""
    model_spec = get_model(model_name)
    response_hash = sha(response)
    stem = response_hash.split(":")[-1]

    delivered_fields = None
    if agent == "worker_agent":
        try:
            df = json.loads(response).get("delivered_fields", {})
            delivered_fields = sha(df)
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    transcript = {
        "agent": agent,
        "model": model_name,
        "provider": model_spec.provider.value if model_spec else "groq",
        "prompt_version": "0.1.0",
        "record_id": record_id,
        "request": {
            "model": model_name,
            "system_prompt": "(injected agent-failure transcript)",
            "user_prompt": f"Record {record_id} (agent-failure probe)",
            "max_tokens": 1024,
            "temperature": 0.0,
        },
        "response": response,
        "response_hash": response_hash,
        "delivered_fields_hash": delivered_fields,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
        "latency_ms": 0,
        "retries": 0,
        "status": "mock",
    }

    filepath = TRANSCRIPTS_DIR / f"{stem}.json"
    filepath.write_text(json.dumps(transcript, indent=2, default=str, ensure_ascii=False))
    return response_hash


def main(silent: bool = False) -> int:
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    worker_hash = write_transcript(
        agent="worker_agent",
        record_id="REC-006",
        response=WORKER_RESPONSE,
    )
    if not silent:
        print(f"Worker transcript: {worker_hash.split(':')[-1][:20]}...")

    verifier_hash = write_transcript(
        agent="verifier_agent",
        record_id="REC-006",
        response=VERIFIER_RESPONSE,
    )
    if not silent:
        print(f"Verifier transcript: {verifier_hash.split(':')[-1][:20]}...")

    if not silent:
        print("Agent-failure sample injected for REC-006.")
    return 0


if __name__ == "__main__":
    silent = "--silent" in sys.argv
    sys.exit(main(silent=silent))
