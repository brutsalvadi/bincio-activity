import { Camera, GeoJSONSource, Layer, Map } from '@maplibre/maplibre-react-native';
import * as FileSystem from 'expo-file-system';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { Alert, Modal, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import Svg, { Defs, LinearGradient, Path, Stop } from 'react-native-svg';
import { useSQLiteContext } from 'expo-sqlite';
import { deleteActivity, setActivityTitle, useActivity, useSetting } from '@/db/queries';
import { useTheme } from '@/ThemeContext';

const MAP_STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';

// ── Types ────────────────────────────────────────────────────────────────────

type Timeseries = {
  t: number[];
  elevation_m:  (number | null)[];
  speed_kmh?:   (number | null)[] | null;
  hr_bpm?:      (number | null)[] | null;
  cadence_rpm?: (number | null)[] | null;
  power_w?:     (number | null)[] | null;
  lat?: (number | null)[] | null;
  lon?: (number | null)[] | null;
};

// ── Screen ───────────────────────────────────────────────────────────────────

export default function ActivityScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const db = useSQLiteContext();
  const theme = useTheme();
  const row = useActivity(id);
  const instanceUrl = useSetting('instance_url')?.replace(/\/$/, '') ?? '';
  const token = useSetting('api_token') ?? '';

  const [geojson, setGeojson] = useState<object | null>(null);
  const [timeseries, setTimeseries] = useState<Timeseries | null>(null);
  const [loadingMap, setLoadingMap] = useState(false);
  const [loadingChart, setLoadingChart] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');

  async function confirmDelete() {
    Alert.alert(
      'Delete activity',
      'This will permanently remove this activity from your device.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            const originalPath = await deleteActivity(db, id);
            if (originalPath) {
              try { await FileSystem.deleteAsync(originalPath, { idempotent: true }); } catch {}
            }
            router.back();
          },
        },
      ],
    );
  }

  // instanceUrl and token are in the dep array to avoid a stale-closure bug in
  // release builds: Hermes executes effects sooner and captures empty strings if
  // the deps are omitted.  Guards on geojson/timeseries prevent double-fetching.
  useEffect(() => {
    if (!row) return;

    if (row.geojson) {
      setGeojson(JSON.parse(row.geojson));
    } else if (row.origin === 'remote' && instanceUrl && token) {
      setLoadingMap(true);
      fetch(`${instanceUrl}/api/activity/${row.id}/geojson`, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then(r => r.ok ? r.json() : null)
        .then(data => { if (data) setGeojson(data); })
        .catch(() => {})
        .finally(() => setLoadingMap(false));
    }

    if (row.timeseries_json) {
      setTimeseries(JSON.parse(row.timeseries_json));
    } else if (row.origin === 'remote' && instanceUrl && token) {
      setLoadingChart(true);
      fetch(`${instanceUrl}/api/activity/${row.id}/timeseries`, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then(r => r.ok ? r.json() : null)
        .then(data => { if (data) setTimeseries(data); })
        .catch(() => {})
        .finally(() => setLoadingChart(false));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row?.id, instanceUrl, token]);

  if (!row) {
    return (
      <View style={styles.center}>
        <Text style={styles.notFound}>Activity not found</Text>
      </View>
    );
  }

  const detail = JSON.parse(row.detail_json);
  const edits = row.edits_json ? JSON.parse(row.edits_json) : {};
  const displayTitle = edits.title ?? detail.title;
  const canEdit = row.origin === 'local';
  const km          = detail.distance_m != null ? (detail.distance_m / 1000).toFixed(2) : null;
  const elev        = detail.elevation_gain_m != null ? Math.round(detail.elevation_gain_m) : null;
  const elevLoss    = detail.elevation_loss_m != null ? Math.round(Math.abs(detail.elevation_loss_m)) : null;
  const movingTime  = detail.moving_time_s != null ? formatDuration(detail.moving_time_s) : null;
  const speed       = detail.avg_speed_kmh != null ? detail.avg_speed_kmh.toFixed(1) : null;
  const hr          = detail.avg_hr_bpm != null ? Math.round(detail.avg_hr_bpm) : null;
  const power       = detail.avg_power_w != null ? Math.round(detail.avg_power_w) : null;
  const date        = new Date(detail.started_at).toLocaleDateString(undefined, {
    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
  });

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <View style={styles.topBar}>
        <Pressable style={styles.backButton} onPress={() => router.back()}>
          <Text style={[styles.backText, { color: theme.accent }]}>← Back</Text>
        </Pressable>
        <Pressable style={styles.deleteButton} onPress={confirmDelete}>
          <Text style={styles.deleteText}>Delete</Text>
        </Pressable>
      </View>

      <Text style={styles.sport}>{detail.sport ?? 'Activity'}</Text>
      {editingTitle ? (
        <TextInput
          style={styles.titleInput}
          value={titleDraft}
          onChangeText={setTitleDraft}
          autoFocus
          returnKeyType="done"
          onEndEditing={(e) => {
            const trimmed = e.nativeEvent.text.trim();
            if (trimmed && trimmed !== displayTitle) {
              setActivityTitle(db, id, trimmed);
            }
            setEditingTitle(false);
          }}
        />
      ) : (
        <Pressable
          onPress={canEdit ? () => { setTitleDraft(displayTitle); setEditingTitle(true); } : undefined}
          style={styles.titleRow}
        >
          <Text style={styles.title}>{displayTitle}</Text>
          {canEdit && <Text style={styles.editHint}>✎</Text>}
        </Pressable>
      )}
      <Text style={styles.date}>{date}</Text>

      {/* Map */}
      <RouteMap geojson={geojson} loading={loadingMap} accent={theme.accent} />

      {/* Stats grid */}
      <View style={styles.grid}>
        {km          && <StatCell label="Distance"       value={km}          unit="km"  />}
        {movingTime  && <StatCell label="Moving time"    value={movingTime}  unit=""    />}
        {elev   != null && <StatCell label="Elev gain"   value={String(elev)}     unit="m" />}
        {elevLoss != null && <StatCell label="Elev loss" value={String(elevLoss)} unit="m" />}
        {speed       && <StatCell label="Avg speed"      value={speed}       unit="km/h"/>}
        {hr          && <StatCell label="Avg HR"         value={String(hr)}  unit="bpm" />}
        {power       && <StatCell label="Avg power"      value={String(power)} unit="W" />}
      </View>

      {/* Metric charts */}
      <MetricCharts timeseries={timeseries} loading={loadingChart} accent={theme.accent} />

      {/* Meta */}
      <View style={styles.meta}>
        <MetaRow label="Source"  value={detail.source ?? '—'} />
        <MetaRow label="Device"  value={detail.device ?? '—'} />
        <MetaRow label="Origin"  value={row.origin} />
        <MetaRow label="Synced"  value={row.synced_at ? new Date(row.synced_at * 1000).toLocaleDateString() : 'No'} />
      </View>
    </ScrollView>
  );
}

// ── Map ───────────────────────────────────────────────────────────────────────

function RouteMap({ geojson, loading, accent }: { geojson: object | null; loading: boolean; accent: string }) {
  const [fullscreen, setFullscreen] = useState(false);
  const [currentZoom, setCurrentZoom] = useState(12);
  const cameraRef = useRef<any>(null);

  if (loading) {
    return (
      <View style={styles.mapPlaceholder}>
        <Text style={{ color: accent, fontSize: 13 }}>Loading map…</Text>
      </View>
    );
  }
  if (!geojson) return null;

  // MapLibre uses OpenGL/SurfaceView which crashes the Karoo's Qualcomm GPU
  // driver (Android <29) even without any interaction. Render a pure SVG route
  // trace instead — no native GL surface, no crash.
  if (Platform.OS === 'android' && (Platform.Version as number) < 29) {
    return <SvgRouteView geojson={geojson} accent={accent} />;
  }

  const bounds = geoJsonBounds(geojson);
  const routeSource = (
    <GeoJSONSource id="route" data={geojson as GeoJSON.FeatureCollection}>
      <Layer
        type="line"
        id="route-line"
        paint={{ 'line-color': accent, 'line-width': 3 }}
        layout={{ 'line-cap': 'round', 'line-join': 'round' }}
      />
    </GeoJSONSource>
  );
  const cameraBounds = bounds
    ? { bounds, padding: { top: 24, bottom: 24, left: 24, right: 24 } }
    : undefined;

  return (
    <>
      {/* Thumbnail — tap to expand */}
      <Pressable style={styles.mapContainer} onPress={() => setFullscreen(true)}>
        <Map style={styles.map} mapStyle={MAP_STYLE} dragPan={false} touchZoom={false} touchPitch={false} touchRotate={false}>
          {cameraBounds && <Camera initialViewState={cameraBounds} />}
          {routeSource}
        </Map>
        <View style={styles.mapExpandHint}>
          <Text style={styles.mapExpandText}>⤢ tap to explore</Text>
        </View>
      </Pressable>

      {/* Full-screen map with +/- zoom buttons */}
      <Modal visible={fullscreen} animationType="slide" onRequestClose={() => setFullscreen(false)}>
        <View style={styles.fullscreenMap}>
          <Map
            style={styles.map}
            mapStyle={MAP_STYLE}
            onRegionDidChange={(e: any) => {
              const z = e?.properties?.zoomLevel;
              if (typeof z === 'number') setCurrentZoom(z);
            }}
          >
            {cameraBounds && <Camera ref={cameraRef} initialViewState={cameraBounds} />}
            {routeSource}
          </Map>
          <Pressable style={styles.closeButton} onPress={() => setFullscreen(false)}>
            <Text style={styles.closeText}>✕</Text>
          </Pressable>
          <View style={styles.zoomButtons}>
            <Pressable style={styles.zoomBtn} onPress={() => cameraRef.current?.setCamera({ zoomLevel: currentZoom + 1, animationDuration: 200 })}>
              <Text style={styles.zoomBtnText}>+</Text>
            </Pressable>
            <Pressable style={styles.zoomBtn} onPress={() => cameraRef.current?.setCamera({ zoomLevel: Math.max(1, currentZoom - 1), animationDuration: 200 })}>
              <Text style={styles.zoomBtnText}>−</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
    </>
  );
}

// SVG route trace — used on Android <29 where MapLibre crashes the GPU driver.
// Renders the GPS track as a colored path on a dark background with no tiles.
function SvgRouteView({ geojson, accent }: { geojson: object; accent: string }) {
  const W = 320;
  const H = 180;
  const PAD = 16;

  const all: [number, number][] = [];
  function collect(obj: unknown) {
    if (!obj || typeof obj !== 'object') return;
    const o = obj as Record<string, unknown>;
    if (o.type === 'Feature') { collect(o.geometry); return; }
    if (o.type === 'FeatureCollection') { (o.features as unknown[]).forEach(collect); return; }
    if (o.type === 'LineString') { all.push(...(o.coordinates as [number, number][])); return; }
    if (o.type === 'MultiLineString') { (o.coordinates as [number, number][][]).forEach(c => all.push(...c)); return; }
  }
  collect(geojson);
  if (!all.length) return null;

  const step = Math.max(1, Math.floor(all.length / 500));
  const pts = all.filter((_, i) => i % step === 0);

  const lons = pts.map(c => c[0]);
  const lats = pts.map(c => c[1]);
  const minLon = Math.min(...lons), maxLon = Math.max(...lons);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const spanLon = maxLon - minLon || 0.001;
  const spanLat = maxLat - minLat || 0.001;

  // Correct longitude for latitude (equirectangular)
  const midLat = (minLat + maxLat) / 2;
  const lonFactor = Math.cos((midLat * Math.PI) / 180);
  const adjLon = spanLon * lonFactor;

  const scale = Math.min((W - PAD * 2) / adjLon, (H - PAD * 2) / spanLat);
  const offX = (W - adjLon * scale) / 2;
  const offY = (H - spanLat * scale) / 2;

  const toX = (lon: number) => offX + (lon - minLon) * lonFactor * scale;
  const toY = (lat: number) => H - offY - (lat - minLat) * scale;

  const d = pts.map((c, i) => `${i === 0 ? 'M' : 'L'}${toX(c[0]).toFixed(1)},${toY(c[1]).toFixed(1)}`).join(' ');

  return (
    <View style={[styles.mapContainer, { alignItems: 'center', justifyContent: 'center' }]}>
      <Svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}>
        <Path d={d} fill="none" stroke={accent} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
      </Svg>
    </View>
  );
}

