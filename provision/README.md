# Headless Wi-Fi setup

A board with no Wi-Fi opens a setup hotspot, `Lights-Setup-XXXX` (XXXX = end of
its Wi-Fi MAC), password `lightsetup`. Join it with a phone, the setup page pops
up, pick your network and enter its password. The board joins it and the
hotspot disappears. If it comes back, the password was wrong; the page then
shows the error.

- No connection 90 s after boot, or lost for 2 min → hotspot opens
- Hotspot up 10 min without setup → saved networks are retried (a router that
  was only rebooting gets reconnected), then the hotspot reopens
- Empty password for a network the board already knows = use the saved one
- Wrong password never overwrites a working saved one
- Settings are only accepted from hotspot clients (10.42.0.x), not the home LAN

Requires NetworkManager (Raspberry Pi OS bookworm/trixie) and `dnsmasq-base`.

## Install

```bash
sudo install -m 755 provision.py /opt/lights/provision.py
sudo install -m 644 lights-provision.service /etc/systemd/system/
sudo install -d /etc/NetworkManager/dnsmasq-shared.d
sudo install -m 644 lights-captive.conf /etc/NetworkManager/dnsmasq-shared.d/
sudo cp lights-provision.conf.example /etc/lights-provision.conf   # optional, e.g. own hotspot password
sudo systemctl daemon-reload && sudo systemctl enable --now lights-provision
```

## Test without losing access

Use a board that also has Ethernet, then force the hotspot:

```bash
sudo touch /run/lights-provision/force-ap   # cleared automatically after a successful setup
```

Unit tests: `python3 -m unittest test_provision`
