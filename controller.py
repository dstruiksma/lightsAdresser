import ast

import board
import neopixel
from time import sleep


def rgb_to_grb(color):
    color[0], color[1] = color[1], color[0]
    return color


class LightsController:
    def __init__(self):
        self.lights = neopixel.NeoPixel(board.D18, 400, brightness=1, auto_write=False)
        self.color = [50, 255, 10]
        self.brightness = 1
        self.on = False

    def set_light_color(self, color):
        grb_color = rgb_to_grb(ast.literal_eval(color))
        self.color = grb_color
        self.lights.fill((color[0], color[1], color[2]))
        self.lights.show()
        self.on = True

    def set_brightness(self, brightness):
        self.lights.brightness = brightness
        self.brightness = brightness
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

