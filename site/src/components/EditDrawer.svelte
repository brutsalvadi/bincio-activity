<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { Sport } from '../lib/types';

  export let activityId: string;
  export let editUrl: string;

  const dispatch = createEventDispatcher<{ saved: { title: string; description: string }; close: void; deleted: void }>();

  const SPORTS: Sport[] = ['cycling', 'running', 'hiking', 'walking', 'swimming', 'skiing', 'other'];
  const STAT_PANELS = [
    { key: 'elevation',   label: 'Elevation' },
    { key: 'speed',       label: 'Speed' },
    { key: 'heart_rate',  label: 'Heart rate' },
    { key: 'cadence',     label: 'Cadence' },
    { key: 'power',       label: 'Power' },
  ];

  let loading = true;
  let loadError = '';
  let saving = false;
  let saveStatus = '';
  let saveOk = false;
  let confirmDelete = false;
  let deleting = false;

  // Elevation recalculation
  let recalculating: '' | 'dem' | 'hysteresis' = '';
  let recalcStatus = '';
  let recalcOk = false;

  // Form state
  let title = '';
  let sport: Sport = 'cycling';
  let gear = '';
  let description = '';
  let highlight = false;
  let isPrivate = false;
  let hideStats: string[] = [];
  let images: string[] = [];

  // Image upload
  let uploading = false;
  let fileInput: HTMLInputElement;

  // editUrl is empty in multi-user VPS mode — the Vite proxy forwards /api/* to bincio serve.
  const api = `${editUrl}/api/activity/${activityId}`;

  async function load() {
    loading = true;
    loadError = '';
    try {
      const res = await fetch(api);
      if (!res.ok) throw new Error(`Edit server returned ${res.status} — is bincio edit running?`);
      const d = await res.json();
      title       = d.title       ?? '';
      sport       = d.sport       ?? 'cycling';
      gear        = d.gear        ?? '';
      // Strip any auto-inserted image markdown refs — images are tracked via custom.images
      description = (d.description ?? '').replace(/!\[[^\]]*\]\([^)]+\)\n?/g, '').trim();
      highlight   = d.highlight   ?? false;
      // d.private is a bool (from the API); d.privacy is the raw field on older
      // endpoints. Accept either so the drawer works with both serve and edit servers.
      isPrivate   = d.private     ?? (d.privacy === 'unlisted' || d.privacy === 'private') ?? false;
      hideStats   = d.hide_stats  ?? [];
      images      = d.images      ?? [];
    } catch (e: any) {
      loadError = e.message;
    } finally {
      loading = false;
    }
  }

  async function save() {
    saving = true;
    saveStatus = '';
    saveOk = false;
    try {
      const res = await fetch(api, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, sport, gear, description, highlight, private: isPrivate, hide_stats: hideStats }),
      });
      if (!res.ok) throw new Error(await res.text());
      saveStatus = 'Saved';
      saveOk = true;
      dispatch('saved', { title, description });
    } catch (e: any) {
      saveStatus = e.message;
      saveOk = false;
    } finally {
      saving = false;
    }
  }

  async function uploadImages(files: FileList) {
    uploading = true;
    try {
      for (const file of Array.from(files)) {
        const fd = new FormData();
        fd.append('file', file);
        const res = await fetch(`${api}/images`, { method: 'POST', body: fd });
        if (res.ok) {
          const d = await res.json();
          if (!images.includes(d.filename)) images = [...images, d.filename];
        }
      }
    } catch (e: any) {
      saveStatus = `Upload failed: ${e.message}`;
      saveOk = false;
    } finally {
      uploading = false;
    }
  }

  async function deleteImage(filename: string) {
    await fetch(`${api}/images/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    images = images.filter(f => f !== filename);
  }

  function toggleStat(key: string) {
    hideStats = hideStats.includes(key)
      ? hideStats.filter(s => s !== key)
      : [...hideStats, key];
  }

  async function recalculateElevation(method: 'dem' | 'hysteresis') {
    recalculating = method;
    recalcStatus = '';
    recalcOk = false;
    try {
      const res = await fetch(`${api}/recalculate-elevation/${method}`, { method: 'POST' });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail ?? await res.text());
      recalcOk = true;
      const gain = d.elevation_gain_m != null ? `↑ ${Math.round(d.elevation_gain_m)} m` : '';
      const loss = d.elevation_loss_m != null ? `↓ ${Math.round(d.elevation_loss_m)} m` : '';
      recalcStatus = [gain, loss].filter(Boolean).join('  ');
    } catch (e: any) {
      recalcStatus = e.message;
      recalcOk = false;
    } finally {
      recalculating = '';
    }
  }

  async function deleteActivity() {
    if (!confirmDelete) { confirmDelete = true; return; }
    deleting = true;
    try {
      const res = await fetch(api, { method: 'DELETE' });
      if (!res.ok) throw new Error(await res.text());
      dispatch('deleted');
    } catch (e: any) {
      saveStatus = `Delete failed: ${e.message}`;
      saveOk = false;
      confirmDelete = false;
    } finally {
      deleting = false;
    }
  }

  load();
</script>

<!-- Backdrop -->
<div
  class="fixed inset-0 bg-black/60 z-40 backdrop-blur-sm"
  on:click={() => dispatch('close')}
  role="presentation"
></div>

<!-- Drawer -->
<aside class="fixed top-0 right-0 h-full w-full max-w-md bg-zinc-950 border-l border-zinc-800 z-50 flex flex-col shadow-2xl">
  <!-- Header -->
  <div class="flex items-center justify-between px-5 py-4 border-b border-zinc-800 shrink-0">
    <h2 class="font-semibold text-white text-sm">Edit activity</h2>
    <button
      class="text-zinc-500 hover:text-white transition-colors text-xl leading-none"
      on:click={() => dispatch('close')}
      aria-label="Close"
    >×</button>
  </div>

  <!-- Body -->
  <div class="flex-1 overflow-y-auto px-5 py-4">
    {#if loading}
      <div class="space-y-3 animate-pulse">
        {#each Array(4) as _}
          <div class="h-9 rounded bg-zinc-800"></div>
        {/each}
      </div>
    {:else if loadError}
      <p class="text-red-400 text-sm">{loadError}</p>
    {:else}
      <!-- Title -->
      <div class="mb-4">
        <label class="block text-xs text-zinc-500 mb-1" for="ed-title">Title</label>
        <input
          id="ed-title"
          type="text"
          bind:value={title}
          class="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-lg text-sm text-white outline-none focus:border-blue-500 transition-colors"
        />
      </div>

      <!-- Sport + Gear -->
      <div class="grid grid-cols-2 gap-3 mb-4">
        <div>
          <label class="block text-xs text-zinc-500 mb-1" for="ed-sport">Sport</label>
          <select
            id="ed-sport"
            bind:value={sport}
            class="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-lg text-sm text-white outline-none focus:border-blue-500 transition-colors"
          >
            {#each SPORTS as s}
              <option value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
            {/each}
          </select>
        </div>
        <div>
          <label class="block text-xs text-zinc-500 mb-1" for="ed-gear">Gear</label>
          <input
            id="ed-gear"
            type="text"
            bind:value={gear}
            placeholder="e.g. Trek Domane"
            class="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-lg text-sm text-white placeholder-zinc-600 outline-none focus:border-blue-500 transition-colors"
          />
        </div>
      </div>

      <!-- Description -->
      <div class="mb-4">
        <label class="block text-xs text-zinc-500 mb-1" for="ed-desc">Description <span class="text-zinc-600">(markdown)</span></label>
        <textarea
          id="ed-desc"
          bind:value={description}
          rows={6}
          placeholder="Write about this activity…"
          class="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-lg text-sm text-white placeholder-zinc-600 outline-none focus:border-blue-500 transition-colors resize-y"
        ></textarea>
      </div>

      <!-- Images -->
      <div class="mb-4">
        <p class="text-xs text-zinc-500 mb-2">Images</p>
        <button
          type="button"
          class="w-full border border-dashed border-zinc-700 rounded-lg px-4 py-3 text-center text-xs text-zinc-500 cursor-pointer hover:border-zinc-500 hover:text-zinc-300 transition-colors"
          on:click={() => fileInput.click()}
          on:dragover|preventDefault
          on:drop|preventDefault={e => e.dataTransfer?.files && uploadImages(e.dataTransfer.files)}
        >
          {uploading ? 'Uploading…' : 'Drop images or click to upload'}
        </button>
        <input bind:this={fileInput} type="file" accept="image/*" multiple class="hidden"
          on:change={e => e.currentTarget.files && uploadImages(e.currentTarget.files)} />
        {#if images.length}
          <div class="flex flex-wrap gap-2 mt-2">
            {#each images as img}
              <span class="flex items-center gap-1 text-xs bg-zinc-800 border border-zinc-700 rounded-full px-2 py-0.5">
                {img}
                <button class="text-zinc-500 hover:text-red-400 transition-colors" on:click={() => deleteImage(img)}>×</button>
              </span>
            {/each}
          </div>
        {/if}
      </div>

      <!-- Hide stats -->
      <div class="mb-4">
        <p class="text-xs text-zinc-500 mb-2">Hide stat panels</p>
        <div class="flex flex-wrap gap-2">
          {#each STAT_PANELS as panel}
            <button
              type="button"
              class="text-xs px-3 py-1 rounded-full border transition-colors"
              class:border-zinc-700={!hideStats.includes(panel.key)}
              class:text-zinc-400={!hideStats.includes(panel.key)}
              class:border-blue-500={hideStats.includes(panel.key)}
              class:text-white={hideStats.includes(panel.key)}
              style={hideStats.includes(panel.key) ? 'background:rgba(59,130,246,.15)' : ''}
              on:click={() => toggleStat(panel.key)}
            >
              {panel.label}
            </button>
          {/each}
        </div>
      </div>

      <!-- Elevation recalculation -->
      <div class="mb-4">
        <p class="text-xs text-zinc-500 mb-2">Elevation</p>
        <div class="flex gap-2">
          <button
            type="button"
            class="flex-1 flex items-center justify-center gap-1 px-3 py-2 rounded-lg border border-zinc-700 text-xs text-zinc-400 hover:border-zinc-500 hover:text-zinc-200 transition-colors disabled:opacity-40"
            disabled={recalculating !== ''}
            on:click={() => recalculateElevation('hysteresis')}
            title="Recompute from the original recorded elevation using noise-filtering (fast, no network)"
          >
            {recalculating === 'hysteresis' ? 'Computing…' : '📐 Recalculate (hysteresis)'}
          </button>
          <button
            type="button"
            class="flex-1 flex items-center justify-center gap-1 px-3 py-2 rounded-lg border border-zinc-700 text-xs text-zinc-400 hover:border-zinc-500 hover:text-zinc-200 transition-colors disabled:opacity-40"
            disabled={recalculating !== ''}
            on:click={() => recalculateElevation('dem')}
            title="Replace elevation with SRTM terrain data from the internet (slower, most accurate for GPS-only devices)"
          >
            {recalculating === 'dem' ? 'Querying terrain…' : '⛰ Recalculate (DEM)'}
          </button>
        </div>
        {#if recalcStatus}
          <p class="text-xs mt-1.5 text-center" class:text-green-400={recalcOk} class:text-red-400={!recalcOk}>
            {recalcStatus}
          </p>
        {/if}
      </div>

      <!-- Flags -->
      <div class="flex gap-3 mb-2">
        <button
          type="button"
          class="flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg border transition-colors"
          class:border-zinc-700={!highlight}
          class:text-zinc-400={!highlight}
          class:border-yellow-500={highlight}
          class:text-yellow-300={highlight}
          style={highlight ? 'background:rgba(234,179,8,.1)' : ''}
          on:click={() => highlight = !highlight}
        >
          ★ Highlight
        </button>
        <button
          type="button"
          class="flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg border transition-colors"
          class:border-zinc-700={!isPrivate}
          class:text-zinc-400={!isPrivate}
          class:border-red-500={isPrivate}
          class:text-red-300={isPrivate}
          style={isPrivate ? 'background:rgba(239,68,68,.1)' : ''}
          on:click={() => isPrivate = !isPrivate}
        >
          ⊘ Unlisted
        </button>
      </div>
    {/if}
  </div>

  <!-- Footer -->
  {#if !loading && !loadError}
    <div class="px-5 py-4 border-t border-zinc-800 flex items-center gap-3 shrink-0">
      <button
        class="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white text-sm font-medium rounded-lg transition-colors"
        disabled={saving || deleting}
        on:click={save}
      >
        {saving ? 'Saving…' : 'Save'}
      </button>
      <button
        class="px-3 py-2 text-sm font-medium rounded-lg border transition-colors disabled:opacity-40 ml-auto"
        class:border-zinc-700={!confirmDelete}
        class:text-zinc-500={!confirmDelete}
        class:hover:border-red-600={!confirmDelete}
        class:hover:text-red-400={!confirmDelete}
        class:border-red-500={confirmDelete}
        class:text-red-400={confirmDelete}
        class:bg-red-950={confirmDelete}
        disabled={deleting}
        on:click={deleteActivity}
        on:blur={() => confirmDelete = false}
      >
        {deleting ? 'Deleting…' : confirmDelete ? 'Confirm delete?' : 'Delete'}
      </button>
      {#if saveStatus}
        <span class="text-xs" class:text-green-400={saveOk} class:text-red-400={!saveOk}>
          {saveStatus}
        </span>
      {/if}
    </div>
  {/if}
</aside>
