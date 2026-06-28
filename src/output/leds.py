class LEDstrip: 
    def __init__(self, num_pixels: int = 60):
        self.num_pixels = num_pixels
        # we are simulating using print for now, implement hardware later when LED model + mapping is available
    
    def render(self, visual:dict) -> None: 
        print(f"LEDs --> hue{visual['hue']:>3}, brightness {visual['brightness']:.2f}")