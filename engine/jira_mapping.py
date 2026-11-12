"""Turn Jira issues (REST JSON) into the Customer / Ticket / Interaction model used for scoring."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .models import Customer, Interaction, Ticket, parse_ts
from .store import DataStore

PRIOR_ESCALATION_LABELS = {"escalated", "escalation"}


def load_issues_file(path: str | Path) -> List[dict]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return raw["issues"] if isinstance(raw, dict) else raw


def _email(user: Optional[dict]) -> str:
    return ((user or {}).get("emailAddress") or "").lower()


def _is_support(user: Optional[dict], reporter: Optional[dict], support_domains: Sequence[str]) -> bool:
    """Staff vs customer.
    1. JIRA_SUPPORT_DOMAINS set  -> staff if the e-mail domain is in the list.
    2. Not set                   -> staff if the e-mail domain differs from the reporter's (external customers).
    3. No e-mail visible         -> staff unless it is the reporter (matched by accountId).
    For an internal help desk where everyone shares one domain, set JIRA_SUPPORT_DOMAINS."""
    email = _email(user)
    if email and support_domains:
        return email.split("@")[-1] in support_domains
    r_email = _email(reporter)
    if email and r_email:
        return email.split("@")[-1] != r_email.split("@")[-1]
    if user and reporter and user.get("accountId") and user.get("accountId") == reporter.get("accountId"):
        return False
    return True


def _customer(fields: dict) -> tuple[str, str, str]:
    """(customer_id, display name, tier). Priority: label customer-<x>, else reporter's e-mail domain."""
    labels = [l.lower() for l in fields.get("labels") or []]
    tier = next((l[5:] for l in labels if l.startswith("tier-")), "standard")
    cust = next((l[len("customer-"):] for l in labels if l.startswith("customer-")), "")
    if not cust:
        reporter = fields.get("reporter") or {}
        email = _email(reporter)
        cust = email.split("@")[-1] if email else (reporter.get("accountId") or reporter.get("displayName") or "unknown")
    return cust, cust.replace("-", " ").title() if "." not in cust else cust, tier


def load_issues_into_store(
    store: DataStore,
    issues: Iterable[dict],
    support_domains: Sequence[str] = (),
    risk_label: str = "escalation-risk",
    rebase_to: Optional[datetime] = None,
) -> Dict[str, int]:
    """Fill `store` from Jira issues. `rebase_to` shifts all timestamps so the newest one equals that
    moment (used for bundled sample data so the demo always looks 'live')."""
    support_domains = [d.lower() for d in support_domains]
    parsed: List[tuple] = []
    skipped = 0
    for issue in issues:
        f = issue.get("fields", {})
        if risk_label in (f.get("labels") or []):      # never analyse our own escalation tickets
            skipped += 1
            continue
        parsed.append((issue["key"], f))

    all_ts = [parse_ts(f["created"]) for _, f in parsed if f.get("created")]
    for _, f in parsed:
        all_ts += [parse_ts(c["created"]) for c in (f.get("comment") or {}).get("comments", []) if c.get("created")]
    shift = (rebase_to - max(all_ts)) if (rebase_to and all_ts) else timedelta(0)

    prior: Dict[str, int] = {}
    n_interactions = 0
    for key, f in parsed:
        cid, cname, tier = _customer(f)
        if cid not in store.customers:
            store.customers[cid] = Customer(id=cid, name=cname, tier=tier)
        elif tier != "standard":
            store.customers[cid].tier = tier
        labels = {l.lower() for l in f.get("labels") or []}
        if labels & PRIOR_ESCALATION_LABELS:
            prior[cid] = prior.get(cid, 0) + 1

        done = ((f.get("status") or {}).get("statusCategory") or {}).get("key") == "done"
        store.tickets[key] = Ticket(
            id=key, customer_id=cid, subject=f.get("summary", ""),
            status="resolved" if done else "open", priority=((f.get("priority") or {}).get("name") or ""))

        reporter = f.get("reporter")
        body = f"{f.get('summary', '')}. {f.get('description') or ''}".strip()
        store.interactions.append(Interaction(
            id=f"{key}-desc", customer_id=cid, ticket_id=key, channel="jira",
            author="agent" if (support_domains and _email(reporter) and _is_support(reporter, reporter, support_domains)) else "customer",
            text=body, timestamp=parse_ts(f["created"]) + shift))
        n_interactions += 1
        for c in (f.get("comment") or {}).get("comments", []):
            who = c.get("author")
            store.interactions.append(Interaction(
                id=f"{key}-c{c.get('id', n_interactions)}", customer_id=cid, ticket_id=key, channel="jira",
                author="agent" if _is_support(who, reporter, support_domains) else "customer",
                text=c.get("body") or "", timestamp=parse_ts(c["created"]) + shift))
            n_interactions += 1

    for cid, n in prior.items():
        store.customers[cid].prior_escalations = n
    store.interactions.sort(key=lambda i: i.timestamp)
    return {"issues": len(parsed), "skipped_own_escalation_tickets": skipped,
            "customers": len(store.customers), "interactions": n_interactions}
