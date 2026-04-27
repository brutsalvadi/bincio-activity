import { useSQLiteContext } from 'expo-sqlite';

// ── Types ──────────────────────────────────────────────────────────────────

export type ActivityRow = {
  id: string;
  source_hash: string;
  detail_json: string;
  timeseries_json: string | null;
  geojson: string | null;
  original_path: string | null;
  source_path: string | null;
  synced_at: number | null;
  origin: 'local' | 'remote';
  created_at: number;
  edits_json: string | null;
};

export type ActivitySummary = {
  id: string;
  title: string;
  user_title: string | null;  // from edits_json; takes display priority over title
  sport: string;
  started_at: string;
  distance_m: number | null;
  duration_s: number | null;
  elevation_gain_m: number | null;
  origin: 'local' | 'remote';
  synced_at: number | null;
};

// ── Activities ─────────────────────────────────────────────────────────────

const PAGE_SIZE = 50;

export function useActivities(searchQuery = '', limit = PAGE_SIZE): ActivitySummary[] {
  const db = useSQLiteContext();
  const like = `%${searchQuery}%`;
  const rows = db.getAllSync<ActivitySummary>(`
    SELECT
      id, origin, synced_at,
      json_extract(detail_json, '$.title')            AS title,
      json_extract(edits_json,  '$.title')            AS user_title,
      json_extract(detail_json, '$.sport')            AS sport,
      json_extract(detail_json, '$.started_at')       AS started_at,
      json_extract(detail_json, '$.distance_m')       AS distance_m,
      json_extract(detail_json, '$.duration_s')       AS duration_s,
      json_extract(detail_json, '$.elevation_gain_m') AS elevation_gain_m
    FROM activities
    WHERE (? = '' OR json_extract(detail_json, '$.title') LIKE ?)
    ORDER BY json_extract(detail_json, '$.started_at') DESC
    LIMIT ?
  `, [searchQuery, like, limit]);
  return rows;
}

export function useActivityCount(searchQuery = ''): number {
  const db = useSQLiteContext();
  const like = `%${searchQuery}%`;
  const row = db.getFirstSync<{ n: number }>(
    `SELECT COUNT(*) as n FROM activities
     WHERE (? = '' OR json_extract(detail_json, '$.title') LIKE ?)`,
    [searchQuery, like],
  );
  return row?.n ?? 0;
}

export { PAGE_SIZE };

export type ActivityFilter = {
  sport: string;    // '' = all sports
  dateFrom: string; // '' = no lower bound; ISO-like 'YYYY-MM-DDTHHMMSSZ' for comparison
  dateTo: string;   // '' = no upper bound
  sort: 'date' | 'distance' | 'elevation';
};

const SORT_SQL: Record<string, string> = {
  date:      "json_extract(detail_json, '$.started_at') DESC",
  distance:  "json_extract(detail_json, '$.distance_m') DESC",
  elevation: "json_extract(detail_json, '$.elevation_gain_m') DESC",
};

export function useFilteredActivities(filter: ActivityFilter, limit = PAGE_SIZE): ActivitySummary[] {
  const db = useSQLiteContext();
  const order = SORT_SQL[filter.sort] ?? SORT_SQL.date;
  return db.getAllSync<ActivitySummary>(`
    SELECT
      id, origin, synced_at,
      json_extract(detail_json, '$.title')            AS title,
      json_extract(edits_json,  '$.title')            AS user_title,
      json_extract(detail_json, '$.sport')            AS sport,
      json_extract(detail_json, '$.started_at')       AS started_at,
      json_extract(detail_json, '$.distance_m')       AS distance_m,
      json_extract(detail_json, '$.duration_s')       AS duration_s,
      json_extract(detail_json, '$.elevation_gain_m') AS elevation_gain_m
    FROM activities
    WHERE (? = '' OR json_extract(detail_json, '$.sport') = ?)
      AND (? = '' OR json_extract(detail_json, '$.started_at') >= ?)
      AND (? = '' OR json_extract(detail_json, '$.started_at') <  ?)
    ORDER BY ${order}
    LIMIT ?
  `, [filter.sport, filter.sport, filter.dateFrom, filter.dateFrom, filter.dateTo, filter.dateTo, limit]);
}

export function useFilteredCount(filter: ActivityFilter): number {
  const db = useSQLiteContext();
  const row = db.getFirstSync<{ n: number }>(`
    SELECT COUNT(*) as n FROM activities
    WHERE (? = '' OR json_extract(detail_json, '$.sport') = ?)
      AND (? = '' OR json_extract(detail_json, '$.started_at') >= ?)
      AND (? = '' OR json_extract(detail_json, '$.started_at') <  ?)
  `, [filter.sport, filter.sport, filter.dateFrom, filter.dateFrom, filter.dateTo, filter.dateTo]);
  return row?.n ?? 0;
}

