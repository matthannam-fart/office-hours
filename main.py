"""
main.py — Office Hours Menu Bar App
System tray app with floating panel UI.
"""
import sys
import os
import threading
import time
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtCore import Qt, QTimer, Signal, Slot, QObject, QPoint
from PySide6.QtGui import QAction

from config import (TCP_PORT, UDP_PORT, BUFFER_SIZE, SAMPLE_RATE, CHANNELS, CHUNK_SIZE,
                     DTYPE, RELAY_HOST, RELAY_PORT, RELAY_TLS, RELAY_CA_CERT,
                     MAX_FILE_SIZE, MAX_FRAME_SIZE, APP_NAME, LOG_LEVEL, log)
from network_manager import NetworkManager
from audio_manager import AudioManager
from stream_deck_manager import StreamDeckHandler
from discovery_manager import DiscoveryManager
from user_settings import get_display_name, set_display_name, get_user_id, get_ptt_hotkey, _config_dir
from hotkey_manager import HotkeyManager
from floating_panel import FloatingPanel, create_oh_icon
from ui_constants import COLORS

# Mock for systems without Stream Deck
class MockDeck:
    def update_key_color(self, k, r, g, b, l=""): pass
    def update_key_image(self, key, text="", color=(0,0,0), render_oh=False):
        pass
    def close(self): pass

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

    MODE_GREEN = "GREEN"
    MODE_YELLOW = "YELLOW"
    MODE_RED = "RED"
    MODE_OPEN = "OPEN"
    MODE_LABELS = {"GREEN": "Available", "YELLOW": "Busy", "RED": "DND", "OPEN": "Open"}
    MODE_COLORS = {"GREEN": "#4caf50", "YELLOW": "#ffb300", "RED": "#e53935", "OPEN": "#9c27b0"}

    def __init__(self):
        super().__init__()

        # State
        self.mode = self.MODE_GREEN
        self.remote_mode = self.MODE_GREEN
        self.peer_ip = "127.0.0.1"
        self.has_message = False
        self.is_flashing = False
        self.flash_state = False
        self.incoming_message_path = None
        self.pending_connection = False
        self.peer_talking = False
        self.online_users = {}
        self.pending_room = None       # Internal relay session ID
        self.pending_from_id = None
        self.active_room_code = None   # Internal relay session ID for active call
        self._pre_call_mode = None     # Mode before entering a call (restored on disconnect)


        # User identity
        self.display_name = get_display_name()
        self.user_id = get_user_id()

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

        try:
            self.deck = StreamDeckHandler(self.handle_deck_input)
            self.deck.update_key_image(0, render_oh=True)
        except Exception as e:
            self.log(f"Stream Deck: Not connected ({e})")
            self.deck = MockDeck()

        # Global PTT Hotkey
        ptt_key = get_ptt_hotkey()
        self.hotkey = HotkeyManager(
            on_press=self._hotkey_talk_press,
            on_release=self._hotkey_talk_release,
            key_name=ptt_key,
            log_callback=self.log_signal.emit,
        )
        self.hotkey.start()

        # Discovery
        self.discovery = DiscoveryManager(self.on_peer_found, self.on_peer_lost)
        self.peer_map = {}

        # ── System Tray ────────────────────────────────────────
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(create_oh_icon(COLORS['GREEN']))
        self.tray.setToolTip("Office Hours")
        self.tray.activated.connect(self._on_tray_click)
        self.tray.setVisible(True)

        # ── Floating Panel ─────────────────────────────────────
        self.panel = FloatingPanel()
        self._connect_panel_signals()

        # Set initial state
        self.panel.set_mode(self.mode)
        self.panel.set_connection(False)

        # Prompt for name on first launch
        if not self.display_name:
            self._prompt_for_name()

        self.panel.set_display_name(self.display_name or "Office Hours")

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
        self.mic_level_signal.connect(self.panel.set_mic_level)
        self.speaker_level_signal.connect(self.panel.set_speaker_level)

        self.update_deck_display()
        self.log("System Ready. Scanning for peers...")

        # Auto-connect to presence if relay host is configured
        if RELAY_HOST and self.display_name:
            threading.Thread(target=self._auto_connect_presence, daemon=True).start()

    # ── Panel Signal Wiring ───────────────────────────────────────
    def _connect_panel_signals(self):
        self.panel.mode_cycle_requested.connect(self.cycle_mode)
        self.panel.open_toggled.connect(self._on_open_toggle)
        self.panel.ptt_pressed.connect(self.on_talk_press)
        self.panel.ptt_released.connect(self.on_talk_release)
        self.panel.call_user_requested.connect(self._on_call_user)
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

    # ── Tray Icon ─────────────────────────────────────────────────
    def _on_tray_click(self, reason):
        if reason == QSystemTrayIcon.Trigger:
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
                    # Windows: tray geometry often returns (0,0,0,0)
                    # Position at bottom-right of available screen
                    import sys
                    screen = QApplication.primaryScreen().availableGeometry()
                    if sys.platform == 'win32':
                        x = screen.right() - self.panel.width() - 8
                        y = screen.bottom() - self.panel.height() - 8
                    else:
                        x = screen.right() - self.panel.width() - 8
                        y = screen.top() + 30
                    self.panel.move(x, y)
                    self.panel.show()
                    self.panel.raise_()
                    self.panel.activateWindow()

    def _update_tray_icon(self):
        color = COLORS.get(self.mode, COLORS['GREEN'])
        self.tray.setIcon(create_oh_icon(color))

    # ── Open Toggle ───────────────────────────────────────────────
    def _on_open_toggle(self, is_on):
        if is_on:
            old_mode = self.mode
            self.mode = self.MODE_OPEN
            self.panel.set_open_line(True)
            self.panel.set_mode(self.MODE_OPEN)
            self.tray.setIcon(create_oh_icon(COLORS['OPEN']))
            if self.network.connected:
                self.audio.start_streaming()
            self.send_status()
            self.network.update_presence_mode(self.mode)
        else:
            # Restore to GREEN (default when turning off open)
            self.mode = self.MODE_GREEN
            self.audio.stop_streaming()
            self.panel.set_open_line(False)
            self.panel.set_mode(self.MODE_GREEN)
            self._update_tray_icon()
            self.send_status()
            self.network.update_presence_mode(self.mode)
        self.update_deck_display()
        self._update_ptt_for_mode()

    # ── Call User ─────────────────────────────────────────────────
    def _on_call_user(self, user_id):
        # Check LAN peers first (user_id is the Zeroconf service name)
        if user_id in self.peer_map:
            lan_ip = self.peer_map[user_id]
            # Extract friendly name
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

        # Fall back to relay/presence users
        target_name = self.online_users.get(user_id, {}).get("name", "Unknown")
        self.log(f"Calling {target_name}...")
        self._calling_user_id = user_id
        self.panel.show_outgoing(target_name)

        target_mode = self.online_users.get(user_id, {}).get("mode", "GREEN")
        target_room = self.online_users.get(user_id, {}).get("room", "")

        # If target is BUSY and in a call, join their session (conference)
        if target_mode == "BUSY" and target_room:
            self.log(f"Joining {target_name}'s call...")
            threading.Thread(
                target=self._join_relay_room, args=(target_room, "joiner"), daemon=True
            ).start()
            return

        lan_ip = self._find_lan_ip(target_name)
        if lan_ip:
            self.log(f"Trying direct connection...")
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
            self.log(f"Connected — sending call request...")
            # Send a connection request — callee will show accept/decline
            self.network.send_control("CONNECTION_REQUEST", {
                "name": self.network.display_name or "Unknown"
            })
            # Keep showing "Calling..." — wait for CONNECTION_ACCEPTED
        else:
            self.log(f"Direct connection failed, trying relay...")
            if hasattr(self, '_calling_user_id'):
                self.network.connect_to_user(self._calling_user_id)

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
            self._set_busy()
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
        self.panel.show_incoming(requester_name)
        # Store for accept/decline
        self.pending_from_id = ip

        # Show the panel if hidden
        if not self.panel.isVisible():
            geo = self.tray.geometry()
            self.panel.show_at(QPoint(geo.center().x(), geo.bottom()))

    def _handle_connection_response(self, accepted):
        """Handle response after our connection request was accepted/rejected."""
        self.panel.hide_outgoing()
        if accepted:
            # Get peer name for display
            peer_name = "Peer"
            if hasattr(self, '_calling_user_id') and self._calling_user_id in self.online_users:
                peer_name = self.online_users[self._calling_user_id].get("name", "Peer")
            self.panel.set_connection(True, peer_name)
            self._start_open_line_if_ready()
            self._set_busy()
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

        self.audio.stop_streaming()  # Stop any active stream (PTT or open line)
        self._clear_busy()
        self.peer_talking = False
        self.network.disconnect()
        self.panel.set_connection(False)
        self.panel.hide_call()

        self.log("Disconnected.")

    def _set_busy(self):
        if self._pre_call_mode is None:
            self._pre_call_mode = self.mode  # Save mode before call
        self.network.update_presence_mode("BUSY", self.active_room_code or "")

    def _clear_busy(self):
        self.active_room_code = None
        # Restore pre-call mode
        if self._pre_call_mode is not None:
            self.mode = self._pre_call_mode
            self._pre_call_mode = None
        self.panel.set_mode(self.mode)
        self._update_tray_icon()
        self.network.update_presence_mode(self.mode)

    def _start_open_line_if_ready(self):
        if self.mode == self.MODE_OPEN and self.network.connected:
            self.audio.start_streaming()
            self.log("Open line active — streaming...")

    # ── Presence Methods ──────────────────────────────────────────

    def _prompt_for_name(self):
        from PySide6.QtWidgets import QInputDialog
        dialog = QInputDialog()
        dialog.setWindowTitle("Welcome to Office Hours")
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
        try:
            success = self.network.connect_presence(
                RELAY_HOST, RELAY_PORT, self.display_name, self.user_id, self.mode
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
            self.log_signal.emit(f"Connecting...")
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

    def _join_relay_room(self, room_code, role):
        """Connect to relay for an active call. Room code is internal — never shown to user."""
        success = self.network.join_room(RELAY_HOST, room_code, RELAY_PORT)
        if success:
            self.active_room_code = room_code  # Internal tracking only

            # Determine peer name for UI
            if role == "creator":
                target_name = "Peer"
                if hasattr(self, '_calling_user_id') and self._calling_user_id in self.online_users:
                    target_name = self.online_users[self._calling_user_id].get("name", "Peer")
                self.call_connected_signal.emit(target_name)
            elif role == "joiner":
                caller_name = "Peer"
                if hasattr(self, 'pending_from_id') and self.pending_from_id in self.online_users:
                    caller_name = self.online_users[self.pending_from_id].get("name", "Peer")
                self.call_connected_signal.emit(caller_name)

            self._start_open_line_if_ready()
            self._set_busy()
        else:
            self.log_signal.emit("Could not connect to peer.")
            self.panel.set_connection(False)

    @Slot(str)
    def _on_call_connected(self, peer_name):
        """Called on main thread when call is established."""
        self.panel.hide_outgoing()
        self.panel.show_call(peer_name)
        self.panel.set_connection(True, peer_name)


    @Slot(list)
    def _update_online_users(self, users):
        """Update the panel user list from presence data."""
        self.online_users = {}
        panel_users = []

        for user in users:
            uid = user.get("user_id", "")
            name = user.get("name", "Unknown")
            mode = user.get("mode", "GREEN")
            self.online_users[uid] = {"name": name, "mode": mode, "room": user.get("room", "")}
            panel_users.append({
                'id': uid,
                'name': name,
                'mode': mode,
                'has_message': False  # TODO: track per-user messages
            })

        self.panel.set_users(panel_users)

    @Slot(str, str, str)
    def _show_presence_request(self, from_name, from_id, room_code):
        """Show incoming call via presence."""
        self.log(f"Incoming call from {from_name} (id={from_id}, room={room_code!r})")
        self.pending_from_id = from_id
        self.pending_room = room_code  # Internal — not shown to user
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
            # Format: "Office Hours (hostname)._talkback._tcp.local."
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
        self.panel.set_users(panel_users)

    def log(self, msg):
        log.info(msg)

    # ── Button / Deck Logic ───────────────────────────────────────

    def handle_deck_input(self, key, state):
        if key == 0:
            if state:
                self.on_talk_press()
            else:
                self.on_talk_release()
        elif key == 1:
            if state:
                self.on_answer()
        elif key == 2:
            if state:
                self.cycle_mode()

    def on_talk_press(self):
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

        if self.remote_mode in (self.MODE_GREEN, self.MODE_OPEN):
            self.log("Streaming Audio...")
            self.audio.start_streaming()
            self.deck.update_key_color(0, 255, 0, 0, "LIVE")
        elif self.remote_mode == self.MODE_YELLOW:
            self.log("Recording Message...")
            self.audio.start_recording_message()
            self.deck.update_key_color(0, 255, 255, 0, "REC")
        elif self.remote_mode == self.MODE_RED:
            self.log("Peer is unavailable (DND).")

    def on_talk_release(self):
        self.audio.stop_streaming()
        self.network.send_control("TALK_STOP", {})
        self.panel.set_ptt_active(False)

        if self.audio.recording:
            filename = self.audio.stop_recording_message()
            if filename:
                self.log(f"Sending Message ({filename})...")
                self.network.send_file(filename)

        self.update_deck_display()

    def on_answer(self):
        if self.has_message and self.incoming_message_path:
            self.log("Playing Message...")
            self.has_message = False
            self.is_flashing = False
            self.update_deck_display()
            self.audio.play_file(self.incoming_message_path)

    def _on_play_message(self):
        """Play voicemail from the panel banner."""
        if self.has_message and self.incoming_message_path:
            self.log("Playing Message...")
            self.has_message = False
            self.is_flashing = False
            self.update_deck_display()
            self.panel.hide_message()
            self.audio.play_file(self.incoming_message_path)

    def cycle_mode(self):
        old_mode = self.mode

        if self.mode == self.MODE_GREEN:
            self.mode = self.MODE_YELLOW
        elif self.mode == self.MODE_YELLOW:
            self.mode = self.MODE_RED
        elif self.mode == self.MODE_RED:
            self.mode = self.MODE_GREEN
        else:  # OPEN — cycling goes back to GREEN
            self.mode = self.MODE_GREEN

        # Handle streaming transitions for OPEN mode
        if old_mode == self.MODE_OPEN and self.mode != self.MODE_OPEN:
            self.audio.stop_streaming()
            self.panel.set_open_line(False)

        # Update UI
        self.panel.set_mode(self.mode)
        self._update_tray_icon()

        label = self.MODE_LABELS.get(self.mode, self.mode)
        self.log(f"Mode: {label}")
        self.send_status()
        self.network.update_presence_mode(self.mode)
        self.update_deck_display()
        self._update_ptt_for_mode()

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

    # ── Network Callbacks ─────────────────────────────────────────

    def handle_audio_stream(self, data):
        if self.mode in (self.MODE_GREEN, self.MODE_OPEN):
            self.audio.play_audio_chunk(data)

    def handle_network_message(self, msg):
        msg_type = msg.get("type")
        payload = msg.get("payload")

        if msg_type == "STATUS":
            self.remote_mode = payload.get("mode")
            self.log_signal.emit(f"Remote is now {self.remote_mode}")

        elif msg_type == "TALK_START":
            self.peer_talking = True
            self.log_signal.emit("Peer is talking...")

        elif msg_type == "TALK_STOP":
            self.peer_talking = False

        elif msg_type == "PEER_CONNECTED":
            ip = payload.get("ip", "unknown")
            self.peer_ip = ip
            self.log_signal.emit(f"Direct connection from {ip}")

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
                self.log_signal.emit(f"Ignoring direct CONNECTION_REQUEST — relay call already pending")

        elif msg_type == "CONNECTION_ACCEPTED":
            self.log_signal.emit("Call accepted!")
            caller_name = payload.get("name", "Peer")
            self.call_connected_signal.emit(caller_name)
            self._start_open_line_if_ready()
            self._set_busy()


        elif msg_type == "CALL_ENDED":
            # Remote peer ended the call — disconnect our side and restore mode
            self.log_signal.emit("Call ended by peer.")
            self.audio.stop_streaming()
            self._clear_busy()
            self.peer_talking = False
            self.network.disconnect()
            self.panel.set_connection(False)
            self.panel.hide_call()

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
                fn = os.path.join(_config_dir(), "incoming_message.wav")
                with open(fn, 'wb') as f:
                    f.write(data)
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
        if not self.deck: return

        COLOR_GREEN = (0, 100, 0)
        COLOR_YELLOW = (200, 180, 0)
        COLOR_RED = (50, 0, 0)
        COLOR_OFF = (0, 0, 0)

        if self.is_flashing:
            if self.flash_state:
                self.deck.update_key_image(0, text="READ", color=COLOR_YELLOW)
            else:
                self.deck.update_key_image(0, text="MSG", color=COLOR_RED)
        else:
            bg = COLOR_OFF
            if self.mode == self.MODE_GREEN: bg = COLOR_GREEN
            elif self.mode == self.MODE_YELLOW: bg = COLOR_YELLOW
            elif self.mode == self.MODE_RED: bg = COLOR_RED
            self.deck.update_key_image(0, render_oh=True, color=bg)

        if self.mode == self.MODE_GREEN:
            self.deck.update_key_image(1, text="TALK", color=COLOR_GREEN)
            self.deck.update_key_image(2, text="DND", color=(50, 50, 50))
        elif self.mode == self.MODE_YELLOW:
            self.deck.update_key_image(1, text="REC", color=COLOR_YELLOW)
            self.deck.update_key_image(2, text="BACK", color=(50, 50, 50))
        elif self.mode == self.MODE_RED:
            self.deck.update_key_image(1, text="--", color=COLOR_RED)
            self.deck.update_key_image(2, text="OPEN", color=COLOR_GREEN)

    def flash_loop(self):
        if self.is_flashing and self.has_message:
            self.flash_state = not self.flash_state
            if self.flash_state:
                self.deck.update_key_color(1, 255, 255, 0, "MSG!")
            else:
                self.deck.update_key_color(1, 0, 0, 0, "")
        elif not self.has_message:
            self.deck.update_key_color(1, 0, 0, 0, "")

    def _cleanup_messages(self):
        cfg_dir = _config_dir()
        for fname in ("outgoing_message.wav", "incoming_message.wav"):
            path = os.path.join(cfg_dir, fname)
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as e:
                print(f"Could not delete {fname}: {e}")

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
        # Re-register Zeroconf with updated name
        try:
            self.discovery.close()
            self.discovery.register_service()
            self.discovery.start_browsing()
        except Exception as e:
            self.log(f"Could not re-register service: {e}")

    def _quit(self):
        self._cleanup_messages()
        self.hotkey.stop()
        self.discovery.close()
        self.network.close()
        self.tray.setVisible(False)
        QApplication.quit()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # Keep running with just tray icon
    # Load and set Focal as the global app font
    from PySide6.QtGui import QFont
    from floating_panel import _load_fonts, FONT_FAMILY
    _load_fonts()
    app.setFont(QFont(FONT_FAMILY, 13))
    intercom = IntercomApp()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
