from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB por archivo
MAX_BATCH_FILES = 10
MAX_BATCH_BYTES = 60 * 1024 * 1024  # 60 MB por lote

_MAGIC_BY_EXTENSION: dict[str, tuple[bytes, ...]] = {
    ".xlsx": (b"PK\x03\x04",),
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
}

_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9 ._-]")


class FileKind:
    SPREADSHEET = "spreadsheet"
    INVOICE_PDF = "invoice_pdf"
    PHOTO = "photo"
    UNRECOGNIZED = "unrecognized"
    INVALID = "invalid"


@dataclass(frozen=True)
class FileClassification:
    kind: str
    extension: str
    message: str | None = None


def sanitize_display_name(original_name: str) -> str:
    """Never trust or reuse the browser-supplied filename as a path.

    Only used to show something readable back to the user; the real
    storage name is always a random uuid assigned by store_upload().
    """
    name = Path(original_name or "archivo").name
    name = _UNSAFE_NAME_CHARS.sub("_", name)
    return name[:120] or "archivo"


def classify_upload(original_name: str, content: bytes) -> FileClassification:
    extension = Path(original_name or "").suffix.lower()
    if extension not in _MAGIC_BY_EXTENSION:
        return FileClassification(FileKind.INVALID, extension, "Formato no admitido. Usá .xlsx, .pdf, .png, .jpg o .jpeg.")

    magics = _MAGIC_BY_EXTENSION[extension]
    if not any(content.startswith(magic) for magic in magics):
        return FileClassification(FileKind.INVALID, extension, "El contenido del archivo no coincide con su extensión.")

    if extension == ".xlsx":
        return FileClassification(FileKind.SPREADSHEET, extension)
    if extension == ".pdf":
        return FileClassification(FileKind.INVOICE_PDF, extension)
    return FileClassification(FileKind.PHOTO, extension)


def store_upload(content: bytes, *, extension: str, uploads_dir: Path) -> Path:
    """Save content under a random internal name, never the user-supplied one."""
    uploads_dir.mkdir(parents=True, exist_ok=True)
    internal_name = f"{uuid4()}{extension}"
    destination = (uploads_dir / internal_name).resolve()
    if uploads_dir.resolve() not in destination.parents:
        raise ValueError("Ruta de almacenamiento inválida")
    destination.write_bytes(content)
    return destination
