"""
Shared fixtures for Vox tests.

These tests are designed to run WITHOUT a running app, network, or GUI.
They validate config defaults, settings persistence, protocol logic,
and pure utility functions so Claude can self-verify changes.
"""

import os
import sys

import pytest

# Add project root to path so we can import modules directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def tmp_settings_file(tmp_path):
    """Provide a temporary settings file path and patch user_settings to use it."""
    settings_file = tmp_path / "settings.json"
    return str(settings_file)


@pytest.fixture
def patched_settings(tmp_settings_file, monkeypatch):
    """Patch user_settings module to use a temporary settings file.
    Returns the path so tests can inspect the file directly."""
    import user_settings
    monkeypatch.setattr(user_settings, "SETTINGS_FILE", tmp_settings_file)
    return tmp_settings_file
