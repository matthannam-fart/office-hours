# Vox — Stream Deck Plugin

Control your Vox intercom directly from the Elgato Stream Deck app.

## Actions

| Action | Description |
|--------|-------------|
| **Push to Talk** | Hold to talk, release to stop |
| **Status Mode** | Cycle: Available → Busy → DND |
| **Switch Team** | Cycle through your teams |
| **Select User** | Cycle through online users |
| **Show Panel** | Open the Vox panel window |

## How It Works

The plugin connects to Vox via a local WebSocket (`ws://localhost:50003`). Vox must be running for the buttons to be active. When Vox isn't running, buttons appear dimmed and reconnect automatically when it starts.

## Install

### Quick Install (pre-built)

Double-click `com.vox.intercom.streamDeckPlugin` to install.

### Build from Source

Requires Node.js 20+:

```bash
cd com.vox.intercom.sdPlugin/bin
npm install
```

Then copy the `com.vox.intercom.sdPlugin` folder to your Stream Deck plugins directory:

- **macOS**: `~/Library/Application Support/com.elgato.StreamDeck/Plugins/`
- **Windows**: `%APPDATA%\Elgato\StreamDeck\Plugins\`

Restart the Stream Deck app.

## Setup

1. Open the Stream Deck app
2. Find "Vox" in the action list (right sidebar)
3. Drag actions onto your Stream Deck buttons
4. Launch Vox — buttons activate automatically
