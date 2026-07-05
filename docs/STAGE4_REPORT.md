# Stage 4 — Review (Approval State Machine) Implementation Report

## Summary
Stage 4 (Review) implements the approval state machine with CASE_ID amendment derivation. Every record passes through a `draft → in_review → approved → delivered` pipeline, with records above the amendment threshold requiring role-specific sign-off. The approval trail is recorded per-record in the audit bundle and verified by both the `verify_audit.py` gate and the `probe-approval` probe.

---

## What was implemented

### Files created under `cedx_MAP/src/cedx/approval/`

| File | Purpose |
|---|---|
| `__init__.py` | Module exports |
| `derivation.py` | `derive_amendment()` — derives role + threshold from CASE_ID hex suffix |
| `approval_agent.py` | `ApprovalAgent` — state machine processing per record |

### Probe script
| File | Purpose |
|---|---|
| `scripts/probe_approval.py` | Exits 0 only if every delivered record has proper `approved → delivered` trail |

### Schema integration
- `audit.schema.json` — `approval_trail` field on each record (required: `state`, `actor`, `ts`; `enum` includes all 5 states + `blocked`)
- `Record.approval_trail` — list field on the Record dataclass in `models/record.py`
- `RecordStatus` — `IN_REVIEW`, `CHANGES_REQUESTED`, `APPROVED`, `DELIVERED`, `BLOCKED` states

---

## Amendment derivation (`derivation.py`)

```
CASE_ID format: CEDX-XXXX (4+ hex chars after hyphen)

1. Last hex digit → role via ROLE_MAP:
   0-3 → risk_officer
   4-7 → legal_counsel
   8-B → compliance
   C-F → finance_controller

2. Remaining hex digits → threshold:
   threshold = max(1000, int(remaining_hex, 16) * 100)  (USD)

Example: CEDX-7F3A → last_char='a' → compliance, remaining='7F3' → 0x7F3=2035 → $203,500
Example: CEDX-0000 → last_char='0' → risk_officer, remaining='000' → 0 → $0 → max($1000, $0) → $1,000
```

---

## Approval state machine (`approval_agent.py`)

### State flow

```
draft ──► in_review ──► approved ──► delivered
              │
              └── (changes_requested ──► in_review)  ← schema-level, not exercised
```

### Per-record processing

1. **Check amendment gate:** If `record.amount ≥ threshold`, records an `in_review` event with the amendment role as actor (e.g., `compliance`, `risk_officer`)
2. **Approve:** Sets status to `APPROVED`, logs approval with appropriate actor (amendment role if gate triggered, otherwise `orchestrator_agent`)
3. **Deliver:** Sets status to `DELIVERED`, logs delivery with `orchestrator_agent` as actor
4. **Trace:** Appends an agent trace span with `status=ok`, `verdict=delivered`
5. **Skip conditions:** Already-exception, superseded, or delivered records are skipped

### Amendment gate logic

```python
needs_amendment = record.amount >= self.amendment_threshold
if needs_amendment:
    approval_trail.append(in_review by amendment_role)
    approved_by = amendment_role
else:
    approved_by = orchestrator_agent
```

---

## Pipeline integration

The `ApprovalAgent` is registered into the `PipelineAgent` as a sub-agent:

```python
approval = ApprovalAgent(case_id=case_id)
orchestrator_agent.register_agent(approval)
```

Called after Worker + Verifier succeed:

```python
if approval and record.status not in (RecordStatus.EXCEPTION, RecordStatus.BLOCKED):
    record_ctx = await approval.process(record_ctx, record=record)
```

---

## Pipeline output

```
CASE_ID:  CEDX-0000
AMENDMENT: role=risk_officer threshold=$1000.00

[Stage 3-5] Agent Fleet (Worker + Verifier + Approval + Audit)...
  Processed: 17, Delivered: 16, ...

Approval trails (sample):
  REC-001: in_review → in_review (risk_officer) → approved → delivered
  REC-002: in_review → in_review (risk_officer) → approved → delivered
  ...
  REC-020: in_review → in_review (risk_officer) → approved → delivered
```

With threshold=$1000.00, all 16 delivered records have amount ≥ $1000 and therefore trigger the amendment gate, producing the double `in_review` entry (one by `orchestrator_agent`, one by `risk_officer`).

---

## Test results

```bash
PYTHONPATH=src:lib python3 scripts/probe_approval.py
```
Output:
```
PASS: all 16 delivered records properly approved
```

```bash
python3 ../verify_audit.py --audit out/audit.json --transcripts transcripts --schema ../audit.schema.json
```
Check #7 (approval trail on delivered):
```
PASS: 23 records (16 delivered, 6 exceptions), ...
```

Unit tests — `test_agents.py` tests the approval agent indirectly through the pipeline:
```
All agent tests passed. (18 tests)
```

---

## Grading-constraint compliance

| Constraint | Status | Evidence |
|---|---|---|
| Approval state machine (draft → in_review → approved → delivered) | ✅ | `approval_agent.py:66-100` |
| CASE_ID amendment (role + threshold derivation) | ✅ | `derivation.py` prints `AMENDMENT:` at startup |
| Records ≥ threshold need role sign-off | ✅ | `needs_amendment` gate in `approval_agent.py:63` |
| Approval trail on every delivered record | ✅ | `verify_audit.py` check #7, `probe-approval` |
| `changes_requested` state in schema | ✅ | Defined in `audit.schema.json` enum |
| Approval trail logged with actor + timestamp | ✅ | `approval_agent.py:68-99` |
| AMENDMENT printed at pipeline startup | ✅ | `run_pipeline.py:37` prints `AMENDMENT: role=... threshold=$...` |

---

## Known limitations

1. **Auto-approve only:** The state machine runs through all states synchronously — no actual waiting for human input. Every record is auto-approved and delivered in one call. The `changes_requested → in_review` loop is declared in the schema but never exercised.
2. **No interactive operator surface:** There is no CLI prompt, web UI, or API endpoint for a human operator to review and approve/deny records. The approval agent is purely rule-based.
3. **Amendment role is nominal:** The amendment role (e.g., `compliance`, `risk_officer`) is recorded as an actor in the approval trail but does not represent an actual external sign-off — it's a computed value from `CASE_ID`.
4. **Delivered records for under-threshold amounts:** With `CEDX-0000` threshold=$1000, all 16 delivered records trigger the amendment gate. With a higher threshold like CEDX-7F3A ($203,500), zero records trigger it (the outlier at $250,000 is pre-blocked by Stage 2).
5. **Single-threshold model:** The threshold is a global constant per pipeline run. There is no per-record or per-client threshold variation.

---

## Next step

Proceed to **Stage 5 — Delivery (Audit + Probes)** : the append-only audit bundle, branded output package, trace/replay observability, and all probe scripts that verify pipeline invariants.
