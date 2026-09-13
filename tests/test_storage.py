from datetime import date
from decimal import Decimal
from pathlib import Path

from cuentas_corrientes.backup import create_backup, list_backups, restore_backup
from cuentas_corrientes.domain import Account, Direction, Evidence, Movement, MovementStatus
from cuentas_corrientes.storage import AccountStore


def _account_with_movement(reference: str) -> Account:
    account = Account("Empresa", "Contraparte")
    movement = Movement(
        date(2026, 7, 1),
        "Movimiento de prueba",
        Decimal("1000"),
        "ARS",
        Direction.COMPANY,
        MovementStatus.CONFIRMED,
        (Evidence("fixture", reference, Evidence.fingerprint(reference.encode())),),
    )
    account.add_movement(movement, actor="test")
    return account


def test_create_backup_writes_file_and_prunes_old_ones(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "cuentas.sqlite3"
    backups_dir = tmp_path / "backups"
    store = AccountStore(str(db_path))
    store.save("acc-1", _account_with_movement("bkp-1"))

    first_backup = create_backup(db_path, backups_dir)
    assert first_backup is not None
    assert first_backup.exists()

    for index in range(25):
        create_backup(db_path, backups_dir)

    remaining = list_backups(backups_dir)
    assert len(remaining) <= 20


def test_restore_backup_round_trips_data(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "cuentas.sqlite3"
    backups_dir = tmp_path / "backups"
    store = AccountStore(str(db_path))
    store.save("acc-1", _account_with_movement("bkp-original"))

    backup_path = create_backup(db_path, backups_dir)
    assert backup_path is not None

    other_store = AccountStore(str(db_path))
    other_store.connection.execute("DELETE FROM accounts")
    other_store.connection.commit()
    other_store.save("acc-2", _account_with_movement("bkp-overwritten"))
    assert "acc-1" not in other_store.load_all()

    restore_backup(backup_path, db_path)

    restored_store = AccountStore(str(db_path))
    accounts = restored_store.load_all()
    assert "acc-1" in accounts
    assert accounts["acc-1"].movements[0].description == "Movimiento de prueba"

    pre_restore_backups = [p for p in backups_dir.glob("pre-restore-*.sqlite3")]
    assert len(pre_restore_backups) == 1
