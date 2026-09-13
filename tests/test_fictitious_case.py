import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cuentas_corrientes import (  # noqa: E402
    Account,
    Direction,
    Evidence,
    Movement,
    MovementStatus,
    fuel_advance_amount,
    provisional_freight_amount,
)


def evidence(reference: str) -> tuple[Evidence, ...]:
    return (Evidence("fixture", reference, Evidence.fingerprint(reference.encode())),)


class FictitiousAccountTest(unittest.TestCase):
    def setUp(self) -> None:
        self.account = Account("Agro Norte SA", "Transporte Ruiz")

    def add(self, movement: Movement) -> None:
        self.assertTrue(self.account.add_movement(movement, actor="administracion"))

    def test_expected_confirmed_and_projected_balances(self) -> None:
        movements = [
            Movement(date(2026, 7, 1), "Saldo inicial", Decimal("500000"), "ARS", Direction.COUNTERPARTY, MovementStatus.CONFIRMED, evidence("saldo-inicial")),
            Movement(date(2026, 7, 8), "Factura 101", Decimal("2014650"), "ARS", Direction.COUNTERPARTY, MovementStatus.CONFIRMED, evidence("fac-101")),
            Movement(date(2026, 7, 15), "Factura 102", Decimal("2055306"), "ARS", Direction.COUNTERPARTY, MovementStatus.CONFIRMED, evidence("fac-102")),
            Movement(date(2026, 7, 5), "Gasoil 500 L", fuel_advance_amount(litres=500, last_paid_unit_price=1250), "ARS", Direction.COMPANY, MovementStatus.CONFIRMED, evidence("go-001"), unassigned_amount=625000),
            Movement(date(2026, 7, 12), "Gasoil 300 L", fuel_advance_amount(litres=300, last_paid_unit_price=1250), "ARS", Direction.COMPANY, MovementStatus.CONFIRMED, evidence("go-002"), unassigned_amount=375000),
            Movement(date(2026, 7, 8), "Transferencia factura 101", Decimal("900000"), "ARS", Direction.COMPANY, MovementStatus.CONFIRMED, evidence("tr-001")),
            Movement(date(2026, 7, 20), "Transferencia general", Decimal("700000"), "ARS", Direction.COMPANY, MovementStatus.CONFIRMED, evidence("tr-002"), unassigned_amount=700000),
            Movement(date(2026, 7, 18), "V007 sin factura", provisional_freight_amount(manual_tonnes="30.600", tariff_per_tonne=19500), "ARS", Direction.COUNTERPARTY, MovementStatus.CONFIRMED, evidence("v007")),
            Movement(date(2026, 7, 22), "V008 L2", provisional_freight_amount(gross_kg=41600, tare_kg=13800, tariff_per_tonne=19500), "ARS", Direction.COUNTERPARTY, MovementStatus.REVIEW, evidence("v008")),
        ]
        for movement in movements:
            self.add(movement)

        self.assertEqual(self.account.balance_label("ARS"), ("A favor de la contraparte", Decimal("2566656.00")))
        self.assertEqual(self.account.balance_label("ARS", projected=True), ("A favor de la contraparte", Decimal("3108756.00")))
        self.assertEqual(self.account.unassigned_total("ARS"), Decimal("1700000.00"))

    def test_exact_duplicate_is_not_added_twice(self) -> None:
        source = evidence("same-transfer")
        first = Movement(date(2026, 7, 20), "Transferencia", 700000, "ARS", Direction.COMPANY, MovementStatus.CONFIRMED, source)
        copy = Movement(date(2026, 7, 20), "Copia transferencia", 700000, "ARS", Direction.COMPANY, MovementStatus.CONFIRMED, source)
        self.assertTrue(self.account.add_movement(first, actor="contador"))
        self.assertFalse(self.account.add_movement(copy, actor="contador"))
        self.assertEqual(len(self.account.movements), 1)

    def test_currencies_are_never_mixed(self) -> None:
        self.add(Movement(date(2026, 7, 1), "ARS", 100, "ARS", Direction.COMPANY, MovementStatus.CONFIRMED, evidence("ars")))
        self.add(Movement(date(2026, 7, 1), "USD", 10, "USD", Direction.COUNTERPARTY, MovementStatus.CONFIRMED, evidence("usd")))
        self.assertEqual(self.account.balances(), {"ARS": Decimal("100.00"), "USD": Decimal("-10.00")})

    def test_correct_movement_requires_review_status_and_reason(self) -> None:
        proposal = Movement(date(2026, 7, 1), "Propuesta", 100, "ARS", Direction.COMPANY, MovementStatus.REVIEW, evidence("prop"))
        self.add(proposal)

        with self.assertRaises(ValueError):
            self.account.correct_movement(proposal.id, actor="contador", reason="", amount=Decimal("200"))

        corrected = self.account.correct_movement(proposal.id, actor="contador", reason="Importe mal cargado", amount=Decimal("200"))
        self.assertEqual(corrected.amount, Decimal("200.00"))
        self.assertEqual(corrected.id, proposal.id)
        self.assertEqual(corrected.version, proposal.version + 1)

        confirmed = self.account.approve_movement(proposal.id, actor="contador")
        with self.assertRaises(ValueError):
            self.account.correct_movement(confirmed.id, actor="contador", reason="tarde", amount=Decimal("1"))

    def test_reverse_movement_adds_counter_movement_without_touching_original(self) -> None:
        confirmed = Movement(date(2026, 7, 1), "Transferencia", 100, "ARS", Direction.COMPANY, MovementStatus.CONFIRMED, evidence("tr"))
        self.add(confirmed)
        self.assertEqual(self.account.balances(), {"ARS": Decimal("100.00")})

        counter = self.account.reverse_movement(confirmed.id, actor="contador", reason="Se cargó por error")
        self.assertEqual(counter.reversal_of, confirmed.id)
        self.assertEqual(counter.direction, Direction.COUNTERPARTY)
        self.assertEqual(counter.status, MovementStatus.CONFIRMED)
        self.assertEqual(self.account.balances(), {"ARS": Decimal("0.00")})

        original_still_present = next(m for m in self.account.movements if m.id == confirmed.id)
        self.assertEqual(original_still_present, confirmed)
        self.assertEqual(len(self.account.movements), 2)

        with self.assertRaises(ValueError):
            self.account.reverse_movement(confirmed.id, actor="contador", reason="otra vez")

    def test_reverse_movement_rejects_non_confirmed(self) -> None:
        proposal = Movement(date(2026, 7, 1), "Propuesta", 100, "ARS", Direction.COMPANY, MovementStatus.REVIEW, evidence("prop2"))
        self.add(proposal)
        with self.assertRaises(ValueError):
            self.account.reverse_movement(proposal.id, actor="contador", reason="motivo")


if __name__ == "__main__":
    unittest.main()

