<script lang="ts">
  import * as Plot from '@observablehq/plot';
  import { onMount, onDestroy } from 'svelte';
  import type { Timeseries, AthleteZones } from '../lib/types';

  export let timeseries: Timeseries;
  // Linked hover: emit/receive index into timeseries arrays
  export let hoveredIdx: number | null = null;
  export let athlete: AthleteZones | null = null;

  const HR_ZONE_COLORS  = ['#60a5fa', '#4ade80', '#facc15', '#fb923c', '#f87171'];
  const PWR_ZONE_COLORS = ['#60a5fa', '#34d399', '#facc15', '#fb923c', '#f87171', '#c084fc', '#f43f5e'];

  type Tab = 'elevation' | 'speed' | 'hr' | 'cadence' | 'power';
  type XMode = 'time' | 'distance';
  type ChartType = 'line' | 'histogram';

  let activeTab: Tab = 'elevation';
  let xMode: XMode = 'time';
  let chartType: ChartType = 'line';
  let chartEl: HTMLDivElement;
  let chart: SVGElement | null = null;

  // Cumulative distance in km, integrated from speed_kmh.
  // Speeds > 150 km/h are treated as 0 (GPS glitch guard) — otherwise a single
  // 1-second spike at 220 km/h pushes all subsequent points ~60 m too far right
  // on the distance axis and stretches the chart out of proportion.
  $: dist_km = (() => {
    if (!timeseries.speed_kmh.some(v => v != null)) return null;
    const d: number[] = [0];
    for (let i = 1; i < timeseries.t.length; i++) {
      const v = timeseries.speed_kmh[i];
      const dt = timeseries.t[i] - timeseries.t[i - 1];
      const prev = d[i - 1];
      // Clamp to 150 km/h; treat null or out-of-range as 0 movement
      const vSafe = (v != null && v > 0 && v <= 150) ? v : 0;
      d.push(prev + vSafe * dt / 3600);
    }
    return d;
  })();

  // Pre-build data array once
  $: data = timeseries.t.map((t, i) => ({
    t,
    dist_km: dist_km ? dist_km[i] : null,
    elevation: timeseries.elevation_m[i],
    speed: timeseries.speed_kmh[i],
    hr: timeseries.hr_bpm[i],
    cadence: timeseries.cadence_rpm[i],
    power: timeseries.power_w[i],
  }));

  $: hasHR        = timeseries.hr_bpm.some(v => v != null);
  $: hasCadence   = timeseries.cadence_rpm.some(v => v != null);
  $: hasElevation = timeseries.elevation_m.some(v => v != null);
  $: hasSpeed     = timeseries.speed_kmh.some(v => v != null);
  $: hasPower     = timeseries.power_w.some(v => v != null);
  $: hasDistance  = dist_km !== null;

  const tabLabels: Record<Tab, string> = {
    elevation: 'Elevation',
    speed:     'Speed',
    hr:        'Heart Rate',
    cadence:   'Cadence',
    power:     'Power',
  };

  const tabMeta: Record<Tab, { color: string; yLabel: string; yKey: string }> = {
    elevation: { color: '#00c8ff', yLabel: 'Elevation (m)',    yKey: 'elevation' },
    speed:     { color: '#ff6b35', yLabel: 'Speed (km/h)',     yKey: 'speed'     },
    hr:        { color: '#f87171', yLabel: 'Heart Rate (bpm)', yKey: 'hr'        },
    cadence:   { color: '#a78bfa', yLabel: 'Cadence (rpm)',    yKey: 'cadence'   },
    power:     { color: '#facc15', yLabel: 'Power (W)',        yKey: 'power'     },
  };

  // ── Histogram controls ───────────────────────────────────────────────────
  let bins = 15;

  // Metric values for current tab (non-null)
  $: yKey = tabMeta[activeTab].yKey;
  $: metricValues = data
    .map(d => (d as any)[yKey] as number | null)
    .filter((v): v is number => v != null);
  $: dataMin = metricValues.length ? Math.min(...metricValues) : 0;
  $: dataMax = metricValues.length ? Math.max(...metricValues) : 100;

  // Explicit y domain for the line chart.
  // We compute this once from all data and pass it explicitly to Plot so that
  // switching x-axis mode (time ↔ distance) never changes the y range — Observable
  // Plot auto-infers different domains when the x-channel changes because it only
  // considers plottable points, but we want the scale to stay anchored to the
  // full dataset.  areaY extends down to 0, so include 0 in the minimum.
  $: lineDomainMin = Math.min(0, dataMin);
  $: lineDomainMax = dataMax;

  // Range handles — reset whenever the metric or chart type changes
  let trimMin = 0;
  let trimMax = 100;
  let lastResetTab: Tab | null = null;
  $: {
    // Reset trim on tab change OR when data range changes
    if (activeTab !== lastResetTab || trimMin < dataMin || trimMax > dataMax) {
      trimMin = dataMin;
      trimMax = dataMax;
      lastResetTab = activeTab;
    }
  }

  $: step = (dataMax - dataMin) / 200 || 1;

  // Percentage positions for the active-range highlight bar
  $: span = dataMax - dataMin || 1;
  $: leftPct  = ((trimMin - dataMin) / span) * 100;
  $: rightPct = ((dataMax - trimMax) / span) * 100;

  // Pre-filtered data + explicit evenly-spaced thresholds anchored to [trimMin, trimMax].
  // d3's count-based thresholds snap to "nice" values and produce the wrong bin count
  // when the range is narrow — explicit thresholds give exactly `bins` bins always.
  $: histData = data.filter(d => {
    const v = (d as any)[yKey];
    return v != null && v >= trimMin && v <= trimMax;
  });
  $: histThresholds = Array.from(
    { length: bins - 1 },
    (_, i) => trimMin + (i + 1) * (trimMax - trimMin) / bins,
  );

  // ── Zone alignment ───────────────────────────────────────────────────────
  let alignZones = false;
  $: canAlignZones = chartType === 'histogram' && !!(
    activeTab === 'hr'    ? athlete?.hr_zones?.length :
    activeTab === 'power' ? athlete?.power_zones?.length :
    false
  );
  // Reset when switching away from a zone-capable metric or leaving histogram
  $: if (!canAlignZones) alignZones = false;

  // ── Theme-aware colours ──────────────────────────────────────────────────
  function getThemeColors() {
    const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
    return {
      axis:       isDark ? '#71717a' : '#52525b',          // zinc-500 / zinc-600
      rule:       isDark ? 'rgba(255,255,255,0.25)' : 'rgba(0,0,0,0.2)',
      tooltipFg:  isDark ? '#ffffff' : '#18181b',
      tooltipBg:  isDark ? '#09090b' : '#ffffff',          // text outline backing
      ruleY:      isDark ? '#3f3f46' : '#d4d4d8',          // baseline rule
    };
  }

  // ── Rendering ────────────────────────────────────────────────────────────
  let themeObserver: MutationObserver | null = null;

  onMount(() => {
    renderChart();
    themeObserver = new MutationObserver(() => renderChart());
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  });
  onDestroy(() => { chart?.remove(); chart = null; themeObserver?.disconnect(); });

  $: if (chartEl) {
    activeTab; xMode; chartType; histData; histThresholds; alignZones;
    renderChart();
  }

  function renderChart() {
    if (!chartEl) return;
    chart?.remove();

    const w = chartEl.clientWidth || 800;
    const h = 220;
    const { color, yLabel, yKey } = tabMeta[activeTab];

    const tabEnabled =
      activeTab === 'elevation' ? hasElevation :
      activeTab === 'speed'     ? hasSpeed     :
      activeTab === 'hr'        ? hasHR        :
      activeTab === 'cadence'   ? hasCadence   :
      hasPower;
    if (!tabEnabled) return;

    chart = chartType === 'histogram'
      ? renderHistogram(w, h, yKey, yLabel, color)
      : renderLine(w, h, yKey, yLabel, color);

    if (chartType === 'line') {
      chart.addEventListener('input', () => {
        const pt = (chart as any)?.value;
        hoveredIdx = pt ? timeseries.t.findIndex(t => t === pt.t) : null;
      });
    }

    chartEl.appendChild(chart);
  }

  function renderLine(w: number, h: number, yKey: string, yLabel: string, color: string) {
    const x = xMode === 'distance' ? 'dist_km' : 't';
    const tc = getThemeColors();
    const marks: any[] = [];

    // monotone-x requires strictly increasing x. In time mode t is always
    // strictly increasing. In distance mode, stopped segments produce many
    // consecutive points with identical dist_km, which causes NaN Bézier
    // control points and visual artifacts — use linear instead.
    const curve = xMode === 'distance' ? 'linear' : 'monotone-x';

    if (activeTab === 'cadence') {
      marks.push(Plot.lineY(data, { x, y: yKey, stroke: color, strokeWidth: 1.5, curve }));
    } else {
      marks.push(
        Plot.areaY(data, { x, y: yKey, fill: color, fillOpacity: 0.15, curve }),
        Plot.lineY(data, { x, y: yKey, stroke: color, strokeWidth: 1.5, curve }),
      );
    }

    marks.push(
      Plot.ruleX(data, Plot.pointerX({ x, stroke: tc.rule, strokeWidth: 1, strokeDasharray: '4,4' })),
      Plot.dot(data,  Plot.pointerX({ x, y: yKey, r: 4, fill: color, stroke: tc.tooltipBg, strokeWidth: 1.5 })),
      Plot.text(data, Plot.pointerX({
        x, y: yKey,
        text: (d: any) => d[yKey] != null ? `${Math.round(d[yKey])}` : '',
        dy: -12,
        fill: tc.tooltipFg, stroke: tc.tooltipBg, strokeWidth: 3,
        fontSize: 11, fontWeight: '600',
      })),
    );

    const xTickFormat = xMode === 'distance'
      ? (v: number) => `${v.toFixed(1)} km`
      : (t: number) => {
          const h = Math.floor(t / 3600);
          const m = Math.floor((t % 3600) / 60);
          return h > 0 ? `${h}h${m.toString().padStart(2, '0')}` : `${m}m`;
        };

    return Plot.plot({
      width: w, height: h, marginLeft: 48, marginBottom: 32,
      style: { background: 'transparent', color: tc.axis, fontSize: '11px' },
      x: { label: null, tickFormat: xTickFormat, grid: false, ticks: 6 },
      y: { label: yLabel, grid: true, tickCount: 4, domain: [lineDomainMin, lineDomainMax] },
      marks,
    });
  }

  function renderHistogram(w: number, h: number, yKey: string, yLabel: string, color: string) {
    const yTickFormat = (v: number) => v >= 60 ? `${Math.round(v / 60)}m` : `${v}s`;
    const rawZones = activeTab === 'hr' ? athlete?.hr_zones : activeTab === 'power' ? athlete?.power_zones : null;
    const zoneColors = activeTab === 'hr' ? HR_ZONE_COLORS : PWR_ZONE_COLORS;
    const tc = getThemeColors();

    // ── Zone-aligned: one colored bar per zone ──────────────────────────────
    if (alignZones && rawZones?.length) {
      // Cap the top zone's hi at the actual data max so sentinel values like
      // 999 bpm or 9999 W don't stretch the x-axis into empty space.
      const dataMax = Math.max(...data.map((d: any) => d[yKey]).filter((v: any) => v != null));
      const clampedZones = rawZones.map((z, i) =>
        i === rawZones.length - 1 ? [z[0], Math.min(z[1], dataMax * 1.05)] : z
      );

      const zoneBars = clampedZones.map((z, i) => ({
        lo: z[0], hi: z[1],
        // Count directly from full data — trim sliders don't apply in zone mode
        count: data.filter((d: any) => { const v = d[yKey]; return v != null && v >= rawZones[i][0] && v < rawZones[i][1]; }).length,
        color: zoneColors[i] ?? zoneColors[zoneColors.length - 1],
        label: `Z${i + 1}`,
      }));

      return Plot.plot({
        width: w, height: h, marginLeft: 48, marginBottom: 32,
        style: { background: 'transparent', color: tc.axis, fontSize: '11px' },
        x: { label: yLabel, grid: false, domain: [clampedZones[0][0], clampedZones[clampedZones.length - 1][1]] },
        y: { label: 'Time', grid: true, tickCount: 4, tickFormat: yTickFormat },
        marks: [
          Plot.rect(zoneBars, {
            x1: 'lo', x2: 'hi', y1: 0, y2: 'count',
            fill: 'color', fillOpacity: 0.75,
          }),
          Plot.text(zoneBars, {
            x: (d: any) => (d.lo + d.hi) / 2,
            y: 'count',
            text: 'label',
            fill: 'color',
            fontSize: 10, fontWeight: '600',
            dy: -8,
          }),
          Plot.ruleY([0], { stroke: tc.ruleY }),
        ],
      });
    }

    // ── Normal histogram with optional zone overlays ─────────────────────────
    const marks: any[] = [
      Plot.rectY(histData, Plot.binX(
        { y: 'count' },
        { x: yKey, fill: color, fillOpacity: 0.7, thresholds: histThresholds },
      )),
      Plot.ruleY([0], { stroke: tc.ruleY }),
    ];

    if (rawZones?.length) {
      const boundaries = rawZones.slice(0, -1).map((z, i) => ({
        x: z[1],
        color: zoneColors[i + 1] ?? zoneColors[zoneColors.length - 1],
      })).filter(b => b.x > trimMin && b.x < trimMax);

      const labels = rawZones.map((z, i) => ({
        mid: (Math.max(z[0], trimMin) + Math.min(z[1], trimMax)) / 2,
        label: `Z${i + 1}`,
        color: zoneColors[i] ?? zoneColors[zoneColors.length - 1],
        visible: z[1] > trimMin && z[0] < trimMax,
      })).filter(l => l.visible && l.mid >= trimMin && l.mid <= trimMax);

      marks.push(
        Plot.ruleX(boundaries, {
          x: 'x',
          stroke: (d: any) => d.color,
          strokeWidth: 1, strokeOpacity: 0.5, strokeDasharray: '4,3',
        }),
        Plot.text(labels, {
          x: 'mid', text: 'label', fill: (d: any) => d.color,
          fontSize: 9, fontWeight: '600', frameAnchor: 'top', dy: 6,
        }),
      );
    }

    return Plot.plot({
      width: w, height: h, marginLeft: 48, marginBottom: 32,
      style: { background: 'transparent', color: tc.axis, fontSize: '11px' },
      x: { label: yLabel, grid: false, ticks: 6, domain: [trimMin, trimMax] },
      y: { label: 'Time', grid: true, tickCount: 4, tickFormat: yTickFormat },
      marks,
    });
  }
