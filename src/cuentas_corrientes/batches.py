from __future__ import annotations

import copy
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from .domain import Account, AuditEvent, Direction, Evidence, Movement, MovementStatus
from .files import FileKind, classify_upload, sanitize_display_name, store_upload
from .image_intake import validate_image
from .pdf_intake import ingest_pdf
from .rules import fuel_advance_amount
from .spreadsheet_intake import ingest_spreadsheet


@dataclass
class LineItem:
    batch_id: str
    kind: str  # spreadsheet_row | fuel_charge | invoice_pdf | photo | invalid
    status: str  # ready | needs_answer | exception | excluded | confirmed
    fields: dict[str, str] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    reason: str | None = None
    source_reference: str = ""
    row_hash_seed: str | None = None
    original_filename: str = ""
    id: str = field(default_factory=lambda: str(uuid4()))
    movement_id: str | None = None


@dataclass
class ConsolidatedQuestion:
    batch_id: str
    kind: str  # fuel_price | missing_currency | missing_date | possible_duplicates
    prompt: str
    affected_line_item_ids: list[str] = field(default_factory=list)
    answered: bool = False
    answer: dict | None = None
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass
class ImportBatch:
    account_id: str
    status: str = "needs_questions"  # needs_questions | ready | confirmed
    line_items: list[LineItem] = field(default_factory=list)
    questions: list[ConsolidatedQuestion] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    confirmed_at: datetime | None = None


@dataclass
class AccountRule:
    account_id: str
    rule_type: str  # "fuel_price"
    value: Decimal
    currency: str
    effective_from: date
    evidence_reference: str
    confirmed_by: str
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _hash_seed(seed: str) -> str:
    return Evidence.fingerprint(seed.encode())


def applicable_fuel_rule(rules: list[AccountRule], *, currency: str, on_date: date) -> AccountRule | None:
    candidates = [
        r for r in rules
        if r.rule_type == "fuel_price" and r.currency == currency and r.effective_from <= on_date
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.effective_from)


_QUESTION_PROMPTS = {
    "price_per_litre": lambda n: f"Encontramos {n} carga(s) de gasoil sin precio. ¿Qué precio por litro debemos usar?",
    "currency": lambda n: f"Encontramos {n} movimiento(s) sin moneda. ¿Todos corresponden a ARS?",
    "operation_date": lambda n: f"Encontramos {n} movimiento(s) sin fecha clara. ¿Qué fecha corresponde?",
}
_QUESTION_KIND_BY_MISSING_FIELD = {
    "price_per_litre": "fuel_price",
    "currency": "missing_currency",
    "operation_date": "missing_date",
}


def _generate_new_questions(batch: ImportBatch) -> None:
    already_covered = {
        item_id
        for question in batch.questions
        if not question.answered
        for item_id in question.affected_line_item_ids
    }
    by_missing: dict[str, list[LineItem]] = defaultdict(list)
    for item in batch.line_items:
        if item.status != "needs_answer" or item.id in already_covered:
            continue
        for missing in item.missing_fields:
            by_missing[missing].append(item)

    for missing, items in by_missing.items():
        batch.questions.append(
            ConsolidatedQuestion(
                batch_id=batch.id,
                kind=_QUESTION_KIND_BY_MISSING_FIELD[missing],
                prompt=_QUESTION_PROMPTS[missing](len(items)),
                affected_line_item_ids=[i.id for i in items],
            )
        )

    covered_duplicate_ids = {
        item_id for question in batch.questions if question.kind == "possible_duplicates" for item_id in question.affected_line_item_ids
    }
    duplicates = [
        item for item in batch.line_items
        if item.reason == "Posible duplicado." and item.status in ("exception",) and item.id not in covered_duplicate_ids
    ]
    if duplicates:
        batch.questions.append(
            ConsolidatedQuestion(
                batch_id=batch.id,
                kind="possible_duplicates",
                prompt=f"Encontramos {len(duplicates)} posible(s) duplicado(s). ¿Querés excluirlos?",
                affected_line_item_ids=[i.id for i in duplicates],
            )
        )


