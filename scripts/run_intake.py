#!/usr/bin/env python3
"""CLI to run Stage 1 intake against SEED_DIR."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Make cedx_MAP/src importable when running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cedx.intake import run_intake


def main() -> int:
    seed_dir = os.environ.get("SEED_DIR", "seed")
    store_path = os.environ.get("STORE_PATH", "out/records.db")
    aliases_path = os.environ.get("ALIASES_PATH")

    result = run_intake(
        seed_dir=seed_dir,
        store_path=store_path,
        aliases_path=aliases_path,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
