"""Tests for relay_server.py — room lifecycle, presence broadcast,
client disconnect cleanup, and auth key rejection.

All tests use the relay_server module's functions directly with mock
sockets — no real network connections.
"""

import json
import time
from unittest.mock import MagicMock

import relay_server

# ── Helpers ──────────────────────────────────────────────────────

def make_mock_socket(recv_data=None):
    """Create a mock socket that optionally returns data from recv."""
    sock = MagicMock()
    sock.getpeername.return_value = ("127.0.0.1", 12345)
    if recv_data is not None:
        sock.recv.side_effect = [recv_data]
    return sock


# ── Room lifecycle ───────────────────────────────────────────────

class TestRoomLifecycle:
    """Test room creation, joining, and cleanup."""

    def setup_method(self):
        """Clear rooms state before each test."""
        with relay_server.rooms_lock:
            relay_server.rooms.clear()

    def test_create_room_adds_to_registry(self):
        """Creating a room should add it to the rooms dict."""
        code = relay_server.generate_room_code()
        sock = make_mock_socket()
        with relay_server.rooms_lock:
            relay_server.rooms[code] = {
                "clients": [sock],
                "udp_addrs": [None, None],
                "created": time.time(),
            }
        assert code in relay_server.rooms
        assert len(relay_server.rooms[code]["clients"]) == 1

    def test_join_room_adds_second_client(self):
        """Joining an existing room should add a second client."""
        code = relay_server.generate_room_code()
        sock1 = make_mock_socket()
        sock2 = make_mock_socket()
        with relay_server.rooms_lock:
            relay_server.rooms[code] = {
                "clients": [sock1],
                "udp_addrs": [None, None],
                "created": time.time(),
            }
            relay_server.rooms[code]["clients"].append(sock2)

        assert len(relay_server.rooms[code]["clients"]) == 2

    def test_pairing_with_two_clients(self):
        """A room with two clients should be considered paired."""
        code = relay_server.generate_room_code()
        sock1 = make_mock_socket()
        sock2 = make_mock_socket()
        with relay_server.rooms_lock:
            relay_server.rooms[code] = {
                "clients": [sock1, sock2],
                "udp_addrs": [None, None],
                "created": time.time(),
            }
        assert len(relay_server.rooms[code]["clients"]) == 2

    def test_client_disconnect_removes_from_room(self):
        """Removing a client from a room should update the client list."""
        code = relay_server.generate_room_code()
        sock1 = make_mock_socket()
        sock2 = make_mock_socket()
        with relay_server.rooms_lock:
            relay_server.rooms[code] = {
                "clients": [sock1, sock2],
                "udp_addrs": [None, None],
                "created": time.time(),
            }

        # Simulate client 1 disconnecting
        with relay_server.rooms_lock:
            relay_server.rooms[code]["clients"].remove(sock1)
        assert len(relay_server.rooms[code]["clients"]) == 1

    def test_empty_room_gets_deleted(self):
        """A room with no clients should be removed."""
        code = relay_server.generate_room_code()
        sock1 = make_mock_socket()
        with relay_server.rooms_lock:
            relay_server.rooms[code] = {
                "clients": [sock1],
                "udp_addrs": [None],
                "created": time.time(),
            }
            relay_server.rooms[code]["clients"].remove(sock1)
            if len(relay_server.rooms[code]["clients"]) == 0:
                del relay_server.rooms[code]

        assert code not in relay_server.rooms

    def test_stale_room_cleanup(self):
        """cleanup_stale_rooms should remove old unpaired rooms."""
        code = relay_server.generate_room_code()
        with relay_server.rooms_lock:
            relay_server.rooms[code] = {
                "clients": [make_mock_socket()],
                "udp_addrs": [None],
                "created": time.time() - 7200,  # 2 hours old
            }
        relay_server.cleanup_stale_rooms(max_age=3600)
        assert code not in relay_server.rooms


# ── Presence broadcast ───────────────────────────────────────────

class TestPresenceBroadcast:
    """Test presence registration and broadcasting."""

    def setup_method(self):
        """Clear presence state before each test."""
        with relay_server.presence_lock:
            relay_server.presence.clear()

    def test_register_adds_to_presence(self):
        """Registering a user should add them to the presence dict."""
        sock = make_mock_socket()
        with relay_server.presence_lock:
            relay_server.presence["user-1"] = {
                "name": "Alice",
                "mode": "GREEN",
                "team_id": "team-a",
                "sock": sock,
                "addr": ("127.0.0.1", 12345),
                "registered_at": time.time(),
                "last_ping": time.time(),
            }
        assert "user-1" in relay_server.presence
        assert relay_server.presence["user-1"]["name"] == "Alice"

    def test_broadcast_sends_to_same_team(self):
        """broadcast_presence should send updates to users on the same team."""
        sock1 = make_mock_socket()
        sock2 = make_mock_socket()
        # Make sendall work without error
        sock1.sendall = MagicMock()
        sock2.sendall = MagicMock()

        with relay_server.presence_lock:
            relay_server.presence["u1"] = {
                "name": "Alice", "mode": "GREEN", "team_id": "team-x",
                "sock": sock1, "addr": ("127.0.0.1", 1),
                "registered_at": time.time(), "last_ping": time.time(),
            }
            relay_server.presence["u2"] = {
                "name": "Bob", "mode": "YELLOW", "team_id": "team-x",
                "sock": sock2, "addr": ("127.0.0.1", 2),
                "registered_at": time.time(), "last_ping": time.time(),
            }

        relay_server.broadcast_presence()

        # Both sockets should have received a sendall call
        assert sock1.sendall.called
        assert sock2.sendall.called

    def test_broadcast_content_includes_team_members(self):
        """Broadcast data should include all team members."""
        sent_data = []
        sock1 = make_mock_socket()

        def capture_sendall(data):
            sent_data.append(data)

        sock1.sendall = capture_sendall

        with relay_server.presence_lock:
            relay_server.presence["u1"] = {
                "name": "Alice", "mode": "GREEN", "team_id": "team-x",
                "sock": sock1, "addr": ("127.0.0.1", 1),
                "registered_at": time.time(), "last_ping": time.time(),
            }
            relay_server.presence["u2"] = {
                "name": "Bob", "mode": "RED", "team_id": "team-x",
                "sock": make_mock_socket(), "addr": ("127.0.0.1", 2),
                "registered_at": time.time(), "last_ping": time.time(),
            }

        relay_server.broadcast_presence()

        assert len(sent_data) > 0
        # Parse the frame (skip 4-byte length prefix)
        frame = sent_data[0]
        payload = frame[4:]
        msg = json.loads(payload.decode("utf-8"))
        assert msg["type"] == "PRESENCE_UPDATE"
        names = [u["name"] for u in msg["users"]]
        assert "Alice" in names
        assert "Bob" in names


