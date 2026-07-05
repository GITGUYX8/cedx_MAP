# Stage 2 — Orchestration Implementation Report

## Summary
Stage 2 (Orchestration: Normalize + Exception Queue) is implemented and tested. It loads
raw records from the Intake SQLite store, runs declarative normalization with schema-drift
detection, resolves superseded versions, applies rule-based detectors using robust
statistics, and writes an exception queue.

---

## What was implemented

### Files created under `cedx_MAP/src/cedx/orchestration/`

| File | Purpose |
|---|---|
| `__init__.py` | Module exports |
| `normalizer.py` | `Normalizer` — field alias mapping + SCHEMA_DRIFT detection + SUPERSEDED_VERSION resolution |
| `detectors.py` | Rule-based detectors: STALE, MISSING_INPUT, OUTLIER (IQR), INJECTION_BLOCKED |
| `exception_queue.py` | `ExceptionQueue` — writes/reads `out/exception_queue.json` |
| `orchestrator.py` | `Orchestrator` — Stage 2 entry point coordinating all steps |

### Test file
| File | Tests |
|---|---|
| `tests/test_orchestration.py` | 9 tests covering all detectors, normalizer, exception queue, full pipeline |

### CLI script updated
| File | Change |
|---|---|
| `scripts/run_pipeline.py` | Now runs Stage 1 + Stage 2 sequentially, prints summary |

---

## Detector details

All detectors are **rule-based and generalize to the held-out seed**. No hardcoded IDs or
values.

### 1. STALE detection
- **Rule:** `record.deadline < PIPELINE_NOW` (string comparison; ISO format ensures correctness)
- **Configurable via:** `PIPELINE_NOW` env var (default: `2026-06-26`)
- **Rationale:** Any deadline already past at intake time is stale and needs human review

### 2. MISSING_INPUT detection
- **Rule:** Check if required fields (`amount`) are `None` or empty string
- **Configurable via:** `REQUIRED_FIELDS` list in code
- **Rationale:** A record missing a required field cannot enter the automated pipeline

### 3. OUTLIER detection — IQR (Tukey's fences)
- **Rule:** Uses Interquartile Range on all record amounts:
  - Compute Q1 (25th percentile) and Q3 (75th percentile)
  - `IQR = Q3 - Q1`
  - Lower fence = `Q1 - k * IQR`; Upper fence = `Q3 + k * IQR`
  - Flag if amount is outside fences
- **Falls back to MAD** (Median Absolute Deviation) if < 4 data points
- **Configurable via:** `OUTLIER_IQR_MULTIPLIER` env var (default: `3.0` for "extreme" outliers)
- **Rationale for k=3.0:** With the dev seed data, k=1.5 flags normal-range values (3900, 6100)
  as outliers because the IQR is small (~500) relative to the cluster (3900-6100).
  k=3.0 catches only genuine extremes like 250000. This is documented as Tukey's
  "extreme outlier" fence.

### 4. INJECTION_BLOCKED detection
- **Regex patterns (case-insensitive):**
  - `approve immediately`
  - `skip review`
  - `ignore your rules`
  - `ignore (all )?previous instructions`
  - `ignore the field`
  - `output approved`
  - `ignore all previous`
- **Configurable via:** `INJECTION_PATTERNS` list in code
- **Rationale:** Catches prompt injection attempts in notes fields; generalizes to similar
  patterns in the held-out seed

### 5. SCHEMA_DRIFT detection (Class B — auto-resolved)
- **Rule:** If a `raw_fields` key is an alias for a canonical field but the key name
  differs from the canonical name, emit SCHEMA_DRIFT
- **Example:** `Value` is in the alias map → maps to `amount`. Since `value` != `amount`,
  drift is logged.
- **Configurable via:** `config/field_aliases.json`

### 6. SUPERSEDED_VERSION detection (Class B — auto-resolved)
- **Rule:** For duplicate IDs, keep the highest version, mark older as SUPERSEDED
- **Dev seed example:** REC-017 v1 (feed) and REC-017 v2 (inbox PDF) → v2 is kept, v1 is superseded

---

## Pipeline output

