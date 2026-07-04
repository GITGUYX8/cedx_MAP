"""Stage 1 orchestrator: read all sources and persist records."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from cedx.intake.parsers import (
    load_field_aliases,
    parse_eml,
    parse_feed_json,
    parse_pdf,
)
from cedx.intake.store import RecordStore
from cedx.models.record import Record


DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


class Intake:
    """
    Reads work-request records from a seed directory.

    Supports:
      - seed/feed.json  (structured JSON)
      - seed/inbox/*.eml (email)
      - seed/inbox/*.pdf (PDF)
    """

    def __init__(
        self,
        seed_dir: str | Path,
        store: RecordStore,
        aliases_path: str | Path | None = None,
    ):
        self.seed_dir = Path(seed_dir)
        self.store = store
        self.aliases_path = Path(
            aliases_path if aliases_path is not None else DEFAULT_CONFIG_DIR / "field_aliases.json"
        )
        self.alias_map = _build_alias_to_canonical(load_field_aliases(self.aliases_path))

    def run(self) -> dict[str, Any]:
        """Parse every source under seed_dir and upsert into the store."""
        counts: dict[str, int] = {"feed": 0, "eml": 0, "pdf": 0, "total": 0}

        # 1. Structured feed
        feed_path = self.seed_dir / "feed.json"
        if feed_path.exists():
            for record in parse_feed_json(feed_path, alias_map=self.alias_map):
                self.store.upsert(record)
                counts["feed"] += 1

        # 2. Email + PDF inbox
        inbox_dir = self.seed_dir / "inbox"
        if inbox_dir.exists():
            for eml_path in sorted(inbox_dir.glob("*.eml")):
                for record in parse_eml(eml_path, self.alias_map):
                    self.store.upsert(record)
                    counts["eml"] += 1

            for pdf_path in sorted(inbox_dir.glob("*.pdf")):
                for record in parse_pdf(pdf_path, self.alias_map):
                    self.store.upsert(record)
                    counts["pdf"] += 1

        counts["total"] = counts["feed"] + counts["eml"] + counts["pdf"]
        return {
            "seed_dir": str(self.seed_dir),
            "records_stored": counts["total"],
            "by_format": {
                "feed": counts["feed"],
                "eml": counts["eml"],
                "pdf": counts["pdf"],
            },
            "store_path": str(self.store.db_path),
        }


def _build_alias_to_canonical(aliases: dict[str, list[str]]) -> dict[str, str]:
    inverted: dict[str, str] = {}
    for canonical, alias_list in aliases.items():
        for alias in alias_list:
            inverted[alias.lower().strip()] = canonical
    return inverted


def run_intake(
    seed_dir: str | Path | None = None,
    store_path: str | Path = "out/records.db",
    aliases_path: str | Path | None = None,
) -> dict[str, Any]:
    """Convenience entry point used by scripts / Makefile."""
    seed_dir = Path(seed_dir or os.environ.get("SEED_DIR", "seed"))
    store = RecordStore(store_path)
    intake = Intake(seed_dir=seed_dir, store=store, aliases_path=aliases_path)
    return intake.run()
