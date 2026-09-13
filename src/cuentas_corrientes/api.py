from __future__ import annotations

import logging
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from . import files as file_security
from .batches import (
    AccountRule,
    BatchConfirmError,
    ImportBatch,
    apply_answer,
    build_summary,
    confirm_batch,
    create_batch,
    exclude_line_items,
)
from .documents import PendingUpload, build_movement_from_resolved_fields, process_document_upload
from .domain import Account, Direction, Evidence, Movement, MovementStatus, ReviewRequired
from .exports import build_excel_report, build_pdf_report
from .image_intake import validate_image
from .intake import interpret_text
from .spreadsheet_intake import ingest_spreadsheet
from .storage import AccountStore


logger = logging.getLogger("cuentas_corrientes")

app = FastAPI(title="Agente de cuentas corrientes", version="0.1.0")
_default_db = Path.cwd() / "data" / "cuentas.sqlite3"
_store = AccountStore(os.environ.get("CUENTAS_DB_PATH", str(_default_db)))
_accounts: dict[str, Account] = _store.load_all()

if _store.database_path is not None:
    _data_root = _store.database_path.parent  # e.g. data/ — already outside Git
else:
    _data_root = Path(tempfile.mkdtemp(prefix="cuentas-local-"))
_uploads_root = _data_root / "uploads"
_logos_root = _data_root / "logos"

_pending_uploads: dict[str, dict[str, PendingUpload]] = {}
for _account_id, _items in _store.load_pending_uploads().items():
    _pending_uploads[_account_id] = {item.id: item for item in _items}

_account_configs: dict[str, dict[str, str | None]] = _store.load_account_configs()

_import_batches: dict[str, dict[str, ImportBatch]] = {}
for _account_id, _batch_list in _store.load_import_batches().items():
    _import_batches[_account_id] = {b.id: b for b in _batch_list}

_account_rules: dict[str, list[AccountRule]] = _store.load_account_rules()


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Ocurrió un error inesperado. Probá de nuevo en unos segundos."})


class AccountCreate(BaseModel):
    company: str = Field(min_length=1)
    counterparty: str = Field(min_length=1)
    opening_balance_amount: Decimal | None = Field(default=None, ge=0)
    opening_balance_currency: str | None = Field(default=None, min_length=3, max_length=3)
    opening_balance_favor: Literal["empresa", "contraparte"] | None = None
    opening_balance_reference: str | None = None


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
    opening_label: str
    opening_amount: Decimal
    confirmed_label: str
    confirmed_amount: Decimal
    final_label: str
    final_amount: Decimal
    projected_label: str
    projected_amount: Decimal
    unassigned_amount: Decimal
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


class ReverseMovement(BaseModel):
    reason: str = Field(min_length=1)


class AnswerQuestion(BaseModel):
    scope: Literal["all", "selection", "period"]
    selection_ids: list[str] | None = None
    period_start: date | None = None
    period_end: date | None = None
    price: Decimal | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    effective_from: date | None = None
    evidence_reference: str | None = None
    currency_answer: str | None = Field(default=None, min_length=3, max_length=3)
    date_answer: date | None = None
    duplicate_action: Literal["exclude", "include"] | None = None


class ExcludeLineItems(BaseModel):
    line_item_ids: list[str] = Field(min_length=1)


class ResolvePendingUpload(BaseModel):
    operation_date: date
    description: str = Field(min_length=1)
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    direction: Direction
    external_reference: str | None = None


class ResolveSpreadsheetMapping(BaseModel):
    header_row_index: int = Field(ge=0)
    fecha: int = Field(ge=0)
    concepto: int = Field(ge=0)
    debe: int = Field(ge=0)
    haber: int = Field(ge=0)
    moneda: int = Field(ge=0)
    comprobante: int | None = None
    referencia: int | None = None


class AccountConfigResponse(BaseModel):
    display_name: str
    has_logo: bool


