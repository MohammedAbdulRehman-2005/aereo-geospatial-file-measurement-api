"""Unit tests for CRS resolution service (Specification §8)."""
import pytest
from shapely.geometry import Point, Polygon

from app.core.exceptions import MissingCRSError
from app.parsers.base import ParsedFeature
from app.services.crs_service import (
    _utm_epsg_for_centroid,
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
