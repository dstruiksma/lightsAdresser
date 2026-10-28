"""Gentle animated effects, as pure functions of time.

Each effect turns (time, pixel count, base RGB color) into one RGB tuple per
pixel. No hardware access here, so the maths can be tested anywhere; the
controller pushes the frames to the strip.
"""
import colorsys
import math
import random

EFFECTS = ("none", "breathe", "twinkle", "gradient")

BREATHE_PERIOD_S = 6.0
BREATHE_MIN = 0.25          # never fully dark
TWINKLE_BASE = 0.15         # dim glow between fireflies
GRADIENT_PERIOD_S = 20.0
GRADIENT_HUE_SPREAD = 0.06  # +/- hue shift, so the colour stays recognisable


def _scale(color, factor):
    return tuple(max(0, min(255, round(c * factor))) for c in color)


def breathe(t, count, color):
    phase = 0.5 - 0.5 * math.cos(2 * math.pi * t / BREATHE_PERIOD_S)
    pixel = _scale(color, BREATHE_MIN + (1 - BREATHE_MIN) * phase)
    return [pixel] * count


class _Fireflies:
    """Per-pixel rhythm, fixed per strip length so frames are a pure function of t."""

    def __init__(self, count):
        rng = random.Random(count)
        self.periods = [rng.uniform(3.0, 9.0) for _ in range(count)]
        self.phases = [rng.random() for _ in range(count)]


_fireflies_cache = {}


def twinkle(t, count, color):
    flies = _fireflies_cache.get(count)
    if flies is None:
        flies = _fireflies_cache[count] = _Fireflies(count)
    frame = []
    for period, phase in zip(flies.periods, flies.phases):
        # A sharp, short peak once per period: mostly dim, briefly bright.
        wave = math.sin(2 * math.pi * (t / period + phase))
        glow = max(0.0, wave) ** 12
        frame.append(_scale(color, TWINKLE_BASE + (1 - TWINKLE_BASE) * glow))
    return frame


def gradient(t, count, color):
    r, g, b = (c / 255 for c in color)
    hue, saturation, value = colorsys.rgb_to_hsv(r, g, b)
    if saturation < 0.05:
        # White has no hue to shift; give it a soft travelling brightness wave instead.
        return [
            _scale(color, 0.75 + 0.25 * math.sin(2 * math.pi * (i / max(count, 1) + t / GRADIENT_PERIOD_S)))
            for i in range(count)
        ]
    frame = []
    for i in range(count):
        offset = GRADIENT_HUE_SPREAD * math.sin(2 * math.pi * (i / max(count, 1) + t / GRADIENT_PERIOD_S))
        rr, gg, bb = colorsys.hsv_to_rgb((hue + offset) % 1.0, saturation, value)
        frame.append((round(rr * 255), round(gg * 255), round(bb * 255)))
    return frame


RENDERERS = {"breathe": breathe, "twinkle": twinkle, "gradient": gradient}


def render(effect, t, count, color):
    """Frame for effect at time t (seconds). 'none' gives the plain colour."""
    if effect == "none":
        return [tuple(color)] * count
    try:
        return RENDERERS[effect](t, count, tuple(color))
    except KeyError:
        raise ValueError(f"unknown effect, choose one of: {', '.join(EFFECTS)}") from None
