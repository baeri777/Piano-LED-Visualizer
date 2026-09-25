/* Piano LED – Smartphone-Oberfläche (ohne Framework) */
(() => {
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
let cfg = null, status = null, meta = null, ws = null, saveTimer = null, pending = {};

// ---------- Hilfen ----------
const toast = (msg, ms = 2200) => { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), ms); };
const api = async (path, method = "GET", body) => {
  const r = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.error) || r.statusText);
  return data;
};
const get = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
const rgbToHex = c => "#" + (c || [0,0,0]).map(v => Math.max(0, Math.min(255, v|0)).toString(16).padStart(2, "0")).join("");
const hexToRgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
const noteName = n => ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"][n % 12] + (Math.floor(n / 12) - 1);

// Änderungen sammeln und gebündelt senden (max. alle 150 ms)
function setCfg(path, value, immediate = false) {
  pending[path] = value;
  // lokal sofort übernehmen, damit die UI nicht springt
  const parts = path.split("."); let o = cfg; for (const p of parts.slice(0, -1)) o = o[p]; o[parts.at(-1)] = value;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushCfg, immediate ? 0 : 150);
}
async function flushCfg() {
  const changes = pending; pending = {};
  if (!Object.keys(changes).length) return;
  try { cfg = await api("/api/config", "POST", { changes }); render(); }
  catch (e) { toast("Speichern fehlgeschlagen: " + e.message); }
}

// ---------- Tabs ----------
$$("nav button").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  $$("nav button").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  $$("section.tab").forEach(s => s.classList.toggle("active", s.id === "tab-" + name));
  try { localStorage.setItem("tab", name); } catch (e) {}
  if (name === "wifi") loadWifi();
  if (name === "extras") { loadSongs(); }
  if (name === "system") renderSystem();
}

// ---------- Bindings ----------
function bindInputs() {
  $$("[data-cfg]").forEach(el => {
    const path = el.dataset.cfg;
    const ev = el.type === "range" ? "input" : "change";
    el.addEventListener(ev, () => {
      let v;
      if (el.type === "checkbox") v = el.checked;
      else if (el.dataset.int !== undefined || el.type === "range" && el.step === "" ) v = parseInt(el.value, 10);
      else if (el.dataset.float !== undefined || (el.type === "range" && el.step)) v = parseFloat(el.value);
      else if (el.type === "number") v = Number(el.value);
      else v = el.value;
      if (Number.isNaN(v)) return;
      const valEl = $(`[data-val="${path}"]`); if (valEl) valEl.textContent = v + (valEl.dataset.unit || "");
      setCfg(path, v);
    });
  });
  $$("[data-color]").forEach(el => el.addEventListener("input", () => setCfg(el.dataset.color, hexToRgb(el.value))));
  $$("[data-seg]").forEach(seg => $$("button", seg).forEach(b => b.addEventListener("click", () => {
    const v = seg.dataset.int !== undefined ? parseInt(b.dataset.v, 10) : b.dataset.v;
    $$("button", seg).forEach(x => x.classList.toggle("active", x === b));
    setCfg(seg.dataset.seg, v, true); render();
  })));
  $$("[data-test]").forEach(b => b.addEventListener("click", () => api("/api/test", "POST", JSON.parse(b.dataset.test)).then(() => toast("Testbild läuft"))));
}

