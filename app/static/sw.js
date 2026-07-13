// Network-first service worker. Always tries the network so new deploys show up
// immediately; falls back to cache only when offline. This avoids the
// stale-shell trap that a cache-first worker causes during active development,
// while keeping the app installable + offline-capable.
const CACHE = "transcribe-shell-v4";
const SHELL = ["/", "/index.html", "/app.css", "/app.js", "/manifest.webmanifest", "/icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // API + sample audio always go straight to the network, never cached.
  if (url.pathname.startsWith("/v1/") || url.pathname.startsWith("/samples/")) return;
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  // Network-first, and bypass the HTTP cache on the network hit ({cache:
  // "no-store"}) so a redeploy is never masked by a stale HTTP-cached asset —
  // the failure mode a plain fetch() still allows. Offline → cached shell.
  e.respondWith(
    fetch(e.request, { cache: "no-store" })
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(e.request).then((hit) => hit || caches.match("/")))
  );
});
