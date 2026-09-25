/* Piano LED – Steuerungs-App. Hängt an index.html, nutzt core.js und wifi.js. */
"use strict";

const App = {
  cfg: null,          // aktuelle Konfiguration (vom Server)
  status: null,       // letzter Status (vom Server)
  version: null,
  ws: null,
  online: false,
  pending: {},        // noch nicht gesendete Einstellungsänderungen
  sendTimer: null,
  picker: null,
  timers: {},
};

const get = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
const setLocal = (path, value) => { const p = path.split("."); let o = App.cfg; for (const k of p.slice(0, -1)) o = o[k]; o[p.at(-1)] = value; };
const hex = c => "#" + (c || [0, 0, 0]).map(v => Math.max(0, Math.min(255, v | 0)).toString(16).padStart(2, "0")).join("");
const rgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
const signed = v => (v > 0 ? "+" : "") + v;

// =====================================================================
// Einstellungen senden (gedrosselt: beim Ziehen max. ~8 Anfragen/s)
// =====================================================================
function patch(path, value, immediate = false) {
  App.pending[path] = value;
  setLocal(path, value);
  if (immediate) { clearTimeout(App.sendTimer); App.sendTimer = null; return flush(); }
  if (!App.sendTimer) App.sendTimer = setTimeout(flush, 120);
}
async function flush() {
  App.sendTimer = null;
  const changes = App.pending;
  App.pending = {};
  if (!Object.keys(changes).length) return;
  try { App.cfg = await api("/api/config", "POST", { changes }); renderConfig(); }
  catch (e) { toast("Speichern fehlgeschlagen: " + e.message, "err"); }
}

