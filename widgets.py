"""
widgets.py — Reusable UI widgets for Office Hours
Extracted from floating_panel.py for maintainability.
"""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Signal, QRectF, Property
from PySide6.QtGui import QColor, QPainter, QBrush, QRadialGradient, QPen

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

    def set_color(self, mode):
        self._color = QColor(COLORS.get(mode, COLORS['GREEN']))
        self.update()

    # Alias so both names work (panel uses set_mode, some code uses set_color)
    def set_mode(self, mode):
        self.set_color(mode)

    def start_breathing(self):
        self._breathing = True
        self._anim.start()

    def stop_breathing(self):
        self._anim.stop()
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
    """Press-and-hold intercom button for each online user."""
    call_clicked = Signal(str)            # user_id (legacy, still used for single-click fallback)
    intercom_pressed = Signal(str)        # user_id — finger/mouse down
    intercom_released = Signal(str)       # user_id — finger/mouse up

    # Visual states
    STATE_IDLE = "idle"
    STATE_CONNECTING = "connecting"
    STATE_LIVE = "live"

    def __init__(self, user_id, name, mode='GREEN', has_message=False, parent=None):
        super().__init__(parent)
        self.user_id = user_id
        self._mode = mode
        self._pressed = False
        self._state = self.STATE_IDLE
        self.setFixedHeight(50)
        self.setCursor(Qt.PointingHandCursor)
        self._apply_style()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 12, 6)
        layout.setSpacing(10)

        # Orb
        self.orb = SmallOrb(mode)
        layout.addWidget(self.orb, 0, Qt.AlignVCenter)

        # Name
        self.name_label = QLabel(name)
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
        elif self._state == self.STATE_CONNECTING:
            self.setStyleSheet(f"""
                UserRow {{
                    background: rgba(255, 255, 255, 0.06);
                    border: 1px solid {DARK['BORDER']};
                    border-radius: 10px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                UserRow {{
                    background: {DARK['BG_RAISED']};
                    border-radius: 10px;
                }}
                UserRow:hover {{
                    background: {DARK['BG_HOVER']};
                }}
            """)

    def set_state(self, state):
        """Update visual state: idle, connecting, or live."""
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
        else:
            self._status_lbl.setVisible(False)
            self._eq.setVisible(False)

    def set_eq_level(self, level):
        """Update the inline EQ meter."""
        self._eq.set_level(level)

    def set_message(self, has_msg):
        self.msg_dot.setVisible(has_msg)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self.intercom_pressed.emit(self.user_id)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            self.intercom_released.emit(self.user_id)
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
