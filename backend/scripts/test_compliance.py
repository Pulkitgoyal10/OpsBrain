"""Verify the compliance agent against the real ingested graph data.

Usage: .venv/bin/python scripts/test_compliance.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.compliance.agent import find_overdue_maintenance  # noqa: E402


def main() -> None:
    gaps = find_overdue_maintenance()

    print(f"Found {len(gaps)} overdue maintenance item(s)\n")
    for g in gaps:
        print(f"  {g.equipment_tag}  ({g.location or 'unknown location'})")
        print(
            f"    next_due_date={g.next_due_date}  "
            f"days_overdue={g.days_overdue}  severity={g.severity}"
        )
        print(f"    issue_found: {g.issue_found}")
        print(f"    source: {g.source_document}, page {g.page_number}")
        print()

    high = sum(1 for g in gaps if g.severity == "high")
    medium = sum(1 for g in gaps if g.severity == "medium")
    low = sum(1 for g in gaps if g.severity == "low")
    print(f"Summary: {high} high, {medium} medium, {low} low severity")


if __name__ == "__main__":
    main()
