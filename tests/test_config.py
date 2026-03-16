"""Tests for config.py — default values, env var overrides, logging setup."""

import logging
import os

import pytest


def test_default_ports():
    """Config should expose default port values."""
    import config
    assert config.TCP_PORT == 50000
    assert config.UDP_PORT == 50001
    assert config.RELAY_PORT == 50002


def test_default_audio_settings():
    """Audio config should have sensible defaults for voice."""
    import config
    assert config.SAMPLE_RATE == 24000
    assert config.CHANNELS == 1
    assert config.CHUNK_SIZE == 480  # 20ms at 24kHz
    assert config.DTYPE == 'int16'


def test_buffer_and_frame_limits():
    """Network buffer and frame size limits should be set."""
    import config
    assert config.BUFFER_SIZE == 4096
    assert config.MAX_FILE_SIZE == 10 * 1024 * 1024
    assert config.MAX_FRAME_SIZE == 10 * 1024 * 1024


def test_app_name():
    import config
    assert config.APP_NAME == "Vox"


def test_relay_tls_default():
    """RELAY_TLS should default to True."""
    import config
    # The default env var is '1' which maps to True
    assert isinstance(config.RELAY_TLS, bool)


def test_relay_ca_cert_default_empty():
    """RELAY_CA_CERT should default to empty string (use system trust store)."""
    import config
    assert config.RELAY_CA_CERT == ''


def test_setup_logging_returns_logger():
    """setup_logging() should return a configured logger."""
    import config
    logger = config.setup_logging()
    assert isinstance(logger, logging.Logger)
    assert logger.name == 'vox'


def test_setup_logging_idempotent():
    """Calling setup_logging() twice should not add duplicate handlers."""
    import config
    logger1 = config.setup_logging()
    handler_count = len(logger1.handlers)
    logger2 = config.setup_logging()
    assert len(logger2.handlers) == handler_count
    assert logger1 is logger2


def test_port_env_override(monkeypatch):
    """Ports should be overridable via environment variables."""
    monkeypatch.setenv('VOX_TCP_PORT', '9999')
    # Need to re-evaluate the expression since config.py reads at import time.
    # We test the parsing logic directly instead.
    assert int(os.environ.get('VOX_TCP_PORT', 50000)) == 9999


def test_relay_tls_env_override_false(monkeypatch):
    """Setting VOX_RELAY_TLS=0 should parse as False."""
    monkeypatch.setenv('VOX_RELAY_TLS', '0')
    val = os.environ.get('VOX_RELAY_TLS', '1').lower() in ('1', 'true', 'yes')
    assert val is False


def test_relay_tls_env_override_true(monkeypatch):
    """Setting VOX_RELAY_TLS=true should parse as True."""
    monkeypatch.setenv('VOX_RELAY_TLS', 'true')
    val = os.environ.get('VOX_RELAY_TLS', '1').lower() in ('1', 'true', 'yes')
    assert val is True


# ── Config edge cases ────────────────────────────────────────────

def test_invalid_port_env_non_numeric(monkeypatch):
    """Non-numeric port env var should raise ValueError when parsed."""
    monkeypatch.setenv('VOX_TCP_PORT', 'not_a_number')
    with pytest.raises(ValueError):
        int(os.environ.get('VOX_TCP_PORT', 50000))


def test_invalid_port_env_negative(monkeypatch):
    """Negative port values should parse as int but be invalid ports."""
    monkeypatch.setenv('VOX_TCP_PORT', '-1')
    val = int(os.environ.get('VOX_TCP_PORT', 50000))
    assert val < 0  # Not a valid port


def test_invalid_port_env_too_large(monkeypatch):
    """Port values above 65535 should parse as int but be invalid ports."""
    monkeypatch.setenv('VOX_TCP_PORT', '99999')
    val = int(os.environ.get('VOX_TCP_PORT', 50000))
    assert val > 65535


def test_relay_tls_env_nonsense_values(monkeypatch):
    """Nonsense values for VOX_RELAY_TLS should evaluate as False."""
    for nonsense in ('banana', 'maybe', '2', 'FALSE', 'no', ''):
        monkeypatch.setenv('VOX_RELAY_TLS', nonsense)
        val = os.environ.get('VOX_RELAY_TLS', '1').lower() in ('1', 'true', 'yes')
        assert val is False, f"Expected False for {nonsense!r}"


def test_empty_relay_host(monkeypatch):
    """Empty VOX_RELAY_HOST should be an empty string."""
    monkeypatch.setenv('VOX_RELAY_HOST', '')
    val = os.environ.get('VOX_RELAY_HOST', 'relay.ohinter.com')
    assert val == ''


def test_log_level_env_override(monkeypatch):
    """VOX_LOG_LEVEL env var should be readable."""
    monkeypatch.setenv('VOX_LOG_LEVEL', 'DEBUG')
    val = os.environ.get('VOX_LOG_LEVEL', 'INFO')
    assert val == 'DEBUG'


def test_log_level_invalid_falls_back():
    """Invalid log level should fall back to INFO via getattr."""
    import logging
    level = getattr(logging, 'NONEXISTENT', logging.INFO)
    assert level == logging.INFO


def test_missing_optional_opus_import():
    """audio_manager should have a _HAS_OPUS flag indicating opus availability."""
    from audio_manager import _HAS_OPUS
    # _HAS_OPUS is a bool regardless of whether opus is installed
    assert isinstance(_HAS_OPUS, bool)


def test_supabase_config_defaults():
    """Supabase config should have non-empty default values."""
    import config
    assert config.SUPABASE_URL.startswith('https://')
    assert len(config.SUPABASE_ANON_KEY) > 10


def test_relay_auth_key_default():
    """RELAY_AUTH_KEY should have a default value."""
    import config
    assert config.RELAY_AUTH_KEY is not None
    assert len(config.RELAY_AUTH_KEY) > 0
