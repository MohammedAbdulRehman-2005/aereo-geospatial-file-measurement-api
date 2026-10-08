"""Hardened KML parser adapter using defusedxml and Shapely geometry normalization."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import defusedxml.ElementTree as defused_ET
from defusedxml.common import DefusedXmlException, DTDForbidden, EntitiesForbidden
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
    mapping,
)
from shapely.geometry.base import BaseGeometry

from app.core.config import settings
from app.core.exceptions import FeatureLimitExceededError, InvalidGeospatialDataError
from app.core.logging import get_logger
from app.parsers.base import BaseParser, ParsedFeature, ParseResult

logger = get_logger(__name__)

KML_DEFAULT_CRS = "EPSG:4326"


def _clean_tag(elem: Any) -> str:
    """Return local XML tag without namespace."""
    tag = getattr(elem, "tag", "")
    return tag.split("}")[-1] if "}" in tag else tag


def _parse_coordinates(coord_str: str) -> list[tuple[float, float]]:
    """Parse KML coordinate tuple string 'lon,lat,alt lon,lat,alt' robustly."""
    coords: list[tuple[float, float]] = []
    cleaned = coord_str.replace(";", " ")
    tokens = re.split(r"\s+", cleaned.strip())
    for token in tokens:
        if not token:
            continue
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                lon = float(parts[0])
                lat = float(parts[1])
                coords.append((lon, lat))
            except ValueError:
                continue
    return coords


def _close_ring(ring: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """
    Ensure linear ring is properly closed (first point == last point per OGC specifications).
    If unclosed and len >= 3, append the first point.
    """
    if len(ring) >= 3:
        if ring[0] != ring[-1]:
            return list(ring) + [ring[0]]
        return ring
    return ring


def _parse_geometry_element(elem: Any) -> BaseGeometry | None:
    """Extract Shapely geometry from a KML XML element with ring closure and validation."""
    tag = _clean_tag(elem)

    if tag == "Point":
        for sub in elem.iter():
            if _clean_tag(sub) == "coordinates" and sub.text:
                coords = _parse_coordinates(sub.text)
                if coords:
                    return Point(coords[0])
        return Point()

    elif tag in ("LineString", "LinearRing"):
        for sub in elem.iter():
            if _clean_tag(sub) == "coordinates" and sub.text:
                coords = _parse_coordinates(sub.text)
                if len(coords) >= 2:
                    return LineString(coords)
                elif len(coords) == 1:
                    return Point(coords[0])
        return LineString()

    elif tag == "Polygon":
        outer_ring: list[tuple[float, float]] = []
        inner_rings: list[list[tuple[float, float]]] = []

        for child in elem:
            ctag = _clean_tag(child)
            if ctag == "outerBoundaryIs":
                for sub in child.iter():
                    if _clean_tag(sub) == "coordinates" and sub.text:
                        outer_ring = _parse_coordinates(sub.text)
            elif ctag == "innerBoundaryIs":
                for sub in child.iter():
                    if _clean_tag(sub) == "coordinates" and sub.text:
                        ring = _parse_coordinates(sub.text)
                        if len(ring) >= 3:
                            inner_rings.append(_close_ring(ring))

        if len(outer_ring) >= 3:
            outer_closed = _close_ring(outer_ring)
            try:
                return Polygon(outer_closed, inner_rings)
            except Exception:
                return Polygon()
        return Polygon()

    elif tag in ("MultiGeometry", "MultiPolygon", "MultiLineString"):
        parts: list[BaseGeometry] = []
        for child in elem:
            g = _parse_geometry_element(child)
            if g is not None and not g.is_empty:
                parts.append(g)
        if not parts:
            return None
        if all(isinstance(p, Polygon) for p in parts):
            return MultiPolygon(parts)
        elif all(isinstance(p, LineString) for p in parts):
            return MultiLineString(parts)
        return GeometryCollection(parts) if len(parts) > 1 else parts[0]

    return None


class KMLParser(BaseParser):
    """
    Hardened KML parser supporting defusedxml parsing, XML bomb / XXE protection,
    and GeoJSON geometry normalization.
    """

    def parse(self, path: str) -> ParseResult:
        file_path = Path(path)
        if not file_path.exists():
            raise InvalidGeospatialDataError(
                f"KML file not found: {file_path.name}",
                details={"filename": file_path.name},
            )

        logger.info("Parsing KML file", extra={"file_name": file_path.name})

        # --- Hardened XML Parsing using defusedxml ---
        try:
            tree = defused_ET.parse(str(file_path))
            root = tree.getroot()
        except (DefusedXmlException, DTDForbidden, EntitiesForbidden) as exc:
            raise InvalidGeospatialDataError(
                f"KML file contains forbidden XML DOCTYPE or ENTITY definition: {exc}",
                details={"file_name": file_path.name, "error": str(exc)},
            ) from exc
        except Exception as exc:
            raise InvalidGeospatialDataError(
                f"Failed to parse KML file XML structure: {file_path.name}",
                details={"error": str(exc)},
            ) from exc

        placemarks = [el for el in root.iter() if _clean_tag(el) == "Placemark"]
        if not placemarks:
            raise InvalidGeospatialDataError(
                "KML file contains no Placemark features.",
                details={"filename": file_path.name},
            )

        if len(placemarks) > settings.max_features:
            raise FeatureLimitExceededError(
                f"KML feature count ({len(placemarks)}) exceeds maximum allowed limit of {settings.max_features} features.",
                details={"feature_count": len(placemarks), "max_features": settings.max_features},
            )

        features: list[ParsedFeature] = []
        for idx, pm in enumerate(placemarks):
            props: dict[str, Any] = {}
            geom: BaseGeometry | None = None

            for child in pm:
                ctag = _clean_tag(child)
                if ctag == "name" and child.text:
                    props["name"] = child.text.strip()
                elif ctag == "description" and child.text:
                    props["description"] = child.text.strip()
                elif ctag == "ExtendedData":
                    for data_el in child.iter():
                        tag_name = _clean_tag(data_el)
                        if tag_name in ("Data", "SimpleData"):
                            d_name = data_el.attrib.get("name")
                            val = "".join(data_el.itertext()).strip()
                            if d_name:
                                props[d_name] = val
                elif ctag in ("Polygon", "LineString", "Point", "MultiGeometry", "LinearRing"):
                    geom = _parse_geometry_element(child)

            # If geometry wasn't direct child, search subtree
            if geom is None:
                for sub in pm.iter():
                    stag = _clean_tag(sub)
                    if stag in ("Polygon", "LineString", "Point", "MultiGeometry", "LinearRing") and sub != pm:
                        geom = _parse_geometry_element(sub)
                        if geom is not None and not geom.is_empty:
                            break

            gtype, is_valid, is_empty, msg = self._classify_geometry(geom)

            geom_json: dict[str, Any] | None = None
            if geom is not None and not is_empty:
                try:
                    geom_json = mapping(geom)
                except Exception:
                    pass

            features.append(ParsedFeature(
                index=idx,
                geometry_type=gtype,
                geometry=geom,
                geometry_json=geom_json,
                properties=props,
                geometry_valid=is_valid,
                geometry_empty=is_empty,
                validation_message=msg,
            ))

        logger.info(
            "KML parsing complete",
            extra={"file_name": file_path.name, "feature_count": len(features)},
        )

        return ParseResult(
            features=features,
            source_crs=KML_DEFAULT_CRS,
            format="KML",
            raw_gdf=None,
        )
