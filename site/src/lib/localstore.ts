/**
 * IndexedDB helper for local activity storage.
 *
 * Activities converted on-device are written here. The service worker (sw.js)
 * reads from the same database and merges local activities into the feed.
 */

const DB_NAME    = 'bincio';
const DB_VERSION = 1;
const STORE      = 'files';

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = e =>
      (e.target as IDBOpenDBRequest).result.createObjectStore(STORE, { keyPath: 'path' });
    req.onsuccess = e => resolve((e.target as IDBOpenDBRequest).result);
    req.onerror   = e => reject((e.target as IDBOpenDBRequest).error);
  });
}

async function idbPut(path: string, data: unknown): Promise<void> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).put({ path, data });
    tx.oncomplete = () => resolve();
    tx.onerror    = e => reject((e.target as IDBTransaction).error);
  });
}

async function idbGet<T>(path: string): Promise<T | null> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const req = db.transaction(STORE, 'readonly').objectStore(STORE).get(path);
    req.onsuccess = e => resolve((e.target as IDBRequest).result?.data ?? null);
    req.onerror   = e => reject((e.target as IDBRequest).error);
  });
}

// ── Public API ────────────────────────────────────────────────────────────────

/** Save a converted activity to IndexedDB and update the local summary index. */
export async function saveActivityLocally(
  detail:  Record<string, unknown>,
  geojson: Record<string, unknown> | null,
): Promise<void> {
  const id = detail.id as string;

  await idbPut(`/data/activities/${id}.json`, detail);
  if (geojson) {
    await idbPut(`/data/activities/${id}.geojson`, geojson);
  }

  // Maintain a flat list of local summaries (read by the service worker)
  const existing = (await idbGet<ActivitySummary[]>('/data/local-index')) ?? [];
  const summary  = toSummary(detail);
  const idx = existing.findIndex(a => a.id === id);
  if (idx >= 0) existing[idx] = summary; else existing.push(summary);
  await idbPut('/data/local-index', existing);
}

/** Return all locally-stored activity summaries. */
export async function listLocalActivities(): Promise<ActivitySummary[]> {
  return (await idbGet<ActivitySummary[]>('/data/local-index')) ?? [];
}

/** Return the summary for a single locally-stored activity, or null. */
export async function getLocalActivity(id: string): Promise<ActivitySummary | null> {
  const list = await listLocalActivities();
  return list.find(a => a.id === id) ?? null;
}

/** Return true if at least one activity is stored locally. */
export async function hasLocalActivities(): Promise<boolean> {
  const list = await listLocalActivities();
  return list.length > 0;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

type ActivitySummary = Record<string, unknown>;

const SUMMARY_KEYS = [
  'id', 'title', 'sport', 'sub_sport', 'started_at', 'distance_m',
  'duration_s', 'moving_time_s', 'elevation_gain_m', 'avg_speed_kmh',
  'avg_hr_bpm', 'avg_cadence_rpm', 'avg_power_w', 'privacy',
  'detail_url', 'track_url', 'preview_coords',
] as const;

function toSummary(detail: Record<string, unknown>): ActivitySummary {
  const id = detail.id as string;
  const summary = Object.fromEntries(
    SUMMARY_KEYS.filter(k => k in detail).map(k => [k, detail[k]])
  );
  // These live in the index summary, not the detail JSON — derive from id
  if (!summary.detail_url) summary.detail_url = `activities/${id}.json`;
  if (!summary.track_url && detail.bbox)  summary.track_url = `activities/${id}.geojson`;
  return summary;
}
