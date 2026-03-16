"""Tests for relay_server.py — pure logic that doesn't require a running server."""


def test_generate_room_code_format():
    """Room codes should match VOX-XXXXXX format (6 uppercase alphanumeric chars)."""
    from relay_server import generate_room_code
    for _ in range(20):
        code = generate_room_code()
        assert code.startswith("VOX-")
        suffix = code[4:]
        assert len(suffix) == 6
        assert suffix.isalnum()
        assert suffix == suffix.upper()


def test_generate_room_code_uniqueness():
    """Generated room codes should be unique (with high probability)."""
    from relay_server import generate_room_code
    codes = {generate_room_code() for _ in range(100)}
    # With 36^6 possible codes, 100 should all be unique
    assert len(codes) == 100


def test_check_auth_no_key_set():
    """check_auth should return True when no auth key is configured."""
    import relay_server
    original = relay_server.RELAY_AUTH_KEY
    try:
        relay_server.RELAY_AUTH_KEY = None
        assert relay_server.check_auth({}) is True
        assert relay_server.check_auth({"auth_key": "anything"}) is True
    finally:
        relay_server.RELAY_AUTH_KEY = original


def test_check_auth_correct_key():
    """check_auth should return True when auth_key matches."""
    import relay_server
    original = relay_server.RELAY_AUTH_KEY
    try:
        relay_server.RELAY_AUTH_KEY = "test-key-123"
        assert relay_server.check_auth({"auth_key": "test-key-123"}) is True
    finally:
        relay_server.RELAY_AUTH_KEY = original


def test_check_auth_wrong_key():
    """check_auth should return False when auth_key doesn't match."""
    import relay_server
    original = relay_server.RELAY_AUTH_KEY
    try:
        relay_server.RELAY_AUTH_KEY = "test-key-123"
        assert relay_server.check_auth({"auth_key": "wrong"}) is False
        assert relay_server.check_auth({}) is False
    finally:
        relay_server.RELAY_AUTH_KEY = original


def test_check_rate_limit_allows_under_limit():
    """Rate limiter should allow requests under the limit."""
    import relay_server
    # Use a unique IP to avoid interference from other tests
    test_ip = "test_rate_limit_allow_192.0.2.1"
    # Clean up
    with relay_server.join_attempts_lock:
        relay_server.join_attempts.pop(test_ip, None)

    for _ in range(relay_server.RATE_LIMIT_MAX):
        assert relay_server.check_rate_limit(test_ip) is True

    # Clean up
    with relay_server.join_attempts_lock:
        relay_server.join_attempts.pop(test_ip, None)


def test_check_rate_limit_blocks_over_limit():
    """Rate limiter should block requests over the limit."""
    import relay_server
    test_ip = "test_rate_limit_block_192.0.2.2"
    # Clean up
    with relay_server.join_attempts_lock:
        relay_server.join_attempts.pop(test_ip, None)

    for _ in range(relay_server.RATE_LIMIT_MAX):
        relay_server.check_rate_limit(test_ip)

    # Next request should be blocked
    assert relay_server.check_rate_limit(test_ip) is False

    # Clean up
    with relay_server.join_attempts_lock:
        relay_server.join_attempts.pop(test_ip, None)


def test_rate_limit_constants():
    """Rate limit constants should have sensible values."""
    from relay_server import RATE_LIMIT_MAX, RATE_LIMIT_WINDOW
    assert RATE_LIMIT_WINDOW > 0
    assert RATE_LIMIT_MAX > 0
    assert RATE_LIMIT_MAX <= 100  # Sanity check


def test_send_frame_format():
    """send_frame protocol: 4-byte big-endian length + payload."""
    import struct
    data = b'{"action": "CREATE_ROOM"}'
    msg_len = struct.pack('!I', len(data))
    frame = msg_len + data
    # Verify the frame can be decoded
    decoded_len = struct.unpack('!I', frame[:4])[0]
    assert decoded_len == len(data)
    assert frame[4:] == data
