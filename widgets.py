"""
widgets.py — Reusable UI widgets for Office Hours
Extracted from floating_panel.py for maintainability.
"""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QSizePolicy
from PySide6.QtCore import Qt, Signal, QRectF, QTimer
from PySide6.QtGui import QColor, QPainter, QBrush, QRadialGradient, QPen, QFont

# Import shared constants (from separate module to avoid circular imports)
from ui_constants import COLORS, DARK


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
        self._glow_dir = 1
        self._breathing = False
        self.setFixedSize(size, size)

        # Breathing animation via QTimer to avoid PySide6 custom property crashes on Windows
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._anim_step)

    def _anim_step(self):
        # ~2500ms full cycle at 30ms ticks: 1250/30 ≈ 41 ticks, 6.0/41 ≈ 0.14
        self._glow_radius += self._glow_dir * 0.14
        if self._glow_radius >= 6.0:
            self._glow_radius = 6.0
            self._glow_dir = -1
        elif self._glow_radius <= 0.0:
            self._glow_radius = 0.0
            self._glow_dir = 1
        self.update()

    def set_color(self, mode):
        self._color = QColor(COLORS.get(mode, COLORS['GREEN']))
        self.update()

    # Alias so both names work (panel uses set_mode, some code uses set_color)
    def set_mode(self, mode):
        self.set_color(mode)

    def start_breathing(self):
        self._breathing = True
        self._glow_radius = 0.0
        self._glow_dir = 1
        self._anim_timer.start(30)

    def stop_breathing(self):
        self._anim_timer.stop()
        self._breathing = False
        self._glow_radius = 0.0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx = self._size / 2
        cy = self._size / 2
        r = self._size / 2 - 4

        # Outer glow
        glow_r = r + 4 + self._glow_radius
        glow = QColor(self._color)
        glow.setAlpha(int(50 - self._glow_radius * 5))
        g = QRadialGradient(cx, cy, glow_r)
        g.setColorAt(0, glow)
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(g))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(cx - glow_r, cy - glow_r, glow_r * 2, glow_r * 2))

        # Main orb with gradient
        grad = QRadialGradient(cx - r * 0.3, cy - r * 0.3, r * 1.5)
        grad.setColorAt(0, QColor(self._color).lighter(130))
        grad.setColorAt(0.7, self._color)
        grad.setColorAt(1, QColor(self._color).darker(110))
        p.setBrush(QBrush(grad))

        # Subtle border
        border = QColor(self._color).darker(115)
        border.setAlpha(80)
        p.setPen(QPen(border, 0.5))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        p.end()


# ═══════════════════════════════════════════════════════════════════
#  Audio Level Meter
# ═══════════════════════════════════════════════════════════════════
class LevelMeter(QWidget):
    """Tiny horizontal audio level bar for call banners."""

    def __init__(self, color="#4caf50", width=60, height=6, parent=None):
        super().__init__(parent)
        self._level = 0.0  # 0.0–1.0
        self._color = QColor(color)
        self._bg = QColor(DARK['BORDER'])
        self.setFixedSize(width, height)

    def set_level(self, level):
        """Set audio level (0.0–1.0). Thread-safe via Qt's queued connection."""
        self._level = max(0.0, min(1.0, level))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.height() / 2

        # Background track
        p.setPen(Qt.NoPen)
        p.setBrush(self._bg)
        p.drawRoundedRect(0, 0, self.width(), self.height(), r, r)

        # Filled portion
        if self._level > 0.01:
            fill_w = max(self.height(), int(self.width() * self._level))
            p.setBrush(self._color)
            p.drawRoundedRect(0, 0, fill_w, self.height(), r, r)

        p.end()


# ═══════════════════════════════════════════════════════════════════
#  Unicode EQ Visualizer — retro VU meter (green → yellow → red)
# ═══════════════════════════════════════════════════════════════════
import random

# Block characters from shortest to tallest
_BLOCKS = " ▁▂▃▄▅▆▇█"

# VU meter colors: green (low) → yellow (mid) → red (hot)
_VU_COLORS = {
    0: "#333333",  # silent — dim
    1: "#4caf50",  # green
    2: "#4caf50",
    3: "#66bb6a",
    4: "#c8e600",  # yellow-green
    5: "#fdd835",  # yellow
    6: "#ffb300",  # amber
    7: "#ff6d00",  # orange
    8: "#e53935",  # red — hot!
}

