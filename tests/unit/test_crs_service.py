"""Unit tests for CRS resolution service (Specification §8)."""
import pytest
from shapely.geometry import Point, Polygon

from app.core.exceptions import MissingCRSError
from app.parsers.base import ParsedFeature
from app.services.crs_service import (
    _utm_epsg_for_centroid,
    inspect_crs,
    resolve_measurement_crs,
)


def test_utm_zone_calculation():
    """Verify UTM zone EPSG calculation for various locations."""
    # Bangalore (lon 77.59, lat 12.97) -> Zone 43 North -> EPSG:32643
    assert _utm_epsg_for_centroid(77.59, 12.97) == "EPSG:32643"

    # London (lon -0.12, lat 51.50) -> Zone 30 North -> EPSG:32630
    assert _utm_epsg_for_centroid(-0.12, 51.50) == "EPSG:32630"

    # Sydney (lon 151.20, lat -33.86) -> Zone 56 South -> EPSG:32756
    assert _utm_epsg_for_centroid(151.20, -33.86) == "EPSG:32756"

    # New York (lon -74.00, lat 40.71) -> Zone 18 North -> EPSG:32618
    assert _utm_epsg_for_centroid(-74.00, 40.71) == "EPSG:32618"


def test_missing_crs_raises_error():
    """Verify that absent CRS raises MissingCRSError and never silently assumes a CRS."""
    features = [
        ParsedFeature(index=0, geometry_type="Point", geometry=Point(77.5, 12.9))
    ]
    with pytest.raises(MissingCRSError):
        resolve_measurement_crs(features, None)

    with pytest.raises(MissingCRSError):
        resolve_measurement_crs(features, "")

    with pytest.raises(MissingCRSError):
        resolve_measurement_crs(features, "   ")


def test_invalid_crs_string_raises_missing_crs_error():
    """Verify that invalid CRS strings raise MissingCRSError."""
    features = [
        ParsedFeature(index=0, geometry_type="Point", geometry=Point(77.5, 12.9))
    ]
    with pytest.raises(MissingCRSError):
        resolve_measurement_crs(features, "INVALID_CRS_XYZ_9999")


def test_projected_source_crs_preserved():
    """Verify that an already-projected CRS is preserved for measurement."""
    features = [
        ParsedFeature(index=0, geometry_type="Polygon", geometry=Polygon([(100, 100), (200, 100), (200, 200), (100, 100)]))
    ]
    res = resolve_measurement_crs(features, "EPSG:32643")
    assert res.source_crs == "EPSG:32643"
    assert res.measurement_crs == "EPSG:32643"
    assert res.is_projected is True
    assert res.unit == "m"


def test_web_mercator_epsg3857_rejected_and_reprojected():
    """
    Verify that EPSG:3857 (Web Mercator) is recognized as unsuitable for authoritative
    planar area/distance calculations, and is reprojected to local UTM.
    """
    features = [
        ParsedFeature(
            index=0,
            geometry_type="Polygon",
            geometry=Polygon([(77.5, 12.9), (77.6, 12.9), (77.6, 13.0), (77.5, 13.0), (77.5, 12.9)]),
        )
    ]
    res = resolve_measurement_crs(features, "EPSG:3857")
    assert res.source_crs == "EPSG:3857"
    # Must NOT measure in EPSG:3857; must reproject to local UTM
    assert res.measurement_crs == "EPSG:32643"
    assert res.is_projected is False
    assert res.unit == "m"


def test_authoritative_crs_inspection():
    """Verify explicit distinction between identification, type, units, and suitability."""
    # EPSG:4326 - Geographic, angular degree
    geo = inspect_crs("EPSG:4326")
    assert geo.is_geographic is True
    assert geo.is_projected is False
    assert geo.is_measurement_suitable is False
    assert "degree" in geo.unit_name

    # EPSG:3857 - Projected, metre, but conformal with extreme area distortion
    merc = inspect_crs("EPSG:3857")
    assert merc.is_projected is True
    assert merc.is_geographic is False
    assert merc.is_measurement_suitable is False
    assert merc.rejection_reason is not None

    # EPSG:32643 - Projected UTM zone, metre, suitable
    utm = inspect_crs("EPSG:32643")
    assert utm.is_projected is True
    assert utm.is_geographic is False
    assert utm.is_measurement_suitable is True
    assert utm.unit_name == "m"


