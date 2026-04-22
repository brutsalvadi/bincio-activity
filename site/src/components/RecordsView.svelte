<script lang="ts">
  import { onMount } from 'svelte';
  import type { AthleteJson } from '../lib/types';
  import { formatDate, sportIcon, sportColor } from '../lib/format';

  export let athlete: AthleteJson;
  export let base: string = '/';

  // ── Distance label formatting ──────────────────────────────────────────────
  function distLabel(km: number): string {
    if (km === 0.4)   return '400 m';
    if (km === 0.1)   return '100 m';
    if (km === 0.2)   return '200 m';
    if (km === 0.5)   return '500 m';
    if (km === 1.609) return '1 mile';
    if (Number.isInteger(km)) return `${km} km`;
    return `${km} km`;
  }

  // ── Time formatting ────────────────────────────────────────────────────────
  function fmtTime(s: number): string {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = Math.round(s % 60);
    if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
    return `${m}:${String(sec).padStart(2,'0')}`;
  }

  // Pace in min/km for running/walking/hiking
  function fmtPace(distKm: number, timeS: number): string {
    const secPerKm = timeS / distKm;
    const m = Math.floor(secPerKm / 60);
    const s = Math.round(secPerKm % 60);
    return `${m}:${String(s).padStart(2,'0')} /km`;
  }

  // Speed in km/h for cycling/swimming
  function fmtSpeed(distKm: number, timeS: number): string {
    return `${((distKm / timeS) * 3600).toFixed(1)} km/h`;
  }

  // ── Sport tabs ─────────────────────────────────────────────────────────────
  type SportTab = 'running' | 'cycling' | 'swimming' | 'hiking' | 'walking';
  const TABS: { key: SportTab; label: string }[] = [
    { key: 'running',  label: 'Running'  },
    { key: 'cycling',  label: 'Cycling'  },
    { key: 'swimming', label: 'Swimming' },
    { key: 'hiking',   label: 'Hiking'   },
    { key: 'walking',  label: 'Walking'  },
  ];

  let activeTab: SportTab = 'running';
  let mounted = false;

  $: if (mounted) {
    const params = new URLSearchParams(window.location.search);
    if (activeTab === 'running') params.delete('sport'); else params.set('sport', activeTab);
    const qs = params.toString();
    history.replaceState(null, '', qs ? `?${qs}` : window.location.pathname);
  }

  onMount(() => {
    const sp = new URLSearchParams(window.location.search).get('sport') as SportTab | null;
    if (sp && TABS.some(t => t.key === sp)) activeTab = sp;
    mounted = true;
  });

  // Tabs that have at least one record
  function hasRecords(sport: SportTab): boolean {
    const bucket = (athlete as any).records?.[sport];
    return bucket && Object.keys(bucket).length > 0;
  }

  // ── Record data helpers ────────────────────────────────────────────────────
  interface EffortRecord {
    time_s: number;
    activity_id: string;
    started_at: string;
    title: string;
  }
  interface ValueRecord {
    value: number;
    activity_id: string;
    started_at: string;
    title: string;
  }

  function distanceRecords(sport: SportTab): { distKm: number; rec: EffortRecord }[] {
    const bucket = (athlete as any).records?.[sport] ?? {};
    return Object.entries(bucket)
      .filter(([k]) => !isNaN(Number(k)))
      .map(([k, v]) => ({ distKm: Number(k), rec: v as EffortRecord }))
      .sort((a, b) => a.distKm - b.distKm);
  }

  function valueRecord(sport: SportTab, key: string): ValueRecord | null {
    return (athlete as any).records?.[sport]?.[key] ?? null;
  }

  const activityUrl = (id: string) => `${base}activity/${id}/`;
</script>

