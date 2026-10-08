"""Unit tests for ZIP security and archive validation."""
import io
import tempfile
import zipfile
from pathlib import Path
import pytest

from app.core.exceptions import InvalidArchiveError
from app.utils.zip_security import find_shapefile_components, validate_and_extract_zip


def test_zip_slip_path_traversal_rejected():
    """Verify that archives with '../' path traversal attempts are rejected."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../etc/passwd", "malicious_content")
        zf.writestr("test.shp", "content")

    with tempfile.TemporaryDirectory() as td:
        zip_path = Path(td) / "malicious.zip"
        zip_path.write_bytes(buf.getvalue())
        extract_dir = Path(td) / "extracted"

        with pytest.raises(InvalidArchiveError) as exc_info:
            validate_and_extract_zip(zip_path, extract_dir)

        assert "path traversal" in str(exc_info.value).lower()


def test_zip_forbidden_extensions_rejected():
    """Verify nested archives or executables inside ZIP are rejected."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("nested.zip", "nested archive")
        zf.writestr("test.shp", "content")

    with tempfile.TemporaryDirectory() as td:
        zip_path = Path(td) / "nested.zip"
        zip_path.write_bytes(buf.getvalue())
        extract_dir = Path(td) / "extracted"

        with pytest.raises(InvalidArchiveError) as exc_info:
            validate_and_extract_zip(zip_path, extract_dir)

        assert "disallowed file type" in str(exc_info.value).lower()


def test_empty_zip_rejected():
    """Verify empty archives are rejected."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass

    with tempfile.TemporaryDirectory() as td:
        zip_path = Path(td) / "empty.zip"
        zip_path.write_bytes(buf.getvalue())
        extract_dir = Path(td) / "extracted"

        with pytest.raises(InvalidArchiveError) as exc_info:
            validate_and_extract_zip(zip_path, extract_dir)

        assert "empty" in str(exc_info.value).lower()


def test_shapefile_missing_components_rejected():
    """Verify Shapefile archive missing .shx is rejected with structured error."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        shp = p / "data.shp"
        dbf = p / "data.dbf"
        shp.write_text("shp")
        dbf.write_text("dbf")
        # Missing data.shx

        extracted_files = [shp, dbf]
        with pytest.raises(InvalidArchiveError) as exc_info:
            find_shapefile_components(extracted_files)

        assert "missing_components" in exc_info.value.details
        assert any("shx" in c for c in exc_info.value.details["missing_components"])
