"""
floating_panel.py — Office Hours Menu Bar Panel
Frameless popup widget anchored to the system tray icon.
Matches the wireframe at menubar_wireframe.html.
"""
import sys
import os
import json
import threading
from urllib.request import urlopen, Request
from urllib.error import URLError
from ctypes import c_void_p
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QSizePolicy, QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect, QSystemTrayIcon, QLineEdit, QSpacerItem, QSlider,
    QComboBox, QStackedWidget, QMenu
)
from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, Signal, Slot,
    QSize, QRect, QPoint, Property, QRectF
)
from PySide6.QtGui import (
    QFont, QColor, QPainter, QBrush, QRadialGradient, QPen,
    QPixmap, QIcon, QPainterPath, QFontDatabase, QAction
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtCore import QUrl

# Shared constants (extracted to ui_constants.py)
from ui_constants import COLORS, MODE_LABELS, RADIO_STATIONS, PANEL_W, PANEL_RADIUS, DARK, LIGHT, SIDEBAR_W

# Snapshot the original dark palette so we can restore it after switching to light
_DARK_ORIGINAL = dict(DARK)

# Widget classes (extracted to widgets.py)
from widgets import GlowingOrb, LevelMeter, UnicodeEQ, SmallOrb, UserRow, ToggleSwitch, NavButton

# ── Font Loading ─────────────────────────────────────────────────
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'commercial-type-2507-JRGALW-desktop')
FONT_FAMILY = 'Focal'
# Fallback chain for systems without the bundled font
FONT_FALLBACK = 'SF Pro Display, -apple-system, Helvetica Neue, Arial'

def _load_fonts():
    """Load bundled Focal font family into Qt.
    Returns True if at least one font loaded successfully."""
    if not os.path.isdir(FONT_DIR):
        return False
    loaded = False
    for fname in sorted(os.listdir(FONT_DIR)):
        if fname.endswith(('.otf', '.ttf')):
            fpath = os.path.join(FONT_DIR, fname)
            font_id = QFontDatabase.addApplicationFont(fpath)
            if font_id >= 0:
                loaded = True
            else:
                print(f"Warning: could not load font {fname}")
    return loaded

_fonts_loaded = False


