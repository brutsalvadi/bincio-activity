/**
 * Data access abstraction layer.
 *
 * All Svelte components load BAS data through these functions instead of
 * calling fetch() directly. Each function merges server/bundled data with
 * any activities stored locally in IndexedDB (via localstore.ts), so the
 * app works the same whether it is connected to a cloud instance, running
 * offline, or somewhere in between.
 *
 * Design notes:
 * - Server fetch and IDB read run concurrently (Promise.allSettled).
 * - If the server is unreachable, local-only data is returned.
 * - If IDB is empty, pure server data is returned — zero overhead.
 * - Local activities override server ones with the same ID (local is authoritative
 *   for anything the user recorded or converted on this device).
 */

import type { ActivityDetail, ActivitySummary, BASIndex, Timeseries } from './types';
import { listLocalActivities } from './localstore';

// ── Helpers ───────────────────────────────────────────────────────────────────

async function fetchJSON<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json() as Promise<T>;
}

async function idbGetActivity(id: string): Promise<ActivityDetail | null> {
  // Inline IDB read — avoids importing openDB into every caller
  return new Promise(resolve => {
    try {
      const req = indexedDB.open('bincio', 1);
      req.onsuccess = e => {
        const db: IDBDatabase = (e.target as IDBOpenDBRequest).result;
        const tx = db.transaction('files', 'readonly');
        const get = tx.objectStore('files').get(`/data/activities/${id}.json`);
        get.onsuccess = ge => resolve((ge.target as IDBRequest).result?.data ?? null);
        get.onerror   = () => resolve(null);
      };
      req.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

function emptyIndex(): BASIndex {
  return {
    bas_version: '1.0',
    owner: { handle: 'unknown', display_name: '' },
    generated_at: '',
    shards: [],
    activities: [],
  };
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function isYearShardUrl(url: string): boolean {
  return /(?:^|\/)index-\d{4}\.json$/.test(url);
}

function rewriteActivityUrls(a: ActivitySummary, shardBase: string): ActivitySummary {
  // Skip if URL is already absolute (http:// or root-relative /) — avoids
  // double-rewriting when shards are nested (e.g. user shard → year shard).
  const needsRewrite = (url: string | null | undefined): boolean =>
    !!url && !url.startsWith('http') && !url.startsWith('/');
  return {
    ...a,
    detail_url: needsRewrite(a.detail_url) ? `${shardBase}${a.detail_url}` : a.detail_url,
    track_url:  needsRewrite(a.track_url)  ? `${shardBase}${a.track_url}`  : a.track_url,
  };
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Resolve shards from a BASIndex into a flat activity list.
 *
 * Handles two shard types transparently:
 *  - handle shards: multi-user manifest (url = "{handle}/index.json")
 *  - year shards:   per-user pagination (url = "index-2025.json")
 *
 * Shard URLs are resolved relative to the index URL that declared them.
 * All shard fetches run concurrently. Errors are silently skipped so a
 * single unavailable shard doesn't break the whole feed.
 */
async function resolveShards(
  index: BASIndex,
  indexUrl: string,
): Promise<ActivitySummary[]> {
  if (!index.shards?.length) return index.activities ?? [];

  const base = indexUrl.substring(0, indexUrl.lastIndexOf('/') + 1);

  const shardResults = await Promise.allSettled(
    index.shards.map(async shard => {
      const url = shard.url.startsWith('http') ? shard.url : `${base}${shard.url}`;
      // Base URL of this shard's directory (e.g. "http://…/data/dave/_merged/")
      const shardBase = url.substring(0, url.lastIndexOf('/') + 1);
      const sub = await fetchJSON<BASIndex>(url);
      // Recursively resolve nested shards (e.g. user shard that itself paginates)
      const activities = await resolveShards(sub, url);
      // Rewrite relative detail_url / track_url to be absolute so they can be
      // fetched correctly regardless of where the root index lives.
      return activities.map(a => ({
        ...rewriteActivityUrls(a, shardBase),
        ...(shard.handle ? { handle: shard.handle } : {}),
      }));
    }),
  );

  // Log shard fetch failures to help diagnose missing-activity issues
  shardResults.forEach((r, i) => {
    if (r.status === 'rejected') {
      console.error('[bincio] shard fetch failed:', index.shards[i]?.url, r.reason);
    }
  });

  const own = index.activities ?? [];
  const fromShards = shardResults.flatMap(r => r.status === 'fulfilled' ? r.value : []);
  return [...own, ...fromShards];
}

/**
 * Load the activity index, resolving any shards (multi-user or pagination),
 * then merging with locally-stored activities from IndexedDB.
 *
 * Single-user indexes with no shards work exactly as before — zero overhead.
 *
 * @param baseUrl   Site base URL (used for IDB local activities)
 * @param indexUrl  Full URL of the index to load (defaults to baseUrl + data/index.json)
 */
export async function loadIndex(baseUrl: string, indexUrl?: string): Promise<BASIndex> {
  indexUrl = indexUrl ?? `${baseUrl}data/index.json`;

  const [serverResult, localResult] = await Promise.allSettled([
    fetchJSON<BASIndex>(indexUrl),
    listLocalActivities(),
  ]);

  const server = serverResult.status === 'fulfilled' ? serverResult.value : null;
  const local  = localResult.status  === 'fulfilled' ? localResult.value  : [];

  const serverActivities = server
    ? await resolveShards(server, indexUrl)
    : [];

  if (local.length === 0 && !server) return emptyIndex();

  // Local overrides server for the same ID; new local entries are appended
  const merged = new Map<string, ActivitySummary>();
  for (const a of serverActivities)          merged.set(a.id, a);
  for (const a of local as ActivitySummary[]) merged.set(a.id, a);

  return {
    ...(server ?? emptyIndex()),
    activities: [...merged.values()].sort(
      (a, b) => (b.started_at ?? '').localeCompare(a.started_at ?? ''),
    ),
  };
}

/**
 * Like loadIndex but only fetches the most-recent year shard immediately.
 * Returns the first-page activities plus a list of remaining shard URLs that
 * can be fetched on demand (e.g. when the user clicks "Load more").
 *
 * Falls back to full eager loading for non-year shard manifests (multi-user
 * combined feed) so the behaviour is identical to loadIndex in those cases.
 */
export async function loadIndexPaged(
  baseUrl: string,
  indexUrl?: string,
): Promise<{ index: BASIndex; pendingShards: string[] }> {
  indexUrl = indexUrl ?? `${baseUrl}data/index.json`;

  const [serverResult, localResult] = await Promise.allSettled([
    fetchJSON<BASIndex>(indexUrl),
    listLocalActivities(),
  ]);

  const server = serverResult.status === 'fulfilled' ? serverResult.value : null;
  const local  = localResult.status  === 'fulfilled' ? localResult.value  : [];

  if (!server && local.length === 0) return { index: emptyIndex(), pendingShards: [] };

  const base = indexUrl.substring(0, indexUrl.lastIndexOf('/') + 1);
  const allShards = server?.shards ?? [];

  const yearShards  = allShards.filter(s => isYearShardUrl(s.url));
  const otherShards = allShards.filter(s => !isYearShardUrl(s.url));

  // ── Year-sharded index (single-user or profile page) ───────────────────────
  // Load only the first (most-recent) year shard; return the rest as pending.
  let yearFirstActivities: ActivitySummary[] = [];
  let pendingShards: string[] = [];

  if (yearShards.length > 0) {
    const sorted = [...yearShards].sort((a, b) => b.url.localeCompare(a.url));
    const firstUrl = sorted[0].url.startsWith('http') ? sorted[0].url : `${base}${sorted[0].url}`;
    const shardBase = firstUrl.substring(0, firstUrl.lastIndexOf('/') + 1);
    try {
      const first = await fetchJSON<BASIndex>(firstUrl);
      yearFirstActivities = (first.activities ?? []).map(a => rewriteActivityUrls(a, shardBase));
    } catch (e) {
      console.error('[bincio] first year shard failed:', sorted[0].url, e);
    }
    pendingShards = sorted.slice(1).map(s =>
      s.url.startsWith('http') ? s.url : `${base}${s.url}`,
    );
  }

  // ── Non-year shards (multi-user manifest) — loaded eagerly as before ───────
  let otherActivities: ActivitySummary[] = [];
  if (otherShards.length > 0) {
    const otherIndex: BASIndex = { ...(server ?? emptyIndex()), shards: otherShards };
    otherActivities = await resolveShards(otherIndex, indexUrl);
  }

  // ── Own activities (legacy flat index with no shards) ──────────────────────
  const ownActivities = allShards.length === 0 ? (server?.activities ?? []) : [];

  // Merge: server + local (local overrides server for same id)
  const serverActivities = [...ownActivities, ...otherActivities, ...yearFirstActivities];
  const merged = new Map<string, ActivitySummary>();
  for (const a of serverActivities)           merged.set(a.id, a);
  for (const a of local as ActivitySummary[]) merged.set(a.id, a);

  return {
    index: {
      ...(server ?? emptyIndex()),
      activities: [...merged.values()].sort(
        (a, b) => (b.started_at ?? '').localeCompare(a.started_at ?? ''),
      ),
    },
    pendingShards,
  };
}

/**
 * Fetch activities from a single year shard URL (absolute).
 * Used by ActivityFeed to lazily load older years when "Load more" is clicked.
 */
export async function loadShardActivities(shardUrl: string): Promise<ActivitySummary[]> {
  try {
    const data = await fetchJSON<BASIndex>(shardUrl);
    const base = shardUrl.substring(0, shardUrl.lastIndexOf('/') + 1);
    return (data.activities ?? []).map(a => rewriteActivityUrls(a, base));
  } catch {
    return [];
  }
}

interface FeedPage {
  page: number;
  total_pages: number;
  total_activities: number;
  activities: ActivitySummary[];
}

/**
 * Load the combined feed (multi-user global feed). Returns the first page of
 * activities pre-sorted across all users, plus remaining page count.
 *
 * Falls back to the full shard-resolution path if feed.json doesn't exist
 * (single-user installs, older data).
 */
export async function loadCombinedFeed(
  baseUrl: string,
): Promise<{ activities: ActivitySummary[]; remainingPages: number; totalActivities: number } | null> {
  try {
    const feed = await fetchJSON<FeedPage>(`${baseUrl}data/feed.json`);
    return {
      activities: feed.activities ?? [],
      remainingPages: (feed.total_pages ?? 1) - 1,
      totalActivities: feed.total_activities ?? 0,
    };
  } catch {
    return null;
  }
}

/**
 * Load a subsequent page of the combined feed (feed-2.json, feed-3.json, etc.).
 */
export async function loadCombinedFeedPage(
  baseUrl: string,
  page: number,
): Promise<ActivitySummary[]> {
  try {
    const feed = await fetchJSON<FeedPage>(`${baseUrl}data/feed-${page}.json`);
    return feed.activities ?? [];
  } catch {
    return [];
  }
}

/**
 * Load a single activity detail, checking IndexedDB first so locally-converted
 * activities are available offline.
 *
 * @param id        Activity ID (used for the IDB lookup)
 * @param detailUrl Relative path from the BAS index (e.g. "activities/id.json")
 * @param baseUrl   Site base URL
 */
export async function loadActivity(
  id:        string,
  detailUrl: string,
  baseUrl:   string,
): Promise<ActivityDetail | null> {
  // IDB first — instant and works offline
  const cached = await idbGetActivity(id);
  if (cached) return cached;

  try {
    const url = detailUrl.startsWith('http') || detailUrl.startsWith('/')
      ? detailUrl
      : `${baseUrl}data/${detailUrl}`;
    return await fetchJSON<ActivityDetail>(url);
  } catch {
    return null;
  }
}

/**
 * Fetch the timeseries for an activity. Called lazily when the charts section
 * is shown, so the initial detail load stays small (~1 KB instead of ~600 KB).
 *
 * @param timeseriesUrl Relative path from the detail JSON (e.g. "activities/id.timeseries.json")
 * @param detailUrl     The URL from which the detail JSON was fetched — used to resolve
 *                      relative paths correctly in both single- and multi-user modes.
 * @param baseUrl       Site base URL — fallback when detailUrl is empty
 */
export async function loadTimeseries(
  timeseriesUrl: string,
  detailUrl: string,
  baseUrl: string,
): Promise<Timeseries | null> {
  try {
    let url: string;
    // Strip the leading "activities/" from timeseriesUrl so we can append it
    // to whatever directory the detail JSON lives in.
    const filename = timeseriesUrl.replace(/^activities\//, '');

    if (timeseriesUrl.startsWith('http')) {
      url = timeseriesUrl;
    } else if (detailUrl.startsWith('http') || detailUrl.startsWith('/')) {
      // absolute detailUrl (browser shard resolution) → same directory
      const dir = detailUrl.substring(0, detailUrl.lastIndexOf('/') + 1);
      url = `${dir}${filename}`;
    } else {
      // relative detailUrl — may be plain ("activities/{id}.json", single-user)
      // or prefixed ("dave/_merged/activities/{id}.json", multi-user SSG prop).
      // In both cases, resolve the timeseries file from the same directory.
      const dir = detailUrl.includes('/')
        ? detailUrl.substring(0, detailUrl.lastIndexOf('/') + 1)
        : '';
      url = `${baseUrl}data/${dir}${filename}`;
    }
    return await fetchJSON<Timeseries>(url);
  } catch {
    return null;
  }
}

/**
 * Load athlete profile. Athlete data is not stored locally yet, so this is
 * always a network fetch with a graceful null on failure.
 *
 * @param baseUrl    Site base URL (used to build the default path)
 * @param athleteUrl Explicit full URL — use for per-user pages in multi-user mode
 */
export async function loadAthlete(
  baseUrl: string,
  athleteUrl?: string,
): Promise<Record<string, unknown> | null> {
  try {
    return await fetchJSON(athleteUrl ?? `${baseUrl}data/athlete.json`);
  } catch {
    return null;
  }
}
