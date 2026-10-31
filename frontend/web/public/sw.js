// SafarSaathi service worker: app shell offline, last-known public bus data when the network drops.
// Personal API data (/api/me/*, queue tokens) is never cached, so a shared phone doesn't leak it.
const VERSION = 'v1'
const SHELL = `shell-${VERSION}`
const DATA = `data-${VERSION}`
const PUBLIC_API = [/^\/api\/routes$/, /^\/api\/buses$/, /^\/api\/announcements$/]

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(['/', '/manifest.webmanifest', '/favicon.svg', '/icon-192.png'])))
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL && k !== DATA).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

async function networkFirst(request, cacheName, fallbackUrl) {
  const cache = await caches.open(cacheName)
  try {
    const res = await fetch(request)
    if (res.ok) cache.put(fallbackUrl ?? request, res.clone())
    return res
  } catch {
    const hit = await cache.match(fallbackUrl ?? request)
    if (hit) return hit
    throw new Error('offline')
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(SHELL)
  const hit = await cache.match(request)
  if (hit) return hit
  const res = await fetch(request)
  if (res.ok) cache.put(request, res.clone())
  return res
}

self.addEventListener('fetch', (event) => {
  const { request } = event
  const url = new URL(request.url)
  if (request.method !== 'GET' || url.origin !== self.location.origin) return

  if (request.mode === 'navigate') {
    // Every page is the same SPA index; serve the cached copy when offline.
    event.respondWith(networkFirst(request, SHELL, '/'))
  } else if (url.pathname.startsWith('/assets/')) {
    // Vite asset names carry a content hash, so a cached copy never goes stale.
    event.respondWith(cacheFirst(request))
  } else if (PUBLIC_API.some((re) => re.test(url.pathname))) {
    event.respondWith(networkFirst(request, DATA))
  }
})
