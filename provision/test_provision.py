import http.client
import threading
import unittest
from urllib.parse import urlencode

import provision


class FakeNetwork:
    def __init__(self, connected=False, good_password="correct-horse"):
        self.connected = connected
        self.good_password = good_password
        self.profiles = {"Home": "old-password"}
        self.events = []

    def device_id(self):
        return "4F2A"

    def is_connected(self):
        return self.connected

    def scan(self):
        return [{"ssid": "Home", "signal": 80, "secure": True}, {"ssid": "Cafe <b>", "signal": 40, "secure": False}]

    def start_hotspot(self, ssid, password):
        self.events.append(("hotspot", ssid))

    def stop_hotspot(self):
        self.events.append(("stop",))

    def save_network(self, ssid, password):
        self.events.append(("save", ssid))
        if ssid in self.profiles and not password:
            return ssid, provision.KEEP_SAVED
        previous = self.profiles.get(ssid)
        self.profiles[ssid] = password
        return ssid, previous

    def connect(self, name):
        ok = self.profiles.get(name) == self.good_password
        self.connected = ok
        self.events.append(("connect", name, ok))
        return ok

    def restore_network(self, name, previous_password):
        if previous_password is provision.KEEP_SAVED:
            self.events.append(("restore", name))
            return
        if previous_password is None:
            del self.profiles[name]
        else:
            self.profiles[name] = previous_password
        self.events.append(("restore", name))

    def let_autoconnect_try(self):
        self.events.append(("autoconnect",))


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class StopLoop(Exception):
    pass


def make(network):
    clock = FakeClock()
    return provision.Provisioner(network, clock=clock, sleep=clock.sleep), clock


class ProvisionerTest(unittest.TestCase):

    def test_hotspot_name_uses_device_id(self):
        p, _ = make(FakeNetwork())
        self.assertEqual("Lights-Setup-4F2A", p.ssid)

    def test_connected_board_never_opens_hotspot(self):
        net = FakeNetwork(connected=True)
        p, clock = make(net)
        def sleep(s):
            clock.now += s
            if clock.now > 3600:
                raise StopLoop
        p.sleep = sleep
        with self.assertRaises(StopLoop):
            p.run()
        self.assertNotIn(("hotspot", p.ssid), net.events)

    def test_no_network_at_boot_opens_hotspot_after_grace(self):
        net = FakeNetwork(connected=False)
        p, clock = make(net)
        p.wait_for_connection(provision.BOOT_GRACE_S)
        p.open_hotspot()
        self.assertGreaterEqual(clock.now, provision.BOOT_GRACE_S)
        self.assertTrue(p.hotspot_up)
        self.assertEqual("Home", p.networks[0]["ssid"])

    def test_short_outage_does_not_open_hotspot_but_long_one_does(self):
        net = FakeNetwork(connected=True)
        p, clock = make(net)
        def sleep(s):
            clock.now += s
            if clock.now == 100:
                net.connected = False          # router reboots...
            if clock.now == 150:
                net.connected = True           # ...and is back within the grace period
            if clock.now == 400:
                net.connected = False          # real outage
            if clock.now > 400 + provision.OFFLINE_GRACE_S + 10:
                raise StopLoop
        p.sleep = sleep
        with self.assertRaises(StopLoop):
            p.run()
        hotspots = [e for e in net.events if e[0] == "hotspot"]
        self.assertEqual(1, len(hotspots))

    def test_correct_password_joins_and_closes_hotspot(self):
        net = FakeNetwork()
        p, _ = make(net)
        p.open_hotspot()
        connected = []
        p.on_connected = lambda: connected.append(True)

        self.assertTrue(p.submit("Home", "correct-horse"))

        self.assertFalse(p.hotspot_up)
        self.assertIsNone(p.last_error)
        self.assertEqual([True], connected)
        self.assertEqual("correct-horse", net.profiles["Home"])

    def test_wrong_password_restores_old_profile_and_reopens_hotspot(self):
        net = FakeNetwork()
        p, _ = make(net)
        p.open_hotspot()

        self.assertFalse(p.submit("Home", "wrong-password"))

        self.assertTrue(p.hotspot_up)
        self.assertIn("wachtwoord", p.last_error)
        self.assertEqual("old-password", net.profiles["Home"])  # old working password kept

    def test_wrong_password_for_new_network_deletes_the_profile(self):
        net = FakeNetwork()
        p, _ = make(net)
        p.open_hotspot()

        self.assertFalse(p.submit("Neighbour", "whatever123"))

        self.assertNotIn("Neighbour", net.profiles)

    def test_known_network_with_empty_password_keeps_saved_password(self):
        net = FakeNetwork(good_password="old-password")
        p, _ = make(net)
        p.open_hotspot()

        self.assertTrue(p.submit("Home", ""))

        self.assertEqual("old-password", net.profiles["Home"])

    def test_failed_reconnect_with_saved_password_leaves_profile_alone(self):
        net = FakeNetwork(good_password="something-else")
        p, _ = make(net)
        p.open_hotspot()

        self.assertFalse(p.submit("Home", ""))

        self.assertEqual("old-password", net.profiles["Home"])
        self.assertTrue(p.hotspot_up)

    def test_hotspot_is_not_opened_once_a_submission_has_connected(self):
        net = FakeNetwork(connected=True)
        p, _ = make(net)

        p.open_hotspot()  # e.g. the loop decided "offline" just before a submit connected

        self.assertFalse(p.hotspot_up)
        self.assertNotIn(("hotspot", p.ssid), net.events)

    def test_invalid_password_length_is_rejected_without_touching_wifi(self):
        net = FakeNetwork()
        p, _ = make(net)
        p.open_hotspot()
        before = list(net.events)

        self.assertFalse(p.submit("Home", "short"))

        self.assertEqual(before, net.events)
        self.assertTrue(p.hotspot_up)

    def test_hotspot_steps_aside_periodically_so_saved_networks_can_return(self):
        net = FakeNetwork(connected=False)
        p, clock = make(net)
        def sleep(s):
            clock.now += s
            if ("autoconnect",) in net.events:
                net.connected = True   # router came back
            if clock.now > provision.BOOT_GRACE_S + provision.AP_RETRY_S + 200:
                raise StopLoop
        p.sleep = sleep
        with self.assertRaises(StopLoop):
            p.run()
        self.assertIn(("autoconnect",), net.events)
        self.assertFalse(p.hotspot_up)


