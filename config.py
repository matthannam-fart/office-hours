import os

# Network Configuration (overridable via environment variables)
TCP_PORT = int(os.environ.get('VOX_TCP_PORT', 50000))
UDP_PORT = int(os.environ.get('VOX_UDP_PORT', 50001))
BUFFER_SIZE = 4096    # Network buffer size

# Audio Configuration (optimised for clear, low-latency voice)
SAMPLE_RATE = 24000   # Hz — super-wideband (clear voice without excess bandwidth)
CHANNELS = 1          # Mono
CHUNK_SIZE = 480      # 20 ms per frame at 24 kHz
DTYPE = 'int16'       # Audio data type

# Remote / Relay Configuration
RELAY_HOST = os.environ.get('VOX_RELAY_HOST', 'relay.ohinter.com')
RELAY_PORT = int(os.environ.get('VOX_RELAY_PORT', 50002))

# TLS Configuration
# Encrypted connections to the relay server (Let's Encrypt cert)
RELAY_TLS = os.environ.get('VOX_RELAY_TLS', '1').lower() in ('1', 'true', 'yes')
# Path to custom CA cert (for self-signed relay). Empty = use system trust store (Let's Encrypt)
RELAY_CA_CERT = os.environ.get('VOX_RELAY_CA_CERT', '')

# Supabase Configuration (teams / user management)
SUPABASE_URL = os.environ.get('VOX_SUPABASE_URL', 'https://kfxiawqlboqnwzkxbyid.supabase.co')
SUPABASE_ANON_KEY = os.environ.get('VOX_SUPABASE_KEY', 'sb_publishable_5zTaoo3rYTDpXv0gHN0c8g_PEkHyXIO')

# Security Configuration
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB max voicemail file size
MAX_FRAME_SIZE = 10 * 1024 * 1024  # 10MB max TCP frame size

# Relay authentication — gates access to the relay server.
# All clients must send this key with their first message.
# Later this can be replaced with per-user Supabase JWTs.
RELAY_AUTH_KEY = os.environ.get('VOX_RELAY_KEY', 'vox-relay-v1-2026')

# Application Configuration
APP_NAME = "Vox"
LOG_LEVEL = os.environ.get('VOX_LOG_LEVEL', 'INFO')

# ── Logging Setup ─────────────────────────────────────────────
import logging


def setup_logging():
    """Configure the application-wide logger."""
    logger = logging.getLogger('vox')
    if logger.handlers:
        return logger  # Already configured
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    fmt = logging.Formatter('[%(asctime)s] %(name)s.%(module)s: %(message)s', datefmt='%H:%M:%S')
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger

log = setup_logging()
