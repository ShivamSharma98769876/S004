# Chart assets

**`trendpulse-rupee-coin.png`** — used by TrendPulse at `/images/trendpulse-rupee-coin.png`.

## Remove checkerboard / gray background (automated)

From repo root (requires [Pillow](https://pypi.org/project/pillow/)):

```bash
pip install pillow
python frontend/scripts/remove_rupee_coin_bg.py
```

- Overwrites `trendpulse-rupee-coin.png` with a version where light neutral pixels are **transparent**.
- Saves the previous file as **`trendpulse-rupee-coin.png.bak`**.
- **Watermarks** (e.g. faint text on the coin) may still show if they’re similar in color to the gold; for a perfectly clean asset, use a licensed file or manual touch-up.

Recommended source: square PNG (~256–1024px); script works on opaque checkerboard exports.