// =====================================================================
// Navigation (#play, #light, #wifi, #more, #more/strip …)
// =====================================================================
function go(route) { if (location.hash.slice(1) !== route) location.hash = route; else route_(); }
function route_() {
  const route = location.hash.slice(1) || "play";
  const [view, sub] = route.split("/");
  const known = ["play", "light", "wifi", "more"];
  const v = known.includes(view) ? view : "play";
  $$(".view").forEach(s => s.classList.toggle("active", s.id === "view-" + v));
  $$("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.go === v));
  $("#more-home").classList.toggle("hidden", v !== "more" || !!sub);
  $$(".sub-page").forEach(p => p.classList.toggle("hidden", p.id !== "page-" + sub));
  window.scrollTo({ top: 0 });
  onEnter(v, sub);
}
window.addEventListener("hashchange", route_);
document.addEventListener("click", e => {
  const t = e.target.closest("[data-go]");
  if (t) { e.preventDefault(); haptic(); go(t.dataset.go); }
});

function onEnter(view, sub) {
  Object.values(App.timers).forEach(clearInterval);
  App.timers = {};
  if (view === "play" && App.status && App.status.simulate) App.timers.sim = setInterval(pollSimStrip, 120);
  if (view === "wifi") enterWifi();
  if (sub === "extras") loadSongs();
  if (sub === "display") { refreshLcd(); App.timers.lcd = setInterval(refreshLcd, 800); }
  if (sub === "log") { loadLog(); App.timers.log = setInterval(loadLog, 3000); }
  if (sub === "system" || sub === "midi") renderStatus();
}

// =====================================================================
// Bindings: data-cfg, data-color, data-seg, data-show, data-val
// =====================================================================
function readInput(input) {
  if (input.type === "checkbox") return input.checked;
  if (input.dataset.int !== undefined) return parseInt(input.value, 10);
  if (input.dataset.float !== undefined || input.step.includes(".")) return parseFloat(input.value);
  if (input.type === "range" || input.type === "number") return Number(input.value);
  return input.value;
}
function fillRange(input) {
  if (input.type !== "range") return;
  const p = (input.value - input.min) / (input.max - input.min) * 100;
  input.style.setProperty("--p", p + "%");
}
function showValue(path, value) {
  $$(`[data-val="${path}"]`).forEach(v => { v.textContent = value + (v.dataset.unit || ""); });
}
function bindControls() {
  $$("[data-cfg]").forEach(input => {
    const path = input.dataset.cfg;
    const live = input.type === "range";
    input.addEventListener(live ? "input" : "change", () => {
      const value = readInput(input);
      if (typeof value === "number" && Number.isNaN(value)) return;
      if (input.dataset.minlen && String(value).length < +input.dataset.minlen) { toast(`Mindestens ${input.dataset.minlen} Zeichen`, "err"); return; }
      fillRange(input);
      showValue(path, value);
      if (input.type === "checkbox") haptic();
      patch(path, value, !live);
      if (input.type === "checkbox") renderConfig();
    });
    if (live) input.addEventListener("change", () => flush());
  });
  $$("[data-color]").forEach(input => input.addEventListener("input", () => patch(input.dataset.color, rgb(input.value))));
  $$("[data-seg]").forEach(seg => $$("button", seg).forEach(b => b.addEventListener("click", () => {
    haptic();
    const value = seg.dataset.int !== undefined ? parseInt(b.dataset.v, 10) : b.dataset.v;
    patch(seg.dataset.seg, value, true);
    renderConfig();
  })));
  $$("[data-test]").forEach(b => b.addEventListener("click", () => { haptic(); api("/api/test", "POST", JSON.parse(b.dataset.test)).then(() => toast("Testbild läuft")); }));
  $$("[data-action]").forEach(b => b.addEventListener("click", () => systemAction(b.dataset.action)));
  $$("[data-lcd]").forEach(b => b.addEventListener("click", () => { haptic(); api("/api/lcd/" + b.dataset.lcd, "POST").then(refreshLcd); }));
}

function renderConfig() {
  const cfg = App.cfg;
  if (!cfg) return;
  $$("[data-cfg]").forEach(input => {
    const path = input.dataset.cfg;
    if (path in App.pending) return;
    const v = get(cfg, path);
    if (v === undefined) return;
    if (input.type === "checkbox") input.checked = !!v;
    else if (document.activeElement !== input) input.value = typeof v === "number" && !Number.isInteger(v) ? Math.round(v * 100) / 100 : v;
    fillRange(input);
    showValue(path, v);
  });
  $$("[data-color]").forEach(i => { if (document.activeElement !== i) i.value = hex(get(cfg, i.dataset.color)); });
  $$("[data-seg]").forEach(seg => { const v = String(get(cfg, seg.dataset.seg)); $$("button", seg).forEach(b => b.classList.toggle("active", b.dataset.v === v)); });
  $$("[data-show]").forEach(n => n.classList.toggle("hidden", !get(cfg, n.dataset.show)));

  const look = cfg.look;
  ["single", "multicolor", "rainbow"].forEach(m => $("#mode-" + m).classList.toggle("hidden", look.color_mode !== m));
  $("#light-desc").textContent = {
    normal: "Die LED leuchtet, solange die Taste gedrückt ist.",
    fading: "Die LED leuchtet beim Anschlag auf und blendet nach dem Loslassen weich aus.",
    velocity: "Je fester du spielst, desto heller. Blendet danach weich aus.",
  }[look.light_mode];
  $("#row-fade").classList.toggle("hidden", look.light_mode === "normal");
  $("#row-vmin").classList.toggle("hidden", look.light_mode !== "velocity");
  $("#backlight-opts").classList.toggle("hidden", !look.backlight.enabled);
  $("#adj-color").classList.toggle("hidden", look.adjacent.mode !== "rgb");
  const density = Math.round(1000 / cfg.strip.led_pitch_mm);
  $("#density").value = ["144", "120", "96", "72", "60"].includes(String(density)) ? String(density) : "0";

  renderTranspose();
  renderSwatches();
  renderPresets();
  renderMulticolor();
  renderSysex();
  renderMidiPorts();
}

// =====================================================================
// Spielen: Tastatur, Transpose, Farben, Presets
// =====================================================================
const BLACK = new Set([1, 3, 6, 8, 10]);
const keyEls = {};
function buildPiano() {
  const piano = $("#piano");
  const whites = [];
  for (let n = 21; n <= 108; n++) if (!BLACK.has(n % 12)) whites.push(n);
  const w = 100 / whites.length;
  let i = 0;
  for (let n = 21; n <= 108; n++) {
    const black = BLACK.has(n % 12);
    const k = el(black ? "div.b" : "div.w", { style: black ? { left: (i * w - w * 0.32) + "%", width: (w * 0.64) + "%" } : { left: (i * w) + "%", width: w + "%" } });
    if (!black) i++;
    keyEls[n] = k;
    piano.append(k);
  }
}
let litKeys = new Set();
function renderKeys(keys) {
  if (!keys) return;
  const colors = keys.colors || {};
  const now = new Set(Object.keys(colors).map(Number));
  for (const n of litKeys) if (!now.has(n) && keyEls[n]) { keyEls[n].style.background = ""; keyEls[n].classList.remove("on"); }
  for (const n of now) {
    const k = keyEls[n];
    if (!k) continue;
    k.style.background = colors[n];
    k.style.setProperty("--c", colors[n]);
    k.classList.add("on");
  }
  litKeys = now;
  const live = $("#live");
  const s = App.status;
  const parts = [];
  if (s && !s.midi.connected) parts.push(el("span", { text: "Kein Piano verbunden" }));
  else parts.push(el("span", { text: now.size ? `${now.size} ${now.size === 1 ? "Taste" : "Tasten"}` : "Bereit" }));
  if (keys.sustain) parts.push(el("span.tag", { text: "Sustain" }));
  if (keys.idle_animation) parts.push(el("span.tag", { text: "Leerlauf-Animation" }));
  if (keys.power_scale < 1) parts.push(el("span.tag", { style: { color: "var(--warn)" }, text: `Strombegrenzung ${Math.round(keys.power_scale * 100)} %` }));
  live.replaceChildren(...parts);
  const p = $("#power-now");
  if (p) p.textContent = `aktuell ca. ${keys.estimated_ma} mA`;
}
async function pollSimStrip() {
  try {
    const { frame } = await api("/api/sim/frame");
    if (!frame) return;
    const strip = $("#sim-strip");
    strip.classList.remove("hidden");
    const n = frame.length / 3;
    if (strip.children.length !== n) strip.replaceChildren(...Array.from({ length: n }, () => el("i")));
    for (let i = 0; i < n; i++) strip.children[i].style.background = `rgb(${frame[i * 3]},${frame[i * 3 + 1]},${frame[i * 3 + 2]})`;
  } catch (e) { /* offline */ }
}

function renderTranspose() {
  const v = App.cfg.transpose.semitones;
  const num = $("#tp-num");
  num.textContent = v ? signed(v) : "0";
  num.classList.toggle("nz", v !== 0);
  $("#tp-hint").textContent = App.cfg.transpose.roland_sysex.enabled ? "folgt dem Piano" : "";
}
async function transpose(body) {
  haptic();
  try { const r = await api("/api/transpose", "POST", body); setLocal("transpose.semitones", r.semitones); renderTranspose(); }
  catch (e) { toast(e.message, "err"); }
}

const SWATCHES = [[255, 255, 255], [255, 170, 90], [255, 40, 40], [255, 120, 0], [255, 210, 0], [40, 230, 90], [0, 210, 200], [30, 110, 255], [150, 70, 255], [255, 40, 150]];
function renderSwatches() {
  const look = App.cfg.look;
  const same = c => look.color_mode === "single" && c.every((v, i) => v === look.color[i]);
  const items = SWATCHES.map(c => el("button.sw" + (same(c) ? ".sel" : ""), { style: { background: hex(c) }, "aria-label": "Farbe " + hex(c),
    onclick: () => { haptic(); patch("look.color", c); patch("look.color_mode", "single", true); renderConfig(); } }));
  items.push(el("button.sw.rainbow" + (look.color_mode === "rainbow" ? ".sel" : ""), { "aria-label": "Regenbogen",
    onclick: () => { haptic(); patch("look.color_mode", "rainbow", true); renderConfig(); } }));
  const custom = !SWATCHES.some(same) && look.color_mode === "single";
  const input = el("input", { type: "color", value: hex(look.color), "aria-label": "Eigene Farbe" });
  input.addEventListener("input", () => { patch("look.color", rgb(input.value)); if (look.color_mode !== "single") patch("look.color_mode", "single"); });
  input.addEventListener("change", () => { flush(); renderConfig(); });
  items.push(el("span.sw.custom" + (custom ? ".sel" : ""), { style: custom ? { background: hex(look.color) } : {} }, [custom ? null : icon("plus"), input]));
  $("#swatches").replaceChildren(...items);
}

function renderPresets() {
  const list = App.cfg.presets || [];
  const active = App.status && App.status.active_preset;
  const chips = list.map(p => {
    let timer, long = false;
    const chip = el("button.preset" + (p.id === active ? ".active" : ""), {
      onpointerdown: () => { long = false; timer = setTimeout(async () => {
        long = true; haptic(25);
        if (await confirmSheet(`„${p.name}“ löschen?`, "Das Preset wird entfernt.", "Löschen", "danger")) {
          await api(`/api/presets/${p.id}`, "DELETE"); await reloadConfig(); toast("Preset gelöscht");
        }
      }, 550); },
      onpointerup: () => clearTimeout(timer), onpointerleave: () => clearTimeout(timer),
      oncontextmenu: e => e.preventDefault(),
      onclick: async () => { if (long) return; haptic(); await api(`/api/presets/${p.id}/apply`, "POST"); if (App.status) App.status.active_preset = p.id; await reloadConfig(); },
    }, [el("span.dot", { style: { background: p.look.color_mode === "rainbow" ? "conic-gradient(red,#ff0,lime,cyan,blue,#f0f,red)" : hex(p.look.color) } }), p.name]);
    return chip;
  });
  chips.push(el("button.preset.add", { onclick: savePreset }, ["+ Speichern"]));
  $("#presets").replaceChildren(...chips);
}
async function savePreset() {
  haptic();
  const name = await sheet({ title: "Preset speichern", text: "Speichert Farbe, Helligkeit und Leuchtverhalten unter einem Namen.", input: { placeholder: "z. B. Abendstimmung" }, actions: [{ label: "Speichern" }] });
  if (!name) return;
  const p = await api("/api/presets", "POST", { name });
  if (App.status) App.status.active_preset = p.id;
  await reloadConfig();
  toast(`„${p.name}“ gespeichert`, "ok");
}

// =====================================================================
// Licht: Farbbereiche
// =====================================================================
function renderMulticolor() {
  const rows = App.cfg.look.multicolor.map((entry, i) => {
    const color = el("input.pick", { type: "color", value: hex(entry.color) });
    const from = el("input", { type: "number", min: 21, max: 108, value: entry.range[0], "aria-label": "von" });
    const to = el("input", { type: "number", min: 21, max: 108, value: entry.range[1], "aria-label": "bis" });
    const update = () => {
      const mc = App.cfg.look.multicolor.map(e => ({ color: e.color.slice(), range: e.range.slice() }));
      mc[i] = { color: rgb(color.value), range: [parseInt(from.value, 10) || 21, parseInt(to.value, 10) || 108] };
      patch("look.multicolor", mc);
    };
    color.addEventListener("input", update); from.addEventListener("change", update); to.addEventListener("change", update);
    const del = el("button.btn.ghost.sm", { "aria-label": "Entfernen", onclick: () => {
      const mc = App.cfg.look.multicolor.filter((_, j) => j !== i);
      if (!mc.length) return toast("Mindestens ein Bereich", "err");
      patch("look.multicolor", mc, true);
    } }, [icon("x")]);
    return el("div.mc", {}, [color, from, el("span", { text: `${noteName(entry.range[0])}–${noteName(entry.range[1])}` }), to, del]);
  });
  $("#mc-list").replaceChildren(...rows);
}
$("#mc-add").addEventListener("click", () => patch("look.multicolor", App.cfg.look.multicolor.concat([{ color: [255, 255, 255], range: [21, 108] }]), true));
$("#density").addEventListener("change", e => { const d = parseInt(e.target.value, 10); if (d > 0) patch("strip.led_pitch_mm", Math.round(100000 / d) / 100, true); });

// =====================================================================
// Transpose-Automatik
// =====================================================================
function renderSysex() {
  const rs = App.cfg.transpose.roland_sysex;
  $("#sysex-state").textContent = rs.address
    ? `Eingerichtet (Adresse ${rs.address.map(b => b.toString(16).padStart(2, "0").toUpperCase()).join(" ")}).`
    : "Noch nicht eingerichtet. Folge den Schritten unten.";
}
$("#learn-start").addEventListener("click", async () => {
  await api("/api/transpose/learn/start", "POST");
  $("#learn-next").disabled = false; $("#learn-finish").disabled = true;
  $("#learn-state").textContent = "Gestartet. Transpose am Piano auf +1 stellen, dann „Weiter“.";
});
$("#learn-next").addEventListener("click", async () => {
  const r = await api("/api/transpose/learn/next", "POST");
  $("#learn-finish").disabled = r.steps < 2;
  $("#learn-state").textContent = r.steps < 2 ? "Jetzt Transpose auf +1 stellen, dann „Weiter“." : `Schritt ${r.steps} erfasst. Noch einmal erhöhen und „Weiter“, oder „Fertig“.`;
});
$("#learn-finish").addEventListener("click", async () => {
  const r = await api("/api/transpose/learn/finish", "POST");
  $("#learn-next").disabled = true; $("#learn-finish").disabled = true;
  $("#learn-state").textContent = r.ok ? "Geschafft! Die LEDs folgen jetzt dem Transpose des Pianos." : r.reason;
  if (r.ok) { toast("Transpose-Automatik eingerichtet", "ok"); reloadConfig(); }
});

// =====================================================================
// WLAN
// =====================================================================
function enterWifi() {
  if (!App.picker) { App.picker = new WifiPicker($("#wifi-picker"), { showLastError: true }); App.picker.start(); }
  loadKnown();
  renderNet();
}
function renderNet() {
  const s = (App.status && App.status.wifi) || {};
  const card = $("#net-card");
  const host = (App.status && App.status.hostname) || "pianoled";
  const hero = (bg, fg, ic, title, sub) => el("div.net-hero", {}, [
    el("div.ic", { style: { background: bg, color: fg } }, [ic]),
    el("div", {}, [el("div.t", { text: title }), sub ? el("div.sub", { text: sub }) : null]),
  ]);
  if (s.mode === "wifi") {
    card.replaceChildren(
      hero("rgba(57,217,138,.14)", "var(--ok)", icon("wifi"), "Verbunden", s.ssid),
      el("div.big-ip", { text: s.ip || "" }),
      el("p.hint", { text: `Erreichbar unter http://${host}.local` + (s.signal != null ? ` · Signal ${s.signal} %` : "") }),
    );
  } else if (s.mode === "hotspot") {
    card.replaceChildren(
      hero("rgba(255,176,32,.14)", "var(--warn)", icon("hotspot"), "Hotspot aktiv", "Kein WLAN verbunden"),
      el("div.cred", {}, [el("b", { text: "Name" }), el("span", { text: s.hotspot_ssid || "" }), el("b", { text: "Passwort" }), el("span", { text: s.hotspot_password || "" }), el("b", { text: "Adresse" }), el("span", { text: s.ip || "10.42.0.1" })]),
      el("p.hint", { text: `${s.clients || 0} ${s.clients === 1 ? "Gerät" : "Geräte"} verbunden. Wähle unten dein WLAN aus.` }),
      el("div.btns", { style: { marginTop: "12px" } }, [el("button.btn", { text: "Hotspot beenden", onclick: () => hotspot(false) })]),
    );
  } else if (s.mode === "connecting") {
    card.replaceChildren(hero("rgba(91,140,255,.14)", "var(--accent)", el("span.spin"), "Verbinde …", s.connecting_to || ""));
  } else if (s.mode === "unavailable") {
    card.replaceChildren(hero("var(--surface2)", "var(--muted)", icon("wifiOff"), "WLAN-Verwaltung nicht verfügbar", "NetworkManager fehlt (Entwicklungsmodus)"));
  } else {
    card.replaceChildren(
      hero("rgba(255,93,93,.14)", "var(--err)", icon("wifiOff"), "Nicht verbunden", "Suche bekannte Netzwerke …"),
      el("div.btns", { style: { marginTop: "12px" } }, [el("button.btn", { text: "Hotspot jetzt starten", onclick: () => hotspot(true) })]),
    );
  }
}
async function hotspot(on) {
  haptic();
  if (!on && !(await confirmSheet("Hotspot beenden?", "Bist du gerade über den Hotspot verbunden, reißt die Verbindung ab. Der Visualizer sucht dann bekannte WLANs.", "Beenden"))) return;
  toast(on ? "Hotspot startet …" : "Hotspot wird beendet …");
  api("/api/wifi/hotspot", "POST", { enabled: on }).catch(() => {});
}
async function loadKnown() {
  try {
    const { known } = await api("/api/wifi");
    $("#known-card").classList.toggle("hidden", !known.length);
    $("#known").replaceChildren(...known.map(ssid => el("div.item", {}, [icon("wifi"), el("span.grow", { text: ssid }),
      el("button.btn.ghost.sm", { text: "Vergessen", onclick: async () => {
        if (!(await confirmSheet(`„${ssid}“ vergessen?`, "Der Visualizer verbindet sich dann nicht mehr automatisch mit diesem Netz.", "Vergessen", "danger"))) return;
        await api("/api/wifi/forget", "POST", { ssid }); loadKnown(); toast("Netzwerk entfernt");
      } })])));
  } catch (e) { /* offline */ }
}

// =====================================================================
// Extras: Songs, Aufnahme
// =====================================================================
async function loadSongs() {
  if (!App.cfg || !App.cfg.features.player.enabled) return;
  try {
    const { songs } = await api("/api/songs");
    $("#songs").replaceChildren(...(songs.length ? songs.map(s => el("div.item", {}, [
      el("span.grow", { text: s.name.replace(/\.midi?$/i, "") }),
      el("button.btn.ghost.sm", { text: "▶", "aria-label": "Abspielen", onclick: () => api("/api/player/play", "POST", { name: s.name }).then(() => toast("Spielt " + s.name)).catch(e => toast(e.message, "err")) }),
      el("button.btn.ghost.sm", { "aria-label": "Löschen", onclick: async () => {
        if (await confirmSheet(`„${s.name}“ löschen?`, "", "Löschen", "danger")) { await api(`/api/songs/${encodeURIComponent(s.name)}`, "DELETE"); loadSongs(); }
      } }, [icon("x")]),
    ])) : [el("div.item", {}, [el("span.grow.sub", { text: "Noch keine MIDI-Dateien." })])]));
  } catch (e) { /* offline */ }
}
$("#song-file").addEventListener("change", async e => {
  const fd = new FormData();
  for (const f of e.target.files) fd.append("file", f);
  await fetch("/api/songs", { method: "POST", body: fd });
  e.target.value = "";
  toast("Hochgeladen", "ok");
  loadSongs();
});
$("#player-stop").addEventListener("click", () => api("/api/player/stop", "POST"));
$("#rec-start").addEventListener("click", () => api("/api/recorder/start", "POST").then(() => toast("Aufnahme läuft")).catch(e => toast(e.message, "err")));
$("#rec-cancel").addEventListener("click", () => api("/api/recorder/cancel", "POST").then(() => toast("Verworfen")));
$("#rec-stop").addEventListener("click", async () => {
  const name = await sheet({ title: "Aufnahme speichern", input: { placeholder: "Name (leer = Datum)" }, actions: [{ label: "Speichern" }] });
  const r = await api("/api/recorder/stop", "POST", { name: name || "" }).catch(e => ({ error: e.message }));
  toast(r.ok ? `Gespeichert: ${r.file}` : (r.error || "Nichts aufgenommen"), r.ok ? "ok" : "err");
  loadSongs();
});

// =====================================================================
// Piano & MIDI, Monitor
// =====================================================================
function renderMidiPorts() {
  const select = $("#midi-port");
  const avail = (App.status && App.status.midi.available) || [];
  const current = App.cfg.midi.port_filter || "";
  const opts = [el("option", { value: "", text: "Automatisch" }), ...avail.map(p => el("option", { value: p, text: p }))];
  if (current && !avail.includes(current)) opts.push(el("option", { value: current, text: current + " (nicht verbunden)" }));
  select.replaceChildren(...opts);
  select.value = current;
}
$("#midi-port").addEventListener("change", e => patch("midi.port_filter", e.target.value, true));
$("#monitor-on").addEventListener("change", e => api("/api/monitor", "POST", { enabled: e.target.checked }).then(() => { if (!e.target.checked) $("#monitor").textContent = ""; }));
function monitorLine(ev) {
  const t = new Date(ev.t * 1000).toLocaleTimeString("de-DE");
  let s = `${t}  `;
  if (ev.type === "note_on" || ev.type === "note_off") s += `${ev.type === "note_on" ? "▼" : "▲"} ${noteName(ev.note)} (${ev.note})${ev.velocity !== undefined ? "  Stärke " + ev.velocity : ""}  Kanal ${ev.channel + 1}`;
  else if (ev.type === "cc") s += `CC ${ev.control} = ${ev.value}  Kanal ${ev.channel + 1}`;
  else if (ev.type === "sysex") s += `SysEx F0 ${ev.data} F7`;
  else s += ev.type + (ev.detail ? " " + ev.detail : "");
  const pre = $("#monitor");
  const lines = (pre.textContent ? pre.textContent.split("\n") : []).concat(s).slice(-80);
  pre.textContent = lines.join("\n");
  pre.scrollTop = pre.scrollHeight;
}

// =====================================================================
// System, Display, Protokoll
// =====================================================================
const COMPONENT_NAMES = { renderer: "LED-Steuerung", midi: "Piano (MIDI)", network: "WLAN", lcd: "Display", web: "Weboberfläche" };
const kv = (k, v) => el("div.kv", {}, [el("b", { text: k }), el("span", { text: v == null || v === "" ? "–" : String(v) })]);
function renderStatus() {
  const s = App.status;
  if (!s) return;
  const sys = s.system;
  const up = sys.uptime_s;
  $("#sys-info").replaceChildren(
    kv("Version", s.version), kv("Modell", sys.model), kv("Name", `${s.hostname}.local`),
    kv("CPU-Temperatur", sys.cpu_temp != null ? `${sys.cpu_temp} °C` : null),
    kv("Auslastung", (sys.load || []).join(" · ")), kv("Freier Speicher", sys.mem_free_mb != null ? `${sys.mem_free_mb} MB` : null),
    kv("Läuft seit", up > 3600 ? `${Math.floor(up / 3600)} h ${Math.floor(up % 3600 / 60)} min` : `${Math.floor(up / 60)} min`),
    kv("Stromversorgung", undervoltage(sys.throttled) || "in Ordnung"),
  );
  const comps = Object.assign({ renderer: { alive: s.renderer.heartbeat_age_s < 5, restarts: s.renderer.driver_resets } }, s.components || {});
  $("#sys-components").replaceChildren(...Object.entries(comps).map(([k, c]) => el("div.kv", {}, [
    el("b", { text: COMPONENT_NAMES[k] || k }),
    el("span", { style: { color: c.alive ? "var(--ok)" : "var(--err)" }, text: (c.alive ? "läuft" : "neu gestartet …") + (c.restarts ? ` · ${c.restarts}× neu gestartet` : "") }),
  ])));
  $("#sys-components").append(kv("Rechenzeit pro Bild", `${s.renderer.frame_ms} ms (max. ${s.renderer.max_frame_ms} ms)`));
  const m = s.midi;
  $("#midi-info").replaceChildren(
    kv("Status", m.connected ? (m.alive ? "Verbunden, sendet" : "Verbunden") : "Nicht verbunden"),
    kv("Gerät", m.port), kv("Empfangene Nachrichten", m.messages), m.error ? kv("Hinweis", m.error) : "",
  );
  const rec = s.recorder, pl = s.player;
  $("#rec-state").textContent = rec.recording ? `● Aufnahme läuft: ${Math.round(rec.seconds)} s, ${rec.events} Ereignisse` : "Aufnahmen landen als MIDI-Datei in der Songliste.";
  $("#player-state").textContent = pl.playing ? `Spielt „${pl.song}“ (${Math.round(pl.position)} / ${Math.round(pl.length)} s)` : "";
}
function undervoltage(t) {
  const v = parseInt(t, 16);
  if (!t || Number.isNaN(v) || v === 0) return "";
  if (v & 0x1) return "Unterspannung JETZT – Netzteil prüfen!";
  if (v & 0x10000) return "Unterspannung seit dem Start aufgetreten";
  if (v & 0x6) return "Gedrosselt (Temperatur)";
  return "";
}
async function systemAction(action) {
  const texts = {
    update: ["Update installieren?", "Holt die neueste Version und startet den Dienst neu. Dauert etwa eine Minute.", "Installieren"],
    restart: ["Dienst neu starten?", "Die LEDs gehen kurz aus. Die App verbindet sich danach von selbst neu.", "Neu starten"],
    reboot: ["Raspberry Pi neu starten?", "Dauert etwa 30 Sekunden.", "Neu starten"],
    shutdown: ["Ausschalten?", "Danach kannst du den Strom trennen. Zum Einschalten Strom wieder anstecken.", "Ausschalten"],
  }[action];
  if (!(await confirmSheet(texts[0], texts[1], texts[2], action === "shutdown" ? "danger" : "primary"))) return;
  toast(action === "update" ? "Update läuft …" : "Wird ausgeführt …", "", 6000);
  try {
    const r = await api(`/api/system/${action}`, "POST");
    if (action === "update") sheet({ title: r.ok ? "Update installiert" : "Update fehlgeschlagen", content: el("pre.log", { text: r.detail || "" }), actions: [{ label: "OK" }] });
  } catch (e) { /* Verbindung bricht erwartungsgemäß ab */ }
}
function refreshLcd() {
  const img = new Image();
  img.onload = () => { $("#lcd-img").src = img.src; };
  img.src = "/api/lcd.png?t=" + Date.now();
}
async function loadLog() {
  try { const text = await api("/api/log"); const pre = $("#log"); const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20; pre.textContent = text || "Noch keine Einträge."; if (atEnd) pre.scrollTop = pre.scrollHeight; }
  catch (e) { /* offline */ }
}
$("#log-refresh").addEventListener("click", loadLog);
$("#cfg-export").addEventListener("click", () => {
  const a = el("a", { href: "data:application/json;charset=utf-8," + encodeURIComponent(JSON.stringify(App.cfg, null, 2)), download: "pianoled-einstellungen.json" });
  document.body.append(a); a.click(); a.remove();
});
$("#cfg-import").addEventListener("change", async e => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    const changes = {};
    for (const k of ["strip", "look", "transpose", "midi", "features", "presets"]) if (k in data) changes[k] = data[k];
    App.cfg = await api("/api/config", "POST", { changes });
    renderConfig();
    toast("Einstellungen geladen", "ok");
  } catch (err) { toast("Datei ungültig", "err"); }
});
$("#cfg-reset").addEventListener("click", async () => {
  if (!(await confirmSheet("Alles zurücksetzen?", "Farben, Streifen und Extras gehen auf Werkseinstellung. WLAN-Einstellungen bleiben erhalten.", "Zurücksetzen", "danger"))) return;
  App.cfg = await api("/api/config/reset", "POST");
  renderConfig();
  toast("Zurückgesetzt");
});

