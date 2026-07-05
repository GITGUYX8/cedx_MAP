"""Exception queue writer for Stage 2.

Produces out/exception_queue.json with all records that have a
reason_code (both Class A blocking and Class B auto-resolved).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cedx.models.record import Record, RecordStatus


class ExceptionQueue:
    """Manages the exception queue output."""

    def __init__(self, output_path: str | Path = "out/exception_queue.json"):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, records: list[Record]) -> dict[str, Any]:
        """Write all records with reason codes to the exception queue.

        Returns a summary dict.
        """
        exceptions = []
        for r in records:
            if r.reason_code:
                exceptions.append({
                    "id": r.id,
                    "version": r.version,
                    "source_format": r.source_format,
                    "status": r.status,
                    "reason_code": r.reason_code,
                    "reason_class": r.reason_class,
                    "notes": r.notes,
                    "amount": r.amount,
                    "deadline": r.deadline,
                    "category": r.category,
                })

        with open(self.output_path, "w", encoding="utf-8") as f:
            json.dump(exceptions, f, indent=2, default=str)

        return {
            "path": str(self.output_path),
            "exception_count": len(exceptions),
            "class_a": sum(1 for e in exceptions if e["reason_class"] == "A"),
            "class_b": sum(1 for e in exceptions if e["reason_class"] == "B"),
        }

    def load(self) -> list[dict[str, Any]]:
        """Load the current exception queue."""
        if not self.output_path.exists():
            return []
        with open(self.output_path, "r", encoding="utf-8") as f:
            return json.load(f)
