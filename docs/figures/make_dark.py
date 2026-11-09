"""Generate <name>-dark.svg next to each light SVG in this folder.

Every figure takes its colors from one :root rule; the dark variant swaps those
tokens so the figure reads on GitHub's dark theme. Run after editing a figure:
    python docs/figures/make_dark.py
"""
import re
from pathlib import Path

DARK = "--ink:#e6e4da;--mute:#9b9f94;--line:#575c52;--acc:#e5795f;--paper:#1c201b"
ROOT = re.compile(r":root\{--bg:[^}]*\}|:root\{--ink:[^}]*\}")
# Figures exported from the blog also inline the same tokens as rgb() literals.
LITERALS = {
    "rgb(41, 45, 38)": "rgb(230, 228, 218)",     # ink
    "rgb(139, 143, 132)": "rgb(155, 159, 148)",  # mute
    "rgb(185, 66, 40)": "rgb(229, 121, 95)",     # accent
    "rgb(251, 250, 245)": "rgb(28, 32, 27)",     # paper
}

here = Path(__file__).parent
for svg in sorted(here.glob("*.svg")):
    if svg.stem.endswith("-dark"):
        continue
    text = svg.read_text(encoding="utf-8")
    dark, n = ROOT.subn(lambda m: ":root{" + DARK + "}", text, count=1)
    assert n == 1, f"{svg.name}: no :root color rule found"
    for light, dark_rgb in LITERALS.items():
        dark = dark.replace(light, dark_rgb)
    svg.with_name(svg.stem + "-dark.svg").write_text(dark, encoding="utf-8")
    print("wrote", svg.stem + "-dark.svg")
