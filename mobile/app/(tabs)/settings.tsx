import { useSQLiteContext } from 'expo-sqlite';
import { useState } from 'react';
import {
  ActivityIndicator, Platform, Pressable, ScrollView, StyleSheet,
  Text, TextInput, View,
} from 'react-native';
import { deleteRemoteActivities, getSetting, setSetting, useSetting } from '@/db/queries';
import { PALETTES, type PaletteKey } from '@/theme';
import { useTheme, usePaletteControl } from '@/ThemeContext';

export default function SettingsScreen() {
  const db = useSQLiteContext();

  const storedUrl      = useSetting('instance_url') ?? '';
  const storedHandle   = useSetting('handle') ?? '';
  const storedPath     = useSetting('auto_import_path') ?? '';
  const storedToken    = useSetting('api_token');
  const storedSyncMode     = (useSetting('sync_mode') ?? 'summaries') as 'summaries' | 'full';
  const storedSyncUpload   = useSetting('sync_upload') === 'true';
  const storedUploadFormat = (useSetting('upload_format') ?? 'raw') as 'raw' | 'bas';

  const [instanceUrl, setInstanceUrl] = useState(storedUrl);
  const [handle,      setHandle]      = useState(storedHandle);
  const [autoPath,    setAutoPath]    = useState(storedPath);
  const [syncMode,      setSyncMode]      = useState(storedSyncMode);
  const [syncUpload,    setSyncUpload]    = useState(storedSyncUpload);
  const [uploadFormat,  setUploadFormat]  = useState(storedUploadFormat);
  const [saved,       setSaved]       = useState(false);
  const theme = useTheme();
  const { paletteKey: palette, setPaletteOverride } = usePaletteControl();

  const [password,    setPassword]    = useState('');
  const [connecting,  setConnecting]  = useState(false);
  const [connectMsg,  setConnectMsg]  = useState<{ ok: boolean; text: string } | null>(null);

  const [resetArmed,  setResetArmed]  = useState(false);
  const [resetMsg,    setResetMsg]    = useState<string | null>(null);

  async function save() {
    await setSetting(db, 'instance_url', instanceUrl.trim());
    await setSetting(db, 'handle', handle.trim());
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  async function connect() {
    const url = instanceUrl.trim().replace(/\/$/, '');
    const h   = handle.trim();
    if (!url || !h || !password) {
      setConnectMsg({ ok: false, text: 'Fill in URL, handle, and password first.' });
      return;
    }
    setConnecting(true);
    setConnectMsg(null);
    try {
      const resp = await fetch(`${url}/api/auth/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ handle: h, password }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        setConnectMsg({ ok: false, text: err.detail ?? `Error ${resp.status}` });
        return;
      }
      const data = await resp.json();
      await setSetting(db, 'instance_url', url);
      await setSetting(db, 'handle', h);
      await setSetting(db, 'api_token', data.token);
      setPassword('');
      setConnectMsg({ ok: true, text: `Connected as ${data.display_name || h}` });
    } catch {
      setConnectMsg({ ok: false, text: 'Could not reach instance — check the URL.' });
    } finally {
      setConnecting(false);
    }
  }

  async function disconnect() {
    await setSetting(db, 'api_token', '');
    setConnectMsg(null);
  }

  async function resetSyncedData() {
    if (!resetArmed) {
      setResetArmed(true);
      return;
    }
    const n = await deleteRemoteActivities(db);
    setResetArmed(false);
    setResetMsg(`Removed ${n} synced ${n === 1 ? 'activity' : 'activities'}`);
    setTimeout(() => setResetMsg(null), 3000);
  }

  const isConnected = !!storedToken;

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.header}>Settings</Text>

      <Section title="Instance">
        <Field
          label="Instance URL"
          placeholder="https://bincio.org"
          value={instanceUrl}
          onChangeText={setInstanceUrl}
          autoCapitalize="none"
          keyboardType="url"
        />
        <Field
          label="Handle"
          placeholder="yourhandle"
          value={handle}
          onChangeText={setHandle}
          autoCapitalize="none"
        />
        <Text style={styles.hint}>
          Connect to a Bincio instance to sync your activities. Leave blank to use
          the app offline only.
        </Text>
      </Section>

      <Pressable style={styles.saveButton} onPress={save}>
        <Text style={styles.saveButtonText}>
          {saved ? '✓ Saved' : 'Save'}
        </Text>
      </Pressable>

      <Section title="Connection">
        {isConnected ? (
          <>
            <Row label="Status" value={`Connected as ${storedHandle || '—'}`} />
            <Pressable style={styles.disconnectButton} onPress={disconnect}>
              <Text style={styles.disconnectText}>Disconnect</Text>
            </Pressable>
          </>
        ) : (
          <>
            <Field
              label="Password"
              placeholder="••••••••"
              value={password}
              onChangeText={setPassword}
              autoCapitalize="none"
              secureTextEntry
            />
            <Pressable
              style={[styles.connectButton, connecting && styles.buttonDisabled]}
              onPress={connecting ? undefined : connect}
            >
              {connecting
                ? <ActivityIndicator color="#fff" size="small" />
                : <Text style={styles.connectText}>Connect</Text>}
            </Pressable>
          </>
        )}
        {connectMsg && (
          <Text style={connectMsg.ok ? styles.msgOk : styles.msgErr}>
            {connectMsg.text}
          </Text>
        )}
        <Text style={styles.hint}>
          Your password is used once to obtain a session token, then forgotten.
          The token is stored locally and sent with each sync request.
        </Text>
      </Section>

      {Platform.OS === 'android' && (
        <Section title="Auto-import (Android)">
          {!storedUrl ? (
            <Text style={[styles.hint, styles.hintWarn]}>
              Configure and save a Bincio instance URL above first — it's needed to download the extraction engine.
            </Text>
          ) : (
            <>
              <Field
                label="Watch directory"
                placeholder="/sdcard/FitFiles"
                value={autoPath}
                onChangeText={setAutoPath}
                onBlur={() => setSetting(db, 'auto_import_path', autoPath.trim())}
                autoCapitalize="none"
              />
              <Text style={styles.hint}>
                New FIT files in this folder are imported automatically when you
                open the app. Leave blank to disable. Requires storage permission.
              </Text>
            </>
          )}
        </Section>
      )}

      <Section title="Sync">
        <Text style={styles.subLabel}>Download</Text>
        <View style={styles.modeRow}>
          <ModeButton label="Summaries only" active={syncMode === 'summaries'} accent={theme.accent} dim={theme.dim}
            onPress={() => { setSyncMode('summaries'); setSetting(db, 'sync_mode', 'summaries'); }} />
          <ModeButton label="Full data" active={syncMode === 'full'} accent={theme.accent} dim={theme.dim}
            onPress={() => { setSyncMode('full'); setSetting(db, 'sync_mode', 'full'); }} />
        </View>
        <Text style={styles.hint}>
          {syncMode === 'full'
            ? 'Downloads map route and elevation chart for every activity during sync. Uses more storage and takes longer.'
            : 'Syncs activity summaries only. Map and chart are fetched on demand when you open an activity.'}
        </Text>
        <Text style={[styles.subLabel, { borderTopWidth: 1, borderTopColor: '#27272a', paddingTop: 12 }]}>Upload</Text>
        <View style={styles.modeRow}>
          <ModeButton label="Off" active={!syncUpload} accent={theme.accent} dim={theme.dim}
            onPress={() => { setSyncUpload(false); setSetting(db, 'sync_upload', 'false'); }} />
          <ModeButton label="Upload local activities" active={syncUpload} accent={theme.accent} dim={theme.dim}
            onPress={() => { setSyncUpload(true); setSetting(db, 'sync_upload', 'true'); }} />
        </View>
        <Text style={styles.hint}>
          {syncUpload
            ? 'Local activities are uploaded to the instance during sync.'
            : 'Local activities stay on device only.'}
        </Text>
        <Text style={[styles.subLabel, { borderTopWidth: 1, borderTopColor: '#27272a', paddingTop: 12 }]}>Upload format</Text>
        <View style={styles.modeRow}>
          <ModeButton label="Original file" active={uploadFormat === 'raw'} accent={theme.accent} dim={theme.dim}
            onPress={() => { setUploadFormat('raw'); setSetting(db, 'upload_format', 'raw'); }} />
          <ModeButton label="Extracted JSON" active={uploadFormat === 'bas'} accent={theme.accent} dim={theme.dim}
            onPress={() => { setUploadFormat('bas'); setSetting(db, 'upload_format', 'bas'); }} />
        </View>
        <Text style={styles.hint}>
          {uploadFormat === 'raw'
            ? 'Uploads the original FIT/GPX/TCX file. The server re-extracts it with DEM elevation correction and updates your local copy.'
            : 'Uploads the pre-extracted JSON. Faster, but no DEM elevation correction.'}
        </Text>
      </Section>

      <Section title="Palette">
        <Text style={[styles.hint, { paddingBottom: 0 }]}>
          Auto-switches to race colours during Giro, Tour, and Vuelta. Override here for testing.
        </Text>
        <View style={styles.modeRow}>
          {(['auto', 'default', 'giro', 'tour', 'vuelta'] as PaletteKey[]).map(key => {
            const label = key === 'auto' ? 'Auto' : PALETTES[key as keyof typeof PALETTES].label;
            const keyAccent = key === 'auto' ? theme.accent : PALETTES[key as keyof typeof PALETTES].accent;
            const keyDim    = key === 'auto' ? theme.dim    : PALETTES[key as keyof typeof PALETTES].dim;
            return (
              <ModeButton
                key={key}
                label={label}
                active={palette === key}
                accent={keyAccent}
                dim={keyDim}
                onPress={() => setPaletteOverride(key)}
              />
            );
          })}
        </View>
      </Section>

      <Section title="Data">
        <Pressable
          style={[styles.resetButton, resetArmed && styles.resetButtonArmed]}
          onPress={resetSyncedData}
          onBlur={() => setResetArmed(false)}
        >
          <Text style={[styles.resetText, resetArmed && styles.resetTextArmed]}>
            {resetArmed ? 'Tap again to confirm' : 'Reset synced data'}
          </Text>
        </Pressable>
        {resetMsg && <Text style={styles.msgOk}>{resetMsg}</Text>}
        <Text style={styles.hint}>
          Removes all activities synced from the instance. Locally imported files are kept.
        </Text>
      </Section>

      <Section title="About">
        <Row label="Version" value="0.1.0 (Phase 0.5)" />
        <Row label="Schema"  value="BAS 1.0" />
      </Section>
    </ScrollView>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      <View style={styles.sectionBody}>{children}</View>
    </View>
  );
}

function Field({
  label, placeholder, value, onChangeText, ...rest
}: {
  label: string;
  placeholder: string;
  value: string;
  onChangeText: (v: string) => void;
  [key: string]: unknown;
}) {
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <TextInput
        style={styles.input}
        placeholder={placeholder}
        placeholderTextColor="#52525b"
        value={value}
        onChangeText={onChangeText}
        {...rest}
      />
    </View>
  );
}

function ModeButton({ label, active, accent, dim, onPress }: {
  label: string; active: boolean; accent: string; dim: string; onPress: () => void;
}) {
  return (
    <Pressable
      style={[styles.modeButton, active && { backgroundColor: dim, borderColor: accent }]}
      onPress={onPress}
    >
      <Text style={[styles.modeButtonText, active && { color: accent }]}>{label}</Text>
    </Pressable>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={styles.rowValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#09090b' },
  content:   { padding: 16, paddingTop: 60, paddingBottom: 40 },
  header:    { color: '#fff', fontSize: 22, fontWeight: '700', marginBottom: 24 },
  section:   { marginBottom: 28 },
  sectionTitle: {
    color: '#a1a1aa', fontSize: 11, fontWeight: '600',
    letterSpacing: 0.8, marginBottom: 8,
  },
  sectionBody: {
    backgroundColor: '#18181b', borderRadius: 10,
    borderWidth: 1, borderColor: '#27272a', overflow: 'hidden',
  },
  field:      { padding: 14, borderBottomWidth: 1, borderBottomColor: '#27272a' },
  fieldLabel: { color: '#71717a', fontSize: 11, marginBottom: 4 },
  input:      { color: '#f4f4f5', fontSize: 15 },
  hint:       { color: '#52525b', fontSize: 12, lineHeight: 16, padding: 12 },
  hintWarn:   { color: '#a16207' },
  row: {
    flexDirection: 'row', justifyContent: 'space-between',
    paddingHorizontal: 14, paddingVertical: 12,
    borderBottomWidth: 1, borderBottomColor: '#27272a',
  },
  rowLabel: { color: '#a1a1aa', fontSize: 14 },
  rowValue: { color: '#71717a', fontSize: 14 },
  saveButton: {
    backgroundColor: '#2563eb', borderRadius: 10,
    paddingVertical: 14, alignItems: 'center', marginBottom: 28,
  },
  saveButtonText: { color: '#fff', fontWeight: '600', fontSize: 16 },
  connectButton: {
    backgroundColor: '#059669', borderRadius: 8, margin: 12,
    paddingVertical: 12, alignItems: 'center',
  },
  connectText:      { color: '#fff', fontWeight: '600', fontSize: 15 },
  buttonDisabled:   { opacity: 0.5 },
  disconnectButton: {
    margin: 12, paddingVertical: 10, alignItems: 'center',
    borderRadius: 8, borderWidth: 1, borderColor: '#3f3f46',
  },
  disconnectText: { color: '#71717a', fontSize: 14 },
  msgOk:  { color: '#86efac', fontSize: 13, paddingHorizontal: 12, paddingBottom: 10 },
  msgErr: { color: '#fca5a5', fontSize: 13, paddingHorizontal: 12, paddingBottom: 10 },
  subLabel:           { color: '#52525b', fontSize: 11, fontWeight: '600', letterSpacing: 0.6, paddingHorizontal: 12, paddingTop: 12, paddingBottom: 4 },
  modeRow:            { flexDirection: 'row', gap: 8, padding: 12 },
  modeButton:     { flex: 1, paddingVertical: 9, borderRadius: 8, borderWidth: 1, borderColor: '#3f3f46', alignItems: 'center' },
  modeButtonText: { color: '#71717a', fontSize: 13, fontWeight: '500' },
  resetButton: {
    margin: 12, paddingVertical: 10, alignItems: 'center',
    borderRadius: 8, borderWidth: 1, borderColor: '#3f3f46',
  },
  resetButtonArmed: { borderColor: '#ef4444', backgroundColor: '#1c0a0a' },
  resetText:        { color: '#71717a', fontSize: 14 },
  resetTextArmed:   { color: '#ef4444', fontWeight: '600' },
});