function render() {
  if (!cfg) return;
  $$("[data-cfg]").forEach(el => {
    const v = get(cfg, el.dataset.cfg); if (v === undefined) return;
    if (el.type === "checkbox") el.checked = !!v; else if (document.activeElement !== el) el.value = v;
    const valEl = $(`[data-val="${el.dataset.cfg}"]`); if (valEl) valEl.textContent = v + (valEl.dataset.unit || "");
  });
  $$("[data-color]").forEach(el => { const v = get(cfg, el.dataset.color); if (v) el.value = rgbToHex(v); });
  $$("[data-seg]").forEach(seg => { const v = String(get(cfg, seg.dataset.seg)); $$("button", seg).forEach(b => b.classList.toggle("active", b.dataset.v === v)); });
  const cm = cfg.look.color_mode;
  $("#look-single").style.display = cm === "single" ? "" : "none";
  $("#look-multicolor").style.display = cm === "multicolor" ? "" : "none";
  $("#look-rainbow").style.display = cm === "rainbow" ? "" : "none";
  $("#transpose-value").textContent = (cfg.transpose.semitones > 0 ? "+" : "") + cfg.transpose.semitones;
  const rs = cfg.transpose.roland_sysex;
  $("#sysex-status").textContent = rs.address ? `Eingerichtet: Adresse ${rs.address.map(b => b.toString(16).padStart(2, "0")).join(" ")}, Basiswert ${rs.base_value}` : "Noch nicht eingerichtet.";
  const dens = Math.round(1000 / cfg.strip.led_pitch_mm);
  $("#led-density").value = [144, 120, 96, 60].includes(dens) ? String(dens) : "0";
  $("#rec-ui").style.display = cfg.features.recorder.enabled ? "" : "none";
  $("#player-ui").style.display = cfg.features.player.enabled ? "" : "none";
  renderMulticolor(); renderPresets(); buildPiano();
}

// Multicolor-Bereiche
function renderMulticolor() {
  const list = $("#mc-list"); list.innerHTML = "";
  cfg.look.multicolor.forEach((e, i) => {
    const row = document.createElement("div"); row.className = "mc";
    row.innerHTML = `<input type="color" value="${rgbToHex(e.color)}"><input type="number" min="0" max="127" value="${e.range[0]}"><span>bis</span><input type="number" min="0" max="127" value="${e.range[1]}"><button class="btn small danger">✕</button>`;
    const [col, a, b, del] = row.children.length === 5 ? [row.children[0], row.children[1], row.children[3], row.children[4]] : [];
    const upd = () => { const mc = cfg.look.multicolor.slice(); mc[i] = { color: hexToRgb(col.value), range: [parseInt(a.value, 10) || 0, parseInt(b.value, 10) || 0] }; setCfg("look.multicolor", mc); };
    col.addEventListener("input", upd); a.addEventListener("change", upd); b.addEventListener("change", upd);
    del.addEventListener("click", () => { const mc = cfg.look.multicolor.slice(); mc.splice(i, 1); setCfg("look.multicolor", mc, true); });
    list.appendChild(row);
  });
}
$("#mc-add").addEventListener("click", () => setCfg("look.multicolor", cfg.look.multicolor.concat([{ color: [255, 255, 255], range: [21, 108] }]), true));
[["#ffffff","Weiß"],["#ff3b30","Rot"],["#ff9500","Orange"],["#ffcc00","Gelb"],["#34c759","Grün"],["#00c7be","Türkis"],["#007aff","Blau"],["#af52de","Lila"],["#ff2d55","Pink"]].forEach(([hex, name]) => {
  const b = document.createElement("button"); b.className = "btn small"; b.textContent = name; b.style.borderColor = hex;
  b.addEventListener("click", () => setCfg("look.color", hexToRgb(hex), true)); $("#quick-colors").appendChild(b);
});
$("#led-density").addEventListener("change", e => { const d = parseInt(e.target.value, 10); if (d > 0) setCfg("strip.led_pitch_mm", 1000 / d, true); });

