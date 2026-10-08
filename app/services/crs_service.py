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
    is_metre_unit: bool = False
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
                    is_metre_unit=True,
                    is_measurement_suitable=True,
                )
            else:
                return CRSInspection(
                    identified_crs=identified,
                    is_projected=True,
                    is_geographic=False,
                    unit_name=unit_name,
                    is_metre_unit=False,
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


def _is_crs_geographically_aligned(crs_obj: Any, lon: float, lat: float) -> bool:
    """Check whether a dataset centroid (lon, lat) in degrees falls within the CRS area of use."""
    try:
        aou = getattr(crs_obj, "area_of_use", None)
        if aou is None or aou.bounds is None:
            return True
        west, south, east, north = aou.bounds
        # Worldwide CRS covers all longitudes/latitudes
        if west <= -170 and east >= 170 and south <= -80 and north >= 80:
            return True
        buffer_deg = 3.0
        return (west - buffer_deg <= lon <= east + buffer_deg) and (
            south - buffer_deg <= lat <= north + buffer_deg
        )
    except Exception:
        return True


def resolve_measurement_crs(
    features_or_gdf: Any,
    source_crs_str: str | None,
) -> CRSResolution:
    """
    Resolve the authoritative projected CRS for measurement.

    Deterministically evaluates:
    Case A — Geographic CRS (e.g. EPSG:4326): Angular degree units. Reprojected to local UTM zone or Equal Earth.
    Case B — Web Mercator (EPSG:3857): Conformal distortion. Reprojected to local UTM zone or Equal Earth.
    Case C — Projected non-metre CRS (e.g. US survey feet): Reprojected to metre-based local UTM zone or Equal Earth.
    Case D — Projected metre CRS:
      - If dataset extent > 20° (configurable REGIONAL_EXTENT_THRESHOLD_DEG):
        Transitions to Equal Earth (EPSG:8857) for continental equal-area integrity.
      - If dataset extent <= 20°:
        Verifies geographic alignment between dataset centroid and CRS area of use.
        If geographically aligned (or area of use covers the dataset): Preserves source CRS.
        If geographically mismatched (e.g. European UTM applied to Indian dataset):
        Reprojects to the dataset's actual local UTM zone.
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
    from pyproj import CRS

    source_crs_obj = CRS.from_user_input(source_crs_str)

    raw_centroid_x, raw_centroid_y, raw_extent = _compute_centroid_and_extent(features)

    # Determine lon, lat in degrees and extent in degrees.
    # If the feature coordinates are already in angular degree ranges (-180..180, -90..90),
    # use them directly; otherwise project from projected coordinates to geographic WGS84.
    if abs(raw_centroid_x) <= 180.0 and abs(raw_centroid_y) <= 90.0:
        lon, lat, extent_deg = raw_centroid_x, raw_centroid_y, raw_extent
    elif inspection.is_projected:
        try:
            from pyproj import Transformer

            to_geo = Transformer.from_crs(source_crs_obj, "EPSG:4326", always_xy=True)
            lon, lat = to_geo.transform(raw_centroid_x, raw_centroid_y)
            # Estimate extent in degrees from projected metres
            extent_deg = (
                (raw_extent / 111000.0) if inspection.is_metre_unit else (raw_extent / 364000.0)
            )
        except Exception:
            lon, lat, extent_deg = raw_centroid_x, raw_centroid_y, 0.0
    else:
        lon, lat, extent_deg = raw_centroid_x, raw_centroid_y, raw_extent

    # Case D: Projected metre CRS
    if inspection.is_projected and inspection.is_metre_unit and inspection.is_measurement_suitable:
        # Check if dataset extent exceeds the configurable 20° regional limit
        if extent_deg > REGIONAL_EXTENT_THRESHOLD_DEG:
            measurement_epsg = "EPSG:8857"
            note = (
                f"Source CRS is projected in metres, but dataset extent ({extent_deg:.2f}°) exceeds "
                f"the configurable threshold ({REGIONAL_EXTENT_THRESHOLD_DEG}°). "
                "Reprojected to Equal Earth (EPSG:8857) to ensure continental equal-area fidelity."
            )
            return CRSResolution(
                source_crs=source_crs_str,
                measurement_crs=measurement_epsg,
                is_projected=True,
                unit="m",
                note=note,
            )

        # Check geographic alignment between dataset centroid and CRS area of use
        if (
            features
            and (raw_centroid_x != 0.0 or raw_centroid_y != 0.0)
            and not _is_crs_geographically_aligned(source_crs_obj, lon, lat)
        ):
            measurement_epsg = _utm_epsg_for_centroid(lon, lat)
            note = (
                f"Source CRS ('{source_crs_str}') is projected in metres but geographically mismatched "
                f"with dataset centroid ({lon:.4f}, {lat:.4f}). "
                f"Reprojected to matching local UTM zone {measurement_epsg}."
            )
            return CRSResolution(
                source_crs=source_crs_str,
                measurement_crs=measurement_epsg,
                is_projected=True,
                unit="m",
                note=note,
            )

        return CRSResolution(
            source_crs=source_crs_str,
            measurement_crs=inspection.identified_crs,
            is_projected=True,
            unit="m",
            note="Source CRS is verified as an authoritative projected CRS in metres appropriate for this dataset.",
        )

    # Cases A, B, C (Geographic, Web Mercator, or non-metre projected):
    # Reproject to local UTM zone or Equal Earth based on extent.
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
