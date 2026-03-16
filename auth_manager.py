"""
auth_manager.py — Supabase GoTrue Auth wrapper for Vox.

Uses only urllib (no pip dependencies). Provides email/password, Google OAuth
(PKCE), and magic link authentication flows.
"""

import base64
import hashlib
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from config import SUPABASE_ANON_KEY, SUPABASE_URL, log

# ── Helpers ─────────────────────────────────────────────────────

_AUTH_BASE = f"{SUPABASE_URL}/auth/v1"


class _AuthHTTPServer(HTTPServer):
    """HTTPServer subclass with typed attributes for OAuth callback state."""

    _auth_code: str | None = None
    _auth_error: str | None = None
    _auth_tokens: dict | None = None

# Ports to try for the local OAuth callback server
_CALLBACK_PORTS = [54321, 54322, 54323]

# Timeout for OAuth / magic link callback (seconds)
_CALLBACK_TIMEOUT = 120


def _auth_headers(access_token=None):
    """Standard GoTrue auth headers."""
    h = {
        "apikey": SUPABASE_ANON_KEY,
        "Content-Type": "application/json",
    }
    if access_token:
        h["Authorization"] = f"Bearer {access_token}"
    return h


def _post(endpoint, body=None, access_token=None, params=None):
    """POST to a GoTrue endpoint. Returns parsed JSON or raises."""
    url = f"{_AUTH_BASE}/{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode("utf-8") if body else None
    hdrs = _auth_headers(access_token)
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        log.warning(f"Auth POST {endpoint} → {e.code}: {error_body}")
        try:
            err = json.loads(error_body)
            msg = err.get("error_description") or err.get("msg") or err.get("message") or error_body
        except Exception:
            msg = error_body
        raise AuthError(msg, e.code) from e


def _get(endpoint, access_token=None):
    """GET from a GoTrue endpoint. Returns parsed JSON or raises."""
    url = f"{_AUTH_BASE}/{endpoint}"
    hdrs = _auth_headers(access_token)
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        log.warning(f"Auth GET {endpoint} → {e.code}: {error_body}")
        try:
            err = json.loads(error_body)
            msg = err.get("error_description") or err.get("msg") or err.get("message") or error_body
        except Exception:
            msg = error_body
        raise AuthError(msg, e.code) from e


