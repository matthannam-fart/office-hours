import os

# Network Configuration (overridable via environment variables)
TCP_PORT = int(os.environ.get('TALKBACK_TCP_PORT', 50000))
UDP_PORT = int(os.environ.get('TALKBACK_UDP_PORT', 50001))
BUFFER_SIZE = 4096    # Network buffer size

# Audio Configuration (optimised for clear, low-latency voice)
SAMPLE_RATE = 24000   # Hz — super-wideband (clear voice without excess bandwidth)
CHANNELS = 1          # Mono
CHUNK_SIZE = 480      # 20 ms per frame at 24 kHz
DTYPE = 'int16'       # Audio data type

# Remote / Relay Configuration
RELAY_HOST = os.environ.get('TALKBACK_RELAY_HOST', '165.22.175.71')
RELAY_PORT = int(os.environ.get('TALKBACK_RELAY_PORT', 50002))

# TLS Configuration
# Set TALKBACK_RELAY_TLS=1 to enable encrypted connections to the relay server
# (requires TLS certs on the relay — disabled by default for now)
RELAY_TLS = os.environ.get('TALKBACK_RELAY_TLS', '0').lower() in ('1', 'true', 'yes')
# Path to custom CA cert (for self-signed relay). Empty = use system trust store (Let's Encrypt)
RELAY_CA_CERT = os.environ.get('TALKBACK_RELAY_CA_CERT', '')

# Supabase Configuration (teams / user management)
SUPABASE_URL = os.environ.get('OFFICEHOURS_SUPABASE_URL', 'https://kfxiawqlboqnwzkxbyid.supabase.co')
SUPABASE_ANON_KEY = os.environ.get('OFFICEHOURS_SUPABASE_KEY', 'sb_publishable_5zTaoo3rYTDpXv0gHN0c8g_PEkHyXIO')

# Security Configuration
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB max voicemail file size
MAX_FRAME_SIZE = 10 * 1024 * 1024  # 10MB max TCP frame size

# Relay authentication — gates access to the relay server.
# All clients must send this key with their first message.
# Later this can be replaced with per-user Supabase JWTs.
RELAY_AUTH_KEY = os.environ.get('OFFICEHOURS_RELAY_KEY', 'oh-relay-v1-2026')

# Application Configuration
APP_NAME = "Office Hours"
LOG_LEVEL = os.environ.get('TALKBACK_LOG_LEVEL', 'INFO')

# ── Logging Setup ─────────────────────────────────────────────
import logging

def setup_logging():
    """Configure the application-wide logger."""
    logger = logging.getLogger('officehours')
    if logger.handlers:
        return logger  # Already configured
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    fmt = logging.Formatter('[%(asctime)s] %(name)s.%(module)s: %(message)s', datefmt='%H:%M:%S')
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger

log = setup_logging()
