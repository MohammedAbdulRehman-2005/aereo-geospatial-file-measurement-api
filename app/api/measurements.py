"""Quality API router: GET /api/files/{id}/quality"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.exceptions import FileNotFoundError
from app.db.session import get_db
from app.models import GeoFeature, GeoFile
from app.services.measurement_service import FeatureMeasurement
from app.services.quality_service import run_quality_checks

router = APIRouter(prefix="/api/files", tags=["quality"])


class QualityFlagOut(BaseModel):
    severity: str
    code: str
    message: str
    feature_indices: list[int] = Field(default_factory=list)


class QualityResponse(BaseModel):
    file_id: str
    invalid_features: int
    empty_features: int
    unsupported_features: int
    missing_crs: bool
    mixed_geometry_types: bool
    geometry_type_counts: dict[str, int]
    polygon_area_stats: dict[str, float] | None
    linestring_length_stats: dict[str, float] | None
    flags: list[QualityFlagOut]


@router.get("/{file_id}/quality/", response_model=QualityResponse)
def get_quality_report(
    file_id: str, db: Session = Depends(get_db)
) -> QualityResponse:
    """
    Deterministic data-quality and anomaly report for a processed file.

    Based entirely on computed facts; no AI is used for this endpoint.
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

    # Reconstruct FeatureMeasurement objects from DB for quality checks
    fake_measurements: list[FeatureMeasurement] = []
    for feat in features:
        msr = feat.measurement
        fake_measurements.append(
            FeatureMeasurement(
                feature_index=feat.feature_index,
                geometry_type=feat.geometry_type,
                measurement_type=msr.measurement_type if msr else "none",
                value=msr.value if msr else None,
                unit=msr.unit if msr else None,
                measurement_crs=msr.measurement_crs if msr else None,
                reason=msr.reason if msr else None,
                properties=feat.properties_json or {},
                geometry_valid=feat.geometry_valid,
                geometry_empty=feat.geometry_empty,
                validation_message=feat.validation_message,
            )
        )

    report = run_quality_checks(fake_measurements, geo_file.source_crs)

    flags_out = [
        QualityFlagOut(
            severity=f.severity,
            code=f.code,
            message=f.message,
            feature_indices=f.feature_indices,
        )
        for f in report.flags
    ]

    return QualityResponse(
        file_id=file_id,
        invalid_features=report.invalid_features,
        empty_features=report.empty_features,
        unsupported_features=report.unsupported_features,
        missing_crs=report.missing_crs,
        mixed_geometry_types=report.mixed_geometry_types,
        geometry_type_counts=report.geometry_type_counts,
        polygon_area_stats=report.polygon_area_stats,
        linestring_length_stats=report.linestring_length_stats,
        flags=flags_out,
    )
