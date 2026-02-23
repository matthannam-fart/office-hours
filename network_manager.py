import socket
import threading
import json
import time
import struct
from config import TCP_PORT, UDP_PORT, BUFFER_SIZE, RELAY_PORT

class NetworkManager:
    def __init__(self, message_callback=None, audio_callback=None, log_callback=None):
        self.tcp_socket = None
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.peer_ip = None
        self.peer_tcp_port = TCP_PORT  # Peer's TCP port (same as ours by default)
        self.peer_udp_port = UDP_PORT  # Peer's UDP port (same as ours by default)
        self.connected = False
        self.message_callback = message_callback
        self.audio_callback = audio_callback
        self.log_callback = log_callback
        self.running = True

        # Relay mode state
        self.relay_mode = False
        self.relay_host = None
        self.relay_port = None
        self.relay_udp_socket = None  # Separate UDP socket for relay
        self.room_code = None

        # Presence state
        self.presence_socket = None
        self.presence_connected = False
        self.presence_callback = None  # Called with presence updates
        self.display_name = None
        self.user_id = None

        # Start Servers (for LAN / direct mode)
        self.tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp_server.bind(('0.0.0.0', TCP_PORT))
        self.tcp_server.listen(1)

        self.udp_server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp_server.bind(('0.0.0.0', UDP_PORT))

        # Threads
        threading.Thread(target=self._accept_tcp, daemon=True).start()
        threading.Thread(target=self._listen_udp, daemon=True).start()

    def _log(self, msg):
        if self.log_callback:
            self.log_callback(msg)
        print(msg)

    def set_peer_ip(self, ip):
        self.peer_ip = ip

    def set_peer_ports(self, tcp_port, udp_port):
        """Set the peer's ports (for single-machine testing)"""
        self.peer_tcp_port = tcp_port
        self.peer_udp_port = udp_port

    # ── Direct (LAN) Connection ──────────────────────────────────

    def connect(self, ip):
        """Initiate TCP connection to peer (direct / LAN)"""
        if self.connected:
            self._log(f"Already connected, skipping connect to {ip}")
            return True
        try:
            self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_socket.connect((ip, self.peer_tcp_port))
            self.peer_ip = ip
            self.connected = True
            self.relay_mode = False
            threading.Thread(target=self._listen_tcp, daemon=True).start()
            self._log(f"Connected to {ip}")
            return True
        except Exception as e:
            self._log(f"Connection failed: {e}")
            return False

    # ── Relay Connection ─────────────────────────────────────────

    def create_room(self, relay_host, relay_port=None):
        """Connect to relay and create a new room. Returns room code or None."""
        if relay_port is None:
            relay_port = RELAY_PORT
        self.relay_host = relay_host
        self.relay_port = relay_port

        try:
            self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_socket.settimeout(10)
            self.tcp_socket.connect((relay_host, relay_port))
            self.tcp_socket.settimeout(None)

            # Send CREATE_ROOM handshake
            self._send_frame(json.dumps({"action": "CREATE_ROOM"}).encode('utf-8'))

            # Read response
            response = self._read_frame()
            if not response:
                self._log("No response from relay server")
                self.tcp_socket.close()
                return None

            msg = json.loads(response.decode('utf-8'))
            if msg.get("status") == "created":
                self.room_code = msg.get("room")
                self.relay_mode = True
                self._log(f"Room created: {self.room_code}")
                self._log("Waiting for peer to join...")

                # Start a thread to wait for pairing notification
                threading.Thread(target=self._wait_for_relay_pairing, daemon=True).start()
                return self.room_code
            else:
                self._log(f"Relay error: {msg.get('message', 'Unknown error')}")
                self.tcp_socket.close()
                return None

        except Exception as e:
            self._log(f"Relay connection failed: {e}")
            if self.tcp_socket:
                try:
                    self.tcp_socket.close()
                except:
                    pass
            return None

    def join_room(self, relay_host, room_code, relay_port=None):
        """Connect to relay and join an existing room. Returns True on success."""
        if relay_port is None:
            relay_port = RELAY_PORT
        self.relay_host = relay_host
        self.relay_port = relay_port
        self.room_code = room_code.upper()

        try:
            self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_socket.settimeout(10)
            self.tcp_socket.connect((relay_host, relay_port))
            self.tcp_socket.settimeout(None)

            # Send JOIN_ROOM handshake
            self._send_frame(json.dumps({
                "action": "JOIN_ROOM",
                "room": self.room_code
            }).encode('utf-8'))

            # Read response — may get "waiting" then "paired", or "paired" directly
            while True:
                response = self._read_frame()
                if not response:
                    self._log("No response from relay server")
                    self.tcp_socket.close()
                    return False

                msg = json.loads(response.decode('utf-8'))
                status = msg.get("status")

                if status == "paired":
                    self.relay_mode = True
                    self.connected = True
                    self._log(f"Joined room: {self.room_code} — Connected!")
                    self._register_udp_with_relay()
                    threading.Thread(target=self._listen_tcp, daemon=True).start()
                    threading.Thread(target=self._listen_relay_udp, daemon=True).start()
                    return True
                elif status == "waiting":
                    self._log(f"Waiting for peer in room {self.room_code}...")
                    continue  # Keep reading until we get "paired"
                else:
                    self._log(f"Join failed: {msg.get('message', 'Unknown error')}")
                    self.tcp_socket.close()
                    return False

        except Exception as e:
            self._log(f"Relay connection failed: {e}")
            if self.tcp_socket:
                try:
                    self.tcp_socket.close()
                except:
                    pass
            return False

    def _wait_for_relay_pairing(self):
        """Wait for the relay to send a 'paired' notification (called after CREATE_ROOM)"""
        try:
            response = self._read_frame()
            if not response:
                self._log("Relay connection lost while waiting for peer")
                return

            msg = json.loads(response.decode('utf-8'))
            if msg.get("status") == "paired":
                self.connected = True
                self._log(f"Peer joined room {self.room_code} — Connected!")

                # Register UDP with relay
                self._register_udp_with_relay()

                # Start listening for relayed data
                threading.Thread(target=self._listen_tcp, daemon=True).start()
                threading.Thread(target=self._listen_relay_udp, daemon=True).start()
            elif msg.get("status") == "timeout":
                self._log("Room timed out — no peer joined")
                self.relay_mode = False
                self.tcp_socket.close()
        except Exception as e:
            self._log(f"Error waiting for pairing: {e}")

    def _register_udp_with_relay(self):
        """Set up a UDP socket for relay and register it with the server"""
        try:
            self.relay_udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.relay_udp_socket.bind(('0.0.0.0', 0))  # Bind to any available port
            local_udp_port = self.relay_udp_socket.getsockname()[1]

            # Tell the relay our UDP port via TCP
            reg_msg = json.dumps({"type": "UDP_REGISTER", "udp_port": local_udp_port})
            self.send_tcp_data(reg_msg.encode('utf-8'))

            # Send a UDP packet to the relay to punch through NAT
            self.relay_udp_socket.sendto(b"HELLO", (self.relay_host, self.relay_port))

            self._log(f"UDP registered with relay (local port {local_udp_port})")
        except Exception as e:
            self._log(f"UDP registration failed: {e}")

    def _listen_relay_udp(self):
        """Receive UDP audio relayed from the server"""
        if not self.relay_udp_socket:
            return
        self._log("Listening for relay UDP audio...")
        while self.running and self.relay_mode:
            try:
                data, addr = self.relay_udp_socket.recvfrom(BUFFER_SIZE)
                if data == b"HELLO":
                    continue  # Ignore handshake packets
                if self.audio_callback:
                    self.audio_callback(data)
            except Exception as e:
                if self.running and self.relay_mode:
                    self._log(f"Relay UDP error: {e}")
                break

    # ── Send Methods ─────────────────────────────────────────────

    def _send_frame(self, data):
        """Send raw data with 4-byte length prefix (low-level)"""
        msg_len = struct.pack('!I', len(data))
        self.tcp_socket.sendall(msg_len + data)

    def _read_frame(self):
        """Read a length-prefixed frame from tcp_socket"""
        raw_len = self._recv_all(4)
        if not raw_len:
            return None
        msg_len = struct.unpack('!I', raw_len)[0]
        return self._recv_all(msg_len)

    def send_tcp_data(self, data):
        """Send data with 4-byte length prefix (public API)"""
        if not self.connected or not self.tcp_socket:
            return
        try:
            self._send_frame(data)
        except Exception as e:
            self._log(f"TCP Send Error: {e}")
            self.connected = False

    def send_control(self, msg_type, payload=None):
        """Send JSON control message via TCP"""
        message = {
            "type": msg_type,
            "payload": payload,
            "timestamp": time.time()
        }
        try:
            data = json.dumps(message).encode('utf-8')
            self.send_tcp_data(data)
        except Exception as e:
            self._log(f"Send Control Error: {e}")

    def send_audio(self, audio_chunk):
        """Send raw audio bytes via UDP (direct or relay)"""
        try:
            if self.relay_mode and self.relay_udp_socket:
                # Send to relay server, which forwards to peer
                self.relay_udp_socket.sendto(audio_chunk, (self.relay_host, self.relay_port))
            elif self.peer_ip:
                # Direct UDP to peer
                self.udp_socket.sendto(audio_chunk, (self.peer_ip, self.peer_udp_port))
        except Exception as e:
            self._log(f"Audio Send Error: {e}")

    def send_file(self, file_path):
        """Send a file via TCP (for Voicemail)"""
        if not self.connected:
            return

        try:
            with open(file_path, 'rb') as f:
                file_data = f.read()
            
            # 1. Send file header
            import os
            self.send_control("FILE_HEADER", {
                "size": len(file_data),
                "name": os.path.basename(file_path)
            })
            
            # 2. Send raw file data
            self.send_tcp_data(file_data)
            
            self._log("File sent successfully")
        except Exception as e:
            self._log(f"File Send Error: {e}")

    # ── Receive / Listen ─────────────────────────────────────────

    def _accept_tcp(self):
        """Listen for incoming TCP connections (direct/LAN mode)"""
        self._log(f"Listening on TCP {TCP_PORT}")
        while self.running:
            try:
                client, addr = self.tcp_server.accept()
                
                # If we already have an active connection, reject the new one
                if self.connected and self.tcp_socket:
                    self._log(f"Already connected, rejecting incoming from {addr}")
                    client.close()
                    continue
                
                self.tcp_socket = client
                self.peer_ip = addr[0]
                self.connected = True
                self.relay_mode = False
                self._log(f"Accepted connection from {addr}")
                threading.Thread(target=self._listen_tcp, daemon=True).start()
                
                # Notify the app that a connection was accepted
                if self.message_callback:
                    self.message_callback({"type": "PEER_CONNECTED", "payload": {"ip": addr[0], "direction": "inbound"}})
            except Exception as e:
                if self.running:
                    self._log(f"Accept Error: {e}")

    def _recv_all(self, n):
        """Helper to receive exactly n bytes"""
        data = b''
        while len(data) < n:
            packet = self.tcp_socket.recv(n - len(data))
            if not packet:
                return None
            data += packet
        return data

    def _listen_tcp(self):
        """Receive Loop for Control Messages"""
        while self.connected and self.tcp_socket:
            try:
                # 1. Read 4-byte Length
                raw_len = self._recv_all(4)
                if not raw_len:
                    break
                
                msg_len = struct.unpack('!I', raw_len)[0]
                
                # 2. Read Payload
                payload = self._recv_all(msg_len)
                if not payload:
                    break
                
                # 3. Process — try JSON, fallback to binary
                try:
                    msg = json.loads(payload.decode('utf-8'))
                    if self.message_callback:
                        self.message_callback(msg)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    if self.message_callback:
                        self.message_callback({"type": "BINARY_DATA", "payload": payload})

            except Exception as e:
                self._log(f"TCP Recv Error: {e}")
                break
        
        self.connected = False
        self._log("Disconnected")

    def _listen_udp(self):
        """Receive Loop for Audio (direct/LAN mode)"""
        self._log(f"Listening on UDP {UDP_PORT}")
        while self.running:
            try:
                data, addr = self.udp_server.recvfrom(BUFFER_SIZE)
                if self.audio_callback and not self.relay_mode:
                    self.audio_callback(data)
            except Exception as e:
                if self.running:
                    self._log(f"UDP Recv Error: {e}")

    # ── Disconnect / Cleanup ─────────────────────────────────────

    def disconnect(self):
        """Cleanly disconnect from current session"""
        self.connected = False
        self.relay_mode = False
        self.room_code = None
        if self.tcp_socket:
            try:
                self.tcp_socket.close()
            except:
                pass
            self.tcp_socket = None
        if self.relay_udp_socket:
            try:
                self.relay_udp_socket.close()
            except:
                pass
            self.relay_udp_socket = None
        self.peer_ip = None
        self._log("Disconnected")

    def close(self):
        self.running = False
        self.disconnect()
        self.disconnect_presence()
        if self.tcp_server:
            self.tcp_server.close()
        if self.udp_server:
            self.udp_server.close()

    # ── Presence Connection ──────────────────────────────────────

    def connect_presence(self, relay_host, relay_port, display_name, user_id, mode="GREEN"):
        """Connect to the relay server's presence channel"""
        self.relay_host = relay_host
        self.relay_port = relay_port
        self.display_name = display_name
        self.user_id = user_id

        try:
            self.presence_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.presence_socket.settimeout(10)
            self.presence_socket.connect((relay_host, relay_port))
            self.presence_socket.settimeout(None)

            # Send REGISTER
            reg_msg = json.dumps({
                "action": "REGISTER",
                "name": display_name,
                "user_id": user_id,
                "mode": mode
            }).encode('utf-8')
            self._send_frame_on(self.presence_socket, reg_msg)

            # Read response
            response = self._read_frame_on(self.presence_socket)
            if response:
                msg = json.loads(response.decode('utf-8'))
                if msg.get("status") == "registered":
                    self.presence_connected = True
                    self._log(f"Presence registered as {display_name}")
                    threading.Thread(target=self._listen_presence, daemon=True).start()
                    return True

            self._log("Presence registration failed")
            self.presence_socket.close()
            return False

        except Exception as e:
            self._log(f"Presence connection failed: {e}")
            if self.presence_socket:
                try:
                    self.presence_socket.close()
                except:
                    pass
            return False

    def update_presence_mode(self, mode):
        """Notify the presence server of a mode change"""
        if not self.presence_connected or not self.presence_socket:
            return
        try:
            msg = json.dumps({"action": "MODE_UPDATE", "mode": mode}).encode('utf-8')
            self._send_frame_on(self.presence_socket, msg)
        except Exception as e:
            self._log(f"Presence mode update failed: {e}")

    def connect_to_user(self, target_user_id):
        """Request a connection to a specific online user via presence"""
        if not self.presence_connected or not self.presence_socket:
            self._log("Not connected to presence server")
            return
        try:
            msg = json.dumps({
                "action": "CONNECT_TO",
                "target_id": target_user_id,
                "name": self.display_name
            }).encode('utf-8')
            self._send_frame_on(self.presence_socket, msg)
            self._log(f"Connection request sent to user {target_user_id}")
        except Exception as e:
            self._log(f"Connect-to request failed: {e}")

    def accept_presence_connection(self, room_code, from_id):
        """Accept an incoming connection request via presence"""
        if not self.presence_connected or not self.presence_socket:
            return
        try:
            msg = json.dumps({
                "action": "ACCEPT_CONNECTION",
                "room": room_code
            }).encode('utf-8')
            self._send_frame_on(self.presence_socket, msg)
        except Exception as e:
            self._log(f"Accept connection failed: {e}")

    def reject_presence_connection(self, from_id):
        """Reject an incoming connection request via presence"""
        if not self.presence_connected or not self.presence_socket:
            return
        try:
            msg = json.dumps({
                "action": "REJECT_CONNECTION",
                "from_id": from_id
            }).encode('utf-8')
            self._send_frame_on(self.presence_socket, msg)
        except Exception as e:
            self._log(f"Reject connection failed: {e}")

    def cancel_connection(self, target_user_id=None):
        """Cancel an outgoing connection request"""
        if not self.presence_connected or not self.presence_socket:
            return
        try:
            msg = json.dumps({
                "action": "CANCEL_CONNECTION",
                "target_id": target_user_id or ""
            }).encode('utf-8')
            self._send_frame_on(self.presence_socket, msg)
            self._log("Connection request cancelled")
        except Exception as e:
            self._log(f"Cancel connection failed: {e}")

    def _listen_presence(self):
        """Listen for presence updates from the server"""
        while self.running and self.presence_connected:
            try:
                frame = self._read_frame_on(self.presence_socket)
                if frame is None:
                    break

                msg = json.loads(frame.decode('utf-8'))
                msg_type = msg.get("type")

                if msg_type == "PRESENCE_UPDATE":
                    if self.presence_callback:
                        self.presence_callback(msg)

                elif msg_type == "CONNECTION_REQUEST":
                    # Incoming connection request from another user
                    if self.presence_callback:
                        self.presence_callback(msg)

                elif msg_type == "CONNECT_ROOM":
                    # Server tells us to join a room for audio
                    if self.presence_callback:
                        self.presence_callback(msg)

                elif msg_type == "CONNECTION_REJECTED":
                    if self.presence_callback:
                        self.presence_callback(msg)

                elif msg_type == "ERROR":
                    self._log(f"Presence error: {msg.get('message')}")

            except Exception as e:
                if self.running and self.presence_connected:
                    self._log(f"Presence listen error: {e}")
                break

        self.presence_connected = False
        self._log("Presence disconnected")

    def disconnect_presence(self):
        """Disconnect from the presence server"""
        self.presence_connected = False
        if self.presence_socket:
            try:
                self.presence_socket.close()
            except:
                pass
            self.presence_socket = None

    def _send_frame_on(self, sock, data):
        """Send a length-prefixed frame on a specific socket"""
        msg_len = struct.pack('!I', len(data))
        sock.sendall(msg_len + data)

    def _read_frame_on(self, sock):
        """Read a length-prefixed frame from a specific socket"""
        raw_len = b''
        while len(raw_len) < 4:
            chunk = sock.recv(4 - len(raw_len))
            if not chunk:
                return None
            raw_len += chunk
        msg_len = struct.unpack('!I', raw_len)[0]
        data = b''
        while len(data) < msg_len:
            chunk = sock.recv(msg_len - len(data))
            if not chunk:
                return None
            data += chunk
        return data
