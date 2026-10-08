"""Pydantic schemas for AI insight endpoint."""
from __future__ import annotations

from pydantic import BaseModel, Field


class QualityFlagSchema(BaseModel):
    severity: str
    reason: str
    feature_ids: list[int] = Field(default_factory=list)


class InsightResponse(BaseModel):
    """Response for GET /api/files/{id}/insights"""

    file_id: str
    summary: str
    key_observations: list[str]
    quality_flags: list[QualityFlagSchema] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    disclaimer: str
