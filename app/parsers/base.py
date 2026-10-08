"""Base parser interface and normalized feature representation."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import geopandas as gpd


@dataclass
class ParsedFeature:
    """Normalized representation of a single geospatial feature."""

    index: int
    geometry_type: str  # Canonical Shapely geometry type string
    geometry: Any | None  # Shapely geometry object or None
    properties: dict[str, Any] = field(default_factory=dict)
    geometry_json: dict[str, Any] | None = None
    geometry_valid: bool = True
    geometry_empty: bool = False
    validation_message: str | None = None


@dataclass
class ParseResult:
    """Result of parsing a geospatial file."""

    features: list[ParsedFeature]
    source_crs: str | None
    format: str  # "KML" | "SHAPEFILE"
    raw_gdf: gpd.GeoDataFrame | None = None  # Kept for CRS resolution


class BaseParser(ABC):
    """Abstract parser interface. All parsers must produce normalized features."""

    @abstractmethod
    def parse(self, path: str) -> ParseResult:
        """Parse the file at *path* and return a normalized ParseResult."""
        ...

    def _classify_geometry(self, geom: Any) -> tuple[str, bool, bool, str | None]:
        """
        Classify a geometry object.

        Returns (geom_type, is_valid, is_empty, validation_message).
        """
        if geom is None:
            return "Unknown", False, True, "Geometry is null."
        try:
            gtype = geom.geom_type
            is_empty = geom.is_empty
            if is_empty:
                return gtype, True, True, "Geometry is empty."
            is_valid = geom.is_valid
            msg = None if is_valid else f"Invalid geometry: {geom.is_valid}"
            return gtype, is_valid, False, msg
        except Exception as exc:
            return "Unknown", False, True, f"Error inspecting geometry: {exc}"
