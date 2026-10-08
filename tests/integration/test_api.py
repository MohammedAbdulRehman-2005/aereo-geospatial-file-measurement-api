"""Integration and API endpoint tests (Specification §15.2)."""
import io
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import settings


def test_health_endpoint(client: TestClient):
    """GET /health returns 200 with service status."""
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] in ("healthy", "degraded")
    assert "version" in data


def test_upload_kml_and_retrieve_measurements_and_features(client: TestClient, fixtures_dir: Path):
    """Full lifecycle: upload KML, get file info, get measurements with geometry, get features."""
    kml_path = fixtures_dir / "small_polygon_4326.kml"

    # 1. Upload
    with open(kml_path, "rb") as f:
        res = client.post(
            "/api/files/",
            files={"file": ("small_polygon_4326.kml", f, "application/vnd.google-earth.kml+xml")},
        )
    assert res.status_code == 201
    file_data = res.json()
    file_id = file_data["id"]
    assert file_data["filename"] == "small_polygon_4326.kml"
    assert file_data["format"] == "KML"
    assert file_data["status"] == "COMPLETED"
    assert file_data["feature_count"] == 1
    assert file_data["crs"] == "EPSG:4326"
    assert file_data["measurement_crs"] == "EPSG:32643"

    # 2. Get file info
    info_res = client.get(f"/api/files/{file_id}/")
    assert info_res.status_code == 200
    info_data = info_res.json()
    assert info_data["id"] == file_id
    assert info_data["status"] == "COMPLETED"
    assert info_data["processing_summary"]["polygon_count"] == 1

    # 3. Get measurements (must expose geometry dict)
    meas_res = client.get(f"/api/files/{file_id}/measurements/")
    assert meas_res.status_code == 200
    meas_data = meas_res.json()
    assert meas_data["file_id"] == file_id
    assert meas_data["measurement_crs"] == "EPSG:32643"
    assert len(meas_data["features"]) == 1
    feat = meas_data["features"][0]
    assert feat["geometry_type"] == "Polygon"
    assert feat["measurement_type"] == "area"
    assert feat["value"] is not None
    assert feat["value"] > 0
    assert feat["unit"] == "m2"
    assert feat["source_crs"] == "EPSG:4326"
    assert feat["measurement_crs"] == "EPSG:32643"
    assert feat["method"] == "planar_projected_area"
    assert feat["geometry_repaired"] is False
    assert feat["area"] is not None
    assert feat["area"] > 0
    assert feat["area_unit"] == "m2"
    assert feat["reason"] is None
    # Expose extracted geometry
    assert feat["geometry"] is not None
    assert feat["geometry"]["type"] == "Polygon"
    assert len(feat["geometry"]["coordinates"]) > 0

    # 4. Get features endpoint (paginated features with geometries)
    feats_res = client.get(f"/api/files/{file_id}/features/")
    assert feats_res.status_code == 200
    feats_data = feats_res.json()
    assert feats_data["file_id"] == file_id
    assert feats_data["total_features"] == 1
    assert feats_data["page"] == 1
    assert len(feats_data["features"]) == 1
    f_detail = feats_data["features"][0]
    assert f_detail["geometry_type"] == "Polygon"
    assert f_detail["geometry"] is not None
    assert f_detail["geometry"]["type"] == "Polygon"
    assert f_detail["crs"] == "EPSG:4326"
    assert f_detail["geometry_repaired"] is False

    # 5. Get quality report
    qual_res = client.get(f"/api/files/{file_id}/quality/")
    assert qual_res.status_code == 200
    qual_data = qual_res.json()
    assert qual_data["file_id"] == file_id
    assert qual_data["invalid_features"] == 0


