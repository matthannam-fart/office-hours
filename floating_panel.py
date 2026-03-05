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

# Shared constants (extracted to ui_constants.py)
from ui_constants import COLORS, MODE_LABELS, RADIO_STATIONS, PANEL_W, PANEL_RADIUS

# Widget classes (extracted to widgets.py)
from widgets import GlowingOrb, LevelMeter, SmallOrb, UserRow, ToggleSwitch

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
        self._team_bar.setVisible(True)  # Always visible so user can create first team
        root.addWidget(self._team_bar)

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

        # Connection label
        self.conn_label = QLabel("Connected")
        self.conn_label.setStyleSheet("font-size: 12px; font-weight: 600; color: #5a5a5a; letter-spacing: 0.5px; border: none;")
        h.addWidget(self.conn_label, 1)

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

        # Status label
        status_lbl = QLabel("Connecting...")
        status_lbl.setStyleSheet("font-size: 12px; color: #aaa; font-weight: 500;")
        h.addWidget(status_lbl, 1)

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

    # ── Team Selector Bar ────────────────────────────────────────
    def _build_team_bar(self):
        bar = QFrame()
        bar.setStyleSheet("border: none;")
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 6, 14, 2)
        h.setSpacing(6)

        lbl = QLabel("TEAM")
        lbl.setStyleSheet("font-size: 10px; font-weight: 700; color: #aaa; letter-spacing: 1px;")
        h.addWidget(lbl)

        self._team_combo = QComboBox()
        self._team_combo.setStyleSheet("""
            QComboBox {
                background: #f0eeeb; border: 1px solid #ddd; border-radius: 6px;
                padding: 3px 8px; font-size: 12px; color: #333; min-width: 120px;
            }
            QComboBox::drop-down {
                border: none; width: 20px;
            }
            QComboBox QAbstractItemView {
                background: white; border: 1px solid #ddd; border-radius: 4px;
                selection-background-color: #e8e6e3;
            }
        """)
        self._team_combo.currentIndexChanged.connect(self._on_team_combo_changed)
        h.addWidget(self._team_combo, 1)

        # Manage button (gear icon) — only shown for admins
        self._team_manage_btn = QPushButton("⚙")
        self._team_manage_btn.setFixedSize(24, 24)
        self._team_manage_btn.setStyleSheet("""
            QPushButton {
                background: transparent; border: none; font-size: 14px; color: #888;
            }
            QPushButton:hover { color: #333; }
        """)
        self._team_manage_btn.setToolTip("Manage Team")
        self._team_manage_btn.clicked.connect(self.manage_team_requested.emit)
        self._team_manage_btn.setVisible(False)
        h.addWidget(self._team_manage_btn)

        # + button to create a new team
        add_btn = QPushButton("+")
        add_btn.setFixedSize(24, 24)
        add_btn.setStyleSheet("""
            QPushButton {
                background: transparent; border: 1px solid #ccc; border-radius: 12px;
                font-size: 16px; font-weight: bold; color: #888;
            }
            QPushButton:hover { background: #e8e6e3; color: #333; }
        """)
        add_btn.setToolTip("Create Team")
        add_btn.clicked.connect(self._on_create_team_click)
        h.addWidget(add_btn)

        return bar

    def _on_team_combo_changed(self, index):
        if index < 0:
            return
        team_id = self._team_combo.itemData(index)
        if team_id:
            self.team_changed.emit(team_id)
            # Show manage button only if admin
            role = self._team_combo.itemData(index, Qt.UserRole + 1)
            self._team_manage_btn.setVisible(role == "admin")

    def _on_create_team_click(self):
        """Prompt for a team name and emit create signal."""
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Create Team", "Team name:")
        if ok and name.strip():
            self.create_team_requested.emit(name.strip())

    def set_teams(self, teams, active_team_id=""):
        """Update the team dropdown with available teams.
        teams: [{id, name, role}, ...]
        """
        self._team_combo.blockSignals(True)
        self._team_combo.clear()
        active_index = 0
        for i, team in enumerate(teams):
            self._team_combo.addItem(team["name"], team["id"])
            # Store the role as UserRole + 1
            self._team_combo.setItemData(i, team.get("role", "member"), Qt.UserRole + 1)
            if team["id"] == active_team_id:
                active_index = i
        self._team_combo.setCurrentIndex(active_index)
        self._team_combo.blockSignals(False)
        self._team_bar.setVisible(True)  # Always show — "+" button needed even with 0 teams
        # Show manage button if admin of current team
        if teams and active_index < len(teams):
            self._team_manage_btn.setVisible(teams[active_index].get("role") == "admin")
        self.adjustSize()

    def show_manage_team_dialog(self, team_name, team_id, members, add_callback=None, remove_callback=None):
        """Show a dialog to manage team members."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QListWidget, QListWidgetItem
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Manage: {team_name}")
        dlg.setFixedWidth(300)
        layout = QVBoxLayout(dlg)

        # Members list
        layout.addWidget(QLabel("Members:"))
        member_list = QListWidget()
        for m in members:
            item = QListWidgetItem(f"{m['display_name']} ({m['role']})")
            item.setData(Qt.UserRole, m["user_id"])
            item.setData(Qt.UserRole + 1, m["role"])
            member_list.addItem(item)
        layout.addWidget(member_list)

        # Remove button
        remove_btn = QPushButton("Remove Selected")
        remove_btn.setStyleSheet("background: #e53935; color: white; border: none; border-radius: 4px; padding: 6px;")
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

        # Add member section
        layout.addWidget(QLabel("Add member by name:"))
        add_row = QHBoxLayout()
        name_input = QLineEdit()
        name_input.setPlaceholderText("Display name...")
        add_row.addWidget(name_input, 1)
        add_btn = QPushButton("Add")
        add_btn.setStyleSheet("background: #4caf50; color: white; border: none; border-radius: 4px; padding: 6px 12px;")
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
        self.call_name_label.setStyleSheet("font-size: 14px; font-weight: 600; color: #2e7d32;")
        top.addWidget(self.call_name_label, 1)

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
        top.addWidget(end_btn)
        outer.addLayout(top)

        # Bottom row: audio level meters
        meters = QHBoxLayout()
        meters.setSpacing(6)

        mic_icon = QLabel("\U0001F3A4")  # 🎤
        mic_icon.setFixedWidth(14)
        mic_icon.setStyleSheet("font-size: 10px;")
        meters.addWidget(mic_icon)
        self.mic_meter = LevelMeter(color="#4caf50", width=80, height=5)
        meters.addWidget(self.mic_meter)

        meters.addSpacing(8)

        spk_icon = QLabel("\U0001F50A")  # 🔊
        spk_icon.setFixedWidth(14)
        spk_icon.setStyleSheet("font-size: 10px;")
        meters.addWidget(spk_icon)
        self.speaker_meter = LevelMeter(color="#2196f3", width=80, height=5)
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

    def set_connection(self, connected, peer_name=""):
        """Switch between connected and disconnected states."""
        self._connected = connected
        self._conn_bar.setVisible(connected)
        self._disconn_bar.setVisible(not connected)
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
        if not self._pinned:
            # Fixed chrome: header(48) + disconn/conn(46) + section hdr(28) + ptt(60) + margins(20)
            chrome_height = 48 + 46 + 28 + 60 + 20
            user_height = len(users) * 46
            target = chrome_height + max(user_height, 46)  # min space for 1 row
            target = min(target, 500)  # cap so it doesn't go off-screen
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
        self.adjustSize()

    def hide_outgoing(self):
        """Hide the outgoing call banner."""
        self._outgoing_banner.setVisible(False)
        self._user_section.setVisible(True)
        self.adjustSize()

    def show_incoming(self, caller_name):
        """Show the incoming call banner."""
        self._hide_all_banners()
        self.incoming_name.setText(caller_name)
        self._incoming_banner.setVisible(True)
        self._user_section.setVisible(False)
        self.adjustSize()

    def hide_incoming(self):
        """Hide the incoming call banner."""
        self._incoming_banner.setVisible(False)
        if not self._outgoing_banner.isVisible() and not self._call_banner.isVisible():
            self._user_section.setVisible(True)
        self.adjustSize()

    def show_call(self, caller_name):
        """Show the in-call banner."""
        self._hide_all_banners()
        self.call_name_label.setText(caller_name)
        self._call_banner.setVisible(True)
        self._user_section.setVisible(False)
        self.adjustSize()

    def hide_call(self):
        """Hide the in-call banner and restore normal layout."""
        self._hide_all_banners()
        self._user_section.setVisible(True)
        self.adjustSize()

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
        hdr.setStyleSheet("border-bottom: 1px solid #eae8e4;")
        hdr.setFixedHeight(40)
        hdr_layout = QHBoxLayout(hdr)
        hdr_layout.setContentsMargins(10, 0, 10, 0)
        hdr_layout.setSpacing(8)

        back_btn = QPushButton("<")
        back_btn.setFixedSize(28, 28)
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setStyleSheet("""
            QPushButton {
                font-size: 16px; font-weight: 700; color: #2ABFBF;
                background: transparent; border: none; border-radius: 6px;
            }
            QPushButton:hover { background: rgba(42,191,191,0.1); }
        """)
        back_btn.clicked.connect(self._close_settings)
        hdr_layout.addWidget(back_btn)

        title = QLabel("Settings")
        title.setStyleSheet("font-size: 13px; font-weight: 700; color: #555; border: none;")
        hdr_layout.addWidget(title, 1)
        v.addWidget(hdr)

        # ── Scrollable settings items ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { width: 4px; background: transparent; }
            QScrollBar::handle:vertical { background: #ddd; border-radius: 2px; min-height: 20px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        container = QWidget()
        self._settings_layout = QVBoxLayout(container)
        self._settings_layout.setContentsMargins(8, 6, 8, 6)
        self._settings_layout.setSpacing(2)

        scroll.setWidget(container)
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

        row_style = """
            QPushButton {
                text-align: left; padding: 8px 12px; font-size: 13px;
                font-weight: 500; color: #444; background: transparent;
                border: none; border-radius: 8px;
            }
            QPushButton:hover { background: rgba(0,0,0,0.04); }
        """

        # ── Change Name ──
        name_btn = QPushButton(f"✏️   Change Name")
        name_btn.setCursor(Qt.PointingHandCursor)
        name_btn.setStyleSheet(row_style)
        name_btn.clicked.connect(lambda: (self._close_settings(), self._change_name_dialog()))
        layout.addWidget(name_btn)

        # ── Incognito ──
        incognito_text = "👻   Visible" if self._incognito else "👻   Incognito"
        incognito_btn = QPushButton(incognito_text)
        incognito_btn.setCursor(Qt.PointingHandCursor)
        incognito_btn.setStyleSheet(row_style)
        incognito_btn.clicked.connect(lambda: (self._toggle_incognito(), self._populate_settings()))
        layout.addWidget(incognito_btn)

        # ── Dark Mode ──
        dark_text = "☀️   Light Mode" if self._dark_mode else "🌙   Dark Mode"
        dark_btn = QPushButton(dark_text)
        dark_btn.setCursor(Qt.PointingHandCursor)
        dark_btn.setStyleSheet(row_style)
        dark_btn.clicked.connect(lambda: (self._toggle_dark_mode(), self._populate_settings()))
        layout.addWidget(dark_btn)

        # ── Divider ──
        div1 = QFrame()
        div1.setFixedHeight(1)
        div1.setStyleSheet("background: rgba(0,0,0,0.06); margin: 4px 12px;")
        layout.addWidget(div1)

        # ── Radio ──
        if self._radio_station:
            radio_btn = QPushButton("📻   Stop Radio")
            radio_btn.setCursor(Qt.PointingHandCursor)
            radio_btn.setStyleSheet(row_style)
            radio_btn.clicked.connect(lambda: (self._stop_radio(), self._populate_settings()))
            layout.addWidget(radio_btn)

            # Volume slider row
            vol_row = QWidget()
            vol_h = QHBoxLayout(vol_row)
            vol_h.setContentsMargins(12, 4, 12, 4)
            vol_h.setSpacing(8)
            vol_icon = QLabel("🔊")
            vol_icon.setStyleSheet("font-size: 12px;")
            vol_h.addWidget(vol_icon)
            vol_slider = QSlider(Qt.Horizontal)
            vol_slider.setRange(0, 100)
            vol_slider.setValue(int(self._audio_output.volume() * 100))
            vol_slider.setStyleSheet("""
                QSlider::groove:horizontal { height: 3px; background: rgba(0,0,0,0.1); border-radius: 1px; }
                QSlider::handle:horizontal { width: 10px; height: 10px; margin: -4px 0; background: #2ABFBF; border-radius: 5px; }
                QSlider::sub-page:horizontal { background: #2ABFBF; border-radius: 1px; }
            """)
            vol_slider.valueChanged.connect(lambda v: self._audio_output.setVolume(v / 100.0))
            vol_h.addWidget(vol_slider, 1)
            layout.addWidget(vol_row)
        else:
            radio_btn = QPushButton("📻   Radio")
            radio_btn.setCursor(Qt.PointingHandCursor)
            radio_btn.setStyleSheet(row_style)
            radio_btn.clicked.connect(lambda: (self._play_radio('NTS Radio'), self._populate_settings()))
            layout.addWidget(radio_btn)

        # ── Divider ──
        div2 = QFrame()
        div2.setFixedHeight(1)
        div2.setStyleSheet("background: rgba(0,0,0,0.06); margin: 4px 12px;")
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
            in_icon = QLabel("🎤")
            in_icon.setStyleSheet("font-size: 13px;")
            in_h.addWidget(in_icon)
            in_combo = QComboBox()
            in_combo.setStyleSheet("""
                QComboBox {
                    font-size: 11px; padding: 3px 6px;
                    border: 1px solid rgba(0,0,0,0.1); border-radius: 6px;
                    background: white;
                }
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
            out_icon = QLabel("🔈")
            out_icon.setStyleSheet("font-size: 13px;")
            out_h.addWidget(out_icon)
            out_combo = QComboBox()
            out_combo.setStyleSheet("""
                QComboBox {
                    font-size: 11px; padding: 3px 6px;
                    border: 1px solid rgba(0,0,0,0.1); border-radius: 6px;
                    background: white; min-width: 100px;
                }
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
        div3.setStyleSheet("background: rgba(0,0,0,0.06); margin: 4px 12px;")
        layout.addWidget(div3)

        # ── Quit ──
        quit_btn = QPushButton("🙊   Quit OH")
        quit_btn.setCursor(Qt.PointingHandCursor)
        quit_btn.setStyleSheet("""
            QPushButton {
                text-align: left; padding: 8px 12px; font-size: 13px;
                font-weight: 500; color: #c0392b; background: transparent;
                border: none; border-radius: 8px;
            }
            QPushButton:hover { background: rgba(192,57,43,0.06); }
        """)
        quit_btn.clicked.connect(self.quit_requested.emit)
        layout.addWidget(quit_btn)

        # Stretch at bottom
        layout.addStretch()

    def _show_hamburger_menu(self):
        """Toggle the inline settings view."""
        if self._settings_view.isVisible():
            self._close_settings()
        else:
            self._open_settings()

    def _open_settings(self):
        """Show the inline settings, hiding main content."""
        self._populate_settings()
        self._user_section.setVisible(False)
        self._ptt_bar.setVisible(False)
        self._settings_view.setVisible(True)
        self.adjustSize()

    def _close_settings(self):
        """Hide settings, restore main content."""
        self._settings_view.setVisible(False)
        self._user_section.setVisible(True)
        self._ptt_bar.setVisible(True)
        self.adjustSize()

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
            self._conn_bar.setVisible(self._connected)
            self._disconn_bar.setVisible(not self._connected)
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

    sys.exit(app.exec())
