"""Validation service: pre-processing checks before parsing."""
from __future__ import annotations

from app.core.logging import get_logger
from app.core.security import sanitize_filename, validate_upload

logger = get_logger(__name__)


def validate_upload_request(
    filename: str,
    content_type: str,
    file_size: int,
) -> tuple[str, str]:
    """
    Validate the incoming upload request.

    Returns (sanitized_filename, canonical_extension).
    Raises domain exceptions on failure.
    """
    safe_name = sanitize_filename(filename)
    ext = validate_upload(safe_name, content_type, file_size)
    logger.info(
        "Upload validated",
        extra={"file_name": safe_name, "extension": ext, "size_bytes": file_size},
    )
    return safe_name, ext
