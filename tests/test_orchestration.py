"""Unit tests for Stage 2 — Orchestration (Normalize + Exception Queue)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from cedx.intake import parse_feed_json, parse_eml, parse_pdf, load_field_aliases, RecordStore, run_intake
from cedx.models.record import Record, ReasonCode, ReasonClass, RecordStatus
from cedx.orchestration import (
    Normalizer,
    OutlierDetector,
    ExceptionQueue,
    Orchestrator,
    run_orchestration,
)


SEED_DIR = Path(__file__).resolve().parents[2] / "seed"


def _alias_map():
    aliases = load_field_aliases(SEED_DIR.parent / "cedx_MAP" / "config" / "field_aliases.json")
    inverted = {}
    for canonical, alias_list in aliases.items():
        for alias in alias_list:
            inverted[alias.lower().strip()] = canonical
    return inverted


def _load_records():
    """Load all raw records from the dev seed."""
    records = []
    records.extend(parse_feed_json(SEED_DIR / "feed.json", alias_map=_alias_map()))
    for eml in sorted((SEED_DIR / "inbox").glob("*.eml")):
        records.extend(parse_eml(eml, _alias_map()))
    for pdf in sorted((SEED_DIR / "inbox").glob("*.pdf")):
        records.extend(parse_pdf(pdf, _alias_map()))
    return records


def test_stale_detector():
    """REC-011 has deadline 2026-06-01 which is before PIPELINE_NOW (2026-06-26)."""
    records = _load_records()
    rec = next(r for r in records if r.id == "REC-011")
    from cedx.orchestration.detectors import detect_stale
    assert detect_stale(rec) == ReasonCode.STALE


def test_missing_input_detector():
    """REC-012 has amount=None."""
    records = _load_records()
    rec = next(r for r in records if r.id == "REC-012")
    from cedx.orchestration.detectors import detect_missing_input
    assert detect_missing_input(rec) == ReasonCode.MISSING_INPUT


def test_outlier_detector():
    """REC-013 has amount=250000 which is an outlier vs normal ~5000."""
    records = _load_records()
    detector = OutlierDetector()
    detector.fit(records)
    rec = next(r for r in records if r.id == "REC-013")
    assert detector.is_outlier(rec)


def test_injection_detector():
    """REC-014 has injection text in notes."""
    records = _load_records()
    rec = next(r for r in records if r.id == "REC-014")
    from cedx.orchestration.detectors import detect_injection
    assert detect_injection(rec) == ReasonCode.INJECTION_BLOCKED


def test_normalizer_schema_drift():
    """REC-016 uses 'Value' instead of 'Amount' — SCHEMA_DRIFT detected."""
    records = _load_records()
    rec = next(r for r in records if r.id == "REC-016")
    normalizer = Normalizer()
    normalizer.normalize_record(rec)
    assert rec.reason_code == ReasonCode.SCHEMA_DRIFT
    assert rec.reason_class == ReasonClass.B
    assert rec.amount == 4750  # mapped from Value


def test_superseded_version():
    """REC-017 appears in feed (v1) and inbox PDF (v2) — v1 should be SUPERSEDED."""
    records = _load_records()
    normalizer = Normalizer()
    normalized = normalizer.normalize_batch(records)
    rec_v1 = next(r for r in normalized if r.id == "REC-017" and r.version == 1)
    rec_v2 = next(r for r in normalized if r.id == "REC-017" and r.version == 2)
    assert rec_v1.status == RecordStatus.SUPERSEDED
    assert rec_v1.reason_code == ReasonCode.SUPERSEDED_VERSION
    assert rec_v2.status != RecordStatus.SUPERSEDED


def test_full_orchestration():
    """Run the full Stage 2 pipeline on the dev seed."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "records.db"
        eq = Path(tmp) / "exception_queue.json"

        # First do intake
        run_intake(seed_dir=SEED_DIR, store_path=db)
        store = RecordStore(db)

        # Then orchestrate
        orchestrator = Orchestrator(
            store=store,
            exception_queue_path=eq,
        )
        result = orchestrator.run()

        assert result["status"] == "ok"
        assert result["records_loaded"] == 23

        # Check exceptions
        assert result["exceptions"]["blocking_class_a"] >= 4  # STALE, MISSING, OUTLIER, INJECTION
        assert result["exceptions"]["logged_class_b"] >= 2  # SCHEMA_DRIFT, SUPERSEDED_VERSION

        # Verify exception queue file exists
        assert eq.exists()
        eq_data = json.loads(eq.read_text())
        codes = {e["reason_code"] for e in eq_data}
        assert "STALE" in codes
        assert "MISSING_INPUT" in codes
        assert "OUTLIER" in codes
        assert "INJECTION_BLOCKED" in codes
        assert "SCHEMA_DRIFT" in codes
        assert "SUPERSEDED_VERSION" in codes


def test_exception_queue_writer():
    """ExceptionQueue properly writes and loads."""
    with tempfile.TemporaryDirectory() as tmp:
        eq_path = Path(tmp) / "exceptions.json"
        eq = ExceptionQueue(eq_path)

        records = _load_records()
        records[0].reason_code = ReasonCode.STALE
        records[0].reason_class = ReasonClass.A
        records[0].status = RecordStatus.EXCEPTION

        summary = eq.write(records)
        assert summary["exception_count"] == 1
        assert summary["class_a"] == 1

        loaded = eq.load()
        assert len(loaded) == 1
        assert loaded[0]["reason_code"] == "STALE"


def test_outlier_thresholds_generalizable():
    """Outlier thresholds should be data-driven, not hardcoded."""
    records = _load_records()
    detector = OutlierDetector()
    detector.fit(records)
    t = detector.thresholds()
    # Lower bound below normal range, upper bound above normal range but below outlier.
    assert 2000 < t["lower"] < 4000  # 3200 — below the cluster but above 0
    assert 6000 < t["upper"] < 10000  # 6700 — above normal cluster, below 250000
    assert t["multiplier"] == 3.0


if __name__ == "__main__":
    test_stale_detector()
    test_missing_input_detector()
    test_outlier_detector()
    test_injection_detector()
    test_normalizer_schema_drift()
    test_superseded_version()
    test_full_orchestration()
    test_exception_queue_writer()
    test_outlier_thresholds_generalizable()
    print("All orchestration tests passed.")
