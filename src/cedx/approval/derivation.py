"""Derive amendment role + threshold from CASE_ID.

CASE_ID format: CEDX-XXXX where XXXX is 4+ hex chars.

Derivation rules:
  - Last hex digit → role (0-3 : risk_officer, 4-7 : legal_counsel,
    8-B : compliance, C-F : finance_controller)
  - Remaining hex digits → threshold in USD
"""
from __future__ import annotations


ROLE_MAP: dict[str, str] = {
    "0": "risk_officer", "1": "risk_officer", "2": "risk_officer", "3": "risk_officer",
    "4": "legal_counsel", "5": "legal_counsel", "6": "legal_counsel", "7": "legal_counsel",
    "8": "compliance",     "9": "compliance",     "a": "compliance",     "b": "compliance",
    "c": "finance_controller", "d": "finance_controller", "e": "finance_controller", "f": "finance_controller",
}


def derive_amendment(case_id: str) -> tuple[str, float]:
    """Derive (amendment_role, threshold_usd) from CASE_ID.

    Args:
        case_id: e.g. "CEDX-7F3A" or "CEDX-0000"

    Returns:
        Tuple of (role_name, threshold).
    """
    if "-" not in case_id:
        return "risk_officer", 5000.0

    hex_part = case_id.split("-", 1)[1].strip()
    if not hex_part:
        return "risk_officer", 5000.0

    last_char = hex_part[-1].lower()
    role = ROLE_MAP.get(last_char, "risk_officer")

    # Remaining hex digits → threshold
    threshold_hex = hex_part[:-1] if len(hex_part) > 1 else "0"
    try:
        threshold_raw = int(threshold_hex, 16) if threshold_hex else 0
    except ValueError:
        threshold_raw = 0

    threshold = max(1000.0, float(threshold_raw) * 100.0)
    return role, threshold