class PortalTest(unittest.TestCase):

    def setUp(self):
        self.net = FakeNetwork()
        self.p, _ = make(self.net)
        self.p.open_hotspot()
        self.httpd = provision.serve_portal(self.p, host="127.0.0.1", port=0)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/x-www-form-urlencoded"} if body else {}
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.getheader("Location"), resp.read().decode()

    def test_setup_page_lists_networks_escaped(self):
        status, _, page = self.request("GET", "/")
        self.assertEqual(200, status)
        self.assertIn("Lights-Setup-4F2A", page)
        self.assertIn('<option value="Home">Home</option>', page)
        self.assertIn("Cafe &lt;b&gt; (open)", page)
        self.assertNotIn("Cafe <b>", page)

    def test_phone_connectivity_checks_redirect_to_portal(self):
        for path in ("/generate_204", "/hotspot-detect.html", "/connecttest.txt"):
            status, location, _ = self.request("GET", path)
            self.assertEqual(302, status, path)
            self.assertEqual("http://10.42.0.1/", location)

    def test_connect_from_home_lan_is_refused(self):
        # The test client is 127.0.0.1, i.e. not a hotspot client.
        status, _, _ = self.request("POST", "/connect", urlencode({"ssid": "Home", "password": "correct-horse"}))
        self.assertEqual(403, status)
        self.assertNotIn(("save", "Home"), self.net.events)

    def test_connect_from_hotspot_client_starts_joining(self):
        joined = threading.Event()
        original = self.p.submit
        def submit(ssid, password):
            result = original(ssid, password)
            joined.set()
            return result
        self.p.submit = submit
        handler = self.httpd.RequestHandlerClass
        original_init = handler.__init__
        def as_hotspot_client(self_, request, client_address, server):
            original_init(self_, request, ("10.42.0.23", client_address[1]), server)
        handler.__init__ = as_hotspot_client
        try:
            status, _, page = self.request("POST", "/connect", urlencode({"ssid": "Home", "password": "correct-horse"}))
        finally:
            handler.__init__ = original_init

        self.assertEqual(200, status)
        self.assertIn("Verbinden met Home", page)
        self.assertTrue(joined.wait(5))
        self.assertTrue(self.net.connected)


if __name__ == "__main__":
    unittest.main()
