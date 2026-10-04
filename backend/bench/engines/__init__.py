"""Engines that live with the bench, not the service: Potrace and VTracer.

Each module registers its engine into `studi0trace.engines.registry` when it is
imported, so importing this package is what puts them in the registry. The app
never imports it (`registry.load_builtin` is Vexel alone, and `GET /engines`
serves one engine); `bench.adapters.load()` and the bench CLI do.

VTracer is the optional `bench` extra (`pip install -e '.[bench]'`); without it
that engine is simply not registered, and the bench says so when it is asked for.
"""
from __future__ import annotations

from bench.engines import potrace  # noqa: F401

try:
    from bench.engines import vtracer  # noqa: F401
except ImportError:  # the `vtracer` wheel is not installed
    pass
