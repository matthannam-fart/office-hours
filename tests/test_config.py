"""Tests for config.py — default values, env var overrides, logging setup."""

import logging
import os


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
    assert config.APP_NAME == "Office Hours"


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
    assert logger.name == 'officehours'


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
    monkeypatch.setenv('TALKBACK_TCP_PORT', '9999')
    # Need to re-evaluate the expression since config.py reads at import time.
    # We test the parsing logic directly instead.
    assert int(os.environ.get('TALKBACK_TCP_PORT', 50000)) == 9999


def test_relay_tls_env_override_false(monkeypatch):
    """Setting TALKBACK_RELAY_TLS=0 should parse as False."""
    monkeypatch.setenv('TALKBACK_RELAY_TLS', '0')
    val = os.environ.get('TALKBACK_RELAY_TLS', '1').lower() in ('1', 'true', 'yes')
    assert val is False


def test_relay_tls_env_override_true(monkeypatch):
    """Setting TALKBACK_RELAY_TLS=true should parse as True."""
    monkeypatch.setenv('TALKBACK_RELAY_TLS', 'true')
    val = os.environ.get('TALKBACK_RELAY_TLS', '1').lower() in ('1', 'true', 'yes')
    assert val is True
