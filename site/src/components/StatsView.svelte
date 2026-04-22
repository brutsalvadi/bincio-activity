<script lang="ts">
  import { onMount } from 'svelte';
  import type { ActivitySummary, BASIndex, Sport } from '../lib/types';
  import { formatDistance, formatDuration, isUnlisted, sportIcon, sportColor, sportLabel } from '../lib/format';
  import { loadIndex } from '../lib/dataloader';

  /** Explicit index URL — use for per-user stats pages in multi-user mode. */
  export let indexUrl: string = '';

  const PAGE_YEARS = 4;

  let all: ActivitySummary[] = [];
  let sport: Sport | 'all' = 'all';
  let page = 0;
  let loading = true;
  let error = '';
  let theme = 'dark';
  let mounted = false;

  $: totalPages = Math.ceil(allYears.length / PAGE_YEARS);
  $: years = allYears.slice(page * PAGE_YEARS, (page + 1) * PAGE_YEARS);

  $: if (mounted) {
    const params = new URLSearchParams(window.location.search);
    if (sport === 'all') params.delete('sport'); else params.set('sport', sport);
    if (page === 0) params.delete('page'); else params.set('page', String(page));
    const qs = params.toString();
    history.replaceState(null, '', qs ? `?${qs}` : window.location.pathname);
  }

  onMount(async () => {
    const params = new URLSearchParams(window.location.search);
    sport = (params.get('sport') as Sport | 'all') ?? 'all';
    page  = parseInt(params.get('page') ?? '0', 10) || 0;
    mounted = true;
    try {
      const index = await loadIndex(import.meta.env.BASE_URL, indexUrl || undefined);
      all = index.activities.filter(a => !isUnlisted(a.privacy) && a.distance_m);
    } catch (e: any) {
      error = e.message;
    }
    loading = false;

    theme = document.documentElement.getAttribute('data-theme') ?? 'dark';
    const obs = new MutationObserver(() => {
      theme = document.documentElement.getAttribute('data-theme') ?? 'dark';
    });
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => obs.disconnect();
  });

  $: activities = sport === 'all' ? all : all.filter(a => a.sport === sport);

  // ── Tooltip ───────────────────────────────────────────────────────────────
  $: activitiesByDate = (() => {
    const m = new Map<string, ActivitySummary[]>();
    for (const a of activities) {
      const d = a.started_at.slice(0, 10);
      if (!m.has(d)) m.set(d, []);
      m.get(d)!.push(a);
    }
    return m;
  })();

  let hoveredDate: string | null = null;
  let pinnedDate: string | null = null;
  let tooltipEl: HTMLElement | null = null;
  let tooltipPos = { x: 0, y: 0 };
  let hideTimer: ReturnType<typeof setTimeout> | null = null;

  $: tooltipDate = pinnedDate ?? hoveredDate;
  $: tooltipActivities = tooltipDate ? (activitiesByDate.get(tooltipDate) ?? []) : [];

  function updatePos(e: MouseEvent) {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const tw = 280; // matches w-[280px]
    const th = 260; // approximate tooltip height
    const gap = 14;
    let x = e.clientX + gap;
    if (x + tw > vw) x = e.clientX - gap - tw;
    x = Math.max(4, Math.min(x, vw - tw - 4));
    const y = Math.max(4, Math.min(e.clientY - 8, vh - th - 4));
    tooltipPos = { x, y };
  }

  function onCellEnter(date: string, e: MouseEvent) {
    if (!date || !activitiesByDate.has(date)) return;
    if (pinnedDate) return;
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    hoveredDate = date;
    updatePos(e);
  }

  function onCellLeave() {
    if (pinnedDate) return;
    hideTimer = setTimeout(() => { hoveredDate = null; }, 120);
  }

  function onCellClick(date: string, e: MouseEvent) {
    if (!date || !activitiesByDate.has(date)) return;
    e.stopPropagation();
    if (pinnedDate === date) {
      pinnedDate = null;
    } else {
      pinnedDate = date;
      updatePos(e);
    }
  }

  function onTooltipEnter() {
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
  }

  function onTooltipLeave() {
    if (pinnedDate) return;
    hoveredDate = null;
  }

  function onWindowClick(e: MouseEvent) {
    if (!pinnedDate) return;
    if (tooltipEl && tooltipEl.contains(e.target as Node)) return;
    pinnedDate = null;
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') pinnedDate = null;
  }

  // ── Heatmap data ─────────────────────────────────────────────────────────
  // byDateBySport: date → sport → total distance (m)
  $: byDateBySport = (() => {
    const m = new Map<string, Map<string, number>>();
    for (const a of activities) {
      const d = a.started_at.slice(0, 10);
      if (!m.has(d)) m.set(d, new Map());
      const sm = m.get(d)!;
      sm.set(a.sport, (sm.get(a.sport) ?? 0) + (a.distance_m ?? 0));
    }
    return m;
  })();

  $: byDate = new Map(
    [...byDateBySport.entries()].map(([d, sm]) => [
      d,
      [...sm.values()].reduce((s, v) => s + v, 0),
    ])
  );

  // Sorted daily distances for percentile-based intensity scaling
  $: sortedDaily = [...byDate.values()].sort((a, b) => a - b);
  $: maxDailyKm = (sortedDaily[sortedDaily.length - 1] ?? 0) / 1000 || 1;

  function pctRank(value: number, sorted: number[]): number {
    if (!sorted.length) return 0;
    let lo = 0, hi = sorted.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (sorted[mid] <= value) lo = mid + 1; else hi = mid; }
    return lo / sorted.length;
  }

  // ── Totals ────────────────────────────────────────────────────────────────
  $: totalsByYear = (() => {
    const m = new Map<number, { dist: number; count: number }>();
    for (const a of activities) {
      const y = new Date(a.started_at).getFullYear();
      const cur = m.get(y) ?? { dist: 0, count: 0 };
      cur.dist += a.distance_m ?? 0;
      cur.count += 1;
      m.set(y, cur);
    }
    return m;
  })();

  $: allYears = [...totalsByYear.keys()].sort((a, b) => b - a);

  // ── Color helpers ─────────────────────────────────────────────────────────
  function hexToRgb(hex: string): [number, number, number] {
    return [
      parseInt(hex.slice(1, 3), 16),
      parseInt(hex.slice(3, 5), 16),
      parseInt(hex.slice(5, 7), 16),
    ];
  }

  // Base cell color: zinc-800 dark (#27272a=39,39,42) or zinc-200 light (#e4e4e7=228,228,231)
  $: emptyColor  = theme === 'light' ? '#e4e4e7' : '#27272a';
  $: baseRgb     = theme === 'light'
    ? [228, 228, 231] as [number, number, number]
    : [39,  39,  42]  as [number, number, number];

  // Lerp from base bg color toward target sport color
  function applyIntensity(hex: string, intensity: number, base: [number, number, number]): string {
    const [tr, tg, tb] = hexToRgb(hex);
    const [br, bg, bb] = base;
    return `rgb(${Math.round(br + (tr - br) * intensity)},${Math.round(bg + (tg - bg) * intensity)},${Math.round(bb + (tb - bb) * intensity)})`;
  }

  // Precompute date→color as a reactive Map so Svelte tracks it directly in
  // the template. (Calling a plain function with a static string arg won't
  // re-trigger when byDate/maxDailyKm change — the Map reference does.)
  $: cellColors = (() => {
    const base = baseRgb;
    const empty = emptyColor;
    const m = new Map<string, string>();
    for (const [date, sportMap] of byDateBySport) {
      const total = byDate.get(date) ?? 0;
      if (total === 0) { m.set(date, empty); continue; }
      const intensity = 0.12 + pctRank(total, sortedDaily) * 0.88;
      let tr = 0, tg = 0, tb = 0;
      for (const [sp, dist] of sportMap) {
        const w = dist / total;
        const [cr, cg, cb] = hexToRgb(sportColor(sp as Sport));
        tr += cr * w; tg += cg * w; tb += cb * w;
      }
      const blended = `#${Math.round(tr).toString(16).padStart(2,'0')}${Math.round(tg).toString(16).padStart(2,'0')}${Math.round(tb).toString(16).padStart(2,'0')}`;
      m.set(date, applyIntensity(blended, intensity, base));
    }
    return m;
  })();

  // Legend: 6 swatches from base bg to full sport color (or neutral for 'all')
  $: legendColor = sport !== 'all' ? sportColor(sport) : (theme === 'light' ? '#0284c7' : '#00c8ff');
  $: legendSwatches = [0, 0.18, 0.38, 0.58, 0.78, 1.0].map(t =>
    t === 0 ? emptyColor : applyIntensity(legendColor, t, baseRgb)
  );

  // Sport chips present in filtered data (for 'all' color key)
  $: sportsInData = sport === 'all'
    ? ([...new Set(activities.map(a => a.sport))] as Sport[]).sort()
    : ([] as Sport[]);

  // ── Calendar helpers ──────────────────────────────────────────────────────

  function localISO(d: Date): string {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }

  function getWeeks(year: number): string[][] {
    const jan1 = new Date(year, 0, 1);
    const dec31 = new Date(year, 11, 31);
    const start = new Date(jan1);
    start.setDate(jan1.getDate() - ((jan1.getDay() + 6) % 7));
    const end = new Date(dec31);
    end.setDate(dec31.getDate() + (6 - (dec31.getDay() + 6) % 7));
    const weeks: string[][] = [];
    let cur = new Date(start);
    while (cur <= end) {
      const week: string[] = [];
      for (let d = 0; d < 7; d++) {
        week.push(cur.getFullYear() === year ? localISO(cur) : '');
        cur.setDate(cur.getDate() + 1);
      }
      weeks.push(week);
    }
    return weeks;
  }

  const DOW = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  const sports: Array<{ value: Sport | 'all'; label: string }> = [
    { value: 'all',      label: 'All' },
    { value: 'cycling',  label: '🚴 Cycling' },
    { value: 'running',  label: '🏃 Running' },
    { value: 'hiking',   label: '🥾 Hiking' },
    { value: 'walking',  label: '🚶 Walking' },
    { value: 'swimming', label: '🏊 Swimming' },
    { value: 'skiing',   label: '⛷️ Skiing' },
    { value: 'other',    label: '⚡ Other' },
  ];