// =====================================================================
// Status, Banner, Chips
// =====================================================================
function applyStatus(s) {
  App.status = s;
  const piano = $("#chip-piano");
  piano.className = "chip " + (s.midi.connected ? "ok" : "err");
  piano.lastChild.textContent = s.midi.connected ? "Piano" : "Kein Piano";
  const w = s.wifi || {};
  const chip = $("#chip-wifi");
  const label = { wifi: w.ssid || "WLAN", hotspot: "Hotspot", connecting: "Verbinde …", offline: "Offline", unavailable: "Lokal" }[w.mode] || "WLAN";
  chip.className = "chip " + ({ wifi: "ok", hotspot: "warn", connecting: "", offline: "err" }[w.mode] || "");
  chip.lastChild.textContent = label;
  $("#calib-box").classList.toggle("hidden", !s.calibrating);
  renderKeys(s.keys);
  renderBanners();
  if ($("#view-wifi").classList.contains("active")) renderNet();
  renderStatus();
}
function renderBanners() {
  const s = App.status;
  const out = [];
  if (!App.online) out.push(el("div.banner.err", {}, [el("span.spin"), el("span", { text: "Verbindung zum Visualizer getrennt – verbinde neu …" })]));
  else if (s) {
    if (s.wifi && s.wifi.mode === "hotspot" && location.hash.slice(1) !== "wifi")
      out.push(el("div.banner.warn", {}, [el("span", { text: "Hotspot-Modus: kein WLAN verbunden." }), el("button.btn.sm", { text: "Einrichten", onclick: () => go("wifi") })]));
    const uv = undervoltage(s.system && s.system.throttled);
    if (uv && uv.includes("JETZT")) out.push(el("div.banner.err", { text: "⚡ Unterspannung! Das Netzteil ist zu schwach oder das Kabel zu dünn. Das kann Abstürze verursachen." }));
  }
  $("#banners").replaceChildren(...out);
}

