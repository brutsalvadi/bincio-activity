import * as DocumentPicker from 'expo-document-picker';
import * as FileSystem from 'expo-file-system/legacy';
import { useFocusEffect } from 'expo-router';
import { useSQLiteContext } from 'expo-sqlite';
import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, PermissionsAndroid, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { insertActivity, isSourcePathImported, getSetting } from '@/db/queries';
import { PyodideWebView } from '@/extraction/PyodideWebView';
import { extractFile, waitForEngine, onEngineProgress, isEngineAvailable } from '@/extraction/extractActivity';
import { extractFileViaServer, checkServerAuth } from '@/extraction/extractServer';
import { useTheme } from '@/ThemeContext';

async function sha256hex(text: string): Promise<string> {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
}

const FIT_EXTENSIONS   = ['.fit', '.fit.gz'];
const OTHER_EXTENSIONS = ['.gpx', '.tcx', '.gpx.gz', '.tcx.gz'];
const ALL_NATIVE_EXTENSIONS = [...FIT_EXTENSIONS, ...OTHER_EXTENSIONS];

type ImportState =
  | { status: 'idle' }
  | { status: 'loading'; msg: string; current: number; total: number }
  | { status: 'done'; count: number; errors: Array<{ name: string; message: string }> }
  | { status: 'error'; message: string };

