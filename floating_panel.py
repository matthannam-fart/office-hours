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
    QSystemTrayIcon, QMenu, QLineEdit, QSpacerItem, QSlider, QWidgetAction,
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

# ── Color Constants (from wireframe) ──────────────────────────────
COLORS = {
    'GREEN':  '#00a651',
    'YELLOW': '#e6af00',
    'RED':    '#e22a1a',
    'OPEN':   '#2ABFBF',
    'INCOGNITO': '#333333',
}

MODE_LABELS = {
    'GREEN':  'Available',
    'YELLOW': 'Busy',
    'RED':    'DND',
    'OPEN':   'Open',
}

RADIO_STATIONS = {
    'NTS Radio': 'https://stream-relay-geo.ntslive.net/stream?client=NTSRadio',
}

# Panel dimensions
PANEL_W = 280
PANEL_RADIUS = 12

# ── Font Loading ─────────────────────────────────────────────────
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'commercial-type-2507-JRGALW-desktop')
FONT_FAMILY = 'Focal'

def _load_fonts():
    """Load bundled Focal font family into Qt."""
    if not os.path.isdir(FONT_DIR):
        return
    for fname in sorted(os.listdir(FONT_DIR)):
        if fname.endswith(('.otf', '.ttf')):
            fpath = os.path.join(FONT_DIR, fname)
            font_id = QFontDatabase.addApplicationFont(fpath)
            if font_id < 0:
                print(f"Warning: could not load font {fname}")

_fonts_loaded = False


# ═══════════════════════════════════════════════════════════════════
#  Glowing Orb Widget (custom painted)
# ═══════════════════════════════════════════════════════════════════
class GlowingOrb(QWidget):
    """A QPainter-drawn glowing orb that changes color by mode."""

    def __init__(self, size=30, parent=None):
        super().__init__(parent)
        self._size = size
        self._color = QColor(COLORS['GREEN'])
        self._glow_radius = 0.0
        self._breathing = False
        self.setFixedSize(size, size)

        # Breathing animation
        self._anim = QPropertyAnimation(self, b"glowRadius")
        self._anim.setDuration(2500)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(6.0)
        self._anim.setEasingCurve(QEasingCurve.InOutSine)
        self._anim.setLoopCount(-1)  # infinite

    def get_glow_radius(self):
        return self._glow_radius

    def set_glow_radius(self, v):
        self._glow_radius = v
        self.update()

    glowRadius = Property(float, get_glow_radius, set_glow_radius)

    def set_mode(self, mode):
        color_hex = COLORS.get(mode, COLORS['GREEN'])
        self._color = QColor(color_hex)
        if mode in ('OPEN',):
            self.start_breathing()
        else:
            self.stop_breathing()
        self.update()

    def start_breathing(self):
        if not self._breathing:
            self._breathing = True
            self._anim.start()

    def stop_breathing(self):
        if self._breathing:
            self._breathing = False
            self._anim.stop()
            self._glow_radius = 0.0
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = self._size / 2, self._size / 2
        r = self._size / 2 - 4

        # Outer glow
        glow_color = QColor(self._color)
        glow_color.setAlpha(40)
        glow_grad = QRadialGradient(cx, cy, r + 6 + self._glow_radius)
        glow_grad.setColorAt(0, glow_color)
        glow_grad.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(glow_grad))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(0, 0, self._size, self._size))

        # Inner orb
        grad = QRadialGradient(cx - r * 0.3, cy - r * 0.3, r * 1.4)
        lighter = QColor(self._color).lighter(115)
        grad.setColorAt(0, lighter)
        grad.setColorAt(1, self._color)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        p.end()


# ═══════════════════════════════════════════════════════════════════
#  Small Orb for user list
# ═══════════════════════════════════════════════════════════════════
class SmallOrb(QWidget):
    """10px status orb for user rows."""

    def __init__(self, color='GREEN', parent=None):
        super().__init__(parent)
        self._color = QColor(COLORS.get(color, COLORS['GREEN']))
        self.setFixedSize(16, 16)

    def set_color(self, mode):
        self._color = QColor(COLORS.get(mode, COLORS['GREEN']))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = 8, 8
        r = 5

        # Subtle glow
        glow = QColor(self._color)
        glow.setAlpha(30)
        g = QRadialGradient(cx, cy, r + 3)
        g.setColorAt(0, glow)
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(g))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(0, 0, 16, 16))

        # Orb
        grad = QRadialGradient(cx - 1.5, cy - 1.5, r * 1.3)
        grad.setColorAt(0, QColor(self._color).lighter(115))
        grad.setColorAt(1, self._color)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        p.end()