def test_upload_shapefile_zip(client: TestClient, fixtures_dir: Path):
    """Upload valid Shapefile ZIP and verify LineString measurements with geometries."""
    zip_path = fixtures_dir / "sample_roads.zip"

    with open(zip_path, "rb") as f:
        res = client.post(
            "/api/files/",
            files={"file": ("sample_roads.zip", f, "application/zip")},
        )
    assert res.status_code == 201
    file_data = res.json()
    assert file_data["format"] == "SHAPEFILE"
    assert file_data["status"] == "COMPLETED"
    assert file_data["feature_count"] == 2
    file_id = file_data["id"]

    meas_res = client.get(f"/api/files/{file_id}/measurements/")
    assert meas_res.status_code == 200
    features = meas_res.json()["features"]
    assert len(features) == 2
    for f in features:
        assert f["geometry_type"] == "LineString"
        assert f["measurement_type"] == "length"
        assert f["value"] is not None
        assert f["value"] > 0
        assert f["unit"] == "m"
        assert f["method"] == "planar_projected_length"
        assert f["length"] is not None
        assert f["length"] > 0
        assert f["length_unit"] == "m"
        assert f["geometry"] is not None
        assert f["geometry"]["type"] == "LineString"

    # Verify GET /api/files/{id}/features/ returns Shapefile features with geometry
    feats_res = client.get(f"/api/files/{file_id}/features/")
    assert feats_res.status_code == 200
    feats_data = feats_res.json()
    assert feats_data["total_features"] == 2
    for shp_feat in feats_data["features"]:
        assert shp_feat["geometry_type"] == "LineString"
        assert shp_feat["geometry"] is not None
        assert shp_feat["geometry"]["type"] == "LineString"


def test_features_pagination(client: TestClient, fixtures_dir: Path):
    """Verify pagination on GET /api/files/{id}/features/."""
    mixed_path = fixtures_dir / "mixed_geometry.kml"
    with open(mixed_path, "rb") as f:
        res = client.post(
            "/api/files/",
            files={"file": ("mixed.kml", f, "application/vnd.google-earth.kml+xml")},
        )
    file_id = res.json()["id"]

    # Request page 1 with page_size=2
    p1_res = client.get(f"/api/files/{file_id}/features/?page=1&page_size=2")
    assert p1_res.status_code == 200
    p1 = p1_res.json()
    assert p1["total_features"] == 3
    assert len(p1["features"]) == 2
    assert p1["page"] == 1

    # Request page 2 with page_size=2
    p2_res = client.get(f"/api/files/{file_id}/features/?page=2&page_size=2")
    assert p2_res.status_code == 200
    p2 = p2_res.json()
    assert p2["total_features"] == 3
    assert len(p2["features"]) == 1
    assert p2["page"] == 2


def test_kml_doctype_entity_rejected(client: TestClient):
    """Verify KML containing malicious XML DOCTYPE/ENTITY is rejected."""
    bad_kml = b'<?xml version="1.0"?><!DOCTYPE test [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><kml><Document><Placemark><name>&xxe;</name></Placemark></Document></kml>'
    res = client.post(
        "/api/files/",
        files={"file": ("xxe.kml", io.BytesIO(bad_kml), "application/vnd.google-earth.kml+xml")},
    )
    assert res.status_code == 400
    data = res.json()
    assert data["error"]["code"] == "INVALID_GEOSPATIAL_DATA"


def test_file_not_found_returns_404(client: TestClient):
    """GET on non-existent file ID returns 404 with structured error envelope."""
    res = client.get("/api/files/00000000-0000-0000-0000-000000000000/")
    assert res.status_code == 404
    data = res.json()
    assert "error" in data
    assert data["error"]["code"] == "FILE_NOT_FOUND"


def test_unsupported_file_extension_returns_400(client: TestClient):
    """Upload with unsupported extension (.geojson) returns 400."""
    fake_data = io.BytesIO(b'{"type": "FeatureCollection"}')
    res = client.post(
        "/api/files/",
        files={"file": ("data.geojson", fake_data, "application/json")},
    )
    assert res.status_code == 400
    data = res.json()
    assert data["error"]["code"] == "INVALID_FILE_TYPE"


def test_malformed_zip_fails_gracefully(client: TestClient, fixtures_dir: Path):
    """Zip slip attempt returns 400 INVALID_ARCHIVE."""
    malicious_zip = fixtures_dir / "malicious_paths.zip"
    with open(malicious_zip, "rb") as f:
        res = client.post(
            "/api/files/",
            files={"file": ("malicious.zip", f, "application/zip")},
        )
    assert res.status_code == 400
    data = res.json()
    assert data["error"]["code"] == "INVALID_ARCHIVE"


