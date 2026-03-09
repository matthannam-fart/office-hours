# Office Hours — Team Intercom

A push-to-talk intercom for teams. Sits in your menu bar, lets you talk to your team instantly — like a walkie-talkie for your desktop.

Built with PySide6 (Qt), backed by Supabase for team management, and a custom relay server for real-time presence and audio routing.

## Install

### macOS

1. Download from [ohinter.com](https://ohinter.com) (passphrase required)
2. Unzip and open the folder
3. Double-click **`Office Hours.command`**

The first time macOS will block it. To fix:

1. Double-click `Office Hours.command` — a warning appears, click **Done**
2. Open **System Settings → Privacy & Security**
3. Scroll down, find "Office Hours.command was blocked" → click **Open Anyway**
4. Enter your password if prompted

That's it. The script handles everything: Python, dependencies, audio drivers, updates, and permissions. Just double-click to launch every time.

### Push-to-Talk Hotkey

The global PTT hotkey (backtick key) needs Accessibility permissions. The installer will detect this and open the right settings pane automatically. Add **Terminal** (or whichever terminal app you use) to the Accessibility list.

### Windows

Double-click **`Office Hours.bat`** (requires Python 3.10+ with "Add to PATH" checked during install).

### Manual Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Getting Started

When you first launch, you'll see the onboarding screen:

1. **Enter your name** — this is how your team sees you
2. **Create a new team** to start your own room
3. Or **join with an invite code** (e.g. `OH-7X3K5`) if someone shared one with you

Teams are private — you can only see and contact people on your own team. Once you're on a team, the app shows who's online and available.

## How It Works

### Status Modes

Click the mode button in the header to cycle through:

| Mode | What Happens When Someone Contacts You |
|------|----------------------------------------|
| **Green** (Available) | Auto-connects — instant intercom, no ringing |
| **Yellow** (Busy) | They send a page — you accept or decline |
| **Red** (DND) | They can leave a voice message |

### Talking

| Action | How |
|--------|-----|
| **Hold to Talk** | Hold the PTT button in the panel, or hold the **backtick** key (`` ` ``) anywhere on your system |
| **Page All** | Hold the Page All button to record a message sent to everyone on the team |
| **Hotline** | Toggle in the header — always-on hot mic, no button holding needed |

### Teams

Teams are private rooms. You join via an invite code shared by a team member, or create your own. To find your invite code: open settings (···) → **Copy Invite Code**.

You can be on multiple teams and switch between them from the team dropdown. You only see and hear people on your currently active team.

Admins can manage members via the gear button next to the team dropdown. To leave a team: settings (···) → **Leave Team**.

### Voicemail

When someone contacts you while you're in DND mode, they can leave a voice message. You'll see a notification and can play it back from the panel.

### Incognito

Toggle incognito mode from settings to appear offline to your team while still being connected.

## Architecture

```
┌──────────────┐        ┌──────────────┐        ┌──────────────┐
│   Client A   │◄──────►│ Relay Server │◄──────►│   Client B   │
│  (PySide6)   │  TCP   │ (DigitalOcean│  TCP   │  (PySide6)   │
│              │  UDP   │  :50002)     │  UDP   │              │
└──────┬───────┘        └──────────────┘        └──────┬───────┘
       │                                               │
       │           ┌──────────────────┐                │
       └──────────►│    Supabase      │◄───────────────┘
                   │  (teams, users,  │
                   │   join_requests) │
                   └──────────────────┘
```

Clients on the same LAN also discover each other via Zeroconf/Bonjour for lower-latency local connections.

## Network

| Port | Protocol | Purpose |
|------|----------|---------|
| 50000 | TCP | Control messages & file transfer (LAN) |
| 50001 | UDP | Live audio streaming (LAN) |
| 50002 | TCP+UDP | Presence & relay server (remote) |

Remote connections go through the relay server — no port forwarding needed on the client side.

## Project Structure

```
main.py               App entry point, state management, signal wiring
floating_panel.py     Frameless menu bar panel UI (onboarding, lobby, team view)
network_manager.py    TCP/UDP networking, relay connection, presence protocol
audio_manager.py      Microphone capture, speaker playback, level metering
supabase_client.py    Supabase REST API (users, teams, join requests)
relay_server.py       Remote relay server (presence, audio, join request routing)
discovery_manager.py  LAN peer discovery via Zeroconf/Bonjour
hotkey_manager.py     Global push-to-talk hotkey listener
deck_ws_server.py     WebSocket bridge for Stream Deck Elgato plugin
user_settings.py      Local config persistence (name, team, hotkey)
config.py             Ports, relay host, audio settings, logging
ui_constants.py       Color palette, mode labels, panel dimensions
widgets.py            Reusable Qt widgets (GlowingOrb, LevelMeter, UserRow, etc.)
generate_certs.py     TLS certificate generation for relay server
```

## Stream Deck (Optional)

Works with the Elgato Stream Deck app via a plugin that auto-installs on launch. Open the Stream Deck app and drag actions from the "Office Hours" category onto your deck.