class AuthError(Exception):
    """Raised when an auth API call fails."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


# ── PKCE helpers ────────────────────────────────────────────────

def _generate_pkce():
    """Generate PKCE code_verifier and code_challenge (S256)."""
    verifier_bytes = os.urandom(32)
    code_verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode("ascii")
    challenge_hash = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(challenge_hash).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


# ── Auth Functions ──────────────────────────────────────────────

def sign_up(email, password, display_name, redirect_to=None):
    """Create a new account with email + password.

    If redirect_to is provided, the confirmation email link will redirect
    there (with tokens in the URL hash fragment).

    Returns the API response dict. If email confirmation is enabled, this
    will NOT contain an access_token yet — the user must click the link first.
    """
    body = {
        "email": email,
        "password": password,
        "data": {"display_name": display_name},
    }
    # GoTrue v2: emailRedirectTo tells Supabase where the confirmation link
    # should redirect the user. Without this, it uses the Site URL default.
    if redirect_to:
        body["emailRedirectTo"] = redirect_to
    return _post("signup", body=body)


def sign_in_email(email, password):
    """Sign in with email + password.
    Returns session dict with access_token, refresh_token, user, etc."""
    body = {"email": email, "password": password}
    return _post("token", body=body, params={"grant_type": "password"})


def send_magic_link(email, redirect_to=None):
    """Send a magic link email. The user clicks it to authenticate.
    If redirect_to is provided, the magic link will redirect there."""
    body = {"email": email}
    if redirect_to:
        body["data"] = {"redirect_to": redirect_to}
    # Use the OTP endpoint with magic link type
    body["create_user"] = True
    return _post("magiclink", body=body)


def sign_in_google(callback_port=None):
    """Initiate Google OAuth PKCE flow.

    1. Generate PKCE verifier/challenge
    2. Open browser to Supabase authorize URL
    3. Run a temporary localhost HTTP server to catch the callback
    4. Exchange the auth code for a session

    Returns session dict or raises AuthError.
    """
    code_verifier, code_challenge = _generate_pkce()

    # Find an available port
    server = None
    port = callback_port
    if port:
        ports_to_try = [port]
    else:
        ports_to_try = list(_CALLBACK_PORTS)

    for p in ports_to_try:
        try:
            server = _AuthHTTPServer(("127.0.0.1", p), _OAuthCallbackHandler)
            port = p
            break
        except OSError:
            continue

    if server is None:
        raise AuthError("Could not start local callback server (ports in use)")

    server.timeout = _CALLBACK_TIMEOUT
    redirect_uri = f"http://localhost:{port}/callback"

    # Build the authorize URL
    authorize_params = urllib.parse.urlencode({
        "provider": "google",
        "redirect_to": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    authorize_url = f"{_AUTH_BASE}/authorize?{authorize_params}"

    # Open browser
    log.info(f"Opening browser for Google sign-in (redirect to :{port})")
    webbrowser.open(authorize_url)

    # Wait for callback
    server._auth_code = None
    server._auth_error = None
    server._auth_tokens = None  # For fragment-based token delivery

    try:
        # Handle requests until we get the code or timeout
        deadline = threading.Event()

        def _serve():
            while not deadline.is_set():
                server.handle_request()
                if server._auth_code or server._auth_error or server._auth_tokens:
                    break

        serve_thread = threading.Thread(target=_serve, daemon=True)
        serve_thread.start()
        serve_thread.join(timeout=_CALLBACK_TIMEOUT)
        deadline.set()
    finally:
        try:
            server.server_close()
        except Exception:
            pass

    if server._auth_error:
        raise AuthError(f"OAuth error: {server._auth_error}")

    if server._auth_tokens:
        # Tokens came directly in the redirect (implicit flow fallback)
        return server._auth_tokens

    if not server._auth_code:
        raise AuthError("OAuth timed out — no callback received within 120 seconds")

    # Exchange code for session
    return exchange_code(server._auth_code, code_verifier)


def start_magic_link_listener(callback_port=None):
    """Start a localhost HTTP server to listen for the magic link callback.

    Returns (server, port) tuple. The caller should wait for the server to
    receive the callback, then check server._auth_tokens for the session.
    """
    server = None
    port = callback_port
    if port:
        ports_to_try = [port]
    else:
        ports_to_try = list(_CALLBACK_PORTS)

    for p in ports_to_try:
        try:
            server = _AuthHTTPServer(("127.0.0.1", p), _OAuthCallbackHandler)
            port = p
            break
        except OSError:
            continue

    if server is None:
        raise AuthError("Could not start local callback server (ports in use)")

    server.timeout = _CALLBACK_TIMEOUT
    server._auth_code = None
    server._auth_error = None
    server._auth_tokens = None

    return server, port


def wait_for_magic_link_callback(server, cancel_event=None):
    """Block until the magic link callback is received on the given server.

    If cancel_event (threading.Event) is provided, setting it will abort the wait
    early and raise AuthError("cancelled").

    Returns session dict or raises AuthError.
    """
    deadline = threading.Event()

    def _serve():
        while not deadline.is_set():
            server.handle_request()
            if server._auth_code or server._auth_error or server._auth_tokens:
                break

    serve_thread = threading.Thread(target=_serve, daemon=True)
    serve_thread.start()

    # Poll so we can detect cancellation
    elapsed = 0.0
    poll_interval = 0.5
    while elapsed < _CALLBACK_TIMEOUT:
        if cancel_event and cancel_event.is_set():
            deadline.set()
            break
        if not serve_thread.is_alive():
            break
        serve_thread.join(timeout=poll_interval)
        elapsed += poll_interval

    deadline.set()

    try:
        server.server_close()
    except Exception:
        pass

    if cancel_event and cancel_event.is_set():
        raise AuthError("Cancelled")

    if server._auth_error:
        raise AuthError(f"Magic link error: {server._auth_error}")

    if server._auth_tokens:
        return server._auth_tokens

    if server._auth_code:
        # Shouldn't happen for magic links, but handle gracefully
        return {"code": server._auth_code}

    raise AuthError("Magic link timed out — no callback received within 120 seconds")


def exchange_code(code, code_verifier):
    """Exchange an OAuth authorization code for a session (PKCE)."""
    body = {
        "auth_code": code,
        "code_verifier": code_verifier,
    }
    return _post("token", body=body, params={"grant_type": "pkce"})


def refresh_session(refresh_token):
    """Refresh an expired session using a refresh token.
    Returns new session dict with updated tokens."""
    body = {"refresh_token": refresh_token}
    return _post("token", body=body, params={"grant_type": "refresh_token"})


def get_user(access_token):
    """Get the authenticated user's profile from GoTrue."""
    return _get("user", access_token=access_token)