def _recompute_status(batch: ImportBatch) -> None:
    if batch.status == "confirmed":
        return
    blocking_unanswered = any(not q.answered for q in batch.questions if q.kind != "possible_duplicates")
    batch.status = "needs_questions" if blocking_unanswered else "ready"


def create_batch(
    account: Account,
    *,
    account_id: str,
    files: list[tuple[str, bytes]],
    uploads_dir: Path,
    existing_rules: list[AccountRule],
) -> ImportBatch:
    batch = ImportBatch(account_id=account_id)
    seen_hashes: set[str] = set()

    def is_duplicate(row_hash_seed: str | None) -> bool:
        if not row_hash_seed:
            return False
        if row_hash_seed in seen_hashes:
            return True
        return account.has_source_hash(_hash_seed(row_hash_seed))

    for original_name, content in files:
        display_name = sanitize_display_name(original_name)
        classification = classify_upload(original_name, content)

        if classification.kind == FileKind.INVALID:
            batch.line_items.append(
                LineItem(batch.id, "invalid", "exception", reason=classification.message or "Archivo inválido.", source_reference=display_name, original_filename=display_name)
            )
            continue

        if classification.kind == FileKind.PHOTO:
            validation = validate_image(content)
            if not validation.valid:
                batch.line_items.append(
                    LineItem(batch.id, "invalid", "exception", reason=validation.message or "Imagen inválida.", source_reference=display_name, original_filename=display_name)
                )
                continue
            store_upload(content, extension=classification.extension, uploads_dir=uploads_dir / account_id)
            batch.line_items.append(
                LineItem(
                    batch.id, "photo", "exception",
                    reason="No se pudo leer automáticamente esta imagen. Completá los datos a mano.",
                    source_reference=display_name, original_filename=display_name,
                )
            )
            continue

        if classification.kind == FileKind.SPREADSHEET:
            try:
                result = ingest_spreadsheet(content=content, filename=display_name)
            except Exception:
                batch.line_items.append(
                    LineItem(batch.id, "invalid", "exception", reason="No se pudo abrir la planilla. Verificá que sea un .xlsx válido.", source_reference=display_name, original_filename=display_name)
                )
                continue
            store_upload(content, extension=classification.extension, uploads_dir=uploads_dir / account_id)

            if not result.header_found:
                batch.line_items.append(
                    LineItem(
                        batch.id, "spreadsheet_mapping", "exception",
                        reason="No pudimos reconocer los encabezados de esta planilla. Completala desde la revisión individual.",
                        source_reference=display_name, original_filename=display_name,
                    )
                )
                continue

            for row in result.ready_rows:
                dup = is_duplicate(row.row_hash_seed)
                fields = {
                    "operation_date": row.operation_date.isoformat(),
                    "description": row.description,
                    "amount": str(row.amount),
                    "currency": row.currency,
                    "direction": row.direction,
                    "external_reference": row.external_reference or "",
                }
                seen_hashes.add(row.row_hash_seed)
                batch.line_items.append(
                    LineItem(
                        batch.id, "spreadsheet_row", "exception" if dup else "ready",
                        fields=fields, reason="Posible duplicado." if dup else None,
                        source_reference=f"{display_name} · hoja {result.sheet_name} · fila {row.row_number}",
                        row_hash_seed=row.row_hash_seed, original_filename=display_name,
                    )
                )

            for fuel_row in result.fuel_charge_rows:
                dup = is_duplicate(fuel_row.row_hash_seed)
                fields = {
                    "operation_date": fuel_row.operation_date.isoformat(),
                    "description": fuel_row.description,
                    "currency": fuel_row.currency,
                    "litres": str(fuel_row.litres),
                    "external_reference": fuel_row.external_reference or "",
                    "direction": "company",
                }
                seen_hashes.add(fuel_row.row_hash_seed)
                source_reference = f"{display_name} · hoja {result.sheet_name} · fila {fuel_row.row_number}"
                if dup:
                    batch.line_items.append(
                        LineItem(batch.id, "fuel_charge", "exception", fields=fields, missing_fields=["price_per_litre"], reason="Posible duplicado.", source_reference=source_reference, row_hash_seed=fuel_row.row_hash_seed, original_filename=display_name)
                    )
                    continue
                rule = applicable_fuel_rule(existing_rules, currency=fuel_row.currency, on_date=fuel_row.operation_date)
                if rule is not None:
                    amount = fuel_advance_amount(litres=fuel_row.litres, last_paid_unit_price=rule.value)
                    fields["amount"] = str(amount)
                    fields["price_per_litre"] = str(rule.value)
                    status = "ready"
                    missing: list[str] = []
                else:
                    status = "needs_answer"
                    missing = ["price_per_litre"]
                batch.line_items.append(
                    LineItem(batch.id, "fuel_charge", status, fields=fields, missing_fields=missing, source_reference=source_reference, row_hash_seed=fuel_row.row_hash_seed, original_filename=display_name)
                )

            for amb in result.ambiguous_rows:
                reason_lower = amb.reason.casefold()
                if "moneda" in reason_lower:
                    status, missing = "needs_answer", ["currency"]
                elif "fecha" in reason_lower:
                    status, missing = "needs_answer", ["operation_date"]
                else:
                    status, missing = "exception", []
                batch.line_items.append(
                    LineItem(
                        batch.id, "spreadsheet_row", status, missing_fields=missing, reason=amb.reason,
                        fields=amb.extracted,
                        source_reference=f"{display_name} · hoja {result.sheet_name} · fila {amb.row_number}",
                        original_filename=display_name,
                    )
                )
            continue

        # INVOICE_PDF
        try:
            pdf_result = ingest_pdf(content=content)
        except Exception:
            batch.line_items.append(
                LineItem(batch.id, "invalid", "exception", reason="No se pudo abrir el PDF. Verificá que no esté dañado.", source_reference=display_name, original_filename=display_name)
            )
            continue
        store_upload(content, extension=classification.extension, uploads_dir=uploads_dir / account_id)

        if pdf_result.is_scanned or not (pdf_result.total_amount and pdf_result.currency and pdf_result.invoice_date):
            reason = (
                "No se pudo leer el texto del PDF (parece una factura escaneada). Completá los datos a mano."
                if pdf_result.is_scanned
                else "No se pudo determinar el importe total, la moneda o la fecha de la factura. Completá los datos."
            )
            batch.line_items.append(LineItem(batch.id, "invoice_pdf", "exception", reason=reason, source_reference=display_name, original_filename=display_name))
            continue

        row_hash_seed = f"{display_name}:invoice:{pdf_result.invoice_date}:{pdf_result.total_amount}:{pdf_result.currency}"
        dup = is_duplicate(row_hash_seed)
        fields = {
            "operation_date": pdf_result.invoice_date.isoformat(),
            "description": (f"Factura {pdf_result.invoice_number}".strip() if pdf_result.invoice_number else "Factura importada"),
            "amount": str(pdf_result.total_amount),
            "currency": pdf_result.currency,
            "direction": "counterparty",
            "external_reference": pdf_result.invoice_number or "",
        }
        seen_hashes.add(row_hash_seed)
        batch.line_items.append(
            LineItem(
                batch.id, "invoice_pdf", "exception" if dup else "ready", fields=fields,
                reason="Posible duplicado." if dup else None, source_reference=display_name,
                row_hash_seed=row_hash_seed, original_filename=display_name,
            )
        )

    _generate_new_questions(batch)
    _recompute_status(batch)
    return batch


