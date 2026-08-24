"""Test answer synthesis against the same two questions as test_retrieval.py.

Usage: .venv/bin/python scripts/test_answer.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.rag.answer import answer_question  # noqa: E402

TEST_QUESTIONS = [
    "What PPE is required when handling sodium hypochlorite?",
    "When is pump P-102 due for its next inspection?",
]

# Explicit expected-value checks for questions with a known-correct graph
# answer, so regressions are caught by assertion instead of the manual
# eyeballing every previous Phase 3.5 iteration was checked against.
EXPECTED = {
    "When is pump P-102 due for its next inspection?": {
        "answer_contains": "2026-04-12",
        "citation_page": 1,
    },
}


def main() -> None:
    failures = []

    for question in TEST_QUESTIONS:
        print("=" * 80)
        print(f"QUESTION: {question}")
        print("=" * 80)

        result = answer_question(question)

        print("\nANSWER:")
        print(result.answer)

        print("\nCITATIONS USED:")
        if result.citations:
            for c in result.citations:
                print(f"  - {c.source_filename}, page {c.page_number}")
        else:
            print("  (none)")

        print("\nGRAPH CITATIONS USED:")
        if result.graph_citations:
            for c in result.graph_citations:
                print(f"  - {c.source_filename}, page {c.page_number}")
        else:
            print("  (none)")

        expected = EXPECTED.get(question)
        if expected:
            value_ok = expected["answer_contains"] in result.answer
            all_citations = result.citations + result.graph_citations
            page_ok = any(c.page_number == expected["citation_page"] for c in all_citations)
            status = "PASS" if (value_ok and page_ok) else "FAIL"
            print(f"\n[{status}] value_ok={value_ok} page_ok={page_ok}")
            if status == "FAIL":
                failures.append(question)

        print()

    checked = len(EXPECTED)
    if failures:
        print(f"FAILED: {len(failures)}/{checked} checked question(s)")
        sys.exit(1)
    elif checked:
        print(f"PASSED: {checked}/{checked} checked question(s)")


if __name__ == "__main__":
    main()
