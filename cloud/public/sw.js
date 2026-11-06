const CACHE_NAME = 'stock-offline-v5';
const SHELL_FILES = [
  '/',
  '/index.html',
  '/login',
  '/scan',
  '/auth.js',
  '/notifications.js',
  '/sw.js',
  '/manifest.webmanifest',
  '/offline.html',
  '/icon.svg',
  '/vendor/qrcode.min.js',
  '/vendor/html5-qrcode.min.js'
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME)
    .then(cache => cache.addAll(SHELL_FILES))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys()
    .then(names => Promise.all(names
      .filter(name => name.startsWith('stock-offline-') && name !== CACHE_NAME)
      .map(name => caches.delete(name))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;

  // Jamais de cache pour les API : les données sont propres à chaque boutique.
  if (url.pathname.startsWith('/api/')) return;

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).then(async response => {
      if (response.ok) {
        await caches.open(CACHE_NAME).then(cache => cache.put(request, response.clone()));
      }
      return response;
    }).catch(async () => {
      const cached = await caches.match(request);
      return cached || caches.match('/offline.html');
    }));
    return;
  }

  event.respondWith(caches.match(request).then(cached => {
    if (cached) return cached;
    return fetch(request).then(response => {
      if (response.ok) {
        const copy = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
      }
      return response;
    });
  }));
});

self.addEventListener('push', event => {
  let message = {};
  try {
    message = event.data ? event.data.json() : {};
  } catch {
    message = {body: event.data ? event.data.text() : ''};
  }
  event.waitUntil(self.registration.showNotification(message.title || 'Alerte de stock', {
    body: message.body || 'Un produit approche de sa date d’expiration.',
    tag: message.tag || 'expiration-produit',
    data: {url: message.url || '/'}
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || '/', self.location.origin).href;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    for (const client of windows) {
      if (client.url === target && 'focus' in client) return client.focus();
    }
    return self.clients.openWindow(target);
  })());
});
