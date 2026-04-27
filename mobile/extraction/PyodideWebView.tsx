import { StyleSheet } from 'react-native';
import WebView from 'react-native-webview';
import { handleWebViewMessage, pyodideRef } from './extractActivity';

const CDN        = 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/';
// v0.18.1: last version whose JS wrapper avoids ??, ?., and other syntax
// unavailable on Chrome <80 (e.g. Karoo WebView 61). Used in the compat path.
const CDN_COMPAT  = 'https://cdn.jsdelivr.net/pyodide/v0.18.1/full/';

// Python snippets embedded as JSON strings to avoid any JS/TS escaping issues.
const PY_INSTALL_PACKAGES = [
  'import micropip',
  'await micropip.install(["fitdecode", "gpxpy"])',
].join('\n');

// emfs:// is Pyodide's Emscripten-FS URL scheme — the only reliable way to
// install a wheel from bytes without an http/https URL (blob: URLs are not
// recognised by micropip and cause an InvalidRequirement parse error).
// _wheel_path is set as a Pyodide global before this runs.
const PY_INSTALL_WHEEL = [
  'import micropip',
  'await micropip.install("emfs://" + _wheel_path, deps=False)',
].join('\n');

const PY_EXTRACT = [
  'import json, shutil',
  'from pathlib import Path',
  'from bincio.extract.parsers.factory import parse_file',
  'from bincio.extract.metrics import compute',
  'from bincio.extract.writer import make_activity_id, write_activity',
  '',
  'outdir = Path("/tmp/bincio_out")',
  'if outdir.exists(): shutil.rmtree(outdir)',
  'outdir.mkdir()',
  '',
  'activity = parse_file(Path("/tmp/" + _filename))',
  'metrics  = compute(activity)',
  'write_activity(activity, metrics, outdir, privacy="public", rdp_epsilon=0.0001)',
  'act_id = make_activity_id(activity)',
  '',
  'detail_path  = outdir / "activities" / (act_id + ".json")',
  'ts_path      = outdir / "activities" / (act_id + ".timeseries.json")',
  'geojson_path = outdir / "activities" / (act_id + ".geojson")',
  '',
  '# write_activity in the installed wheel silently skips timeseries — write it directly.',
  'if not ts_path.exists():',
  '    from bincio.extract.timeseries import build_timeseries as _bts',
  '    _ts = _bts(activity.points, activity.started_at, "public")',
  '    if _ts.get("t"):',
  '        ts_path.write_text(json.dumps(_ts))',
  '',
  'json.dumps({',
  '  "id": act_id,',
  '  "detail": json.loads(detail_path.read_text()),',
  '  "timeseries": json.loads(ts_path.read_text()) if ts_path.exists() else None,',
  '  "geojson": json.loads(geojson_path.read_text()) if geojson_path.exists() else None,',
  '})',
].join('\n');

