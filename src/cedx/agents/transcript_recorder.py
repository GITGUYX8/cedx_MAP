"""Transcript recorder for CEDX LLM calls.

Each LLM call is recorded as a JSON file in the transcripts/ directory,
keyed by the sha256 hash of the response content.

This enables:
  - REPLAY_LLM=true: replay committed transcripts deterministically
  - REPLAY_LLM=false: make real LLM calls
  - Grading verification: each delivered record's transcript_hash proves
    a Worker agent made the load-bearing call.

Pattern adapted from KiwiQ's LLM metadata + response tracking.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from cedx.utils.hashing import sha
from cedx.agents.model_metadata import ModelSpec


# Default transcripts directory (relative to repo root)
DEFAULT_TRANSCRIPTS_DIR = Path(os.environ.get("TRANSCRIPTS_DIR", "transcripts"))


class TranscriptRecorder:
    """Records and replays LLM call transcripts."""

    def __init__(self, transcripts_dir: str | Path = DEFAULT_TRANSCRIPTS_DIR):
        self.dir = Path(transcripts_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        agent: str,
        model_spec: ModelSpec,
        prompt_version: str,
        request: dict[str, Any],
        response: Any,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        latency_ms: float,
        record_id: str,
        delivered_fields_hash: Optional[str] = None,
        retries: int = 0,
        status: str = "ok",
    ) -> str:
        """Record an LLM call as a transcript file.

        Returns the transcript hash (sha256 of response).
        """
        response_hash = sha(response)
        transcript = {
            "agent": agent,
            "model": model_spec.model_name,
            "provider": model_spec.provider.value,
            "prompt_version": prompt_version,
            "record_id": record_id,
            "request": request,
            "response": response,
            "response_hash": response_hash,
            "delivered_fields_hash": delivered_fields_hash,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
            "retries": retries,
            "status": status,
        }
        # Filename = response hash (without sha256: prefix)
        stem = response_hash.split(":")[-1]
        filepath = self.dir / f"{stem}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(transcript, f, indent=2, default=str, ensure_ascii=False)
        return response_hash

    def load(self, transcript_hash: str) -> Optional[dict[str, Any]]:
        """Load a transcript by its hash (e.g. 'sha256:<hex>')."""
        stem = transcript_hash.split(":")[-1]
        filepath = self.dir / f"{stem}.json"
        if not filepath.exists():
            return None
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def find_by_record(self, record_id: str, agent: str) -> Optional[dict[str, Any]]:
        """Find the latest transcript for a given record and agent."""
        best = None
        for fp in self.dir.glob("*.json"):
            t = json.loads(fp.read_text(encoding="utf-8"))
            if t.get("record_id") == record_id and t.get("agent") == agent:
                best = t
        return best

    def exists(self, transcript_hash: str) -> bool:
        stem = transcript_hash.split(":")[-1]
        return (self.dir / f"{stem}.json").exists()
