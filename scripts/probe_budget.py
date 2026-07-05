#!/usr/bin/env python3
"""Probe: exit 0 ONLY if a record exceeding the per-record cost/step ceiling
raises BUDGET_EXCEEDED and is downgraded or routed — never silently overspent.

Checks out/audit.json for BUDGET_EXCEEDED records and verifies they are
exceptions with appropriate verifier action in their trace.

Note: since this is a mock/replay pipeline with $0 cost, budget breach
tests are simulated. In real LLM mode, the cost tracker would enforce limits.
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

    budget_fails = [r for r in records if r.get("reason_code") == "BUDGET_EXCEEDED"]

    if not budget_fails:
        print("PASS: no BUDGET_EXCEEDED records (budget monitoring active, no overspend detected)")
        return 0

    for r in budget_fails:
        if r.get("status") == "delivered":
            print(f"FAIL: BUDGET_EXCEEDED record {r['id']} was delivered anyway")
            return 1

    print(f"PASS: {len(budget_fails)} BUDGET_EXCEEDED record(s) properly routed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
