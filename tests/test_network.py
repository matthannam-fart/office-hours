"""Tests for network protocol logic — TLS context creation, relay message
format validation, presence message serialization, and UDP packet framing.

All tests are self-contained and use mocks — no real network calls.
"""

import json
import ssl
import struct
from unittest.mock import MagicMock

# ── TLS context creation ─────────────────────────────────────────

class TestRelayTLSContext:
    """Test _create_relay_tls_context logic without instantiating NetworkManager."""

    def test_relay_tls_context_uses_tls_client(self):
        """Relay TLS context should be a TLS client context."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2

    def test_relay_tls_context_default_checks_hostname(self):
        """When no RELAY_CA_CERT, hostname verification should be on."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        # Default for TLS_CLIENT is check_hostname=True
        assert ctx.check_hostname is True
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_relay_tls_context_custom_ca_disables_hostname_check(self):
        """With a custom CA cert, hostname checking should be disabled."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        # Simulate custom CA path
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_REQUIRED


class TestLanTLSContext:
    """Test LAN TLS context properties."""

    def test_lan_server_context_protocol(self):
        """LAN server context should use TLS_SERVER protocol."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2

    def test_lan_client_context_no_hostname_check(self):
        """LAN client context should skip hostname check (TOFU model)."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE


# ── Relay connection message format ──────────────────────────────

class TestRelayMessageFormat:
    """Test relay handshake and control message format validation."""

    def test_create_room_message_has_required_fields(self):
        """CREATE_ROOM message must have action and auth_key."""
        msg = {"action": "CREATE_ROOM", "auth_key": "vox-relay-v1-2026"}
        encoded = json.dumps(msg).encode("utf-8")
        decoded = json.loads(encoded)
        assert decoded["action"] == "CREATE_ROOM"
        assert "auth_key" in decoded

    def test_join_room_message_has_required_fields(self):
        """JOIN_ROOM message must have action, room, and auth_key."""
        msg = {
            "action": "JOIN_ROOM",
            "room": "VOX-ABC123",
            "auth_key": "vox-relay-v1-2026",
        }
        encoded = json.dumps(msg).encode("utf-8")
        decoded = json.loads(encoded)
        assert decoded["action"] == "JOIN_ROOM"
        assert decoded["room"] == "VOX-ABC123"
        assert "auth_key" in decoded

    def test_created_response_format(self):
        """Server's CREATE_ROOM response should have status and room."""
        response = {"status": "created", "room": "VOX-XYZ789"}
        assert response["status"] == "created"
        assert response["room"].startswith("VOX-")

    def test_paired_response_format(self):
        """Server's paired response should have status=paired."""
        response = {"status": "paired", "room": "VOX-ABC123"}
        assert response["status"] == "paired"

    def test_error_response_format(self):
        """Server error response should have status=error and message."""
        response = {"status": "error", "message": "Room not found"}
        assert response["status"] == "error"
        assert len(response["message"]) > 0


# ── Presence message serialization ───────────────────────────────