// ── Metric charts ─────────────────────────────────────────────────────────────

type TabKey = 'elevation' | 'speed' | 'hr' | 'cadence' | 'power';

const TAB_META: Record<TabKey, { label: string; unit: string; color: string; decimals: number }> = {
  elevation: { label: 'Elevation', unit: 'm',     color: '#00c8ff', decimals: 0 },
  speed:     { label: 'Speed',     unit: 'km/h',  color: '#ff6b35', decimals: 1 },
  hr:        { label: 'HR',        unit: 'bpm',   color: '#f87171', decimals: 0 },
  cadence:   { label: 'Cadence',   unit: 'rpm',   color: '#a78bfa', decimals: 0 },
  power:     { label: 'Power',     unit: 'W',     color: '#facc15', decimals: 0 },
};

function MetricCharts({ timeseries, loading, accent }: { timeseries: Timeseries | null; loading: boolean; accent: string }) {
  const [activeTab, setActiveTab] = useState<TabKey>('elevation');

  if (loading) {
    return (
      <View style={styles.chartPlaceholder}>
        <Text style={{ color: accent, fontSize: 13 }}>Loading chart…</Text>
      </View>
    );
  }
  if (!timeseries) return null;

  const seriesMap: Record<TabKey, (number | null)[] | null | undefined> = {
    elevation: timeseries.elevation_m,
    speed:     timeseries.speed_kmh,
    hr:        timeseries.hr_bpm,
    cadence:   timeseries.cadence_rpm,
    power:     timeseries.power_w,
  };

  const available = (Object.keys(TAB_META) as TabKey[]).filter(
    k => seriesMap[k]?.some(v => v != null)
  );

  if (!available.length) return null;

  const tab = available.includes(activeTab) ? activeTab : available[0];
  const { color, unit, decimals } = TAB_META[tab];
  const raw = seriesMap[tab]!;

  return (
    <View style={styles.chartContainer}>
      {/* Tab row */}
      <View style={styles.chartTabs}>
        {available.map(k => (
          <Pressable
            key={k}
            style={[styles.chartTab, tab === k && { borderBottomColor: TAB_META[k].color, borderBottomWidth: 2 }]}
            onPress={() => setActiveTab(k)}
          >
            <Text style={[styles.chartTabText, tab === k && { color: TAB_META[k].color }]}>
              {TAB_META[k].label}
            </Text>
          </Pressable>
        ))}
      </View>
      {/* Chart */}
      <MetricChart key={tab} times={timeseries.t} values={raw} color={color} unit={unit} decimals={decimals} />
    </View>
  );
}

