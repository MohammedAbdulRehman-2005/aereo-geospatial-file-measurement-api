"""Deterministic quality and anomaly analysis layer.

Checks run before the AI layer and produce structured findings grounded
in computed facts. Uses robust statistics (IQR/MAD) for outlier detection.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.services.measurement_service import FeatureMeasurement

logger = get_logger(__name__)

# Coordinate bounds for basic geographic sanity check
VALID_LON_RANGE = (-180.0, 180.0)
VALID_LAT_RANGE = (-90.0, 90.0)


@dataclass
class QualityFlag:
    """A single quality finding with severity and context."""

    severity: str  # LOW | MEDIUM | HIGH
    code: str
    message: str
    feature_indices: list[int] = field(default_factory=list)


@dataclass
class QualityReport:
    """Deterministic quality report for a processed file."""

    invalid_features: int
    empty_features: int
    unsupported_features: int
    missing_crs: bool
    mixed_geometry_types: bool
    geometry_type_counts: dict[str, int]
    flags: list[QualityFlag]
    # Summary statistics for measurable features
    polygon_area_stats: dict[str, float] | None
    linestring_length_stats: dict[str, float] | None


def _iqr_outlier_indices(values: list[float], feature_indices: list[int]) -> list[int]:
    """Return feature indices whose values fall outside 1.5×IQR bounds."""
    if len(values) < 4:
        return []
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    q1 = sorted_vals[n // 4]
    q3 = sorted_vals[(3 * n) // 4]
    iqr = q3 - q1
    if iqr == 0:
        return []
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    return [
        fi for v, fi in zip(values, feature_indices, strict=False)
        if v < lower or v > upper
    ]


def _describe(values: list[float]) -> dict[str, float]:
    """Basic descriptive statistics for a list of numeric values."""
    if not values:
        return {}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": round(statistics.mean(values), 4),
        "median": round(statistics.median(values), 4),
        "stdev": round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
    }


def run_quality_checks(
    measurements: list[FeatureMeasurement],
    source_crs: str | None,
) -> QualityReport:
    """
    Run all deterministic quality checks and return a structured report.

    This function is called on the output of the measurement engine,
    before any AI layer, so all findings are based on trusted computed facts.
    """
    flags: list[QualityFlag] = []

    # --- CRS check ---
    missing_crs = source_crs is None
    if missing_crs:
        flags.append(QualityFlag(
            severity="HIGH",
            code="MISSING_SOURCE_CRS",
            message="Source CRS is absent. Measurements may use a fallback projection.",
        ))

    # --- Geometry classification ---
    invalid_indices: list[int] = []
    empty_indices: list[int] = []
    unsupported_indices: list[int] = []
    type_counts: dict[str, int] = {}

    for m in measurements:
        gt = m.geometry_type
        type_counts[gt] = type_counts.get(gt, 0) + 1

        if m.geometry_empty:
            empty_indices.append(m.feature_index)
        elif not m.geometry_valid:
            invalid_indices.append(m.feature_index)

        if m.reason == "UNSUPPORTED_GEOMETRY_TYPE":
            unsupported_indices.append(m.feature_index)

    if invalid_indices:
        flags.append(QualityFlag(
            severity="MEDIUM",
            code="INVALID_GEOMETRY",
            message=f"{len(invalid_indices)} feature(s) have invalid geometry.",
            feature_indices=invalid_indices,
        ))

    if empty_indices:
        flags.append(QualityFlag(
            severity="LOW",
            code="EMPTY_GEOMETRY",
            message=f"{len(empty_indices)} feature(s) have empty geometry.",
            feature_indices=empty_indices,
        ))

    if unsupported_indices:
        flags.append(QualityFlag(
            severity="LOW",
            code="UNSUPPORTED_GEOMETRY_TYPE",
            message=f"{len(unsupported_indices)} feature(s) have unsupported geometry type.",
            feature_indices=unsupported_indices,
        ))

    # --- Mixed geometry types ---
    measurable_types = {gt for gt in type_counts if gt not in ("Unknown",)}
    mixed = len(measurable_types) > 1
    if mixed:
        flags.append(QualityFlag(
            severity="LOW",
            code="MIXED_GEOMETRY_TYPES",
            message=f"Dataset contains mixed geometry types: {sorted(measurable_types)}.",
        ))

    # --- Area outlier detection ---
    area_msmts = [m for m in measurements if m.measurement_type == "area" and m.value is not None]
    polygon_stats: dict[str, float] | None = None
    if area_msmts:
        area_values: list[float] = [float(m.value) for m in area_msmts if m.value is not None]
        area_indices = [m.feature_index for m in area_msmts]
        polygon_stats = _describe(area_values)

        outlier_indices = _iqr_outlier_indices(area_values, area_indices)
        if outlier_indices:
            flags.append(QualityFlag(
                severity="LOW",
                code="EXTREME_AREA",
                message=(
                    f"{len(outlier_indices)} polygon feature(s) have area values "
                    "that are statistical outliers (IQR method)."
                ),
                feature_indices=outlier_indices,
            ))

    # --- Length outlier detection ---
    len_msmts = [m for m in measurements if m.measurement_type == "length" and m.value is not None]
    length_stats: dict[str, float] | None = None
    if len_msmts:
        length_values: list[float] = [float(m.value) for m in len_msmts if m.value is not None]
        length_indices = [m.feature_index for m in len_msmts]
        length_stats = _describe(length_values)

        outlier_indices = _iqr_outlier_indices(length_values, length_indices)
        if outlier_indices:
            flags.append(QualityFlag(
                severity="LOW",
                code="EXTREME_LENGTH",
                message=(
                    f"{len(outlier_indices)} linestring feature(s) have length values "
                    "that are statistical outliers (IQR method)."
                ),
                feature_indices=outlier_indices,
            ))

    return QualityReport(
        invalid_features=len(invalid_indices),
        empty_features=len(empty_indices),
        unsupported_features=len(unsupported_indices),
        missing_crs=missing_crs,
        mixed_geometry_types=mixed,
        geometry_type_counts=type_counts,
        flags=flags,
        polygon_area_stats=polygon_stats,
        linestring_length_stats=length_stats,
    )
