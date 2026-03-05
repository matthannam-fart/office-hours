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
        self.setFixedHeight(46)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)

        # Card-style background
        self.setStyleSheet(f"""
            UserRow {{
                background: {DARK['BG_RAISED']};
                border-radius: 8px;
            }}
            UserRow:hover {{
                background: {DARK['BG_HOVER']};
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 8, 6)
        layout.setSpacing(8)

        # Orb
        self.orb = SmallOrb(mode)
        layout.addWidget(self.orb, 0, Qt.AlignVCenter)

        # Name
        self.name_label = QLabel(name)
        self.name_label.setStyleSheet(f"font-size: 14px; font-weight: 500; color: {DARK['TEXT']}; padding-bottom: 1px;")
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

        # Call button (hidden until hover) — label depends on mode
        btn_labels = {
            'GREEN': 'Intercom', 'YELLOW': 'Page', 'RED': 'Message',
            'BUSY': 'Join', 'OPEN': 'Intercom',
        }
        btn_text = btn_labels.get(mode, 'Call')
        self.call_btn = QPushButton(btn_text)
        self.call_btn.setFixedWidth(52)
        self.call_btn.setCursor(Qt.PointingHandCursor)
        if mode == 'YELLOW':
            self.call_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(230, 175, 0, 0.15); color: {DARK['WARN']};
                    border: 1px solid rgba(230, 175, 0, 0.30);
                    border-radius: 6px; font-size: 11px; font-weight: 700; padding: 4px 0;
                }}
                QPushButton:hover {{ background: rgba(230, 175, 0, 0.25); }}
            """)
        elif mode == 'RED':
            # Message button — muted styling
            self.call_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,255,255,0.06); color: {DARK['TEXT_DIM']};
                    border: 1px solid {DARK['BORDER']};
                    border-radius: 6px; font-size: 11px; font-weight: 700; padding: 4px 0;
                }}
                QPushButton:hover {{ background: rgba(255,255,255,0.10); }}
            """)
        else:
            self.call_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(0, 166, 81, 0.12); color: {DARK['ACCENT']};
                    border: 1px solid rgba(0, 166, 81, 0.25);
                    border-radius: 6px; font-size: 11px; font-weight: 700; padding: 4px 0;
                }}
                QPushButton:hover {{ background: rgba(0, 166, 81, 0.22); }}
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
        track_color = QColor('#2ABFBF') if self._on else QColor(DARK['BORDER'])
        p.setBrush(QBrush(track_color))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, 0, 36, 20), 10, 10)

        # Knob
        knob_x = 18 if self._on else 2
        p.setBrush(QBrush(QColor('white')))
        p.drawEllipse(QRectF(knob_x, 2, 16, 16))
        p.end()
