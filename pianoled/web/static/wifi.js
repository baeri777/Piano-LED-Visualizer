/* WLAN-Auswahl und Verbindungsablauf – genutzt von /setup und vom WLAN-Tab der App. */
"use strict";

/**
 * WifiPicker rendert die Netzwerkliste in `root` und führt durch den Verbindungsablauf.
 * options.onDone(): wird aufgerufen, wenn der Nutzer den Ablauf abschließt.
 */
class WifiPicker {
  constructor(root, options = {}) {
    this.root = root;
    this.opts = options;
    this.state = null;
    this.hostname = "pianoled";
    this.pollTimer = null;
  }

  async start() {
    this.renderList();
    await this.refresh(false);
  }

  // ---------- Liste ----------
  renderList(message) {
    const list = el("div.list", { id: "wifi-list" }, [el("div.item", {}, [el("span.spin"), el("span.grow.sub", { text: "Suche Netzwerke …" })])]);
    this.listEl = list;
    this.metaEl = el("p.hint", { text: message || "" });
    this.root.replaceChildren(
      el("div.row", {}, [
        el("span.label", { style: { fontWeight: 600 }, text: "Verfügbare Netzwerke" }),
        el("button.btn.ghost.sm", { onclick: () => this.refresh(true), "aria-label": "Neu suchen" }, [icon("refresh"), "Suchen"]),
      ]),
      list,
      this.metaEl,
      el("button.btn.block", { style: { marginTop: "10px" }, onclick: () => this.askPassword("", true) }, [icon("plus"), "Anderes Netzwerk"]),
    );
  }

  async refresh(userAction) {
    if (userAction) haptic();
    try {
      const [info, scan] = await Promise.all([api("/api/wifi"), api("/api/wifi/scan")]);
      this.state = info.state;
      this.showNetworks(scan, info);
      if (info.attempt && info.attempt.status === "failed" && this.opts.showLastError !== false) {
        this.metaEl.textContent = "";
        this.metaEl.append(el("span", { style: { color: "var(--err)" }, text: `Letzter Versuch mit „${info.attempt.ssid}“ fehlgeschlagen: ${info.attempt.message || ""}` }));
      }
    } catch (e) {
      this.listEl.replaceChildren(el("div.item", {}, [el("span.grow.sub", { text: "Netzwerke konnten nicht geladen werden." })]));
    }
  }

  showNetworks(scan, info) {
    const current = info.state && info.state.mode === "wifi" ? info.state.ssid : null;
    const known = new Set(info.known || []);
    const nets = scan.networks || [];
    if (!nets.length) {
      this.listEl.replaceChildren(el("div.item", {}, [el("span.grow.sub", { text: info.state && info.state.mode === "hotspot" ? "Im Hotspot-Betrieb kann nicht gesucht werden. Nutze „Anderes Netzwerk“." : "Keine Netzwerke gefunden." })]));
    } else {
      this.listEl.replaceChildren(...nets.map(n => el("button.item", { onclick: () => this.askPassword(n.ssid, false, n.secure, known.has(n.ssid)) }, [
        signalBars(n.signal),
        el("span.grow", { text: n.ssid }),
        n.ssid === current ? el("span.meta", { style: { color: "var(--ok)" }, text: "verbunden" }) : known.has(n.ssid) ? el("span.meta", { text: "gespeichert" }) : null,
        n.secure ? icon("lock") : null,
        icon("chevron"),
      ])));
    }
    this.metaEl.textContent = scan.cached && scan.age_s != null
      ? `Liste von vor ${Math.max(1, Math.round(scan.age_s / 60))} Min. (im Hotspot kann nicht neu gesucht werden). Nur 2,4-GHz-Netze werden unterstützt.`
      : "Der Pi Zero 2 W unterstützt nur 2,4-GHz-Netze.";
  }

  // ---------- Passwort ----------
  async askPassword(ssid, manual, secure = true, known = false) {
    haptic();
    const ssidField = el("input", { type: "text", placeholder: "Netzwerkname (SSID)", value: ssid, autocapitalize: "off", autocomplete: "off", spellcheck: false });
    const pw = el("input", { type: "password", placeholder: known ? "Passwort (leer = gespeichertes)" : "WLAN-Passwort", autocomplete: "current-password", autocapitalize: "off", spellcheck: false });
    const eye = el("button.btn.ghost.sm.eye", { type: "button", "aria-label": "Passwort zeigen", onclick: () => {
      const show = pw.type === "password"; pw.type = show ? "text" : "password";
      eye.replaceChildren(icon(show ? "eyeOff" : "eye"));
    } }, [icon("eye")]);
    const hidden = el("input", { type: "checkbox" });
    const content = el("div.stack", {}, [
      manual ? ssidField : null,
      secure ? el("div.field", {}, [pw, eye]) : el("p.hint", { text: "Offenes Netzwerk ohne Passwort." }),
      manual ? el("label.row", {}, [el("span.label", { text: "Verstecktes Netzwerk" }), el("span.switch", {}, [hidden, el("span")])]) : null,
    ]);
    const connectNow = async () => {
      const name = (manual ? ssidField.value : ssid).trim();
      if (!name) { toast("Bitte Netzwerknamen eingeben", "err"); return; }
      if (secure && pw.value && pw.value.length < 8) { toast("WLAN-Passwörter haben mindestens 8 Zeichen", "err"); return; }
      scrim.remove();
      this.connect(name, pw.value, hidden.checked);
    };
    pw.addEventListener("keydown", e => { if (e.key === "Enter") connectNow(); });
    const scrim = el("div.scrim", { onclick: e => { if (e.target === scrim) scrim.remove(); } }, [el("div.sheet", {}, [
      el("h3", { text: manual ? "Netzwerk hinzufügen" : ssid }),
      el("p", { text: "Der Visualizer verbindet sich mit diesem WLAN und merkt es sich." }),
      content,
      el("div.btns", {}, [el("button.btn", { text: "Abbrechen", onclick: () => scrim.remove() }), el("button.btn.primary", { text: "Verbinden", onclick: connectNow })]),
    ])]);
    document.body.append(scrim);
    setTimeout(() => (manual ? ssidField : pw).focus(), 250);
  }

