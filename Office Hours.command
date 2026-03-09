#!/bin/bash
# Office Hours — macOS
# Double-click this file to launch (or run: bash "Office Hours.command")

cd "$(dirname "$0")"

echo ""
echo "  ╔════════════════════════════════════════╗"
echo "  ║       Office Hours — Intercom          ║"
echo "  ╚════════════════════════════════════════╝"
echo ""

# ── Step 0: Clear Gatekeeper quarantine from downloaded files ──
xattr -rd com.apple.quarantine "$(dirname "$0")" 2>/dev/null || true

# ── Step 0.5: Auto-update ──
REPO_URL="https://github.com/matthannam-fart/office-hours"
{
    if [ -d ".git" ] && command -v git &> /dev/null; then
        echo "  Checking for updates..."
        # Stash any local changes (like downloaded opus.dll) before pulling
        git stash -q 2>/dev/null
        PULL_OUTPUT=$(git pull --ff-only 2>&1)
        PULL_EXIT=$?
        git stash pop -q 2>/dev/null

        if [ $PULL_EXIT -eq 0 ]; then
            if echo "$PULL_OUTPUT" | grep -q "Already up to date"; then
                echo "  ✓ Already up to date."
            else
                echo "  ✓ Updated to latest version."
                # Force dep reinstall on next check since requirements may have changed
                rm -f .deps_ok
            fi
        else
            echo "  ✓ Already up to date (pull skipped)."
        fi
        echo ""
    else
        # Non-git user — download latest from GitHub
        echo "  Checking for updates..."
        LATEST_SHA=$(curl -fsSL --connect-timeout 5 \
            "https://api.github.com/repos/matthannam-fart/office-hours/commits/main" 2>/dev/null \
            | python3 -c "import sys,json; print(json.load(sys.stdin)['sha'])" 2>/dev/null)

        LOCAL_SHA=""
        if [ -f ".version" ]; then
            LOCAL_SHA=$(cat .version)
        fi

        if [ -n "$LATEST_SHA" ] && [ "$LATEST_SHA" != "$LOCAL_SHA" ]; then
            echo "  New version available — downloading..."
            TMPDIR_UP=$(mktemp -d)
            UPDATE_OK=false
            if curl -fsSL "${REPO_URL}/archive/refs/heads/main.zip" -o "$TMPDIR_UP/update.zip" 2>/dev/null; then
                unzip -qo "$TMPDIR_UP/update.zip" -d "$TMPDIR_UP"
                # Verify the extracted directory exists and has main.py
                EXTRACTED="$TMPDIR_UP/office-hours-main"
                if [ -f "$EXTRACTED/main.py" ]; then
                    # Only copy source files, preserve all runtime/generated files
                    rsync -a \
                        --exclude='venv' --exclude='.version' --exclude='.last_update_check' \
                        --exclude='.deps_ok' --exclude='crash.log' \
                        --exclude='__pycache__' --exclude='.git' \
                        "$EXTRACTED/" "$(pwd)/"
                    if [ $? -eq 0 ]; then
                        UPDATE_OK=true
                    fi
                fi
            fi
            rm -rf "$TMPDIR_UP"
            if [ "$UPDATE_OK" = true ]; then
                echo "$LATEST_SHA" > .version
                echo "  ✓ Updated to latest version."
                xattr -rd com.apple.quarantine "$(pwd)" 2>/dev/null || true
                rm -f .deps_ok
            else
                echo "  ✓ Already up to date (download failed?)."
            fi
        else
            echo "  ✓ Already up to date."
        fi
        echo ""
    fi
}

# ── Step 1: Xcode Command Line Tools ──
# These provide the C compiler needed by pip to build some dependencies.
if ! xcode-select -p &> /dev/null; then
    echo "  ┌──────────────────────────────────────────┐"
    echo "  │  Installing Xcode Command Line Tools     │"
    echo "  │  (one-time setup — takes a few minutes)  │"
    echo "  └──────────────────────────────────────────┘"
    echo ""
    echo "  A macOS popup will appear. Click 'Install' and agree to the license."
    echo "  This window will wait automatically and continue when it's done."
    echo ""

    # Trigger the install dialog
    xcode-select --install 2>/dev/null

    # Wait for the install to actually finish (polls every 5 seconds)
    echo "  Waiting for Xcode tools to install..."
    while ! xcode-select -p &> /dev/null; do
        sleep 5
    done
    echo "  ✓ Xcode Command Line Tools installed."
    echo ""
fi

# ── Helper: Install Homebrew if needed ──
install_homebrew() {
    if ! command -v brew &> /dev/null; then
        echo ""
        echo "  ┌──────────────────────────────────────────┐"
        echo "  │  Installing Homebrew (package manager)   │"
        echo "  │  You may be asked for your Mac password. │"
        echo "  └──────────────────────────────────────────┘"
        echo ""
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

        # Add Homebrew to PATH for this session (Apple Silicon)
        if [ -f "/opt/homebrew/bin/brew" ]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        fi

        if ! command -v brew &> /dev/null; then
            echo ""
            echo "  ✗ Homebrew installation failed."
            echo "    Try installing manually: https://brew.sh"
            echo ""
            read -p "  Press Enter to exit..."
            exit 1
        fi
        echo ""
        echo "  ✓ Homebrew installed."
    fi
}

# ── Step 2: Python 3.10+ ──
# PySide6 (the UI framework) requires Python 3.10 or newer.
NEED_PYTHON=false

if command -v python3 &> /dev/null; then
    PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

    if [ "$PY_MINOR" -lt 10 ]; then
        echo "  Found Python $PY_VERSION, but Office Hours needs 3.10+."
        NEED_PYTHON=true
    fi
