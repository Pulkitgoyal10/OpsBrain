"""GET /compliance."""

from __future__ import annotations

from neo4j.exceptions import Neo4jError

from app.compliance.agent import ComplianceGap


def test_get_compliance_returns_empty_summary_when_no_gaps(client, mock_find_overdue_maintenance):
    mock_find_overdue_maintenance.return_value = []

    response = client.get("/compliance")

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["summary"] == "No overdue maintenance found."


def test_get_compliance_returns_gap_shape_and_severity_summary(
    client, mock_find_overdue_maintenance
):
    mock_find_overdue_maintenance.return_value = [
        ComplianceGap(
            equipment_tag="P-102",
            location="Building 3",
            next_due_date="2026-01-01",
            days_overdue=120,
            severity="high",
            issue_found="Seal leak",
            source_document="Sample Maintenance Inspection Report.pdf",
            page_number=2,
        )
    ]

    response = client.get("/compliance")

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == [
        {
            "equipment_tag": "P-102",
            "location": "Building 3",
            "next_due_date": "2026-01-01",
            "days_overdue": 120,
            "severity": "high",
            "issue_found": "Seal leak",
            "source_document": "Sample Maintenance Inspection Report.pdf",
            "page_number": 2,
        }
    ]
    assert body["summary"] == "1 overdue maintenance item(s): 1 high, 0 medium, 0 low severity."


def test_get_compliance_returns_500_on_neo4j_error(client, mock_find_overdue_maintenance):
    mock_find_overdue_maintenance.side_effect = Neo4jError("connection refused")

    response = client.get("/compliance")

    assert response.status_code == 500
