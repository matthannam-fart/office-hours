"""Tests for user_settings.py — settings persistence, user ID, display name, etc."""

import json


def test_load_settings_returns_empty_dict_when_no_file(patched_settings):
    """load_settings() should return {} when no settings file exists."""
    import user_settings
    result = user_settings.load_settings()
    assert result == {}


def test_save_and_load_settings(patched_settings):
    """Settings saved to disk should be loadable."""
    import user_settings
    data = {"display_name": "TestUser", "ptt_hotkey": "F5"}
    user_settings.save_settings(data)
    loaded = user_settings.load_settings()
    assert loaded == data


def test_save_settings_creates_valid_json(patched_settings):
    """Saved settings file should be valid JSON."""
    import user_settings
    user_settings.save_settings({"key": "value"})
    with open(patched_settings) as f:
        data = json.load(f)
    assert data == {"key": "value"}


def test_get_display_name_default(patched_settings):
    """get_display_name() should return None when not set."""
    import user_settings
    assert user_settings.get_display_name() is None


def test_set_and_get_display_name(patched_settings):
    """set_display_name() should persist and be retrievable."""
    import user_settings
    user_settings.set_display_name("Alice")
    assert user_settings.get_display_name() == "Alice"


def test_set_display_name_creates_user_id(patched_settings):
    """set_display_name() should also create a user_id if missing."""
    import user_settings
    user_settings.set_display_name("Bob")
    settings = user_settings.load_settings()
    assert "user_id" in settings
    assert len(settings["user_id"]) >= 16  # Full UUID


def test_get_user_id_creates_if_missing(patched_settings):
    """get_user_id() should create a UUID if none exists."""
    import user_settings
    # Seed with a display name but no user_id
    user_settings.save_settings({"display_name": "Test"})
    uid = user_settings.get_user_id()
    assert len(uid) >= 16


def test_get_user_id_migrates_short_ids(patched_settings):
    """get_user_id() should replace short IDs (< 16 chars) with full UUIDs."""
    import user_settings
    user_settings.save_settings({"user_id": "abcd1234"})
    uid = user_settings.get_user_id()
    assert len(uid) >= 16
    assert uid != "abcd1234"


def test_get_user_id_stable(patched_settings):
    """get_user_id() should return the same ID on repeated calls."""
    import user_settings
    user_settings.set_display_name("Stable")
    uid1 = user_settings.get_user_id()
    uid2 = user_settings.get_user_id()
    assert uid1 == uid2


def test_ptt_hotkey_default(patched_settings):
    """get_ptt_hotkey() should return None by default."""
    import user_settings
    assert user_settings.get_ptt_hotkey() is None


def test_set_and_get_ptt_hotkey(patched_settings):
    """PTT hotkey should round-trip through save/load."""
    import user_settings
    user_settings.set_ptt_hotkey("F6")
    assert user_settings.get_ptt_hotkey() == "F6"


def test_active_team_ids_default(patched_settings):
    """get_active_team_ids() should return empty list by default."""
    import user_settings
    assert user_settings.get_active_team_ids() == []


def test_set_and_get_active_team_ids(patched_settings):
    import user_settings
    user_settings.set_active_team_ids(["team-abc", "team-xyz"])
    assert user_settings.get_active_team_ids() == ["team-abc", "team-xyz"]


def test_active_team_ids_roundtrip(patched_settings):
    import user_settings
    user_settings.set_active_team_ids(["one"])
    assert user_settings.get_active_team_ids() == ["one"]
    user_settings.set_active_team_ids([])
    assert user_settings.get_active_team_ids() == []


def test_deck_guide_dismissed_default(patched_settings):
    import user_settings
    assert user_settings.get_deck_guide_dismissed() is False


def test_set_deck_guide_dismissed(patched_settings):
    import user_settings
    user_settings.set_deck_guide_dismissed(True)
    assert user_settings.get_deck_guide_dismissed() is True


def test_trusted_peers_default(patched_settings):
    """Trusted peers should be empty by default."""
    import user_settings
    assert user_settings.get_trusted_peers() == {}


def test_trust_peer_and_retrieve(patched_settings):
    """trust_peer() should store fingerprint, get_peer_fingerprint() retrieves it."""
    import user_settings
    user_settings.trust_peer("192.168.1.10", "aa:bb:cc:dd")
    assert user_settings.get_peer_fingerprint("192.168.1.10") == "aa:bb:cc:dd"
    assert user_settings.get_peer_fingerprint("192.168.1.99") is None


def test_compute_cert_fingerprint():
    """compute_cert_fingerprint() should return colon-separated SHA-256 hex."""
    import user_settings
    # Known SHA-256 of b"test"
    fp = user_settings.compute_cert_fingerprint(b"test")
    assert ":" in fp
    # SHA-256 of b"test" is well-known
    parts = fp.split(":")
    assert len(parts) == 32  # 32 hex pairs for SHA-256
    assert all(len(p) == 2 for p in parts)


def test_load_settings_handles_corrupt_file(patched_settings):
    """load_settings() should return {} if the file contains invalid JSON."""
    import user_settings
    with open(patched_settings, 'w') as f:
        f.write("not valid json {{{")
    result = user_settings.load_settings()
    assert result == {}