# ═══════════════════════════════════════════════════════════════════
#  User Row Widget
# ═══════════════════════════════════════════════════════════════════
class UserRow(QWidget):
    """Single user row in the online list."""
    call_clicked = Signal(str)  # user_id

    def __init__(self, user_id, name, mode='GREEN', has_message=False, parent=None):
        super().__init__(parent)
        self.user_id = user_id
        self._hovered = False
        self.setFixedHeight(44)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 8, 6)
        layout.setSpacing(8)

        # Orb — centered vertically in the row
        self.orb = SmallOrb(mode)
        layout.addWidget(self.orb, 0, Qt.AlignVCenter)

        # Name
        self.name_label = QLabel(name)
        self.name_label.setStyleSheet("font-size: 14px; font-weight: 500; color: #3a3a3a; padding-bottom: 1px;")
        layout.addWidget(self.name_label, 1, Qt.AlignVCenter)

        # Message indicator (amber dot)
        self.msg_dot = QLabel()
        self.msg_dot.setFixedSize(18, 18)
        self.msg_dot.setStyleSheet("""
            background: #fff4cc;
            border: 1px solid #ffe066;
            border-radius: 9px;
        """)
        self.msg_dot.setVisible(has_message)
        layout.addWidget(self.msg_dot)

        # Call button (hidden until hover)
        btn_text = "Page" if mode == 'YELLOW' else "Call"
        self.call_btn = QPushButton(btn_text)
        self.call_btn.setFixedWidth(36)
        self.call_btn.setCursor(Qt.PointingHandCursor)
        if mode == 'YELLOW':
            self.call_btn.setStyleSheet("""
                QPushButton {
                    background: #fff4cc; color: #b8860b; border: 1px solid #ffe066;
                    border-radius: 6px; font-size: 11px; font-weight: 700; padding: 4px 0;
                }
                QPushButton:hover { background: #ffe680; }
            """)
        elif mode == 'RED':
            self.call_btn.setVisible(False)  # Can't call DND users
        else:
            self.call_btn.setStyleSheet("""
                QPushButton {
                    background: #e6f9ed; color: #008040; border: 1px solid #80e6a0;
                    border-radius: 6px; font-size: 11px; font-weight: 700; padding: 4px 0;
                }
                QPushButton:hover { background: #c8f0d8; }
            """)
        self.call_btn.setVisible(False)
        self.call_btn.clicked.connect(lambda: self.call_clicked.emit(self.user_id))
        layout.addWidget(self.call_btn)

    def set_message(self, has_msg):
        self.msg_dot.setVisible(has_msg)

    def enterEvent(self, event):
        self._hovered = True
        self.call_btn.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.call_btn.setVisible(False)
        super().leaveEvent(event)


# ═══════════════════════════════════════════════════════════════════
#  Toggle Switch Widget
# ═══════════════════════════════════════════════════════════════════
class ToggleSwitch(QWidget):
    """iOS-style toggle switch."""
    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._on = False
        self.setFixedSize(36, 20)
        self.setCursor(Qt.PointingHandCursor)

    def is_on(self):
        return self._on

    def set_on(self, val):
        self._on = val
        self.update()

    def mousePressEvent(self, event):
        self._on = not self._on
        self.toggled.emit(self._on)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # Track
        track_color = QColor('#2ABFBF') if self._on else QColor('#ddd')
        p.setBrush(QBrush(track_color))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, 0, 36, 20), 10, 10)

        # Knob
        knob_x = 18 if self._on else 2
        p.setBrush(QBrush(QColor('white')))
        p.drawEllipse(QRectF(knob_x, 2, 16, 16))
        p.end()


