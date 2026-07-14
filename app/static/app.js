"use strict";
const $ = (id) => document.getElementById(id);
const TOKKEY = "transcribe_token";
const OIDC_TOKEN_KEY = "transcribe_oidc_tokens";
const OIDC_FLOW_KEY = "transcribe_oidc_flow";
const HISTKEY = "transcribe_history";

const SAMPLES = [
  { file: "quick_note.mp3", label: "Note", meta: "1 spk · 14s" },
  { file: "quick_qa.mp3", label: "Q&A", meta: "2 spk · 32s" },
  { file: "friendly_conversation.mp3", label: "Catch-up", meta: "2 spk · 91s" },
  { file: "team_standup.mp3", label: "Standup", meta: "3 spk · 43s" },
];

let blob = null, blobName = "", blobUrl = null;
let authConfig = { mode: "static", oidc: { enabled: false } };
let oidcMetadata = null;
let oidcTokens = null;

/* ---------- helpers ---------- */
function token() { return (oidcTokens && oidcTokens.access_token) || ($("token").value || "").trim(); }
function authHeaders() { return { "Authorization": "Bearer " + token() }; }
function fmtTime(s) { s = Math.max(0, Math.floor(s || 0)); const m = Math.floor(s / 60);
  return String(m).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); }
function speakerLabel(raw) { return raw === "SPEAKER_??" ? "?" : raw; }

/* ---------- authentication ---------- */
function base64Url(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function randomUrlSafe(size = 32) { const bytes = new Uint8Array(size); crypto.getRandomValues(bytes); return base64Url(bytes); }
function redirectUri() { return location.origin + "/"; }
function loadOidcTokens() {
  try { return JSON.parse(sessionStorage.getItem(OIDC_TOKEN_KEY) || "null"); } catch { return null; }
}
function saveOidcTokens(value) {
  oidcTokens = value;
  if (value) sessionStorage.setItem(OIDC_TOKEN_KEY, JSON.stringify(value));
  else sessionStorage.removeItem(OIDC_TOKEN_KEY);
  renderAuth(); refreshGo();
}
async function metadata() {
  if (oidcMetadata) return oidcMetadata;
  const r = await fetch(authConfig.oidc.issuer + "/.well-known/openid-configuration", { cache: "no-store" });
  if (!r.ok) throw new Error("Could not load sign-in provider configuration.");
  oidcMetadata = await r.json();
  if ((oidcMetadata.issuer || "").replace(/\/$/, "") !== authConfig.oidc.issuer.replace(/\/$/, ""))
    throw new Error("Sign-in provider returned an unexpected issuer.");
  return oidcMetadata;
}
async function signIn() {
  try {
    const m = await metadata();
    const verifier = randomUrlSafe(64), state = randomUrlSafe(32);
    const challenge = base64Url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
    sessionStorage.setItem(OIDC_FLOW_KEY, JSON.stringify({ verifier, state }));
    const q = new URLSearchParams({ response_type: "code", client_id: authConfig.oidc.client_id,
      redirect_uri: redirectUri(), scope: authConfig.oidc.scopes.join(" "), state,
      code_challenge: challenge, code_challenge_method: "S256" });
    location.assign(m.authorization_endpoint + "?" + q.toString());
  } catch (e) { setErr(e.message); }
}
async function handleSignInCallback() {
  const q = new URLSearchParams(location.search);
  if (!q.has("code") && !q.has("error")) return;
  history.replaceState({}, document.title, location.pathname);
  if (q.has("error")) throw new Error("Sign-in failed: " + (q.get("error_description") || q.get("error")));
  let flow = null;
  try { flow = JSON.parse(sessionStorage.getItem(OIDC_FLOW_KEY) || "null"); } catch { /* handled below */ }
  sessionStorage.removeItem(OIDC_FLOW_KEY);
  if (!flow || q.get("state") !== flow.state) throw new Error("Sign-in response state did not match.");
  const m = await metadata();
  const body = new URLSearchParams({ grant_type: "authorization_code", client_id: authConfig.oidc.client_id,
    code: q.get("code"), redirect_uri: redirectUri(), code_verifier: flow.verifier });
  const r = await fetch(m.token_endpoint, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body });
  if (!r.ok) throw new Error("The sign-in code could not be exchanged.");
  const result = await r.json();
  result.obtained_at = Math.floor(Date.now() / 1000);
  saveOidcTokens(result);
}
async function refreshOidcToken() {
  if (!oidcTokens || !oidcTokens.refresh_token) return false;
  const m = await metadata();
  const body = new URLSearchParams({ grant_type: "refresh_token", client_id: authConfig.oidc.client_id,
    refresh_token: oidcTokens.refresh_token });
  const r = await fetch(m.token_endpoint, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body });
  if (!r.ok) { saveOidcTokens(null); return false; }
  const next = await r.json();
  saveOidcTokens({ ...oidcTokens, ...next, obtained_at: Math.floor(Date.now() / 1000) });
  return true;
}
function oidcTokenExpiring() {
  return oidcTokens && oidcTokens.expires_in && Date.now() / 1000 > oidcTokens.obtained_at + oidcTokens.expires_in - 60;
}
async function authorizedFetch(url, options = {}) {
  if (oidcTokenExpiring()) await refreshOidcToken();
  options.headers = { ...(options.headers || {}), ...authHeaders() };
  let r = await fetch(url, options);
  if (r.status === 401 && oidcTokens && await refreshOidcToken()) {
    options.headers = { ...(options.headers || {}), ...authHeaders() };
    r = await fetch(url, options);
  }
  return r;
}
async function signOut() {
  saveOidcTokens(null);
  sessionStorage.removeItem(TOKKEY);
  $("token").value = "";
  $("auth-user").textContent = "Not signed in";
}
async function renderAuth() {
  const oidcEnabled = !!authConfig.oidc.enabled;
  $("oidc-auth").hidden = !oidcEnabled;
  $("legacy-auth").hidden = authConfig.mode === "oidc";
  $("legacy-token-label").textContent = authConfig.mode === "hybrid" ? "Emergency API token" : "API token";
  $("sign-in").hidden = !!oidcTokens;
  $("sign-out").hidden = !oidcTokens;
  if (!oidcTokens) { $("auth-user").textContent = "Not signed in"; return; }
  try {
    const r = await authorizedFetch("/v1/auth/me");
    const me = r.ok ? await r.json() : null;
    $("auth-user").textContent = (me && (me.email || me.subject)) || "Signed in";
  } catch { $("auth-user").textContent = "Signed in"; }
}