class CorrectMovement(BaseModel):
    reason: str = Field(min_length=1)
    operation_date: date | None = None
    description: str | None = Field(default=None, min_length=1)
    amount: Decimal | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    direction: Direction | None = None
    external_reference: str | None = None
    unassigned_amount: Decimal | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/accounts", status_code=201)
def create_account(payload: AccountCreate) -> dict[str, str]:
    account_id = str(uuid4())
    account = Account(payload.company, payload.counterparty)
    _accounts[account_id] = account

    if payload.opening_balance_amount and payload.opening_balance_amount > 0:
        if not payload.opening_balance_currency or not payload.opening_balance_favor:
            raise HTTPException(
                status_code=422,
                detail="Para cargar un saldo inicial hace falta indicar la moneda y a favor de quién está.",
            )
        direction = Direction.COUNTERPARTY if payload.opening_balance_favor == "contraparte" else Direction.COMPANY
        opening_movement = Movement(
            operation_date=date.today(),
            description="Saldo inicial",
            amount=payload.opening_balance_amount,
            currency=payload.opening_balance_currency,
            direction=direction,
            status=MovementStatus.CONFIRMED,
            evidence=(Evidence("manual", payload.opening_balance_reference or "saldo inicial declarado"),),
            is_opening_balance=True,
        )
        account.add_movement(opening_movement, actor="api-user")

    _store.save(account_id, account)
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
        raise HTTPException(status_code=404, detail="No encontramos esa cuenta. Elegí una cuenta válida de la lista.")
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
        raise HTTPException(status_code=422, detail=f"No se pudo cargar el movimiento: {exc}") from exc
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


@app.post("/accounts/{account_id}/movements/{movement_id}/correct")
def correct_movement(account_id: str, movement_id: str, payload: CorrectMovement) -> dict[str, str]:
    account = _account(account_id)
    changes = {
        key: value
        for key, value in payload.model_dump(exclude={"reason"}).items()
        if value is not None
    }
    if "currency" in changes:
        changes["currency"] = changes["currency"].upper()
    try:
        movement = account.correct_movement(movement_id, actor="api-user", reason=payload.reason, **changes)
        _store.save(account_id, account)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"No se pudo corregir la propuesta: {exc}") from exc
    return {"id": movement.id, "status": movement.status.value}


@app.post("/accounts/{account_id}/movements/{movement_id}/approve")
def approve_movement(account_id: str, movement_id: str) -> dict[str, str]:
    try:
        account = _account(account_id)
        movement = account.approve_movement(movement_id, actor="api-user")
        _store.save(account_id, account)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"No se pudo aprobar: {exc}") from exc
    return {"id": movement.id, "status": movement.status.value}


@app.post("/accounts/{account_id}/movements/{movement_id}/discard")
def discard_movement(account_id: str, movement_id: str, payload: DiscardMovement) -> dict[str, str]:
    try:
        account = _account(account_id)
        movement = account.discard_movement(movement_id, actor="api-user", reason=payload.reason)
        _store.save(account_id, account)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"No se pudo denegar: {exc}") from exc
    return {"id": movement.id, "status": movement.status.value}


@app.post("/accounts/{account_id}/movements/{movement_id}/reverse")
def reverse_movement(account_id: str, movement_id: str, payload: ReverseMovement) -> dict[str, str]:
    try:
        account = _account(account_id)
        counter = account.reverse_movement(movement_id, actor="api-user", reason=payload.reason)
        _store.save(account_id, account)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"No se pudo revertir: {exc}") from exc
    return {"id": counter.id, "status": counter.status.value}


