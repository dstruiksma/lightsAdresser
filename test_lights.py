"""Tests for effects, controller and HTTP API, with the LED hardware stubbed out."""
import http.client
import json
import sys
import threading
import types
import unittest

# Stand-ins for the Pi-only hardware modules, installed before importing controller.
board = types.ModuleType("board")
board.D18 = 18
neopixel = types.ModuleType("neopixel")


class FakeNeoPixel:
    def __init__(self, pin, count, brightness=1, auto_write=False):
        self.pixels = [(0, 0, 0)] * count
        self.brightness = brightness
        self.shows = 0

    def fill(self, color):
        self.pixels = [tuple(color)] * len(self.pixels)

    def __setitem__(self, index, value):
        if isinstance(index, slice):
            self.pixels[index] = [tuple(v) for v in value]
        else:
            self.pixels[index] = tuple(value)

    def show(self):
        self.shows += 1


neopixel.NeoPixel = FakeNeoPixel
sys.modules.setdefault("board", board)
sys.modules.setdefault("neopixel", neopixel)

import controller  # noqa: E402
import effects  # noqa: E402
import server  # noqa: E402

ORANGE = (255, 120, 20)


class EffectsTest(unittest.TestCase):

    def test_every_effect_returns_one_valid_pixel_per_led(self):
        for effect in effects.EFFECTS:
            for t in (0, 1.3, 7.9, 120.5):
                frame = effects.render(effect, t, 50, ORANGE)
                self.assertEqual(50, len(frame), effect)
                for pixel in frame:
                    self.assertEqual(3, len(pixel))
                    self.assertTrue(all(isinstance(c, int) and 0 <= c <= 255 for c in pixel), (effect, pixel))

    def test_breathe_dims_and_brightens_but_never_goes_dark(self):
        levels = [effects.breathe(t / 10, 1, ORANGE)[0][0] for t in range(0, 61)]
        self.assertEqual(255, max(levels))
        self.assertGreaterEqual(min(levels), round(255 * effects.BREATHE_MIN) - 1)
        self.assertEqual(levels[0], effects.breathe(effects.BREATHE_PERIOD_S, 1, ORANGE)[0][0])

    def test_twinkle_is_mostly_dim_with_a_few_bright_fireflies(self):
        frame = effects.twinkle(4.2, 400, ORANGE)
        bright = sum(1 for p in frame if p[0] > 128)
        self.assertGreater(bright, 0)
        self.assertLess(bright, 400 * 0.25)
        self.assertTrue(all(p[0] >= round(255 * effects.TWINKLE_BASE) - 1 for p in frame))

    def test_twinkle_is_deterministic_for_a_given_time(self):
        self.assertEqual(effects.twinkle(3.0, 100, ORANGE), effects.twinkle(3.0, 100, ORANGE))

    def test_gradient_stays_close_to_the_chosen_colour_and_moves(self):
        frame = effects.gradient(0, 100, ORANGE)
        self.assertGreater(len(set(frame)), 10)
        self.assertTrue(all(p[0] > 200 for p in frame))  # still clearly orange-ish
        self.assertNotEqual(frame, effects.gradient(5, 100, ORANGE))

    def test_gradient_on_white_gives_a_brightness_wave(self):
        frame = effects.gradient(0, 100, (255, 255, 255))
        self.assertTrue(all(p[0] == p[1] == p[2] for p in frame))
        self.assertGreater(len(set(frame)), 5)

    def test_unknown_effect_is_rejected(self):
        with self.assertRaises(ValueError):
            effects.render("disco", 0, 10, ORANGE)


class ControllerTest(unittest.TestCase):

    def setUp(self):
        self.c = controller.LightsController(pixel_count=20)

    def test_colour_is_stored_as_rgb_and_written_as_grb(self):
        self.c.set_light_color([255, 120, 20])
        self.assertEqual([255, 120, 20], self.c.state()["color"])
        self.assertEqual((120, 255, 20), self.c.lights.pixels[0])

    def test_effect_frames_are_written_while_on(self):
        self.c.set_light_color([255, 120, 20])
        self.c.set_effect("twinkle")
        shows = self.c.lights.shows
        self.c.render_frame()
        self.assertEqual(shows + 1, self.c.lights.shows)
        self.assertEqual("twinkle", self.c.state()["effect"])

    def test_no_frames_while_off(self):
        self.c.set_effect("breathe")
        self.c.light_switch()   # on
        self.c.light_switch()   # off
        shows = self.c.lights.shows
        self.c.render_frame()
        self.assertEqual(shows, self.c.lights.shows)
        self.assertEqual({(0, 0, 0)}, set(self.c.lights.pixels))

    def test_switching_effect_off_restores_the_plain_colour(self):
        self.c.set_light_color([10, 20, 30])
        self.c.set_effect("gradient")
        self.c.set_effect("none")
        self.assertEqual({(20, 10, 30)}, set(self.c.lights.pixels))

    def test_unknown_effect_raises(self):
        with self.assertRaises(ValueError):
            self.c.set_effect("strobe")


class ServerTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.httpd = server.HTTPServer(("127.0.0.1", 0), server.LightsHandler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        payload = json.dumps(body) if body is not None else None
        conn.request(method, path, body=payload, headers={"Content-Type": "application/json"} if payload else {})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")

    def test_set_effect_and_read_it_back(self):
        self.assertEqual(200, self.request("POST", "/set_effect", {"effect": "breathe"})[0])
        self.assertEqual("breathe", self.request("GET", "/state")[1]["effect"])
        self.assertEqual(200, self.request("POST", "/set_effect", {"effect": "none"})[0])

    def test_unknown_effect_is_a_400_with_the_options(self):
        status, body = self.request("POST", "/set_effect", {"effect": "disco"})
        self.assertEqual(400, status)
        self.assertIn("twinkle", body["error"])

    def test_existing_endpoints_still_work(self):
        self.assertEqual(200, self.request("POST", "/set_color", {"color": "[255,120,20]"})[0])
        self.assertEqual(200, self.request("POST", "/set_brightness", {"brightness": 0.5})[0])
        self.assertEqual("ok", self.request("GET", "/healthz")[1]["status"])


if __name__ == "__main__":
    unittest.main()
