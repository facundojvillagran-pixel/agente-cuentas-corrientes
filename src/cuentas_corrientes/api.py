from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .domain import Account, Direction, Evidence, Movement, MovementStatus, ReviewRequired


app = FastAPI(title="Agente de cuentas corrientes", version="0.1.0")
_accounts: dict[str, Account] = {}


class AccountCreate(BaseModel):
    company: str = Field(min_length=1)
    counterparty: str = Field(min_length=1)


class MovementCreate(BaseModel):
    operation_date: date
    description: str = Field(min_length=1)
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    direction: Direction
    status: MovementStatus
    evidence_kind: str = Field(min_length=1)
    evidence_reference: str = Field(min_length=1)
    source_hash: str | None = None
    external_reference: str | None = None
    subaccount: str = "general"
    unassigned_amount: Decimal = Decimal("0.00")


class BalanceResponse(BaseModel):
    currency: str
    confirmed_label: str
    confirmed_amount: Decimal
    projected_label: str
    projected_amount: Decimal
    pending_count: int


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/accounts", status_code=201)
def create_account(payload: AccountCreate) -> dict[str, str]:
    account_id = str(uuid4())
    _accounts[account_id] = Account(payload.company, payload.counterparty)
    return {"id": account_id, "company": payload.company, "counterparty": payload.counterparty}


def _account(account_id: str) -> Account:
    account = _accounts.get(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")
    return account


@app.post("/accounts/{account_id}/movements", status_code=201)
def add_movement(account_id: str, payload: MovementCreate) -> dict[str, str | bool]:
    account = _account(account_id)
    try:
        movement = Movement(
            operation_date=payload.operation_date,
            description=payload.description,
            amount=payload.amount,
            currency=payload.currency,
            direction=payload.direction,
            status=payload.status,
            evidence=(Evidence(payload.evidence_kind, payload.evidence_reference, payload.source_hash),),
            external_reference=payload.external_reference,
            subaccount=payload.subaccount,
            unassigned_amount=payload.unassigned_amount,
        )
        added = account.add_movement(movement, actor="api-user")
    except (ValueError, ReviewRequired) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": movement.id, "added": added}


@app.get("/accounts/{account_id}/balance/{currency}", response_model=BalanceResponse)
def get_balance(account_id: str, currency: str) -> BalanceResponse:
    account = _account(account_id)
    confirmed_label, confirmed_amount = account.balance_label(currency)
    projected_label, projected_amount = account.balance_label(currency, projected=True)
    return BalanceResponse(
        currency=currency.upper(),
        confirmed_label=confirmed_label,
        confirmed_amount=confirmed_amount,
        projected_label=projected_label,
        projected_amount=projected_amount,
        pending_count=sum(1 for movement in account.movements if movement.status is MovementStatus.REVIEW),
    )

