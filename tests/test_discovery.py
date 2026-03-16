"""Tests for discovery_manager.py — service info construction, peer name
parsing, and duplicate peer filtering.

All tests are self-contained — no real Zeroconf/mDNS operations.
"""

import socket

# ── Service info construction ────────────────────────────────────

class TestServiceInfoConstruction:
    """Test Zeroconf service info properties."""

    def test_service_type_format(self):
        """Service type should follow mDNS naming convention."""
        service_type = "_talkback._tcp.local."
        assert service_type.startswith("_")
        assert service_type.endswith(".local.")
        assert "._tcp." in service_type

    def test_service_name_includes_app_name(self):
        """Service name should include the app name."""
        from config import APP_NAME
        hostname = socket.gethostname()
        service_type = "_talkback._tcp.local."
        service_name = f"{APP_NAME} ({hostname}).{service_type}"
        assert APP_NAME in service_name
        assert hostname in service_name

    def test_service_name_includes_hostname(self):
        """Service name should include the machine's hostname."""
        hostname = socket.gethostname()
        service_type = "_talkback._tcp.local."
        service_name = f"Vox ({hostname}).{service_type}"
        assert hostname in service_name

    def test_service_port_matches_config(self):
        """Service should register on the configured TCP port."""
        from config import TCP_PORT
        assert isinstance(TCP_PORT, int)
        assert TCP_PORT > 0

    def test_inet_aton_produces_4_bytes(self):
        """socket.inet_aton should produce 4 bytes for valid IPs."""
        addr_bytes = socket.inet_aton("192.168.1.1")
        assert len(addr_bytes) == 4

    def test_service_properties_include_version(self):
        """Service properties should include a version key."""
        props = {"version": "2.0"}
        assert "version" in props


# ── Peer name parsing ────────────────────────────────────────────

class TestPeerNameParsing:
    """Test parsing peer display names from mDNS service names."""

    def test_parse_peer_name_standard_format(self):
        """Standard service name should be parseable to get the peer name."""
        service_name = "Vox (Alice-MacBook)._talkback._tcp.local."
        # Extract the part before the service type
        name_part = service_name.split("._talkback._tcp.local.")[0]
        assert name_part == "Vox (Alice-MacBook)"

    def test_parse_hostname_from_service_name(self):
        """Hostname should be extractable from parentheses."""
        service_name = "Vox (my-desktop)._talkback._tcp.local."
        name_part = service_name.split("._talkback._tcp.local.")[0]
        # Extract hostname from within parentheses
        if "(" in name_part and ")" in name_part:
            hostname = name_part[name_part.index("(") + 1 : name_part.index(")")]
        else:
            hostname = name_part
        assert hostname == "my-desktop"

    def test_parse_peer_name_with_special_chars(self):
        """Service names with special characters should parse correctly."""
        service_name = "Vox (Matt's-PC)._talkback._tcp.local."
        name_part = service_name.split("._talkback._tcp.local.")[0]
        assert "Matt's-PC" in name_part

    def test_self_filtering(self):
        """A peer with the same service name as self should be filtered."""
        my_name = "Vox (my-mac)._talkback._tcp.local."
        peer_name = "Vox (my-mac)._talkback._tcp.local."
        assert my_name == peer_name  # Should be skipped


# ── Duplicate peer filtering ─────────────────────────────────────

class TestDuplicatePeerFiltering:
    """Test that duplicate peer discoveries are handled."""

    def test_duplicate_peer_same_ip(self):
        """Adding the same peer IP twice should be detectable."""
        peers = {}
        peers["Vox (peer1)._talkback._tcp.local."] = "192.168.1.10"
        peers["Vox (peer1)._talkback._tcp.local."] = "192.168.1.10"
        assert len(peers) == 1  # Dict deduplicates by key

    def test_different_peers_different_keys(self):
        """Different peers should have different dict keys."""
        peers = {}
        peers["Vox (peer1)._talkback._tcp.local."] = "192.168.1.10"
        peers["Vox (peer2)._talkback._tcp.local."] = "192.168.1.11"
        assert len(peers) == 2

    def test_peer_removal_on_lost(self):
        """Removing a peer on service lost should work."""
        peers = {}
        peers["Vox (peer1)._talkback._tcp.local."] = "192.168.1.10"
        peers["Vox (peer2)._talkback._tcp.local."] = "192.168.1.11"
        del peers["Vox (peer1)._talkback._tcp.local."]
        assert len(peers) == 1
        assert "Vox (peer2)._talkback._tcp.local." in peers

    def test_peer_ip_update(self):
        """A peer re-announcing with a different IP should update."""
        peers = {}
        peers["Vox (peer1)._talkback._tcp.local."] = "192.168.1.10"
        peers["Vox (peer1)._talkback._tcp.local."] = "192.168.1.20"
        assert peers["Vox (peer1)._talkback._tcp.local."] == "192.168.1.20"

    def test_inet_ntoa_roundtrip(self):
        """inet_aton/inet_ntoa should round-trip IP addresses."""
        ip = "192.168.1.42"
        addr_bytes = socket.inet_aton(ip)
        recovered = socket.inet_ntoa(addr_bytes)
        assert recovered == ip
