"""Stage 2 orchestrator: normalize records and run detectors."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cedx.intake.store import RecordStore
from cedx.models.record import Record, RecordStatus
from cedx.orchestration.detectors import OutlierDetector, run_all_detectors
from cedx.orchestration.exception_queue import ExceptionQueue
from cedx.orchestration.normalizer import Normalizer


class Orchestrator:
    """Stage 2 — Orchestration.

    Steps:
      1. Load raw records from the store.
      2. Normalize fields + detect schema drift (Normalizer).
      3. Resolve superseded versions.
      4. Fit the outlier detector on all records.
      5. Run per-record detectors (STALE, MISSING_INPUT, OUTLIER, INJECTION).
      6. Write exception queue.
      7. Persist updated records back to the store.
      8. Return clean records (for Stage 3) and exception records.
    """

    def __init__(
        self,
        store: RecordStore,
        aliases_path: str | Path | None = None,
        exception_queue_path: str | Path = "out/exception_queue.json",
    ):
        self.store = store
        self.normalizer = Normalizer(aliases_path=aliases_path)
        self.exception_queue = ExceptionQueue(exception_queue_path)
        self.outlier_detector = OutlierDetector()

    def run(self) -> dict[str, Any]:
        """Execute the full orchestration pipeline.

        Returns:
            dict with summary statistics.
        """
        # 1. Load all raw records.
        raw_records = self.store.get_all()
        if not raw_records:
            return {
                "status": "no_records",
                "records_loaded": 0,
                "clean": 0,
                "exceptions": {"class_a": 0, "class_b": 0},
            }

        # 2. Normalize + resolve superseded.
        normalized = self.normalizer.normalize_batch(raw_records)

        # 3. Fit outlier detector on ALL records (needs full distribution).
        self.outlier_detector.fit(normalized)

        # 4. Run detectors.
        annotated = run_all_detectors(normalized, self.outlier_detector)

        # 5. Separate blocking (Class A) vs clean (Class B + no code).
        #    Class A records NEVER go to delivery (blocking).
        #    Class B records ARE logged in exception queue but CONTINUE to delivery.
        #    Clean records have no reason_code and continue to delivery.
        blocking_records: list[Record] = []
        clean_records: list[Record] = []  # includes Class B (auto-resolved)
        class_a_count = 0
        class_b_count = 0

        for record in annotated:
            if record.reason_code:
                if record.reason_class == "A":
                    blocking_records.append(record)
                    class_a_count += 1
                    record.status = RecordStatus.EXCEPTION
                elif record.reason_class == "B":
                    # Class B — auto-resolved, continues to delivery
                    class_b_count += 1
                    clean_records.append(record)
                    if not record.status:
                        record.status = RecordStatus.PENDING
                else:
                    clean_records.append(record)
            else:
                clean_records.append(record)

        # 6. Write exception queue.
        eq_summary = self.exception_queue.write(annotated)

        # 7. Persist updated records back to store.
        for record in annotated:
            self.store.upsert(record)

        outlier_thresholds = self.outlier_detector.thresholds()
        context_note = (
            _generate_context_note(annotated)
        )

        return {
            "status": "ok",
            "records_loaded": len(raw_records),
            "records_processed": len(annotated),
            "clean_for_assembly": len(clean_records),
            "exceptions": {
                "blocking_class_a": class_a_count,
                "logged_class_b": class_b_count,
            },
            "outlier_thresholds": outlier_thresholds,
            "exception_queue": eq_summary,
        }


def _generate_context_note(records: list[Record]) -> str:
    """Generate context note about the state of the orchestrated records."""
    codes = {}
    for r in records:
        if r.reason_code:
            codes[r.reason_code] = codes.get(r.reason_code, 0) + 1
    return json.dumps(codes, default=str)


def run_orchestration(
    seed_dir: str | Path | None = None,
    store_path: str | Path = "out/records.db",
    aliases_path: str | Path | None = None,
    exception_queue_path: str | Path = "out/exception_queue.json",
) -> dict[str, Any]:
    """Convenience entry point for scripts / Makefile."""
    store = RecordStore(store_path)
    orchestrator = Orchestrator(
        store=store,
        aliases_path=aliases_path,
        exception_queue_path=exception_queue_path,
    )
    return orchestrator.run()
