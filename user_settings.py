import hashlib
import json
import os
import sys
import uuid


def _win_restrict_file(filepath):
    """Restrict file access to current user only on Windows (equivalent of chmod 600)."""
    if sys.platform != 'win32':
        return
    try:
        import subprocess
        # Remove inherited permissions and grant only current user full control
        username = os.environ.get('USERNAME', '')
        if username:
            subprocess.run(
                ['icacls', filepath, '/inheritance:r',
                 '/grant:r', f'{username}:(F)'],
                capture_output=True, timeout=10
            )
    except Exception:
        pass  # Non-critical — best effort

def _config_dir():
    """Return platform-appropriate config directory for Vox.
    Migrates from old 'OfficeHours' directory if it exists."""
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA', os.path.expanduser('~'))
        d = os.path.join(base, 'Vox')
        _old_d = os.path.join(base, 'OfficeHours')
    elif sys.platform == 'darwin':
        d = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'Vox')
        _old_d = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'OfficeHours')
    else:
        d = os.path.join(os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config')), 'vox')
        _old_d = os.path.join(os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config')), 'officehours')
    # Migrate old config directory
    if os.path.isdir(_old_d) and not os.path.exists(d):
        try:
            import shutil
            shutil.move(_old_d, d)
        except Exception:
            pass
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
            with open(SETTINGS_FILE) as f:
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

def get_auth_session():
    """Return the stored auth session dict, or None if not logged in.
    Session contains: access_token, refresh_token, expires_at, user_id, email."""
    settings = load_settings()
    session = settings.get("auth_session")
    if session and session.get("access_token"):
        return session
    return None


def save_auth_session(session):
    """Store an auth session dict to settings.
    Expected keys: access_token, refresh_token, expires_at, user_id, email."""
    settings = load_settings()
    settings["auth_session"] = {
        "access_token": session.get("access_token", ""),
        "refresh_token": session.get("refresh_token", ""),
        "expires_at": session.get("expires_at", 0),
        "user_id": session.get("user_id", ""),
        "email": session.get("email", ""),
    }
    save_settings(settings)


def clear_auth_session():
    """Remove the auth session from settings."""
    settings = load_settings()
    settings.pop("auth_session", None)
    save_settings(settings)


def is_logged_in():
    """Check if an auth session exists with a valid access token."""
    return get_auth_session() is not None


def get_ptt_hotkey():
    """Get the configured PTT hotkey name, or None for default."""
    return load_settings().get("ptt_hotkey")

def set_ptt_hotkey(key_name):
    """Set the PTT hotkey name."""
    settings = load_settings()
    settings["ptt_hotkey"] = key_name
    save_settings(settings)

# ── Active Team ──────────────────────────────────────────────

def get_active_team_ids():
    """Get the list of active team IDs (teams with presence enabled)."""
    return load_settings().get("active_team_ids", [])

def set_active_team_ids(team_ids):
    """Save the list of active team IDs."""
    settings = load_settings()
    settings["active_team_ids"] = list(team_ids)
    save_settings(settings)

# ── Stream Deck Guide ────────────────────────────────────────

def get_deck_guide_dismissed():
    """Check if user dismissed the Stream Deck setup guide."""
    return load_settings().get("deck_guide_dismissed", False)

def set_deck_guide_dismissed(dismissed=True):
    """Mark the Stream Deck setup guide as dismissed."""
    settings = load_settings()
    settings["deck_guide_dismissed"] = dismissed
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
        import datetime

        # Use the cryptography library if available, otherwise fall back to openssl CLI
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.x509.oid import NameOID

            key = ec.generate_private_key(ec.SECP256R1())
            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, "Vox LAN"),
            ])
            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.datetime.now(datetime.UTC))
                .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=3650))
                .sign(key, hashes.SHA256())
            )

            with open(KEY_FILE, 'wb') as f:
                f.write(key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption()
                ))
            if sys.platform == 'win32':
                _win_restrict_file(KEY_FILE)
            else:
                os.chmod(KEY_FILE, 0o600)

            with open(CERT_FILE, 'wb') as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))

            return CERT_FILE, KEY_FILE

        except ImportError:
            if sys.platform == 'win32':
                # No openssl CLI on Windows typically; cryptography is required
                print("cryptography package required on Windows for LAN TLS")
                print("  Install with: pip install cryptography")
                return None, None
            # Fallback: use openssl command line (macOS/Linux)
            import subprocess
            subprocess.run([
                'openssl', 'req', '-x509', '-newkey', 'ec',
                '-pkeyopt', 'ec_paramgen_curve:prime256v1',
                '-keyout', KEY_FILE, '-out', CERT_FILE,
                '-days', '3650', '-nodes',
                '-subj', '/CN=Vox LAN'
            ], check=True, capture_output=True)
            os.chmod(KEY_FILE, 0o600)
            return CERT_FILE, KEY_FILE

    except Exception as e:
        print(f"Failed to generate LAN certificate: {e}")
        return None, None