/* ---------- init ---------- */
(async function init() {
  // Migrate the original persistent token once, then remove it from localStorage.
  const oldToken = localStorage.getItem(TOKKEY) || "";
  if (oldToken && !sessionStorage.getItem(TOKKEY)) sessionStorage.setItem(TOKKEY, oldToken);
  localStorage.removeItem(TOKKEY);
  $("token").value = sessionStorage.getItem(TOKKEY) || "";
  oidcTokens = loadOidcTokens();
  $("host-label").textContent = location.host;
  $("token").addEventListener("input", () => { sessionStorage.setItem(TOKKEY, ($("token").value || "").trim()); refreshGo(); });
  $("sign-in").addEventListener("click", signIn);
  $("sign-out").addEventListener("click", signOut);

  try {
    const configResponse = await fetch("/v1/auth/config", { cache: "no-store" });
    if (!configResponse.ok) throw new Error("Could not load authentication settings.");
    authConfig = await configResponse.json();
    if (authConfig.oidc.enabled) await handleSignInCallback();
    await renderAuth();
  } catch (e) { setErr(e.message); }

  // sample chips
  for (const s of SAMPLES) {
    const b = document.createElement("button");
    b.className = "sample-chip";
    b.innerHTML = `${s.label}<small>${s.meta}</small>`;
    b.addEventListener("click", () => loadSample(s));
    $("sample-chips").appendChild(b);
  }

  // dropzone
  const dz = $("dropzone");
  $("dz-inner").addEventListener("click", () => $("file").click());
  $("pick").addEventListener("click", (e) => { e.stopPropagation(); $("file").click(); });
  $("file").addEventListener("change", (e) => { const f = e.target.files[0]; if (f) setFile(f, f.name); });
  ["dragover", "dragenter"].forEach(ev => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach(ev => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) setFile(f, f.name); });
  $("clear-file").addEventListener("click", clearFile);

  $("record").addEventListener("click", toggleRecord);
  $("go").addEventListener("click", transcribe);
  $("edit-btn").addEventListener("click", toggleEdit);
  $("copy-btn").addEventListener("click", copyTranscript);
  $("dl-txt").addEventListener("click", () => download(currentUrls.txt, "transcript.txt"));
  $("dl-json").addEventListener("click", () => download(currentUrls.json, "transcript.json"));

  // settings drawer
  const open = () => { $("drawer").hidden = false; $("drawer-backdrop").hidden = false; };
  const close = () => { $("drawer").hidden = true; $("drawer-backdrop").hidden = true; };
  $("settings-btn").addEventListener("click", open);
  $("drawer-close").addEventListener("click", close);
  $("drawer-backdrop").addEventListener("click", close);

  $("clear-history").addEventListener("click", () => { localStorage.removeItem(HISTKEY); renderHistory(); });

  renderHistory();
  pollStatus();
  setInterval(pollStatus, 15000);
  if (!token()) open();
})();

