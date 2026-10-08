"""
Ingestion service: orchestrates the end-to-end processing pipeline.

Pipeline steps (per specification §5):
1.  Receive multipart upload (handled by route)
2.  Validate extension, MIME type, and size
3.  Generate internal file ID
4.  Store original file in controlled temp storage
5.  ZIP: validate archive, prevent path traversal, enforce limits
6.  Detect format and invoke parser adapter
7.  Read features
8.  Validate geometries and normalize empty/null geometries
9.  Resolve source CRS
10. Select appropriate projected CRS for measurement
11. Transform geometry for measurement
12. Calculate area/length where supported
13. Record unsupported/error features without crashing the job
14. Persist file metadata + feature/measurement summaries
15. Run deterministic quality analysis
16. Mark job COMPLETED / PARTIAL / FAILED
17. Return API response

Business logic lives here. Route handlers must remain thin.
"""
from __future__ import annotations

import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import (
    GeospatialAPIError,
    InvalidGeospatialDataError,
    MissingCRSError,
    ProcessingError,
)
from app.core.logging import get_logger
from app.models import (
    FileFormat,
    FileStatus,
    GeoFeature,
    GeoFile,
    Measurement,
    ProcessingError as DBProcessingError,
)
from app.parsers.base import ParseResult
from app.parsers.kml_parser import KMLParser
from app.parsers.shapefile_parser import ShapefileParser
from app.services.crs_service import CRSResolution, resolve_measurement_crs
from app.services.measurement_service import FeatureMeasurement, measure_features
from app.services.quality_service import QualityReport, run_quality_checks
from app.utils.ids import new_id
from app.utils.zip_security import find_shapefile_components, validate_and_extract_zip

logger = get_logger(__name__)


def _determine_file_status(
    measurements: list[FeatureMeasurement],
    had_parse_error: bool,
) -> str:
    """
    Determine the file processing status.

    - FAILED: if no features could be extracted
    - PARTIAL: if some features had errors
    - COMPLETED: all features processed without errors
    """
    if had_parse_error:
        return FileStatus.FAILED
    if not measurements:
        return FileStatus.FAILED
    has_errors = any(
        m.reason is not None and m.reason not in (
            "POINT_REQUIRES_NO_MEASUREMENT",
            "EMPTY_GEOMETRY",
            "UNSUPPORTED_GEOMETRY_TYPE",
        )
        for m in measurements
    )
    if has_errors:
        return FileStatus.PARTIAL
    return FileStatus.COMPLETED


def process_upload(
    filename: str,
    file_bytes: bytes,
    file_ext: str,
    db: Session,
) -> GeoFile:
    """
    Core processing pipeline. Returns the persisted GeoFile record.

    This function is designed to be called synchronously from the upload
    route, but is structured so it can later be moved to a background worker
    without rewriting the API contract.
    """
    start_time = time.monotonic()
    file_id = new_id()

    logger.info(
        "Processing started",
        extra={"file_id": file_id, "upload_filename": filename, "extension": file_ext},
    )

    # --- Create initial DB record ---
    geo_file = GeoFile(
        id=file_id,
        filename=filename,
        format=_ext_to_format(file_ext),
        status=FileStatus.PROCESSING,
        file_size_bytes=len(file_bytes),
    )
    db.add(geo_file)
    db.commit()

    with tempfile.TemporaryDirectory(
        prefix="aereo_", dir=settings.temp_dir or None
    ) as tmp_dir:
        tmp_path = Path(tmp_dir)
        raw_file_path = tmp_path / filename

        try:
            # --- Write uploaded bytes to temp storage ---
            raw_file_path.write_bytes(file_bytes)

            # --- Parse ---
            parse_result, shp_path = _parse_file(
                raw_file_path, file_ext, tmp_path, file_id
            )

            if not parse_result.features:
                _mark_failed(db, geo_file, "No features found in the uploaded file.")
                return geo_file

            logger.info(
                "Parsed features",
                extra={
                    "file_id": file_id,
                    "format": parse_result.format,
                    "feature_count": len(parse_result.features),
                    "source_crs": parse_result.source_crs,
                },
            )

            # --- CRS resolution ---
            try:
                crs_resolution = resolve_measurement_crs(
                    parse_result.features,
                    parse_result.source_crs,
                )
            except MissingCRSError as exc:
                _mark_failed(db, geo_file, str(exc))
                _record_error(db, file_id, None, "MISSING_CRS", str(exc))
                return geo_file

            logger.info(
                "CRS resolved",
                extra={
                    "file_id": file_id,
                    "source_crs": crs_resolution.source_crs,
                    "measurement_crs": crs_resolution.measurement_crs,
                },
            )

            # --- Measurements ---
            measurements = measure_features(parse_result.features, crs_resolution)

            # --- Quality analysis ---
            quality_report = run_quality_checks(measurements, parse_result.source_crs)

            # --- Persist features + measurements ---
            _persist_features_and_measurements(
                db, file_id, measurements, crs_resolution
            )

            # --- Update GeoFile record ---
            status = _determine_file_status(measurements, had_parse_error=False)
            geo_file.status = status
            geo_file.source_crs = crs_resolution.source_crs
            geo_file.measurement_crs = crs_resolution.measurement_crs
            geo_file.feature_count = len(parse_result.features)
            geo_file.completed_at = datetime.now(tz=timezone.utc)
            db.commit()

            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.info(
                "Processing completed",
                extra={
                    "file_id": file_id,
                    "status": status,
                    "duration_ms": duration_ms,
                    "feature_count": len(measurements),
                    "source_crs": crs_resolution.source_crs,
                    "measurement_crs": crs_resolution.measurement_crs,
                },
            )

            return geo_file

        except GeospatialAPIError as exc:
            try:
                db.delete(geo_file)
                db.commit()
            except Exception:
                db.rollback()
            raise
        except Exception as exc:
            logger.exception(
                "Unexpected processing error",
                extra={"file_id": file_id, "error": str(exc)},
            )
            _mark_failed(db, geo_file, "An unexpected error occurred during processing.")
            _record_error(db, file_id, None, "PROCESSING_ERROR", str(exc))
            return geo_file