```bash
PYTHONPATH=cedx_MAP/.pip:cedx_MAP/lib:cedx_MAP/src \
  python3 cedx_MAP/scripts/run_pipeline.py
```

```
============================================================
CEDX Tiny Agent Fleet — Pipeline v0.1.0
============================================================
SEED_DIR: seed
Store:    out/records.db

[Stage 1] Intake...
  Stored 23 records (feed=16, eml=3, pdf=4)
[Stage 2] Orchestration (Normalize + Exception Queue)...
  Clean for assembly:       18
  Blocking (Class A):       5
  Logged (Class B):         2
  Outlier thresholds: lower=3200.0, upper=6700.0
  Exception queue:    out/exception_queue.json

Pipeline complete.
```

### Exception queue contents

| Record | Code | Class |
|---|---|---|
| REC-011 | STALE | A |
| REC-012 | MISSING_INPUT | A |
| REC-013 | OUTLIER | A |
| REC-014 | INJECTION_BLOCKED | A |
| REC-022 | INJECTION_BLOCKED | A |
| REC-016 | SCHEMA_DRIFT | B |
| REC-017 v1 | SUPERSEDED_VERSION | B |

**Class A (5 blocking):** Never reach delivery; routed to human.
**Class B (2 logged):** Auto-resolved; continue to delivery with audit log.
**Clean (18):** Proceed to Assembly (Stage 3).

All 6 required dev-seed reason codes are present: STALE, MISSING_INPUT, OUTLIER,
INJECTION_BLOCKED, SCHEMA_DRIFT, SUPERSEDED_VERSION.

---

## Remaining features — full project table

