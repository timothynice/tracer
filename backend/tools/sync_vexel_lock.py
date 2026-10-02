"""The two lockfiles of vexel-rs, and keeping them one.

`backend/vexel-rs` is a member of the repo's Cargo workspace, and inside it cargo reads and writes the ROOT
`Cargo.lock`; `backend/vexel-rs/Cargo.lock` is never touched by cargo there. It exists for one reader: the Docker
build (`backend/Dockerfile`), whose context is `backend/` alone, so the image builds vexel-rs on its own and
`maturin build --locked` takes its dependency versions from that file. If it falls behind the root one, the image
ships dependency versions nobody tested.

    .venv/bin/python -m tools.sync_vexel_lock --check    # exits 1, and says what differs, if they disagree
    .venv/bin/python -m tools.sync_vexel_lock            # rewrites backend/vexel-rs/Cargo.lock from the root one

Both are checked by `tests/test_vexel_lock_matches_workspace.py`, which uses `differences()` below (what it compares,
and why it is not simply "the vexel-rs closure of the root lock", is in its docstring). The refresh
lets cargo itself do the work: it copies the crate out of the workspace with the root lock beside it, and cargo
prunes the lock to what the crate needs (and writes `"png"` where the workspace needs `"png 0.17.16"` to tell two
versions apart), so the file is what `cargo` would have written for the crate on its own. It needs `cargo` and the
crates in its registry cache (they are, after any build of the workspace); it never needs the network.
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[2]
ROOT_LOCK = ROOT / "Cargo.lock"
DOCKER_LOCK = ROOT / "backend" / "vexel-rs" / "Cargo.lock"
CRATE = "vexel-rs"

# (name, version) -> (source, checksum): what makes a build of a package the same build
Packages = dict[tuple[str, str], tuple[str | None, str | None]]


def _read(path: pathlib.Path) -> list[dict]:
    return tomllib.loads(path.read_text(encoding="utf-8")).get("package", [])


def _resolve(entry: str, by_name: dict[str, list[dict]]) -> dict:
    """A package a `dependencies` line names: "name", "name version" or "name version (source)"."""
    name, _, rest = entry.partition(" ")
    version = rest.split(" ", 1)[0] if rest else None
    candidates = by_name.get(name, [])
    if version is not None:
        candidates = [p for p in candidates if p["version"] == version]
    if len(candidates) != 1:
        raise ValueError(f"{entry!r} names {len(candidates)} packages in the lockfile")
    return candidates[0]


def closure(lock: pathlib.Path, root: str = CRATE) -> Packages:
    """`root` and everything it depends on, by the lockfile's own `dependencies` (optional ones and
    other targets' included: a lockfile resolves every feature), as (name, version) -> (source, checksum)."""
    packages = _read(lock)
    by_name: dict[str, list[dict]] = {}
    for p in packages:
        by_name.setdefault(p["name"], []).append(p)
    start = by_name.get(root, [])
    if len(start) != 1:
        raise ValueError(f"{lock} has {len(start)} packages named {root!r}")
    seen: dict[tuple[str, str], dict] = {}
    todo = [start[0]]
    while todo:
        p = todo.pop()
        key = (p["name"], p["version"])
        if key in seen:
            continue
        seen[key] = p
        todo.extend(_resolve(d, by_name) for d in p.get("dependencies", []))
    return {k: (p.get("source"), p.get("checksum")) for k, p in seen.items()}


def lock_packages(lock: pathlib.Path) -> Packages:
    """Every package of a lockfile, as `closure` returns them."""
    return {(p["name"], p["version"]): (p.get("source"), p.get("checksum")) for p in _read(lock)}


def _names(entry: dict) -> set[str]:
    return {d.partition(" ")[0] for d in entry.get("dependencies", [])}


