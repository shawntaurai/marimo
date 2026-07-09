# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""Rebrand marimo/_static assets as dedomena (fork helper, see FORK.md).

Regenerates the favicon/logo files with a dedomena mark (white delta on a
deep teal rounded square) and rewrites the web manifests' app names.

The files in marimo/_static are overwritten whenever assets are refreshed
from the upstream PyPI wheel, so fork_refresh_static.py invokes this
script automatically after every refresh. Safe to run repeatedly.

Usage:
    python scripts/fork_rebrand_static.py
"""
# ruff: noqa: T201

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image, ImageFont

APP_NAME = "dedomena"
BACKGROUND = "#0f4c4c"  # deep teal
FOREGROUND = "#ffffff"
GLYPH = "δ"  # δ — delta, for dedomena

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC = REPO_ROOT / "marimo" / "_static"

ICONS = {
    "favicon-16x16.png": 16,
    "favicon-32x32.png": 32,
    "apple-touch-icon.png": 180,
    "android-chrome-192x192.png": 192,
    "android-chrome-512x512.png": 512,
    "logo.png": 512,
}


def _find_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    from PIL import ImageFont

    for name in ("seguisb.ttf", "segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def make_icon(px: int) -> Image.Image:
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = max(2, px // 5)
    draw.rounded_rectangle((0, 0, px - 1, px - 1), radius, fill=BACKGROUND)

    font = _find_font(int(px * 0.62))
    left, top, right, bottom = draw.textbbox((0, 0), GLYPH, font=font)
    x = (px - (right - left)) / 2 - left
    y = (px - (bottom - top)) / 2 - top
    draw.text((x, y), GLYPH, font=font, fill=FOREGROUND)
    return img


def main() -> None:
    for filename, px in ICONS.items():
        make_icon(px).save(STATIC / filename)
        print(f"wrote {filename} ({px}x{px})")

    make_icon(32).save(
        STATIC / "favicon.ico",
        sizes=[(16, 16), (32, 32), (48, 48)],
    )
    print("wrote favicon.ico")

    for manifest_name in ("site.webmanifest", "manifest.json"):
        path = STATIC / manifest_name
        if not path.exists():
            continue
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for key in ("name", "short_name"):
            if key in manifest:
                manifest[key] = APP_NAME
        path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"updated {manifest_name}")

    print(f"Done: static assets branded as {APP_NAME}")


if __name__ == "__main__":
    main()
