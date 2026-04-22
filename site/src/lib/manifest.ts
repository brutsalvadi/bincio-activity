/**
 * Build-time helpers for reading the BAS shard manifest.
 * Only import this in .astro frontmatter — it uses Node.js APIs.
 */
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

export function findDataDir(): string | null {
  const candidates = [
    process.env.BINCIO_DATA_DIR,
    resolve(process.cwd(), 'public', 'data'),
    resolve(process.cwd(), '..', 'bincio_data'),
  ].filter(Boolean) as string[];
  return candidates.find(d => {
    try { readFileSync(join(d, 'index.json')); return true; } catch { return false; }
  }) ?? null;
}

export interface ShardHandle {
  handle: string;
  /** Shard URL as written in the manifest (relative to data root). */
  url: string;
}

export function isInstancePrivate(): boolean {
  try {
    const dataDir = findDataDir();
    if (!dataDir) return false;
    const root = JSON.parse(readFileSync(join(dataDir, 'index.json'), 'utf-8'));
    return root?.instance?.private === true;
  } catch {
    return false;
  }
}

export function readShardHandles(): ShardHandle[] {
  try {
    const dataDir = findDataDir();
    if (!dataDir) return [];
    const root = JSON.parse(readFileSync(join(dataDir, 'index.json'), 'utf-8'));
    const shards: Array<{ handle?: string; url: string }> = root.shards ?? [];
    return shards
      .filter(s => !!s.handle)
      .map(s => ({ handle: s.handle!, url: s.url }));
  } catch {
    return [];
  }
}
