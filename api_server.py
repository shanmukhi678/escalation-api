"""EscalationGuard data + tools API (pre-built, allowed: API endpoint, NOT an agent).

Serves bundled SAMPLE Jira-style tickets, scores escalation risk, and offers mock Jira / e-mail
endpoints so agents built on the day can run end to end without touching a real Jira or mail server.
No real customer data belongs here: this service is meant to be reachable from the public internet.

Registered in UIA as three tools:  get_risk_report (GET /risk), create_ticket (POST /jira/issues),
send_email (POST /notify/email).  Inputs are deliberately forgiving (a string where a list is
expected, extra fields, lower-case levels) because an LLM agent is the caller.
"""
import json
import os
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from engine.analyzer import analyze_text
from engine.jira_mapping import load_issues_file, load_issues_into_store
from engine.models import utcnow
from engine.scoring import LEVEL_ORDER, RiskScorer
from engine.store import DataStore

DATA_FILE = Path(os.getenv("TICKETS_FILE", Path(__file__).parent / "data" / "sample_jira_issues.json"))
SAMPLE_LAG_HOURS = 12      # sample timestamps are shifted so the newest activity is this old
DEFAULT_RECIPIENTS = ["support-leads@example.com"]

app = FastAPI(
    title="EscalationGuard Tools API",
    version="1.1.0",
    description="Tickets, escalation-risk scoring, and mock Jira/e-mail endpoints for the AgentCraftery hackathon.",
)


def require_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """If API_KEY is set, every call (except / and /health) must send it as the X-API-Key header."""
    key = os.getenv("API_KEY")
    if key and x_api_key != key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")


def _load() -> tuple:
    store = DataStore()
    load_issues_into_store(store, load_issues_file(DATA_FILE), support_domains=[],
                           rebase_to=utcnow() - timedelta(hours=SAMPLE_LAG_HOURS))
    return store, RiskScorer(store)


def _row(r) -> Dict[str, Any]:
    """A risk assessment as a dict, plus ready-to-quote reasons for the agent."""
    d = r.to_dict()
    d["top_reasons"] = [f"{f.name.replace('_', ' ')}: {f.detail}" for f in r.top_factors(3) if f.contribution > 0]
    return d


def _as_list(v: Any) -> Any:
    """Accept a real list, a JSON list in a string, or 'a@x.com, b@x.com' / 'a;b'."""
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("["):
            try:
                return json.loads(s)
            except ValueError:
                pass
        return [x.strip() for x in s.replace(";", ",").split(",") if x.strip()]
    return v


# ---------------------------------------------------------------- meta
@app.get("/", tags=["meta"], summary="Service info (no auth)")
def root() -> Dict[str, Any]:
    return {
        "service": "EscalationGuard Tools API", "version": app.version, "data": "SAMPLE data only",
        "docs": "/docs", "health": "/health",
        "tools": {"get_risk_report": "GET /risk", "create_ticket": "POST /jira/issues",
                  "send_email": "POST /notify/email"},
    }


@app.get("/health", tags=["meta"], summary="Liveness check (no auth)")
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


# ---------------------------------------------------------------- risk  (UIA tool: get_risk_report)
@app.get("/risk", tags=["risk"], dependencies=[Depends(require_key)],
         summary="Score every customer's escalation risk (0-100); most risky first")
def risk(min_level: str = Query("MEDIUM", pattern="^(?i)(LOW|MEDIUM|HIGH|CRITICAL)$",
                                description="Minimum level to return: LOW, MEDIUM, HIGH or CRITICAL"),
         limit: int = Query(20, ge=1, le=100)) -> Dict[str, Any]:
    _, scorer = _load()
    everyone = scorer.assess_all()
    counts = {lvl: sum(1 for r in everyone if r.level == lvl) for lvl in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}
    rows = [_row(r) for r in everyone if LEVEL_ORDER[r.level] >= LEVEL_ORDER[min_level.upper()]]
    return {"count": len(rows[:limit]), "level_counts": counts,
            "at_risk_total": counts["CRITICAL"] + counts["HIGH"], "customers": rows[:limit]}


@app.get("/risk/{customer_id}", tags=["risk"], dependencies=[Depends(require_key)],
         summary="Risk score and factors for one customer")
def risk_one(customer_id: str) -> Dict[str, Any]:
    _, scorer = _load()
    try:
        return _row(scorer.assess(customer_id))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown customer: {customer_id}")


class AnalyzeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(..., description="One customer message")


@app.post("/analyze", tags=["risk"], dependencies=[Depends(require_key)],
          summary="Sentiment, urgency and escalation phrases for one message")
def analyze(body: AnalyzeIn) -> Dict[str, Any]:
    return asdict(analyze_text(body.text))


# ---------------------------------------------------------------- mock Jira  (UIA tool: create_ticket)
_jira: List[Dict[str, Any]] = []
_emails: List[Dict[str, Any]] = []


class JiraIssueIn(BaseModel):
    model_config = ConfigDict(extra="ignore")          # tolerate fields an agent invents
    summary: str
    description: str
    priority: str = "High"
    labels: List[str] = []
    customer_id: Optional[str] = None
    linked_keys: List[str] = []

    _lists = field_validator("labels", "linked_keys", mode="before")(lambda cls, v: _as_list(v))


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


# ---------------------------------------------------------------- mock e-mail  (UIA tool: send_email)
class EmailIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    to: List[str] = DEFAULT_RECIPIENTS
    subject: str
    body: str

    @field_validator("to", mode="before")
    @classmethod
    def _to_list(cls, v: Any) -> Any:
        return _as_list(v) or DEFAULT_RECIPIENTS       # empty -> default recipients


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
