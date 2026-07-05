#!/usr/bin/env python3
"""Probe: SIGKILL between stages, re-run. Exit 0 if resumes w/o dupes.

Run 1: start pipeline, SIGKILL partway through, capture partial output.
Run 2: re-run pipeline fully.
Check: outputs consistent (no duplicate records in store or audit).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    run_dir = Path.cwd()
    store_path = Path("out/records.db")
    audit_path = Path("out/audit.json")
    eq_path = Path("out/exception_queue.json")

    # Clean start
    for p in [store_path, audit_path, eq_path]:
        if p.exists():
            p.unlink()
    pkg = Path("out/package")
    if pkg.exists():
        import shutil
        shutil.rmtree(pkg)
    tdir = Path("transcripts")
    if tdir.exists():
        import shutil
        shutil.rmtree(tdir)

    env = {
        **os.environ,
        "SEED_DIR": os.environ.get("SEED_DIR", "seed"),
        "PYTHONPATH": "src:lib",
        "REPLAY_LLM": "true",
    }

    # Run 1: launch in background, kill after 2 seconds
    print("[Run 1] Launching pipeline (will SIGKILL after ~2s)...")
    proc = subprocess.Popen(
        [sys.executable, "scripts/run_pipeline.py"],
        cwd=run_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    time.sleep(2)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait()
    print(f"[Run 1] Killed after 2s (exit code: {proc.returncode})")

    # Read partial outputs (if any were written before kill)
    partial_store = None
    partial_audit = None
    partial_eq = None
    if store_path.exists():
        partial_store = store_path.stat().st_size
        print(f"[Run 1] Partial store: {partial_store} bytes")
    if audit_path.exists():
        partial_audit = audit_path.stat().st_size
        print(f"[Run 1] Partial audit: {partial_audit} bytes")
    if eq_path.exists():
        partial_eq = eq_path.stat().st_size
        print(f"[Run 1] Partial exception queue: {partial_eq} bytes")

    # Run 2: full re-run
    print("[Run 2] Re-running pipeline (full)...")
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

    # Verify no duplicates in store
    import sqlite3
    db = sqlite3.connect(str(store_path))
    db.row_factory = sqlite3.Row
    cur = db.execute("SELECT id, version, COUNT(*) as cnt FROM records GROUP BY id, version HAVING cnt > 1")
    dupes = cur.fetchall()
    if dupes:
        print(f"FAIL: {len(dupes)} duplicate records in store")
        for d in dupes:
            print(f"  {d['id']} v{d['version']}: {d['cnt']} copies")
        return 1

    # Verify no duplicate records in audit
    with open(audit_path) as f:
        audit = json.load(f)
    record_ids = [(r["id"], r.get("version", 1)) for r in audit["records"]]
    if len(record_ids) != len(set(record_ids)):
        print("FAIL: duplicate records in audit")
        return 1

    # Check audit passes verify_audit
    verify = subprocess.run(
        [sys.executable, "verify_audit.py", "--audit", "out/audit.json",
         "--transcripts", "transcripts", "--schema", "audit.schema.json"],
        cwd=run_dir,
        capture_output=True,
        text=True,
    )
    if verify.returncode != 0:
        print(f"FAIL: verify_audit.py failed after crash recovery:\n{verify.stdout[:500]}\n{verify.stderr[:500]}")
        return 1

    print(f"PASS: pipeline survived SIGKILL — {len(audit['records'])} records, "
          f"no duplicates, verify_audit.py OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
