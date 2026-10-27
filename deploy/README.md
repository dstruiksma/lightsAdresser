# Raspberry Pi Zero hardening

## Lightweight server (recommended)

`server.py` is a dependency-free replacement for the Flask app (`lights_api.py`)
with the same endpoints plus `GET /state`. It uses ~24 MB RAM. Deployed this way
on the living room Pi (192.168.178.227):

```bash
sudo mkdir -p /opt/lights && sudo cp server.py controller.py /opt/lights/
cd /opt/lights && sudo python3 -m venv .venv
sudo .venv/bin/pip install adafruit-circuitpython-neopixel rpi_ws281x adafruit-blinka "gpiod>=2.2.2,<3"
sudo cp deploy/systemd/lights-server.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now lights-server
```

GPIO18 shares the PWM block with onboard audio. Without the following the driver
reports success but the strip stays dark:

```bash
sudo sed -i 's/^dtparam=audio=on/dtparam=audio=off/' /boot/firmware/config.txt
echo "blacklist snd_bcm2835" | sudo tee /etc/modprobe.d/lights-no-onboard-audio.conf
sudo reboot
```

## Legacy service (lights_api.py, now a wrapper around server.py)

This project can run for long periods on a Pi 3, but a Pi Zero is much more memory constrained.
Use `systemd` to keep the API alive and optionally recycle it on a schedule.

## 1) Install service

Copy files:

```bash
sudo cp deploy/systemd/lightsadresser.service /etc/systemd/system/
sudo cp deploy/systemd/lightsadresser-restart.service /etc/systemd/system/
sudo cp deploy/systemd/lightsadresser-restart.timer /etc/systemd/system/
sudo cp deploy/systemd/pi-nightly-reboot.service /etc/systemd/system/
sudo cp deploy/systemd/pi-nightly-reboot.timer /etc/systemd/system/
```

Reload and enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lightsadresser.service
```

## 2) Choose recycle strategy

### Option A: Restart only the API every hour (recommended first)

```bash
sudo systemctl enable --now lightsadresser-restart.timer
```

### Option B: Reboot the whole Pi nightly (fallback)

```bash
sudo systemctl enable --now pi-nightly-reboot.timer
```

## 3) Verify

```bash
systemctl status lightsadresser.service
systemctl list-timers --all | grep -E 'lightsadresser|pi-nightly-reboot'
journalctl -u lightsadresser.service -n 100 --no-pager
```

## 4) Diagnose likely Pi Zero causes

```bash
dmesg -T | grep -i -E 'out of memory|oom'
vcgencmd get_throttled
free -h
```

- If OOM appears, reduce LED count/brightness or keep hourly restart enabled.
- If throttled, improve power supply/cable and cooling.
