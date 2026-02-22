#!/bin/bash
# Office Hours — macOS Install & Run
# Double-click this file to launch (or run: bash install_and_run.command)

cd "$(dirname "$0")"

echo "============================================"
echo "  Office Hours — Intercom"
echo "============================================"
echo ""

# ── Step 0: Clear Gatekeeper quarantine from downloaded files ──
echo "Clearing macOS quarantine flags..."
xattr -rd com.apple.quarantine "$(dirname "$0")" 2>/dev/null || true

# ── Step 1: Check for Xcode Command Line Tools ──
if ! xcode-select -p &> /dev/null; then
    echo "Installing Xcode Command Line Tools (required)..."
    xcode-select --install
    echo ""
    echo "A popup should appear. Click 'Install', then re-run this script when done."
    read -p "Press Enter to exit..."
    exit 0
fi

# ── Helper: Install Homebrew if needed ──
install_homebrew() {
    if ! command -v brew &> /dev/null; then
        echo "Installing Homebrew (package manager)..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

        # Add Homebrew to PATH for Apple Silicon Macs
        if [ -f "/opt/homebrew/bin/brew" ]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        fi
    fi
}

# ── Step 2: Check for Python 3.10+ ──
# PySide6 requires Python 3.10 or newer.
NEED_PYTHON=false

if command -v python3 &> /dev/null; then
    PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

    if [ "$PY_MINOR" -lt 10 ]; then
        echo "Found Python $PY_VERSION, but Office Hours requires Python 3.10+."
        NEED_PYTHON=true
    fi
else
    echo "Python 3 not found."
    NEED_PYTHON=true
fi

if [ "$NEED_PYTHON" = true ]; then
    echo "Installing Python 3 via Homebrew..."
    echo ""
    install_homebrew

    brew install python@3.12
    echo ""

    # Use Homebrew Python explicitly
    if [ -f "/opt/homebrew/bin/python3.12" ]; then
        PYTHON="/opt/homebrew/bin/python3.12"
    elif [ -f "/usr/local/bin/python3.12" ]; then
        PYTHON="/usr/local/bin/python3.12"
    elif [ -f "/opt/homebrew/bin/python3" ]; then
        PYTHON="/opt/homebrew/bin/python3"
    elif [ -f "/usr/local/bin/python3" ]; then
        PYTHON="/usr/local/bin/python3"
    else
        PYTHON="python3"
    fi
else
    PYTHON="python3"
fi

# Verify Python
if ! "$PYTHON" --version &> /dev/null; then
    echo "ERROR: Python 3.10+ installation failed."
    echo "Please install manually from: https://www.python.org/downloads/"
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi

echo "Using Python: $("$PYTHON" --version)"

# ── Step 3: Check for PortAudio (required by sounddevice) ──
if command -v brew &> /dev/null; then
    if ! brew list portaudio &> /dev/null 2>&1; then
        echo "Installing PortAudio (audio driver)..."
        brew install portaudio
    fi
else
    echo "Installing Homebrew for PortAudio..."
    install_homebrew
    echo "Installing PortAudio (audio driver)..."
    brew install portaudio
fi

# ── Step 4: Create venv & install dependencies ──
# If venv exists but uses wrong Python, recreate it
if [ -d "venv" ]; then
    VENV_PY_MINOR=$(./venv/bin/python -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo "0")
    if [ "$VENV_PY_MINOR" -lt 10 ]; then
        echo "Existing venv uses Python 3.$VENV_PY_MINOR — recreating with newer Python..."
        rm -rf venv
    fi
fi

if [ ! -d "venv" ]; then
    echo ""
    echo "Creating virtual environment..."
    "$PYTHON" -m venv venv
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

# ── Step 6: Launch ──
echo ""
echo "Starting Office Hours..."
echo "============================================"
echo ""
./venv/bin/python main.py