@app.get("/accounts/{account_id}/movements")
def list_movements(account_id: str) -> list[dict[str, str | bool | None]]:
    account = _account(account_id)
    reversed_ids = {m.reversal_of for m in account.movements if m.reversal_of}
    return [
        {
            "id": movement.id,
            "operation_date": movement.operation_date.isoformat(),
            "description": movement.description,
            "amount": str(movement.amount),
            "currency": movement.currency,
            "direction": movement.direction.value,
            "status": movement.status.value,
            "external_reference": movement.external_reference,
            "unassigned_amount": str(movement.unassigned_amount),
            "is_opening_balance": movement.is_opening_balance,
            "reversal_of": movement.reversal_of,
            "reversed": movement.id in reversed_ids,
        }
        for movement in account.ordered_movements()
    ]


@app.get("/", response_class=HTMLResponse)
def user_interface() -> HTMLResponse:
    html_path = Path(__file__).with_name("static") / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/accounts/{account_id}/balance/{currency}", response_model=BalanceResponse)
def get_balance(account_id: str, currency: str) -> BalanceResponse:
    account = _account(account_id)
    opening_amount = account.opening_balances().get(currency.upper(), Decimal("0.00"))
    if opening_amount > 0:
        opening_label = "A favor de la empresa"
    elif opening_amount < 0:
        opening_label = "A favor de la contraparte"
    else:
        opening_label = "Sin saldo inicial cargado"
    confirmed_label, confirmed_amount = account.balance_label(currency)
    projected_label, projected_amount = account.balance_label(currency, projected=True)
    return BalanceResponse(
        currency=currency.upper(),
        opening_label=opening_label,
        opening_amount=abs(opening_amount),
        confirmed_label=confirmed_label,
        confirmed_amount=confirmed_amount,
        final_label=confirmed_label,
        final_amount=confirmed_amount,
        projected_label=projected_label,
        projected_amount=projected_amount,
        unassigned_amount=account.unassigned_total(currency),
        pending_count=sum(1 for movement in account.movements if movement.status is MovementStatus.REVIEW),
    )


def _pending(account_id: str, doc_id: str) -> PendingUpload:
    _account(account_id)
    pending = _pending_uploads.get(account_id, {}).get(doc_id)
    if not pending:
        raise HTTPException(status_code=404, detail="No encontramos ese documento pendiente de revisión.")
    return pending


def _pending_upload_view(pending: PendingUpload) -> dict[str, object]:
    return {
        "id": pending.id,
        "kind": pending.kind,
        "status": pending.status,
        "message": pending.message,
        "extracted": pending.extracted,
        "source_reference": pending.source_reference,
        "original_filename": pending.original_filename,
        "sheet_name": pending.sheet_name,
        "preview_rows": pending.preview_rows,
        "sheet_names": pending.sheet_names,
    }


@app.post("/accounts/{account_id}/documents", status_code=201)
async def upload_documents(account_id: str, files: list[UploadFile] = File(...)) -> list[dict[str, object]]:
    account = _account(account_id)
    if len(files) > file_security.MAX_BATCH_FILES:
        raise HTTPException(status_code=422, detail=f"Se admiten hasta {file_security.MAX_BATCH_FILES} archivos por vez.")

    uploads_dir = _uploads_root / account_id
    results: list[dict[str, object]] = []
    total_bytes = 0
    for upload in files:
        content = await upload.read()
        total_bytes += len(content)
        display_name = file_security.sanitize_display_name(upload.filename or "archivo")
        if len(content) > file_security.MAX_FILE_BYTES:
            results.append({"filename": display_name, "kind": "invalido", "outcome": "invalido", "message": "El archivo supera el tamaño máximo permitido (10 MB).", "movement_ids": [], "pending_upload_ids": []})
            continue
        if total_bytes > file_security.MAX_BATCH_BYTES:
            results.append({"filename": display_name, "kind": "invalido", "outcome": "invalido", "message": "El lote de archivos supera el tamaño máximo permitido.", "movement_ids": [], "pending_upload_ids": []})
            continue

        outcome, new_pendings = process_document_upload(
            account,
            account_id=account_id,
            actor="api-user",
            original_name=upload.filename or "archivo",
            content=content,
            uploads_dir=uploads_dir,
        )
        for pending in new_pendings:
            _pending_uploads.setdefault(account_id, {})[pending.id] = pending
            _store.save_pending_upload(pending)
        results.append(
            {
                "filename": outcome.filename,
                "kind": outcome.kind,
                "outcome": outcome.outcome,
                "message": outcome.message,
                "movement_ids": outcome.movement_ids,
                "pending_upload_ids": outcome.pending_upload_ids,
            }
        )

    _store.save(account_id, account)
    return results


