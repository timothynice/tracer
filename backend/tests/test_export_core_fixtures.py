"""The fixture exporter's provenance: where it refuses to write, and what it records.

The core's golden fixtures are exact only for macOS on arm64 with the Rust engine and the venv's numerical
libraries (`tools/export_core_fixtures.py`, `crates/studi0trace-core/tests/fixtures/provenance.json`).
"""
from __future__ import annotations

import json
import sys

import pytest

from tools import export_core_fixtures as export

HERE = {
    "python": "3.13.3", "numpy": "2.5.3", "scipy": "1.18.1", "scikit-image": "0.26.0", "Pillow": "12.3.0",
    "resvg-py": "0.5.0", "machine": "arm64", "system": "Darwin", "vexel_backend": "rust",
}


def record(**changes) -> dict:
    return {"environment": {**HERE, **changes}, "commit": "abc", "sources_differ_from_commit": False}


def test_the_machine_the_fixtures_are_exact_for_may_export():
    assert export.refusals(HERE, {"exporters": {}}, {"api"}) == []
    assert export.refusals(HERE, {"exporters": {"intake": record()}}, {"api"}) == []


def test_another_platform_and_the_python_engine_are_refused():
    why = export.refusals({**HERE, "machine": "x86_64", "system": "Linux"}, {"exporters": {}}, {"api"})
    assert len(why) == 1 and "Linux on x86_64" in why[0] and "macOS on arm64" in why[0]
    why = export.refusals({**HERE, "vexel_backend": "python"}, {"exporters": {}}, {"api"})
    assert len(why) == 1 and "'python'" in why[0] and "maturin develop" in why[0]


def test_fixtures_of_another_environment_beside_are_refused_unless_they_are_the_ones_remade():
    older = {"exporters": {"intake": record(numpy="2.4.0"), "api": record(numpy="2.4.0")}}
    # --only api: intake keeps the numpy of the old record, which is not this one
    why = export.refusals(HERE, older, {"api"})
    assert len(why) == 1 and "`intake`" in why[0] and "numpy: 2.4.0 -> 2.5.3" in why[0], why
    # remaking both leaves nothing of the old environment beside
    assert export.refusals(HERE, older, {"api", "intake"}) == []
    # a library that was not installed then and is now is a change too
    why = export.refusals(HERE, {"exporters": {"intake": record(**{"resvg-py": None})}}, {"api"})
    assert why and "resvg-py: None -> 0.5.0" in why[0], why


def run(monkeypatch, tmp_path, env: dict, *argv: str) -> None:
    monkeypatch.setattr(export, "OUT", tmp_path)
    monkeypatch.setattr(export, "PROVENANCE", tmp_path / "provenance.json")
    monkeypatch.setattr(export, "environment", lambda: env)
    monkeypatch.setattr(export, "git_state", lambda: {"commit": "feed", "sources_differ_from_commit": True})
    monkeypatch.setattr(sys, "argv", ["export_core_fixtures", *argv])
    export.main()


def test_a_run_writes_its_record_and_keeps_the_others(monkeypatch, tmp_path):
    run(monkeypatch, tmp_path, HERE, "--only", "presets")
    first = json.loads((tmp_path / "provenance.json").read_text())
    assert list(first["exporters"]) == ["presets"]
    assert first["exporters"]["presets"] == {"environment": HERE, "commit": "feed", "sources_differ_from_commit": True}
    assert (tmp_path / "presets.json").exists()
    run(monkeypatch, tmp_path, HERE, "--only", "schema")
    second = json.loads((tmp_path / "provenance.json").read_text())
    assert sorted(second["exporters"]) == ["presets", "schema"], "--only updates only the entries it writes"
    assert second["exporters"]["presets"] == first["exporters"]["presets"]


def test_a_refused_run_writes_nothing_and_exits_with_the_reasons_and_force_goes_past(monkeypatch, tmp_path, capsys):
    linux = {**HERE, "machine": "x86_64", "system": "Linux"}
    with pytest.raises(SystemExit) as e:
        run(monkeypatch, tmp_path, linux, "--only", "presets")
    assert "refusing to export" in str(e.value) and "Linux on x86_64" in str(e.value)
    assert not (tmp_path / "presets.json").exists() and not (tmp_path / "provenance.json").exists()
    run(monkeypatch, tmp_path, linux, "--only", "presets", "--force")
    assert "forced past" in capsys.readouterr().err
    forced = json.loads((tmp_path / "provenance.json").read_text())
    assert forced["exporters"]["presets"]["environment"]["system"] == "Linux", "what was true is what is recorded"


def test_a_changed_environment_refuses_a_partial_rerun_but_not_a_full_one(monkeypatch, tmp_path):
    run(monkeypatch, tmp_path, HERE, "--only", "presets,schema")
    newer = {**HERE, "numpy": "9.9.9"}
    with pytest.raises(SystemExit) as e:
        run(monkeypatch, tmp_path, newer, "--only", "presets")
    assert "`schema`" in str(e.value) and "numpy: 2.5.3 -> 9.9.9" in str(e.value)
    run(monkeypatch, tmp_path, newer, "--only", "presets,schema")  # both remade: nothing of the old beside
    both = json.loads((tmp_path / "provenance.json").read_text())["exporters"]
    assert {r["environment"]["numpy"] for r in both.values()} == {"9.9.9"}


def test_the_real_environment_is_described():
    env = export.environment()
    assert set(env) == {"python", *export.DISTRIBUTIONS, "machine", "system", "vexel_backend"}
    assert env["numpy"] and env["Pillow"] and env["vexel_backend"] in ("rust", "python")
