#!/usr/bin/env python3
"""Print one record's FULL agent decision path from the audit log.

Usage:
    python3 scripts/trace.py --id REC-001

Outputs: which agent ran, model, tokens/cost, retries, Verifier verdict, routing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="Record ID to trace")
    ap.add_argument("--audit", default="out/audit.json", help="Audit file path")
    args = ap.parse_args()

    audit_path = Path(args.audit)
    if not audit_path.exists():
        print(f"FAIL: {args.audit} not found")
        return 1

    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    records = audit.get("records", [])

    target = None
    for r in records:
        if r["id"] == args.id:
            target = r
            break

    if target is None:
        print(f"FAIL: record {args.id} not found in audit")
        return 1

    print(f"Record: {target['id']} v{target.get('version', 1)}")
    print(f"Format: {target.get('source_format', '?')}")
    print(f"Status: {target['status']}")
    print(f"Reason: {target.get('reason_code') or 'none'}")
    print()

    trace = target.get("agent_trace") or []
    if not trace:
        print("No agent trace available.")
    else:
        for i, span in enumerate(trace):
            print(f"Step {i}: {span.get('agent', '?')}")
            print(f"  Status:   {span.get('status', '?')}")
            print(f"  Verdict:  {span.get('verdict') or '?'}")
            print(f"  Model:    {span.get('model') or 'n/a'}")
            print(f"  Tokens:   {span.get('tokens_in') or 0} in, {span.get('tokens_out') or 0} out")
            print(f"  Cost:     ${span.get('cost_usd') or 0:.6f}")
            print(f"  Latency:  {span.get('latency_ms') or 0:.0f} ms")
            print(f"  Retries:  {span.get('retries') or 0}")
            print()

    print(f"Transcript hash: {target.get('transcript_hash') or 'none'}")
    print(f"Delivered fields hash: {target.get('delivered_fields_hash') or 'none'}")

    trail = target.get("approval_trail") or []
    if trail:
        print("Approval trail:")
        for t in trail:
            print(f"  {t.get('state')} by {t.get('actor')} at {t.get('ts')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
