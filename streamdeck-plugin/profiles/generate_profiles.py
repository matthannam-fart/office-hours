#!/usr/bin/env python3
"""Generate .streamDeckProfile files for Office Hours.

Creates importable profiles for:
  - Stream Deck Mini (3x2) / Neo (4x2-ish)
  - Stream Deck Standard (5x3)

Users double-click the .streamDeckProfile file to import into Stream Deck app.
"""
import json
import os
import shutil
import tempfile
import uuid

PLUGIN_UUID = "com.officehours.intercom"

# Action UUIDs
ACTIONS = {
    "ptt":   f"{PLUGIN_UUID}.talk",
    "mode":  f"{PLUGIN_UUID}.mode",
    "team":  f"{PLUGIN_UUID}.team",
    "user":  f"{PLUGIN_UUID}.user",
    "logo":  f"{PLUGIN_UUID}.logo",
    "panel": f"{PLUGIN_UUID}.panel",
}

# Device models (from Elgato docs / community research)
DEVICES = {
    "mini":     0,  # Stream Deck Mini (3x2)
    "standard": 1,  # Stream Deck (5x3)
    "xl":       2,  # Stream Deck XL (8x4)
    "neo":      9,  # Stream Deck Neo (4x2)
}


def make_action(action_key: str, title: str) -> dict:
    """Create an action entry for a key position."""
    return {
        "Name": title,
        "Settings": {},
        "State": 0,
        "States": [
            {
                "FFamily": "",
                "FSize": "9",
                "FStyle": "",
                "FUnderline": "off",
                "Image": "",
                "Title": title,
                "TitleAlignment": "bottom",
                "TitleColor": "#ffffff",
                "TitleShow": "",
            }
        ],
        "UUID": ACTIONS[action_key],
    }


def build_manifest(
    name: str,
    device_model: int,
    actions: dict[str, dict],
) -> dict:
    """Build a manifest.json for a Stream Deck profile."""
    profile_uuid = str(uuid.uuid4()).upper()
    page_uuid = str(uuid.uuid4()).upper()

    return {
        "Actions": actions,
        "Controllers": {
            "Keypad": {
                "CurrentPage": page_uuid,
                "Pages": {
                    page_uuid: {
                        "Actions": actions,
                        "Name": "Default",
                    }
                },
            }
        },
        "DeviceModel": device_model,
        "DeviceUUID": "",
        "Name": name,
        "Pages": [page_uuid],
        "Version": "1.0",
        "AppIdentifier": "",
        "UUID": profile_uuid,
    }


def create_profile(name: str, device_model: int, layout: list[list[str | None]], output_dir: str):
    """Create a .streamDeckProfile file.

    layout: 2D array [row][col] of action keys or None for empty.
    """
    actions = {}
    for row_idx, row in enumerate(layout):
        for col_idx, action_key in enumerate(row):
            if action_key is not None:
                actions[f"{col_idx},{row_idx}"] = make_action(action_key, {
                    "ptt": "Push to Talk",
                    "mode": "Status",
                    "team": "Team",
                    "user": "User",
                    "logo": "OH",
                    "panel": "Panel",
                }[action_key])

    manifest = build_manifest(name, device_model, actions)

    # .streamDeckProfile is a directory with manifest.json, packaged as a folder
    # The Stream Deck app accepts both raw folders and the file directly
    profile_dir = os.path.join(output_dir, f"{name}.streamDeckProfile")
    os.makedirs(profile_dir, exist_ok=True)

    with open(os.path.join(profile_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"  Created: {profile_dir}")
    return profile_dir


def main():
    output_dir = os.path.dirname(os.path.abspath(__file__))

    print("Generating Office Hours Stream Deck profiles...\n")

    # ── 3x2 Mini layout ──
    # [Logo]  [Mode]   [Panel]
    # [PTT ]  [User]   [Team ]
    create_profile(
        name="Office Hours - Mini",
        device_model=DEVICES["mini"],
        layout=[
            ["logo",  "mode",  "panel"],
            ["ptt",   "user",  "team"],
        ],
        output_dir=output_dir,
    )

    # ── 5x3 Standard layout ──
    # [Logo]  [ ]     [ ]     [ ]     [Panel]
    # [PTT ]  [Mode]  [ ]     [ ]     [     ]
    # [Team]  [User]  [ ]     [ ]     [     ]
    create_profile(
        name="Office Hours - Standard",
        device_model=DEVICES["standard"],
        layout=[
            ["logo",  None,   None,  None,  "panel"],
            ["ptt",   "mode", None,  None,  None],
            ["team",  "user", None,  None,  None],
        ],
        output_dir=output_dir,
    )

    print("\nDone! Users can double-click the .streamDeckProfile folders to import.")
    print("Tip: In Stream Deck app → Preferences → Profiles → Import.")


if __name__ == "__main__":
    main()
