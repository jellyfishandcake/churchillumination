"""
WebSocket server: the bridge between the Python pipeline and the browser.
 
Two jobs:
  1. Publish the latest EnvironmentState to any connected browser (~20Hz).
     The browser uses this to update its dashboard AND to drive p5 sketches.
  2. Receive a colour array back from the browser (sampled from the p5 canvas)
     and pass it on to the LED strip.
 
Also serves the /web folder as static files so you can just open
http://localhost:8000 in a browser — no separate web server needed.
"""
import asyncio
import json
import pathlib
import re
from http import HTTPStatus
import websockets
from websockets.http11 import Response
from websockets.datastructures import Headers
 
# The "shared state" the pipeline updates and the server reads.
# Kept as a simple module-level dict so main.py can mutate it directly.
# For a bigger project you'd use a queue; for now this is fine and clearer.
latest = {
    "state": {"activity_level": 0.0, "mood": "neutral", "presence_count": 0, "audio_scene": None},
    "sensor_health": {},
    # LED count + physical layout, so the browser can build the pixel map
    # itself instead of guessing a hardcoded LED count.
    "leds": {"num_pixels": 60, "layout": "strip"},
    # Progress/result of the most recent contribute.html palette build, so
    # that page can just read this every broadcast tick instead of needing
    # a dedicated reply message. See palette_job_request below and
    # main.py's palette_build_loop, which actually does the work.
    "palette_job": {"status": "idle", "name": None, "hex_colors": None, "error": None, "overwritten": False},
}

# When the browser sends colours back, we stash them here so main.py can pick up.
# Same pattern: shared dict, one writer, one reader.
incoming = {"pixels": None}

# A pending contribute.html submission, waiting for main.py's
# palette_build_loop to pick it up and clear this slot. Single-slot (not a
# queue) - only one build runs at a time, see the "processing" check below.
palette_job_request = None

# Set of connected browser WebSockets (usually 1 — you, watching the dashboard)
clients = set()

# Terminal control state - the admin/public terminal pages mutate this via
# `{"control": {...}}` messages (see _handle_control below); main.py's loops
# read it each tick. Same "simple shared dict" philosophy as `latest`/
# `incoming` above. Real values are seeded from config in main.py's main() -
# these are just structurally-valid placeholders so the server can run
# before that happens.
runtime_settings = {
    "effect": None,
    "palette": None,
    "sensors_enabled": {},
    "activation_timeout_seconds": None,
    "smoothing_alpha": None,
    "state_override": None,
}

# Passcode gating admin-only actions, and the set of connections that have
# proven they know it. Both set once from config in main.py's main().
# admin_passcode is deliberately never included in any broadcast payload.
admin_passcode = None
admin_clients = set()

# Actions only an authenticated admin connection may perform. "admin_login",
# "set_effect", and "build_palette" are intentionally absent - login is how
# you become admin, and the effect/palette picker plus the contribute.html
# palette builder are the controls public users (anyone on the LAN) get.
ADMIN_ACTIONS = {
    "toggle_sensor",
    "set_activation_timeout",
    "set_smoothing_alpha",
    "set_state_override",
    "clear_state_override",
}

PALETTE_NAME_RE = re.compile(r"^[A-Za-z0-9_\- ]{1,40}$")
MAX_IMAGE_DATA_URL_CHARS = 8_000_000  # comfortably under max_size below, after base64 overhead


