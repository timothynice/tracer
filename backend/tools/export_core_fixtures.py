"""Golden fixtures for crates/studi0trace-core, exported from the Python reference.

    .venv/bin/python -m tools.export_core_fixtures            # everything
    .venv/bin/python -m tools.export_core_fixtures --only schema,presets

Each exporter writes one JSON file into the crate's tests/fixtures. The Rust
tests compare against them, so the Python stays the definition of what the
core does until the Python server is retired.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "crates" / "studi0trace-core" / "tests" / "fixtures"
EXPORTERS: dict[str, Callable[[], None]] = {}


def exporter(name: str):
    def wrap(fn):
        EXPORTERS[name] = fn
        return fn
    return wrap


def write(name: str, data) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


@exporter("schema")
def _schema() -> None:
    from studi0trace.engines.vexel.engine import VexelParams

    # `write` sorts keys, so the order the UI lays its controls out in (the order the
    # fields are declared, which Pydantic keeps in `properties`) is carried explicitly.
    write("schema", {
        "schema": VexelParams.model_json_schema(),
        "defaults": VexelParams().model_dump(),
        "order": list(VexelParams.model_fields),
    })


@exporter("presets")
def _presets() -> None:
    from studi0trace.engines.presets import all_presets

    # `GET /presets` as the API sends it, `detail` lines and all.
    write("presets", [p.model_dump() for p in all_presets()])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only = {s for s in args.only.split(",") if s}
    unknown = sorted(only - EXPORTERS.keys())
    if unknown:
        sys.exit(f"unknown exporter {', '.join(unknown)}; known: {', '.join(sorted(EXPORTERS))}")
    for name, fn in EXPORTERS.items():
        if not only or name in only:
            fn()
            print("wrote", name)


if __name__ == "__main__":
    main()
