"""
ui_constants.py — Shared UI constants for Office Hours
Colour palettes, mode labels, dimensions used by both widgets and the panel.
"""

# ── Color Constants ──────────────────────────────────────────────
COLORS = {
    'GREEN':  '#00a651',
    'YELLOW': '#e6af00',
    'RED':    '#e22a1a',
    'OPEN':   '#2ABFBF',
    'BUSY':   '#ff9800',
    'INCOGNITO': '#555555',
}

MODE_LABELS = {
    'GREEN':  'Available',
    'YELLOW': 'Busy',
    'RED':    'DND',
    'OPEN':   'Open',
    'BUSY':   'In Call',
}

RADIO_STATIONS = {
    'NTS Radio': 'https://stream-relay-geo.ntslive.net/stream?client=NTSRadio',
}

# Panel dimensions
PANEL_W = 340
PANEL_RADIUS = 14

# ── Dark Theme Palette ───────────────────────────────────────────
DARK = {
    'BG':         '#1e1e1e',   # Panel background
    'BG_RAISED':  '#2a2a2a',   # Cards, inputs, elevated surfaces
    'BG_HOVER':   '#333333',   # Hover state
    'BORDER':     '#3a3a3a',   # Subtle borders
    'BORDER_LT':  '#2e2e2e',   # Lighter border (dividers)
    'TEXT':        '#e8e8e8',   # Primary text
    'TEXT_DIM':    '#999999',   # Secondary / muted text
    'TEXT_FAINT':  '#666666',   # Labels, hints
    'ACCENT':     '#00a651',   # Green accent (matches status)
    'ACCENT_DIM': '#008040',   # Darker accent for hover
    'ACCENT_LT':  '#66bb6a',   # Lighter green for labels on dark
    'INFO':       '#42a5f5',   # Blue accent (calls, links)
    'INFO_LT':    '#90caf9',   # Lighter blue for labels on dark
    'TEAL':       '#2ABFBF',   # Teal accent (open, settings back)
    'DANGER':     '#e53935',   # Red / destructive
    'WARN':       '#e6af00',   # Yellow / busy
}
