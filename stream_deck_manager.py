import os
import sys
import threading
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper
from PIL import Image, ImageDraw, ImageFont

# ── Key Layout ────────────────────────────────────────────────
# Optimized for 3x3 minimum (rows map naturally to any size):
#
# 3x3 (9 keys):             5x3 (15 keys):
#   0=TALK 1=MODE 2=LOGO      0=TALK 1=MODE 2=LOGO 3=-- 4=--
#   3=TEAM 4=USER 5=WINDOW    5=TEAM 6=USER 7=WIN  8=-- 9=--
#   6=--   7=--   8=--       10=--  11=--  12=--  13=-- 14=--

KEY_TALK = 0
KEY_MODE = 1
KEY_LOGO = 2
# Row 2 keys — offset by number of columns (set at runtime)
# 3-col: 3,4,5   5-col: 5,6,7
KEY_TEAM_OFFSET = 0   # +cols from row start
KEY_USER_OFFSET = 1
KEY_WINDOW_OFFSET = 2

# ── Colors ────────────────────────────────────────────────────
OH_TEAL = (113, 173, 163)          # #71ada3 — from the OH logo
OH_TEAL_DIM = (40, 65, 60)         # Dimmed teal
COLOR_OFF = (0, 0, 0)
COLOR_GREEN = (0, 140, 60)
COLOR_YELLOW = (180, 140, 0)
COLOR_RED = (180, 40, 30)

# Mode cycle order
MODE_CYCLE = ["GREEN", "YELLOW", "RED"]
MODE_LABELS = {"GREEN": "AVAIL", "YELLOW": "BUSY", "RED": "DND"}
MODE_COLORS = {"GREEN": COLOR_GREEN, "YELLOW": COLOR_YELLOW, "RED": COLOR_RED}

# Auto-select delay (seconds)
AUTO_SELECT_DELAY = 1.5

# Bottom row browse modes


