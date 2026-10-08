"""Unique ID generation using UUID4."""
from __future__ import annotations

import uuid


def new_id() -> str:
    """Generate a URL-safe unique identifier."""
    return str(uuid.uuid4())