export default function ImportScreen() {
  const db = useSQLiteContext();
  const theme = useTheme();
  const [state, setState] = useState<ImportState>({ status: 'idle' });
  const [watchPath, setWatchPath] = useState('');
  const [engineAvailable, setEngineAvailable] = useState<boolean | null>(null);
  const isImporting = useRef(false);

  // Track engine availability so we can show the server-extraction notice.
  useEffect(() => {
    waitForEngine(30_000)
      .then(() => setEngineAvailable(true))
      .catch((e: unknown) => {
        if (e instanceof Error && e.message === 'engine_unavailable') setEngineAvailable(false);
      });
  }, []);

  // Reload watch path every time the Import tab comes into focus so changes
  // saved in Settings are picked up without remounting the tab.
  useFocusEffect(useCallback(() => {
    if (Platform.OS !== 'android') return;
    const row = db.getFirstSync<{ value: string }>(
      'SELECT value FROM settings WHERE key = ?',
      ['auto_import_path'],
    );
    setWatchPath(row?.value ?? '');
  }, [db]));

  // Auto-scan watch folder on mount and when app comes to foreground.
  useEffect(() => {
    if (Platform.OS !== 'android') return;
    runAutoScan();

    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active') runAutoScan();
    });
    return () => sub.remove();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function runAutoScan() {
    if (isImporting.current) return;
    const path = await getSetting(db, 'auto_import_path');
    if (!path) return;
    const instanceUrl = await getSetting(db, 'instance_url');
    if (!instanceUrl) return;

    // Wait for engine — skip auto-scan on init failure, but continue if device is
    // too old for local extraction (importNativeFile will use the server instead).
    try { await waitForEngine(120_000); } catch (e: unknown) {
      if (!(e instanceof Error) || e.message !== 'engine_unavailable') return;
    }

    // Server-mode requires a valid token — verify before touching any files.
    if (isEngineAvailable() === false) {
      const token = await getSetting(db, 'api_token');
      if (!token) return;
      try { await checkServerAuth(instanceUrl, token); } catch { return; }
    }

    const newFiles = await discoverNewFiles(db, path);
    if (newFiles.length === 0) return;

    isImporting.current = true;
    try {
      await processBatch(newFiles.map(f => ({ uri: `file://${f}`, name: f.split('/').pop() ?? f, sourcePath: f })));
    } finally {
      isImporting.current = false;
    }
  }

  async function manualScan() {
    if (isImporting.current) return;
    const path = await getSetting(db, 'auto_import_path');
    if (!path) return;
    const instanceUrl = await getSetting(db, 'instance_url');
    if (!instanceUrl) {
      setState({ status: 'error', message: 'No Bincio instance configured. Go to Settings and enter an instance URL first — it\'s needed to download the extraction engine.' });
      return;
    }

    const serverMode = isEngineAvailable() === false;
    if (!serverMode) {
      setState({ status: 'loading', msg: 'Preparing extraction engine…', current: 0, total: 0 });
      const unsubScan = onEngineProgress((msg) =>
        setState({ status: 'loading', msg, current: 0, total: 0 }),
      );
      try {
        await waitForEngine();
      } catch (e: unknown) {
        if (!(e instanceof Error) || e.message !== 'engine_unavailable') {
          setState({ status: 'error', message: e instanceof Error ? e.message : String(e) });
          return;
        }
        // engine_unavailable — fall through to server mode
      } finally {
        unsubScan();
      }
    } else {
      const token = await getSetting(db, 'api_token');
      if (!token) {
        setState({ status: 'error', message: 'Server extraction requires a Bincio account. Connect in Settings.' });
        return;
      }
      // Verify the token is valid before processing any files.
      setState({ status: 'loading', msg: 'Checking connection…', current: 0, total: 0 });
      try {
        await checkServerAuth(instanceUrl, token);
      } catch (e: unknown) {
        setState({ status: 'error', message: e instanceof Error ? e.message : String(e) });
        return;
      }
    }

    setState({ status: 'loading', msg: 'Scanning…', current: 0, total: 0 });
    const newFiles = await discoverNewFiles(db, path);
    if (newFiles.length === 0) {
      setState({ status: 'done', count: 0, errors: [] });
      return;
    }

    isImporting.current = true;
    try {
      await processBatch(newFiles.map(f => ({ uri: `file://${f}`, name: f.split('/').pop() ?? f, sourcePath: f })));
    } finally {
      isImporting.current = false;
    }
  }

  async function pickFiles() {
    if (isImporting.current) return;
    setState({ status: 'loading', msg: 'Picking files…', current: 0, total: 0 });
    try {
      let result: DocumentPicker.DocumentPickerResult;
      try {
        result = await DocumentPicker.getDocumentAsync({
          type: ['*/*'],
          copyToCacheDirectory: true,
          multiple: true,
        });
      } catch (pickerErr: unknown) {
        // Some Android devices (e.g. Karoo) have no system file picker app.
        const raw = pickerErr instanceof Error ? pickerErr.message : String(pickerErr);
        const noApp = raw.includes('ActivityNotFoundException') || raw.includes('No Activity found');
        setState({
          status: 'error',
          message: noApp
            ? 'No file picker available on this device. Set a Watch directory in Settings to import from a folder.'
            : raw,
        });
        return;
      }

      if (result.canceled || !result.assets?.length) {
        setState({ status: 'idle' });
        return;
      }
      isImporting.current = true;
      const unsubPick = onEngineProgress((msg) =>
        setState({ status: 'loading', msg, current: 0, total: 0 }),
      );
      try {
        await processBatch(result.assets.map(a => ({ uri: a.uri, name: a.name ?? '', sourcePath: null })));
      } finally {
        unsubPick();
        isImporting.current = false;
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setState({ status: 'error', message: msg });
      isImporting.current = false;
    }
  }

  async function processBatch(files: Array<{ uri: string; name: string; sourcePath: string | null }>) {
    const total  = files.length;
    const errors: Array<{ name: string; message: string }> = [];
    let   count  = 0;

    for (let i = 0; i < files.length; i++) {
      const { uri, name, sourcePath } = files[i];
      const lower = name.toLowerCase();

      setState({ status: 'loading', msg: `Processing ${name}…`, current: i + 1, total });

      try {
        if (lower.endsWith('.json')) {
          await importBasJson(uri, name, sourcePath, (msg) =>
            setState({ status: 'loading', msg, current: i + 1, total }),
          );
        } else if (ALL_NATIVE_EXTENSIONS.some(ext => lower.endsWith(ext))) {
          await importNativeFile(uri, name, sourcePath, (msg) =>
            setState({ status: 'loading', msg, current: i + 1, total }),
          );
        } else {
          errors.push({ name, message: 'Unsupported file type' });
          continue;
        }
        count++;
      } catch (e: unknown) {
        errors.push({ name, message: e instanceof Error ? e.message : String(e) });
      }
    }

    setState({ status: 'done', count, errors });
  }

  // ── BAS JSON import (no extraction needed) ──────────────────────────────────

  async function importBasJson(
    uri: string,
    _name: string,
    sourcePath: string | null,
    onStatus: (msg: string) => void,
  ) {
    onStatus('Importing…');
    const text   = await FileSystem.readAsStringAsync(uri);
    const detail = JSON.parse(text);

    if (!detail.id || !detail.started_at) {
      throw new Error('Not a valid BAS activity JSON (missing id or started_at)');
    }

    const hash    = detail.source_hash ?? await sha256hex(text);
    const origDir = `${FileSystem.documentDirectory}originals/`;
    await FileSystem.makeDirectoryAsync(origDir, { intermediates: true });
    const dest = `${origDir}${detail.id}.json`;
    await FileSystem.copyAsync({ from: uri, to: dest });

    await insertActivity(db, {
      id: detail.id,
      source_hash: hash,
      detail_json: text,
      timeseries_json: null,
      geojson: null,
      original_path: dest,
      source_path: sourcePath,
      origin: 'local',
    });
  }

  // ── FIT / GPX / TCX import via Pyodide (local) or server fallback ───────────

  async function importNativeFile(
    uri: string,
    name: string,
    sourcePath: string | null,
    onStatus: (msg: string) => void,
  ) {
    onStatus('Reading file…');

    // Read the original file as base64 so we can (a) pass it to the extractor
    // and (b) copy it to permanent storage without a second read.
    const base64 = await FileSystem.readAsStringAsync(uri, {
      encoding: FileSystem.EncodingType.Base64,
    });

    let result;

    if (isEngineAvailable() === false) {
      // Device WebView is too old for WebAssembly.Global (Chrome <69).
      // Send the raw file to the Bincio instance for server-side extraction.
      const instanceUrl = await getInstanceUrl(db);
      const token = db.getFirstSync<{ value: string }>(
        'SELECT value FROM settings WHERE key = ?', ['api_token'],
      )?.value ?? '';
      if (!token) throw new Error('Server extraction requires a Bincio account — connect in Settings.');
      result = await extractFileViaServer(name, base64, instanceUrl, token, onStatus);
    } else {
      // Fetch the bincio wheel here (React Native networking), not inside the
      // WebView. WKWebView blocks HTTP requests via ATS; RN native networking
      // allows local-network HTTP (NSAllowsLocalNetworking=true in Info.plist).
      const instanceUrl = await getInstanceUrl(db);
      onStatus('Fetching Bincio engine…');
      const { base64: wheelBase64, filename: wheelFilename } = await fetchWheelBase64(instanceUrl);
      result = await extractFile(name, base64, wheelBase64, wheelFilename, onStatus);
    }

    onStatus('Saving…');

    // Copy original file to permanent storage (keeps original bytes for future re-extraction)
    const ext     = name.includes('.') ? name.slice(name.lastIndexOf('.')) : '';
    const origDir = `${FileSystem.documentDirectory}originals/`;
    await FileSystem.makeDirectoryAsync(origDir, { intermediates: true });
    const dest = `${origDir}${result.id}${ext}`;
    await FileSystem.copyAsync({ from: uri, to: dest });

    await insertActivity(db, {
      id: result.id,
      source_hash: result.sourceHash,
      detail_json: JSON.stringify(result.detail),
      timeseries_json: result.timeseries ? JSON.stringify(result.timeseries) : null,
      geojson: result.geojson ? JSON.stringify(result.geojson) : null,
      original_path: dest,
      source_path: sourcePath,
      origin: 'local',
    });
  }

  return (
    <View style={styles.screen}>
      {/* Hidden WebView for Pyodide — only mounted on devices that can run it.
          Android <29 has a system WebView (Chrome <69) that lacks WebAssembly.Global
          AND causes GPU SurfaceView crashes on old drivers. Skip it entirely there. */}
      {(Platform.OS !== 'android' || (Platform.Version as number) >= 29) && (
        <View style={styles.hiddenEngine}>
          <PyodideWebView />
        </View>
      )}
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.header}>Import</Text>

      <Text style={styles.body}>
        Import FIT, GPX, or TCX files — extracted on your device, nothing uploaded.
        You can also import pre-extracted BAS <Text style={[styles.code, { color: theme.accent }]}>.json</Text> files.
      </Text>

      {engineAvailable === false && (
        <View style={styles.serverNotice}>
          <Text style={styles.serverNoticeText}>
            This device's Android WebView is too old to run local extraction (requires Chrome 69+).
            Activities are processed by your Bincio instance instead — a connected account is required.
          </Text>
        </View>
      )}

      {watchPath ? (
        <View style={styles.watchBox}>
          <Text style={styles.watchLabel}>Watch folder</Text>
          <Text style={styles.watchPath} numberOfLines={2}>{watchPath}</Text>
          <Pressable
            style={[styles.scanButton, state.status === 'loading' && styles.buttonDisabled]}
            onPress={state.status !== 'loading' ? manualScan : undefined}
          >
            <Text style={styles.buttonText}>
              {state.status === 'loading' ? 'Working…' : '↺ Scan for new rides'}
            </Text>
          </Pressable>
        </View>
      ) : null}

      <Pressable
        style={[styles.button, state.status === 'loading' && styles.buttonDisabled]}
        onPress={state.status !== 'loading' ? pickFiles : undefined}
      >
        <Text style={styles.buttonText}>
          {state.status === 'loading' ? 'Working…' : '＋ Pick files'}
        </Text>
      </Pressable>

      {state.status === 'loading' && (
        <View style={styles.statusBox}>
          {state.total > 1 && (
            <Text style={styles.statusCounter}>
              File {state.current} of {state.total}
            </Text>
          )}
          <Text style={[styles.statusMsg, { color: theme.accent }]}>{state.msg}</Text>
          {engineAvailable !== false && (
            <Text style={styles.statusHint}>
              First run downloads ~35 MB (Python runtime + packages). Subsequent runs are instant.
            </Text>
          )}
        </View>
      )}

      {state.status === 'done' && (
        <View style={[styles.success, state.count === 0 && state.errors.length === 0 && styles.successEmpty]}>
          <Text style={styles.successText}>
            {state.count === 0 && state.errors.length === 0
              ? 'No new rides found'
              : `✓ Imported ${state.count} ${state.count === 1 ? 'activity' : 'activities'}`}
          </Text>
          {state.errors.map((e, i) => (
            <Text key={i} style={styles.batchError}>✗ {e.name}: {e.message}</Text>
          ))}
          <Pressable onPress={() => setState({ status: 'idle' })}>
            <Text style={styles.errorRetry}>Dismiss</Text>
          </Pressable>
        </View>
      )}

      {state.status === 'error' && (
        <View style={styles.error}>
          <Text style={styles.errorText}>{state.message}</Text>
          <Pressable onPress={() => setState({ status: 'idle' })}>
            <Text style={styles.errorRetry}>Try again</Text>
          </Pressable>
        </View>
      )}

      <View style={styles.divider} />

      <Text style={styles.sectionTitle}>Supported formats</Text>
      {([
        ['FIT',      'Garmin, Wahoo, Karoo native format'],
        ['GPX',      'Most GPS devices and apps'],
        ['TCX',      'Garmin Training Center'],
        ['BAS JSON', 'Pre-extracted Bincio format (instant)'],
      ] as [string, string][]).map(([fmt, desc]) => (
        <View key={fmt} style={styles.formatRow}>
          <Text style={styles.formatName}>{fmt}</Text>
          <Text style={styles.formatDesc}>{desc}</Text>
        </View>
      ))}

      <View style={styles.notice}>
        <Text style={styles.noticeText}>
          {engineAvailable === false
            ? 'Activities are sent to your Bincio instance for extraction and stored there + locally. A connected account is required.'
            : `FIT/GPX/TCX extraction runs entirely on your device.\nA Bincio instance must be reachable on first run to download the extraction engine (~35 MB, then cached).`}
          {'\n\n'}
          On Karoo: set Watch directory to <Text style={styles.noticeCode}>/sdcard/FitFiles</Text> in Settings to auto-import rides.
        </Text>
      </View>
    </ScrollView>
    </View>
  );
}

