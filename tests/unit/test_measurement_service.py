"""Unit tests for Measurement Engine (Specification §6, §8, §9)."""
from shapely.geometry import LineString, Point, Polygon

from app.parsers.base import ParsedFeature
from app.services.crs_service import CRSResolution
from app.services.measurement_service import (
    REASON_EMPTY,
    REASON_INVALID,
    REASON_POINT,
    measure_features,
)

CRS_UTM43 = CRSResolution(
    source_crs="EPSG:4326",
    measurement_crs="EPSG:32643",
    is_projected=False,
    unit="m",
)


def test_polygon_area_calculation():
    """Verify polygon area is calculated in square meters (m²) and non-zero."""
    # ~0.1 deg square near Bangalore (~120 km² = ~1.2e8 m²)
    poly = Polygon([(77.5, 12.9), (77.6, 12.9), (77.6, 13.0), (77.5, 13.0), (77.5, 12.9)])
    features = [
        ParsedFeature(index=0, geometry_type="Polygon", geometry=poly, geometry_valid=True)
    ]
    results = measure_features(features, CRS_UTM43)

    assert len(results) == 1
    m = results[0]
    assert m.measurement_type == "area"
    assert m.unit == "m2"
    assert m.value is not None
    assert 110_000_000 < m.value < 130_000_000  # expected approx 120 km²
    assert m.reason is None


def test_linestring_length_calculation():
    """Verify LineString length is calculated in meters (m) and non-zero."""
    # 0.1 deg line east-west at lat 12.9 (~10.8 km = ~10,800 m)
    line = LineString([(77.5, 12.9), (77.6, 12.9)])
    features = [
        ParsedFeature(index=0, geometry_type="LineString", geometry=line, geometry_valid=True)
    ]
    results = measure_features(features, CRS_UTM43)

    assert len(results) == 1
    m = results[0]
    assert m.measurement_type == "length"
    assert m.unit == "m"
    assert m.value is not None
    assert 10_000 < m.value < 12_000  # expected approx 10.8 km
    assert m.reason is None


def test_point_no_measurement_behavior():
    """Verify Point features return explicit POINT_REQUIRES_NO_MEASUREMENT reason."""
    pt = Point(77.5, 12.9)
    features = [
        ParsedFeature(index=0, geometry_type="Point", geometry=pt, geometry_valid=True)
    ]
    results = measure_features(features, CRS_UTM43)

    assert len(results) == 1
    m = results[0]
    assert m.measurement_type == "none"
    assert m.value is None
    assert m.reason == REASON_POINT


def test_empty_geometry_handling():
    """Verify empty geometries return EMPTY_GEOMETRY and do not crash."""
    features = [
        ParsedFeature(index=0, geometry_type="Polygon", geometry=Polygon(), geometry_empty=True)
    ]
    results = measure_features(features, CRS_UTM43)

    assert len(results) == 1
    m = results[0]
    assert m.value is None
    assert m.reason == REASON_EMPTY


def test_invalid_bowtie_polygon_repaired():
    """Verify self-intersecting polygon is safely repaired and measured."""
    bowtie = Polygon([(77.5, 12.9), (77.6, 13.0), (77.6, 12.9), (77.5, 13.0), (77.5, 12.9)])
    features = [
        ParsedFeature(index=0, geometry_type="Polygon", geometry=bowtie, geometry_valid=False)
    ]
    results = measure_features(features, CRS_UTM43)

    assert len(results) == 1
    m = results[0]
    # Repaired via buffer(0)
    assert m.measurement_type == "area"
    assert m.value is not None
    assert m.value > 0
    assert m.reason is None
