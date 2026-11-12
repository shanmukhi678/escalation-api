"""Step 3: calculate an explainable escalation risk score (0-100)."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from .analyzer import analyze_text
from .models import Factor, Interaction, RiskAssessment, utcnow
from .store import DataStore

WEIGHTS = {
    "negative_sentiment": 0.20,
    "worsening_trend": 0.08,
    "urgency": 0.12,
    "escalation_language": 0.20,
    "repeat_contacts": 0.12,
    "unanswered_wait": 0.10,
    "ticket_age": 0.06,
    "history": 0.12,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

TIER_MULTIPLIER = {"enterprise": 1.2, "business": 1.0, "standard": 0.9}
LEVELS = [(70, "CRITICAL"), (50, "HIGH"), (25, "MEDIUM"), (0, "LOW")]
LEVEL_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

PRIORITY_URGENCY = {"blocker": 1.0, "critical": 1.0, "highest": 0.9, "high": 0.6}
SLA_WAIT_HOURS = 24      # a customer waiting this long unanswered maxes out that factor...
PRIORITY_SLA_HOURS = {"blocker": 4, "critical": 4, "highest": 8, "high": 12}   # ...less for urgent tickets
TICKET_AGE_HOURS = 72    # an open ticket this old maxes out that factor
STRONG_PHRASE_FLOOR = 60 # explicit legal/cancel/public threats never score below HIGH
STRONG_REPEAT_FLOOR = 70 # ...and a threat repeated across 3+ customer messages is CRITICAL


def level_for(score: float) -> str:
    for threshold, name in LEVELS:
        if score >= threshold:
            return name
    return "LOW"


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


class RiskScorer:
    def __init__(self, store: DataStore) -> None:
        self.store = store

    def assess(self, customer_id: str, now: Optional[datetime] = None) -> RiskAssessment:
        now = now or utcnow()
        customer = self.store.customers.get(customer_id)
        if customer is None:
            raise KeyError(f"Unknown customer: {customer_id}")

        open_tickets = self.store.open_tickets(customer_id)
        # Feedback/CSAT items are used for history only, not for ticket age or contact counts.
        items = [i for i in self.store.interactions_for(customer_id, open_only=True) if i.channel != "feedback"]
        cust_msgs = [i for i in items if i.author == "customer"]

        if not cust_msgs:
            return RiskAssessment(
                customer_id=customer.id, customer_name=customer.name, tier=customer.tier,
                score=0.0, level="LOW", priority=0.0, factors=[], open_tickets=[t.id for t in open_tickets],
                escalation_phrases=[], last_message="", assessed_at=now.isoformat(),
            )

        signals = [analyze_text(m.text) for m in cust_msgs]
        recent = signals[-3:]

        # 1. negative sentiment of the most recent messages
        # latest message weighs more than the recent average: the most recent mood is the best predictor
        avg_recent = 0.6 * recent[-1].sentiment + 0.4 * (sum(s.sentiment for s in recent) / len(recent))
        f_sent = _clip((1 - avg_recent) / 2 - 0.25) / 0.75   # neutral (0) -> ~0.33, very negative -> 1

        # 2. trend: is it getting worse? (needs >= 2 messages)
        if len(signals) >= 2:
            half = max(1, len(signals) // 2)
            early = sum(s.sentiment for s in signals[:half]) / half
            late = sum(s.sentiment for s in signals[half:]) / (len(signals) - half)
            f_trend = _clip((early - late) / 1.0)
        else:
            f_trend = 0.0

        # 3. urgency
        f_urg = max(s.urgency for s in recent)
        prio = max((PRIORITY_URGENCY.get(t.priority.lower(), 0.0) for t in open_tickets), default=0.0)
        f_urg = max(f_urg, prio)       # Jira priority Highest/Blocker counts as urgency

        # 4. escalation language
        phrases = sorted({p for s in signals for p in s.escalation_phrases})
        strong = any(s.strong_escalation for s in signals)
        f_lang = 1.0 if strong else _clip(len(phrases) * 0.5)

        # 5. repeat contacts on open tickets
        f_rep = _clip((len(cust_msgs) - 1) / 4)

        # 6. unanswered wait: last message is from the customer and nobody replied
        last = items[-1]
        if last.author == "customer":
            wait_h = (now - last.timestamp).total_seconds() / 3600
            sla = min([PRIORITY_SLA_HOURS.get(t.priority.lower(), SLA_WAIT_HOURS) for t in open_tickets] or [SLA_WAIT_HOURS])
            f_wait = _clip(wait_h / sla)
        else:
            wait_h, f_wait = 0.0, 0.0

        # 7. ticket age
        age_h = (now - items[0].timestamp).total_seconds() / 3600
        f_age = _clip(age_h / TICKET_AGE_HOURS)

        # 8. history: prior escalations and recent low CSAT
        csat = self.store.csat_history(customer_id)
        f_csat = 1.0 if csat and csat[-1] <= 2 else (0.5 if csat and csat[-1] == 3 else 0.0)
        f_hist = _clip(max(customer.prior_escalations / 2, f_csat))

        raw = {
            "negative_sentiment": (f_sent, f"weighted sentiment of last {len(recent)} msgs = {avg_recent:+.2f}"),
            "worsening_trend": (f_trend, "tone is deteriorating" if f_trend > 0.1 else "stable tone"),
            "urgency": (f_urg, "urgent wording detected" if f_urg > 0 else "no urgency cues"),
            "escalation_language": (f_lang, ", ".join(phrases) if phrases else "none"),
            "repeat_contacts": (f_rep, f"{len(cust_msgs)} customer messages on open tickets"),
            "unanswered_wait": (f_wait, f"waiting {wait_h:.1f}h with no reply" if f_wait else "customer has a reply"),
            "ticket_age": (f_age, f"oldest open message {age_h:.1f}h ago"),
            "history": (f_hist, f"{customer.prior_escalations} prior escalations, last CSAT={csat[-1] if csat else 'n/a'}"),
        }
        factors: List[Factor] = [
            Factor(name=k, value=round(v, 3), weight=WEIGHTS[k],
                   contribution=round(v * WEIGHTS[k] * 100, 1), detail=d)
            for k, (v, d) in raw.items()
        ]

        score = sum(f.contribution for f in factors)
        if strong:
            score = max(score, STRONG_REPEAT_FLOOR if len(cust_msgs) >= 3 else STRONG_PHRASE_FLOOR)
        score = round(min(100.0, score), 1)

        mult = TIER_MULTIPLIER.get(customer.tier, 1.0)
        return RiskAssessment(
            customer_id=customer.id,
            customer_name=customer.name,
            tier=customer.tier,
            score=score,
            level=level_for(score),
            priority=round(min(100.0, score * mult), 1),
            factors=factors,
            open_tickets=[t.id for t in open_tickets],
            escalation_phrases=phrases,
            last_message=cust_msgs[-1].text[:200],
            assessed_at=now.isoformat(),
        )

    def assess_all(self, now: Optional[datetime] = None) -> List[RiskAssessment]:
        now = now or utcnow()
        results = [self.assess(cid, now) for cid in self.store.customers]
        return sorted(results, key=lambda r: r.priority, reverse=True)
