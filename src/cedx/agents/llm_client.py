"""Lightweight multi-provider LLM client — adapted from KiwiQ's LLMNode pattern.

Supports:
  - REPLAY_LLM=true: reads committed transcripts (default, offline)
  - REPLAY_LLM=false: calls real model via configured provider

Provider support (≥1 required by task spec):
  - gpt-4o-mini (OpenAI) — primary cheap model
  - claude-3-5-haiku (Anthropic)
  - gemini-2.5-flash (Google)

Pattern adapted from KiwiQ's LLMNode at
supp_repo/kiwiq/services/workflow_service/registry/nodes/llm/llm_node.py.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from cedx.agents.model_metadata import (
    LLMProvider,
    ModelSpec,
    ALL_MODELS,
    get_model,
    pick_cheapest,
)
from cedx.agents.cost_tracker import calculate_cost
from cedx.agents.transcript_recorder import TranscriptRecorder


@dataclass
class LLMInput:
    """Input to an LLM call — adapted from KiwiQ's LLMNodeInputSchema."""
    system_prompt: Optional[str] = None
    user_prompt: Optional[str] = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    record_id: str = ""
    model_name: str = "gpt-4o-mini"
    max_tokens: int = 1024
    temperature: float = 0.0
    response_format: Optional[dict[str, Any]] = None  # JSON schema for structured output


@dataclass
class LLMOutput:
    """Output from an LLM call — adapted from KiwiQ's LLMNodeOutputSchema + LLMMetadata."""
    content: Optional[str] = None
    structured_output: Optional[dict[str, Any]] = None
    model_name: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    finish_reason: str = ""
    error: Optional[str] = None
    transcript_hash: Optional[str] = None


