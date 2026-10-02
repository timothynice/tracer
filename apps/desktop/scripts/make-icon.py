"""Render src-tauri/icons/icon.svg to the 1024 px PNG `tauri icon` makes the app's icons from.

Run from apps/desktop with the backend's venv (it has resvg-py): `npm run icon`, then
`npx tauri icon src-tauri/icons/icon-1024.png --output <tmp>` and copy the five files tauri.conf.json lists.
"""
from pathlib import Path

import resvg_py

here = Path(__file__).resolve().parent.parent / "src-tauri" / "icons"
png = resvg_py.svg_to_bytes(svg_string=(here / "icon.svg").read_text(encoding="utf-8"), width=1024, height=1024)
(here / "icon-1024.png").write_bytes(bytes(png))
print("wrote", here / "icon-1024.png")
