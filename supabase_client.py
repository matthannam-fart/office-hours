"""
supabase_client.py — Lightweight Supabase REST client for Vox teams.

Uses only urllib (no pip dependencies). Wraps PostgREST API for:
  - User profiles (upsert on launch)
  - Teams (CRUD, invite-code based joining)
  - Team membership
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from config import SUPABASE_ANON_KEY, SUPABASE_URL, log


def _get_auth_token():
    """Get the current auth access token, refreshing if expired.
    Returns the access token string, or None to fall back to anon key."""
    try:
        from user_settings import get_auth_session, save_auth_session
        session = get_auth_session()
        if not session or not session.get("access_token"):
            return None

        # Check if token is expired (with 60s buffer)
        expires_at = session.get("expires_at", 0)
        if expires_at and time.time() > (expires_at - 60):
            # Token expired or about to expire — try to refresh
            refresh_token = session.get("refresh_token")
            if refresh_token:
                try:
                    import auth_manager
                    new_session = auth_manager.refresh_session(refresh_token)
                    if new_session and new_session.get("access_token"):
                        # Save the refreshed session
                        user = new_session.get("user", {})
                        save_auth_session({
                            "access_token": new_session["access_token"],
                            "refresh_token": new_session.get("refresh_token", refresh_token),
                            "expires_at": int(time.time()) + new_session.get("expires_in", 3600),
                            "user_id": user.get("id", session.get("user_id", "")),
                            "email": user.get("email", session.get("email", "")),
                        })
                        return new_session["access_token"]
                except Exception as e:
                    log.warning(f"Token refresh failed: {e}")
            return None  # Fall back to anon key if refresh fails

        return session["access_token"]
    except Exception:
        return None


def _headers(extra=None):
    """Standard Supabase REST headers. Uses auth token if available."""
    token = _get_auth_token()
    bearer = token if token else SUPABASE_ANON_KEY

    h = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {bearer}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    if extra:
        h.update(extra)
    return h


def _request(method, path, body=None, headers_extra=None, params=None):
    """Make an HTTP request to the Supabase PostgREST API.
    Retries once on transient errors (5xx, timeout)."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)

    data = json.dumps(body).encode("utf-8") if body else None
    hdrs = _headers(headers_extra)

    last_err = None
    for attempt in range(2):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else []
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            if e.code >= 500 and attempt == 0:
                log.warning(f"Supabase {method} {path} → {e.code} (retrying): {error_body}")
                time.sleep(0.5)
                last_err = e
                continue
            log.warning(f"Supabase {method} {path} → {e.code}: {error_body}")
            return None
        except (TimeoutError, urllib.error.URLError, OSError) as e:
            if attempt == 0:
                log.warning(f"Supabase {method} {path} transient error (retrying): {e}")
                time.sleep(0.5)
                last_err = e
                continue
            log.warning(f"Supabase {method} {path} failed after retry: {e}")
            return None
        except Exception as e:
            log.warning(f"Supabase request failed: {e}")
            return None

    log.warning(f"Supabase {method} {path} failed after retry: {last_err}")
    return None


# ── Profiles ─────────────────────────────────────────────────────

def ensure_profile(user_id: str, display_name: str):
    """Upsert the user's profile on app launch. Updates last_seen timestamp."""
    now = datetime.now(UTC).isoformat()
    result = _request(
        "POST", "profiles",
        body={"id": user_id, "display_name": display_name, "last_seen": now},
        headers_extra={
            "Prefer": "return=representation,resolution=merge-duplicates",
        },
    )
    if result:
        log.info(f"Profile synced for {display_name}")
    return result


def lookup_users(name_query: str):
    """Search profiles by display_name (case-insensitive partial match)."""
    return _request(
        "GET", "profiles",
        params={"display_name": f"ilike.*{name_query}*", "select": "id,display_name"},
    ) or []


# ── Teams ────────────────────────────────────────────────────────

def get_my_teams(user_id: str):
    """Fetch all teams the user belongs to, with their role."""
    # Uses a PostgREST embedded join: team_members → teams
    result = _request(
        "GET", "team_members",
        params={
            "user_id": f"eq.{user_id}",
            "select": "role,teams(id,name,invite_code)",
        },
    )
    if not result:
        return []
    teams = []
    for row in result:
        team_info = row.get("teams")
        if team_info:
            teams.append({
                "id": team_info["id"],
                "name": team_info["name"],
                "invite_code": team_info.get("invite_code", ""),
                "role": row.get("role", "member"),
            })
    return teams


def get_team_members(team_id: str):
    """Get all members of a team with their profiles."""
    result = _request(
        "GET", "team_members",
        params={
            "team_id": f"eq.{team_id}",
            "select": "role,user_id,profiles(id,display_name)",
        },
    )
    if not result:
        return []
    members = []
    for row in result:
        profile = row.get("profiles")
        if profile:
            members.append({
                "user_id": profile["id"],
                "display_name": profile["display_name"],
                "role": row.get("role", "member"),
            })
    return members


