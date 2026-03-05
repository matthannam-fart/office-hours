"""
floating_panel.py — Office Hours Menu Bar Panel
Frameless popup widget anchored to the system tray icon.
Matches the wireframe at menubar_wireframe.html.
"""
import sys
import os
from ctypes import c_void_p
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QSizePolicy, QGraphicsDropShadowEffect,
    QSystemTrayIcon, QLineEdit, QSpacerItem, QSlider,
    QComboBox
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
from ui_constants import COLORS, MODE_LABELS, RADIO_STATIONS, PANEL_W, PANEL_RADIUS, DARK

# Widget classes (extracted to widgets.py)
from widgets import GlowingOrb, LevelMeter, SmallOrb, UserRow, ToggleSwitch

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
    call_user_requested = Signal(str)     # user_id
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

        # Radio player
        self._audio_output = QAudioOutput()
        self._audio_output.setVolume(0.2)
        self._radio_player = QMediaPlayer()
        self._radio_player.setAudioOutput(self._audio_output)

        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool  # Don't show in dock/taskbar
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(PANEL_W)

        self._build_ui()
        self._vibrancy_applied = False

    def _apply_vibrancy(self):
        """Make the native macOS window background transparent
        so our semi-transparent Qt frame shows the desktop through."""
        if self._vibrancy_applied:
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


        root = QVBoxLayout(self._frame)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ────────────────────────────────────────────
        self._header = self._build_header()
        root.addWidget(self._header)

        # ── Outgoing Call Banner (hidden by default) ──────────
        self._outgoing_banner = self._build_outgoing_banner()
        self._outgoing_banner.setVisible(False)
        root.addWidget(self._outgoing_banner)

        # ── Incoming Call Banner (hidden by default) ──────────
        self._incoming_banner = self._build_incoming_banner()
        self._incoming_banner.setVisible(False)
        root.addWidget(self._incoming_banner)

        # ── Call Banner (hidden by default) ───────────────────
        self._call_banner = self._build_call_banner()
        self._call_banner.setVisible(False)
        root.addWidget(self._call_banner)

        # ── Message Banner (hidden by default) ────────────────
        self._message_banner = self._build_message_banner()
        self._message_banner.setVisible(False)
        root.addWidget(self._message_banner)



        # ── Connected Bar (hidden by default) ────────────────
        self._conn_bar = self._build_conn_bar()
        self._conn_bar.setVisible(False)
        root.addWidget(self._conn_bar)

        # ── Disconnected Bar (hidden when connected) ──────────
        self._disconn_bar = self._build_disconn_bar()
        self._disconn_bar.setVisible(False)
        root.addWidget(self._disconn_bar)

        # ── Team Selector ────────────────────────────────────
        self._team_bar = self._build_team_bar()
        self._team_bar.setVisible(False)  # Hidden until teams loaded
        root.addWidget(self._team_bar)

        # ── Onboarding (shown when no teams) ─────────────────
        self._onboarding = self._build_onboarding()
        self._onboarding.setVisible(False)
        root.addWidget(self._onboarding, 1)

        # ── User List ─────────────────────────────────────────
        self._user_section = self._build_user_section()
        root.addWidget(self._user_section, 1)

        # ── PTT Bar ───────────────────────────────────────────
        self._ptt_bar = self._build_ptt_bar()
        root.addWidget(self._ptt_bar)

        # ── Settings View (hidden by default) ──────────────────
        self._settings_view = self._build_settings_view()
        self._settings_view.setVisible(False)
        root.addWidget(self._settings_view, 1)

        # ── Pinned compact (hidden by default) ────────────────
        self._pinned_compact = self._build_pinned_compact()
        self._pinned_compact.setVisible(False)
        root.addWidget(self._pinned_compact)

        # Store root layout ref
        self._root = root

    # ── Header ────────────────────────────────────────────────────
    def _build_header(self):
        header = QFrame()
        header.setStyleSheet(f"border-bottom: 1px solid {DARK['BORDER']};")
        header.setFixedHeight(48)

        h = QHBoxLayout(header)
        h.setContentsMargins(12, 8, 10, 8)
        h.setSpacing(8)

        # Status orb (16px — color conveys mode)
        self.orb = GlowingOrb(16)
        h.addWidget(self.orb)

        # Mode cycle button
        self.mode_btn = QPushButton()
        self.mode_btn.setCursor(Qt.PointingHandCursor)
        self.mode_btn.setMinimumHeight(28)
        self.mode_btn.clicked.connect(self.mode_cycle_requested.emit)
        h.addWidget(self.mode_btn)

        self._update_mode_btn()

        # Spacer
        h.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Expanding, QSizePolicy.Minimum))

        # Hotline toggle with label
        self._hotline_lbl = QLabel("Hotline")
        self._hotline_lbl.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_DIM']}; font-weight: 500; border: none;")
        h.addWidget(self._hotline_lbl)

        self.open_toggle = ToggleSwitch()
        self.open_toggle.toggled.connect(self.hotline_toggled.emit)
        h.addWidget(self.open_toggle)

        # Hamburger / settings button (replaces pin in header)
        self.menu_btn = QPushButton("···")
        self.menu_btn.setFixedSize(26, 26)
        self.menu_btn.setCursor(Qt.PointingHandCursor)
        self.menu_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 14px; font-weight: 900; color: {DARK['TEXT_DIM']};
                background: transparent; border: none;
                border-radius: 6px; padding: 0 0 4px 0; letter-spacing: 1px;
            }}
            QPushButton:hover {{ background: rgba(255,255,255,0.06); }}
        """)
        self.menu_btn.clicked.connect(self._show_hamburger_menu)
        h.addWidget(self.menu_btn)

        # Hidden pin button (used programmatically, no longer in header)
        self.pin_btn = QPushButton()
        self.pin_btn.setFixedSize(0, 0)
        self.pin_btn.setVisible(False)
        self._update_pin_style(False)

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

    def _update_mode_btn(self):
        mode = self._current_mode
        color = COLORS.get(mode, COLORS['GREEN'])
        # Lighter text colors for dark background
        text_colors = {
            'GREEN': '#4cdf80', 'YELLOW': '#f0c040',
            'RED': '#f06060', 'OPEN': '#4cd8d8'
        }
        text_color = text_colors.get(mode, '#4cdf80')
        label = MODE_LABELS.get(mode, 'Available')

        self.mode_btn.setText(label)
        self.mode_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 4px 10px;
                border-radius: 8px;
                border: 1px solid {DARK['BORDER']};
                background: {DARK['BG_RAISED']};
                font-size: 12px;
                font-weight: 600;
                color: {text_color};
                text-align: left;
            }}
            QPushButton:hover {{
                background: {DARK['BG_HOVER']};
            }}
        """)

    # ── Connection Bar ────────────────────────────────────────────
    def _build_conn_bar(self):
        bar = QFrame()
        bar.setStyleSheet(f"background: {DARK['BG_RAISED']}; border-bottom: 1px solid {DARK['BORDER']};")
        bar.setFixedHeight(36)

        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 0, 8, 0)
        h.setSpacing(6)

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
        bar.setFixedHeight(32)

        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 4, 14, 4)

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
    def _build_onboarding(self):
        """First-launch screen: set your name, browse available teams, or create/join with code."""
        # Custom frame that paints an "OH" watermark behind the content
        class WatermarkFrame(QFrame):
            def paintEvent(self, event):
                super().paintEvent(event)
                p = QPainter(self)
                p.setRenderHint(QPainter.Antialiasing)
                bg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oh_bg.png")
                if os.path.exists(bg_path):
                    pm = QPixmap(bg_path)
                    scaled = pm.scaledToWidth(self.width(), Qt.SmoothTransformation)
                    y = (self.height() - scaled.height()) // 2
                    p.setOpacity(0.08)
                    p.drawPixmap(0, y, scaled)
                else:
                    font = QFont(FONT_FAMILY, 160)
                    font.setWeight(QFont.Black)
                    p.setFont(font)
                    color = QColor(DARK['TEAL'])
                    color.setAlpha(18)
                    p.setPen(color)
                    r = self.rect()
                    r.moveTop(-10)
                    p.drawText(r, Qt.AlignCenter, "OH")
                p.end()

        frame = WatermarkFrame()
        frame.setStyleSheet("border: none;")
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
        self._onboarding_name_input.setPlaceholderText("Display name")
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

        # ── Available Teams (lobby) ──
        teams_lbl = QLabel("AVAILABLE TEAMS")
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

    def set_available_teams(self, teams, my_teams=None):
        """Populate the lobby team list on the onboarding screen.
        teams: [{id, name, created_by}, ...] — teams user can request to join
        my_teams: [{id, name, role}, ...] — teams user already belongs to (shown first with Select btn)
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

                select_btn = QPushButton("Select")
                select_btn.setCursor(Qt.PointingHandCursor)
                select_btn.setFixedSize(54, 24)
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
                        self._on_lobby_select_click(tid, tn)
                )
                h.addWidget(select_btn)
                self._lobby_layout.addWidget(row)

        # ── Show other teams (with "Join" button) ──
        if teams:
            has_content = True
            if my_teams:
                # Add a small divider between sections
                section_lbl2 = QLabel("OTHER TEAMS")
                section_lbl2.setStyleSheet(f"font-size: 8px; font-weight: 700; color: {DARK['TEXT_FAINT']}; letter-spacing: 1.5px; padding: 6px 2px 2px 2px;")
                self._lobby_layout.addWidget(section_lbl2)

            for team in teams:
                row = QFrame()
                row.setStyleSheet(f"""
                    QFrame {{ background: transparent; border-radius: 4px; }}
                    QFrame:hover {{ background: {DARK['BG_HOVER']}; }}
                """)
                h = QHBoxLayout(row)
                h.setContentsMargins(6, 3, 6, 3)
                h.setSpacing(6)

                name_lbl = QLabel(team["name"])
                name_lbl.setStyleSheet(f"font-size: 12px; font-weight: 500; color: {DARK['TEXT']};")
                h.addWidget(name_lbl, 1)

                join_btn = QPushButton("Join")
                join_btn.setCursor(Qt.PointingHandCursor)
                join_btn.setFixedSize(50, 24)
                join_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {DARK['ACCENT']}; color: white; border: none;
                        border-radius: 4px; font-size: 10px; font-weight: 700;
                    }}
                    QPushButton:hover {{ background: {DARK['ACCENT_DIM']}; }}
                    QPushButton:disabled {{ background: {DARK['BORDER']}; color: {DARK['TEXT_FAINT']}; }}
                """)
                team_id = team["id"]
                team_name = team["name"]
                admin_id = team.get("created_by", "")
                join_btn.clicked.connect(
                    lambda checked=False, tid=team_id, tn=team_name, aid=admin_id, btn=join_btn:
                        self._on_lobby_join_click(tid, tn, aid, btn)
                )
                h.addWidget(join_btn)
                self._lobby_layout.addWidget(row)

        if not has_content:
            empty = QLabel("No teams yet — create one!")
            empty.setAlignment(Qt.AlignCenter)
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
        self._resize_panel()

    def hide_join_request(self):
        """Hide the join request notification banner."""
        if hasattr(self, '_join_request_banner'):
            self._join_request_banner.setVisible(False)
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
        orb = SmallOrb(DARK['TEAL'], 10)
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
            # Show lobby (onboarding) — user picks their team
            self._onboarding.setVisible(True)
            self._team_bar.setVisible(False)
            self._user_section.setVisible(False)
            self._ptt_bar.setVisible(False)
            self._is_onboarding = True
            self._disconn_bar.setVisible(False)
            self.setFixedHeight(500)
            return

        # Transition to normal team UI (after user selected a team)
        self._onboarding.setVisible(False)
        self._team_bar.setVisible(True)
        self._user_section.setVisible(True)
        self._ptt_bar.setVisible(True)

        # Leaving onboarding — clear height lock
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
        # Show manage button if admin of current team
        if active_index < len(teams):
            self._team_manage_btn.setVisible(True)
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
        sec_hdr.setContentsMargins(14, 10, 14, 4)
        self._online_label = QLabel("ONLINE")
        self._online_label.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {DARK['TEXT_FAINT']}; letter-spacing: 1px;")
        sec_hdr.addWidget(self._online_label)
        sec_hdr.addStretch()
        self.online_count = QLabel("0")
        self.online_count.setStyleSheet(f"""
            font-size: 11px; font-weight: 700; color: {DARK['TEXT_DIM']};
            background: {DARK['BG_RAISED']}; border-radius: 8px; padding: 2px 7px;
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
        self._user_layout.setContentsMargins(6, 2, 6, 6)
        self._user_layout.setSpacing(4)
        self._user_layout.addStretch()

        scroll.setWidget(self._user_container)
        v.addWidget(scroll, 1)

        return section

    # ── PTT Bar ───────────────────────────────────────────────────
    def _build_ptt_bar(self):
        bar = QFrame()
        bar.setStyleSheet(f"border-top: 1px solid {DARK['BORDER']};")
        bar.setFixedHeight(60)

        v = QVBoxLayout(bar)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(4)

        # Horizontal row: PTT (5/6) + Page All (1/6)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        # PTT button
        self.ptt_btn = QPushButton("●  Hold to Talk")
        self.ptt_btn.setCursor(Qt.PointingHandCursor)
        self.ptt_btn.setStyleSheet(f"""
            QPushButton {{
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 10px; padding: 8px; font-size: 13px;
                font-weight: 500; color: {DARK['TEXT_DIM']};
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; border-color: {DARK['BORDER']}; }}
            QPushButton:pressed {{ background: {DARK['BG']}; }}
        """)
        self.ptt_btn.pressed.connect(self.ptt_pressed.emit)
        self.ptt_btn.released.connect(self.ptt_released.emit)
        self.ptt_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        btn_row.addWidget(self.ptt_btn, 5)

        # Page All button
        self.page_all_btn = QPushButton("Page All")
        self.page_all_btn.setCursor(Qt.PointingHandCursor)
        self.page_all_btn.setFixedWidth(62)
        self.page_all_btn.setStyleSheet(f"""
            QPushButton {{
                background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                border-radius: 10px; padding: 8px; font-size: 11px;
                font-weight: 600; color: {DARK['TEXT_FAINT']};
            }}
            QPushButton:hover {{ background: {DARK['BG_HOVER']}; color: {DARK['TEXT_DIM']}; }}
            QPushButton:pressed {{ background: {DARK['BG']}; }}
        """)
        self.page_all_btn.pressed.connect(self.page_all_pressed.emit)
        self.page_all_btn.released.connect(self.page_all_released.emit)
        btn_row.addWidget(self.page_all_btn, 1)

        v.addLayout(btn_row)

        # Mode label below both buttons
        self.ptt_mode_label = QLabel("Hotline — always-on hot mic.")
        self.ptt_mode_label.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_FAINT']}; border: none;")
        self.ptt_mode_label.setAlignment(Qt.AlignCenter)
        self.ptt_mode_label.setVisible(False)
        v.addWidget(self.ptt_mode_label)

        return bar

    # ── Incoming Call Banner ──────────────────────────────────────
    # ── Outgoing Call Banner ────────────────────────────────────────
    def _build_outgoing_banner(self):
        banner = QFrame()
        banner.setStyleSheet(f"""
            background: rgba(30, 70, 120, 0.25);
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
        self.outgoing_name.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {DARK['INFO_LT']};")
        call_info.addWidget(self.outgoing_name)
        self.outgoing_sub = QLabel("Calling...")
        self.outgoing_sub.setStyleSheet(f"font-size: 11px; color: {DARK['INFO']};")
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
            background: rgba(0, 100, 50, 0.20);
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
        self.incoming_name.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {DARK['ACCENT_LT']};")
        caller_info.addWidget(self.incoming_name)
        caller_sub = QLabel("wants to connect")
        caller_sub.setStyleSheet(f"font-size: 11px; color: {DARK['ACCENT_LT']};")
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
            background: rgba(0, 100, 50, 0.20);
            border-bottom: 1px solid {DARK['BORDER']};
        """)

        outer = QVBoxLayout(banner)
        outer.setContentsMargins(14, 8, 14, 8)
        outer.setSpacing(6)

        # Top row: orb + name + end button
        top = QHBoxLayout()
        top.setSpacing(8)

        call_orb = GlowingOrb(20)
        call_orb.start_breathing()
        top.addWidget(call_orb)

        self.call_name_label = QLabel("")
        self.call_name_label.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {DARK['ACCENT_LT']};")
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

        # Bottom row: audio level meters
        meters = QHBoxLayout()
        meters.setSpacing(6)

        mic_icon = QLabel("●")
        mic_icon.setFixedWidth(12)
        mic_icon.setStyleSheet(f"font-size: 8px; color: {DARK['ACCENT']};")
        meters.addWidget(mic_icon)
        self.mic_meter = LevelMeter(color=DARK['ACCENT'], width=80, height=5)
        meters.addWidget(self.mic_meter)

        meters.addSpacing(8)

        spk_icon = QLabel("●")
        spk_icon.setFixedWidth(12)
        spk_icon.setStyleSheet(f"font-size: 8px; color: {DARK['INFO']};")
        meters.addWidget(spk_icon)
        self.speaker_meter = LevelMeter(color=DARK['INFO'], width=80, height=5)
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
    def _build_pinned_compact(self):
        bar = QFrame()
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
        self.orb.set_mode(mode)
        self.pinned_orb.set_mode(mode)
        self._update_mode_btn()
        self._update_pinned_style()

        # PTT state
        if mode == 'RED':
            self.ptt_btn.setEnabled(False)
            self.ptt_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                    border-radius: 10px; padding: 8px; font-size: 13px;
                    font-weight: 500; color: {DARK['TEXT_FAINT']};
                }}
            """)
            self.ptt_mode_label.setVisible(False)
        else:
            self.ptt_btn.setEnabled(True)
            self.ptt_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {DARK['BG_RAISED']}; border: 1px solid {DARK['BORDER']};
                    border-radius: 10px; padding: 8px; font-size: 13px;
                    font-weight: 500; color: {DARK['TEXT_DIM']};
                }}
                QPushButton:hover {{ background: {DARK['BG_HOVER']}; border-color: {DARK['BORDER']}; }}
                QPushButton:pressed {{ background: {DARK['BG']}; }}
            """)

    def set_hotline(self, is_on):
        """Toggle hotline state."""
        self._is_open_line = is_on
        self.open_toggle.set_on(is_on)
        in_call = getattr(self, '_call_peer_name', None)
        if is_on:
            self.ptt_btn.setText("●  Hotline")
            self.ptt_mode_label.setText("Hotline — always-on hot mic.")
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

    def set_display_name(self, name):
        """Set the display name shown in the pinned compact bar."""
        self._display_name = name
        self._update_pinned_style()

    def set_connection(self, connected, peer_name=""):
        """Switch between connected and disconnected states."""
        self._connected = connected
        self._conn_bar.setVisible(connected)
        # Only show disconn bar if not in onboarding mode
        self._disconn_bar.setVisible(not connected and not self._onboarding.isVisible())
        if connected and peer_name:
            self.conn_label.setText(f"Connected to {peer_name}")
        elif connected:
            self.conn_label.setText("Connected")

    def set_users(self, users):
        """Replace the user list. users = [{id, name, mode, has_message}, ...]"""
        # Clear existing
        while self._user_layout.count() > 1:  # keep the stretch
            item = self._user_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for u in users:
            row = UserRow(
                u.get('id', ''),
                u.get('name', 'Unknown'),
                u.get('mode', 'GREEN'),
                u.get('has_message', False)
            )
            row.call_clicked.connect(self.call_user_requested.emit)
            self._user_layout.insertWidget(self._user_layout.count() - 1, row)

        self.online_count.setText(str(len(users)))

        # Dynamic panel height based on user count
        if not self._pinned and not self._is_onboarding:
            # Fixed chrome: header(48) + disconn/conn(36) + team(28) + section hdr(28) + ptt(60) + margins(16)
            chrome_height = 48 + 36 + 28 + 28 + 60 + 16
            user_height = len(users) * 46
            target = chrome_height + max(user_height, 46)  # min space for 1 row
            target = min(target, 520)  # cap so it doesn't go off-screen
            self.setFixedHeight(target)

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
        self._user_section.setVisible(False)
        self._resize_panel()

    def hide_outgoing(self):
        """Hide the outgoing call banner."""
        self._outgoing_banner.setVisible(False)
        self._user_section.setVisible(True)
        self._resize_panel()

    def show_incoming(self, caller_name):
        """Show the incoming call banner."""
        self._hide_all_banners()
        self.incoming_name.setText(caller_name)
        self._incoming_banner.setVisible(True)
        self._user_section.setVisible(False)
        self._resize_panel()

    def hide_incoming(self):
        """Hide the incoming call banner."""
        self._incoming_banner.setVisible(False)
        if not self._outgoing_banner.isVisible() and not self._call_banner.isVisible():
            self._user_section.setVisible(True)
        self._resize_panel()

    def show_call(self, caller_name):
        """Show the in-call banner."""
        self._hide_all_banners()
        self.call_name_label.setText(caller_name)
        self._call_banner.setVisible(True)
        self._user_section.setVisible(False)
        # Update PTT to show who you're talking to
        self._call_peer_name = caller_name
        if not self._is_open_line:
            self.ptt_btn.setText(f"●  Talking to {caller_name}")
        self._resize_panel()

    def hide_call(self):
        """Hide the in-call banner and restore normal layout."""
        self._hide_all_banners()
        self._user_section.setVisible(True)
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
                    border-radius: 10px; padding: 8px; font-size: 13px;
                    font-weight: 600; color: {DARK['DANGER']};
                }}
            """)
        else:
            # Restore normal style
            self.set_mode(self._current_mode)

    # ── Settings View (inline) ──────────────────────────────────
    def _build_settings_view(self):
        """Build an inline settings panel that replaces the main content."""
        view = QFrame()
        view.setStyleSheet("border: none;")

        v = QVBoxLayout(view)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── Header with back button ──
        hdr = QFrame()
        hdr.setStyleSheet(f"border-bottom: 1px solid {DARK['BORDER']};")
        hdr.setFixedHeight(40)
        hdr_layout = QHBoxLayout(hdr)
        hdr_layout.setContentsMargins(10, 0, 10, 0)
        hdr_layout.setSpacing(8)

        back_btn = QPushButton("<")
        back_btn.setFixedSize(28, 28)
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 16px; font-weight: 700; color: {DARK['TEAL']};
                background: transparent; border: none; border-radius: 6px;
            }}
            QPushButton:hover {{ background: rgba(42,191,191,0.12); }}
        """)
        back_btn.clicked.connect(self._close_settings)
        hdr_layout.addWidget(back_btn)

        title = QLabel("Settings")
        title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {DARK['TEXT']}; border: none;")
        hdr_layout.addWidget(title, 1)
        v.addWidget(hdr)

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

        # ── Change Name ──
        name_btn = QPushButton("Change Name")
        name_btn.setCursor(Qt.PointingHandCursor)
        name_btn.setStyleSheet(row_style)
        name_btn.clicked.connect(lambda: (self._close_settings(), self._change_name_dialog()))
        layout.addWidget(name_btn)

        # ── Incognito ──
        incognito_text = "Visible" if self._incognito else "Incognito"
        incognito_btn = QPushButton(incognito_text)
        incognito_btn.setCursor(Qt.PointingHandCursor)
        incognito_btn.setStyleSheet(row_style)
        incognito_btn.clicked.connect(lambda: (self._toggle_incognito(), self._populate_settings()))
        layout.addWidget(incognito_btn)

        # ── Pin Window ──
        pin_text = "Unpin Window" if self._pinned else "Pin Window"
        pin_btn = QPushButton(pin_text)
        pin_btn.setCursor(Qt.PointingHandCursor)
        pin_btn.setStyleSheet(row_style)
        pin_btn.clicked.connect(lambda: (self._close_settings(), self._toggle_pin()))
        layout.addWidget(pin_btn)

        # ── Divider ──
        div1 = QFrame()
        div1.setFixedHeight(1)
        div1.setStyleSheet(f"background: {DARK['BORDER']}; margin: 4px 12px;")
        layout.addWidget(div1)

        # ── Radio ──
        if self._radio_station:
            radio_btn = QPushButton("Stop Radio")
            radio_btn.setCursor(Qt.PointingHandCursor)
            radio_btn.setStyleSheet(row_style)
            radio_btn.clicked.connect(lambda: (self._stop_radio(), self._populate_settings()))
            layout.addWidget(radio_btn)

            # Volume slider row
            vol_row = QWidget()
            vol_h = QHBoxLayout(vol_row)
            vol_h.setContentsMargins(12, 4, 12, 4)
            vol_h.setSpacing(8)
            vol_slider = QSlider(Qt.Horizontal)
            vol_slider.setRange(0, 100)
            vol_slider.setValue(int(self._audio_output.volume() * 100))
            vol_slider.setStyleSheet(f"""
                QSlider::groove:horizontal {{ height: 3px; background: {DARK['BORDER']}; border-radius: 1px; }}
                QSlider::handle:horizontal {{ width: 10px; height: 10px; margin: -4px 0; background: {DARK['TEAL']}; border-radius: 5px; }}
                QSlider::sub-page:horizontal {{ background: {DARK['TEAL']}; border-radius: 1px; }}
            """)
            vol_slider.valueChanged.connect(lambda v: self._audio_output.setVolume(v / 100.0))
            vol_h.addWidget(vol_slider, 1)
            layout.addWidget(vol_row)
        else:
            radio_btn = QPushButton("Radio")
            radio_btn.setCursor(Qt.PointingHandCursor)
            radio_btn.setStyleSheet(row_style)
            radio_btn.clicked.connect(lambda: (self._play_radio('NTS Radio'), self._populate_settings()))
            layout.addWidget(radio_btn)

        # ── Divider ──
        div2 = QFrame()
        div2.setFixedHeight(1)
        div2.setStyleSheet(f"background: {DARK['BORDER']}; margin: 4px 12px;")
        layout.addWidget(div2)

        # ── Audio Devices ──
        try:
            import sounddevice as sd
            devices = sd.query_devices()

            # Input device
            in_row = QWidget()
            in_h = QHBoxLayout(in_row)
            in_h.setContentsMargins(12, 4, 12, 4)
            in_h.setSpacing(8)
            in_icon = QLabel("Input")
            in_icon.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_FAINT']};")
            in_h.addWidget(in_icon)
            in_combo = QComboBox()
            in_combo.setStyleSheet(f"""
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
            """)
            in_combo.addItem("System Default", None)
            current_in = getattr(self, '_current_input_idx', None)
            for i, d in enumerate(devices):
                if d['max_input_channels'] > 0:
                    in_combo.addItem(d['name'][:28], i)
                    if current_in == i:
                        in_combo.setCurrentIndex(in_combo.count() - 1)
            in_combo.currentIndexChanged.connect(
                lambda idx, c=in_combo: self._on_input_device_changed(c.currentData())
            )
            in_h.addWidget(in_combo, 1)
            layout.addWidget(in_row)

            # Output device
            out_row = QWidget()
            out_h = QHBoxLayout(out_row)
            out_h.setContentsMargins(12, 4, 12, 4)
            out_h.setSpacing(8)
            out_icon = QLabel("Output")
            out_icon.setStyleSheet(f"font-size: 11px; color: {DARK['TEXT_FAINT']};")
            out_h.addWidget(out_icon)
            out_combo = QComboBox()
            out_combo.setStyleSheet(f"""
                QComboBox {{
                    font-size: 11px; padding: 3px 6px;
                    border: 1px solid {DARK['BORDER']}; border-radius: 6px;
                    background: {DARK['BG_RAISED']}; color: {DARK['TEXT']};
                    min-width: 100px;
                }}
                QComboBox QAbstractItemView {{
                    background: {DARK['BG_RAISED']}; color: {DARK['TEXT']};
                    border: 1px solid {DARK['BORDER']};
                    selection-background-color: {DARK['BG_HOVER']};
                }}
            """)
            out_combo.addItem("System Default", None)
            current_out = getattr(self, '_current_output_idx', None)
            for i, d in enumerate(devices):
                if d['max_output_channels'] > 0:
                    out_combo.addItem(d['name'][:28], i)
                    if current_out == i:
                        out_combo.setCurrentIndex(out_combo.count() - 1)
            out_combo.currentIndexChanged.connect(
                lambda idx, c=out_combo: self._on_output_device_changed(c.currentData())
            )
            out_h.addWidget(out_combo, 1)
            layout.addWidget(out_row)
        except Exception as e:
            print(f"Audio device settings error: {e}")

        # ── Divider ──
        div3 = QFrame()
        div3.setFixedHeight(1)
        div3.setStyleSheet(f"background: {DARK['BORDER']}; margin: 4px 12px;")
        layout.addWidget(div3)

        # ── Copy Invite Code ──
        copy_code_btn = QPushButton("Copy Invite Code")
        copy_code_btn.setCursor(Qt.PointingHandCursor)
        copy_code_btn.setStyleSheet(row_style)
        copy_code_btn.clicked.connect(lambda: self._copy_invite_code())
        layout.addWidget(copy_code_btn)

        # ── Leave Team ──
        leave_team_btn = QPushButton("Leave Team")
        leave_team_btn.setCursor(Qt.PointingHandCursor)
        leave_team_btn.setStyleSheet(f"""
            QPushButton {{
                text-align: left; padding: 8px 12px; font-size: 13px;
                font-weight: 500; color: {DARK['WARN']}; background: transparent;
                border: none; border-radius: 8px;
            }}
            QPushButton:hover {{ background: rgba(230,175,0,0.10); }}
        """)
        leave_team_btn.clicked.connect(lambda: (self._close_settings(), self._confirm_leave_team()))
        layout.addWidget(leave_team_btn)

        # ── Quit ──
        quit_btn = QPushButton("Quit OH")
        quit_btn.setCursor(Qt.PointingHandCursor)
        quit_btn.setStyleSheet(f"""
            QPushButton {{
                text-align: left; padding: 8px 12px; font-size: 13px;
                font-weight: 500; color: {DARK['DANGER']}; background: transparent;
                border: none; border-radius: 8px;
            }}
            QPushButton:hover {{ background: rgba(229,57,53,0.10); }}
        """)
        quit_btn.clicked.connect(self.quit_requested.emit)
        layout.addWidget(quit_btn)

    def _show_hamburger_menu(self):
        """Toggle the inline settings view."""
        if self._settings_view.isVisible():
            self._close_settings()
        else:
            self._open_settings()

    def _open_settings(self):
        """Show the inline settings, hiding main content."""
        self._populate_settings()
        # Save visibility state of all main sections
        self._pre_settings_vis = {
            'user':      self._user_section.isVisible(),
            'ptt':       self._ptt_bar.isVisible(),
            'disconn':   self._disconn_bar.isVisible(),
            'conn':      self._conn_bar.isVisible(),
            'team':      self._team_bar.isVisible(),
            'onboarding': self._onboarding.isVisible(),
            'outgoing':  self._outgoing_banner.isVisible(),
            'incoming':  self._incoming_banner.isVisible(),
            'call':      self._call_banner.isVisible(),
            'message':   self._message_banner.isVisible(),
        }
        # Hide everything
        self._user_section.setVisible(False)
        self._ptt_bar.setVisible(False)
        self._disconn_bar.setVisible(False)
        self._conn_bar.setVisible(False)
        self._team_bar.setVisible(False)
        self._onboarding.setVisible(False)
        self._outgoing_banner.setVisible(False)
        self._incoming_banner.setVisible(False)
        self._call_banner.setVisible(False)
        self._message_banner.setVisible(False)
        self._settings_view.setVisible(True)
        # Expand panel to fit settings content (header + all settings items)
        self.setMinimumHeight(500)
        self._resize_panel()

    def _close_settings(self):
        """Hide settings, restore main content."""
        self._settings_view.setVisible(False)
        # Clear the minimum height set by _open_settings
        self.setMinimumHeight(0)
        # Restore previous visibility state
        vis = getattr(self, '_pre_settings_vis', {})
        self._user_section.setVisible(vis.get('user', True))
        self._ptt_bar.setVisible(vis.get('ptt', True))
        self._disconn_bar.setVisible(vis.get('disconn', False))
        self._conn_bar.setVisible(vis.get('conn', False))
        self._team_bar.setVisible(vis.get('team', False))
        self._onboarding.setVisible(vis.get('onboarding', False))
        self._outgoing_banner.setVisible(vis.get('outgoing', False))
        self._incoming_banner.setVisible(vis.get('incoming', False))
        self._call_banner.setVisible(vis.get('call', False))
        self._message_banner.setVisible(vis.get('message', False))
        self._resize_panel()

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
            self.orb.set_mode('INCOGNITO')
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
        self.dark_mode_toggled.emit(self._dark_mode)

    def apply_dark_mode(self, enabled):
        """Apply dark or light mode color palette to the panel.
        Note: The panel defaults to dark. This method exists for the settings toggle.
        """
        self._dark_mode = enabled
        # Since dark is now the default, this is a no-op for enabled=True.
        # A full light-mode implementation would require restyling everything;
        # for now we just update user row text color for consistency.
        text_color = DARK['TEXT'] if enabled else "#3a3a3a"
        for i in range(self._user_layout.count()):
            widget = self._user_layout.itemAt(i).widget()
            if widget and hasattr(widget, 'name_label'):
                widget.name_label.setStyleSheet(f"font-size: 14px; font-weight: 500; color: {text_color}; padding-bottom: 1px;")

    def _toggle_pin(self):
        self._pinned = not self._pinned
        self.pin_toggled.emit(self._pinned)
        self._update_pin_style(self._pinned)

        if self._pinned:
            # Collapse to compact PTT bar
            self._header.setVisible(False)

            self._conn_bar.setVisible(False)
            self._disconn_bar.setVisible(False)
            self._user_section.setVisible(False)
            self._ptt_bar.setVisible(False)
            self._incoming_banner.setVisible(False)
            self._call_banner.setVisible(False)
            self._pinned_compact.setVisible(True)
            # Force panel to compact size
            self.setFixedHeight(58)
        else:
            # Expand to full panel
            self.setMaximumHeight(16777215)  # Remove fixed height
            self.setMinimumHeight(0)
            self._header.setVisible(True)
            self._conn_bar.setVisible(self._connected)
            self._disconn_bar.setVisible(not self._connected)
            self._user_section.setVisible(True)
            self._ptt_bar.setVisible(True)
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
    def _resize_panel(self):
        """Resize the panel, but skip during onboarding (height is locked)."""
        if self._is_onboarding:
            return
        self.adjustSize()

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
    from PySide6.QtGui import QFontMetrics
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