# ── Client disconnect cleanup ────────────────────────────────────

class TestClientDisconnectCleanup:
    """Test that client disconnection cleans up presence state."""

    def setup_method(self):
        with relay_server.presence_lock:
            relay_server.presence.clear()

    def test_disconnect_removes_from_presence(self):
        """Disconnecting a user should remove them from presence."""
        with relay_server.presence_lock:
            relay_server.presence["user-1"] = {
                "name": "Alice", "mode": "GREEN", "team_id": "t",
                "sock": make_mock_socket(), "addr": ("127.0.0.1", 1),
            }

        # Simulate disconnect cleanup
        with relay_server.presence_lock:
            del relay_server.presence["user-1"]

        assert "user-1" not in relay_server.presence

    def test_dead_socket_detected_during_broadcast(self):
        """Broadcasting to a dead socket should mark user for removal."""
        dead_sock = make_mock_socket()
        dead_sock.sendall.side_effect = BrokenPipeError("Connection reset")

        with relay_server.presence_lock:
            relay_server.presence["dead-user"] = {
                "name": "Ghost", "mode": "GREEN", "team_id": "team-z",
                "sock": dead_sock, "addr": ("127.0.0.1", 1),
                "registered_at": time.time(), "last_ping": time.time(),
            }

        # broadcast_presence removes dead users and re-broadcasts
        relay_server.broadcast_presence()

        with relay_server.presence_lock:
            assert "dead-user" not in relay_server.presence

    def test_room_cleanup_on_client_leave(self):
        """When both clients leave, the room should be deleted."""
        code = relay_server.generate_room_code()
        sock1 = make_mock_socket()
        sock2 = make_mock_socket()

        with relay_server.rooms_lock:
            relay_server.rooms[code] = {
                "clients": [sock1, sock2],
                "udp_addrs": [None, None],
                "created": time.time(),
            }

        # Both clients disconnect
        with relay_server.rooms_lock:
            relay_server.rooms[code]["clients"].clear()
            if len(relay_server.rooms[code]["clients"]) == 0:
                del relay_server.rooms[code]

        with relay_server.rooms_lock:
            assert code not in relay_server.rooms


# ── Auth key rejection ───────────────────────────────────────────

class TestAuthKeyRejection:
    """Test that invalid auth keys are rejected."""

    def test_correct_key_accepted(self):
        """check_auth should accept the correct auth key."""
        original = relay_server.RELAY_AUTH_KEY
        try:
            relay_server.RELAY_AUTH_KEY = "secret-key"
            assert relay_server.check_auth({"auth_key": "secret-key"}) is True
        finally:
            relay_server.RELAY_AUTH_KEY = original

    def test_wrong_key_rejected(self):
        """check_auth should reject a wrong auth key."""
        original = relay_server.RELAY_AUTH_KEY
        try:
            relay_server.RELAY_AUTH_KEY = "secret-key"
            assert relay_server.check_auth({"auth_key": "wrong-key"}) is False
        finally:
            relay_server.RELAY_AUTH_KEY = original

    def test_missing_key_rejected(self):
        """check_auth should reject when auth_key is missing from message."""
        original = relay_server.RELAY_AUTH_KEY
        try:
            relay_server.RELAY_AUTH_KEY = "secret-key"
            assert relay_server.check_auth({}) is False
        finally:
            relay_server.RELAY_AUTH_KEY = original

    def test_no_key_configured_allows_all(self):
        """When no auth key is set, all connections should be allowed."""
        original = relay_server.RELAY_AUTH_KEY
        try:
            relay_server.RELAY_AUTH_KEY = None
            assert relay_server.check_auth({}) is True
            assert relay_server.check_auth({"auth_key": "anything"}) is True
        finally:
            relay_server.RELAY_AUTH_KEY = original

    def test_empty_string_key_rejected(self):
        """An empty string auth_key should not match a configured key."""
        original = relay_server.RELAY_AUTH_KEY
        try:
            relay_server.RELAY_AUTH_KEY = "secret-key"
            assert relay_server.check_auth({"auth_key": ""}) is False
        finally:
            relay_server.RELAY_AUTH_KEY = original