def create_team(name: str, creator_id: str):
    """Create a new team. The creator becomes the admin.
    invite_code is auto-generated by the database default."""
    # 1. Insert the team (invite_code auto-generated by DB)
    team = _request(
        "POST", "teams",
        body={"name": name, "created_by": creator_id},
    )
    if not team or not isinstance(team, list) or len(team) == 0:
        return None
    team_record = team[0]
    team_id = team_record["id"]

    # 2. Add the creator as admin member
    _request(
        "POST", "team_members",
        body={"team_id": team_id, "user_id": creator_id, "role": "admin"},
    )
    log.info(f"Created team '{name}' (id={team_id}, code={team_record.get('invite_code')})")
    return team_record


def join_team_by_code(invite_code: str, user_id: str):
    """Join a team using its invite code. Returns the team record or None."""
    # Look up team by invite code
    result = _request(
        "GET", "teams",
        params={
            "invite_code": f"eq.{invite_code.upper().strip()}",
            "select": "id,name,invite_code",
        },
    )
    if not result or not isinstance(result, list) or len(result) == 0:
        log.warning(f"No team found for invite code: {invite_code}")
        return None
    team = result[0]
    team_id = team["id"]

    # Add user as member (upsert to avoid duplicates)
    _request(
        "POST", "team_members",
        body={"team_id": team_id, "user_id": user_id, "role": "member"},
        headers_extra={
            "Prefer": "return=representation,resolution=merge-duplicates",
        },
    )
    log.info(f"Joined team '{team['name']}' via invite code {invite_code}")
    return team


def get_team_invite_code(team_id: str):
    """Get the invite code for a team."""
    result = _request(
        "GET", "teams",
        params={
            "id": f"eq.{team_id}",
            "select": "invite_code",
        },
    )
    if result and isinstance(result, list) and len(result) > 0:
        return result[0].get("invite_code", "")
    return ""


def add_member(team_id: str, user_id: str):
    """Add a user to a team as a regular member."""
    result = _request(
        "POST", "team_members",
        body={"team_id": team_id, "user_id": user_id, "role": "member"},
        headers_extra={
            "Prefer": "return=representation,resolution=merge-duplicates",
        },
    )
    if result:
        log.info(f"Added {user_id} to team {team_id}")
    return result


def remove_member(team_id: str, user_id: str):
    """Remove a user from a team."""
    result = _request(
        "DELETE", "team_members",
        params={
            "team_id": f"eq.{team_id}",
            "user_id": f"eq.{user_id}",
        },
    )
    log.info(f"Removed {user_id} from team {team_id}")
    return result


def leave_team(team_id: str, user_id: str):
    """User leaves a team (same as remove but self-initiated)."""
    return remove_member(team_id, user_id)


# ── Join Requests (Lobby) ──────────────────────────────────────

def get_all_teams():
    """Fetch all teams for lobby listing."""
    return _request("GET", "teams", params={"select": "id,name,created_by"}) or []


def submit_join_request(team_id: str, user_id: str):
    """Submit a join request. Deletes any prior request first to avoid duplicates."""
    # Clear any stale/declined request for this team+user combo
    _request(
        "DELETE", "join_requests",
        params={
            "team_id": f"eq.{team_id}",
            "requester_id": f"eq.{user_id}",
        },
    )
    return _request(
        "POST", "join_requests",
        body={"team_id": team_id, "requester_id": user_id, "status": "pending"},
    )


def approve_join_request(request_id: str, team_id: str, requester_id: str, admin_id: str):
    """Approve request: update status + add to team_members."""
    _request(
        "PATCH", "join_requests",
        params={"id": f"eq.{request_id}"},
        body={"status": "approved", "responded_by": admin_id},
    )
    return add_member(team_id, requester_id)


def decline_join_request(request_id: str, admin_id: str):
    """Decline a join request."""
    return _request(
        "PATCH", "join_requests",
        params={"id": f"eq.{request_id}"},
        body={"status": "declined", "responded_by": admin_id},
    )


def get_join_request(request_id: str):
    """Look up a join request by ID. Used as fallback when relay doesn't send requester_id."""
    result = _request(
        "GET", "join_requests",
        params={
            "id": f"eq.{request_id}",
            "select": "id,team_id,requester_id,status",
        },
    )
    if result and isinstance(result, list) and len(result) > 0:
        return result[0]
    return None


def send_invite_email(to_email: str, team_name: str, invite_code: str, sender_name: str):
    """Send an invite email via the Supabase Edge Function + Resend."""
    url = f"{SUPABASE_URL}/functions/v1/send-invite"
    data = json.dumps({
        "to": to_email,
        "team_name": team_name,
        "invite_code": invite_code,
        "sender_name": sender_name,
    }).encode("utf-8")
    hdrs = {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
    }
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            log.info(f"Invite email sent to {to_email}: {result}")
            return result
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        log.warning(f"Invite email failed for {to_email}: {e.code} {error_body}")
        return None
    except Exception as e:
        log.warning(f"Invite email request failed: {e}")
        return None


def delete_team(team_id: str):
    """Delete a team and all memberships."""
    # Delete memberships first (cascade may handle this, but be explicit)
    _request(
        "DELETE", "team_members",
        params={"team_id": f"eq.{team_id}"},
    )
    _request(
        "DELETE", "teams",
        params={"id": f"eq.{team_id}"},
    )
    log.info(f"Deleted team {team_id}")
