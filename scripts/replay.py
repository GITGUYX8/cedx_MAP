#!/usr/bin/env python3
"""Reconstruct one delivered output's DATA lineage from the append-only log alone.

Usage:
    python3 scripts/replay.py --id REC-001

Outputs: the full data provenance for a record from the audit + transcripts.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="Record ID to replay")
    ap.add_argument("--audit", default="out/audit.json", help="Audit file path")
    ap.add_argument("--transcripts", default="transcripts", help="Transcripts directory")
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

    print(f"=== Data Lineage for {target['id']} ===")
    print(f"Status: {target['status']}")
    print()

    # Events related to this record
    print("--- Events ---")
    for ev in audit.get("events", []):
        if ev.get("record_id") == args.id:
            print(f"  seq={ev['seq']} | {ev['actor']} | {ev['action']} | {ev['ts']}")

    print()
    print("--- Agent Trace ---")
    for span in target.get("agent_trace", []):
        print(f"  Agent: {span.get('agent')} | Verdict: {span.get('verdict')} | Status: {span.get('status')}")
        if span.get("cost_usd"):
            print(f"  Cost: ${span['cost_usd']:.6f} | Tokens: {span.get('tokens_in')} in / {span.get('tokens_out')} out")

    print()
    print("--- Approval Trail ---")
    for t in target.get("approval_trail", []):
        print(f"  {t['state']} by {t['actor']}")

    # Transcript
    th = target.get("transcript_hash")
    if th:
        stem = th.split(":")[-1]
        tp = Path(args.transcripts) / f"{stem}.json"
        if tp.exists():
            transcript = json.loads(tp.read_text(encoding="utf-8"))
            print()
            print("--- Load-Bearing Transcript ---")
            print(f"  Agent: {transcript.get('agent')}")
            print(f"  Model: {transcript.get('model')}")
            print(f"  Prompt version: {transcript.get('prompt_version')}")
            print(f"  Tokens: {transcript.get('tokens_in')} in / {transcript.get('tokens_out')} out")
            print(f"  Cost: ${transcript.get('cost_usd', 0):.6f}")
            print(f"  Response hash: {transcript.get('response_hash')}")

    print()
    print("--- Delivered Fields ---")
    df = target.get("delivered_fields") or {}
    for k, v in df.items():
        print(f"  {k}: {v}")
    print(f"  hash: {target.get('delivered_fields_hash')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
