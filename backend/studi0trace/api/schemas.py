"""Response models. Kept separate so the frontend contract is easy to read."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str


class EngineResult(BaseModel):
    svg: str | None = None
    elapsed_ms: float | None = None
    stats: dict[str, int] | None = None
    error: ErrorBody | None = None


class UploadResponse(BaseModel):
    image_id: str
    width: int
    height: int
    format: str


class VectorizeResponse(BaseModel):
    success: bool = True
    image_id: str
    width: int
    height: int
    results: dict[str, EngineResult]
    parameters_used: dict[str, dict[str, Any]]


class EngineDescription(BaseModel):
    id: str
    label: str
    description: str
    #: Whether the app shows this engine. All engines stay callable either way.
    primary: bool = False
    params: dict[str, Any]
    defaults: dict[str, Any]


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    engines: list[str]
