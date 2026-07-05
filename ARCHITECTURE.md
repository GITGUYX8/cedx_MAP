# CEDX Tiny Agent Fleet — Architecture

## Topology

```
                  ┌──────────────────────────────────────────────┐
                  │             SEED (feed.json, .eml, .pdf)     │
                  └──────────────────┬───────────────────────────┘
                                     │
                          ┌──────────▼──────────┐
                          │  Stage 1: Intake     │
                          │  (parsers.py,        │
                          │   store.py — SQLite) │
                          └──────────┬──────────┘
                                     │ raw records
                          ┌──────────▼──────────┐
                          │ Stage 2: Orchestration│
                          │  ┌──────────────┐    │
                          │  │ Normalizer   │─── │── SCHEMA_DRIFT(B)
                          │  │ (field_aliases.json)│
                          │  └──────┬───────┘    │
                          │  ┌──────▼───────┐    │
                          │  │ Detectors    │    │
                          │  │ STALE, MISSING_INPUT,  │
                          │  │ OUTLIER(IQR), │    │
                          │  │ INJECTION_BLOCKED,    │
                          │  │ UNVERIFIED_ANOMALY    │── Class A → Exception Queue
                          │  └──────┬───────┘    │
                          │  ┌──────▼───────┐    │
                          │  │ Superseded   │─── │── SUPERSEDED_VERSION(B)
                          │  │ Version Res. │    │
                          │  └──────────────┘    │
                          └──────────┬──────────┘
                                     │ clean records
                          ┌──────────▼──────────────────────────┐
                          │  Stage 3-5: Agent Fleet              │
                          │                                     │
                          │  Orchestrator_Agent ──can_call──►    │
                          │    │  (typed contract)               │
                          │    │                                 │
                          │    ├──► Worker_Agent                 │
                          │    │    │  (model router: cheap→strong→abstain)
                          │    │    │  writes transcript          │
                          │    │    ▼                             │
                          │    │  Verifier_Agent                  │
                          │    │    │  (agent-checks-agent)       │
                          │    │    │  verdict: pass/fail         │
                          │    │    ▼                             │
                          │    │  Approval_Agent                  │
                          │    │    │  (state machine:            │
                          │    │    │   draft→in_review→approved→delivered)
                          │    │    │  + CASE_ID amendment gate   │
                          │    │    ▼                             │
                          │    │  Exception Queue                 │
                          │    │  (AGENT_HALLUCINATION,           │
                          │    │   AGENT_LOOP, AGENT_MALFORMED,   │
                          │    │   BUDGET_EXCEEDED, LOW_CONFIDENCE)│
                          │                                     │
                          └──────────┬──────────────────────────┘
                                     │
                          ┌──────────▼──────────┐
                          │  out/audit.json      │
                          │  out/package/        │
                          │  out/exception_queue.json │
                          │  transcripts/        │
                          └─────────────────────┘
```

## Agent Contracts

| Agent | Role | Models | can_call |
|---|---|---|---|
| orchestrator_agent | orchestrator | gpt-4o-mini, claude-3-5-haiku, gemini-2.5-flash | worker_agent, verifier_agent, approval_agent |
| worker_agent | worker | gpt-4o-mini, claude-3-5-haiku, gemini-2.5-flash, gpt-4o, claude-sonnet-4 | verifier_agent |
| verifier_agent | verifier | gpt-4o-mini | (none) |
| approval_agent | operator | (none — rule-based) | (none) |

## Data Flow

1. **Intake** parses 3 source formats → SQLite store (keyed by source_version_hash)
2. **Orchestration** normalizes fields, runs 5 rule-based detectors, resolves superseded versions → exception queue
3. **Worker Agent** drafts branded output via LLM (model router: cheap → strong → abstain)
4. **Verifier Agent** independently checks Worker output → fail records get AGENT_HALLUCINATION/MALFORMED
5. **Approval Agent** runs state machine; records ≥ amendment threshold need role sign-off
6. **Audit Builder** writes append-only event log, agent traces, cost summary, output package hash

## Key Design Decisions

- **SQLite over JSONL:** ACID transactions, atomic UPSERT for idempotency
- **IQR k=3.0 outlier detection:** Tukey's extreme outlier fence — generalizes to any numeric distribution
- **REPLAY_LLM=true for dev:** Committed transcripts in `transcripts/` provide deterministic, offline pipeline runs
- **No god-function:** 4 distinct agents with typed contracts; each independently testable
- **Cost tracking:** Per-call cost from token counts × model pricing; aggregated in audit bundle
