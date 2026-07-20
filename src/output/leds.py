"""APA102 (DotStar-compatible) LED strip driver, over the Pi's hardware SPI0.

Protocol: a start frame of 32 zero bits, then one 32-bit frame per pixel
(0b111 + a 5-bit global brightness, then B, G, R bytes - APA102/SK9822 take
colour in BGR order, not RGB), then an end frame of extra clock pulses so
every pixel's data actually latches through the whole chain. (num_pixels +
15) // 16 bytes of 0xFF is the commonly-documented safe end-frame length -
covers SK9822 clones' clock-latching behaviour that the older "N zero
bytes" advice doesn't reliably cover.

Per-pixel global brightness (the 5-bit field) is left at max - the RGB
bytes already carry the actual colour/intensity at 8 bits/channel (higher
resolution than the 5-bit field gives), and gamma correction already
happens upstream in main.py's led_loop before render_pixels is ever
called, so this file has no colour math of its own to do.
"""
try:
    import spidev
except ImportError:
    spidev = None

START_FRAME = bytes(4)
MAX_BRIGHTNESS_BYTE = 0b11100000 | 31  # 0xFF - global brightness field at max


class LEDStrip:
    def __init__(self, num_pixels: int = 60, spi_bus: int = 0, spi_device: int = 0, spi_speed_hz: int = 8_000_000):
        self.num_pixels = num_pixels
        self._spi = None

        if spidev is None:
            print("[LEDStrip] spidev not installed - printing frames instead of driving real LEDs (pip install -r requirements-pi.txt)")
        else:
            try:
                spi = spidev.SpiDev()
                spi.open(spi_bus, spi_device)
                spi.max_speed_hz = spi_speed_hz
                spi.mode = 0
                self._spi = spi
            except Exception as exc:
                # No strip wired up yet, or SPI isn't enabled (raspi-config
                # -> Interface Options -> SPI) - falls back to printing
                # frames instead of driving real LEDs, same "safe on a dev
                # laptop and on the Pi" contract every sensor here follows.
                print(f"[LEDStrip] couldn't open SPI ({exc}) - printing frames instead of driving real LEDs")
                self._spi = None

    def render_pixels(self, pixels: list) -> None:
        """An array of [r,g,b] triples, one per LED, already gamma-corrected
        by main.py's led_loop. Pushes them to the real strip over SPI, or
        prints a summary if SPI isn't available (dev laptop, or the strip
        isn't wired up / SPI isn't enabled yet)."""
        if not pixels:
            return

        if self._spi is None:
            n = len(pixels)
            first, mid, last = pixels[0], pixels[n // 2], pixels[-1]
            print(f"LEDs [{n}px] first={first} mid={mid} last={last}")
            return

        buf = bytearray(START_FRAME)
        for r, g, b in pixels:
            buf += bytes((MAX_BRIGHTNESS_BYTE, int(b) & 0xFF, int(g) & 0xFF, int(r) & 0xFF))
        end_frame_len = (len(pixels) + 15) // 16
        buf += bytes([0xFF] * end_frame_len)

        try:
            self._spi.writebytes2(buf)
        except Exception as exc:
            print(f"[LEDStrip] write failed ({exc})")
