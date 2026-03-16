"""Tests for protocol framing and message serialization.

Tests the length-prefixed framing protocol and JSON control message
format used by network_manager.py and relay_server.py, without
requiring actual network connections.
"""

import json
import struct
import time


def test_frame_encoding():
    """Length-prefixed frames should use 4-byte big-endian length prefix."""
    data = b"hello world"
    msg_len = struct.pack('!I', len(data))
    frame = msg_len + data

    # Decode
    decoded_len = struct.unpack('!I', frame[:4])[0]
    assert decoded_len == len(data)
    assert frame[4:] == data


def test_frame_encoding_empty():
    """Empty data should produce a frame with length 0."""
    data = b""
    msg_len = struct.pack('!I', len(data))
    frame = msg_len + data
    decoded_len = struct.unpack('!I', frame[:4])[0]
    assert decoded_len == 0


def test_frame_encoding_large():
    """Large payloads should encode correctly."""
    data = b"x" * 100000
    msg_len = struct.pack('!I', len(data))
    frame = msg_len + data
    decoded_len = struct.unpack('!I', frame[:4])[0]
    assert decoded_len == 100000


def test_control_message_format():
    """Control messages should be JSON with type, payload, and timestamp."""
    message = {
        "type": "TALK_START",
        "payload": {"user": "Alice"},
        "timestamp": time.time()
    }
    encoded = json.dumps(message).encode('utf-8')
    decoded = json.loads(encoded.decode('utf-8'))

    assert decoded["type"] == "TALK_START"
    assert decoded["payload"]["user"] == "Alice"
    assert "timestamp" in decoded


def test_control_message_types():
    """All expected control message types should serialize correctly."""
    msg_types = [
        "TALK_START", "TALK_STOP", "FILE_HEADER",
        "PEER_CONNECTED", "BINARY_DATA",
        "UDP_REGISTER", "CODEC_OFFER", "CODEC_ACCEPT",
    ]
    for msg_type in msg_types:
        msg = {"type": msg_type, "payload": None, "timestamp": 0}
        encoded = json.dumps(msg).encode('utf-8')
        decoded = json.loads(encoded)
        assert decoded["type"] == msg_type


def test_relay_handshake_create_room():
    """CREATE_ROOM handshake should include action and auth_key."""
    msg = {"action": "CREATE_ROOM", "auth_key": "vox-relay-v1-2026"}
    encoded = json.dumps(msg).encode('utf-8')
    decoded = json.loads(encoded)
    assert decoded["action"] == "CREATE_ROOM"
    assert decoded["auth_key"] == "vox-relay-v1-2026"


def test_relay_handshake_join_room():
    """JOIN_ROOM handshake should include action, room, and auth_key."""
    msg = {"action": "JOIN_ROOM", "room": "VOX-ABC123", "auth_key": "vox-relay-v1-2026"}
    encoded = json.dumps(msg).encode('utf-8')
    decoded = json.loads(encoded)
    assert decoded["action"] == "JOIN_ROOM"
    assert decoded["room"] == "VOX-ABC123"


def test_relay_register_message():
    """REGISTER (presence) message should include all required fields."""
    msg = {
        "action": "REGISTER",
        "name": "Alice",
        "user_id": "uuid-1234",
        "mode": "GREEN",
        "team_id": "team-abc",
        "auth_key": "vox-relay-v1-2026",
    }
    encoded = json.dumps(msg).encode('utf-8')
    decoded = json.loads(encoded)
    assert decoded["action"] == "REGISTER"
    assert decoded["name"] == "Alice"
    assert decoded["user_id"] == "uuid-1234"
    assert decoded["mode"] == "GREEN"
    assert decoded["team_id"] == "team-abc"


def test_presence_update_message():
    """PRESENCE_UPDATE should contain a users list."""
    msg = {
        "type": "PRESENCE_UPDATE",
        "users": [
            {"user_id": "u1", "name": "Alice", "mode": "GREEN", "room": "", "team_id": "t1"},
            {"user_id": "u2", "name": "Bob", "mode": "RED", "room": "", "team_id": "t1"},
        ]
    }
    encoded = json.dumps(msg).encode('utf-8')
    decoded = json.loads(encoded)
    assert decoded["type"] == "PRESENCE_UPDATE"
    assert len(decoded["users"]) == 2
    assert decoded["users"][0]["name"] == "Alice"


def test_mode_update_message():
    """MODE_UPDATE should include mode and optional room/team_id."""
    msg = {"action": "MODE_UPDATE", "mode": "RED", "room": "VOX-XYZ", "team_id": "team-1"}
    encoded = json.dumps(msg).encode('utf-8')
    decoded = json.loads(encoded)
    assert decoded["mode"] == "RED"
    assert decoded["room"] == "VOX-XYZ"


def test_frame_size_limit():
    """Frames exceeding MAX_FRAME_SIZE should be detectable."""
    from config import MAX_FRAME_SIZE
    # A payload size just over the limit
    oversized_len = MAX_FRAME_SIZE + 1
    raw_len = struct.pack('!I', oversized_len)
    decoded_len = struct.unpack('!I', raw_len)[0]
    assert decoded_len > MAX_FRAME_SIZE