def apply_answer(
    batch: ImportBatch,
    question_id: str,
    *,
    scope: str,
    actor: str,
    selection_ids: list[str] | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    price: Decimal | None = None,
    currency: str | None = None,
    effective_from: date | None = None,
    evidence_reference: str | None = None,
    currency_answer: str | None = None,
    date_answer: date | None = None,
    duplicate_action: str | None = None,
) -> AccountRule | None:
    question = next((q for q in batch.questions if q.id == question_id), None)
    if question is None:
        raise ValueError("No encontramos esa pregunta en este lote")
    if question.answered:
        raise ValueError("Esta pregunta ya fue respondida")

    items_by_id = {item.id: item for item in batch.line_items}
    targets = [items_by_id[i] for i in question.affected_line_item_ids if i in items_by_id]
    if scope == "selection":
        if not selection_ids:
            raise ValueError("Elegí al menos un movimiento para aplicar la respuesta")
        selection = set(selection_ids)
        targets = [t for t in targets if t.id in selection]
    elif scope == "period":
        if not period_start or not period_end:
            raise ValueError("Indicá el período de vigencia")
        targets = [t for t in targets if period_start.isoformat() <= t.fields.get("operation_date", "") <= period_end.isoformat()]
    elif scope != "all":
        raise ValueError("Alcance de respuesta no reconocido")

    created_rule: AccountRule | None = None

    if question.kind == "fuel_price":
        if price is None or price <= 0 or not currency:
            raise ValueError("Indicá el precio por litro (mayor a cero) y la moneda")
        matching_targets = [t for t in targets if t.fields.get("currency") == currency]
        target_dates = [date.fromisoformat(t.fields["operation_date"]) for t in matching_targets]
        # effective_from is metadata for reusing this rule in FUTURE batches (applicable_fuel_rule);
        # it must never gate which of THIS batch's already-scoped targets get resolved now,
        # otherwise a price answered today would never apply to past charges (see plan/tests).
        rule_effective_from = effective_from or (min(target_dates) if target_dates else date.today())
        created_rule = AccountRule(
            account_id=batch.account_id, rule_type="fuel_price", value=price, currency=currency,
            effective_from=rule_effective_from,
            evidence_reference=evidence_reference or "Confirmado durante la carga del lote",
            confirmed_by=actor,
        )
        for item in matching_targets:
            litres = Decimal(item.fields["litres"])
            amount = fuel_advance_amount(litres=litres, last_paid_unit_price=price)
            item.fields["amount"] = str(amount)
            item.fields["price_per_litre"] = str(price)
            item.status = "ready"
            item.missing_fields = []
    elif question.kind == "missing_currency":
        if not currency_answer:
            raise ValueError("Indicá la moneda")
        for item in targets:
            item.fields["currency"] = currency_answer.upper()
            item.missing_fields = [m for m in item.missing_fields if m != "currency"]
            if not item.missing_fields:
                item.status = "ready"
    elif question.kind == "missing_date":
        if not date_answer:
            raise ValueError("Indicá la fecha")
        for item in targets:
            item.fields["operation_date"] = date_answer.isoformat()
            item.missing_fields = [m for m in item.missing_fields if m != "operation_date"]
            if not item.missing_fields:
                item.status = "ready"
    elif question.kind == "possible_duplicates":
        action = duplicate_action or "exclude"
        for item in targets:
            if action == "include":
                item.status = "needs_answer" if item.missing_fields else "ready"
                item.reason = None
            else:
                item.status = "excluded"
    else:
        raise ValueError("Tipo de pregunta no reconocido")

    question.answered = True
    question.answer = {"scope": scope, "duplicate_action": duplicate_action, "currency_answer": currency_answer}

    _generate_new_questions(batch)
    _recompute_status(batch)
    return created_rule


