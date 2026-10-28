import ast
import math
import os
import time

import board
import neopixel

from effects import EFFECTS, render

PIXEL_COUNT = int(os.environ.get("LIGHTS_PIXELS", "400"))
FRAME_INTERVAL_S = 1 / 25


def rgb_to_grb(color):
    red, green, blue = color
    return (green, red, blue)


class LightsController:
    def __init__(self, pixel_count=PIXEL_COUNT):
        self.pixel_count = pixel_count
        self.lights = neopixel.NeoPixel(board.D18, pixel_count, brightness=1, auto_write=False)
        self.rgb = (255, 50, 10)
        self.brightness = 1
        self.on = False
        self.effect = "none"
        self._effect_started = time.monotonic()

    def set_light_color(self, color):
        # Older app builds send the color as a string such as "[255, 120, 40]".
        if isinstance(color, str):
            try:
                color = ast.literal_eval(color)
            except (SyntaxError, ValueError):
                raise ValueError("color string must look like [r, g, b]")
        if not isinstance(color, (list, tuple)) or len(color) != 3:
            raise ValueError("color must be a list with exactly 3 values")
        rgb = tuple(int(v) for v in color)
        if any(v < 0 or v > 255 for v in rgb):
            raise ValueError("each color value must be between 0 and 255")
        self.rgb = rgb
        self.on = True
        self._show_current()

    def set_brightness(self, brightness):
        value = float(brightness)
        if not math.isfinite(value) or value < 0 or value > 1:
            raise ValueError("brightness must be between 0 and 1")
        self.lights.brightness = value
        self.brightness = value
        self.lights.show()

    def set_effect(self, effect):
        if effect not in EFFECTS:
            raise ValueError(f"unknown effect, choose one of: {', '.join(EFFECTS)}")
        self.effect = effect
        self._effect_started = time.monotonic()
        if self.on:
            self._show_current()

    def light_switch(self):
        self.on = not self.on
        if self.on:
            self._show_current()
        else:
            self.lights.fill((0, 0, 0))
            self.lights.show()

    def render_frame(self):
        """Push the next animation frame. Call with the hardware lock held."""
        if self.on and self.effect != "none":
            self._write(render(self.effect, time.monotonic() - self._effect_started, self.pixel_count, self.rgb))

    def run_effects(self, lock):
        """Background loop that animates the active effect; idles otherwise."""
        while True:
            with lock:
                self.render_frame()
            time.sleep(FRAME_INTERVAL_S)

    def _show_current(self):
        if self.effect == "none":
            self.lights.fill(rgb_to_grb(self.rgb))
            self.lights.show()
        else:
            self.render_frame()

    def _write(self, frame):
        self.lights[:] = [rgb_to_grb(pixel) for pixel in frame]
        self.lights.show()

    def state(self):
        return {"on": self.on, "color": list(self.rgb), "brightness": self.brightness, "effect": self.effect}
