"""
floating_panel.py — Vox Menu Bar Panel
Frameless popup widget anchored to the system tray icon.
Matches the wireframe at menubar_wireframe.html.
"""
import json
import os
import sys
import threading
from urllib.request import Request, urlopen

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

# Shared constants (extracted to ui_constants.py)
from ui_constants import (
    COLORS,
    DARK,
    LIGHT,
    MODE_LABELS,
    PANEL_RADIUS,
    PANEL_W,
    SIDEBAR_W,
    STRIP_AVATAR_SIZE,
    STRIP_RADIUS,
    STRIP_W,
)

# Snapshot the original dark palette so we can restore it after switching to light
_DARK_ORIGINAL = dict(DARK)

# Widget classes (extracted to widgets.py)
from widgets import GlowingOrb, SmallOrb, ToggleSwitch, UnicodeEQ, UserRow

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
    login_completed = Signal(dict)                 # emitted with session data after successful auth
    login_skipped = Signal()                       # emitted when user clicks "Skip"
    sign_out_requested = Signal()                  # emitted when user clicks "Sign Out" in settings

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
        self._cached_users = []  # Last user list for strip avatar rebuilds
        self._strip_selected_uid = ""  # Currently selected user in strip
        self._fav_selected_uid = ""  # Currently selected user in sidebar favorites
        self._fav_ring_timer = QTimer(self)  # Animated ring on selected favorite
        self._fav_ring_opacity = 0.6
        self._fav_ring_dir = 1
        self._fav_ring_timer.timeout.connect(self._fav_ring_step)


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

        # Page 5: Login
        self._login_page = self._build_login_page()
        self._content_stack.addWidget(self._login_page)

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

        # ── Compact vertical strip (hidden by default) ────
        self._compact_strip = self._build_compact_strip()
        self._compact_strip.setVisible(False)

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
        oh_logo = QLabel()
        oh_logo.setAlignment(Qt.AlignCenter)
        oh_logo.setFixedHeight(28)
        _logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'oh_logo.png')
        if os.path.exists(_logo_path):
            _logo_px = QPixmap(_logo_path).scaledToHeight(20, Qt.SmoothTransformation)
            oh_logo.setPixmap(_logo_px)
        else:
            oh_logo.setText("VOX")
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

        # Hidden radio button placeholder (radio controls live in settings now)
        self._sidebar_radio_btn = QPushButton()
        self._sidebar_radio_btn.setVisible(False)

        # ── Mail icon (hidden by default, shown when messages are waiting) ──
        self._sidebar_mail_btn = QPushButton("✉")
        self._sidebar_mail_btn.setFixedSize(36, 36)
        self._sidebar_mail_btn.setCursor(Qt.PointingHandCursor)
        self._sidebar_mail_btn.setVisible(False)
        self._sidebar_mail_btn.setToolTip("Messages waiting")
        self._sidebar_mail_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                border-radius: 18px; font-size: 22px; color: {DARK['WARN']};
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

        # ── Status orb dropdown (single orb, expands to pick R/Y/G) ──
        status_container = QVBoxLayout()
        status_container.setContentsMargins(5, 0, 5, 0)
        status_container.setAlignment(Qt.AlignCenter)
        status_container.setSpacing(2)

        status_label = QLabel("STATUS")
        status_label.setAlignment(Qt.AlignCenter)
        status_label.setStyleSheet(
            f"font-size: 8px; font-weight: 700; color: {DARK['TEXT_FAINT']};"
            " letter-spacing: 1px; border: none;"
        )
        status_container.addWidget(status_label)

        self._status_orb_frame = QFrame()
        self._status_orb_frame.setObjectName("statusOrb")
        self._status_orb_frame.setFixedSize(32, 32)
        self._status_orb_frame.setStyleSheet(f"""
            QFrame#statusOrb {{
                background: transparent;
                border: 1px solid {DARK['TEAL']};
                border-radius: 8px;
            }}
        """)
        orb_layout = QVBoxLayout(self._status_orb_frame)
        orb_layout.setContentsMargins(0, 0, 0, 0)
        orb_layout.setAlignment(Qt.AlignCenter)

        self._status_orb_btn = QPushButton("●")
        self._status_orb_btn.setFixedSize(30, 30)
        self._status_orb_btn.setCursor(Qt.PointingHandCursor)
        self._status_orb_btn.setToolTip("Change status")
        self._status_orb_btn.clicked.connect(self._show_status_dropdown)
        orb_layout.addWidget(self._status_orb_btn, 0, Qt.AlignCenter)

        status_container.addWidget(self._status_orb_frame, 0, Qt.AlignCenter)
        v.addLayout(status_container)

        # Keep legacy refs for compat
        self._traffic_dots = {}

        # Initial orb styling
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
        """Style the single status orb to reflect the current mode."""
        mode = getattr(self, '_current_mode', 'GREEN')
        color = COLORS.get(mode, COLORS['GREEN'])
        self._status_orb_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                border-radius: 10px;
                font-size: 32px; color: {color};
                padding-bottom: 4px;
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
        """)
        self._status_orb_btn.setToolTip(MODE_LABELS.get(mode, mode))

    def _show_status_dropdown(self):
        """Show a dropdown menu to pick status (Available / Busy / DND)."""
        from PySide6.QtWidgets import QWidgetAction
        mode = getattr(self, '_current_mode', 'GREEN')
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 6px; padding: 4px;
            }}
        """)
        for mode_key, color in [("GREEN", COLORS['GREEN']), ("YELLOW", COLORS['YELLOW']), ("RED", COLORS['RED'])]:
            label = MODE_LABELS.get(mode_key, mode_key)
            is_active = (mode_key == mode)
            prefix = "✓ " if is_active else "   "

            row = QWidget()
            row.setCursor(Qt.PointingHandCursor)
            h = QHBoxLayout(row)
            h.setContentsMargins(10, 5, 14, 5)
            h.setSpacing(6)

            check_lbl = QLabel(prefix)
            check_lbl.setStyleSheet(f"font-size: 12px; color: {DARK['TEXT']}; border: none;")
            check_lbl.setFixedWidth(16)
            h.addWidget(check_lbl)

            dot = QLabel("●")
            dot.setStyleSheet(f"font-size: 14px; color: {color}; border: none;")
            h.addWidget(dot)

            text_lbl = QLabel(label)
            text_lbl.setStyleSheet(f"font-size: 12px; color: {DARK['TEXT']}; border: none;")
            h.addWidget(text_lbl)
            h.addStretch()

            row.setStyleSheet(f"""
                QWidget {{ border-radius: 4px; }}
                QWidget:hover {{ background: {DARK['BG_HOVER']}; }}
            """)

            wa = QWidgetAction(menu)
            wa.setDefaultWidget(row)
            wa.triggered.connect(
                lambda checked=False, mk=mode_key: self.mode_set_requested.emit(mk)
            )
            menu.addAction(wa)
        menu.exec(self._status_orb_frame.mapToGlobal(
            QPoint(self._status_orb_frame.width() + 4, 0)
        ))

    def _reset_sidebar_radio_btn(self):
        """Reset sidebar radio button to 'off' style."""
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

    def _sidebar_radio_toggle(self):
        """Toggle NTS radio from sidebar button. Show/hide volume popup."""
        self._toggle_radio()
        if self._radio_playing:
            # Update button to show "on" state
            self._sidebar_radio_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(42, 191, 191, 0.15); border: none;
                    border-radius: 18px; font-size: 18px;
                }
                QPushButton:hover { background: rgba(42, 191, 191, 0.25); }
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
        popup.setFixedSize(36, 120)

        v = QVBoxLayout(popup)
        v.setContentsMargins(4, 8, 4, 8)
        v.setSpacing(4)

        icon = QLabel("🔊")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("border: none; font-size: 12px;")
        v.addWidget(icon)

        vol = QSlider(Qt.Vertical)
        vol.setRange(0, 100)
        vol.setValue(self._radio_volume.value() if hasattr(self, '_radio_volume') else 20)
        vol.setStyleSheet(f"""
            QSlider::groove:vertical {{
                width: 4px; background: {DARK['BORDER']}; border-radius: 2px;
            }}
            QSlider::handle:vertical {{
                background: {DARK['TEAL']}; width: 12px; height: 12px;
                margin: 0 -4px; border-radius: 6px;
            }}
            QSlider::sub-page:vertical {{ background: {DARK['BORDER']}; border-radius: 2px; }}
            QSlider::add-page:vertical {{ background: {DARK['TEAL']}; border-radius: 2px; }}
        """)
        vol.valueChanged.connect(self._on_radio_volume)
        if hasattr(self, '_radio_volume'):
            vol.valueChanged.connect(lambda v_val: self._radio_volume.setValue(v_val))
        v.addWidget(vol, 1, Qt.AlignHCenter)

        # Position to the right of the radio button
        btn_pos = self._sidebar_radio_btn.mapTo(self, QPoint(0, 0))
        popup.move(btn_pos.x() + self._sidebar_radio_btn.width() + 4,
                   btn_pos.y() - 40)
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
            is_selected = (uid == self._fav_selected_uid)
            self._style_fav_button(btn, mode, is_selected)
            btn.clicked.connect(lambda checked=False, u_id=uid: self.user_selected.emit(u_id))
            self._fav_layout.addWidget(btn, 0, Qt.AlignCenter)
            self._fav_buttons[uid] = btn

    def _style_fav_button(self, btn, mode, selected=False):
        """Apply styling to a sidebar favorite button. Selected gets a bright ring."""
        color = COLORS.get(mode, COLORS['GREEN'])
        if selected:
            qc = QColor(color)
            r, g, b = qc.red(), qc.green(), qc.blue()
            alpha = self._fav_ring_opacity
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {color}; color: white;
                    border: 3px solid rgba({r}, {g}, {b}, {alpha:.2f});
                    border-radius: 18px; font-size: 12px; font-weight: 800;
                }}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {color}; color: white; border: none;
                    border-radius: 18px; font-size: 12px; font-weight: 800;
                }}
                QPushButton:hover {{ border: 2px solid {DARK['TEXT']}; }}
            """)

    def _fav_ring_step(self):
        """Animate the selected favorite's ring opacity."""
        self._fav_ring_opacity += self._fav_ring_dir * 0.03
        if self._fav_ring_opacity >= 1.0:
            self._fav_ring_opacity = 1.0
            self._fav_ring_dir = -1
        elif self._fav_ring_opacity <= 0.4:
            self._fav_ring_opacity = 0.4
            self._fav_ring_dir = 1
        btn = self._fav_buttons.get(self._fav_selected_uid)
        if btn:
            mode = 'GREEN'
            for u in self._cached_users:
                if u.get('id') == self._fav_selected_uid:
                    mode = u.get('mode', 'GREEN')
                    break
            self._style_fav_button(btn, mode, selected=True)

    def _sync_fav_selection(self, selected_uid):
        """Sync sidebar favorite selection to match the given user id."""
        self._fav_selected_uid = selected_uid
        # Stop animation if deselected
        if not selected_uid:
            self._fav_ring_timer.stop()
        else:
            self._fav_ring_opacity = 0.6
            self._fav_ring_dir = 1
            self._fav_ring_timer.start(40)
        # Re-style old and new buttons
        for uid, btn in self._fav_buttons.items():
            mode = 'GREEN'
            for u in self._cached_users:
                if u.get('id') == uid:
                    mode = u.get('mode', 'GREEN')
                    break
            self._style_fav_button(btn, mode, uid == selected_uid)

    def _on_nav_clicked(self, key):
        """Handle sidebar nav button clicks (legacy compat)."""
        self._switch_page(key)

    def _switch_page(self, key):
        """Switch the content area to show the selected page."""
        self._active_nav = key
        # Page map: welcome=0, users=1, teams=2, radio=3, settings=4, login=5
        page_map = {"welcome": 0, "users": 1, "teams": 2, "radio": 3, "settings": 4, "login": 5}
        self._content_stack.setCurrentIndex(page_map.get(key, 0))

        is_welcome = (key == "welcome")
        is_login = (key == "login")
        has_team = bool(getattr(self, '_active_team_name_cache', ''))
        settings_from_welcome = (key == "settings" and getattr(self, '_settings_back_page', '') == "welcome")
        show_sidebar = key in ("users", "teams", "radio") and not settings_from_welcome

        # Sidebar: visible only when a team is active (not from welcome)
        self._sidebar.setVisible(show_sidebar)
        if hasattr(self, '_content_frame'):
            self._content_frame.setStyleSheet(
                f"border-left: 1px solid {DARK['BORDER']};" if show_sidebar else "border: none;"
            )

        # Hide status bar and header on welcome/login
        is_settings = (key == "settings")
        self._status_bar.setVisible(show_sidebar)
        self._content_header.setVisible(show_sidebar or is_settings)

        # Panel width
        if is_welcome or is_login or is_settings:
            self.setFixedWidth(260)
        elif settings_from_welcome:
            self.setFixedWidth(260)
        else:
            self.setFixedWidth(PANEL_W)

        # Header elements
        if key == "users":
            team_name = getattr(self, '_active_team_name_cache', 'Team')
            self._section_title.setText(f"{team_name} ▾")
            self._section_title.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            self._hamburger_btn.setText("⚙")
            self._hamburger_btn.setVisible(True)
            self._collapse_btn.setVisible(True)
        elif key == "settings":
            self._section_title.setText("Settings")
            self._section_title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._hamburger_btn.setText("✕")
            self._hamburger_btn.setVisible(True)
            self._collapse_btn.setVisible(False)
        elif key == "teams":
            self._section_title.setText("Teams")
            self._hamburger_btn.setText("←")
            self._hamburger_btn.setVisible(True)
            self._collapse_btn.setVisible(False)
        elif key == "radio":
            self._section_title.setText("Radio")
            self._hamburger_btn.setText("←")
            self._hamburger_btn.setVisible(True)
            self._collapse_btn.setVisible(False)

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
        v.setContentsMargins(12, 0, 6, 0)
        v.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 4, 0, 0)

        title_row.addStretch()

        # Team name button — clickable dropdown to switch teams / copy code
        self._section_title = QPushButton("TEAM ▾")
        self._section_title.setCursor(Qt.PointingHandCursor)
        self._section_title.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._section_title.setStyleSheet(f"""
            QPushButton {{
                font-size: 13px; font-weight: 700; color: {DARK['TEXT']};
                letter-spacing: 0.5px; border: none;
                background: {DARK['BG_RAISED']}; border-radius: 6px;
                padding: 4px 12px;
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
        """)
        self._section_title.clicked.connect(self._on_title_clicked)
        title_row.addWidget(self._section_title)

        # Hidden compat ref (some code checks this)
        self._team_dropdown_btn = self._section_title

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
                padding-bottom: 4px;
                margin-left: 15px;
            }}
            QPushButton:hover {{ color: {DARK['TEAL']}; }}
        """)
        self._hamburger_btn.clicked.connect(self._toggle_settings)
        self._hamburger_btn.setVisible(False)
        title_row.addWidget(self._hamburger_btn)

        # Pop out to compact strip button
        self._collapse_btn = QPushButton("↗")
        self._collapse_btn.setFixedSize(28, 28)
        self._collapse_btn.setCursor(Qt.PointingHandCursor)
        self._collapse_btn.setToolTip("Pop out")
        self._collapse_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 14px; color: {DARK['TEXT_DIM']};
                background: transparent; border: none;
            }}
            QPushButton:hover {{ color: {DARK['TEAL']}; }}
        """)
        self._collapse_btn.clicked.connect(self._toggle_pin)
        self._collapse_btn.setVisible(False)
        title_row.addWidget(self._collapse_btn)

        v.addLayout(title_row)

        return header

    def _on_title_clicked(self):
        """Title button click — team dropdown on users page, back on sub-pages."""
        if self._active_nav == "users":
            self._show_team_dropdown()
        else:
            self._switch_page("users")

    def _show_team_dropdown(self):
        """Show dropdown menu to switch teams, copy room code, or manage teams."""
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
            QMenu::separator {{ background: {DARK['BORDER']}; height: 1px; margin: 4px 8px; }}
        """)

        # ── Active team header ──
        active_idx = self._team_combo.currentIndex()
        active_name = self._team_combo.itemText(active_idx) if active_idx >= 0 else ""
        if active_name:
            hdr = menu.addAction(f"✓ {active_name}")
            hdr.setEnabled(False)

        # ── Active team actions ──
        code = self._team_combo.itemData(active_idx, Qt.UserRole + 2) if active_idx >= 0 else ""
        if code:
            menu.addAction("📋  Copy Room Code").triggered.connect(
                lambda: (self._copy_invite_code(), self._show_copied_toast())
            )
        menu.addAction("📨  Copy Invite").triggered.connect(
            self._invite_friend_email
        )
        menu.addAction("Leave Team").triggered.connect(
            self._confirm_leave_team
        )

        # ── Other teams ──
        other_teams = []
        for i in range(self._team_combo.count()):
            if i == active_idx:
                continue
            other_teams.append((
                self._team_combo.itemText(i),
                self._team_combo.itemData(i),
            ))
        if other_teams:
            menu.addSeparator()
            for tn, tid in other_teams:
                menu.addAction(f"   {tn}").triggered.connect(
                    lambda checked=False, t=tid, n=tn:
                        self._on_team_page_select(t, n)
                )

        # ── Bottom actions ──
        menu.addSeparator()
        menu.addAction("Manage Teams…").triggered.connect(
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
        v.setContentsMargins(24, 0, 24, 16)
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

        # OH Logo from file (compact)
        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'oh_logo.png')
        if os.path.exists(logo_path):
            logo_px = QPixmap(logo_path).scaledToHeight(20, Qt.SmoothTransformation)
            logo.setPixmap(logo_px)
        v.addWidget(logo)
        v.addSpacing(4)

        # Welcome text
        welcome = QLabel("Welcome to Vox")
        welcome.setAlignment(Qt.AlignCenter)
        welcome.setStyleSheet(f"font-size: 11px; font-weight: 500; color: {DARK['TEXT_DIM']};")
        v.addWidget(welcome)
        v.addSpacing(10)

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
        team_scroll.setFixedHeight(140)
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
        self._welcome_code_input.setPlaceholderText("VOX-XXXXX")
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

    # ── Login Page ────────────────────────────────────────────────────
    def _build_login_page(self):
        """Login/signup page: email+password, Google OAuth, magic link."""
        page = QWidget()
        page.setStyleSheet("border: none;")
        v = QVBoxLayout(page)
        v.setContentsMargins(24, 16, 24, 16)
        v.setSpacing(0)

        # OH Logo from file
        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'oh_logo.png')
        if os.path.exists(logo_path):
            logo_px = QPixmap(logo_path).scaledToHeight(40, Qt.SmoothTransformation)
            logo.setPixmap(logo_px)
        v.addWidget(logo)
        v.addSpacing(6)

        # Subtitle
        self._login_subtitle = QLabel("Sign in to Vox")
        self._login_subtitle.setAlignment(Qt.AlignCenter)
        self._login_subtitle.setStyleSheet(f"font-size: 13px; font-weight: 500; color: {DARK['TEXT_DIM']};")
        v.addWidget(self._login_subtitle)
        v.addSpacing(14)

        # Display name field (only visible in signup mode)
        self._login_name_label = QLabel("DISPLAY NAME")
        self._login_name_label.setStyleSheet(
            f"font-size: 9px; font-weight: 700; color: {DARK['TEXT_FAINT']}; letter-spacing: 1.5px;"
        )
        self._login_name_label.setVisible(False)
        v.addWidget(self._login_name_label)
        v.addSpacing(2)

        self._login_name_input = QLineEdit()
        self._login_name_input.setPlaceholderText("Your Name")
        self._login_name_input.setStyleSheet(f"""
            QLineEdit {{
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 6px; padding: 7px 10px; font-size: 12px;
                color: {DARK['TEXT']};
            }}
            QLineEdit:focus {{ border-color: {DARK['TEXT_FAINT']}; }}
        """)
        self._login_name_input.setMaxLength(30)
        self._login_name_input.setVisible(False)
        v.addWidget(self._login_name_input)
        v.addSpacing(6)

        # Email field
        email_lbl = QLabel("EMAIL")
        email_lbl.setStyleSheet(
            f"font-size: 9px; font-weight: 700; color: {DARK['TEXT_FAINT']}; letter-spacing: 1.5px;"
        )
        v.addWidget(email_lbl)
        v.addSpacing(2)

        self._login_email_input = QLineEdit()
        self._login_email_input.setPlaceholderText("you@example.com")
        self._login_email_input.setStyleSheet(f"""
            QLineEdit {{
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 6px; padding: 7px 10px; font-size: 12px;
                color: {DARK['TEXT']};
            }}
            QLineEdit:focus {{ border-color: {DARK['TEXT_FAINT']}; }}
        """)
        v.addWidget(self._login_email_input)
        v.addSpacing(6)

        # Password field
        self._login_password_label = QLabel("PASSWORD")
        self._login_password_label.setStyleSheet(
            f"font-size: 9px; font-weight: 700; color: {DARK['TEXT_FAINT']}; letter-spacing: 1.5px;"
        )
        v.addWidget(self._login_password_label)
        v.addSpacing(2)

        self._login_password_input = QLineEdit()
        self._login_password_input.setPlaceholderText("Password")
        self._login_password_input.setEchoMode(QLineEdit.Password)
        self._login_password_input.setStyleSheet(f"""
            QLineEdit {{
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 6px; padding: 7px 10px; font-size: 12px;
                color: {DARK['TEXT']};
            }}
            QLineEdit:focus {{ border-color: {DARK['TEXT_FAINT']}; }}
        """)
        v.addWidget(self._login_password_input)
        v.addSpacing(10)

        # Sign In / Sign Up button
        self._login_submit_btn = QPushButton("Sign In")
        self._login_submit_btn.setCursor(Qt.PointingHandCursor)
        self._login_submit_btn.setStyleSheet(f"""
            QPushButton {{
                background: {DARK['TEAL']}; color: white; border: none;
                border-radius: 6px; padding: 9px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: #239E9E; }}
            QPushButton:disabled {{ background: {DARK['BORDER']}; color: {DARK['TEXT_FAINT']}; }}
        """)
        self._login_submit_btn.clicked.connect(self._on_login_submit)
        v.addWidget(self._login_submit_btn)
        v.addSpacing(10)

        # Divider "or"
        divider_row = QHBoxLayout()
        divider_row.setContentsMargins(0, 0, 0, 0)
        line_l = QFrame()
        line_l.setFrameShape(QFrame.HLine)
        line_l.setStyleSheet(f"color: {DARK['BORDER']};")
        line_r = QFrame()
        line_r.setFrameShape(QFrame.HLine)
        line_r.setStyleSheet(f"color: {DARK['BORDER']};")
        or_lbl = QLabel("or")
        or_lbl.setStyleSheet(f"color: {DARK['TEXT_FAINT']}; font-size: 10px;")
        or_lbl.setAlignment(Qt.AlignCenter)
        divider_row.addWidget(line_l, 1)
        divider_row.addWidget(or_lbl)
        divider_row.addWidget(line_r, 1)
        v.addLayout(divider_row)
        v.addSpacing(10)

        # Google sign-in button
        google_btn = QPushButton("Sign in with Google")
        google_btn.setCursor(Qt.PointingHandCursor)
        google_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {DARK['TEXT']};
                border: 1px solid {DARK['BORDER']}; border-radius: 6px;
                padding: 8px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; border-color: {DARK['TEXT_FAINT']}; }}
        """)
        google_btn.clicked.connect(self._on_login_google)
        v.addWidget(google_btn)
        v.addSpacing(6)

        # Magic link button
        magic_btn = QPushButton("Email me a link")
        magic_btn.setCursor(Qt.PointingHandCursor)
        magic_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {DARK['TEXT_DIM']};
                border: none; font-size: 11px; font-weight: 500;
            }}
            QPushButton:hover {{ color: {DARK['TEXT']}; }}
        """)
        magic_btn.clicked.connect(self._on_login_magic_link)
        v.addWidget(magic_btn)
        v.addSpacing(12)

        # Error message label
        self._login_error = QLabel("")
        self._login_error.setAlignment(Qt.AlignCenter)
        self._login_error.setWordWrap(True)
        self._login_error.setStyleSheet(f"font-size: 11px; color: {DARK['DANGER']};")
        self._login_error.setVisible(False)
        v.addWidget(self._login_error)

        # Status / info message (e.g., "Check your email")
        self._login_info = QLabel("")
        self._login_info.setAlignment(Qt.AlignCenter)
        self._login_info.setWordWrap(True)
        self._login_info.setStyleSheet(f"font-size: 11px; color: {DARK['TEAL']};")
        self._login_info.setVisible(False)
        v.addWidget(self._login_info)

        # Cancel button (visible only while waiting for email confirmation)
        self._login_cancel_btn = QPushButton("Cancel")
        self._login_cancel_btn.setCursor(Qt.PointingHandCursor)
        self._login_cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {DARK['DANGER']};
                border: 1px solid {DARK['DANGER']}; border-radius: 6px;
                padding: 6px; font-size: 11px; font-weight: 500;
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
        """)
        self._login_cancel_btn.setVisible(False)
        self._login_cancel_btn.clicked.connect(self._on_login_cancel)
        v.addWidget(self._login_cancel_btn)
        v.addSpacing(8)

        # Toggle sign-in / sign-up link
        self._login_toggle_btn = QPushButton("Don't have an account? Sign Up")
        self._login_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._login_toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {DARK['TEAL']};
                border: none; font-size: 11px; font-weight: 500;
            }}
            QPushButton:hover {{ color: {DARK['INFO_LT']}; }}
        """)
        self._login_toggle_btn.clicked.connect(self._toggle_login_mode)
        v.addWidget(self._login_toggle_btn)
        v.addSpacing(8)

        # Skip link
        skip_btn = QPushButton("Skip — use without an account")
        skip_btn.setCursor(Qt.PointingHandCursor)
        skip_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {DARK['TEXT_FAINT']};
                border: none; font-size: 10px;
            }}
            QPushButton:hover {{ color: {DARK['TEXT_DIM']}; }}
        """)
        skip_btn.clicked.connect(self.login_skipped.emit)
        v.addWidget(skip_btn)

        v.addStretch()

        # Internal state
        self._login_is_signup = False
        self._login_cancel_event = None  # threading.Event to cancel email wait

        return page

    def _toggle_login_mode(self):
        """Toggle between sign-in and sign-up mode on the login page."""
        self._login_is_signup = not self._login_is_signup
        self._login_error.setVisible(False)
        self._login_info.setVisible(False)
        if self._login_is_signup:
            self._login_subtitle.setText("Create your account")
            self._login_submit_btn.setText("Sign Up")
            self._login_toggle_btn.setText("Already have an account? Sign In")
            self._login_name_label.setVisible(True)
            self._login_name_input.setVisible(True)
        else:
            self._login_subtitle.setText("Sign in to Vox")
            self._login_submit_btn.setText("Sign In")
            self._login_toggle_btn.setText("Don't have an account? Sign Up")
            self._login_name_label.setVisible(False)
            self._login_name_input.setVisible(False)

    def _show_login_error(self, msg):
        """Show an error message on the login page (thread-safe via Signal)."""
        self._login_error.setText(str(msg))
        self._login_error.setVisible(True)
        self._login_info.setVisible(False)
        self._login_submit_btn.setEnabled(True)

    def _show_login_info(self, msg):
        """Show an info message on the login page."""
        self._login_info.setText(str(msg))
        self._login_info.setVisible(True)
        self._login_error.setVisible(False)

    def _on_login_cancel(self):
        """Cancel a pending email confirmation wait."""
        if self._login_cancel_event:
            self._login_cancel_event.set()
        self._login_cancel_btn.setVisible(False)
        self._login_info.setVisible(False)
        self._login_submit_btn.setEnabled(True)

    def _on_login_submit(self):
        """Handle the Sign In / Sign Up button click."""
        import time

        email = self._login_email_input.text().strip()
        password = self._login_password_input.text().strip()

        if not email:
            self._show_login_error("Please enter your email.")
            return
        if not password:
            self._show_login_error("Please enter your password.")
            return
        if self._login_is_signup:
            name = self._login_name_input.text().strip()
            if not name:
                self._show_login_error("Please enter your display name.")
                return

        self._login_error.setVisible(False)
        self._login_info.setVisible(False)
        self._login_submit_btn.setEnabled(False)

        def _do_auth():
            try:
                import auth_manager

                if self._login_is_signup:
                    name = self._login_name_input.text().strip()
                    # Start a callback listener BEFORE sign-up so it's ready
                    # when the user clicks the confirmation link in their email
                    server, port = auth_manager.start_magic_link_listener()
                    redirect_url = f"http://localhost:{port}/callback"
                    result = auth_manager.sign_up(email, password, name, redirect_to=redirect_url)
                else:
                    result = auth_manager.sign_in_email(email, password)

                if not result:
                    self._show_login_error("Authentication failed — no response.")
                    return

                # Check if email confirmation is required (signup without token)
                if self._login_is_signup and not result.get("access_token"):
                    self._login_cancel_event = threading.Event()
                    self._show_login_info(
                        "Check your email and click the confirmation link.\n"
                        "Waiting..."
                    )
                    self._login_cancel_btn.setVisible(True)
                    # Wait for the confirmation callback (user clicks email link)
                    try:
                        callback_result = auth_manager.wait_for_magic_link_callback(
                            server, cancel_event=self._login_cancel_event
                        )
                        self._login_cancel_btn.setVisible(False)
                        if callback_result and callback_result.get("access_token"):
                            session = {
                                "access_token": callback_result["access_token"],
                                "refresh_token": callback_result.get("refresh_token", ""),
                                "expires_at": int(time.time()) + int(callback_result.get("expires_in", 3600)),
                                "user_id": "",  # Will be fetched below
                                "email": email,
                            }
                            # Fetch user profile to get the user ID and metadata
                            try:
                                user_info = auth_manager.get_user(callback_result["access_token"])
                                session["user_id"] = user_info.get("id", "")
                                meta = user_info.get("user_metadata", {})
                                session["display_name"] = (
                                    meta.get("display_name") or meta.get("full_name")
                                    or meta.get("name") or name
                                )
                            except Exception:
                                session["display_name"] = name
                            self.login_completed.emit(session)
                            return
                        else:
                            self._show_login_error("Confirmation failed. Please try again.")
                            self._login_submit_btn.setEnabled(True)
                            return
                    except auth_manager.AuthError as e:
                        self._login_cancel_btn.setVisible(False)
                        if "Cancelled" in str(e):
                            # User cancelled — just reset the form
                            self._login_info.setVisible(False)
                            self._login_submit_btn.setEnabled(True)
                            return
                        self._show_login_error(f"Waiting for confirmation: {e}")
                        self._login_submit_btn.setEnabled(True)
                        return
                    except Exception as e:
                        self._login_cancel_btn.setVisible(False)
                        self._show_login_error(f"Waiting for confirmation: {e}")
                        self._login_submit_btn.setEnabled(True)
                        return

                # Direct sign-in (or sign-up without email confirmation)
                user = result.get("user", {})
                session = {
                    "access_token": result.get("access_token", ""),
                    "refresh_token": result.get("refresh_token", ""),
                    "expires_at": int(time.time()) + result.get("expires_in", 3600),
                    "user_id": user.get("id", ""),
                    "email": user.get("email", email),
                }

                # Extract display name from user metadata
                meta = user.get("user_metadata", {})
                display_name = meta.get("display_name") or meta.get("full_name") or meta.get("name") or ""
                if display_name:
                    session["display_name"] = display_name
                elif self._login_is_signup:
                    session["display_name"] = self._login_name_input.text().strip()

                self.login_completed.emit(session)

            except auth_manager.AuthError as e:
                msg = str(e)
                # If signup fails because user already exists, suggest sign-in
                if self._login_is_signup and ("already registered" in msg.lower()
                        or "already been registered" in msg.lower()
                        or "user already registered" in msg.lower()):
                    self._show_login_error("This email is already registered. Try signing in instead.")
                else:
                    self._show_login_error(msg)
                self._login_submit_btn.setEnabled(True)
            except Exception as e:
                self._show_login_error(str(e))
                self._login_submit_btn.setEnabled(True)

        threading.Thread(target=_do_auth, daemon=True).start()

    def _on_login_google(self):
        """Handle Google sign-in button click."""
        import time

        self._login_error.setVisible(False)
        self._show_login_info("Opening browser for Google sign-in...")

        def _do_google():
            try:
                import auth_manager
                result = auth_manager.sign_in_google()

                if not result or not result.get("access_token"):
                    self._show_login_error("Google sign-in failed — no token received.")
                    return

                # Get user info
                user = result.get("user", {})
                if not user:
                    try:
                        user = auth_manager.get_user(result["access_token"])
                    except Exception:
                        user = {}

                meta = user.get("user_metadata", {})
                display_name = meta.get("full_name") or meta.get("name") or meta.get("display_name") or ""

                session = {
                    "access_token": result.get("access_token", ""),
                    "refresh_token": result.get("refresh_token", ""),
                    "expires_at": int(time.time()) + int(result.get("expires_in", 3600)),
                    "user_id": user.get("id", ""),
                    "email": user.get("email", ""),
                    "display_name": display_name,
                }

                self.login_completed.emit(session)

            except Exception as e:
                self._show_login_error(str(e))

        threading.Thread(target=_do_google, daemon=True).start()

    def _on_login_magic_link(self):
        """Handle 'Email me a link' button click."""
        import time

        email = self._login_email_input.text().strip()
        if not email:
            self._show_login_error("Please enter your email first.")
            return

        self._login_error.setVisible(False)

        def _do_magic():
            try:
                import auth_manager

                # Start the listener first so we know the port
                server, port = auth_manager.start_magic_link_listener()
                redirect_uri = f"http://localhost:{port}/callback"

                # Send the magic link with redirect
                auth_manager.send_magic_link(email, redirect_to=redirect_uri)
                self._show_login_info("Check your email — click the link to sign in.")

                # Wait for the callback
                result = auth_manager.wait_for_magic_link_callback(server)

                if not result or not result.get("access_token"):
                    self._show_login_error("Magic link sign-in failed — no token received.")
                    return

                # Get user info
                user = {}
                try:
                    user = auth_manager.get_user(result["access_token"])
                except Exception:
                    pass

                meta = user.get("user_metadata", {})
                display_name = meta.get("full_name") or meta.get("name") or meta.get("display_name") or ""

                session = {
                    "access_token": result.get("access_token", ""),
                    "refresh_token": result.get("refresh_token", ""),
                    "expires_at": int(time.time()) + int(result.get("expires_in", 3600)),
                    "user_id": user.get("id", ""),
                    "email": user.get("email", email),
                    "display_name": display_name,
                }

                self.login_completed.emit(session)

            except Exception as e:
                self._show_login_error(str(e))

        threading.Thread(target=_do_magic, daemon=True).start()

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

        all_teams.sort(key=lambda t: t[0].get('name', '').lower())

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
        actions.setStyleSheet("border: none; background: transparent;")
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
                row.setStyleSheet("""
                    QFrame {
                        background: rgba(0, 166, 81, 0.08);
                        border: 1px solid rgba(0, 166, 81, 0.30);
                        border-radius: 8px;
                    }
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
        bar.setStyleSheet("border: none; background: transparent;")

        v = QVBoxLayout(bar)
        v.setContentsMargins(8, 0, 8, 8)
        v.setSpacing(4)

        # Spacer to push buttons to bottom, aligned with sidebar status orb
        v.addStretch()

        # Combined PTT / Page All bar with teal border
        btn_row = QHBoxLayout()
        btn_row.setSpacing(0)

        # PTT button — left side, ~2/3 width
        self.ptt_btn = QPushButton("PUSH TO TALK")
        self.ptt_btn.setCursor(Qt.PointingHandCursor)
        self.ptt_btn.setFixedHeight(32)
        self.ptt_btn.pressed.connect(self.ptt_pressed.emit)
        self.ptt_btn.released.connect(self.ptt_released.emit)
        self.ptt_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        btn_row.addWidget(self.ptt_btn, 2)

        # Thin divider
        divider = QFrame()
        divider.setFixedWidth(1)
        divider.setFixedHeight(32)
        divider.setStyleSheet(f"background: {DARK['TEAL']}; border: none;")
        btn_row.addWidget(divider)

        # Page All button — right side, ~1/3 width
        self.page_all_btn = QPushButton("PAGE ALL")
        self.page_all_btn.setCursor(Qt.PointingHandCursor)
        self.page_all_btn.setFixedHeight(32)
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
        gear.setToolTip("Quit Vox")
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
        title = QLabel("Welcome to Vox")
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
        """Auto-capitalize and prepend VOX- as the user types."""
        # Block re-entrant calls while we update the text
        self._invite_input.blockSignals(True)
        cursor_pos = self._invite_input.cursorPosition()

        # Strip whitespace and uppercase
        raw = text.strip().upper()
        # Remove any existing VOX- prefix so we can re-add it cleanly
        if raw.startswith("VOX-"):
            raw = raw[4:]
        elif raw.startswith("VOX"):
            raw = raw[3:]
        # Remove dashes from the code portion
        raw = raw.replace("-", "")
        # Rebuild with VOX- prefix if user has typed anything
        if raw:
            formatted = f"VOX-{raw}"
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
        teams = sorted(teams, key=lambda t: t.get("name", "").lower())
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
        from PySide6.QtWidgets import (
            QDialog,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QListWidget,
            QListWidgetItem,
            QPushButton,
            QVBoxLayout,
        )
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
            code_frame.setStyleSheet("background: rgba(0, 166, 81, 0.08); border: 1px solid rgba(0, 166, 81, 0.20); border-radius: 8px;")
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
                    border-radius: 18px; font-size: 22px; color: {DARK['WARN']};
                }}
                QPushButton:hover {{ background: rgba(230, 175, 0, 0.15); }}
            """)
        else:
            self._sidebar_mail_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; border: none;
                    border-radius: 18px; font-size: 22px; color: {DARK['TEXT_FAINT']};
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
        name = getattr(self, '_display_name', 'Vox')
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

    # ── Compact Vertical Strip ────────────────────────────────────
    def _build_compact_strip(self):
        """Build a sidebar-width vertical strip: OH logo, status btn, PTT btn."""
        strip = QFrame(self._frame)
        strip.setObjectName("compactStrip")
        strip.setStyleSheet(f"""
            QFrame#compactStrip {{
                background: {DARK['BG']};
                border: 1px solid {DARK['BORDER']};
                border-radius: {STRIP_RADIUS}px;
            }}
        """)

        v = QVBoxLayout(strip)
        v.setContentsMargins(6, 10, 6, 10)
        v.setSpacing(8)
        v.setAlignment(Qt.AlignTop | Qt.AlignHCenter)

        # ── Logo at top (click to expand) ──
        expand_btn = QPushButton()
        expand_btn.setFixedSize(40, 28)
        expand_btn.setCursor(Qt.PointingHandCursor)
        expand_btn.setToolTip("Expand panel")
        _logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'oh_logo.png')
        if os.path.exists(_logo_path):
            _logo_px = QPixmap(_logo_path).scaledToHeight(20, Qt.SmoothTransformation)
            expand_btn.setIcon(QIcon(_logo_px))
            expand_btn.setIconSize(_logo_px.size())
        else:
            expand_btn.setText("VOX")
        expand_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                border-radius: 6px;
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
        """)
        expand_btn.clicked.connect(self._toggle_pin)
        v.addWidget(expand_btn, 0, Qt.AlignCenter)

        # ── Separator ──
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setFixedWidth(STRIP_W - 16)
        sep.setStyleSheet(f"background: {DARK['BORDER']};")
        v.addWidget(sep, 0, Qt.AlignCenter)

        # ── Status button (outlined, matching main status orb) ──
        self._strip_status_dot = QPushButton()
        self._strip_status_dot.setFixedSize(40, 40)
        self._strip_status_dot.setCursor(Qt.PointingHandCursor)
        self._strip_status_dot.setToolTip("Change status")
        self._update_strip_status_dot()
        self._strip_status_dot.clicked.connect(self._show_strip_mode_menu)
        v.addWidget(self._strip_status_dot, 0, Qt.AlignCenter)

        # ── PTT button (outlined, same size as status) ──
        self._strip_ptt_btn = QPushButton("🎙")
        self._strip_ptt_btn.setFixedSize(40, 40)
        self._strip_ptt_btn.setCursor(Qt.PointingHandCursor)
        self._strip_ptt_btn.setToolTip("Push to Talk")
        self._strip_ptt_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: 1px solid {DARK['TEAL']};
                border-radius: 8px;
                font-size: 16px;
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
            QPushButton:pressed {{ background: {DARK['ACCENT']}; }}
        """)
        self._strip_ptt_btn.pressed.connect(
            lambda: self.ptt_pressed.emit()
        )
        self._strip_ptt_btn.released.connect(
            lambda: self.ptt_released.emit()
        )
        v.addWidget(self._strip_ptt_btn, 0, Qt.AlignCenter)

        v.addStretch()

        # Keep avatar dict for compatibility
        self._strip_avatar_buttons: dict[str, QPushButton] = {}
        return strip

    def _update_strip_status_dot(self):
        """Update the strip status button to reflect current mode (matches full panel orb)."""
        color = COLORS.get(self._current_mode, COLORS['GREEN'])
        self._strip_status_dot.setText("●")
        self._strip_status_dot.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                border-radius: 10px;
                font-size: 32px; color: {color};
                padding-bottom: 4px;
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; }}
        """)
        self._strip_status_dot.setToolTip(
            f"Status: {MODE_LABELS.get(self._current_mode, 'Available')} — click to change"
        )

    def _show_strip_mode_menu(self):
        """Show a rich dropdown to pick status mode (matches full panel menu)."""
        from PySide6.QtWidgets import QWidgetAction
        mode = getattr(self, '_current_mode', 'GREEN')
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 6px; padding: 4px;
            }}
        """)
        for mode_key, color in [("GREEN", COLORS['GREEN']), ("YELLOW", COLORS['YELLOW']), ("RED", COLORS['RED'])]:
            label = MODE_LABELS.get(mode_key, mode_key)
            is_active = (mode_key == mode)
            prefix = "✓ " if is_active else "   "

            row = QWidget()
            row.setCursor(Qt.PointingHandCursor)
            h = QHBoxLayout(row)
            h.setContentsMargins(10, 5, 14, 5)
            h.setSpacing(6)

            check_lbl = QLabel(prefix)
            check_lbl.setStyleSheet(f"font-size: 12px; color: {DARK['TEXT']}; border: none;")
            check_lbl.setFixedWidth(16)
            h.addWidget(check_lbl)

            dot = QLabel("●")
            dot.setStyleSheet(f"font-size: 14px; color: {color}; border: none;")
            h.addWidget(dot)

            text_lbl = QLabel(label)
            text_lbl.setStyleSheet(f"font-size: 12px; color: {DARK['TEXT']}; border: none;")
            h.addWidget(text_lbl)
            h.addStretch()

            row.setStyleSheet(f"""
                QWidget {{ border-radius: 4px; }}
                QWidget:hover {{ background: {DARK['BG_HOVER']}; }}
            """)

            wa = QWidgetAction(menu)
            wa.setDefaultWidget(row)
            wa.triggered.connect(
                lambda checked=False, mk=mode_key: self.mode_set_requested.emit(mk)
            )
            menu.addAction(wa)
        menu.exec(self._strip_status_dot.mapToGlobal(
            QPoint(self._strip_status_dot.width() + 4, 0)
        ))

    def _update_strip_avatars(self, users):
        """No-op — compact strip no longer shows avatars."""
        pass

    def _style_strip_avatar(self, btn, mode, selected=False):
        """Apply styling to a strip avatar button. Selected gets a bright mode-colored ring."""
        color = COLORS.get(mode, COLORS['GREEN'])
        radius = STRIP_AVATAR_SIZE // 2
        if selected:
            qc = QColor(color)
            lighter = qc.lighter(140).name()
            border = f"border: 3px solid {lighter};"
        else:
            border = "border: none;"
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {color}; color: white; {border}
                border-radius: {radius}px; font-size: 12px; font-weight: 800;
            }}
            QPushButton:hover {{ border: 2px solid {DARK['TEXT']}; }}
        """)

    def _on_strip_avatar_clicked(self, user_id):
        """Handle click on a strip avatar — select/deselect as PTT target."""
        if self._strip_selected_uid == user_id:
            self._strip_selected_uid = ""
            self.user_selected.emit("")
        else:
            self._strip_selected_uid = user_id
            self.user_selected.emit(user_id)
        # Re-style all avatars
        for uid, btn in self._strip_avatar_buttons.items():
            # Find mode from cached users
            mode = 'GREEN'
            for u in self._cached_users:
                if u.get('id') == uid:
                    mode = u.get('mode', 'GREEN')
                    break
            self._style_strip_avatar(btn, mode, uid == self._strip_selected_uid)

    def _calc_strip_height(self):
        """Fixed strip height: OH logo (28) + sep (1) + status (40) + PTT (40) + margins/spacing."""
        # margins top/bottom (20) + OH (28) + spacing (8) + sep (1) + spacing (8)
        # + status (40) + spacing (8) + PTT (40) + bottom padding
        return 170

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
        # Update compact strip status dot
        if hasattr(self, '_strip_status_dot'):
            self._update_strip_status_dot()

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
            self._sidebar_peer_initials.setStyleSheet("""
                QPushButton {
                    background: transparent; border: none;
                    border-radius: 19px; font-size: 15px; font-weight: 800;
                    color: transparent;
                }
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

        # Cache users (needed by _update_favorites and _sync_fav_selection)
        self._cached_users = list(users)

        # Update sidebar favorites and sync selection highlight
        if selected_user_id:
            self._fav_selected_uid = selected_user_id
        self._update_favorites(users)
        self._sync_fav_selection(self._fav_selected_uid)

        # Update compact strip avatars
        if selected_user_id:
            self._strip_selected_uid = selected_user_id
        if hasattr(self, '_strip_avatar_buttons'):
            self._update_strip_avatars(users)

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
        selected_uid = ""
        if row:
            if row._state == UserRow.STATE_SELECTED:
                row.set_state(UserRow.STATE_IDLE)
                selected_uid = ""
            elif row._state == UserRow.STATE_IDLE:
                row.set_state(UserRow.STATE_SELECTED)
                selected_uid = user_id
        # Sync sidebar favorites and strip avatars
        self._sync_fav_selection(selected_uid)
        self._strip_selected_uid = selected_uid
        if hasattr(self, '_strip_avatar_buttons'):
            for uid, btn in self._strip_avatar_buttons.items():
                mode = 'GREEN'
                for u in self._cached_users:
                    if u.get('id') == uid:
                        mode = u.get('mode', 'GREEN')
                        break
                self._style_strip_avatar(btn, mode, uid == selected_uid)
        self.user_selected.emit(selected_uid)

    def highlight_selected_user(self, user_id):
        """Reinforce the visual selection on a single user across all UI areas.
        Deselects all others and highlights the given user_id (or clears if empty)."""
        # User rows
        for uid, row in self._user_rows.items():
            if uid == user_id:
                if row._state not in (UserRow.STATE_CONNECTING, UserRow.STATE_LIVE):
                    row.set_state(UserRow.STATE_SELECTED)
            else:
                if row._state == UserRow.STATE_SELECTED:
                    row.set_state(UserRow.STATE_IDLE)
        # Sidebar favorites
        self._sync_fav_selection(user_id or "")
        # Strip avatars
        self._strip_selected_uid = user_id or ""
        if hasattr(self, '_strip_avatar_buttons'):
            for uid, btn in self._strip_avatar_buttons.items():
                mode = 'GREEN'
                for u in self._cached_users:
                    if u.get('id') == uid:
                        mode = u.get('mode', 'GREEN')
                        break
                self._style_strip_avatar(btn, mode, uid == self._strip_selected_uid)

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
        """Create a fresh media player and start the stream."""
        # Destroy old player to avoid stale state after stop/error
        if self._radio_player:
            try:
                self._radio_player.errorOccurred.disconnect(self._on_radio_error)
            except RuntimeError:
                pass
            self._radio_player.stop()
            self._radio_player.setSource(QUrl())  # Clear source before delete
            self._radio_player.deleteLater()
            self._radio_player = None
        if self._radio_audio_out:
            self._radio_audio_out.deleteLater()
            self._radio_audio_out = None

        self._radio_player = QMediaPlayer(self)
        self._radio_audio_out = QAudioOutput(self)
        self._radio_audio_out.setVolume(self._radio_volume.value() / 100.0)
        self._radio_player.setAudioOutput(self._radio_audio_out)
        self._radio_player.errorOccurred.connect(self._on_radio_error)

        urls = [
            "https://stream-relay-geo.ntslive.net/stream?client=NTSRadio",
            "https://stream-relay-geo.ntslive.net/stream2?client=NTSRadio",
        ]
        print(f"[Radio] Starting stream: {urls[self._radio_channel]}")
        self._radio_player.setSource(QUrl(urls[self._radio_channel]))
        self._radio_player.play()

    def _on_radio_error(self, error, message=""):
        """Handle media player errors — reset state so user can retry."""
        # QMediaPlayer emits error 0 (NoError) on normal operations — ignore it
        if error == 0:
            return
        print(f"[Radio] Error {error}: {message}")
        # TLS renegotiation / transient network errors fire while the stream
        # is still playing fine.  Only tear down if the player truly stopped.
        from PySide6.QtMultimedia import QMediaPlayer as _QMP
        if self._radio_player and self._radio_player.playbackState() == _QMP.PlayingState:
            print("[Radio] Ignoring transient error — stream still playing")
            return
        self._stop_radio()
        self._reset_sidebar_radio_btn()

    def _on_radio_volume(self, val):
        if self._radio_audio_out:
            self._radio_audio_out.setVolume(val / 100.0)

    def _fetch_nts_meta(self):
        """Fetch NTS live metadata in a background thread."""
        def _fetch():
            try:
                req = Request("https://www.nts.live/api/v2/live",
                              headers={"User-Agent": "Vox/1.0"})
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
        v.setContentsMargins(0, 4, 0, 8)
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
        self._settings_layout.setContentsMargins(8, 0, 8, 8)
        self._settings_layout.setSpacing(0)

        scroll.setWidget(container)
        scroll.setMinimumHeight(250)
        v.addWidget(scroll, 1)

        return view

    def _populate_settings(self):
        """Rebuild settings — grouped sections with headers."""
        layout = self._settings_layout

        # Clear existing items
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        section_header_style = f"""
            QLabel {{
                font-size: 10px; font-weight: 700; color: {DARK['TEXT_DIM']};
                letter-spacing: 1px; border: none; padding: 0;
            }}
        """
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
            QPushButton::menu-indicator {{ width: 0; height: 0; }}
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
            """One row: label left, value right."""
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(14, 0, 14, 0)
            h.setSpacing(8)
            h.addWidget(_label(label_text))
            h.addStretch()
            h.addWidget(value_widget)
            return row

        def _section(title):
            """Section header with divider line."""
            wrapper = QWidget()
            wrapper.setStyleSheet("border: none;")
            v_inner = QVBoxLayout(wrapper)
            v_inner.setContentsMargins(14, 12, 14, 4)
            v_inner.setSpacing(4)
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background: {DARK['BORDER_LT']};")
            v_inner.addWidget(line)
            hdr = QLabel(title.upper())
            hdr.setStyleSheet(section_header_style)
            v_inner.addWidget(hdr)
            return wrapper

        # ═══════════════════════════════════════
        # PROFILE
        # ═══════════════════════════════════════
        layout.addWidget(_section("Profile"))

        display_name = getattr(self, '_display_name', None) or 'Not set'
        name_val = _value(display_name)
        name_val.clicked.connect(lambda: (self._change_name_dialog(), self._populate_settings()))
        layout.addWidget(_credit("Name", name_val))

        theme_text = "Dark" if self._dark_mode else "Light"
        theme_val = _value(theme_text)
        theme_val.clicked.connect(lambda: (self._toggle_dark_mode(), self._populate_settings()))
        layout.addWidget(_credit("Theme", theme_val))

        # ═══════════════════════════════════════
        # AUDIO
        # ═══════════════════════════════════════
        layout.addWidget(_section("Audio"))

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
            layout.addWidget(_credit("Input", in_val))

            out_val = _value(current_out_name + "  ▾")
            out_menu = QMenu(out_val)
            out_menu.setStyleSheet(menu_style)
            for name, idx in output_devices:
                action = out_menu.addAction(name)
                action.triggered.connect(
                    lambda checked=False, i=idx: (self._on_output_device_changed(i), self._populate_settings())
                )
            out_val.setMenu(out_menu)
            layout.addWidget(_credit("Output", out_val))
        except Exception as e:
            print(f"Audio device settings error: {e}")

        # Radio + volume together
        is_playing = getattr(self, '_radio_playing', False)
        play_text = "Stop" if is_playing else "Play"
        play_color = DARK['DANGER'] if is_playing else DARK['ACCENT']
        radio_val = _value(play_text)
        radio_val.setStyleSheet(value_style.replace(f"color: {DARK['TEXT']}", f"color: {play_color}"))
        radio_val.clicked.connect(lambda: (self._toggle_radio(), self._populate_settings()))
        layout.addWidget(_credit("Radio", radio_val))

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

        deck_connected = getattr(self, '_deck_connected', False)
        status_text = "Connected" if deck_connected else "Not Connected"
        status_color = DARK['ACCENT'] if deck_connected else DARK['TEXT_DIM']
        deck_val = _value(status_text)
        deck_val.setStyleSheet(value_style.replace(f"color: {DARK['TEXT']}", f"color: {status_color}"))
        deck_val.clicked.connect(self._show_deck_setup_guide)
        layout.addWidget(_credit("Stream Deck", deck_val))

        # ═══════════════════════════════════════
        # Bottom: destructive actions
        # ═══════════════════════════════════════
        layout.addStretch()
        layout.addWidget(_section(""))  # divider only, empty title

        # Account row: Sign Out + Quit on one line
        acct_row = QWidget()
        acct_h = QHBoxLayout(acct_row)
        acct_h.setContentsMargins(14, 0, 14, 0)
        acct_h.setSpacing(8)
        acct_h.addWidget(_label("Account"))
        acct_h.addStretch()

        try:
            from user_settings import is_logged_in
            if is_logged_in():
                signout_val = _value("Sign Out")
                signout_val.setStyleSheet(
                    value_style.replace(f"color: {DARK['TEXT']}", f"color: {DARK['WARN']}")
                )
                signout_val.clicked.connect(self.sign_out_requested.emit)
                acct_h.addWidget(signout_val)
        except Exception:
            pass

        quit_val = _value("Quit")
        quit_val.setStyleSheet(value_style.replace(f"color: {DARK['TEXT']}", f"color: {DARK['DANGER']}"))
        quit_val.clicked.connect(self.quit_requested.emit)
        acct_h.addWidget(quit_val)

        layout.addWidget(acct_row)

    def set_deck_status(self, connected, deck_name="Stream Deck"):
        """Update Stream Deck connection status (called from main.py)."""
        self._deck_connected = connected
        self._deck_name = deck_name

    def _show_deck_setup_guide(self):
        """Show Stream Deck setup instructions for the Elgato plugin."""
        from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout
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
            "Vox works with the Elgato Stream Deck app.\n"
            "The plugin was auto-installed — just add the actions:"
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"font-size: 12px; color: {DARK['TEXT_DIM']}; border: none;")
        layout.addWidget(subtitle)

        steps = QLabel(
            "1. Open the Stream Deck app\n\n"
            "2. In the right sidebar, find the\n"
            "   \"Vox\" category\n\n"
            "3. Drag these actions onto your deck:\n\n"
            "   • Push to Talk — hold to talk\n"
            "   • Status Mode — cycle availability\n"
            "   • Vox Logo — shows status & previews\n"
            "   • Switch Team — cycle teams\n"
            "   • Select User — cycle users\n"
            "   • Show Panel — open the Vox window\n\n"
            "Suggested layout (top row):\n"
            "   PTT  |  Mode  |  Logo"
        )
        steps.setWordWrap(True)
        steps.setStyleSheet(f"font-size: 12px; color: {DARK['TEXT_DIM']}; line-height: 1.4; border: none;")
        layout.addWidget(steps)

        # "Don't show again" + OK buttons
        from PySide6.QtWidgets import QCheckBox, QHBoxLayout
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

    def _show_copied_toast(self, text="Copied!"):
        """Show a toast label that fades up and out."""
        toast = QLabel(text, self)
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
        """Copy a shareable invite message to the clipboard."""
        idx = self._team_combo.currentIndex()
        code = ""
        team_name = "Vox"
        if idx >= 0:
            code = self._team_combo.itemData(idx, Qt.UserRole + 2) or ""
            team_name = self._team_combo.currentText() or "Vox"

        if code:
            msg = (
                f"Join me on Vox!\n\n"
                f"Team: {team_name}\n"
                f"Code: {code}\n\n"
                f"Download: https://github.com/matthannam-fart/vox/archive/refs/heads/main.zip"
            )
        else:
            msg = (
                f"Join me on Vox!\n\n"
                f"Team: {team_name}\n\n"
                f"Download: https://github.com/matthannam-fart/vox/archive/refs/heads/main.zip"
            )

        QApplication.clipboard().setText(msg)
        self._show_copied_toast("Invite copied to clipboard!")

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
            # Save position so we can restore on expand
            self._pre_pin_pos = self.pos()
            # Collapse to compact vertical strip
            self._sidebar.setVisible(False)
            self._content_frame.setVisible(False)
            self._pinned_compact.setVisible(False)  # Keep old bar hidden
            self._compact_strip.setVisible(True)
            # Force panel to strip size
            strip_h = self._calc_strip_height()
            self.setFixedWidth(STRIP_W + 2)  # +2 for border
            self.setFixedHeight(strip_h)
            # Float on top of all windows with 90% opacity
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
            self.setWindowOpacity(0.9)
            self.show()
            # Move to top right corner of screen
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.right() - self.width() - 8, screen.top() + 8)
        else:
            # Expand to full panel — restore normal window flags and opacity
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
            self.setWindowOpacity(1.0)
            self.show()
            self.setMaximumWidth(16777215)  # Remove fixed width
            self.setMaximumHeight(16777215)  # Remove fixed height
            self.setMinimumHeight(0)
            self.setFixedWidth(PANEL_W)
            self._compact_strip.setVisible(False)
            self._sidebar.setVisible(True)
            self._content_frame.setVisible(True)
            self._pinned_compact.setVisible(False)
            self._switch_page("users")
            self._resize_panel()
            # Restore original position
            if hasattr(self, '_pre_pin_pos'):
                self.move(self._pre_pin_pos)

    def is_pinned(self):
        return self._pinned

    # ── Notch (triangle pointing to tray icon) ──────────────────
    def paintEvent(self, event):
        """Draw a small notch/triangle at the top of the panel pointing up."""
        super().paintEvent(event)
        # Skip the notch when in compact strip mode
        if self._pinned:
            return
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
    PANEL_H = 380
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
            if hasattr(self, '_compact_strip'):
                self._compact_strip.setGeometry(r)

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
    icon_path = os.path.join(icon_dir, "vox_icon.png")
    icon_2x_path = os.path.join(icon_dir, "vox_icon@2x.png")

    if os.path.exists(icon_2x_path):
        # Load @2x and scale down — ensures alpha is preserved
        img = QImage(icon_2x_path)
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
        pixmap = QPixmap.fromImage(img)
        icon = QIcon(pixmap)
        return icon
    elif os.path.exists(icon_path):
        img = QImage(icon_path)
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
        pixmap = QPixmap.fromImage(img)
        icon = QIcon(pixmap)
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
    text_width = fm.horizontalAdvance("VOX") + 4

    pixmap = QPixmap(max(text_width, size), size)
    pixmap.fill(QColor(0, 0, 0, 0))

    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setFont(font)
    p.setPen(QColor(0, 0, 0))
    p.drawText(QRect(0, 0, pixmap.width(), pixmap.height()), Qt.AlignCenter, "VOX")
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
    panel.set_connection(True, "VOX-7X3K")

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
