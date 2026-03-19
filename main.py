"""
main.py — Vox Menu Bar App
System tray app with floating panel UI.
"""
import os
import sys
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import auth_manager
import supabase_client
from audio_manager import AudioManager
from config import (
    DTYPE,
    MAX_FILE_SIZE,
    RELAY_HOST,
    RELAY_PORT,
    SAMPLE_RATE,
    log,
)
from deck_ws_server import DeckWSServer
from discovery_manager import DiscoveryManager
from floating_panel import FloatingPanel, create_oh_icon
from hotkey_manager import HotkeyManager
from network_manager import NetworkManager
from ui_constants import COLORS
from user_settings import (
    _config_dir,
    clear_auth_session,
    get_active_team,
    get_active_team_name,
    get_auth_session,
    get_deck_guide_dismissed,
    get_display_name,
    get_ptt_hotkey,
    get_user_id,
    is_logged_in,
    save_auth_session,
    set_active_team,
    set_active_team_name,
    set_deck_guide_dismissed,
    set_display_name,
)


class IntercomApp(QObject):
    # Signals to update UI from other threads
    log_signal = Signal(str)
    status_signal = Signal(str)
    peer_found_signal = Signal(str, str) # name, ip
    peer_lost_signal = Signal(str)       # name
    connection_request_signal = Signal(str, str)  # requester_name, ip
    connection_response_signal = Signal(bool)     # accepted or rejected
    presence_update_signal = Signal(list)          # list of online users
    presence_request_signal = Signal(str, str, str) # from_name, from_id, room_code (internal)
    call_connected_signal = Signal(str)            # peer_name — call established
    message_received_signal = Signal()              # new voicemail received
    hotkey_press_signal = Signal()                   # global PTT pressed
    hotkey_release_signal = Signal()                 # global PTT released
    mic_level_signal = Signal(float)                 # mic audio level 0.0–1.0
    speaker_level_signal = Signal(float)             # speaker audio level 0.0–1.0
    _teams_loaded_signal = Signal()                   # Supabase teams loaded (internal)
    _join_request_signal = Signal(str, str, str, str)  # request_id, team_id, requester_name, requester_id
    _join_response_signal = Signal(str, bool)          # request_id, approved
    _join_request_failed_signal = Signal(str)           # reason
    _show_manage_dialog_signal = Signal(list)             # members list — thread-safe bounce
    _available_teams_signal = Signal(list)                  # lobby teams — thread-safe bounce
    _switch_to_team_signal = Signal()                         # transition from lobby to team view (thread-safe)
    _show_invite_prompt_signal = Signal(str, str)               # team_name, invite_code — prompt to invite after create
    _join_pending_signal = Signal(str)                          # show "waiting for admin" (thread-safe)
    _ws_command_signal = Signal(str, dict)                        # action, payload from WS plugin

    MODE_GREEN = "GREEN"
    MODE_YELLOW = "YELLOW"
    MODE_RED = "RED"
    MODE_LABELS = {"GREEN": "Available", "YELLOW": "Busy", "RED": "DND"}

    def __init__(self):
        super().__init__()

        # State
        self.mode = self.MODE_GREEN
        self.remote_mode = self.MODE_GREEN
        self._hotline_on = False
        self.peer_ip = "127.0.0.1"
        self.has_message = False
        self.is_flashing = False
        self.flash_state = False
        self.incoming_message_path = None
        self._message_queue = []       # List of message file paths (inbox)
        self._user_messages = {}       # Per-user message queues: {user_id: [path, ...]}
        self._playing_message = False
        self.pending_connection = False
        self.peer_talking = False
        self.online_users = {}
        self.pending_room = None       # Internal relay session ID
        self.pending_from_id = None
        self.active_room_code = None   # Internal relay session ID for active call
        self._pre_call_mode = None     # Mode before entering a call (restored on disconnect)
        self._connected_peer_id = None # User ID of peer we're in a call with

        # Intercom state (click to select + PTT)
        self._intercom_target_id = None   # user_id selected as PTT target
        self._intercom_connected = False  # True once connection is ready for audio
        self._intercom_streaming = False  # True while audio is actively streaming
        self._intercom_ptt_held = False   # True while PTT is physically held down
        self._intercom_keep_alive = None  # QTimer for 60s keep-alive after PTT release
        self._last_panel_users = []      # Cached for WS plugin broadcast
        self._auto_select_attempted = ""  # Guard against auto-select retry loops

        # WS plugin browse state (mirrors plugin cycling)
        self._ws_team_index = 0
        self._ws_user_index = 0
        self._ws_preview_name = ""       # Name shown during browse
        self._ws_auto_select_timer = None

        # User identity — prefer auth session UID over local UUID
        self._auth_logged_in = is_logged_in()
        if self._auth_logged_in:
            session = get_auth_session()
            self.user_id = session.get("user_id", "") or get_user_id()
            self.display_name = get_display_name()
        else:
            self.display_name = get_display_name()
            self.user_id = get_user_id()

        # Team state
        self.active_team_id = get_active_team() or ""
        self.active_team_name = get_active_team_name() or ""
        self.my_teams = []  # [{id, name, role}, ...] loaded from Supabase
        self._team_members = {}  # {user_id: display_name} — all members of active team
        self._pending_join_requests = {}  # request_id -> {team_id, requester_id, requester_name}

        # Managers
        self.network = NetworkManager(self.handle_network_message, log_callback=self.log_signal.emit)
        self.audio = AudioManager(self.network, log_callback=self.log_signal.emit)
        self.network.audio_callback = self.handle_audio_stream
        self.network.presence_callback = self.handle_presence_message
        self.network.display_name = self.display_name
        self.network.user_id = self.user_id
        self.audio.mic_level_callback = lambda l: self.mic_level_signal.emit(l)
        self.audio.speaker_level_callback = lambda l: self.speaker_level_signal.emit(l)
        self.audio.start_listening()

        # Stream Deck Plugin WebSocket bridge
        self.deck_ws = DeckWSServer(
            command_callback=lambda action, msg: self._ws_command_signal.emit(action, msg),
            log_callback=lambda m: self.log_signal.emit(m),
        )
        self.deck_ws.start()

        # Global PTT Hotkey
        ptt_key = get_ptt_hotkey()
        self.hotkey = HotkeyManager(
            on_press=self._hotkey_talk_press,
            on_release=self._hotkey_talk_release,
            key_name=ptt_key,
            log_callback=self.log_signal.emit,
        )
        # Global hotkey disabled — use the UI PTT button instead
        # self.hotkey.start()

        # Discovery
        self.discovery = DiscoveryManager(self.on_peer_found, self.on_peer_lost)
        self.peer_map = {}

        # ── System Tray ────────────────────────────────────────
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(create_oh_icon(COLORS['GREEN']))
        self.tray.setToolTip("Vox")
        self.tray.activated.connect(self._on_tray_click)
        self._tray_menu = QMenu()
        self._rebuild_tray_menu()
        # On macOS, setContextMenu hijacks ALL clicks, so we don't set it.
        # Instead we show the menu manually via _on_tray_click.
        # On Windows, setContextMenu is fine for right-click only.
        if sys.platform == 'win32':
            self.tray.setContextMenu(self._tray_menu)
        self.tray.setVisible(True)

        # ── Floating Panel ─────────────────────────────────────
        self.panel = FloatingPanel()
        self._connect_panel_signals()

        # Set initial state
        self.panel.set_mode(self.mode)
        self.panel.set_connection(False)

        # Pre-fill onboarding name if we have one saved
        if self.display_name:
            self.panel.set_onboarding_name(self.display_name)

        self.panel.set_display_name(self.display_name or "Vox")

        # Stream Deck status — check actual client connections
        deck_connected = hasattr(self, 'deck_ws') and self.deck_ws.client_count > 0
        self.panel.set_deck_status(deck_connected)

        # Start Services
        self.discovery.register_service()
        self.discovery.start_browsing()

        # Timers
        self.flash_timer = QTimer()
        self.flash_timer.timeout.connect(self.flash_loop)
        self.flash_timer.start(500)

        # Signal connections
        self.log_signal.connect(self.log)
        self.peer_found_signal.connect(self.add_peer_to_ui)
        self.peer_lost_signal.connect(self.remove_peer_from_ui)
        self.call_connected_signal.connect(self._on_call_connected)
        self.message_received_signal.connect(self.panel.show_message)
        self.connection_request_signal.connect(self._show_connection_request)
        self.connection_response_signal.connect(self._handle_connection_response)
        self.presence_update_signal.connect(self._update_online_users)
        self.presence_request_signal.connect(self._show_presence_request)
        self.hotkey_press_signal.connect(self.on_talk_press)
        self.hotkey_release_signal.connect(self.on_talk_release)
        self.mic_level_signal.connect(self._on_mic_level)
        self.speaker_level_signal.connect(self._on_speaker_level)
        self._ws_command_signal.connect(self._handle_ws_command)
        self._teams_loaded_signal.connect(self._on_teams_loaded)
        self._join_request_signal.connect(self._show_join_request)
        self._join_response_signal.connect(self._handle_join_response)
        self._join_request_failed_signal.connect(self._handle_join_request_failed)
        self._show_manage_dialog_signal.connect(self._show_manage_team_dialog)
        self._available_teams_signal.connect(self._set_available_teams)

        # Lobby refresh timer — polls available teams every 10s while lobby is showing
        self._lobby_refresh_timer = QTimer(self)
        self._lobby_refresh_timer.setInterval(10_000)  # 10 seconds
        self._lobby_refresh_timer.timeout.connect(self._refresh_lobby_teams)
        self._switch_to_team_signal.connect(self._switch_to_team_view)
        self._show_invite_prompt_signal.connect(self._show_invite_prompt)
        self._join_pending_signal.connect(lambda name: self.panel.show_join_pending(name))

        self.update_deck_display()
        self.log("System Ready. Scanning for peers...")

        # Auth: connect login signals
        self.panel.login_completed.connect(self._on_login_completed)
        self.panel.login_skipped.connect(self._on_login_skipped)
        self.panel.sign_out_requested.connect(self._on_sign_out)

        # Auth token refresh timer (every 50 minutes)
        self._auth_refresh_timer = QTimer(self)
        self._auth_refresh_timer.setInterval(50 * 60 * 1000)  # 50 minutes
        self._auth_refresh_timer.timeout.connect(self._refresh_auth_token)

        # Startup: check auth state and route accordingly
        if RELAY_HOST:
            if self._auth_logged_in:
                # Logged in — try to refresh token, then auto-connect
                self._auth_refresh_timer.start()
                if self.display_name:
                    threading.Thread(target=self._auto_connect_presence, daemon=True).start()
                else:
                    self._teams_loaded_signal.emit()
            elif self.display_name:
                # Has a display name but not logged in — show login page
                self.panel._switch_page("login")
            else:
                # No name, no auth — show login page
                self.panel._switch_page("login")

        # On Windows, auto-show panel at startup since the tray icon
        # often gets hidden in the overflow area and users can't find the app
        if sys.platform == 'win32':
            QTimer.singleShot(500, self._auto_show_panel_windows)

        # Auto-show Stream Deck setup guide if Elgato app is installed
        if not get_deck_guide_dismissed():
            QTimer.singleShot(3000, self._maybe_show_deck_guide)

    def _maybe_show_deck_guide(self):
        """Show the Stream Deck setup guide if the Elgato app is installed."""
        sd_dir = ""
        if sys.platform == 'darwin':
            sd_dir = os.path.expanduser(
                "~/Library/Application Support/com.elgato.StreamDeck/Plugins")
        elif sys.platform == 'win32':
            sd_dir = os.path.join(
                os.environ.get('APPDATA', ''), 'Elgato', 'StreamDeck', 'Plugins')
        if sd_dir and os.path.isdir(sd_dir):
            self.panel._show_deck_setup_guide()

    # ── Panel Signal Wiring ───────────────────────────────────────
    def _connect_panel_signals(self):
        self.panel.mode_cycle_requested.connect(self.cycle_mode)
        self.panel.mode_set_requested.connect(self._on_mode_set)
        self.panel.hotline_toggled.connect(self._on_hotline_toggle)
        self.panel.page_all_pressed.connect(self.on_page_all_press)
        self.panel.page_all_released.connect(self.on_page_all_release)
        self.panel.ptt_pressed.connect(self.on_talk_press)
        self.panel.ptt_released.connect(self.on_talk_release)
        self.panel.call_user_requested.connect(self._on_call_user)
        self.panel.intercom_pressed.connect(self._on_intercom_press)
        self.panel.intercom_released.connect(self._on_intercom_release)
        self.panel.user_selected.connect(self._on_user_selected)
        self.panel.leave_requested.connect(self.do_disconnect)
        self.panel.accept_call_requested.connect(self._on_accept_call)
        self.panel.decline_call_requested.connect(self._on_decline_call)
        self.panel.end_call_requested.connect(self.do_disconnect)
        self.panel.cancel_call_requested.connect(self._on_cancel_call)
        self.panel.play_message_requested.connect(self._on_play_message)
        self.panel.audio_input_changed.connect(self.audio.set_input_device)
        self.panel.audio_output_changed.connect(self.audio.set_output_device)
        self.panel.quit_requested.connect(self._quit)
        self.panel.incognito_toggled.connect(self._on_incognito_toggle)
        self.panel.dark_mode_toggled.connect(self._on_dark_mode_toggle)
        self.panel.name_change_requested.connect(self._on_name_changed)
        self.panel.team_changed.connect(self._on_team_changed)
        self.panel.create_team_requested.connect(self._on_create_team)
        self.panel.manage_team_requested.connect(self._on_manage_team)
        self.panel.join_code_requested.connect(self._on_join_code)
        self.panel.leave_team_requested.connect(self._on_leave_team)
        self.panel.request_to_join.connect(self._on_request_to_join)
        self.panel.join_request_accepted.connect(self._on_approve_join)
        self.panel.join_request_declined.connect(self._on_decline_join)
        self.panel.team_selected_from_lobby.connect(self._on_team_selected_from_lobby)
        self.panel.deck_guide_dismissed.connect(lambda: set_deck_guide_dismissed(True))

    # ── Windows Auto-Show ───────────────────────────────────────
    def _auto_show_panel_windows(self):
        """Show the panel on Windows at startup since the system tray
        icon is often hidden in the overflow area."""
        if self.panel.isVisible():
            return
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.right() - self.panel.width() - 8
        y = screen.bottom() - self.panel.height() - 8
        self.panel.move(x, y)
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()
        # Auto-pin so the panel doesn't vanish on focus loss (macOS only).
        # On Windows the panel stays visible without pinning, and auto-pin
        # collapses it into the 58px compact bar which is confusing at startup.
        if sys.platform != 'win32' and not self.panel.is_pinned():
            self.panel._toggle_pin()

    # ── Tray Icon ─────────────────────────────────────────────────
    def _on_tray_click(self, reason):
        if reason == QSystemTrayIcon.Context:
            # Right-click — show context menu (Windows path; macOS uses below)
            self._show_tray_menu()
            return

        if reason == QSystemTrayIcon.Trigger:
            # On macOS without setContextMenu, check if right mouse button is held
            if sys.platform == 'darwin':
                from PySide6.QtGui import QCursor
                buttons = QApplication.mouseButtons()
                if buttons & Qt.RightButton:
                    self._show_tray_menu()
                    return

            if self.panel.isVisible() and self.panel.is_pinned():
                # Clicking tray while pinned = unpin and show full panel
                self.panel._toggle_pin()
            elif self.panel.isVisible():
                self.panel.hide()
            else:
                geo = self.tray.geometry()
                if geo.isValid() and geo.width() > 0:
                    # macOS: tray geometry works, anchor below icon
                    self.panel.show_at(QPoint(geo.center().x(), geo.bottom()))
                else:
                    # Tray geometry unavailable — use cursor position as anchor
                    from PySide6.QtGui import QCursor
                    cursor = QCursor.pos()
                    screen = QApplication.primaryScreen().availableGeometry()
                    if sys.platform == 'win32':
                        # Windows: tray is at bottom, position panel above cursor
                        x = cursor.x() - self.panel.width() // 2
                        y = screen.bottom() - self.panel.height() - 8
                    else:
                        # Other: position below menu bar area
                        x = screen.right() - self.panel.width() - 8
                        y = screen.top() + 4
                    # Keep on screen
                    x = max(screen.left() + 4, min(x, screen.right() - self.panel.width() - 4))
                    self.panel._notch_x = cursor.x() - x
                    self.panel.move(x, y)
                    self.panel.show()
                    self.panel.raise_()
                    self.panel.activateWindow()

    def _show_tray_menu(self):
        """Show tray context menu at cursor position."""
        from PySide6.QtGui import QCursor
        self._rebuild_tray_menu()
        self._tray_menu.popup(QCursor.pos())

    def _update_tray_icon(self):
        color = COLORS.get(self.mode, COLORS['GREEN'])
        self.tray.setIcon(create_oh_icon(color))

    def _rebuild_tray_menu(self):
        """Rebuild the right-click tray menu with current online users."""
        self._tray_menu.clear()

        # Online users section
        users = getattr(self, '_last_panel_users', [])
        online = [u for u in users if u.get('mode') != 'OFFLINE']
        if online:
            for u in online:
                name = u.get('name', 'Unknown')
                mode = u.get('mode', 'GREEN')
                uid = u.get('id', '')
                dot = {'GREEN': '🟢', 'YELLOW': '🟡', 'RED': '🔴'}.get(mode, '⚪')
                action = self._tray_menu.addAction(f"{dot} {name}")
                action.triggered.connect(lambda checked=False, uid=uid: self._tray_select_user(uid))
        else:
            no_users = self._tray_menu.addAction("No users online")
            no_users.setEnabled(False)

        self._tray_menu.addSeparator()
        self._tray_menu.addAction("Show Panel", self._show_panel_at_tray)
        self._tray_menu.addSeparator()
        self._tray_menu.addAction("Quit Vox", self._quit)

    def _tray_select_user(self, user_id):
        """Select a user from the tray menu as PTT target."""
        self._on_user_selected(user_id)
        self._show_panel_at_tray()

    # ── Hotline Toggle ─────────────────────────────────────────────
    def _on_hotline_toggle(self, is_on):
        self._hotline_on = is_on
        if is_on:
            old_mode = self.mode
            self.mode = self.MODE_GREEN
            self.panel.set_hotline(True)
            self.panel.set_mode(self.MODE_GREEN)
            self.tray.setIcon(create_oh_icon(COLORS['GREEN']))
            self.audio.set_hotline(True)  # Always-on with soft noise suppression
            if self.network.connected:
                self.audio.start_streaming()
            self.send_status()
            self.network.update_presence_mode(self.mode)
        else:
            # Restore to GREEN (default when turning off hotline)
            self.mode = self.MODE_GREEN
            self.audio.set_hotline(False)
            self.audio.stop_streaming()
            self.panel.set_hotline(False)
            self.panel.set_mode(self.MODE_GREEN)
            self._update_tray_icon()
            self.send_status()
            self.network.update_presence_mode(self.mode)
        self.update_deck_display()
        self._update_ptt_for_mode()

    # ── Call User (mode-based routing) ───────────────────────────
    def _on_call_user(self, user_id):
        # Check LAN peers first (user_id is the Zeroconf service name)
        if user_id in self.peer_map:
            lan_ip = self.peer_map[user_id]
            target_name = user_id
            if '(' in user_id and ')' in user_id:
                target_name = user_id.split('(')[1].split(')')[0]
            elif '._talkback' in user_id:
                target_name = user_id.split('._talkback')[0]

            self.log(f"Calling {target_name}...")
            self._calling_user_id = user_id
            self.panel.show_outgoing(target_name)
            self.log(f"Trying direct connection to {lan_ip}...")
            threading.Thread(
                target=self._try_direct_connect, args=(lan_ip, target_name), daemon=True
            ).start()
            return

        # Relay/presence users — route based on target's mode
        target = self.online_users.get(user_id, {})
        target_name = target.get("name", "Unknown")
        target_mode = target.get("mode", "GREEN")
        target_room = target.get("room", "")

        # RED (DND): completely unavailable, no contact allowed
        if target_mode == self.MODE_RED:
            self.log(f"{target_name} is in Do Not Disturb")
            return

        # GREEN, YELLOW, OPEN: initiate connection
        # Green targets auto-accept on their end
        # Yellow (Busy) targets auto-accept silently; audio is buffered as voicemail
        self.log(f"Calling {target_name}...")
        self._calling_user_id = user_id
        self._calling_user_name = target_name
        self.panel.show_outgoing(target_name)

        lan_ip = self._find_lan_ip(target_name)
        if lan_ip:
            self.log("Trying direct connection...")
            threading.Thread(
                target=self._try_direct_connect, args=(lan_ip, target_name), daemon=True
            ).start()
        else:
            self.log("Connecting via relay...")
            self.network.connect_to_user(user_id)

    def _find_lan_ip(self, target_name):
        """Find a peer's LAN IP from Zeroconf-discovered peers by matching name."""
        # Exact match first
        if target_name in self.peer_map:
            return self.peer_map[target_name]
        # Case-insensitive exact match
        lower = target_name.lower()
        for name, ip in self.peer_map.items():
            if name.lower() == lower:
                return ip
        # Note: partial matching removed to avoid routing remote users
        # through the direct LAN path accidentally
        return None

    def _try_direct_connect(self, ip, target_name):
        """Try direct TCP connection to a LAN peer."""
        success = self.network.connect(ip)
        if success:
            self.log("Connected — sending call request...")
            self._send_codec_offer()
            # Send a connection request — callee will show accept/decline
            self.network.send_control("CONNECTION_REQUEST", {
                "name": self.network.display_name or "Unknown"
            })
            # Keep showing "Calling..." — wait for CONNECTION_ACCEPTED
        else:
            self.log("Direct connection failed, trying relay...")
            if hasattr(self, '_calling_user_id'):
                self.network.connect_to_user(self._calling_user_id)

    # ── Intercom: Click to Select + PTT ──────────────────────────
    def _on_user_selected(self, user_id):
        """User clicked a row to select as PTT target, or play messages if they have any."""
        if not user_id:
            # Deselected — clear target
            self._intercom_target_id = None
            self._intercom_ptt_held = False
            self.panel.set_connection(False)
            self.log("Target cleared")
            return

        # If this user has unread messages, play them
        if user_id in self._user_messages and self._user_messages[user_id]:
            self._play_user_messages(user_id)
            return

        target = self.online_users.get(user_id, {})
        target_name = target.get("name", "Unknown")
        target_mode = target.get("mode", "GREEN")

        # DND users can't be targeted
        if target_mode == self.MODE_RED:
            self.log(f"{target_name} is unavailable")
            self.panel.set_user_state(user_id, "idle")
            return

        self._intercom_target_id = user_id
        self.log(f"Target: {target_name}")
        self.panel.set_connection(True, target_name, target_mode)
        self._broadcast_deck_state()

        # Cancel any keep-alive timer from a previous session
        if self._intercom_keep_alive:
            self._intercom_keep_alive.stop()
            self._intercom_keep_alive = None

        # Pre-connect in background so PTT is instant
        if not (self.network.connected and self._connected_peer_id == user_id):
            if self.network.connected:
                self.do_disconnect()
            self.panel.set_user_state(user_id, "connecting")
            self._calling_user_id = user_id
            self._intercom_connected = False
            lan_ip = self._find_lan_ip(target_name)
            if lan_ip:
                threading.Thread(
                    target=self._try_direct_connect, args=(lan_ip, target_name), daemon=True
                ).start()
            else:
                self.network.connect_to_user(user_id)
        else:
            self._intercom_connected = True

    def _on_intercom_press(self, user_id):
        """PTT pressed — start streaming to selected target.
        Yellow (Busy) targets auto-accept and buffer audio as voicemail on their end."""
        if not self._intercom_target_id:
            return
        self._intercom_ptt_held = True
        target = self.online_users.get(self._intercom_target_id, {})
        target_name = target.get("name", "Unknown")
        target_mode = target.get("mode", "GREEN")

        # DND: block entirely
        if target_mode == self.MODE_RED:
            self.log(f"{target_name} is in Do Not Disturb")
            self._intercom_ptt_held = False
            return

        if self.network.connected and self._connected_peer_id == self._intercom_target_id:
            self._intercom_connected = True
            self._start_intercom_stream(self._intercom_target_id, target_name)
        else:
            # Not yet connected — connect and stream once ready
            # Busy targets auto-accept silently; their client buffers as voicemail
            self._on_user_selected(self._intercom_target_id)

    def _start_intercom_stream(self, user_id, target_name):
        """Begin streaming audio to the intercom target."""
        self._intercom_streaming = True
        self.panel.set_user_state(user_id, "live")
        self.network.send_control("TALK_START", {})
        self.audio.start_streaming()
        self.log(f"Talking to {target_name}")

    def _on_intercom_release(self, user_id):
        """PTT released — stop streaming, keep connection warm."""
        self._intercom_ptt_held = False
        if self._intercom_streaming:
            self._intercom_streaming = False
            self.audio.stop_streaming()
            self.network.send_control("TALK_STOP", {})
            self.log("Released")

        # Keep row in selected state (not idle)
        if self._intercom_target_id:
            self.panel.set_user_state(self._intercom_target_id, "selected")

        # Keep connection alive for 60 seconds so re-PTT is instant
        if self.network.connected:
            if not self._intercom_keep_alive:
                self._intercom_keep_alive = QTimer(self)
                self._intercom_keep_alive.setSingleShot(True)
                self._intercom_keep_alive.timeout.connect(self._intercom_keepalive_expired)
            self._intercom_keep_alive.start(60_000)

    def _on_mic_level(self, level):
        """Route mic level to call banner and/or active intercom row."""
        self.panel.set_mic_level(level)
        if self._intercom_target_id and self._intercom_streaming:
            self.panel.set_user_eq_level(self._intercom_target_id, level)

    def _on_speaker_level(self, level):
        """Route speaker level to call banner and connected peer's row EQ."""
        self.panel.set_speaker_level(level)
        if self._connected_peer_id:
            self.panel.set_user_eq_level(self._connected_peer_id, level)

    def _intercom_keepalive_expired(self):
        """30 seconds idle — disconnect."""
        if self.network.connected and not self._intercom_streaming:
            self.log("Idle — disconnecting")
            self.do_disconnect()

    # ── Accept / Decline Call ─────────────────────────────────────
    def _on_accept_call(self):
        self.panel.hide_incoming()
        self.log(f"Accept: room={self.pending_room!r} from_id={self.pending_from_id!r} connected={self.network.connected}")
        if self.pending_room:
            # Relay-based call: send ACCEPT to presence server
            self.log(f"Accepted relay call — connecting to room {self.pending_room}...")
            self.network.accept_presence_connection(self.pending_room, self.pending_from_id)
        elif self.network.connected:
            # Direct LAN call: send CONNECTION_ACCEPTED via TCP
            self.log("Accepted direct LAN call")
            self.network.send_control("CONNECTION_ACCEPTED", {
                "name": self.network.display_name or "Unknown"
            })
            caller_name = "Peer"
            if hasattr(self, '_incoming_caller_name'):
                caller_name = self._incoming_caller_name
            self.call_connected_signal.emit(caller_name)
            self._start_open_line_if_ready()
        elif self.pending_from_id:
            # Have a from_id but no room — try relay accept by ID
            self.log("Accepted call — requesting relay room from presence...")
            self.network.accept_presence_connection_by_id(self.pending_from_id)
        else:
            self.log("Accept failed — no active connection (no room, no from_id, not connected)")

    def _on_decline_call(self):
        self.panel.hide_incoming()
        if self.pending_from_id:
            self.network.reject_presence_connection(self.pending_from_id)
        self.pending_room = None
        self.pending_from_id = None

    def _on_cancel_call(self):
        """Cancel an outgoing call that hasn't been answered yet."""
        self.panel.hide_outgoing()
        target_id = getattr(self, '_calling_user_id', None)
        self.network.cancel_connection(target_id)
        # Also disconnect TCP in case we have a direct LAN connection open
        if self.network.connected and not self.network.relay_mode:
            self.network.disconnect()
        self._calling_user_id = None
        self.log("Call cancelled.")

    # ── Connection Logic ──────────────────────────────────────────

    def _show_connection_request(self, requester_name, ip):
        """Show incoming connection request as a panel banner."""
        self.pending_from_id = ip

        # Green mode: auto-accept (intercom behavior)
        if self.mode == self.MODE_GREEN:
            self.log(f"Auto-accepting from {requester_name} (green mode)")
            self._on_accept_call()
            return

        self.panel.show_incoming(requester_name)

        # Show the panel if hidden
        if not self.panel.isVisible():
            geo = self.tray.geometry()
            self.panel.show_at(QPoint(geo.center().x(), geo.bottom()))

    def _handle_connection_response(self, accepted):
        """Handle response after our connection request was accepted/rejected."""
        self.panel.hide_outgoing()
        if accepted:
            # Get peer name for display
            peer_name = ""
            if hasattr(self, '_calling_user_id') and self._calling_user_id in self.online_users:
                peer_name = self.online_users[self._calling_user_id].get("name", "")
            if not peer_name:
                peer_name = getattr(self, '_calling_user_name', "Peer")
            peer_mode = ""
            if hasattr(self, '_calling_user_id') and self._calling_user_id in self.online_users:
                peer_mode = self.online_users[self._calling_user_id].get("mode", "GREEN")
            self.panel.set_connection(True, peer_name, peer_mode)
            self._start_open_line_if_ready()
        else:
            self.log("Connection declined.")
            self.panel.set_connection(False)

    def do_disconnect(self):
        """Disconnect from current session."""
        # Tell remote peer we're leaving so they can restore their mode
        if self.network.connected:
            try:
                self.network.send_control("CALL_ENDED", {})
            except Exception:
                pass  # Best-effort — connection may already be broken

        self._hotline_on = False
        self.audio.set_hotline(False)
        self.audio.stop_streaming()  # Stop any active stream (PTT or hotline)
        self.audio.reset_codec()  # Reset to default codec for next connection
        self._clear_busy()
        self.peer_talking = False
        self._connected_peer_id = None  # Clear so they reappear in user list
        self.network.disconnect()
        self.panel.set_connection(False)
        self.panel.set_hotline_enabled(False)
        self.panel.set_hotline(False)
        self.panel.hide_call()

        self.log("Disconnected.")

    def _clear_busy(self):
        self.active_room_code = None
        self._pre_call_mode = None

    def _start_open_line_if_ready(self):
        if self._hotline_on and self.network.connected:
            self.audio.start_streaming()
            self.log("Hotline active — streaming...")

    # ── Presence Methods ──────────────────────────────────────────

    def _prompt_for_name(self):
        from PySide6.QtWidgets import QInputDialog
        dialog = QInputDialog()
        dialog.setWindowTitle("Welcome to Vox")
        dialog.setLabelText("Enter your display name:")
        ok = dialog.exec()
        name = dialog.textValue()
        if ok and name.strip():
            self.display_name = name.strip()
            set_display_name(self.display_name)
            self.log(f"Display name set to: {self.display_name}")
        else:
            import socket
            self.display_name = socket.gethostname()
            set_display_name(self.display_name)
            self.log(f"Using hostname: {self.display_name}")

    def _auto_connect_presence(self):
        time.sleep(1)

        # Sync profile and load teams from Supabase
        try:
            supabase_client.ensure_profile(self.user_id, self.display_name)
            teams = supabase_client.get_my_teams(self.user_id)
            self.my_teams = teams or []
            self.log_signal.emit(f"Supabase: {len(self.my_teams)} team(s) loaded")

            # Show team picker — only the user's own teams (no public lobby)
            self._teams_loaded_signal.emit()
            self._available_teams_signal.emit([[], self.my_teams])  # No public teams

        except Exception as e:
            self.log_signal.emit(f"Supabase sync: {e}")
            # Still show lobby even if Supabase fails
            self._teams_loaded_signal.emit()

        # Connect to presence — use active_team_id if auto-selected, else empty
        try:
            success = self.network.connect_presence(
                RELAY_HOST, RELAY_PORT, self.display_name, self.user_id,
                self.mode, self.active_team_id or "",
            )
            if success:
                self.log_signal.emit(f'Connected to presence as "{self.display_name}"')
            else:
                self.log_signal.emit("Could not connect to presence server")
        except Exception as e:
            self.log_signal.emit(f"Presence auto-connect failed: {e}")

    def handle_presence_message(self, msg):
        msg_type = msg.get("type")

        if msg_type == "PRESENCE_UPDATE":
            users = msg.get("users", [])
            filtered = [u for u in users if u.get("user_id") != self.user_id]
            self.presence_update_signal.emit(filtered)

        elif msg_type == "CONNECTION_REQUEST":
            from_name = msg.get("from_name", "Someone")
            from_id = msg.get("from_id", "")
            room_code = msg.get("room", "")
            self.presence_request_signal.emit(from_name, from_id, room_code)

        elif msg_type == "CONNECT_ROOM":
            room_code = msg.get("room", "")
            role = msg.get("role", "")
            self.log_signal.emit("Connecting...")
            threading.Thread(
                target=self._join_relay_room, args=(room_code, role), daemon=True
            ).start()

        elif msg_type == "CONNECTION_REJECTED":
            self.log_signal.emit("Call was declined.")
            self.panel.hide_outgoing()
            self.panel.set_connection(False)

        elif msg_type == "CONNECTION_CANCELLED":
            self.log_signal.emit("Call was cancelled by caller.")
            self.panel.hide_incoming()
            self.pending_room = None
            self.pending_from_id = None

        elif msg_type == "JOIN_REQUEST":
            # Admin received a join request from lobby
            request_id = msg.get("request_id", "")
            requester_id = msg.get("requester_id", "")
            team_id = msg.get("team_id", "")
            requester_name = msg.get("requester_name", "Someone")

            # Fallback: if relay didn't send requester_id, look it up from Supabase
            if not requester_id and request_id:
                jr = supabase_client.get_join_request(request_id)
                if jr:
                    requester_id = jr.get("requester_id", "")
                    team_id = team_id or jr.get("team_id", "")

            self._join_request_signal.emit(request_id, team_id, requester_name, requester_id)

        elif msg_type == "JOIN_RESPONSE":
            # Requester received a response from admin
            self._join_response_signal.emit(
                msg.get("request_id", ""),
                msg.get("approved", False),
            )

        elif msg_type == "JOIN_REQUEST_FAILED":
            self._join_request_failed_signal.emit(
                msg.get("reason", "Could not reach team admin")
            )

        elif msg_type == "PAGE_ALL":
            # One-way broadcast message from a teammate
            self._handle_page_all(msg)

    def _join_relay_room(self, room_code, role):
        """Connect to relay for an active call. Room code is internal — never shown to user."""
        success = self.network.join_room(RELAY_HOST, room_code, RELAY_PORT)
        if success:
            self.active_room_code = room_code  # Internal tracking only
            self._send_codec_offer()

            # Determine peer name and ID for UI
            if role == "creator":
                self._connected_peer_id = getattr(self, '_calling_user_id', None)
                target_name = self.online_users.get(self._connected_peer_id, {}).get("name", "") if self._connected_peer_id else ""
                if not target_name:
                    target_name = getattr(self, '_calling_user_name', "Peer")
                self.call_connected_signal.emit(target_name)
            elif role == "joiner":
                self._connected_peer_id = self.pending_from_id
                caller_name = self.online_users.get(self._connected_peer_id, {}).get("name", "") if self._connected_peer_id else ""
                if not caller_name:
                    caller_name = getattr(self, 'pending_from_name', "Peer")
                self.call_connected_signal.emit(caller_name)

            self._start_open_line_if_ready()
        else:
            self.log_signal.emit("Could not connect to peer.")
            self.panel.set_connection(False)

    @Slot(str)
    def _on_call_connected(self, peer_name):
        """Called on main thread when call is established."""
        self._intercom_connected = True

        # If PTT is physically held while connection completes, start streaming now
        if self._intercom_target_id and self._intercom_ptt_held and not self._intercom_streaming:
            self._start_intercom_stream(self._intercom_target_id, peer_name)
            self.panel.hide_outgoing()
            return

        # If just selecting (no PTT), update row state and suppress call banner
        if self._intercom_target_id:
            self.panel.set_user_state(self._intercom_target_id, "selected")
            self.panel.hide_outgoing()
            peer_mode = self.online_users.get(self._intercom_target_id, {}).get("mode", "GREEN")
            self.panel.set_connection(True, peer_name, peer_mode)
            self.log(f"Ready — {peer_name}")
            return

        # Legacy call flow (call banners etc.)
        self.panel.hide_outgoing()
        self.panel.show_call(peer_name)
        self.panel.set_connection(True, peer_name)
        self.panel.set_hotline_enabled(True)


    @Slot(list)
    def _update_online_users(self, users):
        """Update the panel user list from presence data, filtered by active team."""
        self.online_users = {}
        panel_users = []

        for user in users:
            uid = user.get("user_id", "")
            name = user.get("name", "Unknown")
            mode = user.get("mode", "GREEN")
            team_id = user.get("team_id", "")
            self.online_users[uid] = {"name": name, "mode": mode, "room": user.get("room", ""), "team_id": team_id}
            # Filter by active team — only show users in the same team
            if self.active_team_id and team_id != self.active_team_id:
                continue
            panel_users.append({
                'id': uid,
                'name': name,
                'mode': mode,
                'has_message': False  # TODO: track per-user messages
            })

        # Append offline team members
        online_ids = {u['id'] for u in panel_users}
        for uid, name in self._team_members.items():
            if uid not in online_ids:
                panel_users.append({
                    'id': uid,
                    'name': name,
                    'mode': 'OFFLINE',
                    'has_message': False,
                })

        self._last_panel_users = panel_users
        self.panel.set_users(panel_users, self._intercom_target_id)
        self._rebuild_tray_menu()
        self._broadcast_deck_state()

        # Update sidebar peer badge if connected peer's mode changed
        if self._intercom_target_id and self._intercom_target_id in self.online_users:
            peer_mode = self.online_users[self._intercom_target_id].get("mode", "GREEN")
            self.panel.set_peer_mode(peer_mode)

        # Auto-select if there's exactly one online user and no current target
        # Only fire once per user to avoid retry loops on connection failures
        online_count = sum(1 for u in panel_users if u.get('mode') != 'OFFLINE')
        if online_count == 1 and not self._intercom_target_id:
            only_user = next(u for u in panel_users if u.get('mode') != 'OFFLINE')
            uid = only_user['id']
            if only_user.get('mode') != self.MODE_RED and uid != getattr(self, '_auto_select_attempted', ''):
                self._auto_select_attempted = uid
                self._on_user_selected(uid)

    @Slot(str, str, str)
    def _show_presence_request(self, from_name, from_id, room_code):
        """Show incoming call via presence."""
        self.log(f"Incoming call from {from_name} (id={from_id}, room={room_code!r})")
        self.pending_from_id = from_id
        self.pending_from_name = from_name
        self.pending_room = room_code  # Internal — not shown to user

        # Green mode: auto-accept (intercom behavior)
        if self.mode == self.MODE_GREEN:
            self.log(f"Auto-accepting from {from_name} (green mode)")
            self._intercom_target_id = from_id
            self._on_accept_call()
            return

        # Yellow (Busy): auto-accept silently so caller can leave voicemail
        if self.mode == self.MODE_YELLOW:
            self.log(f"Auto-accepting voicemail from {from_name} (busy mode)")
            self._intercom_target_id = from_id
            self._on_accept_call()
            return

        self.panel.show_incoming(from_name)

        # Show panel if hidden
        if not self.panel.isVisible():
            geo = self.tray.geometry()
            self.panel.show_at(QPoint(geo.center().x(), geo.bottom()))

    # ── Peer Discovery ────────────────────────────────────────────

    def on_peer_found(self, name, ip):
        self.peer_found_signal.emit(name, ip)

    def on_peer_lost(self, name):
        self.peer_lost_signal.emit(name)

    def add_peer_to_ui(self, name, ip):
        if name not in self.peer_map:
            self.peer_map[name] = ip
            self.log(f"Found Peer: {name} ({ip})")
            self._refresh_lan_user_list()

    def remove_peer_from_ui(self, name):
        if name in self.peer_map:
            del self.peer_map[name]
            self.log(f"Lost Peer: {name}")
            self._refresh_lan_user_list()

    def _refresh_lan_user_list(self):
        """Update the panel user list from LAN-discovered peers."""
        # If we have presence/relay users, those take priority
        if self.online_users:
            return
        # Build user list from LAN peers
        panel_users = []
        for name, ip in self.peer_map.items():
            # Extract a friendly name from the Zeroconf service name
            # Format: "Vox (hostname)._talkback._tcp.local."
            friendly = name
            if '(' in name and ')' in name:
                friendly = name.split('(')[1].split(')')[0]
            elif '._talkback' in name:
                friendly = name.split('._talkback')[0]
            panel_users.append({
                'id': name,
                'name': friendly,
                'mode': 'GREEN',
                'has_message': False,
            })
        self.panel.set_users(panel_users, self._intercom_target_id)

    def log(self, msg):
        log.info(msg)

    # ── Button / Deck Logic ───────────────────────────────────────

    @Slot(str, dict)
    def _handle_ws_command(self, action, msg):
        """Handle commands from the Stream Deck plugin via WebSocket."""
        if action == "ptt_press":
            self.on_talk_press()
        elif action == "ptt_release":
            self.on_talk_release()
        elif action == "cycle_mode":
            self.cycle_mode()  # Uses app's cycle (updates deck + panel + presence)
        elif action == "cycle_team":
            self._ws_cycle_team()
        elif action == "cycle_user":
            self._ws_cycle_user()
        elif action == "show_panel":
            self._toggle_panel()

    def _ws_cycle_team(self):
        """Cycle through teams for WS plugin (mirrors plugin)."""
        teams = getattr(self, 'my_teams', [])
        if not teams:
            return
        self._ws_team_index = (self._ws_team_index + 1) % len(teams)
        team = teams[self._ws_team_index]
        self._ws_preview_name = team.get("name", "?")
        self._broadcast_deck_state()
        # Cancel existing timer and start new 1.5s auto-select
        self._ws_cancel_auto_select()
        self._ws_auto_select_timer = QTimer()
        self._ws_auto_select_timer.setSingleShot(True)
        self._ws_auto_select_timer.timeout.connect(self._ws_auto_select_team)
        self._ws_auto_select_timer.start(1500)

    def _ws_cycle_user(self):
        """Cycle through users for WS plugin (mirrors plugin)."""
        users = [u for u in self._last_panel_users if u.get("mode") != "OFFLINE"]
        if not users:
            return
        self._ws_user_index = (self._ws_user_index + 1) % len(users)
        user = users[self._ws_user_index]
        self._ws_preview_name = user.get("name", "?")
        self._broadcast_deck_state()
        # Cancel existing timer and start new 1.5s auto-select
        self._ws_cancel_auto_select()
        self._ws_auto_select_timer = QTimer()
        self._ws_auto_select_timer.setSingleShot(True)
        self._ws_auto_select_timer.timeout.connect(self._ws_auto_select_user)
        self._ws_auto_select_timer.start(1500)

    def _ws_auto_select_team(self):
        """Auto-confirm team selection after browse timeout."""
        teams = getattr(self, 'my_teams', [])
        if self._ws_team_index < len(teams):
            team = teams[self._ws_team_index]
            self._switch_team(team.get("id", ""), team.get("name", ""))
        self._ws_preview_name = ""
        self._broadcast_deck_state()

    def _ws_auto_select_user(self):
        """Auto-confirm user selection after browse timeout."""
        users = [u for u in self._last_panel_users if u.get("mode") != "OFFLINE"]
        if self._ws_user_index < len(users):
            user = users[self._ws_user_index]
            self._on_user_selected(user.get("id", ""))
        self._ws_preview_name = ""
        self._broadcast_deck_state()

    def _ws_cancel_auto_select(self):
        if self._ws_auto_select_timer:
            self._ws_auto_select_timer.stop()
            self._ws_auto_select_timer = None

    def _broadcast_deck_state(self):
        """Send current app state to connected Stream Deck plugins."""
        if not hasattr(self, 'deck_ws'):
            return
        # Determine talk state
        talk = "idle"
        if getattr(self, '_intercom_ptt_held', False):
            talk = "live"
        elif getattr(self, 'peer_talking', False):
            talk = "listen"

        teams = []
        for t in getattr(self, 'my_teams', []):
            teams.append({"id": t.get("id", ""), "name": t.get("name", "")})

        users = []
        for u in getattr(self, '_last_panel_users', []):
            if u.get("mode") == "OFFLINE":
                continue  # Don't send offline members to Stream Deck
            users.append({"id": u.get("id", ""), "name": u.get("name", ""), "mode": u.get("mode", "GREEN")})

        self.deck_ws.broadcast_state({
            "mode": self.mode,
            "talk": talk,
            "message": getattr(self, 'has_message', False),
            "teams": teams,
            "users": users,
            "activeTeamId": getattr(self, 'active_team_id', "") or "",
            "activeUserId": getattr(self, '_intercom_target_id', "") or "",
            "connected": getattr(self.network, 'connected', False),
            "peerName": self.online_users.get(self._connected_peer_id or "", {}).get("name", ""),
            "preview": self._ws_preview_name,
            "browseTeamIndex": self._ws_team_index,
            "browseUserIndex": self._ws_user_index,
        })

    def _activate_app(self):
        """Bring the app to the foreground (needed on macOS for background actions)."""
        if sys.platform == 'darwin':
            try:
                from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
                ns_app = NSApplication.sharedApplication()
                # Menu-bar-only apps default to Prohibited policy which blocks
                # window activation.  Temporarily switch to Accessory so macOS
                # allows us to show and raise windows.
                ns_app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
                ns_app.activateIgnoringOtherApps_(True)
            except ImportError:
                pass
        elif sys.platform == 'win32':
            try:
                import ctypes
                hwnd = int(self.panel.winId())
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass  # Fall through to Qt raise_/activateWindow below

    def _toggle_panel_visibility(self):
        """Toggle panel window visibility (must be called on the main thread)."""
        if self.panel.isVisible():
            self.panel.hide()
        else:
            self._activate_app()
            self.panel.show()
            self.panel.raise_()
            self.panel.activateWindow()

    def _toggle_panel(self):
        """Toggle panel visibility — used by Stream Deck OH button."""
        if self.panel.isVisible():
            self.panel.hide()
        else:
            self._show_panel_at_tray()

    def _show_panel_at_tray(self):
        """Show the panel anchored below the menu bar icon."""
        self._activate_app()
        if self.panel.isVisible():
            self.panel.raise_()
            self.panel.activateWindow()
            return
        geo = self.tray.geometry()
        if geo.isValid() and geo.width() > 0:
            self.panel.show_at(QPoint(geo.center().x(), geo.bottom()))
        else:
            # Fallback positioning
            screen = QApplication.primaryScreen().availableGeometry()
            if sys.platform == 'win32':
                # Windows: above taskbar, right-aligned
                x = screen.right() - self.panel.width() - 8
                y = screen.bottom() - self.panel.height() - 8
            else:
                # macOS: below menu bar, right-aligned
                x = screen.right() - self.panel.width() - 8
                y = screen.top() + 4
            self.panel.move(x, y)
            self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def on_talk_press(self):
        # Route through intercom system if a target is selected
        if self._intercom_target_id:
            self._on_intercom_press(self._intercom_target_id)
            self.panel.set_ptt_active(True)
            self._broadcast_deck_state()
            return

        if not self.network.connected:
            self.log("Not connected to a peer.")
            return
        if self.mode == self.MODE_RED:
            self.log("You are in DND mode.")
            return
        if self.peer_talking:
            self.log("Peer is talking — wait.")
            return

        self.network.send_control("TALK_START", {})
        self.panel.set_ptt_active(True)

        if self.remote_mode in (self.MODE_GREEN, self.MODE_GREEN, self.MODE_YELLOW):
            self.log("Streaming Audio...")
            self.audio.start_streaming()
        elif self.remote_mode == self.MODE_RED:
            self.log("Peer is unavailable (DND).")

    def on_talk_release(self):
        # Route through intercom system if a target is selected
        if self._intercom_target_id:
            self._on_intercom_release(self._intercom_target_id)
            self.panel.set_ptt_active(False)
            self._broadcast_deck_state()
            return

        self.audio.stop_streaming()
        self.network.send_control("TALK_STOP", {})
        self.panel.set_ptt_active(False)

        if self.audio.recording:
            filename = self.audio.stop_recording_message()
            if filename:
                self.log(f"Sending Message ({filename})...")
                self.network.send_file(filename)

        self.update_deck_display()

    # ── Page All ───────────────────────────────────────────────────
    def on_page_all_press(self):
        """Record a broadcast message."""
        self.audio.start_recording_message()
        self.panel.set_ptt_active(True)
        self.log("Broadcasting...")

    def on_page_all_release(self):
        """Stop recording and broadcast to all GREEN team members."""
        filename = self.audio.stop_recording_message()
        self.panel.set_ptt_active(False)
        if not filename:
            return
        if not self.active_team_id:
            self.log("No team selected — cannot page all")
            return

        def _send():
            ok = self.network.send_page_all(
                filename, self.active_team_id, self.display_name
            )
            if ok:
                self.log_signal.emit("Page All sent")
            else:
                self.log_signal.emit("Page All failed — not connected")

        threading.Thread(target=_send, daemon=True).start()
        # Reinforce the selected user highlight after page-all ends
        if self._intercom_target_id:
            self.panel.highlight_selected_user(self._intercom_target_id)

    def on_answer(self):
        if self.has_message or self._message_queue:
            self._on_play_message()

    def _play_user_messages(self, user_id):
        """Play all messages from a specific user, then clear their message state."""
        if self._playing_message:
            return
        paths = self._user_messages.get(user_id, [])
        if not paths:
            return

        self._playing_message = True
        target = self.online_users.get(user_id, {})
        target_name = target.get("name", "Unknown")
        count = len(paths)
        self.log(f"Playing {count} message{'s' if count > 1 else ''} from {target_name}...")

        # Remove from global queue too
        for p in paths:
            if p in self._message_queue:
                self._message_queue.remove(p)

        def _play():
            while paths:
                path = paths.pop(0)
                try:
                    self.audio.play_file(path)
                    os.remove(path)
                except Exception as e:
                    print(f"[Message] Play error: {e}")
            self._playing_message = False
            # Clear this user's messages
            self._user_messages.pop(user_id, None)
            # Check if any messages remain globally
            if not self._user_messages:
                self.has_message = False
                self.is_flashing = False
                self.incoming_message_path = None
            QTimer.singleShot(0, lambda: self.panel.set_user_state(user_id, "idle"))
            QTimer.singleShot(0, self._broadcast_deck_state)

        threading.Thread(target=_play, daemon=True).start()

    def _on_play_message(self):
        """Play all queued messages in succession (legacy / play-all fallback)."""
        if self._playing_message:
            return  # Already playing
        if not self._message_queue:
            if self.has_message and self.incoming_message_path:
                # Fallback: single message (legacy)
                self._message_queue = [self.incoming_message_path]
            else:
                return

        self._playing_message = True
        self.has_message = False
        self.is_flashing = False
        self.update_deck_display()
        self.panel.hide_message()
        count = len(self._message_queue)
        self.log(f"Playing {count} message{'s' if count > 1 else ''}...")

        def _play_all():
            while self._message_queue:
                path = self._message_queue.pop(0)
                try:
                    self.audio.play_file(path)
                    # Clean up played file
                    os.remove(path)
                except Exception as e:
                    print(f"[Message] Play error: {e}")
            self._playing_message = False
            self.incoming_message_path = None
            self.has_message = False
            # Clear all per-user messages too
            for uid in list(self._user_messages.keys()):
                self._user_messages.pop(uid, None)
                QTimer.singleShot(0, lambda u=uid: self.panel.set_user_state(u, "idle"))
            QTimer.singleShot(0, self._broadcast_deck_state)

        threading.Thread(target=_play_all, daemon=True).start()

    def _handle_page_all(self, msg):
        """Handle an incoming PAGE_ALL broadcast — save audio and queue it."""
        import base64
        import time as _time

        from_name = msg.get("from_name", "Someone")
        from_id = msg.get("from_id", "")
        audio_b64 = msg.get("audio_b64", "")
        if not audio_b64:
            return
        try:
            audio_data = base64.b64decode(audio_b64)
            fn = os.path.join(_config_dir(), f"page_{int(_time.time()*1000)}.wav")
            with open(fn, 'wb') as f:
                f.write(audio_data)
            self._message_queue.append(fn)
            self.incoming_message_path = fn
            self.has_message = True
            self.is_flashing = True
            # Track per-user
            if from_id:
                if from_id not in self._user_messages:
                    self._user_messages[from_id] = []
                self._user_messages[from_id].append(fn)
                QTimer.singleShot(0, lambda uid=from_id: self.panel.set_user_state(uid, "message"))
            self.update_deck_display()
            self.audio.play_notification()
            self.message_received_signal.emit()
            self.log(f"Page All from {from_name}")
        except Exception as e:
            self.log(f"Page All receive error: {e}")

    def _save_voicemail_from_buffer(self):
        """Save buffered audio from a peer who talked while we were busy."""
        import time as _time

        import soundfile as sf
        sender_id = self._connected_peer_id or ""
        try:
            audio = np.concatenate(self._vm_buffer)
            fn = os.path.join(_config_dir(), f"msg_{int(_time.time()*1000)}.wav")
            sf.write(fn, audio, SAMPLE_RATE)
            self._message_queue.append(fn)
            self.incoming_message_path = fn
            self.has_message = True
            # Track per-user
            if sender_id:
                if sender_id not in self._user_messages:
                    self._user_messages[sender_id] = []
                self._user_messages[sender_id].append(fn)
                # Set user row to MESSAGE state
                QTimer.singleShot(0, lambda uid=sender_id: self.panel.set_user_state(uid, "message"))
            self.log(f"Voicemail received ({len(self._vm_buffer)} chunks)")
            self.message_received_signal.emit()
            self.update_deck_display()
        except Exception as e:
            self.log(f"Voicemail save error: {e}")
        finally:
            self._vm_buffer = []

    def _set_mode(self, new_mode):
        """Set mode directly (used by deck buttons)."""
        if new_mode == self.mode:
            return
        old_mode = self.mode
        self.mode = new_mode
        # Clear voicemail buffer if leaving busy mode
        if old_mode == self.MODE_YELLOW and hasattr(self, '_vm_buffer'):
            self._vm_buffer = []

        if old_mode == self.MODE_GREEN and self.mode != self.MODE_GREEN:
            self.audio.stop_streaming()
            self.panel.set_hotline(False)

        self.panel.set_mode(self.mode)
        self._update_tray_icon()
        label = self.MODE_LABELS.get(self.mode, self.mode)
        self.log(f"Mode: {label}")
        self.send_status()
        self.network.update_presence_mode(self.mode)
        self.update_deck_display()
        self._update_ptt_for_mode()

    def cycle_mode(self):
        if self.mode == self.MODE_GREEN:
            self._set_mode(self.MODE_YELLOW)
        elif self.mode == self.MODE_YELLOW:
            self._set_mode(self.MODE_RED)
        elif self.mode == self.MODE_RED:
            self._set_mode(self.MODE_GREEN)
        else:  # OPEN — cycling goes back to GREEN
            self._set_mode(self.MODE_GREEN)

    def _on_mode_set(self, mode):
        """Direct mode set from sidebar dropdown."""
        self._set_mode(mode)

    def _fetch_team_members(self):
        """Fetch all members of the active team in background."""
        if not self.active_team_id:
            self._team_members = {}
            return
        tid = self.active_team_id
        uid = self.user_id
        print(f"[TeamMembers] Starting fetch for team {tid}, user {uid}")
        def _fetch():
            try:
                members = supabase_client.get_team_members(tid)
                print(f"[TeamMembers] Raw response: {len(members)} members")
                result = {}
                for m in members:
                    mid = m.get("user_id", "")
                    if mid and mid != uid:  # Exclude self
                        result[mid] = m.get("display_name", "Unknown")
                self._team_members = result
                print(f"[TeamMembers] Fetched {len(result)} members (excl self): {list(result.values())}")
                # Re-filter to merge offline members into the list
                QTimer.singleShot(0, self._refilter_online_users)
            except Exception as e:
                print(f"[TeamMembers] ERROR: {e}")
        threading.Thread(target=_fetch, daemon=True).start()

    def _switch_team(self, team_id, team_name):
        """Switch active team (used by deck buttons)."""
        if team_id == self.active_team_id:
            return
        self.active_team_id = team_id
        self.active_team_name = team_name
        set_active_team(team_id)
        set_active_team_name(team_name)
        self.log(f"Switched to team: {team_name}")
        self.network.update_presence_team(team_id)
        self._fetch_team_members()
        self._refilter_online_users()
        self.panel.set_teams(self.my_teams, self.active_team_id)

    def _hotkey_talk_press(self):
        """Called from pynput thread — emit signal to run on Qt thread."""
        self.hotkey_press_signal.emit()

    def _hotkey_talk_release(self):
        """Called from pynput thread — emit signal to run on Qt thread."""
        self.hotkey_release_signal.emit()

    def _update_ptt_for_mode(self):
        # PTT state is handled by panel.set_mode() now
        # Just update deck
        pass

    def send_status(self):
        self.network.send_control("STATUS", {"mode": self.mode})

    def _send_codec_offer(self):
        """Send our supported codecs to the peer for negotiation."""
        codecs = self.audio.get_supported_codecs()
        self.network.send_control("CODEC_OFFER", {"codecs": codecs})

    # ── Network Callbacks ─────────────────────────────────────────

    def handle_audio_stream(self, data):
        if self.mode == self.MODE_GREEN:
            self.audio.play_audio_chunk(data)
        elif self.mode == self.MODE_YELLOW and self.peer_talking:
            # Busy mode: buffer incoming audio as a voicemail
            try:
                raw = self.audio._decode(data)
                audio_data = np.frombuffer(raw, dtype=DTYPE)
                if len(audio_data) > 0:
                    if not hasattr(self, '_vm_buffer'):
                        self._vm_buffer = []
                    self._vm_buffer.append(audio_data.copy())
            except Exception:
                pass

    def handle_network_message(self, msg):
        msg_type = msg.get("type")
        payload = msg.get("payload")

        if msg_type == "STATUS":
            self.remote_mode = payload.get("mode")
            self.log_signal.emit(f"Remote is now {self.remote_mode}")

        elif msg_type == "TALK_START":
            self.peer_talking = True
            peer_id = self._connected_peer_id
            print(f"[DEBUG] TALK_START: peer_id={peer_id}, rows={list(self.panel._user_rows.keys())}")
            QTimer.singleShot(0, lambda: self.panel.set_ptt_locked(True))
            if peer_id:
                QTimer.singleShot(0, lambda: self.panel.set_user_state(peer_id, "live"))
            self._broadcast_deck_state()
            self.log_signal.emit("Peer is talking...")
            # Start fresh voicemail buffer if we're busy
            if self.mode == self.MODE_YELLOW:
                self._vm_buffer = []

        elif msg_type == "TALK_STOP":
            self.peer_talking = False
            peer_id = self._connected_peer_id
            QTimer.singleShot(0, lambda: self.panel.set_ptt_locked(False))
            if peer_id:
                QTimer.singleShot(0, lambda: self.panel.set_user_state(peer_id, "selected"))
            self._broadcast_deck_state()
            self.audio.play_talk_ended()
            # If we were in busy mode and buffered audio, save as voicemail
            vm_count = len(self._vm_buffer) if hasattr(self, '_vm_buffer') else 0
            print(f"[DEBUG] TALK_STOP: mode={self.mode}, vm_buffer={vm_count} chunks")
            if self.mode == self.MODE_YELLOW and hasattr(self, '_vm_buffer') and self._vm_buffer:
                self._save_voicemail_from_buffer()

        elif msg_type == "CODEC_OFFER":
            # Peer sent their supported codecs — pick the best common one and reply
            peer_codecs = payload.get("codecs", ["ulaw"])
            chosen = self.audio.negotiate_codec(peer_codecs)
            self.network.send_control("CODEC_ACCEPT", {"codec": chosen})
            if chosen == "ulaw":
                self.log_signal.emit("⚠ Audio: µ-law (peer missing Opus)")
            else:
                self.log_signal.emit(f"Audio: {chosen}")

        elif msg_type == "CODEC_ACCEPT":
            # Peer accepted our codec offer — activate the chosen codec
            chosen = payload.get("codec", "ulaw")
            self.audio.negotiate_codec([chosen])
            if chosen == "ulaw":
                self.log_signal.emit("⚠ Audio: µ-law (peer missing Opus)")
            else:
                self.log_signal.emit(f"Audio: {chosen}")

        elif msg_type == "PEER_CONNECTED":
            ip = payload.get("ip", "unknown")
            self.peer_ip = ip
            self.log_signal.emit(f"Direct connection from {ip}")
            self._send_codec_offer()

        elif msg_type == "CONNECTION_REQUEST":
            requester_name = payload.get("name", "Unknown")
            self.log_signal.emit(f"Incoming direct LAN call from {requester_name}")
            # Store caller name for accept handler
            self._incoming_caller_name = requester_name
            # Only overwrite pending state if we don't already have a relay call pending
            if not self.pending_room:
                self.pending_from_id = self.peer_ip or "direct"
                self.pending_room = None  # No relay session for direct calls
                self.presence_request_signal.emit(requester_name, self.pending_from_id, "")
            else:
                self.log_signal.emit("Ignoring direct CONNECTION_REQUEST — relay call already pending")

        elif msg_type == "CONNECTION_ACCEPTED":
            self.log_signal.emit("Call accepted!")
            caller_name = payload.get("name", "Peer")
            self.call_connected_signal.emit(caller_name)
            self._start_open_line_if_ready()


        elif msg_type == "CALL_ENDED":
            # Remote peer ended the call — disconnect our side and restore mode
            self.log_signal.emit("Call ended by peer.")
            self.audio.stop_streaming()
            self.audio.reset_codec()
            self._clear_busy()
            self.peer_talking = False
            self._connected_peer_id = None
            self.network.disconnect()
            QTimer.singleShot(0, lambda: (self.panel.set_connection(False), self.panel.hide_call()))

        elif msg_type == "CONNECTION_REJECTED":
            self.log_signal.emit("Connection declined.")
            self.pending_connection = False
            self.connection_response_signal.emit(False)
            self.network.disconnect()

        elif msg_type == "FILE_HEADER":
            self.incoming_file_size = payload.get("size", 0)
            if self.incoming_file_size > MAX_FILE_SIZE:
                self.log_signal.emit(f"Rejected message: too large ({self.incoming_file_size} bytes, max {MAX_FILE_SIZE})")
                return
            self.log_signal.emit(f"Receiving Message ({self.incoming_file_size} bytes)...")

        elif msg_type == "BINARY_DATA":
            data = payload
            self.log_signal.emit(f"File Received: {len(data)} bytes")
            try:
                import time as _time
                fn = os.path.join(_config_dir(), f"msg_{int(_time.time()*1000)}.wav")
                with open(fn, 'wb') as f:
                    f.write(data)
                self._message_queue.append(fn)
                self.incoming_message_path = fn
                self.has_message = True
                self.is_flashing = True
                self.update_deck_display()
                self.audio.play_notification()
                self.message_received_signal.emit()
                self.log_signal.emit("Message Saved.")
            except Exception as e:
                self.log_signal.emit(f"Error saving file: {e}")

    # ── Display ───────────────────────────────────────────────────

    def update_deck_display(self):
        self._broadcast_deck_state()

    def flash_loop(self):
        # Message pulsing handled by Stream Deck plugin
        pass

    def _cleanup_messages(self):
        cfg_dir = _config_dir()
        import glob
        for pattern in ("outgoing_message.wav", "incoming_message.wav", "msg_*.wav"):
            for path in glob.glob(os.path.join(cfg_dir, pattern)):
                try:
                    os.remove(path)
                except OSError as e:
                    print(f"Could not delete {path}: {e}")
        self._message_queue.clear()

    def _on_incognito_toggle(self, enabled):
        """Toggle incognito mode — hide from online user list."""
        if enabled:
            self.log("Incognito mode ON — you are now invisible")
            self.network.disconnect_presence()
            self.panel.set_users([])  # Clear user list
        else:
            self.log("Incognito mode OFF — you are now visible")
            if RELAY_HOST and self.display_name:
                threading.Thread(target=self._auto_connect_presence, daemon=True).start()

    def _on_dark_mode_toggle(self, enabled):
        """Toggle dark mode appearance."""
        self.panel.apply_dark_mode(enabled)
        self.log(f"Dark mode {'ON' if enabled else 'OFF'}")

    def _on_name_changed(self, new_name):
        """Handle display name change from settings menu."""
        self.display_name = new_name
        set_display_name(new_name)
        self.network.display_name = new_name
        self.panel.set_display_name(new_name)
        self.log(f"Display name changed to: {new_name}")
        # Sync to Supabase in background so it persists across launches
        import threading
        threading.Thread(
            target=lambda: supabase_client.ensure_profile(self.user_id, new_name),
            daemon=True,
        ).start()
        # Update presence server with new name
        if self.network.presence_connected:
            self.network.update_presence_name(new_name)

    # ── Auth Handlers ──────────────────────────────────────────

    @Slot(dict)
    def _on_login_completed(self, session):
        """Handle successful login — set user_id, save session, connect."""
        user_id = session.get("user_id", "")
        email = session.get("email", "")
        display_name = session.get("display_name", "")

        if user_id:
            self.user_id = user_id
            self.network.user_id = user_id

        # Save auth session
        save_auth_session(session)
        self._auth_logged_in = True

        # Start token refresh timer
        self._auth_refresh_timer.start()

        # Set display name from auth if we got one, else keep existing
        if display_name:
            self.display_name = display_name
            set_display_name(display_name)
            self.network.display_name = display_name
            self.panel.set_display_name(display_name)

        if not self.display_name and email:
            # Use email prefix as fallback display name
            fallback = email.split("@")[0]
            self.display_name = fallback
            set_display_name(fallback)
            self.network.display_name = fallback
            self.panel.set_display_name(fallback)

        self.log(f'Logged in as "{self.display_name}" ({email})')

        # Sync profile and connect
        threading.Thread(target=self._auto_connect_presence, daemon=True).start()

    @Slot()
    def _on_login_skipped(self):
        """Handle login skip — use old local UUID flow."""
        self.log("Login skipped — using local identity")
        self._auth_logged_in = False

        if self.display_name:
            # Already have a name — just connect
            threading.Thread(target=self._auto_connect_presence, daemon=True).start()
        else:
            # Show onboarding to collect name
            self._teams_loaded_signal.emit()

    @Slot()
    def _on_sign_out(self):
        """Handle sign out — clear session, show login page."""
        session = get_auth_session()
        if session:
            token = session.get("access_token", "")
            # Sign out on server in background
            threading.Thread(
                target=lambda: auth_manager.sign_out(token), daemon=True
            ).start()

        clear_auth_session()
        self._auth_logged_in = False
        self._auth_refresh_timer.stop()
        self.log("Signed out")

        # Revert to local UUID for identity
        self.user_id = get_user_id()
        self.network.user_id = self.user_id

        # Show login page
        self.panel._switch_page("login")

    def _refresh_auth_token(self):
        """Periodically refresh the auth token (called by QTimer)."""
        def _do_refresh():
            session = get_auth_session()
            if not session:
                return
            refresh_token = session.get("refresh_token")
            if not refresh_token:
                return
            try:
                result = auth_manager.refresh_session(refresh_token)
                if result and result.get("access_token"):
                    user = result.get("user", {})
                    save_auth_session({
                        "access_token": result["access_token"],
                        "refresh_token": result.get("refresh_token", refresh_token),
                        "expires_at": int(time.time()) + result.get("expires_in", 3600),
                        "user_id": user.get("id", session.get("user_id", "")),
                        "email": user.get("email", session.get("email", "")),
                    })
                    log.info("Auth token refreshed")
            except Exception as e:
                log.warning(f"Auth token refresh failed: {e}")

        threading.Thread(target=_do_refresh, daemon=True).start()

    # ── Team Management ─────────────────────────────────────────

    @Slot()
    def _on_teams_loaded(self):
        """Called on main thread when Supabase teams are loaded.
        Auto-selects if the user has exactly one team; otherwise shows lobby."""
        # Always show welcome page — let user pick their team
        self.panel.set_teams(self.my_teams, self.active_team_id, force_lobby=True)
        self._lobby_refresh_timer.start()

        # Pre-fetch team members so offline users are ready when team is selected
        if self.active_team_id:
            self._fetch_team_members()

    @Slot(list)
    def _set_available_teams(self, data):
        """Called on main thread to populate the lobby with available + my teams.
        data is [available_teams, my_teams] or just [available_teams]."""
        if isinstance(data, list) and len(data) == 2 and isinstance(data[0], list):
            available, my_teams = data[0], data[1]
            self.panel.set_available_teams(available, my_teams=my_teams,
                                           active_team_id=self.active_team_id)
        else:
            self.panel.set_available_teams(data, active_team_id=self.active_team_id)

    def _refresh_lobby_teams(self):
        """Poll Supabase for updated team list while lobby is showing."""
        import threading
        def _do_refresh():
            try:
                my_teams = supabase_client.get_my_teams(self.user_id) or []
                self.my_teams = my_teams
                self._available_teams_signal.emit([[], my_teams])
            except Exception:
                pass  # Silently skip — will retry next interval
        threading.Thread(target=_do_refresh, daemon=True).start()

    @Slot(str)
    def _on_team_changed(self, team_id):
        """User selected a different team from the dropdown."""
        if team_id == self.active_team_id:
            return
        self.active_team_id = team_id
        # Find team name
        for t in self.my_teams:
            if t["id"] == team_id:
                self.active_team_name = t["name"]
                break
        set_active_team(team_id)
        set_active_team_name(self.active_team_name)
        self.log(f"Switched to team: {self.active_team_name}")
        # Notify relay so presence broadcast includes new team_id
        self.network.update_presence_team(team_id)
        # Fetch members for the new team and re-filter
        self._fetch_team_members()
        self._refilter_online_users()

    def _refilter_online_users(self):
        """Re-filter and display online users based on current team.
        Appends offline team members (greyed out) after online users."""
        panel_users = []
        online_ids = set()
        for uid, info in self.online_users.items():
            if self.active_team_id and info.get("team_id", "") != self.active_team_id:
                continue
            panel_users.append({
                'id': uid,
                'name': info["name"],
                'mode': info["mode"],
                'has_message': False,
            })
            online_ids.add(uid)

        # Append offline team members
        for uid, name in self._team_members.items():
            if uid not in online_ids:
                panel_users.append({
                    'id': uid,
                    'name': name,
                    'mode': 'OFFLINE',
                    'has_message': False,
                })

        online_count = len(online_ids)
        offline_count = len(panel_users) - online_count
        print(f"[Refilter] {online_count} online, {offline_count} offline, {len(self._team_members)} team members cached")
        self._last_panel_users = panel_users
        self.panel.set_users(panel_users, self._intercom_target_id)
        self._rebuild_tray_menu()
        self._broadcast_deck_state()

    def _ensure_presence_connected(self):
        """Ensure Supabase profile exists and presence server is connected.
        Call from background threads after onboarding sets a name."""
        if not self.network.presence_connected:
            # Only sync profile if we need to connect (avoids extra round-trip)
            supabase_client.ensure_profile(self.user_id, self.display_name)
        if not self.network.presence_connected:
            try:
                success = self.network.connect_presence(
                    RELAY_HOST, RELAY_PORT, self.display_name, self.user_id,
                    self.mode, self.active_team_id,
                )
                if success:
                    self.log_signal.emit(f'Connected to presence as "{self.display_name}"')
            except Exception as e:
                self.log_signal.emit(f"Presence connect failed: {e}")

    @Slot()
    def _switch_to_team_view(self):
        """Thread-safe transition from lobby to team view.
        Called via signal from background threads after create/join/approve."""
        self._lobby_refresh_timer.stop()  # Stop polling — user picked a team
        self.panel.set_teams(self.my_teams, self.active_team_id)

    @Slot(str, str)
    def _show_invite_prompt(self, team_name, invite_code):
        """After creating a team, ask if the user wants to invite people."""
        from PySide6.QtWidgets import QMessageBox
        msg = QMessageBox(self.panel)
        msg.setWindowTitle("Invite Your Team")
        msg.setText(f'"{team_name}" is ready!\n\nInvite your teammates?')
        msg.setInformativeText(f"Invite code: {invite_code}")
        email_btn = msg.addButton("Send Email Invite", QMessageBox.AcceptRole)
        copy_btn = msg.addButton("Copy Code", QMessageBox.ActionRole)
        msg.addButton("Later", QMessageBox.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == email_btn:
            self.panel._invite_friend_email()
        elif clicked == copy_btn:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(invite_code)

    @Slot(str)
    def _on_create_team(self, team_name):
        """Create a new team (runs on background thread)."""
        def _do_create():
            self._ensure_presence_connected()
            result = supabase_client.create_team(team_name, self.user_id)
            if result:
                self.log_signal.emit(f"Created team: {team_name}")
                # Use the result directly — no need to reload from server
                team_entry = {
                    "id": result["id"],
                    "name": team_name,
                    "invite_code": result.get("invite_code", ""),
                    "role": "admin",
                }
                self.my_teams.append(team_entry)
                self.active_team_id = result["id"]
                self.active_team_name = team_name
                set_active_team(self.active_team_id)
                set_active_team_name(self.active_team_name)
                self.network.update_presence_team(self.active_team_id)
                # Transition directly to team view (not back to lobby)
                self._switch_to_team_signal.emit()
                # Prompt to invite teammates
                invite_code = result.get("invite_code", "")
                if invite_code:
                    self._show_invite_prompt_signal.emit(team_name, invite_code)
            else:
                self.log_signal.emit(f"Failed to create team: {team_name}")
        threading.Thread(target=_do_create, daemon=True).start()

    @Slot(str)
    def _on_join_code(self, invite_code):
        """User entered an invite code to join a team."""
        def _do_join():
            self._ensure_presence_connected()
            result = supabase_client.join_team_by_code(invite_code, self.user_id)
            if result:
                self.log_signal.emit(f"Joined team: {result['name']}")
                # Use the result directly — no extra round-trip
                team_entry = {
                    "id": result["id"],
                    "name": result["name"],
                    "invite_code": result.get("invite_code", ""),
                    "role": "member",
                }
                self.my_teams.append(team_entry)
                self.active_team_id = result["id"]
                self.active_team_name = result["name"]
                set_active_team(self.active_team_id)
                set_active_team_name(self.active_team_name)
                self.network.update_presence_team(self.active_team_id)
                # Transition directly to team view (not back to lobby)
                self._switch_to_team_signal.emit()
            else:
                self.log_signal.emit(f"Invalid invite code: {invite_code}")
                # Show error on onboarding screen (must happen on main thread)
                from PySide6.QtCore import Q_ARG, QMetaObject
                QMetaObject.invokeMethod(
                    self.panel, "set_onboarding_error",
                    Qt.QueuedConnection,
                    Q_ARG(str, "Invalid invite code. Please check and try again."),
                )
        threading.Thread(target=_do_join, daemon=True).start()

    @Slot()
    def _on_manage_team(self):
        """Open team info dialog (all members can view, only admins can remove)."""
        if not self.active_team_id:
            return
        # Fetch current members in background, then show dialog via signal
        def _fetch_and_show():
            try:
                members = supabase_client.get_team_members(self.active_team_id)
                self._show_manage_dialog_signal.emit(members)
            except Exception as e:
                self.log(f"Failed to fetch team members: {e}")
        threading.Thread(target=_fetch_and_show, daemon=True).start()

    def _show_manage_team_dialog(self, members):
        """Show team management dialog on the main thread."""
        # Get invite code and role for current team
        invite_code = ""
        is_admin = False
        for t in self.my_teams:
            if t["id"] == self.active_team_id:
                invite_code = t.get("invite_code", "")
                is_admin = t.get("role") == "admin"
                break
        self.panel.show_manage_team_dialog(
            self.active_team_name, self.active_team_id, members,
            invite_code=invite_code,
            is_admin=is_admin,
            add_callback=self._add_team_member,
            remove_callback=self._remove_team_member,
        )

    def _add_team_member(self, team_id, user_name):
        """Add a member to a team by display name (background thread)."""
        def _do_add():
            users = supabase_client.lookup_users(user_name)
            if not users:
                self.log_signal.emit(f"No user found matching '{user_name}'")
                return
            # Use first match
            target = users[0]
            result = supabase_client.add_member(team_id, target["id"])
            if result:
                self.log_signal.emit(f"Added {target['display_name']} to team")
            else:
                self.log_signal.emit(f"Failed to add {user_name} to team")
        threading.Thread(target=_do_add, daemon=True).start()

    def _remove_team_member(self, team_id, user_id):
        """Remove a member from a team (background thread)."""
        def _do_remove():
            supabase_client.remove_member(team_id, user_id)
            self.log_signal.emit("Member removed from team")
        threading.Thread(target=_do_remove, daemon=True).start()

    # ── Lobby Team Selection ──────────────────────────────────

    @Slot(str, str)
    def _on_team_selected_from_lobby(self, team_id, team_name):
        """User selected one of their own teams from the lobby."""
        self.active_team_id = team_id
        self.active_team_name = team_name
        set_active_team(team_id)
        set_active_team_name(team_name)
        self.log(f"Selected team: {team_name}")

        # Update presence with the chosen team
        self.network.update_presence_team(team_id)

        # Fetch team members (includes offline) and refresh user list
        self._fetch_team_members()
        self._refilter_online_users()

        # Transition from lobby to normal team view
        self.panel.set_teams(self.my_teams, self.active_team_id)

    # ── Lobby Join Request Flow ─────────────────────────────────

    @Slot(str, str, str)
    def _on_request_to_join(self, team_id, team_name, admin_id):
        """User clicked 'Join' on a team in the lobby."""
        def _do_request():
            self._ensure_presence_connected()
            result = supabase_client.submit_join_request(team_id, self.user_id)
            if not result:
                self.log_signal.emit("Failed to submit join request")
                self._join_request_failed_signal.emit("Could not submit request. Try again.")
                return

            request_id = result[0]["id"] if isinstance(result, list) and result else ""
            if not request_id:
                self.log_signal.emit("Join request returned no ID")
                return

            self.log_signal.emit(f"Submitted join request for '{team_name}'")

            # Send JOIN_REQUEST via relay to admin
            self.network.send_presence_message({
                "action": "JOIN_REQUEST",
                "team_id": team_id,
                "admin_id": admin_id,
                "requester_name": self.display_name,
                "request_id": request_id,
            })

            self._join_pending_signal.emit(team_name)

        threading.Thread(target=_do_request, daemon=True).start()

    @Slot(str, str, str, str)
    def _show_join_request(self, request_id, team_id, requester_name, requester_id):
        """Admin received a join request — show notification banner."""
        self._pending_join_requests[request_id] = {
            "team_id": team_id,
            "requester_id": requester_id,
            "requester_name": requester_name,
        }
        self.log(f"Join request from {requester_name}")
        # Show banner and set requester context AFTER show_join_request
        # (show_join_request resets _active_join_requester_id to None)
        def _show():
            self.panel.show_join_request(request_id, requester_name)
            self.panel._active_join_requester_id = requester_id
        QTimer.singleShot(0, _show)

    @Slot(str)
    def _on_approve_join(self, request_id):
        """Admin clicked Accept on a join request."""
        ctx = self._pending_join_requests.pop(request_id, {})
        team_id = ctx.get("team_id", self.active_team_id)
        requester_id = ctx.get("requester_id", "")
        requester_name = ctx.get("requester_name", "Unknown")

        def _do_approve():
            result = supabase_client.approve_join_request(
                request_id, team_id, requester_id, self.user_id)
            if result:
                self.log_signal.emit(f"Approved {requester_name}")
            else:
                self.log_signal.emit(f"Failed to approve {requester_name}")

            # Notify requester via relay
            self.network.send_presence_message({
                "action": "JOIN_RESPONSE",
                "request_id": request_id,
                "approved": True,
                "requester_id": requester_id,
            })

        threading.Thread(target=_do_approve, daemon=True).start()
        self.panel.hide_join_request()

    @Slot(str, str)
    def _on_decline_join(self, request_id, requester_id):
        """Admin clicked Decline on a join request."""
        ctx = self._pending_join_requests.pop(request_id, {})
        requester_id = requester_id or ctx.get("requester_id", "")

        def _do_decline():
            supabase_client.decline_join_request(request_id, self.user_id)
            self.log_signal.emit("Join request declined")

            # Notify requester via relay
            self.network.send_presence_message({
                "action": "JOIN_RESPONSE",
                "request_id": request_id,
                "approved": False,
                "requester_id": requester_id,
            })

        threading.Thread(target=_do_decline, daemon=True).start()
        self.panel.hide_join_request()

    @Slot(str, bool)
    def _handle_join_response(self, request_id, approved):
        """Requester received a response from the admin."""
        if approved:
            self.log("Join request approved! Loading teams...")
            # Reload teams from Supabase after short delay
            def _reload():
                time.sleep(0.5)  # Let Supabase catch up
                teams = supabase_client.get_my_teams(self.user_id)
                self.my_teams = teams or []
                if self.my_teams:
                    # Select the most recently joined team (last in list)
                    self.active_team_id = self.my_teams[-1]["id"]
                    self.active_team_name = self.my_teams[-1]["name"]
                    set_active_team(self.active_team_id)
                    set_active_team_name(self.active_team_name)
                    self.network.update_presence_team(self.active_team_id)
                # Transition directly to team view (not back to lobby)
                self._switch_to_team_signal.emit()
            threading.Thread(target=_reload, daemon=True).start()
        else:
            self.log("Join request was declined.")
            QTimer.singleShot(0, lambda: self.panel.show_join_declined())

    @Slot(str)
    def _handle_join_request_failed(self, reason):
        """Join request couldn't be routed (admin offline, etc.)."""
        self.log(f"Join request failed: {reason}")
        QTimer.singleShot(0, lambda: self.panel.show_join_request_failed(reason))

    def _on_leave_team(self):
        """User wants to leave the current team."""
        if not self.active_team_id:
            return
        team_id = self.active_team_id
        team_name = self.active_team_name

        def _do_leave():
            supabase_client.leave_team(team_id, self.user_id)
            self.log_signal.emit(f"Left team: {team_name}")
            # Remove from local list
            self.my_teams = [t for t in self.my_teams if t["id"] != team_id]
            if self.my_teams:
                # Switch to the first remaining team
                self.active_team_id = self.my_teams[0]["id"]
                self.active_team_name = self.my_teams[0]["name"]
                set_active_team(self.active_team_id)
                set_active_team_name(self.active_team_name)
                self.network.update_presence_team(self.active_team_id)
            else:
                # No teams left — clear state
                self.active_team_id = ""
                self.active_team_name = ""
                set_active_team("")
                set_active_team_name("")
            # Update UI on main thread
            self._switch_to_team_signal.emit()
        threading.Thread(target=_do_leave, daemon=True).start()

    def _quit(self):
        self._cleanup_messages()
        self.log("Shutting down...")
        # Stop audio threads FIRST so PortAudio callbacks aren't
        # running when Python tears down C extensions (cffi crash fix)
        self.audio.stop_streaming()
        self.audio.stop_listening()
        # Stop radio player
        self.panel.stop_radio()
        self.hotkey.stop()
        self.discovery.close()
        # Disconnect presence first so relay broadcasts our departure
        self.network.disconnect_presence()
        self.network.close()
        # Stop WebSocket bridge
        if hasattr(self, 'deck_ws'):
            self.deck_ws.stop()
        self.tray.setVisible(False)
        QApplication.quit()


def main():
    # Enable consistent scaling across different monitor DPIs.
    # Must be set before QApplication is created.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # Keep running with just tray icon
    # Load and set Focal as the global app font
    from PySide6.QtGui import QFont

    from floating_panel import FONT_FAMILY, _load_fonts
    _load_fonts()
    app.setFont(QFont(FONT_FAMILY, 13))
    intercom = IntercomApp()
    app.exec()
    # Use os._exit to skip Python finalization — daemon threads
    # (PortAudio/cffi) may still be winding down and would SIGSEGV
    # if Py_FinalizeEx tears down C extensions under them.
    # All real cleanup is already done in _quit() before we get here.
    os._exit(0)


if __name__ == "__main__":
    main()
