"""Corpus loading. The manifest is the source of truth; PNGs live beside it."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from studi0trace.imaging.svg import svg_stats

MANIFEST = "manifest.yaml"


@dataclass
class Item:
    id: str
    cls: str
    png: Path
    width: int
    height: int
    truth_svg: Path | None = None
    tags: list[str] = field(default_factory=list)

    @property
    def truth_paths(self) -> int | None:
        if self.truth_svg is None or not self.truth_svg.exists():
            return None
        return svg_stats(self.truth_svg.read_text(encoding="utf-8")).paths

    def to_manifest(self, root: Path) -> dict:
        entry = {
            "id": self.id,
            "class": self.cls,
            "png": self.png.relative_to(root).as_posix(),
            "width": self.width,
            "height": self.height,
            "tags": list(self.tags),
        }
        if self.truth_svg is not None:
            entry["truth_svg"] = self.truth_svg.relative_to(root).as_posix()
        return entry

    @classmethod
    def from_manifest(cls, entry: dict, root: Path) -> "Item":
        truth = entry.get("truth_svg")
        return cls(
            id=entry["id"],
            cls=entry["class"],
            png=root / entry["png"],
            width=int(entry["width"]),
            height=int(entry["height"]),
            truth_svg=(root / truth) if truth else None,
            tags=list(entry.get("tags", [])),
        )


def load_corpus(root: Path, classes: list[str] | None = None, ids: list[str] | None = None) -> list[Item]:
    manifest = root / MANIFEST
    if not manifest.exists():
        raise FileNotFoundError(f"No {MANIFEST} in {root}. Run `python -m bench generate` first.")
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    items = [Item.from_manifest(e, root) for e in data.get("items", [])]
    if classes:
        items = [i for i in items if i.cls in classes]
    if ids:
        wanted = set(ids)
        items = [i for i in items if i.id in wanted]
    return items


def write_manifest(root: Path, items: list[Item]) -> Path:
    manifest = root / MANIFEST
    payload = {"items": [i.to_manifest(root) for i in sorted(items, key=lambda i: i.id)]}
    manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return manifest
