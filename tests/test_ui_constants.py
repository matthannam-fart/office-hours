"""Tests for ui_constants.py — color palettes and mode labels."""


def test_colors_defined():
    """All mode colors should be defined as hex strings."""
    from ui_constants import COLORS
    assert "GREEN" in COLORS
    assert "YELLOW" in COLORS
    assert "RED" in COLORS
    assert "INCOGNITO" in COLORS
    for color in COLORS.values():
        assert color.startswith("#")


def test_mode_labels_defined():
    """Mode labels should be defined for all active modes."""
    from ui_constants import MODE_LABELS
    assert MODE_LABELS["GREEN"] == "Available"
    assert MODE_LABELS["YELLOW"] == "Busy"
    assert MODE_LABELS["RED"] == "DND"


def test_dark_theme_has_required_keys():
    """Dark theme palette should have all required keys."""
    from ui_constants import DARK
    required = ['BG', 'BG_RAISED', 'BG_HOVER', 'BORDER', 'TEXT', 'TEXT_DIM', 'ACCENT', 'DANGER']
    for key in required:
        assert key in DARK, f"Missing key: {key}"
        assert DARK[key].startswith("#")


def test_light_theme_has_required_keys():
    """Light theme palette should have all required keys."""
    from ui_constants import LIGHT
    required = ['BG', 'BG_RAISED', 'BG_HOVER', 'BORDER', 'TEXT', 'TEXT_DIM', 'ACCENT', 'DANGER']
    for key in required:
        assert key in LIGHT, f"Missing key: {key}"
        assert LIGHT[key].startswith("#")


def test_dark_and_light_have_same_keys():
    """Both themes should define the same set of keys."""
    from ui_constants import DARK, LIGHT
    assert set(DARK.keys()) == set(LIGHT.keys())


def test_panel_dimensions():
    """Panel dimensions should be positive integers."""
    from ui_constants import PANEL_RADIUS, PANEL_W, SIDEBAR_W
    assert PANEL_W > 0
    assert SIDEBAR_W > 0
    assert PANEL_RADIUS > 0
    assert SIDEBAR_W < PANEL_W