// =====================================================================
// WebSocket
// =====================================================================
let wsRetry = 0, offlineTimer;
function connect() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  App.ws = ws;
  ws.onopen = () => { wsRetry = 0; clearTimeout(offlineTimer); if (!App.online) { App.online = true; renderBanners(); } };
  ws.onmessage = e => {
    const { topic, data } = JSON.parse(e.data);
    switch (topic) {
      case "hello":
        if (App.version && App.version !== data.build) { location.reload(); return; }   // neue Version installiert
        App.version = data.build; App.cfg = data.config; renderConfig(); applyStatus(data.status); break;
      case "status": applyStatus(data); break;
      case "keys": if (App.status) App.status.keys = data; renderKeys(data); break;
      case "config": App.cfg = data; renderConfig(); break;
      case "transpose": setLocal("transpose.semitones", data.semitones); renderTranspose(); break;
      case "calibration":
        $("#calib-box").classList.toggle("hidden", !data.active);
        if (!data.active) data.result == null ? toast("Keine Taste erkannt", "err") : toast(`Transpose erkannt: ${data.result ? signed(data.result) : "0"}`, "ok");
        break;
      case "wifi": if (App.status) { App.status.wifi = data; applyStatus(App.status); } break;
      case "midi":
        if (App.status) {
          const was = App.status.midi.connected;
          App.status.midi = data; applyStatus(App.status); renderMidiPorts();
          if (was !== data.connected) toast(data.connected ? "Piano verbunden" : "Piano getrennt", data.connected ? "ok" : "err");
        }
        break;
      case "monitor": monitorLine(data); break;
      case "preset": if (App.status) App.status.active_preset = data.active; renderPresets(); toast(`Preset „${data.name}“`); break;
      case "restart": toast(`${COMPONENT_NAMES[data.component] || data.component} wurde automatisch neu gestartet`); break;
    }
  };
  ws.onclose = () => {
    App.ws = null;
    clearTimeout(offlineTimer);
    offlineTimer = setTimeout(() => { App.online = false; renderBanners(); }, 1500);
    setTimeout(connect, Math.min(5000, 600 * 2 ** wsRetry++));
  };
  ws.onerror = () => ws.close();
}
async function reloadConfig() { App.cfg = await api("/api/config"); renderConfig(); }

// =====================================================================
// Start
// =====================================================================
function init() {
  $$("[data-icon]").forEach(n => n.prepend(icon(n.dataset.icon)));
  buildPiano();
  bindControls();
  $("#tp-up").addEventListener("click", () => transpose({ delta: 1 }));
  $("#tp-down").addEventListener("click", () => transpose({ delta: -1 }));
  $("#tp-zero").addEventListener("click", () => transpose({ semitones: 0 }));
  $("#tp-calibrate").addEventListener("click", () => { haptic(); api("/api/transpose/calibrate", "POST").then(() => $("#calib-box").classList.remove("hidden")); });
  $("#panic").addEventListener("click", () => { haptic(20); api("/api/panic", "POST").then(() => toast("Alle LEDs aus")); });
  api("/api/meta").then(meta => {
    $("#anim").replaceChildren(...Object.entries(meta.animations).map(([k, v]) => el("option", { value: k, text: v })));
    if (App.cfg) renderConfig();
  }).catch(() => {});
  App.online = true;
  connect();
  route_();
  document.addEventListener("visibilitychange", () => { if (!document.hidden && App.ws && App.ws.readyState === 1) App.ws.send("status"); });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
}
init();
