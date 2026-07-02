class LEDstrip: 
    def __init__(self, num_pixels: int = 60):
        self.num_pixels = num_pixels
        # we are simulating using print for now, implement hardware later when LED model + mapping is available
    
    def render(self, visual:dict) -> None: 
        print(f"LEDs --> hue{visual['hue']:>3}, brightness {visual['brightness']:.2f}")

class LEDStrip:
    def __init__(self, num_pixels: int = 60):
        self.num_pixels = num_pixels
        # TODO: init real hardware later (rpi_ws281x on the Pi)
 
    def render(self, visual: dict) -> None:
        """Legacy path: one hue + brightness for the whole strip.
        Still used before the browser is connected."""
        print(f"LEDs -> hue {visual['hue']:>3}, brightness {visual['brightness']:.2f}")
 
    def render_pixels(self, pixels: list) -> None:
        """New path: an array of [r,g,b] triples, one per LED.
        This is what the browser sends after sampling its p5 canvas.
 
        For now we just print a summary. On the Pi this will drive rpi_ws281x."""
        if not pixels:
            return
        # Print first, middle, last so you can see something changing without
        # spamming 60 colours per line
        n = len(pixels)
        first, mid, last = pixels[0], pixels[n // 2], pixels[-1]
        print(f"LEDs [{n}px] first={first} mid={mid} last={last}")
 