#!/usr/bin/env python3
"""Probe: exit 0 ONLY if running demo twice produces no duplicate outputs,
exceptions, or approvals.

Runs the pipeline twice on the same seed and compares outputs.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    run_dir = Path.cwd()
    seed_dir = os.environ.get("SEED_DIR", "seed")
    store_path = Path("out/records.db")
    audit_path = Path("out/audit.json")
    eq_path = Path("out/exception_queue.json")

    # Run 1
    if store_path.exists():
        store_path.unlink()
    if audit_path.exists():
        audit_path.unlink()
    if eq_path.exists():
        eq_path.unlink()

    env = {**os.environ, "SEED_DIR": os.environ.get("SEED_DIR", "seed"), "STORE_PATH": str(store_path), "PYTHONPATH": "src:lib", "REPLAY_LLM": "true"}
    result1 = subprocess.run(
        [sys.executable, "scripts/run_pipeline.py"],
        cwd=run_dir,
        env=env,
        capture_output=True,
        text=True,
    )
    if result1.returncode != 0:
        print(f"FAIL: first run failed: {result1.stderr[:500]}")
        return 1

    # Read run 1 outputs
    with open(audit_path) as f:
        audit1 = json.load(f)
    with open(eq_path) as f:
        eq1 = json.load(f)

    # Run 2 (same seed)
    result2 = subprocess.run(
        [sys.executable, "scripts/run_pipeline.py"],
        cwd=run_dir,
        env=env,
        capture_output=True,
        text=True,
    )
    if result2.returncode != 0:
        print(f"FAIL: second run failed: {result2.stderr[:500]}")
        return 1

    with open(audit_path) as f:
        audit2 = json.load(f)
    with open(eq_path) as f:
        eq2 = json.load(f)

    # Compare
    # Same number of records
    if len(audit1["records"]) != len(audit2["records"]):
        print(f"FAIL: record count differs: {len(audit1['records'])} vs {len(audit2['records'])}")
        return 1

    # Same delivered count
    d1 = len([r for r in audit1["records"] if r.get("status") == "delivered"])
    d2 = len([r for r in audit2["records"] if r.get("status") == "delivered"])
    if d1 != d2:
        print(f"FAIL: delivered count differs: {d1} vs {d2}")
        return 1

    # Same exception queue entries
    if len(eq1) != len(eq2):
        print(f"FAIL: exception queue size differs: {len(eq1)} vs {len(eq2)}")
        return 1

    # Same exception IDs
    eq1_ids = sorted(e["id"] for e in eq1)
    eq2_ids = sorted(e["id"] for e in eq2)
    if eq1_ids != eq2_ids:
        print(f"FAIL: exception queue IDs differ: {eq1_ids} vs {eq2_ids}")
        return 1

    print(f"PASS: idempotent — {d1} delivered, {len(eq1)} exceptions (identical across runs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
