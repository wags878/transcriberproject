"use strict";
const $ = (id) => document.getElementById(id);
const TOKKEY = "transcribe_token";
const HISTKEY = "transcribe_history";

const SAMPLES = [
  { file: "quick_note.mp3", label: "Note", meta: "1 spk · 14s" },
  { file: "quick_qa.mp3", label: "Q&A", meta: "2 spk · 32s" },
  { file: "friendly_conversation.mp3", label: "Catch-up", meta: "2 spk · 91s" },
  { file: "team_standup.mp3", label: "Standup", meta: "3 spk · 43s" },
];

let blob = null, blobName = "", blobUrl = null;

/* ---------- helpers ---------- */
function token() { return ($("token").value || "").trim(); }
function authHeaders() { return { "Authorization": "Bearer " + token() }; }
function fmtTime(s) { s = Math.max(0, Math.floor(s || 0)); const m = Math.floor(s / 60);
  return String(m).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); }
function speakerLabel(raw) { return raw === "SPEAKER_??" ? "?" : raw; }

/* ---------- init ---------- */
(function init() {
  $("token").value = localStorage.getItem(TOKKEY) || "";
  $("host-label").textContent = location.host;
  $("token").addEventListener("input", () => { localStorage.setItem(TOKKEY, token()); refreshGo(); });

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

    const r = await fetch("/v1/transcribe", { method: "POST", headers: authHeaders(), body: fd });
    if (r.status === 401) throw new Error("401 Unauthorized — check your API token (⚙ Settings).");
    if (!r.ok) throw new Error("Server error " + r.status + ": " + (await r.text()));
    const meta = await r.json();
    const wall = (performance.now() - t0) / 1000;

    const jr = await fetch(meta.transcript_json_url, { headers: authHeaders() });
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
function turnsFromSegments(segs) {
  const turns = [];
  for (const s of (segs || [])) {
    const text = (s.text || "").trim(); if (!text) continue;
    const spk = s.speaker || "SPEAKER_??";
    const last = turns[turns.length - 1];
    if (last && last.speaker === spk) last.text += " " + text;
    else turns.push({ speaker: spk, start: +s.start || 0, text });
  }
  return turns;
}

function renderResult(doc, meta, wall, audioUrl) {
  const turns = turnsFromSegments(doc.segments);
  const gpu = (doc.asr_backend || "").startsWith("speaches");
  $("result-title").textContent = doc.id ? ($("title").value.trim() || "Transcript") : "Transcript";

  $("meta").innerHTML = "";
  const chips = [
    `${(meta.duration_seconds || 0).toFixed(1)}s audio`,
    `${wall ? wall.toFixed(1) + "s processing" : ""}`,
    `${meta.speakers_detected} speaker${meta.speakers_detected == 1 ? "" : "s"}`,
    `lang ${meta.language || "?"}`,
  ];
  for (const c of chips) { if (!c) continue; const s = document.createElement("span"); s.textContent = c; $("meta").appendChild(s); }
  const bk = document.createElement("span");
  bk.className = gpu ? "gpu" : "cpu";
  bk.textContent = gpu ? "GPU · " + shortModel(doc.model) : "CPU · " + shortModel(doc.model);
  $("meta").appendChild(bk);

  const ra = $("result-audio");
  if (audioUrl) { ra.src = audioUrl; ra.hidden = false; } else { ra.removeAttribute("src"); ra.hidden = true; }

  const spkColors = {}; let ci = 0;
  const wrap = $("transcript"); wrap.innerHTML = "";
  turns.forEach((t) => {
    if (!(t.speaker in spkColors)) spkColors[t.speaker] = t.speaker === "SPEAKER_??" ? "var(--muted)" : `var(--spk${ci++ % 6})`;
    const el = document.createElement("div"); el.className = "turn";
    const idx = Object.keys(spkColors).indexOf(t.speaker) + 1;
    el.innerHTML =
      `<div class="avatar" style="background:${spkColors[t.speaker]}">${t.speaker === "SPEAKER_??" ? "?" : idx}</div>` +
      `<div class="turn-body"><div class="turn-head">` +
      `<span class="turn-spk" style="color:${spkColors[t.speaker]}">Speaker ${t.speaker === "SPEAKER_??" ? "?" : idx}</span>` +
      `<span class="turn-time">${fmtTime(t.start)}</span></div>` +
      `<div class="turn-text"></div></div>`;
    el.querySelector(".turn-text").textContent = t.text;
    el.addEventListener("click", () => {
      if (!audioUrl) return;
      document.querySelectorAll(".turn.active").forEach(x => x.classList.remove("active"));
      el.classList.add("active");
      ra.currentTime = t.start; ra.play();
    });
    wrap.appendChild(el);
  });
  $("result").hidden = false;
  $("result").scrollIntoView({ behavior: "smooth", block: "start" });
}

function shortModel(m) {
  if (!m) return "";
  if (m.includes("large-v3")) return "large-v3";
  return m.split("/").pop();
}

/* ---------- actions ---------- */
function copyTranscript() {
  const lines = [...document.querySelectorAll(".turn")].map(t =>
    `${t.querySelector(".turn-spk").textContent} [${t.querySelector(".turn-time").textContent}]: ${t.querySelector(".turn-text").textContent}`);
  navigator.clipboard.writeText(lines.join("\n\n")).then(() => flash($("copy-btn"), "Copied"));
}
function flash(btn, msg) { const o = btn.textContent; btn.textContent = msg; setTimeout(() => btn.textContent = o, 1200); }
async function download(url, name) {
  if (!url) return;
  const r = await fetch(url, { headers: authHeaders() });
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
    const jr = await fetch(it.json, { headers: authHeaders() });
    if (!jr.ok) throw new Error("Transcript no longer available (retention).");
    const doc = await jr.json();
    currentUrls = { txt: it.txt, json: it.json };
    $("title").value = it.title === "Untitled" ? "" : it.title;
    renderResult(doc, { duration_seconds: it.duration, speakers_detected: it.speakers, language: it.language }, 0, null);
  } catch (e) { setErr(e.message); }
}

/* ---------- pwa ---------- */
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
