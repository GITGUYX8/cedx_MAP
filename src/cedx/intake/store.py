"""Persistent record store used by Stage 1 (and later stages)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from cedx.models.record import Record


class RecordStore:
    """
    SQLite-backed store for records.

    Uses `source_version_hash` as the primary key so re-running intake is
    idempotent (same source content -> same hash -> upsert, never duplicate).
    This underpins the idempotency and crash-resumability probes.
    """

    def __init__(self, db_path: str | Path = "out/records.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_table()

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS records (
                    source_version_hash TEXT PRIMARY KEY,
                    id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    source_format TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_records_id ON records(id)"
            )
            conn.commit()

    def upsert(self, record: Record) -> None:
        """Insert or replace a record keyed by its source version hash."""
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO records (source_version_hash, id, version, source_format, source_path, data, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_version_hash) DO UPDATE SET
                    id=excluded.id,
                    version=excluded.version,
                    source_format=excluded.source_format,
                    source_path=excluded.source_path,
                    data=excluded.data,
                    created_at=excluded.created_at
                """,
                (
                    record.source_version_hash,
                    record.id,
                    record.version,
                    record.source_format,
                    record.source_path,
                    json.dumps(record.to_dict(), sort_keys=True),
                    record.intake_at,
                ),
            )
            conn.commit()

    def get(self, source_version_hash: str) -> Record | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT data FROM records WHERE source_version_hash = ?",
                (source_version_hash,),
            ).fetchone()
            if row is None:
                return None
            return Record.from_dict(json.loads(row["data"]))

    def get_all(self) -> list[Record]:
        with self._connection() as conn:
            rows = conn.execute("SELECT data FROM records ORDER BY id, version").fetchall()
            return [Record.from_dict(json.loads(row["data"])) for row in rows]

    def get_by_id(self, record_id: str) -> list[Record]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT data FROM records WHERE id = ? ORDER BY version",
                (record_id,),
            ).fetchall()
            return [Record.from_dict(json.loads(row["data"])) for row in rows]

    def count(self) -> int:
        with self._connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM records").fetchone()
            return row["c"]

    def clear(self) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM records")
            conn.commit()
