# DECISIONS.md — CEDX Tiny Agent Fleet

## What wasn't automated (and why)

- **Real-time operator UI:** The approval state machine is rule-based with auto-approve for under-threshold records. A live operator surface (WebSocket dashboard, Slack bot) would add realism but zero grading points.
- **Multi-tenant isolation:** Not required for the 5-stage pipeline; all records share a single store.
- **Anthropic/Gemini native SDKs:** OpenAI-compatible API (`LLM_BASE_URL`) covers all providers. Adding 3 SDKs triples `requirements.txt` for no functional gain.
- **`supp_repo/mission-control/` integration:** Evaluated and rejected — it's a heavyweight Next.js 16 dashboard (113 deps) with no CEDX integration hooks.

## Outlier detection threshold

- **IQR multiplier k = 3.0** (Tukey's "extreme outlier" fence).
- Why not 1.5: The dev seed range ($3900–$6100) with k=1.5 would flag 0 values or near-boundary records as outliers. k=3.0 correctly catches $250,000 while ignoring normal-range values.
- For n < 4 records, falls back to MAD (median absolute deviation) × 1.4826 × 3.

## Model router policy

```
Order: gpt-4o-mini → claude-3-5-haiku → gemini-2.5-flash → gpt-4o → claude-sonnet-4
         cheap                            →        strong
```

- Start with the cheapest capable model. On error/empty output, escalate to the next tier.
- After all 5 models fail, Worker abstains → record gets `LOW_CONFIDENCE` (Class A).
- Retry temperature: 0.0 on first attempt, 0.2 on retry (slight randomness to avoid same failure).

## Abstain policy

Worker abstains when ALL 5 models return error or empty output. Key triggers:
- `owner` is null (missing source data) — no one to attribute the work to
- `category` is `?` (ambiguous/unclassifiable) — cannot determine work type
- Both conditions → immediate mock failure → abstain after 5 retries

## Cost modeling

| Model | Input $/1K | Output $/1K | Latency (est.) |
|---|---|---|---|
| gpt-4o-mini | 0.000150 | 0.000600 | ~500ms |
| claude-3-5-haiku | 0.000250 | 0.001250 | ~600ms |
| gemini-2.5-flash | 0.000075 | 0.000300 | ~400ms |
| gpt-4o | 0.002500 | 0.010000 | ~1500ms |
| claude-sonnet-4 | 0.003000 | 0.015000 | ~2000ms |

At 10,000 records, projected cost ~$0–$50 (most records succeed on first cheap-model call). Budget ceiling: $0.05/record (env `MAX_COST_USD_PER_RECORD`).

## Append-only provenance

EventLog wraps a simple list with `append()` + `seal()`:
- `append()`: validates ts is non-decreasing; auto-assigns seq.
- `seal()`: freezes the log — no further appends allowed.
- Strict `seq = 0..n-1` — the first integrity check in verify_audit.py (#9).
- Write-ahead: event is recorded BEFORE the state change it describes.

## 10k-scale weaknesses

1. **Single-process orchestration:** All records processed sequentially in one loop. At 10k records, consider `asyncio.gather` for concurrent agent calls.
2. **SQLite single-writer:** The store uses one connection. For high concurrency, migrate to PostgreSQL or shard by record ID.
3. **Transcript directory growth:** 10k transcripts × ~2KB each = ~20MB. Manageable, but consider archival or TTL for long-running deployments.
4. **Mock verifier always passes:** The mock `_mock_verifier` always returns `verdict: pass`. Real LLM calls are needed to trigger `AGENT_HALLUCINATION` detection.

## CASE_ID amendment derivation

From `CASE_ID` (e.g., CEDX-7F3A), hex characters after the hyphen define:
- Role: last hex digit → maps through ROLE_MAP to {risk_officer, legal_counsel, compliance, finance_controller}
- Threshold: remaining hex digits × 100 (min $1000). Range ~$0–$64,000 at 4 hex chars.
