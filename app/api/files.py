"""Files API router: upload, info, measurements, quality, and insights."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.core.exceptions import FileNotFoundError
from app.core.logging import get_logger
from app.core.security import validate_upload
from app.db.session import get_db
from app.models import GeoFeature, GeoFile, Measurement
from app.schemas.file import FileInfoResponse, FileUploadResponse, ProcessingSummary
from app.schemas.measurement import (
    FeatureDetailSchema,
    FeatureMeasurementSchema,
    FeaturesResponse,
    MeasurementsResponse,
)
from app.services.ingestion_service import process_upload
from app.services.validation_service import validate_upload_request

logger = get_logger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("/", response_model=FileUploadResponse, status_code=201)
async def upload_file(
    file: UploadFile = File(..., description="KML or zipped Shapefile to process."),
    db: Session = Depends(get_db),
) -> FileUploadResponse:
    """
    Upload and process a geospatial file (KML or zipped Shapefile).

    Validates the file, extracts features, resolves CRS, computes measurements,
    and persists everything. Returns a summary of the processed file.
    """
    file_bytes = await file.read()
    safe_name, ext = validate_upload_request(
        filename=file.filename or "upload",
        content_type=file.content_type or "",
        file_size=len(file_bytes),
    )

    geo_file = process_upload(
        filename=safe_name,
        file_bytes=file_bytes,
        file_ext=ext,
        db=db,
    )

    return FileUploadResponse(
        id=geo_file.id,
        filename=geo_file.filename,
        format=geo_file.format,
        feature_count=geo_file.feature_count,
        crs=geo_file.source_crs,
        measurement_crs=geo_file.measurement_crs,
        status=geo_file.status,
        created_at=geo_file.created_at,
        error_message=geo_file.error_message,
    )


@router.get("/{file_id}/", response_model=FileInfoResponse)
def get_file_info(file_id: str, db: Session = Depends(get_db)) -> FileInfoResponse:
    """
    Get metadata and processing summary for a previously uploaded file.
    """
    geo_file = db.query(GeoFile).filter(GeoFile.id == file_id).first()
    if geo_file is None:
        raise FileNotFoundError(f"File with id '{file_id}' not found.")

    # Build processing summary from feature records
    summary: ProcessingSummary | None = None
    features = db.query(GeoFeature).filter(GeoFeature.file_id == file_id).all()
    if features:
        polygon_count = sum(
            1 for f in features if f.geometry_type in ("Polygon", "MultiPolygon")
        )
        linestring_count = sum(
            1 for f in features
            if f.geometry_type in ("LineString", "MultiLineString", "LinearRing")
        )
        point_count = sum(
            1 for f in features if f.geometry_type in ("Point", "MultiPoint")
        )
        unsupported_count = sum(
            1 for f in features
            if f.measurement and f.measurement.reason == "UNSUPPORTED_GEOMETRY_TYPE"
        )
        invalid_count = sum(1 for f in features if not f.geometry_valid)
        empty_count = sum(1 for f in features if f.geometry_empty)

        summary = ProcessingSummary(
            polygon_count=polygon_count,
            linestring_count=linestring_count,
            point_count=point_count,
            unsupported_count=unsupported_count,
            invalid_count=invalid_count,
            empty_count=empty_count,
        )

    return FileInfoResponse(
        id=geo_file.id,
        filename=geo_file.filename,
        format=geo_file.format,
        feature_count=geo_file.feature_count,
        crs=geo_file.source_crs,
        measurement_crs=geo_file.measurement_crs,
        status=geo_file.status,
        processing_summary=summary,
        created_at=geo_file.created_at,
        completed_at=geo_file.completed_at,
        error_message=geo_file.error_message,
    )


@router.get("/{file_id}/measurements/", response_model=MeasurementsResponse)
def get_measurements(
    file_id: str, db: Session = Depends(get_db)
) -> MeasurementsResponse:
    """
    Retrieve feature-level measurement results for a processed file.

    Returns area (m²) for Polygons, length (m) for LineStrings, and an
    explicit reason for features where measurement is not applicable.
    """
    geo_file = db.query(GeoFile).filter(GeoFile.id == file_id).first()
    if geo_file is None:
        raise FileNotFoundError(f"File with id '{file_id}' not found.")

    features = (
        db.query(GeoFeature)
        .filter(GeoFeature.file_id == file_id)
        .order_by(GeoFeature.feature_index)
        .all()
    )

    feature_schemas: list[FeatureMeasurementSchema] = []
    for feat in features:
        msr = feat.measurement
        schema = FeatureMeasurementSchema(
            feature_id=feat.feature_index,
            geometry_type=feat.geometry_type,
            measurement_type=msr.measurement_type if msr else "none",
            area=msr.value if msr and msr.measurement_type == "area" else None,
            area_unit=msr.unit if msr and msr.measurement_type == "area" else None,
            length=msr.value if msr and msr.measurement_type == "length" else None,
            length_unit=msr.unit if msr and msr.measurement_type == "length" else None,
            reason=msr.reason if msr else None,
            geometry=feat.geometry_json,
            properties=feat.properties_json or {},
            geometry_valid=feat.geometry_valid,
            geometry_empty=feat.geometry_empty,
            validation_message=feat.validation_message,
        )
        feature_schemas.append(schema)

    return MeasurementsResponse(
        file_id=file_id,
        source_crs=geo_file.source_crs,
        measurement_crs=geo_file.measurement_crs,
        features=feature_schemas,
        total_features=len(feature_schemas),
    )


@router.get("/{file_id}/features/", response_model=FeaturesResponse)
def get_features(
    file_id: str,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
) -> FeaturesResponse:
    """
    Retrieve extracted geometries, metadata, and attributes for a processed file.
    Supports pagination via page and page_size query parameters.
    """
    geo_file = db.query(GeoFile).filter(GeoFile.id == file_id).first()
    if geo_file is None:
        raise FileNotFoundError(f"File with id '{file_id}' not found.")

    total = db.query(GeoFeature).filter(GeoFeature.file_id == file_id).count()
    offset = max(0, (page - 1) * page_size)

    features = (
        db.query(GeoFeature)
        .filter(GeoFeature.file_id == file_id)
        .order_by(GeoFeature.feature_index)
        .offset(offset)
        .limit(page_size)
        .all()
    )

    items = [
        FeatureDetailSchema(
            feature_id=f.feature_index,
            geometry_type=f.geometry_type,
            geometry=f.geometry_json,
            properties=f.properties_json or {},
            geometry_valid=f.geometry_valid,
            geometry_empty=f.geometry_empty,
            validation_message=f.validation_message,
        )
        for f in features
    ]

    return FeaturesResponse(
        file_id=file_id,
        page=page,
        page_size=page_size,
        total_features=total,
        features=items,
    )
