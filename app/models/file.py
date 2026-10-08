"""SQLAlchemy models for the Aereo Geospatial API."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONBType = JSON().with_variant(JSONB, "postgresql")


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class Base(DeclarativeBase):
    """Shared declarative base."""


class FileStatus(StrEnum):
    UPLOADED = "UPLOADED"
    VALIDATING = "VALIDATING"
    PROCESSING = "PROCESSING"
    PARTIAL = "PARTIAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FileFormat(StrEnum):
    KML = "KML"
    SHAPEFILE = "SHAPEFILE"


class GeometryType(StrEnum):
    POINT = "Point"
    MULTIPOINT = "MultiPoint"
    LINESTRING = "LineString"
    MULTILINESTRING = "MultiLineString"
    POLYGON = "Polygon"
    MULTIPOLYGON = "MultiPolygon"
    GEOMETRYCOLLECTION = "GeometryCollection"
    UNKNOWN = "Unknown"


class MeasurementType(StrEnum):
    AREA = "area"
    LENGTH = "length"
    NONE = "none"


class GeoFile(Base):
    """Represents an uploaded geospatial file and its processing state."""

    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    source_crs: Mapped[str | None] = mapped_column(String(64), nullable=True)
    measurement_crs: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=FileStatus.UPLOADED
    )
    feature_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    features: Mapped[list[GeoFeature]] = relationship(
        "GeoFeature", back_populates="file", cascade="all, delete-orphan"
    )
    processing_errors: Mapped[list[ProcessingError]] = relationship(
        "ProcessingError", back_populates="file", cascade="all, delete-orphan"
    )
    insight_reports: Mapped[list[InsightReport]] = relationship(
        "InsightReport", back_populates="file", cascade="all, delete-orphan"
    )


class GeoFeature(Base):
    """A single geospatial feature extracted from a file."""

    __tablename__ = "features"
    __table_args__ = (
        UniqueConstraint("file_id", "feature_index", name="uq_file_feature_idx"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    feature_index: Mapped[int] = mapped_column(Integer, nullable=False)
    geometry_type: Mapped[str] = mapped_column(String(64), nullable=False)
    geometry_valid: Mapped[bool] = mapped_column(default=True)
    geometry_repaired: Mapped[bool] = mapped_column(default=False)
    geometry_empty: Mapped[bool] = mapped_column(default=False)
    validation_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    validation_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    geometry_json: Mapped[dict[str, Any] | None] = mapped_column(JSONBType, nullable=True)
    properties_json: Mapped[dict[str, Any] | None] = mapped_column(JSONBType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    file: Mapped[GeoFile] = relationship("GeoFile", back_populates="features")
    measurement: Mapped[Measurement | None] = relationship(
        "Measurement", back_populates="feature", uselist=False, cascade="all, delete-orphan"
    )


class Measurement(Base):
    """Area or length measurement for a feature with full provenance."""

    __tablename__ = "measurements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    feature_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("features.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    measurement_type: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_crs: Mapped[str | None] = mapped_column(String(64), nullable=True)
    measurement_crs: Mapped[str | None] = mapped_column(String(64), nullable=True)
    method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    geometry_repaired: Mapped[bool] = mapped_column(default=False)
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    feature: Mapped[GeoFeature] = relationship("GeoFeature", back_populates="measurement")


class ProcessingError(Base):
    """Records errors encountered during feature processing."""

    __tablename__ = "processing_errors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    feature_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("features.id", ondelete="SET NULL"), nullable=True
    )
    error_code: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    file: Mapped[GeoFile] = relationship("GeoFile", back_populates="processing_errors")


class InsightReport(Base):
    """Stores AI-generated insight reports."""

    __tablename__ = "insight_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    file: Mapped[GeoFile] = relationship("GeoFile", back_populates="insight_reports")
