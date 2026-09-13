import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from cuentas_corrientes import api as api_module
from cuentas_corrientes.api import _account_configs, _account_rules, _accounts, _import_batches, _pending_uploads, app
from cuentas_corrientes.batches import BatchConfirmError

client = TestClient(app)


def setup_function() -> None:
    _accounts.clear()
    _pending_uploads.clear()
    _account_configs.clear()
    _import_batches.clear()
    _account_rules.clear()


def _new_account(**opening) -> str:
    payload = {"company": "Agro Norte SA", "counterparty": "Transporte Ruiz", **opening}
    return client.post("/accounts", json=payload).json()["id"]


def _xlsx_bytes(headers: list[str], rows: list[list]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _upload_batch(account_id: str, filename: str, content: bytes):
    return client.post(
        f"/accounts/{account_id}/batches",
        files=[("files", (filename, content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
    )


def _gasoil_rows(count: int, *, month: str = "07", currency: str = "ARS", litres_start: int = 100) -> list[list]:
    return [
        [f"{(i % 28) + 1:02d}/{month}/2026", f"Gasoil carga {i}", None, None, currency, litres_start + i]
        for i in range(count)
    ]


def _find_question(batch: dict, kind: str) -> dict:
    return next(q for q in batch["questions"] if q["kind"] == kind and not q["answered"])


def test_fifty_fuel_charges_processed_as_single_batch_with_one_question() -> None:
    account_id = _new_account()
    rows = _gasoil_rows(50) + [["15/07/2026", "Cobro normal", None, 5000, "ARS", None]]
    content = _xlsx_bytes(["Fecha", "Concepto", "Debe", "Haber", "Moneda", "Litros"], rows)

    response = _upload_batch(account_id, "gasoil.xlsx", content)
    assert response.status_code == 201
    batch = response.json()
    assert batch["status"] == "needs_questions"
    assert len(batch["line_items"]) == 51
    fuel_questions = [q for q in batch["questions"] if q["kind"] == "fuel_price"]
    assert len(fuel_questions) == 1
    assert fuel_questions[0]["prompt"].startswith("Encontramos 50 carga(s) de gasoil sin precio")
    assert len(fuel_questions[0]["affected_line_item_ids"]) == 50

    answer = client.post(
        f"/accounts/{account_id}/batches/{batch['id']}/questions/{fuel_questions[0]['id']}/answer",
        json={"scope": "all", "price": "1250", "currency": "ARS"},
    )
    assert answer.status_code == 200
    updated = answer.json()
    assert updated["status"] == "ready"
    assert updated["summary"]["ready_count"] == 51
    assert updated["summary"]["exceptions_pending"] == 0
    assert updated["summary"]["fuel_prices_applied"] == [{"currency": "ARS", "price_per_litre": "1250"}]

    confirm = client.post(f"/accounts/{account_id}/batches/{batch['id']}/confirm")
    assert confirm.status_code == 200
    body = confirm.json()
    assert body["confirmed_count"] == 51
    assert body["batch"]["status"] == "confirmed"

    movements = client.get(f"/accounts/{account_id}/movements").json()
    assert len(movements) == 51
    assert all(m["status"] == "confirmed" for m in movements)  # never landed as individual "review" approvals

    balance = client.get(f"/accounts/{account_id}/balance/ARS").json()
    assert balance["confirmed_amount"] == "7776250.00"  # 50 * avg_litres * 1250 - 5000, verified via balances()

    rules = client.get(f"/accounts/{account_id}/rules").json()
    assert len(rules) == 1
    assert rules[0]["value"] == "1250"
    assert rules[0]["confirmed_by"] == "api-user"


def test_two_fuel_prices_for_different_periods() -> None:
    account_id = _new_account()
    rows = [[f"0{i + 1}/07/2026", f"Gasoil julio {i}", None, None, "ARS", 100] for i in range(5)]
    rows += [[f"0{i + 1}/08/2026", f"Gasoil agosto {i}", None, None, "ARS", 100] for i in range(5)]
    content = _xlsx_bytes(["Fecha", "Concepto", "Debe", "Haber", "Moneda", "Litros"], rows)
    batch = _upload_batch(account_id, "gasoil2.xlsx", content).json()

    q1 = _find_question(batch, "fuel_price")
    r1 = client.post(
        f"/accounts/{account_id}/batches/{batch['id']}/questions/{q1['id']}/answer",
        json={"scope": "period", "period_start": "2026-07-01", "period_end": "2026-07-31", "price": "1000", "currency": "ARS"},
    ).json()
    assert r1["status"] == "needs_questions"  # august rows still pending

    q2 = _find_question(r1, "fuel_price")
    r2 = client.post(
        f"/accounts/{account_id}/batches/{batch['id']}/questions/{q2['id']}/answer",
        json={"scope": "all", "price": "1100", "currency": "ARS"},
    ).json()
    assert r2["status"] == "ready"
    assert r2["summary"]["ready_count"] == 10

    confirm = client.post(f"/accounts/{account_id}/batches/{batch['id']}/confirm").json()
    amounts = sorted(m["amount"] for m in client.get(f"/accounts/{account_id}/movements").json())
    assert amounts == ["100000.00"] * 5 + ["110000.00"] * 5
    assert confirm["confirmed_count"] == 10


def test_ars_and_usd_never_mixed_in_batch() -> None:
    account_id = _new_account()
    rows = _gasoil_rows(3, currency="ARS", litres_start=100) + _gasoil_rows(3, currency="USD", litres_start=50)
    content = _xlsx_bytes(["Fecha", "Concepto", "Debe", "Haber", "Moneda", "Litros"], rows)
    batch = _upload_batch(account_id, "monedas.xlsx", content).json()

    fuel_q = _find_question(batch, "fuel_price")
    answered = client.post(
        f"/accounts/{account_id}/batches/{batch['id']}/questions/{fuel_q['id']}/answer",
        json={"scope": "all", "price": "1200", "currency": "ARS"},
    ).json()
    # Only the ARS rows resolved; USD rows still need their own price.
    assert answered["status"] == "needs_questions"
    ready = [i for i in answered["line_items"] if i["status"] == "ready"]
    assert len(ready) == 3
    assert all(i["fields"]["currency"] == "ARS" for i in ready)

    usd_q = _find_question(answered, "fuel_price")
    final = client.post(
        f"/accounts/{account_id}/batches/{batch['id']}/questions/{usd_q['id']}/answer",
        json={"scope": "all", "price": "0.9", "currency": "USD"},
    ).json()
    assert final["status"] == "ready"
    client.post(f"/accounts/{account_id}/batches/{batch['id']}/confirm")
    ars_balance = client.get(f"/accounts/{account_id}/balance/ARS").json()
    usd_balance = client.get(f"/accounts/{account_id}/balance/USD").json()
    assert ars_balance["confirmed_amount"] != "0.00"
    assert usd_balance["confirmed_amount"] != "0.00"


def test_ambiguous_row_becomes_exception_and_does_not_block_the_rest() -> None:
    account_id = _new_account()
    rows = [
        ["01/07/2026", "Fila normal", 1000, None, "ARS", None],
        ["02/07/2026", "Fila con debe y haber", 500, 300, "ARS", None],
    ]
    content = _xlsx_bytes(["Fecha", "Concepto", "Debe", "Haber", "Moneda", "Litros"], rows)
    batch = _upload_batch(account_id, "ambigua.xlsx", content).json()
    assert batch["status"] == "ready"  # no consolidated question blocks it — it's a one-off exception
    exception_items = [i for i in batch["line_items"] if i["status"] == "exception"]
    assert len(exception_items) == 1
    assert "Debe y en Haber" in exception_items[0]["reason"]

    confirm = client.post(f"/accounts/{account_id}/batches/{batch['id']}/confirm").json()
    assert confirm["confirmed_count"] == 1
    movements = client.get(f"/accounts/{account_id}/movements").json()
    assert len(movements) == 1


def test_duplicate_row_is_not_incorporated() -> None:
    account_id = _new_account()
    content = _xlsx_bytes(
        ["Fecha", "Concepto", "Debe", "Haber", "Moneda", "Litros"],
        [["01/07/2026", "Pago único", 5000, None, "ARS", None]],
    )
    first = _upload_batch(account_id, "dup.xlsx", content).json()
    client.post(f"/accounts/{account_id}/batches/{first['id']}/confirm")

    second = _upload_batch(account_id, "dup.xlsx", content).json()
    assert second["summary"]["duplicates_excluded"] == 1
    assert second["summary"]["ready_count"] == 0
    movements = client.get(f"/accounts/{account_id}/movements").json()
    assert len(movements) == 1  # not loaded twice


def test_confirm_failure_leaves_no_partial_save(monkeypatch: pytest.MonkeyPatch) -> None:
    account_id = _new_account()
    content = _xlsx_bytes(
        ["Fecha", "Concepto", "Debe", "Haber", "Moneda", "Litros"],
        [
            ["01/07/2026", "Uno", 1000, None, "ARS", None],
            ["02/07/2026", "Dos", None, 2000, "ARS", None],
        ],
    )
    batch = _upload_batch(account_id, "falla.xlsx", content).json()
    assert batch["summary"]["ready_count"] == 2

    def _boom(self, account_id, account):
        raise RuntimeError("disco lleno (simulado)")

    monkeypatch.setattr(api_module._store.__class__, "save", _boom)

    # TestClient re-raises exceptions caught by the app's generic handler by default
    # (that's how Starlette lets a real ASGI server still see/log them); a real HTTP
    # client would just receive the friendly 500 body from unexpected_error_handler.
    no_raise_client = TestClient(app, raise_server_exceptions=False)
    response = no_raise_client.post(f"/accounts/{account_id}/batches/{batch['id']}/confirm")
    assert response.status_code == 500
    assert response.json()["detail"] == "Ocurrió un error inesperado. Probá de nuevo en unos segundos."

    monkeypatch.undo()
    movements = client.get(f"/accounts/{account_id}/movements").json()
    assert movements == []  # nothing was saved, not even partially
    still_open = client.get(f"/accounts/{account_id}/batches/{batch['id']}").json()
    assert still_open["status"] == "ready"  # batch was not marked confirmed either


def test_malicious_content_shown_as_text_never_executed() -> None:
    account_id = _new_account()
    content = _xlsx_bytes(
        ["Fecha", "Concepto", "Debe", "Haber", "Moneda", "Litros"],
        [["01/07/2026", "<img src=x onerror=alert(1)> \"gasto\" & cia", 1000, None, "ARS", None]],
    )
    batch = _upload_batch(account_id, "<script>evil</script>.xlsx", content).json()
    assert batch["line_items"][0]["fields"]["description"] == '<img src=x onerror=alert(1)> "gasto" & cia'
    # served as JSON, never as HTML — no tag was interpreted, it round-trips as plain text
    assert "<img" in batch["line_items"][0]["fields"]["description"]


def test_persistence_round_trip_keeps_batch_and_answers(tmp_path: Path) -> None:
    from cuentas_corrientes.batches import create_batch
    from cuentas_corrientes.domain import Account
    from cuentas_corrientes.storage import AccountStore

    db_path = tmp_path / "data" / "cuentas.sqlite3"
    store = AccountStore(str(db_path))
    account = Account("Empresa Persistencia", "Contraparte Persistencia")
    store.save("acc-1", account)

    content = _xlsx_bytes(
        ["Fecha", "Concepto", "Debe", "Haber", "Moneda", "Litros"],
        [["01/07/2026", "Gasoil", None, None, "ARS", 100]],
    )
    batch = create_batch(account, account_id="acc-1", files=[("g.xlsx", content)], uploads_dir=tmp_path / "uploads", existing_rules=[])
    store.save_import_batch(batch)

    reloaded_batches = store.load_import_batches()
    assert "acc-1" in reloaded_batches
    reloaded_batch = reloaded_batches["acc-1"][0]
    assert reloaded_batch.id == batch.id
    assert reloaded_batch.status == "needs_questions"
    assert len(reloaded_batch.questions) == 1
    assert reloaded_batch.questions[0].kind == "fuel_price"
    assert not reloaded_batch.questions[0].answered


def test_confirm_before_answering_all_questions_is_rejected() -> None:
    account_id = _new_account()
    content = _xlsx_bytes(
        ["Fecha", "Concepto", "Debe", "Haber", "Moneda", "Litros"],
        [["01/07/2026", "Gasoil sin precio", None, None, "ARS", 100]],
    )
    batch = _upload_batch(account_id, "sin_precio.xlsx", content).json()
    assert batch["status"] == "needs_questions"

    response = client.post(f"/accounts/{account_id}/batches/{batch['id']}/confirm")
    assert response.status_code == 422