@app.get("/accounts/{account_id}/documents")
def list_pending_documents(account_id: str) -> list[dict[str, object]]:
    _account(account_id)
    return [_pending_upload_view(pending) for pending in _pending_uploads.get(account_id, {}).values()]


@app.post("/accounts/{account_id}/documents/{doc_id}/resolve")
def resolve_pending_document(account_id: str, doc_id: str, payload: ResolvePendingUpload) -> dict[str, str]:
    account = _account(account_id)
    pending = _pending(account_id, doc_id)
    try:
        fields = {
            "operation_date": payload.operation_date.isoformat(),
            "description": payload.description,
            "amount": str(payload.amount),
            "currency": payload.currency.upper(),
            "direction": payload.direction.value,
            "external_reference": payload.external_reference,
        }
        movement = build_movement_from_resolved_fields(fields, evidence_reference=pending.source_reference)
        added = account.add_movement(movement, actor="api-user")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"No se pudo completar el documento: {exc}") from exc

    del _pending_uploads[account_id][doc_id]
    _store.delete_pending_upload(doc_id)
    _store.save(account_id, account)
    if not added:
        return {"id": movement.id, "status": "duplicate"}
    return {"id": movement.id, "status": movement.status.value}


@app.post("/accounts/{account_id}/documents/{doc_id}/resolve-mapping")
def resolve_spreadsheet_mapping(account_id: str, doc_id: str, payload: ResolveSpreadsheetMapping) -> dict[str, object]:
    account = _account(account_id)
    pending = _pending(account_id, doc_id)
    if pending.kind != "spreadsheet_mapping":
        raise HTTPException(status_code=422, detail="Este documento no necesita un mapeo de columnas.")

    mapping = {"fecha": payload.fecha, "concepto": payload.concepto, "debe": payload.debe, "haber": payload.haber, "moneda": payload.moneda}
    if payload.comprobante is not None:
        mapping["comprobante"] = payload.comprobante
    if payload.referencia is not None:
        mapping["referencia"] = payload.referencia

    try:
        content = Path(pending.stored_path).read_bytes()
        result = ingest_spreadsheet(
            content=content,
            filename=pending.original_filename,
            sheet_name=pending.sheet_name,
            forced_mapping=mapping,
            forced_header_row=payload.header_row_index,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"No se pudo volver a leer la planilla: {exc}") from exc

    created_ids: list[str] = []
    duplicate_count = 0
    new_pendings: list[PendingUpload] = []
    for row in result.ready_rows:
        movement = Movement(
            operation_date=row.operation_date,
            description=row.description,
            amount=row.amount,
            currency=row.currency,
            direction=Direction.COMPANY if row.direction == "company" else Direction.COUNTERPARTY,
            status=MovementStatus.REVIEW,
            evidence=(Evidence("spreadsheet", f"{pending.original_filename} · hoja {result.sheet_name} · fila {row.row_number}", Evidence.fingerprint(row.row_hash_seed.encode())),),
            external_reference=row.external_reference,
        )
        if account.add_movement(movement, actor="api-user"):
            created_ids.append(movement.id)
        else:
            duplicate_count += 1

    for ambiguous in result.ambiguous_rows:
        new_pending = PendingUpload(
            account_id=account_id,
            kind="spreadsheet_row",
            message=ambiguous.reason,
            extracted=ambiguous.extracted,
            source_reference=f"{pending.original_filename} · hoja {result.sheet_name} · fila {ambiguous.row_number}",
            original_filename=pending.original_filename,
            stored_path=pending.stored_path,
            sheet_name=result.sheet_name,
        )
        new_pendings.append(new_pending)
        _pending_uploads.setdefault(account_id, {})[new_pending.id] = new_pending
        _store.save_pending_upload(new_pending)

    del _pending_uploads[account_id][doc_id]
    _store.delete_pending_upload(doc_id)
    _store.save(account_id, account)
    return {"created": len(created_ids), "needs_review": len(new_pendings), "duplicates": duplicate_count}


