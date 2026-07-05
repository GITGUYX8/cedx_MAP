# SCOPE — Live tracer checkpoint

- **Candidate name:** Cedx Tiny Kit
- **CASE_ID (assigned live):** CEDX-7F3A
- **Industry chosen:** Financial services — work-request intake, routing, and branded delivery
- **Tier:** Standard (5 governed stages)
- **Stack / language:** Python 3.12, SQLite, JSON, OpenAI-compatible LLM API

## Amendment (compute from your CASE_ID)

- Last hex digit → role (0-3: risk_officer, 4-7: legal_counsel, 8-B: compliance, C-F: finance_controller)
- Remaining hex digits × 100 → threshold (min $1000)

- **My role R:** compliance
- **My threshold T:** $203500.00

## What I will build (the 5 governed stages)

- [x] Sources/Intake (parse feed.json + inbox PDF/email)
- [x] Orchestration (declarative normalize + exception queue, all reason codes)
- [x] Assembly (LLM structured output + abstain path)
- [x] Review (operator surface + approval state machine + my CASE_ID amendment)
- [x] Delivery (branded package + append-only audit + replay)

## What I will deliberately NOT build (and why)

- **Web dashboard / UI:** Zero grading points; time better spent on core pipeline correctness.
- **Real LLM API integration for testing:** Mock/replay mode provides deterministic results without API keys. Real mode only needed for `REPLAY_LLM=false`.
- **Anthropic/Gemini native SDK clients:** OpenAI-compatible API covers all providers via `LLM_BASE_URL`. Native SDKs add dependency weight without functional benefit.
- **Production auth/queuing:** Not required for the 5-stage pipeline specification.
- **`supp_repo/mission-control/` integration:** It's a heavyweight Next.js 16 dashboard with no CEDX integration layer — zero grading benefit.
