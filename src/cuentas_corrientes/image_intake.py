from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError


@dataclass(frozen=True)
class ImageValidation:
    valid: bool
    format: str | None
    width: int | None
    height: int | None
    message: str | None = None


def validate_image(content: bytes) -> ImageValidation:
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
        with Image.open(io.BytesIO(content)) as image:
            return ImageValidation(True, image.format, image.width, image.height)
    except (UnidentifiedImageError, OSError, ValueError):
        return ImageValidation(False, None, None, None, "El archivo no es una imagen válida.")
