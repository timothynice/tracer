"""FastAPI application factory.

    uvicorn studi0trace.main:app --reload
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from studi0trace import __version__
from studi0trace.api.routes import router
from studi0trace.engines import registry
from studi0trace.engines.vexel.engine import backend as vexel_backend
from studi0trace.imaging.cache import UploadCache
from studi0trace.settings import Settings, get_settings

log = logging.getLogger("studi0trace")


class ErrorBoundary:
    """Turn unhandled exceptions into a JSON 500 *inside* the CORS layer.

    Starlette's own `Exception` handler lives in the outermost
    ServerErrorMiddleware, so its responses never pass through CORSMiddleware
    and browsers report a CORS failure instead of the real error. Catching
    one layer further in fixes that without duplicating header logic.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def guarded_send(message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, guarded_send)
        except Exception:  # noqa: BLE001 - this is the boundary
            log.exception("unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if response_started:
                raise
            response = JSONResponse(
                status_code=500,
                content={"detail": {"code": "internal_error", "message": "Internal server error"}},
            )
            await response(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    registry.load_builtin()

    # Resolve the Vexel backend once, at startup. With VEXEL_BACKEND=rust set —
    # which is what the deployment does — this raises if the extension did not
    # make it into the image, so the container fails to come up instead of
    # serving every trace ten times slower and looking healthy while it does.
    log.info("vexel backend: %s", vexel_backend())

    app = FastAPI(title="Studi0Trace API", version=__version__)
    app.state.settings = settings
    app.state.uploads = UploadCache(settings.max_upload_cache_bytes, settings.upload_ttl_seconds)

    # add_middleware() inserts at the outside: ErrorBoundary is added first so it
    # ends up *inside* CORSMiddleware.
    app.add_middleware(ErrorBoundary)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("studi0trace.main:app", host="0.0.0.0", port=8000, reload=True)