def differences(root_lock: pathlib.Path = ROOT_LOCK, docker_lock: pathlib.Path = DOCKER_LOCK) -> list[str]:
    """Where the Docker build's lockfile has fallen behind the workspace's (empty if it has not), one line each.

    It is not "the same packages as the vexel-rs closure of the root lock", though that is what it comes to, because
    a lockfile lists a package's dependencies as the whole workspace activates them: `strict-num` lists `float-cmp`
    in the root lock because resvg's `tiny-skia-path` (the core's) turns that feature on, and the engine on its own
    does not need it, so walking the root lock from vexel-rs reaches one package more than the Docker lock holds.
    What can drift is a version, a checksum, or a dependency the engine's manifest gained or lost, and those are
    what is compared:

    - every package of the Docker lock is in the root lock, at the same version, source and checksum (a
      `cargo update`, or a bump of anything in the engine's closure, breaks this);
    - the engine's own dependencies (its manifest's, optional ones included) are the same set;
    - a package's dependencies in the Docker lock are among its dependencies in the root lock;
    - every package of the Docker lock is reachable from vexel-rs by its own edges (an orphan makes `--locked`
      want to rewrite the file).
    """
    root_packages = {(p["name"], p["version"]): p for p in _read(root_lock)}
    docker_entries = _read(docker_lock)
    lines = []
    for p in docker_entries:
        key = (p["name"], p["version"])
        want = root_packages.get(key)
        if want is None:
            held = sorted(v for n, v in root_packages if n == p["name"])
            lines.append(f"stale in {docker_lock.name}: {p['name']} {p['version']} (the workspace has {', '.join(held) if held else 'no such package'})")
            continue
        if (want.get("source"), want.get("checksum")) != (p.get("source"), p.get("checksum")):
            lines.append(f"{p['name']} {p['version']}: source or checksum differ from the workspace's")
        extra = _names(p) - _names(want)
        if extra:
            lines.append(f"{p['name']} {p['version']}: depends on {', '.join(sorted(extra))} here and not in the workspace's lock")
    engine = {n: p for (n, _), p in root_packages.items() if n == CRATE}.get(CRATE)
    held = next((p for p in docker_entries if p["name"] == CRATE), None)
    if engine is None or held is None:
        lines.append(f"{CRATE} is missing from {'the workspace lock' if engine is None else docker_lock.name}")
    elif _names(engine) != _names(held):
        a, b = sorted(_names(engine) - _names(held)), sorted(_names(held) - _names(engine))
        lines.append(f"{CRATE}'s own dependencies differ: the workspace has {a or 'nothing'} more, {docker_lock.name} has {b or 'nothing'} more")
    if held is not None:
        orphans = lock_packages(docker_lock).keys() - closure(docker_lock).keys()
        lines.extend(f"orphan in {docker_lock.name}: {n} {v} (nothing depends on it)" for n, v in sorted(orphans))
    return lines


REFRESH = (
    "refresh it from the repo root with\n"
    "    cd backend && .venv/bin/python -m tools.sync_vexel_lock\n"
    "(which copies backend/vexel-rs out of the workspace beside the root Cargo.lock and lets cargo prune it), then\n"
    "check that the image's build would accept it (it builds the crate on its own, with --locked) with\n"
    "    d=$(mktemp -d) && cp -R backend/vexel-rs \"$d\" && (cd \"$d/vexel-rs\" && cargo metadata --locked --offline --format-version 1 > /dev/null)\n"
    "and commit backend/vexel-rs/Cargo.lock. (After `cargo update`, or a change of a dependency in any workspace\n"
    "manifest, this is needed; `backend/Dockerfile` builds with `--locked`, so a stale file fails the image.)"
)


def refresh(root_lock: pathlib.Path = ROOT_LOCK, docker_lock: pathlib.Path = DOCKER_LOCK) -> None:
    crate = docker_lock.parent
    with tempfile.TemporaryDirectory() as tmp:
        copy = pathlib.Path(tmp) / CRATE
        shutil.copytree(crate, copy, ignore=shutil.ignore_patterns("target", "Cargo.lock", ".git"))
        shutil.copy(root_lock, copy / "Cargo.lock")
        # a crate copied out of the workspace is its own root: cargo resolves it against the lock beside it,
        # keeps what it pins, drops what it does not need and writes the file back
        subprocess.run(["cargo", "metadata", "--offline", "--format-version", "1", "--manifest-path", str(copy / "Cargo.toml")],
                       check=True, stdout=subprocess.DEVNULL)
        shutil.copy(copy / "Cargo.lock", docker_lock)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="only compare; exit 1 if the two lockfiles disagree")
    args = ap.parse_args()
    if not args.check:
        refresh()
    diff = differences()
    if diff:
        print("\n".join(diff))
        sys.exit(f"{DOCKER_LOCK.relative_to(ROOT)} is not the vexel-rs closure of Cargo.lock; {REFRESH}")
    print(f"{DOCKER_LOCK.relative_to(ROOT)} holds the vexel-rs closure of Cargo.lock ({len(lock_packages(DOCKER_LOCK))} packages)")


if __name__ == "__main__":
    main()
