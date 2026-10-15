"""TUK World 식당 이미지 검증·저장."""

import io
import os
import uuid

from PIL import Image, UnidentifiedImageError

from app.config import Config

# Pillow가 판별한 실제 포맷 -> (MIME, 저장 확장자)
ALLOWED_FORMATS: dict[str, tuple[str, str]] = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class ImageValidationError(ValueError):
    """업로드 이미지가 허용 조건을 만족하지 않을 때."""


def validate_image(
    data: bytes, original_name: str | None, declared_type: str | None
) -> tuple[str, str]:
    """이미지 바이트를 검증하고 (MIME, 확장자)를 반환합니다.

    Raises:
        ImageValidationError: 크기·확장자·MIME·실제 포맷 중 하나라도 어긋날 때.
    """
    if not data:
        raise ImageValidationError("empty_file")
    if len(data) > Config.TUK_WORLD_MAX_IMAGE_BYTES:
        raise ImageValidationError("file_too_large")
    ext = os.path.splitext(original_name or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ImageValidationError("unsupported_extension")
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format or ""
            img.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageValidationError("invalid_image") from exc
    if fmt not in ALLOWED_FORMATS:
        raise ImageValidationError("unsupported_image_format")
    mime, saved_ext = ALLOWED_FORMATS[fmt]
    if declared_type != mime:
        raise ImageValidationError("content_type_mismatch")
    return mime, saved_ext


def save_image(data: bytes, ext: str) -> str:
    """서버 생성 파일명으로 저장하고 파일명을 반환합니다."""
    filename = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(Config.TUK_WORLD_IMAGE_DIR, filename), "xb") as f:
        f.write(data)
    return filename


def image_path(filename: str) -> str:
    """저장된 파일명의 절대 경로."""
    return os.path.join(Config.TUK_WORLD_IMAGE_DIR, os.path.basename(filename))


def remove_image(filename: str) -> None:
    """이미지 파일을 지웁니다. 이미 없으면 무시합니다."""
    try:
        os.remove(image_path(filename))
    except FileNotFoundError:
        pass
