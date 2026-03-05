# Office Hours — Team Intercom

A push-to-talk intercom for teams. Sits in your menu bar, lets you talk to your team instantly — like a walkie-talkie for your desktop.

## Install

### macOS

1. Download from [ohinter.com](https://ohinter.com) (passphrase required)
2. Unzip and open the folder
3. Double-click **`install_and_run.command`**

The first time macOS will block it. To fix:

1. Double-click `install_and_run.command` — a warning appears, click **Done**
2. Open **System Settings → Privacy & Security**
3. Scroll down, find "install_and_run.command was blocked" → click **Open Anyway**
4. Enter your password if prompted

That's it. The script handles everything else: Python, dependencies, audio drivers, and permissions. After the first run, just double-click to launch.

### Push-to-Talk Hotkey

The global PTT hotkey (backtick key) needs Accessibility permissions. The installer will detect this and open the right settings pane automatically. Add **Terminal** (or whichever terminal app you use) to the Accessibility list.

### Windows

Double-click `install_and_run.bat` (requires Python 3.10+ with "Add to PATH" checked during install).

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
2. **Join a team** with an invite code (e.g. `OH-7X3K5`), or **create a new team**

Once you're on a team, the app connects to the presence server and shows who's online.

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

Teams are private groups. Create a team, get an invite code, share it with your people. To find your invite code: open settings (···) → **Copy Invite Code**.

Anyone with the code can join. Admins can manage members via the ⚙ button next to the team dropdown.

To leave a team: settings (···) → **Leave Team**.

## Network

| Port | Protocol | Purpose |
|------|----------|---------|
| 50000 | TCP | Control messages & file transfer (LAN) |
| 50001 | UDP | Live audio streaming (LAN) |
| 50002 | TCP+UDP | Presence & relay server (remote) |

LAN discovery uses Zeroconf/Bonjour. Remote connections go through the relay server — no port forwarding needed on the client side.

## Stream Deck (Optional)

If an Elgato Stream Deck is connected, hardware keys mirror the on-screen controls with color-coded feedback.
