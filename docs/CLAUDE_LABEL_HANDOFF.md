# Prompt: designing labels for a Nelko P21 with an AI assistant

Paste everything below the line into Claude (or another assistant), then add what you want,
e.g. "Make labels for these circuits: …" or "Design a label for my spice jars."

There are two ways to work:
- **A. Breaker-style labels:** the assistant writes rows for `make_labels.py`'s CSV. This is the
  quickest and most consistent.
- **B. Custom artwork:** the assistant draws the PNGs itself, to the hard constraints below.

---

I print labels on a **Nelko P21** thermal printer with the `nelko-p21-ble-labels` project.
It prints PNG files **1:1 with no scaling**, so the images must match the printer exactly.

## Hard constraints (any label)

- **Canvas:** exactly **(label length in mm x 8) x 96 px**, landscape. A 40 mm label is
  **320 x 96**. One pixel is one printer dot (203 dpi). 96 px is the head's 12 mm print width.
- **Pure black and white only**, Pillow mode `"1"`. No grey, anti-aliasing or dithering. Draw
  at 4x, downsample, then threshold at 128, or draw text with `ImageDraw.fontmode = "1"`.
- **Margins:** nothing black within **8 px of the left edge**, 4 px of the right, or 2 px of the
  top and bottom. The printer starts very close to the left end.
- **Strokes at least 2 px wide.** Thinner lines print faint or broken.
- **Text:** use bold weights. Cap height at least 13 px for main text and at least 9 px for
  secondary text. Regular weights at small sizes print too thin.
- Save as PNG. The file name (without `.png`) is how the label is printed: `./print.sh L1`.

## Option A: breaker-style labels with `make_labels.py`

Give me a CSV with this header, one row per label:

```csv
id,amps,title,subtitle,icon
L1,15A,Living Room,Outlets · AFCI,outlet
L2,20A,Kitchen Counter / Outlets,GFCI,outlet
R2,20A,Refrigerator,,fridge
```

- `id`: short circuit ID, at most about 3 characters, e.g. `L1`, `R12`, `7`. It goes in the
  black badge.
- `amps`: e.g. `20A`, or `30/40` for a combined circuit.
- `title`: main text, about 20 characters per line at most. Use ` / ` to choose the line break
  for a two-line title.
- `subtitle`: optional. Keep it under about 28 characters. Separate parts with ` · `
  (U+00B7 with spaces). If it's too long, the generator drops the last part.
- `icon`: one of `bed`, `bolt`, `bulb`, `drop`, `fan`, `flame`, `fridge`, `outlet`, `plug`,
  `snowflake`, `stove`, `washer`, or empty. Pick the closest match.

Then I run:
```bash
.venv/bin/python make_labels.py my_panel.csv -o labels
.venv/bin/python check_labels.py labels
```

If I ask for an icon that isn't in the list, write a new `_icon_<name>(d, p, s)` function for
`make_labels.py`, in the same style as the existing ones: drawn in a 32 x 32 box, filled
shapes, strokes at least 2.5 units, using `p(x, y)` for points and `s(v)` for widths. Then add
it to the `ICONS` dict.

## Option B: custom artwork

Write a Python + Pillow script that produces the PNGs to the hard constraints above. Then:

1. Check every file is the right size, mode `"1"`, and only contains values 0 and 255.
2. Check the ink bounding box respects the margins.
3. Show me a 3x nearest-neighbour preview, and point out anything that may be hard to read at
   203 dpi.

I'll then run `.venv/bin/python check_labels.py <folder>`. Add `--length-mm N` if the label
isn't 40 mm. It must report every label as passing.
