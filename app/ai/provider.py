"""AI provider implementations: Mock, OpenAI, Gemini."""
from __future__ import annotations

import json
from typing import Any

from app.ai.base import BaseAIProvider
from app.ai.prompts import ANALYSIS_PROMPT_TEMPLATE, SYSTEM_PROMPT
from app.core.config import settings
from app.core.exceptions import AIProviderUnavailableError
from app.core.logging import get_logger

logger = get_logger(__name__)


class MockProvider(BaseAIProvider):
    """
    Deterministic mock provider for local development and testing.
    Returns a valid InsightOutput without any external API call.
    """

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def model_name(self) -> str:
        return "mock-v1"

    def generate_insights(self, context: dict[str, Any]) -> dict[str, Any]:
        feature_count = context.get("file", {}).get("feature_count", 0)
        quality = context.get("quality", {})
        invalid = quality.get("invalid_features", 0)
        geom_counts = context.get("geometry_counts", {})
        types_str = ", ".join(f"{k}: {v}" for k, v in geom_counts.items()) or "unknown"

        summary = (
            f"Dataset contains {feature_count} feature(s) with geometry types: {types_str}."
        )
        observations = [
            f"Total features: {feature_count}.",
            f"Geometry distribution: {types_str}.",
        ]
        flags = []
        if invalid > 0:
            flags.append({
                "severity": "MEDIUM",
                "reason": f"{invalid} feature(s) have invalid geometry.",
                "feature_ids": [],
            })

        return {
            "summary": summary,
            "key_observations": observations,
            "quality_flags": flags,
            "recommended_actions": ["Review invalid geometries before further analysis."] if invalid else [],
            "disclaimer": (
                "Insights are generated from deterministic processing results. "
                "No geospatial calculations were performed by the AI."
            ),
        }


class GeminiProvider(BaseAIProvider):
    """Google Gemini provider using the google-genai SDK."""

    def __init__(self) -> None:
        try:
            import google.generativeai as genai  # type: ignore[import-untyped]
            genai.configure(api_key=settings.ai_api_key)
            self._genai = genai
            self._model_id = settings.ai_model or "gemini-1.5-flash"
        except ImportError as exc:
            raise AIProviderUnavailableError(
                "google-generativeai package is not installed.",
                details={"hint": "pip install google-generativeai"},
            ) from exc

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model_id

    def generate_insights(self, context: dict[str, Any]) -> dict[str, Any]:
        context_json = json.dumps(context, default=str, indent=2)
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(context_json=context_json)
        try:
            model = self._genai.GenerativeModel(
                self._model_id,
                system_instruction=SYSTEM_PROMPT,
            )
            response = model.generate_content(
                prompt,
                generation_config={"max_output_tokens": settings.ai_max_output_tokens},
            )
            raw_text = response.text.strip()
            # Strip markdown code blocks if present
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                raw_text = raw_text.removeprefix("json")
            return json.loads(raw_text)
        except Exception as exc:
            raise AIProviderUnavailableError(
                f"Gemini API call failed: {exc}",
                details={"model": self._model_id},
            ) from exc


class OpenAIProvider(BaseAIProvider):
    """OpenAI provider using the openai SDK."""

    def __init__(self) -> None:
        try:
            import openai  # type: ignore[import-untyped]
            self._client = openai.OpenAI(api_key=settings.ai_api_key)
            self._model_id = settings.ai_model or "gpt-4o-mini"
        except ImportError as exc:
            raise AIProviderUnavailableError(
                "openai package is not installed.",
                details={"hint": "pip install openai"},
            ) from exc

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._model_id

    def generate_insights(self, context: dict[str, Any]) -> dict[str, Any]:
        context_json = json.dumps(context, default=str, indent=2)
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(context_json=context_json)
        try:
            response = self._client.chat.completions.create(
                model=self._model_id,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=settings.ai_max_output_tokens,
                response_format={"type": "json_object"},
            )
            raw_text = response.choices[0].message.content or "{}"
            return json.loads(raw_text)
        except Exception as exc:
            raise AIProviderUnavailableError(
                f"OpenAI API call failed: {exc}",
                details={"model": self._model_id},
            ) from exc


def get_provider() -> BaseAIProvider:
    """Factory: return the configured AI provider."""
    provider = settings.ai_provider
    if provider == "mock":
        return MockProvider()
    if provider == "gemini":
        return GeminiProvider()
    if provider == "openai":
        return OpenAIProvider()
    # none / disabled — return mock for safety (caller checks ai_enabled)
    return MockProvider()
