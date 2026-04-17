// ── Lexlo Service Worker — Modo Offline ─────────────────────
const CACHE = 'lexlo-v2';
const STATIC = [
  '/',
  '/index.html',
  '/logo.png',
  '/logo.ico',
  'https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:ital,wght@0,300;0,400;1,300&display=swap'
];

// ── Instalar: cachear archivos estáticos ─────────────────────
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(STATIC))
      .catch(err => console.error('Cache install failed:', err))
      .then(() => self.skipWaiting())
  );
});

// ── Activar: limpiar caches viejos ───────────────────────────
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// ── Fetch: estrategia por tipo de request ────────────────────
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Ignorar schemes no-soportados
  if (!['http:', 'https:'].includes(url.protocol)) return;

  // Requests a la API
  if (url.pathname.includes('/api/')) {
    e.respondWith(
      fetch(e.request)
        .then(res => {
          if (res.ok && e.request.method === 'GET' && url.pathname.includes('words.php')) {
            const resClone = res.clone();
            resClone.json().then(data => {
              self.clients.matchAll().then(clients =>
                clients.forEach(c => c.postMessage({ type: 'API_CACHE', url: url.href, data }))
              );
            }).catch(() => {});
          }
          return res;
        })
        .catch(() => {
          return new Response(JSON.stringify({ ok: false, offline: true, error: 'Sin conexión' }), {
            status: 503,
            headers: { 'Content-Type': 'application/json' }
          });
        })
    );
    return;
  }

  // Recursos estáticos — cache first
  e.respondWith(
    caches.match(e.request).then(cached => {
      if (cached) return cached;
      return fetch(e.request).then(res => {
        if (res && res.ok) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      }).catch(() => {
        // Fallback para cuando no hay caché ni red
        if (e.request.mode === 'navigate') {
          return caches.match('/index.html');
        }
        return new Response('Offline', { status: 503 });
      });
    })
  );
});