#!/usr/bin/env python3
"""
Office Hours Relay Server

Two channels:
1. Presence — clients register with a name, server broadcasts who's online + modes
2. Room relay — pairs two clients and forwards audio/control between them

Usage:
    python relay_server.py [--port 50002]
"""

import socket
import struct
import threading
import json
import time
import random
import string
import argparse

# ── Presence Registry ────────────────────────────────────────────

presence = {}          # user_id -> {"name": str, "mode": str, "sock": socket, "addr": tuple}
presence_lock = threading.Lock()

def broadcast_presence():
    """Send the current user list to all registered clients"""
    dead_uids = []
    with presence_lock:
        user_list = []
        for uid, info in presence.items():
            user_list.append({
                "user_id": uid,
                "name": info["name"],
                "mode": info["mode"]
            })
        
        msg = json.dumps({"type": "PRESENCE_UPDATE", "users": user_list}).encode('utf-8')
        
        for uid, info in list(presence.items()):
            try:
                send_frame(info["sock"], msg)
            except Exception:
                # Client disconnected — mark for cleanup
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

def handle_presence_client(client_sock, client_addr):
    """Handle a presence connection: register, then listen for updates"""
    user_id = None
    print(f"[Presence] New connection from {client_addr}")
    
    try:
        # Set socket timeout so dead connections are detected
        client_sock.settimeout(300)  # 5 minute timeout
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
                
                with presence_lock:
                    presence[user_id] = {
                        "name": name,
                        "mode": mode,
                        "sock": client_sock,
                        "addr": client_addr
                    }
                
                print(f"[Presence] Registered: {name} ({user_id})")
                send_json(client_sock, {"status": "registered", "user_id": user_id})
                broadcast_presence()
            
            elif action == "MODE_UPDATE":
                mode = msg.get("mode", "GREEN")
                with presence_lock:
                    if user_id and user_id in presence:
                        presence[user_id]["mode"] = mode
                print(f"[Presence] {user_id} mode -> {mode}")
                broadcast_presence()
            
            elif action == "CONNECT_TO":
                target_id = msg.get("target_id")
                requester_name = msg.get("name", "Someone")
                
                with presence_lock:
                    target = presence.get(target_id)
                
                if target:
                    # Pre-create the room so both clients can JOIN_ROOM
                    room_code = generate_room_code()
                    with rooms_lock:
                        rooms[room_code] = {
                            "clients": [],
                            "udp_addrs": [None, None],
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
                        except Exception:
                            pass
    
    except Exception as e:
        print(f"[Presence] Error with {client_addr}: {e}")
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
    """Generate a human-friendly room code like OH-7X3K"""
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"OH-{suffix}"

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
                except:
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
                my_index = len(rooms[room_code]["clients"]) - 1
                
                if len(rooms[room_code]["clients"]) == 2:
                    # Second client — we can pair immediately
                    peer_sock = rooms[room_code]["clients"][0]
                else:
                    peer_sock = None  # First client — need to wait

            if peer_sock:
                send_json(client_sock, {"status": "paired", "room": room_code})
                print(f"[Room] {room_code}: Paired!")
            else:
                # First client — wait for second
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

        else:
            send_json(client_sock, {"status": "error", "message": f"Unknown action: {action}"})
            client_sock.close()
            return

        # Step 2: Relay TCP
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
                        if room_code in rooms:
                            rooms[room_code]["udp_addrs"][my_index] = (client_addr[0], udp_port)
                    print(f"[UDP] Registered {client_addr[0]}:{udp_port} for {room_code}")
                    continue
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

            try:
                send_frame(peer_sock, frame)
            except Exception:
                break

    except Exception as e:
        print(f"[Room] Error with {client_addr}: {e}")
    finally:
        client_sock.close()
        if room_code:
            with rooms_lock:
                if room_code in rooms:
                    for c in rooms[room_code]["clients"]:
                        try:
                            c.close()
                        except:
                            pass
                    del rooms[room_code]

# ── UDP Relay ────────────────────────────────────────────────────

def udp_relay_loop(udp_sock):
    """Receive UDP packets and forward to the paired peer"""
    while True:
        try:
            data, addr = udp_sock.recvfrom(8192)
            with rooms_lock:
                for code, room in rooms.items():
                    udp_addrs = room["udp_addrs"]
                    if udp_addrs[0] and udp_addrs[0] == addr:
                        if udp_addrs[1]:
                            udp_sock.sendto(data, udp_addrs[1])
                        break
                    elif udp_addrs[1] and udp_addrs[1] == addr:
                        if udp_addrs[0]:
                            udp_sock.sendto(data, udp_addrs[0])
                        break
        except Exception as e:
            print(f"[UDP] Error: {e}")

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
        
        action = msg.get("action")
        
        if action == "REGISTER":
            # This is a presence connection — handle inline
            # Re-process the REGISTER message
            user_id = msg.get("user_id", "unknown")
            name = msg.get("name", "Unknown")
            mode = msg.get("mode", "GREEN")
            
            with presence_lock:
                presence[user_id] = {
                    "name": name,
                    "mode": mode,
                    "sock": client_sock,
                    "addr": client_addr
                }
            
            print(f"[Presence] Registered: {name} ({user_id})")
            send_json(client_sock, {"status": "registered", "user_id": user_id})
            broadcast_presence()
            
            # Continue handling presence messages
            handle_presence_client(client_sock, client_addr)
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
    
    except Exception as e:
        print(f"[Router] Error: {e}")
        try:
            client_sock.close()
        except:
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

        # Relay TCP
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
            try:
                send_frame(peer_sock, frame)
            except Exception:
                break

    except Exception as e:
        print(f"[Room] Error: {e}")
    finally:
        client_sock.close()
        if room_code:
            with rooms_lock:
                if room_code in rooms:
                    for c in rooms[room_code]["clients"]:
                        try:
                            c.close()
                        except:
                            pass
                    del rooms[room_code]

# ── Main ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Office Hours Relay Server")
    parser.add_argument("--port", type=int, default=50002, help="Port for TCP and UDP (default: 50002)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    args = parser.parse_args()

    tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_server.bind((args.host, args.port))
    tcp_server.listen(20)
    print(f"[Server] Office Hours Relay listening on {args.host}:{args.port}")
    print(f"[Server] Presence + Room relay active")

    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp_sock.bind((args.host, args.port))

    threading.Thread(target=udp_relay_loop, args=(udp_sock,), daemon=True).start()

    def cleanup_loop():
        while True:
            time.sleep(60)
            cleanup_stale_rooms()
    threading.Thread(target=cleanup_loop, daemon=True).start()

    print(f"[Server] Ready on port {args.port}")
    try:
        while True:
            client_sock, client_addr = tcp_server.accept()
            threading.Thread(target=handle_client, args=(client_sock, client_addr, udp_sock), daemon=True).start()
    except KeyboardInterrupt:
        print("\n[Server] Shutting down...")
    finally:
        tcp_server.close()
        udp_sock.close()

if __name__ == "__main__":
    main()
