import io
import shutil
import xml.etree.ElementTree as ET

import pytest
from PIL import Image
from pydantic import BaseModel, ValidationError

from studi0trace.engines import registry
from studi0trace.engines.base import Engine, TraceInput, TraceResult, finish
from studi0trace.engines.potrace import PotraceEngine, PotraceParams
from studi0trace.engines.vtracer import VTracerEngine, VTracerParams
from studi0trace.imaging.intake import load_upload
from tests.conftest import black_square_on_transparent, encode, make_png, two_colour_image

LIMITS = dict(max_bytes=20 * 1024 * 1024, max_pixels=40_000_000)
needs_potrace = pytest.mark.skipif(shutil.which("potrace") is None, reason="potrace binary not installed")


def inp(data: bytes) -> TraceInput:
    return load_upload(data, **LIMITS)


def root_of(svg: str) -> ET.Element:
    return ET.fromstring(svg.encode())


def assert_well_formed(result: TraceResult, width: int, height: int) -> ET.Element:
    root = root_of(result.svg)
    assert root.tag.endswith("svg")
    assert root.get("viewBox") == f"0 0 {width} {height}"
    assert root.get("width") is None and root.get("height") is None
    assert result.elapsed_ms > 0
    assert result.stats.bytes == len(result.svg.encode())
    return root


# --- registry -----------------------------------------------------------------


class FakeParams(BaseModel):
    wobble: int = 3


class FakeEngine:
    id = "fake"
    label = "Fake"
    description = "test double"
    Params = FakeParams

    def trace(self, image: TraceInput, params: BaseModel) -> TraceResult:
        import time
        return finish('<svg width="1" height="1"><path d="M0 0Z"/></svg>', image, time.perf_counter())


def test_registry_roundtrip():
    registry.register(FakeEngine())
    try:
        assert registry.get("fake").label == "Fake"
        assert "fake" in registry.ids()
        assert any(e.id == "fake" for e in registry.all())
        desc = registry.describe(registry.get("fake"))
        assert desc["defaults"] == {"wobble": 3}
        assert desc["params"]["properties"]["wobble"]["default"] == 3
        assert isinstance(registry.get("fake"), Engine)
    finally:
        registry.unregister("fake")
    with pytest.raises(registry.UnknownEngine):
        registry.get("fake")


def test_builtin_engines_registered():
    registry.load_builtin()
    assert {"potrace", "vtracer"} <= set(registry.ids())


# --- potrace ------------------------------------------------------------------


@needs_potrace
def test_potrace_traces_black_square():
    result = PotraceEngine().trace(inp(black_square_on_transparent(64, 16)), PotraceParams())
    assert_well_formed(result, 64, 64)
    assert result.stats.paths >= 1
    assert result.stats.nodes >= 4


@needs_potrace
def test_potrace_invert_changes_output():
    image = inp(black_square_on_transparent(64, 16))
    plain = PotraceEngine().trace(image, PotraceParams()).svg
    inverted = PotraceEngine().trace(image, PotraceParams(invert=True)).svg
    assert plain != inverted


@needs_potrace
def test_potrace_threshold_controls_ink():
    grey = make_png(32, 32, "RGB", (128, 128, 128))
    high = PotraceEngine().trace(inp(grey), PotraceParams(threshold=250))  # grey < 250 → ink
    low = PotraceEngine().trace(inp(grey), PotraceParams(threshold=10))  # grey > 10 → paper
    assert high.stats.paths >= 1
    assert low.stats.paths == 0


@needs_potrace
def test_potrace_accepts_dict_params():
    result = PotraceEngine().trace(inp(black_square_on_transparent()), {"turdsize": 5, "opticurve": False})
    assert result.stats.paths >= 1


def test_potrace_params_bounds():
    with pytest.raises(ValidationError):
        PotraceParams(alphamax=2.0)
    with pytest.raises(ValidationError):
        PotraceParams(turnpolicy="sideways")
    with pytest.raises(ValidationError):
        PotraceParams(unknown=1)
    assert PotraceParams(threshold="200").threshold == 200  # form values arrive as strings


def test_potrace_schema_carries_ui_hints():
    schema = PotraceParams.model_json_schema()
    assert schema["properties"]["threshold"]["ui"]["control"] == "slider"
    assert schema["properties"]["alphamax"]["maximum"] == 1.3334


# --- vtracer ------------------------------------------------------------------


def test_vtracer_traces_two_colours():
    result = VTracerEngine().trace(inp(two_colour_image(64)), VTracerParams())
    assert_well_formed(result, 64, 64)
    assert result.stats.paths >= 2
    assert result.stats.unique_fills >= 2


def test_vtracer_preserves_transparency():
    import resvg_py

    result = VTracerEngine().trace(inp(black_square_on_transparent(64, 16)), VTracerParams())
    png = resvg_py.svg_to_bytes(svg_string=result.svg, width=64, height=64)
    rendered = Image.open(io.BytesIO(bytes(png))).convert("RGBA")
    assert rendered.getpixel((2, 2))[3] == 0, "background should stay transparent"
    assert rendered.getpixel((32, 32))[3] == 255


def test_vtracer_binary_mode():
    result = VTracerEngine().trace(inp(two_colour_image(64)), VTracerParams(colormode="binary"))
    assert result.stats.unique_fills <= 2


def test_vtracer_params_bounds():
    with pytest.raises(ValidationError):
        VTracerParams(color_precision=9)
    with pytest.raises(ValidationError):
        VTracerParams(length_threshold=1.0)
    with pytest.raises(ValidationError):
        VTracerParams(mode="wiggly")
    assert VTracerParams(corner_threshold="45").corner_threshold == 45


def test_primary_is_optional_for_an_engine():
    """A third-party engine must not have to know about the app's UI.

    `primary` is read off the engine if present. Putting it in the runtime
    checkable Engine protocol would make it mandatory, and an engine that
    never declared it would stop being an Engine.
    """
    registry.register(FakeEngine())
    try:
        assert registry.describe(registry.get("fake"))["primary"] is False
        assert isinstance(registry.get("fake"), Engine)
    finally:
        registry.unregister("fake")
    assert registry.describe(registry.get("vexel"))["primary"] is True
