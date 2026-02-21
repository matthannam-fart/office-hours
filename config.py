import os

# Network Configuration (overridable via environment variables)
TCP_PORT = int(os.environ.get('TALKBACK_TCP_PORT', 50000))
UDP_PORT = int(os.environ.get('TALKBACK_UDP_PORT', 50001))
BUFFER_SIZE = 4096    # Network buffer size

# Audio Configuration
SAMPLE_RATE = 44100   # Hz
CHANNELS = 1          # Mono
CHUNK_SIZE = 1024     # Audio chunk size
DTYPE = 'int16'       # Audio data type

# Remote / Relay Configuration
RELAY_HOST = os.environ.get('TALKBACK_RELAY_HOST', 'ohinter.com')
RELAY_PORT = int(os.environ.get('TALKBACK_RELAY_PORT', 50002))

# Application Configuration
APP_NAME = "Office Hours"
LOG_LEVEL = "INFO"
