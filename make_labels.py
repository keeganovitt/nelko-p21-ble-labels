#!/usr/bin/env python3
"""Generate print-ready breaker-panel labels from a CSV.

Each row becomes a 1-bit PNG sized 1:1 to the printer's dots, so it prints with no
scaling: a black badge with the circuit ID and amperage, an icon, a divider, and
a title with an optional subtitle.

CSV columns: id,amps,title,subtitle,icon
  * title:    " / " marks the preferred line break for a two-line title (not printed)
  * subtitle: optional
  * icon:     one of the names in ICONS (see --list-icons), or empty

Usage:
    python make_labels.py examples/breaker-panel/labels.csv -o labels
    python make_labels.py my_panel.csv -o labels L3 R7       # only some rows
    python make_labels.py --list-icons
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

SUBTITLE_WEIGHT = "bold"  # regular weights print 1-px strokes that come out faint
SS = 4  # supersampling factor: draw at 4x, then downsample + threshold to 1-bit

# Fonts are looked up in order; pass --font-bold / --font-regular to override.
BOLD_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",            # macOS
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",                                  # Windows
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",          # Debian/Ubuntu
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",        # Fedora
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",                      # Arch
]
REGULAR_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]


# --- Layout (1x pixel units; one pixel = one printer dot) ---------------------
class Layout:
    """Geometry for a label of ``length`` x 96 dots (40 mm -> 320)."""

    def __init__(self, length: int) -> None:
        self.w, self.h = length, 96
        self.border = (8, 2, length - 5, 93)        # 8 px left / 4 px right margin, 2 px top/bottom
        self.badge = (12, 6, 62, 89)
        self.icon = (71, 32, 103, 64)               # 32 x 32
        self.divider_x = 112
        self.text_x0, self.text_x1 = 120, length - 12
        self.title_sizes = range(24, 17, -1)        # px; 18 px keeps cap height >= 13
        self.sub_sizes = range(15, 12, -1)          # px; 13 px keeps cap height >= 9


def find_font(candidates: list[str], override: str | None) -> str:
    """Return the first existing font path."""
    for path in ([override] if override else []) + candidates:
        if path and Path(path).is_file():
            return path
    sys.exit("No usable TrueType font found; pass --font-bold and --font-regular.")


class Fonts:
    """Cached supersampled fonts."""

    def __init__(self, bold: str, regular: str) -> None:
        self.paths = {"bold": bold, "regular": regular}
        self.cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    def get(self, kind: str, size: int) -> ImageFont.FreeTypeFont:
        key = (kind, size)
        if key not in self.cache:
            self.cache[key] = ImageFont.truetype(self.paths[kind], size * SS)
        return self.cache[key]


def text_width(font: ImageFont.FreeTypeFont, text: str) -> float:
    """Width of ``text`` in 1x pixels."""
    return font.getlength(text) / SS


def cap_height(font: ImageFont.FreeTypeFont) -> float:
    """Cap height of ``font`` in 1x pixels."""
    box = font.getbbox("H")
    return (box[3] - box[1]) / SS


# --- Icons: drawn in a 32x32 box (1x units), filled black on white ----------------
IconFn = Callable[[ImageDraw.ImageDraw, Callable[[float, float], tuple[int, int]], Callable[[float], int]], None]


def _icon_outlet(d, p, s):
    d.rounded_rectangle([p(3, 3), p(29, 29)], radius=s(6), fill=0)
    d.rectangle([p(10, 8), p(13, 16)], fill=255)
    d.rectangle([p(19, 8), p(22, 16)], fill=255)
    d.pieslice([p(13, 18), p(19, 26)], 180, 360, fill=255)
    d.rectangle([p(13, 22), p(19, 26)], fill=255)


def _icon_bulb(d, p, s):
    d.ellipse([p(7, 2), p(25, 20)], fill=0)
    d.polygon([p(10, 16), p(22, 16), p(20, 23), p(12, 23)], fill=0)
    d.rectangle([p(12, 24), p(20, 26)], fill=0)
    d.rectangle([p(13, 27), p(19, 29)], fill=0)


def _icon_bolt(d, p, s):
    d.polygon([p(19, 1), p(6, 18), p(15, 18), p(12, 31), p(26, 13), p(17, 13), p(21, 1)], fill=0)


def _icon_drop(d, p, s):
    d.ellipse([p(7, 12), p(25, 30)], fill=0)
    d.polygon([p(16, 1), p(7.6, 18), p(24.4, 18)], fill=0)


def _icon_flame(d, p, s):
    d.ellipse([p(6, 12), p(26, 31)], fill=0)
    d.polygon([p(16, 1), p(26, 18), p(6, 18)], fill=0)
    d.polygon([p(24, 6), p(27, 18), p(20, 14)], fill=0)
    d.ellipse([p(11, 19), p(21, 29)], fill=255)
    d.polygon([p(16, 13), p(21, 23), p(11, 23)], fill=255)


def _icon_snowflake(d, p, s):
    for angle in (90, 30, -30):
        a = math.radians(angle)
        dx, dy = 14 * math.cos(a), 14 * math.sin(a)
        d.line([p(16 - dx, 16 - dy), p(16 + dx, 16 + dy)], fill=0, width=s(3))
        for sign in (1, -1):
            bx, by = 16 + sign * dx * 0.62, 16 + sign * dy * 0.62
            for turn in (35, -35):
                b = a + math.radians(turn) + (math.pi if sign < 0 else 0)
                d.line([p(bx, by), p(bx + 5 * math.cos(b), by + 5 * math.sin(b))], fill=0, width=s(2.5))


def _icon_stove(d, p, s):
    d.rounded_rectangle([p(3, 3), p(29, 29)], radius=s(3), outline=0, width=s(3))
    d.rectangle([p(3, 9), p(29, 11)], fill=0)
    for x in (9, 16, 23):
        d.ellipse([p(x - 2, 4.5), p(x + 2, 8)], fill=0)
    d.rectangle([p(9, 15), p(23, 24)], outline=0, width=s(2.5))


def _icon_washer(d, p, s):
    d.rounded_rectangle([p(4, 2), p(28, 30)], radius=s(3), outline=0, width=s(3))
    d.rectangle([p(4, 7), p(28, 9)], fill=0)
    d.ellipse([p(7, 3.5), p(10, 6.5)], fill=0)
    d.ellipse([p(9, 12), p(23, 26)], outline=0, width=s(3))


def _icon_fridge(d, p, s):
    d.rounded_rectangle([p(7, 1), p(25, 31)], radius=s(3), fill=0)
    d.rectangle([p(7, 11), p(25, 13)], fill=255)
    d.rectangle([p(10, 5), p(12, 9)], fill=255)
    d.rectangle([p(10, 16), p(12, 22)], fill=255)


def _icon_bed(d, p, s):
    d.rectangle([p(2, 7), p(5, 28)], fill=0)
    d.rectangle([p(2, 20), p(30, 24)], fill=0)
    d.rectangle([p(27, 20), p(30, 28)], fill=0)
    d.ellipse([p(7, 11), p(13, 17)], fill=0)
    d.rounded_rectangle([p(15, 12), p(30, 19)], radius=s(3), fill=0)


def _icon_fan(d, p, s):
    for angle in (90, 210, 330):
        a = math.radians(angle)
        cx, cy = 16 + 8 * math.cos(a), 16 - 8 * math.sin(a)
        d.ellipse([p(cx - 6, cy - 6), p(cx + 6, cy + 6)], fill=0)
    d.ellipse([p(12, 12), p(20, 20)], fill=255)
    d.ellipse([p(14, 14), p(18, 18)], fill=0)


def _icon_plug(d, p, s):
    d.rectangle([p(10, 2), p(13, 9)], fill=0)
    d.rectangle([p(19, 2), p(22, 9)], fill=0)
    d.rounded_rectangle([p(6, 9), p(26, 20)], radius=s(4), fill=0)
    d.rectangle([p(14, 20), p(18, 31)], fill=0)


ICONS: dict[str, IconFn] = {
    "outlet": _icon_outlet, "bulb": _icon_bulb, "bolt": _icon_bolt, "drop": _icon_drop,
    "flame": _icon_flame, "snowflake": _icon_snowflake, "stove": _icon_stove, "washer": _icon_washer,
    "fridge": _icon_fridge, "bed": _icon_bed, "fan": _icon_fan, "plug": _icon_plug,
}


def draw_icon(draw: ImageDraw.ImageDraw, name: str, box: tuple[int, int, int, int]) -> None:
    """Draw icon ``name`` scaled into ``box`` (1x coords)."""
    x0, y0, x1, y1 = box
    k = (x1 - x0) / 32

    def p(x: float, y: float) -> tuple[int, int]:
        return round((x0 + x * k) * SS), round((y0 + y * k) * SS)

    def s(v: float) -> int:
        return max(1, round(v * k * SS))

    ICONS[name](draw, p, s)


# --- Label rendering ------------------------------------------------------------
def fit_title(fonts: Fonts, layout: Layout, title: str) -> tuple[list[str], ImageFont.FreeTypeFont]:
    """Choose title lines and the largest size that fits the text column."""
    room = layout.text_x1 - layout.text_x0
    one_line = title.replace(" / ", " ")
    if " / " in title:
        two_lines = [part.strip() for part in title.split(" / ", 1)]
    else:  # break at the word boundary that best balances the two lines
        words = one_line.split()
        splits = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
        two_lines = list(min(splits, key=lambda a: abs(len(a[0]) - len(a[1])))) if splits else [one_line]
    for size in layout.title_sizes:
        font = fonts.get("bold", size)
        if text_width(font, one_line) <= room:
            return [one_line], font
    for size in layout.title_sizes:
        font = fonts.get("bold", size)
        if all(text_width(font, line) <= room for line in two_lines):
            return two_lines, font
    raise ValueError(f"title {title!r} doesn't fit even at the minimum size")


def fit_subtitle(fonts: Fonts, layout: Layout, subtitle: str) -> tuple[str, ImageFont.FreeTypeFont, bool]:
    """Choose the subtitle size; drop trailing ' · ' parts if it still doesn't fit."""
    room = layout.text_x1 - layout.text_x0
    text, dropped = subtitle, False
    while True:
        for size in layout.sub_sizes:
            font = fonts.get(SUBTITLE_WEIGHT, size)
            if text_width(font, text) <= room:
                return text, font, dropped
        if " · " not in text:
            raise ValueError(f"subtitle {subtitle!r} doesn't fit")
        text, dropped = text.rsplit(" · ", 1)[0], True