else
    echo "  Python 3 not found."
    NEED_PYTHON=true
fi

if [ "$NEED_PYTHON" = true ]; then
    echo "  Installing Python 3.12 via Homebrew..."
    install_homebrew
    brew install python@3.12

    # Find the newly installed Python
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

# Verify Python works
if ! "$PYTHON" --version &> /dev/null; then
    echo ""
    echo "  ✗ Python installation failed."
    echo "    Please install Python 3.10+ from: https://www.python.org/downloads/"
    echo ""
    read -p "  Press Enter to exit..."
    exit 1
fi

echo "  Using $("$PYTHON" --version)"

# ── Step 3: Native libraries (PortAudio + Opus) ──
if ! command -v brew &> /dev/null; then
    install_homebrew
fi

BREW_NEEDED=""
if ! brew list portaudio &> /dev/null 2>&1; then
    BREW_NEEDED="portaudio"
fi
if ! brew list opus &> /dev/null 2>&1; then
    BREW_NEEDED="$BREW_NEEDED opus"
fi

if [ -n "$BREW_NEEDED" ]; then
    echo "  Installing audio libraries: $BREW_NEEDED"
    brew install $BREW_NEEDED
fi

# ── Step 4: Virtual environment & Python dependencies ──
# If venv exists but uses an old Python, recreate it
if [ -d "venv" ]; then
    VENV_PY_MINOR=$(./venv/bin/python -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo "0")
    if [ "$VENV_PY_MINOR" -lt 10 ]; then
        echo "  Existing venv uses Python 3.$VENV_PY_MINOR — recreating..."
        rm -rf venv
    fi
fi

if [ ! -d "venv" ]; then
    echo "  Creating virtual environment..."
    "$PYTHON" -m venv venv
    if [ $? -ne 0 ]; then
        echo ""
        echo "  ✗ Failed to create virtual environment."
        echo "    Try: $PYTHON -m pip install --upgrade pip"
        echo ""
        read -p "  Press Enter to exit..."
        exit 1
    fi
fi

# Install/verify Python packages
# .deps_ok is deleted after updates to force a reinstall
if [ ! -f ".deps_ok" ] || ! ./venv/bin/python -c "import sounddevice, numpy, PySide6, zeroconf, opuslib" 2>/dev/null; then
    echo "  Installing Python packages..."
    ./venv/bin/pip install --upgrade pip -q
    ./venv/bin/pip install -r requirements.txt 2>&1 | while read line; do
        # Show progress without overwhelming output
        case "$line" in
            *Installing*|*Successfully*|*ERROR*|*error*|*Error*)
                echo "    $line"
                ;;
        esac
    done

    # Verify the install worked
    if ! ./venv/bin/python -c "import sounddevice, numpy, PySide6" 2>/dev/null; then
        echo ""
        echo "  ✗ Some dependencies failed to install."
        echo "    Running full install with details..."
        echo ""
        ./venv/bin/pip install -r requirements.txt
        echo ""
        read -p "  Press Enter to exit..."
        exit 1
    fi
    echo "  ✓ Dependencies installed."
    touch .deps_ok
else
    echo "  ✓ All dependencies OK."
fi

# Install optional dependencies (Stream Deck) — non-fatal if they fail
if ! ./venv/bin/python -c "import StreamDeck" 2>/dev/null; then
    echo "  Installing optional packages (Stream Deck support)..."
    ./venv/bin/pip install -r requirements-optional.txt -q 2>/dev/null
    if ./venv/bin/python -c "import StreamDeck" 2>/dev/null; then
        echo "  ✓ Stream Deck support installed."
    else
        echo "  ✓ Stream Deck support skipped (no hardware detected)."
    fi
fi

# ── Step 5: Clear Gatekeeper quarantine from venv (PySide6/Qt libs) ──
if [ -d "venv" ]; then
    xattr -rd com.apple.quarantine venv/ 2>/dev/null || true
fi

# ── Step 6: Accessibility permissions (for global PTT hotkey) ──
if ! ./venv/bin/python -c "
import subprocess, sys
result = subprocess.run(
    ['osascript', '-e', 'tell application \"System Events\" to keystroke \"\"'],
    capture_output=True, timeout=5
)
sys.exit(0 if result.returncode == 0 else 1)
" 2>/dev/null; then
    echo ""
    echo "  ┌──────────────────────────────────────────┐"
    echo "  │  Push-to-Talk needs Accessibility access │"
    echo "  └──────────────────────────────────────────┘"
    echo ""
    echo "  The global PTT hotkey (backtick key) needs"
    echo "  Accessibility permissions to work system-wide."
    echo ""
    echo "  Opening System Settings → Privacy → Accessibility..."
    echo "  Add 'Terminal' (or your terminal app) to the list."
    echo ""
    open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
    echo "  Office Hours will still launch — PTT just won't work"
    echo "  until you grant access and restart the app."
    echo ""
    read -p "  Press Enter to continue launching..."
fi

# ── Step 7: Launch ──
echo ""
echo "  Starting Office Hours..."
echo "  ════════════════════════════════════════"
echo ""
./venv/bin/python run.py
EXIT_CODE=$?

# Keep window open if something went wrong
if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "  ──────────────────────────────────────"
    echo "  Office Hours exited unexpectedly."
    if [ -f "crash.log" ]; then
        echo ""
        echo "  Crash log:"
        tail -20 crash.log
    fi
    echo ""
    echo "  If this keeps happening, please report at:"
    echo "  https://github.com/matthannam-fart/office-hours/issues"
    echo "  ──────────────────────────────────────"
    echo ""
    read -p "  Press Enter to close..."
fi
