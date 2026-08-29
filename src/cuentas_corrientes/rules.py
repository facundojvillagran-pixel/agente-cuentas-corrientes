from decimal import Decimal, ROUND_HALF_UP

from .domain import ReviewRequired


def _decimal(value: Decimal | int | str) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def provisional_freight_amount(
    *,
    gross_kg: Decimal | int | str | None = None,
    tare_kg: Decimal | int | str | None = None,
    manual_tonnes: Decimal | int | str | None = None,
    invoice_tonnes: Decimal | int | str | None = None,
    tariff_per_tonne: Decimal | int | str,
) -> Decimal:
    """Calculate freight without adding VAT.

    Priority: approved manual tonnes, invoice tonnes, then gross minus tare.
    """
    if manual_tonnes is not None:
        tonnes = _decimal(manual_tonnes)
    elif invoice_tonnes is not None:
        tonnes = _decimal(invoice_tonnes)
    elif gross_kg is not None and tare_kg is not None:
        tonnes = (_decimal(gross_kg) - _decimal(tare_kg)) / Decimal("1000")
    else:
        raise ReviewRequired("Freight weight is missing")
    if tonnes <= 0:
        raise ReviewRequired("Freight net weight must be positive")
    return _money(tonnes * _decimal(tariff_per_tonne))


def fuel_advance_amount(*, litres: Decimal | int | str, last_paid_unit_price: Decimal | int | str | None) -> Decimal:
    if last_paid_unit_price is None:
        raise ReviewRequired("A paid fuel invoice or approved manual price is required")
    litres_value = _decimal(litres)
    price = _decimal(last_paid_unit_price)
    if litres_value <= 0 or price <= 0:
        raise ReviewRequired("Fuel litres and unit price must be positive")
    return _money(litres_value * price)

