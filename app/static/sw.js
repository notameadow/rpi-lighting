// Minimal service worker for PWA install.
// Network-only: never cache, always fetch fresh from server.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(names => Promise.all(names.map(n => caches.delete(n))))
  );
});
self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});
