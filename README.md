# Piano LED Visualizer

Über jeder Taste, die du spielst, leuchtet eine LED. Gesteuert wird alles bequem vom Handy.
Das System repariert sich bei Fehlern selbst, und die WLAN-Einrichtung läuft ohne Tastatur und Bildschirm.

**Hardware:** Raspberry Pi Zero 2 W · WS2812B-Streifen mit 144 LEDs/m (176 LEDs für 88 Tasten) ·
Piano per USB-MIDI (z. B. Roland RD-700NX) · optional Waveshare 1,44"-Display-Hat.

---

## Installation

1. **SD-Karte vorbereiten.** Mit dem [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
   „Raspberry Pi OS Lite (64-bit)“ aufspielen. In den Imager-Einstellungen SSH aktivieren.
   WLAN kannst du dort eintragen, musst du aber nicht, denn der Visualizer hat eine eigene Einrichtung.
2. **Installieren** (per SSH auf dem Pi):

   ```bash
   sudo apt-get install -y git
   git clone https://github.com/baeri777/Piano-LED-Visualizer.git
   sudo bash Piano-LED-Visualizer/install/install.sh
   sudo reboot
   ```

3. **Loslegen.** Auf dem Handy `http://pianoled.local` öffnen und im Browser-Menü
   „Zum Home-Bildschirm“ wählen. Dann startet die Steuerung wie eine App.

## WLAN einrichten

Kennt der Visualizer kein WLAN oder ist es nicht erreichbar, öffnet er den Hotspot **PianoLED**.

| So geht's | |
|---|---|
| 1. | Auf dem Display stehen Netzname, Passwort und Adresse. Joystick drücken zeigt einen **QR-Code**. Mit der Handykamera scannen, und das Handy verbindet sich sofort. |
| 2. | Das Handy öffnet von selbst die Einrichtungsseite (Captive Portal). Sonst `http://10.42.0.1` aufrufen. |
| 3. | Dein WLAN antippen, Passwort eingeben, „Verbinden“. |
| 4. | Das Display zeigt „WLAN verbunden“ und die neue IP-Adresse. Handy zurück ins Heim-WLAN, `http://pianoled.local` öffnen. |

Ist das Passwort falsch, kommt der Hotspot nach kurzer Zeit zurück, und die Seite nennt den Grund.
Fällt das Heim-WLAN aus, wartet der Visualizer 45 Sekunden und öffnet dann den Hotspot. Kommt das
WLAN zurück, wechselt er selbst wieder hinein. Hinweis: Der Pi Zero 2 W funkt nur im 2,4-GHz-Band.

## Die App

| Tab | Inhalt |
|---|---|
| **Spielen** | Live-Tastatur in den echten LED-Farben, Helligkeit, Transpose, Farbwahl, Presets, „Alle LEDs aus“ |
| **Licht** | Einfarbig, Farbbereiche oder Regenbogen · Normal, Ausblenden oder Anschlagstärke · Hintergrundlicht, Nachbar-LEDs |
| **WLAN** | Verbindungsstatus mit IP, Netzwerk wechseln, gespeicherte Netze, Hotspot-Einstellungen |
| **Mehr** | Streifen einrichten mit Testbildern, Transpose-Automatik, Extras, Display-Fernbedienung, MIDI-Monitor, System, Protokoll |

Zusatzfunktionen wie Synthesia, Aufnahme, MIDI-Wiedergabe, Presets per Pedal und Leerlauf-Animation
sind eingebaut, aber ausgeschaltet. Du aktivierst sie unter **Mehr → Extras**.

## Transpose

Steht das Piano auf „+2“, sendet es jede Note zwei Halbtöne höher. Damit die LEDs trotzdem über den
richtigen Tasten leuchten, muss der Visualizer den Versatz kennen:

- **Von Hand:** Im Tab „Spielen“ mit − und + denselben Wert einstellen, oder am Display mit dem Joystick hoch und runter.
- **Erkennen:** „Erkennen“ tippen und die tiefste Taste drücken. Der Versatz wird berechnet.
- **Pedal-Trick:** Unter Mehr → Transpose-Automatik einschalten. Dann das Soft-Pedal dreimal schnell treten
  und die tiefste Taste drücken. Das geht ganz ohne Handy.
- **Automatisch vom Piano:** Mit „Tx Edit Data“ am RD-700NX kann der Visualizer die Einstellung direkt
  mitlesen. Die App führt durch das einmalige Anlernen.

## Display

| Taste | Statusseite | Menü |
|---|---|---|
| Joystick ◀ ▶ | Helligkeit | Wert ändern |
| Joystick ▲ ▼ | Transpose | Auswahl |
| Joystick ● | QR-Code (Hotspot beitreten bzw. App öffnen) | Ausführen |
| KEY1 | Menü | Schließen |
| KEY2 | Zurück | Zurück |
| KEY3 | Alle LEDs aus | Alle LEDs aus |

Die Statusseite zeigt immer den Netzwerkzustand: WLAN-Name und IP, den Hotspot mit Passwort oder
„Verbinde …“. Nach einigen Minuten ohne Bedienung schaltet sich das Display ab. Ein Tastendruck oder
eine neue Meldung wie „WLAN verbunden“ oder „Piano getrennt“ weckt es wieder.

## Stabilität und Selbstheilung

| Problem | Was der Visualizer tut |
|---|---|
| LEDs bleiben hängen | Stuck-Note-Timeout, „All Notes Off“, regelmäßige komplette Neuübertragung, alle LEDs aus, sobald das Piano getrennt wird |
| Piano aus- und wieder eingeschaltet | erkennt es und verbindet sich automatisch neu |
| Ein Teil stürzt ab (MIDI, WLAN, Display, Web) | der Supervisor startet genau diesen Teil neu |
| LED-Treiber meldet Fehler | Treiber wird neu initialisiert |
| Programm hängt | systemd-Watchdog startet den Dienst nach 30 s neu |
| Ganzes System hängt | Hardware-Watchdog startet den Pi neu |
| Kaputte Einstellungsdatei | Werkseinstellungen, die defekte Datei wird als `.broken` gesichert |
| Zu schwaches Netzteil | Strombegrenzung dimmt vorher. Die App warnt bei Unterspannung. |
| SD-Karte | Einstellungen werden gebündelt und atomar geschrieben, das Systemprotokoll liegt im RAM |

## Verkabelung

- LED-Daten an **GPIO 18**. Die Masse von Netzteil, Streifen und Pi verbinden.
- Den Streifen direkt vom 5-V-Netzteil versorgen, am besten an beiden Enden einspeisen.
- Ein Level-Shifter (3,3 V → 5 V) auf der Datenleitung verhindert flackernde Pixel.
- Unter Mehr → LED-Streifen das Netzteil-Limit eintragen. Für 3 A sind 2500 mA ein guter Wert.

## Entwicklung

```bash
pip install -r requirements-dev.txt
python -m pianoled --simulate --port 8080    # läuft ohne Hardware, Streifen und Display als Vorschau in der App
python -m pytest
```

```
pianoled/
  __main__.py      Start, Logging, systemd-Watchdog
  app.py           verbindet alles, Schnittstelle für Web und Display
  supervisor.py    startet abgestürzte Komponenten neu
  renderer.py      einziger Thread am LED-Streifen: Ereignisse → Frames
  keymap.py        Note → LED (Tastengeometrie, Transpose, Richtung, Versatz)
  state.py         Tastenzustand, Sustain, Stuck-Timeout
  leds.py          ws281x-Treiber, Helligkeit, Strombegrenzung, Gamma
  midi_input.py    MIDI per Callback, Hot-Plug
  transpose.py     Pedal-Trick, Roland-SysEx-Anlernen
  config.py        Einstellungen mit Standardwerten und Prüfung
  lcd/             screens.py (Bilder), ui.py (Bedienung), device.py (ST7735-Hardware)
  system/          network.py (WLAN/Hotspot über NetworkManager), sysinfo.py
  web/             server.py (API, WebSocket, Captive Portal), static/ (App + Einrichtung)
install/           install.sh, systemd-Dienst, optional Bluetooth-MIDI
```

Protokoll auf dem Pi: `journalctl -u pianoled -f` oder in der App unter Mehr → Protokoll.

## Lizenz

MIT, siehe LICENSE. Ursprünglich basierend auf [onlaj/Piano-LED-Visualizer](https://github.com/onlaj/Piano-LED-Visualizer).
