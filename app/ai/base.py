"""AI provider interface and schemas."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseAIProvider(ABC):
    """Abstract AI provider interface. All providers must implement this."""

    @abstractmethod
    def generate_insights(self, context: dict[str, Any]) -> dict[str, Any]:
        """Generate structured insights from trusted geospatial context."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Canonical provider name for recording."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier for recording."""
        ...
