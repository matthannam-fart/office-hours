import sys
import os
import threading
import time
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QLabel, QComboBox, QLineEdit, QPushButton,
                               QTextEdit, QGroupBox, QTabWidget, QMessageBox,
                               QInputDialog, QListWidget, QListWidgetItem, QCheckBox)
from PySide6.QtCore import Qt, QTimer, Signal, Slot, QObject

from config import *
from network_manager import NetworkManager
from audio_manager import AudioManager
from stream_deck_manager import StreamDeckHandler
from discovery_manager import DiscoveryManager
from user_settings import get_display_name, set_display_name, get_user_id

# Mock for systems without Stream Deck
class MockDeck:
    def update_key_color(self, k, r, g, b, l=""): pass
    def update_key_image(self, key, text="", color=(0,0,0), render_oh=False):
        pass
    def close(self): pass

class IntercomApp(QMainWindow):
    # Signals to update UI from other threads
    log_signal = Signal(str)
    status_signal = Signal(str)
    peer_found_signal = Signal(str, str) # name, ip
    peer_lost_signal = Signal(str)       # name
    relay_status_signal = Signal(str)    # relay connection status
    connection_request_signal = Signal(str, str)  # requester_name, ip
    connection_response_signal = Signal(bool)     # accepted or rejected
    presence_update_signal = Signal(list)          # list of online users
    presence_request_signal = Signal(str, str, str) # from_name, from_id, room_code
    
    MODE_GREEN = "GREEN"
    MODE_YELLOW = "YELLOW"
    MODE_RED = "RED"
    MODE_OPEN = "OPEN"
    MODE_LABELS = {"GREEN": "Available", "YELLOW": "Busy", "RED": "DND", "OPEN": "Open"}
    MODE_COLORS = {"GREEN": "#4caf50", "YELLOW": "#ffb300", "RED": "#e53935", "OPEN": "#9c27b0"}

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(620, 520)
        
        # Apply Style
        self.setStyleSheet("""
            QMainWindow {
                background-color: #2b2b2b;
            }
            QLabel {
                color: #e0e0e0;
                font-family: 'Segoe UI', 'Helvetica Neue', sans-serif;
            }
            QPushButton {
                background-color: #404040;
                color: white;
                border: 1px solid #555;
                padding: 8px;
                border-radius: 4px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
            QPushButton:pressed {
                background-color: #202020;
            }
            QComboBox, QLineEdit, QTextEdit {
                background-color: #1e1e1e;
                color: #e0e0e0;
                border: 1px solid #555;
                padding: 4px;
                border-radius: 4px;
            }
            QComboBox::drop-down {
                border: 0px;
            }
            QComboBox QAbstractItemView {
                background-color: #1e1e1e;
                color: #e0e0e0;
                selection-background-color: #404040;
                selection-color: white;
                border: 1px solid #555;
            }
            QGroupBox {
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 16px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                padding: 0 6px;
            }
            QTabWidget::pane {
                border: 1px solid #555;
                background-color: #2b2b2b;
            }
            QTabBar::tab {
                background-color: #404040;
                color: #e0e0e0;
                padding: 8px 16px;
                border: 1px solid #555;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background-color: #2b2b2b;
                color: white;
            }
        """)

        # State
        self.mode = self.MODE_GREEN
        self.remote_mode = self.MODE_GREEN
        self.peer_ip = "127.0.0.1"
        self.has_message = False
        self.is_flashing = False
        self.flash_state = False
        self.incoming_message_path = None
        self.pending_connection = False  # True while waiting for accept/reject
        self.peer_talking = False  # True when peer is push-to-talking
        self.online_users = {}  # user_id -> {name, mode}
        self.pending_room = None  # room code from pending connection request
        self.pending_from_id = None  # user_id of pending requester

        # User identity
        self.display_name = get_display_name()
        self.user_id = get_user_id()

        # Managers
        self.network = NetworkManager(self.handle_network_message, log_callback=self.log_signal.emit)
        self.audio = AudioManager(self.network, log_callback=self.log_signal.emit)
        # Fix circular dependency
        self.network.audio_callback = self.handle_audio_stream
        self.network.presence_callback = self.handle_presence_message
        self.audio.start_listening()
        
        try:
            self.deck = StreamDeckHandler(self.handle_deck_input)
            self.deck.update_key_image(0, render_oh=True)
        except Exception as e:
            self.log_signal.emit(f"Stream Deck: Not connected ({e})")
            self.deck = MockDeck()

        # Discovery
        self.discovery = DiscoveryManager(self.on_peer_found, self.on_peer_lost)
        self.peer_map = {}

        # UI Setup
        self.init_ui()

        # Prompt for name on first launch
        if not self.display_name:
            self._prompt_for_name()

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
        self.relay_status_signal.connect(self._update_relay_status)
        self.connection_request_signal.connect(self._show_connection_request)
        self.connection_response_signal.connect(self._handle_connection_response)
        self.presence_update_signal.connect(self._update_online_users)
        self.presence_request_signal.connect(self._show_presence_request)
        
        self.update_deck_display()
        self.log("System Ready. Scanning for peers...")

        # Auto-connect to presence if relay host is configured
        if RELAY_HOST and self.display_name:
            threading.Thread(target=self._auto_connect_presence, daemon=True).start()

    def init_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)

        # ── Connection Tabs ──────────────────────────────────────
        conn_tabs = QTabWidget()

        # Tab 1: LAN Connection
        lan_tab = QWidget()
        lan_layout = QVBoxLayout(lan_tab)

        lan_row = QHBoxLayout()
        self.peer_combo = QComboBox()
        self.peer_combo.setPlaceholderText("Select Peer")
        self.peer_combo.setMinimumWidth(200)
        
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("IP or IP:TCP:UDP")
        
        connect_btn = QPushButton("Connect")
        connect_btn.clicked.connect(self.do_connect)
        
        lan_row.addWidget(QLabel("Peer:"))
        lan_row.addWidget(self.peer_combo)
        lan_row.addWidget(self.ip_input)
        lan_row.addWidget(connect_btn)
        lan_layout.addLayout(lan_row)
        conn_tabs.addTab(lan_tab, "🏠 LAN")

        # Tab 2: Remote Connection
        remote_tab = QWidget()
        remote_layout = QVBoxLayout(remote_tab)

        # Relay server row
        relay_row = QHBoxLayout()
        self.relay_host_input = QLineEdit()
        self.relay_host_input.setPlaceholderText("relay.example.com")
        if RELAY_HOST:
            self.relay_host_input.setText(RELAY_HOST)
        
        self.relay_port_input = QLineEdit()
        self.relay_port_input.setPlaceholderText(str(RELAY_PORT))
        self.relay_port_input.setMaximumWidth(70)
        
        relay_row.addWidget(QLabel("Server:"))
        relay_row.addWidget(self.relay_host_input)
        relay_row.addWidget(QLabel(":"))
        relay_row.addWidget(self.relay_port_input)
        remote_layout.addLayout(relay_row)

        # Room code row
        room_row = QHBoxLayout()
        self.room_code_input = QLineEdit()
        self.room_code_input.setPlaceholderText("Room code (e.g. OH-7X3K)")
        
        create_room_btn = QPushButton("Create Room")
        create_room_btn.clicked.connect(self.do_create_room)
        
        join_room_btn = QPushButton("Join Room")
        join_room_btn.clicked.connect(self.do_join_room)
        
        room_row.addWidget(QLabel("Room:"))
        room_row.addWidget(self.room_code_input)
        room_row.addWidget(create_room_btn)
        room_row.addWidget(join_room_btn)
        remote_layout.addLayout(room_row)
        
        # Relay status
        self.relay_status_label = QLabel("")
        self.relay_status_label.setStyleSheet("color: #888; font-style: italic;")
        remote_layout.addWidget(self.relay_status_label)

        conn_tabs.addTab(remote_tab, "🌐 Remote")

        # Tab 3: Online Users
        users_tab = QWidget()
        users_layout = QVBoxLayout(users_tab)

        self.online_users_list = QListWidget()
        self.online_users_list.setStyleSheet("""
            QListWidget {
                background-color: #1e1e1e;
                border: 1px solid #444;
                border-radius: 4px;
                font-size: 14px;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #333;
            }
            QListWidget::item:selected {
                background-color: #3a3a5a;
            }
        """)
        users_layout.addWidget(self.online_users_list)

        users_btn_row = QHBoxLayout()
        self.connect_user_btn = QPushButton("Connect to User")
        self.connect_user_btn.clicked.connect(self.do_connect_to_user)
        users_btn_row.addWidget(self.connect_user_btn)
        users_layout.addLayout(users_btn_row)

        self.presence_status_label = QLabel("")
        self.presence_status_label.setStyleSheet("color: #888; font-style: italic; font-size: 11px;")
        users_layout.addWidget(self.presence_status_label)

        conn_tabs.addTab(users_tab, "👥 Online")

        layout.addWidget(conn_tabs)

        # ── Audio Device Section ─────────────────────────────────
        audio_layout = QHBoxLayout()
        
        self.input_combo = QComboBox()
        self.input_combo.setMinimumWidth(150)
        self.input_combo.currentIndexChanged.connect(self.update_audio_devices)
        audio_layout.addWidget(QLabel("Mic:"))
        audio_layout.addWidget(self.input_combo)

        self.output_combo = QComboBox()
        self.output_combo.setMinimumWidth(150)
        self.output_combo.currentIndexChanged.connect(self.update_audio_devices)
        audio_layout.addWidget(QLabel("Speaker:"))
        audio_layout.addWidget(self.output_combo)
        
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_devices)
        audio_layout.addWidget(refresh_btn)
        
        layout.addLayout(audio_layout)
        self.refresh_devices()

        # Status
        self.status_label = QLabel(f"Mode: {self.mode}")
        self.status_label.setStyleSheet("font-size: 18px; font-weight: bold; color: green;")
        layout.addWidget(self.status_label)

        # Connection info
        self.conn_info_label = QLabel("")
        self.conn_info_label.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self.conn_info_label)

        # Logs
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        layout.addWidget(self.log_box)

        # Controls
        ctrl_layout = QHBoxLayout()
        self.btn_talk = QPushButton("HOLD TO TALK")
        self.btn_talk.pressed.connect(lambda: self.handle_deck_input(0, True))
        self.btn_talk.released.connect(lambda: self.handle_deck_input(0, False))
        
        self.btn_answer = QPushButton("ANSWER MESSAGE")
        self.btn_answer.clicked.connect(lambda: self.handle_deck_input(1, True))
        
        self.btn_mode = QPushButton("CYCLE MODE")
        self.btn_mode.setStyleSheet(
            "QPushButton { border: 1px solid #333; border-radius: 12px; "
            "background: #1e1e1e; padding: 6px 12px; }"
            "QPushButton:hover { border-color: #4a4a4a; background: #252525; }"
        )
        self.btn_mode.clicked.connect(lambda: self.handle_deck_input(2, True))

        self.btn_disconnect = QPushButton("DISCONNECT")
        self.btn_disconnect.clicked.connect(self.do_disconnect)
        self.btn_disconnect.setStyleSheet(
            "QPushButton { background-color: #5a2020; } "
            "QPushButton:hover { background-color: #6a3030; }"
        )
        
        ctrl_layout.addWidget(self.btn_talk)
        ctrl_layout.addWidget(self.btn_answer)
        ctrl_layout.addWidget(self.btn_mode)
        ctrl_layout.addWidget(self.btn_disconnect)
        layout.addLayout(ctrl_layout)

        # Message notification indicator
        self.message_indicator = QLabel("")
        self.message_indicator.setStyleSheet("font-size: 13px; font-weight: bold; color: #ff6600; padding: 2px;")
        self.message_indicator.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.message_indicator)

        self.setCentralWidget(central)

    def refresh_devices(self):
        current_input = self.input_combo.currentData()
        current_output = self.output_combo.currentData()

        self.input_combo.blockSignals(True)
        self.output_combo.blockSignals(True)
        
        self.input_combo.clear()
        self.output_combo.clear()
        
        try:
            devices = self.audio.list_devices()
        except Exception as e:
            self.log(f"Error listing devices: {e}")
            devices = []
        
        self.input_combo.addItem("System Default", None)
        self.output_combo.addItem("System Default", None)
        
        for i, d in enumerate(devices):
            name = d.get('name', f"Device {i}")
            if d.get('max_input_channels', 0) > 0:
                self.input_combo.addItem(f"{name}", i)
            if d.get('max_output_channels', 0) > 0:
                self.output_combo.addItem(f"{name}", i)
                
        if current_input is not None:
            idx = self.input_combo.findData(current_input)
            if idx >= 0: self.input_combo.setCurrentIndex(idx)
        
        if current_output is not None:
            idx = self.output_combo.findData(current_output)
            if idx >= 0: self.output_combo.setCurrentIndex(idx)

        self.input_combo.blockSignals(False)
        self.output_combo.blockSignals(False)
        self.update_audio_devices()

    def update_audio_devices(self):
        in_idx = self.input_combo.currentData()
        out_idx = self.output_combo.currentData()
        self.audio.set_input_device(in_idx)
        self.audio.set_output_device(out_idx)

    # ── Connection Actions ───────────────────────────────────────

    def do_connect(self):
        """LAN / direct IP connect — sends a connection request"""
        if self.network.connected:
            self.log("Already connected.")
            return

        raw = self.ip_input.text().strip()
        ip = None
        peer_tcp = None
        peer_udp = None

        if raw:
            parts = raw.split(':')
            ip = parts[0]
            if len(parts) == 3:
                try:
                    peer_tcp = int(parts[1])
                    peer_udp = int(parts[2])
                except ValueError:
                    self.log("Invalid port format. Use IP:TCP_PORT:UDP_PORT")
                    return
        else:
            idx = self.peer_combo.currentIndex()
            if idx >= 0:
                name = self.peer_combo.itemText(idx)
                ip = self.peer_map.get(name)

        if not ip:
            self.log("Please select a peer or enter an IP.")
            return

        self.peer_ip = ip
        self.network.set_peer_ip(ip)
        if peer_tcp and peer_udp:
            self.network.set_peer_ports(peer_tcp, peer_udp)
            self.log(f"Peer ports set to TCP:{peer_tcp} UDP:{peer_udp}")
        
        # Establish TCP but don't mark as fully connected yet
        if self.network.connect(ip):
            import socket
            my_name = f"{APP_NAME} ({socket.gethostname()})"
            self.network.send_control("CONNECTION_REQUEST", {"name": my_name})
            self.pending_connection = True
            self.log(f"Connection request sent to {ip}...")
            self.conn_info_label.setText("Waiting for peer to accept...")
            self.conn_info_label.setStyleSheet("color: #D4AF37; font-size: 12px;")
        else:
            self.log("Connection initialization failed.")

    def do_create_room(self):
        """Create a new relay room"""
        relay_host = self.relay_host_input.text().strip()
        if not relay_host:
            self.log("Enter a relay server address first.")
            return

        port_text = self.relay_port_input.text().strip()
        relay_port = int(port_text) if port_text else RELAY_PORT

        self.log(f"Connecting to relay {relay_host}:{relay_port}...")
        self.relay_status_label.setText("Creating room...")
        self.relay_status_label.setStyleSheet("color: #D4AF37; font-style: italic;")

        def _create():
            room_code = self.network.create_room(relay_host, relay_port)
            if room_code:
                self.relay_status_signal.emit(f"Room: {room_code} — Waiting for peer...")
                # Update room code field so user can share it
                # (we'll update via signal when paired)
                self._check_relay_connected(room_code)
            else:
                self.relay_status_signal.emit("Failed to create room")

        threading.Thread(target=_create, daemon=True).start()

    def do_join_room(self):
        """Join an existing relay room"""
        relay_host = self.relay_host_input.text().strip()
        room_code = self.room_code_input.text().strip().upper()
        
        if not relay_host:
            self.log("Enter a relay server address first.")
            return
        if not room_code:
            self.log("Enter a room code to join.")
            return

        port_text = self.relay_port_input.text().strip()
        relay_port = int(port_text) if port_text else RELAY_PORT

        self.log(f"Joining room {room_code} via {relay_host}:{relay_port}...")
        self.relay_status_label.setText("Joining room...")
        self.relay_status_label.setStyleSheet("color: #D4AF37; font-style: italic;")

        def _join():
            success = self.network.join_room(relay_host, room_code, relay_port)
            if success:
                self.relay_status_signal.emit(f"Connected via relay — Room: {room_code}")
                self.log_signal.emit(f"Connected to peer via relay!")
                self.send_status()
            else:
                self.relay_status_signal.emit("Failed to join room")

        threading.Thread(target=_join, daemon=True).start()

    def _check_relay_connected(self, room_code):
        """Poll until relay connection is established (after CREATE_ROOM)"""
        def _poll():
            for _ in range(600):  # 5 min max
                if self.network.connected:
                    self.relay_status_signal.emit(f"Connected via relay — Room: {room_code}")
                    self.log_signal.emit("Peer connected! Ready to talk.")
                    self.send_status()
                    return
                time.sleep(0.5)
            if not self.network.connected:
                self.relay_status_signal.emit("Room timed out")
        threading.Thread(target=_poll, daemon=True).start()

    def _update_relay_status(self, text):
        """Update the relay status label and connection info (called via signal)"""
        self.relay_status_label.setText(text)
        if "Connected" in text:
            self.relay_status_label.setStyleSheet("color: #4CAF50; font-style: italic;")
            self.conn_info_label.setText(text)
            self.conn_info_label.setStyleSheet("color: #4CAF50; font-size: 12px;")
            # Show the room code in the input field for easy sharing
            if self.network.room_code:
                self.room_code_input.setText(self.network.room_code)
        elif "Waiting" in text:
            self.relay_status_label.setStyleSheet("color: #D4AF37; font-style: italic;")
            if self.network.room_code:
                self.room_code_input.setText(self.network.room_code)
        else:
            self.relay_status_label.setStyleSheet("color: #f44; font-style: italic;")

    def _show_connection_request(self, requester_name, ip):
        """Show a dialog asking the user to accept or reject an incoming connection"""
        reply = QMessageBox.question(
            self,
            "Incoming Connection",
            f"{requester_name}\nwants to connect.\n\nAccept?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        
        if reply == QMessageBox.Yes:
            self.log(f"Accepted connection from {requester_name}")
            self.network.send_control("CONNECTION_ACCEPTED", {})
            self.conn_info_label.setText(f"Connected (LAN) \u2192 {ip}")
            self.conn_info_label.setStyleSheet("color: #4CAF50; font-size: 12px;")
            self.send_status()
            self._start_open_line_if_ready()
            self._set_busy()
        else:
            self.log(f"Declined connection from {requester_name}")
            self.network.send_control("CONNECTION_REJECTED", {})
            self.network.disconnect()
            self.conn_info_label.setText("")

    def _handle_connection_response(self, accepted):
        """Handle the response after our connection request was accepted or rejected"""
        if accepted:
            self.conn_info_label.setText(f"Connected (LAN) \u2192 {self.peer_ip}")
            self.conn_info_label.setStyleSheet("color: #4CAF50; font-size: 12px;")
            self._start_open_line_if_ready()
            self._set_busy()
        else:
            self.conn_info_label.setText("Connection declined")
            self.conn_info_label.setStyleSheet("color: #f44; font-size: 12px;")

    def do_disconnect(self):
        """Disconnect from current session"""
        if self.mode == self.MODE_OPEN:
            self.audio.stop_streaming()
            self.log("Open line closed.")
        self._clear_busy()
        self.peer_talking = False
        self.network.disconnect()
        self.conn_info_label.setText("Disconnected")
        self.conn_info_label.setStyleSheet("color: #888; font-size: 12px;")
        self.relay_status_label.setText("")
        self.log("Disconnected from peer.")

    def _set_busy(self):
        """Set presence to BUSY when in a call"""
        self.network.update_presence_mode("BUSY")
        self.log("Status set to BUSY")

    def _clear_busy(self):
        """Restore presence to actual mode after a call"""
        self.network.update_presence_mode(self.mode)
        self.log(f"Status restored to {self.mode}")

    def _start_open_line_if_ready(self):
        """Start open-line streaming if in OPEN mode and connected"""
        if self.mode == self.MODE_OPEN and self.network.connected:
            self.audio.start_streaming()
            self.log_signal.emit("Open line active — streaming...")

    # ── Presence Methods ─────────────────────────────────────────

    def _prompt_for_name(self):
        """Prompt the user for a display name on first launch"""
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Welcome to Office Hours")
        dialog.setLabelText("Enter your display name:")
        dialog.setStyleSheet("""
            QInputDialog {
                background-color: #1e1e1e;
                color: #eee;
            }
            QLabel {
                color: #eee;
                font-size: 14px;
            }
            QLineEdit {
                background-color: #2b2b2b;
                color: #eee;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 6px;
                font-size: 14px;
            }
            QPushButton {
                background-color: #3a3a3a;
                color: #eee;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 6px 16px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
            }
        """)
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
            self.log(f"Using hostname as display name: {self.display_name}")

    def _auto_connect_presence(self):
        """Auto-connect to the presence server on startup"""
        time.sleep(1)  # Brief delay to let UI finish initializing
        try:
            relay_port = RELAY_PORT
            success = self.network.connect_presence(
                RELAY_HOST, relay_port, self.display_name, self.user_id, self.mode
            )
            if success:
                self.log_signal.emit(f"Connected to presence server as \"{self.display_name}\"")
            else:
                self.log_signal.emit("Could not connect to presence server")
        except Exception as e:
            self.log_signal.emit(f"Presence auto-connect failed: {e}")

    def handle_presence_message(self, msg):
        """Handle messages from the presence channel (called from network thread)"""
        msg_type = msg.get("type")

        if msg_type == "PRESENCE_UPDATE":
            users = msg.get("users", [])
            # Filter out ourselves
            filtered = [u for u in users if u.get("user_id") != self.user_id]
            self.presence_update_signal.emit(filtered)

        elif msg_type == "CONNECTION_REQUEST":
            from_name = msg.get("from_name", "Someone")
            from_id = msg.get("from_id", "")
            room_code = msg.get("room", "")
            self.presence_request_signal.emit(from_name, from_id, room_code)

        elif msg_type == "CONNECT_ROOM":
            # Server tells us to join a room (we initiated the request)
            room_code = msg.get("room", "")
            role = msg.get("role", "")
            self.log_signal.emit(f"Joining room {room_code} as {role}...")
            threading.Thread(
                target=self._join_relay_room, args=(room_code, role), daemon=True
            ).start()

        elif msg_type == "CONNECTION_REJECTED":
            self.log_signal.emit("Connection request was declined.")
            self.relay_status_signal.emit("Connection declined")

    def _join_relay_room(self, room_code, role):
        """Join a relay room for audio (called from presence flow)"""
        relay_host = RELAY_HOST
        relay_port = RELAY_PORT
        # Both creator and joiner use join_room — server pre-created the room
        success = self.network.join_room(relay_host, room_code, relay_port)
        if success:
            self.relay_status_signal.emit(f"Connected via relay (Room: {room_code})")
            self._start_open_line_if_ready()
            self._set_busy()
        else:
            self.relay_status_signal.emit("Failed to join room")

    @Slot(list)
    def _update_online_users(self, users):
        """Update the online users list widget"""
        self.online_users_list.clear()
        self.online_users = {}

        mode_icons = {"GREEN": "\U0001f7e2", "YELLOW": "\U0001f7e1", "RED": "\U0001f534", "BUSY": "\U0001f4de", "OPEN": "\U0001f7e3"}

        for user in users:
            uid = user.get("user_id", "")
            name = user.get("name", "Unknown")
            mode = user.get("mode", "GREEN")
            self.online_users[uid] = {"name": name, "mode": mode}

            icon = mode_icons.get(mode, "\u26ab")
            item = QListWidgetItem(f"{icon}  {name}")
            item.setData(256, uid)  # Store user_id in the item
            self.online_users_list.addItem(item)

        count = len(users)
        self.presence_status_label.setText(
            f"{count} user{'s' if count != 1 else ''} online" if count > 0 else "No other users online"
        )

    def do_connect_to_user(self):
        """Request connection to the selected online user"""
        item = self.online_users_list.currentItem()
        if not item:
            self.log("Select a user to connect to.")
            return

        target_id = item.data(256)
        target_name = self.online_users.get(target_id, {}).get("name", "Unknown")
        
        self.log(f"Requesting connection to {target_name}...")
        self.conn_info_label.setText(f"Requesting {target_name}...")
        self.conn_info_label.setStyleSheet("color: #D4AF37; font-size: 12px;")
        self.network.connect_to_user(target_id)

    @Slot(str, str, str)
    def _show_presence_request(self, from_name, from_id, room_code):
        """Show dialog for incoming connection request via presence"""
        reply = QMessageBox.question(
            self,
            "Incoming Connection",
            f"{from_name}\nwants to connect.\n\nAccept?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )

        if reply == QMessageBox.Yes:
            self.log(f"Accepted connection from {from_name}")
            self.network.accept_presence_connection(room_code, from_id)
        else:
            self.log(f"Declined connection from {from_name}")
            self.network.reject_presence_connection(from_id)

    # ── Peer Discovery ───────────────────────────────────────────

    def on_peer_found(self, name, ip):
        self.peer_found_signal.emit(name, ip)

    def on_peer_lost(self, name):
        self.peer_lost_signal.emit(name)

    def add_peer_to_ui(self, name, ip):
        if name not in self.peer_map:
            self.peer_map[name] = ip
            self.peer_combo.addItem(name)
            self.log(f"Found Peer: {name} ({ip})")

    def remove_peer_from_ui(self, name):
        if name in self.peer_map:
            del self.peer_map[name]
            index = self.peer_combo.findText(name)
            if index >= 0:
                self.peer_combo.removeItem(index)
            self.log(f"Lost Peer: {name}")

    def log(self, msg):
        self.log_box.append(f"[{time.strftime('%H:%M:%S')}] {msg}")

    # --- Logic ---

    def handle_deck_input(self, key, state):
        """Handle Button Press/Release"""
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
        # Block if not connected
        if not self.network.connected:
            self.log("Not connected to a peer.")
            return

        # Block if WE are in DND
        if self.mode == self.MODE_RED:
            self.log("You are in DND mode. Switch modes to talk.")
            return

        # Block if peer is already talking
        if self.peer_talking:
            self.log("Peer is talking — wait for them to finish.")
            return

        # Notify peer we're talking
        self.network.send_control("TALK_START", {})

        # Update button to show speaking state
        self.btn_talk.setText("SPEAKING")
        self.btn_talk.setStyleSheet(
            "QPushButton { background-color: #8b1a1a; color: #ff6666; font-weight: bold; }"
        )

        # Route based on RECEIVER's mode
        if self.remote_mode in (self.MODE_GREEN, self.MODE_OPEN):
            self.log("Streaming Audio...")
            self.audio.start_streaming()
            self.deck.update_key_color(0, 255, 0, 0, "LIVE")
        elif self.remote_mode == self.MODE_YELLOW:
            self.log("Recording Message...")
            self.audio.start_recording_message()
            self.deck.update_key_color(0, 255, 255, 0, "REC")
        elif self.remote_mode == self.MODE_RED:
            self.log("Peer is unavailable (Do Not Disturb).")
        else:
            self.log("Peer status unknown.")

    def on_talk_release(self):
        self.audio.stop_streaming()
        self.network.send_control("TALK_STOP", {})
        
        # Restore button
        if self.mode != self.MODE_OPEN:
            self.btn_talk.setText("HOLD TO TALK")
            self.btn_talk.setStyleSheet("")
        
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
            self.message_indicator.setText("")
            self.btn_answer.setStyleSheet("")
            self.update_deck_display()
            self.audio.play_file(self.incoming_message_path)

    def cycle_mode(self):
        old_mode = self.mode

        if self.mode == self.MODE_GREEN:
            self.mode = self.MODE_YELLOW
        elif self.mode == self.MODE_YELLOW:
            self.mode = self.MODE_RED
        elif self.mode == self.MODE_RED:
            self.mode = self.MODE_OPEN
        else:
            self.mode = self.MODE_GREEN

        # Handle streaming transitions for OPEN mode
        if old_mode == self.MODE_OPEN and self.mode != self.MODE_OPEN:
            self.audio.stop_streaming()
        elif self.mode == self.MODE_OPEN and self.network.connected:
            self.audio.start_streaming()
            
        label = self.MODE_LABELS.get(self.mode, self.mode)
        self.log(f"Mode: {label}")
        self.send_status()
        self.network.update_presence_mode(self.mode)
        self.update_deck_display()
        self._update_ptt_for_mode()

    def _update_ptt_for_mode(self):
        """Enable/disable PTT button based on current mode"""
        label = self.MODE_LABELS.get(self.mode, self.mode)
        color = self.MODE_COLORS.get(self.mode, "#888")
        
        # Update mode button to show current state (like wireframe pill)
        self.btn_mode.setText(f"● {label}")
        self.btn_mode.setStyleSheet(
            f"QPushButton {{ border: 1px solid #333; border-radius: 12px; "
            f"background: #1e1e1e; padding: 6px 12px; color: {color}; font-weight: bold; }}"
            f"QPushButton:hover {{ border-color: #4a4a4a; background: #252525; }}"
        )
        
        if self.mode == self.MODE_RED:
            self.btn_talk.setEnabled(False)
            self.btn_talk.setText("\u2014")
            self.btn_talk.setStyleSheet("QPushButton { opacity: 0.3; }")
        elif self.mode == self.MODE_OPEN:
            self.btn_talk.setEnabled(False)
            self.btn_talk.setText("OPEN LINE")
            self.btn_talk.setStyleSheet(
                "QPushButton { background-color: #2d1045; color: #bb86fc; font-weight: bold; }"
            )
        else:
            self.btn_talk.setEnabled(True)
            self.btn_talk.setText("HOLD TO TALK")
            self.btn_talk.setStyleSheet("")

    def send_status(self):
        self.network.send_control("STATUS", {"mode": self.mode})
        label = self.MODE_LABELS.get(self.mode, self.mode)
        color = self.MODE_COLORS.get(self.mode, "#888")
        self.status_label.setText(f"Mode: {label}")
        self.status_label.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {color};")

    # --- Network Callbacks ---

    def handle_audio_stream(self, data):
        """Callback for incoming UDP audio — play if GREEN or OPEN"""
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
            self.log_signal.emit("📡 Peer is talking...")
        
        elif msg_type == "TALK_STOP":
            self.peer_talking = False
        
        elif msg_type == "PEER_CONNECTED":
            # Inbound TCP accepted — wait for their CONNECTION_REQUEST
            ip = payload.get("ip", "unknown")
            self.peer_ip = ip
            self.log_signal.emit(f"Incoming connection from {ip}...")
        
        elif msg_type == "CONNECTION_REQUEST":
            requester_name = payload.get("name", "Unknown")
            self.log_signal.emit(f"Connection request from {requester_name}")
            # Show dialog on UI thread via signal
            self.connection_request_signal.emit(requester_name, self.peer_ip or "unknown")
        
        elif msg_type == "CONNECTION_ACCEPTED":
            self.log_signal.emit("Connection accepted!")
            self.pending_connection = False
            # Signal the UI thread to update
            self.connection_response_signal.emit(True)
            self.send_status()
        
        elif msg_type == "CONNECTION_REJECTED":
            self.log_signal.emit("Connection declined.")
            self.pending_connection = False
            self.connection_response_signal.emit(False)
            self.network.disconnect()

        elif msg_type == "FILE_HEADER":
            self.incoming_file_size = payload.get("size")
            self.log_signal.emit(f"Receiving Message ({self.incoming_file_size} bytes)...")
            
        elif msg_type == "BINARY_DATA":
            data = payload
            self.log_signal.emit(f"File Received: {len(data)} bytes")
            try:
                fn = os.path.join(os.path.dirname(os.path.abspath(__file__)), "incoming_message.wav")
                with open(fn, 'wb') as f:
                    f.write(data)
                
                self.incoming_message_path = fn
                self.has_message = True
                self.is_flashing = True
                self.update_deck_display()
                self.audio.play_notification()
                self.log_signal.emit("Message Saved.")
            except Exception as e:
                 self.log_signal.emit(f"Error saving file: {e}")

    # --- Display ---

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
                self.message_indicator.setText("📩  NEW MESSAGE — press ANSWER")
                self.btn_answer.setStyleSheet(
                    "QPushButton { background-color: #664400; color: #ffcc00; font-weight: bold; }"
                )
            else:
                self.deck.update_key_color(1, 0, 0, 0, "")
                self.message_indicator.setText("")
                self.btn_answer.setStyleSheet("")
        elif not self.has_message:
             self.deck.update_key_color(1, 0, 0, 0, "")

    def _cleanup_messages(self):
        """Delete any leftover message WAV files."""
        app_dir = os.path.dirname(os.path.abspath(__file__))
        for fname in ("outgoing_message.wav", "incoming_message.wav"):
            path = os.path.join(app_dir, fname)
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as e:
                print(f"Could not delete {fname}: {e}")

    def closeEvent(self, event):
        self._cleanup_messages()
        self.discovery.close()
        self.network.close()
        event.accept()

def main():
    app = QApplication(sys.argv)
    window = IntercomApp()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
