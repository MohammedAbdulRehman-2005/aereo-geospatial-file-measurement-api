"""Domain exceptions and HTTP mapping."""
from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    INVALID_FILE_TYPE = "INVALID_FILE_TYPE"
    INVALID_ARCHIVE = "INVALID_ARCHIVE"
    INVALID_SHAPEFILE_ARCHIVE = "INVALID_SHAPEFILE_ARCHIVE"
    INVALID_GEOSPATIAL_DATA = "INVALID_GEOSPATIAL_DATA"
    MISSING_CRS = "MISSING_CRS"
    CRS_TRANSFORMATION_FAILED = "CRS_TRANSFORMATION_FAILED"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PROCESSING_ERROR = "PROCESSING_ERROR"
    AI_PROVIDER_UNAVAILABLE = "AI_PROVIDER_UNAVAILABLE"
    AI_DISABLED = "AI_DISABLED"


class GeospatialAPIError(Exception):
    """Base domain exception. Maps to a specific HTTP status."""

    http_status: int = 500
    error_code: ErrorCode = ErrorCode.PROCESSING_ERROR

    def __init__(
        self,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.error_code,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return {"error": payload}


class InvalidFileTypeError(GeospatialAPIError):
    http_status = 400
    error_code = ErrorCode.INVALID_FILE_TYPE


class InvalidArchiveError(GeospatialAPIError):
    http_status = 400
    error_code = ErrorCode.INVALID_ARCHIVE


class InvalidShapefileArchiveError(GeospatialAPIError):
    http_status = 400
    error_code = ErrorCode.INVALID_SHAPEFILE_ARCHIVE


class InvalidGeospatialDataError(GeospatialAPIError):
    http_status = 400
    error_code = ErrorCode.INVALID_GEOSPATIAL_DATA


class MissingCRSError(GeospatialAPIError):
    http_status = 400
    error_code = ErrorCode.MISSING_CRS


class CRSTransformationFailedError(GeospatialAPIError):
    http_status = 400
    error_code = ErrorCode.CRS_TRANSFORMATION_FAILED


class FileNotFoundError(GeospatialAPIError):
    http_status = 404
    error_code = ErrorCode.FILE_NOT_FOUND


class FileTooLargeError(GeospatialAPIError):
    http_status = 413
    error_code = ErrorCode.FILE_TOO_LARGE


class ProcessingError(GeospatialAPIError):
    http_status = 500
    error_code = ErrorCode.PROCESSING_ERROR


class AIProviderUnavailableError(GeospatialAPIError):
    http_status = 503
    error_code = ErrorCode.AI_PROVIDER_UNAVAILABLE


class AIDisabledError(GeospatialAPIError):
    http_status = 503
    error_code = ErrorCode.AI_DISABLED
