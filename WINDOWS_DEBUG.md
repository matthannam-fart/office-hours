# Vox — Windows Debugging Guide

## The Problem
The app either crashes on launch or the UI is unreadable (elements crammed together, text overlapping, panel too small or misaligned).

## Quick Start

```powershell
cd path\to\office-hours
git pull
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python run.py
```

If it crashes, check `crash.log` in the app folder.

---

## Step 1: Collect Diagnostics

Run this in the office-hours directory and share the output:

```powershell
venv\Scripts\python -c "
import sys, platform
print('Python:', sys.version)
print('Platform:', platform.platform())
print('Architecture:', platform.architecture())

# Check all required imports
modules = ['PySide6', 'sounddevice', 'numpy', 'zeroconf', 'cryptography', 'pynput', 'opuslib']
for m in modules:
    try:
        mod = __import__(m)
        ver = getattr(mod, '__version__', getattr(mod, 'VERSION', '?'))
        print(f'{m}: {ver}')
    except ImportError as e:
        print(f'{m}: MISSING - {e}')

# Check PySide6 submodules (these can fail independently)
qt_modules = [
    'PySide6.QtWidgets', 'PySide6.QtCore', 'PySide6.QtGui',
    'PySide6.QtMultimedia'
]
for m in qt_modules:
    try:
        __import__(m)
        print(f'{m}: OK')
    except ImportError as e:
        print(f'{m}: FAILED - {e}')

# Check display scaling
try:
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    screen = app.primaryScreen()
    print(f'Screen: {screen.size().width()}x{screen.size().height()}')
    print(f'Device pixel ratio: {screen.devicePixelRatio()}')
    print(f'Logical DPI: {screen.logicalDotsPerInch()}')
    print(f'Physical DPI: {screen.physicalDotsPerInch()}')
except Exception as e:
    print(f'Screen info failed: {e}')
"
```

## Step 2: Known Windows Issues

### UI is crammed / unreadable
This is likely a **DPI scaling issue**. Windows high-DPI displays (125%, 150%, 200%) can cause Qt widgets to render at wrong sizes.

**Fix attempt — add to the very top of `main.py` (before any other imports):**
```python
import os
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"
```

Or try running with scaling overridden:
```powershell
set QT_SCALE_FACTOR=1.0
venv\Scripts\python run.py
```

### Panel appears off-screen or at wrong position
The panel anchors to the system tray icon position, which behaves differently on Windows. The tray icon may be in the overflow area.

**To test:** Look in `floating_panel.py` for `show_at` and check if the panel position is within screen bounds.

### App crashes instantly (no window at all)
1. Check `crash.log` — if it exists, it has the traceback
2. If no crash.log, the error is at import time. Run diagnostics from Step 1.
3. Common cause: `PySide6.QtMultimedia` fails on Windows without media codecs. Try:
   ```powershell
   venv\Scripts\pip install PySide6-Addons
   ```

### Stream Deck not detected
- The Elgato Stream Deck app locks the USB device exclusively
- Quit the Elgato app, then restart Vox
- If no Stream Deck hardware, ignore this — the app runs fine without it

---

## Step 3: UI Layout Investigation

The floating panel (`floating_panel.py`) uses hardcoded pixel sizes designed on macOS. Key values to inspect on Windows:

- `PANEL_W` — panel width (currently fixed)
- Font sizes in stylesheets (`font-size: 13px`, etc.)
- `setFixedHeight()` / `setFixedWidth()` calls
- Margin/padding values in `setContentsMargins()` and `setSpacing()`

**To see what's happening visually**, add this temporarily to `floating_panel.py` in the `__init__` after `self._build_ui()`:

```python
# DEBUG: Draw borders on all frames to see layout
self.setStyleSheet(self.styleSheet() + """
    QFrame { border: 1px solid red; }
    QLabel { border: 1px solid blue; }
    QPushButton { border: 1px solid green; }
""")
```

This will outline every widget so you can see what's overlapping.

---

## Step 4: Specific Files to Look At

| File | Windows concern |
|------|----------------|
| `floating_panel.py` | Main UI — all layout, sizing, positioning |
| `main.py` | App init, tray icon, panel positioning |
| `network_manager.py` | Socket binding (port conflicts) |
| `hotkey_manager.py` | pynput global hotkeys (needs accessibility) |
| `deck_ws_server.py` | WebSocket bridge for Stream Deck plugin |
| `config.py` | Network ports, relay server address |
| `user_settings.py` | Config file paths (`%APPDATA%\Vox`) |

---

## Step 5: Share Results

Please share:
1. Output from the diagnostics script (Step 1)
2. Contents of `crash.log` (if it exists)
3. A screenshot of the UI if it launches but looks wrong
4. Windows version and display scaling percentage (Settings > Display > Scale)
