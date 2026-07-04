"""Source-format parsers for Stage 1 — Intake."""
from __future__ import annotations

import email
import json
import re
from pathlib import Path
from typing import Any

from cedx.models.record import Record


def load_field_aliases(path: str | Path) -> dict[str, list[str]]:
    """Load canonical -> aliases mapping from JSON config."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_alias_to_canonical(aliases: dict[str, list[str]]) -> dict[str, str]:
    """Invert {canonical: [aliases]} into {lowercase_alias: canonical}."""
    inverted: dict[str, str] = {}
    for canonical, alias_list in aliases.items():
        for alias in alias_list:
            inverted[alias.lower().strip()] = canonical
    return inverted


def _normalize_key(key: str, alias_map: dict[str, str]) -> str:
    """Map a raw key to its canonical name, if known."""
    return alias_map.get(key.lower().strip(), key.lower().strip())


def _normalize_value(key: str, value: Any) -> Any:
    """Best-effort type coercion for known canonical fields."""
    if value is None:
        return None
    s = str(value).strip()
    if s == "":
        return None
    if s.lower() in {"null", "none", "n/a", "na"}:
        return None

    if key == "amount":
        # Strip currency symbols / commas
        cleaned = re.sub(r"[^\d.\-]", "", s.replace(",", ""))
        try:
            if "." in cleaned:
                return float(cleaned)
            return int(cleaned)
        except ValueError:
            return None

    if key == "version":
        try:
            return int(s)
        except ValueError:
            return 1

    return s


def _extract_from_text(
    text: str,
    source_format: str,
    source_path: str,
    alias_map: dict[str, str],
) -> Record:
    """
    Extract key/value pairs from plain text using 'Key: value' conventions.

    Preserves original keys in `raw_fields` and maps them to canonical fields
    using the alias map. Multi-line notes are supported: once a 'notes' key is
    seen, following lines that do not match a known key are appended.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    raw_fields: dict[str, Any] = {}
    canonical: dict[str, Any] = {}
    current_notes_key: str | None = None

    for line in lines:
        # Try to split on first colon
        if ":" in line:
            maybe_key, maybe_value = line.split(":", 1)
            maybe_key = maybe_key.strip()
            maybe_value = maybe_value.strip()
            canonical_key = _normalize_key(maybe_key, alias_map)

            # If this line looks like a known field, capture it.
            if canonical_key in alias_map.values() or maybe_key.lower() in alias_map:
                raw_fields[maybe_key] = maybe_value
                canonical[canonical_key] = _normalize_value(canonical_key, maybe_value)
                if canonical_key == "notes":
                    current_notes_key = maybe_key
                else:
                    current_notes_key = None
                continue

        # Continuation of multi-line notes
        if current_notes_key is not None:
            raw_fields[current_notes_key] = raw_fields.get(current_notes_key, "") + " " + line
            canonical["notes"] = canonical.get("notes", "") + " " + line

    record = Record(
        id=canonical.get("id", ""),
        owner=canonical.get("owner"),
        deadline=canonical.get("deadline"),
        category=canonical.get("category"),
        amount=canonical.get("amount"),
        notes=canonical.get("notes"),
        version=canonical.get("version", 1) or 1,
        source_format=source_format,
        source_path=source_path,
        raw_fields=raw_fields,
    )
    record.source_version_hash = record.compute_source_version_hash()
    return record


def parse_feed_json(path: str | Path, alias_map: dict[str, str] | None = None) -> list[Record]:
    """Parse the structured feed.json file."""
    path = Path(path)
    records: list[Record] = []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for item in data:
        raw_fields = {k: v for k, v in item.items()}
        canonical: dict[str, Any] = {}
        if alias_map is not None:
            for key, value in item.items():
                canonical_key = _normalize_key(key, alias_map)
                if canonical_key in {"id", "owner", "deadline", "category", "amount", "notes", "version"}:
                    canonical[canonical_key] = _normalize_value(canonical_key, value)
                else:
                    canonical[canonical_key] = value
        else:
            canonical = dict(raw_fields)

        record = Record(
            id=canonical.get("id", ""),
            owner=canonical.get("owner"),
            deadline=canonical.get("deadline"),
            category=canonical.get("category"),
            amount=canonical.get("amount"),
            notes=canonical.get("notes"),
            version=canonical.get("version", 1) or 1,
            source_format="feed",
            source_path=str(path),
            raw_fields=raw_fields,
        )
        record.source_version_hash = record.compute_source_version_hash()
        records.append(record)
    return records


def parse_eml(path: str | Path, alias_map: dict[str, str]) -> list[Record]:
    """Parse an email file and extract the work-request body."""
    path = Path(path)
    msg = email.message_from_bytes(path.read_bytes())

    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")
                    break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode("utf-8", errors="replace")

    record = _extract_from_text(body, "eml", str(path), alias_map)
    return [record]


def parse_pdf(path: str | Path, alias_map: dict[str, str]) -> list[Record]:
    """Parse a PDF file and extract the work-request text."""
    from pypdf import PdfReader

    path = Path(path)
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        parts.append(text)
    full_text = "\n".join(parts)

    record = _extract_from_text(full_text, "pdf", str(path), alias_map)
    return [record]
