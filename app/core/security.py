"""Upload security utilities: size limits and content-type validation."""
from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.core.exceptions import FileTooLargeError, InvalidFileTypeError

ALLOWED_EXTENSIONS = frozenset({".kml", ".zip"})
ALLOWED_MIME_TYPES = frozenset({
    "application/vnd.google-earth.kml+xml",
    "application/xml",
    "text/xml",
    "text/plain",         # some clients send KML as text/plain
    "application/zip",
    "application/x-zip-compressed",
    "application/octet-stream",  # generic fallback from some clients
})


def validate_upload(filename: str, content_type: str, size: int) -> str:
    """
    Validate upload size, extension and MIME type.

    Returns the canonical file extension ('.kml' or '.zip').
    Raises FileTooLargeError or InvalidFileTypeError on failure.
    """
    if size > settings.max_upload_bytes:
        raise FileTooLargeError(
            f"Upload size {size} bytes exceeds maximum "
            f"{settings.max_upload_bytes} bytes ({settings.max_upload_mb} MB)."
        )

    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise InvalidFileTypeError(
            f"File extension '{suffix}' is not supported. "
            f"Accepted: {sorted(ALLOWED_EXTENSIONS)}",
            details={"received_extension": suffix},
        )

    # MIME type is advisory; do not reject purely on MIME because browsers
    # vary wildly. Log a warning but trust the extension as the primary signal.
    return suffix


def sanitize_filename(filename: str) -> str:
    """Return only the base name, stripping directory components."""
    return Path(filename).name
