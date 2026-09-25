# Piano LED Visualizer – Konzept für den Neuaufbau

Stand: 2026-09-25. Dieses Dokument fasst die Analyse des bestehenden Codes zusammen,
beschreibt die Zielarchitektur und listet die offenen Klärungspunkte.

## 1. Befund: Warum das aktuelle System instabil und langsam ist

Der Code ist ein Fork von 2019/2020 (`visualizer.py`, ~1900 Zeilen, Python-2-Stil).
Die wichtigsten Ursachen für Abstürze, hängende LEDs und Trägheit:

| Problem | Ursache im Code |
|---|---|
| 100 % CPU, Verzögerung, verpasste Noten | Endlos-Busy-Loop ohne `sleep`; pollt GPIO, MIDI und rendert in jedem Durchlauf. In Fading/Velocity-Modus werden pro Durchlauf alle 176 LEDs mit Python-Float-Mathe neu berechnet. `strip.show()` wird **bei jedem Durchlauf** aufgerufen, auch ohne Änderung (~5 ms Bus-Zeit pro Frame). |
| Abstürze / Flackern | Animations-Threads (Screensaver, Rainbow, ...) rufen `strip.show()` parallel zur Hauptschleife auf. Der ws281x-DMA-Treiber ist nicht thread-sicher. |
| Abstürze / Endlosschleife | `singleton()` ruft bei fehlgeschlagenem Lock `os.execl` auf sich selbst auf: Exec-Schleife statt sauberem Exit. Überall `except: pass`/`except: continue`, echte Fehler werden verschluckt. |
| Piano nach dem Pi eingeschaltet = keine Reaktion | MIDI-Port wird nur einmal beim Start gesucht. Fehlt er, läuft die Hauptschleife per `except: continue` ewig ohne LED-Update und ohne Reconnect. Kein Hot-Plug. |
| LEDs bleiben an | Buffer wird nur per Event geändert. Ein verlorenes `note_off` (Queue-Overflow während LCD-Rendering, Störung auf der Datenleitung) bleibt ewig an. Kein periodischer Full-Refresh, kein Stuck-Note-Timeout, kein "All Notes Off" (CC 120/123). |
| Konstante Last durch Roland | Das RD-700NX sendet alle ~300 ms Active Sensing (0xFE). Der Parser wandelt **jede** Nachricht per `str(msg)`-String-Splitting um (`find_between`). Teuer und fragil. |
| Kein Transpose | Note→LED ist hart codiert: `(note-20)*2 - offset`, 176 LEDs, 88 Tasten. Kein Offset, keine Strip-Richtung, keine LED-Anzahl konfigurierbar. |
| Langer Boot | Volles Raspbian, Python + PIL + psutil + mido Import, LCD-Init, Skript per rc.local. Kein systemd-Service, keine Boot-Optimierung. |
| Kein WLAN-Setup / Fallback | Nichts im Projekt. Netzwerk komplett dem OS überlassen. |
| Vendored Treiber | `rpi_ws281x.py`/`neopixel.py` sind alte SWIG-Kopien, zusätzlich `rpi-ws281x` in requirements: Versionskonflikte. |

Hardware-Verdachtsmomente (im Code nicht sichtbar, aber typisch für genau diese Symptome):

- **Spannungseinbruch**: 176 LEDs @ 50 % Weiß ≈ 5,3 A. Hängt der Pi am selben 5 V/6 A-Netzteil, brownt er bei hellen Akkorden aus → Absturz/Reboot.
- **Datenleitung 3,3 V ohne Level-Shifter** und lange Leitung → zufällige Pixel, die "hängen bleiben".
- **Onboard-Audio nicht deaktiviert** (`snd_bcm2835`) → PWM-Konflikt mit dem LED-Treiber → Müll auf dem Strip.

## 2. Zielarchitektur

Ein Neuaufbau als eigenständiges Paket `pianoled/` (Python 3.11+, systemd-Service), kein Monolith.

```
pianoled/
  core/
    midi_input.py     # rtmidi-Callback (kein Polling), Hot-Plug via ALSA/udev-Watch, Reconnect
    keymap.py         # Note→LED-Mapping: LED-Anzahl, LEDs pro Taste, Richtung, Offset, TRANSPOSE
    state.py          # Tastenzustand (on/off, Velocity, Sustain, Zeitstempel)
    renderer.py       # Frame-Loop mit fester Rate (z. B. 60 Hz), numpy-Framebuffer, show() nur bei Änderung
    effects/          # Normal, Fading, Velocity, Rainbow, Multicolor, Backlight, Screensaver-Animationen
    leds.py           # einziger Besitzer des ws281x-Objekts (thread-sicher), Gamma, Helligkeitslimit, Watchdog
  web/
    server.py         # aiohttp: REST + WebSocket, statische PWA
    static/           # Smartphone-UI (PWA, offline-fähig, Dark Mode)
  system/
    network.py        # WLAN-Verwaltung via NetworkManager (nmcli), Hotspot-Fallback
    service.py        # Watchdog-Ping an systemd, Reboot/Shutdown/Update
  config.py           # YAML/JSON-Config mit Schema und Defaults, atomares Schreiben
  main.py
install/
  install.sh          # Fresh-Install auf Raspberry Pi OS Lite, Boot-Optimierung
  pianoled.service    # systemd, Restart=always, WatchdogSec
  99-pianoled-midi.rules
```