// Presets
function renderPresets() {
  const list = $("#preset-list"); list.innerHTML = "";
  if (!cfg.presets.length) { list.innerHTML = '<p class="hint">Noch keine Presets. Stelle Farben ein und speichere sie hier.</p>'; return; }
  cfg.presets.forEach(p => {
    const item = document.createElement("div"); item.className = "item";
    const active = status && status.active_preset === p.id;
    item.innerHTML = `<i style="width:14px;height:14px;border-radius:50%;background:${rgbToHex(p.look.color)}"></i><span>${p.name}${active ? " ✓" : ""}</span><button class="btn small">Laden</button><button class="btn small danger">✕</button>`;
    item.children[2].addEventListener("click", () => api(`/api/presets/${p.id}/apply`, "POST").then(refreshConfig));
    item.children[3].addEventListener("click", () => api(`/api/presets/${p.id}`, "DELETE").then(refreshConfig));
    list.appendChild(item);
  });
}
$("#btn-save-preset").addEventListener("click", () => { const name = prompt("Name des Presets:"); if (name) api("/api/presets", "POST", { name }).then(refreshConfig); });
$("#btn-panic").addEventListener("click", () => api("/api/panic", "POST").then(() => toast("Alle LEDs aus")));

// Piano-Darstellung
const LOW = 21, HIGH = 108;
function buildPiano() {
  const piano = $("#piano"); if (piano.dataset.built) return; piano.dataset.built = "1";
  const whites = []; for (let n = LOW; n <= HIGH; n++) if (![1,3,6,8,10].includes(n % 12)) whites.push(n);
  const w = 100 / whites.length;
  let wi = 0;
  for (let n = LOW; n <= HIGH; n++) {
    const black = [1,3,6,8,10].includes(n % 12);
    const el = document.createElement("div"); el.dataset.note = n;
    if (black) { el.className = "black"; el.style.left = (wi * w - w * 0.3) + "%"; el.style.width = (w * 0.6) + "%"; }
    else { el.className = "white"; el.style.left = (wi * w) + "%"; el.style.width = w + "%"; wi++; }
    piano.appendChild(el);
  }
}
function renderKeys(k) {
  const lit = new Set(k.lit || []);
  $$("#piano div").forEach(el => el.classList.toggle("lit", lit.has(parseInt(el.dataset.note, 10))));
  $("#live-hint").textContent = (status && status.midi.connected) ? `${k.lit.length} Taste(n) aktiv${k.sustain ? " · Sustain" : ""}${k.idle_animation ? " · Leerlauf-Animation" : ""}` : "Kein Piano verbunden";
  const ph = `Geschätzter Strom: ${k.estimated_ma} mA${k.power_scale < 1 ? ` · gedimmt auf ${Math.round(k.power_scale * 100)} % (Netzteil-Limit)` : ""}`;
  $("#power-hint").textContent = ph; $("#power-hint2").textContent = ph;
}
async function renderSimStrip() {
  if (!status || !status.simulate) { $("#strip").style.display = "none"; return; }
  try { const { frame } = await api("/api/sim/frame"); if (!frame) return; const s = $("#strip");
    if (s.children.length !== frame.length) { s.innerHTML = ""; frame.forEach(() => s.appendChild(document.createElement("i"))); }
    frame.forEach((c, i) => s.children[i].style.background = `rgb(${c[0]},${c[1]},${c[2]})`); } catch (e) {}
}

// Transpose
$("#tr-minus").addEventListener("click", () => api("/api/transpose", "POST", { delta: -1 }).then(refreshConfig));
$("#tr-plus").addEventListener("click", () => api("/api/transpose", "POST", { delta: 1 }).then(refreshConfig));
$("#tr-zero").addEventListener("click", () => api("/api/transpose", "POST", { semitones: 0 }).then(refreshConfig));
$("#btn-calibrate").addEventListener("click", () => api("/api/transpose/calibrate", "POST").then(() => { $("#calib-status").textContent = "Jetzt die tiefste Taste (A0) am Piano drücken …"; }));
$("#learn-start").addEventListener("click", () => api("/api/transpose/learn/start", "POST").then(() => $("#learn-status").textContent = "Schritt 1: Transpose am Piano auf 0, dann „Nächster Schritt“."));
$("#learn-next").addEventListener("click", () => api("/api/transpose/learn/next", "POST").then(r => $("#learn-status").textContent = `Schritt ${r.steps} erfasst. Jetzt Transpose um +1 erhöhen, dann weiter.`));
$("#learn-finish").addEventListener("click", () => api("/api/transpose/learn/finish", "POST").then(r => { $("#learn-status").textContent = r.ok ? "Erfolgreich eingerichtet." : r.reason; refreshConfig(); }));

