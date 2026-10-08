"""
Aereo Geospatial File Measurement API — FastAPI application entry point.

Responsibilities of main.py:
- Configure logging
- Create the FastAPI app with metadata
- Register routers
- Register global exception handlers
- Provide /health endpoint

Business logic must NOT live here. See app/services/ for processing logic.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import files, insights, measurements
from app.core.config import settings
from app.core.exceptions import GeospatialAPIError
from app.core.logging import configure_logging, get_logger

configure_logging(settings.log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Aereo Geospatial API starting up", extra={"env": settings.app_env})
    yield
    logger.info("Aereo Geospatial API shutting down")


app = FastAPI(
    title="Aereo Geospatial File Measurement API",
    description=(
        "Upload KML or zipped Shapefiles, extract features, compute CRS-aware "
        "measurements (area, length), and retrieve structured results with "
        "optional AI-generated interpretations."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# --- Routers ---
app.include_router(files.router)
app.include_router(measurements.router)
app.include_router(insights.router)


# --- Exception Handlers ---

@app.exception_handler(GeospatialAPIError)
async def domain_exception_handler(
    request: Request, exc: GeospatialAPIError
) -> JSONResponse:
    """Map domain exceptions to structured JSON error responses."""
    logger.warning(
        "Domain error",
        extra={
            "error_code": exc.error_code,
            "error_detail": exc.message,
            "path": request.url.path,
        },
    )
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return a structured error for Pydantic validation failures."""
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed.",
                "details": {"errors": exc.errors()},
            }
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all: return 500 without leaking internal details."""
    logger.exception(
        "Unhandled exception",
        extra={"path": request.url.path, "error": str(exc)},
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "PROCESSING_ERROR",
                "message": "An internal server error occurred.",
            }
        },
    )


# --- Health Endpoint ---

@app.get("/health", tags=["observability"])
def health() -> dict[str, Any]:
    """Liveness/readiness health check."""
    from app.db.session import check_db_connection

    db_ok = check_db_connection()
    return {
        "status": "healthy" if db_ok else "degraded",
        "database": "connected" if db_ok else "unreachable",
        "version": "1.0.0",
    }


# --- Request correlation ID middleware ---

@app.middleware("http")
async def add_correlation_id(request: Request, call_next):
    """Attach a unique correlation ID to every request for traceability."""
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.correlation_id = correlation_id
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    return response
