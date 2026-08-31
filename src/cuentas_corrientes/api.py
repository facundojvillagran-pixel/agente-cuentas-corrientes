from __future__ import annotations

from datetime import date
from decimal import Decimal
import os
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .domain import Account, Direction, Evidence, Movement, MovementStatus, ReviewRequired
from .intake import interpret_text
from .storage import AccountStore


app = FastAPI(title="Agente de cuentas corrientes", version="0.1.0")
_default_db = Path.cwd() / "data" / "cuentas.sqlite3"
_store = AccountStore(os.environ.get("CUENTAS_DB_PATH", str(_default_db)))
_accounts: dict[str, Account] = _store.load_all()


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


class TextIntakeCreate(BaseModel):
    text: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    received_on: date | None = None


class TextIntakeResponse(BaseModel):
    created: bool
    movement_id: str | None
    status: str
    questions: list[str]
    extracted: dict[str, str]


class DiscardMovement(BaseModel):
    reason: str = Field(min_length=1)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/accounts", status_code=201)
def create_account(payload: AccountCreate) -> dict[str, str]:
    account_id = str(uuid4())
    _accounts[account_id] = Account(payload.company, payload.counterparty)
    _store.save(account_id, _accounts[account_id])
    return {"id": account_id, "company": payload.company, "counterparty": payload.counterparty}


@app.get("/accounts")
def list_accounts() -> list[dict[str, str]]:
    return [
        {"id": account_id, "company": account.company, "counterparty": account.counterparty}
        for account_id, account in sorted(_accounts.items(), key=lambda item: item[1].counterparty.casefold())
    ]


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
        if added:
            _store.save(account_id, account)
    except (ValueError, ReviewRequired) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": movement.id, "added": added}


@app.post("/accounts/{account_id}/intake/text", response_model=TextIntakeResponse)
def intake_text(account_id: str, payload: TextIntakeCreate) -> TextIntakeResponse:
    account = _account(account_id)
    result = interpret_text(
        text=payload.text,
        source_reference=payload.source_reference,
        received_on=payload.received_on,
    )
    if result.movement is None:
        return TextIntakeResponse(
            created=False,
            movement_id=None,
            status="needs_clarification",
            questions=list(result.questions),
            extracted=result.extracted,
        )
    added = account.add_movement(result.movement, actor="api-user")
    if added:
        _store.save(account_id, account)
    return TextIntakeResponse(
        created=added,
        movement_id=result.movement.id if added else None,
        status="review" if added else "duplicate",
        questions=list(result.questions),
        extracted=result.extracted,
    )


@app.post("/accounts/{account_id}/movements/{movement_id}/approve")
def approve_movement(account_id: str, movement_id: str) -> dict[str, str]:
    try:
        account = _account(account_id)
        movement = account.approve_movement(movement_id, actor="api-user")
        _store.save(account_id, account)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": movement.id, "status": movement.status.value}


@app.post("/accounts/{account_id}/movements/{movement_id}/discard")
def discard_movement(account_id: str, movement_id: str, payload: DiscardMovement) -> dict[str, str]:
    try:
        account = _account(account_id)
        movement = account.discard_movement(movement_id, actor="api-user", reason=payload.reason)
        _store.save(account_id, account)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": movement.id, "status": movement.status.value}


@app.get("/accounts/{account_id}/movements")
def list_movements(account_id: str) -> list[dict[str, str]]:
    return [
        {
            "id": movement.id,
            "operation_date": movement.operation_date.isoformat(),
            "description": movement.description,
            "amount": str(movement.amount),
            "currency": movement.currency,
            "direction": movement.direction.value,
            "status": movement.status.value,
        }
        for movement in _account(account_id).ordered_movements()
    ]


@app.get("/", response_class=HTMLResponse)
def user_interface() -> HTMLResponse:
    html_path = Path(__file__).with_name("static") / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


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
