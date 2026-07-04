"""Core record model used across the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

from cedx.utils.hashing import canonical_json, sha


class ReasonCode:
    """All reason codes used by exception queue + audit."""

    # Class A — blocking
    STALE = "STALE"
    MISSING_INPUT = "MISSING_INPUT"
    OUTLIER = "OUTLIER"
    INJECTION_BLOCKED = "INJECTION_BLOCKED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    UNVERIFIED_ANOMALY = "UNVERIFIED_ANOMALY"

    # Agent-layer failures
    AGENT_HALLUCINATION = "AGENT_HALLUCINATION"
    AGENT_LOOP = "AGENT_LOOP"
    AGENT_MALFORMED = "AGENT_MALFORMED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"

    # Class B — auto-resolved
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    SUPERSEDED_VERSION = "SUPERSEDED_VERSION"


class ReasonClass:
    """Class A = blocking, Class B = auto-resolved & logged."""

    A = "A"
    B = "B"


class RecordStatus:
    """Lifecycle status of a record."""

    PENDING = "pending"
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    DELIVERED = "delivered"
    EXCEPTION = "exception"
    SUPERSEDED = "superseded"
    BLOCKED = "blocked"


@dataclass
class Record:
    """
    A unified work-request record.

    Stage 1 (Intake) populates the source-derived fields and persists the record.
    Stage 2+ add status, reason codes, traces, and approval trails.
    """

    id: str
    owner: Optional[str]
    deadline: Optional[str]  # ISO-8601 date string
    category: Optional[str]
    amount: Optional[float]
    notes: Optional[str]
    version: int = 1

    source_format: str = "feed"  # feed | eml | pdf
    source_path: str = ""
    source_version_hash: str = ""

    # Original key/value pairs as extracted from the source (preserved for provenance).
    raw_fields: dict[str, Any] = field(default_factory=dict)

    # Pipeline bookkeeping
    intake_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = RecordStatus.PENDING
    reason_code: Optional[str] = None
    reason_class: Optional[str] = None

    # Filled in later stages
    delivered_fields: Optional[dict[str, Any]] = None
    delivered_fields_hash: Optional[str] = None
    transcript_hash: Optional[str] = None
    agent_trace: list[dict[str, Any]] = field(default_factory=list)
    approval_trail: list[dict[str, Any]] = field(default_factory=list)

    def compute_source_version_hash(self) -> str:
        """Deterministic hash of the source-derived content."""
        payload = {
            "source_format": self.source_format,
            "source_path": self.source_path,
            "raw_fields": self.raw_fields,
        }
        return sha(payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Record":
        return cls(**data)

    def canonical_input_hash(self) -> str:
        """Hash of the canonical input fields (used by Worker/Verifier)."""
        payload = {
            "id": self.id,
            "owner": self.owner,
            "deadline": self.deadline,
            "category": self.category,
            "amount": self.amount,
            "notes": self.notes,
            "version": self.version,
        }
        return sha(payload)
