#!/usr/bin/env python3
"""
preview.py — Lightweight UI preview for Office Hours.

Shows the FloatingPanel with mocked data so you can inspect layouts
without running the full app (no login, no network, no audio).

Usage:
    python preview.py              # shows 'welcome' page, lists available pages
    python preview.py welcome      # welcome / team-picker screen
    python preview.py users        # user list with dummy users
    python preview.py teams        # team management page
    python preview.py radio        # NTS radio player page
    python preview.py settings     # settings view
    python preview.py onboarding   # first-launch onboarding overlay
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from floating_panel import FloatingPanel

# ── Available pages ──────────────────────────────────────────────
PAGES = ["welcome", "users", "teams", "radio", "settings", "onboarding", "compact", "login"]

# ── Fake user data for the 'users' page ─────────────────────────
DUMMY_USERS = [
    {"id": "u1", "name": "Alice Chen", "mode": "GREEN", "has_message": False},
    {"id": "u2", "name": "Bob Marley", "mode": "YELLOW", "has_message": True},
    {"id": "u3", "name": "Carol Ng", "mode": "RED", "has_message": False},
    {"id": "u4", "name": "Dave Park", "mode": "GREEN", "has_message": False},
    {"id": "u5", "name": "Eve Torres", "mode": "OFFLINE", "has_message": False},
]

# ── Fake team data for welcome + teams pages ────────────────────
DUMMY_TEAMS = [
    {"id": "t1", "name": "Post Team Alpha", "invite_code": "OH-ABC123"},
    {"id": "t2", "name": "Sound Dept", "invite_code": "OH-XYZ789"},
]


def populate_panel(panel: FloatingPanel, page: str) -> None:
    """Feed the panel enough mock data to render the requested page."""

    # Give it a display name (used by pinned bar, settings, etc.)
    panel.set_display_name("Preview User")
    panel._active_team_name_cache = "Post Team Alpha"

    if page == "users":
        # Populate user list, show sidebar, switch to users page
        panel.set_users(DUMMY_USERS, selected_user_id="u1")
        panel._switch_page("users")
        panel.set_connection(True, peer_name="", peer_mode="GREEN")

    elif page == "teams":
        # Show the teams/lobby page with dummy teams
        panel._refresh_teams_list(DUMMY_TEAMS, active_team_id="t1")
        panel._switch_page("teams")

    elif page == "radio":
        # Show the radio player page
        panel._switch_page("radio")

    elif page == "settings":
        # Populate and show settings
        panel._settings_back_page = "welcome"
        panel._switch_page("settings")
        panel._populate_settings()

    elif page == "onboarding":
        # Show the onboarding overlay
        panel._onboarding.setVisible(True)
        panel._is_onboarding = True
        panel.setFixedWidth(280)

    elif page == "login":
        # Show the login/signup page
        panel._switch_page("login")

    elif page == "compact":
        # Show compact vertical strip mode
        panel.set_users(DUMMY_USERS, selected_user_id="u1")
        panel._switch_page("users")
        panel._toggle_pin()

    else:
        # Default: welcome page
        panel._switch_page("welcome")


def main() -> None:
    page = sys.argv[1].lower() if len(sys.argv) > 1 else "welcome"

    if page not in PAGES:
        print(f"Unknown page '{page}'. Available pages: {', '.join(PAGES)}")
        sys.exit(1)

    if len(sys.argv) <= 1:
        print("No page specified — showing 'welcome'.")
        print(f"Available pages: {', '.join(PAGES)}")
        print("Usage: python preview.py <page>")

    app = QApplication(sys.argv)

    panel = FloatingPanel()

    # Remove the Tool flag so it shows as a normal window (easier to find)
    panel.setWindowFlags(
        Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint  # type: ignore[arg-type]
    )
    panel.setWindowTitle(f"OH Preview — {page}")

    populate_panel(panel, page)

    # Show at a sensible position (center-ish of primary screen)
    screen = app.primaryScreen().availableGeometry()
    x = screen.center().x() - panel.width() // 2
    y = screen.center().y() - panel.height() // 2
    panel.move(QPoint(x, y))
    panel.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