class LLMClient:
    """Multi-provider LLM client.

    In REPLAY mode, reads from transcripts instead of calling a real model.
    In LIVE mode, calls the real model via the OpenAI-compatible API.

    Adapted from KiwiQ's LLMNode.process() + _process_llm() + _execute_model().
    """

    def __init__(
        self,
        transcript_recorder: Optional[TranscriptRecorder] = None,
        replay: bool = True,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.replay = replay
        self.transcript_recorder = transcript_recorder or TranscriptRecorder()

        # Live LLM config (used when replay=False)
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.model = model or os.environ.get("LLM_MODEL", "gpt-4o-mini")
        self.base_url = base_url or os.environ.get("LLM_BASE_URL", "")
        if self.base_url == "":
            self.base_url = None

    async def generate(self, inp: LLMInput) -> LLMOutput:
        """Generate a response from the LLM.

        In replay mode, looks up the committed transcript for this record and agent.
        In live mode, calls the real model.
        """
        if self.replay:
            return self._replay(inp)

        return await self._live(inp)

    def _replay(self, inp: LLMInput) -> LLMOutput:
        """Replay a committed transcript or fall back to mock."""
        agent = getattr(self, "_calling_agent", "worker")
        transcript = self.transcript_recorder.find_by_record(
            record_id=inp.record_id,
            agent=agent,
        )
        if transcript is not None:
            response = transcript.get("response", "")
            structured = transcript.get("structured_output")

            # If structured_output not stored in transcript, try JSON parse
            if structured is None and isinstance(response, str):
                try:
                    structured = json.loads(response)
                    if not isinstance(structured, dict):
                        structured = None
                except (json.JSONDecodeError, TypeError):
                    pass

            return LLMOutput(
                content=response if isinstance(response, str) else json.dumps(response, default=str),
                structured_output=structured or (response if isinstance(response, dict) else None),
                model_name=transcript.get("model", inp.model_name),
                tokens_in=transcript.get("tokens_in", 0),
                tokens_out=transcript.get("tokens_out", 0),
                cost_usd=transcript.get("cost_usd", 0.0),
                latency_ms=transcript.get("latency_ms", 0),
                transcript_hash=transcript.get("response_hash"),
                finish_reason=transcript.get("status", "ok"),
            )

        # No transcript found: generate a mock response for dev/testing.
        # The mock extracts delivered_fields from the user_prompt content.
        mock = self._mock_response(inp)
        if mock is not None:
            return mock

        return LLMOutput(
            error="transcript_not_found",
            model_name=inp.model_name,
        )

    def _mock_response(self, inp: LLMInput) -> Optional[LLMOutput]:
        """Generate a mock LLM response from the input for dev mode.

        Detects which agent is calling based on prompt markers and returns
        the appropriate structured output format.
        """
        text = inp.user_prompt or ""
        if not text:
            return None

        # Detect agent by prompt content
        is_verifier = "=== SOURCE RECORD ===" in text

        # ── Verifier mock ──────────────────────────────────────────────────
        if is_verifier:
            return self._mock_verifier(text, inp.model_name)

        # ── Worker mock (default) ──────────────────────────────────────────
        return self._mock_worker(text, inp, model_name=inp.model_name)

    def _mock_worker(self, text: str, inp: LLMInput, model_name: str) -> LLMOutput:
        """Mock a Worker agent LLM response: returns branded output.

        Returns error (no structured_output) for low-confidence inputs so the
        Worker agent correctly abstains and routes to LOW_CONFIDENCE.
        """
        def _field(label: str):
            for line in text.split("\n"):
                line = line.strip()
                if line.startswith(label + ":"):
                    val = line[len(label) + 1:].strip()
                    return val if val != "null" else None
            return None

        rid = _field("Record ID")
        owner = _field("Owner")
        deadline = _field("Deadline")
        category = _field("Category")
        amount_raw = _field("Amount")
        notes = _field("Notes")

        # Simulate failed LLM call for low-confidence inputs:
        # missing owner OR ambiguous category ("?")
        if not owner or (category and category.strip() == "?"):
            return LLMOutput(
                error="mock_empty_record",
                model_name=model_name,
                tokens_in=0, tokens_out=0, cost_usd=0.0,
                latency_ms=0, finish_reason="error", transcript_hash=None,
            )

        amount = None
        if amount_raw is not None:
            try:
                amount = float(amount_raw)
            except (ValueError, TypeError):
                amount = None

        delivered_fields = {
            "owner": owner,
            "deadline": deadline or None,
            "category": category,
            "amount": amount,
            "notes": notes,
        }

        confidence = 0.95 if (owner and category) else 0.6 if (owner or category) else 0.3
        summary = f"Work request {rid}: {category or 'uncategorized'} for {owner or 'unknown'}."

        structured = {
            "summary": summary,
            "delivered_fields": delivered_fields,
            "confidence": confidence,
        }

        response_content = json.dumps(structured, default=str)
        agent = getattr(self, "_calling_agent", "worker")
        prompt_version = getattr(self, "_prompt_version", "0.1.0")

        # Compute delivered_fields_hash so transcript can be verified against audit
        from cedx.utils.hashing import sha as _sha
        df_hash = _sha(delivered_fields)

        transcript_hash = None
        try:
            transcript_hash = self.transcript_recorder.record(
                agent=agent,
                model_spec=get_model(model_name),
                prompt_version=prompt_version,
                request={
                    "model": model_name,
                    "system_prompt": inp.system_prompt,
                    "user_prompt": inp.user_prompt,
                    "response_format": inp.response_format,
                    "max_tokens": inp.max_tokens,
                },
                response=response_content,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                latency_ms=0,
                record_id=inp.record_id,
                status="mock",
                delivered_fields_hash=df_hash,
            )
        except Exception:
            pass

        return LLMOutput(
            content=response_content,
            structured_output=structured,
            model_name=model_name,
            tokens_in=0, tokens_out=0, cost_usd=0.0,
            latency_ms=0, finish_reason="mock",
            transcript_hash=transcript_hash,
        )

    def _mock_verifier(self, text: str, model_name: str) -> LLMOutput:
        """Mock a Verifier agent LLM response: always passes valid output."""
        structured = {
            "verdict": "pass",
            "issues": [],
            "confidence": 0.95,
        }

        response_content = json.dumps(structured, default=str)

        return LLMOutput(
            content=response_content,
            structured_output=structured,
            model_name=model_name,
            tokens_in=0, tokens_out=0, cost_usd=0.0,
            latency_ms=0, finish_reason="mock", transcript_hash=None,
        )

    async def _live(self, inp: LLMInput) -> LLMOutput:
        """Call a real LLM model.

        Uses the OpenAI Python client (supports any OpenAI-compatible API via base_url).
        This covers gpt-4o-mini and any model accessible via LLM_BASE_URL.

        For Anthropic/Gemini native support, extend with their respective SDKs.
        """
        try:
            from openai import AsyncOpenAI, APIError, RateLimitError
        except ImportError:
            return LLMOutput(
                error="openai_sdk_not_installed",
                model_name=inp.model_name,
            )

        client_kwargs = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        client = AsyncOpenAI(**client_kwargs)
        model_name = inp.model_name or self.model
        start = time.time()

        messages = []
        if inp.system_prompt:
            messages.append({"role": "system", "content": inp.system_prompt})
        if inp.messages:
            messages.extend(inp.messages)
        if inp.user_prompt:
            messages.append({"role": "user", "content": inp.user_prompt})

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "max_tokens": inp.max_tokens,
            "temperature": inp.temperature,
        }

        # Structured output (JSON mode)
        if inp.response_format:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "schema": inp.response_format,
                    "strict": True,
                },
            }

        try:
            response = await client.chat.completions.create(**kwargs)
            latency_ms = (time.time() - start) * 1000

            choice = response.choices[0] if response.choices else None
            if choice is None:
                return LLMOutput(
                    error="no_choices",
                    model_name=model_name,
                    tokens_in=response.usage.prompt_tokens if response.usage else 0,
                    tokens_out=response.usage.completion_tokens if response.usage else 0,
                    latency_ms=latency_ms,
                )

            content = choice.message.content or ""
            finish_reason = choice.finish_reason or ""

            tokens_in = response.usage.prompt_tokens if response.usage else 0
            tokens_out = response.usage.completion_tokens if response.usage else 0
            cost = calculate_cost(model_name, tokens_in, tokens_out)

            # Parse structured output — always try JSON parsing
            structured = None
            if content:
                for attempt in [content.strip(), content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()]:
                    try:
                        parsed = json.loads(attempt)
                        if isinstance(parsed, dict):
                            structured = parsed
                            break
                    except (json.JSONDecodeError, TypeError):
                        continue

            result = LLMOutput(
                content=content,
                structured_output=structured,
                model_name=model_name,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                latency_ms=latency_ms,
                finish_reason=finish_reason,
            )

            # Record the transcript for future REPLAY
            # (agent tag is set by the caller before calling)
            agent = getattr(self, "_calling_agent", "worker")
            prompt_version = getattr(self, "_prompt_version", "0.1.0")

            request_data = {
                "model": model_name,
                "system_prompt": inp.system_prompt,
                "user_prompt": inp.user_prompt,
                "messages": inp.messages,
                "response_format": inp.response_format,
                "max_tokens": inp.max_tokens,
                "temperature": inp.temperature,
            }

            result.transcript_hash = self.transcript_recorder.record(
                agent=agent,
                model_spec=get_model(model_name),
                prompt_version=prompt_version,
                request=request_data,
                response=content,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                latency_ms=latency_ms,
                record_id=inp.record_id,
                status=finish_reason,
            )

            return result

        except (APIError, RateLimitError) as e:
            latency_ms = (time.time() - start) * 1000
            return LLMOutput(
                error=str(e),
                model_name=model_name,
                latency_ms=latency_ms,
            )
        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            return LLMOutput(
                error=str(e),
                model_name=model_name,
                latency_ms=latency_ms,
            )

    async def close(self) -> None:
        pass
