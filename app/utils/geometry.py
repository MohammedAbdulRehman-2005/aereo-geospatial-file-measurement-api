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
    Attempt a deterministic geometry repair using buffer(0).

    Returns (repaired_geom, was_repaired).
    Only repairs Polygon and MultiPolygon types where buffer(0) is safe.
    """
    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        return geom, False
    try:
        repaired = geom.buffer(0)
        if repaired.is_valid:
            return repaired, True
    except Exception:
        pass
    return geom, False
