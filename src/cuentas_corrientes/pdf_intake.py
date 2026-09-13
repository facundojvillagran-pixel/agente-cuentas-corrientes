from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from pypdf import PdfReader

_MIN_USEFUL_TEXT_CHARS = 20

_CUIT = re.compile(r"\b(\d{2}-\d{8}-\d{1})\b")
_INVOICE_NUMBER = re.compile(r"(?i)\b(FACTURA|COMPROBANTE|FC)\s*[A-Z]?\s*[:#Nº°-]*\s*([0-9]{4}-?[0-9]{8}|[A-Z0-9-]{4,})")
_DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
_TOTAL = re.compile(r"(?i)\btotal\b[:\s]*\$?\s*([0-9][0-9.]*(?:,[0-9]{1,2})?)")
_NETO = re.compile(r"(?i)neto gravado[:\s]*\$?\s*([0-9][0-9.]*(?:,[0-9]{1,2})?)")
_IVA = re.compile(r"(?i)\biva\b[:\s]*\$?\s*([0-9][0-9.]*(?:,[0-9]{1,2})?)")
_CURRENCY = re.compile(r"(?i)\b(USD|U\$S|US\$|ARS|PESOS)\b")
_TONNES = re.compile(r"(?i)([0-9]+(?:[.,][0-9]+)?)\s*(?:tn|toneladas?)\b")
_TARIFF = re.compile(r"(?i)tarifa[:\s]*\$?\s*([0-9][0-9.]*(?:,[0-9]{1,2})?)")


def _decimal(raw: str) -> Decimal | None:
    normalized = raw.replace(".", "").replace(",", ".")
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        return None
    return value if value > 0 else None


def _parse_date(text: str) -> date | None:
    match = _DATE.search(text)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


@dataclass
class PdfIngestResult:
    is_scanned: bool
    extracted_text_length: int
    invoice_date: date | None
    invoice_number: str | None
    cuit: str | None
    currency: str | None
    net_amount: Decimal | None
    vat_amount: Decimal | None
    total_amount: Decimal | None
    tonnes: Decimal | None
    tariff_per_tonne: Decimal | None


def ingest_pdf(*, content: bytes) -> PdfIngestResult:
    reader = PdfReader(io.BytesIO(content))
    text_parts = []
    for page in reader.pages:
        extracted = page.extract_text() or ""
        text_parts.append(extracted)
    text = " ".join(" ".join(text_parts).split())

    if len(text) < _MIN_USEFUL_TEXT_CHARS:
        return PdfIngestResult(True, len(text), None, None, None, None, None, None, None, None, None)

    invoice_number_match = _INVOICE_NUMBER.search(text)
    currency_match = _CURRENCY.search(text)
    currency = None
    if currency_match:
        token = currency_match.group(1).upper()
        currency = "USD" if token in {"USD", "U$S", "US$"} else "ARS" if token in {"ARS", "PESOS"} else None

    total_match = _TOTAL.search(text)
    net_match = _NETO.search(text)
    iva_match = _IVA.search(text)
    cuit_match = _CUIT.search(text)
    tonnes_match = _TONNES.search(text)
    tariff_match = _TARIFF.search(text)

    return PdfIngestResult(
        is_scanned=False,
        extracted_text_length=len(text),
        invoice_date=_parse_date(text),
        invoice_number=invoice_number_match.group(0).strip() if invoice_number_match else None,
        cuit=cuit_match.group(1) if cuit_match else None,
        currency=currency,
        net_amount=_decimal(net_match.group(1)) if net_match else None,
        vat_amount=_decimal(iva_match.group(1)) if iva_match else None,
        total_amount=_decimal(total_match.group(1)) if total_match else None,
        tonnes=_decimal(tonnes_match.group(1)) if tonnes_match else None,
        tariff_per_tonne=_decimal(tariff_match.group(1)) if tariff_match else None,
    )
