#!/bin/sh
# Install the DNS-SD announcement for this board. Run with sudo.
set -e
ID=$(tr -d ':' < /sys/class/net/wlan0/address | tail -c 5 | tr 'a-f' 'A-F')
sed "s/@ID@/$ID/g" "$(dirname "$0")/lights.service.in" > /etc/avahi/services/lights.service
chmod 644 /etc/avahi/services/lights.service
echo "announcing as 'Lights $ID' (_lights._tcp, port 5000)"
