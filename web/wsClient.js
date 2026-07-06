// wsClient.js — shared websocket connect/reconnect boilerplate used by both
// app.js (public dashboard) and admin.js (admin terminal), so admin.js
// doesn't need to duplicate this or reuse app.js against a DOM it doesn't
// match (app.js assumes public-page elements like #noise-value that don't
// exist on admin.html).
//
// onMessage(data) is called with the already-JSON-parsed payload of every
// message received. Returns the WebSocket so callers can also .send() on it.

function connectWS(onMessage, statusEl, onClose) {
  let ws;

  function connect() {
    ws = new WebSocket(`ws://${location.host}`);

    ws.onopen = () => {
      if (statusEl) {
        statusEl.textContent = "● live";
        statusEl.className = "connected";
      }
    };

    ws.onclose = () => {
      if (statusEl) {
        statusEl.textContent = "disconnected — retrying…";
        statusEl.className = "";
      }
      // A new connection is unauthenticated server-side (admin status is
      // per-connection, not per-session) - callers with login state to
      // reset (e.g. admin.js) get a chance to do so here.
      onClose?.();
      setTimeout(connect, 1000);
    };

    ws.onmessage = (event) => {
      onMessage(JSON.parse(event.data));
    };

    window.ws = ws;
  }

  connect();
  return {
    send: (obj) => {
      if (window.ws?.readyState === WebSocket.OPEN) {
        window.ws.send(JSON.stringify(obj));
      }
    },
  };
}

window.connectWS = connectWS;
