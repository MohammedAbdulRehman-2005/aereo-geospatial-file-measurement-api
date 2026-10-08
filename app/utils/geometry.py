"""Geometry utility helpers."""
from __future__ import annotations

from shapely.geometry.base import BaseGeometry


def is_measurable_polygon(geom: BaseGeometry) -> bool:
    return geom.geom_type in ("Polygon", "MultiPolygon")


def is_measurable_line(geom: BaseGeometry) -> bool:
    return geom.geom_type in ("LineString", "MultiLineString", "LinearRing")


def is_point_type(geom: BaseGeometry) -> bool:
    return geom.geom_type in ("Point", "MultiPoint")


def safe_repair(geom: BaseGeometry) -> tuple[BaseGeometry, bool]:
    """
    Perform a deterministic topology repair attempt using buffer(0).

    Note: This is a deterministic topology repair attempt, not non-destructive
    healing, as buffer(0) can alter geometry topology and vertex layout.

    Returns (repaired_geom, was_repaired).
    Applies only to Polygon and MultiPolygon types.
    """
    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        return geom, False
    try:
        repaired = geom.buffer(0)
        if repaired.is_valid and not repaired.is_empty:
            return repaired, True
    except Exception:
        pass
    return geom, False