// JSON.stringify gives us safely-quoted JS string literals for embedding in HTML.
const PYODIDE_HTML = `<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body>
<script>
var _PY_INSTALL_PACKAGES = ${JSON.stringify(PY_INSTALL_PACKAGES)};
var _PY_INSTALL_WHEEL    = ${JSON.stringify(PY_INSTALL_WHEEL)};
var _PY_EXTRACT          = ${JSON.stringify(PY_EXTRACT)};
var _CDN       = ${JSON.stringify(CDN)};
var _CDN_COMPAT = ${JSON.stringify(CDN_COMPAT)};

function _post(m) { window.ReactNativeWebView.postMessage(JSON.stringify(m)); }

var pyodide       = null;
var packagesReady = false;
var wheelReady    = false;
var initError     = null;

(async function init() {
  try {
    // WebAssembly.Global was added in Chrome 69. Without it Pyodide cannot
    // initialise on any version. Bail out immediately so the mobile app can
    // fall back to server-side extraction without attempting a 35 MB download.
    if (typeof WebAssembly === 'undefined' || typeof WebAssembly.Global === 'undefined') {
      _post({ type: 'engine_unavailable', reason: 'wasm_global' });
      return;
    }

    _post({ type: 'progress', msg: 'Loading Python runtime…' });

    // Chrome <80 is missing features that modern Pyodide uses in its JS wrapper:
    //   Chrome <71: no globalThis  →  factory throws ReferenceError immediately
    //   Chrome <63: no dynamic import() / for-await-of  →  parse/runtime failure
    // Detection: read Chrome version from UA; absent means non-Chrome (assume modern).
    var _chromeVer = (navigator.userAgent.match(/Chrome\\/([0-9]+)/) || [])[1];
    var _needsPatch = _chromeVer && parseInt(_chromeVer) < 80;

    if (_needsPatch) {
      // Use v0.18.1 — its JS wrapper avoids ??, ?., and other Chrome-80+ syntax.
      // Then apply three text patches before injecting via Blob URL (Blob scripts
      // bypass the browser's module pre-scanner, so patched keywords are invisible).
      //
      // Patches (split/join avoids regex escapes, which template literals corrupt):
      //   1. globalThis polyfill prepended  — Chrome <71 lacks globalThis entirely
      //   2. import( → __loadScript(        — Chrome <63 cannot parse dynamic import
      //   3. for await( → for(              — Chrome <63 lacks async iteration;
      //      the only affected fn (getFsHandles/NativeFS) is never called by us
      window.__loadScript = function(url) {
        return new Promise(function(res, rej) {
          var s = document.createElement('script');
          s.src = url;
          s.onload = res;
          s.onerror = function() { rej(new Error('Failed to load ' + url)); };
          document.head.appendChild(s);
        });
      };
      var _pyResp = await fetch(_CDN_COMPAT + 'pyodide.js');
      if (!_pyResp.ok) throw new Error('Could not fetch pyodide.js (' + _pyResp.status + ')');
      var _pyCode = await _pyResp.text();
      _pyCode = 'var globalThis=typeof globalThis!=="undefined"?globalThis:self;\\n' + _pyCode;
      _pyCode = _pyCode.split('import(').join('__loadScript(');
      _pyCode = _pyCode.split('for await(').join('for(');
      await new Promise(function(res, rej) {
        var blob = new Blob([_pyCode], { type: 'application/javascript' });
        var blobUrl = URL.createObjectURL(blob);
        var s = document.createElement('script');
        s.src = blobUrl;
        s.onload = function() { URL.revokeObjectURL(blobUrl); res(); };
        s.onerror = function() { URL.revokeObjectURL(blobUrl); rej(new Error('Failed to inject patched pyodide.js')); };
        document.head.appendChild(s);
      });
      pyodide = await loadPyodide({ indexURL: _CDN_COMPAT });
    } else {
      await new Promise(function(res, rej) {
        var s = document.createElement('script');
        s.src = _CDN + 'pyodide.js';
        s.onload = res; s.onerror = rej;
        document.head.appendChild(s);
      });
      pyodide = await loadPyodide({ indexURL: _CDN });
    }

    _post({ type: 'progress', msg: 'Loading packages…' });
    await pyodide.loadPackage(['lxml', 'pyyaml', 'micropip']);

    _post({ type: 'progress', msg: 'Installing fitdecode, gpxpy…' });
    await pyodide.runPythonAsync(_PY_INSTALL_PACKAGES);

    packagesReady = true;
    _post({ type: 'pyodide_ready' });
  } catch(e) {
    initError = String(e);
    _post({ type: 'init_error', message: initError });
  }
})();

window._bincioExtract = async function(params) {
  var reqId         = params.reqId;
  var filename      = params.filename;
  var base64        = params.base64;
  var wheelBase64   = params.wheelBase64;    // pre-fetched by React Native (avoids ATS/HTTP issues)
  var wheelFilename = params.wheelFilename;  // e.g. "bincio-0.1.0-py3-none-any.whl"

  function post(m) { _post(Object.assign({}, m, { reqId: reqId })); }

  try {
    // Wait for base packages if still loading
    if (!packagesReady && !initError) {
      await new Promise(function(res, rej) {
        var n = 0;
        var id = setInterval(function() {
          if (packagesReady)  { clearInterval(id); res(undefined); }
          else if (initError) { clearInterval(id); rej(new Error(initError)); }
          else if (++n > 300) { clearInterval(id); rej(new Error('Pyodide init timed out')); }
        }, 200);
      });
    }
    if (initError) throw new Error(initError);

    // Install bincio wheel on first extraction.
    // Wheel bytes arrive pre-fetched from React Native (avoids ATS/HTTP issues).
    // Write to Pyodide's Emscripten FS so micropip can install via emfs:// URL
    // (blob: URLs are not recognised by micropip — they cause an InvalidRequirement error).
    if (!wheelReady) {
      post({ type: 'progress', msg: 'Loading Bincio…' });
      var wheelBytes = Uint8Array.from(atob(wheelBase64), function(c) { return c.charCodeAt(0); });
      var wheelPath  = '/tmp/' + wheelFilename;
      pyodide.FS.writeFile(wheelPath, wheelBytes);
      pyodide.globals.set('_wheel_path', wheelPath);
      await pyodide.runPythonAsync(_PY_INSTALL_WHEEL);
      wheelReady = true;
    }

    post({ type: 'progress', msg: 'Extracting…' });

    // Decode base64 file bytes and write to Pyodide's virtual filesystem
    var bytes = Uint8Array.from(atob(base64), function(c) { return c.charCodeAt(0); });
    pyodide.FS.writeFile('/tmp/' + filename, bytes);

    // SHA-256 of original file bytes (replaces the stub source_hash)
    var hashBuf    = await crypto.subtle.digest('SHA-256', bytes.buffer);
    var sourceHash = Array.from(new Uint8Array(hashBuf))
      .map(function(b) { return b.toString(16).padStart(2, '0'); })
      .join('');

    // Run the bincio extraction pipeline
    pyodide.globals.set('_filename', filename);
    var resultJson = await pyodide.runPythonAsync(_PY_EXTRACT);
    var result     = JSON.parse(resultJson);

    _post({
      type: 'result',
      reqId: reqId,
      id: result.id,
      detail: result.detail,
      timeseries: result.timeseries,
      geojson: result.geojson,
      sourceHash: sourceHash,
    });
  } catch(e) {
    _post({ type: 'error', reqId: reqId, message: e.message || String(e) });
  }
};
</script>
</body></html>`;

export function PyodideWebView() {
  return (
    <WebView
      ref={pyodideRef}
      source={{ html: PYODIDE_HTML, baseUrl: 'https://localhost' }}
      style={styles.hidden}
      onMessage={handleWebViewMessage}
      javaScriptEnabled
      originWhitelist={['*']}
    />
  );
}

const styles = StyleSheet.create({
  // Off-screen but still rendered — display:none / opacity:0 can suppress JS on some platforms.
  hidden: {
    position: 'absolute',
    top: -2000,
    left: 0,
    width: 1,
    height: 1,
  },
});