<!-- Sport tabs -->
<div class="flex gap-1 mb-6 flex-wrap">
  {#each TABS as tab}
    {@const active = activeTab === tab.key}
    {@const has = hasRecords(tab.key)}
    <button
      on:click={() => activeTab = tab.key}
      disabled={!has}
      class="px-3 py-1.5 rounded-full text-sm font-medium transition-colors"
      style={active
        ? `background:${sportColor(tab.key as any)}22; border:1px solid ${sportColor(tab.key as any)}; color:${sportColor(tab.key as any)}`
        : 'background:transparent; border:1px solid #3f3f46; color:' + (has ? '#a1a1aa' : '#52525b')}
    >
      {sportIcon(tab.key as any)} {tab.label}
    </button>
  {/each}
</div>

<!-- Running / Cycling / Swimming — distance-based sliding-window records -->
{#if activeTab === 'running' || activeTab === 'cycling' || activeTab === 'swimming'}
  {@const rows = distanceRecords(activeTab)}
  {#if rows.length}
    <div class="bg-zinc-900 rounded-xl border border-zinc-800 overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-zinc-800 text-zinc-500 text-xs uppercase tracking-wide">
            <th class="text-left px-4 py-3 font-medium">Distance</th>
            <th class="text-left px-4 py-3 font-medium">Time</th>
            <th class="text-left px-4 py-3 font-medium">
              {activeTab === 'running' ? 'Pace' : 'Speed'}
            </th>
            <th class="text-left px-4 py-3 font-medium">Date</th>
            <th class="text-left px-4 py-3 font-medium">Activity</th>
          </tr>
        </thead>
        <tbody>
          {#each rows as { distKm, rec }, i}
            <tr class="border-b border-zinc-800/50 last:border-0 hover:bg-zinc-800/30 transition-colors">
              <td class="px-4 py-3 font-semibold text-white">{distLabel(distKm)}</td>
              <td class="px-4 py-3 font-mono text-white">{fmtTime(rec.time_s)}</td>
              <td class="px-4 py-3 text-zinc-400">
                {activeTab === 'running'
                  ? fmtPace(distKm, rec.time_s)
                  : fmtSpeed(distKm, rec.time_s)}
              </td>
              <td class="px-4 py-3 text-zinc-400">{formatDate(rec.started_at)}</td>
              <td class="px-4 py-3">
                <a
                  href={activityUrl(rec.activity_id)}
                  class="text-blue-400 hover:text-blue-300 truncate max-w-[200px] block transition-colors"
                  title={rec.title}
                >{rec.title}</a>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

    <!-- Best climbs for cycling -->
    {#if activeTab === 'cycling' && (athlete as any).best_climbs?.length}
      {@const climbs = (athlete as any).best_climbs}
      <div class="mt-6">
        <h3 class="text-sm font-medium text-zinc-400 uppercase tracking-wide mb-3">⛰️ Best climb in one go</h3>
        <div class="bg-zinc-900 rounded-xl border border-zinc-800 overflow-hidden">
          <table class="w-full text-sm">
            <thead>
              <tr class="border-b border-zinc-800 text-zinc-500 text-xs uppercase tracking-wide">
                <th class="text-left px-4 py-3 font-medium w-8">#</th>
                <th class="text-left px-4 py-3 font-medium">Elevation</th>
                <th class="text-left px-4 py-3 font-medium">Date</th>
                <th class="text-left px-4 py-3 font-medium">Activity</th>
              </tr>
            </thead>
            <tbody>
              {#each climbs as bc, i}
                <tr class="border-b border-zinc-800/50 last:border-0 hover:bg-zinc-800/30 transition-colors">
                  <td class="px-4 py-3 text-zinc-600 text-xs">{i + 1}</td>
                  <td class="px-4 py-3 font-semibold text-white">{Math.round(bc.climb_m)} m</td>
                  <td class="px-4 py-3 text-zinc-400">{formatDate(bc.started_at)}</td>
                  <td class="px-4 py-3">
                    <a
                      href={activityUrl(bc.activity_id)}
                      class="text-blue-400 hover:text-blue-300 truncate max-w-[200px] block transition-colors"
                      title={bc.title}
                    >{bc.title}</a>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </div>
    {/if}

  {:else}
    <p class="text-zinc-500 text-sm">No {activeTab} records yet. Records are computed from activities with GPS speed data.</p>
  {/if}

<!-- Hiking / Walking — aggregate records only -->
{:else}
  {@const longest = valueRecord(activeTab, 'longest_m')}
  {@const mostElev = valueRecord(activeTab, 'most_elevation_m')}

  {#if longest || mostElev}
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
      {#if longest}
        <div class="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
          <p class="text-xs text-zinc-500 uppercase tracking-wide mb-1">Longest {activeTab}</p>
          <p class="text-white font-semibold text-xl">{(longest.value / 1000).toFixed(1)} km</p>
          <a href={activityUrl(longest.activity_id)} class="text-sm text-blue-400 hover:text-blue-300 mt-1 block transition-colors">
            {longest.title} · {formatDate(longest.started_at)}
          </a>
        </div>
      {/if}
      {#if mostElev}
        <div class="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
          <p class="text-xs text-zinc-500 uppercase tracking-wide mb-1">Most elevation</p>
          <p class="text-white font-semibold text-xl">{Math.round(mostElev.value)} m</p>
          <a href={activityUrl(mostElev.activity_id)} class="text-sm text-blue-400 hover:text-blue-300 mt-1 block transition-colors">
            {mostElev.title} · {formatDate(mostElev.started_at)}
          </a>
        </div>
      {/if}
    </div>
  {:else}
    <p class="text-zinc-500 text-sm">No {activeTab} records yet.</p>
  {/if}
{/if}
