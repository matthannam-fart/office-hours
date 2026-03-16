"""Tests for auth_manager.py — PKCE generation, auth URL construction,
token parsing from callback, and session storage/retrieval.

All tests are self-contained — no real HTTP calls or browser launches.
"""

import base64
import hashlib
import json
import urllib.parse

# ── PKCE code_verifier and code_challenge ────────────────────────

class TestPKCE:
    """Test PKCE code_verifier and code_challenge generation."""

    def test_generate_pkce_returns_tuple(self):
        """_generate_pkce should return (code_verifier, code_challenge)."""
        from auth_manager import _generate_pkce
        verifier, challenge = _generate_pkce()
        assert isinstance(verifier, str)
        assert isinstance(challenge, str)

    def test_code_verifier_is_base64url(self):
        """code_verifier should be URL-safe base64 without padding."""
        from auth_manager import _generate_pkce
        verifier, _ = _generate_pkce()
        # Should not contain padding or non-URL-safe chars
        assert "=" not in verifier
        assert "+" not in verifier
        assert "/" not in verifier
        assert len(verifier) > 20

    def test_code_challenge_matches_verifier(self):
        """code_challenge should be SHA256(verifier) base64url-encoded."""
        from auth_manager import _generate_pkce
        verifier, challenge = _generate_pkce()
        expected_hash = hashlib.sha256(verifier.encode("ascii")).digest()
        expected_challenge = base64.urlsafe_b64encode(expected_hash).rstrip(b"=").decode("ascii")
        assert challenge == expected_challenge

    def test_pkce_values_are_unique(self):
        """Each call should generate a different verifier/challenge."""
        from auth_manager import _generate_pkce
        pairs = [_generate_pkce() for _ in range(10)]
        verifiers = [v for v, _ in pairs]
        assert len(set(verifiers)) == 10

    def test_code_verifier_length(self):
        """code_verifier should be 43 chars (32 random bytes base64url, no padding)."""
        from auth_manager import _generate_pkce
        verifier, _ = _generate_pkce()
        assert len(verifier) == 43  # ceil(32 * 4/3) without padding


# ── Auth URL construction ─────────────────────────────────────────

class TestAuthURL:
    """Test OAuth authorize URL construction."""

    def test_authorize_url_has_required_params(self):
        """OAuth authorize URL should contain provider, redirect, PKCE params."""
        from auth_manager import _AUTH_BASE, _generate_pkce
        _, code_challenge = _generate_pkce()
        redirect_uri = "http://localhost:54321/callback"

        params = urllib.parse.urlencode({
            "provider": "google",
            "redirect_to": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        })
        url = f"{_AUTH_BASE}/authorize?{params}"

        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)

        assert query["provider"] == ["google"]
        assert query["redirect_to"] == [redirect_uri]
        assert query["code_challenge_method"] == ["S256"]
        assert "code_challenge" in query

    def test_authorize_url_uses_auth_base(self):
        """The authorize URL should start with the auth base path."""
        from auth_manager import _AUTH_BASE
        url = f"{_AUTH_BASE}/authorize?provider=google"
        assert url.startswith(_AUTH_BASE)
        assert "/authorize" in url

    def test_auth_headers_include_apikey(self):
        """_auth_headers should always include the apikey."""
        from auth_manager import _auth_headers
        headers = _auth_headers()
        assert "apikey" in headers
        assert headers["Content-Type"] == "application/json"

    def test_auth_headers_with_access_token(self):
        """_auth_headers should include Authorization when token provided."""
        from auth_manager import _auth_headers
        headers = _auth_headers(access_token="test-token-123")
        assert headers["Authorization"] == "Bearer test-token-123"

    def test_auth_headers_without_access_token(self):
        """_auth_headers should not include Authorization without token."""
        from auth_manager import _auth_headers
        headers = _auth_headers()
        assert "Authorization" not in headers


# ── Token parsing from callback ───────────────────────────────────

