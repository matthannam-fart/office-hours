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
FONT_FALLBACK = 'Segoe UI, SF Pro Display, -apple-system, Helvetica Neue, Arial'

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
    deck_guide_dismissed = Signal()               # user clicked "don't show again" on deck guide

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
            # Qt.Tool hides from taskbar; FramelessWindowHint removes title bar
            self.setWindowFlags(
                Qt.Tool |
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

        # ── LEFT: Sidebar (favorites bar — hidden until team is active) ──
        self._sidebar = self._build_sidebar()
        self._sidebar.setVisible(False)
        frame_layout.addWidget(self._sidebar)

        # ── RIGHT: Content area ───────────────────────────────
        content_frame = QFrame()
        content_frame.setObjectName("content_frame")
        content_frame.setStyleSheet("border: none;")
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

        # ── Content header (team name + hamburger menu) ───────
        self._content_header = self._build_content_header()
        self._content_layout.addWidget(self._content_header)

        # ── Stacked content pages ─────────────────────────────
        self._content_stack = QStackedWidget()

        # Page 0: Welcome (no team selected)
        self._welcome_page = self._build_welcome_page()
        self._content_stack.addWidget(self._welcome_page)

        # Page 1: Users (team active — default view)
        self._users_page = QWidget()
        users_v = QVBoxLayout(self._users_page)
        users_v.setContentsMargins(0, 0, 0, 0)
        users_v.setSpacing(0)
        self._user_section = self._build_user_section()
        users_v.addWidget(self._user_section, 1)
        self._content_stack.addWidget(self._users_page)

        # Page 2: Teams (team management / switching)
        self._teams_page = self._build_teams_page()
        self._content_stack.addWidget(self._teams_page)

        # Page 3: Radio
        self._radio_page = self._build_radio_page()
        self._content_stack.addWidget(self._radio_page)

        # Page 4: Settings
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
        self._team_bar = QWidget()  # Dummy
        self._team_bar.setVisible(False)

        # Store layout refs
        self._root = self._content_layout
        self._content_frame = content_frame

        # Default to welcome page
        self._active_nav = "welcome"
        self._switch_page("welcome")

    # ── Sidebar ─────────────────────────────────────────────────────
    def _build_sidebar(self):
        """Sidebar: OH logo → radio/mail icons → favorites → user badge → traffic light."""
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
        v.setContentsMargins(0, 10, 0, 8)
        v.setSpacing(4)

        # ── OH logo at top ──
        oh_logo = QLabel("OH")
        oh_logo.setAlignment(Qt.AlignCenter)
        oh_logo.setFixedHeight(28)
        oh_logo.setStyleSheet(f"""
            font-size: 16px; font-weight: 900; color: {DARK['ACCENT']};
            border: none; letter-spacing: 1px;
        """)
        logo_container = QHBoxLayout()
        logo_container.setContentsMargins(0, 0, 0, 4)
        logo_container.setAlignment(Qt.AlignCenter)
        logo_container.addWidget(oh_logo)
        v.addLayout(logo_container)

        # ── OH logo — click to go home ──
        oh_logo.setCursor(Qt.PointingHandCursor)
        oh_logo.mousePressEvent = lambda e: self._switch_page("users")

        # ── Radio icon — toggles NTS stream directly ──
        self._sidebar_radio_btn = QPushButton("📻")
        self._sidebar_radio_btn.setFixedSize(36, 36)
        self._sidebar_radio_btn.setCursor(Qt.PointingHandCursor)
        self._sidebar_radio_btn.setToolTip("Radio")
        self._sidebar_radio_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                border-radius: 18px; font-size: 18px;
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
        """)
        self._sidebar_radio_btn.clicked.connect(self._sidebar_radio_toggle)
        radio_container = QHBoxLayout()
        radio_container.setContentsMargins(0, 0, 0, 0)
        radio_container.setAlignment(Qt.AlignCenter)
        radio_container.addWidget(self._sidebar_radio_btn)
        v.addLayout(radio_container)

        # ── Mail icon (hidden by default, shown when messages are waiting) ──
        self._sidebar_mail_btn = QPushButton("✉")
        self._sidebar_mail_btn.setFixedSize(36, 36)
        self._sidebar_mail_btn.setCursor(Qt.PointingHandCursor)
        self._sidebar_mail_btn.setVisible(False)
        self._sidebar_mail_btn.setToolTip("Messages waiting")
        self._sidebar_mail_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                border-radius: 18px; font-size: 18px; color: {DARK['WARN']};
            }}
            QPushButton:hover {{ background: rgba(230, 175, 0, 0.15); }}
        """)
        self._sidebar_mail_btn.clicked.connect(self.play_message_requested.emit)
        mail_container = QHBoxLayout()
        mail_container.setContentsMargins(0, 0, 0, 0)
        mail_container.setAlignment(Qt.AlignCenter)
        mail_container.addWidget(self._sidebar_mail_btn)
        v.addLayout(mail_container)

        # Pulse animation for mail icon
        self._mail_pulse_timer = QTimer(self)
        self._mail_pulse_timer.setInterval(800)
        self._mail_pulse_on = True
        self._mail_pulse_timer.timeout.connect(self._pulse_mail_icon)

        # ── Favorite user avatars (populated dynamically) ──
        self._fav_container = QWidget()
        self._fav_layout = QVBoxLayout(self._fav_container)
        self._fav_layout.setContentsMargins(4, 4, 4, 4)
        self._fav_layout.setSpacing(6)
        self._fav_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self._fav_buttons = {}  # uid -> QPushButton
        v.addWidget(self._fav_container, 1)

        v.addStretch()

        # ── Connected peer initials (hidden — redundant with favorites) ──
        self._sidebar_peer_initials = QPushButton("")
        self._sidebar_peer_initials.setFixedSize(0, 0)
        self._sidebar_peer_initials.setVisible(False)
        self._sidebar_peer_mode = ""

        # ── Traffic light mode selector (R / Y / G stacked dots) ──
        self._traffic_light_frame = QFrame()
        self._traffic_light_frame.setObjectName("trafficLight")
        self._traffic_light_frame.setFixedWidth(SIDEBAR_W - 10)
        self._traffic_light_frame.setStyleSheet(f"""
            QFrame#trafficLight {{
                background: {DARK['BG_RAISED']};
                border: 1px solid {DARK['BORDER']};
                border-radius: 10px;
            }}
        """)
        tl_layout = QVBoxLayout(self._traffic_light_frame)
        tl_layout.setContentsMargins(0, 6, 0, 6)
        tl_layout.setSpacing(2)
        tl_layout.setAlignment(Qt.AlignCenter)

        self._traffic_dots = {}
        for mode_key, color in [("RED", COLORS['RED']), ("YELLOW", COLORS['YELLOW']), ("GREEN", COLORS['GREEN'])]:
            dot_btn = QPushButton("●")
            dot_btn.setFixedSize(40, 40)
            dot_btn.setCursor(Qt.PointingHandCursor)
            dot_btn.setToolTip(MODE_LABELS.get(mode_key, mode_key))
            dot_btn.clicked.connect(lambda checked=False, mk=mode_key: self.mode_set_requested.emit(mk))
            self._traffic_dots[mode_key] = dot_btn
            tl_layout.addWidget(dot_btn, 0, Qt.AlignCenter)

        tl_container = QHBoxLayout()
        tl_container.setContentsMargins(5, 0, 5, 6)
        tl_container.setAlignment(Qt.AlignCenter)
        tl_container.addWidget(self._traffic_light_frame)
        v.addLayout(tl_container)

        # Initial traffic light styling
        self._update_traffic_light()

        # ── Hidden legacy refs for compat ──
        # Status menu (still needed for external callers)
        self._status_menu = QMenu(self)
        # Sidebar status button (hidden — replaced by traffic light)
        self._sidebar_status_btn = QPushButton()
        self._sidebar_status_btn.setFixedSize(0, 0)
        self._sidebar_status_btn.setVisible(False)
        # Sidebar orb (hidden — replaced by traffic light)
        self._sidebar_orb = GlowingOrb(0)
        self._sidebar_orb.setVisible(False)
        # Status frame (hidden)
        self._sidebar_status_frame = QFrame()
        self._sidebar_status_frame.setVisible(False)
        # Team label (hidden — team shown in header instead)
        self._sidebar_team_label = QLabel("")
        self._sidebar_team_label.setVisible(False)

        # Legacy nav buttons dict (empty — nav removed)
        self._nav_buttons = {}

        return sidebar

    def _update_traffic_light(self):
        """Style the traffic light dots — all show their color, active gets a rounded gray bg."""
        mode = getattr(self, '_current_mode', 'GREEN')
        for mk, dot_btn in self._traffic_dots.items():
            color = COLORS.get(mk, COLORS['GREEN'])
            if mk == mode:
                # Active — colored dot with rounded gray rectangle behind it
                dot_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {DARK['BG_HOVER']}; border: none;
                        border-radius: 10px;
                        font-size: 32px; color: {color};
                    }}
                    QPushButton:hover {{ background: {DARK['BORDER']}; }}
                """)
            else:
                # Inactive — colored dot, no background
                dot_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent; border: none;
                        border-radius: 10px;
                        font-size: 32px; color: {color};
                    }}
                    QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
                """)

    def _sidebar_radio_toggle(self):
        """Toggle NTS radio from sidebar button. Show/hide volume popup."""
        self._toggle_radio()
        if self._radio_playing:
            # Update button to show "on" state
            self._sidebar_radio_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(42, 191, 191, 0.15); border: none;
                    border-radius: 18px; font-size: 18px;
                }}
                QPushButton:hover {{ background: rgba(42, 191, 191, 0.25); }}
            """)
            self._sidebar_radio_btn.setToolTip("Radio (playing)")
            self._show_radio_vol_popup()
        else:
            self._sidebar_radio_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; border: none;
                    border-radius: 18px; font-size: 18px;
                }}
                QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
            """)
            self._sidebar_radio_btn.setToolTip("Radio")
            if hasattr(self, '_radio_vol_popup') and self._radio_vol_popup:
                self._radio_vol_popup.hide()

    def _show_radio_vol_popup(self):
        """Show a small volume slider popup next to the radio button. Auto-hides after 5s."""
        # Increment generation counter to invalidate previous auto-hide timers
        if not hasattr(self, '_radio_vol_gen'):
            self._radio_vol_gen = 0
        self._radio_vol_gen += 1
        gen = self._radio_vol_gen

        if hasattr(self, '_radio_vol_popup') and self._radio_vol_popup:
            self._radio_vol_popup.show()
            QTimer.singleShot(5000, lambda: self._auto_hide_vol_popup(gen))
            return

        popup = QFrame(self)
        popup.setObjectName("radioVolPopup")
        popup.setStyleSheet(f"""
            QFrame#radioVolPopup {{
                background: {DARK['BG_RAISED']};
                border: 1px solid {DARK['BORDER']};
                border-radius: 8px;
            }}
        """)
        popup.setFixedSize(140, 36)

        h = QHBoxLayout(popup)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(6)

        icon = QLabel("🔊")
        icon.setStyleSheet("border: none; font-size: 12px;")
        h.addWidget(icon)

        vol = QSlider(Qt.Horizontal)
        vol.setRange(0, 100)
        vol.setValue(self._radio_volume.value() if hasattr(self, '_radio_volume') else 20)
        vol.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px; background: {DARK['BORDER']}; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {DARK['TEAL']}; width: 12px; height: 12px;
                margin: -4px 0; border-radius: 6px;
            }}
            QSlider::sub-page:horizontal {{ background: {DARK['TEAL']}; border-radius: 2px; }}
        """)
        vol.valueChanged.connect(self._on_radio_volume)
        if hasattr(self, '_radio_volume'):
            vol.valueChanged.connect(lambda v: self._radio_volume.setValue(v))
        h.addWidget(vol, 1)

        # Position to the right of the radio button
        btn_pos = self._sidebar_radio_btn.mapTo(self, QPoint(0, 0))
        popup.move(btn_pos.x() + self._sidebar_radio_btn.width() + 4,
                   btn_pos.y() + (self._sidebar_radio_btn.height() - 36) // 2)
        popup.show()
        self._radio_vol_popup = popup

        # Auto-hide after 5 seconds
        QTimer.singleShot(5000, lambda: self._auto_hide_vol_popup(gen))

    def _auto_hide_vol_popup(self, gen):
        """Hide volume popup if no newer show request has been made."""
        if getattr(self, '_radio_vol_gen', 0) == gen and hasattr(self, '_radio_vol_popup') and self._radio_vol_popup:
            self._radio_vol_popup.hide()

    def _update_favorites(self, users):
        """Update the sidebar favorites with user initials from the user list."""
        # Clear existing
        for btn in self._fav_buttons.values():
            btn.deleteLater()
        self._fav_buttons = {}

        for u in users:
            if u.get('mode') == 'OFFLINE':
                continue
            uid = u.get('id', '')
            name = u.get('name', '?')
            mode = u.get('mode', 'GREEN')
            initials = self._peer_initials(name)
            if not initials:
                continue
            btn = QPushButton(initials)
            btn.setFixedSize(36, 36)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(name)
            color = COLORS.get(mode, COLORS['GREEN'])
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {color}; color: white; border: none;
                    border-radius: 18px; font-size: 12px; font-weight: 800;
                }}
                QPushButton:hover {{ border: 2px solid {DARK['TEXT']}; }}
            """)
            btn.clicked.connect(lambda checked=False, u_id=uid: self.user_selected.emit(u_id))
            self._fav_layout.addWidget(btn, 0, Qt.AlignCenter)
            self._fav_buttons[uid] = btn

    def _on_nav_clicked(self, key):
        """Handle sidebar nav button clicks (legacy compat)."""
        self._switch_page(key)

    def _switch_page(self, key):
        """Switch the content area to show the selected page."""
        self._active_nav = key
        # Page map: welcome=0, users=1, teams=2, radio=3, settings=4
        page_map = {"welcome": 0, "users": 1, "teams": 2, "radio": 3, "settings": 4}
        self._content_stack.setCurrentIndex(page_map.get(key, 0))

        is_welcome = (key == "welcome")
        has_team = bool(getattr(self, '_active_team_name_cache', ''))
        settings_from_welcome = (key == "settings" and getattr(self, '_settings_back_page', '') == "welcome")
        show_sidebar = key in ("users", "teams", "radio", "settings") and not settings_from_welcome

        # Sidebar: visible only when a team is active (not from welcome)
        self._sidebar.setVisible(show_sidebar)
        if hasattr(self, '_content_frame'):
            self._content_frame.setStyleSheet(
                f"border-left: 1px solid {DARK['BORDER']};" if show_sidebar else "border: none;"
            )

        # Hide status bar and header on welcome/settings
        self._status_bar.setVisible(show_sidebar)
        self._content_header.setVisible(show_sidebar and key != "settings")

        # Panel width
        if is_welcome:
            self.setFixedWidth(260)
        elif settings_from_welcome:
            self.setFixedWidth(260)
        else:
            self.setFixedWidth(PANEL_W)

        # Header elements
        if key == "users":
            team_name = getattr(self, '_active_team_name_cache', 'Team')
            self._section_title.setText(f"{team_name} ▾")
            self._hamburger_btn.setText("⚙")
            self._hamburger_btn.setVisible(True)
        elif key == "settings":
            self._section_title.setText("Settings")
            self._hamburger_btn.setText("←")
            self._hamburger_btn.setVisible(True)
        elif key == "teams":
            self._section_title.setText("Teams")
            self._hamburger_btn.setText("←")
            self._hamburger_btn.setVisible(True)
        elif key == "radio":
            self._section_title.setText("Radio")
            self._hamburger_btn.setText("←")
            self._hamburger_btn.setVisible(True)

        # Populate settings when navigating to it
        if key == "settings":
            self._populate_settings()
        self._auto_resize()

    # ── Content Header (search + section title) ───────────────────
    def _build_content_header(self):
        """Header bar: clickable team name (dropdown) + hamburger menu button."""
        header = QFrame()
        header.setStyleSheet("border: none;")

        v = QVBoxLayout(header)
        v.setContentsMargins(12, 10, 12, 0)
        v.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 4, 0, 0)

        title_row.addStretch()

        # Team name button — clickable dropdown to switch teams
        self._section_title = QPushButton("TEAM ▾")
        self._section_title.setCursor(Qt.PointingHandCursor)
        self._section_title.setStyleSheet(f"""
            QPushButton {{
                font-size: 13px; font-weight: 700; color: {DARK['TEXT']};
                letter-spacing: 0.5px; border: none;
                background: {DARK['BG_RAISED']}; border-radius: 6px;
                padding: 4px 10px;
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
        """)
        self._section_title.clicked.connect(self._on_title_clicked)
        title_row.addWidget(self._section_title)

        # Hidden compat ref (some code checks this)
        self._team_dropdown_btn = self._section_title

        # Room code button — always visible, copies invite code
        self._room_code_btn = QPushButton("ROOM CODE")
        self._room_code_btn.setCursor(Qt.PointingHandCursor)
        self._room_code_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 9px; font-weight: 700; color: {DARK['TEXT_DIM']};
                background: transparent; border: 1px solid {DARK['BORDER']};
                border-radius: 4px; padding: 3px 8px; letter-spacing: 0.5px;
            }}
            QPushButton:hover {{ color: {DARK['TEXT']}; border-color: {DARK['TEXT_FAINT']}; }}
        """)
        self._room_code_btn.clicked.connect(lambda: (self._copy_invite_code(), self._show_copied_toast()))
        title_row.addWidget(self._room_code_btn)

        title_row.addStretch()

        # Hotline toggle (only visible during active calls)
        self._hotline_lbl = QLabel("Hotline")
        self._hotline_lbl.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_DIM']}; font-weight: 500; border: none;")
        self._hotline_lbl.setVisible(False)
        title_row.addWidget(self._hotline_lbl)

        self.open_toggle = ToggleSwitch()
        self.open_toggle.toggled.connect(self.hotline_toggled.emit)
        self.open_toggle.setVisible(False)
        title_row.addWidget(self.open_toggle)

        # Settings gear button — toggles settings page
        self._hamburger_btn = QPushButton("⚙")
        self._hamburger_btn.setFixedSize(36, 36)
        self._hamburger_btn.setCursor(Qt.PointingHandCursor)
        self._hamburger_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 24px; color: {DARK['TEXT']};
                background: transparent; border: none;
            }}
            QPushButton:hover {{ color: {DARK['TEAL']}; }}
        """)
        self._hamburger_btn.clicked.connect(self._toggle_settings)
        self._hamburger_btn.setVisible(False)
        title_row.addWidget(self._hamburger_btn)

        v.addLayout(title_row)

        return header

    def _on_title_clicked(self):
        """Title button click — team dropdown on users page, back on sub-pages."""
        if self._active_nav == "users":
            self._show_team_dropdown()
        else:
            self._switch_page("users")

    def _show_team_dropdown(self):
        """Show dropdown menu to switch teams or go back to teams list."""
        menu = QMenu(self)
        menu.setStyleSheet(f"""
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

        # List all teams from the hidden combo
        for i in range(self._team_combo.count()):
            team_name = self._team_combo.itemText(i)
            team_id = self._team_combo.itemData(i)
            is_active = (i == self._team_combo.currentIndex())
            action = menu.addAction(f"{'✓ ' if is_active else '   '}{team_name}")
            action.triggered.connect(
                lambda checked=False, tid=team_id, tn=team_name:
                    self._on_team_page_select(tid, tn)
            )

        menu.addSeparator()
        menu.addAction("Manage Teams...").triggered.connect(
            lambda: self._switch_page("teams")
        )

        menu.exec(self._team_dropdown_btn.mapToGlobal(
            QPoint(0, self._team_dropdown_btn.height() + 4)
        ))

    def _toggle_settings(self):
        """Gear/back button: on users/welcome opens settings, on settings goes back."""
        if self._active_nav == "settings":
            # Go back to where we came from
            back_to = getattr(self, '_settings_back_page', 'users')
            self._switch_page(back_to)
        else:
            self._settings_back_page = self._active_nav
            self._switch_page("settings")

    # ── Welcome Page (no team selected) ──────────────────────────
    def _build_welcome_page(self):
        """Welcome screen: OH logo, greeting, team list."""
        page = QWidget()
        page.setStyleSheet("border: none;")
        v = QVBoxLayout(page)
        v.setContentsMargins(24, 16, 24, 16)
        v.setSpacing(0)

        # Gear icon top-right
        gear_row = QHBoxLayout()
        gear_row.setContentsMargins(0, 0, 0, 0)
        gear_row.addStretch()
        self._welcome_gear = QPushButton("⚙")
        self._welcome_gear.setFixedSize(36, 36)
        self._welcome_gear.setCursor(Qt.PointingHandCursor)
        self._welcome_gear.setStyleSheet(f"""
            QPushButton {{
                font-size: 24px; color: {DARK['TEXT']};
                background: transparent; border: none;
            }}
            QPushButton:hover {{ color: {DARK['TEAL']}; }}
        """)
        self._welcome_gear.clicked.connect(self._toggle_settings)
        gear_row.addWidget(self._welcome_gear)
        v.addLayout(gear_row)

        # OH Logo from file
        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'oh_logo.png')
        if os.path.exists(logo_path):
            logo_px = QPixmap(logo_path).scaledToHeight(40, Qt.SmoothTransformation)
            logo.setPixmap(logo_px)
        v.addWidget(logo)
        v.addSpacing(6)

        # Welcome text
        welcome = QLabel("Welcome to Office Hours")
        welcome.setAlignment(Qt.AlignCenter)
        welcome.setStyleSheet(f"font-size: 13px; font-weight: 500; color: {DARK['TEXT_DIM']};")
        v.addWidget(welcome)
        v.addSpacing(14)

        # TEAMS label
        teams_hdr = QLabel("TEAMS")
        teams_hdr.setStyleSheet(f"font-size: 9px; font-weight: 700; color: {DARK['TEXT_FAINT']}; letter-spacing: 1.5px;")
        v.addWidget(teams_hdr)
        v.addSpacing(6)

        # Scrollable team list
        team_scroll = QScrollArea()
        team_scroll.setWidgetResizable(True)
        team_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        team_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        team_scroll.setFixedHeight(80)
        team_scroll.setStyleSheet(f"""
            QScrollArea {{ border: 1px solid {DARK['BORDER']}; border-radius: 6px; background: {DARK['BG_RAISED']}; }}
            QScrollBar:vertical {{ width: 4px; background: transparent; }}
            QScrollBar::handle:vertical {{ background: {DARK['BORDER']}; border-radius: 2px; min-height: 20px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        self._welcome_team_container = QWidget()
        self._welcome_team_layout = QVBoxLayout(self._welcome_team_container)
        self._welcome_team_layout.setContentsMargins(6, 4, 6, 4)
        self._welcome_team_layout.setSpacing(2)

        self._welcome_empty = QLabel("Loading teams...")
        self._welcome_empty.setAlignment(Qt.AlignCenter)
        self._welcome_empty.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_FAINT']}; padding: 16px;")
        self._welcome_team_layout.addWidget(self._welcome_empty)
        self._welcome_team_layout.addStretch()

        team_scroll.setWidget(self._welcome_team_container)
        v.addWidget(team_scroll)
        v.addSpacing(10)

        # Start a Team button (OH teal)
        create_btn = QPushButton("+ Start a Team")
        create_btn.setCursor(Qt.PointingHandCursor)
        create_btn.setStyleSheet(f"""
            QPushButton {{
                background: {DARK['TEAL']}; color: white;
                border: none; border-radius: 6px;
                padding: 8px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ background: #5c9a91; }}
        """)
        create_btn.clicked.connect(self._on_create_team_click)
        v.addWidget(create_btn)
        v.addSpacing(6)

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
        v.addLayout(divider_row)
        v.addSpacing(6)

        # Invite code row
        code_row = QHBoxLayout()
        code_row.setSpacing(6)
        self._welcome_code_input = QLineEdit()
        self._welcome_code_input.setPlaceholderText("OH-XXXXX")
        self._welcome_code_input.setAlignment(Qt.AlignCenter)
        self._welcome_code_input.setStyleSheet(f"""
            QLineEdit {{
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 6px; padding: 6px 8px; font-size: 12px;
                font-weight: 600; letter-spacing: 2px; color: {DARK['TEXT']};
            }}
            QLineEdit:focus {{ border-color: {DARK['TEXT_FAINT']}; }}
        """)
        self._welcome_code_input.setMaxLength(10)
        code_row.addWidget(self._welcome_code_input, 1)

        join_btn = QPushButton("Join")
        join_btn.setCursor(Qt.PointingHandCursor)
        join_btn.setStyleSheet(f"""
            QPushButton {{
                background: {DARK['ACCENT']}; color: white; border: none;
                border-radius: 6px; padding: 6px 14px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {DARK['ACCENT_DIM']}; }}
        """)
        join_btn.clicked.connect(self._on_welcome_join_click)
        code_row.addWidget(join_btn)
        v.addLayout(code_row)

        v.addSpacing(8)

        return page

    def _on_welcome_join_click(self):
        """Join button clicked on welcome page."""
        code = self._welcome_code_input.text().strip()
        if code:
            self.join_code_requested.emit(code)

    def _populate_welcome_teams(self, teams, my_teams=None, active_team_id=""):
        """Populate the welcome page team list."""
        # Clear existing
        while self._welcome_team_layout.count() > 0:
            item = self._welcome_team_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        all_teams = []
        if my_teams:
            for t in my_teams:
                all_teams.append((t, True))
        if teams:
            my_ids = {t['id'] for t in (my_teams or [])}
            for t in teams:
                if t['id'] not in my_ids:
                    all_teams.append((t, False))

        if not all_teams:
            empty = QLabel("No teams yet — start one!")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_FAINT']}; padding: 16px;")
            self._welcome_team_layout.addWidget(empty)
            self._welcome_team_layout.addStretch()
            return

        for team, is_member in all_teams:
            row = QFrame()
            row.setStyleSheet(f"""
                QFrame {{
                    background: transparent; border: none; border-radius: 4px;
                }}
                QFrame:hover {{ background: {DARK['BG_HOVER']}; }}
            """)
            h = QHBoxLayout(row)
            h.setContentsMargins(6, 4, 6, 4)
            h.setSpacing(6)

            name = QLabel(team.get("name", "Team"))
            name.setStyleSheet(f"font-size: 12px; font-weight: 500; color: {DARK['TEXT']}; border: none;")
            h.addWidget(name, 1)

            if is_member:
                select_btn = QPushButton("Select")
                select_btn.setCursor(Qt.PointingHandCursor)
                select_btn.setFixedSize(50, 22)
                select_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {COLORS['GREEN']}; color: white; border: none;
                        border-radius: 4px; font-size: 10px; font-weight: 700;
                    }}
                    QPushButton:hover {{ background: #2bbd6e; }}
                """)
                tid = team["id"]
                tn = team.get("name", "")
                select_btn.clicked.connect(
                    lambda checked=False, t_id=tid, t_name=tn:
                        self.team_selected_from_lobby.emit(t_id, t_name)
                )
                h.addWidget(select_btn)
            else:
                join_btn = QPushButton("Join")
                join_btn.setCursor(Qt.PointingHandCursor)
                join_btn.setFixedSize(40, 22)
                join_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {DARK['BG_RAISED']}; color: {DARK['TEXT_DIM']};
                        border: 1px solid {DARK['BORDER']}; border-radius: 4px;
                        font-size: 10px; font-weight: 600;
                    }}
                    QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
                """)
                tid = team["id"]
                tn = team.get("name", "")
                admin_id = team.get("created_by", "")
                join_btn.clicked.connect(
                    lambda checked=False, t_id=tid, t_name=tn, a_id=admin_id:
                        self.request_to_join.emit(t_id, t_name, a_id)
                )
                h.addWidget(join_btn)

            self._welcome_team_layout.addWidget(row)

        self._welcome_team_layout.addStretch()

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

    # ── Status Bar (bottom of content) — PTT + Page All ──────────
    def _build_status_bar(self):
        bar = QFrame()
        bar.setStyleSheet(f"border: none; background: transparent;")
        bar.setFixedHeight(56)

        v = QVBoxLayout(bar)
        v.setContentsMargins(8, 4, 8, 8)
        v.setSpacing(4)

        # Combined PTT / Page All bar with teal border
        btn_row = QHBoxLayout()
        btn_row.setSpacing(0)

        # PTT button — left side, ~2/3 width
        self.ptt_btn = QPushButton("PUSH TO TALK")
        self.ptt_btn.setCursor(Qt.PointingHandCursor)
        self.ptt_btn.setFixedHeight(40)
        self.ptt_btn.pressed.connect(self.ptt_pressed.emit)
        self.ptt_btn.released.connect(self.ptt_released.emit)
        self.ptt_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        btn_row.addWidget(self.ptt_btn, 2)

        # Thin divider
        divider = QFrame()
        divider.setFixedWidth(1)
        divider.setFixedHeight(40)
        divider.setStyleSheet(f"background: {DARK['TEAL']}; border: none;")
        btn_row.addWidget(divider)

        # Page All button — right side, ~1/3 width
        self.page_all_btn = QPushButton("PAGE ALL")
        self.page_all_btn.setCursor(Qt.PointingHandCursor)
        self.page_all_btn.setFixedHeight(40)
        self.page_all_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: 1px solid {DARK['TEAL']};
                border-left: none;
                border-top-right-radius: 8px; border-bottom-right-radius: 8px;
                border-top-left-radius: 0; border-bottom-left-radius: 0;
                padding: 6px 8px; font-size: 11px;
                font-weight: 700; color: {DARK['TEAL']};
                letter-spacing: 0.5px;
            }}
            QPushButton:hover {{ background: rgba(42, 191, 191, 0.10); color: {DARK['TEXT']}; }}
            QPushButton:pressed {{ background: rgba(42, 191, 191, 0.20); }}
        """)
        self.page_all_btn.pressed.connect(self.page_all_pressed.emit)
        self.page_all_btn.released.connect(self.page_all_released.emit)
        btn_row.addWidget(self.page_all_btn, 1)

        v.addLayout(btn_row)

        # VU meters row (hidden until connected)
        self._vu_row = QWidget()
        vu_h = QHBoxLayout(self._vu_row)
        vu_h.setContentsMargins(4, 0, 4, 0)
        vu_h.setSpacing(4)

        mic_lbl = QLabel("MIC")
        mic_lbl.setStyleSheet(f"font-size: 8px; font-weight: 700; color: {DARK['ACCENT']}; border: none; letter-spacing: 0.5px;")
        vu_h.addWidget(mic_lbl)
        self._ptt_mic_meter = UnicodeEQ(num_bars=6, color=DARK['ACCENT'])
        vu_h.addWidget(self._ptt_mic_meter)

        vu_h.addSpacing(8)

        rcv_lbl = QLabel("RCV")
        rcv_lbl.setStyleSheet(f"font-size: 8px; font-weight: 700; color: {DARK['INFO']}; border: none; letter-spacing: 0.5px;")
        vu_h.addWidget(rcv_lbl)
        self._ptt_spk_meter = UnicodeEQ(num_bars=6, color=DARK['INFO'])
        vu_h.addWidget(self._ptt_spk_meter)

        vu_h.addStretch()
        self._vu_row.setVisible(False)
        v.addWidget(self._vu_row)

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
        'RED': '#f06060',
    }
    _MODE_BG_COLORS = {
        'GREEN': 'rgba(0, 166, 81, 0.10)',
        'YELLOW': 'rgba(230, 175, 0, 0.10)',
        'RED': 'rgba(229, 57, 53, 0.10)',
    }
    _MODE_BORDER_COLORS = {
        'GREEN': 'rgba(0, 166, 81, 0.30)',
        'YELLOW': 'rgba(230, 175, 0, 0.30)',
        'RED': 'rgba(229, 57, 53, 0.30)',
    }

    def _update_mode_btn(self):
        """Update the traffic light dots to reflect current mode."""
        self._update_traffic_light()

    def _update_ptt_style(self):
        """Style the PTT button — teal-bordered, mode-aware."""
        mode = self._current_mode
        text_color = self._MODE_TEXT_COLORS.get(mode, '#4cdf80')

        if mode == 'RED':
            # DND — disabled look
            self.ptt_btn.setEnabled(False)
            self.ptt_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(229, 57, 53, 0.05);
                    border: 1px solid {DARK['BORDER']};
                    border-right: none;
                    border-top-left-radius: 8px; border-bottom-left-radius: 8px;
                    border-top-right-radius: 0; border-bottom-right-radius: 0;
                    padding: 8px; font-size: 12px;
                    font-weight: 700; color: {DARK['TEXT_FAINT']};
                    letter-spacing: 0.5px;
                }}
            """)
        else:
            self.ptt_btn.setEnabled(True)
            self.ptt_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: 1px solid {DARK['TEAL']};
                    border-right: none;
                    border-top-left-radius: 8px; border-bottom-left-radius: 8px;
                    border-top-right-radius: 0; border-bottom-right-radius: 0;
                    padding: 8px; font-size: 12px;
                    font-weight: 700; color: {text_color};
                    letter-spacing: 0.5px;
                }}
                QPushButton:hover {{ background: rgba(42, 191, 191, 0.08); }}
                QPushButton:pressed {{ background: rgba(42, 191, 191, 0.15); }}
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
        self._invite_input.setPlaceholderText("Enter code")
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
        self._invite_input.textChanged.connect(self._format_invite_code)
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

        # Also populate the welcome page team list
        self._populate_welcome_teams(teams, my_teams=my_teams, active_team_id=active_team_id)

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
        orb = SmallOrb('GREEN')  # Status orb for join requests
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

    def _format_invite_code(self, text):
        """Auto-capitalize and prepend OH- as the user types."""
        # Block re-entrant calls while we update the text
        self._invite_input.blockSignals(True)
        cursor_pos = self._invite_input.cursorPosition()

        # Strip whitespace and uppercase
        raw = text.strip().upper()
        # Remove any existing OH- prefix so we can re-add it cleanly
        if raw.startswith("OH-"):
            raw = raw[3:]
        elif raw.startswith("OH"):
            raw = raw[2:]
        # Remove dashes from the code portion
        raw = raw.replace("-", "")
        # Rebuild with OH- prefix if user has typed anything
        if raw:
            formatted = f"OH-{raw}"
        else:
            formatted = ""

        self._invite_input.setText(formatted)
        # Keep cursor in a reasonable spot
        self._invite_input.setCursorPosition(min(cursor_pos + (len(formatted) - len(text)), len(formatted)))
        self._invite_input.blockSignals(False)

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
        If no teams or force_lobby, show welcome page instead.
        """
        has_teams = bool(teams)

        if force_lobby or not has_teams:
            # Show welcome page (no team selected)
            self._is_onboarding = True
            self._onboarding.setVisible(False)  # Don't use old overlay
            # Populate welcome team list with user's teams
            self._populate_welcome_teams([], my_teams=teams)
            self._switch_page("welcome")
            return

        # Transition to team-active UI
        self._onboarding.setVisible(False)
        self._is_onboarding = False
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)  # Qt default max

        self._team_combo.blockSignals(True)
        self._team_combo.clear()
        active_index = 0
        for i, team in enumerate(teams):
            self._team_combo.addItem(team["name"], team["id"])
            self._team_combo.setItemData(i, team.get("role", "member"), Qt.UserRole + 1)
            self._team_combo.setItemData(i, team.get("invite_code", ""), Qt.UserRole + 2)
            if team["id"] == active_team_id:
                active_index = i
        self._team_combo.setCurrentIndex(active_index)
        self._team_combo.blockSignals(False)

        # Refresh the visual team list on the teams management page
        self._refresh_teams_list(teams, active_team_id)

        # Cache active team name for header display
        active_name = ""
        for t in teams:
            if t["id"] == active_team_id:
                active_name = t["name"]
                break
        self._active_team_name_cache = active_name
        self.set_sidebar_team(active_name)

        # Go to users page (team is active)
        if active_team_id:
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
        if hasattr(self, '_ptt_mic_meter'):
            self._ptt_mic_meter.set_level(level)

    def set_speaker_level(self, level):
        """Update speaker level meter (0.0–1.0)."""
        self.speaker_meter.set_level(level)
        if hasattr(self, '_ptt_spk_meter'):
            self._ptt_spk_meter.set_level(level)

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
        """Show the new message indicator — sidebar mail icon only."""
        self._sidebar_mail_btn.setVisible(True)
        self._mail_pulse_on = True
        self._mail_pulse_timer.start()

    def hide_message(self):
        """Hide the message indicator — sidebar mail icon."""
        self._sidebar_mail_btn.setVisible(False)
        self._mail_pulse_timer.stop()

    def _pulse_mail_icon(self):
        """Alternate mail icon opacity for a pulsing effect."""
        self._mail_pulse_on = not self._mail_pulse_on
        if self._mail_pulse_on:
            self._sidebar_mail_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; border: none;
                    border-radius: 19px; font-size: 20px; color: {DARK['WARN']};
                }}
                QPushButton:hover {{ background: rgba(230, 175, 0, 0.15); }}
            """)
        else:
            self._sidebar_mail_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; border: none;
                    border-radius: 19px; font-size: 20px; color: {DARK['TEXT_FAINT']};
                }}
                QPushButton:hover {{ background: rgba(230, 175, 0, 0.15); }}
            """)

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
            self.ptt_btn.setText("HOTLINE")
            self.ptt_mode_label.setText("Hotline — open mic, same-room feel.")
            self.ptt_mode_label.setVisible(True)
        elif in_call:
            self.ptt_btn.setText(f"TALKING TO {in_call.upper()}")
            self.ptt_mode_label.setVisible(False)
        else:
            self.ptt_btn.setText("PUSH TO TALK")
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
        """Set the display name shown in the pinned compact bar."""
        self._display_name = name
        self._update_pinned_style()

    def _peer_initials(self, name):
        """Return 1–2 character initials for a name."""
        if not name:
            return ""
        parts = name.strip().split()
        if len(parts) >= 2:
            return parts[0][0].upper() + parts[-1][0].upper()
        elif parts:
            return parts[0][0].upper()
        return ""

    def _update_peer_badge_style(self, mode=""):
        """Style the peer initials badge based on peer's mode color."""
        if not mode:
            # Empty/hidden state
            self._sidebar_peer_initials.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; border: none;
                    border-radius: 19px; font-size: 15px; font-weight: 800;
                    color: transparent;
                }}
            """)
            return
        bg = COLORS.get(mode, COLORS['GREEN'])
        # Use white text on dark backgrounds (red, green), black on bright (yellow)
        text_color = "#000" if mode == "YELLOW" else "#fff"
        self._sidebar_peer_initials.setStyleSheet(f"""
            QPushButton {{
                background: {bg}; border: none;
                border-radius: 19px; font-size: 15px; font-weight: 800;
                color: {text_color};
            }}
            QPushButton:hover {{ background: {bg}; border: 2px solid #fff; }}
        """)

    def set_peer_mode(self, mode):
        """Update the connected peer's mode color on the sidebar badge."""
        self._sidebar_peer_mode = mode
        if self._sidebar_peer_initials.text():
            self._update_peer_badge_style(mode)

    def _show_quick_switch_menu(self):
        """Show a popup menu of online team users for quick switching."""
        if not self._sidebar_peer_initials.text():
            return  # No peer connected
        from PySide6.QtWidgets import QMenu, QWidgetAction
        menu = QMenu(self)
        menu.setStyleSheet(f"""
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
        # Get online users from the user rows (skip offline and current target)
        added = 0
        for uid, row in self._user_rows.items():
            if row._offline:
                continue
            color = COLORS.get(row._mode, COLORS['GREEN'])
            label = QLabel(f'<span style="color:{color};">●</span> {row.name_label.text()}')
            label.setStyleSheet(f"padding: 6px 16px; font-size: 12px; color: {DARK['TEXT']};")
            label.setCursor(Qt.PointingHandCursor)
            wa = QWidgetAction(menu)
            wa.setDefaultWidget(label)
            wa.triggered.connect(lambda checked=False, u=uid: self._quick_switch_to(u))
            menu.addAction(wa)
            added += 1
        if added == 0:
            menu.addAction("No other users online").setEnabled(False)
        # Show below the badge
        menu.exec(self._sidebar_peer_initials.mapToGlobal(
            QPoint(self._sidebar_peer_initials.width() + 4, 0)
        ))

    def _quick_switch_to(self, user_id):
        """Quick-switch PTT target from the sidebar popup."""
        # Deselect current row, select new one
        for uid, row in self._user_rows.items():
            if row._state == UserRow.STATE_SELECTED:
                row.set_state(UserRow.STATE_IDLE)
        row = self._user_rows.get(user_id)
        if row:
            row.set_state(UserRow.STATE_SELECTED)
        self.user_selected.emit(user_id)

    def set_connection(self, connected, peer_name="", peer_mode=""):
        """Switch between connected and disconnected states.
        Banners are now hidden — connection state is conveyed by row highlights."""
        self._connected = connected
        self._conn_bar.setVisible(False)
        self._disconn_bar.setVisible(False)
        if hasattr(self, '_vu_row'):
            self._vu_row.setVisible(connected)
        if connected and peer_name:
            self.conn_label.setText(f"Connected to {peer_name}")
            self._sidebar_peer_initials.setText(self._peer_initials(peer_name))
            self._sidebar_peer_initials.setToolTip(peer_name)
            self._sidebar_peer_mode = peer_mode or "GREEN"
            self._update_peer_badge_style(self._sidebar_peer_mode)
        elif connected:
            self.conn_label.setText("Connected")
        else:
            self._sidebar_peer_initials.setText("")
            self._sidebar_peer_initials.setToolTip("")
            self._sidebar_peer_mode = ""
            self._update_peer_badge_style("")

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

        online_count = sum(1 for u in users if u.get('mode') != 'OFFLINE')
        self.online_count.setText(str(online_count))

        # Update sidebar favorites
        self._update_favorites(users)

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
            self.ptt_btn.setText(f"TALKING TO {caller_name.upper()}")
        # Show hotline toggle during active calls
        self._hotline_lbl.setVisible(True)
        self.open_toggle.setVisible(True)
        self._resize_panel()

    def hide_call(self):
        """Hide the in-call banner and restore normal layout."""
        self._hide_all_banners()
        self._call_peer_name = None
        # Reset PTT text
        if self._is_open_line:
            self.ptt_btn.setText("HOTLINE")
        else:
            self.ptt_btn.setText("PUSH TO TALK")
        # Hide hotline toggle when not in a call
        self._hotline_lbl.setVisible(False)
        self.open_toggle.setVisible(False)
        self._resize_panel()

    def set_ptt_active(self, active):
        """Visual feedback when PTT is held."""
        if active:
            self.ptt_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(226, 42, 26, 0.15); border: 2px solid {DARK['DANGER']};
                    border-right: none;
                    border-top-left-radius: 8px; border-bottom-left-radius: 8px;
                    border-top-right-radius: 0; border-bottom-right-radius: 0;
                    padding: 8px; font-size: 12px;
                    font-weight: 700; color: {DARK['DANGER']};
                    letter-spacing: 0.5px;
                }}
            """)
        else:
            # Restore normal style
            self._update_ptt_style()

    def set_ptt_locked(self, locked):
        """Disable PTT while the peer is talking."""
        if locked:
            self.ptt_btn.setEnabled(False)
            self.ptt_btn.setText("LISTENING...")
            self.ptt_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {DARK['BG_RAISED']};
                    border: 1px solid {DARK['BORDER']};
                    border-right: none;
                    border-top-left-radius: 8px; border-bottom-left-radius: 8px;
                    border-top-right-radius: 0; border-bottom-right-radius: 0;
                    padding: 8px; font-size: 12px;
                    font-weight: 600; color: {DARK['TEXT_FAINT']};
                    letter-spacing: 0.5px;
                }}
            """)
        else:
            self.ptt_btn.setEnabled(True)
            # Restore text
            in_call = getattr(self, '_call_peer_name', None)
            if self._is_open_line:
                self.ptt_btn.setText("HOTLINE")
            elif in_call:
                self.ptt_btn.setText(f"TALKING TO {in_call.upper()}")
            else:
                self.ptt_btn.setText("PUSH TO TALK")
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
            self._radio_player.errorOccurred.connect(self._on_radio_error)

        self._radio_player.stop()
        # Channel 1 and 2 have separate stream URLs
        urls = [
            "https://stream-relay-geo.ntslive.net/stream?client=NTSRadio",
            "https://stream-relay-geo.ntslive.net/stream2?client=NTSRadio",
        ]
        print(f"[Radio] Starting stream: {urls[self._radio_channel]}")
        self._radio_player.setSource(QUrl(urls[self._radio_channel]))
        self._radio_player.play()

    def _on_radio_error(self, error, message=""):
        """Handle media player errors — reset state so user can retry."""
        print(f"[Radio] Error: {error} — {message}")
        self._stop_radio()

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
        v.setContentsMargins(0, 8, 0, 8)
        v.setSpacing(0)

        # ── Header: back button ──
        header = QHBoxLayout()
        header.setContentsMargins(12, 4, 12, 4)

        self._settings_back_btn = QPushButton("←")
        self._settings_back_btn.setFixedSize(32, 32)
        self._settings_back_btn.setCursor(Qt.PointingHandCursor)
        self._settings_back_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 18px; color: {DARK['TEXT_DIM']};
                background: transparent; border: none;
            }}
            QPushButton:hover {{ color: {DARK['TEXT']}; }}
        """)
        self._settings_back_btn.clicked.connect(self._toggle_settings)
        header.addWidget(self._settings_back_btn)
        header.addStretch()
        v.addLayout(header)

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
        self._settings_layout.setContentsMargins(8, 0, 8, 8)
        self._settings_layout.setSpacing(0)

        scroll.setWidget(container)
        scroll.setMinimumHeight(250)
        v.addWidget(scroll, 1)

        return view

    def _populate_settings(self):
        """Rebuild settings — movie credits style: labels left, values right."""
        layout = self._settings_layout

        # Clear existing items
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        label_style = f"""
            QLabel {{
                font-size: 11px; font-weight: 600; color: {DARK['TEXT_DIM']};
                text-transform: uppercase; border: none; padding: 4px 0;
            }}
        """
        value_style = f"""
            QPushButton {{
                text-align: right; padding: 4px 0; font-size: 12px;
                font-weight: 600; color: {DARK['TEXT']};
                background: transparent; border: none; border-radius: 4px;
            }}
            QPushButton:hover {{ color: {DARK['TEAL']}; }}
        """
        menu_style = f"""
            QMenu {{
                background: {DARK['BG_RAISED']}; color: {DARK['TEXT']};
                border: 1px solid {DARK['BORDER']}; border-radius: 6px; padding: 4px;
            }}
            QMenu::item {{ padding: 5px 12px; border-radius: 4px; }}
            QMenu::item:selected {{ background: {DARK['BG_HOVER']}; }}
        """

        def _label(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(label_style)
            return lbl

        def _value(text):
            btn = QPushButton(text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(value_style)
            return btn

        def _credit(label_text, value_widget):
            """One movie-credits row: label left, value right."""
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(14, 0, 14, 0)
            h.setSpacing(8)
            h.addWidget(_label(label_text))
            h.addStretch()
            h.addWidget(value_widget)
            return row

        def _spacer(h=6):
            s = QWidget()
            s.setFixedHeight(h)
            return s

        # ── OH Logo ──
        import os
        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet("border: none;")
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'oh_logo.png')
        if os.path.exists(logo_path):
            logo_px = QPixmap(logo_path).scaledToHeight(32, Qt.SmoothTransformation)
            logo.setPixmap(logo_px)
        layout.addWidget(logo)

        subtitle = QLabel("Settings")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {DARK['TEXT_DIM']}; border: none; padding: 0 0 8px 0;")
        layout.addWidget(subtitle)

        # ── Name ──
        display_name = getattr(self, '_display_name', None) or 'Not set'
        name_val = _value(display_name)
        name_val.clicked.connect(lambda: (self._change_name_dialog(), self._populate_settings()))
        layout.addWidget(_credit("Name", name_val))

        # ── Theme ──
        theme_text = "Light" if self._dark_mode else "Dark"
        theme_val = _value(theme_text)
        theme_val.clicked.connect(lambda: (self._toggle_dark_mode(), self._populate_settings()))
        layout.addWidget(_credit("Theme", theme_val))

        # ── Incognito ──
        incognito_text = "On" if self._incognito else "Off"
        incognito_val = _value(incognito_text)
        incognito_val.clicked.connect(lambda: (self._toggle_incognito(), self._populate_settings()))
        layout.addWidget(_credit("Incognito", incognito_val))

        layout.addWidget(_spacer(8))

        # ── Audio In ──
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

            in_val = _value(current_in_name + "  ▾")
            in_menu = QMenu(in_val)
            in_menu.setStyleSheet(menu_style)
            for name, idx in input_devices:
                action = in_menu.addAction(name)
                action.triggered.connect(
                    lambda checked=False, i=idx: (self._on_input_device_changed(i), self._populate_settings())
                )
            in_val.setMenu(in_menu)
            layout.addWidget(_credit("Audio In", in_val))

            out_val = _value(current_out_name + "  ▾")
            out_menu = QMenu(out_val)
            out_menu.setStyleSheet(menu_style)
            for name, idx in output_devices:
                action = out_menu.addAction(name)
                action.triggered.connect(
                    lambda checked=False, i=idx: (self._on_output_device_changed(i), self._populate_settings())
                )
            out_val.setMenu(out_menu)
            layout.addWidget(_credit("Audio Out", out_val))
        except Exception as e:
            print(f"Audio device settings error: {e}")

        layout.addWidget(_spacer(8))

        # ── Send Invite ──
        invite_val = _value("Send")
        invite_val.clicked.connect(self._invite_friend_email)
        layout.addWidget(_credit("Invite", invite_val))

        # ── Radio ──
        is_playing = getattr(self, '_radio_playing', False)
        play_text = "Stop" if is_playing else "Play"
        play_color = DARK['DANGER'] if is_playing else DARK['ACCENT']
        radio_val = _value(play_text)
        radio_val.setStyleSheet(value_style.replace(f"color: {DARK['TEXT']}", f"color: {play_color}"))
        radio_val.clicked.connect(lambda: (self._toggle_radio(), self._populate_settings()))
        layout.addWidget(_credit("Radio", radio_val))

        # ── Volume ──
        vol_slider = QSlider(Qt.Horizontal)
        vol_slider.setRange(0, 100)
        vol_slider.setValue(self._radio_volume.value() if hasattr(self, '_radio_volume') else 20)
        vol_slider.setFixedWidth(100)
        vol_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 3px; background: {DARK['BORDER']}; border-radius: 1px;
            }}
            QSlider::handle:horizontal {{
                width: 10px; height: 10px; margin: -4px 0;
                background: {DARK['TEXT_DIM']}; border-radius: 5px;
            }}
            QSlider::handle:horizontal:hover {{ background: {DARK['TEXT']}; }}
            QSlider::sub-page:horizontal {{ background: {DARK['ACCENT_DIM']}; border-radius: 1px; }}
        """)
        vol_slider.valueChanged.connect(self._on_radio_volume)
        vol_slider.valueChanged.connect(lambda v: self._radio_volume.setValue(v) if hasattr(self, '_radio_volume') else None)
        layout.addWidget(_credit("Volume", vol_slider))

        layout.addWidget(_spacer(8))

        # ── Plug Ins ──
        deck_connected = getattr(self, '_deck_connected', False)
        if deck_connected:
            deck_val = _value("Connected")
            deck_val.setStyleSheet(value_style.replace(f"color: {DARK['TEXT']}", f"color: {DARK['ACCENT']}"))
            deck_val.setEnabled(False)
        else:
            deck_val = _value("Setup")
            deck_val.clicked.connect(self._show_deck_setup_guide)
        layout.addWidget(_credit("Plug Ins", deck_val))

        # ── Leave Team | Quit ──
        layout.addStretch()

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {DARK['BORDER_LT']}; margin: 0 14px;")
        layout.addWidget(divider)
        layout.addWidget(_spacer(4))

        leave_val = _value("Leave")
        leave_val.setStyleSheet(value_style.replace(f"color: {DARK['TEXT']}", f"color: {DARK['WARN']}"))
        leave_val.clicked.connect(lambda: (self._close_settings(), self._confirm_leave_team()))
        layout.addWidget(_credit("Team", leave_val))

        quit_val = _value("Quit")
        quit_val.setStyleSheet(value_style.replace(f"color: {DARK['TEXT']}", f"color: {DARK['DANGER']}"))
        quit_val.clicked.connect(self.quit_requested.emit)
        layout.addWidget(_credit("Office Hours", quit_val))

    def set_deck_status(self, connected, deck_name="Stream Deck"):
        """Update Stream Deck connection status (called from main.py)."""
        self._deck_connected = connected
        self._deck_name = deck_name

    def _show_deck_setup_guide(self):
        """Show Stream Deck setup instructions for the Elgato plugin."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
        from PySide6.QtGui import QPixmap, QImage
        dlg = QDialog(self, Qt.Dialog | Qt.WindowStaysOnTopHint)
        dlg.setWindowTitle("Stream Deck Setup")
        dlg.setFixedWidth(380)
        dlg.setStyleSheet(f"""
            QDialog {{ background: {DARK['BG']}; border: 1px solid {DARK['BORDER']}; border-radius: 10px; }}
            QLabel {{ color: {DARK['TEXT']}; border: none; }}
        """)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        title = QLabel("🎛  Stream Deck Setup")
        title.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {DARK['TEXT']}; border: none;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Office Hours works with the Elgato Stream Deck app.\n"
            "The plugin was auto-installed — just add the actions:"
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"font-size: 12px; color: {DARK['TEXT_DIM']}; border: none;")
        layout.addWidget(subtitle)

        steps = QLabel(
            "1. Open the Stream Deck app\n\n"
            "2. In the right sidebar, find the\n"
            "   \"Office Hours\" category\n\n"
            "3. Drag these actions onto your deck:\n\n"
            "   • Push to Talk — hold to talk\n"
            "   • Status Mode — cycle availability\n"
            "   • OH Logo — shows status & previews\n"
            "   • Switch Team — cycle teams\n"
            "   • Select User — cycle users\n"
            "   • Show Panel — open the OH window\n\n"
            "Suggested layout (top row):\n"
            "   PTT  |  Mode  |  Logo"
        )
        steps.setWordWrap(True)
        steps.setStyleSheet(f"font-size: 12px; color: {DARK['TEXT_DIM']}; line-height: 1.4; border: none;")
        layout.addWidget(steps)

        # "Don't show again" + OK buttons
        from PySide6.QtWidgets import QHBoxLayout, QCheckBox
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        dismiss_cb = QCheckBox("Don't show again")
        dismiss_cb.setStyleSheet(f"color: {DARK['TEXT_DIM']}; font-size: 11px; border: none;")
        btn_row.addWidget(dismiss_cb)

        btn_row.addStretch()

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
        btn_row.addWidget(ok_btn)

        layout.addLayout(btn_row)

        # Position near the app panel
        panel_geo = self.geometry()
        dlg.adjustSize()
        dlg_w = dlg.width()
        dlg_h = dlg.height()
        x = panel_geo.x() + panel_geo.width() + 8
        y = panel_geo.y()
        from PySide6.QtWidgets import QApplication
        screen = QApplication.screenAt(panel_geo.center())
        if screen:
            screen_geo = screen.availableGeometry()
            if x + dlg_w > screen_geo.right():
                x = panel_geo.x() - dlg_w - 8
            if y + dlg_h > screen_geo.bottom():
                y = screen_geo.bottom() - dlg_h
        dlg.move(x, y)

        dlg.exec()

        if dismiss_cb.isChecked():
            self.deck_guide_dismissed.emit()

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
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit
        idx = self._team_combo.currentIndex()
        code = ""
        team_name = "Office Hours"
        if idx >= 0:
            code = self._team_combo.itemData(idx, Qt.UserRole + 2) or ""
            team_name = self._team_combo.currentText() or "Office Hours"

        sender_name = getattr(self, '_display_name', None) or "A teammate"

        # Build dialog — use explicit flags so it doesn't inherit
        # the panel's frameless/tool flags which can make it invisible
        dlg = QDialog(self, Qt.Dialog | Qt.WindowStaysOnTopHint)
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

        def _do_send(checked=False):
            email = email_input.text().strip()
            print(f"[Invite] _do_send called, email={email!r}")
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
                try:
                    print(f"[Invite] Calling send_invite_email({email!r}, {team_name!r})")
                    from supabase_client import send_invite_email
                    result = send_invite_email(email, team_name, code, sender_name)
                    print(f"[Invite] Result: {result}")
                except Exception as e:
                    print(f"[Invite] Exception: {e}")
                    result = None
                # Update UI from main thread via QTimer.singleShot
                if result:
                    QTimer.singleShot(0, lambda: _on_result(True))
                else:
                    QTimer.singleShot(0, lambda: _on_result(False))

            def _on_result(success):
                try:
                    if success:
                        status_label.setText("Sent!")
                        status_label.setStyleSheet(f"font-size: 11px; color: {DARK['ACCENT']}; border: none;")
                        status_label.setVisible(True)
                        QTimer.singleShot(1000, dlg.accept)
                    else:
                        status_label.setText("Failed to send. Try again.")
                        status_label.setStyleSheet(f"font-size: 11px; color: {DARK['DANGER']}; border: none;")
                        status_label.setVisible(True)
                        send_btn.setEnabled(True)
                        send_btn.setText("Send Invite")
                except RuntimeError:
                    pass  # Dialog already closed

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
    PANEL_H = 460
    WELCOME_H = 380

    def _auto_resize(self):
        """Set panel to a fixed height so it stays consistent across pages."""
        if self._pinned:
            return
        if self._active_nav == "welcome":
            self.setFixedHeight(self.WELCOME_H)
        else:
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
            tray.setIcon(create_oh_icon(COLORS['GREEN']))
        else:
            tray.setIcon(create_oh_icon(COLORS[modes[mode_idx[0]]]))

    panel.hotline_toggled.connect(on_hotline_toggle)

    def on_leave():
        panel.set_connection(False)

    panel.leave_requested.connect(on_leave)

    sys.exit(app.exec())
