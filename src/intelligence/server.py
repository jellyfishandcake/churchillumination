"""
WebSocket server: the bridge between the Python pipeline and the browser.
 
Two jobs:
  1. Publish the latest EnvironmentState + visual to any connected browser (~20Hz).
     The browser uses this to update its dashboard AND to drive p5 sketches.
  2. Receive a colour array back from the browser (sampled from the p5 canvas)
     and pass it on to the LED strip.
 
Also serves the /web folder as static files so you can just open
http://localhost:8000 in a browser — no separate web server needed.
"""
import asyncio
import json
import pathlib
from http import HTTPStatus
import websockets
from websockets.http11 import Response
from websockets.datastructures import Headers
 
# The "shared state" the pipeline updates and the server reads.
# Kept as a simple module-level dict so main.py can mutate it directly.
# For a bigger project you'd use a queue; for now this is fine and clearer.
latest = {
    "state": {"activity_level": 0.0, "mood": "neutral", "presence_count": 0},
    "visual": {"hue": 200, "brightness": 0.2},
    "sensor_health": {},
}
 
# When the browser sends colours back, we stash them here so main.py can pick up.
# Same pattern: shared dict, one writer, one reader.
incoming = {"pixels": None}
 
# Set of connected browser WebSockets (usually 1 — you, watching the dashboard)
clients = set()
 
 
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
 
 
async def send_loop(websocket):
    """Push the latest state to the browser 20 times per second."""
    while True:
        await websocket.send(json.dumps(latest))
        await asyncio.sleep(0.05)  # 20 Hz
 
 
async def recv_loop(websocket):
    """Receive colour arrays sampled from the browser canvas."""
    async for message in websocket:
        try:
            data = json.loads(message)
            if "pixels" in data:
                # Expected: [[r,g,b], [r,g,b], ...]  (one triple per LED)
                incoming["pixels"] = data["pixels"]
        except (json.JSONDecodeError, KeyError):
            pass  # Ignore malformed messages
 
 
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
        handle_client, host, port, process_request=serve_static
    ):
        await asyncio.Future()  # run forever

