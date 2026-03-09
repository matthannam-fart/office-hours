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

# ── Step 0.5: Auto-update ──
REPO_URL="https://github.com/matthannam-fart/office-hours"

if [ -d ".git" ] && command -v git &> /dev/null; then
    # Git user — just pull
    echo "Checking for updates..."
    if git pull --ff-only 2>/dev/null; then
        echo "✓ Updated to latest version."
    else
        echo "ℹ Already up to date (or merge needed)."
    fi
    echo ""
else
    # Non-git user — download latest from GitHub
    echo "Checking for updates..."
    LATEST_SHA=$(curl -fsSL "https://api.github.com/repos/matthannam-fart/office-hours/commits/main" 2>/dev/null | grep '"sha"' | head -1 | cut -d'"' -f4)

    LOCAL_SHA=""
    if [ -f ".version" ]; then
        LOCAL_SHA=$(cat .version)
    fi

    if [ -n "$LATEST_SHA" ] && [ "$LATEST_SHA" != "$LOCAL_SHA" ]; then
        echo "New version available — downloading..."
        TMPDIR_UP=$(mktemp -d)
        if curl -fsSL "${REPO_URL}/archive/refs/heads/main.zip" -o "$TMPDIR_UP/update.zip" 2>/dev/null; then
            unzip -qo "$TMPDIR_UP/update.zip" -d "$TMPDIR_UP"
            # Copy updated files over (preserve venv, user_settings, .version)
            rsync -a --exclude='venv' --exclude='.version' --exclude='user_settings.json' \
                "$TMPDIR_UP/office-hours-main/" "$(pwd)/"
            echo "$LATEST_SHA" > .version
            echo "✓ Updated to latest version."
            # Re-clear quarantine on new files
            xattr -rd com.apple.quarantine "$(pwd)" 2>/dev/null || true
        else
            echo "ℹ Could not download update (no internet?)."
        fi
        rm -rf "$TMPDIR_UP"
    else
        echo "✓ Already up to date."
    fi
    echo ""
fi

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

# ── Step 3: Check for PortAudio and Opus (required by sounddevice and Opus codec) ──
if command -v brew &> /dev/null; then
    if ! brew list portaudio &> /dev/null 2>&1; then
        echo "Installing PortAudio (audio driver)..."
        brew install portaudio
    fi
    if ! brew list opus &> /dev/null 2>&1; then
        echo "Installing Opus (audio codec)..."
        brew install opus
    fi
else
    echo "Installing Homebrew for PortAudio and Opus..."
    install_homebrew
    echo "Installing PortAudio (audio driver)..."
    brew install portaudio
    echo "Installing Opus (audio codec)..."
    brew install opus
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
if ! ./venv/bin/python -c "import sounddevice, numpy, PySide6, zeroconf, opuslib" 2>/dev/null; then
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

# ── Step 6: Check Accessibility permissions (needed for global PTT hotkey) ──
# We use a small AppleScript trick to test if we have Accessibility access.
# If not, we open the right System Settings pane and let the user know.
if ! ./venv/bin/python -c "
import subprocess, sys
result = subprocess.run(
    ['osascript', '-e', 'tell application \"System Events\" to keystroke \"\"'],
    capture_output=True, timeout=5
)
sys.exit(0 if result.returncode == 0 else 1)
" 2>/dev/null; then
    echo ""
    echo "──────────────────────────────────────────"
    echo "  Push-to-Talk needs Accessibility access"
    echo "──────────────────────────────────────────"
    echo ""
    echo "  The global PTT hotkey (backtick key) needs"
    echo "  Accessibility permissions to work system-wide."
    echo ""
    echo "  Opening System Settings → Accessibility..."
    echo "  Add 'Terminal' (or your terminal app) to the list."
    echo ""
    open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
    echo "  Once added, Office Hours will launch with PTT enabled."
    echo ""
    read -p "  Press Enter to continue launching..."
fi

# ── Step 7: Launch ──
echo ""
echo "Starting Office Hours..."
echo "============================================"
echo ""
./venv/bin/python main.py