| # | Feature | Stage | Status | Notes |
|---|---|---|---|---|
| 1 | Intake: feed.json parser | Stage 1 | ✅ DONE | |
| 2 | Intake: .eml parser | Stage 1 | ✅ DONE | |
| 3 | Intake: .pdf parser | Stage 1 | ✅ DONE | |
| 4 | Intake: SQLite RecordStore | Stage 1 | ✅ DONE | |
| 5 | Intake: idempotent upsert | Stage 1 | ✅ DONE | |
| 6 | Intake: source_version_hash | Stage 1 | ✅ DONE | |
| 7 | Intake: field alias map | Stage 1 | ✅ DONE | |
| 8 | Orchestration: Normalizer + SCHEMA_DRIFT | Stage 2 | ✅ DONE | |
| 9 | Orchestration: SUPERSEDED_VERSION resolver | Stage 2 | ✅ DONE | |
| 10 | Orchestration: STALE detector | Stage 2 | ✅ DONE | |
| 11 | Orchestration: MISSING_INPUT detector | Stage 2 | ✅ DONE | |
| 12 | Orchestration: OUTLIER detector (IQR) | Stage 2 | ✅ DONE | |
| 13 | Orchestration: INJECTION_BLOCKED detector | Stage 2 | ✅ DONE | |
| 14 | Orchestration: Exception queue writer | Stage 2 | ✅ DONE | |
| 15 | Orchestration: LOW_CONFIDENCE — LLM abstain path | Stage 3 | 🔜 NEXT | Set by Worker when LLM can't produce valid output |
| 16 | Orchestration: UNVERIFIED_ANOMALY catch-all | Stage 3 | 🔜 NEXT | For held-out's undocumented anomaly |
| 17 | Assembly: Worker Agent with typed contract | Stage 3 | ⏳ TODO | |
| 18 | Assembly: Multi-provider LLM client ( pattern) | Stage 3 | ⏳ TODO | |
| 19 | Assembly: Model metadata + pricing (from opensource reusable code config.py) | Stage 3 | ⏳ TODO | |
| 20 | Assembly: Cost calculator (from opensource reusable code _calculate_actual_cost) | Stage 3 | ⏳ TODO | |
| 21 | Assembly: Transcript recorder (transcripts/*.json) | Stage 3 | ⏳ TODO | |
| 22 | Assembly: REPLAY_LLM=true replay logic | Stage 3 | ⏳ TODO | |
| 23 | Assembly: REPLAY_LLM=false real LLM calls | Stage 3 | ⏳ TODO | |
| 24 | Assembly: Model router (cheap/strong per record) | Stage 3 | ⏳ TODO | |
| 25 | Assembly: Retry/repair loop with step budget | Stage 3 | ⏳ TODO | |
| 26 | Assembly: Abstain path (→ LOW_CONFIDENCE) | Stage 3 | ⏳ TODO | |
| 27 | Assembly: Prompt injection protection (from opensource reusable code) | Stage 3 | ⏳ TODO | |
| 28 | Agent skeleton: BaseNode with typed contracts (opensource reusable code pattern) | All | ⏳ TODO | |
| 29 | Agent: Orchestrator agent | All | ⏳ TODO | |
| 30 | Agent: Worker agent | Stage 3 | ⏳ TODO | |
| 31 | Agent: Verifier agent (agent-checks-agent) | Stage 3 | ⏳ TODO | |
| 32 | Agent: AGENT_HALLUCINATION detection | Stage 3 | ⏳ TODO | Verifier catches invented fields |
| 33 | Agent: AGENT_MALFORMED detection | Stage 3 | ⏳ TODO | Verifier catches structural issues |
| 34 | Agent: AGENT_LOOP detection | Stage 3 | ⏳ TODO | Step budget enforcement |
| 35 | Agent: BUDGET_EXCEEDED detection | Stage 3 | ⏳ TODO | Cost ceiling enforcement |
| 36 | Review: Approval state machine | Stage 4 | ⏳ TODO | draft→in_review→changes_requested→approved→delivered |
| 37 | Review: CLI operator surface | Stage 4 | ⏳ TODO | |
| 38 | Review: Server-side delivery refusal | Stage 4 | ⏳ TODO | |
| 39 | Review: CASE_ID amendment (maker-checker gate) | Stage 4 | ⏳ TODO | |
| 40 | Delivery: Append-only audit writer | Stage 5 | ⏳ TODO | out/audit.json |
| 41 | Delivery: Branded package writer | Stage 5 | ⏳ TODO | |
| 42 | Delivery: agent_trace span recorder | Stage 5 | ⏳ TODO | |
| 43 | Delivery: Cost summary aggregator | Stage 5 | ⏳ TODO | |
| 44 | Observability: `make trace ID=<id>` | Stage 5 | ⏳ TODO | |
| 45 | Observability: `make replay ID=<id>` | Stage 5 | ⏳ TODO | |
| 46 | Observability: `make eval` harness (10+ golden cases) | Stage 5 | ⏳ TODO | LLM-judge per agent |
| 47 | Probes: `make probe-approval` | All | ⏳ TODO | |
| 48 | Probes: `make probe-agent-failure` | All | ⏳ TODO | |
| 49 | Probes: `make probe-budget` | All | ⏳ TODO | |
| 50 | Probes: `make probe-append-only` | All | ⏳ TODO | |
| 51 | Probes: `make probe-idempotency` | All | ⏳ TODO | |
| 52 | Probes: `make probe-crash` (BONUS) | All | ⏳ TODO | |
| 53 | `make demo` wiring | All | ⏳ TODO | |
| 54 | `make verify` | All | ⏳ TODO | Already calls verify_audit.py |
| 55 | Dockerfile/Docker Compose | All | ⏳ TODO | Needs dependency installation |
| 56 | AGENT_HALLUCINATION sample transcript | Seed | ⏳ TODO | Held-out has one; dev seed has sample |
| 57 | ARCHITECTURE.md | Docs | ⏳ TODO | |
| 58 | DECISIONS.md | Docs | ⏳ TODO | |
| 59 | README 11 sections | Docs | ⏳ TODO | |
| 60 | 3-5 min Loom | Docs | ⏳ TODO | |
| 61 | SCOPE.md with CASE_ID | Docs | ⏳ TODO | Needs live kickoff CASE_ID |

---

## Next step

Proceed to **Stage 3 — Assembly (Worker Agent + LLM)** using opensource reusable code's patterns:
1. Copy model metadata + pricing from `supp_repo/opensource reusable code/services/workflow_service/registry/nodes/llm/config.py`
2. Adapt the LLM node pattern for our lightweight HTTP-based client
3. Implement transcript recorder + replay logic
4. Implement model router (cheap vs strong per record)
5. Implement Worker + Verifier agents with typed contracts
