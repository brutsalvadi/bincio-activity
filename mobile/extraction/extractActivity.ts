import { createRef } from 'react';
import { Platform } from 'react-native';
import type WebView from 'react-native-webview';
import type { WebViewMessageEvent } from 'react-native-webview';

export type ExtractionResult = {
  id: string;
  detail: object;
  timeseries: object | null;
  geojson: object | null;
  sourceHash: string;
};

type Pending = {
  resolve: (r: ExtractionResult) => void;
  reject: (e: Error) => void;
  onStatus: (msg: string) => void;
};

export const pyodideRef = createRef<WebView>();

const pending = new Map<string, Pending>();
let reqCounter = 0;
let isExtracting = false;

// Engine readiness — tracked so callers can wait before batching files.
let _engineReady = false;
let _engineError: string | null = null;
// Android <29 (API 27 = Android 8.1, e.g. Karoo) ships with a system WebView
// (Chrome <69) that lacks WebAssembly.Global, so Pyodide cannot run. Mounting
// a WebView on those devices also causes GPU driver crashes (SurfaceView
// conflicts). Skip the engine entirely and route to server extraction instead.
let _engineUnavailable = Platform.OS === 'android' && (Platform.Version as number) < 29;
const _engineResolvers: Array<() => void> = [];
const _engineRejecters: Array<(e: Error) => void> = [];

// Init-phase progress listeners (messages sent before any extraction starts).
const _progressListeners = new Set<(msg: string) => void>();
export function onEngineProgress(cb: (msg: string) => void): () => void {
  _progressListeners.add(cb);
  return () => _progressListeners.delete(cb);
}

export function isEngineAvailable(): boolean | null {
  // null = not yet determined; true = ready; false = unavailable
  if (_engineReady) return true;
  if (_engineUnavailable || _engineError) return false;
  return null;
}

export function waitForEngine(timeoutMs = 300_000): Promise<void> {
  if (_engineReady) return Promise.resolve();
  if (_engineUnavailable) return Promise.reject(new Error('engine_unavailable'));
  if (_engineError) return Promise.reject(new Error(_engineError));
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error('Extraction engine timed out — check network and Bincio instance URL'));
    }, timeoutMs);
    _engineResolvers.push(() => { clearTimeout(timer); resolve(); });
    _engineRejecters.push((e) => { clearTimeout(timer); reject(e); });
  });
}

export function handleWebViewMessage(e: WebViewMessageEvent): void {
  let msg: Record<string, unknown>;
  try { msg = JSON.parse(e.nativeEvent.data); } catch { return; }

  const reqId = msg.reqId as string | undefined;
  const p = reqId ? pending.get(reqId) : undefined;

  switch (msg.type) {
    case 'pyodide_ready':
      _engineReady = true;
      _engineResolvers.splice(0).forEach(fn => fn());
      break;
    case 'engine_unavailable':
      _engineUnavailable = true;
      _engineRejecters.splice(0).forEach(fn => fn(new Error('engine_unavailable')));
      break;
    case 'init_error':
      _engineError = msg.message as string;
      _engineRejecters.splice(0).forEach(fn => fn(new Error(_engineError!)));
      break;
    case 'result':
      if (p) {
        pending.delete(reqId!);
        p.resolve({
          id: msg.id as string,
          detail: msg.detail as object,
          timeseries: (msg.timeseries as object | null) ?? null,
          geojson: (msg.geojson as object | null) ?? null,
          sourceHash: msg.sourceHash as string,
        });
      }
      break;
    case 'error':
      if (p) {
        pending.delete(reqId!);
        p.reject(new Error(msg.message as string));
      }
      break;
    case 'progress':
      if (p) {
        p.onStatus(msg.msg as string);
      } else {
        _progressListeners.forEach(fn => fn(msg.msg as string));
      }
      break;
  }
}

// wheelBase64 is the bincio .whl file pre-fetched by the React Native side
// (native networking supports HTTP on local network; WKWebView does not).
export function extractFile(
  filename: string,
  base64: string,
  wheelBase64: string,
  wheelFilename: string,
  onStatus: (msg: string) => void = () => {},
): Promise<ExtractionResult> {
  if (isExtracting) return Promise.reject(new Error('Another extraction is already in progress'));

  const webview = pyodideRef.current;
  if (!webview) return Promise.reject(new Error('Extraction engine not ready — restart the app'));

  isExtracting = true;
  const reqId = String(++reqCounter);
  const args = JSON.stringify({ reqId, filename, base64, wheelBase64, wheelFilename });

  return new Promise<ExtractionResult>((resolve, reject) => {
    pending.set(reqId, {
      resolve: (r) => { isExtracting = false; resolve(r); },
      reject: (e) => { isExtracting = false; reject(e); },
      onStatus,
    });
    webview.injectJavaScript(`window._bincioExtract(${args}); true;`);
  });
}
