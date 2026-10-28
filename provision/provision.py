"""Headless Wi-Fi setup for the lights boards.

When the board has no Wi-Fi connection it opens a setup hotspot
("Lights-Setup-XXXX"). Joining it from a phone pops up a captive portal page
where you pick your network and enter its password. The board then joins that
network and turns the hotspot off. If the connection is lost later, the
hotspot comes back; it also periodically steps aside so a router that was only
briefly down gets reconnected to automatically.

Runs as root under systemd and drives NetworkManager through nmcli. Standard
library only.
"""
import html
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

IFACE = os.environ.get("PROVISION_IFACE", "wlan0")
AP_PASSWORD = os.environ.get("PROVISION_AP_PASSWORD", "lightsetup")
AP_CONNECTION = "lights-setup"
AP_ADDRESS = "10.42.0.1"
PORTAL_PORT = int(os.environ.get("PROVISION_PORTAL_PORT", "80"))
BOOT_GRACE_S = int(os.environ.get("PROVISION_BOOT_GRACE", "90"))
OFFLINE_GRACE_S = int(os.environ.get("PROVISION_OFFLINE_GRACE", "120"))
AP_RETRY_S = int(os.environ.get("PROVISION_AP_RETRY", "600"))
CONNECT_TIMEOUT_S = 30
POLL_S = 5
KEEP_SAVED = object()  # save_network left the saved password untouched
FORCE_AP_FLAG = "/run/lights-provision/force-ap"


def log(message):
    print(f"[provision] {message}", flush=True)


