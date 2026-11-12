"""EscalationGuard data + tools API (pre-built, allowed: API endpoint, NOT an agent).

Serves bundled SAMPLE Jira-style tickets, scores escalation risk, and offers mock Jira / e-mail
endpoints so agents built on the day can run end to end without touching a real Jira or mail server.
No real customer data belongs here: this service is meant to be reachable from the public internet.
"""
import os
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from engine.analyzer import analyze_text
from engine.jira_mapping import load_issues_file, load_issues_into_store
from engine.models import utcnow
from engine.scoring import LEVEL_ORDER, RiskScorer
from engine.store import DataStore

DATA_FILE = Path(os.getenv("TICKETS_FILE", Path(__file__).parent / "data" / "sample_jira_issues.json"))
SAMPLE_LAG_HOURS = 12      # sample timestamps are shifted so the newest activity is this old

app = FastAPI(
    title="EscalationGuard Tools API",
    version="1.0.0",
    description="Tickets, escalation-risk scoring, and mock Jira/e-mail endpoints for the AgentCraftery hackathon.",
)


def require_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """If API_KEY is set, every call (except /health) must send it as the X-API-Key header."""
    key = os.getenv("API_KEY")
    if key and x_api_key != key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")


def _load() -> tuple:
    store = DataStore()
    load_issues_into_store(store, load_issues_file(DATA_FILE), support_domains=[],
                           rebase_to=utcnow() - timedelta(hours=SAMPLE_LAG_HOURS))
    return store, RiskScorer(store)


# ---------------------------------------------------------------- health
@app.get("/health", tags=["meta"])
def health() -> Dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------- tickets
@app.get("/tickets", tags=["tickets"], dependencies=[Depends(require_key)],
         summary="List support tickets with their full message thread")
def tickets(status: str = Query("open", pattern="^(open|resolved|all)$")) -> Dict[str, Any]:
    store, _ = _load()
    out: List[Dict[str, Any]] = []
    for t in store.tickets.values():
        if status != "all" and t.status != status:
            continue
        cust = store.customers[t.customer_id]
        out.append({
            "ticket": t.id, "subject": t.subject, "status": t.status, "priority": t.priority,
            "customer_id": cust.id, "customer_name": cust.name, "tier": cust.tier,
            "messages": [{"author": i.author, "at": i.timestamp.isoformat(), "text": i.text}
                         for i in store.interactions if i.ticket_id == t.id],
        })
    return {"count": len(out), "tickets": out}


# ---------------------------------------------------------------- risk
@app.get("/risk", tags=["risk"], dependencies=[Depends(require_key)],
         summary="Score every customer's escalation risk (0-100) and return the contributing factors")
def risk(min_level: str = Query("MEDIUM", pattern="^(?i)(LOW|MEDIUM|HIGH|CRITICAL)$"),
         limit: int = Query(20, ge=1, le=100)) -> Dict[str, Any]:
    _, scorer = _load()
    rows = [r.to_dict() for r in scorer.assess_all() if LEVEL_ORDER[r.level] >= LEVEL_ORDER[min_level.upper()]]
    return {"count": len(rows[:limit]), "customers": rows[:limit]}


@app.get("/risk/{customer_id}", tags=["risk"], dependencies=[Depends(require_key)],
         summary="Risk score and factors for one customer")
def risk_one(customer_id: str) -> Dict[str, Any]:
    _, scorer = _load()
    try:
        return scorer.assess(customer_id).to_dict()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown customer: {customer_id}")


class AnalyzeIn(BaseModel):
    text: str = Field(..., description="One customer message")


@app.post("/analyze", tags=["risk"], dependencies=[Depends(require_key)],
          summary="Sentiment, urgency and escalation phrases for one message")
def analyze(body: AnalyzeIn) -> Dict[str, Any]:
    return asdict(analyze_text(body.text))


# ---------------------------------------------------------------- mock Jira
_jira: List[Dict[str, Any]] = []
_emails: List[Dict[str, Any]] = []


class JiraIssueIn(BaseModel):
    summary: str
    description: str
    priority: str = "High"
    labels: List[str] = []
    customer_id: Optional[str] = None
    linked_keys: List[str] = []


@app.post("/jira/issues", tags=["mock-jira"], dependencies=[Depends(require_key)], status_code=201,
          summary="MOCK: create an escalation ticket (kept in memory, nothing real is created)")
def create_issue(body: JiraIssueIn) -> Dict[str, Any]:
    key = f"ESC-{101 + len(_jira)}"
    issue = {"key": key, "url": f"https://mock-jira.example.com/browse/{key}", "mock": True,
             "created_at": utcnow().isoformat(), **body.model_dump()}
    _jira.append(issue)
    return issue


@app.get("/jira/issues", tags=["mock-jira"], dependencies=[Depends(require_key)],
         summary="MOCK: list tickets created since the service started")
def list_issues() -> Dict[str, Any]:
    return {"count": len(_jira), "issues": _jira}


# ---------------------------------------------------------------- mock e-mail
class EmailIn(BaseModel):
    to: List[str]
    subject: str
    body: str


@app.post("/notify/email", tags=["mock-email"], dependencies=[Depends(require_key)], status_code=201,
          summary="MOCK: send an e-mail (stored in an outbox, nothing is sent)")
def send_email(body: EmailIn) -> Dict[str, Any]:
    msg = {"id": f"MAIL-{len(_emails) + 1}", "mock": True, "sent_at": utcnow().isoformat(), **body.model_dump()}
    _emails.append(msg)
    return msg


@app.get("/notify/outbox", tags=["mock-email"], dependencies=[Depends(require_key)],
         summary="MOCK: list e-mails 'sent' since the service started")
def outbox() -> Dict[str, Any]:
    return {"count": len(_emails), "emails": _emails}
