#!/bin/bash
# Office Hours — macOS Install & Run
# Double-click this file to launch (or run: bash install_and_run.command)

cd "$(dirname "$0")"

echo "============================================"
echo "  Office Hours — Intercom"
echo "============================================"
echo ""

# ── Step 1: Check for Xcode Command Line Tools ──
if ! xcode-select -p &> /dev/null; then
    echo "Installing Xcode Command Line Tools (required)..."
    xcode-select --install
    echo ""
    echo "A popup should appear. Click 'Install', then re-run this script when done."
    read -p "Press Enter to exit..."
    exit 0
fi

# ── Step 2: Check for Python 3 ──
if ! command -v python3 &> /dev/null; then
    echo "Python 3 not found. Attempting to install via Homebrew..."
    echo ""

    # Install Homebrew if needed
    if ! command -v brew &> /dev/null; then
        echo "Installing Homebrew (package manager)..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

        # Add Homebrew to PATH for Apple Silicon Macs
        if [ -f "/opt/homebrew/bin/brew" ]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        fi
    fi

    echo "Installing Python 3..."
    brew install python3
    echo ""
fi

# Verify Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 installation failed."
    echo "Please install manually from: https://www.python.org/downloads/"
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi

echo "Using Python: $(python3 --version)"

# ── Step 3: Check for PortAudio (required by sounddevice) ──
if command -v brew &> /dev/null; then
    if ! brew list portaudio &> /dev/null 2>&1; then
        echo "Installing PortAudio (audio driver)..."
        brew install portaudio
    fi
fi

# ── Step 4: Create venv & install dependencies ──
if [ ! -d "venv" ]; then
    echo ""
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Verify dependencies are installed (catches broken/incomplete venvs)
if ! ./venv/bin/python -c "import sounddevice, numpy, PySide6, zeroconf" 2>/dev/null; then
    echo "Installing dependencies..."
    ./venv/bin/pip install --upgrade pip -q
    ./venv/bin/pip install -r requirements.txt
    echo ""
    echo "Setup complete!"
else
    echo "All dependencies OK."
fi

# ── Step 5: Remove Gatekeeper quarantine from PySide6/Qt libs ──
if [ -d "venv" ]; then
    echo "Clearing Gatekeeper flags..."
    xattr -rd com.apple.quarantine venv/ 2>/dev/null || true
fi

# ── Step 5: Launch ──
echo ""
echo "Starting Office Hours..."
echo "============================================"
echo ""
./venv/bin/python main.py