# ═══════════════════════════════════════════════════════════════════
#  The Main Floating Panel
# ═══════════════════════════════════════════════════════════════════
class FloatingPanel(QWidget):
    """Menu bar dropdown panel matching the wireframe."""

    # Signals to communicate back to IntercomApp
    mode_cycle_requested = Signal()
    hotline_toggled = Signal(bool)
    pin_toggled = Signal(bool)
    ptt_pressed = Signal()
    ptt_released = Signal()
    page_all_pressed = Signal()
    page_all_released = Signal()
    call_user_requested = Signal(str)     # user_id (legacy)
    intercom_pressed = Signal(str)        # user_id — hold to talk
    intercom_released = Signal(str)       # user_id — release to stop
    user_selected = Signal(str)           # user_id — click to select as PTT target
    leave_requested = Signal()
    accept_call_requested = Signal()
    decline_call_requested = Signal()
    end_call_requested = Signal()
    cancel_call_requested = Signal()
    incognito_toggled = Signal(bool)
    dark_mode_toggled = Signal(bool)
    quit_requested = Signal()
    play_message_requested = Signal()
    audio_input_changed = Signal(object)   # device index or None
    audio_output_changed = Signal(object)  # device index or None
    name_change_requested = Signal(str)    # new display name
    team_changed = Signal(str)             # team_id
    create_team_requested = Signal(str)    # team_name
    manage_team_requested = Signal()       # open team management
    join_code_requested = Signal(str)      # invite_code — user wants to join a team
    leave_team_requested = Signal()        # user wants to leave current team
    mode_set_requested = Signal(str)       # direct mode selection (GREEN/YELLOW/RED)
    request_to_join = Signal(str, str, str)   # team_id, team_name, admin_id (lobby join request)
    join_request_accepted = Signal(str)        # request_id — admin accepted a join request
    join_request_declined = Signal(str, str)   # request_id, requester_id — admin declined
    team_selected_from_lobby = Signal(str, str)  # team_id, team_name — user picked existing team

    def __init__(self, parent=None):
        super().__init__(parent)
        # Load fonts once (must happen after QApplication exists)
        global _fonts_loaded
        if not _fonts_loaded:
            _fonts_loaded = _load_fonts()
        # Set panel font — Focal if available, else system fallback
        font_family = FONT_FAMILY if _fonts_loaded else FONT_FALLBACK.split(',')[0].strip()
        self.setFont(QFont(font_family, 11))
        self._pinned = False
        self._connected = False
        self._current_mode = 'GREEN'
        self._is_open_line = False
        self._incognito = False
        self._dark_mode = True
        self._is_onboarding = False  # True while onboarding is shown
        self._radio_station = None  # currently playing station name
        self._user_rows = {}  # uid -> UserRow (initialized before set_users can be called)

        # Radio player
        self._audio_output = QAudioOutput()
        self._audio_output.setVolume(0.2)
        self._radio_player = QMediaPlayer()
        self._radio_player.setAudioOutput(self._audio_output)

        import sys
        if sys.platform == 'win32':
            # On Windows: use Qt.Window so taskbar works, skip Qt.Tool
            self.setWindowFlags(
                Qt.Window |
                Qt.FramelessWindowHint |
                Qt.WindowStaysOnTopHint
            )
            # WA_TranslucentBackground is unreliable on Windows without DWM hooks
            self.setStyleSheet(f"FloatingPanel {{ background-color: {DARK['BG']}; }}")
        else:
            self.setWindowFlags(
                Qt.FramelessWindowHint |
                Qt.WindowStaysOnTopHint |
                Qt.Tool  # Don't show in dock/taskbar (macOS has visible tray)
            )
            self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(PANEL_W)

        self._build_ui()
        self._vibrancy_applied = False

    def _apply_vibrancy(self):
        """Make the native macOS window background transparent
        so our semi-transparent Qt frame shows the desktop through."""
        if self._vibrancy_applied or sys.platform != 'darwin':
            return
        try:
            import objc
            from AppKit import NSColor

            ns_view = objc.objc_object(c_void_p=int(self.winId()))
            ns_window = ns_view.window()
            if ns_window:
                ns_window.setBackgroundColor_(NSColor.clearColor())
                ns_window.setOpaque_(False)
                ns_window.invalidateShadow()
                self._vibrancy_applied = True
        except Exception as e:
            print(f"Transparency fallback: {e}")

    def _build_ui(self):
        # Main layout inside a styled frame
        self._frame = QFrame(self)
        self._frame.setObjectName("panelFrame")
        self._frame.setStyleSheet(f"""
            #panelFrame {{
                background: {DARK['BG']};
                border: 1px solid {DARK['BORDER']};
                border-radius: {PANEL_RADIUS}px;
            }}
        """)

        self._notch_h = 7  # Height of the notch triangle

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, self._notch_h, 0, 9)
        outer.addWidget(self._frame)

        # Drop shadow — subtle on dark
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 80))
        self._frame.setGraphicsEffect(shadow)

        # ── Top-level: horizontal split (sidebar | content) ───
        frame_layout = QHBoxLayout(self._frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)

        # ── LEFT: Sidebar ─────────────────────────────────────
        self._sidebar = self._build_sidebar()
        frame_layout.addWidget(self._sidebar)

        # ── RIGHT: Content area ───────────────────────────────
        content_frame = QFrame()
        content_frame.setObjectName("content_frame")
        content_frame.setStyleSheet(f"border-left: 1px solid {DARK['BORDER']};")
        self._content_layout = QVBoxLayout(content_frame)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)

        # Banners (connection, calls) sit above the stacked content
        self._outgoing_banner = self._build_outgoing_banner()
        self._outgoing_banner.setVisible(False)
        self._content_layout.addWidget(self._outgoing_banner)

        self._incoming_banner = self._build_incoming_banner()
        self._incoming_banner.setVisible(False)
        self._content_layout.addWidget(self._incoming_banner)

        self._call_banner = self._build_call_banner()
        self._call_banner.setVisible(False)
        self._content_layout.addWidget(self._call_banner)

        self._message_banner = self._build_message_banner()
        self._message_banner.setVisible(False)
        self._content_layout.addWidget(self._message_banner)

        self._conn_bar = self._build_conn_bar()
        self._conn_bar.setVisible(False)
        self._content_layout.addWidget(self._conn_bar)

        self._disconn_bar = self._build_disconn_bar()
        self._disconn_bar.setVisible(False)
        self._content_layout.addWidget(self._disconn_bar)

        # ── Content header (search + section title) ───────────
        self._content_header = self._build_content_header()
        self._content_layout.addWidget(self._content_header)

        # ── Stacked content pages ─────────────────────────────
        self._content_stack = QStackedWidget()

        # Page 0: Users
        self._users_page = QWidget()
        users_v = QVBoxLayout(self._users_page)
        users_v.setContentsMargins(0, 0, 0, 0)
        users_v.setSpacing(0)
        self._user_section = self._build_user_section()
        users_v.addWidget(self._user_section, 1)
        self._content_stack.addWidget(self._users_page)

        # Page 1: Teams
        self._teams_page = self._build_teams_page()
        self._content_stack.addWidget(self._teams_page)

        # Page 2: Radio
        self._radio_page = self._build_radio_page()
        self._content_stack.addWidget(self._radio_page)

        # Page 3: Settings
        self._settings_view = self._build_settings_view()
        self._content_stack.addWidget(self._settings_view)

        self._content_layout.addWidget(self._content_stack, 1)

        # ── Status bar at bottom of content ───────────────────
        self._status_bar = self._build_status_bar()
        self._content_layout.addWidget(self._status_bar)

        frame_layout.addWidget(content_frame, 1)

        # ── Onboarding overlay (hidden, covers entire panel) ──
        self._onboarding = self._build_onboarding(self._frame)
        self._onboarding.setVisible(False)

        # ── Pinned compact (hidden by default) ────────────────
        self._pinned_compact = self._build_pinned_compact(self._frame)
        self._pinned_compact.setVisible(False)

        # Legacy compat: _header reference (used by _toggle_pin etc.)
        self._header = self._sidebar
        # Team bar reference (used by set_teams, _auto_resize, etc.)
        self._team_bar = QWidget()  # Dummy — teams now in teams_page
        self._team_bar.setVisible(False)

        # Store layout refs
        self._root = self._content_layout
        self._content_frame = content_frame

        # Default to Users page
        self._active_nav = "teams"
        self._switch_page("teams")

    # ── Sidebar ─────────────────────────────────────────────────────
    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(SIDEBAR_W)
        sidebar.setStyleSheet(f"""
            QFrame#sidebar {{
                background: {DARK['BG']};
                border: none;
                border-top-left-radius: {PANEL_RADIUS}px;
                border-bottom-left-radius: {PANEL_RADIUS}px;
            }}
        """)

        v = QVBoxLayout(sidebar)
        v.setContentsMargins(0, 8, 0, 8)
        v.setSpacing(2)

        # Navigation buttons — Team first (entry point)
        self._nav_teams = NavButton("teams", "📁", "LOBBY")
        self._nav_teams.clicked.connect(self._on_nav_clicked)
        v.addWidget(self._nav_teams)

        self._nav_users = NavButton("users", "👥", "USERS")
        self._nav_users.clicked.connect(self._on_nav_clicked)
        v.addWidget(self._nav_users)

        self._nav_radio = NavButton("radio", "📻", "RADIO")
        self._nav_radio.clicked.connect(self._on_nav_clicked)
        v.addWidget(self._nav_radio)

        self._nav_settings = NavButton("settings", "", "")
        # Use a QPixmap gear icon instead of emoji (emojis don't scale on macOS)
        gear_pixmap = QPixmap(36, 36)
        gear_pixmap.fill(QColor(0, 0, 0, 0))
        gp = QPainter(gear_pixmap)
        gp.setRenderHint(QPainter.Antialiasing)
        gf = QFont()
        gf.setPixelSize(34)
        gp.setFont(gf)
        gp.setPen(QColor(DARK['TEXT_DIM']))
        gp.drawText(QRect(0, 0, 36, 36), Qt.AlignCenter, "⚙")
        gp.end()
        self._nav_settings._icon.setPixmap(gear_pixmap)
        self._nav_settings._icon.setFixedSize(36, 36)
        self._nav_settings.setFixedSize(56, 56)
        self._nav_settings.clicked.connect(self._on_nav_clicked)
        v.addWidget(self._nav_settings)

        self._nav_buttons = {
            "teams": self._nav_teams,
            "users": self._nav_users,
            "radio": self._nav_radio,
            "settings": self._nav_settings,
        }

        v.addStretch()

        # ── Info section above orb ──

        # Active team name
        self._sidebar_team_label = QLabel("")
        self._sidebar_team_label.setAlignment(Qt.AlignCenter)
        self._sidebar_team_label.setWordWrap(True)
        self._sidebar_team_label.setStyleSheet(f"""
            font-size: 9px; font-weight: 600; color: {DARK['TEXT_FAINT']};
            border: none; padding: 0 4px;
            letter-spacing: 0.5px;
        """)
        self._sidebar_team_label.setFixedWidth(SIDEBAR_W)
        v.addWidget(self._sidebar_team_label)

        # User initials (circular badge — shows local user's initials)
        self._sidebar_user_initials = QLabel("")
        self._sidebar_user_initials.setAlignment(Qt.AlignCenter)
        self._sidebar_user_initials.setFixedSize(36, 36)
        self._sidebar_user_initials.setStyleSheet(f"""
            background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
            border-radius: 18px; font-size: 14px; font-weight: 700;
            color: {DARK['TEXT_DIM']};
        """)
        user_container = QHBoxLayout()
        user_container.setContentsMargins(0, 4, 0, 2)
        user_container.setAlignment(Qt.AlignCenter)
        user_container.addWidget(self._sidebar_user_initials)
        v.addLayout(user_container)

        # Status dropdown button (shows current mode, click for menu)
        self._sidebar_status_btn = QPushButton("Available ▾")
        self._sidebar_status_btn.setCursor(Qt.PointingHandCursor)
        self._sidebar_status_btn.setFixedSize(SIDEBAR_W - 8, 24)
        self._sidebar_status_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 9px; font-weight: 600;
                color: #4cdf80; background: rgba(0, 166, 81, 0.10);
                border: 1px solid rgba(0, 166, 81, 0.30);
                border-radius: 6px; padding: 2px 4px;
            }}
            QPushButton:hover {{ background: rgba(0, 166, 81, 0.20); }}
            QPushButton::menu-indicator {{ width: 0; height: 0; }}
        """)
        # Build the mode selection menu
        self._status_menu = QMenu(self)
        self._status_menu.setStyleSheet(f"""
            QMenu {{
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 6px; padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 16px; font-size: 12px; color: {DARK['TEXT']};
                border-radius: 4px;
            }}
            QMenu::item:selected {{ background: {DARK['BG_HOVER']}; }}
        """)
        for mode_key, label in [("GREEN", "Available"), ("YELLOW", "Busy"), ("RED", "DND")]:
            action = self._status_menu.addAction(f"● {label}")
            color = COLORS[mode_key]
            action.setData(mode_key)
            action.triggered.connect(lambda checked=False, mk=mode_key: self.mode_set_requested.emit(mk))
        self._sidebar_status_btn.setMenu(self._status_menu)
        status_container = QHBoxLayout()
        status_container.setContentsMargins(4, 2, 4, 2)
        status_container.setAlignment(Qt.AlignCenter)
        status_container.addWidget(self._sidebar_status_btn)
        v.addLayout(status_container)

        # Mode orb at bottom of sidebar (large)
        self._sidebar_orb = GlowingOrb(28)
        orb_container = QHBoxLayout()
        orb_container.setContentsMargins(0, 4, 0, 6)
        orb_container.setAlignment(Qt.AlignCenter)
        orb_container.addWidget(self._sidebar_orb)
        v.addLayout(orb_container)

        return sidebar

    def _on_nav_clicked(self, key):
        """Handle sidebar nav button clicks."""
        self._switch_page(key)

    def _switch_page(self, key):
        """Switch the content area to show the selected page."""
        self._active_nav = key
        # Update nav button states
        for k, btn in self._nav_buttons.items():
            btn.set_selected(k == key)
        # Switch stacked widget
        page_map = {"users": 0, "teams": 1, "radio": 2, "settings": 3}
        self._content_stack.setCurrentIndex(page_map.get(key, 0))
        # Update section title
        self._section_title.setText(key.upper())
        # Hide search bar and hotline toggle on pages where they don't apply
        show_search = key in ("users", "teams")
        self._hotline_lbl.setVisible(show_search)
        self.open_toggle.setVisible(show_search)
        # Populate settings when navigating to it
        if key == "settings":
            self._populate_settings()
        # Auto-play radio when navigating to it
        if key == "radio":
            self.start_radio_on_nav()
        self._auto_resize()

    # ── Content Header (search + section title) ───────────────────
    def _build_content_header(self):
        header = QFrame()
        header.setStyleSheet("border: none;")

        v = QVBoxLayout(header)
        v.setContentsMargins(12, 10, 12, 0)
        v.setSpacing(8)

        # Section title with accent underline
        title_row = QHBoxLayout()
        title_row.setContentsMargins(2, 4, 0, 0)
        self._section_title = QLabel("USERS")
        self._section_title.setStyleSheet(f"""
            font-size: 16px; font-weight: 800; color: {DARK['TEXT']};
            letter-spacing: 1px; border: none; padding-bottom: 4px;
            border-bottom: 2px solid {DARK['ACCENT']};
        """)
        title_row.addWidget(self._section_title)
        title_row.addStretch()

        # Hotline toggle (moved from old header)
        self._hotline_lbl = QLabel("Hotline")
        self._hotline_lbl.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_DIM']}; font-weight: 500; border: none;")
        title_row.addWidget(self._hotline_lbl)

        self.open_toggle = ToggleSwitch()
        self.open_toggle.toggled.connect(self.hotline_toggled.emit)
        title_row.addWidget(self.open_toggle)

        v.addLayout(title_row)

        return header

    # ── Teams Page ────────────────────────────────────────────────
    def _build_teams_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Scrollable team list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        container = QWidget()
        self._teams_list_layout = QVBoxLayout(container)
        self._teams_list_layout.setContentsMargins(10, 6, 10, 6)
        self._teams_list_layout.setSpacing(4)
        self._teams_list_layout.addStretch()

        scroll.setWidget(container)
        v.addWidget(scroll, 1)

        # ── Bottom actions ──
        actions = QFrame()
        actions.setStyleSheet(f"border: none; background: transparent;")
        af = QVBoxLayout(actions)
        af.setContentsMargins(10, 6, 10, 10)
        af.setSpacing(6)

        # Hidden combo used for team switching logic (visible combo is in sidebar)
        self._team_combo = QComboBox()
        self._team_combo.setVisible(False)
        self._team_combo.currentIndexChanged.connect(self._on_team_combo_changed)
        af.addWidget(self._team_combo)

        # Hidden labels kept for compatibility
        self._invite_code_lbl = QLabel("")
        self._invite_code_lbl.setVisible(False)
        af.addWidget(self._invite_code_lbl)
        self._team_manage_btn = QPushButton()
        self._team_manage_btn.setVisible(False)
        af.addWidget(self._team_manage_btn)

        # Create Team button
        create_btn = QPushButton("+ Create Team")
        create_btn.setCursor(Qt.PointingHandCursor)
        create_btn.setStyleSheet(f"""
            QPushButton {{
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 8px; padding: 10px; font-size: 13px;
                font-weight: 500; color: {DARK['TEXT']};
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; border-color: {DARK['TEXT_FAINT']}; }}
        """)
        create_btn.clicked.connect(self._on_create_team_click)
        af.addWidget(create_btn)

        # Leave Team button
        self._lobby_leave_btn = QPushButton("Leave Team")
        self._lobby_leave_btn.setCursor(Qt.PointingHandCursor)
        self._lobby_leave_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: 1px solid rgba(229,57,53,0.3);
                border-radius: 8px; padding: 10px; font-size: 13px;
                font-weight: 500; color: {DARK['DANGER']};
            }}
            QPushButton:hover {{ background: rgba(229,57,53,0.08); }}
        """)
        self._lobby_leave_btn.clicked.connect(self.leave_team_requested.emit)
        self._lobby_leave_btn.setVisible(False)  # shown only when a team is active
        af.addWidget(self._lobby_leave_btn)

        v.addWidget(actions)
        return page

    def _refresh_teams_list(self, teams, active_team_id=""):
        """Rebuild the visual team list on the Teams/Lobby page."""
        # Clear existing rows (keep the stretch at the end)
        while self._teams_list_layout.count() > 1:
            item = self._teams_list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not teams:
            empty = QLabel("No teams yet")
            empty.setStyleSheet(f"font-size: 12px; color: {DARK['TEXT_DIM']}; padding: 20px;")
            empty.setAlignment(Qt.AlignCenter)
            self._teams_list_layout.insertWidget(0, empty)
            self._lobby_leave_btn.setVisible(False)
            return

        has_active = False
        for team in teams:
            is_active = (team["id"] == active_team_id)
            if is_active:
                has_active = True

            row = QFrame()
            if is_active:
                row.setStyleSheet(f"""
                    QFrame {{
                        background: rgba(0, 166, 81, 0.08);
                        border: 1px solid rgba(0, 166, 81, 0.30);
                        border-radius: 8px;
                    }}
                """)
            else:
                row.setStyleSheet(f"""
                    QFrame {{
                        background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                        border-radius: 8px;
                    }}
                    QFrame:hover {{ background: {DARK['BG_HOVER']}; border-color: {DARK['TEXT_FAINT']}; }}
                """)

            h = QHBoxLayout(row)
            h.setContentsMargins(10, 8, 10, 8)
            h.setSpacing(8)

            name_lbl = QLabel(team["name"])
            name_lbl.setStyleSheet(
                f"font-size: 13px; font-weight: 600; border: none; color: "
                f"{COLORS['GREEN'] if is_active else DARK['TEXT']};"
            )
            h.addWidget(name_lbl, 1)

            if is_active:
                invite_code = team.get("invite_code", "")
                if invite_code:
                    code_btn = QPushButton(f"📋 {invite_code}")
                    code_btn.setCursor(Qt.PointingHandCursor)
                    code_btn.setToolTip("Click to copy invite code")
                    code_btn.setStyleSheet(f"""
                        QPushButton {{
                            background: transparent; border: 1px solid {DARK['BORDER']};
                            border-radius: 4px; font-size: 10px; color: {DARK['TEXT_DIM']};
                            padding: 2px 6px; font-family: monospace;
                        }}
                        QPushButton:hover {{ color: {DARK['TEXT']}; border-color: {DARK['TEXT_FAINT']}; }}
                    """)
                    code_btn.clicked.connect(
                        lambda checked=False, c=invite_code: self._copy_code(c)
                    )
                    h.addWidget(code_btn)
                check = QLabel("✓")
                check.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {COLORS['GREEN']}; border: none;")
                h.addWidget(check)
            else:
                select_btn = QPushButton("Select")
                select_btn.setCursor(Qt.PointingHandCursor)
                select_btn.setFixedSize(54, 26)
                select_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {COLORS['GREEN']}; color: white; border: none;
                        border-radius: 4px; font-size: 10px; font-weight: 700;
                    }}
                    QPushButton:hover {{ background: #2bbd6e; }}
                """)
                team_id = team["id"]
                team_name = team["name"]
                select_btn.clicked.connect(
                    lambda checked=False, tid=team_id, tn=team_name:
                        self._on_team_page_select(tid, tn)
                )
                h.addWidget(select_btn)

            self._teams_list_layout.insertWidget(self._teams_list_layout.count() - 1, row)

        self._lobby_leave_btn.setVisible(has_active)

    def _on_team_page_select(self, team_id, team_name):
        """User clicked Select on a team in the lobby page."""
        self.team_selected_from_lobby.emit(team_id, team_name)

    # ── Status Bar (bottom of content) — now houses PTT ──────────
    def _build_status_bar(self):
        bar = QFrame()
        bar.setStyleSheet(f"border: none; background: transparent;")
        bar.setFixedHeight(60)

        v = QVBoxLayout(bar)
        v.setContentsMargins(8, 4, 8, 6)
        v.setSpacing(4)

        # Horizontal row: PTT (5/6) + Page All (1/6)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        # PTT button — full-width, mode-colored
        self.ptt_btn = QPushButton("●  Hold to Talk")
        self.ptt_btn.setCursor(Qt.PointingHandCursor)
        self.ptt_btn.setFixedHeight(36)
        self.ptt_btn.pressed.connect(self.ptt_pressed.emit)
        self.ptt_btn.released.connect(self.ptt_released.emit)
        self.ptt_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        btn_row.addWidget(self.ptt_btn, 5)

        # Page All button
        self.page_all_btn = QPushButton("Page All")
        self.page_all_btn.setCursor(Qt.PointingHandCursor)
        self.page_all_btn.setFixedWidth(62)
        self.page_all_btn.setFixedHeight(36)
        self.page_all_btn.setStyleSheet(f"""
            QPushButton {{
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 8px; padding: 6px; font-size: 11px;
                font-weight: 600; color: {DARK['TEXT_FAINT']};
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; color: {DARK['TEXT_DIM']}; }}
            QPushButton:pressed {{ background: {DARK['BG']}; }}
        """)
        self.page_all_btn.pressed.connect(self.page_all_pressed.emit)
        self.page_all_btn.released.connect(self.page_all_released.emit)
        btn_row.addWidget(self.page_all_btn, 1)

        v.addLayout(btn_row)

        # Hotline mode label below PTT
        self.ptt_mode_label = QLabel("Hotline — open mic, same-room feel.")
        self.ptt_mode_label.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_FAINT']}; border: none;")
        self.ptt_mode_label.setAlignment(Qt.AlignCenter)
        self.ptt_mode_label.setVisible(False)
        v.addWidget(self.ptt_mode_label)

        # Legacy: hidden mode_btn (some code references it)
        self.mode_btn = QPushButton()
        self.mode_btn.setFixedSize(0, 0)
        self.mode_btn.setVisible(False)
        self.mode_btn.clicked.connect(self.mode_cycle_requested.emit)

        # Hidden menu button (triggered from settings page)
        self.menu_btn = QPushButton()
        self.menu_btn.setFixedSize(0, 0)
        self.menu_btn.setVisible(False)
        self.menu_btn.clicked.connect(self._show_hamburger_menu)

        # Hidden pin button (used programmatically)
        self.pin_btn = QPushButton()
        self.pin_btn.setFixedSize(0, 0)
        self.pin_btn.setVisible(False)
        self._update_pin_style(False)

        self._update_ptt_style()

        return bar

    # ── Header (LEGACY — no longer used, widgets moved to status bar + content header) ──
    def _build_header(self):
        """Legacy stub — header widgets now live in _build_status_bar and _build_content_header."""
        header = QFrame()
        header.setFixedHeight(0)
        header.setVisible(False)
        return header

    def _update_pin_style(self, pinned):
        if pinned:
            self.pin_btn.setText("v")
            self.pin_btn.setStyleSheet("""
                QPushButton {
                    border: none; background: rgba(255,152,0,0.15);
                    font-size: 16px; font-weight: 900; color: #ffb74d;
                    border-radius: 6px;
                }
                QPushButton:hover { background: rgba(255,152,0,0.25); }
            """)
        else:
            self.pin_btn.setText("^")
            self.pin_btn.setStyleSheet("""
                QPushButton {
                    border: none; background: rgba(255,152,0,0.10);
                    font-size: 16px; font-weight: 900; color: #ff9800;
                    border-radius: 6px;
                }
                QPushButton:hover { background: rgba(255,152,0,0.18); color: #ffb74d; }
            """)

    # Mode color maps (shared by PTT bar and sidebar status)
    _MODE_TEXT_COLORS = {
        'GREEN': '#4cdf80', 'YELLOW': '#f0c040',
        'RED': '#f06060', 'OPEN': '#4cd8d8'
    }
    _MODE_BG_COLORS = {
        'GREEN': 'rgba(0, 166, 81, 0.10)',
        'YELLOW': 'rgba(230, 175, 0, 0.10)',
        'RED': 'rgba(229, 57, 53, 0.10)',
        'OPEN': 'rgba(42, 191, 191, 0.10)',
    }
    _MODE_BORDER_COLORS = {
        'GREEN': 'rgba(0, 166, 81, 0.30)',
        'YELLOW': 'rgba(230, 175, 0, 0.30)',
        'RED': 'rgba(229, 57, 53, 0.30)',
        'OPEN': 'rgba(42, 191, 191, 0.30)',
    }

    def _update_mode_btn(self):
        """Update the sidebar status button to reflect current mode."""
        mode = self._current_mode
        text_color = self._MODE_TEXT_COLORS.get(mode, '#4cdf80')
        bg_color = self._MODE_BG_COLORS.get(mode, 'rgba(0, 166, 81, 0.10)')
        border_color = self._MODE_BORDER_COLORS.get(mode, 'rgba(0, 166, 81, 0.30)')
        label = MODE_LABELS.get(mode, 'Available')

        self._sidebar_status_btn.setText(f"{label} ▾")
        self._sidebar_status_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 9px; font-weight: 600;
                color: {text_color}; background: {bg_color};
                border: 1px solid {border_color};
                border-radius: 6px; padding: 2px 4px;
            }}
            QPushButton:hover {{ background: {bg_color.replace('0.10', '0.20')}; }}
            QPushButton::menu-indicator {{ width: 0; height: 0; }}
        """)

    def _update_ptt_style(self):
        """Style the PTT button with mode-colored tint."""
        mode = self._current_mode
        text_color = self._MODE_TEXT_COLORS.get(mode, '#4cdf80')
        bg_color = self._MODE_BG_COLORS.get(mode, 'rgba(0, 166, 81, 0.10)')
        border_color = self._MODE_BORDER_COLORS.get(mode, 'rgba(0, 166, 81, 0.30)')

        if mode == 'RED':
            # DND — disabled look
            self.ptt_btn.setEnabled(False)
            self.ptt_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {bg_color}; border: 1px solid {border_color};
                    border-radius: 8px; padding: 8px; font-size: 13px;
                    font-weight: 600; color: {DARK['TEXT_FAINT']};
                }}
            """)
        else:
            self.ptt_btn.setEnabled(True)
            self.ptt_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {bg_color}; border: 1px solid {border_color};
                    border-radius: 8px; padding: 8px; font-size: 13px;
                    font-weight: 600; color: {text_color};
                }}
                QPushButton:hover {{ background: {DARK['BG_HOVER']}; border-color: {text_color}; }}
                QPushButton:pressed {{ background: {DARK['BG']}; }}
            """)

    # ── Connection Bar ────────────────────────────────────────────
    def _build_conn_bar(self):
        bar = QFrame()
        bar.setStyleSheet(f"background: {DARK['BG_RAISED']}; border-bottom: 1px solid {DARK['BORDER']};")
        bar.setFixedHeight(42)

        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 12, 0)
        h.setSpacing(8)

        # Green dot
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {COLORS['GREEN']}; font-size: 10px; border: none;")
        h.addWidget(dot)

        # Connection label
        self.conn_label = QLabel("Connected")
        self.conn_label.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {DARK['TEXT']}; letter-spacing: 0.5px; border: none;")
        h.addWidget(self.conn_label, 1)

        # Leave button
        self.leave_btn = QPushButton("Leave")
        self.leave_btn.setCursor(Qt.PointingHandCursor)
        self.leave_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 11px; font-weight: 700; color: {DARK['DANGER']};
                background: transparent; border: 1px solid rgba(229,57,53,0.3);
                border-radius: 8px; padding: 3px 10px;
            }}
            QPushButton:hover {{ background: rgba(229,57,53,0.12); }}
        """)
        self.leave_btn.clicked.connect(self.leave_requested.emit)
        h.addWidget(self.leave_btn)

        # Quit button
        gear = QPushButton("✕")
        gear.setFixedSize(24, 24)
        gear.setToolTip("Quit Office Hours")
        gear.setStyleSheet(f"""
            QPushButton {{
                border: none; background: transparent; font-size: 13px;
                color: {DARK['TEXT_FAINT']}; border-radius: 4px;
            }}
            QPushButton:hover {{ color: {DARK['DANGER']}; background: rgba(229,57,53,0.10); }}
        """)
        gear.clicked.connect(self.quit_requested.emit)
        h.addWidget(gear)

        return bar

    # ── Disconnected Bar ──────────────────────────────────────────
    def _build_disconn_bar(self):
        bar = QFrame()
        bar.setStyleSheet(f"background: transparent; border-bottom: 1px solid {DARK['BORDER']};")
        bar.setFixedHeight(38)

        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 6, 16, 6)

        # Status label
        self._disconn_label = QLabel("Connecting...")
        self._disconn_label.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_FAINT']}; font-weight: 500;")
        h.addWidget(self._disconn_label, 1)

        return bar

    # ── Team Selector Bar ────────────────────────────────────────
    def _build_team_bar(self):
        bar = QFrame()
        bar.setStyleSheet("border: none;")
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 6, 14, 2)
        h.setSpacing(6)

        lbl = QLabel("TEAM")
        lbl.setStyleSheet(f"font-size: 10px; font-weight: 700; color: {DARK['TEXT_FAINT']}; letter-spacing: 1px;")
        h.addWidget(lbl)

        self._team_combo = QComboBox()
        self._team_combo.setStyleSheet(f"""
            QComboBox {{
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 6px; padding: 3px 8px; font-size: 12px;
                color: {DARK['TEXT']}; min-width: 120px;
            }}
            QComboBox::drop-down {{
                border: none; width: 20px;
            }}
            QComboBox QAbstractItemView {{
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 4px; selection-background-color: {DARK['BG_HOVER']};
                color: {DARK['TEXT']};
            }}
        """)
        self._team_combo.currentIndexChanged.connect(self._on_team_combo_changed)
        h.addWidget(self._team_combo, 1)

        # Manage button (gear icon) — shows team info for all members
        self._team_manage_btn = QPushButton("⚙")
        self._team_manage_btn.setFixedSize(28, 28)
        self._team_manage_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none; font-size: 18px; color: {DARK['TEXT_DIM']};
            }}
            QPushButton:hover {{ color: {DARK['TEXT']}; }}
        """)
        self._team_manage_btn.setToolTip("Team Info")
        self._team_manage_btn.clicked.connect(self.manage_team_requested.emit)
        self._team_manage_btn.setVisible(False)
        h.addWidget(self._team_manage_btn)

        # + button to create a new team
        add_btn = QPushButton("+")
        add_btn.setFixedSize(24, 24)
        add_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: 1px solid {DARK['BORDER']};
                border-radius: 12px; font-size: 16px; font-weight: bold;
                color: {DARK['TEXT_DIM']};
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; color: {DARK['TEXT']}; }}
        """)
        add_btn.setToolTip("Create Team")
        add_btn.clicked.connect(self._on_create_team_click)
        h.addWidget(add_btn)

        return bar

    # ── Onboarding (no teams yet) ────────────────────────────
    def _build_onboarding(self, parent_widget=None):
        """First-launch screen: set your name, browse available teams, or create/join with code."""
        frame = QFrame(parent_widget)
        frame.setStyleSheet(f"background-color: {DARK['BG']}; border: none; border-radius: {PANEL_RADIUS}px;")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(0)

        # Title
        title = QLabel("Welcome to Office Hours")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {DARK['TEXT']};")
        layout.addWidget(title)
        layout.addSpacing(4)

        subtitle = QLabel("Set your name and pick a team.")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"font-size: 12px; color: {DARK['TEXT_DIM']};")
        layout.addWidget(subtitle)
        layout.addSpacing(12)

        # ── Name input ──
        name_lbl = QLabel("YOUR NAME")
        name_lbl.setStyleSheet(f"font-size: 9px; font-weight: 700; color: {DARK['TEXT_FAINT']}; letter-spacing: 1.5px;")
        layout.addWidget(name_lbl)
        layout.addSpacing(3)

        self._onboarding_name_input = QLineEdit()
        self._onboarding_name_input.setPlaceholderText("First Last")
        self._onboarding_name_input.setStyleSheet(f"""
            QLineEdit {{
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 6px; padding: 7px 10px; font-size: 12px;
                color: {DARK['TEXT']};
            }}
            QLineEdit:focus {{ border-color: {DARK['TEXT_FAINT']}; }}
        """)
        self._onboarding_name_input.setMaxLength(30)
        layout.addWidget(self._onboarding_name_input)
        layout.addSpacing(10)

        # ── Your Teams ──
        teams_lbl = QLabel("YOUR TEAMS")
        teams_lbl.setStyleSheet(f"font-size: 9px; font-weight: 700; color: {DARK['TEXT_FAINT']}; letter-spacing: 1.5px;")
        layout.addWidget(teams_lbl)
        layout.addSpacing(3)

        # Scrollable team list
        team_scroll = QScrollArea()
        team_scroll.setWidgetResizable(True)
        team_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        team_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        team_scroll.setFixedHeight(100)
        team_scroll.setStyleSheet(f"""
            QScrollArea {{ border: 1px solid {DARK['BORDER']}; border-radius: 6px; background: {DARK['BG_RAISED']}; }}
            QScrollBar:vertical {{ width: 4px; background: transparent; }}
            QScrollBar::handle:vertical {{ background: {DARK['BORDER']}; border-radius: 2px; min-height: 20px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        self._lobby_container = QWidget()
        self._lobby_layout = QVBoxLayout(self._lobby_container)
        self._lobby_layout.setContentsMargins(6, 4, 6, 4)
        self._lobby_layout.setSpacing(2)

        # Placeholder while loading
        self._lobby_empty_label = QLabel("Loading teams...")
        self._lobby_empty_label.setAlignment(Qt.AlignCenter)
        self._lobby_empty_label.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_FAINT']}; padding: 16px;")
        self._lobby_layout.addWidget(self._lobby_empty_label)
        self._lobby_layout.addStretch()

        team_scroll.setWidget(self._lobby_container)
        layout.addWidget(team_scroll)
        layout.addSpacing(8)

        # Create team button
        create_btn = QPushButton("+ Create a New Team")
        create_btn.setCursor(Qt.PointingHandCursor)
        create_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {DARK['TEXT_DIM']};
                border: 1px solid {DARK['BORDER']}; border-radius: 6px;
                padding: 7px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
        """)
        create_btn.clicked.connect(self._on_create_team_click)
        layout.addWidget(create_btn)
        layout.addSpacing(6)

        # Divider
        divider_row = QHBoxLayout()
        divider_row.setContentsMargins(0, 0, 0, 0)
        line_l = QFrame(); line_l.setFrameShape(QFrame.HLine); line_l.setStyleSheet(f"color: {DARK['BORDER']};")
        line_r = QFrame(); line_r.setFrameShape(QFrame.HLine); line_r.setStyleSheet(f"color: {DARK['BORDER']};")
        or_lbl = QLabel("or join with code")
        or_lbl.setStyleSheet(f"color: {DARK['TEXT_FAINT']}; font-size: 10px;")
        or_lbl.setAlignment(Qt.AlignCenter)
        divider_row.addWidget(line_l, 1)
        divider_row.addWidget(or_lbl)
        divider_row.addWidget(line_r, 1)
        layout.addLayout(divider_row)
        layout.addSpacing(6)

        # Invite code row (compact)
        code_row = QHBoxLayout()
        code_row.setSpacing(6)
        self._invite_input = QLineEdit()
        self._invite_input.setPlaceholderText("OH-XXXXX")
        self._invite_input.setAlignment(Qt.AlignCenter)
        self._invite_input.setStyleSheet(f"""
            QLineEdit {{
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 6px; padding: 6px 8px; font-size: 12px;
                font-weight: 600; letter-spacing: 2px; color: {DARK['TEXT']};
            }}
            QLineEdit:focus {{ border-color: {DARK['TEXT_FAINT']}; }}
        """)
        self._invite_input.setMaxLength(10)
        code_row.addWidget(self._invite_input, 1)

        join_btn = QPushButton("Join")
        join_btn.setCursor(Qt.PointingHandCursor)
        join_btn.setStyleSheet(f"""
            QPushButton {{
                background: {DARK['ACCENT']}; color: white; border: none;
                border-radius: 6px; padding: 6px 14px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {DARK['ACCENT_DIM']}; }}
            QPushButton:disabled {{ background: {DARK['BORDER']}; color: {DARK['TEXT_FAINT']}; }}
        """)
        join_btn.clicked.connect(self._on_join_code_click)
        self._join_btn = join_btn
        code_row.addWidget(join_btn)
        layout.addLayout(code_row)

        # Status label for errors / pending
        layout.addSpacing(6)
        self._onboarding_status = QLabel("")
        self._onboarding_status.setAlignment(Qt.AlignCenter)
        self._onboarding_status.setWordWrap(True)
        self._onboarding_status.setStyleSheet(f"font-size: 11px; color: {DARK['DANGER']};")
        self._onboarding_status.setVisible(False)
        layout.addWidget(self._onboarding_status)

        return frame

    def set_available_teams(self, teams, my_teams=None, active_team_id=""):
        """Populate the lobby team list on the onboarding screen.
        teams: [{id, name, created_by}, ...] — teams user can request to join
        my_teams: [{id, name, role}, ...] — teams user already belongs to (shown first with Select btn)
        active_team_id: if set, show a checkmark on the already-selected team
        """
        # Clear existing items
        while self._lobby_layout.count() > 0:
            item = self._lobby_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        has_content = False

        # ── Show user's own teams first (with "Select" button) ──
        if my_teams:
            has_content = True
            section_lbl = QLabel("YOUR TEAMS")
            section_lbl.setStyleSheet(f"font-size: 8px; font-weight: 700; color: {DARK['TEXT_FAINT']}; letter-spacing: 1.5px; padding: 4px 2px 2px 2px;")
            self._lobby_layout.addWidget(section_lbl)

            for team in my_teams:
                row = QFrame()
                row.setStyleSheet(f"""
                    QFrame {{ background: transparent; border-radius: 4px; }}
                    QFrame:hover {{ background: {DARK['BG_HOVER']}; }}
                """)
                h = QHBoxLayout(row)
                h.setContentsMargins(6, 3, 6, 3)
                h.setSpacing(6)

                name_lbl = QLabel(team["name"])
                name_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {DARK['TEXT']};")
                h.addWidget(name_lbl, 1)

                team_id = team["id"]
                team_name = team["name"]
                is_active = (team_id == active_team_id)

                select_btn = QPushButton("✓" if is_active else "Select")
                select_btn.setCursor(Qt.PointingHandCursor)
                select_btn.setFixedSize(54, 24)
                if is_active:
                    select_btn.setStyleSheet(f"""
                        QPushButton {{
                            background: transparent; color: {COLORS['GREEN']};
                            border: 1px solid {COLORS['GREEN']}; border-radius: 4px;
                            font-size: 12px; font-weight: 700;
                        }}
                    """)
                    select_btn.setEnabled(False)
                else:
                    select_btn.setStyleSheet(f"""
                        QPushButton {{
                            background: {COLORS['GREEN']}; color: white; border: none;
                            border-radius: 4px; font-size: 10px; font-weight: 700;
                        }}
                        QPushButton:hover {{ background: #2bbd6e; }}
                    """)
                    select_btn.clicked.connect(
                        lambda checked=False, tid=team_id, tn=team_name:
                            self._on_lobby_select_click(tid, tn)
                    )
                h.addWidget(select_btn)
                self._lobby_layout.addWidget(row)

        if not has_content:
            empty = QLabel("No teams yet.\nCreate one or join with an invite code.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setWordWrap(True)
            empty.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_FAINT']}; padding: 16px;")
            self._lobby_layout.addWidget(empty)

        self._lobby_layout.addStretch()

    def _on_lobby_select_click(self, team_id, team_name):
        """User clicked 'Select' on one of their own teams in the lobby."""
        if not self._get_onboarding_name():
            return
        # Emit name first
        self.name_change_requested.emit(self._onboarding_name_input.text().strip())
        self.team_selected_from_lobby.emit(team_id, team_name)

    def _on_lobby_join_click(self, team_id, team_name, admin_id, btn):
        """User clicked 'Join' on a team in the lobby."""
        if not self._get_onboarding_name():
            return
        # Emit name first
        self.name_change_requested.emit(self._onboarding_name_input.text().strip())
        # Disable the button and show pending state
        btn.setText("...")
        btn.setEnabled(False)
        self._onboarding_status.setText(f"Requesting to join {team_name}...")
        self._onboarding_status.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_DIM']};")
        self._onboarding_status.setVisible(True)
        self.request_to_join.emit(team_id, team_name, admin_id)

    def show_join_pending(self, team_name):
        """Show waiting-for-admin message on onboarding."""
        self._onboarding_status.setText(f"Waiting for {team_name} admin to accept...")
        self._onboarding_status.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_DIM']};")
        self._onboarding_status.setVisible(True)

    def show_join_declined(self):
        """Show declined message on onboarding, re-enable join buttons."""
        self._onboarding_status.setText("Request declined. Try another team or create one.")
        self._onboarding_status.setStyleSheet(f"font-size: 11px; color: {DARK['DANGER']};")
        self._onboarding_status.setVisible(True)
        # Re-enable all lobby join buttons
        if hasattr(self, '_lobby_container'):
            for i in range(self._lobby_layout.count()):
                item = self._lobby_layout.itemAt(i)
                if item and item.widget():
                    for child in item.widget().findChildren(QPushButton):
                        child.setText("Join")
                        child.setEnabled(True)

    def show_join_request_failed(self, reason=""):
        """Show error when join request couldn't be sent."""
        msg = reason or "Could not send request. Try again or use an invite code."
        self._onboarding_status.setText(msg)
        self._onboarding_status.setStyleSheet(f"font-size: 11px; color: {DARK['DANGER']};")
        self._onboarding_status.setVisible(True)
        # Re-enable lobby buttons
        if hasattr(self, '_lobby_container'):
            for i in range(self._lobby_layout.count()):
                item = self._lobby_layout.itemAt(i)
                if item and item.widget():
                    for child in item.widget().findChildren(QPushButton):
                        child.setText("Join")
                        child.setEnabled(True)

    def show_join_request(self, request_id, requester_name):
        """Show admin notification: someone wants to join your team."""
        # Store the request_id for accept/decline
        self._active_join_request_id = request_id
        self._active_join_requester_id = None  # Will be set from main.py context

        # Reuse the incoming banner area but with join request content
        if not hasattr(self, '_join_request_banner'):
            self._join_request_banner = self._build_join_request_banner()
            self._root.insertWidget(4, self._join_request_banner)  # After message banner

        self._join_req_name.setText(f"{requester_name}")
        self._join_req_sub.setText("wants to join your team")
        self._join_request_banner.setVisible(True)
        # If on lobby, grow panel to fit banner; otherwise normal resize
        if self._is_onboarding:
            self.setFixedHeight(580)
        else:
            self._resize_panel()

    def hide_join_request(self):
        """Hide the join request notification banner."""
        if hasattr(self, '_join_request_banner'):
            self._join_request_banner.setVisible(False)
            if self._is_onboarding:
                self.setFixedHeight(500)
            else:
                self._resize_panel()

    def _build_join_request_banner(self):
        """Build the admin join request notification banner."""
        banner = QFrame()
        banner.setStyleSheet(f"""
            background: rgba(42, 191, 191, 0.15);
            border-bottom: 1px solid {DARK['BORDER']};
        """)

        v = QVBoxLayout(banner)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(6)

        top = QHBoxLayout()
        orb = SmallOrb('OPEN')  # Teal orb
        top.addWidget(orb)

        info = QVBoxLayout()
        self._join_req_name = QLabel("")
        self._join_req_name.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {DARK['TEAL']};")
        info.addWidget(self._join_req_name)
        self._join_req_sub = QLabel("wants to join your team")
        self._join_req_sub.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_DIM']};")
        info.addWidget(self._join_req_sub)
        top.addLayout(info, 1)
        v.addLayout(top)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        accept_btn = QPushButton("Accept")
        accept_btn.setCursor(Qt.PointingHandCursor)
        accept_btn.setStyleSheet(f"""
            QPushButton {{
                background: {DARK['ACCENT']}; color: white; border: none;
                border-radius: 8px; padding: 6px 16px; font-weight: 600; font-size: 12px;
            }}
            QPushButton:hover {{ background: {DARK['ACCENT_DIM']}; }}
        """)
        accept_btn.clicked.connect(lambda: self.join_request_accepted.emit(
            getattr(self, '_active_join_request_id', '')))
        btns.addWidget(accept_btn, 1)

        decline_btn = QPushButton("Decline")
        decline_btn.setCursor(Qt.PointingHandCursor)
        decline_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {DARK['DANGER']};
                border: 1px solid rgba(229,57,53,0.3);
                border-radius: 8px; padding: 6px 16px; font-weight: 600; font-size: 12px;
            }}
            QPushButton:hover {{ background: rgba(229,57,53,0.12); }}
        """)
        decline_btn.clicked.connect(lambda: self.join_request_declined.emit(
            getattr(self, '_active_join_request_id', ''),
            getattr(self, '_active_join_requester_id', '')))
        btns.addWidget(decline_btn, 1)

        v.addLayout(btns)
        return banner

    def _get_onboarding_name(self):
        """Get the name from the onboarding input, or None if empty."""
        name = self._onboarding_name_input.text().strip()
        if not name:
            self._onboarding_status.setText("Please enter your name first.")
            self._onboarding_status.setStyleSheet(f"font-size: 11px; color: {DARK['DANGER']};")
            self._onboarding_status.setVisible(True)
            return None
        return name

    def get_onboarding_name(self):
        """Public getter for the name entered on the onboarding screen."""
        return self._onboarding_name_input.text().strip()

    def set_onboarding_name(self, name):
        """Pre-fill the onboarding name field (e.g. if user already has a saved name)."""
        self._onboarding_name_input.setText(name)

    def _on_join_code_click(self):
        """User submitted an invite code."""
        if not self._get_onboarding_name():
            return
        code = self._invite_input.text().strip().upper()
        if not code:
            self._onboarding_status.setText("Please enter an invite code.")
            self._onboarding_status.setStyleSheet(f"font-size: 11px; color: {DARK['DANGER']};")
            self._onboarding_status.setVisible(True)
            return
        # Emit the name change first
        self.name_change_requested.emit(self._onboarding_name_input.text().strip())
        self._onboarding_status.setText("Joining...")
        self._onboarding_status.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_DIM']};")
        self._onboarding_status.setVisible(True)
        self._join_btn.setEnabled(False)
        self.join_code_requested.emit(code)

    @Slot(str)
    def set_onboarding_error(self, message):
        """Show an error on the onboarding screen."""
        self._onboarding_status.setText(message)
        self._onboarding_status.setStyleSheet(f"font-size: 11px; color: {DARK['DANGER']};")
        self._onboarding_status.setVisible(True)
        self._join_btn.setEnabled(True)

    def _on_team_combo_changed(self, index):
        if index < 0:
            return
        team_id = self._team_combo.itemData(index)
        if team_id:
            self.team_changed.emit(team_id)
            # Show manage button only if admin
            role = self._team_combo.itemData(index, Qt.UserRole + 1)
            self._team_manage_btn.setVisible(True)
            # Switch to Users page after selecting a team
            self._switch_page("users")

    def _on_create_team_click(self):
        """Prompt for a team name and emit create signal."""
        # If onboarding is visible, validate the display name first
        if self._onboarding.isVisible():
            if not self._get_onboarding_name():
                return
            # Emit name change
            self.name_change_requested.emit(self._onboarding_name_input.text().strip())
        from PySide6.QtWidgets import QInputDialog
        # Use None parent to avoid macOS tool-window z-order issues
        name, ok = QInputDialog.getText(None, "Create Team", "Team name:")
        if ok and name.strip():
            self.create_team_requested.emit(name.strip())

    def set_teams(self, teams, active_team_id="", force_lobby=False):
        """Update the team dropdown with available teams.
        teams: [{id, name, invite_code, role}, ...]
        If no teams or force_lobby, show onboarding/lobby instead.
        """
        has_teams = bool(teams)

        if force_lobby or not has_teams:
            # Show lobby (onboarding) — overlays entire panel
            self._onboarding.setVisible(True)
            self._onboarding.raise_()
            self._onboarding.setGeometry(self._frame.rect())
            self._is_onboarding = True
            self.setFixedHeight(500)
            return

        # Transition to normal sidebar UI (after user selected a team)
        self._onboarding.setVisible(False)
        self._is_onboarding = False
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)  # Qt default max

        self._team_combo.blockSignals(True)
        self._team_combo.clear()
        active_index = 0
        for i, team in enumerate(teams):
            self._team_combo.addItem(team["name"], team["id"])
            # Store the role as UserRole + 1, invite_code as UserRole + 2
            self._team_combo.setItemData(i, team.get("role", "member"), Qt.UserRole + 1)
            self._team_combo.setItemData(i, team.get("invite_code", ""), Qt.UserRole + 2)
            if team["id"] == active_team_id:
                active_index = i
        self._team_combo.setCurrentIndex(active_index)
        self._team_combo.blockSignals(False)

        # Refresh the visual team list on the lobby page
        self._refresh_teams_list(teams, active_team_id)

        # Update sidebar team label
        active_name = ""
        for t in teams:
            if t["id"] == active_team_id:
                active_name = t["name"]
                break
        self.set_sidebar_team(active_name)

        # Single team auto-selected → go straight to Users
        # Multiple teams or manual browse → stay on Teams
        if active_team_id and len(teams) == 1:
            self._switch_page("users")
        else:
            self._switch_page("teams")
        self._resize_panel()

    def show_manage_team_dialog(self, team_name, team_id, members, invite_code="",
                               is_admin=False, add_callback=None, remove_callback=None):
        """Show team info dialog. All members see code + members, admins can add/remove."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QListWidget, QListWidgetItem
        # Store ref to prevent garbage collection; use panel as parent but override window flags
        self._manage_dlg = dlg = QDialog(self)
        dlg.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        dlg.setWindowTitle(team_name)
        dlg.setFixedWidth(320)
        dlg.setStyleSheet(f"""
            QDialog {{ background: {DARK['BG']}; color: {DARK['TEXT']}; }}
            QLabel {{ color: {DARK['TEXT']}; }}
            QListWidget {{ background: {DARK['BG_RAISED']}; color: {DARK['TEXT']}; border: 1px solid {DARK['BORDER']}; border-radius: 6px; }}
            QListWidget::item {{ padding: 4px 8px; }}
            QListWidget::item:selected {{ background: {DARK['BG_HOVER']}; }}
            QLineEdit {{ background: {DARK['BG_RAISED']}; color: {DARK['TEXT']}; border: 1px solid {DARK['BORDER']}; border-radius: 4px; padding: 4px 8px; }}
            QPushButton {{ background: {DARK['BG_RAISED']}; color: {DARK['TEXT']}; border: 1px solid {DARK['BORDER']}; border-radius: 4px; padding: 6px 12px; }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
        """)
        layout = QVBoxLayout(dlg)

        # Invite code display
        if invite_code:
            code_frame = QFrame()
            code_frame.setStyleSheet(f"background: rgba(0, 166, 81, 0.08); border: 1px solid rgba(0, 166, 81, 0.20); border-radius: 8px;")
            code_layout = QVBoxLayout(code_frame)
            code_layout.setContentsMargins(12, 10, 12, 10)
            code_lbl = QLabel("Invite Code")
            code_lbl.setStyleSheet("font-size: 10px; font-weight: 700; color: #888; letter-spacing: 1px; border: none;")
            code_layout.addWidget(code_lbl, alignment=Qt.AlignCenter)
            code_val = QLabel(invite_code)
            code_val.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {DARK['ACCENT']}; letter-spacing: 3px; border: none;")
            code_val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            code_layout.addWidget(code_val, alignment=Qt.AlignCenter)
            hint = QLabel("Share this code so others can join")
            hint.setStyleSheet("font-size: 10px; color: #999; border: none;")
            code_layout.addWidget(hint, alignment=Qt.AlignCenter)

            # Copy button
            copy_btn = QPushButton("Copy Code")
            copy_btn.setCursor(Qt.PointingHandCursor)
            copy_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {DARK['ACCENT']}; border: 1px solid rgba(0,166,81,0.3);
                    border-radius: 4px; padding: 4px 10px; font-size: 11px; font-weight: 600;
                }}
                QPushButton:hover {{ background: rgba(0,166,81,0.10); }}
            """)
            copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(invite_code))
            code_layout.addWidget(copy_btn, alignment=Qt.AlignCenter)
            layout.addWidget(code_frame)

        # Members list
        layout.addWidget(QLabel("Members:"))
        member_list = QListWidget()
        for m in members:
            item = QListWidgetItem(f"{m['display_name']} ({m['role']})")
            item.setData(Qt.UserRole, m["user_id"])
            item.setData(Qt.UserRole + 1, m["role"])
            member_list.addItem(item)
        layout.addWidget(member_list)

        # Admin-only: Remove and Add
        if is_admin:
            remove_btn = QPushButton("Remove Selected")
            remove_btn.setStyleSheet(f"background: {DARK['DANGER']}; color: white; border: none; border-radius: 4px; padding: 6px;")
            def _on_remove():
                item = member_list.currentItem()
                if item:
                    uid = item.data(Qt.UserRole)
                    role = item.data(Qt.UserRole + 1)
                    if role == "admin":
                        return  # Can't remove admin
                    if remove_callback:
                        remove_callback(team_id, uid)
                    member_list.takeItem(member_list.row(item))
            remove_btn.clicked.connect(_on_remove)
            layout.addWidget(remove_btn)

            layout.addWidget(QLabel("Add member by name:"))
            add_row = QHBoxLayout()
            name_input = QLineEdit()
            name_input.setPlaceholderText("Display name...")
            add_row.addWidget(name_input, 1)
            add_btn = QPushButton("Add")
            add_btn.setStyleSheet(f"background: {DARK['ACCENT']}; color: white; border: none; border-radius: 4px; padding: 6px 12px;")
            def _on_add():
                name = name_input.text().strip()
                if name and add_callback:
                    add_callback(team_id, name)
                    member_list.addItem(f"{name} (member)")
                    name_input.clear()
            add_btn.clicked.connect(_on_add)
            add_row.addWidget(add_btn)
            layout.addLayout(add_row)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)

        print(f"[DEBUG] About to show manage dialog, size={dlg.sizeHint()}")
        dlg.raise_()
        dlg.activateWindow()
        dlg.exec()

    # ── User Section ──────────────────────────────────────────────
    def _build_user_section(self):
        section = QFrame()
        section.setStyleSheet("border: none;")
        v = QVBoxLayout(section)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Section header
        sec_hdr = QHBoxLayout()
        sec_hdr.setContentsMargins(16, 10, 16, 4)
        self._online_label = QLabel("ONLINE")
        self._online_label.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {DARK['TEXT_FAINT']}; letter-spacing: 1.2px;")
        sec_hdr.addWidget(self._online_label)
        sec_hdr.addStretch()
        self.online_count = QLabel("0")
        self.online_count.setStyleSheet(f"""
            font-size: 10px; font-weight: 700; color: {DARK['TEXT_DIM']};
            background: {DARK['BG_RAISED']}; border-radius: 8px; padding: 1px 6px;
        """)
        sec_hdr.addWidget(self.online_count)
        v.addLayout(sec_hdr)

        # Scrollable user list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{
                width: 4px; background: transparent;
            }}
            QScrollBar::handle:vertical {{
                background: {DARK['BORDER']}; border-radius: 2px; min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)

        self._user_container = QWidget()
        self._user_layout = QVBoxLayout(self._user_container)
        self._user_layout.setContentsMargins(8, 4, 8, 8)
        self._user_layout.setSpacing(4)
        self._user_layout.addStretch()

        scroll.setWidget(self._user_container)
        v.addWidget(scroll, 1)

        return section



    # ── Incoming Call Banner ──────────────────────────────────────
    # ── Outgoing Call Banner ────────────────────────────────────────
    def _build_outgoing_banner(self):
        banner = QFrame()
        banner.setStyleSheet(f"""
            background: {DARK['BG_RAISED']};
            border-bottom: 1px solid {DARK['BORDER']};
        """)

        v = QVBoxLayout(banner)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(6)

        # Target info
        top = QHBoxLayout()
        self.outgoing_orb = GlowingOrb(24)
        self.outgoing_orb.start_breathing()
        top.addWidget(self.outgoing_orb)

        call_info = QVBoxLayout()
        self.outgoing_name = QLabel("")
        self.outgoing_name.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {DARK['TEXT']};")
        call_info.addWidget(self.outgoing_name)
        self.outgoing_sub = QLabel("Calling...")
        self.outgoing_sub.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_DIM']};")
        call_info.addWidget(self.outgoing_sub)
        top.addLayout(call_info, 1)
        v.addLayout(top)

        # Cancel button
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {DARK['DANGER']};
                border: 1px solid rgba(229,57,53,0.3);
                border-radius: 8px; padding: 6px 16px; font-weight: 600;
                font-size: 13px;
            }}
            QPushButton:hover {{ background: rgba(229,57,53,0.12); }}
        """)
        cancel_btn.clicked.connect(self.cancel_call_requested.emit)
        v.addWidget(cancel_btn)

        return banner

    # ── Incoming Call Banner ───────────────────────────────────────
    def _build_incoming_banner(self):
        banner = QFrame()
        banner.setStyleSheet(f"""
            background: {DARK['BG_RAISED']};
            border-bottom: 1px solid {DARK['BORDER']};
        """)

        v = QVBoxLayout(banner)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(6)

        # Caller info
        top = QHBoxLayout()
        self.incoming_orb = GlowingOrb(24)
        self.incoming_orb.start_breathing()
        top.addWidget(self.incoming_orb)

        caller_info = QVBoxLayout()
        self.incoming_name = QLabel("Jane D.")
        self.incoming_name.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {DARK['TEXT']};")
        caller_info.addWidget(self.incoming_name)
        caller_sub = QLabel("wants to connect")
        caller_sub.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_DIM']};")
        caller_info.addWidget(caller_sub)
        top.addLayout(caller_info, 1)
        v.addLayout(top)

        # Accept / Decline
        btns = QHBoxLayout()
        accept_btn = QPushButton("Accept")
        accept_btn.setCursor(Qt.PointingHandCursor)
        accept_btn.setStyleSheet(f"""
            QPushButton {{
                background: {DARK['ACCENT']}; color: white; border: none;
                border-radius: 8px; padding: 6px 16px; font-weight: 700;
                font-size: 13px;
            }}
            QPushButton:hover {{ background: {DARK['ACCENT_DIM']}; }}
        """)
        accept_btn.clicked.connect(self.accept_call_requested.emit)
        btns.addWidget(accept_btn)

        decline_btn = QPushButton("Decline")
        decline_btn.setCursor(Qt.PointingHandCursor)
        decline_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {DARK['TEXT_DIM']};
                border: 1px solid {DARK['BORDER']};
                border-radius: 8px; padding: 6px 16px; font-weight: 600;
                font-size: 13px;
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
        """)
        decline_btn.clicked.connect(self.decline_call_requested.emit)
        btns.addWidget(decline_btn)
        v.addLayout(btns)

        return banner

    # ── Call Banner ────────────────────────────────────────────────
    def _build_call_banner(self):
        banner = QFrame()
        banner.setStyleSheet(f"""
            background: {DARK['BG_RAISED']};
            border-bottom: 1px solid {DARK['BORDER']};
        """)

        outer = QVBoxLayout(banner)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(8)

        # Top row: orb + name + end button
        top = QHBoxLayout()
        top.setSpacing(8)

        call_orb = GlowingOrb(20)
        call_orb.start_breathing()
        top.addWidget(call_orb)

        self.call_name_label = QLabel("")
        self.call_name_label.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {DARK['TEXT']};")
        top.addWidget(self.call_name_label, 1)

        end_btn = QPushButton("End")
        end_btn.setCursor(Qt.PointingHandCursor)
        end_btn.setStyleSheet(f"""
            QPushButton {{
                background: {DARK['DANGER']}; color: white; border: none;
                border-radius: 8px; padding: 4px 12px; font-weight: 700;
                font-size: 12px;
            }}
            QPushButton:hover {{ background: #c62828; }}
        """)
        end_btn.clicked.connect(self.end_call_requested.emit)
        top.addWidget(end_btn)
        outer.addLayout(top)

        # Bottom row: retro unicode EQ meters
        meters = QHBoxLayout()
        meters.setSpacing(4)

        mic_lbl = QLabel("MIC")
        mic_lbl.setStyleSheet(f"font-size: 9px; font-weight: 700; color: {DARK['ACCENT']}; border: none; letter-spacing: 1px;")
        meters.addWidget(mic_lbl)
        self.mic_meter = UnicodeEQ(num_bars=8, color=DARK['ACCENT'])
        meters.addWidget(self.mic_meter)

        meters.addSpacing(6)

        spk_lbl = QLabel("RCV")
        spk_lbl.setStyleSheet(f"font-size: 9px; font-weight: 700; color: {DARK['INFO']}; border: none; letter-spacing: 1px;")
        meters.addWidget(spk_lbl)
        self.speaker_meter = UnicodeEQ(num_bars=8, color=DARK['INFO'])
        meters.addWidget(self.speaker_meter)

        meters.addStretch()
        outer.addLayout(meters)

        return banner

    def set_mic_level(self, level):
        """Update mic level meter (0.0–1.0)."""
        self.mic_meter.set_level(level)

    def set_speaker_level(self, level):
        """Update speaker level meter (0.0–1.0)."""
        self.speaker_meter.set_level(level)

    # ── Pinned Compact ────────────────────────────────────────────
    def _build_message_banner(self):
        banner = QFrame()
        banner.setStyleSheet(f"""
            background: rgba(120, 100, 0, 0.20);
            border-bottom: 1px solid {DARK['BORDER']};
        """)

        h = QHBoxLayout(banner)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(8)

        icon = QLabel("›")
        icon.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {DARK['WARN']};")
        h.addWidget(icon)

        self._msg_label = QLabel("New Message")
        self._msg_label.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {DARK['WARN']};")
        h.addWidget(self._msg_label, 1)

        play_btn = QPushButton("\u25b6 Play")
        play_btn.setCursor(Qt.PointingHandCursor)
        play_btn.setStyleSheet(f"""
            QPushButton {{
                background: {DARK['ACCENT']}; color: white; border: none;
                border-radius: 8px; padding: 4px 12px; font-weight: 700;
                font-size: 12px;
            }}
            QPushButton:hover {{ background: {DARK['ACCENT_DIM']}; }}
        """)
        play_btn.clicked.connect(self.play_message_requested.emit)
        h.addWidget(play_btn)

        dismiss_btn = QPushButton("\u2715")
        dismiss_btn.setCursor(Qt.PointingHandCursor)
        dismiss_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {DARK['TEXT_FAINT']}; border: none;
                font-size: 16px; font-weight: 700; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {DARK['TEXT_DIM']}; }}
        """)
        dismiss_btn.clicked.connect(lambda: self._message_banner.setVisible(False))
        h.addWidget(dismiss_btn)

        return banner

    def show_message(self):
        """Show the new message indicator banner."""
        self._message_banner.setVisible(True)
        self._resize_panel()

    def hide_message(self):
        """Hide the message indicator banner."""
        self._message_banner.setVisible(False)
        self._resize_panel()

    # ── Pinned Compact ────────────────────────────────────────────
    def _build_pinned_compact(self, parent_widget=None):
        bar = QFrame(parent_widget)
        bar.setFixedHeight(46)

        h = QHBoxLayout(bar)
        h.setContentsMargins(6, 6, 6, 6)
        h.setSpacing(0)

        # Single PTT button — same shape as the full panel's PTT
        self.pinned_ptt = QPushButton("Hold to Talk")
        self.pinned_ptt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.pinned_ptt.setCursor(Qt.PointingHandCursor)
        self.pinned_ptt.pressed.connect(self.ptt_pressed.emit)
        self.pinned_ptt.released.connect(self.ptt_released.emit)

        # Double-click to unpin
        self.pinned_ptt.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pinned_ptt.customContextMenuRequested.connect(lambda: self._toggle_pin())

        h.addWidget(self.pinned_ptt)

        # Hidden refs
        self.pinned_orb = GlowingOrb(1)
        self.pinned_orb.setVisible(False)
        self.pinned_name = QLabel("")
        self.pinned_name.setVisible(False)

        self._pinned_bar_frame = bar
        self._update_pinned_style()
        return bar

    def _update_pinned_style(self):
        """Update the pinned compact bar to match the PTT button shape, colored by mode."""
        color_hex = COLORS.get(self._current_mode, COLORS['GREEN'])
        label = MODE_LABELS.get(self._current_mode, 'Available')
        name = getattr(self, '_display_name', 'Office Hours')
        self.pinned_ptt.setText(f"{name}  -  {label}")
        # Convert hex to rgba for translucency
        c = QColor(color_hex)
        rgba = f"rgba({c.red()}, {c.green()}, {c.blue()}, 0.25)"
        rgba_border = f"rgba({c.red()}, {c.green()}, {c.blue()}, 0.5)"
        rgba_pressed = f"rgba({c.red()}, {c.green()}, {c.blue()}, 0.5)"
        self.pinned_ptt.setStyleSheet(f"""
            QPushButton {{
                background: {rgba}; border: 2px solid {rgba_border};
                border-radius: 10px; padding: 8px; font-size: 14px;
                font-weight: 600; color: white;
            }}
            QPushButton:hover {{ border-color: {rgba_pressed}; }}
            QPushButton:pressed {{ background: {rgba_pressed}; border: 2px solid {rgba_border}; }}
        """)

    # ══════════════════════════════════════════════════════════════
    #  Public API — called by IntercomApp
    # ══════════════════════════════════════════════════════════════

    def set_mode(self, mode):
        """Update the current mode display."""
        self._current_mode = mode
        self._sidebar_orb.set_mode(mode)
        self.pinned_orb.set_mode(mode)
        self._update_mode_btn()
        self._update_ptt_style()
        self._update_pinned_style()

        if mode == 'RED':
            self.ptt_mode_label.setVisible(False)

    def set_hotline(self, is_on):
        """Toggle hotline state."""
        self._is_open_line = is_on
        self.open_toggle.set_on(is_on)
        in_call = getattr(self, '_call_peer_name', None)
        if is_on:
            self.ptt_btn.setText("●  Hotline")
            self.ptt_mode_label.setText("Hotline — open mic, same-room feel.")
            self.ptt_mode_label.setVisible(True)
        elif in_call:
            self.ptt_btn.setText(f"●  Talking to {in_call}")
            self.ptt_mode_label.setVisible(False)
        else:
            self.ptt_btn.setText("●  Hold to Talk")
            self.ptt_mode_label.setVisible(False)

    def set_hotline_enabled(self, enabled):
        """Enable/disable hotline toggle (only active when connected)."""
        self.open_toggle.setEnabled(enabled)
        if not enabled and not self._is_open_line:
            self._hotline_lbl.setStyleSheet(f"font-size: 12px; color: {DARK['TEXT_FAINT']}; font-weight: 500; border: none;")
        else:
            self._hotline_lbl.setStyleSheet(f"font-size: 12px; color: {DARK['TEXT_DIM']}; font-weight: 500; border: none;")

    def set_sidebar_team(self, team_name):
        """Show the active team name in the sidebar."""
        self._sidebar_team_label.setText(team_name.upper() if team_name else "")

    def set_display_name(self, name):
        """Set the display name shown in the pinned compact bar and sidebar initials."""
        self._display_name = name
        self._update_pinned_style()
        # Update sidebar initials
        if name:
            parts = name.strip().split()
            if len(parts) >= 2:
                initials = parts[0][0].upper() + parts[-1][0].upper()
            elif parts:
                initials = parts[0][:2].upper()
            else:
                initials = ""
            self._sidebar_user_initials.setText(initials)
            self._sidebar_user_initials.setToolTip(name)
        else:
            self._sidebar_user_initials.setText("")

    def set_connection(self, connected, peer_name=""):
        """Switch between connected and disconnected states.
        Banners are now hidden — connection state is conveyed by row highlights."""
        self._connected = connected
        self._conn_bar.setVisible(False)
        self._disconn_bar.setVisible(False)
        if connected and peer_name:
            self.conn_label.setText(f"Connected to {peer_name}")
        elif connected:
            self.conn_label.setText("Connected")

    def set_users(self, users, selected_user_id=None):
        """Replace the user list. users = [{id, name, mode, has_message}, ...]
        Preserves intercom state (connecting/live) for rows that still exist."""
        # Snapshot active states before rebuild
        old_states = {}
        for uid, row in self._user_rows.items():
            if row._state != UserRow.STATE_IDLE:
                old_states[uid] = row._state
        # Ensure selected target is preserved
        if selected_user_id and selected_user_id not in old_states:
            old_states[selected_user_id] = UserRow.STATE_SELECTED

        # Clear existing
        self._user_rows = {}
        while self._user_layout.count() > 1:  # keep the stretch
            item = self._user_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for u in users:
            uid = u.get('id', '')
            row = UserRow(
                uid,
                u.get('name', 'Unknown'),
                u.get('mode', 'GREEN'),
                u.get('has_message', False)
            )
            row.call_clicked.connect(self.call_user_requested.emit)
            row.intercom_pressed.connect(self.intercom_pressed.emit)
            row.intercom_released.connect(self.intercom_released.emit)
            row.user_selected.connect(self._on_user_row_clicked)
            # Restore intercom state if this row was active
            if uid in old_states:
                row.set_state(old_states[uid])
            self._user_rows[uid] = row
            self._user_layout.insertWidget(self._user_layout.count() - 1, row)

        self.online_count.setText(str(len(users)))

        # Dynamic panel height based on visible content
        if not self._pinned and not self._is_onboarding:
            self._auto_resize()

    def _on_user_row_clicked(self, user_id):
        """Handle click on a user row — select as PTT target."""
        # Deselect all other rows (except if they're in connecting/live state)
        for uid, row in self._user_rows.items():
            if uid != user_id and row._state == UserRow.STATE_SELECTED:
                row.set_state(UserRow.STATE_IDLE)
        # Toggle selection on clicked row
        row = self._user_rows.get(user_id)
        if row:
            if row._state == UserRow.STATE_SELECTED:
                row.set_state(UserRow.STATE_IDLE)
                self.user_selected.emit("")  # Deselect
            elif row._state == UserRow.STATE_IDLE:
                row.set_state(UserRow.STATE_SELECTED)
                self.user_selected.emit(user_id)

    def set_user_state(self, user_id, state):
        """Set a user row's visual state (idle/selected/connecting/live)."""
        row = self._user_rows.get(user_id)
        if row:
            row.set_state(state)

    def set_user_eq_level(self, user_id, level):
        """Update a user row's inline EQ meter."""
        row = self._user_rows.get(user_id)
        if row:
            row.set_eq_level(level)

    def _hide_all_banners(self):
        """Clear all call banners — ensures clean state."""
        self._outgoing_banner.setVisible(False)
        self._incoming_banner.setVisible(False)
        self._call_banner.setVisible(False)

    def show_outgoing(self, target_name):
        """Show the outgoing call banner while waiting for response."""
        self._hide_all_banners()
        self.outgoing_name.setText(target_name)
        self.outgoing_sub.setText("Calling...")
        self._outgoing_banner.setVisible(True)
        self._resize_panel()

    def hide_outgoing(self):
        """Hide the outgoing call banner."""
        self._outgoing_banner.setVisible(False)
        self._resize_panel()

    def show_incoming(self, caller_name):
        """Show the incoming call banner."""
        self._hide_all_banners()
        self.incoming_name.setText(caller_name)
        self._incoming_banner.setVisible(True)
        self._resize_panel()

    def hide_incoming(self):
        """Hide the incoming call banner."""
        self._incoming_banner.setVisible(False)
        self._resize_panel()

    def show_call(self, caller_name):
        """Show the in-call banner."""
        self._hide_all_banners()
        self.call_name_label.setText(caller_name)
        self._call_banner.setVisible(True)
        self._conn_bar.setVisible(False)   # Call banner replaces connection bar
        self._call_peer_name = caller_name
        if not self._is_open_line:
            self.ptt_btn.setText(f"●  Talking to {caller_name}")
        self._resize_panel()

    def hide_call(self):
        """Hide the in-call banner and restore normal layout."""
        self._hide_all_banners()
        self._call_peer_name = None
        # Reset PTT text
        if self._is_open_line:
            self.ptt_btn.setText("●  Hotline")
        else:
            self.ptt_btn.setText("●  Hold to Talk")
        self._resize_panel()

    def set_ptt_active(self, active):
        """Visual feedback when PTT is held."""
        if active:
            self.ptt_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(226, 42, 26, 0.15); border: 2px solid {DARK['DANGER']};
                    border-radius: 8px; padding: 8px; font-size: 13px;
                    font-weight: 600; color: {DARK['DANGER']};
                }}
            """)
        else:
            # Restore normal style
            self._update_ptt_style()

    def set_ptt_locked(self, locked):
        """Disable PTT while the peer is talking."""
        if locked:
            self.ptt_btn.setEnabled(False)
            self.ptt_btn.setText("●  Listening...")
            self.ptt_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                    border-radius: 8px; padding: 8px; font-size: 13px;
                    font-weight: 500; color: {DARK['TEXT_FAINT']};
                }}
            """)
        else:
            self.ptt_btn.setEnabled(True)
            # Restore text
            in_call = getattr(self, '_call_peer_name', None)
            if self._is_open_line:
                self.ptt_btn.setText("●  Hotline")
            elif in_call:
                self.ptt_btn.setText(f"●  Talking to {in_call}")
            else:
                self.ptt_btn.setText("●  Hold to Talk")
            self._update_ptt_style()

    # ── Radio Page ─────────────────────────────────────────────
    _nts_meta_ready = Signal(dict)   # emitted from bg thread with metadata

    def _build_radio_page(self):
        """NTS Radio player with live metadata display."""
        page = QFrame()
        page.setStyleSheet("border: none;")
        v = QVBoxLayout(page)
        v.setContentsMargins(12, 12, 12, 8)
        v.setSpacing(8)

        # ── Now Playing header ──
        now_lbl = QLabel("NOW PLAYING")
        now_lbl.setStyleSheet(f"font-size: 9px; font-weight: 700; color: {DARK['TEXT_FAINT']}; letter-spacing: 1px; border: none;")
        v.addWidget(now_lbl)

        # ── Show title ──
        self._radio_title = QLabel("NTS Radio")
        self._radio_title.setWordWrap(True)
        self._radio_title.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {DARK['TEXT']}; border: none;")
        v.addWidget(self._radio_title)

        # ── Show subtitle (location / genres) ──
        self._radio_subtitle = QLabel("")
        self._radio_subtitle.setWordWrap(True)
        self._radio_subtitle.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_DIM']}; border: none;")
        self._radio_subtitle.setVisible(False)
        v.addWidget(self._radio_subtitle)

        # ── Channel selector (1 / 2) ──
        ch_row = QHBoxLayout()
        ch_row.setSpacing(6)
        self._radio_ch1_btn = QPushButton("1")
        self._radio_ch2_btn = QPushButton("2")
        for btn in (self._radio_ch1_btn, self._radio_ch2_btn):
            btn.setFixedSize(32, 24)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: 11px; font-weight: 700; color: {DARK['TEXT_DIM']};
                    background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                    border-radius: 6px;
                }}
                QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
            """)
        self._radio_ch1_btn.clicked.connect(lambda: self._switch_radio_channel(0))
        self._radio_ch2_btn.clicked.connect(lambda: self._switch_radio_channel(1))
        ch_row.addWidget(self._radio_ch1_btn)
        ch_row.addWidget(self._radio_ch2_btn)
        ch_row.addStretch()

        ch_row.addStretch()
        v.addLayout(ch_row)

        # ── Play / Stop button (full width, clear label) ──
        self._radio_play_btn = QPushButton("▶  Play")
        self._radio_play_btn.setFixedHeight(32)
        self._radio_play_btn.setCursor(Qt.PointingHandCursor)
        self._radio_play_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 12px; font-weight: 600; color: {DARK['ACCENT']};
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 8px; padding: 0 16px;
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; border-color: {DARK['ACCENT']}; }}
        """)
        self._radio_play_btn.clicked.connect(self._toggle_radio)
        v.addWidget(self._radio_play_btn)

        # ── Volume slider ──
        vol_row = QHBoxLayout()
        vol_row.setSpacing(8)
        vol_icon = QLabel("🔈")
        vol_icon.setStyleSheet("font-size: 12px; border: none;")
        vol_row.addWidget(vol_icon)
        self._radio_volume = QSlider(Qt.Horizontal)
        self._radio_volume.setRange(0, 100)
        self._radio_volume.setValue(20)  # start low
        self._radio_volume.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background: {DARK['BG_RAISED']}; height: 4px; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {DARK['ACCENT']}; width: 12px; height: 12px;
                margin: -4px 0; border-radius: 6px;
            }}
            QSlider::sub-page:horizontal {{
                background: {DARK['ACCENT_DIM']}; border-radius: 2px;
            }}
        """)
        self._radio_volume.valueChanged.connect(self._on_radio_volume)
        vol_row.addWidget(self._radio_volume, 1)
        v.addLayout(vol_row)

        v.addSpacing(4)

        # ── Divider ──
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background: {DARK['BORDER_LT']}; border: none;")
        v.addWidget(div)

        v.addSpacing(2)

        # ── Up Next ──
        next_hdr = QLabel("UP NEXT")
        next_hdr.setStyleSheet(f"font-size: 9px; font-weight: 700; color: {DARK['TEXT_FAINT']}; letter-spacing: 1px; border: none;")
        v.addWidget(next_hdr)

        self._radio_next = QLabel("")
        self._radio_next.setWordWrap(True)
        self._radio_next.setStyleSheet(f"font-size: 12px; color: {DARK['TEXT_DIM']}; border: none;")
        v.addWidget(self._radio_next)

        v.addStretch()

        # ── Internal state ──
        self._radio_playing = False
        self._radio_channel = 0  # 0 = Channel 1, 1 = Channel 2
        self._radio_meta = {}    # cached API response
        self._nts_meta_ready.connect(self._apply_radio_meta)

        # Media player (created lazily on first play)
        self._radio_player = None
        self._radio_audio_out = None

        # Metadata refresh timer
        self._radio_meta_timer = QTimer(self)
        self._radio_meta_timer.timeout.connect(self._fetch_nts_meta)
        self._radio_meta_timer.setInterval(30_000)  # refresh every 30s

        # Highlight channel 1 by default
        self._update_channel_btns()

        return page

    def _switch_radio_channel(self, ch):
        """Switch between NTS channel 1 and 2."""
        if ch == self._radio_channel:
            return
        self._radio_channel = ch
        self._update_channel_btns()
        self._apply_radio_meta(self._radio_meta)
        # If playing, restart stream on new channel
        if self._radio_playing:
            self._start_radio_stream()

    def _update_channel_btns(self):
        """Highlight the active channel button."""
        for i, btn in enumerate((self._radio_ch1_btn, self._radio_ch2_btn)):
            if i == self._radio_channel:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        font-size: 11px; font-weight: 700; color: {DARK['TEXT']};
                        background: {DARK['ACCENT_DIM']}; border: 1px solid {DARK['ACCENT']};
                        border-radius: 6px;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        font-size: 11px; font-weight: 700; color: {DARK['TEXT_DIM']};
                        background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                        border-radius: 6px;
                    }}
                    QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
                """)

    def _toggle_radio(self):
        """Play or stop the NTS stream."""
        if self._radio_playing:
            self._stop_radio()
        else:
            self._start_radio()

    def _start_radio(self):
        """Start NTS stream at low volume."""
        self._radio_playing = True
        self._radio_play_btn.setText("■  Stop")
        self._radio_play_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 12px; font-weight: 600; color: {DARK['DANGER']};
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 8px; padding: 0 16px;
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; border-color: {DARK['DANGER']}; }}
        """)
        self._start_radio_stream()
        self._fetch_nts_meta()
        self._radio_meta_timer.start()

    def _stop_radio(self):
        """Stop the NTS stream."""
        self._radio_playing = False
        self._radio_play_btn.setText("▶  Play")
        self._radio_play_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 12px; font-weight: 600; color: {DARK['ACCENT']};
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 8px; padding: 0 16px;
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; border-color: {DARK['ACCENT']}; }}
        """)
        if self._radio_player:
            self._radio_player.stop()
        self._radio_meta_timer.stop()

    def stop_radio(self):
        """Public method for shutdown — stop radio without touching UI."""
        self._radio_playing = False
        if hasattr(self, '_radio_player') and self._radio_player:
            self._radio_player.stop()
        if hasattr(self, '_radio_meta_timer'):
            self._radio_meta_timer.stop()

    def _start_radio_stream(self):
        """Create or restart the media player on the current channel."""
        if not self._radio_player:
            self._radio_player = QMediaPlayer(self)
            self._radio_audio_out = QAudioOutput(self)
            self._radio_audio_out.setVolume(self._radio_volume.value() / 100.0)
            self._radio_player.setAudioOutput(self._radio_audio_out)

        self._radio_player.stop()
        # Channel 1 and 2 have separate stream URLs
        urls = [
            "https://stream-relay-geo.ntslive.net/stream?client=NTSRadio",
            "https://stream-relay-geo.ntslive.net/stream2?client=NTSRadio",
        ]
        self._radio_player.setSource(QUrl(urls[self._radio_channel]))
        self._radio_player.play()

    def _on_radio_volume(self, val):
        if self._radio_audio_out:
            self._radio_audio_out.setVolume(val / 100.0)

    def _fetch_nts_meta(self):
        """Fetch NTS live metadata in a background thread."""
        def _fetch():
            try:
                req = Request("https://www.nts.live/api/v2/live",
                              headers={"User-Agent": "OfficeHours/1.0"})
                with urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read())
                    self._nts_meta_ready.emit(data)
            except Exception as e:
                log.debug(f"NTS metadata fetch failed: {e}")
        threading.Thread(target=_fetch, daemon=True).start()

    @Slot(dict)
    def _apply_radio_meta(self, data):
        """Update the radio page with fresh NTS metadata."""
        self._radio_meta = data
        results = data.get("results", [])
        if not results or self._radio_channel >= len(results):
            return

        ch = results[self._radio_channel]
        now = ch.get("now", {})
        nxt = ch.get("next", {})

        # Show title
        title = now.get("broadcast_title", "NTS Radio")
        self._radio_title.setText(title)

        # Subtitle: location + genres from embeds
        details = now.get("embeds", {}).get("details", {})
        parts = []
        location = details.get("location_long", "")
        if location:
            parts.append(location)
        genres = details.get("genres", [])
        if genres:
            genre_names = [g.get("value", g) if isinstance(g, dict) else str(g) for g in genres[:3]]
            parts.append(" · ".join(genre_names))
        if parts:
            self._radio_subtitle.setText("  ·  ".join(parts))
            self._radio_subtitle.setVisible(True)
        else:
            self._radio_subtitle.setVisible(False)

        # Up next
        next_title = nxt.get("broadcast_title", "")
        if next_title:
            self._radio_next.setText(next_title)
        else:
            self._radio_next.setText("—")

    def start_radio_on_nav(self):
        """Called when user clicks the Radio nav — auto-play if not already playing."""
        if not self._radio_playing:
            self._start_radio()

    # ── Settings View (inline) ──────────────────────────────────
    def _build_settings_view(self):
        """Build an inline settings panel that replaces the main content."""
        view = QFrame()
        view.setStyleSheet("border: none;")

        v = QVBoxLayout(view)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── Scrollable settings items ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{ width: 4px; background: transparent; }}
            QScrollBar::handle:vertical {{ background: {DARK['BORDER']}; border-radius: 2px; min-height: 20px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        container = QWidget()
        self._settings_layout = QVBoxLayout(container)
        self._settings_layout.setContentsMargins(8, 6, 8, 6)
        self._settings_layout.setSpacing(2)

        scroll.setWidget(container)
        scroll.setMinimumHeight(250)  # Ensure settings view gets enough vertical space
        v.addWidget(scroll, 1)

        return view

    def _populate_settings(self):
        """Rebuild the settings items (called each time settings opens so state is fresh)."""
        layout = self._settings_layout

        # Clear existing items
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        row_style = f"""
            QPushButton {{
                text-align: left; padding: 8px 12px; font-size: 13px;
                font-weight: 500; color: {DARK['TEXT']}; background: transparent;
                border: none; border-radius: 8px;
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
        """
        section_style = f"font-size: 10px; font-weight: 700; color: {DARK['TEXT_FAINT']}; padding: 4px 12px 2px 12px; text-transform: uppercase; letter-spacing: 1px;"

        combo_style = f"""
            QComboBox {{
                font-size: 11px; padding: 3px 6px;
                border: 1px solid {DARK['BORDER']}; border-radius: 6px;
                background: {DARK['BG_RAISED']}; color: {DARK['TEXT']};
            }}
            QComboBox QAbstractItemView {{
                background: {DARK['BG_RAISED']}; color: {DARK['TEXT']};
                border: 1px solid {DARK['BORDER']};
                selection-background-color: {DARK['BG_HOVER']};
            }}
        """

        def _divider():
            d = QFrame()
            d.setFixedHeight(1)
            d.setStyleSheet(f"background: {DARK['BORDER']}; margin: 4px 12px;")
            return d

        def _section(title):
            lbl = QLabel(title)
            lbl.setStyleSheet(section_style)
            return lbl

        tile_style = f"""
            QPushButton {{
                padding: 5px 6px; font-size: 11px; font-weight: 500;
                color: {DARK['TEXT']}; background: transparent;
                border: 1px solid {DARK['BORDER']}; border-radius: 6px;
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; border-color: {DARK['TEXT_FAINT']}; }}
        """
        menu_style = f"""
            QMenu {{
                background: {DARK['BG_RAISED']}; color: {DARK['TEXT']};
                border: 1px solid {DARK['BORDER']}; border-radius: 6px; padding: 4px;
            }}
            QMenu::item {{ padding: 5px 12px; border-radius: 4px; }}
            QMenu::item:selected {{ background: {DARK['BG_HOVER']}; }}
        """

        def _tile_row(*buttons):
            """Create a horizontal row of equally-sized tile buttons."""
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 0, 4, 0)
            h.setSpacing(6)
            for btn in buttons:
                h.addWidget(btn, 1)
            return row

        # ── Row 1: Name + Theme ──
        display_name = getattr(self, '_display_name', None) or 'Not set'
        name_btn = QPushButton(f"👤  {display_name}")
        name_btn.setCursor(Qt.PointingHandCursor)
        name_btn.setStyleSheet(tile_style)
        name_btn.clicked.connect(lambda: (self._change_name_dialog(), self._populate_settings()))

        theme_text = "☀  Light Mode" if self._dark_mode else "🌙  Dark Mode"
        theme_btn = QPushButton(theme_text)
        theme_btn.setCursor(Qt.PointingHandCursor)
        theme_btn.setStyleSheet(tile_style)
        theme_btn.clicked.connect(lambda: (self._toggle_dark_mode(), self._populate_settings()))

        layout.addWidget(_tile_row(name_btn, theme_btn))

        # ── Row 2: Incognito ──
        incognito_text = "👁  Go Incognito" if not self._incognito else "👁‍🗨  Go Visible"
        incognito_btn = QPushButton(incognito_text)
        incognito_btn.setCursor(Qt.PointingHandCursor)
        incognito_btn.setStyleSheet(tile_style)
        incognito_btn.clicked.connect(lambda: (self._toggle_incognito(), self._populate_settings()))

        layout.addWidget(_tile_row(incognito_btn))

        layout.addWidget(_divider())

        # ── AUDIO: Input + Output ──
        layout.addWidget(_section("AUDIO"))

        try:
            import sounddevice as sd
            devices = sd.query_devices()

            input_devices = [("Default", None)]
            output_devices = [("Default", None)]
            current_in = getattr(self, '_current_input_idx', None)
            current_out = getattr(self, '_current_output_idx', None)
            current_in_name = "Default"
            current_out_name = "Default"

            for i, d in enumerate(devices):
                short_name = d['name'][:20]
                if d['max_input_channels'] > 0:
                    input_devices.append((short_name, i))
                    if current_in == i:
                        current_in_name = short_name
                if d['max_output_channels'] > 0:
                    output_devices.append((short_name, i))
                    if current_out == i:
                        current_out_name = short_name

            in_btn = QPushButton(f"🎤  {current_in_name}")
            in_btn.setCursor(Qt.PointingHandCursor)
            in_btn.setStyleSheet(tile_style)
            in_menu = QMenu(in_btn)
            in_menu.setStyleSheet(menu_style)
            for name, idx in input_devices:
                action = in_menu.addAction(name)
                action.triggered.connect(
                    lambda checked=False, i=idx: (self._on_input_device_changed(i), self._populate_settings())
                )
            in_btn.setMenu(in_menu)

            out_btn = QPushButton(f"🔊  {current_out_name}")
            out_btn.setCursor(Qt.PointingHandCursor)
            out_btn.setStyleSheet(tile_style)
            out_menu = QMenu(out_btn)
            out_menu.setStyleSheet(menu_style)
            for name, idx in output_devices:
                action = out_menu.addAction(name)
                action.triggered.connect(
                    lambda checked=False, i=idx: (self._on_output_device_changed(i), self._populate_settings())
                )
            out_btn.setMenu(out_menu)

            layout.addWidget(_tile_row(in_btn, out_btn))
        except Exception as e:
            print(f"Audio device settings error: {e}")

        layout.addWidget(_divider())

        # ── TEAM: Copy Code + Invite a Friend ──
        layout.addWidget(_section("TEAM"))

        copy_code_btn = QPushButton("📋  Copy Code")
        copy_code_btn.setCursor(Qt.PointingHandCursor)
        copy_code_btn.setStyleSheet(tile_style)
        copy_code_btn.clicked.connect(lambda: (self._copy_invite_code(), self._show_copied_toast()))

        invite_btn = QPushButton("✉  Invite a Friend")
        invite_btn.setCursor(Qt.PointingHandCursor)
        invite_btn.setStyleSheet(tile_style)
        invite_btn.clicked.connect(self._invite_friend_email)

        layout.addWidget(_tile_row(copy_code_btn, invite_btn))

        layout.addWidget(_divider())

        # ── STREAM DECK ──
        layout.addWidget(_section("STREAM DECK"))

        deck_connected = getattr(self, '_deck_connected', False)
        if deck_connected:
            deck_name = getattr(self, '_deck_name', 'Stream Deck')
            deck_btn = QPushButton(f"🎛  {deck_name} — Connected")
            deck_btn.setStyleSheet(f"""
                QPushButton {{
                    padding: 5px 6px; font-size: 11px; font-weight: 500;
                    color: {DARK['ACCENT']}; background: transparent;
                    border: 1px solid {DARK['BORDER']}; border-radius: 6px;
                }}
            """)
            deck_btn.setEnabled(False)
        else:
            deck_btn = QPushButton("🎛  Not Connected — Setup Guide")
            deck_btn.setCursor(Qt.PointingHandCursor)
            deck_btn.setStyleSheet(tile_style)
            deck_btn.clicked.connect(self._show_deck_setup_guide)

        layout.addWidget(_tile_row(deck_btn))

        layout.addWidget(_divider())

        # ── Leave Team + Quit ──
        leave_team_btn = QPushButton("Leave Team")
        leave_team_btn.setCursor(Qt.PointingHandCursor)
        leave_team_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 5px 6px; font-size: 11px; font-weight: 500;
                color: {DARK['WARN']}; background: transparent;
                border: 1px solid rgba(230,175,0,0.3); border-radius: 6px;
            }}
            QPushButton:hover {{ background: rgba(230,175,0,0.10); }}
        """)
        leave_team_btn.clicked.connect(lambda: (self._close_settings(), self._confirm_leave_team()))

        quit_btn = QPushButton("Quit OH")
        quit_btn.setCursor(Qt.PointingHandCursor)
        quit_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 5px 6px; font-size: 11px; font-weight: 500;
                color: {DARK['DANGER']}; background: transparent;
                border: 1px solid rgba(229,57,53,0.3); border-radius: 6px;
            }}
            QPushButton:hover {{ background: rgba(229,57,53,0.10); }}
        """)
        quit_btn.clicked.connect(self.quit_requested.emit)

        layout.addStretch()
        layout.addWidget(_tile_row(leave_team_btn, quit_btn))

    def set_deck_status(self, connected, deck_name="Stream Deck"):
        """Update Stream Deck connection status (called from main.py)."""
        self._deck_connected = connected
        self._deck_name = deck_name

    def _show_deck_setup_guide(self):
        """Show Stream Deck setup instructions."""
        import sys as _sys
        dlg = QDialog(self)
        dlg.setWindowTitle("Stream Deck Setup")
        dlg.setFixedWidth(340)
        dlg.setStyleSheet(f"""
            QDialog {{ background: {DARK['BG']}; border: 1px solid {DARK['BORDER']}; border-radius: 10px; }}
            QLabel {{ color: {DARK['TEXT']}; border: none; }}
        """)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title = QLabel("Stream Deck Setup")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {DARK['TEXT']}; border: none;")
        layout.addWidget(title)

        if _sys.platform == 'darwin':
            steps = (
                "1. Quit the Elgato Stream Deck app\n"
                "   (it locks the device)\n\n"
                "2. Install hidapi if you haven't:\n"
                "   brew install hidapi\n\n"
                "3. Restart Office Hours\n\n"
                "Your Stream Deck will be detected\nautomatically on next launch."
            )
        elif _sys.platform == 'win32':
            steps = (
                "1. Quit the Elgato Stream Deck app\n"
                "   (it locks the device)\n\n"
                "2. Install the LibUSB driver:\n"
                "   a. Download Zadig: zadig.akeo.ie\n"
                "   b. Options > List All Devices\n"
                "   c. Select your Stream Deck\n"
                "   d. Click Install WinUSB\n\n"
                "3. Restart Office Hours\n\n"
                "Your Stream Deck will be detected\nautomatically on next launch."
            )
        else:
            steps = (
                "1. Install libhidapi:\n"
                "   sudo apt install libhidapi-libusb0\n\n"
                "2. Add udev rules for Stream Deck\n\n"
                "3. Restart Office Hours"
            )

        body = QLabel(steps)
        body.setWordWrap(True)
        body.setStyleSheet(f"font-size: 12px; color: {DARK['TEXT_DIM']}; line-height: 1.5; border: none;")
        layout.addWidget(body)

        ok_btn = QPushButton("Got it")
        ok_btn.setCursor(Qt.PointingHandCursor)
        ok_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 6px 14px; font-size: 12px; font-weight: 600;
                color: #fff; background: {DARK['ACCENT']};
                border: none; border-radius: 6px;
            }}
            QPushButton:hover {{ background: {DARK['TEAL']}; }}
        """)
        ok_btn.clicked.connect(dlg.accept)
        layout.addWidget(ok_btn, alignment=Qt.AlignRight)

        dlg.exec()

    def _show_hamburger_menu(self):
        """Toggle the inline settings view."""
        if self._settings_view.isVisible():
            self._close_settings()
        else:
            self._open_settings()

    def _open_settings(self):
        """Switch to settings page in the sidebar nav."""
        self._populate_settings()
        self._switch_page("settings")

    def _close_settings(self):
        """Switch back to users page from settings."""
        self._switch_page("users")

    def _on_input_device_changed(self, device_index):
        self._current_input_idx = device_index
        self.audio_input_changed.emit(device_index)

    def _on_output_device_changed(self, device_index):
        self._current_output_idx = device_index
        self.audio_output_changed.emit(device_index)

    def _change_name_dialog(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, "Change Display Name",
            "Enter your display name:",
            text=self._display_name or ""
        )
        if ok and name.strip():
            self._display_name = name.strip()
            self._update_pinned_style()
            self.name_change_requested.emit(self._display_name)

    def _copy_code(self, code):
        """Copy an invite code to clipboard with visual feedback."""
        QApplication.clipboard().setText(code)
        self._show_copied_toast()

    def _show_copied_toast(self):
        """Show a 'Copied!' label that fades up and out."""
        toast = QLabel("Copied!", self)
        toast.setStyleSheet(f"""
            background: {DARK['BG_RAISED']}; color: {COLORS['GREEN']};
            border: 1px solid {DARK['BORDER']}; border-radius: 6px;
            padding: 4px 12px; font-size: 11px; font-weight: 600;
        """)
        toast.setAlignment(Qt.AlignCenter)
        toast.adjustSize()
        # Center horizontally in the panel
        x = (self.width() - toast.width()) // 2
        y = self.height() // 2
        toast.move(x, y)
        toast.show()

        # Fade out using window opacity
        effect = QGraphicsOpacityEffect(toast)
        toast.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", toast)
        anim.setDuration(1200)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.6, 1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(toast.deleteLater)
        anim.start()

    def _invite_friend_email(self):
        """Show a dialog to enter email address(es) and send invite via Resend."""
        idx = self._team_combo.currentIndex()
        code = ""
        team_name = "Office Hours"
        if idx >= 0:
            code = self._team_combo.itemData(idx, Qt.UserRole + 2) or ""
            team_name = self._team_combo.currentText() or "Office Hours"

        sender_name = getattr(self, '_display_name', None) or "A teammate"

        # Build dialog
        dlg = QDialog(self)
        dlg.setWindowTitle("Send Invite")
        dlg.setFixedWidth(320)
        dlg.setStyleSheet(f"""
            QDialog {{ background: {DARK['BG']}; border: 1px solid {DARK['BORDER']}; border-radius: 10px; }}
            QLabel {{ color: {DARK['TEXT']}; border: none; }}
        """)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title = QLabel(f"Invite to {team_name}")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {DARK['TEXT']}; border: none;")
        layout.addWidget(title)

        subtitle = QLabel("Enter email address:")
        subtitle.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_DIM']}; border: none;")
        layout.addWidget(subtitle)

        email_input = QLineEdit()
        email_input.setPlaceholderText("friend@example.com")
        email_input.setStyleSheet(f"""
            QLineEdit {{
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 6px; padding: 8px 10px; font-size: 13px;
                color: {DARK['TEXT']};
            }}
            QLineEdit:focus {{ border-color: {DARK['ACCENT']}; }}
        """)
        layout.addWidget(email_input)

        if code:
            code_label = QLabel(f"Code: {code}")
            code_label.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_FAINT']}; border: none;")
            layout.addWidget(code_label)

        status_label = QLabel("")
        status_label.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_DIM']}; border: none;")
        status_label.setVisible(False)
        layout.addWidget(status_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 6px 14px; font-size: 12px; font-weight: 500;
                color: {DARK['TEXT_DIM']}; background: transparent;
                border: 1px solid {DARK['BORDER']}; border-radius: 6px;
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
        """)
        cancel_btn.clicked.connect(dlg.reject)

        send_btn = QPushButton("Send Invite")
        send_btn.setCursor(Qt.PointingHandCursor)
        send_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 6px 14px; font-size: 12px; font-weight: 600;
                color: #fff; background: {DARK['ACCENT']};
                border: none; border-radius: 6px;
            }}
            QPushButton:hover {{ background: {DARK['TEAL']}; }}
        """)

        def _do_send():
            email = email_input.text().strip()
            if not email or "@" not in email:
                status_label.setText("Please enter a valid email address.")
                status_label.setStyleSheet(f"font-size: 11px; color: {DARK['DANGER']}; border: none;")
                status_label.setVisible(True)
                return
            send_btn.setEnabled(False)
            send_btn.setText("Sending...")
            status_label.setVisible(False)

            import threading
            def _send():
                from supabase_client import send_invite_email
                result = send_invite_email(email, team_name, code, sender_name)
                # Update UI from main thread via QTimer.singleShot
                if result:
                    def _on_success():
                        status_label.setText("Sent!")
                        status_label.setStyleSheet(f"font-size: 11px; color: {DARK['ACCENT']}; border: none;")
                        status_label.setVisible(True)
                        QTimer.singleShot(1000, dlg.accept)
                    QTimer.singleShot(0, _on_success)
                else:
                    def _on_fail():
                        status_label.setText("Failed to send. Try again.")
                        status_label.setStyleSheet(f"font-size: 11px; color: {DARK['DANGER']}; border: none;")
                        status_label.setVisible(True)
                        send_btn.setEnabled(True)
                        send_btn.setText("Send Invite")
                    QTimer.singleShot(0, _on_fail)

            threading.Thread(target=_send, daemon=True).start()

        send_btn.clicked.connect(_do_send)
        email_input.returnPressed.connect(_do_send)

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(send_btn)
        layout.addLayout(btn_row)

        dlg.exec()

    def _copy_invite_code(self):
        """Copy the current team's invite code to the clipboard."""
        idx = self._team_combo.currentIndex()
        if idx < 0:
            return
        code = self._team_combo.itemData(idx, Qt.UserRole + 2)  # invite_code stored at UserRole+2
        if code:
            QApplication.clipboard().setText(code)
            # Brief visual feedback — swap button text
            self._close_settings()

    def _confirm_leave_team(self):
        """Ask user to confirm leaving the current team."""
        from PySide6.QtWidgets import QMessageBox
        team_name = self._team_combo.currentText() if self._team_combo.count() > 0 else "this team"
        reply = QMessageBox.question(
            self, "Leave Team",
            f"Leave \"{team_name}\"?\n\nYou'll need an invite code to rejoin.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.leave_team_requested.emit()

    def _toggle_incognito(self):
        self._incognito = not self._incognito
        if self._incognito:
            # Save current mode, go black
            self._pre_incognito_mode = self._current_mode
            self._sidebar_orb.set_mode('INCOGNITO')
            self.mode_btn.setText("Incognito")
            self.mode_btn.setStyleSheet(f"""
                QPushButton {{
                    padding: 5px 12px 5px 10px;
                    border-radius: 8px;
                    border: 1px solid {DARK['BORDER']};
                    background: {DARK['BG_RAISED']};
                    font-size: 13px; font-weight: 600;
                    color: {DARK['TEXT_DIM']}; text-align: left;
                }}
                QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
            """)
        else:
            # Restore previous mode
            mode = getattr(self, '_pre_incognito_mode', 'GREEN')
            self.set_mode(mode)
        self.incognito_toggled.emit(self._incognito)

    def _play_radio(self, station_name):
        """Start playing a radio station stream."""
        url = RADIO_STATIONS.get(station_name)
        if not url:
            return
        self._radio_station = station_name
        self._radio_player.setSource(QUrl(url))
        self._radio_player.play()

    def _stop_radio(self):
        """Stop the radio."""
        self._radio_player.stop()
        self._radio_player.setSource(QUrl())
        self._radio_station = None

    def _toggle_dark_mode(self):
        self._dark_mode = not self._dark_mode
        self._apply_theme()
        self.dark_mode_toggled.emit(self._dark_mode)

    def _apply_theme(self):
        """Swap the DARK dict values in-place to match current theme, then restyle."""
        target = _DARK_ORIGINAL if self._dark_mode else LIGHT
        for k in DARK:
            if k in target:
                DARK[k] = target[k]
        # Restyle the main panel frame
        self._content_frame.setStyleSheet(f"""
            QFrame#content_frame {{
                background: {DARK['BG']};
                border: 1px solid {DARK['BORDER']};
                border-radius: {PANEL_RADIUS}px;
            }}
        """)
        self._sidebar.setStyleSheet(f"""
            QFrame#sidebar {{
                background: {DARK['BG_RAISED']};
                border-right: 1px solid {DARK['BORDER']};
                border-top-left-radius: {PANEL_RADIUS}px;
                border-bottom-left-radius: {PANEL_RADIUS}px;
            }}
        """)
        # Re-populate settings so tile colors update
        self._populate_settings()
        self.update()

    def apply_dark_mode(self, enabled):
        """Apply dark or light mode color palette to the panel."""
        self._dark_mode = enabled
        self._apply_theme()
        text_color = DARK['TEXT']
        for i in range(self._user_layout.count()):
            widget = self._user_layout.itemAt(i).widget()
            if widget and hasattr(widget, 'name_label'):
                widget.name_label.setStyleSheet(f"font-size: 14px; font-weight: 500; color: {text_color}; padding-bottom: 1px;")

    def _toggle_pin(self):
        if self._is_onboarding:
            return
        self._pinned = not self._pinned
        self.pin_toggled.emit(self._pinned)
        self._update_pin_style(self._pinned)

        if self._pinned:
            # Collapse to compact PTT bar
            self._sidebar.setVisible(False)
            self._content_frame.setVisible(False)
            self._pinned_compact.setVisible(True)
            # Force panel to compact size
            self.setFixedHeight(58)
        else:
            # Expand to full panel
            self.setMaximumHeight(16777215)  # Remove fixed height
            self.setMinimumHeight(0)
            self._sidebar.setVisible(True)
            self._content_frame.setVisible(True)
            self._pinned_compact.setVisible(False)
            self._resize_panel()

    def is_pinned(self):
        return self._pinned

    # ── Notch (triangle pointing to tray icon) ──────────────────
    def paintEvent(self, event):
        """Draw a small notch/triangle at the top of the panel pointing up."""
        super().paintEvent(event)
        anchor_x = getattr(self, '_notch_x', self.width() // 2)
        h = self._notch_h
        w = 14  # notch base width

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(DARK['BG']))

        path = QPainterPath()
        path.moveTo(anchor_x - w // 2, h)
        path.lineTo(anchor_x, 0)
        path.lineTo(anchor_x + w // 2, h)
        path.closeSubpath()
        p.drawPath(path)
        p.end()

    # ── Size Management ─────────────────────────────────────────
    # Fixed panel height — consistent across all pages
    PANEL_H = 520

    def _auto_resize(self):
        """Set panel to a fixed height so it stays consistent across pages."""
        if self._pinned or self._is_onboarding:
            return
        self.setFixedHeight(self.PANEL_H)

    def _resize_panel(self):
        """Resize the panel, but skip during onboarding (height is locked)."""
        if self._is_onboarding:
            return
        self._auto_resize()

    # ── Positioning ───────────────────────────────────────────────
    def show_at(self, global_pos):
        """Show panel anchored below a global position (tray icon)."""
        x = global_pos.x() - self.width() // 2
        y = global_pos.y() + 4
        # Keep on screen
        screen = QApplication.primaryScreen().availableGeometry()
        if x + self.width() > screen.right():
            x = screen.right() - self.width() - 4
        if x < screen.left():
            x = screen.left() + 4
        # Store notch x position (where the tray icon is, relative to panel)
        self._notch_x = global_pos.x() - x
        self.move(x, y)
        self.show()
        self.raise_()
        # Apply vibrancy after show (needs native window handle)
        QTimer.singleShot(50, self._apply_vibrancy)
        self.activateWindow()

    def resizeEvent(self, event):
        """Ensure absolute overlay widgets cover the frame properly (needed on Windows)."""
        super().resizeEvent(event)
        if hasattr(self, '_frame'):
            r = self._frame.rect()
            if hasattr(self, '_onboarding'):
                self._onboarding.setGeometry(r)
            if hasattr(self, '_pinned_compact'):
                self._pinned_compact.setGeometry(r)

    # ── Dragging (needed on Windows for frameless windows) ────
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_active = True
            self._drag_start_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if getattr(self, '_drag_active', False):
            self.move(event.globalPosition().toPoint() - self._drag_start_pos)
            event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_active = False
            event.accept()
        super().mouseReleaseEvent(event)

    def focusOutEvent(self, event):
        """Close panel when it loses focus (unless pinned)."""
        if not self._pinned:
            self.hide()
        super().focusOutEvent(event)


# ═══════════════════════════════════════════════════════════════════
#  Tray Icon Helper
# ═══════════════════════════════════════════════════════════════════
def create_oh_icon(color_hex='#00a651', size=22):
    """Load the OH icon from PNG files for the macOS system tray.
    
    Uses oh_icon.png (18x18) and oh_icon@2x.png (36x36).
    macOS picks the @2x automatically for Retina.
    Falls back to text rendering if files not found.
    """
    icon_dir = os.path.dirname(os.path.abspath(__file__))
    icon_path = os.path.join(icon_dir, "oh_icon.png")
    icon_2x_path = os.path.join(icon_dir, "oh_icon@2x.png")

    if os.path.exists(icon_path):
        icon = QIcon()
        icon.addFile(icon_path, QSize(18, 18))
        if os.path.exists(icon_2x_path):
            icon.addFile(icon_2x_path, QSize(36, 36))
        return icon

    # Fallback: render text
    import sys as _sys
    from PySide6.QtGui import QFontMetrics
    if _sys.platform == 'win32':
        font = QFont("Segoe UI")
    else:
        font = QFont(".AppleSystemUIFont")
    font.setPixelSize(size - 4)
    font.setWeight(QFont.Bold)
    font.setLetterSpacing(QFont.AbsoluteSpacing, -1.0)
    fm = QFontMetrics(font)
    text_width = fm.horizontalAdvance("OH") + 4

    pixmap = QPixmap(max(text_width, size), size)
    pixmap.fill(QColor(0, 0, 0, 0))

    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setFont(font)
    p.setPen(QColor(0, 0, 0))
    p.drawText(QRect(0, 0, pixmap.width(), pixmap.height()), Qt.AlignCenter, "OH")
    p.end()

    return QIcon(pixmap)


# ═══════════════════════════════════════════════════════════════════
#  Standalone test
# ═══════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # System tray
    tray = QSystemTrayIcon()
    tray.setIcon(create_oh_icon())
    tray.setVisible(True)

    # Panel
    panel = FloatingPanel()
    panel.set_connection(True, "OH-7X3K")

    # Demo users
    panel.set_users([
        {'id': '1', 'name': 'Jane D.', 'mode': 'GREEN'},
        {'id': '2', 'name': 'Rob S.', 'mode': 'YELLOW', 'has_message': True},
        {'id': '3', 'name': 'Kim L.', 'mode': 'GREEN', 'has_message': True},
    ])

    def toggle_panel(reason):
        if reason == QSystemTrayIcon.Trigger:
            if panel.isVisible():
                panel.hide()
            else:
                geo = tray.geometry()
                panel.show_at(QPoint(geo.center().x(), geo.bottom()))

    tray.activated.connect(toggle_panel)

    # Mode cycling demo
    modes = ['GREEN', 'YELLOW', 'RED']
    mode_idx = [0]

    def on_mode_cycle():
        mode_idx[0] = (mode_idx[0] + 1) % len(modes)
        m = modes[mode_idx[0]]
        panel.set_mode(m)
        tray.setIcon(create_oh_icon(COLORS[m]))

    panel.mode_cycle_requested.connect(on_mode_cycle)

    def on_hotline_toggle(is_on):
        panel.set_hotline(is_on)
        if is_on:
            tray.setIcon(create_oh_icon(COLORS['OPEN']))
        else:
            tray.setIcon(create_oh_icon(COLORS[modes[mode_idx[0]]]))

    panel.hotline_toggled.connect(on_hotline_toggle)

    def on_leave():
        panel.set_connection(False)

    panel.leave_requested.connect(on_leave)

    sys.exit(app.exec())