/* ---------- status ---------- */
async function pollStatus() {
  try {
    const r = await fetch("/v1/health");
    const j = await r.json();
    $("status-dot").className = "dot ok";
    $("status-text").textContent = j.gpu ? "online · GPU" : "online";
  } catch {
    $("status-dot").className = "dot bad";
    $("status-text").textContent = "offline";
  }
}

/* ---------- file handling ---------- */
function setFile(b, name) {
  clearBlobUrl();
  blob = b; blobName = name; blobUrl = URL.createObjectURL(b);
  $("dz-inner").hidden = true;
  $("selected").hidden = false;
  $("sel-name").textContent = name;
  $("preview-audio").src = blobUrl;
  refreshGo();
}
function clearFile() {
  clearBlobUrl();
  blob = null; blobName = "";
  $("file").value = "";
  $("dz-inner").hidden = false;
  $("selected").hidden = true;
  $("preview-audio").removeAttribute("src");
  refreshGo();
}
function clearBlobUrl() { if (blobUrl) { URL.revokeObjectURL(blobUrl); blobUrl = null; } }
function refreshGo() { $("go").disabled = !(blob && token()); }

async function loadSample(s) {
  setErr("");
  try {
    const r = await fetch("/samples/" + s.file);
    if (!r.ok) throw new Error("sample not found");
    setFile(await r.blob(), s.file);
    if (!$("title").value) $("title").value = s.label.toLowerCase().replace(/\s+/g, "-");
  } catch (e) { setErr("Could not load sample: " + e.message); }
}

