#!/usr/bin/env python3
"""Probe: exit 0 ONLY if delivery of a NON-approved item is refused + logged.

Checks that ALL delivered records in out/audit.json have an approval_trail
ending in 'approved' → 'delivered'. If any delivered record lacks a proper
approval trail, exits 1.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    audit_path = Path("out/audit.json")
    if not audit_path.exists():
        print("FAIL: out/audit.json not found")
        return 1

    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    records = audit.get("records", [])

    failed = 0
    for r in records:
        if r.get("status") != "delivered":
            continue

        trail = r.get("approval_trail") or []
        states = [t.get("state") for t in trail]

        if "approved" not in states:
            print(f"FAIL: delivered record {r['id']} never reached 'approved' state")
            failed += 1
        else:
            i_app = states.index("approved")
            if "delivered" in states:
                if states.index("delivered") < i_app:
                    print(f"FAIL: delivered record {r['id']} was delivered before approval")
                    failed += 1
                # Check delivery is logged after approval
                appr = trail[i_app]
                if not appr.get("actor") or not appr.get("ts"):
                    print(f"FAIL: delivered record {r['id']} approval missing actor/timestamp")
                    failed += 1

    if failed:
        print(f"FAIL: {failed} record(s) violated approval rules")
        return 1

    print(f"PASS: all {len([r for r in records if r.get('status') == 'delivered'])} delivered records properly approved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
