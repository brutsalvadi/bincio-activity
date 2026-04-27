import type { ExtractionResult } from './extractActivity';

export async function checkServerAuth(instanceUrl: string, token: string): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(`${instanceUrl}/api/feed`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    throw new Error('Could not reach Bincio instance — check your connection.');
  }
  if (resp.status === 401) throw new Error('Session expired — reconnect in Settings.');
  if (!resp.ok) throw new Error(`Server error (${resp.status})`);
}

export async function extractFileViaServer(
  filename: string,
  base64: string,
  instanceUrl: string,
  token: string,
  onStatus: (msg: string) => void = () => {},
): Promise<ExtractionResult> {
  onStatus('Uploading to Bincio instance…');

  let resp: Response;
  try {
    resp = await fetch(`${instanceUrl}/api/upload/raw`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ filename, base64 }),
    });
  } catch {
    throw new Error('Could not reach Bincio instance — check your connection.');
  }

  if (resp.status === 401) throw new Error('Session expired — reconnect in Settings.');
  if (resp.status === 422) {
    const body = await resp.json().catch(() => ({})) as { detail?: string };
    throw new Error(body.detail ?? 'Server could not process this file.');
  }
  if (!resp.ok) throw new Error(`Server error (${resp.status})`);

  onStatus('Processing on server…');
  const data = await resp.json() as {
    ok: boolean;
    id: string;
    detail: object;
    timeseries: object | null;
    geojson: object | null;
    source_hash: string;
  };

  return {
    id: data.id,
    detail: data.detail,
    timeseries: data.timeseries,
    geojson: data.geojson,
    sourceHash: data.source_hash,
  };
}
