import type { SQLiteDatabase } from 'expo-sqlite';

export async function migrateDb(db: SQLiteDatabase): Promise<void> {
  await db.execAsync('PRAGMA journal_mode = WAL;');
  await db.execAsync(`
    CREATE TABLE IF NOT EXISTS activities (
      id              TEXT PRIMARY KEY,
      source_hash     TEXT NOT NULL,
      detail_json     TEXT NOT NULL,
      timeseries_json TEXT,
      geojson         TEXT,
      original_path   TEXT,
      synced_at       INTEGER,
      origin          TEXT NOT NULL CHECK(origin IN ('local', 'remote')),
      created_at      INTEGER NOT NULL DEFAULT (unixepoch())
    );

    CREATE INDEX IF NOT EXISTS idx_activities_created_at
      ON activities(created_at DESC);

    CREATE TABLE IF NOT EXISTS settings (
      key   TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );
  `);

  // Migration v2: source_path stores the original filesystem path a file was
  // imported from (e.g. /sdcard/Karoo/Rides/ride.fit), used for watch-folder
  // deduplication without re-hashing files.
  try {
    await db.execAsync('ALTER TABLE activities ADD COLUMN source_path TEXT');
    await db.execAsync(
      'CREATE INDEX IF NOT EXISTS idx_activities_source_path ON activities(source_path)',
    );
  } catch {
    // Column already exists — migration already ran, ignore.
  }

  // Migration v3: edits_json stores user overrides (e.g. {"title": "My title"})
  // kept separate from detail_json so server re-extraction (Option A) never
  // clobbers user edits.
  try {
    await db.execAsync('ALTER TABLE activities ADD COLUMN edits_json TEXT');
  } catch {
    // Column already exists — migration already ran, ignore.
  }
}
