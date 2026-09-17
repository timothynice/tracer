import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from studi0trace.engines import registry
from studi0trace.engines.base import TraceInput, TraceResult, finish
from studi0trace.main import create_app
from studi0trace.settings import Settings
from tests.conftest import black_square_on_transparent, encode, make_png

ORIGIN = "http://localhost:5173"


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = create_app(Settings(allowed_origins=[ORIGIN], max_upload_bytes=1_000_000, max_image_pixels=4_000_000))
    return TestClient(app, headers={"Origin": ORIGIN}, raise_server_exceptions=False)


def upload(client: TestClient, data: bytes, **form) -> dict:
    return client.post("/vectorize", files={"file": ("x.png", data, "application/octet-stream")}, data=form)


def test_health_lists_engines(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert {"potrace", "vtracer"} <= set(body["engines"])


def test_engines_expose_schema_and_defaults(client):
    body = client.get("/engines").json()
    potrace = next(e for e in body if e["id"] == "potrace")
    assert potrace["params"]["properties"]["threshold"]["ui"]["control"] == "slider"
    assert potrace["defaults"]["threshold"] == 128


def test_vectorize_runs_all_engines_with_legacy_shape(client):
    r = upload(client, black_square_on_transparent())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] and body["width"] == 64
    assert body["original_image"].startswith("data:image/png;base64,")
    for eid in ("potrace", "vtracer"):
        assert body["results"][eid]["svg"].startswith("<")
        assert body["results"][eid]["stats"]["paths"] >= 1
        assert body["vectorized"][eid].lstrip().startswith(("<?xml", "<svg"))
    assert body["parameters_used"]["potrace"]["threshold"] == 128


def test_selected_method_alias_and_engines_list(client):
    assert set(upload(client, make_png(), selected_method="potrace").json()["results"]) == {"potrace"}
    assert set(upload(client, make_png(), engines="vtracer").json()["results"]) == {"vtracer"}


def test_parameters_are_applied(client):
    body = upload(client, make_png(), engines="potrace", parameters=json.dumps({"potrace": {"threshold": 200}})).json()
    assert body["parameters_used"]["potrace"]["threshold"] == 200


def test_bad_parameter_is_422_with_cors(client):
    r = upload(client, make_png(), parameters=json.dumps({"potrace": {"alphamax": 5}}))
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"][:2] == ["potrace", "alphamax"]
    assert r.headers["access-control-allow-origin"] == ORIGIN


def test_malformed_json_is_400(client):
    r = upload(client, make_png(), parameters="{nope")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "bad_parameters"


def test_unknown_engine_is_400(client):
    assert upload(client, make_png(), engines="magic").json()["detail"]["code"] == "unknown_engine"


def test_garbage_upload_is_400_with_cors(client):
    r = upload(client, b"definitely not an image")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "unsupported_format"
    assert r.headers["access-control-allow-origin"] == ORIGIN


def test_oversize_upload_is_400(client):
    r = upload(client, make_png(3000, 3000, "L", 0))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] in {"too_large", "too_many_pixels"}


def test_engine_failure_is_isolated(client):
    class Boom:
        id, label, description = "boom", "Boom", "always fails"

        class Params(BaseModel):
            pass

        def trace(self, image, params):
            raise RuntimeError("kaboom")

    registry.register(Boom())
    try:
        body = upload(client, make_png(), engines="boom,potrace").json()
    finally:
        registry.unregister("boom")
    assert body["results"]["boom"]["error"]["code"] == "engine_crashed"
    assert body["results"]["potrace"]["svg"]
    assert body["vectorized"]["boom"].startswith("Error:")


def test_engines_run_off_the_event_loop_and_in_parallel(client):
    class Sleepy:
        id, label, description = "sleepy", "Sleepy", "sleeps"

        class Params(BaseModel):
            pass

        def trace(self, image: TraceInput, params) -> TraceResult:
            started = time.perf_counter()
            time.sleep(0.4)
            return finish("<svg/>", image, started)

    registry.register(Sleepy())
    try:
        png = make_png()
        started = time.perf_counter()
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(upload, client, png, engines="sleepy") for _ in range(2)]
            assert all(f.result().status_code == 200 for f in futures)
        wall = time.perf_counter() - started
    finally:
        registry.unregister("sleepy")
    assert wall < 0.7, f"two 0.4s traces took {wall:.2f}s - engines are blocking the loop"


def test_unhandled_error_is_json_500_with_cors(client):
    app = client.app

    @app.get("/_explode")
    async def explode():
        raise ValueError("nope")

    r = client.get("/_explode")
    assert r.status_code == 500
    assert r.json()["detail"]["code"] == "internal_error"
    assert r.headers["access-control-allow-origin"] == ORIGIN
