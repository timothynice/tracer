from __future__ import annotations

import json
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import ValidationError

from studi0trace import __version__
from studi0trace import auto as auto_pick
from studi0trace.api.schemas import (
    AutoCandidate,
    AutoResult,
    CandidateScores,
    EngineDescription,
    EngineResult,
    ErrorBody,
    HealthResponse,
    UploadResponse,
    VectorizeResponse,
)
from studi0trace.engines import registry
from studi0trace.engines.vexel.engine import backend as vexel_backend
from studi0trace.engines.presets import Preset, all_presets
from studi0trace.engines.base import Engine, EngineError, TraceInput
from studi0trace.imaging.cache import UploadCache
from studi0trace.imaging.intake import IntakeError, load_upload
from studi0trace.settings import Settings

router = APIRouter()


def current_settings(request: Request) -> Settings:
    """The Settings this app was created with (see main.create_app)."""
    return request.app.state.settings


def current_cache(request: Request) -> UploadCache:
    return request.app.state.uploads


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(version=__version__, engines=registry.ids(), vexel=vexel_backend())


@router.get("/engines", response_model=list[EngineDescription])
async def engines() -> list[EngineDescription]:
    return [EngineDescription(**registry.describe(e)) for e in registry.all()]


@router.get("/presets", response_model=list[Preset])
async def presets() -> list[Preset]:
    """Auto first, then the named parameter bundles. Each bundle is a trade-off;
    the defaults are `balanced`. Auto (`kind="auto"`) is requested with
    `/vectorize` `auto=true`, not with its (empty) params."""
    known = set(registry.ids())
    return [p for p in all_presets() if p.engine in known]


def _intake(data: bytes, settings: Settings) -> TraceInput:
    try:
        return load_upload(data, max_bytes=settings.max_upload_bytes, max_pixels=settings.max_image_pixels)
    except IntakeError as exc:
        raise HTTPException(400, {"code": exc.code, "message": exc.message})


@router.post("/uploads", response_model=UploadResponse)
async def upload(
    file: Annotated[UploadFile, File()],
    settings: Settings = Depends(current_settings),
    cache: UploadCache = Depends(current_cache),
) -> UploadResponse:
    """Validate once, keep server-side, and hand back an id for repeated /vectorize calls."""
    image = _intake(await file.read(), settings)
    image_id = cache.put(image)
    return UploadResponse(image_id=image_id, width=image.width, height=image.height, format=image.source_format)


def _select_engines(engines_csv: str) -> list[Engine]:
    wanted = [e.strip() for e in engines_csv.split(",") if e.strip()] if engines_csv else []
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


def _failure(exc: Exception) -> ErrorBody:
    if isinstance(exc, EngineError):
        return ErrorBody(code="engine_failed", message=str(exc))
    return ErrorBody(code="engine_crashed", message=f"{type(exc).__name__}: {exc}")


async def _run_engine(engine: Engine, image: TraceInput, params, out: dict[str, EngineResult]) -> None:
    try:
        result = await anyio.to_thread.run_sync(engine.trace, image, params)
        out[engine.id] = EngineResult(svg=result.svg, elapsed_ms=result.elapsed_ms, stats=result.stats.as_dict())
    except Exception as exc:  # noqa: BLE001 - one engine crashing must not take the others down
        out[engine.id] = EngineResult(error=_failure(exc))


