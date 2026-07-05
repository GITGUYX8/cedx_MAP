# Stage 3 — Assembly (Worker Agent + LLM) — Complete

## What Was Built

### `src/cedx/agents/` — Agent Fleet Module

| File | Purpose | open Source |
|---|---|---|
| `model_metadata.py` | Model pricing/specs (gpt-4o-mini, claude-3-5-haiku, gemini-2.5-flash, gpt-4o, claude-sonnet-4) | `config.py` |
| `cost_tracker.py` | Per-call cost from token counts + model pricing | `_calculate_actual_cost()` |
| `transcript_recorder.py` | Record/replay LLM transcripts for REPLAY mode | LLM metadata + response tracking |
| `llm_client.py` | Multi-provider LLM client (REPLAY + LIVE) | `LLMNode.process()` |
| `base.py` | BaseAgent with typed contracts (input/output schemas, can_call roster) | `BaseNode` |
| `worker_agent.py` | Content drafter with model router (cheap → strong → abstain) | `LLMNode` retry/repair/abstain |
| `verifier_agent.py` | Independent output checker (AGENT_HALLUCINATION, AGENT_MALFORMED) | agent-checks-agent pattern |
| `pipeline_agent.py` | Top-level orchestrator running Worker → Verifier pipeline | `graph/builder.py` |

### Scripts
- `scripts/run_agents.py` — standalone Stage 3 CLI (tested)
- `scripts/run_pipeline.py` — updated with Stage 3 (tested)

### Tests
- `tests/test_agents.py` — 11 tests covering all agent components (passing)

## Architecture

```
PipelineAgent (orchestrator_agent)
  ├── record 1 → WorkerAgent (draft) → VerifierAgent (check) → store
  ├── record 2 → WorkerAgent (draft) → VerifierAgent (check) → store
  └── ...
```

Each record gets:
1. **Worker Agent** drafts branded `delivered_fields` via model router (gpt-4o-mini → claude → gemini → gpt-4o → claude-sonnet; first success wins)
2. **Verifier Agent** independently checks for AGENT_HALLUCINATION / AGENT_MALFORMED
3. Failed records routed to exception queue with UNVERIFIED_ANOMALY reason code

## Model Router Budget Strategy
- **Cheap models** (gpt-4o-mini, claude-3-5-haiku, gemini-2.5-flash): tried first
- **Strong models** (gpt-4o, claude-sonnet-4): fallback on error/low-confidence
- **Abstain**: all models exhausted → route to exception queue (LOW_CONFIDENCE)

## Dev vs Grading Mode
- `REPLAY_LLM=true` (default): reads transcripts from `transcripts/<hash>.json` (fallback to deterministic mock for dev)
- `REPLAY_LLM=false` (real): calls configured LLM model via OpenAI SDK; records transcripts
- Mock mode provides basic branded output for dev testing without API keys

## Test Results
```
All agent tests passed.  (11 tests)
All intake tests passed.   (7 tests)
All orchestration tests passed.  (9 tests)
```

## Pipeline Output (dev seed)
- Stage 1: 23 records stored (16 feed, 3 eml, 4 pdf)
- Stage 2: 5 Class A blocked, 2 Class B logged, 18 clean
- Stage 3: 17 processed, 17 passed, 0 failed, $0.000000

## Remaining Features (beyond Stage 3)
- [ ] Stage 4: Approval state machine + CASE_ID amendment
- [ ] Stage 5: Append-only audit (conformant with audit.schema.json)
- [ ] Stage 5: Branded package output + probe scripts
- [ ] Stage 6+: Anomaly detection, HITLNode, delivery
- [ ] Docker setup (`docker compose up`)
