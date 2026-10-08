"""Pydantic response schemas for the Features and Measurements endpoints."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FeatureMeasurementSchema(BaseModel):
    feature_id: int
    geometry_type: str
    measurement_type: str
    area: float | None = None
    area_unit: str | None = None
    length: float | None = None
    length_unit: str | None = None
    measurement: float | None = None
    reason: str | None = None
    geometry: dict[str, Any] | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    geometry_valid: bool = True
    geometry_empty: bool = False
    validation_message: str | None = None


class MeasurementsResponse(BaseModel):
    """Response for GET /api/files/{id}/measurements/"""

    file_id: str
    source_crs: str | None
    measurement_crs: str | None
    features: list[FeatureMeasurementSchema]
    total_features: int


class FeatureDetailSchema(BaseModel):
    """Single feature item returned in GET /api/files/{id}/features"""

    feature_id: int
    geometry_type: str
    geometry: dict[str, Any] | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    geometry_valid: bool = True
    geometry_empty: bool = False
    validation_message: str | None = None


class FeaturesResponse(BaseModel):
    """Response for GET /api/files/{id}/features"""

    file_id: str
    page: int
    page_size: int
    total_features: int
    features: list[FeatureDetailSchema]
