#!/bin/bash
# Install the Office Hours Stream Deck plugin
set -e

PLUGIN_DIR="com.officehours.intercom.sdPlugin"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Installing Office Hours Stream Deck plugin..."

# Install Node dependencies
echo "  Installing dependencies..."
cd "$PLUGIN_DIR/bin"
npm install --production 2>&1 | head -5
cd "$SCRIPT_DIR"

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
cp -R "$PLUGIN_DIR" "$DEST"

echo ""
echo "Done! Restart the Stream Deck app, then find 'Office Hours' in the action list."
