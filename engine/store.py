"""Step 1: collect customer interactions (tickets, emails, chats, feedback)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .models import Customer, Interaction, Ticket, utcnow

DEFAULT_DATA = Path(__file__).parent / "data" / "sample_interactions.json"


class DataStore:
    def __init__(self) -> None:
        self.customers: Dict[str, Customer] = {}
        self.tickets: Dict[str, Ticket] = {}
        self.interactions: List[Interaction] = []

    # ---- loading -------------------------------------------------------
    @classmethod
    def from_json(cls, path: Optional[str | Path] = None, now: Optional[datetime] = None) -> "DataStore":
        store = cls()
        raw = json.loads(Path(path or DEFAULT_DATA).read_text(encoding="utf-8"))
        now = now or utcnow()
        for c in raw.get("customers", []):
            store.customers[c["id"]] = Customer(**c)
        for t in raw.get("tickets", []):
            store.tickets[t["id"]] = Ticket(**t)
        for i in raw.get("interactions", []):
            store.interactions.append(Interaction.from_dict(i, now))
        store.interactions.sort(key=lambda x: x.timestamp)
        return store

    # ---- writes --------------------------------------------------------
    def add_interaction(self, interaction: Interaction) -> None:
        if interaction.customer_id not in self.customers:
            self.customers[interaction.customer_id] = Customer(id=interaction.customer_id, name=interaction.customer_id)
        if interaction.ticket_id and interaction.ticket_id not in self.tickets:
            self.tickets[interaction.ticket_id] = Ticket(id=interaction.ticket_id, customer_id=interaction.customer_id)
        self.interactions.append(interaction)
        self.interactions.sort(key=lambda x: x.timestamp)

    def resolve_ticket(self, ticket_id: str) -> bool:
        t = self.tickets.get(ticket_id)
        if not t:
            return False
        t.status = "resolved"
        return True

    # ---- reads ---------------------------------------------------------
    def open_tickets(self, customer_id: str) -> List[Ticket]:
        return [t for t in self.tickets.values() if t.customer_id == customer_id and t.status == "open"]

    def interactions_for(self, customer_id: str, open_only: bool = True) -> List[Interaction]:
        open_ids = {t.id for t in self.open_tickets(customer_id)}
        return [
            i for i in self.interactions
            if i.customer_id == customer_id and (not open_only or i.ticket_id in open_ids)
        ]

    def csat_history(self, customer_id: str) -> List[int]:
        return [i.csat for i in self.interactions if i.customer_id == customer_id and i.csat is not None]
