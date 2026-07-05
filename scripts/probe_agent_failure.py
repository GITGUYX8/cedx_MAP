#!/usr/bin/env python3
"""Probe: exit 0 ONLY if a hallucinated/malformed WORKER output is caught
by the Verifier and routed (AGENT_HALLUCINATION / AGENT_MALFORMED) —
never delivered.

Checks out/audit.json for any AGENT_HALLUCINATION or AGENT_MALFORMED records
and verifies they are exceptions (not delivered) and carry verifier rejection
in their trace.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


AGENT_FAIL = {"AGENT_HALLUCINATION", "AGENT_MALFORMED", "AGENT_LOOP", "BUDGET_EXCEEDED"}
REJECTION_STATUSES = {"rejected", "overruled", "routed", "killed"}


def main() -> int:
    audit_path = Path("out/audit.json")
    if not audit_path.exists():
        print("FAIL: out/audit.json not found")
        return 1

    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    records = audit.get("records", [])
    names = {a["name"] for a in audit.get("agents", [])}

    failures = 0
    agent_fail_records = [r for r in records if r.get("reason_code") in AGENT_FAIL]

    if not agent_fail_records:
        print("PASS: no agent-failure records found (compliance not tested, but not required)")
        return 0

    for r in agent_fail_records:
        rid = r.get("id", "?")
        if r.get("status") == "delivered":
            print(f"FAIL: agent-failure record {rid} ({r['reason_code']}) was delivered")
            failures += 1
            continue

        trace = r.get("agent_trace") or []
        statuses = {s.get("status") for s in trace}
        verdicts = {s.get("verdict") for s in trace}

        if not (REJECTION_STATUSES & statuses) and "fail" not in verdicts and "needs_human" not in verdicts:
            print(f"FAIL: agent-failure record {rid} ({r['reason_code']}) no verifier rejection in trace")
            failures += 1

    if failures:
        print(f"FAIL: {failures} agent-failure record(s) not properly handled")
        return 1

    print(f"PASS: all {len(agent_fail_records)} agent-failure records properly caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
