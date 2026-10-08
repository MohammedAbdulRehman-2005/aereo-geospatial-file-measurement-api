"""AI output Pydantic schemas for validation."""
from __future__ import annotations

from pydantic import BaseModel, Field


class QualityFlagAI(BaseModel):
    severity: str = Field(pattern="^(LOW|MEDIUM|HIGH)$")
    reason: str
    feature_ids: list[int] = Field(default_factory=list)


class InsightOutput(BaseModel):
    """Schema-validated AI output. Ensures AI cannot alter authoritative values."""

    summary: str
    key_observations: list[str]
    quality_flags: list[QualityFlagAI] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "Insights are generated from deterministic processing results. "
        "No geospatial calculations were performed by the AI."
    )
