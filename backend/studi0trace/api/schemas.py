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


class CandidateScores(BaseModel):
    """How one Auto candidate did on this image. Lower ΔE is closer to the
    source; lower artifact_index is cleaner (0 = no visible defect found)."""
    delta_e: float
    edge_f1: float
    artifact_index: float
    #: no pinhole, sliver, wobbly edge or uneven rectangle
    clean: bool
    #: what is visibly wrong, in a designer's words ("3 pinholes", "wobbly edges")
    issues: list[str]
    shapes: int
    pinholes: int
    slivers: int
    wobble: float
    inflections: int
    uneven_rects: int


class AutoCandidate(BaseModel):
    """One preset Auto traced with: its trace, what it was traced with, and its scores."""
    preset: str
    label: str
    svg: str | None = None
    elapsed_ms: float | None = None
    stats: dict[str, int] | None = None
    parameters: dict[str, Any] | None = None
    scores: CandidateScores | None = None
    error: ErrorBody | None = None


class AutoResult(BaseModel):
    """What Auto tried for one engine and what it chose. The chosen
    candidate's trace is also that engine's entry in `results`."""
    engine: str
    #: the chosen preset id, or None when no candidate could be scored
    pick: str | None
    #: why, in words that finish "Auto chose <label> — …"
    reason: str
    candidates: list[AutoCandidate]


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
    #: per engine, only when the request asked for `auto`
    auto: dict[str, AutoResult] | None = None


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
    # Which Vexel implementation is serving: "rust" or "python". Both produce
    # the same SVG, but the Python one is ten times slower, so a deployment
    # that quietly fell back to it looks healthy and is not.
    vexel: str
