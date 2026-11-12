"""Step 2: analyze sentiment, urgency and escalation language.

This is a transparent, rule-based analyzer so the project works offline and every
score can be explained. The Strands agent (agent.py) adds LLM reasoning on top.
"""
from __future__ import annotations

import re
from typing import Dict, List

from .models import Signals

NEGATIVE: Dict[str, float] = {
    "unacceptable": 3, "terrible": 2, "awful": 2, "worst": 3, "frustrated": 2,
    "frustrating": 2, "angry": 2, "furious": 3, "disappointed": 2, "useless": 3,
    "ridiculous": 2, "horrible": 2, "annoyed": 1, "upset": 2, "waste": 2,
    "broken": 1, "failed": 1, "fails": 1, "nobody": 2, "ignored": 2,
    "waiting": 1, "worse": 2, "again": 1, "still": 1, "never": 1, "unhappy": 2,
    "poor": 1, "incompetent": 3, "fed up": 3, "no update": 2, "costing": 2,
}
POSITIVE: Dict[str, float] = {
    "thanks": 1, "thank you": 1, "great": 2, "appreciate": 2, "perfect": 2,
    "resolved": 2, "works": 1, "happy": 2, "helpful": 2, "excellent": 2,
    "awesome": 2, "fixed": 1, "love": 2,
}
URGENCY: Dict[str, float] = {
    "urgent": 1.0, "asap": 1.0, "immediately": 1.0, "critical": 1.0,
    "production down": 1.5, "outage": 1.5, "blocked": 1.0, "cannot work": 1.2,
    "losing money": 1.5, "deadline": 1.0, "right now": 1.0, "today": 0.5,
    "down": 0.7, "emergency": 1.5, "costing": 1.0,
}
# (phrase, is_strong). Strong = explicit churn / legal / public threat.
ESCALATION: List[tuple] = [
    ("speak to a manager", False), ("speak to your manager", False),
    ("escalate", False), ("supervisor", False), ("unacceptable", False),
    ("last time", False), ("final warning", False), ("refund", False),
    ("switch to", False), ("competitor", False), ("report you", False),
    ("cancel my", True), ("cancel the contract", True), ("cancellation", True),
    ("terminate", True), ("legal action", True), ("lawyer", True),
    ("attorney", True), ("chargeback", True), ("social media", True),
    ("leave a review", True), ("go public", True), ("take my business", True),
]

_NEGATION = r"(?:not|never|no|isn't|wasn't|don't|doesn't|didn't|hardly)\s+(?:\w+\s+)?"


def _count(text: str, term: str) -> int:
    return len(re.findall(r"\b" + re.escape(term) + r"\b", text))


def _negated(text: str, term: str) -> bool:
    return bool(re.search(_NEGATION + re.escape(term) + r"\b", text))


def analyze_text(raw: str) -> Signals:
    """Analyze one customer-authored message."""
    text = raw.lower()

    neg = 0.0
    for term, w in NEGATIVE.items():
        neg += w * _count(text, term)

    pos = 0.0
    for term, w in POSITIVE.items():
        n = _count(text, term)
        if not n:
            continue
        if _negated(text, term):      # "not happy" counts as negative
            neg += w * n
        else:
            pos += w * n

    words = re.findall(r"[A-Za-z']{3,}", raw)
    shouting = (sum(1 for w in words if w.isupper() and len(w) >= 3) / len(words)) if words else 0.0
    neg += min(raw.count("!"), 4) * 0.5 + shouting * 6

    sentiment = (pos - neg) / (pos + neg + 2.0)
    sentiment = max(-1.0, min(1.0, sentiment))

    urgency = min(1.0, sum(w * _count(text, t) for t, w in URGENCY.items()) / 2.0)

    phrases, strong = [], False
    for phrase, is_strong in ESCALATION:
        if _count(text, phrase):
            phrases.append(phrase)
            strong = strong or is_strong

    return Signals(
        sentiment=round(sentiment, 3),
        urgency=round(urgency, 3),
        escalation_phrases=phrases,
        strong_escalation=strong,
        shouting=round(shouting, 3),
    )
