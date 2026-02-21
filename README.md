# Office Hours — LAN & Remote Intercom

A walkie-talkie style intercom app for two users — works on the same LAN or across the internet.

## Quick Start

### macOS
Double-click `install_and_run.command`  
*(If blocked by Gatekeeper: Right-click → Open → Open)*

### Windows
Double-click `install_and_run.bat`  
*(Requires Python 3.9+ with "Add to PATH" checked during install)*

### Manual
```bash
python3 -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
python main.py
```

## How It Works

Both users run the app on their own machine. Connect via:
- **LAN tab** — Auto-discovers peers on the same network, or enter an IP manually
- **Remote tab** — Connect through a relay server using a room code (works across the internet)

### Three Modes

| Your Mode | What Happens When Someone Talks To You |
|-----------|----------------------------------------|
| 🟢 **GREEN** | You hear them live (walkie-talkie) |
| 🟡 **YELLOW** | Their message is recorded for you (answering machine) |
| 🔴 **RED** | They're told you're unavailable (do not disturb) |

### Controls

| Button | Action |
|--------|--------|
| **HOLD TO TALK** | Press and hold to transmit |
| **ANSWER PAGE** | Play back a recorded message |
| **CYCLE MODE** | Switch between Green → Yellow → Red |
| **DISCONNECT** | End the current session |

## Remote Connection

For users on different networks (home ↔ office, two cities, etc.).

### 1. Deploy the Relay Server

Run `relay_server.py` on any machine with a public IP:

```bash
# On your server / VPS
python relay_server.py --port 50002
```

The relay server has **zero dependencies** — just Python 3.

### 2. Connect

**User A (creates the room):**
1. Open the **🌐 Remote** tab
2. Enter the relay server address (e.g., `relay.example.com`)
3. Click **Create Room** — a room code appears (e.g., `OH-7X3K`)
4. Share the room code with User B

**User B (joins the room):**
1. Open the **🌐 Remote** tab
2. Enter the same relay server address
3. Type in the room code
4. Click **Join Room**

Both users are now connected and can talk just like on a LAN.

### Relay Server Options

```
python relay_server.py --help
  --port PORT   TCP/UDP port (default: 50002)
  --host HOST   Bind address (default: 0.0.0.0)
```

You can also pre-configure the relay address via environment variables:
```bash
export TALKBACK_RELAY_HOST=relay.example.com
export TALKBACK_RELAY_PORT=50002
```

## Stream Deck (Optional)

If an Elgato Stream Deck is connected, keys 0-2 mirror the on-screen controls with color-coded feedback.

## Network

| Port | Protocol | Purpose |
|------|----------|---------|
| 50000 | TCP | Control messages & file transfer (LAN mode) |
| 50001 | UDP | Live audio streaming (LAN mode) |
| 50002 | TCP+UDP | Relay server (remote mode) |

- **LAN mode**: Auto-discovery via Zeroconf/Bonjour
- **Remote mode**: Both clients make outbound connections — no port forwarding needed on client side