def _ext_to_format(ext: str) -> str:
    mapping = {".kml": FileFormat.KML, ".zip": FileFormat.SHAPEFILE}
    return mapping.get(ext, "UNKNOWN")


def _parse_file(
    raw_file_path: Path,
    file_ext: str,
    tmp_dir: Path,
    file_id: str,
) -> tuple[ParseResult, Path | None]:
    """Select parser and return ParseResult. Handles ZIP extraction."""
    if file_ext == ".kml":
        parser = KMLParser()
        return parser.parse(str(raw_file_path)), None

    elif file_ext == ".zip":
        extract_dir = tmp_dir / "extracted"
        extracted_files = validate_and_extract_zip(raw_file_path, extract_dir)
        components = find_shapefile_components(extracted_files)
        shp_path = components[".shp"]
        parser = ShapefileParser()
        return parser.parse(str(shp_path)), shp_path

    raise InvalidGeospatialDataError(
        f"Unsupported file extension: {file_ext}",
        details={"extension": file_ext},
    )


def _persist_features_and_measurements(
    db: Session,
    file_id: str,
    measurements: list[FeatureMeasurement],
    crs_resolution: CRSResolution,
) -> None:
    """Bulk-insert features and their measurements."""
    for m in measurements:
        feat_id = new_id()
        feature = GeoFeature(
            id=feat_id,
            file_id=file_id,
            feature_index=m.feature_index,
            geometry_type=m.geometry_type,
            geometry_valid=m.geometry_valid,
            geometry_empty=m.geometry_empty,
            validation_message=m.validation_message,
            geometry_json=m.geometry_json,
            properties_json=m.properties if m.properties else None,
        )
        db.add(feature)

        msr = Measurement(
            id=new_id(),
            feature_id=feat_id,
            measurement_type=m.measurement_type,
            value=m.value,
            unit=m.unit,
            measurement_crs=crs_resolution.measurement_crs,
            reason=m.reason,
        )
        db.add(msr)

    db.commit()


def _mark_failed(db: Session, geo_file: GeoFile, message: str) -> None:
    geo_file.status = FileStatus.FAILED
    geo_file.error_message = message
    geo_file.completed_at = datetime.now(tz=timezone.utc)
    db.commit()


def _record_error(
    db: Session,
    file_id: str,
    feature_id: str | None,
    error_code: str,
    message: str,
) -> None:
    err = DBProcessingError(
        id=new_id(),
        file_id=file_id,
        feature_id=feature_id,
        error_code=error_code,
        message=message,
    )
    db.add(err)
    db.commit()
