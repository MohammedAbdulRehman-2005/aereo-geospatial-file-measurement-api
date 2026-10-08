"""Unit tests for deterministic quality layer (Specification §12)."""
from app.services.measurement_service import FeatureMeasurement
from app.services.quality_service import run_quality_checks


def test_mixed_geometry_detection():
    """Verify mixed geometry types in a single dataset are detected."""
    ms = [
        FeatureMeasurement(
            feature_index=0,
            geometry_type="Polygon",
            measurement_type="area",
            value=1000.0,
            unit="m2",
            measurement_crs="EPSG:32643",
            reason=None,
            properties={},
            geometry_valid=True,
            geometry_empty=False,
            validation_message=None,
        ),
        FeatureMeasurement(
            feature_index=1,
            geometry_type="LineString",
            measurement_type="length",
            value=50.0,
            unit="m",
            measurement_crs="EPSG:32643",
            reason=None,
            properties={},
            geometry_valid=True,
            geometry_empty=False,
            validation_message=None,
        ),
    ]
    report = run_quality_checks(ms, "EPSG:4326")
    assert report.mixed_geometry_types is True
    assert any(f.code == "MIXED_GEOMETRY_TYPES" for f in report.flags)


def test_outlier_detection_iqr():
    """Verify statistical outlier area detection via IQR."""
    # 9 small polygons (values ~100) and 1 extreme outlier (value 100,000)
    values = [100.0, 102.0, 98.0, 101.0, 99.0, 105.0, 97.0, 103.0, 100.0, 100_000.0]
    ms = [
        FeatureMeasurement(
            feature_index=i,
            geometry_type="Polygon",
            measurement_type="area",
            value=v,
            unit="m2",
            measurement_crs="EPSG:32643",
            reason=None,
            properties={},
            geometry_valid=True,
            geometry_empty=False,
            validation_message=None,
        )
        for i, v in enumerate(values)
    ]
    report = run_quality_checks(ms, "EPSG:4326")
    assert any(f.code == "EXTREME_AREA" for f in report.flags)
    outlier_flag = next(f for f in report.flags if f.code == "EXTREME_AREA")
    assert 9 in outlier_flag.feature_indices