def sign_out(access_token):
    """Sign out — invalidates the user's session on the server."""
    try:
        _post("logout", access_token=access_token)
    except AuthError:
        pass  # Best effort — user is signed out locally regardless


# ── OAuth Callback HTTP Handler ─────────────────────────────────

# HTML page that extracts tokens from the URL hash fragment and sends them
# to our server via a POST, since the hash fragment is not sent to the server.
_CALLBACK_HTML = """<!DOCTYPE html>
<html><head><title>Vox — Sign In</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; background: #1e1e1e;
         color: #e8e8e8; display: flex; align-items: center; justify-content: center;
         height: 100vh; margin: 0; }
  .card { text-align: center; padding: 40px; }
  h2 { color: #2ABFBF; margin-bottom: 8px; }
  p { color: #999; }
</style></head>
<body><div class="card">
  <h2>Signed in!</h2>
  <p>You can close this tab and return to Vox.</p>
</div>
<script>
// Check for tokens in the hash fragment (magic link / implicit flow)
(function() {
  var hash = window.location.hash.substring(1);
  var params = new URLSearchParams(hash);
  var access_token = params.get('access_token');
  var refresh_token = params.get('refresh_token');
  var code = new URLSearchParams(window.location.search).get('code');

  if (access_token) {
    // Send tokens back to the local server via POST
    fetch('/token_callback', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        access_token: access_token,
        refresh_token: refresh_token,
        expires_in: params.get('expires_in'),
        token_type: params.get('token_type')
      })
    });
  } else if (code) {
    // PKCE flow — code is in query params, already handled by GET
  }
})();
</script></body></html>"""

_ERROR_HTML = """<!DOCTYPE html>
<html><head><title>Vox — Error</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; background: #1e1e1e;
         color: #e8e8e8; display: flex; align-items: center; justify-content: center;
         height: 100vh; margin: 0; }
  .card { text-align: center; padding: 40px; }
  h2 { color: #e53935; margin-bottom: 8px; }
  p { color: #999; }
</style></head>
<body><div class="card">
  <h2>Sign-in failed</h2>
  <p>%s</p>
  <p>You can close this tab and try again.</p>
</div></body></html>"""


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handles the OAuth/magic link callback on localhost."""

    server: _AuthHTTPServer  # type: ignore[assignment]

    def log_message(self, format, *args):
        # Suppress default HTTP server logs
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        # Check for error
        error = query.get("error", [None])[0]
        if error:
            error_desc = query.get("error_description", [error])[0]
            self.server._auth_error = error_desc
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write((_ERROR_HTML % error_desc).encode("utf-8"))
            return

        # Check for authorization code (PKCE flow)
        code = query.get("code", [None])[0]
        if code:
            self.server._auth_code = code

        # Always serve the callback HTML (it handles hash fragment tokens)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(_CALLBACK_HTML.encode("utf-8"))

    def do_POST(self):
        if self.path == "/token_callback":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                tokens = json.loads(body)
                if tokens.get("access_token"):
                    self.server._auth_tokens = tokens
            except Exception as e:
                log.warning(f"Failed to parse token callback: {e}")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
        else:
            self.send_response(404)
            self.end_headers()
