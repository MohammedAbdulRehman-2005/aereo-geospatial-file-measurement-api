"""Deterministic measurement engine using PyProj as the sole CRS transformation engine.

Computes area (m²) for Polygon/MultiPolygon and length (m) for
LineString/MultiLineString after projecting to the appropriate CRS.

Never measures in degree-based coordinates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shapely.geometry.base import BaseGeometry

from app.core.exceptions import CRSTransformationFailedError
from app.core.logging import get_logger
from app.parsers.base import ParsedFeature
from app.services.crs_service import CRSResolution
from app.utils.geometry import (
    is_measurable_line,
    is_measurable_polygon,
    is_point_type,
    safe_repair,
)

logger = get_logger(__name__)

REASON_POINT = "POINT_REQUIRES_NO_MEASUREMENT"
REASON_UNSUPPORTED = "UNSUPPORTED_GEOMETRY_TYPE"
REASON_EMPTY = "EMPTY_GEOMETRY"
REASON_INVALID = "INVALID_GEOMETRY_NOT_REPAIRABLE"
REASON_CRS_MISSING = "CRS_MISSING"


@dataclass
class FeatureMeasurement:
    """Result of measuring a single feature with full provenance."""

    feature_index: int
    geometry_type: str
    measurement_type: str  # 'area' | 'length' | 'none'
    value: float | None
    unit: str | None
    measurement_crs: str | None
    reason: str | None
    properties: dict[str, Any] = field(default_factory=dict)
    source_crs: str = ""
    method: str | None = None  # 'planar_projected_area' | 'planar_projected_length' | None
    geometry_repaired: bool = False
    geometry_json: dict[str, Any] | None = None
    geometry_valid: bool = True
    geometry_empty: bool = False
    validation_status: str | None = None
    validation_message: str | None = None


def _project_geometry(
    geom: BaseGeometry,
    source_crs: str,
    measurement_crs: str,
) -> BaseGeometry:
    """
    Project a single Shapely geometry into the target measurement CRS using PyProj.

    PyProj / PROJ is the sole CRS transformation engine.
    If transformation fails, raises CRSTransformationFailedError.
    """
    if source_crs == measurement_crs:
        return geom

    try:
        from pyproj import CRS, Transformer

        src_crs_obj = CRS.from_user_input(source_crs)
        tgt_crs_obj = CRS.from_user_input(measurement_crs)
        transformer = Transformer.from_crs(src_crs_obj, tgt_crs_obj, always_xy=True)

        def transform_coords(coords: Any) -> Any:
            import numpy as np

            x_arr = coords[:, 0]
            y_arr = coords[:, 1]
            x_out, y_out = transformer.transform(x_arr, y_arr)
            out = np.column_stack((x_out, y_out))
            if coords.shape[1] > 2:
                out = np.column_stack((out, coords[:, 2:]))
            return out

        import shapely

        return shapely.transform(geom, transform_coords)
    except Exception as exc:
        logger.error(
            "CRS transformation failed",
            extra={
                "source_crs": source_crs,
                "measurement_crs": measurement_crs,
                "error": str(exc),
            },
        )
        raise CRSTransformationFailedError(
            f"Failed to transform geometry from '{source_crs}' to '{measurement_crs}': {exc}",
            details={
                "source_crs": source_crs,
                "measurement_crs": measurement_crs,
                "error": str(exc),
            },
        ) from exc


def measure_features(
    features: list[ParsedFeature],
    crs_resolution: CRSResolution,
) -> list[FeatureMeasurement]:
    """
    Compute measurements for all features using the resolved CRS.

    Features with unsupported or invalid geometries are recorded with an
    explicit reason. One bad feature never prevents processing others.
    """
    results: list[FeatureMeasurement] = []

    for feat in features:
        result = _measure_single_feature(feat, crs_resolution)
        results.append(result)

    return results


def _measure_single_feature(
    feat: ParsedFeature,
    crs: CRSResolution,
) -> FeatureMeasurement:
    """Attempt to measure a single feature. All feature-level errors are recorded."""
    base_kwargs: dict[str, Any] = {
        "feature_index": feat.index,
        "geometry_type": feat.geometry_type,
        "properties": feat.properties,
        "geometry_json": feat.geometry_json,
        "geometry_valid": feat.geometry_valid,
        "geometry_empty": feat.geometry_empty,
        "validation_message": feat.validation_message,
        "source_crs": crs.source_crs,
        "geometry_repaired": False,
    }

    # --- Empty geometry ---
    if feat.geometry_empty or feat.geometry is None:
        return FeatureMeasurement(
            **base_kwargs,
            measurement_type="none",
            value=None,
            unit=None,
            measurement_crs=None,
            method=None,
            reason=REASON_EMPTY,
            validation_status="empty",
        )

    base_kwargs["validation_status"] = "valid" if feat.geometry_valid else "invalid"

    geom = feat.geometry

    # --- Attempt deterministic topology repair for invalid polygons ---
    if not feat.geometry_valid and is_measurable_polygon(geom):
        repaired, was_repaired = safe_repair(geom)
        if was_repaired:
            geom = repaired
            base_kwargs["geometry_valid"] = True
            base_kwargs["geometry_repaired"] = True
            base_kwargs["validation_status"] = "repaired"
            base_kwargs["validation_message"] = (
                "Deterministic topology repair attempt via buffer(0) resolved self-intersection."
            )
            try:
                import shapely.geometry

                base_kwargs["geometry_json"] = shapely.geometry.mapping(repaired)
            except Exception:
                pass
            logger.info(
                "Geometry repaired via deterministic topology repair attempt (buffer(0))",
                extra={"feature_index": feat.index},
            )
        else:
            return FeatureMeasurement(
                **base_kwargs,
                measurement_type="none",
                value=None,
                unit=None,
                measurement_crs=None,
                method=None,
                reason=REASON_INVALID,
                validation_status="invalid",
                validation_message="Geometry is invalid and could not be repaired.",
            )
    elif not feat.geometry_valid:
        return FeatureMeasurement(
            **base_kwargs,
            measurement_type="none",
            value=None,
            unit=None,
            measurement_crs=None,
            method=None,
            reason=REASON_INVALID,
            validation_status="invalid",
        )

    # --- Point types: no measurement required ---
    if is_point_type(geom):
        return FeatureMeasurement(
            **base_kwargs,
            measurement_type="none",
            value=None,
            unit=None,
            measurement_crs=crs.measurement_crs,
            method=None,
            reason=REASON_POINT,
        )

    # --- Project geometry to measurement CRS ---
    try:
        projected_geom = _project_geometry(
            geom,
            source_crs=crs.source_crs,
            measurement_crs=crs.measurement_crs,
        )
    except CRSTransformationFailedError:
        raise
    except Exception as exc:
        logger.warning(
            "Failed to project geometry for measurement",
            extra={"feature_index": feat.index, "error": str(exc)},
        )
        return FeatureMeasurement(
            **base_kwargs,
            measurement_type="none",
            value=None,
            unit=None,
            measurement_crs=crs.measurement_crs,
            method=None,
            reason=f"PROJECTION_FAILED: {exc}",
        )

    # --- Polygon area ---
    if is_measurable_polygon(geom):
        try:
            area_m2 = float(projected_geom.area)
            return FeatureMeasurement(
                **base_kwargs,
                measurement_type="area",
                value=round(area_m2, 4),
                unit="m2",
                measurement_crs=crs.measurement_crs,
                method="planar_projected_area",
                reason=None,
            )
        except Exception as exc:
            logger.warning(
                "Area calculation failed",
                extra={"feature_index": feat.index, "error": str(exc)},
            )
            return FeatureMeasurement(
                **base_kwargs,
                measurement_type="none",
                value=None,
                unit=None,
                measurement_crs=crs.measurement_crs,
                method=None,
                reason=f"AREA_CALCULATION_FAILED: {exc}",
            )

    # --- LineString length ---
    if is_measurable_line(geom):
        try:
            length_m = float(projected_geom.length)
            return FeatureMeasurement(
                **base_kwargs,
                measurement_type="length",
                value=round(length_m, 4),
                unit="m",
                measurement_crs=crs.measurement_crs,
                method="planar_projected_length",
                reason=None,
            )
        except Exception as exc:
            logger.warning(
                "Length calculation failed",
                extra={"feature_index": feat.index, "error": str(exc)},
            )
            return FeatureMeasurement(
                **base_kwargs,
                measurement_type="none",
                value=None,
                unit=None,
                measurement_crs=crs.measurement_crs,
                method=None,
                reason=f"LENGTH_CALCULATION_FAILED: {exc}",
            )

    # --- Unsupported geometry type ---
    return FeatureMeasurement(
        **base_kwargs,
        measurement_type="none",
        value=None,
        unit=None,
        measurement_crs=None,
        method=None,
        reason=REASON_UNSUPPORTED,
    )
