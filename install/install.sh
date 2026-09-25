#!/usr/bin/env bash
# Installation des Piano LED Visualizers auf einem frischen Raspberry Pi OS Lite
# (Bookworm oder Trixie, 32 oder 64 Bit). Aufruf als root:
#   sudo bash install/install.sh
# Optionen über Umgebungsvariablen:
#   INSTALL_DIR=/opt/pianoled   HOSTNAME=pianoled   SKIP_BOOT_TUNING=1   WITH_BLUETOOTH=1
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/pianoled}"
NEW_HOSTNAME="${HOSTNAME_OVERRIDE:-pianoled}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then echo "Bitte mit sudo ausführen."; exit 1; fi

echo "==> Pakete installieren"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  git python3 python3-venv python3-dev python3-numpy python3-pil python3-aiohttp \
  python3-spidev python3-gpiozero python3-lgpio \
  build-essential libasound2-dev libjack-jackd2-dev \
  network-manager avahi-daemon fonts-dejavu-core iw

echo "==> Programm nach $INSTALL_DIR kopieren"
mkdir -p "$INSTALL_DIR"
if [[ "$SRC_DIR" != "$INSTALL_DIR" ]]; then
  rsync -a --delete --exclude venv --exclude config.json --exclude '.git' "$SRC_DIR"/ "$INSTALL_DIR"/ 2>/dev/null || cp -r "$SRC_DIR"/. "$INSTALL_DIR"/
fi
mkdir -p "$INSTALL_DIR/Songs"

echo "==> Python-Umgebung"
if [[ ! -x "$INSTALL_DIR/venv/bin/python" ]]; then
  python3 -m venv --system-site-packages "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip wheel >/dev/null
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

echo "==> Hardware: SPI an, Onboard-Audio aus (Konflikt mit LED-PWM)"
CONFIG_TXT=/boot/firmware/config.txt
[[ -f $CONFIG_TXT ]] || CONFIG_TXT=/boot/config.txt
sed -i 's/^dtparam=audio=on/dtparam=audio=off/' "$CONFIG_TXT"
grep -q '^dtparam=spi=on' "$CONFIG_TXT" || echo 'dtparam=spi=on' >> "$CONFIG_TXT"
grep -q '^dtparam=audio=off' "$CONFIG_TXT" || echo 'dtparam=audio=off' >> "$CONFIG_TXT"
cat > /etc/modprobe.d/pianoled-blacklist.conf <<'EOC'
blacklist snd_bcm2835
EOC

if [[ -z "${SKIP_BOOT_TUNING:-}" ]]; then
  echo "==> Boot beschleunigen"
  grep -q '^disable_splash=1' "$CONFIG_TXT" || echo 'disable_splash=1' >> "$CONFIG_TXT"
  grep -q '^boot_delay=0' "$CONFIG_TXT" || echo 'boot_delay=0' >> "$CONFIG_TXT"
  for svc in triggerhappy ModemManager apt-daily.timer apt-daily-upgrade.timer man-db.timer \
             dphys-swapfile rpi-eeprom-update; do
    systemctl disable --now "$svc" 2>/dev/null || true
  done
  if [[ -z "${WITH_BLUETOOTH:-}" ]]; then
    systemctl disable --now bluetooth.service hciuart.service 2>/dev/null || true
  fi
  # Nicht auf Netzwerk warten
  systemctl disable NetworkManager-wait-online.service 2>/dev/null || true
fi

echo "==> Hostname: $NEW_HOSTNAME (erreichbar als $NEW_HOSTNAME.local)"
if [[ "$(hostname)" != "$NEW_HOSTNAME" ]]; then
  hostnamectl set-hostname "$NEW_HOSTNAME"
  sed -i "s/127.0.1.1.*/127.0.1.1\t$NEW_HOSTNAME/" /etc/hosts || echo -e "127.0.1.1\t$NEW_HOSTNAME" >> /etc/hosts
fi
systemctl enable --now avahi-daemon NetworkManager >/dev/null 2>&1 || true

echo "==> Dienst einrichten"
sed "s#/opt/pianoled#$INSTALL_DIR#g" "$INSTALL_DIR/install/pianoled.service" > /etc/systemd/system/pianoled.service
systemctl daemon-reload
systemctl enable pianoled.service
systemctl restart pianoled.service

if [[ -n "${WITH_BLUETOOTH:-}" ]]; then
  bash "$INSTALL_DIR/install/setup-bluetooth-midi.sh" || echo "Bluetooth-MIDI-Einrichtung fehlgeschlagen (optional)."
fi

echo
echo "Fertig. Weboberfläche: http://$NEW_HOSTNAME.local  (oder http://<IP des Pi>)"
echo "Ohne bekanntes WLAN startet nach ca. 45 s der Hotspot 'PianoLED' (Passwort: pianoled123)."
echo "Ein Neustart ist nötig, damit Audio-Abschaltung und SPI wirksam werden:  sudo reboot"