Kernprinzipien:

1. **Ereignisgetrieben statt Polling.** MIDI kommt per Callback in eine Queue. Der Render-Thread läuft mit fester Rate und schläft dazwischen. CPU im Leerlauf nahe 0 %.
2. **Ein Besitzer für den LED-Strip.** Nur der Renderer ruft `show()` auf. Animationen sind Effekt-Objekte, die einen Frame liefern, keine Threads.
3. **Selbstheilend.** Periodischer Full-Refresh, Stuck-Note-Timeout (konfigurierbar), CC 120/123 → alles aus, "Panic"-Button in der App, systemd `Restart=always` + Watchdog.
4. **Hot-Plug.** Piano an/aus, USB-Kabel ziehen: automatisch neu verbinden, Status in der App.
5. **Schnell booten.** Raspberry Pi OS Lite, Service startet ohne auf Netzwerk zu warten, Netzwerk parallel; Ziel < 20 s auf Zero 2 W.
6. **Alles per Smartphone.** LCD-Hat optional (Anzeige von Status/IP), Bedienung über PWA.

### Note→LED-Mapping und Transpose

```
led_index = base_offset + direction * round((note - transpose - lowest_note) * leds_per_key + gap_correction(note))
```

Transpose-Optionen in der App:

- **Manuell**: `+/-` Halbtöne, wird gespeichert.
- **Kalibrieren**: "Drücke die tiefste Taste (A0)". Der Visualizer setzt `transpose = empfangene_note - 21`. Funktioniert unabhängig davon, was das Piano sendet. Auch über Pedal-Kombination auslösbar (z. B. Soft-Pedal 3× kurz), damit man nicht zum Handy greifen muss.
- **Automatisch (zu prüfen)**: Ein eingebauter MIDI-Monitor in der App zeigt, ob das RD-700NX beim Ändern von Transpose eine SysEx-Nachricht sendet ("Tx Edit Data" muss am Piano eingeschaltet sein). Wenn ja, folgt der Visualizer dem Piano automatisch. Wenn nein, bleibt Kalibrieren der Weg.
- **Hinweis zum Prüfen**: Falls das RD-700NX eine Einstellung hat, mit der Transpose nur den internen Klang und nicht MIDI OUT beeinflusst, verschwindet das Problem an der Quelle.

### Smartphone-Oberfläche (PWA)

- Live-Ansicht der Tastatur (WebSocket), Verbindungsstatus Piano/WLAN
- Farbmodi, Helligkeit, Backlight, Fading/Velocity, Presets
- Transpose (+/-, Kalibrieren)
- Strip-Setup: LED-Anzahl, LEDs pro Taste, Richtung, Test-Muster, Gamma
- WLAN: Netz wählen, Passwort eingeben, Hotspot-Status
- System: Log, CPU/Temp, Neustart, Update, Panic/Clear

### Netzwerk

- Raspberry Pi OS Bookworm/Trixie nutzt NetworkManager. Beim Boot: bekanntes WLAN versuchen (~30 s). Kein Erfolg → Hotspot `PianoLED` (WPA2) mit Weboberfläche unter `http://10.42.0.1` bzw. `http://pianoled.local`.
- Wenn das WLAN später wieder auftaucht: automatisch zurückwechseln.
- mDNS (avahi) für `pianoled.local`.

### Performance-Budget

| Gerät | Erwartung |
|---|---|
| Pi Zero 2 W (4× A53) | Problemlos: Renderer 60 Hz, Web-UI parallel, Latenz Taste→LED < 10 ms. |
| Pi Zero (W) 1 (1× ARMv6) | Machbar mit Python bei ereignisgetriebener Architektur und numpy; Renderer auf 30–40 Hz begrenzen. Web-UI-Aktivität kann kurz spürbar sein. Alternative wäre ein Go-Binary (schnellerer Start, weniger RAM), aber schlechter selbst anzupassen. |

## 3. Entscheidungen

| Punkt | Entscheidung |
|---|---|
| Pi-Modell | Raspberry Pi Zero 2 W (Python-Umsetzung, 60 fps Renderer) |
| OS | Neuinstallation Raspberry Pi OS Lite, Installer unter `install/install.sh` |
| LCD-Hat | Vorhanden, Statusseite + kleines Menü, Bedienung primär per Smartphone |
| Strip | 144 LEDs/m, physikalisches Mapping, per App kalibrierbar |
| Netzteil | ca. 3 A → Leistungsbudget (Default 3000 mA, Empfehlung 2500) |
| MIDI | USB, Hot-Plug |
| Zusatzfunktionen | Alle eingebaut, standardmäßig aus (Extras-Tab) |
| Transpose am Piano | Prüft der Nutzer (Transpose nur auf Klang statt MIDI OUT) |
| Sprache | Deutsch |
