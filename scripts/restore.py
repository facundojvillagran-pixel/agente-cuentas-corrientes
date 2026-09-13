#!/usr/bin/env python3
"""List and restore local SQLite backups for the cuentas corrientes agent.

Usage:
    .venv/bin/python scripts/restore.py --list
    .venv/bin/python scripts/restore.py --file backups/cuentas-20260731-120000123456.sqlite3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cuentas_corrientes.backup import list_backups, restore_backup  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DB = _REPO_ROOT / "data" / "cuentas.sqlite3"
_DEFAULT_BACKUPS_DIR = _REPO_ROOT / "backups"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="Listar los backups disponibles")
    parser.add_argument("--file", type=Path, help="Ruta al backup a restaurar")
    parser.add_argument("--database", type=Path, default=_DEFAULT_DB, help="Base de datos a restaurar (por defecto data/cuentas.sqlite3)")
    args = parser.parse_args()

    if args.list or not args.file:
        backups = list_backups(_DEFAULT_BACKUPS_DIR)
        if not backups:
            print(f"No hay backups en {_DEFAULT_BACKUPS_DIR}")
            return 0
        print(f"Backups disponibles en {_DEFAULT_BACKUPS_DIR} (más reciente primero):")
        for path in backups:
            print(f"  {path}")
        if not args.file:
            return 0

    confirm = input(
        f"Esto va a reemplazar {args.database} con {args.file}. "
        "Se guarda una copia de seguridad de la base actual antes de restaurar. "
        "Escribí 'restaurar' para confirmar: "
    )
    if confirm.strip().lower() != "restaurar":
        print("Restauración cancelada.")
        return 1

    restore_backup(args.file, args.database)
    print(f"Base restaurada desde {args.file} hacia {args.database}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
