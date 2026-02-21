import json
import os
import uuid

SETTINGS_FILE = os.path.expanduser("~/.officehours.json")

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
    """Get or create a persistent user ID"""
    settings = load_settings()
    if "user_id" not in settings:
        settings["user_id"] = str(uuid.uuid4())[:8]
        save_settings(settings)
    return settings["user_id"]

def set_display_name(name):
    """Set the user's display name"""
    settings = load_settings()
    settings["display_name"] = name
    # Ensure user_id exists
    if "user_id" not in settings:
        settings["user_id"] = str(uuid.uuid4())[:8]
    save_settings(settings)
