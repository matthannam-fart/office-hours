#!/bin/bash
# Office Hours — double-click to launch
cd "$(dirname "$0")"

# ── Auto-update ──
REPO_URL="https://github.com/matthannam-fart/office-hours"

if [ -d ".git" ] && command -v git &> /dev/null; then
    git pull --ff-only 2>/dev/null
else
    LATEST_SHA=$(curl -fsSL "https://api.github.com/repos/matthannam-fart/office-hours/commits/main" 2>/dev/null | grep '"sha"' | head -1 | cut -d'"' -f4)
    LOCAL_SHA=""
    [ -f ".version" ] && LOCAL_SHA=$(cat .version)

    if [ -n "$LATEST_SHA" ] && [ "$LATEST_SHA" != "$LOCAL_SHA" ]; then
        echo "Updating..."
        TMPDIR_UP=$(mktemp -d)
        if curl -fsSL "${REPO_URL}/archive/refs/heads/main.zip" -o "$TMPDIR_UP/update.zip" 2>/dev/null; then
            unzip -qo "$TMPDIR_UP/update.zip" -d "$TMPDIR_UP"
            rsync -a --exclude='venv' --exclude='.version' --exclude='user_settings.json' \
                "$TMPDIR_UP/office-hours-main/" "$(pwd)/"
            echo "$LATEST_SHA" > .version
            xattr -rd com.apple.quarantine "$(pwd)" 2>/dev/null || true
        fi
        rm -rf "$TMPDIR_UP"
    fi
fi

# Create venv if it doesn't exist
if [ ! -d "venv" ]; then
    echo "First run — setting up Python environment..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

python3 main.py
