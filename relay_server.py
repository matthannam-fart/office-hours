#!/usr/bin/env python3
"""
Vox Relay Server

Two channels:
1. Presence — clients register with a name, server broadcasts who's online + modes
2. Room relay — pairs two clients and forwards audio/control between them

Usage:
    python relay_server.py [--port 50002]
    python relay_server.py --port 50002 --cert server_cert.pem --key server_key.pem
"""

import argparse
import collections
import json
import os
import random
import socket
import ssl
import string
import struct
import threading
import time

# ── Auth ────────────────────────────────────────────────────────

RELAY_AUTH_KEY = None  # Set in main() from --auth-key / env var

def check_auth(msg):
    """Verify the auth_key field in a client's first message.
    Returns True if auth is disabled (no key set) or key matches."""
    if not RELAY_AUTH_KEY:
        return True
    return msg.get("auth_key") == RELAY_AUTH_KEY

# ── Rate Limiting ───────────────────────────────────────────────

# Track JOIN_ROOM attempts per IP: {ip: deque of timestamps}
join_attempts = {}
join_attempts_lock = threading.Lock()
RATE_LIMIT_WINDOW = 30   # seconds
RATE_LIMIT_MAX = 5        # max attempts per window

def check_rate_limit(ip):
    """Returns True if the IP is within rate limits, False if blocked."""
    now = time.time()
    with join_attempts_lock:
        if ip not in join_attempts:
            join_attempts[ip] = collections.deque()
        q = join_attempts[ip]
        # Purge old entries
        while q and q[0] < now - RATE_LIMIT_WINDOW:
            q.popleft()
        if len(q) >= RATE_LIMIT_MAX:
            return False
        q.append(now)
        return True

def cleanup_rate_limits():
    """Periodically clean up stale rate limit entries."""
    while True:
        time.sleep(60)
        now = time.time()
        with join_attempts_lock:
            stale = [ip for ip, q in join_attempts.items()
                     if not q or q[-1] < now - RATE_LIMIT_WINDOW * 2]
            for ip in stale:
                del join_attempts[ip]

# ── Presence Registry ────────────────────────────────────────────

presence = {}          # user_id -> {"name": str, "mode": str, "team_id": str, "sock": socket, "addr": tuple}
presence_lock = threading.Lock()

def broadcast_presence():
    """Send each client only the users that share a team with them.
    Clients send their team_id on REGISTER/MODE_UPDATE — we use it to
    filter so no one sees users outside their team."""
    dead_uids = []
    with presence_lock:
        # Build per-team user lists
        team_users = {}  # team_id -> [user_info, ...]
        for uid, info in presence.items():
            tid = info.get("team_id", "")
            entry = {
                "user_id": uid,
                "name": info["name"],
                "mode": info["mode"],
                "room": info.get("room", ""),
                "team_id": tid,
            }
            if tid:
                team_users.setdefault(tid, []).append(entry)

        # Send each client only their team's users
        for uid, info in list(presence.items()):
            tid = info.get("team_id", "")
            users_for_client = team_users.get(tid, []) if tid else []
            msg = json.dumps({"type": "PRESENCE_UPDATE", "users": users_for_client}).encode('utf-8')
            try:
                send_frame(info["sock"], msg)
            except Exception:
                dead_uids.append(uid)

    # Clean up dead clients outside the broadcast loop
    if dead_uids:
        with presence_lock:
            for uid in dead_uids:
                if uid in presence:
                    print(f"[Presence] Removing stale user: {uid}")
                    try:
                        presence[uid]["sock"].close()
                    except Exception:
                        pass
                    del presence[uid]
        # Re-broadcast the cleaned list
        broadcast_presence()