// ── Watch-folder helpers ──────────────────────────────────────────────────────

async function requestStoragePermission(): Promise<boolean> {
  if (Platform.OS !== 'android') return true;
  try {
    const granted = await PermissionsAndroid.request(
      PermissionsAndroid.PERMISSIONS.READ_EXTERNAL_STORAGE,
    );
    return granted === PermissionsAndroid.RESULTS.GRANTED;
  } catch {
    return false;
  }
}

async function discoverNewFiles(
  db: ReturnType<typeof useSQLiteContext>,
  watchPath: string,
): Promise<string[]> {
  const ok = await requestStoragePermission();
  if (!ok) return [];

  // Normalize: strip trailing slash, then use file:// URI for expo-fs
  const dir = watchPath.replace(/\/+$/, '');
  const uri = dir.startsWith('file://') ? dir : `file://${dir}`;

  let entries: string[];
  try {
    entries = await FileSystem.readDirectoryAsync(uri);
  } catch {
    return [];
  }

  const newFiles: string[] = [];
  for (const entry of entries) {
    const lower = entry.toLowerCase();
    if (!lower.endsWith('.fit')) continue;
    const fullPath = `${dir}/${entry}`;
    if (!isSourcePathImported(db, fullPath)) {
      newFiles.push(fullPath);
    }
  }
  return newFiles;
}

