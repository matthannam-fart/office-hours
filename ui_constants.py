"""
ui_constants.py — Shared UI constants for Office Hours
Colour palettes, mode labels, dimensions used by both widgets and the panel.
"""

# ── Color Constants (from wireframe) ──────────────────────────────
COLORS = {
    'GREEN':  '#00a651',
    'YELLOW': '#e6af00',
    'RED':    '#e22a1a',
    'OPEN':   '#2ABFBF',
    'BUSY':   '#ff9800',
    'INCOGNITO': '#333333',
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
PANEL_W = 280
PANEL_RADIUS = 12
