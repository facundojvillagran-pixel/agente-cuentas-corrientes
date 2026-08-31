from __future__ import annotations

import json
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from .domain import Account, Direction, Evidence, Movement, MovementStatus


class AccountStore:
    def __init__(self, database: str) -> None:
        if database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database, check_same_thread=False)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        self.connection.commit()

    def save(self, account_id: str, account: Account) -> None:
        payload = {
            "company": account.company,
            "counterparty": account.counterparty,
            "movements": [
                {
                    "id": movement.id,
                    "version": movement.version,
                    "operation_date": movement.operation_date.isoformat(),
                    "description": movement.description,
                    "amount": str(movement.amount),
                    "currency": movement.currency,
                    "direction": movement.direction.value,
                    "status": movement.status.value,
                    "external_reference": movement.external_reference,
                    "subaccount": movement.subaccount,
                    "unassigned_amount": str(movement.unassigned_amount),
                    "evidence": [
                        {"kind": item.kind, "reference": item.reference, "source_hash": item.source_hash}
                        for item in movement.evidence
                    ],
                }
                for movement in account.movements
            ],
        }
        self.connection.execute(
            "INSERT INTO accounts(id, payload) VALUES(?, ?) "
            "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (account_id, json.dumps(payload, ensure_ascii=False)),
        )
        self.connection.commit()

    def load_all(self) -> dict[str, Account]:
        accounts: dict[str, Account] = {}
        for account_id, raw in self.connection.execute("SELECT id, payload FROM accounts"):
            payload = json.loads(raw)
            account = Account(payload["company"], payload["counterparty"])
            for item in payload["movements"]:
                movement = Movement(
                    operation_date=date.fromisoformat(item["operation_date"]),
                    description=item["description"],
                    amount=Decimal(item["amount"]),
                    currency=item["currency"],
                    direction=Direction(item["direction"]),
                    status=MovementStatus(item["status"]),
                    evidence=tuple(Evidence(**evidence) for evidence in item["evidence"]),
                    external_reference=item["external_reference"],
                    subaccount=item["subaccount"],
                    unassigned_amount=Decimal(item["unassigned_amount"]),
                    id=item["id"],
                    version=item["version"],
                )
                account.add_movement(movement, actor="storage")
            accounts[account_id] = account
        return accounts