  // ---------- Verbinden & Übergabe ----------
  async connect(ssid, password, hidden) {
    const viaHotspot = this.state && this.state.mode === "hotspot";
    try {
      const r = await api("/api/wifi/connect", "POST", { ssid, password, hidden });
      this.hostname = r.hostname || this.hostname;
    } catch (e) {
      toast(e.message, "err", 4000);
      return;
    }
    this.showProgress(ssid, viaHotspot);
  }

  showProgress(ssid, viaHotspot) {
    const title = el("h2", { style: { textAlign: "center", margin: "0 0 6px", fontSize: "22px" }, text: `Verbinde mit „${ssid}“ …` });
    const text = el("p.sub", { style: { textAlign: "center", margin: 0 }, text: "Das dauert bis zu 30 Sekunden." });
    const iconBox = el("div.big-icon", { style: { background: "rgba(91,140,255,.14)", color: "var(--accent)" } }, [el("span.spin", { style: { width: "34px", height: "34px", borderWidth: "3px" } })]);
    const detail = el("div", { style: { marginTop: "18px" } });
    this.root.replaceChildren(el("div", {}, [iconBox, title, text, detail]));
    const url = `http://${this.hostname}.local`;
    const handoff = () => {
      iconBox.style.background = "rgba(57,217,138,.14)"; iconBox.style.color = "var(--ok)";
      iconBox.replaceChildren(icon("wifi"));
      title.textContent = "Fast geschafft!";
      text.textContent = viaHotspot ? "Der Hotspot schaltet sich jetzt ab." : "Der Visualizer wechselt jetzt das Netzwerk.";
      detail.replaceChildren(
        el("ol.steps", {}, [
          el("li", { text: `Verbinde dein Handy wieder mit „${ssid}“.` }),
          el("li", {}, ["Öffne ", el("a", { href: url, text: url }), ". Die IP-Adresse steht auch auf dem Display des Visualizers."]),
          el("li", { text: "Klappt es nicht, kommt der Hotspot nach etwa einer Minute zurück. Verbinde dich dann erneut, hier steht der Grund." }),
        ]),
        el("a.btn.primary.block", { href: url, text: `${this.hostname}.local öffnen` }),
      );
    };
    let lost = false, tries = 0;
    clearInterval(this.pollTimer);
    this.pollTimer = setInterval(async () => {
      tries++;
      try {
        const info = await api("/api/wifi");
        const a = info.attempt;
        if (lost && info.state.mode === "hotspot" && a && a.status === "failed") return this.showFailure(a);
        if (a && a.ssid === ssid && a.status === "ok") return this.showSuccess(info.state);
        if (a && a.ssid === ssid && a.status === "failed") return this.showFailure(a);
      } catch (e) {
        if (!lost) { lost = true; handoff(); }
      }
      if (tries > 90) clearInterval(this.pollTimer);
    }, 2000);
  }

  showSuccess(state) {
    clearInterval(this.pollTimer);
    haptic(20);
    const url = `http://${this.hostname}.local`;
    this.root.replaceChildren(el("div", { style: { textAlign: "center" } }, [
      el("div.big-icon", { style: { background: "rgba(57,217,138,.14)", color: "var(--ok)" } }, [icon("check")]),
      el("h2", { style: { margin: "0 0 6px", fontSize: "22px" }, text: `Verbunden mit „${state.ssid || ""}“` }),
      el("div.big-ip", { text: state.ip || "" }),
      el("p.sub", { text: `Ab jetzt erreichst du den Visualizer unter ${url}` }),
      el("div.btns", { style: { marginTop: "16px" } }, [el("a.btn.primary", { href: "/", text: "Zur Steuerung" })]),
    ]));
    if (this.opts.onDone) this.opts.onDone(state);
  }

  showFailure(attempt) {
    clearInterval(this.pollTimer);
    haptic([30, 60, 30]);
    this.root.replaceChildren(el("div", { style: { textAlign: "center" } }, [
      el("div.big-icon", { style: { background: "rgba(255,93,93,.14)", color: "var(--err)" } }, [icon("x")]),
      el("h2", { style: { margin: "0 0 6px", fontSize: "22px" }, text: "Verbindung fehlgeschlagen" }),
      el("p.sub", { text: `„${attempt.ssid}“: ${attempt.message || "Unbekannter Fehler"}` }),
      el("div.btns", { style: { marginTop: "16px" } }, [
        el("button.btn.primary", { text: "Nochmal versuchen", onclick: () => this.start() }),
      ]),
    ]));
  }
}
