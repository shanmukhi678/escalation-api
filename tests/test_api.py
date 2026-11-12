import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.pop("API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

import api_server  # noqa: E402

client = TestClient(api_server.app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_tickets_have_threads():
    r = client.get("/tickets").json()
    assert r["count"] == 4                                   # SUP-105 is resolved
    t = next(x for x in r["tickets"] if x["ticket"] == "SUP-101")
    assert t["customer_name"].lower().startswith("northwind") and len(t["messages"]) == 5
    assert client.get("/tickets?status=all").json()["count"] == 5


def test_two_critical_customers():
    rows = client.get("/risk?min_level=CRITICAL").json()["customers"]
    assert {r["customer_id"] for r in rows} == {"northwind", "greenfield-clinic"}
    assert all(r["score"] >= 70 and r["factors"] for r in rows)


def test_levels_filter_and_detail():
    all_rows = client.get("/risk?min_level=LOW").json()["customers"]
    assert [r["level"] for r in all_rows][:2] == ["CRITICAL", "CRITICAL"] or len(all_rows) == 5
    one = client.get("/risk/apex-manufacturing").json()
    assert one["level"] in ("HIGH", "CRITICAL")
    assert client.get("/risk/nobody").status_code == 404


def test_analyze():
    r = client.post("/analyze", json={"text": "This is UNACCEPTABLE, we will cancel the contract and call our lawyer"}).json()
    assert r["sentiment"] < 0 and r["strong_escalation"] and "lawyer" in r["escalation_phrases"]


def test_mock_jira_and_email():
    j = client.post("/jira/issues", json={"summary": "[CRITICAL] Northwind", "description": "why...",
                                          "priority": "Highest", "customer_id": "northwind"})
    assert j.status_code == 201 and j.json()["key"].startswith("ESC-") and j.json()["mock"] is True
    assert client.get("/jira/issues").json()["count"] >= 1
    m = client.post("/notify/email", json={"to": ["lead@x.com"], "subject": "s", "body": f"see {j.json()['key']}"})
    assert m.status_code == 201 and client.get("/notify/outbox").json()["count"] >= 1


def test_api_key_enforced_when_set(monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret")
    assert client.get("/risk").status_code == 401
    assert client.get("/risk", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/risk", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200           # health stays open
