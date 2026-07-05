# Stage 1 — Intake Implementation Report

## Summary
Stage 1 (Sources/Intake) of the CEDX Tiny Agent Fleet is implemented and tested. It reads
all three source formats from `SEED_DIR`, extracts a unified record schema, computes a
source-version hash for provenance, and persists records to a SQLite store with
idempotent upserts.

---

## What was implemented

### 1. Project structure under `cedx_MAP/`
```
cedx_MAP/
├── src/cedx/
│   ├── __init__.py
│   ├── models/
│   │   ├── __init__.py
│   │   └── record.py          # Record dataclass + reason/status enums
│   ├── utils/
│   │   ├── __init__.py
│   │   └── hashing.py         # canonical_json + sha256 helpers
│   └── intake/
│       ├── __init__.py
│       ├── parsers.py         # feed.json, .eml, .pdf parsers
│       ├── store.py           # SQLite-backed RecordStore
│       └── intake.py          # Stage 1 orchestrator
├── config/
│   └── field_aliases.json     # canonical -> aliases map for schema-drift handling
├── scripts/
│   └── run_intake.py          # CLI entry point
├── tests/
│   ├── __init__.py
│   └── test_intake.py         # 7 unit tests
└── docs/
    └── STAGE1_REPORT.md       # this file
```

### 2. Source parsing
- **`feed.json`**: parses the 16 structured JSON records.
- **`.eml`**: uses Python's `email` module, extracts `text/plain` body, then runs a
  generic `Key: value` extractor.
- **`.pdf`**: uses `pypdf` to extract text, then the same generic extractor.

The generic extractor:
- Preserves **original keys** in `Record.raw_fields` (required for Stage 2 schema-drift
  logging).
- Maps known aliases to canonical fields via `config/field_aliases.json`.
- Coerces `amount` to int/float and `version` to int.
- Supports multi-line notes.

### 3. Unified `Record` model
Fields populated by Intake:
- `id`, `owner`, `deadline`, `category`, `amount`, `notes`, `version`
- `source_format` (`feed` / `eml` / `pdf`)
- `source_path`
- `source_version_hash` — deterministic `sha256:` of source format + path + raw fields
- `raw_fields` — original extracted key/value pairs
- `intake_at` — ISO timestamp

### 4. Persistent store (`RecordStore`)
- SQLite table keyed by `source_version_hash`.
- `UPSERT` semantics guarantee **idempotency**: re-running intake on the same seed does
  not duplicate records. This directly supports the `probe-idempotency` and
  `probe-crash` (resumability) probes.
- Indexed by `id` for fast superseded-version lookups in Stage 2.

### 5. Environment awareness
- Reads `SEED_DIR` env var (defaults to `seed`).
- Reads `STORE_PATH` env var (defaults to `out/records.db`).
- Reads `ALIASES_PATH` env var for custom alias maps.

---

## Test results

```bash
PYTHONPATH=cedx_MAP/.pip:cedx_MAP/lib:cedx_MAP/src \
  python3 cedx_MAP/tests/test_intake.py
```
Output:
```
All intake tests passed.
```

Tests cover:
1. Feed JSON parsing (16 records).
2. EML injection record parsing (REC-014).
3. PDF parsing (REC-007).
4. Schema-drift alias handling (REC-016 `Value` → `amount`).
5. Store idempotency (duplicate upserts do not increase count).
6. Full end-to-end intake on dev seed (23 records total).
7. Deterministic source-version hashing.

Full-seed intake output:
```json
{
  "seed_dir": "seed",
  "records_stored": 23,
  "by_format": {
    "feed": 16,
    "eml": 3,
    "pdf": 4
  },
  "store_path": "out/records.db"
}
```

Idempotency check: running intake twice leaves the count at **23**.

---

## Seed observations (relevant for Stage 2)