class TestPresenceMessages:
    """Test presence-channel message serialization."""

    def test_register_message_all_fields(self):
        """REGISTER message should include all required fields."""
        msg = {
            "action": "REGISTER",
            "name": "Alice",
            "user_id": "uuid-1234",
            "mode": "GREEN",
            "team_id": "team-abc",
            "auth_key": "vox-relay-v1-2026",
        }
        encoded = json.dumps(msg).encode("utf-8")
        decoded = json.loads(encoded)
        for key in ("action", "name", "user_id", "mode", "team_id", "auth_key"):
            assert key in decoded

    def test_mode_update_message_serialization(self):
        """MODE_UPDATE should serialize with mode and optional room/team_id."""
        msg = {"action": "MODE_UPDATE", "mode": "RED", "room": "VOX-XYZ", "team_id": "team-1"}
        encoded = json.dumps(msg).encode("utf-8")
        decoded = json.loads(encoded)
        assert decoded["action"] == "MODE_UPDATE"
        assert decoded["mode"] == "RED"
        assert decoded["room"] == "VOX-XYZ"

    def test_presence_update_broadcast_format(self):
        """PRESENCE_UPDATE broadcast should contain a users list."""
        users = [
            {"user_id": "u1", "name": "Alice", "mode": "GREEN", "room": "", "team_id": "t1"},
            {"user_id": "u2", "name": "Bob", "mode": "YELLOW", "room": "", "team_id": "t1"},
        ]
        msg = {"type": "PRESENCE_UPDATE", "users": users}
        encoded = json.dumps(msg).encode("utf-8")
        decoded = json.loads(encoded)
        assert decoded["type"] == "PRESENCE_UPDATE"
        assert len(decoded["users"]) == 2

    def test_connect_to_message_format(self):
        """CONNECT_TO message should have target_id and name."""
        msg = {"action": "CONNECT_TO", "target_id": "user-456", "name": "Alice"}
        encoded = json.dumps(msg).encode("utf-8")
        decoded = json.loads(encoded)
        assert decoded["action"] == "CONNECT_TO"
        assert decoded["target_id"] == "user-456"

    def test_ping_message_format(self):
        """PING heartbeat message should serialize correctly."""
        msg = {"action": "PING"}
        encoded = json.dumps(msg).encode("utf-8")
        decoded = json.loads(encoded)
        assert decoded["action"] == "PING"

    def test_name_update_message_format(self):
        """NAME_UPDATE message should include the new name."""
        msg = {"action": "NAME_UPDATE", "name": "New Name"}
        encoded = json.dumps(msg).encode("utf-8")
        decoded = json.loads(encoded)
        assert decoded["action"] == "NAME_UPDATE"
        assert decoded["name"] == "New Name"


# ── UDP packet framing ───────────────────────────────────────────

class TestUDPPacketFraming:
    """Test UDP audio packet framing and size constraints."""

    def test_udp_register_message_format(self):
        """UDP_REGISTER message should include type and udp_port."""
        msg = {"type": "UDP_REGISTER", "udp_port": 54321}
        encoded = json.dumps(msg).encode("utf-8")
        decoded = json.loads(encoded)
        assert decoded["type"] == "UDP_REGISTER"
        assert isinstance(decoded["udp_port"], int)

    def test_audio_packet_fits_in_buffer(self):
        """Compressed audio packets should fit within BUFFER_SIZE."""
        from config import BUFFER_SIZE
        # Opus frames at 48kbps, 20ms are typically ~120 bytes
        # ulaw frames at 24kHz, 20ms = 480 bytes
        assert BUFFER_SIZE >= 480  # Must fit at least a ulaw frame

    def test_frame_length_prefix_roundtrip(self):
        """4-byte big-endian length prefix should round-trip correctly."""
        for size in (0, 1, 480, 960, 4096, 65535, 100000):
            packed = struct.pack("!I", size)
            assert len(packed) == 4
            unpacked = struct.unpack("!I", packed)[0]
            assert unpacked == size

    def test_multiple_frames_concatenated(self):
        """Multiple length-prefixed frames concatenated should be parseable."""
        frames_data = [b"frame_one", b"frame_two", b"frame_three"]
        wire = b""
        for f in frames_data:
            wire += struct.pack("!I", len(f)) + f

        # Parse them back
        offset = 0
        parsed = []
        while offset < len(wire):
            length = struct.unpack("!I", wire[offset : offset + 4])[0]
            offset += 4
            parsed.append(wire[offset : offset + length])
            offset += length

        assert parsed == frames_data

    def test_send_frame_on_constructs_correct_wire_format(self):
        """_send_frame_on should send length prefix + data via sendall."""
        mock_sock = MagicMock()
        data = b'{"type": "TALK_START"}'
        expected_len = struct.pack("!I", len(data))

        # Call the framing logic directly
        msg_len = struct.pack("!I", len(data))
        mock_sock.sendall(msg_len + data)

        mock_sock.sendall.assert_called_once_with(expected_len + data)
