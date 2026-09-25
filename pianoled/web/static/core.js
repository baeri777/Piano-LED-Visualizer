/* Piano LED – gemeinsame Helfer für App und WLAN-Einrichtung (ohne Framework). */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/** Element bauen: el("div.card", {onclick}, [kinder]) – Texte immer als Text, nie als HTML. */
function el(spec, props = {}, children = []) {
  const [tag, ...classes] = spec.split(".");
  const node = document.createElement(tag || "div");
  if (classes.length) node.className = classes.join(" ");
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "text") node.textContent = v;
    else if (k === "style") Object.assign(node.style, v);
    else if (k in node && typeof v !== "string") node[k] = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of [].concat(children)) if (c !== null && c !== undefined && c !== false) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}

const ICONS = {
  play: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  palette: '<circle cx="13.5" cy="6.5" r="1.5"/><circle cx="17.5" cy="10.5" r="1.5"/><circle cx="8.5" cy="7.5" r="1.5"/><circle cx="6.5" cy="12.5" r="1.5"/><path d="M12 2a10 10 0 0 0 0 20c1.1 0 2-.9 2-2 0-.5-.2-1-.5-1.3-.3-.4-.5-.8-.5-1.3 0-1.1.9-2 2-2h2.4A5.6 5.6 0 0 0 22 9.8C22 5.5 17.5 2 12 2z"/>',
  wifi: '<path d="M5 12.5a10 10 0 0 1 14 0"/><path d="M1.5 9a15 15 0 0 1 21 0"/><path d="M8.5 16a5 5 0 0 1 7 0"/><circle cx="12" cy="19.5" r="1"/>',
  wifiOff: '<path d="M2 2l20 20"/><path d="M8.5 16a5 5 0 0 1 7 0"/><path d="M5 12.5a10 10 0 0 1 5.2-2.8"/><path d="M1.5 9a15 15 0 0 1 4.5-3"/><circle cx="12" cy="19.5" r="1"/>',
  hotspot: '<circle cx="12" cy="12" r="2"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19.1 4.9a10 10 0 0 1 0 14.2M4.9 19.1a10 10 0 0 1 0-14.2"/>',
  more: '<circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  eyeOff: '<path d="M3 3l18 18"/><path d="M10.6 5.1A10.6 10.6 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3 3.7M6.6 6.6A17 17 0 0 0 2 12s3.6 7 10 7a9.6 9.6 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
  check: '<path d="M20 6L9 17l-5-5"/>',
  x: '<path d="M18 6L6 18M6 6l12 12"/>',
  chevron: '<path d="M9 6l6 6-6 6"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  power: '<path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.8 0"/>',
  strip: '<rect x="2" y="9" width="20" height="6" rx="3"/><path d="M6.5 12h.01M10.5 12h.01M14.5 12h.01M18.5 12h.01"/>',
  star: '<path d="M12 2l3 6.3 6.9.9-5 4.8 1.2 6.9L12 17.6 5.9 20.9 7.1 14 2 9.2l6.9-.9z"/>',
  screen: '<rect x="4" y="3" width="16" height="18" rx="2"/><rect x="7" y="6" width="10" height="8" rx="1"/><path d="M9 18h6"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  transpose: '<path d="M7 4v16M7 4l-3 3M7 4l3 3M17 20V4M17 20l-3-3M17 20l3-3"/>',
  midi: '<circle cx="12" cy="12" r="9"/><circle cx="8" cy="11" r="1"/><circle cx="16" cy="11" r="1"/><circle cx="12" cy="8" r="1"/><circle cx="9.5" cy="14.5" r="1"/><circle cx="14.5" cy="14.5" r="1"/>',
  log: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
};

function icon(name, cls) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("fill", "none");
  s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "2");
  s.setAttribute("stroke-linecap", "round");
  s.setAttribute("stroke-linejoin", "round");
  if (cls) s.setAttribute("class", cls);
  s.innerHTML = ICONS[name] || "";
  return s;
}

/** Leichtes haptisches Feedback (Android); iOS ignoriert es still. */
const haptic = (ms = 8) => { try { navigator.vibrate && navigator.vibrate(ms); } catch (e) { /* egal */ } };

async function api(path, method = "GET", body) {
  const opts = { method, headers: {} };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  const type = res.headers.get("content-type") || "";
  const data = type.includes("json") ? await res.json() : await res.text();
  if (!res.ok || (data && data.ok === false && data.error)) throw new Error((data && data.error) || res.statusText);
  return data;
}

let toastTimer;
function toast(text, kind = "", ms = 2400) {
  let t = $("#toast");
  if (!t) { t = el("div.toast", { id: "toast", role: "status" }); document.body.append(t); }
  t.textContent = text;
  t.className = "toast show " + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), ms);
}

/**
 * Bottom-Sheet. actions: [{label, kind, value}] → Promise mit value (oder null bei Abbruch).
 * Mit input: {placeholder, value} wird der eingegebene Text zurückgegeben.
 */
function sheet({ title, text, actions = [{ label: "OK", kind: "primary", value: true }], input, content }) {
  return new Promise(resolve => {
    let field;
    const close = v => { scrim.remove(); resolve(v); };
    const body = [el("h3", { text: title })];
    if (text) body.push(el("p", { text }));
    if (content) body.push(content);
    if (input) {
      field = el("input", { type: "text", placeholder: input.placeholder || "", value: input.value || "", maxlength: 40,
        onkeydown: e => { if (e.key === "Enter") close(field.value.trim() || null); } });
      body.push(field);
    }
    body.push(el("div.btns", {}, [
      el("button.btn", { text: "Abbrechen", onclick: () => close(null) }),
      ...actions.map(a => el("button.btn." + (a.kind || "primary"), { text: a.label, onclick: () => { haptic(); close(field ? (field.value.trim() || null) : a.value); } })),
    ]));
    const scrim = el("div.scrim", { onclick: e => { if (e.target === scrim) close(null); } }, [el("div.sheet", { role: "dialog" }, body)]);
    document.body.append(scrim);
    if (field) setTimeout(() => field.focus(), 250);
  });
}

const confirmSheet = (title, text, label, kind = "primary") => sheet({ title, text, actions: [{ label, kind, value: true }] });

function signalBars(signal) {
  const n = signal >= 75 ? 4 : signal >= 55 ? 3 : signal >= 35 ? 2 : 1;
  return el("span.bars", { "aria-label": `Signal ${signal} %` }, [1, 2, 3, 4].map(i => el("i" + (i <= n ? ".on" : ""))));
}

const noteName = n => ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"][n % 12] + (Math.floor(n / 12) - 1);
