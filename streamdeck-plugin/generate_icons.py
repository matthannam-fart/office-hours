"""Generate SVG icons for the Stream Deck plugin.

Run this once to create the required icon files.
"""
import os

BASE = os.path.join(os.path.dirname(__file__), "com.vox.intercom.sdPlugin", "imgs")
os.makedirs(os.path.join(BASE, "actions"), exist_ok=True)

OH_TEAL = "#71ada3"
OH_TEAL_DIM = "#283c3c"

def svg_icon(w, h, bg, text, font_size=14):
    lines = text.split("\n")
    line_h = font_size + 4
    total = len(lines) * line_h
    start_y = (h - total) / 2 + font_size
    text_els = ""
    for i, line in enumerate(lines):
        y = start_y + i * line_h
        text_els += f'<text x="{w//2}" y="{y}" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="{font_size}" font-weight="bold" fill="white">{line}</text>'
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"><rect width="{w}" height="{h}" rx="8" fill="{bg}"/>{text_els}</svg>'

def oh_logo(w, h):
    """Vox logo: teal circle with white VOX text."""
    cx, cy = w // 2, h // 2
    r = min(w, h) // 2 - 4
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">'
        f'<rect width="{w}" height="{h}" fill="#000"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{OH_TEAL}"/>'
        f'<text x="{cx}" y="{cy + 8}" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="18" font-weight="bold" fill="white">VOX</text>'
        f'</svg>'
    )

icons = {
    # Plugin-level icons (shown in SD app store / plugin list)
    "plugin-icon": oh_logo(144, 144),
    "category-icon": oh_logo(56, 56),
    # Action icons (shown in action picker sidebar)
    "actions/talk": svg_icon(40, 40, OH_TEAL, "PTT", 10),
    "actions/mode": svg_icon(40, 40, "#008c3c", "MODE", 9),
    "actions/team": svg_icon(40, 40, OH_TEAL_DIM, "TEAM", 9),
    "actions/user": svg_icon(40, 40, OH_TEAL_DIM, "USER", 9),
    "actions/panel": svg_icon(40, 40, OH_TEAL_DIM, "MORE", 9),
}

for name, svg in icons.items():
    # SD SDK looks for extensionless paths and finds .svg
    path = os.path.join(BASE, f"{name}.svg")
    with open(path, "w") as f:
        f.write(svg)
    print(f"  Created {path}")

print("\nDone! Icons generated.")
