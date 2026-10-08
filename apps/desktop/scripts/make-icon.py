"""Make src-tauri/icons/icon-1024.png, the file `tauri icon` builds the app's icons from, out of icon-source.png.

The source is the brand's square artwork. macOS wants an icon as a rounded square of 824 px on a transparent
1024 px canvas with a soft shadow under it, so the artwork is scaled to 824, cut to the corners (radius 185)
and set on that canvas. Run from apps/desktop with the backend's venv (it has Pillow): `npm run icon`, then
`npx tauri icon src-tauri/icons/icon-1024.png --output <tmp>` and copy the five files tauri.conf.json lists.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE, TILE, RADIUS, INSET = 1024, 824, 185, 100
SHADOW_DY, SHADOW_BLUR, SHADOW_ALPHA = 12, 14, 0.32
AA = 4  # the mask is drawn at 4× and scaled down, so the corners are smooth

here = Path(__file__).resolve().parent.parent / "src-tauri" / "icons"
art = Image.open(here / "icon-source.png").convert("RGBA").resize((TILE, TILE), Image.LANCZOS)

mask = Image.new("L", (TILE * AA, TILE * AA), 0)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, TILE * AA - 1, TILE * AA - 1), radius=RADIUS * AA, fill=255)
mask = mask.resize((TILE, TILE), Image.LANCZOS)
tile = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))
tile.paste(art, (0, 0), mask)

canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
shadow = Image.new("L", (SIZE, SIZE), 0)
shadow.paste(mask, (INSET, INSET + SHADOW_DY))
shadow = shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR)).point(lambda v: int(v * SHADOW_ALPHA))
canvas.paste(Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 255)), (0, 0), shadow)
canvas.alpha_composite(tile, (INSET, INSET))
canvas.save(here / "icon-1024.png", optimize=True)
print("wrote", here / "icon-1024.png")