/* ---------- recording ---------- */
let mediaRec = null, chunks = [], recTimer = null, recStart = 0;
async function toggleRecord() {
  if (mediaRec && mediaRec.state === "recording") { mediaRec.stop(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    chunks = []; mediaRec = new MediaRecorder(stream);
    mediaRec.ondataavailable = (e) => chunks.push(e.data);
    mediaRec.onstop = () => {
      stream.getTracks().forEach(t => t.stop());
      clearInterval(recTimer); $("rec-timer").hidden = true;
      $("record").classList.remove("on"); $("record").innerHTML = '<span class="rec-dot"></span> Record';
      setFile(new Blob(chunks, { type: mediaRec.mimeType || "audio/webm" }), "recording.webm");
    };
    mediaRec.start();
    $("record").classList.add("on"); $("record").innerHTML = '<span class="rec-dot"></span> Stop';
    $("rec-timer").hidden = false; recStart = Date.now();
    recTimer = setInterval(() => { $("rec-timer").textContent = fmtTime((Date.now() - recStart) / 1000); }, 250);
  } catch (e) { setErr("Microphone unavailable: " + e.message + " (recording needs https or localhost)"); }
}

/* ---------- transcribe ---------- */
let currentUrls = { txt: "", json: "" };
let currentJobId = "";
function setErr(m) { const e = $("err"); e.hidden = !m; e.textContent = m; }

async function transcribe() {
  if (!blob || !token()) return;
  setErr(""); $("go").disabled = true; $("result").hidden = true;
  $("progress").hidden = false;
  const t0 = performance.now();
  const tick = setInterval(() => {
    $("progress-text").textContent = ((performance.now() - t0) / 1000).toFixed(0) + "s";
  }, 250);
  try {
    const fd = new FormData();
    fd.append("audio", blob, blobName);
    if ($("title").value.trim()) fd.append("title", $("title").value.trim());
    if ($("spk").value) fd.append("num_speakers", $("spk").value);
    // Empty value = auto-detect (omit the field). Defaults to English so an
    // unfiltered intro can't silently mis-detect the language.
    if ($("lang").value) fd.append("language", $("lang").value);
    // Output: "transcribe" (same as audio) or "translate" (force English).
    if ($("task").value) fd.append("task", $("task").value);

    const r = await authorizedFetch("/v1/transcribe", { method: "POST", body: fd });
    if (r.status === 401) throw new Error("Your sign-in or API token is no longer valid. Open Settings to sign in again.");
    if (!r.ok) throw new Error("Server error " + r.status + ": " + (await r.text()));
    const meta = await r.json();
    const wall = (performance.now() - t0) / 1000;

    const jr = await authorizedFetch(meta.transcript_json_url);
    const doc = await jr.json();

    currentUrls = { txt: meta.transcript_txt_url, json: meta.transcript_json_url };
    renderResult(doc, meta, wall, blobUrl);
    addHistory({ id: meta.id, title: $("title").value.trim() || "Untitled",
      created_at: doc.created_at, duration: meta.duration_seconds, speakers: meta.speakers_detected,
      language: meta.language, backend: doc.asr_backend, model: doc.model,
      txt: currentUrls.txt, json: currentUrls.json });
  } catch (e) {
    setErr(e.message);
  } finally {
    clearInterval(tick); $("progress").hidden = true; refreshGo();
  }
}

/* ---------- render ---------- */
// View state so we can re-render on speaker edits without refetching.
let view = { doc: null, meta: null, wall: 0, audioUrl: null };
let editLabels = [];   // one final speaker label per segment (the edit buffer)
let editMode = false;

function buildTurns(segs, labels) {
  const turns = [];
  (segs || []).forEach((s, i) => {
    const text = (s.text || "").trim(); if (!text) return;
    const spk = labels[i] || s.speaker || "SPEAKER_??";
    const last = turns[turns.length - 1];
    if (last && last.speaker === spk) { last.text += " " + text; last.idxs.push(i); }
    else turns.push({ speaker: spk, start: +s.start || 0, text, idxs: [i] });
  });
  return turns;
}

function distinctLabels() {
  const seen = [];
  for (const l of editLabels) if (l !== "SPEAKER_??" && !seen.includes(l)) seen.push(l);
  return seen;
}
function isDirty() {
  const orig = (view.doc && view.doc.segments || []).map(s => s.speaker || "SPEAKER_??");
  return editLabels.length === orig.length && editLabels.some((l, i) => l !== orig[i]);
}

function renderResult(doc, meta, wall, audioUrl) {
  view = { doc, meta, wall, audioUrl };
  currentJobId = doc.id || (meta && meta.id) || "";
  editLabels = (doc.segments || []).map(s => s.speaker || "SPEAKER_??");
  editMode = false;
  renderView();
  $("result").hidden = false;
  $("result").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderView() {
  const { doc, meta, wall, audioUrl } = view;
  const gpu = (doc.asr_backend || "").startsWith("speaches");
  $("result-title").textContent = doc.id ? ($("title").value.trim() || "Transcript") : "Transcript";
  $("edit-btn").textContent = editMode ? "Done" : "✎ Edit speakers";
  $("edit-btn").classList.toggle("primary", editMode);

  const speakersNow = distinctLabels().length;
  const translated = (doc.task || meta.task) === "translate";
  $("meta").innerHTML = "";
  const chips = [
    `${(meta.duration_seconds || 0).toFixed(1)}s audio`,
    `${wall ? wall.toFixed(1) + "s processing" : ""}`,
    `${speakersNow} speaker${speakersNow === 1 ? "" : "s"}`,
    translated ? `${meta.language || "?"} → English (translated)` : `lang ${meta.language || "?"}`,
  ];
  for (const c of chips) { if (!c) continue; const s = document.createElement("span"); s.textContent = c; $("meta").appendChild(s); }
  const bk = document.createElement("span");
  bk.className = gpu ? "gpu" : "cpu";
  bk.textContent = gpu ? "GPU · " + shortModel(doc.model) : "CPU · " + shortModel(doc.model);
  $("meta").appendChild(bk);

  const ra = $("result-audio");
  if (audioUrl) { ra.src = audioUrl; ra.hidden = false; } else { ra.removeAttribute("src"); ra.hidden = true; }

  renderTranscript();
}

// Stable color per label so renames keep their hue.
function labelColor(label, order) {
  if (label === "SPEAKER_??") return "var(--muted)";
  return `var(--spk${order % 6})`;
}
// Pretty display: anonymous SPEAKER_00 -> "Speaker 1"; custom names shown as-is.
function displaySpeaker(label, order) {
  if (label === "SPEAKER_??") return "Speaker ?";
  if (/^SPEAKER_\d+$/.test(label)) return "Speaker " + (order + 1);
  return label;
}
function avatarFor(label, order) {
  if (label === "SPEAKER_??") return "?";
  if (/^SPEAKER_\d+$/.test(label)) return String(order + 1);
  return (label.trim()[0] || "•").toUpperCase();
}

function renderTranscript() {
  const { doc, audioUrl } = view;
  const labels = distinctLabels();
  const turns = buildTurns(doc.segments, editLabels);
  const wrap = $("transcript"); wrap.innerHTML = "";

  if (editMode) wrap.appendChild(buildLegendEditor(labels));

  turns.forEach((t) => {
    const order = Math.max(0, labels.indexOf(t.speaker));
    const color = labelColor(t.speaker, order);
    const isUnknown = t.speaker === "SPEAKER_??";
    const el = document.createElement("div"); el.className = "turn";

    const head = document.createElement("div"); head.className = "turn-head";
    if (editMode) {
      // Reassign this turn to another speaker.
      const sel = document.createElement("select");
      sel.className = "turn-reassign";
      labels.forEach((l, lo) => {
        const o = document.createElement("option"); o.value = l; o.textContent = displaySpeaker(l, lo);
        if (l === t.speaker) o.selected = true; sel.appendChild(o);
      });
      if (isUnknown) { const o = document.createElement("option"); o.value = "SPEAKER_??"; o.textContent = "Speaker ?"; o.selected = true; sel.appendChild(o); }
      sel.style.color = color;
      sel.addEventListener("change", () => { for (const i of t.idxs) editLabels[i] = sel.value; onEdit(); });
      head.appendChild(sel);
    } else {
      const spk = document.createElement("span");
      spk.className = "turn-spk"; spk.style.color = color;
      spk.textContent = displaySpeaker(t.speaker, order);
      head.appendChild(spk);
    }
    const time = document.createElement("span"); time.className = "turn-time"; time.textContent = fmtTime(t.start);
    head.appendChild(time);

    el.innerHTML = `<div class="avatar" style="background:${color}">${avatarFor(t.speaker, order)}</div>`;
    const body = document.createElement("div"); body.className = "turn-body";
    body.appendChild(head);
    const txt = document.createElement("div"); txt.className = "turn-text"; txt.textContent = t.text;
    body.appendChild(txt);
    el.appendChild(body);

    if (!editMode) el.addEventListener("click", () => {
      if (!audioUrl) return;
      const ra = $("result-audio");
      document.querySelectorAll(".turn.active").forEach(x => x.classList.remove("active"));
      el.classList.add("active");
      ra.currentTime = t.start; ra.play();
    });
    wrap.appendChild(el);
  });

  if (editMode) wrap.appendChild(buildSaveBar());
}

function buildLegendEditor(labels) {
  const box = document.createElement("div"); box.className = "legend-editor";
  const hint = document.createElement("p"); hint.className = "legend-hint";
  hint.textContent = "Rename a speaker (applies everywhere), or use the dropdown on a turn to reassign it.";
  box.appendChild(hint);
  labels.forEach((label, order) => {
    const row = document.createElement("div"); row.className = "legend-row";
    const dot = document.createElement("span"); dot.className = "legend-dot"; dot.style.background = labelColor(label, order);
    const inp = document.createElement("input"); inp.type = "text"; inp.value = label; inp.className = "legend-input";
    const rename = () => {
      const nv = inp.value.trim(); if (!nv || nv === label) return;
      editLabels = editLabels.map(l => l === label ? nv : l);
      onEdit();
    };
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { rename(); inp.blur(); } });
    inp.addEventListener("blur", rename);
    row.appendChild(dot); row.appendChild(inp); box.appendChild(row);
  });
  return box;
}