def exclude_line_items(batch: ImportBatch, line_item_ids: list[str]) -> None:
    ids = set(line_item_ids)
    for item in batch.line_items:
        if item.id in ids:
            item.status = "excluded"


def projected_final_balances(account: Account, batch: ImportBatch) -> dict[str, Decimal]:
    result = dict(account.balances())
    for item in batch.line_items:
        if item.status != "ready":
            continue
        currency = item.fields.get("currency", "ARS")
        amount = Decimal(item.fields.get("amount", "0"))
        sign = Decimal("1") if item.fields.get("direction") == "company" else Decimal("-1")
        result[currency] = result.get(currency, Decimal("0.00")) + sign * amount
    return result


def build_summary(account: Account, batch: ImportBatch) -> dict:
    ready = [i for i in batch.line_items if i.status == "ready"]
    needs_answer = [i for i in batch.line_items if i.status == "needs_answer"]
    exceptions = [i for i in batch.line_items if i.status == "exception"]
    duplicates_excluded = [i for i in batch.line_items if i.reason == "Posible duplicado." and i.status in ("exception", "excluded")]

    totals: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"debe": Decimal("0.00"), "haber": Decimal("0.00")})
    for item in ready:
        currency = item.fields.get("currency", "ARS")
        amount = Decimal(item.fields.get("amount", "0"))
        if item.fields.get("direction") == "company":
            totals[currency]["debe"] += amount
        else:
            totals[currency]["haber"] += amount

    fuel_prices = sorted(
        {
            (item.fields["currency"], item.fields["price_per_litre"])
            for item in batch.line_items
            if item.kind == "fuel_charge" and item.fields.get("price_per_litre")
        }
    )
    currencies = sorted(set(totals) | {c for c, _ in fuel_prices})
    projected = projected_final_balances(account, batch)

    return {
        "detected": len(batch.line_items),
        "ready_count": len(ready),
        "duplicates_excluded": len(duplicates_excluded),
        "exceptions_pending": len(exceptions) + len(needs_answer),
        "totals_by_currency": {c: {"debe": str(v["debe"]), "haber": str(v["haber"])} for c, v in totals.items()},
        "opening_by_currency": {c: str(account.opening_balances().get(c, Decimal("0.00"))) for c in currencies},
        "unassigned_by_currency": {c: str(account.unassigned_total(c)) for c in currencies},
        "projected_final_by_currency": {c: str(projected.get(c, Decimal("0.00"))) for c in currencies},
        "fuel_prices_applied": [{"currency": c, "price_per_litre": p} for c, p in fuel_prices],
    }


