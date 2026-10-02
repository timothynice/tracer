"""The Docker image builds vexel-rs from `backend/vexel-rs/Cargo.lock`; the workspace builds it from the root one.

`backend/vexel-rs` is a member of the repo's Cargo workspace, and inside it cargo reads and writes the ROOT
`Cargo.lock` and never touches `backend/vexel-rs/Cargo.lock`. That file is read by exactly one thing: the Docker
build, whose context is `backend/` alone (`backend/Dockerfile`, `maturin build --locked`). Nothing else notices if
it falls behind, so this does: the Docker lock must hold the packages the root lock resolves for vexel-rs, at the
same versions and checksums (`tools.sync_vexel_lock.differences` says exactly what is compared, and why a plain
comparison of the closure of vexel-rs in the root lock with the Docker lock is one package off today: the root
lock lists the dependencies the whole workspace's features activate). The release profile the image carries in
environment variables, because its context has no root manifest, must be the root manifest's.

No network, no cargo: both are read as TOML.
"""
from __future__ import annotations

import pathlib
import re
import tomllib

from tools import sync_vexel_lock as locks

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_the_docker_lockfile_is_the_vexel_rs_closure_of_the_workspace_lockfile():
    diff = locks.differences()
    assert not diff, (
        f"backend/vexel-rs/Cargo.lock is not the vexel-rs closure of the root Cargo.lock:\n  "
        + "\n  ".join(diff)
        + f"\n\n{locks.REFRESH}"
    )


def test_the_closure_reaches_the_engines_dependencies_and_the_docker_lockfile_is_all_of_it():
    # the check above would pass if both sides were empty, or the walk lost its edges
    docker = locks.lock_packages(locks.DOCKER_LOCK)
    assert locks.closure(locks.DOCKER_LOCK) .keys() == docker.keys(), "an orphan package in the Docker lockfile"
    names = {name for name, _ in docker}
    assert {"vexel-rs", "rayon", "tiny-skia", "pyo3"} <= names, sorted(names)
    assert ("tiny-skia", "0.11.4") in docker and not any(n == "tiny-skia" and v.startswith("0.12") for n, v in docker), \
        "vexel-rs refines with tiny-skia 0.11; 0.12 is resvg's (the core's), and not in the engine's closure"
    assert len(docker) > 20
    # the workspace holds more than the engine: the core, and what only it needs
    root = locks.lock_packages(locks.ROOT_LOCK)
    assert "studi0trace-core" in {n for n, _ in root} and "studi0trace-core" not in names
    assert docker.keys() < root.keys()
    # the walk over the workspace's lock reaches the Docker lock's packages, plus what the whole workspace's
    # feature unification adds to a shared package (float-cmp, through strict-num: the docstring of differences())
    walked = locks.closure(locks.ROOT_LOCK)
    assert docker.keys() <= walked.keys() and len(walked) - len(docker) <= 3, sorted(walked.keys() - docker.keys())


def test_the_check_sees_a_stale_version_a_changed_checksum_a_new_dependency_and_an_orphan(tmp_path):
    text = locks.DOCKER_LOCK.read_text(encoding="utf-8")
    assert locks.differences(locks.ROOT_LOCK, locks.DOCKER_LOCK) == [], "the real files are the first test's"

    def with_lock(body: str) -> list[str]:
        docker = tmp_path / "Cargo.lock"
        docker.write_text(body, encoding="utf-8")
        return locks.differences(locks.ROOT_LOCK, docker)

    # a dependency bumped in the workspace (the Docker file still holds the old version)
    lines = with_lock(text.replace('name = "arrayref"\nversion = "0.3.9"', 'name = "arrayref"\nversion = "0.3.8"'))
    assert any(l.startswith("stale") and "arrayref 0.3.8" in l and "0.3.9" in l for l in lines), lines
    # the same version, other bytes
    checksum = re.search(r'name = "adler2"\nversion = "[^"]+"\nsource = "[^"]+"\nchecksum = "([0-9a-f]+)"', text)
    assert checksum, "adler2 is no longer in the lockfile; pick another package"
    lines = with_lock(text.replace(checksum.group(1), "0" * 64))
    assert any("adler2" in l and "checksum" in l for l in lines), lines
    # a dependency the engine's manifest gained
    engine = re.search(r'name = "vexel-rs"\nversion = "[^"]+"\ndependencies = \[\n( "[^"]+",\n)+\]', text)
    assert engine, "vexel-rs is no longer listed with dependencies"
    lines = with_lock(text.replace(engine.group(0), engine.group(0).replace(' "rayon",\n', "")))
    assert any("vexel-rs's own dependencies differ" in l and "rayon" in l for l in lines), lines
    # an extra package nothing depends on (cargo would prune it, and --locked would refuse to)
    lines = with_lock(text.rstrip("\n") + '\n\n[[package]]\nname = "left-over"\nversion = "1.0.0"\n')
    assert any(l.startswith("stale") and "left-over" in l for l in lines), lines


def test_the_dockerfile_carries_the_release_profile_of_the_root_manifest():
    profile = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))["profile"]["release"]
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    env = dict(re.findall(r"(CARGO_PROFILE_RELEASE_[A-Z_]+)=(\S+?)(?:\s|\\|$)", dockerfile))
    # the image has no root manifest, so what the manifest sets beyond cargo's own release defaults
    # (opt-level 3) is carried as environment variables, and must say the same
    assert profile.get("opt-level", 3) == 3
    assert env.get("CARGO_PROFILE_RELEASE_LTO") == str(profile["lto"]), (env, profile)
    assert env.get("CARGO_PROFILE_RELEASE_CODEGEN_UNITS") == str(profile["codegen-units"]), (env, profile)
    assert set(profile) <= {"opt-level", "lto", "codegen-units"}, f"{set(profile)}: a setting the Dockerfile does not carry"
    assert re.search(r"maturin build[^\n]*--locked", dockerfile), "the image must build vexel-rs with --locked, or a stale lockfile ships untested versions"
