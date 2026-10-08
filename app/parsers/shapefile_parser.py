"""Shapefile parser adapter using pyshp and Shapely."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import shapefile
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from app.core.exceptions import InvalidGeospatialDataError
from app.core.logging import get_logger
from app.parsers.base import BaseParser, ParseResult, ParsedFeature

logger = get_logger(__name__)


def _extract_crs_from_prj(prj_path: Path) -> str | None:
    """Read .prj file and detect EPSG or projection name."""
    if not prj_path.exists():
        return None
    try:
        content = prj_path.read_text(encoding="utf-8", errors="ignore").strip()
        if not content:
            return None
        # Check for EPSG code inside AUTHORITY["EPSG","XXXX"]
        epsg_match = re.search(r'AUTHORITY\["EPSG",\s*"?(\d+)"?\]', content)
        if epsg_match:
            return f"EPSG:{epsg_match.group(1)}"
        # Check for WGS 84 / GCS_WGS_1984
        if "WGS_1984" in content or "WGS 84" in content or "4326" in content:
            return "EPSG:4326"
        # Check for Web Mercator
        if "3857" in content or "Pseudo-Mercator" in content:
            return "EPSG:3857"
        # Extract projection/coordinate system name
        name_match = re.match(r'^[A-Z_]+\["([^"]+)"', content)
        if name_match:
            return name_match.group(1)
        return "UNKNOWN_PROJECTED"
    except Exception as exc:
        logger.warning("Error reading .prj file", extra={"error": str(exc)})
        return None


class ShapefileParser(BaseParser):
    """
    Parses Shapefile datasets using pyshp and Shapely.
    Companion files (.shx, .dbf, .prj) must be in the same directory.
    """

    def parse(self, path: str) -> ParseResult:
        shp_path = Path(path)
        if not shp_path.exists():
            raise InvalidGeospatialDataError(
                f"Shapefile not found: {shp_path.name}",
                details={"filename": shp_path.name},
            )

        logger.info("Parsing Shapefile", extra={"file_name": shp_path.name})

        # Read CRS from .prj
        prj_path = shp_path.with_suffix(".prj")
        source_crs = _extract_crs_from_prj(prj_path)

        features: list[ParsedFeature] = []

        try:
            with shapefile.Reader(str(shp_path)) as sf:
                if len(sf) == 0:
                    raise InvalidGeospatialDataError(
                        f"Shapefile contains no features: {shp_path.name}",
                        details={"filename": shp_path.name},
                    )

                field_names = [f[0] for f in sf.fields[1:]]  # skip DeletionFlag

                for idx, shape_rec in enumerate(sf.shapeRecords()):
                    # Extract geometry
                    geom: BaseGeometry | None = None
                    try:
                        geo_interface = shape_rec.shape.__geo_interface__
                        geom = shape(geo_interface)
                    except Exception as exc:
                        logger.warning(
                            "Failed to convert shape to geometry",
                            extra={"index": idx, "error": str(exc)},
                        )

                    # Extract properties
                    props: dict[str, Any] = {}
                    try:
                        record_values = shape_rec.record
                        for fname, val in zip(field_names, record_values):
                            if isinstance(val, bytes):
                                val = val.decode("utf-8", errors="ignore")
                            props[fname] = val
                    except Exception:
                        pass

                    gtype, is_valid, is_empty, msg = self._classify_geometry(geom)

                    features.append(ParsedFeature(
                        index=idx,
                        geometry_type=gtype,
                        geometry=geom,
                        properties=props,
                        geometry_valid=is_valid,
                        geometry_empty=is_empty,
                        validation_message=msg,
                    ))

        except InvalidGeospatialDataError:
            raise
        except Exception as exc:
            raise InvalidGeospatialDataError(
                f"Failed to parse Shapefile: {shp_path.name}",
                details={"error": str(exc)},
            ) from exc

        logger.info(
            "Shapefile parsing complete",
            extra={
                "file_name": shp_path.name,
                "feature_count": len(features),
                "source_crs": source_crs,
            },
        )

        return ParseResult(
            features=features,
            source_crs=source_crs,
            format="SHAPEFILE",
            raw_gdf=None,
        )
