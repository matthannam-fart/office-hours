"""WebSocket bridge between Office Hours and the Stream Deck plugin.

The plugin connects to ws://localhost:50003 and exchanges JSON messages:

  OH → Plugin (state broadcasts):
    {"type":"state", "mode":"GREEN", "talk":"idle", "message":false,
     "teams":[...], "users":[...], "activeTeamId":"", "activeUserId":"",
     "connected":false, "peerName":""}

  Plugin → OH (commands):
    {"action":"ptt_press"}
    {"action":"ptt_release"}
    {"action":"cycle_mode"}
    {"action":"select_team", "index":0}
    {"action":"select_user", "index":0}
    {"action":"show_panel"}
"""

import asyncio
import json
import threading

WS_PORT = 50003


class DeckWSServer:
    def __init__(self, command_callback=None, log_callback=None):
        """
        command_callback(action, payload) — called on the asyncio thread
        when the plugin sends a command.
        """
        self._command_cb = command_callback
        self._log = log_callback or (lambda msg: None)
        self._clients = set()
        self._loop = None
        self._thread = None
        self._state = {}
        self._server = None
        self._ready = threading.Event()

    # ── Public API (called from main thread) ────────────────────

    def start(self):
        """Launch the WebSocket server on a background thread."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def broadcast_state(self, state: dict):
        """Send full state snapshot to all connected plugins."""
        self._state = state
        if not self._ready.is_set():
            return  # Server not ready yet — state is cached for new clients
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(
                asyncio.ensure_future,
                self._broadcast(json.dumps({"type": "state", **state}))
            )

    def stop(self):
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)

    @property
    def client_count(self):
        return len(self._clients)

    # ── Internal ────────────────────────────────────────────────

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())
        self._ready.set()
        self._loop.run_forever()

    async def _serve(self):
        try:
            import websockets
        except ImportError:
            self._log("[DeckWS] websockets package not installed — plugin bridge disabled")
            return

        try:
            self._server = await websockets.serve(
                self._handle_client, "127.0.0.1", WS_PORT,
                ping_interval=20, ping_timeout=10
            )
            self._log(f"[DeckWS] Listening on ws://127.0.0.1:{WS_PORT}")
        except OSError as e:
            self._log(f"[DeckWS] Could not bind port {WS_PORT}: {e}")

    async def _handle_client(self, ws):
        self._clients.add(ws)
        self._log(f"[DeckWS] Plugin connected ({len(self._clients)} client(s))")
        # Send current state immediately
        if self._state:
            try:
                await ws.send(json.dumps({"type": "state", **self._state}))
            except Exception:
                pass
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    action = msg.get("action", "")
                    if self._command_cb:
                        self._command_cb(action, msg)
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass
        finally:
            self._clients.discard(ws)
            self._log(f"[DeckWS] Plugin disconnected ({len(self._clients)} client(s))")

    async def _broadcast(self, data):
        if not self._clients:
            return
        dead = set()
        for ws in self._clients:
            try:
                await ws.send(data)
            except Exception:
                dead.add(ws)
        self._clients -= dead
