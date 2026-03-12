"""
hotkey_manager.py — Global Push-to-Talk Hotkey
Cross-platform (Mac + Windows + Linux) global keyboard listener.
Uses pynput for system-wide key capture without needing app focus.
"""

# Default PTT key — F24 is unused on most keyboards, safe default.
# Users can rebind to any key via settings.
DEFAULT_PTT_KEY = '`'

# Map of friendly names → pynput key references
_SPECIAL_KEYS = {}


def _init_special_keys():
    """Lazy-init special key map (pynput import can be slow)."""
    global _SPECIAL_KEYS
    if _SPECIAL_KEYS:
        return
    try:
        from pynput.keyboard import Key
        # Start with keys available on all platforms
        _SPECIAL_KEYS = {
            'f1': Key.f1, 'f2': Key.f2, 'f3': Key.f3, 'f4': Key.f4,
            'f5': Key.f5, 'f6': Key.f6, 'f7': Key.f7, 'f8': Key.f8,
            'f9': Key.f9, 'f10': Key.f10, 'f11': Key.f11, 'f12': Key.f12,
            'f13': Key.f13, 'f14': Key.f14, 'f15': Key.f15,
            'f16': Key.f16, 'f17': Key.f17, 'f18': Key.f18,
            'f19': Key.f19, 'f20': Key.f20,
            'caps_lock': Key.caps_lock,
            'right_ctrl': Key.ctrl_r,
            'right_shift': Key.shift_r,
            'right_alt': Key.alt_r,
        }
        # Add platform-specific keys only if they exist
        for name, attr in [('scroll_lock', 'scroll_lock'),
                           ('pause', 'pause'),
                           ('insert', 'insert')]:
            if hasattr(Key, attr):
                _SPECIAL_KEYS[name] = getattr(Key, attr)
        # Add F21-F24 if available (not all pynput versions have them)
        for i in range(21, 25):
            attr = f'f{i}'
            if hasattr(Key, attr):
                _SPECIAL_KEYS[attr] = getattr(Key, attr)
    except ImportError:
        pass


class HotkeyManager:
    """Manages a global push-to-talk hotkey.

    Calls `on_press()` when the PTT key is pressed and `on_release()` when released.
    Works system-wide — the app window doesn't need focus.
    """

    def __init__(self, on_press=None, on_release=None, key_name=None, log_callback=None):
        self._on_press = on_press
        self._on_release = on_release
        self._key_name = key_name or DEFAULT_PTT_KEY
        self._log = log_callback or (lambda m: print(f"[Hotkey] {m}"))
        self._listener = None
        self._ptt_held = False
        self._enabled = True
        self._available = False

        # Resolve the target key
        _init_special_keys()
        self._target_key = _SPECIAL_KEYS.get(self._key_name.lower())
        if self._target_key is None:
            # Try as a character key
            if len(self._key_name) == 1:
                self._target_char = self._key_name.lower()
                self._target_key = None  # Will match via char comparison
            else:
                self._log(f"Unknown hotkey '{self._key_name}' — PTT hotkey disabled")
                return
        else:
            self._target_char = None

        self._available = True

    def start(self):
        """Start listening for the global hotkey."""
        if not self._available:
            return
        try:
            from pynput.keyboard import Listener
            self._listener = Listener(
                on_press=self._handle_press,
                on_release=self._handle_release,
            )
            self._listener.daemon = True
            self._listener.start()
            self._log(f"Global PTT hotkey active: {self._key_name.upper()}")
        except ImportError:
            self._log("pynput not installed — global PTT hotkey disabled")
            self._available = False
        except Exception as e:
            self._log(f"Could not start hotkey listener: {e}")
            self._available = False

    def stop(self):
        """Stop the global hotkey listener."""
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self._ptt_held = False

    def set_enabled(self, enabled):
        """Enable/disable the hotkey without stopping the listener."""
        self._enabled = enabled

    def set_key(self, key_name):
        """Change the PTT key at runtime."""
        _init_special_keys()
        self._key_name = key_name
        new_key = _SPECIAL_KEYS.get(key_name.lower())
        if new_key:
            self._target_key = new_key
            self._target_char = None
        elif len(key_name) == 1:
            self._target_key = None
            self._target_char = key_name.lower()
        else:
            self._log(f"Unknown hotkey '{key_name}'")
            return
        self._log(f"PTT hotkey changed to: {key_name.upper()}")

    @property
    def is_available(self):
        return self._available

    @property
    def key_name(self):
        return self._key_name

    def _matches(self, key):
        """Check if a key event matches our target PTT key."""
        if self._target_key is not None:
            return key == self._target_key
        if self._target_char is not None:
            try:
                return hasattr(key, 'char') and key.char and key.char.lower() == self._target_char
            except AttributeError:
                return False
        return False

    def _handle_press(self, key):
        if not self._enabled or self._ptt_held:
            return
        if self._matches(key):
            self._ptt_held = True
            if self._on_press:
                try:
                    self._on_press()
                except Exception as e:
                    self._log(f"PTT press callback error: {e}")

    def _handle_release(self, key):
        if not self._enabled:
            return
        if self._matches(key) and self._ptt_held:
            self._ptt_held = False
            if self._on_release:
                try:
                    self._on_release()
                except Exception as e:
                    self._log(f"PTT release callback error: {e}")
