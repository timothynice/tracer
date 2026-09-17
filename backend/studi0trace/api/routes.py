from __future__ import annotations

import base64
import json
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import ValidationError

from studi0trace import __version__
from studi0trace.api.schemas import EngineDescription, EngineResult, ErrorBody, HealthResponse, VectorizeResponse
from studi0trace.engines import registry
from studi0trace.engines.base import Engine, EngineError, TraceInput
from studi0trace.imaging.intake import IntakeError, load_upload
from studi0trace.settings import Settings

router = APIRouter()


def current_settings(request: Request) -> Settings:
    """The Settings this app was created with (see main.create_app)."""
    return request.app.state.settings


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(version=__version__, engines=registry.ids())


@router.get("/engines", response_model=list[EngineDescription])
async def engines() -> list[EngineDescription]:
    return [EngineDescription(**registry.describe(e)) for e in registry.all()]


def _select_engines(engines_csv: str, selected_method: str) -> list[Engine]:
    wanted = [e.strip() for e in engines_csv.split(",") if e.strip()] if engines_csv else []
    if not wanted and selected_method:
        wanted = [selected_method.strip()]
    if not wanted:
        return registry.all()
    try:
        return [registry.get(w) for w in wanted]
    except registry.UnknownEngine as exc:
        raise HTTPException(400, {"code": "unknown_engine", "message": f"Unknown engine: {exc.args[0]}"})


def _parse_params(raw: str, selected: list[Engine]) -> dict[str, object]:
    try:
        all_params = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(400, {"code": "bad_parameters", "message": f"parameters is not valid JSON: {exc.msg}"})
    if not isinstance(all_params, dict):
        raise HTTPException(400, {"code": "bad_parameters", "message": "parameters must be a JSON object"})

    validated: dict[str, object] = {}
    errors: list[dict] = []
    for engine in selected:
        try:
            validated[engine.id] = engine.Params.model_validate(all_params.get(engine.id) or {})
        except ValidationError as exc:
            errors.extend({**e, "loc": [engine.id, *e["loc"]]} for e in exc.errors(include_url=False))
    if errors:
        raise HTTPException(422, errors)
    return validated


async def _run_engine(engine: Engine, image: TraceInput, params, out: dict[str, EngineResult]) -> None:
    try:
        result = await anyio.to_thread.run_sync(engine.trace, image, params)
        out[engine.id] = EngineResult(svg=result.svg, elapsed_ms=result.elapsed_ms, stats=result.stats.as_dict())
    except EngineError as exc:
        out[engine.id] = EngineResult(error=ErrorBody(code="engine_failed", message=str(exc)))
    except Exception as exc:  # noqa: BLE001 - one engine crashing must not take the others down
        out[engine.id] = EngineResult(error=ErrorBody(code="engine_crashed", message=f"{type(exc).__name__}: {exc}"))


@router.post("/vectorize", response_model=VectorizeResponse)
async def vectorize(
    file: Annotated[UploadFile, File()],
    parameters: Annotated[str, Form()] = "{}",
    engines: Annotated[str, Form()] = "",
    selected_method: Annotated[str, Form()] = "",
    settings: Settings = Depends(current_settings),
) -> VectorizeResponse:
    selected = _select_engines(engines, selected_method)
    params = _parse_params(parameters, selected)

    data = await file.read()
    try:
        image = load_upload(data, max_bytes=settings.max_upload_bytes, max_pixels=settings.max_image_pixels)
    except IntakeError as exc:
        raise HTTPException(400, {"code": exc.code, "message": exc.message})

    results: dict[str, EngineResult] = {}
    async with anyio.create_task_group() as tg:
        for engine in selected:
            tg.start_soon(_run_engine, engine, image, params[engine.id], results)

    mime = f"image/{image.source_format.lower()}"
    return VectorizeResponse(
        original_image=f"data:{mime};base64,{base64.b64encode(data).decode()}",
        width=image.width,
        height=image.height,
        results=results,
        vectorized={
            eid: (r.svg if r.svg is not None else f"Error: {r.error.message if r.error else 'unknown'}")
            for eid, r in results.items()
        },
        parameters_used={eid: p.model_dump() for eid, p in params.items()},
    )