// ── Module-level helpers ──────────────────────────────────────────────────────

async function getInstanceUrl(db: ReturnType<typeof useSQLiteContext>): Promise<string> {
  const row = db.getFirstSync<{ value: string }>(
    'SELECT value FROM settings WHERE key = ?',
    ['instance_url'],
  );
  return (row?.value ?? '').replace(/\/$/, '');
}

// In-memory cache so repeated imports in one session don't re-download the wheel.
let _cachedWheel: { base64: string; filename: string } | null = null;

async function fetchWheelBase64(instanceUrl: string): Promise<{ base64: string; filename: string }> {
  if (_cachedWheel) return _cachedWheel;

  const base = instanceUrl || 'https://bincio.org';

  // Ask the instance for the canonical wheel URL (handles both dev and prod layouts).
  let wheelUrl = `${base}/api/wheel/download`;
  let wheelFilename = 'bincio-0.1.0-py3-none-any.whl';
  try {
    const vr = await fetch(`${base}/api/wheel/version`, { signal: AbortSignal.timeout(5000) });
    if (vr.ok) {
      const d = await vr.json() as { api_url?: string; url?: string };
      const path = d.api_url ?? d.url ?? '/api/wheel/download';
      wheelUrl = path.startsWith('http') ? path : `${base}${path}`;
      // Extract the filename from the URL path (last segment after final /)
      const urlBasename = wheelUrl.split('/').pop() ?? '';
      if (urlBasename.endsWith('.whl')) wheelFilename = urlBasename;
    }
  } catch {}

  // Fetch via React Native networking (supports local HTTP; WKWebView would block it).
  const resp = await fetch(wheelUrl);
  if (!resp.ok) throw new Error(`Could not download Bincio engine (${resp.status}). Is the instance running?`);
  const buf = await resp.arrayBuffer();
  _cachedWheel = { base64: arrayBufferToBase64(buf), filename: wheelFilename };
  return _cachedWheel;
}

function arrayBufferToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = '';
  // Process in chunks to avoid spread-operator stack overflow on large arrays.
  const CHUNK = 8192;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...(bytes.subarray(i, i + CHUNK) as unknown as number[]));
  }
  return btoa(binary);
}

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  screen:       { flex: 1, backgroundColor: '#09090b' },
  hiddenEngine: { position: 'absolute', width: 1, height: 1, overflow: 'hidden' },
  container: { flex: 1 },
  content:   { padding: 16, paddingTop: 60, paddingBottom: 40 },
  header:    { color: '#fff', fontSize: 22, fontWeight: '700', marginBottom: 12 },
  body:      { color: '#a1a1aa', fontSize: 14, lineHeight: 20, marginBottom: 24 },
  code:      { color: '#60a5fa', fontFamily: 'monospace' },
  serverNotice: {
    backgroundColor: '#1c1400', borderRadius: 8, borderWidth: 1,
    borderColor: '#854d0e', padding: 12, marginBottom: 16,
  },
  serverNoticeText: { color: '#fbbf24', fontSize: 13, lineHeight: 18 },
  watchBox: {
    backgroundColor: '#18181b', borderRadius: 10, borderWidth: 1,
    borderColor: '#27272a', padding: 14, marginBottom: 16, gap: 10,
  },
  watchLabel:  { color: '#71717a', fontSize: 11, fontWeight: '600', letterSpacing: 0.5 },
  watchPath:   { color: '#a1a1aa', fontSize: 13, fontFamily: 'monospace' },
  scanButton: {
    backgroundColor: '#16a34a', borderRadius: 10,
    paddingVertical: 14, alignItems: 'center',
  },
  button: {
    backgroundColor: '#2563eb', borderRadius: 10,
    paddingVertical: 14, alignItems: 'center', marginBottom: 16,
  },
  buttonDisabled: { opacity: 0.5 },
  buttonText:     { color: '#fff', fontWeight: '600', fontSize: 16 },
  statusBox: {
    backgroundColor: '#18181b', borderRadius: 8, borderWidth: 1,
    borderColor: '#27272a', padding: 14, marginBottom: 16, gap: 6,
  },
  statusCounter: { color: '#71717a', fontSize: 12, textAlign: 'center' },
  statusMsg:     { color: '#60a5fa', fontSize: 14, textAlign: 'center' },
  statusHint:    { color: '#52525b', fontSize: 12, textAlign: 'center', lineHeight: 16 },
  success: {
    backgroundColor: '#14532d', borderRadius: 8, padding: 12, marginBottom: 16, gap: 6,
  },
  successEmpty: { backgroundColor: '#1c1c1e' },
  successText:  { color: '#86efac', fontSize: 14 },
  batchError:   { color: '#fca5a5', fontSize: 12 },
  error: {
    backgroundColor: '#450a0a', borderRadius: 8, padding: 12, marginBottom: 16, gap: 8,
  },
  errorText:  { color: '#fca5a5', fontSize: 14 },
  errorRetry: { color: '#71717a', fontSize: 13, textDecorationLine: 'underline', marginTop: 4 },
  divider:      { height: 1, backgroundColor: '#27272a', marginVertical: 24 },
  sectionTitle: { color: '#a1a1aa', fontSize: 12, fontWeight: '600', marginBottom: 12, letterSpacing: 0.5 },
  formatRow:    { flexDirection: 'row', gap: 12, marginBottom: 10 },
  formatName:   { color: '#f4f4f5', fontSize: 13, fontWeight: '600', width: 72 },
  formatDesc:   { color: '#71717a', fontSize: 13, flex: 1 },
  notice: {
    marginTop: 8, backgroundColor: '#18181b',
    borderRadius: 8, padding: 12, borderWidth: 1, borderColor: '#27272a',
  },
  noticeText: { color: '#71717a', fontSize: 12, lineHeight: 18 },
  noticeCode: { fontFamily: 'monospace', color: '#a1a1aa' },
});
