import json
import socket
import ssl
import struct
import threading
import time

from config import BUFFER_SIZE, MAX_FRAME_SIZE, RELAY_AUTH_KEY, RELAY_CA_CERT, RELAY_PORT, RELAY_TLS, TCP_PORT, UDP_PORT


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

        # Connection lock — protects tcp_socket, connected, peer_ip transitions
        self._conn_lock = threading.Lock()
        # Incremented on each new connection so old _listen_tcp threads can detect they're stale
        self._conn_generation = 0

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
        self._presence_auto_reconnect = False  # Set True after first successful connect
        self._presence_mode = "GREEN"

        # LAN TLS (TOFU)
        self._lan_tls_context_server = None
        self._lan_tls_context_client = None
        self._setup_lan_tls()

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
        import logging
        logging.getLogger('vox').info(msg)

    # ── TLS Setup ─────────────────────────────────────────────────

    def _setup_lan_tls(self):
        """Set up TLS contexts for LAN TOFU connections."""
        try:
            from user_settings import ensure_lan_cert
            cert_file, key_file = ensure_lan_cert()
            if not cert_file or not key_file:
                self._log("LAN TLS: Could not generate certificates, LAN connections will be unencrypted")
                return

            # Server context (for accepting incoming LAN connections)
            self._lan_tls_context_server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self._lan_tls_context_server.minimum_version = ssl.TLSVersion.TLSv1_2
            self._lan_tls_context_server.load_cert_chain(certfile=cert_file, keyfile=key_file)

            # Client context (for outgoing LAN connections) — don't verify peer cert initially
            self._lan_tls_context_client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            self._lan_tls_context_client.minimum_version = ssl.TLSVersion.TLSv1_2
            self._lan_tls_context_client.check_hostname = False
            self._lan_tls_context_client.verify_mode = ssl.CERT_NONE  # We verify via TOFU fingerprint
            self._lan_tls_context_client.load_cert_chain(certfile=cert_file, keyfile=key_file)

            self._log("LAN TLS: Certificates loaded")
        except Exception as e:
            self._log(f"LAN TLS setup failed: {e}")

    def _create_relay_tls_context(self):
        """Create a TLS context for relay server connections."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2

        if RELAY_CA_CERT:
            # Self-signed relay: load custom CA
            ctx.load_verify_locations(RELAY_CA_CERT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_REQUIRED
        else:
            # Let's Encrypt or system-trusted relay: use default trust store
            ctx.load_default_certs()
            ctx.check_hostname = True
            ctx.verify_mode = ssl.CERT_REQUIRED

        return ctx

    def _wrap_relay_socket(self, sock, relay_host):
        """Wrap a socket with TLS for relay connections."""
        if not RELAY_TLS:
            return sock
        try:
            ctx = self._create_relay_tls_context()
            server_hostname = relay_host if not RELAY_CA_CERT else None
            return ctx.wrap_socket(sock, server_hostname=server_hostname)
        except ssl.SSLError as e:
            self._log(f"TLS handshake failed with relay: {e}")
            raise

    def _verify_peer_tofu(self, ssl_sock, peer_ip):
        """Verify a LAN peer's certificate using Trust On First Use (TOFU).
        Returns True if trusted, False if fingerprint mismatch (potential MITM)."""
        try:
            from user_settings import compute_cert_fingerprint, get_peer_fingerprint, trust_peer
            peer_cert_der = ssl_sock.getpeercert(binary_form=True)
            if not peer_cert_der:
                self._log(f"TOFU: Peer {peer_ip} did not present a certificate")
                return True  # Allow but warn — peer may not support TLS certs

            fingerprint = compute_cert_fingerprint(peer_cert_der)
            stored = get_peer_fingerprint(peer_ip)

            if stored is None:
                # First time seeing this peer — trust and store
                trust_peer(peer_ip, fingerprint)
                self._log(f"TOFU: Trusted new peer {peer_ip} (fingerprint: {fingerprint[:23]}...)")
                return True
            elif stored == fingerprint:
                # Known peer, fingerprint matches
                return True
            else:
                # FINGERPRINT CHANGED — possible MITM
                self._log(f"TOFU WARNING: Peer {peer_ip} certificate changed!")
                self._log(f"  Expected: {stored[:23]}...")
                self._log(f"  Got:      {fingerprint[:23]}...")
                self._log("  Rejecting connection — possible man-in-the-middle attack.")
                self._log("  If this peer legitimately changed certs, remove its entry from trusted_peers in settings.")
                return False
        except Exception as e:
            self._log(f"TOFU verification error: {e}")
            return False  # Reject on verification errors

    def set_peer_ip(self, ip):
        self.peer_ip = ip

    def set_peer_ports(self, tcp_port, udp_port):
        """Set the peer's ports (for single-machine testing)"""
        self.peer_tcp_port = tcp_port
        self.peer_udp_port = udp_port

    # ── Direct (LAN) Connection ──────────────────────────────────

    def connect(self, ip):
        """Initiate TCP connection to peer (direct / LAN) with TLS TOFU"""
        if self.connected:
            self._log(f"Already connected, skipping connect to {ip}")
            return True
        try:
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(5)
            raw_sock.connect((ip, self.peer_tcp_port))
            raw_sock.settimeout(None)

            # Wrap with TLS if available
            if self._lan_tls_context_client:
                try:
                    wrapped = self._lan_tls_context_client.wrap_socket(raw_sock)
                    if not self._verify_peer_tofu(wrapped, ip):
                        wrapped.close()
                        return False
                    self._log(f"Connected to {ip} (TLS encrypted)")
                    final_sock = wrapped
                except ssl.SSLError as e:
                    self._log(f"TLS failed with {ip}, falling back to plaintext: {e}")
                    raw_sock.close()
                    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    raw_sock.connect((ip, self.peer_tcp_port))
                    final_sock = raw_sock
                    self._log(f"Connected to {ip} (plaintext — peer may not support TLS)")
            else:
                final_sock = raw_sock
                self._log(f"Connected to {ip} (plaintext — no TLS certs)")

            with self._conn_lock:
                self.tcp_socket = final_sock
                self.peer_ip = ip
                self.connected = True
                self.relay_mode = False
                self._conn_generation += 1
                gen = self._conn_generation

            threading.Thread(target=self._listen_tcp, args=(gen,), daemon=True).start()
            return True
        except Exception as e:
            self._log(f"Connection failed: {e}")
            return False

    # ── Relay Connection ─────────────────────────────────────────

    def create_room(self, relay_host, relay_port=None):
        """Connect to relay and create a new room. Returns room code or None.
        Uses a local socket variable during handshake to avoid race conditions
        with _accept_tcp() which can overwrite self.tcp_socket."""
        if relay_port is None:
            relay_port = RELAY_PORT
        self.relay_host = relay_host
        self.relay_port = relay_port

        sock = None  # Local socket — not shared until handshake completes
        try:
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(10)
            raw_sock.connect((relay_host, relay_port))

            # Wrap with TLS
            sock = self._wrap_relay_socket(raw_sock, relay_host)
            sock.settimeout(None)

            # Send CREATE_ROOM handshake on the local socket
            self._send_frame_on(sock, json.dumps({"action": "CREATE_ROOM", "auth_key": RELAY_AUTH_KEY}).encode('utf-8'))

            # Read response
            response = self._read_frame_on(sock)
            if not response:
                self._log("No response from relay server")
                sock.close()
                return None

            msg = json.loads(response.decode('utf-8'))
            if msg.get("status") == "created":
                self.room_code = msg.get("room")
                # Assign to self.tcp_socket now — handshake is done
                with self._conn_lock:
                    self.tcp_socket = sock
                    self.relay_mode = True
                self._log(f"Room created: {self.room_code}")
                self._log("Waiting for peer to join...")

                # Start a thread to wait for pairing notification
                threading.Thread(target=self._wait_for_relay_pairing, daemon=True).start()
                return self.room_code
            else:
                self._log(f"Relay error: {msg.get('message', 'Unknown error')}")
                sock.close()
                return None

        except Exception as e:
            self._log(f"Relay connection failed: {e}")
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
            return None

    def join_room(self, relay_host, room_code, relay_port=None):
        """Connect to relay and join an existing room. Returns True on success.
        Uses a local socket variable during handshake to avoid race conditions
        with _accept_tcp() which can overwrite self.tcp_socket."""
        if relay_port is None:
            relay_port = RELAY_PORT
        self.relay_host = relay_host
        self.relay_port = relay_port
        self.room_code = room_code.upper()

        sock = None  # Local socket — not shared until handshake completes
        try:
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(10)
            raw_sock.connect((relay_host, relay_port))

            # Wrap with TLS
            sock = self._wrap_relay_socket(raw_sock, relay_host)
            sock.settimeout(None)

            # Send JOIN_ROOM handshake on the local socket
            self._send_frame_on(sock, json.dumps({
                "action": "JOIN_ROOM",
                "room": self.room_code,
                "auth_key": RELAY_AUTH_KEY
            }).encode('utf-8'))

            # Read response — may get "waiting" then "paired", or "paired" directly.
            # The relay may forward peer messages (TALK_START etc.) before sending
            # "paired" to us, so we buffer those and replay them after pairing.
            early_frames = []
            while True:
                response = self._read_frame_on(sock)
                if not response:
                    self._log("No response from relay server")
                    sock.close()
                    return False

                msg = json.loads(response.decode('utf-8'))
                status = msg.get("status")

                if status == "paired":
                    # Handshake complete — NOW assign to self.tcp_socket
                    with self._conn_lock:
                        self.tcp_socket = sock
                        self.relay_mode = True
                        self.connected = True
                        self._conn_generation += 1
                        gen = self._conn_generation
                    self._log(f"Joined room: {self.room_code} — Connected!")
                    self._register_udp_with_relay()
                    # Replay any early frames that arrived before "paired"
                    for early_msg in early_frames:
                        if self.message_callback:
                            self.message_callback(early_msg)
                    threading.Thread(target=self._listen_tcp, args=(gen,), daemon=True).start()
                    threading.Thread(target=self._listen_relay_udp, daemon=True).start()
                    return True
                elif status == "waiting":
                    self._log(f"Waiting for peer in room {self.room_code}...")
                    continue  # Keep reading until we get "paired"
                elif status == "error":
                    self._log(f"Join failed: {msg.get('message', 'Unknown error')}")
                    sock.close()
                    return False
                elif status is None and msg.get("type"):
                    # Early relay frame from peer (e.g. TALK_START) — buffer it
                    self._log(f"Buffering early relay message: {msg.get('type')}")
                    early_frames.append(msg)
                    continue
                else:
                    self._log(f"Join failed: unexpected status={status!r} msg={msg}")
                    sock.close()
                    return False

        except Exception as e:
            self._log(f"Relay connection failed: {e}")
            if sock:
                try:
                    sock.close()
                except OSError:
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
        if msg_len > MAX_FRAME_SIZE:
            self._log(f"Frame too large ({msg_len} bytes), rejecting")
            return None
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
        """Listen for incoming TCP connections (direct/LAN mode) with TLS TOFU"""
        self._log(f"Listening on TCP {TCP_PORT}")
        while self.running:
            try:
                client, addr = self.tcp_server.accept()

                # Try TLS, but fall back to plaintext if it fails
                if self._lan_tls_context_server:
                    # Peek at first byte to detect TLS (0x16 = TLS handshake)
                    try:
                        client.settimeout(2)
                        first_byte = client.recv(1, socket.MSG_PEEK)
                        client.settimeout(None)
                        if first_byte and first_byte[0] == 0x16:
                            # Looks like TLS
                            try:
                                client = self._lan_tls_context_server.wrap_socket(client, server_side=True)
                                if not self._verify_peer_tofu(client, addr[0]):
                                    client.close()
                                    continue
                                self._log(f"Accepted TLS connection from {addr}")
                            except (ssl.SSLError, OSError) as e:
                                self._log(f"TLS handshake failed from {addr}: {e}")
                                try:
                                    client.close()
                                except OSError:
                                    pass
                                continue
                        else:
                            self._log(f"Accepted plaintext connection from {addr}")
                    except (TimeoutError, OSError):
                        self._log(f"Accepted connection from {addr} (timeout on peek, assuming plaintext)")
                else:
                    self._log(f"Accepted plaintext connection from {addr}")

                with self._conn_lock:
                    # Don't clobber an active relay connection with a LAN probe
                    if self.connected and self.relay_mode:
                        self._log(f"Ignoring LAN connection from {addr} — relay session active")
                        try:
                            client.close()
                        except OSError:
                            pass
                        continue

                    # Close any existing connection
                    if self.connected and self.tcp_socket:
                        self._log(f"Replacing stale connection with new from {addr}")
                        try:
                            self.tcp_socket.close()
                        except OSError:
                            pass

                    self.tcp_socket = client
                    self.peer_ip = addr[0]
                    self.connected = True
                    self.relay_mode = False
                    self._conn_generation += 1
                    gen = self._conn_generation

                threading.Thread(target=self._listen_tcp, args=(gen,), daemon=True).start()

                # Notify the app that a connection was accepted
                if self.message_callback:
                    self.message_callback({"type": "PEER_CONNECTED", "payload": {"ip": addr[0], "direction": "inbound"}})
            except Exception as e:
                if self.running:
                    self._log(f"Accept Error: {e}")

    def _recv_all(self, n):
        """Helper to receive exactly n bytes"""
        with self._conn_lock:
            sock = self.tcp_socket
        if not sock:
            return None
        data = b''
        while len(data) < n:
            packet = sock.recv(n - len(data))
            if not packet:
                return None
            data += packet
        return data

    def _listen_tcp(self, generation=None):
        """Receive Loop for Control Messages.
        If generation is provided, only mark disconnected if we're still on that generation
        (prevents stale threads from killing a newer connection)."""
        while self.connected and self.tcp_socket:
            try:
                # 1. Read 4-byte Length
                raw_len = self._recv_all(4)
                if not raw_len:
                    break

                msg_len = struct.unpack('!I', raw_len)[0]

                # Enforce frame size limit
                if msg_len > MAX_FRAME_SIZE:
                    self._log(f"Rejecting oversized frame ({msg_len} bytes)")
                    break

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

        # Only mark disconnected if this thread owns the current connection
        with self._conn_lock:
            if generation is not None and generation != self._conn_generation:
                self._log("Stale listener exiting (connection was replaced)")
                return
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
            except OSError:
                pass
            self.tcp_socket = None
        if self.relay_udp_socket:
            try:
                self.relay_udp_socket.close()
            except OSError:
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

    def connect_presence(self, relay_host, relay_port, display_name, user_id, mode="GREEN", team_id=""):
        """Connect to the relay server's presence channel (with TLS if enabled)"""
        self.relay_host = relay_host
        self.relay_port = relay_port
        self.display_name = display_name
        self.user_id = user_id
        self._presence_mode = mode
        self._presence_team_id = team_id

        try:
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(10)
            raw_sock.connect((relay_host, relay_port))

            # Wrap with TLS
            self.presence_socket = self._wrap_relay_socket(raw_sock, relay_host)
            self.presence_socket.settimeout(None)

            # Send REGISTER
            reg_msg = json.dumps({
                "action": "REGISTER",
                "name": display_name,
                "user_id": user_id,
                "mode": mode,
                "team_id": team_id,
                "auth_key": RELAY_AUTH_KEY,
            }).encode('utf-8')
            self._send_frame_on(self.presence_socket, reg_msg)

            # Read response
            response = self._read_frame_on(self.presence_socket)
            if response:
                msg = json.loads(response.decode('utf-8'))
                if msg.get("status") == "registered":
                    self.presence_connected = True
                    self._presence_auto_reconnect = True
                    self._log(f"Presence registered as {display_name}" + (" (TLS)" if RELAY_TLS else ""))
                    threading.Thread(target=self._listen_presence, daemon=True).start()
                    threading.Thread(target=self._presence_heartbeat, daemon=True).start()
                    return True

            self._log("Presence registration failed")
            self.presence_socket.close()
            return False

        except Exception as e:
            self._log(f"Presence connection failed: {e}")
            if self.presence_socket:
                try:
                    self.presence_socket.close()
                except OSError:
                    pass
            return False

    def update_presence_mode(self, mode, room_code="", team_id=None):
        """Notify the presence server of a mode/team change"""
        self._presence_mode = mode  # Track for reconnection
        if team_id is not None:
            self._presence_team_id = team_id
        if not self.presence_connected or not self.presence_socket:
            return
        try:
            payload = {"action": "MODE_UPDATE", "mode": mode}
            if room_code:
                payload["room"] = room_code
            if team_id is not None:
                payload["team_id"] = team_id
            msg = json.dumps(payload).encode('utf-8')
            self._send_frame_on(self.presence_socket, msg)
        except Exception as e:
            self._log(f"Presence mode update failed: {e}")

    def update_presence_team(self, team_id):
        """Switch the user's active team without changing mode."""
        self._presence_team_id = team_id
        self.update_presence_mode(self._presence_mode, team_id=team_id)

    def update_presence_name(self, new_name):
        """Notify the presence server of a display name change."""
        self.display_name = new_name
        if not self.presence_connected or not self.presence_socket:
            return
        try:
            msg = json.dumps({"action": "NAME_UPDATE", "name": new_name}).encode('utf-8')
            self._send_frame_on(self.presence_socket, msg)
        except Exception as e:
            self._log(f"Presence name update failed: {e}")

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
            self._log("Cannot accept: not connected to presence")
            return
        try:
            msg = json.dumps({
                "action": "ACCEPT_CONNECTION",
                "room": room_code
            }).encode('utf-8')
            self._send_frame_on(self.presence_socket, msg)
            self._log(f"Sent ACCEPT_CONNECTION for room {room_code}")
        except Exception as e:
            self._log(f"Accept connection failed: {e}")

    def accept_presence_connection_by_id(self, from_id):
        """Fallback: ask the relay to set up a room with the given user"""
        if not self.presence_connected or not self.presence_socket:
            self._log("Cannot accept by ID: not connected to presence")
            return
        try:
            msg = json.dumps({
                "action": "ACCEPT_CONNECTION_BY_ID",
                "from_id": from_id
            }).encode('utf-8')
            self._send_frame_on(self.presence_socket, msg)
            self._log(f"Sent ACCEPT_CONNECTION_BY_ID for {from_id}")
        except Exception as e:
            self._log(f"Accept connection by ID failed: {e}")

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

    def send_presence_message(self, msg_dict):
        """Send an arbitrary JSON message on the presence socket."""
        if not self.presence_connected or not self.presence_socket:
            self._log("Cannot send presence message — not connected")
            return
        try:
            data = json.dumps(msg_dict).encode('utf-8')
            self._send_frame_on(self.presence_socket, data)
        except Exception as e:
            self._log(f"Presence send failed: {e}")

    def send_page_all(self, file_path, team_id, sender_name):
        """Broadcast a voice message to all GREEN team members via relay.

        Reads the audio file, base64-encodes it, and sends via the presence
        socket. The relay forwards to all GREEN members. No call is established.
        """
        import base64
        if not self.presence_connected or not self.presence_socket:
            self._log("Cannot page all — not connected to presence")
            return False
        try:
            with open(file_path, 'rb') as f:
                audio_data = f.read()
            audio_b64 = base64.b64encode(audio_data).decode('ascii')
            msg = {
                "action": "PAGE_ALL",
                "team_id": team_id,
                "sender_name": sender_name,
                "audio_b64": audio_b64,
            }
            data = json.dumps(msg).encode('utf-8')
            self._send_frame_on(self.presence_socket, data)
            self._log(f"Page All sent ({len(audio_data)} bytes)")
            return True
        except Exception as e:
            self._log(f"Page All failed: {e}")
            return False

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
                    self._log(f"[Presence] Got CONNECTION_REQUEST: from={msg.get('from_name')} room={msg.get('room')}")
                    if self.presence_callback:
                        self.presence_callback(msg)

                elif msg_type == "CONNECT_ROOM":
                    # Server tells us to join a room for audio
                    self._log(f"[Presence] Got CONNECT_ROOM: {msg}")
                    if self.presence_callback:
                        self.presence_callback(msg)

                elif msg_type == "CONNECTION_REJECTED":
                    if self.presence_callback:
                        self.presence_callback(msg)

                elif msg_type == "CONNECTION_CANCELLED":
                    if self.presence_callback:
                        self.presence_callback(msg)

                elif msg_type in ("JOIN_REQUEST", "JOIN_RESPONSE", "JOIN_REQUEST_FAILED"):
                    # Lobby join request/response — forward to main.py
                    if self.presence_callback:
                        self.presence_callback(msg)

                elif msg_type == "PAGE_ALL":
                    # One-way broadcast message from a teammate
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

        # Auto-reconnect if this was an unexpected disconnect
        if self._presence_auto_reconnect and self.running:
            threading.Thread(target=self._reconnect_presence, daemon=True).start()

    def _reconnect_presence(self):
        """Auto-reconnect to presence with exponential backoff."""
        backoff = 2  # Start at 2 seconds
        max_backoff = 60
        while self.running and self._presence_auto_reconnect and not self.presence_connected:
            self._log(f"Presence reconnecting in {backoff}s...")
            time.sleep(backoff)
            if not self.running or not self._presence_auto_reconnect:
                break
            try:
                success = self.connect_presence(
                    self.relay_host, self.relay_port,
                    self.display_name, self.user_id,
                    self._presence_mode,
                    getattr(self, '_presence_team_id', ''),
                )
                if success:
                    self._log("Presence reconnected")
                    return
            except Exception as e:
                self._log(f"Presence reconnect failed: {e}")
            backoff = min(backoff * 2, max_backoff)
        if not self.presence_connected:
            self._log("Presence reconnection gave up")

    def _presence_heartbeat(self):
        """Send periodic PING to relay so it can detect dead clients quickly."""
        while self.running and self.presence_connected:
            time.sleep(30)
            if not self.presence_connected or not self.presence_socket:
                break
            try:
                ping = json.dumps({"action": "PING"}).encode('utf-8')
                self._send_frame_on(self.presence_socket, ping)
            except (OSError, ssl.SSLError):
                self._log("Presence heartbeat failed — socket dead")
                break  # Socket dead, _listen_presence will handle reconnect

    def disconnect_presence(self):
        """Disconnect from the presence server (intentional — no auto-reconnect)"""
        self._presence_auto_reconnect = False
        self.presence_connected = False
        if self.presence_socket:
            try:
                self.presence_socket.close()
            except OSError:
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
        if msg_len > MAX_FRAME_SIZE:
            self._log(f"Rejecting oversized frame ({msg_len} bytes)")
            return None
        data = b''
        while len(data) < msg_len:
            chunk = sock.recv(msg_len - len(data))
            if not chunk:
                return None
            data += chunk
        return data