def test_shapefile_missing_components_fails_gracefully(client: TestClient, fixtures_dir: Path):
    """Shapefile ZIP missing required .shx returns 400 INVALID_ARCHIVE."""
    missing_zip = fixtures_dir / "missing_component.zip"
    with open(missing_zip, "rb") as f:
        res = client.post(
            "/api/files/",
            files={"file": ("missing.zip", f, "application/zip")},
        )
    assert res.status_code == 400
    data = res.json()
    assert data["error"]["code"] == "INVALID_ARCHIVE"


def test_ai_insights_disabled_by_default(client: TestClient, fixtures_dir: Path):
    """GET /insights returns 503 AI_DISABLED when AI_ENABLED=false."""
    settings.ai_enabled = False
    kml_path = fixtures_dir / "small_polygon_4326.kml"

    with open(kml_path, "rb") as f:
        up = client.post(
            "/api/files/",
            files={"file": ("small_polygon_4326.kml", f, "application/vnd.google-earth.kml+xml")},
        )
    file_id = up.json()["id"]

    res = client.get(f"/api/files/{file_id}/insights/")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "AI_DISABLED"


def test_ai_insights_enabled_with_mock_provider(client: TestClient, fixtures_dir: Path):
    """GET /insights returns valid insights when AI is enabled with mock provider."""
    settings.ai_enabled = True
    settings.ai_provider = "mock"

    kml_path = fixtures_dir / "small_polygon_4326.kml"
    with open(kml_path, "rb") as f:
        up = client.post(
            "/api/files/",
            files={"file": ("small_polygon_4326.kml", f, "application/vnd.google-earth.kml+xml")},
        )
    file_id = up.json()["id"]

    res = client.get(f"/api/files/{file_id}/insights/")
    assert res.status_code == 200
    data = res.json()
    assert data["file_id"] == file_id
    assert data["summary"] != ""
    assert len(data["key_observations"]) > 0
    assert "deterministic" in data["disclaimer"].lower()

    # Reset
    settings.ai_enabled = False
    settings.ai_provider = "none"


def test_feature_limit_exceeded_via_api(client: TestClient, fixtures_dir: Path, monkeypatch):
    """Uploading a file exceeding MAX_FEATURES returns 400 FEATURE_LIMIT_EXCEEDED."""
    monkeypatch.setattr(settings, "max_features", 1)
    mixed_path = fixtures_dir / "mixed_geometry.kml"  # contains 3 features
    with open(mixed_path, "rb") as f:
        res = client.post(
            "/api/files/",
            files={"file": ("mixed.kml", f, "application/vnd.google-earth.kml+xml")},
        )
    assert res.status_code == 400
    data = res.json()
    assert data["error"]["code"] == "FEATURE_LIMIT_EXCEEDED"


def test_shapefile_invalid_prj_returns_400(client: TestClient, fixtures_dir: Path):
    """Shapefile with malformed, unparseable .prj returns 400 INVALID_GEOSPATIAL_DATA."""
    import io
    import zipfile

    # Build an in-memory zip copying valid shapefile components but replacing .prj with junk
    valid_zip_path = fixtures_dir / "sample_roads.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(valid_zip_path, "r") as src_zip:
        with zipfile.ZipFile(buf, "w") as dst_zip:
            for item in src_zip.infolist():
                if item.filename.endswith(".prj"):
                    dst_zip.writestr(item.filename, "NOT_A_VALID_WKT_DEFINITION_CORRUPT")
                else:
                    dst_zip.writestr(item, src_zip.read(item.filename))
    buf.seek(0)

    res = client.post(
        "/api/files/",
        files={"file": ("corrupt_prj.zip", buf, "application/zip")},
    )
    assert res.status_code == 400
    data = res.json()
    assert data["error"]["code"] == "INVALID_GEOSPATIAL_DATA"
    assert "prj" in data["error"]["message"].lower()
