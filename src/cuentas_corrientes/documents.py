from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from .domain import Account, Direction, Evidence, Movement, MovementStatus
from .files import FileKind, classify_upload, sanitize_display_name, store_upload
from .image_intake import validate_image
from .pdf_intake import ingest_pdf
from .spreadsheet_intake import ingest_spreadsheet

REQUIRED_MOVEMENT_FIELDS = ("operation_date", "description", "amount", "currency", "direction")


@dataclass
class PendingUpload:
    account_id: str
    kind: str  # "spreadsheet_row" | "spreadsheet_mapping" | "invoice_pdf" | "photo"
    message: str
    extracted: dict[str, str] = field(default_factory=dict)
    source_reference: str = ""
    original_filename: str = ""
    stored_path: str = ""
    sheet_name: str | None = None
    preview_rows: list[list[str]] = field(default_factory=list)
    sheet_names: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "needs_review"


@dataclass
class DocumentUploadOutcome:
    filename: str
    kind: str
    outcome: str  # "movimiento_creado" | "necesita_revision" | "posible_duplicado" | "invalido"
    message: str
    movement_ids: list[str] = field(default_factory=list)
    pending_upload_ids: list[str] = field(default_factory=list)


def _direction_from_text(value: str) -> Direction:
    return Direction.COMPANY if value == "company" else Direction.COUNTERPARTY


_VAT_NOTE = re.compile(r"IVA:\s*([0-9.]+)")


def parse_vat_note(reference: str | None) -> Decimal | None:
    if not reference:
        return None
    match = _VAT_NOTE.search(reference)
    if not match:
        return None
    try:
        return Decimal(match.group(1))
    except Exception:
        return None


