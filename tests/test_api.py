from fastapi.testclient import TestClient

from cuentas_corrientes.api import _accounts, app


client = TestClient(app)


def setup_function() -> None:
    _accounts.clear()


def test_account_workflow_separates_confirmed_and_projected_balance() -> None:
    created = client.post("/accounts", json={"company": "Agro Norte SA", "counterparty": "Transporte Ruiz"})
    assert created.status_code == 201
    account_id = created.json()["id"]

    confirmed = {
        "operation_date": "2026-07-01",
        "description": "Saldo inicial",
        "amount": "500000",
        "currency": "ARS",
        "direction": "counterparty",
        "status": "confirmed",
        "evidence_kind": "spreadsheet",
        "evidence_reference": "saldo-inicial.csv",
        "source_hash": "source-1",
    }
    pending = {
        "operation_date": "2026-07-22",
        "description": "Viaje L2",
        "amount": "542100",
        "currency": "ARS",
        "direction": "counterparty",
        "status": "review",
        "evidence_kind": "conversation",
        "evidence_reference": "mensaje-22-07",
        "source_hash": "source-2",
    }
    assert client.post(f"/accounts/{account_id}/movements", json=confirmed).status_code == 201
    assert client.post(f"/accounts/{account_id}/movements", json=pending).status_code == 201

    response = client.get(f"/accounts/{account_id}/balance/ars")
    assert response.status_code == 200
    assert response.json() == {
        "currency": "ARS",
        "confirmed_label": "A favor de la contraparte",
        "confirmed_amount": "500000.00",
        "projected_label": "A favor de la contraparte",
        "projected_amount": "1042100.00",
        "pending_count": 1,
    }


def test_duplicate_source_is_grouped() -> None:
    account_id = client.post("/accounts", json={"company": "A", "counterparty": "B"}).json()["id"]
    payload = {
        "operation_date": "2026-07-20",
        "description": "Transferencia",
        "amount": "700000",
        "currency": "ARS",
        "direction": "company",
        "status": "confirmed",
        "evidence_kind": "bank",
        "evidence_reference": "TR-002",
        "source_hash": "identical-document-hash",
    }
    first = client.post(f"/accounts/{account_id}/movements", json=payload)
    second = client.post(f"/accounts/{account_id}/movements", json={**payload, "description": "Copia"})
    assert first.json()["added"] is True
    assert second.json()["added"] is False

