#!/usr/bin/env bash
# Piano LED Visualizer – Installation auf frischem Raspberry Pi OS Lite (Bookworm/Trixie).
#
#   sudo bash install/install.sh
#
# Optionen (Umgebungsvariablen):
#   INSTALL_DIR=/opt/pianoled    Zielordner
#   PI_HOSTNAME=pianoled         Name im Netz (→ http://pianoled.local)
#   SKIP_BOOT_TUNING=1           Boot-Optimierungen auslassen
#   WITH_BLUETOOTH=1             Bluetooth nicht abschalten (für Bluetooth-MIDI)
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/pianoled}"
PI_HOSTNAME="${PI_HOSTNAME:-pianoled}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_TXT=/boot/firmware/config.txt
[[ -f $CONFIG_TXT ]] || CONFIG_TXT=/boot/config.txt

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
set_config_txt() {   # Zeile in config.txt setzen oder ersetzen
  local key="${1%%=*}"
  if grep -qE "^#?${key}=" "$CONFIG_TXT"; then sed -i -E "s|^#?${key}=.*|$1|" "$CONFIG_TXT"; else echo "$1" >> "$CONFIG_TXT"; fi
}

[[ $EUID -eq 0 ]] || { echo "Bitte mit sudo ausführen."; exit 1; }

step "Pakete installieren"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends \
  git rsync python3 python3-venv python3-dev python3-numpy python3-pil python3-aiohttp \
  python3-spidev python3-gpiozero python3-lgpio python3-qrcode \
  build-essential libasound2-dev libjack-jackd2-dev \
  network-manager dnsmasq-base avahi-daemon iw fonts-dejavu-core

step "Programm nach $INSTALL_DIR kopieren"
mkdir -p "$INSTALL_DIR"
if [[ "$SRC_DIR" != "$INSTALL_DIR" ]]; then
  # .git wird mitkopiert, damit „Update installieren“ in der App funktioniert
  rsync -a --delete --exclude venv --exclude config.json --exclude 'Songs/*.mid' "$SRC_DIR"/ "$INSTALL_DIR"/
fi
mkdir -p "$INSTALL_DIR/Songs"
cp -n "$SRC_DIR"/Songs/*.mid "$INSTALL_DIR/Songs/" 2>/dev/null || true

step "Python-Umgebung"
[[ -x "$INSTALL_DIR/venv/bin/python" ]] || python3 -m venv --system-site-packages "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip wheel
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
"$INSTALL_DIR/venv/bin/python" -m compileall -q "$INSTALL_DIR/pianoled"   # schnellerer Start

step "Hardware: SPI an, Onboard-Audio aus (stört das LED-Signal), Hardware-Watchdog an"
set_config_txt "dtparam=spi=on"
set_config_txt "dtparam=audio=off"
set_config_txt "dtparam=watchdog=on"
echo "blacklist snd_bcm2835" > /etc/modprobe.d/pianoled-no-audio.conf
mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/pianoled-watchdog.conf <<'EOC'
# Hängt das ganze System, startet der Hardware-Watchdog den Pi neu.
[Manager]
RuntimeWatchdogSec=15
RebootWatchdogSec=2min
EOC

step "SD-Karte schonen: Systemprotokoll im RAM"
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/pianoled.conf <<'EOC'
[Journal]
Storage=volatile
RuntimeMaxUse=24M
EOC

if [[ -z "${SKIP_BOOT_TUNING:-}" ]]; then
  step "Boot beschleunigen"
  set_config_txt "disable_splash=1"
  set_config_txt "boot_delay=0"
  for svc in triggerhappy ModemManager apt-daily.timer apt-daily-upgrade.timer man-db.timer \
             dphys-swapfile rpi-eeprom-update cups cups-browsed; do
    systemctl disable --now "$svc" >/dev/null 2>&1 || true
  done
  [[ -n "${WITH_BLUETOOTH:-}" ]] || systemctl disable --now bluetooth.service hciuart.service >/dev/null 2>&1 || true
  systemctl disable NetworkManager-wait-online.service >/dev/null 2>&1 || true
fi

step "WLAN: NetworkManager + Captive Portal für den Hotspot"
systemctl enable --now NetworkManager >/dev/null 2>&1 || true
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
cat > /etc/NetworkManager/dnsmasq-shared.d/pianoled-captive.conf <<'EOC'
# Im Hotspot beantwortet der Pi alle DNS-Anfragen selbst. Handys erkennen dadurch ein
# „Captive Portal“ und öffnen automatisch die WLAN-Einrichtung.
address=/#/10.42.0.1
EOC
# WLAN-Ländercode setzen, sonst bleibt das Funkmodul teils gesperrt
command -v raspi-config >/dev/null && raspi-config nonint do_wifi_country "${WIFI_COUNTRY:-DE}" >/dev/null 2>&1 || true
rfkill unblock wifi 2>/dev/null || true

step "Name im Netz: $PI_HOSTNAME.local"
if [[ "$(hostname)" != "$PI_HOSTNAME" ]]; then
  hostnamectl set-hostname "$PI_HOSTNAME"
  if grep -q '^127.0.1.1' /etc/hosts; then sed -i "s/^127.0.1.1.*/127.0.1.1\t$PI_HOSTNAME/" /etc/hosts
  else echo -e "127.0.1.1\t$PI_HOSTNAME" >> /etc/hosts; fi
fi
systemctl enable --now avahi-daemon >/dev/null 2>&1 || true

step "Dienst einrichten"
sed "s#/opt/pianoled#$INSTALL_DIR#g" "$INSTALL_DIR/install/pianoled.service" > /etc/systemd/system/pianoled.service
systemctl daemon-reload
systemctl enable pianoled.service
systemctl restart pianoled.service

if [[ -n "${WITH_BLUETOOTH:-}" ]]; then
  bash "$INSTALL_DIR/install/setup-bluetooth-midi.sh" || echo "Bluetooth-MIDI-Einrichtung fehlgeschlagen (optional)."
fi

cat <<EOM

Fertig!
  • Neustart nötig, damit Audio-Abschaltung, SPI und Watchdog greifen:  sudo reboot
  • Danach auf dem Handy öffnen:  http://$PI_HOSTNAME.local
  • Ohne bekanntes WLAN öffnet der Visualizer den Hotspot „PianoLED“ (Passwort pianoled123).
    Name, Passwort und Adresse stehen auf dem Display.
  • Protokoll ansehen:  journalctl -u pianoled -f
EOM
