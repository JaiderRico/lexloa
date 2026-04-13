// ── Lexlo Service Worker — Modo Offline ─────────────────────
const CACHE = 'lexlo-v1';
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
    caches.open(CACHE).then(c => c.addAll(STATIC)).then(() => self.skipWaiting())
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

  // Requests a la API — network first, guardar en IDB vía cliente, no cachear
  if (url.pathname.includes('/api/')) {
    e.respondWith(
      fetch(e.request)
        .then(res => {
          // Si la API responde, enviar datos al cliente para que los guarde en IDB
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
          // Sin red: devolver datos guardados en IDB (el cliente los inyecta)
          return new Response(JSON.stringify({ ok: false, offline: true, error: 'Sin conexión' }),
            { headers: { 'Content-Type': 'application/json' } });
        })
    );
    return;
  }

  // Recursos estáticos — cache first, fallback a network
  // Ignorar schemes no-http (chrome-extension, data, blob…)
  const scheme = new URL(e.request.url).protocol;
  if (scheme !== 'https:' && scheme !== 'http:') return;

  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request).then(res => {
      if (res.ok) {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
      }
      return res;
    }))
  );
});
