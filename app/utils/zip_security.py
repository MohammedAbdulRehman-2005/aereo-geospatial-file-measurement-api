"""ZIP archive security utilities.

Prevents:
- Zip Slip / path traversal attacks
- Decompression bombs (unlimited size)
- Nested archives (unless explicitly supported)
- Files with unsafe extensions
"""
from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath

from app.core.config import settings
from app.core.exceptions import InvalidArchiveError

# Extensions allowed as Shapefile components
SHAPEFILE_EXTENSIONS = frozenset({
    ".shp", ".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx", ".fbn",
    ".fbx", ".ain", ".aih", ".ixs", ".mxs", ".atx", ".xml", ".qmd",
})

# Extensions that are never allowed inside a ZIP we process
FORBIDDEN_EXTENSIONS = frozenset({
    ".exe", ".dll", ".so", ".dylib", ".bat", ".cmd", ".sh",
    ".py", ".rb", ".js", ".php", ".zip", ".tar", ".gz", ".7z",
})


def _is_safe_member(member_name: str, extract_root: Path) -> bool:
    """Return True if the member path does not escape the extraction root."""
    # Normalise to POSIX, strip leading slashes and drive letters
    safe_name = PurePosixPath(member_name).as_posix().lstrip("/")
    target = (extract_root / safe_name).resolve()
    return target.is_relative_to(extract_root.resolve())


def validate_and_extract_zip(
    zip_path: Path,
    extract_dir: Path,
) -> list[Path]:
    """
    Validate and extract a ZIP archive safely.

    Returns a list of extracted file paths.
    Raises InvalidArchiveError on any security violation or limit breach.
    """
    if not zipfile.is_zipfile(zip_path):
        raise InvalidArchiveError(
            "The uploaded file is not a valid ZIP archive.",
            details={"filename": zip_path.name},
        )

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = zf.infolist()

            if not members:
                raise InvalidArchiveError("The ZIP archive is empty.")

            # Check for path traversal and forbidden content
            total_uncompressed = 0
            for member in members:
                name = member.filename

                # Reject directory-only entries
                if member.is_dir():
                    continue

                # Path traversal check
                if not _is_safe_member(name, extract_dir):
                    raise InvalidArchiveError(
                        "ZIP archive contains unsafe path (path traversal attempt).",
                        details={"member": name},
                    )

                # Forbidden extension check (nested archives etc.)
                suffix = Path(name).suffix.lower()
                if suffix in FORBIDDEN_EXTENSIONS:
                    raise InvalidArchiveError(
                        f"ZIP archive contains disallowed file type: {suffix}",
                        details={"member": name, "extension": suffix},
                    )

                total_uncompressed += member.file_size

            CHUNK_SIZE = 64 * 1024  # 64 KB streaming chunks

            # Decompression bomb guard on declared header size
            if total_uncompressed > settings.max_extracted_bytes:
                raise InvalidArchiveError(
                    f"ZIP archive uncompressed size "
                    f"({total_uncompressed // (1024*1024)} MB) exceeds limit "
                    f"({settings.max_extracted_mb} MB).",
                    details={"max_bytes": settings.max_extracted_bytes},
                )

            # Extract safely with streaming chunks and live byte accounting
            extract_dir.mkdir(parents=True, exist_ok=True)
            cumulative_extracted = 0

            for member in members:
                if member.is_dir():
                    continue
                safe_name = PurePosixPath(member.filename).as_posix().lstrip("/")
                target_path = extract_dir / safe_name
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target_path, "wb") as dst:
                    while chunk := src.read(CHUNK_SIZE):
                        cumulative_extracted += len(chunk)
                        if cumulative_extracted > settings.max_extracted_bytes:
                            raise InvalidArchiveError(
                                f"ZIP archive decompressed size exceeded limit ({settings.max_extracted_mb} MB).",
                                details={"max_bytes": settings.max_extracted_bytes},
                            )
                        dst.write(chunk)

    except zipfile.BadZipFile as exc:
        raise InvalidArchiveError(
            "The ZIP archive is corrupted or malformed.",
            details={"error": str(exc)},
        ) from exc

    return sorted(extract_dir.rglob("*.*"))


def find_shapefile_components(extracted_files: list[Path]) -> dict[str, Path]:
    """
    Locate the primary Shapefile component set within extracted files.

    Returns a dict mapping lowercase extension → Path.
    Raises InvalidArchiveError if required components (.shp, .shx, .dbf) are missing.
    """
    shp_files = [f for f in extracted_files if f.suffix.lower() == ".shp"]
    if not shp_files:
        raise InvalidArchiveError(
            "The uploaded ZIP does not contain a usable Shapefile dataset.",
            details={"missing_components": [".shp"]},
        )
    if len(shp_files) > 1:
        # Use the first one found; log a warning in callers
        shp_files = shp_files[:1]

    shp = shp_files[0]
    stem = shp.stem
    parent = shp.parent

    components: dict[str, Path] = {".shp": shp}
    required = [".shx", ".dbf"]
    missing: list[str] = []
    for ext in required:
        candidate = parent / (stem + ext)
        if candidate.exists():
            components[ext] = candidate
        else:
            missing.append(stem + ext)

    if missing:
        raise InvalidArchiveError(
            "The uploaded ZIP does not contain a usable Shapefile dataset.",
            details={"missing_components": missing},
        )

    # Optional but common companions
    for ext in (".prj", ".cpg"):
        candidate = parent / (stem + ext)
        if candidate.exists():
            components[ext] = candidate

    return components