function MetricChart({
  times, values, color, unit, decimals,
}: {
  times: number[];
  values: (number | null)[];
  color: string;
  unit: string;
  decimals: number;
}) {
  const W = 340;
  const H = 100;
  const PAD = 4;

  // Downsample to ≤300 points
  const step = Math.max(1, Math.floor(values.length / 300));
  const ts   = times.filter((_, i) => i % step === 0);
  const vs   = values.filter((_, i) => i % step === 0).map(v => v ?? 0);

  const minV = Math.min(...vs);
  const maxV = Math.max(...vs);
  const range = maxV - minV || 1;
  const maxT  = ts[ts.length - 1] || 1;

  const x = (t: number) => PAD + (t / maxT) * (W - PAD * 2);
  const y = (v: number) => PAD + (1 - (v - minV) / range) * (H - PAD * 2);

  const pts      = ts.map((t, i) => `${x(t).toFixed(1)},${y(vs[i]).toFixed(1)}`);
  const linePath = `M ${pts.join(' L ')}`;
  const areaPath = `M ${x(ts[0])},${H} L ${pts.join(' L ')} L ${x(maxT)},${H} Z`;
  const gradId   = `grad-${color.replace('#', '')}`;

  const fmt = (v: number) => decimals === 0 ? String(Math.round(v)) : v.toFixed(decimals);

  return (
    <>
      <Text style={[styles.chartLabel, { color }]}>{fmt(maxV)} {unit}</Text>
      <Svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}>
        <Defs>
          <LinearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <Stop offset="0" stopColor={color} stopOpacity="0.35" />
            <Stop offset="1" stopColor={color} stopOpacity="0.02" />
          </LinearGradient>
        </Defs>
        <Path d={areaPath} fill={`url(#${gradId})`} />
        <Path d={linePath} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
      </Svg>
      <Text style={[styles.chartLabel, { color: '#3f3f46', marginBottom: 10 }]}>{fmt(minV)} {unit}</Text>
    </>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

// Returns [west, south, east, north] per LngLatBounds spec
function geoJsonBounds(gj: object): [number, number, number, number] | null {
  const coords: [number, number][] = [];
  function collect(obj: unknown) {
    if (!obj || typeof obj !== 'object') return;
    const o = obj as Record<string, unknown>;
    if (o.type === 'Feature') { collect(o.geometry); return; }
    if (o.type === 'FeatureCollection') { (o.features as unknown[]).forEach(collect); return; }
    if (o.type === 'LineString') { coords.push(...(o.coordinates as [number, number][])); return; }
    if (o.type === 'MultiLineString') { (o.coordinates as [number, number][][]).forEach(c => coords.push(...c)); return; }
  }
  collect(gj);
  if (!coords.length) return null;
  const lons = coords.map(c => c[0]);
  const lats = coords.map(c => c[1]);
  return [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)];
}

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function StatCell({ label, value, unit }: { label: string; value: string; unit: string }) {
  return (
    <View style={styles.statCell}>
      <View style={styles.statValueRow}>
        <Text style={styles.statValue}>{value}</Text>
        {unit ? <Text style={styles.statUnit}>{unit}</Text> : null}
      </View>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metaRow}>
      <Text style={styles.metaLabel}>{label}</Text>
      <Text style={styles.metaValue}>{value}</Text>
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container:      { flex: 1, backgroundColor: '#09090b' },
  content:        { paddingBottom: 40 },
  center:         { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: '#09090b' },
  notFound:       { color: '#71717a', fontSize: 16 },
  topBar:         { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingTop: 60, paddingBottom: 12 },
  backButton:     { paddingHorizontal: 16 },
  backText:       { fontSize: 15 },
  deleteButton:   { paddingHorizontal: 16 },
  deleteText:     { color: '#f87171', fontSize: 15 },
  sport:          { color: '#71717a', fontSize: 12, fontWeight: '600', letterSpacing: 0.8, paddingHorizontal: 16, marginBottom: 4 },
  titleRow:       { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, marginBottom: 4 },
  title:          { color: '#f4f4f5', fontSize: 22, fontWeight: '700', flexShrink: 1 },
  titleInput:     { color: '#f4f4f5', fontSize: 22, fontWeight: '700', paddingHorizontal: 16, marginBottom: 4, borderBottomWidth: 1, borderBottomColor: '#3b82f6' },
  editHint:       { color: '#52525b', fontSize: 16, marginLeft: 8 },
  date:           { color: '#71717a', fontSize: 13, paddingHorizontal: 16, marginBottom: 16 },
  mapContainer:    { height: 220, marginBottom: 16, borderTopWidth: 1, borderBottomWidth: 1, borderColor: '#27272a' },
  map:             { flex: 1 },
  mapPlaceholder:  { height: 220, backgroundColor: '#18181b', alignItems: 'center', justifyContent: 'center', borderTopWidth: 1, borderBottomWidth: 1, borderColor: '#27272a', marginBottom: 16 },
  mapExpandHint:   { position: 'absolute', bottom: 8, right: 8, backgroundColor: 'rgba(0,0,0,0.55)', borderRadius: 6, paddingHorizontal: 8, paddingVertical: 4 },
  mapExpandText:   { color: '#a1a1aa', fontSize: 11 },
  fullscreenMap:   { flex: 1, backgroundColor: '#09090b' },
  closeButton:     { position: 'absolute', top: 56, right: 16, backgroundColor: 'rgba(0,0,0,0.6)', borderRadius: 20, width: 36, height: 36, alignItems: 'center', justifyContent: 'center' },
  closeText:       { color: '#fff', fontSize: 16 },
  zoomButtons:     { position: 'absolute', bottom: 40, right: 16, gap: 8 },
  zoomBtn:         { backgroundColor: 'rgba(0,0,0,0.65)', borderRadius: 20, width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  zoomBtnText:     { color: '#fff', fontSize: 22, fontWeight: '600', lineHeight: 28 },
  chartContainer:   { marginHorizontal: 16, marginBottom: 16, backgroundColor: '#18181b', borderRadius: 10, borderWidth: 1, borderColor: '#27272a', overflow: 'hidden' },
  chartPlaceholder: { height: 120, backgroundColor: '#18181b', alignItems: 'center', justifyContent: 'center', borderRadius: 10, borderWidth: 1, borderColor: '#27272a', marginHorizontal: 16, marginBottom: 16 },
  chartTabs:        { flexDirection: 'row', borderBottomWidth: 1, borderBottomColor: '#27272a' },
  chartTab:         { flex: 1, paddingVertical: 8, alignItems: 'center', borderBottomWidth: 2, borderBottomColor: 'transparent' },
  chartTabText:     { color: '#52525b', fontSize: 11, fontWeight: '600' },
  chartLabel:       { color: '#3f3f46', fontSize: 10, marginBottom: 2, marginHorizontal: 12, marginTop: 10 },
  grid:           { flexDirection: 'row', flexWrap: 'wrap', paddingHorizontal: 12, gap: 8, marginBottom: 16 },
  statCell:       { backgroundColor: '#18181b', borderRadius: 10, borderWidth: 1, borderColor: '#27272a', padding: 14, width: '47%' },
  statValueRow:   { flexDirection: 'row', alignItems: 'baseline', gap: 4, marginBottom: 4 },
  statValue:      { color: '#f4f4f5', fontSize: 24, fontWeight: '700' },
  statUnit:       { color: '#71717a', fontSize: 13 },
  statLabel:      { color: '#71717a', fontSize: 12 },
  meta:           { marginHorizontal: 16, backgroundColor: '#18181b', borderRadius: 10, borderWidth: 1, borderColor: '#27272a' },
  metaRow:        { flexDirection: 'row', justifyContent: 'space-between', paddingHorizontal: 14, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#27272a' },
  metaLabel:      { color: '#71717a', fontSize: 13 },
  metaValue:      { color: '#a1a1aa', fontSize: 13 },
});