// WLAN
async function loadWifi() {
  try {
    const { state, known } = await api("/api/wifi");
    const el = $("#wifi-state");
    if (!state.available) el.innerHTML = '<p class="hint">WLAN-Verwaltung nicht verfügbar (kein NetworkManager).</p>';
    else if (state.hotspot) el.innerHTML = `<div class="kv"><b>Modus</b><span>Hotspot „${state.hotspot_ssid}“</span></div><div class="kv"><b>Adresse</b><span>${state.ip || "10.42.0.1"}</span></div><div class="kv"><b>Verbundene Geräte</b><span>${state.hotspot_clients}</span></div>`;
    else if (state.connected) el.innerHTML = `<div class="kv"><b>Netzwerk</b><span>${state.ssid}</span></div><div class="kv"><b>Adresse</b><span>${state.ip || "–"}</span></div><div class="kv"><b>Name</b><span>pianoled.local</span></div>`;
    else el.innerHTML = '<p class="hint">Nicht verbunden.</p>';
    const kl = $("#wifi-known"); kl.innerHTML = known.length ? "<p class='hint'>Gespeicherte Netzwerke:</p>" : "";
    known.forEach(ssid => { const i = document.createElement("div"); i.className = "item"; i.innerHTML = `<span>${ssid}</span><button class="btn small danger">Vergessen</button>`;
      i.children[1].addEventListener("click", () => api("/api/wifi/forget", "POST", { ssid }).then(loadWifi)); kl.appendChild(i); });
  } catch (e) { toast("WLAN-Status nicht abrufbar"); }
}
$("#wifi-scan").addEventListener("click", async () => {
  const b = $("#wifi-scan"); b.disabled = true; b.textContent = "Suche …";
  try { const { networks } = await api("/api/wifi/scan"); const l = $("#wifi-list"); l.innerHTML = networks.length ? "" : '<p class="hint">Keine Netzwerke gefunden (im Hotspot-Betrieb ist keine Suche möglich).</p>';
    networks.forEach(n => { const i = document.createElement("div"); i.className = "item"; i.innerHTML = `<span>${n.ssid}</span><small class="pill">${n.signal} %</small>`; i.addEventListener("click", () => { $("#wifi-ssid").value = n.ssid; $("#wifi-pass").focus(); }); l.appendChild(i); });
  } catch (e) { toast("Suche fehlgeschlagen"); } finally { b.disabled = false; b.textContent = "Netzwerke suchen"; }
});
$("#wifi-connect").addEventListener("click", async () => {
  const ssid = $("#wifi-ssid").value.trim(); if (!ssid) return toast("SSID fehlt");
  toast("Verbinde … (kann bis zu 60 s dauern)", 6000);
  try { const r = await api("/api/wifi/connect", "POST", { ssid, password: $("#wifi-pass").value }); toast(r.ok ? "Verbunden mit " + ssid : "Fehlgeschlagen: " + r.detail, 5000); loadWifi(); }
  catch (e) { toast("Verbindung unterbrochen. Handy mit demselben WLAN verbinden und http://pianoled.local öffnen.", 8000); }
});
$("#hotspot-now").addEventListener("click", () => api("/api/wifi/hotspot", "POST", { enabled: true }).then(() => toast("Hotspot wird gestartet")));
$("#hotspot-stop").addEventListener("click", () => api("/api/wifi/hotspot", "POST", { enabled: false }).then(() => toast("Hotspot beendet")));

