/**
 * BincioActivity Service Worker
 *
 * Intercepts requests for /data/* and serves from IndexedDB when local
 * activities are present, merging them with the bundled static index.
 *
 * IndexedDB schema:
 *   db: 'bincio', store: 'files'
 *   key: file path (e.g. '/data/activities/2024-01-01T120000Z-ride.json')
 *   value: { path, data }  — data is the parsed JSON object
 *
 * Local activity summaries are kept under the special key '/data/local-index'.
 * The SW merges these with the static index.json at request time so that
 * activities from the server and from the device appear together in the feed.
 */

const DB_NAME = 'bincio';
const DB_VERSION = 1;
const STORE = 'files';

// ── IndexedDB helpers ─────────────────────────────────────────────────────────

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = e => {
      e.target.result.createObjectStore(STORE, { keyPath: 'path' });
    };
    req.onsuccess  = e => resolve(e.target.result);
    req.onerror    = e => reject(e.target.error);
  });
}

async function idbGet(path) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const req = db.transaction(STORE, 'readonly').objectStore(STORE).get(path);
    req.onsuccess = e => resolve(e.target.result?.data ?? null);
    req.onerror   = e => reject(e.target.error);
  });
}

// ── Fetch intercept ───────────────────────────────────────────────────────────

self.addEventListener('install',  () => self.skipWaiting());
self.addEventListener('activate', e  => e.waitUntil(self.clients.claim()));

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  if (url.pathname === '/data/index.json') {
    event.respondWith(handleIndex(event.request));
    return;
  }

  if (url.pathname.startsWith('/data/activities/')) {
    event.respondWith(handleActivity(url.pathname, event.request));
    return;
  }
});

// Merge local summaries into the server/static index.json
async function handleIndex(request) {
  try {
    const localSummaries = (await idbGet('/data/local-index')) ?? [];

    if (localSummaries.length === 0) {
      return fetch(request);  // nothing local — pass straight through
    }

    // Fetch the bundled static index (may fail if offline with no prior cache)
    let remoteMeta = {};
    let remoteActivities = [];
    try {
      const r   = await fetch(request);
      const raw = await r.json();
      remoteActivities = raw.activities ?? [];
      const { activities: _a, ...rest } = raw;
      remoteMeta = rest;
    } catch (_) {}

    // Local overrides remote for same ID; new local entries appended
    const merged = new Map();
    for (const a of remoteActivities) merged.set(a.id, a);
    for (const a of localSummaries)   merged.set(a.id, a);

    const sorted = [...merged.values()].sort(
      (a, b) => (b.started_at ?? '').localeCompare(a.started_at ?? '')
    );

    return jsonResponse({ ...remoteMeta, activities: sorted });
  } catch (_) {
    return fetch(request);
  }
}

// Serve an individual activity file from IDB if present
async function handleActivity(path, request) {
  try {
    const local = await idbGet(path);
    if (local !== null) return jsonResponse(local);
  } catch (_) {}
  return fetch(request);
}

function jsonResponse(data) {
  return new Response(JSON.stringify(data), {
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  });
}
