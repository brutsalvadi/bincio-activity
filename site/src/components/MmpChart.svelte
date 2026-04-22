<script lang="ts">
  import { onMount } from 'svelte';
  import * as Plot from '@observablehq/plot';
  import type { AthleteJson, MmpCurve, ActivitySummary } from '../lib/types';

  export let athlete: AthleteJson;
  export let activities: ActivitySummary[] = [];

  // ── Range selection ────────────────────────────────────────────────────────
  type RangeKey = 'all_time' | 'last_365d' | 'last_90d' | string;

  interface Season { name: string; start: string; end: string }
  const seasons: Season[] = (athlete as any).seasons ?? [];

  let selectedRanges: Set<RangeKey> = new Set(['all_time']);

  const PRESET_LABELS: Record<string, string> = {
    all_time:   'All time',
    last_365d:  'Last 365 d',
    last_90d:   'Last 90 d',
  };

  // Colours for overlaid curves (cycling through a palette)
  const PALETTE = [
    '#60a5fa', // blue-400
    '#f97316', // orange-500
    '#34d399', // emerald-400
    '#a78bfa', // violet-400
    '#f43f5e', // rose-500
    '#facc15', // yellow-400
    '#22d3ee', // cyan-400
  ];

  function curveColor(key: RangeKey, index: number): string {
    return PALETTE[index % PALETTE.length];
  }

  // ── MMP curve computation ──────────────────────────────────────────────────

  function mergeMmps(mmps: MmpCurve[]): MmpCurve {
    const best = new Map<number, number>();
    for (const curve of mmps) {
      for (const [d, w] of curve) {
        const prev = best.get(d);
        if (prev === undefined || w > prev) best.set(d, w);
      }
    }
    return [...best.entries()].sort((a, b) => a[0] - b[0]) as MmpCurve;
  }

  function mmpsForRange(key: RangeKey): MmpCurve | null {
    // Built-in preset ranges come from athlete.json (pre-computed at extract time)
    if (key in PRESET_LABELS) {
      return (athlete.power_curve as any)[key] ?? null;
    }
    // User-defined seasons: compute on-the-fly from per-activity mmp in index.json
    const season = seasons.find(s => s.name === key);
    if (!season) return null;
    const curves = activities
      .filter(a => a.mmp && a.started_at >= season.start && a.started_at <= season.end + 'T23:59:59')
      .map(a => a.mmp!);
    return curves.length ? mergeMmps(curves) : null;
  }

  // ── Chart rendering ────────────────────────────────────────────────────────

  let chartEl: HTMLElement;

  function formatDuration(s: number): string {
    if (s < 60)   return `${s}s`;
    if (s < 3600) return `${Math.round(s / 60)}min`;
    return `${s / 3600}h`;
  }

  $: selectedKeys = [...selectedRanges];

  $: plotData = selectedKeys.flatMap((key, i) => {
    const curve = mmpsForRange(key);
    if (!curve) return [];
    return curve.map(([d, w]) => ({ d, w, label: key }));
  });

  $: colorMap = Object.fromEntries(selectedKeys.map((k, i) => [k, curveColor(k, i)]));

  function getAxisColor() {
    return document.documentElement.getAttribute('data-theme') === 'light' ? '#52525b' : '#a1a1aa';
  }

  function renderChart(data: typeof plotData, cmap: typeof colorMap) {
    if (!chartEl) return;
    chartEl.innerHTML = '';
    if (!data.length) return;

    const labelFn = (key: string) =>
      PRESET_LABELS[key] ?? key;

    const chart = Plot.plot({
      width: chartEl.clientWidth || 700,
      height: 320,
      marginLeft: 52,
      marginBottom: 40,
      style: { background: 'transparent', color: getAxisColor() },
      x: {
        type: 'log',
        label: 'Duration',
        tickFormat: (d: number) => formatDuration(d),
        grid: true,
        domain: [data[0]?.d ?? 1, Math.max(7200, ...data.map(d => d.d))],
      },
      y: {
        label: 'Avg power (W)',
        grid: true,
        zero: true,
      },
      color: {
        domain: selectedKeys,
        range: selectedKeys.map((k, i) => curveColor(k, i)),
        legend: selectedKeys.length > 1,
      },
      marks: [
        Plot.line(data, {
          x: 'd',
          y: 'w',
          stroke: 'label',
          strokeWidth: 2,
          curve: 'monotone-x',
        }),
        Plot.dot(data, {
          x: 'd',
          y: 'w',
          fill: 'label',
          r: 3,
          tip: true,
          title: (d: any) => `${labelFn(d.label)}\n${formatDuration(d.d)}: ${d.w} W`,
        }),
        ...(athlete.ftp_w ? [
          Plot.ruleY([athlete.ftp_w], {
            stroke: '#71717a',
            strokeDasharray: '4 3',
          }),
          Plot.text([{ x: 3600, y: athlete.ftp_w }], {
            x: 'x', y: 'y',
            text: () => `FTP ${athlete.ftp_w} W`,
            fill: '#71717a',
            fontSize: 11,
            dy: -6,
            textAnchor: 'end',
          }),
        ] : []),
      ],
    });

    chartEl.appendChild(chart);
  }

  $: renderChart(plotData, colorMap);

  // Re-render on resize — use indirect call so we always get current reactive values
  let currentPlotData = plotData;
  let currentColorMap = colorMap;
  $: currentPlotData = plotData;
  $: currentColorMap = colorMap;

  onMount(() => {
    const ro = new ResizeObserver(() => renderChart(currentPlotData, currentColorMap));
    ro.observe(chartEl);
    const mo = new MutationObserver(() => renderChart(currentPlotData, currentColorMap));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => { ro.disconnect(); mo.disconnect(); };
  });

  // ── Toggle helpers ─────────────────────────────────────────────────────────

  function toggleRange(key: RangeKey) {
    const next = new Set(selectedRanges);
    if (next.has(key)) {
      if (next.size > 1) next.delete(key); // always keep at least one
    } else {
      next.add(key);
    }
    selectedRanges = next;
  }

  const allRangeKeys = [
    ...Object.keys(PRESET_LABELS),
    ...seasons.map(s => s.name),
  ];
</script>

<style>
  /* Plot tooltips always have a white background — force black text for contrast */
  :global(.plot-tip text) { fill: #18181b !important; }
</style>

<!-- Range selector pills -->
<div class="flex flex-wrap gap-2 mb-4">
  {#each allRangeKeys as key, i}
    {@const active = selectedRanges.has(key)}
    {@const color = curveColor(key, i)}
    <button
      on:click={() => toggleRange(key)}
      class="px-3 py-1 rounded-full text-sm font-medium border transition-colors"
      style={active
        ? `background:${color}22; border-color:${color}; color:${color}`
        : 'background:transparent; border-color:#3f3f46; color:#71717a'}
    >
      {PRESET_LABELS[key] ?? key}
    </button>
  {/each}
</div>

<!-- Chart -->
<div bind:this={chartEl} class="w-full min-h-[320px]"></div>

{#if !plotData.length}
  <p class="text-zinc-500 text-sm mt-4">No power data for the selected range.</p>
{/if}