// Extras: Songs, Recorder, Monitor
async function loadSongs() {
  if (!cfg || !cfg.features.player.enabled) return;
  try { const { songs } = await api("/api/songs"); const l = $("#song-list"); l.innerHTML = songs.length ? "" : '<p class="hint">Keine MIDI-Dateien im Ordner Songs.</p>';
    songs.forEach(s => { const i = document.createElement("div"); i.className = "item"; i.innerHTML = `<span>${s.name}</span><button class="btn small primary">▶</button><button class="btn small danger">✕</button>`;
      i.children[1].addEventListener("click", () => api("/api/player/play", "POST", { name: s.name }).then(() => toast("Wiedergabe: " + s.name)).catch(e => toast(e.message)));
      i.children[2].addEventListener("click", () => api(`/api/songs/${encodeURIComponent(s.name)}`, "DELETE").then(loadSongs)); l.appendChild(i); });
  } catch (e) {}
}
$("#song-upload").addEventListener("change", async e => { const fd = new FormData(); for (const f of e.target.files) fd.append("file", f); await fetch("/api/songs/upload", { method: "POST", body: fd }); toast("Hochgeladen"); loadSongs(); });
$("#player-stop").addEventListener("click", () => api("/api/player/stop", "POST"));
$("#rec-start").addEventListener("click", () => api("/api/recorder/start", "POST").then(() => toast("Aufnahme läuft")).catch(e => toast(e.message)));
$("#rec-stop").addEventListener("click", () => { const name = prompt("Dateiname (leer = Datum):") ?? ""; api("/api/recorder/stop", "POST", { name }).then(r => toast(r.ok ? "Gespeichert: " + r.file : "Nichts aufgenommen")).catch(e => toast(e.message)); });
$("#rec-cancel").addEventListener("click", () => api("/api/recorder/cancel", "POST"));
$("#monitor-toggle").addEventListener("change", e => api("/api/monitor", "POST", { enabled: e.target.checked }).then(() => { if (!e.target.checked) $("#monitor-log").textContent = ""; }));
function monitorLine(ev) {
  const t = new Date(ev.t * 1000).toLocaleTimeString("de-DE");
  let s = `${t}  ${ev.type}`;
  if (ev.type === "note_on" || ev.type === "note_off") s += `  ${noteName(ev.note)} (${ev.note})${ev.velocity !== undefined ? " vel " + ev.velocity : ""} ch ${ev.channel + 1}`;
  else if (ev.type === "cc") s += `  CC${ev.control} = ${ev.value} ch ${ev.channel + 1}`;
  else if (ev.type === "sysex") s += `  F0 ${ev.data} F7`;
  else if (ev.detail) s += "  " + ev.detail;
  const log = $("#monitor-log"); log.textContent = (log.textContent + s + "\n").split("\n").slice(-60).join("\n"); log.scrollTop = log.scrollHeight;
}

// System
function renderSystem() {
  if (!status) return;
  const s = status.system, kv = (k, v) => `<div class="kv"><b>${k}</b><span>${v ?? "–"}</span></div>`;
  $("#sys-info").innerHTML = kv("Version", s.version) + kv("Modell", s.model) + kv("CPU-Temperatur", s.cpu_temp != null ? s.cpu_temp + " °C" : null) + kv("Last", (s.load || []).join(" / ")) + kv("Freier RAM", s.mem_free_mb != null ? s.mem_free_mb + " MB" : null) + kv("Laufzeit", Math.floor(s.uptime_s / 60) + " min") + kv("Renderer", `${status.renderer.tick_ms} ms/Frame (max ${status.renderer.max_tick_ms})`) + (s.throttled && s.throttled !== "0x0" ? kv("Warnung", "Unterspannung/Drosselung: " + s.throttled) : "");
  const m = status.midi;
  $("#midi-info").innerHTML = kv("Status", m.connected ? "Verbunden" : "Nicht verbunden") + kv("Port", m.port) + kv("Nachrichten", m.messages) + kv("Verfügbar", (m.available || []).join("<br>") || "keine") + (m.error ? kv("Fehler", m.error) : "");
}
const sysAction = (action, confirmText) => async () => { if (confirmText && !confirm(confirmText)) return; try { const r = await api(`/api/system/${action}`, "POST"); toast(r.ok ? "OK" : "Fehler: " + r.detail, 4000); } catch (e) { toast("Verbindung getrennt (Aktion läuft)"); } };
$("#sys-restart").addEventListener("click", sysAction("restart"));
$("#sys-update").addEventListener("click", sysAction("update", "Update per git pull ausführen und Dienst neu starten?"));
$("#sys-reboot").addEventListener("click", sysAction("reboot", "Raspberry Pi neu starten?"));
$("#sys-shutdown").addEventListener("click", sysAction("shutdown", "Raspberry Pi herunterfahren?"));
$("#cfg-reset").addEventListener("click", () => { if (confirm("Alle Einstellungen zurücksetzen?")) api("/api/config/reset", "POST").then(refreshConfig); });
$("#cfg-export").addEventListener("click", () => { const a = document.createElement("a"); a.href = "data:application/json," + encodeURIComponent(JSON.stringify(cfg, null, 2)); a.download = "pianoled-config.json"; a.click(); });
$("#log-load").addEventListener("click", async () => { $("#log-view").textContent = await api("/api/log"); });

