"""
Per-sensor "producer" threads. main.py's render_loop is the "consumer" side
of this split - see its own docstring. Each producer here just calls its
sensor's read() at its own pace and merges the result into a shared dict of
"latest reading per key" - single writer per key, no locks, no queues. No
lock is needed for the dict itself: CPython's GIL makes one dict item
assignment atomic, and no two producers below ever write the same key
(pir/mmwave both write "presence", but config only ever enables one of the
two at once - see mmwave.py's own docstring on why they're alternatives, not
a pair). A queue was deliberately not used here - a queue lets stale
readings pile up behind a slow consumer, and render_loop only ever wants
whatever's newest, never a backlog.

motion/multisensor/heart_rate are the one exception: they share the Pi's
physical I2C bus and cannot be read concurrently (found 2026-08-18 - see
git history: two threads' interleaved I2C transactions corrupted readings
across every I2C sensor at once, surfaced as "all the LEDs behaving
strangely"). The old asyncio version fixed this with a shared
threading.Lock around each of their reads. Here they're combined into ONE
thread (I2CGroupProducer) that reads all three in strict sequence instead -
a single thread can't interleave with itself, so this removes the lock
entirely rather than just moving it somewhere else.

Every other sensor - including ones that already run their own internal
background thread and cache a result, like audio.py's mic callback thread,
nodes.py's MQTT client thread, and weather.py's poll thread - still gets its
own plain SensorProducer here too, even though its read() is already cheap/
non-blocking. This is a deliberate uniformity choice over the "obvious"
optimization of calling those specific sensors' read() straight from
render_loop: it keeps render_loop's own code sensor-agnostic (it only ever
touches the shared dict, never a Sensor instance directly, except to read
.healthy/.last_error for the dashboard), and it means a future sensor whose
read() turns out to block doesn't require moving it out of render_loop later
- it's already on its own thread from day one.
"""
import threading
import time

from src.net import server

# I2C-bus sensors - see I2CGroupProducer. Kept as one tuple so main.py and
# this module agree on which sensor names are "the I2C group" without
# duplicating the list.
I2C_SENSOR_NAMES = ("motion", "multisensor", "heart_rate")

# Per-sensor poll interval, in seconds. No config field exists for this
# today (config.py's sensors block only carries `enabled` + wiring params
# like gpio_pin/serial_port) - these are plain hardcoded constants, same
# "untuned, tune by feel" style as main.py's SMOOTHING_ALPHA/
# MOTION_BURST_THRESHOLD. accel_stick is polled faster than the render
# loop's own 20Hz: its read() never blocks (pyserial opened timeout=0 - see
# accel_stick.py), so polling it quickly just means a real shake's firmware
# line (sent ~20Hz) is never sitting unread for a whole extra render tick,
# which matters for ShakeFireworkEffect's deliberately-instant trigger (see
# that effect's own docstring). weather polls far slower than everything
# else since its own internal cache only actually changes every 10 minutes
# (see weather.py's FETCH_INTERVAL_SECONDS) - polling faster would just be
# redundant wake-ups for a value that hasn't moved.
POLL_INTERVALS = {
    "pir": 0.05,
    "mmwave": 0.05,
    "accel_stick": 0.02,
    "audio": 0.05,
    "nodes": 0.1,
    "weather": 5.0,
}
DEFAULT_POLL_INTERVAL = 0.05

# The shared "latest reading per key" store - module-level, same convention
# as server.py's `latest`/`runtime_settings` (a plain dict any thread can
# import and read/write directly, no wrapper object). main.py's render_loop
# takes a fresh `dict(raw)` snapshot once per tick; every producer below
# writes into this same dict in place.
raw: dict = {}


class SensorProducer:
    """One sensor, one thread, one shared dict. Loops calling sensor.read()
    every `interval` seconds and merges whatever keys it returns into
    `shared` (updated in place, not reassigned - render_loop holds the same
    dict reference and always sees whatever's newest without any hand-off
    mechanism)."""

    def __init__(self, name: str, sensor, shared: dict, interval: float):
        self.name = name
        self.sensor = sensor
        self.shared = shared
        self.interval = interval
        self._thread = threading.Thread(target=self._run, name=f"sensor-{name}", daemon=True)

    def start(self) -> "SensorProducer":
        self._thread.start()
        return self

    def _run(self):
        while True:
            # Live-disable, same feature build_sensors' own docstring
            # promises: a sensor started enabled can be turned off (and
            # back on) from the admin dashboard without a restart. Skipping
            # the read entirely while disabled - not just discarding its
            # result - matches the old sensor_loop's behaviour of dropping
            # a disabled sensor from that tick's read set altogether.
            if server.runtime_settings["sensors_enabled"].get(self.name, True):
                try:
                    reading = self.sensor.read()
                except Exception as exc:
                    # Last-resort net, same reasoning as the old sensor_loop's
                    # try/except: every sensor already falls back to its own
                    # mock internally (see base.py), so reaching here means a
                    # bug even in that fallback path - skip this tick rather
                    # than taking this thread down.
                    print(f"[{self.name}] read() raised even past its own fallback — skipping this tick: {exc}")
                else:
                    self.shared.update(reading)
            time.sleep(self.interval)


class I2CGroupProducer:
    """motion + multisensor + heart_rate, one thread, strict sequence - see
    module docstring for why these three specifically can't each get their
    own SensorProducer."""

    def __init__(self, sensors: dict, shared: dict):
        # Only the ones actually built (see main.py's build_sensors) - a
        # disabled/not-configured one is just skipped, same "config
        # controls what's wired in at all" contract every sensor follows.
        self.sensors = {name: sensors[name] for name in I2C_SENSOR_NAMES if name in sensors}
        self.shared = shared
        self._thread = threading.Thread(target=self._run, name="sensor-i2c-group", daemon=True)

    def start(self) -> "I2CGroupProducer":
        if self.sensors:
            self._thread.start()
        return self

    def _run(self):
        while True:
            for name, sensor in self.sensors.items():
                if not server.runtime_settings["sensors_enabled"].get(name, True):
                    continue
                try:
                    reading = sensor.read()
                except Exception as exc:
                    print(f"[{name}] read() raised even past its own fallback — skipping this tick: {exc}")
                else:
                    self.shared.update(reading)
            # No extra sleep here - motion.read() already blocks for up to
            # ~1/FRAME_RATE_HZ (motion.py) waiting on real hardware, which
            # paces this whole loop on its own. multisensor/heart_rate are
            # fast enough that reading them once per motion frame is still
            # far more often than either actually needs.


def start_producers(sensors: dict, shared: dict) -> list:
    """Builds and starts one producer per sensor (grouped where the
    physical I2C bus demands it - see I2CGroupProducer), returns the list of
    started producers so main.py can hold a reference if it ever needs one
    (e.g. future health-check tooling) - nothing today needs to join() them,
    since every thread here is daemon=True and the process is expected to
    exit via render_loop's own KeyboardInterrupt handling, not by these
    threads stopping first."""
    producers = [I2CGroupProducer(sensors, shared).start()]
    for name, sensor in sensors.items():
        if name in I2C_SENSOR_NAMES:
            continue
        interval = POLL_INTERVALS.get(name, DEFAULT_POLL_INTERVAL)
        producers.append(SensorProducer(name, sensor, shared, interval).start())
    return producers
