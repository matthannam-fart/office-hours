#!/bin/bash
# Install the Vox Stream Deck plugin
set -e

PLUGIN_DIR="com.vox.intercom.sdPlugin"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Installing Vox Stream Deck plugin..."

# Validate Node dependencies (ws module must exist)
if [ ! -d "$PLUGIN_DIR/bin/node_modules/ws" ]; then
    echo "  Node dependencies missing or incomplete."
    if command -v npm &> /dev/null; then
        echo "  Installing dependencies via npm..."
        cd "$PLUGIN_DIR/bin"
        npm install --production 2>&1 | head -5
        cd "$SCRIPT_DIR"
        if [ ! -d "$PLUGIN_DIR/bin/node_modules/ws" ]; then
            echo "  WARNING: npm install ran but ws module still missing."
        else
            echo "  Dependencies installed."
        fi
    else
        echo "  WARNING: npm not found and ws module not bundled."
        echo "  The plugin may not work. Install Node.js from https://nodejs.org"
    fi
else
    echo "  Dependencies OK."
fi

# Determine install location
if [ "$(uname)" = "Darwin" ]; then
    DEST="$HOME/Library/Application Support/com.elgato.StreamDeck/Plugins/$PLUGIN_DIR"
else
    echo "On Windows, run install.bat instead."
    exit 1
fi

# Remove old version
if [ -d "$DEST" ]; then
    echo "  Removing old version..."
    rm -rf "$DEST"
fi

# Copy plugin
echo "  Copying plugin to Stream Deck..."
if cp -R "$PLUGIN_DIR" "$DEST" 2>/dev/null; then
    echo ""
    echo "Done! Restart the Stream Deck app, then find 'Vox' in the action list."
else
    echo ""
    echo "ERROR: Failed to copy plugin. Check permissions on: $DEST"
fi