// ---------- Status / WebSocket ----------
function applyStatus(s) {
  status = s;
  const pm = $("#pill-midi"); pm.textContent = s.midi.connected ? "Piano ✓" : "Kein Piano"; pm.className = "pill " + (s.midi.connected ? "ok" : "err");
  const pw = $("#pill-wifi"); const w = s.wifi || {};
  pw.textContent = w.hotspot ? "Hotspot" : (w.connected ? (w.ssid || "WLAN") : "Kein WLAN"); pw.className = "pill " + (w.hotspot ? "warn" : (w.connected ? "ok" : "err"));
  if (s.keys) renderKeys(s.keys);
  if (s.calibrating) $("#calib-status").textContent = "Jetzt die tiefste Taste (A0) am Piano drücken …";
  if (s.player) $("#player-status").textContent = s.player.playing ? `Spielt: ${s.player.song} (${Math.round(s.player.position)} / ${Math.round(s.player.length)} s)` : "";
  if (s.recorder) $("#rec-status").textContent = s.recorder.recording ? `Aufnahme läuft: ${s.recorder.seconds} s, ${s.recorder.events} Ereignisse` : "";
  if ($("#tab-system").classList.contains("active")) renderSystem();
}
function connectWs() {
  ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onmessage = e => {
    const { topic, data } = JSON.parse(e.data);
    if (topic === "status") applyStatus(data);
    else if (topic === "keys") renderKeys(data);
    else if (topic === "config") { cfg = data; render(); }
    else if (topic === "transpose") { if (cfg) { cfg.transpose.semitones = data.semitones; render(); } }
    else if (topic === "calibration") { $("#calib-status").textContent = data.active ? "Jetzt die tiefste Taste (A0) am Piano drücken …" : (data.result === null || data.result === undefined ? "Abgebrochen (keine Taste erkannt)." : `Fertig: Transpose = ${data.result > 0 ? "+" : ""}${data.result}`); }
    else if (topic === "monitor") monitorLine(data);
    else if (topic === "midi") { if (status) { status.midi = data; applyStatus(status); } }
  };
  ws.onclose = () => setTimeout(connectWs, 2000);
}
async function refreshConfig() { cfg = await api("/api/config"); render(); }

// ---------- Start ----------
async function init() {
  bindInputs();
  try {
    [cfg, meta, status] = await Promise.all([api("/api/config"), api("/api/meta"), api("/api/status")]);
    const sel = $("#anim-select"); Object.entries(meta.animations).forEach(([k, v]) => { const o = document.createElement("option"); o.value = k; o.textContent = v; sel.appendChild(o); });
    render(); applyStatus(status);
  } catch (e) { toast("Keine Verbindung zum Visualizer"); }
  connectWs();
  setInterval(renderSimStrip, 200);
  let tab = "start"; try { tab = localStorage.getItem("tab") || "start"; } catch (e) {}
  showTab(tab);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
}
init();
})();