async def _run_auto(engine: Engine, image: TraceInput, out: dict[str, EngineResult],
                    out_auto: dict[str, AutoResult], out_params: dict[str, dict]) -> None:
    """Trace with every Auto candidate at once, score each against the source,
    pick one (studi0trace.auto), and answer with all of them, so that the app
    can show any candidate without tracing again. A candidate that fails is
    reported and left out of the choice; it never fails the request."""
    presets = auto_pick.candidates(engine.id)
    cands = [AutoCandidate(preset=p.id, label=p.label) for p in presets]
    scores: dict[str, dict] = {}
    ref: dict[str, object] = {}
    ref_ready = anyio.Event()

    async def make_reference() -> None:
        try:
            ref["ref"] = await anyio.to_thread.run_sync(auto_pick.reference, image)
        except Exception:  # noqa: BLE001 - scoring is lost, the traces are not
            ref["ref"] = None
        finally:
            ref_ready.set()

    async def run(preset, cand: AutoCandidate) -> None:
        try:
            params = engine.Params.model_validate(preset.params)
            cand.parameters = params.model_dump()
            result = await anyio.to_thread.run_sync(engine.trace, image, params)
        except Exception as exc:  # noqa: BLE001 - one candidate failing must not fail the request
            cand.error = _failure(exc)
            return
        cand.svg, cand.elapsed_ms, cand.stats = result.svg, result.elapsed_ms, result.stats.as_dict()
        await ref_ready.wait()
        if ref.get("ref") is None:
            return
        try:
            scored = await anyio.to_thread.run_sync(auto_pick.assess, result.svg, ref["ref"])
        except Exception:  # noqa: BLE001 - an unscorable trace is still a trace
            return
        scores[preset.id] = scored
        cand.scores = CandidateScores(**auto_pick.summary(scored))

    async with anyio.create_task_group() as tg:
        tg.start_soon(make_reference)
        for preset, cand in zip(presets, cands):
            tg.start_soon(run, preset, cand)

    scored = [auto_pick.Scored(c.preset, s["delta_e_mean"], s["edge_f1"], s["artifact_index"], int(s["elements"]))
              for c in cands if (s := scores.get(c.preset)) is not None]
    pick, reason = auto_pick.choose(scored)
    chosen = next((c for c in cands if pick is not None and c.preset == pick.id), None)
    if chosen is None:
        # Nothing could be scored: fall back to the first candidate that traced, in preference order.
        chosen = next((c for c in cands if c.svg is not None), None)
        reason = "scoring was unavailable, so the first preset that traced" if chosen else "every candidate failed"
    if chosen is not None:
        out[engine.id] = EngineResult(svg=chosen.svg, elapsed_ms=chosen.elapsed_ms, stats=chosen.stats)
        out_params[engine.id] = chosen.parameters or {}
    else:
        first = next((c.error for c in cands if c.error is not None), None)
        out[engine.id] = EngineResult(error=first or ErrorBody(code="engine_failed", message="no Auto candidate traced"))
    out_auto[engine.id] = AutoResult(engine=engine.id, pick=chosen.preset if chosen else None, reason=reason,
                                     candidates=cands)


@router.post("/vectorize", response_model=VectorizeResponse)
async def vectorize(
    file: Annotated[UploadFile | None, File()] = None,
    image_id: Annotated[str, Form()] = "",
    parameters: Annotated[str, Form()] = "{}",
    engines: Annotated[str, Form()] = "",
    auto: Annotated[bool, Form()] = False,
    settings: Settings = Depends(current_settings),
    cache: UploadCache = Depends(current_cache),
) -> VectorizeResponse:
    """Trace one image with each selected engine.

    With `auto=true`, every selected engine that has Auto candidates (Vexel)
    is traced once per candidate preset instead of with `parameters`, and the
    response's `auto` holds every candidate's trace and scores and the one
    chosen, which is also that engine's entry in `results`."""
    selected = _select_engines(engines)
    params = _parse_params(parameters, selected)
    auto_engines = {e.id for e in selected if auto_pick.candidates(e.id)} if auto else set()
    if auto and not auto_engines:
        raise HTTPException(400, {"code": "auto_unavailable",
                                  "message": "Auto has no candidates for the selected engines"})

    if image_id:
        image = cache.get(image_id)
        if image is None:
            raise HTTPException(404, {"code": "image_expired", "message": "Upload expired or unknown; upload it again"})
    elif file is not None:
        image = _intake(await file.read(), settings)
        image_id = cache.put(image)
    else:
        raise HTTPException(400, {"code": "no_image", "message": "Send either `file` or `image_id`"})

    results: dict[str, EngineResult] = {}
    auto_results: dict[str, AutoResult] = {}
    used: dict[str, dict] = {eid: p.model_dump() for eid, p in params.items()}
    async with anyio.create_task_group() as tg:
        for engine in selected:
            if engine.id in auto_engines:
                tg.start_soon(_run_auto, engine, image, results, auto_results, used)
            else:
                tg.start_soon(_run_engine, engine, image, params[engine.id], results)

    return VectorizeResponse(
        image_id=image_id,
        width=image.width,
        height=image.height,
        results=results,
        parameters_used=used,
        auto=auto_results or None,
    )
