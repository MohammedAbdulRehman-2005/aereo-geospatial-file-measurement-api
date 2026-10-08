"""CRS detection, validation, and projected-CRS selection service using PyProj.

Engineering requirements:
- Authoritative PyProj metadata inspection (CRS.from_user_input, is_projected, is_geographic, axis_info, to_epsg).
- Explicit distinction between:
  1. CRS identification
  2. CRS type (projected vs geographic)
  3. Coordinate units (metre vs degree vs foot, etc.)
  4. Measurement suitability (unsuitable projected CRSs like EPSG:3857 Web Mercator are rejected for planar measurement)
  5. Target measurement CRS
- Never measure area or length directly in geographic (degree) coordinates.
- Regional extent threshold (default: 20.0 degrees) is a configurable application-level engineering threshold
  used to transition from a local projected CRS strategy to a broader equal-area measurement projection (Equal Earth),
  not a universal geodetic law.
- Persist both source and measurement CRS.
- Handle missing or unresolvable CRS as a controlled application error (MissingCRSError).
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import shapely
from shapely.geometry import MultiPoint

from app.core.exceptions import MissingCRSError
from app.core.logging import get_logger
from app.parsers.base import ParsedFeature

logger = get_logger(__name__)

# Configurable engineering threshold (in degrees) used to transition from a local
# projected CRS strategy (UTM) to a broader equal-area measurement projection (Equal Earth).
# Note: This is an application-level engineering heuristic, not a universal geodetic law.
REGIONAL_EXTENT_THRESHOLD_DEG: float = 20.0


@dataclass(frozen=True)
class CRSInspection:
    """Authoritative CRS inspection result distinguishing type, units, and suitability."""

    identified_crs: str
    is_projected: bool
    is_geographic: bool
    unit_name: str
    is_measurement_suitable: bool
    rejection_reason: str | None = None


@dataclass(frozen=True)
class CRSResolution:
    """Result of CRS resolution for measurement."""

    source_crs: str
    measurement_crs: str
    is_projected: bool
    unit: str  # 'm' for meters
    note: str = ""


def _utm_epsg_for_centroid(lon: float, lat: float) -> str:
    """
    Determine the appropriate UTM zone EPSG code for a centroid.
    Uses PyProj query_utm_crs_info when available, with standard 6-degree band calculation.
    """
    try:
        from pyproj.aoi import AreaOfInterest
        from pyproj.database import query_utm_crs_info

        utm_crs_list = query_utm_crs_info(
            datum_name="WGS 84",
            area_of_interest=AreaOfInterest(
                west_lon_degree=lon,
                south_lat_degree=lat,
                east_lon_degree=lon,
                north_lat_degree=lat,
            ),
        )
        if utm_crs_list:
            return f"EPSG:{utm_crs_list[0].code}"
    except Exception as exc:
        logger.debug("PyProj UTM query fallback", extra={"error": str(exc)})

    # Standard 6-degree longitude band formula
    zone_number = math.floor((lon + 180.0) / 6.0) + 1
    zone_number = max(1, min(60, int(zone_number)))
    if lat >= 0:
        epsg = 32600 + zone_number  # WGS 84 / UTM zone XXN
    else:
        epsg = 32700 + zone_number  # WGS 84 / UTM zone XXS
    return f"EPSG:{epsg}"


def inspect_crs(crs_str: str) -> CRSInspection:
    """
    Inspect a CRS string authoritatively using PyProj metadata.

    Explicitly distinguishes:
    1. Identification (EPSG code or canonical name)
    2. CRS type (projected vs geographic)
    3. Coordinate units (metre, degree, foot, etc.)
    4. Measurement suitability:
       - EPSG:3857 (Web Mercator) is projected but explicitly UN-SUITABLE for authoritative
         area/distance calculations due to severe latitude-dependent conformal scale distortion.
       - Geographic systems (degrees) are UN-SUITABLE for planar metric calculations.
       - Projected systems in non-metre units require reprojection to SI metres.
    """
    try:
        from pyproj import CRS

        crs_obj = CRS.from_user_input(crs_str)
        is_proj = crs_obj.is_projected
        is_geo = crs_obj.is_geographic

        # Extract coordinate unit from axis info
        unit_name = "unknown"
        if crs_obj.axis_info:
            unit_name = crs_obj.axis_info[0].unit_name.lower()

        epsg_code = crs_obj.to_epsg()
        identified = f"EPSG:{epsg_code}" if epsg_code else (crs_obj.name or crs_str)

        # Explicitly check for Web Mercator (EPSG:3857, 900913 or equivalent pseudo-mercator)
        crs_name_lower = (crs_obj.name or "").lower()
        if (
            epsg_code in (3857, 900913)
            or "pseudo-mercator" in crs_name_lower
            or "popular visualisations" in crs_name_lower
        ):
            return CRSInspection(
                identified_crs=identified,
                is_projected=True,
                is_geographic=False,
                unit_name=unit_name,
                is_measurement_suitable=False,
                rejection_reason=(
                    "Web Mercator (EPSG:3857) introduces severe latitude-dependent area distortion "
                    "and is unsuitable for authoritative area or distance measurement."
                ),
            )

        if is_proj:
            if unit_name in ("metre", "meter", "m"):
                return CRSInspection(
                    identified_crs=identified,
                    is_projected=True,
                    is_geographic=False,
                    unit_name="m",
                    is_measurement_suitable=True,
                )
            else:
                return CRSInspection(
                    identified_crs=identified,
                    is_projected=True,
                    is_geographic=False,
                    unit_name=unit_name,
                    is_measurement_suitable=False,
                    rejection_reason=f"Projected CRS uses '{unit_name}' units rather than SI metres.",
                )

        if is_geo:
            return CRSInspection(
                identified_crs=identified,
                is_projected=False,
                is_geographic=True,
                unit_name="degree",
                is_measurement_suitable=False,
                rejection_reason="Geographic CRS uses angular degree units unsuitable for planar measurement.",
            )

        return CRSInspection(
            identified_crs=identified,
            is_projected=False,
            is_geographic=False,
            unit_name=unit_name,
            is_measurement_suitable=False,
            rejection_reason="Unrecognized coordinate reference system type.",
        )

    except Exception as exc:
        logger.warning(
            "Authoritative PyProj CRS inspection failed",
            extra={"crs_str": crs_str, "error": str(exc)},
        )
        raise MissingCRSError(
            f"Failed to inspect or parse coordinate reference system '{crs_str}': {exc}",
            details={"crs": crs_str, "error": str(exc)},
        ) from exc


def _compute_centroid_and_extent(features: Sequence[ParsedFeature]) -> tuple[float, float, float]:
    """
    Compute centroid (lon, lat) and max bounding extent in degrees
    across all valid geometries in the feature list.
    """
    valid_geoms = [
        f.geometry for f in features
        if f.geometry is not None and not f.geometry.is_empty
    ]
    if not valid_geoms:
        return 0.0, 0.0, 0.0

    try:
        combined = shapely.unary_union(valid_geoms)
        centroid = combined.centroid
        bounds = combined.bounds  # (minx, miny, maxx, maxy)
        extent = max(bounds[2] - bounds[0], bounds[3] - bounds[1])
        return centroid.x, centroid.y, extent
    except Exception:
        pts = [g.centroid for g in valid_geoms]
        mp = MultiPoint(pts)
        centroid = mp.centroid
        bounds = mp.bounds
        extent = max(bounds[2] - bounds[0], bounds[3] - bounds[1])
        return centroid.x, centroid.y, extent


def resolve_measurement_crs(
    features_or_gdf: Any,
    source_crs_str: str | None,
) -> CRSResolution:
    """
    Resolve the authoritative projected CRS for measurement.

    Steps:
    1. If source CRS is missing or empty -> raise MissingCRSError.
    2. Inspect source CRS using authoritative PyProj metadata:
       - If already projected and suitable for planar metric calculation (e.g., local UTM in metres) -> use directly.
       - If projected but unsuitable (e.g. EPSG:3857 Web Mercator or non-metre units) -> reproject to an appropriate
         measurement CRS according to documented policy.
       - If geographic (e.g. EPSG:4326 in degrees) -> select appropriate measurement CRS.
    3. Target measurement CRS selection:
       - Centroid and extent computed across dataset features.
       - If extent <= REGIONAL_EXTENT_THRESHOLD_DEG (20.0 deg):
         Select the local UTM zone based on dataset centroid.
       - If extent > REGIONAL_EXTENT_THRESHOLD_DEG:
         Select Equal Earth projection (EPSG:8857) to ensure equal-area fidelity over continental extents.
    """
    if not source_crs_str or not source_crs_str.strip():
        raise MissingCRSError(
            "Source CRS is absent or unrecognized. Cannot perform measurement "
            "without a valid coordinate reference system.",
            details={"received_crs": source_crs_str},
        )

    # If features list is passed
    if isinstance(features_or_gdf, (list, tuple)):
        features: Sequence[ParsedFeature] = features_or_gdf
    else:
        features = getattr(features_or_gdf, "features", [])

    # Authoritative CRS inspection
    inspection = inspect_crs(source_crs_str)

    if inspection.is_measurement_suitable:
        return CRSResolution(
            source_crs=source_crs_str,
            measurement_crs=inspection.identified_crs,
            is_projected=True,
            unit="m",
            note="Source CRS is verified as an authoritative projected CRS in metres.",
        )

    # Non-suitable source CRS (Geographic, EPSG:3857, or non-metre projected):
    # Select appropriate measurement CRS based on centroid and spatial extent.
    lon, lat, extent_deg = _compute_centroid_and_extent(features)

    # Note: REGIONAL_EXTENT_THRESHOLD_DEG (20.0°) is a configurable application-level engineering threshold
    # used to determine when a dataset should transition from a local projected CRS strategy (UTM)
    # to a broader equal-area measurement projection (Equal Earth). It is not a universal geodetic law.
    if extent_deg <= REGIONAL_EXTENT_THRESHOLD_DEG:
        measurement_epsg = _utm_epsg_for_centroid(lon, lat)
        note = (
            f"Source CRS ('{source_crs_str}') reprojected to local UTM zone {measurement_epsg} "
            f"based on dataset centroid ({lon:.4f}, {lat:.4f})."
        )
    else:
        measurement_epsg = "EPSG:8857"
        note = (
            f"Dataset extent ({extent_deg:.2f}°) exceeds the configurable regional threshold ({REGIONAL_EXTENT_THRESHOLD_DEG}°). "
            "Using Equal Earth projection (EPSG:8857) for broader equal-area planar measurement."
        )

    logger.info(
        "CRS resolved",
        extra={"source_crs": source_crs_str, "measurement_crs": measurement_epsg, "note": note},
    )

    return CRSResolution(
        source_crs=source_crs_str,
        measurement_crs=measurement_epsg,
        is_projected=False,
        unit="m",
        note=note,
    )
