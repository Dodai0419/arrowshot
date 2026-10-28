# AGENTS.md

This repository ships one tool, `arrowshot.py`, for coding agents (Codex, Claude Code, others).


Draw big, readable arrows with a sign on top of a screenshot (any OS), or put several shots on one numbered sheet.

## When to use
- You need the human to click one specific button, toggle or tab: point at it instead of describing it.
- You are writing a how-to with 2-4 steps: one screenshot with numbered signs ("1. ...", "2. ...").
- You took several screenshots (before/after, a flow) and want to show them as one image.

## Commands
Run from the repository root. Python 3.9+ and Pillow (`pip install pillow`).

```bash
# One mark: --at x,y hits a point, --rect x,y,w,h boxes an element (pixels, top-left origin)
python arrowshot.py point shot.png --rect 826,580,114,36 --text "Click Allow" -o out.png

# Ring a small control, zigzag shaft, green, smaller arrow
python arrowshot.py point shot.png --rect 866,253,48,24 --style ring --shape zigzag --color green --size S --text "Switch this on"

# Several marks from a JSON list (same keys as the flags: at|rect, text, from, style, shape, color, size, corners)
python arrowshot.py point shot.png --marks steps.json -o steps.png

# Many screenshots -> one numbered contact sheet (up to 12)
python arrowshot.py sheet a.png b.png c.png -o sheet.png
```

Options: `--from auto|top|bottom|left|right|top-left|top-right|bottom-left|bottom-right` (a preference; the sign moves if it does not fit), `--style arrow|ring|box`, `--shape bend|straight|zigzag|spiral`, `--color red|orange|yellow|green|teal|blue|purple|pink|black|white|#RRGGBB`, `--size S|M|L`, `--corners round|sharp`, `--json` (print tip and sign position).

## Tips
- Find coordinates from the screenshot itself (open it, or use your own vision on it). `--rect` is safer than `--at` because the arrow stops just outside the box.
- Keep signs short: 2-6 words. Long text wraps to 3 lines and shrinks.
- For multi-step guides, number the text yourself and give each mark its own colour.
- Signs of later marks avoid earlier ones automatically.
