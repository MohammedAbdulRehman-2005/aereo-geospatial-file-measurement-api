"""Shapefile parser adapter using pyshp and Shapely."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import shapefile
from pyproj import CRS
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from app.core.config import settings
from app.core.exceptions import FeatureLimitExceededError, InvalidGeospatialDataError
from app.core.logging import get_logger
from app.parsers.base import BaseParser, ParsedFeature, ParseResult

logger = get_logger(__name__)


def _extract_crs_from_prj(prj_path: Path) -> str | None:
    """Read .prj file and detect EPSG or preserve canonical WKT authoritatively using PyProj."""
    if not prj_path.exists():
        return None

    content = prj_path.read_text(encoding="utf-8", errors="ignore").strip()
    if not content:
        raise InvalidGeospatialDataError(
            f"Shapefile .prj file is empty: {prj_path.name}",
            details={"filename": prj_path.name},
        )

    try:
        crs_obj = CRS.from_wkt(content)
    except Exception as exc:
        raise InvalidGeospatialDataError(
            f"Failed to parse CRS from .prj file '{prj_path.name}': {exc}",
            details={"filename": prj_path.name, "error": str(exc)},
        ) from exc

    epsg = crs_obj.to_epsg()
    if epsg is not None:
        return f"EPSG:{epsg}"
    # Preserve the canonical WKT representation for valid custom CRS without an EPSG mapping
    return crs_obj.to_wkt()


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

                if len(sf) > settings.max_features:
                    raise FeatureLimitExceededError(
                        f"Shapefile feature count ({len(sf)}) exceeds maximum allowed limit of {settings.max_features} features.",
                        details={"feature_count": len(sf), "max_features": settings.max_features},
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
                        for fname, val in zip(field_names, record_values, strict=False):
                            if isinstance(val, bytes):
                                val = val.decode("utf-8", errors="ignore")
                            props[fname] = val
                    except Exception:
                        pass

                    gtype, is_valid, is_empty, msg = self._classify_geometry(geom)

                    geom_json = None
                    if geom is not None and not is_empty:
                        try:
                            from shapely.geometry import mapping
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
