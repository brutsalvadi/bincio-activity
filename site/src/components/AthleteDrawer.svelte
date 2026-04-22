<script lang="ts">
  import { createEventDispatcher } from 'svelte';

  export let editUrl: string;   // PUBLIC_EDIT_URL base

  const dispatch = createEventDispatcher<{ close: void; saved: void }>();

  // ── State ──────────────────────────────────────────────────────────────────
  let loading = true;
  let saving = false;
  let error: string | null = null;
  let status: string | null = null;

  let maxHr: number | null = null;
  let ftpW: number | null = null;
  let hrZones: [number, number][] = [];
  let powerZones: [number, number][] = [];
  let seasons: { name: string; start: string; end: string }[] = [];

  // ── Load ───────────────────────────────────────────────────────────────────
  async function load() {
    loading = true; error = null;
    try {
      const res = await fetch(`${editUrl}/api/athlete`);
      if (!res.ok) throw new Error(await res.text());
      const d = await res.json();
      maxHr      = d.max_hr    ?? null;
      ftpW       = d.ftp_w     ?? null;
      hrZones    = d.hr_zones    ? d.hr_zones.map((z: number[]) => [z[0], z[1]] as [number,number]) : [];
      powerZones = d.power_zones ? d.power_zones.map((z: number[]) => [z[0], z[1]] as [number,number]) : [];
      seasons    = d.seasons ?? [];
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  }
  load();

  // ── Zone editing helpers ───────────────────────────────────────────────────
  function onHiChange(zones: [number, number][], i: number) {
    // Cascade: next row's lo = this row's hi
    if (i + 1 < zones.length) zones[i + 1][0] = zones[i][1];
    zones = [...zones]; // trigger reactivity
    return zones;
  }

  function addZone(zones: [number, number][]): [number, number][] {
    const prevHi = zones.length ? zones[zones.length - 1][1] : 0;
    return [...zones, [prevHi, prevHi + 50]];
  }

  function removeZone(zones: [number, number][], i: number): [number, number][] {
    const next = zones.filter((_, idx) => idx !== i);
    // Re-cascade lo values
    for (let j = 1; j < next.length; j++) next[j][0] = next[j - 1][1];
    return next;
  }

  // ── Season helpers ─────────────────────────────────────────────────────────
  function addSeason() {
    const year = new Date().getFullYear();
    seasons = [...seasons, { name: `${year}`, start: `${year}-01-01`, end: `${year}-12-31` }];
  }
  function removeSeason(i: number) {
    seasons = seasons.filter((_, idx) => idx !== i);
  }

  // ── Save ───────────────────────────────────────────────────────────────────
  async function save() {
    saving = true; status = null; error = null;
    try {
      const res = await fetch(`${editUrl}/api/athlete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          max_hr:       maxHr,
          ftp_w:        ftpW,
          hr_zones:     hrZones,
          power_zones:  powerZones,
          seasons,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      status = 'Saved.';
      dispatch('saved');
    } catch (e: any) {
      error = e.message;
    } finally {
      saving = false;
    }
  }

  function closeDrawer() { dispatch('close'); }

  // Close on Escape
  function onKeydown(e: KeyboardEvent) { if (e.key === 'Escape') closeDrawer(); }
</script>

<svelte:window on:keydown={onKeydown} />

<!-- Backdrop -->
<div
  class="fixed inset-0 bg-black/50 z-40"
  on:click={closeDrawer}
  role="presentation"
></div>

<!-- Drawer -->
<aside class="fixed top-0 right-0 h-full w-full max-w-lg bg-zinc-950 border-l border-zinc-800 z-50 flex flex-col shadow-2xl overflow-hidden">

  <!-- Header -->
  <div class="flex items-center justify-between px-5 py-4 border-b border-zinc-800 flex-shrink-0">
    <h2 class="text-base font-semibold text-white">Edit Athlete Profile</h2>
    <button on:click={closeDrawer} class="text-zinc-400 hover:text-white text-xl leading-none">×</button>
  </div>

  {#if loading}
    <div class="flex-1 flex items-center justify-center text-zinc-500 text-sm">Loading…</div>
  {:else if error && !saving}
    <div class="flex-1 flex items-center justify-center text-red-400 text-sm px-6 text-center">{error}</div>
  {:else}

  <!-- Scrollable body -->
  <div class="flex-1 overflow-y-auto px-5 py-5 space-y-8">

    <!-- Key numbers -->
    <section>
      <h3 class="text-xs font-medium text-zinc-400 uppercase tracking-wide mb-3">Key numbers</h3>
      <div class="grid grid-cols-2 gap-4">
        <label class="block">
          <span class="text-xs text-zinc-500 mb-1 block">Max HR (bpm)</span>
          <input
            type="number" min="100" max="250"
            bind:value={maxHr}
            class="w-full bg-zinc-900 border border-zinc-700 rounded-md px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
          />
        </label>
        <label class="block">
          <span class="text-xs text-zinc-500 mb-1 block">FTP (watts)</span>
          <input
            type="number" min="50" max="600"
            bind:value={ftpW}
            class="w-full bg-zinc-900 border border-zinc-700 rounded-md px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
          />
        </label>
      </div>
    </section>

    <!-- HR Zones -->
    <section>
      <h3 class="text-xs font-medium text-zinc-400 uppercase tracking-wide mb-3">HR Zones (bpm)</h3>
      <div class="space-y-2">
        {#each hrZones as zone, i}
          <div class="flex items-center gap-2">
            <span class="text-xs text-zinc-500 w-5">Z{i+1}</span>
            <input
              type="number"
              bind:value={zone[0]}
              disabled={i === 0}
              class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500 disabled:opacity-40"
            />
            <span class="text-zinc-600 text-xs">–</span>
            <input
              type="number"
              bind:value={zone[1]}
              on:change={() => { hrZones = onHiChange(hrZones, i); }}
              class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
            />
            <span class="text-xs text-zinc-600">bpm</span>
            <button
              on:click={() => { hrZones = removeZone(hrZones, i); }}
              class="ml-auto text-zinc-600 hover:text-red-400 text-sm leading-none"
            >×</button>
          </div>
        {/each}
      </div>
      <button
        on:click={() => { hrZones = addZone(hrZones); }}
        class="mt-2 text-xs text-zinc-500 hover:text-white border border-zinc-700 hover:border-zinc-500 rounded px-3 py-1 transition-colors"
      >+ Add zone</button>
    </section>

    <!-- Power Zones -->
    <section>
      <h3 class="text-xs font-medium text-zinc-400 uppercase tracking-wide mb-3">Power Zones (watts)</h3>
      <div class="space-y-2">
        {#each powerZones as zone, i}
          <div class="flex items-center gap-2">
            <span class="text-xs text-zinc-500 w-5">Z{i+1}</span>
            <input
              type="number"
              bind:value={zone[0]}
              disabled={i === 0}
              class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500 disabled:opacity-40"
            />
            <span class="text-zinc-600 text-xs">–</span>
            <input
              type="number"
              bind:value={zone[1]}
              on:change={() => { powerZones = onHiChange(powerZones, i); }}
              class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
            />
            <span class="text-xs text-zinc-600">W</span>
            <button
              on:click={() => { powerZones = removeZone(powerZones, i); }}
              class="ml-auto text-zinc-600 hover:text-red-400 text-sm leading-none"
            >×</button>
          </div>
        {/each}
      </div>
      <button
        on:click={() => { powerZones = addZone(powerZones); }}
        class="mt-2 text-xs text-zinc-500 hover:text-white border border-zinc-700 hover:border-zinc-500 rounded px-3 py-1 transition-colors"
      >+ Add zone</button>
    </section>

    <!-- Seasons -->
    <section>
      <h3 class="text-xs font-medium text-zinc-400 uppercase tracking-wide mb-1">Seasons</h3>
      <p class="text-xs text-zinc-600 mb-3">Overlay multiple seasons on the power curve chart.</p>
      <div class="space-y-2">
        {#each seasons as season, i}
          <div class="flex items-center gap-2">
            <input
              type="text"
              bind:value={season.name}
              placeholder="Name"
              class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
            />
            <input
              type="date"
              bind:value={season.start}
              class="bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
            />
            <span class="text-zinc-600 text-xs">→</span>
            <input
              type="date"
              bind:value={season.end}
              class="bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
            />
            <button
              on:click={() => removeSeason(i)}
              class="text-zinc-600 hover:text-red-400 text-sm leading-none"
            >×</button>
          </div>
        {/each}
      </div>
      <button
        on:click={addSeason}
        class="mt-2 text-xs text-zinc-500 hover:text-white border border-zinc-700 hover:border-zinc-500 rounded px-3 py-1 transition-colors"
      >+ Add season</button>
    </section>

  </div>

  <!-- Footer -->
  <div class="flex items-center gap-3 px-5 py-4 border-t border-zinc-800 flex-shrink-0">
    <button
      on:click={save}
      disabled={saving}
      class="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white text-sm font-medium rounded-md transition-colors"
    >{saving ? 'Saving…' : 'Save'}</button>
    <button
      on:click={closeDrawer}
      class="px-4 py-2 border border-zinc-700 hover:border-zinc-500 text-zinc-300 text-sm rounded-md transition-colors"
    >Cancel</button>
    {#if status}
      <span class="text-green-400 text-sm">{status}</span>
    {/if}
    {#if error}
      <span class="text-red-400 text-sm truncate">{error}</span>
    {/if}
  </div>

  {/if}
</aside>
