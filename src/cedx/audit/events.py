"""Append-only event log.

Events are stored in-memory during the pipeline run and written
to the audit JSON at the end. The seq field is strictly 0..n-1.
Once written, past entries cannot be mutated (append-only).

Each event has:
  - seq: monotonically increasing integer (0, 1, 2, ...)
  - ts: ISO-8601 timestamp
  - actor: who performed the action
  - action: what happened
  - record_id: optional record reference
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class EventLog:
    """Append-only event log.

    Events are accumulated in _events and dumped to JSON at the end.
    """

    def __init__(self):
        self._events: list[dict[str, Any]] = []
        self._sealed = False

    def append(
        self,
        actor: str,
        action: str,
        record_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Append an event to the log.

        Raises RuntimeError if the log has been sealed (for append-only enforcement).
        """
        if self._sealed:
            raise RuntimeError("event log is sealed (append-only violation)")

        event = {
            "seq": len(self._events),
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "action": action,
            "record_id": record_id,
        }
        self._events.append(event)
        return event

    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def seal(self) -> None:
        """Seal the log — no more appends allowed."""
        self._sealed = True

    def size(self) -> int:
        return len(self._events)
