# Piano LED Visualizer 2.0

LED-Visualizer für Digitalpianos auf dem Raspberry Pi: Über jeder gedrückten Taste leuchtet
die passende LED. Komplett neu aufgebaut mit Smartphone-Bedienung, WLAN-Hotspot-Fallback,
Transpose-Unterstützung und Selbstheilung gegen hängende LEDs.

Zielhardware: Raspberry Pi Zero 2 W, WS2812B-Streifen mit 144 LEDs/m (176 LEDs für 88 Tasten),
Piano per USB-MIDI (getestet gedacht für Roland RD-700NX), optional Waveshare 1,44"-LCD-Hat.

## Was neu ist

| Thema | Vorher | Jetzt |
|---|---|---|
| Bedienung | Nur LCD-Menü | Smartphone-App (PWA) unter `http://pianoled.local`, LCD optional |
| Stabilität | Busy-Loop, Threads schreiben parallel auf die LEDs, Fehler verschluckt | Ereignisgetrieben, ein Renderer-Thread besitzt den Strip, systemd-Watchdog + Auto-Restart |
| Hängende LEDs | Kein Schutz | Periodischer Full-Refresh, Stuck-Note-Timeout, "All Notes Off", Panic-Taste |
| Piano an/aus | Nur beim Start erkannt | Hot-Plug: verbindet automatisch neu |
| Transpose | Nicht vorhanden | +/- in der App, Kalibrierung per tiefster Taste, Pedal-Hack, optional automatisch per Roland-SysEx |
| WLAN | Nichts | Netz per App wählen, Hotspot `PianoLED` als Fallback, `pianoled.local` per mDNS |
| Boot | Volles Raspbian, rc.local | Raspberry Pi OS Lite, Dienst wartet nicht auf Netzwerk, unnötige Dienste aus |
| Strom | 50 % Helligkeit ohne Schutz | Leistungsbudget in mA, Frame wird automatisch gedimmt |
| Performance | 100 % CPU | Leerlauf nahe 0 %, numpy-Framebuffer, nur geänderte Pixel werden gesendet |

Alle Zusatzfunktionen (Synthesia, Aufnahme, MIDI-Wiedergabe, Pedal-Presets, Leerlauf-Animation)
sind eingebaut, aber standardmäßig **aus** und werden in der App unter „Extras“ aktiviert.

## Installation

1. Raspberry Pi OS **Lite** (Bookworm oder Trixie) mit dem Raspberry Pi Imager auf die SD-Karte
   schreiben. Im Imager WLAN, Benutzer und SSH eintragen.
2. Per SSH anmelden und ausführen:

   ```bash
   sudo apt-get install -y git
   git clone https://github.com/baeri777/Piano-LED-Visualizer.git
   cd Piano-LED-Visualizer
   sudo bash install/install.sh
   sudo reboot
   ```

3. Nach dem Neustart auf dem Handy `http://pianoled.local` öffnen (oder die IP des Pi).
   Im Browser „Zum Startbildschirm hinzufügen“ wählen, dann verhält sich die Seite wie eine App.

Ohne bekanntes WLAN startet nach etwa 45 Sekunden der Hotspot **PianoLED** (Passwort `pianoled123`).
Damit verbinden, `http://10.42.0.1` öffnen und unter „WLAN“ das Heimnetz eintragen.

## Verkabelung

- LED-Daten an **GPIO 18** (PWM), Masse von Netzteil und Pi verbinden.
- LEDs mit eigenem 5-V-Netzteil versorgen, Stromeinspeisung möglichst an beiden Enden.
- Empfehlung: Level-Shifter (3,3 V → 5 V) für die Datenleitung, sonst flackern einzelne Pixel.
- In der App unter „Strip → Strom“ das Netzteil-Limit eintragen (bei 3 A etwa 2500 mA).
  Der Visualizer dimmt dann automatisch, bevor die Spannung einbricht.
- Onboard-Audio wird vom Installer deaktiviert (Konflikt mit dem LED-Signal).

## Transpose

Sendet das Piano transponierte Noten (am RD-700NX „Transpose +2“), leuchten ohne Korrektur
die falschen LEDs. Drei Wege:

1. **App**: Unter „Transpose“ denselben Wert wie am Piano einstellen.
2. **Kalibrieren**: „Kalibrieren“ tippen, dann die tiefste Taste (A0) drücken. Fertig.
   Mit dem Pedal-Hack geht das ohne Handy: Soft-Pedal dreimal kurz treten, dann A0 drücken.
3. **Automatisch** (Roland): Wenn am Piano „Tx Edit Data“ aktiv ist und es beim Transponieren
   einen SysEx sendet, kann die App die Adresse lernen und folgt danach dem Piano.
   Der MIDI-Monitor unter „Extras“ zeigt, ob so eine Nachricht ankommt.

Prüfe außerdem, ob das RD-700NX Transpose nur auf den Klang und nicht auf MIDI OUT anwenden kann.
Dann ist gar keine Korrektur nötig.

## Strip einrichten

Unter „Strip → Testen“ leuchten mit „A0 · C4 · C8“ die LEDs über der tiefsten Taste, dem
mittleren C und der höchsten Taste. Passt es nicht: Versatz und LED-Abstand anpassen, bei
gespiegeltem Streifen „Richtung umkehren“. „Rot/Grün/Blau“ prüft die Farbreihenfolge.

## LCD-Hat

Statusseite mit WLAN, IP, Piano-Verbindung, Transpose und Helligkeit.
Joystick links/rechts: Helligkeit, hoch/runter: Transpose. KEY1 öffnet das Menü
(Preset, Kalibrieren, Hotspot, Neustart, Ausschalten), KEY2 zurück, KEY3 alle LEDs aus.
Das Display schaltet sich nach einigen Minuten ab und wacht per Tastendruck auf.

## Entwicklung ohne Pi

```bash
pip install -r requirements-dev.txt
python -m pianoled --simulate --port 8080     # Web-UI mit simuliertem LED-Streifen
python -m pytest
```

Im Simulationsmodus zeigt die Startseite den Streifen als Farbbalken. MIDI wird per
`python-rtmidi` erkannt, sobald ein Gerät angeschlossen ist.

## Aufbau

```
pianoled/
  config.py        Defaults, Validierung, atomares Speichern (config.json)
  keymap.py        Note → LED (physikalisches Modell, Transpose, Richtung, Versatz)
  state.py         Tastenzustand, Sustain, Stuck-Timeout
  renderer.py      Render-Thread mit festem Takt, Effekte, Testbilder, Leerlauf-Animation
  leds.py          ws281x-Treiber, Helligkeit, Leistungsbegrenzung, Gamma
  midi_input.py    MIDI per Callback, Hot-Plug
  transpose.py     Kalibrierung, Pedal-Trigger, Roland-SysEx-Lernfunktion
  app.py           Verdrahtung, Presets, Ereignisse
  web/             aiohttp-Server, REST + WebSocket, PWA (static/)
  lcd/             ST7735-Treiber und LCD-Bedienung
  system/          NetworkManager (nmcli), Systeminfo, Neustart/Update
install/           install.sh, systemd-Unit, optional Bluetooth-MIDI
```

Dienst-Protokoll: `journalctl -u pianoled -f`. Update über die App („System → Update“)
oder `cd /opt/pianoled && git pull && sudo systemctl restart pianoled`.

## Lizenz

MIT, siehe LICENSE. Ursprung: [onlaj/Piano-LED-Visualizer](https://github.com/onlaj/Piano-LED-Visualizer).
