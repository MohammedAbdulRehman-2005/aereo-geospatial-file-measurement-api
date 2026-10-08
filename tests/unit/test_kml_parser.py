"""Unit tests for hardened KML parser adapter using defusedxml."""
import tempfile
from pathlib import Path

import pytest
from shapely.geometry import MultiPolygon, Polygon

from app.core.exceptions import InvalidGeospatialDataError
from app.parsers.kml_parser import KMLParser, _close_ring

SAMPLE_POLYGON_KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>Valid Parcel</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              77.5,12.9,0 77.51,12.9,0 77.51,12.91,0 77.5,12.91,0 77.5,12.9,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>
"""

SAMPLE_HOLE_POLYGON_KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>Parcel With Hole</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              77.5,12.9,0 77.6,12.9,0 77.6,13.0,0 77.5,13.0,0 77.5,12.9,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
        <innerBoundaryIs>
          <LinearRing>
            <coordinates>
              77.52,12.92,0 77.58,12.92,0 77.58,12.98,0 77.52,12.98,0 77.52,12.92,0
            </coordinates>
          </LinearRing>
        </innerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>
"""

SAMPLE_UNCLOSED_RING_KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>Unclosed Ring Parcel</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              77.5,12.9,0 77.51,12.9,0 77.51,12.91,0 77.5,12.91,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>
"""

SAMPLE_MULTIGEOMETRY_KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>MultiPolygon Parcel</name>
      <MultiGeometry>
        <Polygon>
          <outerBoundaryIs>
            <LinearRing>
              <coordinates>
                77.5,12.9,0 77.51,12.9,0 77.51,12.91,0 77.5,12.91,0 77.5,12.9,0
              </coordinates>
            </LinearRing>
          </outerBoundaryIs>
        </Polygon>
        <Polygon>
          <outerBoundaryIs>
            <LinearRing>
              <coordinates>
                77.6,12.9,0 77.61,12.9,0 77.61,12.91,0 77.6,12.91,0 77.6,12.9,0
              </coordinates>
            </LinearRing>
          </outerBoundaryIs>
        </Polygon>
      </MultiGeometry>
    </Placemark>
  </Document>
</kml>
"""

MALICIOUS_XXE_KML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE kml [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>&xxe;</name>
      <Point><coordinates>77.5,12.9,0</coordinates></Point>
    </Placemark>
  </Document>
</kml>
"""

MALICIOUS_BILLION_LAUGHS_KML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE kml [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
]>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>&lol2;</name>
      <Point><coordinates>77.5,12.9,0</coordinates></Point>
    </Placemark>
  </Document>
</kml>
"""


def _write_temp_kml(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(suffix=".kml", delete=False, mode="w", encoding="utf-8")
    f.write(content)
    f.close()
    return Path(f.name)


def test_close_ring_helper():
    """Verify linear ring closure logic."""
    unclosed = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    closed = _close_ring(unclosed)
    assert len(closed) == 4
    assert closed[0] == closed[-1]

    already_closed = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]
    assert _close_ring(already_closed) == already_closed


def test_parse_valid_polygon_kml():
    """Verify parsing valid Polygon KML."""
    path = _write_temp_kml(SAMPLE_POLYGON_KML)
    try:
        parser = KMLParser()
        result = parser.parse(str(path))
        assert len(result.features) == 1
        f = result.features[0]
        assert f.geometry_type == "Polygon"
        assert f.geometry_valid is True
        assert f.geometry is not None
        assert isinstance(f.geometry, Polygon)
    finally:
        path.unlink(missing_ok=True)


def test_parse_polygon_with_inner_boundary():
    """Verify parsing Polygon with inner boundary (hole)."""
    path = _write_temp_kml(SAMPLE_HOLE_POLYGON_KML)
    try:
        parser = KMLParser()
        result = parser.parse(str(path))
        assert len(result.features) == 1
        f = result.features[0]
        assert f.geometry_type == "Polygon"
        assert f.geometry is not None
        assert len(f.geometry.interiors) == 1
    finally:
        path.unlink(missing_ok=True)


def test_parse_unclosed_ring_is_closed():
    """Verify that unclosed linear rings are closed safely without dropping the feature."""
    path = _write_temp_kml(SAMPLE_UNCLOSED_RING_KML)
    try:
        parser = KMLParser()
        result = parser.parse(str(path))
        assert len(result.features) == 1
        f = result.features[0]
        assert f.geometry_type == "Polygon"
        assert f.geometry_valid is True
        assert f.geometry is not None
        coords = list(f.geometry.exterior.coords)
        assert coords[0] == coords[-1]
    finally:
        path.unlink(missing_ok=True)


def test_parse_multigeometry_kml():
    """Verify parsing MultiGeometry containing multiple Polygons."""
    path = _write_temp_kml(SAMPLE_MULTIGEOMETRY_KML)
    try:
        parser = KMLParser()
        result = parser.parse(str(path))
        assert len(result.features) == 1
        f = result.features[0]
        assert f.geometry_type == "MultiPolygon"
        assert isinstance(f.geometry, MultiPolygon)
        assert len(f.geometry.geoms) == 2
    finally:
        path.unlink(missing_ok=True)


def test_malicious_xxe_rejected_by_defusedxml():
    """Verify that defusedxml rejects XML External Entity (XXE) construct."""
    path = _write_temp_kml(MALICIOUS_XXE_KML)
    try:
        parser = KMLParser()
        with pytest.raises(InvalidGeospatialDataError) as exc_info:
            parser.parse(str(path))
        assert "forbidden" in str(exc_info.value).lower()
    finally:
        path.unlink(missing_ok=True)


def test_malicious_billion_laughs_rejected_by_defusedxml():
    """Verify that defusedxml rejects entity expansion bombs."""
    path = _write_temp_kml(MALICIOUS_BILLION_LAUGHS_KML)
    try:
        parser = KMLParser()
        with pytest.raises(InvalidGeospatialDataError) as exc_info:
            parser.parse(str(path))
        assert "forbidden" in str(exc_info.value).lower()
    finally:
        path.unlink(missing_ok=True)