# ═══════════════════════════════════════════════════════════════════
#  The Main Floating Panel
# ═══════════════════════════════════════════════════════════════════
class FloatingPanel(QWidget):
    """Menu bar dropdown panel matching the wireframe."""

    # Signals to communicate back to IntercomApp
    mode_cycle_requested = Signal()
    open_toggled = Signal(bool)
    pin_toggled = Signal(bool)
    ptt_pressed = Signal()
    ptt_released = Signal()
    call_user_requested = Signal(str)     # user_id
    leave_requested = Signal()
    join_requested = Signal(str)          # room code
    create_requested = Signal()
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

    def __init__(self, parent=None):
        super().__init__(parent)
        # Load fonts once (must happen after QApplication exists)
        global _fonts_loaded
        if not _fonts_loaded:
            _load_fonts()
            _fonts_loaded = True
        # Set panel font to Focal
        self.setFont(QFont(FONT_FAMILY, 11))
        self._pinned = False
        self._connected = False
        self._current_mode = 'GREEN'
        self._is_open_line = False
        self._incognito = False
        self._dark_mode = False
        self._radio_station = None  # currently playing station name

        # Radio player
        self._audio_output = QAudioOutput()
        self._audio_output.setVolume(0.5)
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
                background: rgba(245, 245, 243, 200);
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: {PANEL_RADIUS}px;
            }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 1, 0, 9)  # nudged up for visual center
        outer.addWidget(self._frame)

        # Drop shadow
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 40))
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

        # ── Connection Bar ────────────────────────────────────
        self._conn_bar = self._build_conn_bar()
        root.addWidget(self._conn_bar)

        # ── Disconnected Bar (hidden when connected) ──────────
        self._disconn_bar = self._build_disconn_bar()
        self._disconn_bar.setVisible(False)
        root.addWidget(self._disconn_bar)

        # ── User List ─────────────────────────────────────────
        self._user_section = self._build_user_section()
        root.addWidget(self._user_section, 1)

        # ── PTT Bar ───────────────────────────────────────────
        self._ptt_bar = self._build_ptt_bar()
        root.addWidget(self._ptt_bar)

        # ── Pinned compact (hidden by default) ────────────────
        self._pinned_compact = self._build_pinned_compact()
        self._pinned_compact.setVisible(False)
        root.addWidget(self._pinned_compact)

        # Store root layout ref
        self._root = root

    # ── Header ────────────────────────────────────────────────────
    def _build_header(self):
        header = QFrame()
        header.setStyleSheet("border-bottom: 1px solid #eae8e4;")
        header.setFixedHeight(48)

        h = QHBoxLayout(header)
        h.setContentsMargins(12, 8, 10, 8)
        h.setSpacing(8)

        # Glowing orb (24px)
        self.orb = GlowingOrb(24)
        h.addWidget(self.orb)

        # Mode cycle button with custom-painted dot + label
        self.mode_btn = QPushButton()
        self.mode_btn.setCursor(Qt.PointingHandCursor)
        self.mode_btn.setMinimumHeight(28)
        self.mode_btn.clicked.connect(self.mode_cycle_requested.emit)
        h.addWidget(self.mode_btn)

        # The colored dot inside the mode button is drawn via stylesheet
        self._update_mode_btn()

        # Spacer
        h.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Expanding, QSizePolicy.Minimum))

        # Thin vertical divider
        divider = QFrame()
        divider.setFixedSize(1, 22)
        divider.setStyleSheet("background: #e0ddd8; border: none;")
        h.addWidget(divider)

        # Open toggle with label
        open_lbl = QLabel("Open")
        open_lbl.setStyleSheet("font-size: 12px; color: #999; font-weight: 500; border: none;")
        h.addWidget(open_lbl)

        self.open_toggle = ToggleSwitch()
        self.open_toggle.toggled.connect(self.open_toggled.emit)
        h.addWidget(self.open_toggle)

        # Pin button — custom painted
        self.pin_btn = QPushButton()
        self.pin_btn.setFixedSize(26, 26)
        self.pin_btn.setCursor(Qt.PointingHandCursor)
        self._update_pin_style(False)
        self.pin_btn.clicked.connect(self._toggle_pin)
        h.addWidget(self.pin_btn)

        return header

    def _update_pin_style(self, pinned):
        if pinned:
            self.pin_btn.setText("v")
            self.pin_btn.setStyleSheet("""
                QPushButton {
                    border: none; background: rgba(255,152,0,0.12);
                    font-size: 16px; font-weight: 900; color: #f57c00;
                    border-radius: 6px;
                }
                QPushButton:hover { background: rgba(255,152,0,0.22); }
            """)
        else:
            self.pin_btn.setText("^")
            self.pin_btn.setStyleSheet("""
                QPushButton {
                    border: none; background: rgba(255,152,0,0.08);
                    font-size: 16px; font-weight: 900; color: #ff9800;
                    border-radius: 6px;
                }
                QPushButton:hover { background: rgba(255,152,0,0.18); color: #f57c00; }
            """)

    def _update_mode_btn(self):
        mode = self._current_mode
        color = COLORS.get(mode, COLORS['GREEN'])
        # Darker text colors for each mode (from wireframe)
        text_colors = {
            'GREEN': '#008040', 'YELLOW': '#cc9900',
            'RED': '#c41a12', 'OPEN': '#1a8f8f'
        }
        text_color = text_colors.get(mode, '#008040')
        label = MODE_LABELS.get(mode, 'Available')

        # Use HTML to render a colored circle + text since QPushButton supports rich text via stylesheet
        self.mode_btn.setText(label)
        self.mode_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 5px 12px 5px 10px;
                border-radius: 8px;
                border: 1px solid rgba(0,0,0,0.06);
                background: rgba(0,0,0,0.03);
                font-size: 13px;
                font-weight: 600;
                color: {text_color};
                text-align: left;
            }}
            QPushButton:hover {{
                background: rgba(0,0,0,0.07);
            }}
        """)

    # ── Connection Bar ────────────────────────────────────────────
    def _build_conn_bar(self):
        bar = QFrame()
        bar.setStyleSheet("background: #fafaf8; border-bottom: 1px solid #eae8e4;")
        bar.setFixedHeight(36)

        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 0, 8, 0)
        h.setSpacing(6)

        # Green dot
        dot = QLabel("●")
        dot.setStyleSheet("color: #00a651; font-size: 10px; border: none;")
        h.addWidget(dot)

        # Room code
        self.room_label = QLabel("Connected · OH-7X3K")
        self.room_label.setStyleSheet("font-size: 12px; font-weight: 600; color: #5a5a5a; letter-spacing: 0.5px; border: none;")
        h.addWidget(self.room_label, 1)

        # Leave button
        self.leave_btn = QPushButton("Leave")
        self.leave_btn.setCursor(Qt.PointingHandCursor)
        self.leave_btn.setStyleSheet("""
            QPushButton {
                font-size: 11px; font-weight: 700; color: #c0392b;
                background: transparent; border: 1px solid #e8c4c0;
                border-radius: 8px; padding: 3px 10px;
            }
            QPushButton:hover { background: #fde8e5; }
        """)
        self.leave_btn.clicked.connect(self.leave_requested.emit)
        h.addWidget(self.leave_btn)

        # Quit button (gear icon)
        gear = QPushButton("✕")
        gear.setFixedSize(24, 24)
        gear.setToolTip("Quit Office Hours")
        gear.setStyleSheet("""
            QPushButton {
                border: none; background: transparent; font-size: 13px;
                color: #ccc; border-radius: 4px;
            }
            QPushButton:hover { color: #c0392b; background: rgba(192,57,43,0.08); }
        """)
        gear.clicked.connect(self.quit_requested.emit)
        h.addWidget(gear)

        return bar

    # ── Disconnected Bar ──────────────────────────────────────────
    def _build_disconn_bar(self):
        bar = QFrame()
        bar.setStyleSheet("background: transparent; border-bottom: 1px solid rgba(0,0,0,0.06);")
        bar.setFixedHeight(46)

        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 4, 8, 4)
        h.setSpacing(6)

        # Room code input
        self.room_input = QLineEdit()
        self.room_input.setPlaceholderText("Room code e.g. OH-7X3K")
        self.room_input.setStyleSheet("""
            QLineEdit {
                font-size: 12px; border: 1px solid rgba(0,0,0,0.1); border-radius: 6px;
                padding: 4px 8px; background: rgba(255,255,255,0.5); color: #3a3a3a;
            }
        """)
        h.addWidget(self.room_input, 1)

        # Join button
        join_btn = QPushButton("Join")
        join_btn.setCursor(Qt.PointingHandCursor)
        join_btn.setStyleSheet("""
            QPushButton {
                font-size: 11px; font-weight: 700; color: #00a651;
                background: #e6f9ed; border: 1px solid #80e6a0;
                border-radius: 8px; padding: 4px 10px;
            }
            QPushButton:hover { background: #c8f0d8; }
        """)
        join_btn.clicked.connect(lambda: self.join_requested.emit(self.room_input.text().strip()))
        h.addWidget(join_btn)

        # Create room button (+)
        create_btn = QPushButton("+")
        create_btn.setFixedSize(24, 24)
        create_btn.setCursor(Qt.PointingHandCursor)
        create_btn.setStyleSheet("""
            QPushButton {
                font-size: 17px; font-weight: 500; color: #00a651;
                background: transparent; border: none;
                border-radius: 12px; padding: 0 2px 1px 0;
            }
            QPushButton:hover { background: rgba(0,0,0,0.06); }
        """)
        create_btn.setToolTip("Create Room")
        create_btn.clicked.connect(self.create_requested.emit)
        h.addWidget(create_btn)

        # Hamburger menu (☰)
        self.menu_btn = QPushButton("☰")
        self.menu_btn.setFixedSize(24, 24)
        self.menu_btn.setCursor(Qt.PointingHandCursor)
        self.menu_btn.setStyleSheet("""
            QPushButton {
                font-size: 17px; color: #888;
                background: transparent; border: none;
                border-radius: 12px; padding: 0 0 2px 0;
            }
            QPushButton:hover { background: rgba(0,0,0,0.06); }
        """)
        self.menu_btn.clicked.connect(self._show_hamburger_menu)
        h.addWidget(self.menu_btn)

        return bar

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
        self._online_label.setStyleSheet("font-size: 11px; font-weight: 700; color: #aaa; letter-spacing: 1px;")
        sec_hdr.addWidget(self._online_label)
        sec_hdr.addStretch()
        self.online_count = QLabel("0")
        self.online_count.setStyleSheet("""
            font-size: 11px; font-weight: 700; color: #bbb;
            background: #f0eeeb; border-radius: 8px; padding: 2px 7px;
        """)
        sec_hdr.addWidget(self.online_count)
        v.addLayout(sec_hdr)

        # Scrollable user list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical {
                width: 4px; background: transparent;
            }
            QScrollBar::handle:vertical {
                background: #ddd; border-radius: 2px; min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)

        self._user_container = QWidget()
        self._user_layout = QVBoxLayout(self._user_container)
        self._user_layout.setContentsMargins(6, 0, 6, 6)
        self._user_layout.setSpacing(2)
        self._user_layout.addStretch()

        scroll.setWidget(self._user_container)
        v.addWidget(scroll, 1)

        return section

    # ── PTT Bar ───────────────────────────────────────────────────
    def _build_ptt_bar(self):
        bar = QFrame()
        bar.setStyleSheet("border-top: 1px solid #eae8e4;")
        bar.setFixedHeight(60)

        v = QVBoxLayout(bar)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(4)

        # PTT button
        self.ptt_btn = QPushButton("⬤  Hold to Talk")
        self.ptt_btn.setCursor(Qt.PointingHandCursor)
        self.ptt_btn.setStyleSheet("""
            QPushButton {
                background: #fafaf8; border: 1px solid #e0ded8;
                border-radius: 10px; padding: 8px; font-size: 13px;
                font-weight: 500; color: #888;
            }
            QPushButton:hover { background: #f0eeeb; border-color: #ccc; }
            QPushButton:pressed { background: #e8e6e2; }
        """)
        self.ptt_btn.pressed.connect(self.ptt_pressed.emit)
        self.ptt_btn.released.connect(self.ptt_released.emit)
        v.addWidget(self.ptt_btn)

        # Mode label below
        self.ptt_mode_label = QLabel("Open — always-on hot mic.")
        self.ptt_mode_label.setStyleSheet("font-size: 11px; color: #bbb; border: none;")
        self.ptt_mode_label.setAlignment(Qt.AlignCenter)
        self.ptt_mode_label.setVisible(False)
        v.addWidget(self.ptt_mode_label)

        return bar

    # ── Incoming Call Banner ──────────────────────────────────────
    # ── Outgoing Call Banner ────────────────────────────────────────
    def _build_outgoing_banner(self):
        banner = QFrame()
        banner.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #e3f2fd, stop:1 #f0f7ff);
            border-bottom: 1px solid #bbdefb;
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
        self.outgoing_name.setStyleSheet("font-size: 14px; font-weight: 600; color: #1565c0;")
        call_info.addWidget(self.outgoing_name)
        self.outgoing_sub = QLabel("Calling...")
        self.outgoing_sub.setStyleSheet("font-size: 11px; color: #64b5f6;")
        call_info.addWidget(self.outgoing_sub)
        top.addLayout(call_info, 1)
        v.addLayout(top)

        # Cancel button
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #d32f2f; border: 1px solid #ef9a9a;
                border-radius: 8px; padding: 6px 16px; font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover { background: #ffebee; }
        """)
        cancel_btn.clicked.connect(self.cancel_call_requested.emit)
        v.addWidget(cancel_btn)

        return banner

    # ── Incoming Call Banner ───────────────────────────────────────
    def _build_incoming_banner(self):
        banner = QFrame()
        banner.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #e8f5e9, stop:1 #f1f8f2);
            border-bottom: 1px solid #c8e6c9;
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
        self.incoming_name.setStyleSheet("font-size: 14px; font-weight: 600; color: #2e7d32;")
        caller_info.addWidget(self.incoming_name)
        caller_sub = QLabel("wants to connect")
        caller_sub.setStyleSheet("font-size: 11px; color: #66bb6a;")
        caller_info.addWidget(caller_sub)
        top.addLayout(caller_info, 1)
        v.addLayout(top)

        # Accept / Decline
        btns = QHBoxLayout()
        accept_btn = QPushButton("Accept")
        accept_btn.setCursor(Qt.PointingHandCursor)
        accept_btn.setStyleSheet("""
            QPushButton {
                background: #43a047; color: white; border: none;
                border-radius: 8px; padding: 6px 16px; font-weight: 700;
                font-size: 13px;
            }
            QPushButton:hover { background: #388e3c; }
        """)
        accept_btn.clicked.connect(self.accept_call_requested.emit)
        btns.addWidget(accept_btn)

        decline_btn = QPushButton("Decline")
        decline_btn.setCursor(Qt.PointingHandCursor)
        decline_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #999; border: 1px solid #ddd;
                border-radius: 8px; padding: 6px 16px; font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover { background: #f5f5f5; }
        """)
        decline_btn.clicked.connect(self.decline_call_requested.emit)
        btns.addWidget(decline_btn)
        v.addLayout(btns)

        return banner

    # ── Call Banner ────────────────────────────────────────────────
    def _build_call_banner(self):
        banner = QFrame()
        banner.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #e8f5e9, stop:1 #f1f8f2);
            border-bottom: 1px solid #c8e6c9;
        """)

        h = QHBoxLayout(banner)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(8)

        call_orb = GlowingOrb(20)
        call_orb.start_breathing()
        h.addWidget(call_orb)

        self.call_name_label = QLabel("Jane D.")
        self.call_name_label.setStyleSheet("font-size: 14px; font-weight: 600; color: #2e7d32;")
        h.addWidget(self.call_name_label, 1)

        self.call_timer_label = QLabel("0:00")
        self.call_timer_label.setStyleSheet("font-size: 13px; color: #66bb6a; font-weight: 600;")
        h.addWidget(self.call_timer_label)

        end_btn = QPushButton("End")
        end_btn.setCursor(Qt.PointingHandCursor)
        end_btn.setStyleSheet("""
            QPushButton {
                background: #ef5350; color: white; border: none;
                border-radius: 8px; padding: 4px 12px; font-weight: 700;
                font-size: 12px;
            }
            QPushButton:hover { background: #e53935; }
        """)
        end_btn.clicked.connect(self.end_call_requested.emit)
        h.addWidget(end_btn)

        return banner

    # ── Pinned Compact ────────────────────────────────────────────
    def _build_message_banner(self):
        banner = QFrame()
        banner.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #fff8e1, stop:1 #fffde7);
            border-bottom: 1px solid #ffe082;
        """)

        h = QHBoxLayout(banner)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(8)

        icon = QLabel("\U0001f4e9")
        icon.setStyleSheet("font-size: 18px;")
        h.addWidget(icon)

        self._msg_label = QLabel("New Message")
        self._msg_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #f57f17;")
        h.addWidget(self._msg_label, 1)

        play_btn = QPushButton("\u25b6 Play")
        play_btn.setCursor(Qt.PointingHandCursor)
        play_btn.setStyleSheet("""
            QPushButton {
                background: #43a047; color: white; border: none;
                border-radius: 8px; padding: 4px 12px; font-weight: 700;
                font-size: 12px;
            }
            QPushButton:hover { background: #388e3c; }
        """)
        play_btn.clicked.connect(self.play_message_requested.emit)
        h.addWidget(play_btn)

        dismiss_btn = QPushButton("\u2715")
        dismiss_btn.setCursor(Qt.PointingHandCursor)
        dismiss_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #999; border: none;
                font-size: 16px; font-weight: 700; padding: 2px 6px;
            }
            QPushButton:hover { color: #555; }
        """)
        dismiss_btn.clicked.connect(lambda: self._message_banner.setVisible(False))
        h.addWidget(dismiss_btn)

        return banner

    def show_message(self):
        """Show the new message indicator banner."""
        self._message_banner.setVisible(True)
        self.adjustSize()

    def hide_message(self):
        """Hide the message indicator banner."""
        self._message_banner.setVisible(False)
        self.adjustSize()

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
            self.ptt_btn.setStyleSheet("""
                QPushButton {
                    background: #fafaf8; border: 1px solid #e0ded8;
                    border-radius: 10px; padding: 8px; font-size: 13px;
                    font-weight: 500; color: #ccc;
                }
            """)
            self.ptt_mode_label.setVisible(False)
        else:
            self.ptt_btn.setEnabled(True)
            self.ptt_btn.setStyleSheet("""
                QPushButton {
                    background: #fafaf8; border: 1px solid #e0ded8;
                    border-radius: 10px; padding: 8px; font-size: 13px;
                    font-weight: 500; color: #888;
                }
                QPushButton:hover { background: #f0eeeb; border-color: #ccc; }
                QPushButton:pressed { background: #e8e6e2; }
            """)

    def set_open_line(self, is_open):
        """Toggle open line state."""
        self._is_open_line = is_open
        self.open_toggle.set_on(is_open)
        if is_open:
            self.ptt_btn.setText("⬤  Open Line")
            self.ptt_mode_label.setText("Open — always-on hot mic.")
            self.ptt_mode_label.setVisible(True)
        else:
            self.ptt_btn.setText("⬤  Hold to Talk")
            self.ptt_mode_label.setVisible(False)

    def set_display_name(self, name):
        """Set the display name shown in the pinned compact bar."""
        self._display_name = name
        self._update_pinned_style()

    def set_connection(self, connected, room_code=""):
        """Switch between connected and disconnected states."""
        self._connected = connected
        self._conn_bar.setVisible(connected)
        self._disconn_bar.setVisible(not connected)
        if connected and room_code:
            self.room_label.setText(f"Connected · {room_code}")

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
        if not self._pinned:
            # Fixed chrome: header(48) + disconn/conn(46) + section hdr(28) + ptt(60) + margins(20)
            chrome_height = 48 + 46 + 28 + 60 + 20
            user_height = len(users) * 46
            target = chrome_height + max(user_height, 46)  # min space for 1 row
            target = min(target, 500)  # cap so it doesn't go off-screen
            self.setFixedHeight(target)

    def show_outgoing(self, target_name):
        """Show the outgoing call banner while waiting for response."""
        self.outgoing_name.setText(target_name)
        self.outgoing_sub.setText("Calling...")
        self._outgoing_banner.setVisible(True)

    def hide_outgoing(self):
        """Hide the outgoing call banner."""
        self._outgoing_banner.setVisible(False)

    def show_incoming(self, caller_name):
        """Show the incoming call banner."""
        self.incoming_name.setText(caller_name)
        self._incoming_banner.setVisible(True)

    def hide_incoming(self):
        """Hide the incoming call banner."""
        self._incoming_banner.setVisible(False)

    def show_call(self, caller_name):
        """Show the in-call banner."""
        self.call_name_label.setText(caller_name)
        self._call_banner.setVisible(True)
        self._incoming_banner.setVisible(False)

    def hide_call(self):
        """Hide the in-call banner."""
        self._call_banner.setVisible(False)

    def update_call_timer(self, text):
        """Update the call timer display."""
        self.call_timer_label.setText(text)

    def set_ptt_active(self, active):
        """Visual feedback when PTT is held."""
        if active:
            self.ptt_btn.setStyleSheet("""
                QPushButton {
                    background: #fee; border: 2px solid #e22a1a;
                    border-radius: 10px; padding: 8px; font-size: 13px;
                    font-weight: 600; color: #e22a1a;
                }
            """)
        else:
            # Restore normal style
            self.set_mode(self._current_mode)

    # ── Pinned Mode ───────────────────────────────────────────────
    def _show_hamburger_menu(self):
        """Show the settings/options menu from the hamburger button."""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: rgba(255,255,255,0.92);
                border: 1px solid rgba(0,0,0,0.1);
                border-radius: 8px; padding: 2px;
                font-size: 13px;
            }
            QMenu::item {
                padding: 4px 8px;
                border-radius: 4px;
                color: #2ABFBF;
            }
            QMenu::item:selected { background: rgba(0,0,0,0.06); }
            QMenu::separator {
                height: 1px; background: rgba(0,0,0,0.08);
                margin: 2px 6px;
            }
            QMenu::indicator { width: 0; height: 0; }
        """)

        # Incognito toggle
        incognito_label = "👻  Incognito" if not self._incognito else "👻  𝗖𝗼𝗴𝗻𝗶𝘁𝗼"
        incognito = menu.addAction(incognito_label)
        incognito.triggered.connect(self._toggle_incognito)

        # Dark mode toggle
        dark_label = "🌙  Dark Mode" if not self._dark_mode else "☀️  𝗟𝗶𝗴𝗵𝘁 𝗠𝗼𝗱𝗲"
        dark = menu.addAction(dark_label)
        dark.triggered.connect(self._toggle_dark_mode)

        menu.addSeparator()

        # Radio toggle
        if self._radio_station:
            radio = menu.addAction("📻  𝗥𝗮𝗱𝗶𝗼")
            radio.triggered.connect(self._stop_radio)

            # Volume slider
            vol_widget = QWidget()
            vol_layout = QHBoxLayout(vol_widget)
            vol_layout.setContentsMargins(8, 2, 8, 2)
            vol_label = QLabel("🔊")
            vol_label.setStyleSheet("font-size: 11px; color: #2ABFBF;")
            vol_slider = QSlider(Qt.Horizontal)
            vol_slider.setRange(0, 100)
            vol_slider.setValue(int(self._audio_output.volume() * 100))
            vol_slider.setStyleSheet("""
                QSlider::groove:horizontal {
                    height: 3px; background: rgba(0,0,0,0.1); border-radius: 1px;
                }
                QSlider::handle:horizontal {
                    width: 10px; height: 10px; margin: -4px 0;
                    background: #2ABFBF; border-radius: 5px;
                }
                QSlider::sub-page:horizontal { background: #2ABFBF; border-radius: 1px; }
            """)
            vol_slider.valueChanged.connect(lambda v: self._audio_output.setVolume(v / 100.0))
            vol_layout.addWidget(vol_label)
            vol_layout.addWidget(vol_slider)
            vol_action = QWidgetAction(menu)
            vol_action.setDefaultWidget(vol_widget)
            menu.addAction(vol_action)
        else:
            radio = menu.addAction("📻  Radio")
            radio.triggered.connect(lambda: self._play_radio('NTS Radio'))

        menu.addSeparator()

        # Audio Input
        try:
            import sounddevice as sd
            devices = sd.query_devices()

            # Input device
            in_widget = QWidget()
            in_layout = QHBoxLayout(in_widget)
            in_layout.setContentsMargins(8, 4, 8, 4)
            in_label = QLabel("🎤")
            in_label.setStyleSheet("font-size: 13px; color: #2ABFBF;")
            in_combo = QComboBox()
            in_combo.setStyleSheet("""
                QComboBox {
                    font-size: 11px; padding: 2px 4px;
                    border: 1px solid rgba(0,0,0,0.1); border-radius: 4px;
                    background: white; min-width: 140px;
                }
            """)
            in_combo.addItem("System Default", None)
            current_in = getattr(self, '_current_input_idx', None)
            for i, d in enumerate(devices):
                if d['max_input_channels'] > 0:
                    in_combo.addItem(d['name'][:30], i)
                    if current_in == i:
                        in_combo.setCurrentIndex(in_combo.count() - 1)
            in_combo.currentIndexChanged.connect(
                lambda idx, c=in_combo: self._on_input_device_changed(c.currentData())
            )
            in_layout.addWidget(in_label)
            in_layout.addWidget(in_combo, 1)
            in_action = QWidgetAction(menu)
            in_action.setDefaultWidget(in_widget)
            menu.addAction(in_action)

            # Output device
            out_widget = QWidget()
            out_layout = QHBoxLayout(out_widget)
            out_layout.setContentsMargins(8, 4, 8, 4)
            out_label = QLabel("🔈")
            out_label.setStyleSheet("font-size: 13px; color: #2ABFBF;")
            out_combo = QComboBox()
            out_combo.setStyleSheet("""
                QComboBox {
                    font-size: 11px; padding: 2px 4px;
                    border: 1px solid rgba(0,0,0,0.1); border-radius: 4px;
                    background: white; min-width: 140px;
                }
            """)
            out_combo.addItem("System Default", None)
            current_out = getattr(self, '_current_output_idx', None)
            for i, d in enumerate(devices):
                if d['max_output_channels'] > 0:
                    out_combo.addItem(d['name'][:30], i)
                    if current_out == i:
                        out_combo.setCurrentIndex(out_combo.count() - 1)
            out_combo.currentIndexChanged.connect(
                lambda idx, c=out_combo: self._on_output_device_changed(c.currentData())
            )
            out_layout.addWidget(out_label)
            out_layout.addWidget(out_combo, 1)
            out_action = QWidgetAction(menu)
            out_action.setDefaultWidget(out_widget)
            menu.addAction(out_action)
        except Exception as e:
            print(f"Audio device menu error: {e}")

        menu.addSeparator()

        # Quit
        quit_action = menu.addAction("🙊  Quit OH")
        quit_action.triggered.connect(self.quit_requested.emit)

        # Show below the menu button
        pos = self.menu_btn.mapToGlobal(
            QPoint(0, self.menu_btn.height() + 4)
        )
        menu.exec(pos)

    def _on_input_device_changed(self, device_index):
        self._current_input_idx = device_index
        self.audio_input_changed.emit(device_index)

    def _on_output_device_changed(self, device_index):
        self._current_output_idx = device_index
        self.audio_output_changed.emit(device_index)

    def _toggle_incognito(self):
        self._incognito = not self._incognito
        if self._incognito:
            # Save current mode, go black
            self._pre_incognito_mode = self._current_mode
            self.orb.set_mode('INCOGNITO')
            self.mode_btn.setText("Incognito")
            self.mode_btn.setStyleSheet("""
                QPushButton {
                    padding: 5px 12px 5px 10px;
                    border-radius: 8px;
                    border: 1px solid rgba(0,0,0,0.06);
                    background: rgba(0,0,0,0.03);
                    font-size: 13px; font-weight: 600;
                    color: #555; text-align: left;
                }
                QPushButton:hover { background: rgba(0,0,0,0.07); }
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
        """Apply dark or light mode color palette to the panel."""
        self._dark_mode = enabled
        if enabled:
            # Dark frame
            self._frame.setStyleSheet(f"""
                #panelFrame {{
                    background: rgba(30, 30, 30, 230);
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: {PANEL_RADIUS}px;
                }}
            """)
            # Header
            self._header.setStyleSheet("border-bottom: 1px solid rgba(255,255,255,0.08);")
            # Disconn bar
            self._disconn_bar.setStyleSheet("background: transparent; border-bottom: 1px solid rgba(255,255,255,0.06);")
            self.room_input.setStyleSheet("""
                QLineEdit {
                    font-size: 12px; border: 1px solid rgba(255,255,255,0.15); border-radius: 6px;
                    padding: 4px 8px; background: rgba(255,255,255,0.08); color: #ccc;
                }
            """)
            # Section labels
            self._online_label.setStyleSheet("font-size: 11px; font-weight: 700; color: #666; letter-spacing: 2px;")
            self.online_count.setStyleSheet("""
                font-size: 11px; font-weight: 700; color: #888;
                background: rgba(255,255,255,0.08); border-radius: 4px; padding: 1px 6px;
            """)
            # PTT bar
            self._ptt_bar.setStyleSheet("border-top: 1px solid rgba(255,255,255,0.08);")
        else:
            # Light frame (original)
            self._frame.setStyleSheet(f"""
                #panelFrame {{
                    background: rgba(245, 245, 243, 200);
                    border: 1px solid rgba(0, 0, 0, 0.08);
                    border-radius: {PANEL_RADIUS}px;
                }}
            """)
            self._header.setStyleSheet("border-bottom: 1px solid #eae8e4;")
            self._disconn_bar.setStyleSheet("background: transparent; border-bottom: 1px solid rgba(0,0,0,0.06);")
            self.room_input.setStyleSheet("""
                QLineEdit {
                    font-size: 12px; border: 1px solid rgba(0,0,0,0.1); border-radius: 6px;
                    padding: 4px 8px; background: rgba(255,255,255,0.5); color: #3a3a3a;
                }
            """)
            self._online_label.setStyleSheet("font-size: 11px; font-weight: 700; color: #bbb; letter-spacing: 2px;")
            self.online_count.setStyleSheet("""
                font-size: 11px; font-weight: 700; color: #bbb;
                background: #f0eeeb; border-radius: 4px; padding: 1px 6px;
            """)
            self._ptt_bar.setStyleSheet("border-top: 1px solid #eae8e4;")

        # Update user row text color
        text_color = "#ccc" if enabled else "#3a3a3a"
        for i in range(self._user_list_layout.count()):
            widget = self._user_list_layout.itemAt(i).widget()
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
            if self._connected:
                self._conn_bar.setVisible(True)
            else:
                self._disconn_bar.setVisible(True)
            self._user_section.setVisible(True)
            self._ptt_bar.setVisible(True)
            self._pinned_compact.setVisible(False)
            self.adjustSize()

    def is_pinned(self):
        return self._pinned

    # ── Positioning ───────────────────────────────────────────────
    def show_at(self, global_pos):
        """Show panel anchored below a global position (tray icon)."""
        self.adjustSize()
        x = global_pos.x() - self.width() // 2
        y = global_pos.y() + 4
        # Keep on screen
        screen = QApplication.primaryScreen().availableGeometry()
        if x + self.width() > screen.right():
            x = screen.right() - self.width() - 4
        if x < screen.left():
            x = screen.left() + 4
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

    def on_open_toggle(is_on):
        panel.set_open_line(is_on)
        if is_on:
            tray.setIcon(create_oh_icon(COLORS['OPEN']))
        else:
            tray.setIcon(create_oh_icon(COLORS[modes[mode_idx[0]]]))

    panel.open_toggled.connect(on_open_toggle)

    def on_leave():
        panel.set_connection(False)

    panel.leave_requested.connect(on_leave)

    def on_join(code):
        if code:
            panel.set_connection(True, code.upper())

    panel.join_requested.connect(on_join)

    def on_create():
        panel.set_connection(True, "OH-NEW1")

    panel.create_requested.connect(on_create)

    sys.exit(app.exec())