| Record | Problem | How Stage 1 captured it |
|---|---|---|
| REC-011 | `STALE` | deadline `2026-06-01` stored as-is; Stage 2 compares to `PIPELINE_NOW` |
| REC-012 | `MISSING_INPUT` | `amount: None` stored |
| REC-013 | `OUTLIER` | `amount: 250000` stored; Stage 2 will use robust stats |
| REC-014 | `INJECTION_BLOCKED` | notes captured verbatim |
| REC-015 | `LOW_CONFIDENCE` candidate | notes: "Category says INTAKE but body describes a renewal AND a report" |
| REC-016 | `SCHEMA_DRIFT` | raw key `Value` preserved; canonical `amount` = 4750 |
| REC-017 | `SUPERSEDED_VERSION` | both v1 (feed) and v2 (pdf) stored keyed by different hashes |
| REC-021 | `LOW_CONFIDENCE` candidate | `category: ?` + ambiguous notes |
| REC-022 | `UNVERIFIED_ANOMALY` candidate | `amount: 5300` but notes claim real number is 38000 |

All required data-layer reason codes for the dev seed are present.

---

## Grading-constraint compliance

| Constraint | Status | Evidence |
|---|---|---|
| Parse BOTH formats (JSON + email + PDF) | ✅ | `parse_feed_json`, `parse_eml`, `parse_pdf` |
| Persist records, no in-memory arrays | ✅ | SQLite `RecordStore` |
| `owner` + `deadline` preserved | ✅ | Record model |
| Source-version hash per record | ✅ | `Record.compute_source_version_hash()` |
| Do not edit `/seed` | ✅ | Only read operations; `.gitignore` keeps `out/` uncommitted |
| Idempotent re-runs | ✅ | `UPSERT` on `source_version_hash` |
| Supports `SEED_DIR` env var | ✅ | `run_intake()` and CLI |
| Schema-drift raw fields preserved | ✅ | `Record.raw_fields` keeps original keys like `Value` |

---

## Local dependency setup

The system Python had no `pip`/`venv`. With user approval, we bootstrapped pip locally:
- `cedx_MAP/.pip/` — local pip installation
- `cedx_MAP/lib/` — project-local packages (`pypdf`, `jsonschema` + deps)
- `cedx_MAP/.tools/` — bootstrap script

Both `.pip/`, `lib/`, `.tools/` are excluded by `.gitignore`. The final `Dockerfile` will
install these normally via `pip install -r requirements.txt`.

---

## Known limitations / next-stage work

1. **Text-only PDFs**: `pypdf` extracts embedded text. If the held-out seed includes
   scanned/image PDFs, OCR (e.g. `pdf2image` + Tesseract or a vision model) will be added
   in a later stage.
2. **HTML emails**: the current `.eml` parser reads `text/plain`. HTML-only emails would
   need an HTML-to-text step.
3. **Field extraction heuristic**: the `Key: value` regex works for the dev seed. If the
   held-out seed uses tables, forms, or prose, a small LLM-based normalizer can be added
   in Stage 2 without changing Intake's contract (it will still receive raw text and
   output canonical `Record` fields).
4. **Makefile wiring**: `make demo` is not yet wired; that happens after Stages 2–5 are
   built.

---

## How to run Stage 1

```bash
# Default seed dir
PYTHONPATH=cedx_MAP/.pip:cedx_MAP/lib:cedx_MAP/src \
  python3 cedx_MAP/scripts/run_intake.py

# Custom seed dir
SEED_DIR=/path/to/seed \
PYTHONPATH=cedx_MAP/.pip:cedx_MAP/lib:cedx_MAP/src \
  python3 cedx_MAP/scripts/run_intake.py

# Run tests
PYTHONPATH=cedx_MAP/.pip:cedx_MAP/lib:cedx_MAP/src \
  python3 cedx_MAP/tests/test_intake.py
```

---

## Next step

Proceed to **Stage 2 — Orchestration (Normalize + Exception Queue)**: build the
rule-based detectors (STALE, MISSING_INPUT, OUTLIER, INJECTION_BLOCKED, LOW_CONFIDENCE,
UNVERIFIED_ANOMALY) and the Class-B auto-resolvers (SCHEMA_DRIFT, SUPERSEDED_VERSION),
outputting the exception queue and marking records for Assembly.
