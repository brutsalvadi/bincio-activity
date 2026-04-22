<script lang="ts">
  import { onMount } from 'svelte';
  import type { AthleteJson, BASIndex, ActivitySummary } from '../lib/types';
  import MmpChart from './MmpChart.svelte';
  import RecordsView from './RecordsView.svelte';
  import AthleteDrawer from './AthleteDrawer.svelte';
  import { isUnlisted } from '../lib/format';
  import { loadIndex, loadAthlete } from '../lib/dataloader';

  export let base: string = '/';
  /** Explicit index URL for multi-user per-user pages (user's shard). */
  export let indexUrl: string = '';
  /** Explicit athlete.json URL for multi-user per-user pages. */
  export let athleteUrl: string = '';

  let athlete: AthleteJson | null = null;
  let activities: ActivitySummary[] = [];
  let loading = true;
  let error: string | null = null;
  let drawerOpen = false;

  type Tab = 'power' | 'records' | 'profile';
  let activeTab: Tab = 'power';
  let mounted = false;

  const editUrl = import.meta.env.PUBLIC_EDIT_URL ?? '';
  const editEnabled = editUrl !== '' || import.meta.env.PUBLIC_EDIT_ENABLED === 'true';

  $: if (mounted) {
    const params = new URLSearchParams(window.location.search);
    if (activeTab === 'power') params.delete('tab'); else params.set('tab', activeTab);
    const qs = params.toString();
    history.replaceState(null, '', qs ? `?${qs}` : window.location.pathname);
  }

  onMount(async () => {
    const TABS: Tab[] = ['power', 'records', 'profile'];
    const rawTab = new URLSearchParams(window.location.search).get('tab');
    activeTab = TABS.includes(rawTab as Tab) ? (rawTab as Tab) : 'power';
    mounted = true;
    try {
      const [athleteData, index] = await Promise.all([
        loadAthlete(import.meta.env.BASE_URL, athleteUrl || undefined),
        loadIndex(import.meta.env.BASE_URL, indexUrl || undefined),
      ]);
      // Static file may not exist yet if the background rebuild hasn't finished — fall back to API
      let resolvedAthlete = athleteData as AthleteJson | null;
      if (!resolvedAthlete && editEnabled) {
        try {
          const r = await fetch('/api/athlete', { credentials: 'include' });
          if (r.ok) resolvedAthlete = await r.json() as AthleteJson;
        } catch { /* ignore */ }
      }
      athlete = resolvedAthlete;
      activities = index.activities.filter(a => a.mmp && !isUnlisted(a.privacy));
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  });

  async function onSaved() {
    // Try static file first; fall back to API (works before the background rebuild finishes)
    const staticUrl = athleteUrl || `${import.meta.env.BASE_URL}data/athlete.json`;
    let res = await fetch(`${staticUrl}?t=${Date.now()}`);
    if (!res.ok) res = await fetch('/api/athlete', { credentials: 'include' });
    if (res.ok) athlete = await res.json() as AthleteJson;
    drawerOpen = false;
  }

  function fmtZone(zones: [number, number][], i: number): string {
    const [lo, hi] = zones[i];
    return hi >= 9000 ? `${lo}+ W` : `${lo}–${hi} W`;
  }
  function fmtHrZone(zones: [number, number][], i: number): string {
    const [lo, hi] = zones[i];
    return hi >= 900 ? `${lo}+ bpm` : `${lo}–${hi} bpm`;
  }

  const TABS: { key: Tab; label: string }[] = [
    { key: 'power',   label: 'Power Curve' },
    { key: 'records', label: 'Records'     },
    { key: 'profile', label: 'Profile'     },
  ];
</script>

{#if loading}
  <p class="text-zinc-400 text-sm">Loading…</p>
{:else if error}
  <p class="text-red-400 text-sm">{error}</p>
{:else if !athlete}
  <div class="text-zinc-400 text-sm space-y-3">
    <p>No athlete profile yet.</p>
    {#if editEnabled}
      <button
        on:click={() => drawerOpen = true}
        class="px-3 py-1.5 text-xs border border-zinc-700 hover:border-zinc-500 text-zinc-400 hover:text-white rounded-md transition-colors"
      >Create profile</button>
    {/if}
  </div>
{:else}

  <!-- Header row: tabs + edit button -->
  <div class="flex items-center justify-between mb-6 border-b border-zinc-800 pb-0">
    <nav class="flex gap-0">
      {#each TABS as tab}
        <button
          on:click={() => activeTab = tab.key}
          class="px-4 py-3 text-sm font-medium border-b-2 transition-colors -mb-px"
          class:border-blue-500={activeTab === tab.key}
          class:text-white={activeTab === tab.key}
          class:border-transparent={activeTab !== tab.key}
          class:text-zinc-500={activeTab !== tab.key}
          class:hover:text-zinc-300={activeTab !== tab.key}
        >{tab.label}</button>
      {/each}
    </nav>
    {#if editEnabled}
      <button
        on:click={() => drawerOpen = true}
        class="mb-2 px-3 py-1.5 text-xs border border-zinc-700 hover:border-zinc-500 text-zinc-400 hover:text-white rounded-md transition-colors"
      >Edit profile</button>
    {/if}
  </div>

  <!-- Power Curve tab -->
  {#if activeTab === 'power'}
    {#if athlete.power_curve.all_time}
      <div class="bg-zinc-900 rounded-xl p-4 border border-zinc-800">
        <MmpChart {athlete} {activities} />
      </div>
    {:else}
      <p class="text-zinc-500 text-sm">No power data found. Make sure your activities include power meter data.</p>
    {/if}

  <!-- Records tab -->
  {:else if activeTab === 'records'}
    <RecordsView {athlete} {base} />

  <!-- Profile tab -->
  {:else if activeTab === 'profile'}
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">

      <div class="bg-zinc-900 rounded-xl p-4 border border-zinc-800 space-y-3">
        <h3 class="text-sm font-medium text-zinc-400 uppercase tracking-wide">Key numbers</h3>
        {#if athlete.max_hr}
          <div class="flex justify-between text-sm">
            <span class="text-zinc-400">Max HR</span>
            <span class="text-white font-medium">{athlete.max_hr} bpm</span>
          </div>
        {/if}
        {#if athlete.ftp_w}
          <div class="flex justify-between text-sm">
            <span class="text-zinc-400">FTP</span>
            <span class="text-white font-medium">{athlete.ftp_w} W</span>
          </div>
        {/if}
        {#if !athlete.max_hr && !athlete.ftp_w}
          <p class="text-zinc-500 text-sm">Set <code>athlete.max_hr</code> and <code>athlete.ftp_w</code> in your config, or use Edit profile.</p>
        {/if}
      </div>

      {#if athlete.hr_zones}
        <div class="bg-zinc-900 rounded-xl p-4 border border-zinc-800 space-y-2">
          <h3 class="text-sm font-medium text-zinc-400 uppercase tracking-wide">HR Zones</h3>
          {#each athlete.hr_zones as _zone, i}
            <div class="flex justify-between items-center text-sm">
              <span class="text-zinc-400">Z{i + 1}</span>
              <span class="text-white">{fmtHrZone(athlete.hr_zones!, i)}</span>
            </div>
          {/each}
        </div>
      {/if}

      {#if athlete.power_zones}
        <div class="bg-zinc-900 rounded-xl p-4 border border-zinc-800 space-y-2">
          <h3 class="text-sm font-medium text-zinc-400 uppercase tracking-wide">Power Zones</h3>
          {#each athlete.power_zones as _zone, i}
            <div class="flex justify-between items-center text-sm">
              <span class="text-zinc-400">Z{i + 1}</span>
              <span class="text-white">{fmtZone(athlete.power_zones!, i)}</span>
            </div>
          {/each}
        </div>
      {/if}

    </div>
  {/if}

{/if}

{#if drawerOpen && editEnabled}
  <AthleteDrawer
    {editUrl}
    on:close={() => drawerOpen = false}
    on:saved={onSaved}
  />
{/if}