async def handle_client(websocket):
    """Called once per browser connection. Sends the latest state ~20x/sec,
    and reads any messages the browser sends back."""
    clients.add(websocket)
    try:
        # Kick off two concurrent loops: one sending, one receiving.
        send_task = asyncio.create_task(send_loop(websocket))
        recv_task = asyncio.create_task(recv_loop(websocket))
        # Wait until either finishes (usually because the browser disconnected)
        done, pending = await asyncio.wait(
            {send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
    finally:
        clients.discard(websocket)
        admin_clients.discard(websocket)


async def send_loop(websocket):
    """Push the latest state to the browser 20 times per second."""
    while True:
        payload = {**latest, "runtime_settings": runtime_settings}
        await websocket.send(json.dumps(payload))
        await asyncio.sleep(0.05)  # 20 Hz


async def recv_loop(websocket):
    """Receive messages from the browser: either sampled canvas colours
    (legacy `{"pixels": [...]}`, currently unconsumed - see main.py's
    led_loop) or a terminal control message."""
    async for message in websocket:
        try:
            data = json.loads(message)
            if "pixels" in data:
                # Expected: [[r,g,b], [r,g,b], ...]  (one triple per LED)
                incoming["pixels"] = data["pixels"]
            elif "control" in data:
                await _handle_control(websocket, data["control"])
        except (json.JSONDecodeError, KeyError):
            pass  # Ignore malformed messages


async def _handle_control(websocket, payload: dict) -> None:
    """Dispatch a control message. Only structural validation happens here
    (right types, sane ranges) - domain validation (is this a real effect
    or sensor name?) belongs to whichever of main.py's loops actually owns
    that registry; they already need a "fall back safely" path for a stale
    or bad value regardless, so that's where it naturally lives. Keeps this
    module a dumb transport layer with no imports from src.sensing/output."""
    global palette_job_request
    action = payload.get("action")

    if action == "admin_login":
        ok = payload.get("passcode") == admin_passcode
        if ok:
            admin_clients.add(websocket)
        else:
            print(f"[server] admin_login failed from {websocket.remote_address}")
        # Direct, targeted reply - the only one needed, since every other
        # action's effect becomes visible to all clients via the normal
        # broadcast within 50ms.
        await websocket.send(json.dumps({"control_ack": {"action": "admin_login", "ok": ok}}))
        return

    if action in ADMIN_ACTIONS and websocket not in admin_clients:
        print(f"[server] rejected admin action {action!r} from unauthenticated client")
        return

    if action == "set_effect":
        effect = payload.get("effect")
        palette = payload.get("palette")
        if isinstance(effect, str) and isinstance(palette, str):
            runtime_settings["effect"] = effect
            runtime_settings["palette"] = palette

    elif action == "toggle_sensor":
        sensor = payload.get("sensor")
        enabled = payload.get("enabled")
        if isinstance(sensor, str) and isinstance(enabled, bool):
            runtime_settings["sensors_enabled"][sensor] = enabled

    elif action == "set_activation_timeout":
        seconds = payload.get("seconds")
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > 0:
            runtime_settings["activation_timeout_seconds"] = float(seconds)

    elif action == "set_smoothing_alpha":
        alpha = payload.get("alpha")
        if isinstance(alpha, (int, float)) and not isinstance(alpha, bool) and 0.0 <= alpha <= 1.0:
            runtime_settings["smoothing_alpha"] = float(alpha)

    elif action == "set_state_override":
        mood = payload.get("mood")
        activity_level = payload.get("activity_level")
        valid_activity = isinstance(activity_level, (int, float)) and not isinstance(activity_level, bool)
        if isinstance(mood, str) and valid_activity:
            runtime_settings["state_override"] = {
                "mood": mood,
                "activity_level": min(max(float(activity_level), 0.0), 1.0),
            }

    elif action == "clear_state_override":
        runtime_settings["state_override"] = None

    elif action == "build_palette":
        name = payload.get("name")
        image_data_url = payload.get("image_data_url")
        use_ai = payload.get("use_ai")
        n_colors = payload.get("n_colors")
        photo_colors = payload.get("photo_colors")

        if latest["palette_job"]["status"] == "processing":
            print("[server] rejected build_palette: a job is already processing")
            return
        if not (isinstance(name, str) and PALETTE_NAME_RE.match(name)):
            print(f"[server] rejected build_palette: invalid name {name!r}")
            return
        if not (isinstance(image_data_url, str) and image_data_url.startswith("data:image/")):
            print("[server] rejected build_palette: image_data_url missing/malformed")
            return
        if len(image_data_url) > MAX_IMAGE_DATA_URL_CHARS:
            print("[server] rejected build_palette: image_data_url too large")
            latest["palette_job"] = {
                "status": "error", "name": name, "hex_colors": None,
                "error": "Photo too large - try a smaller one.", "overwritten": False,
            }
            return
        if not isinstance(use_ai, bool):
            print("[server] rejected build_palette: use_ai not a bool")
            return
        if not (isinstance(n_colors, int) and not isinstance(n_colors, bool) and 2 <= n_colors <= 8):
            print("[server] rejected build_palette: n_colors out of range")
            return
        if not (isinstance(photo_colors, int) and not isinstance(photo_colors, bool) and 1 <= photo_colors <= 4):
            print("[server] rejected build_palette: photo_colors out of range")
            return

        palette_job_request = {
            "name": name,
            "image_data_url": image_data_url,
            "use_ai": use_ai,
            "n_colors": n_colors,
            "photo_colors": photo_colors,
        }
        latest["palette_job"] = {
            "status": "queued", "name": name, "hex_colors": None,
            "error": None, "overwritten": False,
        }

    else:
        print(f"[server] ignoring unknown control action: {action!r}")
 
 
# ---- Static file serving ---------------------------------------------------
# The websockets library can also serve HTTP requests via a "process_request"
# hook. We use it to serve the /web folder so opening localhost:8000 shows
# the dashboard without needing a separate web server.
 
WEB_DIR = pathlib.Path(__file__).parent.parent.parent / "web"
 
MIME_TYPES = {
    ".html": "text/html",
    ".js": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
}
 
async def serve_static(connection, request):
    """Called before WebSocket upgrade. If the request is HTTP, serve a file.
    If it's a WebSocket upgrade request, return None so websockets handles it."""
    # WebSocket upgrade requests have an Upgrade: websocket header.
    # If we see one, let the WS handler take over.
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return None
 
    path = request.path
    if path == "/":
        path = "/index.html"
 
    file_path = WEB_DIR / path.lstrip("/")
 
    # Security: reject any path that escapes WEB_DIR
    try:
        file_path.resolve().relative_to(WEB_DIR.resolve())
    except ValueError:
        return Response(
            HTTPStatus.FORBIDDEN, "Forbidden",
            Headers([("Content-Type", "text/plain")]),
            b"Forbidden\n",
        )
 
    if not file_path.is_file():
        return None  # Let websockets handle it (probably a WS upgrade request)
 
    content = file_path.read_bytes()
    mime = MIME_TYPES.get(file_path.suffix, "application/octet-stream")
    return Response(
        HTTPStatus.OK, "OK",
        Headers([("Content-Type", mime), ("Content-Length", str(len(content)))]),
        content,
    )
 
 
async def start_server(host="localhost", port=8000):
    """Start the WebSocket + static-file server. Runs forever."""
    print(f"Server running at http://{host}:{port}")
    async with websockets.serve(
        handle_client, host, port, process_request=serve_static,
        # Default is 1 MiB - contribute.html's downscaled photo uploads stay
        # well under that in the normal case, but this is defense-in-depth:
        # exceeding max_size closes the whole connection, not just rejects
        # the one message, so a little headroom avoids that surprise.
        max_size=6 * 1024 * 1024,
    ):
        await asyncio.Future()  # run forever