class BatchConfirmError(ValueError):
    pass


def confirm_batch(account: Account, batch: ImportBatch, *, actor: str) -> tuple[Account, list[str]]:
    if batch.status == "confirmed":
        raise BatchConfirmError("Este lote ya fue confirmado anteriormente")
    ready_items = [item for item in batch.line_items if item.status == "ready"]
    if not ready_items:
        raise BatchConfirmError("No hay movimientos listos para confirmar todavía")

    prepared: list[tuple[LineItem, Movement]] = []
    for item in ready_items:
        fields = item.fields
        try:
            evidence_kind = "spreadsheet" if item.kind in ("spreadsheet_row", "fuel_charge") else item.kind
            movement = Movement(
                operation_date=date.fromisoformat(fields["operation_date"]),
                description=fields.get("description") or "Movimiento importado",
                amount=Decimal(fields["amount"]),
                currency=fields["currency"],
                direction=Direction.COMPANY if fields.get("direction") == "company" else Direction.COUNTERPARTY,
                status=MovementStatus.CONFIRMED,
                evidence=(Evidence(evidence_kind, item.source_reference, _hash_seed(item.row_hash_seed) if item.row_hash_seed else None),),
                external_reference=fields.get("external_reference") or None,
            )
        except (ValueError, KeyError) as exc:
            raise BatchConfirmError(f"No se pudo preparar el movimiento de '{item.source_reference}': {exc}") from exc
        prepared.append((item, movement))

    working_account = copy.deepcopy(account)
    confirmed_ids: list[str] = []
    for item, movement in prepared:
        if working_account.add_movement(movement, actor=actor):
            confirmed_ids.append(movement.id)
            item.movement_id = movement.id
            item.status = "confirmed"
        else:
            item.status = "excluded"
            item.reason = "Se detectó como duplicado al confirmar."

    working_account.audit.append(AuditEvent("batch_confirmed", actor, batch.id, detail=f"{len(confirmed_ids)} movimientos"))
    return working_account, confirmed_ids
