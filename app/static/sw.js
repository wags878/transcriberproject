// Minimal service worker — enough to make the app installable and to serve the
// shell offline. API calls (/v1/*) always go to the network; never cached.
const CACHE = "transcribe-shell-v2";
const SHELL = ["/", "/index.html", "/app.css", "/app.js", "/manifest.webmanifest", "/icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // Never cache API traffic — always live.
  if (url.pathname.startsWith("/v1/")) return;
  if (e.request.method !== "GET") return;
  // Cache-first for the app shell, fall back to network.
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)));
});
