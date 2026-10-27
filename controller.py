import ast
import math

import board
import neopixel


def rgb_to_grb(color):
    red, green, blue = color
    return [green, red, blue]


class LightsController:
    def __init__(self):
        self.lights = neopixel.NeoPixel(board.D18, 400, brightness=1, auto_write=False)
        self.color = [50, 255, 10]
        self.brightness = 1
        self.on = False

    def set_light_color(self, color):
        # Older app builds send the color as a string such as "[255, 120, 40]".
        if isinstance(color, str):
            try:
                color = ast.literal_eval(color)
            except (SyntaxError, ValueError):
                raise ValueError("color string must look like [r, g, b]")
        if not isinstance(color, (list, tuple)) or len(color) != 3:
            raise ValueError("color must be a list with exactly 3 values")
        rgb = [int(v) for v in color]
        if any(v < 0 or v > 255 for v in rgb):
            raise ValueError("each color value must be between 0 and 255")
        grb_color = rgb_to_grb(rgb)
        self.color = grb_color
        self.lights.fill((grb_color[0], grb_color[1], grb_color[2]))
        self.lights.show()
        self.on = True

    def set_brightness(self, brightness):
        value = float(brightness)
        if not math.isfinite(value) or value < 0 or value > 1:
            raise ValueError("brightness must be between 0 and 1")
        self.lights.brightness = value
        self.brightness = value
        self.lights.show()

    def light_switch(self):
        if not self.on:
            self.lights.fill((self.color[0], self.color[1], self.color[2]))
            self.lights.show()
            self.on = True
        else:
            self.lights.fill((0, 0, 0))
            self.lights.show()
            self.on = False

    def state(self):
        green, red, blue = self.color
        return {"on": self.on, "color": [red, green, blue], "brightness": self.brightness}
