"""Pydantic response schemas for the File endpoints."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProcessingSummary(BaseModel):
    polygon_count: int
    linestring_count: int
    point_count: int
    unsupported_count: int
    invalid_count: int
    empty_count: int


class FileUploadResponse(BaseModel):
    """Response for POST /api/files/"""

    id: str
    filename: str
    format: str
    feature_count: int | None
    crs: str | None = Field(None, description="Source CRS of the uploaded file.")
    measurement_crs: str | None
    status: str
    created_at: datetime
    error_message: str | None = None

    model_config = {"from_attributes": True}


class FileInfoResponse(BaseModel):
    """Response for GET /api/files/{id}/"""

    id: str
    filename: str
    format: str
    feature_count: int | None
    crs: str | None = Field(None, description="Source CRS of the uploaded file.")
    measurement_crs: str | None
    status: str
    processing_summary: ProcessingSummary | None = None
    created_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None

    model_config = {"from_attributes": True}
