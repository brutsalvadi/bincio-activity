import * as FileSystem from 'expo-file-system/legacy';
import type { SQLiteDatabase } from 'expo-sqlite';
import { getSetting, upsertRemoteActivity } from './queries';

export type SyncResult = {
  synced: number;
  total: number;
  fetched?: number;
  uploaded?: number;
  failed?: number;
  error?: string;
};

async function resolveCredentials(db: SQLiteDatabase): Promise<{ instanceUrl: string; token: string } | { error: string }> {
  const instanceUrl = (await getSetting(db, 'instance_url'))?.replace(/\/$/, '');
  const token = await getSetting(db, 'api_token');
  if (!instanceUrl || !token) return { error: 'No instance configured — add one in Settings.' };
  return { instanceUrl, token };
}

export async function downloadFeed(db: SQLiteDatabase): Promise<SyncResult> {
  const creds = await resolveCredentials(db);
  if ('error' in creds) return { synced: 0, total: 0, error: creds.error };
  const { instanceUrl, token } = creds;

  let resp: Response;
  try {
    resp = await fetch(`${instanceUrl}/api/feed`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    return { synced: 0, total: 0, error: 'Could not reach instance — check your connection.' };
  }

  if (resp.status === 401) return { synced: 0, total: 0, error: 'Session expired — reconnect in Settings.' };
  if (!resp.ok) return { synced: 0, total: 0, error: `Server error (${resp.status})` };

  const data: { activities?: RemoteSummary[] } = await resp.json();
  const activities = data.activities ?? [];
  const syncMode = (await getSetting(db, 'sync_mode')) ?? 'summaries';

  let synced = 0;
  for (const a of activities) {
    const detailJson = JSON.stringify({
      id: a.id,
      title: a.title ?? a.id,
      sport: a.sport ?? null,
      started_at: a.started_at ?? null,
      distance_m: a.distance_m ?? null,
      moving_time_s: a.moving_time_s ?? null,
      elevation_gain_m: a.elevation_gain_m ?? null,
      avg_speed_kmh: a.avg_speed_kmh ?? null,
      avg_hr_bpm: a.avg_hr_bpm ?? null,
      avg_power_w: a.avg_power_w ?? null,
    });
    const changed = await upsertRemoteActivity(db, a.id, detailJson);
    if (changed) synced++;
  }

  if (syncMode !== 'full') return { synced, total: activities.length };

  // Full mode: fetch geojson + timeseries for activities missing them
  const headers = { Authorization: `Bearer ${token}` };
  let fetched = 0;
  for (const a of activities) {
    const row = db.getFirstSync<{ g: number; t: number }>(
      'SELECT (geojson IS NOT NULL) as g, (timeseries_json IS NOT NULL) as t FROM activities WHERE id = ?',
      [a.id],
    );
    if (row?.g && row?.t) continue;

    let gj: string | null = null;
    let ts: string | null = null;
    try {
      if (!row?.g) {
        const r = await fetch(`${instanceUrl}/api/activity/${a.id}/geojson`, { headers });
        if (r.ok) gj = await r.text();
      }
      if (!row?.t) {
        const r = await fetch(`${instanceUrl}/api/activity/${a.id}/timeseries`, { headers });
        if (r.ok) ts = await r.text();
      }
    } catch {}

    if (gj !== null || ts !== null) {
      await db.runAsync(
        `UPDATE activities SET
           geojson         = COALESCE(geojson,         ?),
           timeseries_json = COALESCE(timeseries_json, ?)
         WHERE id = ? AND origin = 'remote'`,
        [gj, ts, a.id],
      );
      fetched++;
    }
  }

  return { synced, total: activities.length, fetched };
}

export async function uploadFeed(
  db: SQLiteDatabase,
  onProgress?: (n: number, total: number) => void,
): Promise<SyncResult> {
  const creds = await resolveCredentials(db);
  if ('error' in creds) return { synced: 0, total: 0, error: creds.error };
  const { instanceUrl, token } = creds;

  // Reconcile local synced_at against what the server actually has.
  // If the server was wiped/reset, activities we thought were uploaded need
  // re-uploading — clear their synced_at so they re-enter the upload queue.
  try {
    const feedResp = await fetch(`${instanceUrl}/api/feed`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (feedResp.ok) {
      const feedData: { activities?: { id: string }[] } = await feedResp.json();
      const serverIds = new Set((feedData.activities ?? []).map(a => a.id));
      const syncedRows = db.getAllSync<{ id: string }>(
        `SELECT id FROM activities WHERE origin = 'local' AND synced_at IS NOT NULL`,
      );
      for (const row of syncedRows) {
        if (!serverIds.has(row.id)) {
          await db.runAsync(`UPDATE activities SET synced_at = NULL WHERE id = ?`, [row.id]);
        }
      }
    }
  } catch {
    // Best-effort — proceed with upload even if reconciliation fails
  }

  const { uploaded, failed } = await uploadLocalActivities(db, instanceUrl, token, onProgress);
  return { synced: 0, total: 0, uploaded, failed: failed || undefined };
}

export async function syncFeed(db: SQLiteDatabase): Promise<SyncResult> {
  const dl = await downloadFeed(db);
  if (dl.error) return dl;

  const uploadEnabled = (await getSetting(db, 'sync_upload')) === 'true';
  let uploaded = 0;
  if (uploadEnabled) {
    const ul = await uploadFeed(db);
    uploaded = ul.uploaded ?? 0;
  }

  return { ...dl, uploaded: uploaded || undefined };
}

export async function countPendingUploads(db: SQLiteDatabase): Promise<number> {
  const row = db.getFirstSync<{ n: number }>(
    `SELECT COUNT(*) as n FROM activities WHERE origin = 'local' AND synced_at IS NULL`,
  );
  return row?.n ?? 0;
}

async function uploadLocalActivities(
  db: SQLiteDatabase,
  instanceUrl: string,
  token: string,
  onProgress?: (n: number, total: number) => void,
): Promise<{ uploaded: number; failed: number }> {
  const rows = db.getAllSync<{
    id: string;
    detail_json: string;
    timeseries_json: string | null;
    geojson: string | null;
    original_path: string | null;
    edits_json: string | null;
  }>(
    `SELECT id, detail_json, timeseries_json, geojson, original_path, edits_json
     FROM activities WHERE origin = 'local' AND synced_at IS NULL`,
  );

  const preferRaw = (await getSetting(db, 'upload_format') ?? 'raw') === 'raw';
  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
  let uploaded = 0;
  let failed = 0;
  const now = Math.floor(Date.now() / 1000);
  const total = rows.length;

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    onProgress?.(i + 1, total);
    try {
      let resp: Response;

      // When preferRaw is set and the original file is still on disk, send the raw
      // bytes to /api/upload/raw so the server re-extracts with DEM elevation correction.
      const useRaw = preferRaw &&
        row.original_path !== null &&
        (await FileSystem.getInfoAsync(row.original_path)).exists;

      const userTitle: string | null = row.edits_json
        ? (JSON.parse(row.edits_json).title ?? null)
        : null;

      if (useRaw) {
        const filename = row.original_path!.split('/').pop() ?? 'activity.fit';
        const base64 = await FileSystem.readAsStringAsync(row.original_path!, {
          encoding: FileSystem.EncodingType.Base64,
        });
        resp = await fetch(`${instanceUrl}/api/upload/raw`, {
          method: 'POST',
          headers,
          body: JSON.stringify({ filename, base64, ...(userTitle ? { user_title: userTitle } : {}) }),
        });
      } else {
        const detail = JSON.parse(row.detail_json);
        if (userTitle) detail.title = userTitle;
        const body: Record<string, unknown> = { activity: { id: row.id, ...detail } };
        if (row.timeseries_json) body.timeseries = JSON.parse(row.timeseries_json);
        if (row.geojson)         body.geojson    = JSON.parse(row.geojson);
        resp = await fetch(`${instanceUrl}/api/upload/bas`, {
          method: 'POST',
          headers,
          body: JSON.stringify(body),
        });
      }

      if (resp.ok) {
        await db.runAsync(`UPDATE activities SET synced_at = ? WHERE id = ?`, [now, row.id]);
        // Option A: after a raw upload, update local detail/timeseries/geojson with the
        // server's DEM-corrected extraction so the app shows better elevation data.
        if (useRaw) {
          try {
            const data = await resp.json() as {
              id: string;
              detail: object;
              timeseries: object | null;
              geojson: object | null;
              source_hash: string;
            };
            if (data.id === row.id) {
              await db.runAsync(
                `UPDATE activities
                 SET detail_json      = ?,
                     timeseries_json  = COALESCE(?, timeseries_json),
                     geojson          = COALESCE(?, geojson),
                     source_hash      = ?
                 WHERE id = ?`,
                [
                  JSON.stringify(data.detail),
                  data.timeseries ? JSON.stringify(data.timeseries) : null,
                  data.geojson    ? JSON.stringify(data.geojson)    : null,
                  data.source_hash,
                  row.id,
                ],
              );
            }
          } catch {
            // Non-fatal: synced_at is already set, local data stays as-is
          }
        }
        uploaded++;
      } else {
        console.warn(`upload ${row.id}: HTTP ${resp.status}`);
        failed++;
      }
    } catch (err) {
      console.warn(`upload ${row.id}:`, err);
      failed++;
    }
  }

  return { uploaded, failed };
}

type RemoteSummary = {
  id: string;
  title?: string;
  sport?: string;
  started_at?: string;
  distance_m?: number | null;
  moving_time_s?: number | null;
  elevation_gain_m?: number | null;
  avg_speed_kmh?: number | null;
  avg_hr_bpm?: number | null;
  avg_power_w?: number | null;
};