def test_geographic_crs_reprojected_to_utm():
    """Verify that geographic CRS (EPSG:4326) is resolved to local UTM zone."""
    features = [
        ParsedFeature(
            index=0,
            geometry_type="Polygon",
            geometry=Polygon([(77.5, 12.9), (77.6, 12.9), (77.6, 13.0), (77.5, 13.0), (77.5, 12.9)])
        )
    ]
    res = resolve_measurement_crs(features, "EPSG:4326")
    assert res.source_crs == "EPSG:4326"
    assert res.measurement_crs == "EPSG:32643"
    assert res.is_projected is False
    assert res.unit == "m"
    assert "UTM zone EPSG:32643" in res.note


def test_southern_hemisphere_resolution():
    """Verify that coordinates in southern hemisphere resolve to EPSG:327XX."""
    features = [
        ParsedFeature(
            index=0,
            geometry_type="Point",
            geometry=Point(151.20, -33.86)  # Sydney
        )
    ]
    res = resolve_measurement_crs(features, "EPSG:4326")
    assert res.measurement_crs == "EPSG:32756"
    assert res.unit == "m"


def test_large_continental_extent_uses_equal_earth():
    """Verify that datasets with extent > 20 degrees use Equal Earth (EPSG:8857)."""
    # Span from lon 10 to 40 (30 degrees extent > 20)
    features = [
        ParsedFeature(
            index=0,
            geometry_type="Polygon",
            geometry=Polygon([(10.0, 10.0), (40.0, 10.0), (40.0, 35.0), (10.0, 35.0), (10.0, 10.0)])
        )
    ]
    res = resolve_measurement_crs(features, "EPSG:4326")
    assert res.measurement_crs == "EPSG:8857"
    assert "Equal Earth" in res.note


def test_projected_non_metre_crs_reprojected():
    """Verify Case C: Projected CRS with non-metre units (e.g. US survey feet) is reprojected."""
    # EPSG:2263 is NAD83 / New York Long Island (ftUS) (units: US survey foot)
    inspection = inspect_crs("EPSG:2263")
    assert inspection.is_projected is True
    assert inspection.is_metre_unit is False
    assert inspection.is_measurement_suitable is False

    # When resolving, it must be reprojected to an authoritative metre CRS
    features = [
        ParsedFeature(
            index=0,
            geometry_type="Point",
            geometry=Point(-74.00, 40.71),  # New York
        )
    ]
    res = resolve_measurement_crs(features, "EPSG:2263")
    assert res.measurement_crs == "EPSG:32618"
    assert res.unit == "m"


def test_projected_geographically_mismatched_crs_reprojected():
    """Verify Case D: Projected metre CRS mismatched with data location is reprojected to local UTM."""
    # EPSG:32633 is UTM Zone 33N (Europe), but data points are in Bangalore, India (lon 77.5, lat 12.9)
    features = [
        ParsedFeature(
            index=0,
            geometry_type="Polygon",
            geometry=Polygon([(77.5, 12.9), (77.6, 12.9), (77.6, 13.0), (77.5, 13.0), (77.5, 12.9)]),
        )
    ]
    res = resolve_measurement_crs(features, "EPSG:32633")
    # Must detect mismatch with India centroid and reproject to UTM Zone 43N
    assert res.measurement_crs == "EPSG:32643"
    assert "geographically mismatched" in res.note


def test_custom_valid_wkt_crs():
    """Verify that a valid custom WKT CRS without an EPSG number is inspected authoritatively."""
    # Custom Transverse Mercator WKT
    wkt = (
        'PROJCS["Custom_Grid",'
        'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
        'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
        'PROJECTION["Transverse_Mercator"],'
        'PARAMETER["False_Easting",500000.0],PARAMETER["False_Northing",0.0],'
        'PARAMETER["Central_Meridian",75.0],PARAMETER["Scale_Factor",0.9996],'
        'PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]'
    )
    inspection = inspect_crs(wkt)
    assert inspection.is_projected is True
    assert inspection.is_metre_unit is True
    assert inspection.is_measurement_suitable is True
