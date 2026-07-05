"""Audit bundle builder — produces out/audit.json conforming to audit.schema.json.

Aggregates:
  - case_id + amendment
  - agent roster
  - cost summary
  - per-record data (status, reason codes, traces, approval trails)
  - append-only event log
  - output_package_hash
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from cedx.agents.base import BaseAgent
from cedx.audit.events import EventLog
from cedx.intake.store import RecordStore
from cedx.models.record import Record
from cedx.utils.hashing import sha


PIPELINE_VERSION = "0.1.0"


class AuditBuilder:
    """Builds the audit bundle from pipeline state."""

    def __init__(
        self,
        store: RecordStore,
        event_log: EventLog,
        case_id: str = "CEDX-0000",
        seed_dir: str | Path = "seed",
        pipeline_now: Optional[str] = None,
    ):
        self.store = store
        self.event_log = event_log
        self.case_id = case_id
        self.seed_dir = str(seed_dir)
        self.pipeline_now = pipeline_now or os.environ.get("PIPELINE_NOW", "")
        self.output_path: Optional[Path] = None

    def build(
        self,
        agents: list[BaseAgent],
        output_package_path: str | Path = "out/package",
    ) -> dict[str, Any]:
        """Build the full audit bundle.

        Args:
            agents: List of all agents in the fleet (roster).
            output_package_path: Path to the branded output package.

        Returns:
            Audit JSON dict.
        """
        from cedx.approval.derivation import derive_amendment
        amd_role, amd_threshold = derive_amendment(self.case_id)

        records = self.store.get_all()
        delivered = [r for r in records if r.status == "delivered"]

        # Build agent roster entries
        agent_entries = []
        for agent in agents:
            entry = agent.roster_entry()
            entry["role"] = agent.role
            agent_entries.append(entry)

        # Cost summary
        total_cost = 0.0
        record_count = 0
        latencies: list[float] = []
        for r in records:
            for span in r.agent_trace:
                c = span.get("cost_usd")
                if isinstance(c, (int, float)):
                    total_cost += c
                lat_ms = span.get("latency_ms")
                if isinstance(lat_ms, (int, float)):
                    latencies.append(lat_ms)
            record_count += 1

        avg_cost = total_cost / record_count if record_count > 0 else 0.0
        p95_latency = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0.0
        projected = avg_cost * 10000 if avg_cost > 0 else 0.0

        # Output package hash
        package_hash = self._hash_package(output_package_path)

        # Build per-record entries
        record_entries = []
        for r in records:
            entry = {
                "id": r.id,
                "version": r.version,
                "source_format": r.source_format,
                "source_version_hash": r.source_version_hash,
                "status": r.status,
                "reason_code": r.reason_code,
                "reason_class": r.reason_class,
                "transcript_hash": r.transcript_hash,
                "delivered_fields": r.delivered_fields,
                "delivered_fields_hash": r.delivered_fields_hash,
                "agent_trace": r.agent_trace,
                "approval_trail": r.approval_trail,
            }
            record_entries.append(entry)

        audit = {
            "case_id": self.case_id,
            "pipeline_version": PIPELINE_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "seed_dir": self.seed_dir,
            "pipeline_now": self.pipeline_now,
            "amendment": {
                "role": amd_role,
                "threshold": amd_threshold,
            },
            "agents": agent_entries,
            "cost": {
                "total_usd": round(total_cost, 6),
                "avg_usd_per_record": round(avg_cost, 6),
                "p95_latency_ms": round(p95_latency, 2),
                "records": record_count,
                "projected_usd_per_10k": round(projected, 6),
            },
            "output_package_hash": package_hash,
            "records": record_entries,
            "events": self.event_log.events(),
        }

        return audit

    def _hash_package(self, package_path: str | Path) -> str:
        """Compute sha256 of the output package directory."""
        pkg = Path(package_path)
        if not pkg.exists():
            return "sha256:" + "0" * 64

        # Compute a tree hash of the package directory
        contents: list[str] = []
        for fp in sorted(pkg.rglob("*")):
            if fp.is_file():
                contents.append(fp.relative_to(pkg).as_posix())
                contents.append(fp.read_bytes().hex())

        return sha("".join(contents))

    def write(
        self,
        agents: list[BaseAgent],
        output_path: str | Path = "out/audit.json",
        output_package_path: str | Path = "out/package",
    ) -> dict[str, Any]:
        """Build and write the audit bundle to disk.

        Seals the event log before writing (append-only enforcement).
        """
        self.event_log.seal()
        audit = self.build(agents, output_package_path=output_package_path)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(audit, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
        self.output_path = out
        return audit


def build_audit(
    store: RecordStore,
    event_log: EventLog,
    agents: list[BaseAgent],
    case_id: str = "CEDX-0000",
    seed_dir: str | Path = "seed",
    output_path: str | Path = "out/audit.json",
    output_package_path: str | Path = "out/package",
) -> dict[str, Any]:
    """Convenience: build and write the audit bundle."""
    builder = AuditBuilder(
        store=store,
        event_log=event_log,
        case_id=case_id,
        seed_dir=seed_dir,
    )
    return builder.write(agents, output_path=output_path, output_package_path=output_package_path)
