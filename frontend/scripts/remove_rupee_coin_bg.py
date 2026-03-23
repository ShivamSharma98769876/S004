#!/usr/bin/env python3
"""
Remove opaque checkerboard / light gray background from the TrendPulse rupee coin PNG.

Keeps pixels that look like the gold symbol (warm hue or strong chroma).
Neutral light backgrounds (white / gray cells) become transparent.

Usage (from repo root):
  pip install pillow
  python frontend/scripts/remove_rupee_coin_bg.py

Input/output: frontend/public/images/trendpulse-rupee-coin.png
Creates a .bak backup before overwriting.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("Missing Pillow. Run: pip install pillow", file=sys.stderr)
    sys.exit(1)

FRONTEND = Path(__file__).resolve().parent.parent
IMG = FRONTEND / "public" / "images" / "trendpulse-rupee-coin.png"


def is_background(r: int, g: int, b: int, a: int) -> bool:
    """Treat light, low-chroma pixels as background (checkerboard + flat grays)."""
    if a < 10:
        return True
    mx, mn = max(r, g, b), min(r, g, b)
    chroma = mx - mn
    # Dark pixels — keep (coin shadows, deep gold)
    if mx < 95:
        return False
    # Warm gold / bronze: red channel leads green, green leads blue
    if r > 115 and (r > g + 8 or (r + g > b + 80 and g > b + 5)):
        if chroma > 22:
            return False
    # Strong color — keep
    if chroma > 48:
        return False
    # Light neutral (white / light gray checker cells, watermark haze)
    if mx > 118 and chroma <= 48:
        return True
    return False


def main() -> int:
    if not IMG.is_file():
        print(f"Not found: {IMG}", file=sys.stderr)
        return 1

    bak = IMG.with_suffix(".png.bak")
    shutil.copy2(IMG, bak)
    print(f"Backup: {bak}")

    im = Image.open(IMG).convert("RGBA")
    px = im.load()
    w, h = im.size
    removed = 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if is_background(r, g, b, a):
                px[x, y] = (r, g, b, 0)
                removed += 1

    im.save(IMG, "PNG", optimize=True)
    print(f"Wrote {IMG} ({w}x{h}); made {removed} pixels transparent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