@app.delete("/accounts/{account_id}/documents/{doc_id}")
def delete_pending_document(account_id: str, doc_id: str) -> dict[str, str]:
    _pending(account_id, doc_id)
    del _pending_uploads[account_id][doc_id]
    _store.delete_pending_upload(doc_id)
    return {"id": doc_id, "status": "removed"}


@app.post("/accounts/{account_id}/movements/approve-all-valid")
def approve_all_valid(account_id: str) -> dict[str, object]:
    account = _account(account_id)
    approved: list[str] = []
    for movement in list(account.movements):
        if movement.status is MovementStatus.REVIEW:
            account.approve_movement(movement.id, actor="api-user")
            approved.append(movement.id)
    _store.save(account_id, account)
    return {"approved": approved, "count": len(approved)}


@app.get("/accounts/{account_id}/config", response_model=AccountConfigResponse)
def get_account_config(account_id: str) -> AccountConfigResponse:
    account = _account(account_id)
    config = _account_configs.get(account_id, {})
    return AccountConfigResponse(
        display_name=config.get("display_name") or account.company,
        has_logo=bool(config.get("logo_path")),
    )


@app.post("/accounts/{account_id}/config", response_model=AccountConfigResponse)
async def update_account_config(
    account_id: str,
    display_name: str | None = Form(default=None),
    logo: UploadFile | None = File(default=None),
) -> AccountConfigResponse:
    account = _account(account_id)
    config = dict(_account_configs.get(account_id, {}))

    if display_name is not None:
        config["display_name"] = display_name.strip() or None

    if logo is not None:
        content = await logo.read()
        if len(content) > file_security.MAX_FILE_BYTES:
            raise HTTPException(status_code=422, detail="El logo supera el tamaño máximo permitido (10 MB).")
        classification = file_security.classify_upload(logo.filename or "logo.png", content)
        if classification.kind != file_security.FileKind.PHOTO:
            raise HTTPException(status_code=422, detail="El logo debe ser una imagen PNG o JPG.")
        validation = validate_image(content)
        if not validation.valid:
            raise HTTPException(status_code=422, detail="El logo no es una imagen válida.")
        old_logo = config.get("logo_path")
        stored = file_security.store_upload(content, extension=classification.extension, uploads_dir=_logos_root / account_id)
        config["logo_path"] = str(stored)
        if old_logo and Path(old_logo).exists() and old_logo != str(stored):
            Path(old_logo).unlink(missing_ok=True)

    _account_configs[account_id] = config
    _store.save_account_config(account_id, display_name=config.get("display_name"), logo_path=config.get("logo_path"))
    return AccountConfigResponse(display_name=config.get("display_name") or account.company, has_logo=bool(config.get("logo_path")))


@app.delete("/accounts/{account_id}/config/logo", response_model=AccountConfigResponse)
def delete_account_logo(account_id: str) -> AccountConfigResponse:
    account = _account(account_id)
    config = dict(_account_configs.get(account_id, {}))
    old_logo = config.get("logo_path")
    if old_logo and Path(old_logo).exists():
        Path(old_logo).unlink(missing_ok=True)
    config["logo_path"] = None
    _account_configs[account_id] = config
    _store.save_account_config(account_id, display_name=config.get("display_name"), logo_path=None)
    return AccountConfigResponse(display_name=config.get("display_name") or account.company, has_logo=False)


