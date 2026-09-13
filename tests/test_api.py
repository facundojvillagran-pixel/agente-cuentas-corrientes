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
    body = response.json()
    assert body["currency"] == "ARS"
    assert body["confirmed_label"] == "A favor de la contraparte"
    assert body["confirmed_amount"] == "500000.00"
    assert body["final_amount"] == "500000.00"
    assert body["projected_label"] == "A favor de la contraparte"
    assert body["projected_amount"] == "1042100.00"
    assert body["pending_count"] == 1
    assert body["opening_amount"] == "0.00"


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


def test_text_intake_creates_review_then_human_approves() -> None:
    account_id = client.post("/accounts", json={"company": "A", "counterparty": "B"}).json()["id"]
    response = client.post(
        f"/accounts/{account_id}/intake/text",
        json={
            "text": "El 20/07/2026 le transferimos $ 700.000 como anticipo general",
            "source_reference": "chat-ficticio-1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["status"] == "review"
    assert body["extracted"] == {
        "amount": "700000.00",
        "currency": "ARS",
        "direction": "company",
        "operation_date": "2026-07-20",
    }

    projected = client.get(f"/accounts/{account_id}/balance/ARS").json()
    assert projected["confirmed_amount"] == "0.00"
    assert projected["projected_amount"] == "700000.00"
    assert projected["pending_count"] == 1

    approved = client.post(f"/accounts/{account_id}/movements/{body['movement_id']}/approve")
    assert approved.status_code == 200
    confirmed = client.get(f"/accounts/{account_id}/balance/ARS").json()
    assert confirmed["confirmed_amount"] == "700000.00"
    assert confirmed["pending_count"] == 0


def test_text_intake_asks_instead_of_guessing_direction() -> None:
    account_id = client.post("/accounts", json={"company": "A", "counterparty": "B"}).json()["id"]
    response = client.post(
        f"/accounts/{account_id}/intake/text",
        json={"text": "Transferencia de $ 250.000", "source_reference": "chat-ficticio-2"},
    )
    assert response.status_code == 200
    assert response.json()["created"] is False
    assert response.json()["status"] == "needs_clarification"
    assert response.json()["questions"] == ["¿Quién entregó el dinero o valor: la empresa o la contraparte?"]


def test_account_with_opening_balance_reports_it_separately() -> None:
    created = client.post(
        "/accounts",
        json={
            "company": "A",
            "counterparty": "B",
            "opening_balance_amount": "300000",
            "opening_balance_currency": "ARS",
            "opening_balance_favor": "contraparte",
            "opening_balance_reference": "acuerdo previo",
        },
    )
    assert created.status_code == 201
    account_id = created.json()["id"]

    balance = client.get(f"/accounts/{account_id}/balance/ARS").json()
    assert balance["opening_label"] == "A favor de la contraparte"
    assert balance["opening_amount"] == "300000.00"
    assert balance["confirmed_amount"] == "300000.00"
    assert balance["final_amount"] == "300000.00"


def test_opening_balance_requires_currency_and_favor() -> None:
    response = client.post(
        "/accounts",
        json={"company": "A", "counterparty": "B", "opening_balance_amount": "1000"},
    )
    assert response.status_code == 422


def test_correct_movement_updates_review_proposal_before_approval() -> None:
    account_id = client.post("/accounts", json={"company": "A", "counterparty": "B"}).json()["id"]
    intake = client.post(
        f"/accounts/{account_id}/intake/text",
        json={"text": "Le pagamos $ 100.000", "source_reference": "prueba-correccion"},
    ).json()
    movement_id = intake["movement_id"]

    corrected = client.post(
        f"/accounts/{account_id}/movements/{movement_id}/correct",
        json={"reason": "El importe real era otro", "amount": "150000"},
    )
    assert corrected.status_code == 200

    balance = client.get(f"/accounts/{account_id}/balance/ARS").json()
    assert balance["projected_amount"] == "150000.00"

    approved = client.post(f"/accounts/{account_id}/movements/{movement_id}/approve")
    assert approved.status_code == 200
    balance = client.get(f"/accounts/{account_id}/balance/ARS").json()
    assert balance["confirmed_amount"] == "150000.00"


def test_correct_movement_rejects_once_confirmed() -> None:
    account_id = client.post("/accounts", json={"company": "A", "counterparty": "B"}).json()["id"]
    intake = client.post(
        f"/accounts/{account_id}/intake/text",
        json={"text": "Le pagamos $ 100.000", "source_reference": "prueba-correccion-2"},
    ).json()
    movement_id = intake["movement_id"]
    client.post(f"/accounts/{account_id}/movements/{movement_id}/approve")

    response = client.post(
        f"/accounts/{account_id}/movements/{movement_id}/correct",
        json={"reason": "tarde", "amount": "1"},
    )
    assert response.status_code == 422


def test_reverse_confirmed_movement_creates_traceable_counter_movement() -> None:
    account_id = client.post("/accounts", json={"company": "A", "counterparty": "B"}).json()["id"]
    intake = client.post(
        f"/accounts/{account_id}/intake/text",
        json={"text": "Le pagamos $ 100.000", "source_reference": "prueba-reversion"},
    ).json()
    movement_id = intake["movement_id"]
    client.post(f"/accounts/{account_id}/movements/{movement_id}/approve")

    before = client.get(f"/accounts/{account_id}/balance/ARS").json()
    assert before["confirmed_amount"] == "100000.00"

    reversed_response = client.post(
        f"/accounts/{account_id}/movements/{movement_id}/reverse",
        json={"reason": "Se cargó dos veces"},
    )
    assert reversed_response.status_code == 200

    after = client.get(f"/accounts/{account_id}/balance/ARS").json()
    assert after["confirmed_amount"] == "0.00"

    movements = client.get(f"/accounts/{account_id}/movements").json()
    original = next(m for m in movements if m["id"] == movement_id)
    counter = next(m for m in movements if m["reversal_of"] == movement_id)
    assert original["reversed"] is True
    assert original["status"] == "confirmed"
    assert counter["status"] == "confirmed"
    assert counter["amount"] == "100000.00"

    double_reverse = client.post(
        f"/accounts/{account_id}/movements/{movement_id}/reverse",
        json={"reason": "otra vez"},
    )
    assert double_reverse.status_code == 422


def test_pending_movement_can_be_discarded_without_affecting_balance() -> None:
    account_id = client.post("/accounts", json={"company": "A", "counterparty": "B"}).json()["id"]
    intake = client.post(
        f"/accounts/{account_id}/intake/text",
        json={"text": "Le pagamos $ 100.000", "source_reference": "prueba-descartar"},
    ).json()
    response = client.post(
        f"/accounts/{account_id}/movements/{intake['movement_id']}/discard",
        json={"reason": "No corresponde a esta cuenta"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "discarded"
    balance = client.get(f"/accounts/{account_id}/balance/ARS").json()
    assert balance["confirmed_amount"] == "0.00"
    assert balance["projected_amount"] == "0.00"
    assert balance["pending_count"] == 0


def test_full_fictitious_case_end_to_end() -> None:
    account_id = client.post(
        "/accounts",
        json={
            "company": "Agro Norte SA",
            "counterparty": "Transporte Ruiz",
            "opening_balance_amount": "500000",
            "opening_balance_currency": "ARS",
            "opening_balance_favor": "contraparte",
            "opening_balance_reference": "saldo-inicial-ficticio.csv",
        },
    ).json()["id"]

    balance = client.get(f"/accounts/{account_id}/balance/ARS").json()
    assert balance["opening_amount"] == "500000.00"
    assert balance["confirmed_amount"] == "500000.00"

    first_intake = client.post(
        f"/accounts/{account_id}/intake/text",
        json={"text": "El 20/07/2026 nos transfirió $ 700.000 como anticipo general", "source_reference": "chat-1"},
    ).json()
    assert first_intake["created"] is True
    corrected = client.post(
        f"/accounts/{account_id}/movements/{first_intake['movement_id']}/correct",
        json={"reason": "El importe correcto es 750.000", "amount": "750000"},
    )
    assert corrected.status_code == 200

    balance = client.get(f"/accounts/{account_id}/balance/ARS").json()
    assert balance["confirmed_amount"] == "500000.00"
    assert balance["projected_amount"] == "1250000.00"

    approved = client.post(f"/accounts/{account_id}/movements/{first_intake['movement_id']}/approve")
    assert approved.status_code == 200
    balance = client.get(f"/accounts/{account_id}/balance/ARS").json()
    assert balance["confirmed_amount"] == "1250000.00"
    assert balance["final_amount"] == "1250000.00"

    second_intake = client.post(
        f"/accounts/{account_id}/intake/text",
        json={"text": "Le pagamos $ 200.000", "source_reference": "chat-2"},
    ).json()
    discarded = client.post(
        f"/accounts/{account_id}/movements/{second_intake['movement_id']}/discard",
        json={"reason": "No corresponde a esta cuenta"},
    )
    assert discarded.status_code == 200
    balance = client.get(f"/accounts/{account_id}/balance/ARS").json()
    assert balance["confirmed_amount"] == "1250000.00"
    assert balance["pending_count"] == 0

    reversed_response = client.post(
        f"/accounts/{account_id}/movements/{first_intake['movement_id']}/reverse",
        json={"reason": "Se acordó anular el anticipo"},
    )
    assert reversed_response.status_code == 200
    balance = client.get(f"/accounts/{account_id}/balance/ARS").json()
    assert balance["confirmed_amount"] == "500000.00"

    movements = client.get(f"/accounts/{account_id}/movements").json()
    regular_movements = [m for m in movements if not m["is_opening_balance"]]
    assert len(regular_movements) == 3  # aprobado+revertido, contramovimiento, descartado
    assert any(m["reversal_of"] == first_intake["movement_id"] for m in regular_movements)
