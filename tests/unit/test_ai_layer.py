"""Unit tests for AI Insight layer and schema validation (Specification §11)."""
import pytest
from pydantic import ValidationError

from app.ai.provider import MockProvider
from app.ai.schemas import InsightOutput


def test_mock_provider_output_conforms_to_schema():
    """Verify MockProvider produces output that validates against InsightOutput schema."""
    provider = MockProvider()
    context = {
        "file": {"format": "KML", "feature_count": 5},
        "geometry_counts": {"Polygon": 3, "LineString": 2},
        "measurement_statistics": {
            "total_polygon_area_m2": 15000.0,
            "total_linestring_length_m": 800.0,
        },
        "quality": {"invalid_features": 0, "unsupported_features": 0},
    }
    raw = provider.generate_insights(context)
    validated = InsightOutput.model_validate(raw)

    assert validated.summary != ""
    assert len(validated.key_observations) > 0
    assert "deterministic" in validated.disclaimer.lower()


def test_schema_rejects_invalid_severity():
    """Verify Pydantic schema rejects invalid severity values."""
    bad_data = {
        "summary": "Test summary",
        "key_observations": ["Obs 1"],
        "quality_flags": [{"severity": "CRITICAL_DANGER", "reason": "bad", "feature_ids": []}],
        "recommended_actions": [],
        "disclaimer": "Disclaimer",
    }
    with pytest.raises(ValidationError):
        InsightOutput.model_validate(bad_data)