@app.get("/accounts/{account_id}/config/logo")
def get_account_logo(account_id: str) -> Response:
    _account(account_id)
    config = _account_configs.get(account_id, {})
    logo_path = config.get("logo_path")
    if not logo_path or not Path(logo_path).exists():
        raise HTTPException(status_code=404, detail="Esta cuenta no tiene un logo cargado.")
    content = Path(logo_path).read_bytes()
    media_type = "image/png" if logo_path.lower().endswith(".png") else "image/jpeg"
    return Response(content=content, media_type=media_type)


def _safe_download_name(account: Account, extension: str) -> str:
    slug = file_security.sanitize_display_name(account.counterparty).replace(" ", "-")
    return f"cuenta-corriente-{slug}{extension}"


@app.get("/accounts/{account_id}/export.xlsx")
def export_excel(account_id: str) -> Response:
    account = _account(account_id)
    config = _account_configs.get(account_id, {})
    display_name = config.get("display_name") or account.company
    content = build_excel_report(account, display_name=display_name, logo_bytes=None)
    filename = _safe_download_name(account, ".xlsx")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/accounts/{account_id}/export.pdf")
def export_pdf(account_id: str) -> Response:
    account = _account(account_id)
    config = _account_configs.get(account_id, {})
    display_name = config.get("display_name") or account.company
    logo_bytes = None
    logo_path = config.get("logo_path")
    if logo_path and Path(logo_path).exists():
        logo_bytes = Path(logo_path).read_bytes()
    content = build_pdf_report(account, display_name=display_name, logo_bytes=logo_bytes)
    filename = _safe_download_name(account, ".pdf")
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _batch(account_id: str, batch_id: str) -> ImportBatch:
    _account(account_id)
    batch = _import_batches.get(account_id, {}).get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="No encontramos ese lote de importación.")
    return batch


def _line_item_view(item) -> dict[str, object]:
    return {
        "id": item.id,
        "kind": item.kind,
        "status": item.status,
        "fields": item.fields,
        "missing_fields": item.missing_fields,
        "reason": item.reason,
        "source_reference": item.source_reference,
        "original_filename": item.original_filename,
        "movement_id": item.movement_id,
    }


def _question_view(question) -> dict[str, object]:
    return {
        "id": question.id,
        "kind": question.kind,
        "prompt": question.prompt,
        "affected_line_item_ids": question.affected_line_item_ids,
        "answered": question.answered,
        "answer": question.answer,
    }


def _batch_view(account: Account, batch: ImportBatch) -> dict[str, object]:
    return {
        "id": batch.id,
        "status": batch.status,
        "line_items": [_line_item_view(i) for i in batch.line_items],
        "questions": [_question_view(q) for q in batch.questions],
        "summary": build_summary(account, batch),
    }


@app.post("/accounts/{account_id}/batches", status_code=201)
async def upload_batch(account_id: str, files: list[UploadFile] = File(...)) -> dict[str, object]:
    account = _account(account_id)
    if len(files) > file_security.MAX_BATCH_FILES:
        raise HTTPException(status_code=422, detail=f"Se admiten hasta {file_security.MAX_BATCH_FILES} archivos por vez.")

    collected: list[tuple[str, bytes]] = []
    total_bytes = 0
    for upload in files:
        content = await upload.read()
        total_bytes += len(content)
        if len(content) > file_security.MAX_FILE_BYTES:
            raise HTTPException(status_code=422, detail=f"'{upload.filename}' supera el tamaño máximo permitido (10 MB).")
        if total_bytes > file_security.MAX_BATCH_BYTES:
            raise HTTPException(status_code=422, detail="El lote de archivos supera el tamaño máximo permitido.")
        collected.append((upload.filename or "archivo", content))

    batch = create_batch(
        account,
        account_id=account_id,
        files=collected,
        uploads_dir=_uploads_root,
        existing_rules=_account_rules.get(account_id, []),
    )
    _import_batches.setdefault(account_id, {})[batch.id] = batch
    _store.save_import_batch(batch)
    return _batch_view(account, batch)


