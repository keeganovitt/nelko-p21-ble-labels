#!/usr/bin/env python3
"""Check label PNGs against the P21 print spec and build a preview sheet.

For every PNG in a folder (or the names given), checks that it:
  - is exactly <length> x 96 px (320 x 96 for a 40 mm label at 203 dpi)
  - is pure black and white (no grey, no anti-aliasing)
  - keeps the margins clear: 8 px left, 4 px right, 2 px top and bottom
  - passes through p21_ble's renderer unchanged (so it prints 1:1, unscaled)

Usage:
    python check_labels.py examples/breaker-panel/labels
    python check_labels.py labels L3 R7 --length-mm 50
Exits non-zero if any label fails. Writes preview.png into the folder when all pass.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageOps

from p21_ble import natural_key, render_crisp

HEIGHT = 96
MARGINS = {"left": 8, "right": 4, "top": 2, "bottom": 2}


def check(path: Path, width: int) -> list[str]:
    """Return a list of problems with one label file (empty if it passes)."""
    img = Image.open(path)
    if img.size != (width, HEIGHT):
        return [f"size {img.size[0]}x{img.size[1]}, expected {width}x{HEIGHT}"]
    problems: list[str] = []
    gray = img.convert("L")
    if set(gray.tobytes()) - {0, 255}:
        problems.append("contains grey; must be pure black/white")
    box = ImageOps.invert(gray).getbbox()
    if box is None:
        return problems + ["image is blank"]
    room = {"left": box[0], "top": box[1], "right": width - box[2], "bottom": HEIGHT - box[3]}
    for side, need in MARGINS.items():
        if room[side] < need:
            problems.append(f"ink {room[side]} px from {side} edge, need {need}")
    printed = Image.frombytes("1", (HEIGHT, width), render_crisp(path, HEIGHT, width))
    expected = gray.point(lambda v: 255 if v >= 140 else 0)
    if printed.rotate(-90, expand=True).convert("L").tobytes() != expected.tobytes():
        problems.append("would be altered by the print renderer")
    return problems


def preview(paths: list[Path], out: Path, width: int, zoom: int = 3) -> None:
    """Save a two-column contact sheet of ``paths`` at ``zoom`` nearest-neighbour scale."""
    cell_w, cell_h, gap = width * zoom, HEIGHT * zoom, 12
    rows = (len(paths) + 1) // 2
    sheet = Image.new("L", (2 * cell_w + 3 * gap, rows * (cell_h + gap) + gap), 160)
    for i, path in enumerate(paths):
        tile = Image.open(path).convert("L").resize((cell_w, cell_h), Image.Resampling.NEAREST)
        col, row = (0, i) if i < rows else (1, i - rows)
        sheet.paste(tile, (gap + col * (cell_w + gap), gap + row * (cell_h + gap)))
    sheet.save(out)


def main() -> None:
    """Check labels and write preview.png."""
    parser = argparse.ArgumentParser(description="Validate P21 label PNGs.")
    parser.add_argument("folder", type=Path, help="folder of label PNGs")
    parser.add_argument("names", nargs="*", help="only these labels (default: every PNG)")
    parser.add_argument("--length-mm", type=int, default=40, help="label length in mm (default %(default)s)")
    parser.add_argument("--no-preview", action="store_true", help="don't write preview.png")
    args = parser.parse_args()

    width = args.length_mm * 8
    if args.names:
        paths = [args.folder / f"{n}.png" for n in args.names]
    else:
        paths = [f for f in args.folder.glob("*.png") if f.stem.lower() != "preview"]
    paths.sort(key=lambda f: natural_key(f.stem))

    failed = 0
    for path in paths:
        problems = ["file missing"] if not path.is_file() else check(path, width)
        if problems:
            failed += 1
            print(f"FAIL {path.stem}: " + "; ".join(problems))
    print(f"{len(paths) - failed}/{len(paths)} labels pass")
    if paths and not failed and not args.no_preview:
        preview(paths, args.folder / "preview.png", width)
        print(f"Preview: {args.folder / 'preview.png'}")
    sys.exit(1 if failed or not paths else 0)


if __name__ == "__main__":
    main()
