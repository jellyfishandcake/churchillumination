// app.js — connects to the Python server, keeps the dashboard alive,
// and exposes the current sensor/state values as `window.sensors` and
// `window.appState` so the p5 sketch can read them.
 
// These are the globals the sketch will see. Live-updated.
window.sensors = {
  noise: { level: 0.0 },
};
window.appState = {
  mood: "neutral",
  activity: 0.0,
  presenceCount: 0,
  hue: 200,
  brightness: 0.2,
};
 
const status = document.getElementById("status");
 
function connect() {
  // Same host/port as the page — served from the Python websockets library
  const ws = new WebSocket(`ws://${location.host}`);
 
  ws.onopen = () => {
    status.textContent = "● live";
    status.className = "connected";
  };
 
  ws.onclose = () => {
    status.textContent = "disconnected — retrying…";
    status.className = "";
    // Auto-reconnect after 1s. Useful during dev when you restart Python.
    setTimeout(connect, 1000);
  };
 
  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    const { state, visual } = data;
 
    // Update the globals the sketch reads
    window.appState.mood = state.mood;
    window.appState.activity = state.activity_level;
    window.appState.presenceCount = state.presence_count;
    window.appState.hue = visual.hue;
    window.appState.brightness = visual.brightness;
 
    // We don't yet get raw noise separately from the server — for now we
    // approximate it as activity_level. When we split them apart in
    // rules.py, this will just start receiving a separate field.
    window.sensors.noise.level = state.activity_level;
 
    // Update the DOM dashboard
    document.getElementById("noise-value").textContent = window.sensors.noise.level.toFixed(2);
    document.getElementById("noise-bar").style.width = `${window.sensors.noise.level * 100}%`;
    document.getElementById("activity-value").textContent = state.activity_level.toFixed(2);
    document.getElementById("activity-bar").style.width = `${state.activity_level * 100}%`;
    document.getElementById("mood-value").textContent = state.mood;
    document.getElementById("presence-value").textContent = state.presence_count;
    document.getElementById("hue-value").textContent = visual.hue;
    document.getElementById("brightness-value").textContent = visual.brightness.toFixed(2);
  };
 
  // Expose the socket so the sketch can send pixel data back
  window.ws = ws;
}
 
connect();
 
// Every 50ms, sample the p5 canvas and send colours to Python
setInterval(() => {
  if (window.ws?.readyState !== WebSocket.OPEN) return;
  if (typeof window.sampleCanvas !== "function") return;
 
  const pixels = window.sampleCanvas(60); // 60 LEDs
  if (pixels) {
    window.ws.send(JSON.stringify({ pixels }));
  }
}, 50);