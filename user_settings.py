import json
import os
import sys
import uuid
import hashlib

def _config_dir():
    """Return platform-appropriate config directory for Office Hours."""
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA', os.path.expanduser('~'))
        d = os.path.join(base, 'OfficeHours')
    elif sys.platform == 'darwin':
        d = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'OfficeHours')
    else:
        d = os.path.join(os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config')), 'officehours')
    os.makedirs(d, exist_ok=True)
    return d

_CFG = _config_dir()
SETTINGS_FILE = os.path.join(_CFG, 'settings.json')

# Migrate from old location if it exists
_OLD_SETTINGS = os.path.expanduser("~/.officehours.json")
if os.path.exists(_OLD_SETTINGS) and not os.path.exists(SETTINGS_FILE):
    try:
        import shutil
        shutil.move(_OLD_SETTINGS, SETTINGS_FILE)
    except Exception:
        pass

# TLS cert/key paths for LAN TOFU
CERT_FILE = os.path.join(_CFG, 'lan_cert.pem')
KEY_FILE = os.path.join(_CFG, 'lan_key.pem')

# Migrate old cert/key files
for _old, _new in [
    (os.path.expanduser("~/.officehours_cert.pem"), CERT_FILE),
    (os.path.expanduser("~/.officehours_key.pem"), KEY_FILE),
]:
    if os.path.exists(_old) and not os.path.exists(_new):
        try:
            import shutil
            shutil.move(_old, _new)
        except Exception:
            pass

def load_settings():
    """Load user settings from disk, or return defaults"""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_settings(settings):
    """Save user settings to disk"""
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)

def get_display_name():
    """Get the user's display name, or None if not set"""
    return load_settings().get("display_name")

def get_user_id():
    """Get or create a persistent user ID (full UUID for collision resistance)"""
    settings = load_settings()
    user_id = settings.get("user_id", "")
    # Migrate short IDs (8 chars) to full UUIDs
    if len(user_id) < 16:
        settings["user_id"] = str(uuid.uuid4())
        save_settings(settings)
    return settings["user_id"]

def set_display_name(name):
    """Set the user's display name"""
    settings = load_settings()
    settings["display_name"] = name
    # Ensure user_id exists
    if not settings.get("user_id") or len(settings.get("user_id", "")) < 16:
        settings["user_id"] = str(uuid.uuid4())
    save_settings(settings)

def get_ptt_hotkey():
    """Get the configured PTT hotkey name, or None for default."""
    return load_settings().get("ptt_hotkey")

def set_ptt_hotkey(key_name):
    """Set the PTT hotkey name."""
    settings = load_settings()
    settings["ptt_hotkey"] = key_name
    save_settings(settings)

# ── Active Team ──────────────────────────────────────────────

def get_active_team():
    """Get the active team ID, or None if no team selected."""
    return load_settings().get("active_team_id")

def set_active_team(team_id):
    """Set the active team ID."""
    settings = load_settings()
    settings["active_team_id"] = team_id
    save_settings(settings)

def get_active_team_name():
    """Get the active team name (cached locally for display)."""
    return load_settings().get("active_team_name")

def set_active_team_name(name):
    """Cache the active team name locally."""
    settings = load_settings()
    settings["active_team_name"] = name
    save_settings(settings)

# ── Trusted Peers (TOFU) ─────────────────────────────────────

def get_trusted_peers():
    """Get dict of trusted peer fingerprints: {ip_or_name: sha256_hex}"""
    settings = load_settings()
    return settings.get("trusted_peers", {})

def trust_peer(peer_id, fingerprint):
    """Store a peer's certificate fingerprint as trusted"""
    settings = load_settings()
    if "trusted_peers" not in settings:
        settings["trusted_peers"] = {}
    settings["trusted_peers"][peer_id] = fingerprint
    save_settings(settings)

def get_peer_fingerprint(peer_id):
    """Get the stored fingerprint for a peer, or None"""
    return get_trusted_peers().get(peer_id)

def compute_cert_fingerprint(cert_der):
    """Compute SHA-256 fingerprint of a DER-encoded certificate"""
    digest = hashlib.sha256(cert_der).hexdigest()
    # Format as colon-separated pairs for readability
    return ':'.join(digest[i:i+2] for i in range(0, len(digest), 2))

# ── LAN TLS Certificate Management ──────────────────────────

def ensure_lan_cert():
    """Generate a self-signed cert/key for LAN TOFU if they don't exist.
    Returns (cert_path, key_path) or (None, None) if generation fails."""
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        return CERT_FILE, KEY_FILE

    try:
        import ssl
        import datetime
        import tempfile

        # Use the cryptography library if available, otherwise fall back to openssl CLI
        try:
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec

            key = ec.generate_private_key(ec.SECP256R1())
            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, "Office Hours LAN"),
            ])
            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.datetime.utcnow())
                .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
                .sign(key, hashes.SHA256())
            )

            with open(KEY_FILE, 'wb') as f:
                f.write(key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption()
                ))
            os.chmod(KEY_FILE, 0o600)

            with open(CERT_FILE, 'wb') as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))

            return CERT_FILE, KEY_FILE

        except ImportError:
            # Fallback: use openssl command line
            import subprocess
            subprocess.run([
                'openssl', 'req', '-x509', '-newkey', 'ec',
                '-pkeyopt', 'ec_paramgen_curve:prime256v1',
                '-keyout', KEY_FILE, '-out', CERT_FILE,
                '-days', '3650', '-nodes',
                '-subj', '/CN=Office Hours LAN'
            ], check=True, capture_output=True)
            os.chmod(KEY_FILE, 0o600)
            return CERT_FILE, KEY_FILE

    except Exception as e:
        print(f"Failed to generate LAN certificate: {e}")
        return None, None