class UnicodeEQ(QWidget):
    """Retro VU-meter equalizer with green/yellow/red color coding.
    Each bar is colored by its height — like a real stereo."""

    def __init__(self, num_bars=8, color="#4caf50", parent=None):
        super().__init__(parent)
        self._num_bars = num_bars
        self._fallback_color = color
        self._level = 0.0
        self._bars = [0] * num_bars
        self._font_size = 14
        # Size: each bar char is roughly font_size * 0.7 wide, plus letter-spacing
        bar_width = int(self._font_size * 0.75 * num_bars) + 8
        self.setFixedSize(bar_width, self._font_size + 4)

    def set_level(self, level):
        """Update with audio level (0.0–1.0)."""
        self._level = max(0.0, min(1.0, level))
        target = int(self._level * 8)
        for i in range(self._num_bars):
            if self._level > 0.02:
                jitter = random.randint(-2, 2)
                self._bars[i] = max(0, min(8, target + jitter))
            else:
                self._bars[i] = max(0, self._bars[i] - random.randint(1, 2))
        self.update()

    def paintEvent(self, event):
        from PySide6.QtGui import QFont
        import platform
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        mono = "Menlo" if platform.system() == "Darwin" else "Consolas"
        font = QFont(mono, self._font_size)
        font.setStyleHint(QFont.Monospace)
        p.setFont(font)

        char_w = self.width() / self._num_bars
        for i, h in enumerate(self._bars):
            color = QColor(_VU_COLORS.get(h, self._fallback_color))
            p.setPen(color)
            x = int(i * char_w)
            p.drawText(x, self._font_size, _BLOCKS[h])

        p.end()


# ═══════════════════════════════════════════════════════════════════
#  Small Orb for user list
# ═══════════════════════════════════════════════════════════════════
class SmallOrb(QWidget):
    """10px status orb for user rows."""

    def __init__(self, color='GREEN', parent=None):
        super().__init__(parent)
        self._color = QColor(COLORS.get(color, '#555555') if color != 'OFFLINE' else '#555555')
        self.setFixedSize(16, 16)

    def set_color(self, mode):
        if mode == 'OFFLINE':
            self._color = QColor('#555555')
        else:
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
    """Clickable user row — click to select as PTT target."""
    call_clicked = Signal(str)            # user_id (legacy)
    intercom_pressed = Signal(str)        # user_id — used for PTT start (from main.py)
    intercom_released = Signal(str)       # user_id — used for PTT stop (from main.py)
    user_selected = Signal(str)           # user_id — single click to select as target

    # Visual states
    STATE_IDLE = "idle"
    STATE_SELECTED = "selected"
    STATE_CONNECTING = "connecting"
    STATE_LIVE = "live"

    def __init__(self, user_id, name, mode='GREEN', has_message=False, parent=None):
        super().__init__(parent)
        self.user_id = user_id
        self._mode = mode
        self._offline = (mode == 'OFFLINE')
        self._pressed = False
        self._state = self.STATE_IDLE
        self.setFixedHeight(40 if self._offline else 50)
        if not self._offline:
            self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 12, 6)
        layout.setSpacing(10)

        # Orb
        self.orb = SmallOrb(mode)
        layout.addWidget(self.orb, 0, Qt.AlignVCenter)

        # Name
        self.name_label = QLabel(name)
        if self._offline:
            self.name_label.setStyleSheet(f"font-size: 13px; font-weight: 400; color: {DARK['TEXT_FAINT']}; border: none;")
        else:
            self.name_label.setStyleSheet(f"font-size: 15px; font-weight: 500; color: {DARK['TEXT']}; border: none;")
        layout.addWidget(self.name_label, 1, Qt.AlignVCenter)

        # Message indicator (amber dot)
        self.msg_dot = QLabel()
        self.msg_dot.setFixedSize(18, 18)
        self.msg_dot.setStyleSheet(f"""
            background: rgba(230, 175, 0, 0.20);
            border: 1px solid rgba(230, 175, 0, 0.40);
            border-radius: 9px;
        """)
        self.msg_dot.setVisible(has_message)
        layout.addWidget(self.msg_dot)

        # Inline mini EQ (hidden until live)
        self._eq = UnicodeEQ(num_bars=6, color=DARK['TEXT_DIM'])
        self._eq.setVisible(False)
        layout.addWidget(self._eq)

        # Status hint (shown during connecting/live)
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"font-size: 10px; font-weight: 600; color: {DARK['TEXT_FAINT']}; border: none;")
        self._status_lbl.setVisible(False)
        layout.addWidget(self._status_lbl)

        # Apply initial style (must be after name_label is created)
        self._apply_style()

    def _apply_style(self):
        """Set background based on current state."""
        if self._state == self.STATE_LIVE:
            self.setStyleSheet(f"""
                UserRow {{
                    background: rgba(229, 57, 53, 0.12);
                    border: 1px solid rgba(229, 57, 53, 0.25);
                    border-radius: 10px;
                }}
            """)
            self.name_label.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {DARK['DANGER']}; border: none;")
        elif self._state == self.STATE_CONNECTING:
            self.setStyleSheet(f"""
                UserRow {{
                    background: rgba(255, 255, 255, 0.06);
                    border: 1px solid {DARK['BORDER']};
                    border-radius: 10px;
                }}
            """)
            self.name_label.setStyleSheet(f"font-size: 15px; font-weight: 500; color: {DARK['TEXT_DIM']}; border: none;")
        elif self._state == self.STATE_SELECTED:
            self.setStyleSheet(f"""
                UserRow {{
                    background: rgba(0, 166, 81, 0.12);
                    border-left: 3px solid rgba(0, 166, 81, 0.80);
                    border-top: none; border-right: none; border-bottom: none;
                    border-radius: 10px;
                }}
            """)
            self.name_label.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {DARK['ACCENT_LT']}; border: none;")
        else:
            self.setStyleSheet(f"""
                UserRow {{
                    background: {DARK['BG_RAISED']};
                    border: 1px solid transparent;
                    border-radius: 10px;
                }}
                UserRow:hover {{
                    background: {DARK['BG_HOVER']};
                    border: 1px solid {DARK['BORDER']};
                }}
            """)
            self.name_label.setStyleSheet(f"font-size: 15px; font-weight: 500; color: {DARK['TEXT']}; border: none;")

    def set_state(self, state):
        """Update visual state: idle, selected, connecting, or live."""
        self._state = state
        self._apply_style()
        if state == self.STATE_LIVE:
            self._status_lbl.setText("LIVE")
            self._status_lbl.setStyleSheet(f"font-size: 10px; font-weight: 700; color: {DARK['DANGER']}; border: none;")
            self._status_lbl.setVisible(True)
            self._eq.setVisible(True)
        elif state == self.STATE_CONNECTING:
            self._status_lbl.setText("...")
            self._status_lbl.setStyleSheet(f"font-size: 10px; font-weight: 600; color: {DARK['TEXT_FAINT']}; border: none;")
            self._status_lbl.setVisible(True)
            self._eq.setVisible(False)
        elif state == self.STATE_SELECTED:
            self._status_lbl.setText("TALK")
            self._status_lbl.setStyleSheet(f"font-size: 10px; font-weight: 700; color: {DARK['ACCENT']}; border: none;")
            self._status_lbl.setVisible(True)
            self._eq.setVisible(False)
        else:
            self._status_lbl.setVisible(False)
            self._eq.setVisible(False)

    def set_eq_level(self, level):
        """Update the inline EQ meter."""
        self._eq.set_level(level)

    def set_message(self, has_msg):
        self.msg_dot.setVisible(has_msg)

    def mousePressEvent(self, event):
        if self._offline:
            return
        if event.button() == Qt.LeftButton:
            self._pressed = True
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._offline:
            return
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            # Single click selects this user as PTT target
            self.user_selected.emit(self.user_id)
        super().mouseReleaseEvent(event)


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
        track_color = QColor('#2ABFBF') if self._on else QColor(DARK['BORDER'])
        p.setBrush(QBrush(track_color))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, 0, 36, 20), 10, 10)

        # Knob
        knob_x = 18 if self._on else 2
        p.setBrush(QBrush(QColor('white')))
        p.drawEllipse(QRectF(knob_x, 2, 16, 16))
        p.end()


