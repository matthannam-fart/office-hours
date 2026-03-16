#!/usr/bin/env python3
"""Launch wrapper for Vox — catches crashes and logs them."""
import os
import sys


def run():
    try:
        from main import main
        main()
    except Exception:
        import traceback
        crash_log = traceback.format_exc()
        print(crash_log)
        try:
            crash_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash.log")
            with open(crash_path, "w") as f:
                f.write(crash_log)
            print(f"\nCrash log written to: {crash_path}")
        except Exception:
            pass
        if sys.platform == 'win32':
            input("\nPress Enter to close...")
        sys.exit(1)

if __name__ == "__main__":
    run()
