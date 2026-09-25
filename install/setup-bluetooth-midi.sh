#!/usr/bin/env bash
# Optional: Raspberry Pi als Bluetooth-LE-MIDI-Gerät (für Synthesia o. ä.). Experimentell.
# Nutzt bluez-alsa/bluez mit MIDI-Profil, wenn verfügbar. Aufruf: sudo bash install/setup-bluetooth-midi.sh
set -euo pipefail
apt-get install -y --no-install-recommends bluez bluez-tools
systemctl enable --now bluetooth.service hciuart.service || true
if ! command -v btmidi-server >/dev/null 2>&1; then
  echo "btmidi-server nicht in den Paketen. Anleitung: https://neuma.studio/rpi-as-midi-host.html"
  exit 0
fi
cat > /etc/systemd/system/btmidi.service <<'EOC'
[Unit]
Description=Bluetooth MIDI Server
After=bluetooth.service
Requires=bluetooth.service
[Service]
ExecStart=/usr/bin/btmidi-server -v -n "PianoLED"
Restart=always
[Install]
WantedBy=multi-user.target
EOC
systemctl daemon-reload
systemctl enable --now btmidi.service
echo "Bluetooth-MIDI aktiv. Der Port erscheint im Visualizer als eigener MIDI-Eingang (Port-Filter unter System)."