class NetworkManager:
    """Thin nmcli wrapper; the only part that touches the system."""

    def __init__(self, iface=IFACE, run=subprocess.run):
        self.iface = iface
        self._run = run

    def _nmcli(self, *args, check=True, timeout=60):
        result = self._run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"nmcli {args[0]} failed")
        return result.stdout

    def device_id(self):
        with open(f"/sys/class/net/{self.iface}/address") as f:
            return f.read().strip().replace(":", "")[-4:].upper()

    def is_connected(self):
        """True when the interface is connected to a network other than our hotspot."""
        out = self._nmcli("-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION", "device", "show", self.iface, check=False)
        fields = dict(line.split(":", 1) for line in out.splitlines() if ":" in line)
        return fields.get("GENERAL.STATE", "").startswith("100") and fields.get("GENERAL.CONNECTION") != AP_CONNECTION

    def scan(self):
        out = self._nmcli("-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                          "ifname", self.iface, "--rescan", "yes", check=False)
        networks = {}
        for line in out.splitlines():
            # nmcli escapes ':' inside fields as '\:'
            parts = line.replace("\\:", "\0").split(":")
            if len(parts) != 3 or not parts[0]:
                continue
            ssid, signal, security = (p.replace("\0", ":") for p in parts)
            signal = int(signal) if signal.isdigit() else 0
            if ssid not in networks or networks[ssid]["signal"] < signal:
                networks[ssid] = {"ssid": ssid, "signal": signal, "secure": security not in ("", "--")}
        return sorted(networks.values(), key=lambda n: -n["signal"])

    def start_hotspot(self, ssid, password):
        self._nmcli("connection", "delete", AP_CONNECTION, check=False)
        self._nmcli("connection", "add", "type", "wifi", "ifname", self.iface, "con-name", AP_CONNECTION,
                    "autoconnect", "no", "ssid", ssid, "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
                    "ipv4.method", "shared", "ipv4.addresses", f"{AP_ADDRESS}/24", "ipv6.method", "disabled",
                    "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.proto", "rsn",
                    "wifi-sec.pairwise", "ccmp", "wifi-sec.group", "ccmp", "wifi-sec.psk", password)
        self._nmcli("connection", "up", AP_CONNECTION)

    def stop_hotspot(self):
        self._nmcli("connection", "down", AP_CONNECTION, check=False)

    def saved_profiles(self):
        """Saved Wi-Fi profiles other than the hotspot, most recently used first."""
        out = self._nmcli("-t", "-f", "NAME,TYPE,TIMESTAMP", "connection", "show", check=False)
        profiles = []
        for line in out.splitlines():
            name, kind, stamp = (line.rsplit(":", 2) + ["", ""])[:3]
            if kind == "802-11-wireless" and name != AP_CONNECTION:
                profiles.append((int(stamp) if stamp.isdigit() else 0, name))
        return [name for _, name in sorted(profiles, reverse=True)]

    def saved_profile(self, ssid):
        for name in self.saved_profiles():
            profile_ssid = self._nmcli("-t", "-g", "802-11-wireless.ssid", "connection", "show", name, check=False).strip()
            if profile_ssid == ssid:
                return name
        return None

    def profile_password(self, name):
        return self._nmcli("-s", "-g", "802-11-wireless-security.psk", "connection", "show", name, check=False).strip()

    def save_network(self, ssid, password):
        """Create or update the profile for ssid.

        Returns (name, previous) where previous is the old password, None for a new
        profile, or KEEP_SAVED when an empty password re-uses the saved one.
        """
        name = self.saved_profile(ssid)
        if name:
            if not password:
                self._nmcli("connection", "modify", name, "connection.autoconnect", "yes")
                return name, KEEP_SAVED
            previous = self.profile_password(name)
            self._nmcli("connection", "modify", name, "connection.autoconnect", "yes",
                        "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password)
            return name, previous
        security = ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password] if password else []
        self._nmcli("connection", "add", "type", "wifi", "ifname", self.iface, "con-name", ssid,
                    "ssid", ssid, "connection.autoconnect", "yes", *security)
        return ssid, None

    def connect(self, name):
        try:
            self._nmcli("--wait", str(CONNECT_TIMEOUT_S), "connection", "up", name, timeout=CONNECT_TIMEOUT_S + 15)
            return True
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            log(f"connecting to {name} failed: {exc}")
            return False

    def restore_network(self, name, previous_password):
        if previous_password is KEEP_SAVED:
            return
        if previous_password is None:
            self._nmcli("connection", "delete", name, check=False)
        else:
            self._nmcli("connection", "modify", name, "wifi-sec.psk", previous_password, check=False)

    def let_autoconnect_try(self):
        """Try the saved networks again. (Not "nmcli device connect": that re-activates
        the most recently used profile, which is usually the hotspot itself.)"""
        for name in self.saved_profiles():
            if self.connect(name):
                return


class Provisioner:
    """State machine: online -> (offline too long) -> hotspot -> (credentials) -> online."""

    def __init__(self, network, clock=time.monotonic, sleep=time.sleep):
        self.network = network
        self.clock = clock
        self.sleep = sleep
        self.ssid = f"Lights-Setup-{network.device_id()}"
        self.networks = []
        self.last_error = None
        self.hotspot_up = False
        self.lock = threading.Lock()
        self.on_connected = lambda: None
        self.force_ap = lambda: False

    def wait_for_connection(self, seconds):
        deadline = self.clock() + seconds
        while self.clock() < deadline:
            if self.network.is_connected():
                return True
            self.sleep(POLL_S)
        return self.network.is_connected()

    def open_hotspot(self):
        with self.lock:
            # Re-check under the lock: a portal submission may have just connected.
            if self.network.is_connected() and not self.force_ap():
                return
            log("no Wi-Fi connection, scanning and opening hotspot " + self.ssid)
            # A hotspot left over from a previous run would make the scan come back empty.
            self.network.stop_hotspot()
            self.networks = self.network.scan()
            self.network.start_hotspot(self.ssid, AP_PASSWORD)
            self.hotspot_up = True

    def close_hotspot(self):
        with self.lock:
            self.network.stop_hotspot()
            self.hotspot_up = False

    def submit(self, ssid, password):
        """Called from the portal. Returns True when the board joined the network."""
        if not ssid:
            self.last_error = "Kies een netwerk"
            return False
        if password and not 8 <= len(password) <= 63:
            self.last_error = "Een Wi-Fi-wachtwoord is 8 tot 63 tekens"
            return False
        with self.lock:
            log(f"trying network {ssid!r}")
            self.network.stop_hotspot()
            self.hotspot_up = False
            name, previous = self.network.save_network(ssid, password)
            if self.network.connect(name):
                log(f"connected to {ssid!r}")
                self.last_error = None
                self.on_connected()
                return True
            self.network.restore_network(name, previous)
            self.last_error = f"Kon niet verbinden met {ssid}. Klopt het wachtwoord?"
            self.network.start_hotspot(self.ssid, AP_PASSWORD)
            self.hotspot_up = True
            return False

    def run(self, force_ap=lambda: False):
        self.force_ap = force_ap
        if not force_ap():
            # A hotspot left over from a previous run would block saved networks.
            self.network.stop_hotspot()
            self.network.let_autoconnect_try()
        if force_ap() or not self.wait_for_connection(BOOT_GRACE_S):
            self.open_hotspot()
        offline_since = None
        hotspot_since = self.clock() if self.hotspot_up else None
        while True:
            self.sleep(POLL_S)
            if self.lock.locked():
                continue  # a setup attempt from the portal is switching networks
            if self.hotspot_up:
                if self.clock() - hotspot_since >= AP_RETRY_S and not force_ap():
                    log("no setup yet, letting saved networks retry")
                    self.close_hotspot()
                    self.network.let_autoconnect_try()
                    if not self.wait_for_connection(60):
                        self.open_hotspot()
                    hotspot_since = self.clock()
                continue
            if self.network.is_connected() and not force_ap():
                offline_since = None
                continue
            offline_since = offline_since or self.clock()
            if force_ap() or self.clock() - offline_since >= OFFLINE_GRACE_S:
                self.open_hotspot()
                hotspot_since = self.clock()
                offline_since = None


PAGE = """<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Lights instellen</title>
<style>
body{{margin:0;font:16px system-ui,sans-serif;background:#10161f;color:#d4dfea;display:flex;justify-content:center}}
main{{width:100%;max-width:420px;padding:28px 20px}}
h1{{font:600 26px Georgia,serif;margin:0 0 4px}} p{{color:#9fb0c3;margin:0 0 20px}}
label{{display:block;font-weight:600;margin:16px 0 6px}}
select,input{{width:100%;box-sizing:border-box;padding:12px;border-radius:12px;border:1px solid #273347;background:#192230;color:inherit;font:inherit}}
button{{width:100%;margin-top:22px;padding:14px;border:0;border-radius:999px;background:#ff6b3d;color:#fff;font:600 16px system-ui}}
.err{{background:#e2564f22;border:1px solid #e2564f66;color:#ffb3ae;padding:12px;border-radius:12px;margin-bottom:8px}}
small{{display:block;margin-top:18px;color:#6d7f93}}
</style></head><body><main>
<h1>Lights instellen</h1><p>Kies je wifi-netwerk zodat {device} online kan.</p>
{error}
<form method="post" action="/connect">
<label for="ssid">Netwerk</label>
<select id="ssid" name="ssid">{options}<option value="">Ander netwerk…</option></select>
<input name="other" placeholder="Netwerknaam (bij ander netwerk)" style="margin-top:8px">
<label for="pw">Wachtwoord <span style="font-weight:400;color:#6d7f93">(leeg = opgeslagen wachtwoord)</span></label>
<input id="pw" name="password" type="password" autocomplete="current-password">
<button type="submit">Verbinden</button>
</form>
<small>Na verbinden verdwijnt het netwerk {device}. Komt het terug, dan klopte het wachtwoord niet.</small>
</main></body></html>"""

CONNECTING = """<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Verbinden…</title>
<style>body{{margin:0;font:16px system-ui,sans-serif;background:#10161f;color:#d4dfea;padding:28px 20px}}</style></head>
<body><h1>Verbinden met {ssid}…</h1><p>Je telefoon verbreekt zo de verbinding met dit setup-netwerk.
Het licht verschijnt daarna in de app. Komt <b>{device}</b> terug, verbind dan opnieuw en controleer het wachtwoord.</p></body></html>"""


def make_portal_handler(provisioner):
    class PortalHandler(BaseHTTPRequestHandler):
        server_version = "LightsSetup/1.0"

        def do_GET(self):
            if self.path == "/":
                self._send(200, self._page())
            elif self.path == "/status":
                body = json.dumps({"hotspot": provisioner.hotspot_up, "error": provisioner.last_error,
                                   "networks": provisioner.networks})
                self._send(200, body, "application/json")
            else:
                # Every other URL (phones' connectivity checks included) goes to the
                # setup page, which makes the phone show its "sign in to network" prompt.
                self.send_response(302)
                self.send_header("Location", f"http://{AP_ADDRESS}/")
                self.send_header("Content-Length", "0")
                self.end_headers()

        def do_POST(self):
            if self.path != "/connect":
                self._send(404, "not found", "text/plain")
                return
            # Only hotspot clients may change Wi-Fi settings, never the home LAN.
            if not provisioner.hotspot_up or not self.client_address[0].startswith("10.42.0."):
                self._send(403, "setup is only available on the setup hotspot", "text/plain")
                return
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
            ssid = (form.get("other", [""])[0].strip() or form.get("ssid", [""])[0]).strip()
            password = form.get("password", [""])[0]
            if not ssid or (password and not 8 <= len(password) <= 63):
                provisioner.last_error = "Kies een netwerk" if not ssid else "Een Wi-Fi-wachtwoord is 8 tot 63 tekens"
                self._send(200, self._page())
                return
            # Answer first: switching networks drops the phone off the hotspot.
            self._send(200, CONNECTING.format(ssid=html.escape(ssid), device=html.escape(provisioner.ssid)))
            threading.Thread(target=provisioner.submit, args=(ssid, password), daemon=True).start()

        def _page(self):
            options = "".join(
                f'<option value="{html.escape(n["ssid"], quote=True)}">{html.escape(n["ssid"])}'
                f'{"" if n["secure"] else " (open)"}</option>'
                for n in provisioner.networks
            )
            error = f'<div class="err">{html.escape(provisioner.last_error)}</div>' if provisioner.last_error else ""
            return PAGE.format(device=html.escape(provisioner.ssid), options=options, error=error)

        def _send(self, code, body, content_type="text/html; charset=utf-8"):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass

    return PortalHandler


def serve_portal(provisioner, host="0.0.0.0", port=PORTAL_PORT):
    httpd = HTTPServer((host, port), make_portal_handler(provisioner))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    provisioner = Provisioner(NetworkManager())
    provisioner.on_connected = lambda: os.path.exists(FORCE_AP_FLAG) and os.remove(FORCE_AP_FLAG)
    # Listen on all addresses; only hotspot clients reach port 80 in practice, and
    # binding before the hotspot exists means the portal is ready the moment it is.
    serve_portal(provisioner)
    log(f"portal on :{PORTAL_PORT}, hotspot name {provisioner.ssid}")
    provisioner.run(force_ap=lambda: os.path.exists(FORCE_AP_FLAG))


if __name__ == "__main__":
    main()
