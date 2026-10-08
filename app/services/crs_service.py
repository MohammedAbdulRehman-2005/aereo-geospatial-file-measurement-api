"""CRS detection and projected-CRS selection service using PyProj.

Critical engineering requirements (Specification §8):
- Never measure area/length in geographic (degree) coordinates.
- Always select an appropriate projected CRS for measurement.
- Inspect CRS units and verify suitability for planar calculations.
- Persist both source and measurement CRS.
- Return units explicitly (meters / m²).
- Handle missing/unknown CRS as a controlled error.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

import shapely
from shapely.geometry import MultiPoint
from shapely.geometry.base import BaseGeometry

from app.core.exceptions import MissingCRSError
from app.core.logging import get_logger
from app.parsers.base import ParsedFeature

logger = get_logger(__name__)

REGIONAL_EXTENT_THRESHOLD_DEG = 20.0

# Attempt importing native pyproj
PYPROJ_AVAILABLE = False
try:
    import pyproj
    from pyproj import CRS
    from pyproj.aoi import AreaOfInterest
    from pyproj.database import query_utm_crs_info
    # Verify C backend functionality
    _ = CRS.from_epsg(4326)
    PYPROJ_AVAILABLE = True
except Exception:
    PYPROJ_AVAILABLE = False


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
    Standard 6-degree longitude band formula with PyProj query if available.
    """
    if PYPROJ_AVAILABLE:
        try:
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

    # Standard mathematical formulation
    zone_number = math.floor((lon + 180.0) / 6.0) + 1
    zone_number = max(1, min(60, int(zone_number)))
    if lat >= 0:
        epsg = 32600 + zone_number  # WGS 84 / UTM zone XXN
    else:
        epsg = 32700 + zone_number  # WGS 84 / UTM zone XXS
    return f"EPSG:{epsg}"


def _is_known_projected(crs_str: str) -> tuple[bool, str]:
    """
    Inspect whether crs_str is projected and identify its linear unit.
    Returns (is_projected, unit_name).
    """
    if PYPROJ_AVAILABLE:
        try:
            crs_obj = CRS.from_user_input(crs_str)
            if crs_obj.is_projected:
                unit_name = "m"
                try:
                    axis_unit = crs_obj.axis_info[0].unit_name.lower()
                    if axis_unit in ("metre", "meter", "m"):
                        unit_name = "m"
                    else:
                        unit_name = axis_unit
                except Exception:
                    pass
                return True, unit_name
            return False, "degree"
        except Exception:
            pass

    # Fallback heuristic inspection
    upper = crs_str.upper()
    if any(p in upper for p in ("EPSG:326", "EPSG:327", "EPSG:3857", "EPSG:6933", "EPSG:8857")):
        return True, "m"
    if any(p in upper for p in ("UTM", "PROJCS", "MERCATOR", "PROJECTED")):
        return True, "m"
    return False, "degree"


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
    Resolve the appropriate projected CRS for measurement.

    Algorithm:
    1. If source CRS is missing → raise MissingCRSError.
    2. If source CRS is already projected in metres → verify and use directly.
    3. If source CRS is projected in non-metre units (e.g. feet) → reproject to local UTM.
    4. If geographic (e.g. EPSG:4326) and extent <= 20° → pick local UTM zone.
    5. If global / continental extent (> 20°) → use Equal Earth (EPSG:8857).

    Raises MissingCRSError if CRS cannot be determined.
    """
    if not source_crs_str:
        raise MissingCRSError(
            "Source CRS is absent or unrecognised. Cannot perform measurement "
            "without a valid coordinate reference system.",
            details={"received_crs": source_crs_str},
        )

    # If features list is passed
    if isinstance(features_or_gdf, (list, tuple)):
        features: Sequence[ParsedFeature] = features_or_gdf
    else:
        features = getattr(features_or_gdf, "features", [])

    # Check if already projected
    is_projected, unit_name = _is_known_projected(source_crs_str)
    if is_projected:
        if unit_name in ("m", "metre", "meter"):
            return CRSResolution(
                source_crs=source_crs_str,
                measurement_crs=source_crs_str,
                is_projected=True,
                unit="m",
                note="Source CRS is already projected in metres.",
            )
        else:
            logger.warning(
                "Source CRS uses non-metre units; selecting UTM for SI calculation",
                extra={"source_crs": source_crs_str, "unit": unit_name},
            )

    # Geographic CRS or non-metre projected CRS: calculate centroid and extent
    lon, lat, extent_deg = _compute_centroid_and_extent(features)

    if extent_deg <= REGIONAL_EXTENT_THRESHOLD_DEG:
        measurement_epsg = _utm_epsg_for_centroid(lon, lat)
        note = (
            f"Geographic source CRS reprojected to UTM zone {measurement_epsg} "
            f"based on dataset centroid ({lon:.4f}, {lat:.4f})."
        )
    else:
        measurement_epsg = "EPSG:8857"
        note = (
            "Dataset extent is global/very large. "
            "Using Equal Earth projection (EPSG:8857) for equal-area measurement."
        )

    logger.info(
        "CRS resolved",
        extra={"source_crs": source_crs_str, "measurement_crs": measurement_epsg},
    )

    return CRSResolution(
        source_crs=source_crs_str,
        measurement_crs=measurement_epsg,
        is_projected=False,
        unit="m",
        note=note,
    )
