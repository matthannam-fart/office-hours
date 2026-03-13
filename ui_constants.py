"""
ui_constants.py — Shared UI constants for Office Hours
Colour palettes, mode labels, dimensions used by both widgets and the panel.
"""

# ── Color Constants ──────────────────────────────────────────────
COLORS = {
    'GREEN':  '#00a651',
    'YELLOW': '#e6af00',
    'RED':    '#e22a1a',
    'INCOGNITO': '#555555',
}

MODE_LABELS = {
    'GREEN':  'Available',
    'YELLOW': 'Busy',
    'RED':    'DND',
}

RADIO_STATIONS = {
    'NTS Radio': 'https://stream-relay-geo.ntslive.net/stream?client=NTSRadio',
}

# Panel dimensions
PANEL_W = 310
SIDEBAR_W = 56
PANEL_RADIUS = 14

# Compact vertical strip dimensions
STRIP_W = 56
STRIP_AVATAR_SIZE = 36
STRIP_RADIUS = 12

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

# ── Light Theme Palette ──────────────────────────────────────────
LIGHT = {
    'BG':         '#f5f5f5',
    'BG_RAISED':  '#ffffff',
    'BG_HOVER':   '#e8e8e8',
    'BORDER':     '#d0d0d0',
    'BORDER_LT':  '#e0e0e0',
    'TEXT':        '#1e1e1e',
    'TEXT_DIM':    '#666666',
    'TEXT_FAINT':  '#999999',
    'ACCENT':     '#00a651',
    'ACCENT_DIM': '#008040',
    'ACCENT_LT':  '#2e7d32',
    'INFO':       '#1976d2',
    'INFO_LT':    '#1565c0',
    'TEAL':       '#00897b',
    'DANGER':     '#d32f2f',
    'WARN':       '#f9a825',
}
