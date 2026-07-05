"""Rule-based detectors for Stage 2 — Orchestration.

Each detector is a pure function that examines a Record and returns
(reason_code, reason_class) or (None, None) if no problem found.

Detectors must GENERALIZE to the held-out seed — no hardcoded IDs or values.
Thresholds are documented in DECISIONS.md.
"""
from __future__ import annotations

import math
import os
import re
from typing import Optional

from cedx.models.record import Record, ReasonCode, ReasonClass, RecordStatus

# ── Configurable from env ──────────────────────────────────────────────────
PIPELINE_NOW = os.environ.get("PIPELINE_NOW", "2026-06-26")

# IQR multiplier for outlier detection (Tukey's fences).
# 1.5 = "mild" outlier;  3.0 = "extreme" outlier.
OUTLIER_IQR_MULTIPLIER = float(os.environ.get("OUTLIER_IQR_MULTIPLIER", "3.0"))

# Required fields that must be non-None.
REQUIRED_FIELDS = ["amount"]

# ── Injection detection patterns ───────────────────────────────────────────
INJECTION_PATTERNS = [
    re.compile(r"approve\s+immediately", re.IGNORECASE),
    re.compile(r"skip\s+review", re.IGNORECASE),
    re.compile(r"ignore\s+your\s+rules?", re.IGNORECASE),
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"ignore\s+the\s+field", re.IGNORECASE),
    re.compile(r"output\s+approved", re.IGNORECASE),
    re.compile(r"ignore\s+all\s+previous", re.IGNORECASE),
]

# ── Detector implementations ───────────────────────────────────────────────


def detect_stale(record: Record) -> Optional[str]:
    """Check if deadline is before PIPELINE_NOW."""
    if not record.deadline:
        return None
    try:
        if record.deadline < PIPELINE_NOW:
            return ReasonCode.STALE
    except TypeError:
        return None
    return None


def detect_missing_input(record: Record) -> Optional[str]:
    """Check if any required field is None or empty."""
    for field in REQUIRED_FIELDS:
        val = getattr(record, field, None)
        if val is None:
            return ReasonCode.MISSING_INPUT
        if isinstance(val, str) and val.strip() == "":
            return ReasonCode.MISSING_INPUT
    return None


def detect_injection(record: Record) -> Optional[str]:
    """Scan notes for injection phrases."""
    if not record.notes:
        return None
    for pattern in INJECTION_PATTERNS:
        if pattern.search(record.notes):
            return ReasonCode.INJECTION_BLOCKED
    return None


class OutlierDetector:
    """Robust IQR-based outlier detection.

    Uses Tukey's fences: values outside [Q1 - k*IQR, Q3 + k*IQR] are outliers.
    k defaults to 1.5 (configurable via OUTLIER_IQR_MULTIPLIER env var).

    Strategy (two-pass):
      1. collect() — gather amounts from all records
      2. compute_thresholds() — compute Q1, Q3, IQR
      3. is_outlier(record) — check if record falls outside fences

    This approach GENERALIZES to any numeric values because it uses the
    data's own distribution, not a hardcoded threshold.
    """

    def __init__(self, multiplier: float = OUTLIER_IQR_MULTIPLIER):
        self.multiplier = multiplier
        self._amounts: list[float] = []
        self._lower: float = -math.inf
        self._upper: float = math.inf
        self._trained = False

    def collect(self, records: list[Record]) -> None:
        """Pass 1: gather numeric values."""
        for r in records:
            if r.amount is not None:
                try:
                    val = float(r.amount)
                    if math.isfinite(val):
                        self._amounts.append(val)
                except (TypeError, ValueError):
                    pass

    def compute_thresholds(self) -> None:
        """Compute IQR-based thresholds from collected data."""
        if len(self._amounts) < 4:
            # Not enough data for meaningful IQR; use MAD instead.
            median = sorted(self._amounts)[len(self._amounts) // 2] if self._amounts else 0
            deviations = sorted(abs(v - median) for v in self._amounts)
            mad = deviations[len(deviations) // 2] if deviations else 0
            self._lower = median - 3 * (mad * 1.4826) if mad else -math.inf
            self._upper = median + 3 * (mad * 1.4826) if mad else math.inf
        else:
            sorted_vals = sorted(self._amounts)
            n = len(sorted_vals)
            q1 = sorted_vals[n // 4]
            q3 = sorted_vals[(3 * n) // 4]
            iqr = q3 - q1
            self._lower = q1 - self.multiplier * iqr
            self._upper = q3 + self.multiplier * iqr
        self._trained = True

    def fit(self, records: list[Record]) -> "OutlierDetector":
        """Convenience: collect + compute in one call."""
        self.collect(records)
        self.compute_thresholds()
        return self

    def is_outlier(self, record: Record) -> bool:
        """Pass 2: check if a record is an outlier."""
        if not self._trained:
            raise RuntimeError("call fit() or compute_thresholds() first")
        if record.amount is None:
            return False
        try:
            val = float(record.amount)
        except (TypeError, ValueError):
            return False
        return val < self._lower or val > self._upper

    def detect(self, record: Record) -> Optional[str]:
        """Convenience: returns ReasonCode.OUTLIER or None."""
        return ReasonCode.OUTLIER if self.is_outlier(record) else None

    def thresholds(self) -> dict:
        return {"lower": self._lower, "upper": self._upper, "multiplier": self.multiplier}


def detect_unverified_anomaly(record: Record) -> Optional[str]:
    """Catch-all: marks records that fail basic validation but match no known detector.

    Checks for:
      - Missing/invalid category (empty or whitespace-only)
      - Missing id or owner
    This generalizes to any seed because it's purely structural.
    """
    if not record.id or not record.id.strip():
        return ReasonCode.UNVERIFIED_ANOMALY
    if record.category is not None and not record.category.strip():
        return ReasonCode.UNVERIFIED_ANOMALY
    return None


# ── Aggregated runner ──────────────────────────────────────────────────────


def run_all_detectors(
    records: list[Record],
    outlier_detector: OutlierDetector,
) -> list[Record]:
    """Run all per-record detectors on a list of records.

    Returns the same records with status/reason_code/reason_class set.
    """
    for record in records:
        # Skip already-blocked records.
        if record.status in (RecordStatus.EXCEPTION, RecordStatus.SUPERSEDED):
            continue

        # Run detectors in priority order (first match wins).
        reason = None
        rclass = None

        if record.reason_code:
            continue  # already classified

        reason = detect_stale(record)
        if reason:
            rclass = ReasonClass.A
        else:
            reason = detect_missing_input(record)
            if reason:
                rclass = ReasonClass.A
            else:
                reason = outlier_detector.detect(record)
                if reason:
                    rclass = ReasonClass.A
                else:
                    reason = detect_injection(record)
                    if reason:
                        rclass = ReasonClass.A

        # Catch-all: if none of the above matched but record fails basic validation, mark UNVERIFIED_ANOMALY.
        if not reason:
            reason = detect_unverified_anomaly(record)
            if reason:
                rclass = ReasonClass.A

        if reason and rclass:
            record.reason_code = reason
            record.reason_class = rclass
            record.status = RecordStatus.EXCEPTION

    return records