def presence_sweep():
    """Periodically remove clients that haven't sent a PING recently."""
    while True:
        time.sleep(30)
        now = time.time()
        with presence_lock:
            dead = []
            for uid, info in presence.items():
                last_ping = info.get("last_ping", info.get("registered_at", now))
                # No ping in 90 seconds = dead (clients ping every 30s, allow 3 missed)
                if now - last_ping > 90:
                    dead.append(uid)
                else:
                    try:
                        info["sock"].getpeername()
                    except Exception:
                        dead.append(uid)
            for uid in dead:
                print(f"[Sweep] Removing dead client: {uid} ({presence[uid].get('name', '?')})")
                try:
                    presence[uid]["sock"].close()
                except Exception:
                    pass
                del presence[uid]
        if dead:
            broadcast_presence()

def handle_presence_client(client_sock, client_addr, user_id=None):
    """Handle a presence connection: register, then listen for updates"""
    print(f"[Presence] New connection from {client_addr}")

    try:
        # Set socket timeout so dead connections are detected
        client_sock.settimeout(90)  # 90s timeout (clients ping every 30s)
        while True:
            frame = recv_frame(client_sock)
            if frame is None:
                break

            try:
                msg = json.loads(frame.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            action = msg.get("action") or msg.get("type")

            if action == "REGISTER":
                user_id = msg.get("user_id", "unknown")
                name = msg.get("name", "Unknown")
                mode = msg.get("mode", "GREEN")
                team_id = msg.get("team_id", "")

                with presence_lock:
                    presence[user_id] = {
                        "name": name,
                        "mode": mode,
                        "team_id": team_id,
                        "sock": client_sock,
                        "addr": client_addr,
                        "registered_at": time.time(),
                        "last_ping": time.time(),
                    }

                print(f"[Presence] Registered: {name} ({user_id})")
                send_json(client_sock, {"status": "registered", "user_id": user_id})
                broadcast_presence()

            elif action == "PING":
                with presence_lock:
                    if user_id and user_id in presence:
                        presence[user_id]["last_ping"] = time.time()
                # Send PONG back so client knows connection is alive
                send_json(client_sock, {"type": "PONG"})

            elif action == "MODE_UPDATE":
                mode = msg.get("mode", "GREEN")
                room_code = msg.get("room", "")
                team_id = msg.get("team_id")  # None means no change
                with presence_lock:
                    if user_id and user_id in presence:
                        presence[user_id]["mode"] = mode
                        presence[user_id]["room"] = room_code
                        if team_id is not None:
                            presence[user_id]["team_id"] = team_id
                print(f"[Presence] {user_id} mode -> {mode}" + (f" room={room_code}" if room_code else "") + (f" team={team_id}" if team_id else ""))
                broadcast_presence()

            elif action == "CONNECT_TO":
                target_id = msg.get("target_id")
                requester_name = msg.get("name", "Someone")

                with presence_lock:
                    target = presence.get(target_id)
                    # Verify both users are on the same team
                    caller_team = presence.get(user_id, {}).get("team_id", "")
                    target_team = target.get("team_id", "") if target else ""

                if target and caller_team and caller_team == target_team:
                    # Pre-create the room so both clients can JOIN_ROOM
                    room_code = generate_room_code()
                    with rooms_lock:
                        rooms[room_code] = {
                            "clients": [],
                            "udp_addrs": [],
                            "created": time.time()
                        }
                    print(f"[Presence] {user_id} wants to connect to {target_id}, room: {room_code}")

                    # Tell the requester to join this room
                    send_json(client_sock, {
                        "type": "CONNECT_ROOM",
                        "room": room_code,
                        "role": "creator"
                    })

                    # Tell the target they have an incoming request
                    try:
                        send_json(target["sock"], {
                            "type": "CONNECTION_REQUEST",
                            "room": room_code,
                            "from_name": requester_name,
                            "from_id": user_id
                        })
                    except Exception:
                        send_json(client_sock, {"type": "ERROR", "message": "Target user disconnected"})
                else:
                    send_json(client_sock, {"type": "ERROR", "message": "User not found or offline"})

            elif action == "ACCEPT_CONNECTION":
                room_code = msg.get("room")
                if room_code:
                    send_json(client_sock, {
                        "type": "CONNECT_ROOM",
                        "room": room_code,
                        "role": "joiner"
                    })

            elif action == "ACCEPT_CONNECTION_BY_ID":
                # Fallback: callee lost the room code but has the caller's ID
                from_id = msg.get("from_id")
                if from_id:
                    # Create a new room and tell both sides to join
                    room_code = generate_room_code()
                    with rooms_lock:
                        rooms[room_code] = {
                            "clients": [],
                            "udp_addrs": [],
                            "created": time.time()
                        }
                    print(f"[Presence] ACCEPT_BY_ID: created room {room_code} for {user_id} <-> {from_id}")
                    # Tell the acceptor to join
                    send_json(client_sock, {
                        "type": "CONNECT_ROOM",
                        "room": room_code,
                        "role": "joiner"
                    })
                    # Tell the caller to join
                    with presence_lock:
                        caller = presence.get(from_id)
                    if caller:
                        try:
                            send_json(caller["sock"], {
                                "type": "CONNECT_ROOM",
                                "room": room_code,
                                "role": "creator"
                            })
                        except Exception:
                            print(f"[Presence] Could not notify caller {from_id}")

            elif action == "REJECT_CONNECTION":
                target_id = msg.get("from_id")
                if target_id:
                    with presence_lock:
                        target = presence.get(target_id)
                    if target:
                        try:
                            send_json(target["sock"], {
                                "type": "CONNECTION_REJECTED",
                                "message": "Connection declined"
                            })
                        except (OSError, ConnectionError) as e:
                            print(f"[Presence] Could not send rejection to {target_id}: {e}")

            elif action == "CANCEL_CONNECTION":
                # Caller cancelled — notify the target
                target_id = msg.get("target_id")
                if target_id:
                    with presence_lock:
                        target = presence.get(target_id)
                    if target:
                        try:
                            send_json(target["sock"], {
                                "type": "CONNECTION_CANCELLED",
                                "message": "Call was cancelled"
                            })
                            print(f"[Presence] {user_id} cancelled call to {target_id}")
                        except (OSError, ConnectionError) as e:
                            print(f"[Presence] Could not send cancellation to {target_id}: {e}")

            elif action == "JOIN_REQUEST":
                # Lobby: requester wants to join a team — route to admin
                admin_id = msg.get("admin_id")
                requester_name = msg.get("requester_name", "Someone")
                request_id = msg.get("request_id", "")
                team_id = msg.get("team_id", "")

                with presence_lock:
                    admin = presence.get(admin_id)

                if admin:
                    try:
                        send_json(admin["sock"], {
                            "type": "JOIN_REQUEST",
                            "request_id": request_id,
                            "team_id": team_id,
                            "requester_id": user_id,
                            "requester_name": requester_name,
                        })
                        print(f"[Join] Routed request {request_id} from {user_id} to admin {admin_id}")
                    except Exception as e:
                        print(f"[Join] Could not notify admin: {e}")
                        send_json(client_sock, {
                            "type": "JOIN_REQUEST_FAILED",
                            "reason": "Could not reach team admin",
                        })
                else:
                    send_json(client_sock, {
                        "type": "JOIN_REQUEST_FAILED",
                        "reason": "Team admin is not online",
                    })

            elif action == "JOIN_RESPONSE":
                # Admin responds to a join request — route back to requester
                request_id = msg.get("request_id", "")
                approved = msg.get("approved", False)
                requester_id = msg.get("requester_id", "")

                with presence_lock:
                    requester = presence.get(requester_id)

                if requester:
                    try:
                        send_json(requester["sock"], {
                            "type": "JOIN_RESPONSE",
                            "request_id": request_id,
                            "approved": approved,
                        })
                        print(f"[Join] Sent {'approved' if approved else 'declined'} for {request_id} to {requester_id}")
                    except Exception:
                        print(f"[Join] Could not notify requester {requester_id}")
                else:
                    print(f"[Join] Requester {requester_id} not online for response")

            elif action == "PAGE_ALL":
                # Broadcast a one-way voice message to all GREEN team members
                team_id = msg.get("team_id", "")
                sender_name = msg.get("sender_name", "Someone")
                audio_b64 = msg.get("audio_b64", "")

                if not team_id or not audio_b64:
                    print(f"[PageAll] Ignored — missing team_id or audio data from {user_id}")
                    continue

                recipients = []
                with presence_lock:
                    for uid, info in presence.items():
                        if (uid != user_id
                                and info.get("team_id") == team_id
                                and info.get("mode") == "GREEN"):
                            recipients.append((uid, info["sock"]))

                page_msg = {
                    "type": "PAGE_ALL",
                    "from_id": user_id,
                    "from_name": sender_name,
                    "audio_b64": audio_b64,
                }
                sent = 0
                for uid, sock in recipients:
                    try:
                        send_json(sock, page_msg)
                        sent += 1
                    except Exception:
                        print(f"[PageAll] Failed to deliver to {uid}")
                print(f"[PageAll] {sender_name} paged {sent}/{len(recipients)} GREEN members in team {team_id[:8]}")

    except (ConnectionResetError, BrokenPipeError, TimeoutError) as e:
        print(f"[Presence] Client {client_addr} disconnected: {e}")
    except (OSError, ssl.SSLError) as e:
        print(f"[Presence] Socket error with {client_addr}: {e}")
    except Exception as e:
        print(f"[Presence] Unexpected error with {client_addr}: {e}")
    finally:
        if user_id:
            with presence_lock:
                if user_id in presence:
                    del presence[user_id]
            print(f"[Presence] {user_id} disconnected")
            broadcast_presence()
        client_sock.close()

# ── Room Management ──────────────────────────────────────────────

rooms = {}          # room_code -> {"clients": [conn1, conn2], "udp_addrs": [addr1, addr2], "created": timestamp}
rooms_lock = threading.Lock()

def generate_room_code():
    """Generate a human-friendly room code like VOX-7X3KA2 (6 chars for brute-force resistance)"""
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"VOX-{suffix}"

def cleanup_stale_rooms(max_age=3600):
    """Remove rooms older than max_age seconds"""
    now = time.time()
    with rooms_lock:
        stale = [code for code, room in rooms.items()
                 if now - room["created"] > max_age and len(room["clients"]) < 2]
        for code in stale:
            print(f"[Cleanup] Removing stale room: {code}")
            for client in rooms[code]["clients"]:
                try:
                    client.close()
                except OSError:
                    pass
            del rooms[code]

# ── TCP Framing ──────────────────────────────────────────────────

def recv_all(sock, n):
    """Receive exactly n bytes"""
    data = b''
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data += packet
    return data

def recv_frame(sock):
    """Receive a length-prefixed frame"""
    raw_len = recv_all(sock, 4)
    if not raw_len:
        return None
    msg_len = struct.unpack('!I', raw_len)[0]
    if msg_len > 10 * 1024 * 1024:  # 10MB sanity limit
        return None
    payload = recv_all(sock, msg_len)
    return payload

def send_frame(sock, data):
    """Send a length-prefixed frame"""
    msg_len = struct.pack('!I', len(data))
    sock.sendall(msg_len + data)

def send_json(sock, obj):
    """Send a JSON object as a length-prefixed frame"""
    send_frame(sock, json.dumps(obj).encode('utf-8'))

# ── Room Relay (existing) ────────────────────────────────────────

def handle_room_client(client_sock, client_addr, udp_sock):
    """Handle a room client: handshake, then relay"""
    print(f"[Room] New connection from {client_addr}")
    room_code = None
    peer_sock = None
    my_index = None

    try:
        # Step 1: Handshake
        handshake_data = recv_frame(client_sock)
        if not handshake_data:
            client_sock.close()
            return

        try:
            msg = json.loads(handshake_data.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            send_json(client_sock, {"status": "error", "message": "Invalid handshake"})
            client_sock.close()
            return

        action = msg.get("action")

        if action == "CREATE_ROOM":
            room_code = generate_room_code()
            with rooms_lock:
                rooms[room_code] = {
                    "clients": [client_sock],
                    "udp_addrs": [None, None],
                    "created": time.time()
                }
            my_index = 0
            send_json(client_sock, {"status": "created", "room": room_code})
            print(f"[Room] Created: {room_code}")

            # Wait for peer
            timeout = 300
            start = time.time()
            while time.time() - start < timeout:
                with rooms_lock:
                    if room_code in rooms and len(rooms[room_code]["clients"]) == 2:
                        peer_sock = rooms[room_code]["clients"][1]
                        break
                time.sleep(0.5)

            if not peer_sock:
                send_json(client_sock, {"status": "timeout"})
                with rooms_lock:
                    if room_code in rooms:
                        del rooms[room_code]
                client_sock.close()
                return

            send_json(client_sock, {"status": "paired", "room": room_code})

        elif action == "JOIN_ROOM":
            # Rate limit JOIN_ROOM attempts
            client_ip = client_addr[0]
            if not check_rate_limit(client_ip):
                send_json(client_sock, {"status": "error", "message": "Too many attempts. Try again later."})
                client_sock.close()
                return

            room_code = msg.get("room", "").upper()
            with rooms_lock:
                if room_code not in rooms:
                    send_json(client_sock, {"status": "error", "message": "Room not found"})
                    client_sock.close()
                    return
                if len(rooms[room_code]["clients"]) >= 2:
                    send_json(client_sock, {"status": "error", "message": "Room is full"})
                    client_sock.close()
                    return
                rooms[room_code]["clients"].append(client_sock)
                rooms[room_code]["udp_addrs"].append(None)
                my_index = len(rooms[room_code]["clients"]) - 1
                num_clients = len(rooms[room_code]["clients"])

            if num_clients >= 2:
                # Room already has other clients — paired immediately
                send_json(client_sock, {"status": "paired", "room": room_code})
                print(f"[Room] {room_code}: Client #{num_clients} joined (conference)")
            else:
                # First client — wait for second
                send_json(client_sock, {"status": "waiting", "room": room_code})
                print(f"[Room] {room_code}: First client waiting...")
                timeout = 120
                start = time.time()
                while time.time() - start < timeout:
                    with rooms_lock:
                        if room_code in rooms and len(rooms[room_code]["clients"]) >= 2:
                            break
                    time.sleep(0.5)
                else:
                    send_json(client_sock, {"status": "timeout"})
                    with rooms_lock:
                        if room_code in rooms:
                            del rooms[room_code]
                    client_sock.close()
                    return

                send_json(client_sock, {"status": "paired", "room": room_code})
                print(f"[Room] {room_code}: Paired!")

        else:
            send_json(client_sock, {"status": "error", "message": f"Unknown action: {action}"})
            client_sock.close()
            return

        # Step 2: Relay TCP — broadcast to ALL other clients in room
        while True:
            frame = recv_frame(client_sock)
            if frame is None:
                break

            # Check for UDP registration
            try:
                check = json.loads(frame.decode('utf-8'))
                if check.get("type") == "UDP_REGISTER":
                    udp_port = check.get("udp_port")
                    with rooms_lock:
                        if room_code in rooms and my_index < len(rooms[room_code]["udp_addrs"]):
                            rooms[room_code]["udp_addrs"][my_index] = (client_addr[0], udp_port)
                    print(f"[UDP] Registered {client_addr[0]}:{udp_port} for {room_code}")
                    continue
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

            # Broadcast frame to all other clients in room
            with rooms_lock:
                if room_code in rooms:
                    peers = [(i, c) for i, c in enumerate(rooms[room_code]["clients"]) if i != my_index]
                else:
                    peers = []
            for _, peer_sock in peers:
                try:
                    send_frame(peer_sock, frame)
                except Exception:
                    pass

    except (ConnectionResetError, BrokenPipeError, TimeoutError) as e:
        print(f"[Room] Client {client_addr} disconnected: {e}")
    except (OSError, ssl.SSLError) as e:
        print(f"[Room] Socket error with {client_addr}: {e}")
    except Exception as e:
        print(f"[Room] Unexpected error with {client_addr}: {e}")
    finally:
        client_sock.close()
        if room_code:
            with rooms_lock:
                if room_code in rooms:
                    # Remove only this client, not entire room
                    try:
                        idx = rooms[room_code]["clients"].index(client_sock)
                        rooms[room_code]["clients"].pop(idx)
                        rooms[room_code]["udp_addrs"].pop(idx)
                    except (ValueError, IndexError):
                        pass
                    # Delete room if empty
                    if len(rooms[room_code]["clients"]) == 0:
                        del rooms[room_code]
                        print(f"[Room] {room_code}: Empty, removed")
                    else:
                        print(f"[Room] {room_code}: Client left, {len(rooms[room_code]['clients'])} remaining")

# ── UDP Relay ────────────────────────────────────────────────────

def udp_relay_loop(udp_sock):
    """Receive UDP packets and forward to ALL other peers in the room"""
    while True:
        try:
            data, addr = udp_sock.recvfrom(8192)
            with rooms_lock:
                for code, room in rooms.items():
                    udp_addrs = room["udp_addrs"]
                    # Find which index sent this packet
                    sender_idx = None
                    for i, a in enumerate(udp_addrs):
                        if a and a == addr:
                            sender_idx = i
                            break
                    if sender_idx is not None:
                        # Forward to ALL other members in room
                        for i, a in enumerate(udp_addrs):
                            if a and i != sender_idx:
                                udp_sock.sendto(data, a)
                        break
        except OSError as e:
            print(f"[UDP] Socket error: {e}")

# ── TCP Router ───────────────────────────────────────────────────

def handle_client(client_sock, client_addr, udp_sock):
    """Route client to presence or room handler based on first message"""
    try:
        # Peek at the first frame to determine the channel
        frame = recv_frame(client_sock)
        if not frame:
            client_sock.close()
            return

        try:
            msg = json.loads(frame.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            client_sock.close()
            return

        # Verify auth key on every new connection
        if not check_auth(msg):
            print(f"[Auth] Rejected connection from {client_addr} (bad or missing auth key)")
            send_json(client_sock, {"status": "error", "message": "Invalid auth key. Please update the app."})
            client_sock.close()
            return

        action = msg.get("action")

        if action == "REGISTER":
            # This is a presence connection — handle inline
            # Re-process the REGISTER message
            user_id = msg.get("user_id", "unknown")
            name = msg.get("name", "Unknown")
            mode = msg.get("mode", "GREEN")
            team_id = msg.get("team_id", "")

            with presence_lock:
                presence[user_id] = {
                    "name": name,
                    "mode": mode,
                    "team_id": team_id,
                    "sock": client_sock,
                    "addr": client_addr
                }

            print(f"[Presence] Registered: {name} ({user_id})")
            send_json(client_sock, {"status": "registered", "user_id": user_id})
            broadcast_presence()

            # Continue handling presence messages (pass user_id for cleanup)
            handle_presence_client(client_sock, client_addr, user_id)
            # Note: handle_presence_client will handle cleanup
            return

        elif action in ("CREATE_ROOM", "JOIN_ROOM"):
            # This is a room connection — re-inject the first frame
            # We need to handle it specially since we already consumed the handshake
            handle_room_client_with_handshake(client_sock, client_addr, udp_sock, msg)
            return

        else:
            send_json(client_sock, {"status": "error", "message": f"Unknown action: {action}"})
            client_sock.close()

    except (ConnectionResetError, BrokenPipeError, TimeoutError) as e:
        print(f"[Router] Client {client_addr} disconnected during routing: {e}")
        try:
            client_sock.close()
        except OSError:
            pass
    except (OSError, ssl.SSLError) as e:
        print(f"[Router] Socket error from {client_addr}: {e}")
        try:
            client_sock.close()
        except OSError:
            pass
    except Exception as e:
        print(f"[Router] Unexpected error from {client_addr}: {e}")
        try:
            client_sock.close()
        except OSError:
            pass

def handle_room_client_with_handshake(client_sock, client_addr, udp_sock, handshake_msg):
    """Handle a room client where we already parsed the handshake"""
    room_code = None
    peer_sock = None
    my_index = None

    try:
        action = handshake_msg.get("action")

        if action == "CREATE_ROOM":
            room_code = generate_room_code()
            with rooms_lock:
                rooms[room_code] = {
                    "clients": [client_sock],
                    "udp_addrs": [None, None],
                    "created": time.time()
                }
            my_index = 0
            send_json(client_sock, {"status": "created", "room": room_code})
            print(f"[Room] Created: {room_code}")

            timeout = 300
            start = time.time()
            while time.time() - start < timeout:
                with rooms_lock:
                    if room_code in rooms and len(rooms[room_code]["clients"]) == 2:
                        peer_sock = rooms[room_code]["clients"][1]
                        break
                time.sleep(0.5)

            if not peer_sock:
                send_json(client_sock, {"status": "timeout"})
                with rooms_lock:
                    if room_code in rooms:
                        del rooms[room_code]
                client_sock.close()
                return

            send_json(client_sock, {"status": "paired", "room": room_code})

        elif action == "JOIN_ROOM":
            # Rate limit JOIN_ROOM attempts
            client_ip = client_addr[0]
            if not check_rate_limit(client_ip):
                send_json(client_sock, {"status": "error", "message": "Too many attempts. Try again later."})
                client_sock.close()
                return

            room_code = handshake_msg.get("room", "").upper()
            with rooms_lock:
                if room_code not in rooms:
                    send_json(client_sock, {"status": "error", "message": "Room not found"})
                    client_sock.close()
                    return
                if len(rooms[room_code]["clients"]) >= 2:
                    send_json(client_sock, {"status": "error", "message": "Room is full"})
                    client_sock.close()
                    return
                rooms[room_code]["clients"].append(client_sock)
                rooms[room_code]["udp_addrs"].append(None)
                my_index = len(rooms[room_code]["clients"]) - 1

                if len(rooms[room_code]["clients"]) == 2:
                    peer_sock = rooms[room_code]["clients"][0]
                else:
                    peer_sock = None

            if peer_sock:
                send_json(client_sock, {"status": "paired", "room": room_code})
                print(f"[Room] {room_code}: Paired!")
            else:
                send_json(client_sock, {"status": "waiting", "room": room_code})
                print(f"[Room] {room_code}: First client waiting...")
                timeout = 120
                start = time.time()
                while time.time() - start < timeout:
                    with rooms_lock:
                        if room_code in rooms and len(rooms[room_code]["clients"]) == 2:
                            peer_sock = rooms[room_code]["clients"][1]
                            break
                    time.sleep(0.5)

                if not peer_sock:
                    send_json(client_sock, {"status": "timeout"})
                    with rooms_lock:
                        if room_code in rooms:
                            del rooms[room_code]
                    client_sock.close()
                    return

                send_json(client_sock, {"status": "paired", "room": room_code})
                print(f"[Room] {room_code}: Paired!")

        # Relay TCP — broadcast to all other clients in room
        while True:
            frame = recv_frame(client_sock)
            if frame is None:
                break
            try:
                check = json.loads(frame.decode('utf-8'))
                if check.get("type") == "UDP_REGISTER":
                    udp_port = check.get("udp_port")
                    with rooms_lock:
                        if room_code in rooms:
                            rooms[room_code]["udp_addrs"][my_index] = (client_addr[0], udp_port)
                    continue
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

            # Broadcast to all peers in room (not just peer_sock)
            with rooms_lock:
                if room_code in rooms:
                    peers = [(i, c) for i, c in enumerate(rooms[room_code]["clients"]) if i != my_index]
                else:
                    peers = []
            for _, p in peers:
                try:
                    send_frame(p, frame)
                except Exception:
                    pass

    except (ConnectionResetError, BrokenPipeError, TimeoutError) as e:
        print(f"[Room] Client {client_addr} disconnected: {e}")
    except (OSError, ssl.SSLError) as e:
        print(f"[Room] Socket error with {client_addr}: {e}")
    except Exception as e:
        print(f"[Room] Unexpected error with {client_addr}: {e}")
    finally:
        client_sock.close()
        if room_code:
            with rooms_lock:
                if room_code in rooms:
                    try:
                        idx = rooms[room_code]["clients"].index(client_sock)
                        rooms[room_code]["clients"].pop(idx)
                        rooms[room_code]["udp_addrs"].pop(idx)
                    except (ValueError, IndexError):
                        pass
                    if len(rooms[room_code]["clients"]) == 0:
                        del rooms[room_code]
                        print(f"[Room] {room_code}: Empty, removed")
                    else:
                        print(f"[Room] {room_code}: Client left, {len(rooms[room_code]['clients'])} remaining")

# ── TLS Setup ───────────────────────────────────────────────────

def create_tls_context(cert_file, key_file):
    """Create a server-side TLS context."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
    print(f"[TLS] Loaded certificate: {cert_file}")
    return ctx

# ── Main ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Vox Relay Server")
    parser.add_argument("--port", type=int, default=50002, help="Port for TCP and UDP (default: 50002)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--cert", type=str, default=None, help="TLS certificate file (PEM)")
    parser.add_argument("--key", type=str, default=None, help="TLS private key file (PEM)")
    parser.add_argument("--auth-key", type=str,
                        default=os.environ.get('VOX_RELAY_KEY', 'vox-relay-v1-2026'),
                        help="Auth key clients must provide (env: VOX_RELAY_KEY)")
    args = parser.parse_args()

    # Auth setup
    global RELAY_AUTH_KEY
    RELAY_AUTH_KEY = args.auth_key
    if RELAY_AUTH_KEY:
        print("[Server] Auth key required for connections")
    else:
        print("[Server] WARNING: No auth key — relay is open to anyone!")

    # TLS setup
    tls_context = None
    if args.cert and args.key:
        tls_context = create_tls_context(args.cert, args.key)
        print("[Server] TLS ENABLED — encrypted connections required")
    else:
        print("[Server] WARNING: TLS disabled — all connections are plaintext!")
        print("[Server] Use --cert and --key to enable TLS")

    tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_server.bind((args.host, args.port))
    tcp_server.listen(20)
    print(f"[Server] Vox Relay listening on {args.host}:{args.port}")
    print("[Server] Presence + Room relay active")

    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp_sock.bind((args.host, args.port))

    threading.Thread(target=udp_relay_loop, args=(udp_sock,), daemon=True).start()

    def cleanup_loop():
        while True:
            time.sleep(60)
            cleanup_stale_rooms()
    threading.Thread(target=cleanup_loop, daemon=True).start()
    threading.Thread(target=presence_sweep, daemon=True).start()
    threading.Thread(target=cleanup_rate_limits, daemon=True).start()

    print(f"[Server] Ready on port {args.port}")
    try:
        while True:
            client_sock, client_addr = tcp_server.accept()
            # Wrap with TLS if enabled
            if tls_context:
                try:
                    client_sock = tls_context.wrap_socket(client_sock, server_side=True)
                except ssl.SSLError as e:
                    print(f"[TLS] Handshake failed from {client_addr}: {e}")
                    client_sock.close()
                    continue
            threading.Thread(target=handle_client, args=(client_sock, client_addr, udp_sock), daemon=True).start()
    except KeyboardInterrupt:
        print("\n[Server] Shutting down...")
    finally:
        tcp_server.close()
        udp_sock.close()

if __name__ == "__main__":
    main()
