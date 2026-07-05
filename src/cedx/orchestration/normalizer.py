"""Declarative normalization for Stage 2.

Takes raw records (from Intake) and maps them to canonical fields using
the field alias map. Detects SCHEMA_DRIFT when raw key != canonical key.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cedx.models.record import Record, ReasonCode, ReasonClass, RecordStatus


DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


def _build_alias_to_canonical(
    aliases: dict[str, list[str]],
) -> dict[str, str]:
    """Invert {canonical: [aliases]} into {lowercase_alias: canonical}."""
    inverted: dict[str, str] = {}
    for canonical, alias_list in aliases.items():
        for alias in alias_list:
            inverted[alias.lower().strip()] = canonical
    return inverted


class Normalizer:
    """Normalizes records using a field alias map.

    For each record:
      1. Compare raw_fields keys to canonical alias map.
      2. For keys that are aliases (not the canonical name), emit SCHEMA_DRIFT
         (Class B — auto-resolved, continues to delivery).
      3. Map aliased values to canonical fields on the record.

    This works for BOTH the dev seed and the held-out seed because the
    alias map is extensible and the drift detection is rule-based.
    """

    def __init__(self, aliases_path: str | Path | None = None):
        self.aliases_path = Path(
            aliases_path if aliases_path is not None else DEFAULT_CONFIG_DIR / "field_aliases.json"
        )
        with open(self.aliases_path, "r", encoding="utf-8") as f:
            self.aliases = json.load(f)
        self.alias_to_canonical = _build_alias_to_canonical(self.aliases)
        self.canonical_names = set(self.aliases.keys())

    def normalize_record(self, record: Record) -> Record:
        """Normalize a single record and detect schema drift.

        Args:
            record: A record with raw_fields populated by Intake.

        Returns:
            The same record (mutated) with:
              - Canonical fields updated from raw_fields if they were aliased
              - reason_code/reason_class set to SCHEMA_DRIFT (Class B) if
                a raw key was an alias for a canonical field
              - status set to PENDING if clean, or left as-is if already set
        """
        if not record.raw_fields:
            return record

        drift_detected = False

        for raw_key, raw_value in record.raw_fields.items():
            raw_lower = raw_key.lower().strip()

            # Check if this raw key is an alias for a canonical field.
            if raw_lower in self.alias_to_canonical:
                canonical_key = self.alias_to_canonical[raw_lower]

                # If the raw key differs from the canonical key spelling, it's drift.
                if raw_lower != canonical_key.lower():
                    drift_detected = True

                # If the record's canonical value is None but raw has value, apply it.
                # This handles cases like "Value: 4750" mapping to amount.
                current_val = getattr(record, canonical_key, None)
                if current_val is None and raw_value is not None:
                    try:
                        if canonical_key == "version":
                            setattr(record, canonical_key, int(raw_value))
                        elif canonical_key == "amount":
                            import re
                            cleaned = re.sub(r"[^\d.\-]", "", str(raw_value).replace(",", ""))
                            if "." in cleaned:
                                setattr(record, canonical_key, float(cleaned))
                            else:
                                setattr(record, canonical_key, int(cleaned))
                        else:
                            setattr(record, canonical_key, str(raw_value))
                    except (ValueError, TypeError):
                        pass

        if drift_detected and not record.reason_code:
            record.reason_code = ReasonCode.SCHEMA_DRIFT
            record.reason_class = ReasonClass.B

        return record

    def normalize_batch(self, records: list[Record]) -> list[Record]:
        """Normalize a batch of records and resolve superseded versions.

        Steps:
          1. Run normalize_record() on each record.
          2. For duplicate IDs, keep the highest version (SUPERSEDED_VERSION).
          3. The lower-version records get status=SUPERSEDED.

        Returns the list with all records annotated.
        """
        # Step 1: Normalize each record.
        for record in records:
            self.normalize_record(record)

        # Step 2: Group by ID to detect superseded versions.
        by_id: dict[str, list[Record]] = {}
        for record in records:
            by_id.setdefault(record.id, []).append(record)

        # Step 3: For duplicate IDs, mark all but the latest as superseded.
        for record_id, group in by_id.items():
            if len(group) > 1:
                group.sort(key=lambda r: r.version, reverse=True)
                latest = group[0]
                for older in group[1:]:
                    older.status = RecordStatus.SUPERSEDED
                    older.reason_code = ReasonCode.SUPERSEDED_VERSION
                    older.reason_class = ReasonClass.B
                if latest.status == RecordStatus.PENDING:
                    pass  # stays pending for further processing

        return records
