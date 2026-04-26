/**
 * FinAnalytics AI — service worker (Sprint UI F).
 *
 * Estratégia:
 *   - HTML: network-first com fallback ao cache (deploys aparecem imediatamente).
 *   - Static assets (.js/.css/.svg/.png/.ico/.json em /static/): stale-while-revalidate.
 *   - API (/api/*): NÃO interceptado (passa direto ao network — sem cache).
 *   - Skip waiting + claim clients no install/activate (atualizacoes nao ficam pendentes).
 *
 * CACHE_VERSION incrementa a cada deploy de assets criticos para invalidar caches antigos.
 */
const CACHE_VERSION = 'fa-v49';
const STATIC_CACHE = CACHE_VERSION + '-static';
const HTML_CACHE = CACHE_VERSION + '-html';

const PRECACHE = [
  '/static/theme.css',
  '/static/auth_guard.js',
  '/static/sidebar.js',
  '/static/sidebar.html',
  '/static/toast.js',
  '/static/empty_state.js',
  '/static/table_utils.js',
  '/static/notifications.js',
  '/static/breadcrumbs.js',
  '/static/command_palette.js',
  '/static/shortcuts.js',
  '/static/onboarding.js',
  '/static/modal.js',
  '/static/error_handler.js',
  '/static/loading.js',
  '/static/a11y.js',
  '/static/favicon.svg',
  // Sprint UI 22/abr: completar precache offline
  '/static/charts.js',
  '/static/form_validate.js',
  '/static/i18n.js',
  '/static/i18n_pt.json',
  '/static/i18n_en.json',
  '/static/locale_toggle.js',
  '/static/print_helper.js',
  '/static/theme_toggle.js',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(PRECACHE).catch(() => null))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => !k.startsWith(CACHE_VERSION)).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

function isStatic(url) {
  return url.pathname.startsWith('/static/') &&
    /\.(js|css|svg|png|ico|json)$/i.test(url.pathname);
}

function isApi(url) {
  return url.pathname.startsWith('/api/');
}

function isHtml(req) {
  return req.mode === 'navigate' ||
    (req.headers.get('accept') || '').includes('text/html');
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // ignora cross-origin

  if (isApi(url)) return; // never cache API

  // HTML: network-first; só cacheia status 200 (evita cachear 404/500/302)
  if (isHtml(req)) {
    event.respondWith(
      fetch(req).then((res) => {
        if (res && res.status === 200) {
          const clone = res.clone();
          caches.open(HTML_CACHE).then((c) => c.put(req, clone)).catch(() => {});
        }
        return res;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // Static: stale-while-revalidate
  if (isStatic(url)) {
    event.respondWith(
      caches.match(req).then((cached) => {
        const fetchPromise = fetch(req).then((res) => {
          if (res && res.status === 200) {
            const clone = res.clone();
            caches.open(STATIC_CACHE).then((c) => c.put(req, clone)).catch(() => {});
          }
          return res;
        }).catch(() => cached);
        return cached || fetchPromise;
      })
    );
  }
});

// Permite a página forçar update manual (postMessage('skipWaiting'))
self.addEventListener('message', (event) => {
  if (event.data === 'skipWaiting') self.skipWaiting();
});
