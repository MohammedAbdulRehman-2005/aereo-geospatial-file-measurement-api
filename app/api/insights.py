"""AI Insights API router: GET /api/files/{id}/insights"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.insight import InsightResponse
from app.services.insight_service import generate_insights_for_file

router = APIRouter(prefix="/api/files", tags=["insights"])


@router.get("/{file_id}/insights/", response_model=InsightResponse)
def get_insights(
    file_id: str, db: Session = Depends(get_db)
) -> InsightResponse:
    """
    AI-generated interpretation of deterministic processing results.

    The AI layer reasons about trusted computed facts only. It cannot alter
    measurements, CRS values, or feature counts. Requires AI_ENABLED=true.
    """
    result = generate_insights_for_file(file_id=file_id, db=db)
    return InsightResponse(
        file_id=file_id,
        summary=result.get("summary", ""),
        key_observations=result.get("key_observations", []),
        quality_flags=result.get("quality_flags", []),
        recommended_actions=result.get("recommended_actions", []),
        disclaimer=result.get("disclaimer", ""),
    )