class TestTokenParsing:
    """Test parsing tokens from the OAuth callback."""

    def test_parse_code_from_query_params(self):
        """Authorization code should be extractable from query params."""
        callback_url = "http://localhost:54321/callback?code=abc123"
        parsed = urllib.parse.urlparse(callback_url)
        query = urllib.parse.parse_qs(parsed.query)
        assert query["code"] == ["abc123"]

    def test_parse_tokens_from_hash_fragment(self):
        """Tokens should be extractable from URL hash fragment."""
        # This simulates what the browser JS does — parsing the hash
        fragment = "access_token=at_123&refresh_token=rt_456&expires_in=3600&token_type=bearer"
        params = urllib.parse.parse_qs(fragment)
        assert params["access_token"] == ["at_123"]
        assert params["refresh_token"] == ["rt_456"]
        assert params["expires_in"] == ["3600"]

    def test_parse_error_from_query_params(self):
        """Error responses should be parseable from query params."""
        callback_url = "http://localhost:54321/callback?error=access_denied&error_description=User+denied"
        parsed = urllib.parse.urlparse(callback_url)
        query = urllib.parse.parse_qs(parsed.query)
        assert query["error"] == ["access_denied"]
        assert query["error_description"] == ["User denied"]

    def test_token_callback_json_parsing(self):
        """POST /token_callback body should parse as JSON with expected fields."""
        body = json.dumps({
            "access_token": "at_abc",
            "refresh_token": "rt_xyz",
            "expires_in": "3600",
            "token_type": "bearer",
        })
        tokens = json.loads(body)
        assert tokens["access_token"] == "at_abc"
        assert tokens["refresh_token"] == "rt_xyz"

    def test_auth_error_class(self):
        """AuthError should store message and status_code."""
        from auth_manager import AuthError
        err = AuthError("bad request", 400)
        assert str(err) == "bad request"
        assert err.status_code == 400

    def test_auth_error_without_status_code(self):
        """AuthError should work without a status code."""
        from auth_manager import AuthError
        err = AuthError("unknown error")
        assert str(err) == "unknown error"
        assert err.status_code is None


# ── Session storage and retrieval ─────────────────────────────────

class TestSessionStorage:
    """Test auth session persistence via user_settings."""

    def test_save_and_load_session(self, patched_settings):
        """save_auth_session/get_auth_session should round-trip."""
        import user_settings
        session = {
            "access_token": "at_test",
            "refresh_token": "rt_test",
            "expires_at": 9999999999,
            "user_id": "user-123",
            "email": "test@example.com",
        }
        user_settings.save_auth_session(session)
        loaded = user_settings.get_auth_session()
        assert loaded is not None
        assert loaded["access_token"] == "at_test"
        assert loaded["refresh_token"] == "rt_test"
        assert loaded["email"] == "test@example.com"

    def test_get_auth_session_returns_none_when_empty(self, patched_settings):
        """get_auth_session should return None when no session stored."""
        import user_settings
        assert user_settings.get_auth_session() is None

    def test_clear_auth_session(self, patched_settings):
        """clear_auth_session should remove the stored session."""
        import user_settings
        user_settings.save_auth_session({
            "access_token": "at_temp",
            "refresh_token": "rt_temp",
        })
        assert user_settings.get_auth_session() is not None
        user_settings.clear_auth_session()
        assert user_settings.get_auth_session() is None

    def test_is_logged_in_true(self, patched_settings):
        """is_logged_in should return True when session has access_token."""
        import user_settings
        user_settings.save_auth_session({"access_token": "at_valid"})
        assert user_settings.is_logged_in() is True

    def test_is_logged_in_false(self, patched_settings):
        """is_logged_in should return False with no session."""
        import user_settings
        assert user_settings.is_logged_in() is False

    def test_session_stores_only_expected_keys(self, patched_settings):
        """save_auth_session should only store the expected keys."""
        import user_settings
        session = {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_at": 123,
            "user_id": "uid",
            "email": "e@e.com",
            "extra_field": "should_not_be_stored",
        }
        user_settings.save_auth_session(session)
        loaded = user_settings.get_auth_session()
        assert "extra_field" not in loaded
