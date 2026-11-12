"""Core data models."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: str) -> datetime:
    value = value.replace("Z", "+00:00")
    value = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value)   # Jira style +0530 -> +05:30
    ts = datetime.fromisoformat(value)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


@dataclass
class Customer:
    id: str
    name: str
    tier: str = "standard"          # standard | business | enterprise
    prior_escalations: int = 0
    annual_value: float = 0.0


@dataclass
class Ticket:
    id: str
    customer_id: str
    subject: str = ""
    status: str = "open"            # open | resolved
    priority: str = ""              # e.g. Highest / High / Medium (from Jira)


@dataclass
class Interaction:
    id: str
    customer_id: str
    ticket_id: str
    channel: str                    # ticket | email | chat | feedback
    author: str                     # customer | agent
    text: str
    timestamp: datetime
    csat: Optional[int] = None      # 1-5, usually on feedback items

    @classmethod
    def from_dict(cls, d: Dict[str, Any], now: Optional[datetime] = None) -> "Interaction":
        """Accepts either an ISO `timestamp` or a relative `hours_ago` (handy for demo data)."""
        now = now or utcnow()
        if "timestamp" in d:
            ts = parse_ts(d["timestamp"])
        else:
            ts = now - timedelta(hours=float(d.get("hours_ago", 0)))
        return cls(
            id=d["id"],
            customer_id=d["customer_id"],
            ticket_id=d.get("ticket_id", ""),
            channel=d.get("channel", "ticket"),
            author=d.get("author", "customer"),
            text=d.get("text", ""),
            timestamp=ts,
            csat=d.get("csat"),
        )


@dataclass
class Signals:
    """Per-message analysis output."""
    sentiment: float = 0.0                       # -1 (very negative) .. +1 (very positive)
    urgency: float = 0.0                         # 0 .. 1
    escalation_phrases: List[str] = field(default_factory=list)
    strong_escalation: bool = False
    shouting: float = 0.0                        # share of ALL-CAPS words


@dataclass
class Factor:
    name: str
    value: float          # normalized 0..1
    weight: float
    contribution: float   # points added to the 0-100 score
    detail: str = ""


@dataclass
class RiskAssessment:
    customer_id: str
    customer_name: str
    tier: str
    score: float
    level: str                         # LOW | MEDIUM | HIGH | CRITICAL
    priority: float                    # score adjusted for customer tier
    factors: List[Factor]
    open_tickets: List[str]
    escalation_phrases: List[str]
    last_message: str
    assessed_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def top_factors(self, n: int = 3) -> List[Factor]:
        return sorted(self.factors, key=lambda f: f.contribution, reverse=True)[:n]


@dataclass
class Action:
    action: str
    owner: str
    due_minutes: int
    reason: str