def process_document_upload(
    account: Account,
    *,
    account_id: str,
    actor: str,
    original_name: str,
    content: bytes,
    uploads_dir: Path,
) -> tuple[DocumentUploadOutcome, list[PendingUpload]]:
    display_name = sanitize_display_name(original_name)
    classification = classify_upload(original_name, content)

    if classification.kind == FileKind.INVALID:
        return DocumentUploadOutcome(display_name, classification.kind, "invalido", classification.message or "Archivo inválido."), []

    if classification.kind == FileKind.PHOTO:
        validation = validate_image(content)
        if not validation.valid:
            return DocumentUploadOutcome(display_name, FileKind.INVALID, "invalido", validation.message or "Imagen inválida."), []
        stored = store_upload(content, extension=classification.extension, uploads_dir=uploads_dir)
        pending = PendingUpload(
            account_id=account_id,
            kind="photo",
            message="No se pudo leer automáticamente esta imagen. Completá los datos a mano.",
            source_reference=display_name,
            original_filename=display_name,
            stored_path=str(stored),
        )
        return DocumentUploadOutcome(display_name, classification.kind, "necesita_revision", pending.message, pending_upload_ids=[pending.id]), [pending]

    if classification.kind == FileKind.SPREADSHEET:
        try:
            result = ingest_spreadsheet(content=content, filename=display_name)
        except Exception:
            return DocumentUploadOutcome(display_name, FileKind.INVALID, "invalido", "No se pudo abrir la planilla. Verificá que sea un .xlsx válido."), []

        stored = store_upload(content, extension=classification.extension, uploads_dir=uploads_dir)

        if not result.header_found:
            pending = PendingUpload(
                account_id=account_id,
                kind="spreadsheet_mapping",
                message="No pudimos reconocer los encabezados de la planilla. Elegí qué columna corresponde a cada dato.",
                source_reference=display_name,
                original_filename=display_name,
                stored_path=str(stored),
                sheet_name=result.sheet_name,
                preview_rows=result.preview.rows,
                sheet_names=result.sheet_names,
            )
            return DocumentUploadOutcome(display_name, classification.kind, "necesita_revision", pending.message, pending_upload_ids=[pending.id]), [pending]

        created_ids: list[str] = []
        duplicate_count = 0
        pendings: list[PendingUpload] = []
        for row in result.ready_rows:
            movement = Movement(
                operation_date=row.operation_date,
                description=row.description,
                amount=row.amount,
                currency=row.currency,
                direction=_direction_from_text(row.direction),
                status=MovementStatus.REVIEW,
                evidence=(Evidence("spreadsheet", f"{display_name} · hoja {result.sheet_name} · fila {row.row_number}", Evidence.fingerprint(row.row_hash_seed.encode())),),
                external_reference=row.external_reference,
            )
            added = account.add_movement(movement, actor=actor)
            if added:
                created_ids.append(movement.id)
            else:
                duplicate_count += 1

        for ambiguous in result.ambiguous_rows:
            pending = PendingUpload(
                account_id=account_id,
                kind="spreadsheet_row",
                message=ambiguous.reason,
                extracted=ambiguous.extracted,
                source_reference=f"{display_name} · hoja {result.sheet_name} · fila {ambiguous.row_number}",
                original_filename=display_name,
                stored_path=str(stored),
                sheet_name=result.sheet_name,
            )
            pendings.append(pending)

        parts = []
        if created_ids:
            parts.append(f"{len(created_ids)} fila(s) listas para revisar")
        if pendings:
            parts.append(f"{len(pendings)} fila(s) necesitan revisión")
        if duplicate_count:
            parts.append(f"{duplicate_count} fila(s) parecen duplicadas y no se cargaron de nuevo")
        message = "; ".join(parts) if parts else "No se encontraron filas con datos para importar."
        outcome = "movimiento_creado" if created_ids else ("necesita_revision" if pendings else "posible_duplicado" if duplicate_count else "invalido")
        return (
            DocumentUploadOutcome(display_name, classification.kind, outcome, message, movement_ids=created_ids, pending_upload_ids=[p.id for p in pendings]),
            pendings,
        )

    # INVOICE_PDF
    try:
        pdf_result = ingest_pdf(content=content)
    except Exception:
        return DocumentUploadOutcome(display_name, FileKind.INVALID, "invalido", "No se pudo abrir el PDF. Verificá que no esté dañado."), []

    stored = store_upload(content, extension=classification.extension, uploads_dir=uploads_dir)

    if pdf_result.is_scanned:
        pending = PendingUpload(
            account_id=account_id,
            kind="invoice_pdf",
            message="No se pudo leer el texto del PDF (parece una factura escaneada). Completá los datos a mano.",
            source_reference=display_name,
            original_filename=display_name,
            stored_path=str(stored),
        )
        return DocumentUploadOutcome(display_name, classification.kind, "necesita_revision", pending.message, pending_upload_ids=[pending.id]), [pending]

    if pdf_result.total_amount and pdf_result.currency and pdf_result.invoice_date:
        evidence_reference = display_name
        if pdf_result.vat_amount:
            evidence_reference = f"{display_name} | IVA: {pdf_result.vat_amount}"
        movement = Movement(
            operation_date=pdf_result.invoice_date,
            description=f"Factura {pdf_result.invoice_number}".strip() if pdf_result.invoice_number else "Factura importada",
            amount=pdf_result.total_amount,
            currency=pdf_result.currency,
            direction=Direction.COUNTERPARTY,
            status=MovementStatus.REVIEW,
            evidence=(Evidence("invoice_pdf", evidence_reference, Evidence.fingerprint(content)),),
            external_reference=pdf_result.invoice_number,
        )
        added = account.add_movement(movement, actor=actor)
        if added:
            return DocumentUploadOutcome(display_name, classification.kind, "movimiento_creado", "Factura interpretada y lista para revisar.", movement_ids=[movement.id]), []
        return DocumentUploadOutcome(display_name, classification.kind, "posible_duplicado", "Esta factura parece ya haber sido cargada."), []

    extracted = {
        "invoice_number": pdf_result.invoice_number or "",
        "cuit": pdf_result.cuit or "",
        "invoice_date": pdf_result.invoice_date.isoformat() if pdf_result.invoice_date else "",
        "currency": pdf_result.currency or "",
        "total_amount": str(pdf_result.total_amount) if pdf_result.total_amount else "",
        "net_amount": str(pdf_result.net_amount) if pdf_result.net_amount else "",
        "vat_amount": str(pdf_result.vat_amount) if pdf_result.vat_amount else "",
    }
    pending = PendingUpload(
        account_id=account_id,
        kind="invoice_pdf",
        message="No se pudo determinar el importe total, la moneda o la fecha de la factura. Completá los datos.",
        extracted=extracted,
        source_reference=display_name,
        original_filename=display_name,
        stored_path=str(stored),
    )
    return DocumentUploadOutcome(display_name, classification.kind, "necesita_revision", pending.message, pending_upload_ids=[pending.id]), [pending]


def build_movement_from_resolved_fields(fields: dict[str, str], *, evidence_reference: str) -> Movement:
    missing = [name for name in REQUIRED_MOVEMENT_FIELDS if not fields.get(name)]
    if missing:
        raise ValueError(f"Faltan campos obligatorios: {', '.join(missing)}")
    return Movement(
        operation_date=date.fromisoformat(fields["operation_date"]),
        description=fields["description"],
        amount=Decimal(fields["amount"]),
        currency=fields["currency"],
        direction=Direction(fields["direction"]),
        status=MovementStatus.REVIEW,
        evidence=(Evidence("document_review", evidence_reference),),
        external_reference=fields.get("external_reference") or None,
    )
