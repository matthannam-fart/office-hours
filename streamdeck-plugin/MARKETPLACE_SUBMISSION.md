# Vox — Stream Deck Marketplace Submission Checklist

Steps you need to do in a browser to get Vox listed on the Elgato Marketplace.

---

## 1. Register as a Maker

- Go to **https://maker.elgato.com/**
- Sign in or create an Elgato account
- Create an **Organization** (e.g. "Vox" or your company name)
- Sign the **Maker Agreement** (free — Elgato takes 30% of any paid plugins, but free plugins cost nothing)

## 2. Set Up Your Plugin Listing

In the Maker Console:

- Click **New Plugin**
- Fill in the metadata:
  - **Plugin Name**: `Vox Intercom`
  - **UUID**: `com.vox.intercom` (this is locked forever — cannot change after publishing)
  - **Category**: Communication
  - **Short Description**: Control your Vox intercom — push-to-talk, status mode, team and user switching — from your Stream Deck.
  - **Long Description**: (expand on features, mention LAN and remote support, etc.)
  - **Support URL**: `https://matthannam-fart.github.io/vox/`
  - **Version**: `1.0.0.0`

## 3. Upload Assets

The plugin code and icons are ready. You still need:

### Gallery Images (required — at least 1)
Screenshots of the Stream Deck buttons in action. Recommended:
- 1920x1080 or 1280x720 PNG
- Show the Stream Deck with Vox buttons active (PTT lit up, status showing, etc.)
- You can photograph a real Stream Deck or use the Stream Deck app simulator

### Plugin Package
Build the `.streamDeckPlugin` installer file:

```bash
cd ~/Projects/vox/streamdeck-plugin

# If you have the Elgato CLI installed:
streamdeck pack com.vox.intercom.sdPlugin

# Or manually: zip the plugin folder and rename
cd com.vox.intercom.sdPlugin && zip -r ../com.vox.intercom.streamDeckPlugin . && cd ..
```

Upload this file in the Maker Console.

## 4. Submit for Review

- Click **Submit** in the Maker Console
- Elgato's team reviews manually (usually takes a few weeks)
- They may request changes — you'll get notified by email
- Once approved, the plugin appears in the Marketplace inside the Stream Deck app

## 5. Optional: Join the Maker Discord

Elgato has a **Marketplace Makers Discord** for plugin developers. Useful for:
- Getting review status updates
- Asking questions about SDK/Marketplace requirements
- Networking with other plugin devs

Link should be available in the Maker Console after registration.

---

## Quick Reference

| Item | Status |
|------|--------|
| Plugin code (SDK v2) | Done |
| manifest.json (Marketplace-ready) | Done |
| Property Inspector UI | Done |
| PNG icons (plugin, category, actions) | Done |
| OH → Vox rename complete | Done |
| Old HID dependency removed | Done |
| Maker account registration | **You** |
| Gallery screenshots | **You** |
| Package and upload | **You** |
| Submit for review | **You** |

---

## Install the Elgato CLI (optional but handy)

```bash
npm install -g @elgato/cli
```

This gives you:
- `streamdeck pack` — package your plugin into a `.streamDeckPlugin` file
- `streamdeck create` — scaffold new plugins
- `streamdeck link` — symlink plugin for live development
- `streamdeck validate` — check manifest against Marketplace rules
