"""Deterministic measurement engine.

Computes area (m²) for Polygon/MultiPolygon and length (m) for
LineString/MultiLineString after projecting to the appropriate CRS.

Never measures in degree-based coordinates.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

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
    """Result of measuring a single feature."""

    feature_index: int
    geometry_type: str
    measurement_type: str  # 'area' | 'length' | 'none'
    value: float | None
    unit: str | None
    measurement_crs: str | None
    reason: str | None
    properties: dict[str, Any]
    geometry_json: dict[str, Any] | None = None
    geometry_valid: bool = True
    geometry_empty: bool = False
    validation_message: str | None = None


def _latlon_to_utm(lon: float, lat: float, zone: int, northern: bool) -> tuple[float, float]:
    """
    Standard Transverse Mercator (UTM) forward projection from WGS84 (lon, lat)
    to UTM (easting, northing) in metres.
    """
    a = 6378137.0
    f = 1.0 / 298.257223563
    b = a * (1.0 - f)
    e2 = (a**2 - b**2) / (a**2)
    e_prime2 = (a**2 - b**2) / (b**2)
    k0 = 0.9996

    lon0 = (zone - 1) * 6 - 180 + 3
    lon0_rad = math.radians(lon0)
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)

    # Clamp lat to prevent singularities at exact poles
    lat_rad = max(math.radians(-80.0), min(math.radians(84.0), lat_rad))

    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    tan_lat = math.tan(lat_rad)

    N = a / math.sqrt(1.0 - e2 * sin_lat**2)
    T = tan_lat**2
    C = e_prime2 * cos_lat**2
    A = cos_lat * (lon_rad - lon0_rad)

    M = a * (
        (1.0 - e2/4.0 - 3.0*e2**2/64.0 - 5.0*e2**3/256.0) * lat_rad
        - (3.0*e2/8.0 + 3.0*e2**2/32.0 + 45.0*e2**3/1024.0) * math.sin(2.0*lat_rad)
        + (15.0*e2**2/256.0 + 45.0*e2**3/1024.0) * math.sin(4.0*lat_rad)
        - (35.0*e2**3/3072.0) * math.sin(6.0*lat_rad)
    )

    x = k0 * N * (
        A
        + (1.0 - T + C) * A**3 / 6.0
        + (5.0 - 18.0*T + T**2 + 72.0*C - 58.0*e_prime2) * A**5 / 120.0
    ) + 500000.0

    y = k0 * (
        M
        + N * tan_lat * (
            A**2 / 2.0
            + (5.0 - T + 9.0*C + 4.0*C**2) * A**4 / 24.0
            + (61.0 - 58.0*T + T**2 + 600.0*C - 330.0*e_prime2) * A**6 / 720.0
        )
    )
    if not northern:
        y += 10000000.0

    return x, y


def _project_geometry(
    geom: BaseGeometry,
    source_crs: str,
    measurement_crs: str,
) -> BaseGeometry:
    """Project a single Shapely geometry into the target measurement CRS."""
    if source_crs == measurement_crs:
        return geom

    # Check if native pyproj Transformer can be used
    try:
        from pyproj import Transformer
        transformer = Transformer.from_crs(source_crs, measurement_crs, always_xy=True)

        def pyproj_coords(coords: Any) -> Any:
            import numpy as np
            x_arr = coords[:, 0]
            y_arr = coords[:, 1]
            x_out, y_out = transformer.transform(x_arr, y_arr)
            out = np.column_stack((x_out, y_out))
            if coords.shape[1] > 2:
                out = np.column_stack((out, coords[:, 2:]))
            return out

        import shapely
        return shapely.transform(geom, pyproj_coords)
    except Exception as exc:
        logger.debug("PyProj Transformer fallback", extra={"error": str(exc)})

    # Deterministic Transverse Mercator formulation
    utm_match = re.search(r"EPSG:32([67])(\d{2})", measurement_crs.upper())
    if utm_match:
        hemi = utm_match.group(1)
        zone = int(utm_match.group(2))
        is_north = (hemi == "6")

        def transform_coords(coords: Any) -> Any:
            import numpy as np
            out = np.zeros_like(coords)
            for i in range(len(coords)):
                x, y = _latlon_to_utm(float(coords[i, 0]), float(coords[i, 1]), zone=zone, northern=is_north)
                out[i, 0] = x
                out[i, 1] = y
                if coords.shape[1] > 2:
                    out[i, 2] = coords[i, 2]
            return out

        import shapely
        return shapely.transform(geom, transform_coords)

    return geom


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
    """Attempt to measure a single feature. All errors are caught and recorded."""
    base_kwargs = dict(
        feature_index=feat.index,
        geometry_type=feat.geometry_type,
        properties=feat.properties,
        geometry_json=feat.geometry_json,
        geometry_valid=feat.geometry_valid,
        geometry_empty=feat.geometry_empty,
        validation_message=feat.validation_message,
    )

    # --- Empty geometry ---
    if feat.geometry_empty or feat.geometry is None:
        return FeatureMeasurement(
            **base_kwargs,
            measurement_type="none",
            value=None,
            unit=None,
            measurement_crs=None,
            reason=REASON_EMPTY,
        )

    geom = feat.geometry

    # --- Attempt geometry repair for invalid polygons ---
    if not feat.geometry_valid and is_measurable_polygon(geom):
        repaired, was_repaired = safe_repair(geom)
        if was_repaired:
            geom = repaired
            base_kwargs["geometry_valid"] = True
            base_kwargs["validation_message"] = "Self-intersection repaired via buffer(0)"
            try:
                import shapely.geometry
                base_kwargs["geometry_json"] = shapely.geometry.mapping(repaired)
            except Exception:
                pass
            logger.info(
                "Geometry repaired using buffer(0)",
                extra={"feature_index": feat.index},
            )
        else:
            return FeatureMeasurement(
                **base_kwargs,
                measurement_type="none",
                value=None,
                unit=None,
                measurement_crs=None,
                reason=REASON_INVALID,
            )
    elif not feat.geometry_valid:
        return FeatureMeasurement(
            **base_kwargs,
            measurement_type="none",
            value=None,
            unit=None,
            measurement_crs=None,
            reason=REASON_INVALID,
        )

    # --- Point types: no measurement required ---
    if is_point_type(geom):
        return FeatureMeasurement(
            **base_kwargs,
            measurement_type="none",
            value=None,
            unit=None,
            measurement_crs=crs.measurement_crs,
            reason=REASON_POINT,
        )

    # --- Project geometry to measurement CRS ---
    try:
        projected_geom = _project_geometry(
            geom,
            source_crs=crs.source_crs,
            measurement_crs=crs.measurement_crs,
        )
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
                reason=f"LENGTH_CALCULATION_FAILED: {exc}",
            )

    # --- Unsupported geometry type ---
    return FeatureMeasurement(
        **base_kwargs,
        measurement_type="none",
        value=None,
        unit=None,
        measurement_crs=None,
        reason=REASON_UNSUPPORTED,
    )