def render_label(row: dict, fonts: Fonts, layout: Layout) -> tuple[Image.Image, dict]:
    """Render one CSV row; returns the 1-bit image and the sizes used."""
    W, H = layout.w * SS, layout.h * SS
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)

    def box(b):  # 1x box -> SS box
        return [b[0] * SS, b[1] * SS, (b[2] + 1) * SS - 1, (b[3] + 1) * SS - 1]

    d.rounded_rectangle(box(layout.border), radius=6 * SS, outline=0, width=2 * SS)
    d.rounded_rectangle(box(layout.badge), radius=5 * SS, fill=0)

    # Badge: ID (top half), rule, amps (bottom half)
    bx0, by0, bx1, by1 = layout.badge
    cx, mid = (bx0 + bx1 + 1) / 2, (by0 + by1 + 1) / 2
    ident = row["id"]
    for size in range(28, 15, -1):
        id_font = fonts.get("bold", size)
        if text_width(id_font, ident) <= (bx1 - bx0) - 6:
            break
    cap = cap_height(id_font)
    d.text(((cx - text_width(id_font, ident) / 2) * SS, ((by0 + mid) / 2 + cap / 2) * SS),
           ident, font=id_font, fill=255, anchor="ls")
    d.rectangle([round((cx - 16) * SS), round((mid - 1) * SS), round((cx + 16) * SS), round((mid + 1) * SS) - 1], fill=255)
    amps = row.get("amps", "").strip()
    if amps:
        for size in range(16, 10, -1):
            amp_font = fonts.get("bold", size)
            if text_width(amp_font, amps) <= (bx1 - bx0) - 8:
                break
        cap = cap_height(amp_font)
        d.text(((cx - text_width(amp_font, amps) / 2) * SS, ((mid + by1) / 2 + cap / 2) * SS),
               amps, font=amp_font, fill=255, anchor="ls")

    icon = row.get("icon", "").strip().lower()
    if icon:
        if icon not in ICONS:
            raise ValueError(f"unknown icon {icon!r}; choose from {', '.join(sorted(ICONS))}")
        draw_icon(d, icon, layout.icon)

    d.rectangle([layout.divider_x * SS, 10 * SS, (layout.divider_x + 2) * SS - 1, 87 * SS - 1], fill=0)

    # Text block
    lines, title_font = fit_title(fonts, layout, row["title"].strip())
    subtitle = row.get("subtitle", "").strip()
    sub_text, sub_font, dropped = fit_subtitle(fonts, layout, subtitle) if subtitle else ("", None, False)
    t_cap = cap_height(title_font)
    t_lead = t_cap * 1.45                        # baseline-to-baseline for title lines
    block = t_cap + t_lead * (len(lines) - 1)
    if sub_font:
        s_cap = cap_height(sub_font)
        gap = 6 if len(lines) == 1 else 5
        block += gap + s_cap
    y = (layout.h - block) / 2 + t_cap             # first baseline
    for line in lines:
        d.text((layout.text_x0 * SS, y * SS), line, font=title_font, fill=0, anchor="ls")
        y += t_lead
    if sub_font:
        y += gap + s_cap - t_lead
        d.text((layout.text_x0 * SS, y * SS), sub_text, font=sub_font, fill=0, anchor="ls")

    small = img.resize((layout.w, layout.h), Image.Resampling.BOX)
    out = small.point(lambda v: 255 if v >= 128 else 0).convert("1", dither=Image.Dither.NONE)
    info = {"title_px": title_font.size // SS, "lines": len(lines),
            "sub_px": sub_font.size // SS if sub_font else None, "dropped": dropped}
    return out, info


def main() -> None:
    """Parse arguments and render labels."""
    parser = argparse.ArgumentParser(description="Generate 1-bit breaker-panel labels from a CSV.")
    parser.add_argument("csv", nargs="?", type=Path, help="CSV with columns id,amps,title,subtitle,icon")
    parser.add_argument("ids", nargs="*", help="only these IDs (default: every row)")
    parser.add_argument("-o", "--out", type=Path, default=Path("labels"), help="output folder (default ./labels)")
    parser.add_argument("--length-mm", type=int, default=40, help="label length in mm (default %(default)s)")
    parser.add_argument("--font-bold", help="path to a bold .ttf/.otf")
    parser.add_argument("--font-regular", help="path to a regular .ttf/.otf")
    parser.add_argument("--list-icons", action="store_true", help="list icon names and exit")
    args = parser.parse_args()

    if args.list_icons:
        print("\n".join(sorted(ICONS)))
        return
    if not args.csv:
        parser.error("a CSV file is required")

    layout = Layout(round(args.length_mm * 8))
    fonts = Fonts(find_font(BOLD_FONTS, args.font_bold), find_font(REGULAR_FONTS, args.font_regular))
    with args.csv.open(newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("id", "").strip()]
    if args.ids:
        wanted = set(args.ids)
        rows = [r for r in rows if r["id"] in wanted]
        missing = wanted - {r["id"] for r in rows}
        if missing:
            sys.exit(f"Not in CSV: {', '.join(sorted(missing))}")

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"{'id':<6}{'title px':>9}{'lines':>6}{'sub px':>7}  note")
    errors = []
    for row in rows:
        try:
            image, info = render_label(row, fonts, layout)
        except ValueError as error:
            errors.append(f"{row['id']}: {error}")
            print(f"{row['id']:<6}  ERROR: {error}")
            continue
        image.save(args.out / f"{row['id']}.png")
        note = "subtitle shortened" if info["dropped"] else ""
        if info["title_px"] == min(layout.title_sizes) or info["sub_px"] == min(layout.sub_sizes):
            note = (note + "; " if note else "") + "at minimum size"
        print(f"{row['id']:<6}{info['title_px']:>9}{info['lines']:>6}{info['sub_px'] or '-':>7}  {note}")
    print(f"Wrote {len(rows) - len(errors)} label(s) to {args.out}/")
    if errors:
        sys.exit(f"{len(errors)} label(s) didn't fit; shorten the text or use a longer label.")


if __name__ == "__main__":
    main()