</script>

<!-- Metric tabs + chart controls -->
<div class="flex items-center gap-1 mb-3 flex-wrap">
  {#each Object.entries(tabLabels) as [tab, label]}
    {@const enabled =
      tab === 'elevation' ? hasElevation :
      tab === 'speed'     ? hasSpeed     :
      tab === 'hr'        ? hasHR        :
      tab === 'cadence'   ? hasCadence   :
      hasPower}
    <button
      class="px-3 py-1.5 rounded-md text-sm transition-colors"
      class:opacity-30={!enabled}
      class:cursor-not-allowed={!enabled}
      class:bg-zinc-800={activeTab === tab}
      class:text-white={activeTab === tab}
      class:text-zinc-500={activeTab !== tab}
      class:hover:text-zinc-300={activeTab !== tab && enabled}
      disabled={!enabled}
      on:click={() => { if (enabled) activeTab = tab as Tab; }}
    >
      {label}
    </button>
  {/each}

  <div class="flex-1"></div>

  <div class="flex items-center gap-3 text-xs text-zinc-500">
    {#if hasDistance && chartType === 'line'}
      <div class="flex items-center gap-1">
        <span class="mr-0.5">X:</span>
        {#each (['time', 'distance'] as XMode[]) as mode}
          <button
            class="px-2 py-1 rounded transition-colors"
            class:bg-zinc-800={xMode === mode}
            class:text-white={xMode === mode}
            class:hover:text-zinc-300={xMode !== mode}
            on:click={() => xMode = mode}
          >{mode === 'time' ? 'Time' : 'Dist'}</button>
        {/each}
      </div>
    {/if}

    <div class="flex items-center gap-1">
      {#each (['line', 'histogram'] as ChartType[]) as type}
        <button
          class="px-2 py-1 rounded transition-colors"
          class:bg-zinc-800={chartType === type}
          class:text-white={chartType === type}
          class:hover:text-zinc-300={chartType !== type}
          on:click={() => chartType = type}
          title={type === 'line' ? 'Time series' : 'Distribution'}
        >{type === 'line' ? '↗ Line' : '▭ Hist'}</button>
      {/each}
    </div>
  </div>
</div>

<div bind:this={chartEl} class="w-full overflow-hidden" style="min-height:220px"></div>

<!-- Histogram controls (range + bins) -->
{#if chartType === 'histogram'}
  <div class="mt-3 flex flex-col gap-2 text-xs text-zinc-400">

    <!-- Bins mode toggle — only shown when zones are available -->
    {#if canAlignZones}
      <div class="flex items-center gap-1">
        <span class="text-zinc-500 mr-1">Bins:</span>
        {#each ([false, true] as boolean[]) as zoneMode}
          <button
            class="px-2 py-1 rounded transition-colors"
            class:bg-zinc-800={alignZones === zoneMode}
            class:text-white={alignZones === zoneMode}
            class:text-zinc-500={alignZones !== zoneMode}
            class:hover:text-zinc-300={alignZones !== zoneMode}
            on:click={() => alignZones = zoneMode}
          >{zoneMode ? 'Zones' : 'Custom'}</button>
        {/each}
      </div>
    {/if}

    {#if !alignZones}
    <!-- Dual range slider -->
    <div class="flex items-center gap-3">
      <span class="w-8 text-right shrink-0">{Math.round(trimMin)}</span>
      <div class="relative flex-1" style="height:20px">
        <!-- Background track -->
        <div class="absolute left-0 right-0 top-1/2 -translate-y-1/2 h-1 bg-zinc-700 rounded-full pointer-events-none"></div>
        <!-- Active range fill -->
        <div
          class="absolute top-1/2 -translate-y-1/2 h-1 bg-zinc-400 rounded-full pointer-events-none"
          style="left:{leftPct}%; right:{rightPct}%"
        ></div>
        <!-- Min handle -->
        <input
          type="range"
          min={dataMin} max={dataMax} {step}
          value={trimMin}
          on:input={(e) => { const v = +e.currentTarget.value; trimMin = v < trimMax - step ? v : trimMax - step; }}
          class="range-thumb"
        />
        <!-- Max handle -->
        <input
          type="range"
          min={dataMin} max={dataMax} {step}
          value={trimMax}
          on:input={(e) => { const v = +e.currentTarget.value; trimMax = v > trimMin + step ? v : trimMin + step; }}
          class="range-thumb"
        />
      </div>
      <span class="w-8 shrink-0">{Math.round(trimMax)}</span>
    </div>

    <!-- Bins slider -->
    <div class="flex items-center gap-3">
      <span class="w-8 text-right shrink-0 text-zinc-500">Bins</span>
      <input
        type="range" min="5" max="20" step="1"
        bind:value={bins}
        class="flex-1 h-1 accent-zinc-400 cursor-pointer"
      />
      <span class="w-8 shrink-0">{bins}</span>
    </div>
    {/if}

  </div>
{/if}

<style>
  .range-thumb {
    position: absolute;
    left: 0;
    top: 0;
    width: 100%;
    height: 100%;
    margin: 0;
    padding: 0;
    appearance: none;
    -webkit-appearance: none;
    background: transparent;
    pointer-events: none;
  }
  .range-thumb::-webkit-slider-runnable-track {
    background: transparent;
    height: 4px;
  }
  .range-thumb::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: #e4e4e7;
    border: 2px solid #52525b;
    cursor: pointer;
    pointer-events: all;
    margin-top: -5px; /* center on 4px track */
  }
  .range-thumb::-moz-range-track {
    background: transparent;
    height: 4px;
  }
  .range-thumb::-moz-range-thumb {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: #e4e4e7;
    border: 2px solid #52525b;
    cursor: pointer;
    pointer-events: all;
  }
</style>