export function useActivityYears(): string[] {
  const db = useSQLiteContext();
  const rows = db.getAllSync<{ year: string }>(
    `SELECT DISTINCT substr(json_extract(detail_json, '$.started_at'), 1, 4) AS year
     FROM activities
     WHERE json_extract(detail_json, '$.started_at') IS NOT NULL
     ORDER BY year DESC`,
  );
  return rows.map(r => r.year).filter(Boolean);
}

export function useActivity(id: string): ActivityRow | null {
  const db = useSQLiteContext();
  return db.getFirstSync<ActivityRow>(
    'SELECT * FROM activities WHERE id = ?',
    [id],
  ) ?? null;
}

export async function insertActivity(
  db: ReturnType<typeof useSQLiteContext>,
  row: Pick<ActivityRow, 'id' | 'source_hash' | 'detail_json' | 'timeseries_json' | 'geojson' | 'original_path' | 'origin'>
      & { source_path?: string | null },
): Promise<void> {
  await db.runAsync(
    `INSERT OR IGNORE INTO activities
       (id, source_hash, detail_json, timeseries_json, geojson, original_path, source_path, origin)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      row.id,
      row.source_hash,
      row.detail_json,
      row.timeseries_json ?? null,
      row.geojson ?? null,
      row.original_path ?? null,
      row.source_path ?? null,
      row.origin,
    ],
  );
}

export function isSourcePathImported(
  db: ReturnType<typeof useSQLiteContext>,
  sourcePath: string,
): boolean {
  const row = db.getFirstSync<{ id: string }>(
    'SELECT id FROM activities WHERE source_path = ?',
    [sourcePath],
  );
  return row != null;
}

export async function upsertRemoteActivity(
  db: ReturnType<typeof useSQLiteContext>,
  id: string,
  detailJson: string,
): Promise<boolean> {
  const now = Math.floor(Date.now() / 1000);
  const result = await db.runAsync(
    `INSERT INTO activities (id, source_hash, detail_json, origin, synced_at)
     VALUES (?, ?, ?, 'remote', ?)
     ON CONFLICT(id) DO UPDATE SET
       detail_json = excluded.detail_json,
       synced_at   = excluded.synced_at
     WHERE origin = 'remote'`,
    [id, id, detailJson, now],
  );
  return result.changes > 0;
}

export async function deleteRemoteActivities(
  db: ReturnType<typeof useSQLiteContext>,
): Promise<number> {
  const result = await db.runAsync(`DELETE FROM activities WHERE origin = 'remote'`);
  return result.changes;
}

export async function deleteActivity(
  db: ReturnType<typeof useSQLiteContext>,
  id: string,
): Promise<string | null> {
  const row = db.getFirstSync<{ original_path: string | null }>(
    'SELECT original_path FROM activities WHERE id = ?',
    [id],
  );
  await db.runAsync('DELETE FROM activities WHERE id = ?', [id]);
  return row?.original_path ?? null;
}

export async function setActivityTitle(
  db: ReturnType<typeof useSQLiteContext>,
  id: string,
  title: string,
): Promise<void> {
  await db.runAsync(
    `UPDATE activities
     SET edits_json = json_set(COALESCE(edits_json, '{}'), '$.title', ?)
     WHERE id = ?`,
    [title, id],
  );
}

export async function deleteActivities(
  db: ReturnType<typeof useSQLiteContext>,
  ids: string[],
): Promise<Array<string | null>> {
  if (ids.length === 0) return [];
  const rows = db.getAllSync<{ original_path: string | null }>(
    `SELECT original_path FROM activities WHERE id IN (${ids.map(() => '?').join(',')})`,
    ids,
  );
  const placeholders = ids.map(() => '?').join(',');
  await db.runAsync(`DELETE FROM activities WHERE id IN (${placeholders})`, ids);
  return rows.map(r => r.original_path ?? null);
}

// ── Settings ───────────────────────────────────────────────────────────────

export async function getSetting(
  db: ReturnType<typeof useSQLiteContext>,
  key: string,
): Promise<string | null> {
  const row = db.getFirstSync<{ value: string }>(
    'SELECT value FROM settings WHERE key = ?',
    [key],
  );
  return row?.value ?? null;
}

export async function setSetting(
  db: ReturnType<typeof useSQLiteContext>,
  key: string,
  value: string,
): Promise<void> {
  await db.runAsync(
    `INSERT INTO settings (key, value) VALUES (?, ?)
     ON CONFLICT(key) DO UPDATE SET value = excluded.value`,
    [key, value],
  );
}

export function useSetting(key: string): string | null {
  const db = useSQLiteContext();
  const row = db.getFirstSync<{ value: string }>(
    'SELECT value FROM settings WHERE key = ?',
    [key],
  );
  return row?.value ?? null;
}
