"""AI Insight Service: builds structured context and calls the AI provider."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.ai.prompts import PROMPT_VERSION
from app.ai.provider import get_provider
from app.ai.schemas import InsightOutput
from app.core.config import settings
from app.core.exceptions import AIDisabledError, AIProviderUnavailableError
from app.core.logging import get_logger
from app.models import GeoFeature, GeoFile, InsightReport, Measurement
from app.utils.ids import new_id

logger = get_logger(__name__)


def _build_context(geo_file: GeoFile, db: Session) -> dict[str, Any]:
    """
    Build a structured context dict from trusted DB records.

    The AI provider receives ONLY this structured data — never raw files.
    """
    features = db.query(GeoFeature).filter(GeoFeature.file_id == geo_file.id).all()
    measurements = [f.measurement for f in features if f.measurement]

    geom_counts: dict[str, int] = {}
    for f in features:
        geom_counts[f.geometry_type] = geom_counts.get(f.geometry_type, 0) + 1

    total_polygon_area = sum(
        m.value for m in measurements
        if m and m.measurement_type == "area" and m.value is not None
    )
    total_linestring_length = sum(
        m.value for m in measurements
        if m and m.measurement_type == "length" and m.value is not None
    )
    invalid_count = sum(1 for f in features if not f.geometry_valid)
    unsupported_count = sum(
        1 for m in measurements
        if m and m.reason == "UNSUPPORTED_GEOMETRY_TYPE"
    )

    return {
        "file": {
            "format": geo_file.format,
            "source_crs": geo_file.source_crs,
            "measurement_crs": geo_file.measurement_crs,
            "feature_count": geo_file.feature_count,
            "status": geo_file.status,
        },
        "geometry_counts": geom_counts,
        "measurement_statistics": {
            "total_polygon_area_m2": round(total_polygon_area, 4),
            "total_linestring_length_m": round(total_linestring_length, 4),
        },
        "quality": {
            "invalid_features": invalid_count,
            "unsupported_features": unsupported_count,
        },
    }


def generate_insights_for_file(
    file_id: str,
    db: Session,
) -> dict[str, Any]:
    """
    Generate and persist AI insights for a processed file.

    Safety guarantees:
    - AI is called only after deterministic processing is complete.
    - AI receives structured context only (no raw files).
    - Output is schema-validated before persistence.
    - AI provider failure never destroys deterministic results.
    - AI is disabled by default; returns a clear error when disabled.
    """
    if not settings.ai_enabled:
        raise AIDisabledError(
            "AI insights are disabled. Set AI_ENABLED=true to enable.",
            details={"ai_provider": settings.ai_provider},
        )

    from app.models import GeoFile as GeoFileModel  # avoid circular at module level
    geo_file = db.query(GeoFileModel).filter(GeoFileModel.id == file_id).first()
    if geo_file is None:
        from app.core.exceptions import FileNotFoundError
        raise FileNotFoundError(f"File with id '{file_id}' not found.")

    # Return cached report if one already exists
    existing = (
        db.query(InsightReport)
        .filter(InsightReport.file_id == file_id)
        .order_by(InsightReport.created_at.desc())
        .first()
    )
    if existing:
        return existing.result_json  # type: ignore[return-value]

    provider = get_provider()
    context = _build_context(geo_file, db)

    logger.info(
        "Calling AI provider",
        extra={
            "file_id": file_id,
            "provider": provider.provider_name,
            "model": provider.model_name,
        },
    )

    raw_output = provider.generate_insights(context)

    # Schema-validate output — raises ValidationError if AI produces garbage
    validated = InsightOutput.model_validate(raw_output)
    result_dict = validated.model_dump()

    # Persist
    report = InsightReport(
        id=new_id(),
        file_id=file_id,
        provider=provider.provider_name,
        model=provider.model_name,
        prompt_version=PROMPT_VERSION,
        result_json=result_dict,
    )
    db.add(report)
    db.commit()

    logger.info(
        "AI insights generated and persisted",
        extra={"file_id": file_id, "provider": provider.provider_name},
    )
    return result_dict