function buildSaveBar() {
  const bar = document.createElement("div"); bar.className = "save-bar";
  const status = document.createElement("span"); status.className = "save-status";
  status.textContent = isDirty() ? "Unsaved changes" : "No changes";
  const save = document.createElement("button"); save.className = "btn tiny primary"; save.textContent = "Save labels";
  save.disabled = !isDirty();
  save.addEventListener("click", saveLabels);
  const cancel = document.createElement("button"); cancel.className = "btn tiny ghost"; cancel.textContent = "Revert";
  cancel.addEventListener("click", () => { renderResult(view.doc, view.meta, view.wall, view.audioUrl); });
  bar.appendChild(status); bar.appendChild(cancel); bar.appendChild(save);
  return bar;
}

// Re-render just the transcript region after an in-place edit (keeps focus flow simple).
function onEdit() { renderTranscript(); }

function toggleEdit() { editMode = !editMode; renderView(); }

async function saveLabels() {
  if (!currentJobId) { setErr("No job to save (open a transcript first)."); return; }
  try {
    const r = await authorizedFetch(`/v1/results/${currentJobId}/relabel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ speakers: editLabels }),
    });
    if (!r.ok) throw new Error("Save failed " + r.status + ": " + (await r.text()));
    const updated = await r.json();
    view.doc = updated;
    editLabels = updated.segments.map(s => s.speaker || "SPEAKER_??");
    editMode = false;
    renderView();
    // Reflect the new speaker count in history.
    bumpHistorySpeakers(currentJobId, updated.speakers_detected);
  } catch (e) { setErr(e.message); }
}

function shortModel(m) {
  if (!m) return "";
  if (m.includes("large-v3")) return "large-v3";
  return m.split("/").pop();
}

/* ---------- actions ---------- */
function copyTranscript() {
  // Copy from the edit buffer so it reflects current (possibly edited) labels.
  const turns = buildTurns((view.doc && view.doc.segments) || [], editLabels);
  const labels = distinctLabels();
  const lines = turns.map(t => {
    const spk = displaySpeaker(t.speaker, Math.max(0, labels.indexOf(t.speaker)));
    return `${spk} [${fmtTime(t.start)}]: ${t.text}`;
  });
  navigator.clipboard.writeText(lines.join("\n\n")).then(() => flash($("copy-btn"), "Copied"));
}
function flash(btn, msg) { const o = btn.textContent; btn.textContent = msg; setTimeout(() => btn.textContent = o, 1200); }
async function download(url, name) {
  if (!url) return;
  const r = await authorizedFetch(url);
  const b = await r.blob(); const u = URL.createObjectURL(b);
  const a = document.createElement("a"); a.href = u; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(u), 4000);
}

/* ---------- history ---------- */
function loadHist() { try { return JSON.parse(localStorage.getItem(HISTKEY) || "[]"); } catch { return []; } }
function addHistory(item) {
  const h = loadHist().filter(x => x.id !== item.id);
  h.unshift(item);
  localStorage.setItem(HISTKEY, JSON.stringify(h.slice(0, 25)));
  renderHistory();
}
function bumpHistorySpeakers(jobId, speakers) {
  const h = loadHist();
  const it = h.find(x => x.id === jobId);
  if (it) { it.speakers = speakers; localStorage.setItem(HISTKEY, JSON.stringify(h)); renderHistory(); }
}
function renderHistory() {
  const h = loadHist(); const ul = $("history"); ul.innerHTML = "";
  $("library").hidden = h.length === 0;
  for (const it of h) {
    const li = document.createElement("li");
    const when = (it.created_at || "").slice(0, 16).replace("T", " ");
    li.innerHTML = `<span class="hi-title"></span><span class="hi-meta">${it.speakers}spk · ${(it.duration||0).toFixed(0)}s · ${when}</span>`;
    li.querySelector(".hi-title").textContent = it.title;
    li.addEventListener("click", () => openHistory(it));
    ul.appendChild(li);
  }
}
async function openHistory(it) {
  setErr("");
  try {
    const jr = await authorizedFetch(it.json);
    if (!jr.ok) throw new Error("Transcript no longer available (retention).");
    const doc = await jr.json();
    currentUrls = { txt: it.txt, json: it.json };
    $("title").value = it.title === "Untitled" ? "" : it.title;
    renderResult(doc, { duration_seconds: it.duration, speakers_detected: it.speakers, language: it.language }, 0, null);
  } catch (e) { setErr(e.message); }
}

/* ---------- pwa ---------- */
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
