"""Module-level engine registry. Engines register themselves at import time."""
from __future__ import annotations

from studi0trace.engines.base import Engine

_ENGINES: dict[str, Engine] = {}


class UnknownEngine(KeyError):
    pass


def register(engine: Engine) -> Engine:
    _ENGINES[engine.id] = engine
    return engine


def unregister(engine_id: str) -> None:
    _ENGINES.pop(engine_id, None)


def get(engine_id: str) -> Engine:
    try:
        return _ENGINES[engine_id]
    except KeyError:
        raise UnknownEngine(engine_id) from None


def all() -> list[Engine]:  # noqa: A001 - mirrors the API's GET /engines
    return list(_ENGINES.values())


def ids() -> list[str]:
    return list(_ENGINES.keys())


def describe(engine: Engine) -> dict:
    """The public description of an engine: schema + defaults drive the UI."""
    return {
        "id": engine.id,
        "label": engine.label,
        "description": engine.description,
        "primary": getattr(engine, "primary", False),
        "params": engine.Params.model_json_schema(),
        "defaults": engine.Params().model_dump(),
    }


def load_builtin() -> None:
    """Import the built-in engines so they register. Safe to call repeatedly."""
    # Registration order is the order GET /engines lists them; the UI defaults to the first.
    from studi0trace.engines.vexel import engine as vexel  # noqa: F401
    from studi0trace.engines import potrace, vtracer  # noqa: F401
