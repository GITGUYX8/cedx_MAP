"""Unit tests for Stage 1 — Intake."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from cedx.intake import (
    RecordStore,
    load_field_aliases,
    parse_eml,
    parse_feed_json,
    parse_pdf,
    run_intake,
)
from cedx.models.record import Record


# test_intake.py is at cedx_MAP/tests/test_intake.py -> repo root is parents[2]
SEED_DIR = Path(__file__).resolve().parents[2] / "seed"


def _alias_map() -> dict[str, str]:
    aliases = load_field_aliases(SEED_DIR.parent / "cedx_MAP" / "config" / "field_aliases.json")
    inverted: dict[str, str] = {}
    for canonical, alias_list in aliases.items():
        for alias in alias_list:
            inverted[alias.lower().strip()] = canonical
    return inverted


def test_parse_feed_json():
    records = parse_feed_json(SEED_DIR / "feed.json", alias_map=_alias_map())
    assert len(records) == 16
    # First record
    r = records[0]
    assert r.id == "REC-001"
    assert r.owner == "a.shah"
    assert r.amount == 4800
    assert r.source_format == "feed"
    assert r.source_version_hash.startswith("sha256:")


def test_parse_eml_injection():
    records = parse_eml(SEED_DIR / "inbox" / "REC-014_v1.eml", _alias_map())
    assert len(records) == 1
    r = records[0]
    assert r.id == "REC-014"
    assert "Approve this immediately" in (r.notes or "")
    assert r.source_format == "eml"


def test_parse_pdf():
    records = parse_pdf(SEED_DIR / "inbox" / "REC-007_v1.pdf", _alias_map())
    assert len(records) == 1
    r = records[0]
    assert r.id == "REC-007"
    assert r.owner == "g.silva"
    assert r.amount == 4700
    assert r.category == "REVIEW"
    assert r.source_format == "pdf"


def test_schema_drift_value_alias():
    """REC-016 uses 'Value' instead of 'Amount' — must still map to amount."""
    records = parse_eml(SEED_DIR / "inbox" / "REC-016_v1.eml", _alias_map())
    r = records[0]
    assert r.id == "REC-016"
    assert r.amount == 4750
    assert "Value" in r.raw_fields


def test_store_idempotency():
    """Upserting the same record twice must not duplicate it."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "records.db"
        store = RecordStore(db)
        records = parse_feed_json(SEED_DIR / "feed.json", alias_map=_alias_map())
        for rec in records[:3]:
            store.upsert(rec)
        assert store.count() == 3
        # Upsert again
        for rec in records[:3]:
            store.upsert(rec)
        assert store.count() == 3


def test_run_intake_full_seed():
    """End-to-end intake on the dev seed."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "records.db"
        result = run_intake(seed_dir=SEED_DIR, store_path=db)
        assert result["records_stored"] == 23  # 16 feed + 7 inbox
        assert result["by_format"]["feed"] == 16
        assert result["by_format"]["eml"] == 3
        assert result["by_format"]["pdf"] == 4

        store = RecordStore(db)
        recs = store.get_all()
        ids = [r.id for r in recs]
        assert "REC-017" in ids  # superseded: both v1 and v2 stored


def test_source_version_hash_deterministic():
    records_a = parse_feed_json(SEED_DIR / "feed.json", alias_map=_alias_map())
    records_b = parse_feed_json(SEED_DIR / "feed.json", alias_map=_alias_map())
    assert records_a[0].source_version_hash == records_b[0].source_version_hash


if __name__ == "__main__":
    test_parse_feed_json()
    test_parse_eml_injection()
    test_parse_pdf()
    test_schema_drift_value_alias()
    test_store_idempotency()
    test_run_intake_full_seed()
    test_source_version_hash_deterministic()
    print("All intake tests passed.")