</script>

<!-- Filter bar -->
<div class="flex gap-2 mb-6 flex-wrap">
  {#each sports as s}
    <button
      class="px-3 py-1 rounded-full text-sm font-medium border transition-colors"
      class:border-zinc-700={sport !== s.value}
      class:text-zinc-400={sport !== s.value}
      class:border-[--accent]={sport === s.value}
      class:text-white={sport === s.value}
      style={sport === s.value ? 'background:var(--accent-dim)' : ''}
      on:click={() => sport = s.value}
    >
      {s.label}
    </button>
  {/each}
  {#if all.length > 0}
    <span class="ml-auto text-sm text-zinc-500 self-center">
      {activities.length} {activities.length === 1 ? 'activity' : 'activities'}
    </span>
  {/if}
</div>

{#if loading}
  <div class="h-64 rounded-xl bg-zinc-800 animate-pulse mb-6"></div>
{:else if error}
  <p class="text-red-400 text-sm mt-4">{error}</p>
{:else}

<!-- Pagination controls -->
{#if totalPages > 1}
  <div class="flex items-center justify-between mb-6">
    <button
      class="px-3 py-1.5 rounded-lg border border-zinc-700 text-sm text-zinc-400 hover:border-zinc-500 hover:text-white transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
      disabled={page === 0}
      on:click={() => page -= 1}
    >← Newer</button>
    <span class="text-sm text-zinc-500">{years[years.length - 1]} – {years[0]}</span>
    <button
      class="px-3 py-1.5 rounded-lg border border-zinc-700 text-sm text-zinc-400 hover:border-zinc-500 hover:text-white transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
      disabled={page >= totalPages - 1}
      on:click={() => page += 1}
    >Older →</button>
  </div>
{/if}

<!-- Year totals -->
<div class="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
  {#each years as year}
    {@const t = totalsByYear.get(year)}
    <div class="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
      <p class="text-xs text-zinc-500 mb-1">{year}</p>
      <p class="text-2xl font-bold text-white">{formatDistance(t?.dist ?? 0)}</p>
      <p class="text-sm text-zinc-400">{t?.count ?? 0} activities</p>
    </div>
  {/each}
</div>

<!-- Heatmaps per year -->
{#each years as year}
  {@const weeks = getWeeks(year)}
  {@const yt = totalsByYear.get(year)}
  {#if yt}
    <div class="mb-8" role="presentation">
      <div class="flex items-baseline gap-3 mb-2">
        <h2 class="text-lg font-semibold text-white">{year}</h2>
        <span class="text-sm text-zinc-400">
          {formatDistance(yt.dist)} · {yt.count} activities
        </span>
      </div>

      <div class="overflow-x-auto">
        <div class="inline-flex gap-[3px]">
          <!-- Day-of-week labels: blank slot at top to align with month row -->
          <div class="flex flex-col gap-[3px] mr-1">
            <div class="h-4"></div>
            {#each DOW as d, i}
              <span class="text-[9px] text-zinc-600 h-[10px] leading-[10px] w-3 text-right">
                {i % 2 === 1 ? d : ''}
              </span>
            {/each}
          </div>
          <!-- Week columns: month label at top, day cells below -->
          {#each weeks as week, wi}
            {@const firstDay = week.find(d => d)}
            {@const prevFirstDay = wi > 0 ? weeks[wi - 1].find(d => d) : null}
            {@const showMonth = firstDay && (!prevFirstDay || prevFirstDay.slice(5, 7) !== firstDay.slice(5, 7))}
            <div class="flex flex-col gap-[3px]">
              <div class="h-4 relative">
                {#if showMonth}
                  <span class="text-[10px] text-zinc-500 absolute left-0 top-0 whitespace-nowrap">
                    {MONTHS[parseInt(firstDay.slice(5, 7)) - 1]}
                  </span>
                {/if}
              </div>
              {#each week as date}
                <div
                  role="button"
                  tabindex="0"
                  class="w-[10px] h-[10px] rounded-[2px] {date && activitiesByDate.has(date) ? 'cursor-pointer' : ''} {date && date === pinnedDate ? 'ring-1 ring-white ring-offset-[1px]' : ''}"
                  style="background:{cellColors.get(date) ?? emptyColor}; --tw-ring-offset-color: var(--bg-base)"
                  on:mouseenter={e => onCellEnter(date, e)}
                  on:mouseleave={onCellLeave}
                  on:click={e => onCellClick(date, e)}
                  on:keydown={e => e.key === 'Enter' && onCellClick(date, e)}
                ></div>
              {/each}
            </div>
          {/each}
        </div>
      </div>

      <!-- Legend -->
      <div class="flex items-center gap-3 mt-2 flex-wrap">
        <div class="flex items-center gap-1">
          <span class="text-xs text-zinc-500 mr-1">Less</span>
          {#each legendSwatches as c}
            <div class="w-[10px] h-[10px] rounded-[2px]" style="background:{c}"></div>
          {/each}
          <span class="text-xs text-zinc-500 ml-1">More (percentile · max {Math.round(maxDailyKm)} km)</span>
        </div>
        {#if sportsInData.length > 1}
          <div class="flex items-center gap-2 ml-2">
            {#each sportsInData as sp}
              <span class="text-xs flex items-center gap-1" style="color:{sportColor(sp)}">
                <span class="w-[10px] h-[10px] rounded-[2px] inline-block" style="background:{sportColor(sp)}"></span>
                {sportIcon(sp)}
              </span>
            {/each}
          </div>
        {/if}
      </div>
    </div>
  {/if}
{/each}

{/if}

<svelte:window on:click={onWindowClick} on:keydown={onKeydown} />

<!-- Day tooltip -->
{#if tooltipDate && tooltipActivities.length > 0}
  <div
    bind:this={tooltipEl}
    role="tooltip"
    class="fixed z-50 bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl p-3 w-[280px]"
    style="left:{tooltipPos.x}px; top:{tooltipPos.y}px"
    on:mouseenter={onTooltipEnter}
    on:mouseleave={onTooltipLeave}
  >
    <div class="flex items-center justify-between mb-2">
      <p class="text-xs font-medium text-zinc-400">
        {new Date(tooltipDate + 'T12:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })}
      </p>
      {#if pinnedDate}
        <button
          class="text-zinc-500 hover:text-zinc-300 transition-colors text-sm leading-none ml-2"
          on:click|stopPropagation={() => pinnedDate = null}
          aria-label="Close"
        >✕</button>
      {/if}
    </div>
    <div class="flex flex-col gap-1">
      {#each tooltipActivities as a}
        <a
          href={a.detail_url ? `${import.meta.env.BASE_URL}activity/${a.id}/` : `${import.meta.env.BASE_URL}activity/local/?id=${a.id}`}
          class="flex flex-col gap-0.5 rounded-lg px-2 py-1.5 hover:bg-zinc-800 transition-colors"
        >
          <span class="text-sm font-medium text-white truncate">
            {sportIcon(a.sport)} {a.title}
          </span>
          <span class="text-xs text-zinc-400">
            {formatDistance(a.distance_m)}
            {#if a.moving_time_s ?? a.duration_s}
              · {formatDuration(a.moving_time_s ?? a.duration_s)}
            {/if}
            <span class="ml-1" style="color:{sportColor(a.sport)}">{sportLabel(a.sport, a.sub_sport)}</span>
          </span>
        </a>
      {/each}
    </div>
  </div>
{/if}
