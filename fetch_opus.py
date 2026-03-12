#!/usr/bin/env python3
"""Download the Opus codec library for the current platform.

Run this script to fetch the native Opus library needed for high-quality audio.
On macOS it installs via Homebrew; on Windows it downloads a pre-built DLL;
on Linux it installs via the system package manager.

This is called automatically by the install scripts, but can also be run manually:
    python fetch_opus.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# MSYS2 mingw64 opus package — well-maintained, always up to date
OPUS_MSYS2_URL = "https://mirror.msys2.org/mingw/mingw64/mingw-w64-x86_64-opus-1.5.2-1-any.pkg.tar.zst"
# Fallback: GitHub release source (requires building)
OPUS_VERSION = "1.5.2"


def check_opus():
    """Return True if opus is already loadable."""
    try:
        import ctypes
        import ctypes.util
        if ctypes.util.find_library('opus'):
            return True
        # Check known paths by platform
        candidates = [
            os.path.join(APP_DIR, 'opus.dll'),
            os.path.join(APP_DIR, 'libopus-0.dll'),
            os.path.join(APP_DIR, 'libopus.dll'),
            os.path.join(APP_DIR, 'libs', 'opus.dll'),
            '/opt/homebrew/lib/libopus.dylib',
            '/usr/local/lib/libopus.dylib',
            '/usr/lib/x86_64-linux-gnu/libopus.so.0',
            '/usr/lib/libopus.so.0',
        ]
        for path in candidates:
            if os.path.exists(path):
                try:
                    ctypes.CDLL(path)
                    return True
                except OSError:
                    continue
        return False
    except Exception:
        return False


def fetch_windows():
    """Download opus.dll for Windows (usually bundled — this is a fallback)."""
    dll_path = os.path.join(APP_DIR, 'opus.dll')
    if os.path.exists(dll_path):
        print("  ✓ opus.dll bundled with app")
        return True

    # Also check libopus-0.dll (common name from MSYS2/vcpkg)
    alt_path = os.path.join(APP_DIR, 'libopus-0.dll')
    if os.path.exists(alt_path):
        print("  ✓ libopus-0.dll found")
        return True

    print("  opus.dll missing — downloading Opus codec...")

    tmpdir = tempfile.mkdtemp(prefix='opus_')
    try:
        # Strategy 1: pip install opuslib already done, just need the native DLL.
        # Use PowerShell to download from NuGet (most reliable — just a zip file)
        nupkg = os.path.join(tmpdir, 'libopus.zip')
        nuget_url = "https://www.nuget.org/api/v2/package/libopus/1.4.0"
        print("  Trying NuGet package...")
        dl_result = subprocess.run(
            ['powershell', '-Command',
             f'[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; '
             f'Invoke-WebRequest -Uri "{nuget_url}" -OutFile "{nupkg}" -TimeoutSec 30'],
            capture_output=True, timeout=60
        )
        if dl_result.returncode == 0 and os.path.exists(nupkg):
            import zipfile
            extract_dir = os.path.join(tmpdir, 'nuget')
            with zipfile.ZipFile(nupkg, 'r') as z:
                z.extractall(extract_dir)
            # Find the x64 DLL
            best = None
            for root, dirs, files in os.walk(extract_dir):
                for f in files:
                    if f.lower() in ('opus.dll', 'libopus.dll', 'libopus-0.dll'):
                        full = os.path.join(root, f)
                        if 'x64' in root.lower() or 'amd64' in root.lower():
                            best = full
                            break
                        elif not best:
                            best = full
            if best:
                shutil.copy2(best, dll_path)
                print("  ✓ Installed opus.dll from NuGet")
                return True

        print("  NuGet strategy failed.")

        # Strategy 2: Try MSYS2 package (needs modern tar with zstd)
        print("  Trying MSYS2 package...")
        pkg_file = os.path.join(tmpdir, 'opus.pkg.tar.zst')
        result = subprocess.run(
            ['curl', '-fsSL', OPUS_MSYS2_URL, '-o', pkg_file],
            capture_output=True, timeout=30
        )
        if result.returncode == 0 and os.path.exists(pkg_file):
            extract_dir = os.path.join(tmpdir, 'extracted')
            os.makedirs(extract_dir, exist_ok=True)
            result = subprocess.run(
                ['tar', '-xf', pkg_file, '-C', extract_dir],
                capture_output=True, timeout=30
            )
            if result.returncode == 0:
                for root, dirs, files in os.walk(extract_dir):
                    for f in files:
                        if f.lower() in ('libopus-0.dll', 'opus.dll', 'libopus.dll'):
                            src = os.path.join(root, f)
                            shutil.copy2(src, dll_path)
                            print(f"  ✓ Installed {f} from MSYS2")
                            return True

        print("  MSYS2 strategy failed.")

        # Strategy 3: Direct download of a known good build via PowerShell
        print("  Trying direct download...")
        direct_url = "https://github.com/nicedoc/opus-dll/raw/master/opus.dll"
        dl_result = subprocess.run(
            ['powershell', '-Command',
             f'[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; '
             f'Invoke-WebRequest -Uri "{direct_url}" -OutFile "{dll_path}" -TimeoutSec 30'],
            capture_output=True, timeout=60
        )
        if dl_result.returncode == 0 and os.path.exists(dll_path):
            # Verify it's actually loadable
            import ctypes
            try:
                ctypes.CDLL(dll_path)
                print("  ✓ Installed opus.dll")
                return True
            except OSError:
                os.remove(dll_path)
                print("  Downloaded DLL is not compatible with this system.")

    except Exception as e:
        print(f"  Download error: {e}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # All strategies failed
    print()
    print("  Could not auto-install Opus. To install manually:")
    print("    Option A: choco install opus-tools  (if you have Chocolatey)")
    print("    Option B: Download opus.dll and place it in the app folder:")
    print(f"              {APP_DIR}")
    print()
    print("  Office Hours will still work without Opus (using lower-quality µ-law codec).")
    return False


def fetch_macos():
    """Install libopus via Homebrew on macOS."""
    # Check if already installed
    for path in ['/opt/homebrew/lib/libopus.dylib', '/usr/local/lib/libopus.dylib']:
        if os.path.exists(path):
            print(f"  libopus found at {path}")
            return True

    if shutil.which('brew'):
        print("  Installing libopus via Homebrew...")
        result = subprocess.run(['brew', 'install', 'opus'],
                                capture_output=True, text=True)
        if result.returncode == 0:
            print("  libopus installed.")
            return True
        else:
            print(f"  brew install failed: {result.stderr}")
    else:
        print("  Homebrew not found. Install libopus with: brew install opus")

    return False


def fetch_linux():
    """Install libopus via system package manager on Linux."""
    import ctypes.util
    if ctypes.util.find_library('opus'):
        print("  libopus already installed.")
        return True

    # Try common package managers
    for cmd in [
        ['sudo', 'apt-get', 'install', '-y', 'libopus0'],
        ['sudo', 'dnf', 'install', '-y', 'opus'],
        ['sudo', 'pacman', '-S', '--noconfirm', 'opus'],
    ]:
        if shutil.which(cmd[1]):
            print(f"  Installing libopus via {cmd[1]}...")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                print("  libopus installed.")
                return True
            else:
                print(f"  {cmd[1]} install failed: {result.stderr}")
            break

    print("  Could not auto-install libopus. Install manually:")
    print("    Ubuntu/Debian: sudo apt install libopus0")
    print("    Fedora: sudo dnf install opus")
    print("    Arch: sudo pacman -S opus")
    return False


def main():
    print("Checking Opus codec library...")

    if check_opus():
        print("  Opus library is available.")
        return 0

    if sys.platform == 'win32':
        ok = fetch_windows()
    elif sys.platform == 'darwin':
        ok = fetch_macos()
    else:
        ok = fetch_linux()

    if ok and check_opus():
        print("  Opus library ready.")
        return 0
    elif ok:
        print("  Opus installed but could not load — may need a restart.")
        return 0
    else:
        print("  Opus not available — app will use µ-law codec (lower quality).")
        return 1


if __name__ == '__main__':
    sys.exit(main())
