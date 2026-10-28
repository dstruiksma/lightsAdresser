"""Dependency-free HTTP API for the LED strip.

Same endpoints as the old Flask app (lights_api.py), built on the standard
library so it starts fast and stays small on a Pi Zero/3:

    GET  /switch          toggle the strip on/off
    POST /set_color       {"color": [r, g, b]} or {"color": "[r, g, b]"}, 0-255
    POST /set_brightness  {"brightness": 0.0-1.0}
    POST /set_effect      {"effect": "none" | "breathe" | "twinkle" | "gradient"}
    GET  /healthz         liveness check
    GET  /state           current on/color/brightness/effect
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from controller import LightsController

MAX_BODY_BYTES = 1024

lights = LightsController()
# The NeoPixel driver is not thread-safe; serialize every hardware call.
lights_lock = threading.Lock()


class LightsHandler(BaseHTTPRequestHandler):
    server_version = "LightsServer/2.0"

    def do_GET(self):
        if self.path == "/switch":
            with lights_lock:
                lights.light_switch()
            self._reply(200, message="turned on" if lights.on else "turned off", on=lights.on)
        elif self.path == "/healthz":
            self._reply(200, status="ok")
        elif self.path == "/state":
            self._reply(200, **lights.state())
        else:
            self._reply(404, error="not found")

    def do_POST(self):
        routes = {
            "/set_color": ("color", lights.set_light_color, "color set"),
            "/set_brightness": ("brightness", lights.set_brightness, "brightness set"),
            "/set_effect": ("effect", lights.set_effect, "effect set"),
        }
        if self.path not in routes:
            self._reply(404, error="not found")
            return
        field, action, message = routes[self.path]

        data = self._read_json()
        if not isinstance(data, dict) or field not in data:
            self._reply(400, error=f"missing {field}")
            return
        try:
            with lights_lock:
                action(data[field])
        except (TypeError, ValueError) as exc:
            self._reply(400, error=str(exc))
            return
        self._reply(200, message=message)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY_BYTES:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            return None

    def _reply(self, code, **body):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        # Keep the journal quiet: only log errors.
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(format, *args)


def main():
    port = int(os.environ.get("LIGHTS_PORT", "5000"))
    threading.Thread(target=lights.run_effects, args=(lights_lock,), daemon=True).start()
    httpd = HTTPServer(("0.0.0.0", port), LightsHandler)
    print(f"Lights server listening on :{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