class StreamDeckHandler:
    def __init__(self, key_callback):
        self.deck = None
        self.key_callback = key_callback
        self._key_count = 0
        self._cols = 5
        self._icon_dir = os.path.dirname(os.path.abspath(__file__))

        # State
        self._current_mode = "GREEN"

        # Data (for large deck bottom row)
        self._teams = []
        self._users = []
        self._active_team_id = ""
        self._active_user_id = ""
        self._team_index = 0
        self._user_index = 0
        self._auto_select_timer = None

        # Callbacks for auto-select (set by main.py)
        self.on_team_selected = None
        self.on_user_selected = None

        # Message pulse state
        self._msg_pulse_active = False
        self._msg_pulse_on = False
        self._msg_pulse_timer = None

        self.connect()

    def connect(self):
        self._check_elgato_app()

        try:
            streamdecks = DeviceManager().enumerate()
        except Exception as e:
            if sys.platform == 'win32':
                print(f"Stream Deck enumerate failed: {e}")
                print("On Windows, you may need to install LibUSB via Zadig:")
                print("  1. Download Zadig from https://zadig.akeo.ie/")
                print("  2. Options > List All Devices")
                print("  3. Select your Stream Deck")
                print("  4. Install WinUSB driver")
            else:
                print(f"Stream Deck enumerate failed: {e}")
            return

        if not streamdecks:
            print("No Stream Deck found.")
            return

        self.deck = streamdecks[0]
        try:
            self.deck.open()
        except Exception as e:
            if self._elgato_running:
                raise RuntimeError(
                    "Cannot open Stream Deck — the Elgato app is running.\n"
                    "Please quit the Elgato Stream Deck app and restart Office Hours."
                ) from e
            if sys.platform == 'win32':
                print(f"Cannot open Stream Deck: {e}")
                print("Try: quit the Elgato app, or install LibUSB driver via Zadig.")
                self.deck = None
                return
            raise
        self.deck.reset()
        self._key_count = self.deck.key_count()
        self._cols = self.deck.key_layout()[1]

        print(f"Stream Deck Connected: {self.deck.deck_type()} ({self._key_count} keys)")

        self.deck.set_key_callback(self._on_key_change)
        self.deck.set_brightness(100)

        self._init_keys()

    def _check_elgato_app(self):
        """Detect if the Elgato Stream Deck app is running."""
        self._elgato_running = False
        try:
            import subprocess
            if sys.platform == 'darwin':
                result = subprocess.run(
                    ['pgrep', '-f', 'Stream Deck'],
                    capture_output=True, timeout=3
                )
                self._elgato_running = result.returncode == 0
            elif sys.platform == 'win32':
                result = subprocess.run(
                    ['tasklist', '/FI', 'IMAGENAME eq StreamDeck.exe'],
                    capture_output=True, text=True, timeout=3
                )
                self._elgato_running = 'StreamDeck.exe' in result.stdout
        except Exception:
            pass

    @property
    def is_large(self):
        """True for 5x3 (15-key) or larger decks."""
        return self._cols >= 5

    @property
    def _row2_start(self):
        """First key index of row 2."""
        return self._cols

    @property
    def key_team(self):
        return self._row2_start + KEY_TEAM_OFFSET

    @property
    def key_user(self):
        return self._row2_start + KEY_USER_OFFSET

    @property
    def key_window(self):
        return self._row2_start + KEY_WINDOW_OFFSET

    # ── Initialization ────────────────────────────────────────

    def _init_keys(self):
        """Set up the initial key layout."""
        if not self.deck:
            return
        for k in range(self._key_count):
            self.update_key_image(k, text="", color=COLOR_OFF)

        # Row 1: TALK, MODE, LOGO
        self.update_key_image(KEY_TALK, text="TALK", color=OH_TEAL)
        self.set_active_mode("GREEN")
        self._set_logo_key()

        # Row 2: TEAM, USER, WINDOW
        self._render_row2()

    # ── Key event routing ─────────────────────────────────────

    def _on_key_change(self, deck, key, state):
        if self.key_callback:
            self.key_callback(key, state)

    # ── Logo / preview key ────────────────────────────────────

    def _set_logo_key(self):
        """Render the OH icon from file onto key 2."""
        icon_path = os.path.join(self._icon_dir, "oh_icon@2x.png")
        if not os.path.exists(icon_path):
            icon_path = os.path.join(self._icon_dir, "oh_icon.png")
        if os.path.exists(icon_path):
            self.update_key_from_file(KEY_LOGO, icon_path, pad=True)
        else:
            self.update_key_image(KEY_LOGO, render_oh=True)

    def _show_preview(self, name):
        """Temporarily show a name on Key 2 while browsing."""
        self.update_key_image(KEY_LOGO, text=name, color=OH_TEAL)

    def _restore_logo(self):
        """Put the OH icon back on Key 2."""
        self._set_logo_key()

    # ── Mode key (key 1 — cycles through modes) ──────────────

    def set_active_mode(self, mode):
        """Update mode key to show the current mode."""
        if not self.deck:
            return
        self._current_mode = mode
        label = MODE_LABELS.get(mode, "AVAIL")
        color = MODE_COLORS.get(mode, COLOR_GREEN)
        self.update_key_image(KEY_MODE, text=label, color=color)

    def cycle_mode(self):
        """Cycle to next mode. Returns the new mode string."""
        try:
            idx = MODE_CYCLE.index(self._current_mode)
        except ValueError:
            idx = 0
        new_idx = (idx + 1) % len(MODE_CYCLE)
        new_mode = MODE_CYCLE[new_idx]
        self.set_active_mode(new_mode)
        return new_mode

    # ── Talk key ──────────────────────────────────────────────

    def set_talk_active(self, active, recording=False):
        if not self.deck:
            return
        if active and recording:
            self.update_key_image(KEY_TALK, text="REC", color=(255, 0, 0))
        elif active:
            self.update_key_image(KEY_TALK, text="LIVE", color=(255, 0, 0))
        else:
            self.update_key_image(KEY_TALK, text="TALK", color=OH_TEAL)

    def set_talk_locked(self, locked):
        """Show listening state when peer is talking."""
        if not self.deck:
            return
        if locked:
            self.update_key_image(KEY_TALK, text="LISTEN", color=OH_TEAL_DIM)
        else:
            self.update_key_image(KEY_TALK, text="TALK", color=OH_TEAL)

    # ── Message indicator ────────────────────────────────────

    def set_message_indicator(self, has_message):
        if not self.deck:
            return
        if has_message:
            self._start_msg_pulse()
        else:
            self._stop_msg_pulse()

    def _start_msg_pulse(self):
        if self._msg_pulse_active:
            return
        self._msg_pulse_active = True
        self._msg_pulse_on = True
        self._pulse_tick()

    def _stop_msg_pulse(self):
        self._msg_pulse_active = False
        if self._msg_pulse_timer:
            self._msg_pulse_timer.cancel()
            self._msg_pulse_timer = None

    def _pulse_tick(self):
        if not self._msg_pulse_active or not self.deck:
            return
        # Pulse the logo key with MSG text
        if self._msg_pulse_on:
            self.update_key_image(KEY_LOGO, text="MSG", color=OH_TEAL)
        else:
            self._set_logo_key()
        self._msg_pulse_on = not self._msg_pulse_on
        self._msg_pulse_timer = threading.Timer(0.6, self._pulse_tick)
        self._msg_pulse_timer.daemon = True
        self._msg_pulse_timer.start()

    # ── Bottom row: team + user (large decks only) ───────────

    def set_teams(self, teams, active_team_id=""):
        """Update team data. Refreshes bottom row."""
        self._teams = teams or []
        self._active_team_id = active_team_id
        self._team_index = 0
        for i, t in enumerate(self._teams):
            if t.get("id") == active_team_id:
                self._team_index = i
                break
        self._render_row2()

    def set_users(self, users, active_user_id=""):
        """Update online users data. Refreshes bottom row."""
        self._users = users or []
        self._active_user_id = active_user_id
        self._user_index = 0
        for i, u in enumerate(self._users):
            if u.get("id") == active_user_id:
                self._user_index = i
                break
        self._render_row2()

    def handle_team_key(self):
        """Cycle through teams."""
        if not self._teams:
            return
        self._team_index = (self._team_index + 1) % len(self._teams)
        team = self._teams[self._team_index]
        self._active_team_id = team.get("id", "")
        self._show_preview(team.get("name", "?"))
        self._render_row2()
        self._cancel_auto_select()
        self._auto_select_timer = threading.Timer(
            AUTO_SELECT_DELAY, self._auto_select_team
        )
        self._auto_select_timer.daemon = True
        self._auto_select_timer.start()

    def handle_user_key(self):
        """Cycle through users."""
        if not self._users:
            return
        self._user_index = (self._user_index + 1) % len(self._users)
        user = self._users[self._user_index]
        self._active_user_id = user.get("id", "")
        self._show_preview(user.get("name", "?"))
        self._render_row2()
        self._cancel_auto_select()
        self._auto_select_timer = threading.Timer(
            AUTO_SELECT_DELAY, self._auto_select_user
        )
        self._auto_select_timer.daemon = True
        self._auto_select_timer.start()

    def _auto_select_team(self):
        if self._team_index < len(self._teams):
            team = self._teams[self._team_index]
            if self.on_team_selected:
                self.on_team_selected(team.get("id", ""), team.get("name", ""))
        self._restore_logo()

    def _auto_select_user(self):
        if self._user_index < len(self._users):
            user = self._users[self._user_index]
            if self.on_user_selected:
                self.on_user_selected(user.get("id", ""), user.get("name", ""))
        self._restore_logo()

    def _cancel_auto_select(self):
        if self._auto_select_timer:
            self._auto_select_timer.cancel()
            self._auto_select_timer = None

    def _render_row2(self, **_kw):
        """Draw row 2: TEAM, USER, WINDOW."""
        if not self.deck:
            return

        # Team key
        if self._teams and self._team_index < len(self._teams):
            team = self._teams[self._team_index]
            name = team.get("name", "?")
            if len(name) > 6:
                name = name[:5] + "."
            is_active = team.get("id") == self._active_team_id
            self.update_key_image(self.key_team, text=f"TEAM\n{name}", color=OH_TEAL if is_active else OH_TEAL_DIM)
        else:
            self.update_key_image(self.key_team, text="TEAM\n--", color=OH_TEAL_DIM)

        # User key
        if self._users and self._user_index < len(self._users):
            user = self._users[self._user_index]
            name = user.get("name", "?")
            if len(name) > 6:
                name = name[:5] + "."
            is_active = user.get("id") == self._active_user_id
            self.update_key_image(self.key_user, text=f"USER\n{name}", color=OH_TEAL if is_active else OH_TEAL_DIM)
        else:
            self.update_key_image(self.key_user, text="USER\n--", color=OH_TEAL_DIM)

        # Window toggle key
        self.update_key_image(self.key_window, text="WINDOW", color=OH_TEAL_DIM)

    # ── Compat stubs ──────────────────────────────────────────

    def next_team_page(self):
        pass

    def get_team_for_key(self, key):
        return None

    # ── Image rendering ───────────────────────────────────────

    def _get_font(self, size):
        font_paths = []
        if sys.platform == 'darwin':
            font_paths = [
                "/System/Library/Fonts/Helvetica.ttc",
                "/System/Library/Fonts/SFNSText.ttf",
                "/Library/Fonts/Arial.ttf",
            ]
        elif sys.platform == 'win32':
            windir = os.environ.get('WINDIR', 'C:\\Windows')
            font_paths = [
                os.path.join(windir, 'Fonts', 'arial.ttf'),
                os.path.join(windir, 'Fonts', 'segoeui.ttf'),
                os.path.join(windir, 'Fonts', 'tahoma.ttf'),
            ]
        else:
            font_paths = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            ]
        for path in font_paths:
            try:
                if path.endswith('.ttc'):
                    return ImageFont.truetype(path, size, index=1)
                else:
                    return ImageFont.truetype(path, size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    def update_key_color(self, key, r, g, b, label=""):
        self.update_key_image(key, text=label, color=(r, g, b))

    def update_key_image(self, key, text="", color=(0, 0, 0), render_oh=False):
        if not self.deck or key >= self._key_count:
            return

        image = PILHelper.create_image(self.deck)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, image.width, image.height), fill=color)

        font = self._get_font(14)
        oh_font = self._get_font(24)
        w, h = image.width, image.height

        if render_oh:
            lbl = "OH"
            bbox = draw.textbbox((0, 0), lbl, font=oh_font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x, y = (w - tw) / 2, (h - th) / 2
            text_color = "white"
            if (color[0] > 180 and color[1] > 180 and color[2] > 180) or (color[1] > 200):
                text_color = "black"
            draw.text((x, y), lbl, font=oh_font, fill=text_color)
        elif text:
            lines = text.split("\n")
            y = (h - (len(lines) * 18)) / 2
            for line in lines:
                bbox = draw.textbbox((0, 0), line, font=font)
                tw = bbox[2] - bbox[0]
                draw.text(((w - tw) / 2, y), line, font=font,
                          fill="black" if (color[0] > 128 and color[1] > 128) else "white")
                y += 18

        native_image = PILHelper.to_native_format(self.deck, image)
        try:
            self.deck.set_key_image(key, native_image)
        except Exception as e:
            print(f"Deck Update Error: {e}")

    def update_key_from_file(self, key, image_path, pad=False):
        if not self.deck or key >= self._key_count:
            return
        try:
            icon = Image.open(image_path).convert("RGBA")
            image = PILHelper.create_image(self.deck)
            kw, kh = image.width, image.height

            if pad:
                max_dim = int(min(kw, kh) * 0.6)
                icon.thumbnail((max_dim, max_dim), Image.LANCZOS)
                bg = Image.new("RGB", (kw, kh), COLOR_OFF)
                x = (kw - icon.width) // 2
                y = (kh - icon.height) // 2
                bg.paste(icon, (x, y), mask=icon.split()[3] if icon.mode == "RGBA" else None)
            else:
                icon_resized = icon.resize((kw, kh), Image.LANCZOS)
                bg = Image.new("RGB", (kw, kh), COLOR_OFF)
                bg.paste(icon_resized, mask=icon_resized.split()[3] if icon_resized.mode == "RGBA" else None)

            native_image = PILHelper.to_native_format(self.deck, bg)
            self.deck.set_key_image(key, native_image)
        except Exception as e:
            print(f"Deck Image Error: {e}")
            self.update_key_image(key, render_oh=True)

    def close(self):
        self._stop_msg_pulse()
        self._cancel_auto_select()
        if self.deck:
            self.deck.reset()
            self.deck.close()
