from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .domain import Direction, Evidence, Movement, MovementStatus


@dataclass(frozen=True)
class TextIntakeResult:
    movement: Movement | None
    questions: tuple[str, ...]
    extracted: dict[str, str]


_AMOUNT = re.compile(r"(?i)(ARS|USD|U\$S|US\$|\$)\s*([0-9][0-9.]*(?:,[0-9]{1,2})?)(?![0-9])")
_DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
_REFERENCE = re.compile(r"(?i)\b(CP|CTG|FACTURA|FC)\s*[:#Nº°-]*\s*([A-Z0-9-]{3,})")


def _decimal(raw: str) -> Decimal:
    normalized = raw.replace(".", "").replace(",", ".")
    return Decimal(normalized)


def _operation_date(text: str, fallback: date) -> date:
    match = _DATE.search(text)
    if not match:
        return fallback
    day, month, year = (int(part) for part in match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return fallback


def _direction(text: str) -> Direction | None:
    lowered = text.casefold()
    company_markers = ("pagamos", "transferimos", "entregamos", "cargamos", "le pagué", "le pagamos")
    counterparty_markers = ("nos pagó", "nos pago", "nos transfirió", "nos transfirio", "recibimos de")
    company = any(marker in lowered for marker in company_markers)
    counterparty = any(marker in lowered for marker in counterparty_markers)
    if company == counterparty:
        return None
    return Direction.COMPANY if company else Direction.COUNTERPARTY


def interpret_text(*, text: str, source_reference: str, received_on: date | None = None) -> TextIntakeResult:
    clean = " ".join(text.split())
    questions: list[str] = []
    extracted: dict[str, str] = {}

    amount_match = _AMOUNT.search(clean)
    amount: Decimal | None = None
    currency: str | None = None
    if amount_match:
        token, raw_amount = amount_match.groups()
        currency = "USD" if token.upper() in {"USD", "U$S", "US$"} else "ARS"
        try:
            amount = _decimal(raw_amount)
            if amount <= 0:
                amount = None
        except InvalidOperation:
            amount = None
    if amount is None:
        questions.append("¿Cuál es el importe y la moneda de la operación?")
    else:
        extracted.update(amount=str(amount.quantize(Decimal("0.01"))), currency=currency or "")

    direction = _direction(clean)
    if direction is None:
        questions.append("¿Quién entregó el dinero o valor: la empresa o la contraparte?")
    else:
        extracted["direction"] = direction.value

    operation_date = _operation_date(clean, received_on or datetime.now().date())
    extracted["operation_date"] = operation_date.isoformat()
    reference_match = _REFERENCE.search(clean)
    external_reference = " ".join(reference_match.groups()).upper() if reference_match else None
    if external_reference:
        extracted["external_reference"] = external_reference

    if amount is None or currency is None or direction is None:
        return TextIntakeResult(None, tuple(questions), extracted)

    evidence = Evidence("conversation", source_reference, Evidence.fingerprint(clean.encode("utf-8")))
    movement = Movement(
        operation_date=operation_date,
        description=clean[:180],
        amount=amount,
        currency=currency,
        direction=direction,
        status=MovementStatus.REVIEW,
        evidence=(evidence,),
        external_reference=external_reference,
        unassigned_amount=amount if "anticipo" in clean.casefold() else Decimal("0.00"),
    )
    return TextIntakeResult(movement, tuple(questions), extracted)