@app.get("/accounts/{account_id}/batches")
def list_batches(account_id: str) -> list[dict[str, str]]:
    _account(account_id)
    batches = _import_batches.get(account_id, {}).values()
    return [
        {"id": b.id, "status": b.status, "created_at": b.created_at.isoformat()}
        for b in sorted(batches, key=lambda b: b.created_at, reverse=True)
    ]


@app.get("/accounts/{account_id}/batches/{batch_id}")
def get_batch(account_id: str, batch_id: str) -> dict[str, object]:
    account = _account(account_id)
    batch = _batch(account_id, batch_id)
    return _batch_view(account, batch)


@app.get("/accounts/{account_id}/batches/{batch_id}/summary")
def get_batch_summary(account_id: str, batch_id: str) -> dict[str, object]:
    account = _account(account_id)
    batch = _batch(account_id, batch_id)
    return build_summary(account, batch)


@app.post("/accounts/{account_id}/batches/{batch_id}/questions/{question_id}/answer")
def answer_batch_question(account_id: str, batch_id: str, question_id: str, payload: AnswerQuestion) -> dict[str, object]:
    account = _account(account_id)
    batch = _batch(account_id, batch_id)
    try:
        rule = apply_answer(
            batch,
            question_id,
            scope=payload.scope,
            actor="api-user",
            selection_ids=payload.selection_ids,
            period_start=payload.period_start,
            period_end=payload.period_end,
            price=payload.price,
            currency=payload.currency.upper() if payload.currency else None,
            effective_from=payload.effective_from,
            evidence_reference=payload.evidence_reference,
            currency_answer=payload.currency_answer,
            date_answer=payload.date_answer,
            duplicate_action=payload.duplicate_action,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if rule is not None:
        _account_rules.setdefault(account_id, []).append(rule)
        _store.save_account_rule(rule)
    _store.save_import_batch(batch)
    return _batch_view(account, batch)


@app.post("/accounts/{account_id}/batches/{batch_id}/exceptions/exclude")
def exclude_batch_line_items(account_id: str, batch_id: str, payload: ExcludeLineItems) -> dict[str, object]:
    account = _account(account_id)
    batch = _batch(account_id, batch_id)
    exclude_line_items(batch, payload.line_item_ids)
    _store.save_import_batch(batch)
    return _batch_view(account, batch)


@app.post("/accounts/{account_id}/batches/{batch_id}/confirm")
def confirm_import_batch(account_id: str, batch_id: str) -> dict[str, object]:
    account = _account(account_id)
    batch = _batch(account_id, batch_id)
    try:
        new_account, confirmed_ids = confirm_batch(account, batch, actor="api-user")
    except BatchConfirmError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _store.save(account_id, new_account)
    # Only after a successful save do we let the confirmation take effect in memory,
    # so a failure here never leaves the account (or the batch) in a partial state.
    _accounts[account_id] = new_account
    batch.status = "confirmed"
    batch.confirmed_at = datetime.now(timezone.utc)
    _store.save_import_batch(batch)
    return {"confirmed_count": len(confirmed_ids), "movement_ids": confirmed_ids, "batch": _batch_view(new_account, batch)}


@app.get("/accounts/{account_id}/rules")
def list_account_rules(account_id: str) -> list[dict[str, str]]:
    _account(account_id)
    return [
        {
            "id": rule.id,
            "rule_type": rule.rule_type,
            "value": str(rule.value),
            "currency": rule.currency,
            "effective_from": rule.effective_from.isoformat(),
            "evidence_reference": rule.evidence_reference,
            "confirmed_by": rule.confirmed_by,
        }
        for rule in _account_rules.get(account_id, [])
    ]
