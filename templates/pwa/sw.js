/* EquipWatch service worker (served at /sw.js by core/pwa.py).
 * Caches only /static/ files (cache-first) and an offline page.
 * Pages and /api/ requests are network-only: logged-in content is never cached. */
const CACHE_NAME = "{{ cache_name }}";
const STATIC_URL = "{{ static_url }}";
const OFFLINE_URL = "{{ offline_url }}";
const PRECACHE = [
  OFFLINE_URL,
  STATIC_URL + "css/app.css",
  STATIC_URL + "js/app.js",
  STATIC_URL + "js/live.js",
  STATIC_URL + "icons/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  // Delete caches from older versions of the static files.
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return; // CDN files: normal browser caching

  if (url.pathname.startsWith(STATIC_URL)) {
    // Cache-first for static assets.
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached;
        return fetch(request).then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          }
          return response;
        });
      })
    );
    return;
  }

  if (request.mode === "navigate") {
    // Pages: always from the network; show the offline page only when it is unreachable.
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
  }
  // Everything else (API, photos, PDFs): network only - the worker does not touch it.
});
