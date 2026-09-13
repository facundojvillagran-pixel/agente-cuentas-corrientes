from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from . import backup as backup_module
from .batches import AccountRule, ConsolidatedQuestion, ImportBatch, LineItem
from .documents import PendingUpload
from .domain import Account, Direction, Evidence, Movement, MovementStatus

_BACKUP_MIN_INTERVAL = timedelta(minutes=5)


class AccountStore:
    def __init__(self, database: str) -> None:
        self.database_path = None if database == ":memory:" else Path(database)
        if self.database_path is not None:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self.backups_dir = self.database_path.parent.parent / "backups"
        else:
            self.backups_dir = None
        self.connection = sqlite3.connect(database, check_same_thread=False)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS pending_uploads (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS account_config (account_id TEXT PRIMARY KEY, display_name TEXT, logo_path TEXT)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS import_batches (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS account_rules (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        self.connection.commit()
        self._last_backup_at: datetime | None = None
        self._backup(force=True)

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
                    "reversal_of": movement.reversal_of,
                    "is_opening_balance": movement.is_opening_balance,
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
        self._backup(force=False)

    def _backup(self, *, force: bool) -> None:
        if self.database_path is None or self.backups_dir is None:
            return
        now = datetime.now(timezone.utc)
        if not force and self._last_backup_at is not None and now - self._last_backup_at < _BACKUP_MIN_INTERVAL:
            return
        backup_module.create_backup(self.database_path, self.backups_dir)
        self._last_backup_at = now

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
                    reversal_of=item.get("reversal_of"),
                    is_opening_balance=item.get("is_opening_balance", False),
                    id=item["id"],
                    version=item["version"],
                )
                account.add_movement(movement, actor="storage")
            accounts[account_id] = account
        return accounts

    def save_pending_upload(self, pending: PendingUpload) -> None:
        payload = {
            "account_id": pending.account_id,
            "kind": pending.kind,
            "message": pending.message,
            "extracted": pending.extracted,
            "source_reference": pending.source_reference,
            "original_filename": pending.original_filename,
            "stored_path": pending.stored_path,
            "sheet_name": pending.sheet_name,
            "preview_rows": pending.preview_rows,
            "sheet_names": pending.sheet_names,
            "created_at": pending.created_at.isoformat(),
            "status": pending.status,
        }
        self.connection.execute(
            "INSERT INTO pending_uploads(id, account_id, payload) VALUES(?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (pending.id, pending.account_id, json.dumps(payload, ensure_ascii=False)),
        )
        self.connection.commit()

    def delete_pending_upload(self, pending_id: str) -> None:
        self.connection.execute("DELETE FROM pending_uploads WHERE id = ?", (pending_id,))
        self.connection.commit()

    def load_pending_uploads(self) -> dict[str, list[PendingUpload]]:
        result: dict[str, list[PendingUpload]] = {}
        for pending_id, account_id, raw in self.connection.execute("SELECT id, account_id, payload FROM pending_uploads"):
            payload = json.loads(raw)
            pending = PendingUpload(
                account_id=account_id,
                kind=payload["kind"],
                message=payload["message"],
                extracted=payload.get("extracted", {}),
                source_reference=payload.get("source_reference", ""),
                original_filename=payload.get("original_filename", ""),
                stored_path=payload.get("stored_path", ""),
                sheet_name=payload.get("sheet_name"),
                preview_rows=payload.get("preview_rows", []),
                sheet_names=payload.get("sheet_names", []),
                id=pending_id,
                created_at=datetime.fromisoformat(payload["created_at"]),
                status=payload.get("status", "needs_review"),
            )
            result.setdefault(account_id, []).append(pending)
        return result

    def save_account_config(self, account_id: str, *, display_name: str | None, logo_path: str | None) -> None:
        self.connection.execute(
            "INSERT INTO account_config(account_id, display_name, logo_path) VALUES(?, ?, ?) "
            "ON CONFLICT(account_id) DO UPDATE SET display_name=excluded.display_name, logo_path=excluded.logo_path",
            (account_id, display_name, logo_path),
        )
        self.connection.commit()

    def load_account_configs(self) -> dict[str, dict[str, str | None]]:
        result: dict[str, dict[str, str | None]] = {}
        for account_id, display_name, logo_path in self.connection.execute(
            "SELECT account_id, display_name, logo_path FROM account_config"
        ):
            result[account_id] = {"display_name": display_name, "logo_path": logo_path}
        return result

    def save_import_batch(self, batch: ImportBatch) -> None:
        payload = {
            "account_id": batch.account_id,
            "status": batch.status,
            "created_at": batch.created_at.isoformat(),
            "confirmed_at": batch.confirmed_at.isoformat() if batch.confirmed_at else None,
            "line_items": [
                {
                    "id": item.id,
                    "batch_id": item.batch_id,
                    "kind": item.kind,
                    "status": item.status,
                    "fields": item.fields,
                    "missing_fields": item.missing_fields,
                    "reason": item.reason,
                    "source_reference": item.source_reference,
                    "row_hash_seed": item.row_hash_seed,
                    "original_filename": item.original_filename,
                    "movement_id": item.movement_id,
                }
                for item in batch.line_items
            ],
            "questions": [
                {
                    "id": q.id,
                    "batch_id": q.batch_id,
                    "kind": q.kind,
                    "prompt": q.prompt,
                    "affected_line_item_ids": q.affected_line_item_ids,
                    "answered": q.answered,
                    "answer": q.answer,
                }
                for q in batch.questions
            ],
        }
        self.connection.execute(
            "INSERT INTO import_batches(id, account_id, payload) VALUES(?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (batch.id, batch.account_id, json.dumps(payload, ensure_ascii=False)),
        )
        self.connection.commit()

    def load_import_batches(self) -> dict[str, list[ImportBatch]]:
        result: dict[str, list[ImportBatch]] = {}
        for batch_id, account_id, raw in self.connection.execute("SELECT id, account_id, payload FROM import_batches"):
            payload = json.loads(raw)
            batch = ImportBatch(
                account_id=account_id,
                status=payload["status"],
                id=batch_id,
                created_at=datetime.fromisoformat(payload["created_at"]),
                confirmed_at=datetime.fromisoformat(payload["confirmed_at"]) if payload.get("confirmed_at") else None,
                line_items=[
                    LineItem(
                        batch_id=item["batch_id"],
                        kind=item["kind"],
                        status=item["status"],
                        fields=item.get("fields", {}),
                        missing_fields=item.get("missing_fields", []),
                        reason=item.get("reason"),
                        source_reference=item.get("source_reference", ""),
                        row_hash_seed=item.get("row_hash_seed"),
                        original_filename=item.get("original_filename", ""),
                        id=item["id"],
                        movement_id=item.get("movement_id"),
                    )
                    for item in payload["line_items"]
                ],
                questions=[
                    ConsolidatedQuestion(
                        batch_id=q["batch_id"],
                        kind=q["kind"],
                        prompt=q["prompt"],
                        affected_line_item_ids=q.get("affected_line_item_ids", []),
                        answered=q.get("answered", False),
                        answer=q.get("answer"),
                        id=q["id"],
                    )
                    for q in payload["questions"]
                ],
            )
            result.setdefault(account_id, []).append(batch)
        return result

    def delete_import_batch(self, batch_id: str) -> None:
        self.connection.execute("DELETE FROM import_batches WHERE id = ?", (batch_id,))
        self.connection.commit()

    def save_account_rule(self, rule: AccountRule) -> None:
        payload = {
            "account_id": rule.account_id,
            "rule_type": rule.rule_type,
            "value": str(rule.value),
            "currency": rule.currency,
            "effective_from": rule.effective_from.isoformat(),
            "evidence_reference": rule.evidence_reference,
            "confirmed_by": rule.confirmed_by,
            "created_at": rule.created_at.isoformat(),
        }
        self.connection.execute(
            "INSERT INTO account_rules(id, account_id, payload) VALUES(?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (rule.id, rule.account_id, json.dumps(payload, ensure_ascii=False)),
        )
        self.connection.commit()

    def load_account_rules(self) -> dict[str, list[AccountRule]]:
        result: dict[str, list[AccountRule]] = {}
        for rule_id, account_id, raw in self.connection.execute("SELECT id, account_id, payload FROM account_rules"):
            payload = json.loads(raw)
            rule = AccountRule(
                account_id=account_id,
                rule_type=payload["rule_type"],
                value=Decimal(payload["value"]),
                currency=payload["currency"],
                effective_from=date.fromisoformat(payload["effective_from"]),
                evidence_reference=payload["evidence_reference"],
                confirmed_by=payload["confirmed_by"],
                id=rule_id,
                created_at=datetime.fromisoformat(payload["created_at"]),
            )
            result.setdefault(account_id, []).append(rule)
        return result