# ═══════════════════════════════════════════════════════════════════
#  Sidebar Navigation Button
# ═══════════════════════════════════════════════════════════════════
class NavButton(QWidget):
    """Vertical sidebar nav button with icon + label."""
    clicked = Signal(str)  # emits the nav key

    def __init__(self, key, icon_char, label, parent=None):
        super().__init__(parent)
        self._key = key
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(56, 48)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 3)
        layout.setSpacing(1)
        layout.setAlignment(Qt.AlignCenter)

        self._icon = QLabel(icon_char)
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setStyleSheet(f"font-size: 18px; color: {DARK['TEXT_DIM']}; border: none; background: transparent;")
        layout.addWidget(self._icon)

        self._label = QLabel(label)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet(f"font-size: 9px; font-weight: 700; color: {DARK['TEXT_FAINT']}; letter-spacing: 0.5px; border: none; background: transparent;")
        layout.addWidget(self._label)

        self._update_style()

    def set_selected(self, selected):
        self._selected = selected
        self._update_style()

    def _update_style(self):
        if self._selected:
            self.setStyleSheet(f"""
                NavButton {{
                    background: rgba(255, 255, 255, 0.06);
                    border-left: 2px solid {DARK['ACCENT']};
                    border-radius: 0px;
                }}
            """)
            self._icon.setStyleSheet(f"font-size: 18px; color: {DARK['TEXT']}; border: none; background: transparent;")
            self._label.setStyleSheet(f"font-size: 9px; font-weight: 700; color: {DARK['ACCENT_LT']}; letter-spacing: 0.5px; border: none; background: transparent;")
        else:
            self.setStyleSheet(f"""
                NavButton {{
                    background: transparent;
                    border-left: 2px solid transparent;
                    border-radius: 0px;
                }}
                NavButton:hover {{
                    background: rgba(255, 255, 255, 0.03);
                }}
            """)
            self._icon.setStyleSheet(f"font-size: 18px; color: {DARK['TEXT_DIM']}; border: none; background: transparent;")
            self._label.setStyleSheet(f"font-size: 9px; font-weight: 700; color: {DARK['TEXT_FAINT']}; letter-spacing: 0.5px; border: none; background: transparent;")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._key)
        super().mousePressEvent(event)
