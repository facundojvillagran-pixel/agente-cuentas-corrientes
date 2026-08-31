from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from typing import Iterable
from uuid import uuid4


class Direction(str, Enum):
    COMPANY = "company"
    COUNTERPARTY = "counterparty"
    INFORMATIONAL = "informational"


class MovementStatus(str, Enum):
    CONFIRMED = "confirmed"
    REVIEW = "review"
    DISCARDED = "discarded"


class ReviewRequired(ValueError):
    pass


def _money(value: Decimal | int | str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class Evidence:
    kind: str
    reference: str
    source_hash: str | None = None

    @staticmethod
    def fingerprint(content: bytes) -> str:
        return sha256(content).hexdigest()


@dataclass(frozen=True)
class Movement:
    operation_date: date
    description: str
    amount: Decimal
    currency: str
    direction: Direction
    status: MovementStatus
    evidence: tuple[Evidence, ...]
    external_reference: str | None = None
    subaccount: str = "general"
    unassigned_amount: Decimal = Decimal("0.00")
    id: str = field(default_factory=lambda: str(uuid4()))
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", _money(self.amount))
        object.__setattr__(self, "unassigned_amount", _money(self.unassigned_amount))
        object.__setattr__(self, "currency", self.currency.upper())
        if self.amount < 0:
            raise ValueError("Movement amount cannot be negative")
        if self.direction is not Direction.INFORMATIONAL and not self.evidence:
            raise ReviewRequired("A monetary movement requires evidence")
        if self.unassigned_amount > self.amount:
            raise ValueError("Unassigned amount cannot exceed movement amount")

    @property
    def signed_amount(self) -> Decimal:
        if self.direction is Direction.COMPANY:
            return self.amount
        if self.direction is Direction.COUNTERPARTY:
            return -self.amount
        return Decimal("0.00")

    def corrected(self, *, reason: str, **changes: object) -> "Movement":
        if not reason.strip():
            raise ValueError("A correction reason is required")
        return replace(self, **changes, id=str(uuid4()), version=self.version + 1)


@dataclass(frozen=True)
class Allocation:
    source_movement_id: str
    target_movement_id: str
    amount: Decimal
    approved_by: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", _money(self.amount))
        if self.amount <= 0:
            raise ValueError("Allocation amount must be positive")
        if not self.approved_by.strip():
            raise ValueError("Allocation approval identity is required")


@dataclass
class AuditEvent:
    action: str
    actor: str
    entity_id: str
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    detail: str = ""


@dataclass
class Account:
    company: str
    counterparty: str
    movements: list[Movement] = field(default_factory=list)
    allocations: list[Allocation] = field(default_factory=list)
    audit: list[AuditEvent] = field(default_factory=list)
    _source_hashes: set[str] = field(default_factory=set, init=False, repr=False)

    def add_movement(self, movement: Movement, *, actor: str) -> bool:
        hashes = {e.source_hash for e in movement.evidence if e.source_hash}
        if hashes and hashes.issubset(self._source_hashes):
            self.audit.append(AuditEvent("duplicate_grouped", actor, movement.id, detail=",".join(sorted(hashes))))
            return False
        self.movements.append(movement)
        self._source_hashes.update(hashes)
        self.audit.append(AuditEvent("movement_added", actor, movement.id))
        return True

    def approve_movement(self, movement_id: str, *, actor: str) -> Movement:
        for index, movement in enumerate(self.movements):
            if movement.id != movement_id:
                continue
            if movement.status is not MovementStatus.REVIEW:
                raise ValueError("Only a movement under review can be approved")
            approved = replace(movement, status=MovementStatus.CONFIRMED)
            self.movements[index] = approved
            self.audit.append(AuditEvent("movement_approved", actor, movement_id))
            return approved
        raise ValueError("Movement not found")

    def add_allocation(self, allocation: Allocation, *, actor: str) -> None:
        by_id = {m.id: m for m in self.movements}
        source = by_id.get(allocation.source_movement_id)
        target = by_id.get(allocation.target_movement_id)
        if not source or not target:
            raise ValueError("Allocation movements must exist in the account")
        if source.currency != target.currency:
            raise ReviewRequired("Cross-currency allocation requires an approved conversion")
        source_used = sum((a.amount for a in self.allocations if a.source_movement_id == source.id), Decimal("0"))
        target_used = sum((a.amount for a in self.allocations if a.target_movement_id == target.id), Decimal("0"))
        if source_used + allocation.amount > source.amount:
            raise ValueError("Allocation exceeds source amount")
        if target_used + allocation.amount > target.amount:
            raise ValueError("Allocation exceeds target amount")
        self.allocations.append(allocation)
        self.audit.append(AuditEvent("allocation_added", actor, target.id, detail=str(allocation.amount)))

    def balances(self, *, projected: bool = False) -> dict[str, Decimal]:
        included = {MovementStatus.CONFIRMED}
        if projected:
            included.add(MovementStatus.REVIEW)
        result: dict[str, Decimal] = {}
        for movement in self.movements:
            if movement.status not in included:
                continue
            result[movement.currency] = result.get(movement.currency, Decimal("0.00")) + movement.signed_amount
        return {currency: _money(amount) for currency, amount in sorted(result.items())}

    def balance_label(self, currency: str, *, projected: bool = False) -> tuple[str, Decimal]:
        amount = self.balances(projected=projected).get(currency.upper(), Decimal("0.00"))
        if amount > 0:
            return "A favor de la empresa", amount
        if amount < 0:
            return "A favor de la contraparte", abs(amount)
        return "Conciliado", Decimal("0.00")

    def unassigned_total(self, currency: str) -> Decimal:
        return _money(sum((m.unassigned_amount for m in self.movements if m.currency == currency.upper() and m.status is MovementStatus.CONFIRMED), Decimal("0")))

    def ordered_movements(self) -> Iterable[Movement]:
        return sorted(self.movements, key=lambda m: (m.operation_date, m.id))
