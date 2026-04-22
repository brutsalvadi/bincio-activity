<script lang="ts">
  import { onMount } from 'svelte';
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';
  import type { ActivitySummary, ActivityDetail, AthleteZones, Timeseries } from '../lib/types';
  import { formatDistance, formatDuration, formatElevation, formatSpeed, formatDate, formatTime, sportIcon, sportLabel, sportColor } from '../lib/format';
  import ActivityMap from './ActivityMap.svelte';
  import ActivityCharts from './ActivityCharts.svelte';
  import EditDrawer from './EditDrawer.svelte';
  import { loadActivity, loadTimeseries } from '../lib/dataloader';

  export let activity: ActivitySummary;
  export let base: string = '/';
  export let athlete: AthleteZones | null = null;

  const editUrl = import.meta.env.PUBLIC_EDIT_URL ?? '';
  const editEnabled = editUrl !== '' || import.meta.env.PUBLIC_EDIT_ENABLED === 'true';

  let detail: ActivityDetail | null = null;
  let timeseries: Timeseries | null = null;
  let timeseriesLoading = false;
  let error = '';
  let hoveredIdx: number | null = null;
  let editOpen = false;
  let lightboxIndex: number | null = null;

  // Local overrides applied immediately after a save (no re-fetch needed)
  let localTitle = '';
  let localDescription = '';
  $: displayTitle = localTitle || activity.title;

  onMount(async () => {
    try {
      detail = await loadActivity(activity.id, activity.detail_url ?? '', base);
      if (!detail) throw new Error('Activity not found');
      // Use embedded timeseries (IDB activities) or lazy-fetch from URL
      if (detail.timeseries) {
        timeseries = detail.timeseries;
      } else if (detail.timeseries_url) {
        timeseriesLoading = true;
        timeseries = await loadTimeseries(detail.timeseries_url, activity.detail_url ?? '', base);
        timeseriesLoading = false;
      }
    } catch (e: any) {
      error = e.message;
      timeseriesLoading = false;
    }
  });

  function onSaved(e: CustomEvent<{ title: string; description: string }>) {
    editOpen = false;
    localTitle = e.detail.title;
    localDescription = e.detail.description;
  }

  $: trackUrl = activity.track_url
    ? (activity.track_url.startsWith('http') || activity.track_url.startsWith('/') ? activity.track_url : `${base}data/${activity.track_url}`)
    : null;
  $: color = sportColor(activity.sport);

  function lightboxPrev() { if (lightboxIndex !== null) lightboxIndex = (lightboxIndex - 1 + galleryImages.length) % galleryImages.length; }
  function lightboxNext() { if (lightboxIndex !== null) lightboxIndex = (lightboxIndex + 1) % galleryImages.length; }
  function onKeydown(e: KeyboardEvent) {
    if (lightboxIndex === null) return;
    if (e.key === 'ArrowLeft')  { e.preventDefault(); lightboxPrev(); }
    if (e.key === 'ArrowRight') { e.preventDefault(); lightboxNext(); }
    if (e.key === 'Escape')     { lightboxIndex = null; }
  }

  $: rawDescription = localDescription || detail?.description || '';
  $: descriptionHtml = (() => {
    if (!rawDescription) return '';
    // Strip local image refs before marked sees them. marked only parses ![alt](url) as an
    // image when the URL has no spaces — filenames like "WhatsApp Image 2026.jpg" are left
    // as literal text instead. The lazy .*? anchored to the image extension handles filenames
    // with spaces and nested parens (e.g. "file(2).jpg") correctly.
    const stripped = rawDescription
      .replace(/!\[[^\]]*\]\((?!https?:\/\/|\/|data:).*?\.(?:jpe?g|png|gif|webp|bmp|avif|heic)\)/gi, '')
      .trim();
    if (!stripped) return '';
    const renderer = new marked.Renderer();
    // Any remaining remote images render inline; local ones (shouldn't exist after strip) are suppressed
    renderer.image = ({ href, title, text }) => {
      const isLocal = href && !href.startsWith('http') && !href.startsWith('/') && !href.startsWith('data:');
      if (isLocal) return '';
      const titleAttr = title ? ` title="${title}"` : '';
      return `<img src="${href ?? ''}" alt="${text}"${titleAttr} class="rounded-lg max-w-full my-2">`;
    };
    return DOMPurify.sanitize(marked(stripped, { renderer }) as string);
  })();

  // Derive image dir from detail_url so multi-user paths resolve correctly.
  // Relative: "dave/_merged/activities/foo.json" → "/data/dave/_merged/activities/images/{id}/"
  // Absolute: "/data/dave/_merged/activities/foo.json" → "/data/dave/_merged/activities/images/{id}/"
  $: imageBase = (() => {
    const du = activity.detail_url ?? '';
    const dir = du.startsWith('http') || du.startsWith('/')
      ? du.substring(0, du.lastIndexOf('/') + 1)
      : du.includes('/')
        ? `${base}data/${du.substring(0, du.lastIndexOf('/') + 1)}`
        : `${base}data/activities/`;
    return `${dir}images/${activity.id}/`;
  })();
  $: galleryImages = (detail?.custom as any)?.images as string[] ?? [];


  const stat = (label: string, value: string, key?: string) => ({ label, value, key });
  $: hiddenStats = new Set<string>((detail?.custom as any)?.hide_stats ?? []);
  $: stats = [
    stat('Distance',    formatDistance(activity.distance_m)),
    stat('Moving time', formatDuration(activity.moving_time_s ?? activity.duration_s)),
    stat('Elevation ↑', formatElevation(activity.elevation_gain_m),                    'elevation'),
    stat('Avg speed',   formatSpeed(activity.avg_speed_kmh),                           'speed'),
    stat('Max speed',   formatSpeed(activity.max_speed_kmh),                           'speed'),
    stat('Avg HR',      activity.avg_hr_bpm ? `${activity.avg_hr_bpm} bpm` : '—',     'heart_rate'),
    stat('Max HR',      activity.max_hr_bpm ? `${activity.max_hr_bpm} bpm` : '—',     'heart_rate'),
    stat('Cadence',     activity.avg_cadence_rpm ? `${activity.avg_cadence_rpm} rpm` : '—', 'cadence'),
  ].filter(s => !s.key || !hiddenStats.has(s.key));
</script>

<svelte:window on:keydown={onKeydown} />

{#if editOpen && editEnabled}
  <EditDrawer activityId={activity.id} {editUrl} on:saved={onSaved} on:close={() => editOpen = false} on:deleted={() => { window.location.href = base; }} />
{/if}

<!-- Lightbox -->
{#if lightboxIndex !== null}
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div
    class="fixed inset-0 z-50 bg-black/95 flex items-center justify-center"
    on:click={() => lightboxIndex = null}
    on:keydown={onKeydown}
  >
    <!-- Prev -->
    {#if galleryImages.length > 1}
      <button
        class="absolute left-4 top-1/2 -translate-y-1/2 text-white/60 hover:text-white text-3xl px-3 py-6 transition-colors z-10"
        on:click|stopPropagation={lightboxPrev}
        aria-label="Previous"
      >‹</button>
    {/if}

    <button
      type="button"
      class="contents"
      on:click|stopPropagation
      aria-label="Image {lightboxIndex + 1} of {galleryImages.length}"
    >
      <img
        src={imageBase + galleryImages[lightboxIndex]}
        alt={galleryImages[lightboxIndex]}
        class="max-h-[90vh] max-w-[90vw] rounded-lg shadow-2xl object-contain"
      />
    </button>

    <!-- Next -->
    {#if galleryImages.length > 1}
      <button
        class="absolute right-4 top-1/2 -translate-y-1/2 text-white/60 hover:text-white text-3xl px-3 py-6 transition-colors z-10"
        on:click|stopPropagation={lightboxNext}
        aria-label="Next"
      >›</button>
    {/if}

    <!-- Counter + filename -->
    <div class="absolute bottom-6 left-1/2 -translate-x-1/2 text-white/50 text-xs text-center">
      <p>{galleryImages[lightboxIndex]}</p>
      {#if galleryImages.length > 1}
        <p class="mt-0.5">{lightboxIndex + 1} / {galleryImages.length}</p>
      {/if}
    </div>

    <!-- Close -->
    <button
      class="absolute top-4 right-5 text-white/50 hover:text-white text-2xl transition-colors"
      on:click={() => lightboxIndex = null}
      aria-label="Close"
    >×</button>
  </div>
{/if}

<!-- Header -->
<div class="flex items-start gap-4 mb-6">
  <button on:click={() => history.back()} class="text-zinc-500 hover:text-white transition-colors mt-1 shrink-0 cursor-pointer">
    ← Back
  </button>
  <div class="flex-1 min-w-0">
    <div class="flex items-center gap-2 mb-1">
      <span
        class="text-xs font-medium px-2 py-0.5 rounded-full"
        style="background:{color}22;color:{color}"
      >
        {sportIcon(activity.sport)} {sportLabel(activity.sport)}
      </span>
      {#if activity.sub_sport && activity.sub_sport !== 'generic'}
        <span
          class="text-xs font-medium px-2 py-0.5 rounded-full"
          style="background:{color}11;color:{color}cc"
        >
          {sportLabel(activity.sport, activity.sub_sport).split(' ')[0]}
        </span>
      {/if}
      <span class="text-xs text-zinc-500">
        {formatDate(activity.started_at)} · {formatTime(activity.started_at)}{#if activity.handle} · <a href="{base}u/{activity.handle}/" class="hover:text-zinc-300 transition-colors">@{activity.handle}</a>{/if}
      </span>
    </div>
    <div class="flex items-center gap-3">
      <h1 class="text-2xl font-bold text-white">{displayTitle}</h1>
      {#if editEnabled}
        <button
          class="text-xs px-2 py-0.5 rounded border border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:text-white transition-colors shrink-0"
          on:click={() => editOpen = true}
        >
          Edit
        </button>
      {/if}
    </div>
    {#if descriptionHtml}
      <div class="text-zinc-400 mt-2 text-sm leading-relaxed [&_img]:rounded-lg [&_img]:my-2 [&_p]:my-1 [&_a]:text-blue-400">
        {@html descriptionHtml}
      </div>
    {/if}
  </div>
</div>

<!-- Photo gallery -->
{#if galleryImages.length}
  <div class="mb-4 grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 gap-2">
    {#each galleryImages as img, i}
      <button
        class="relative overflow-hidden rounded-lg bg-zinc-800 aspect-square hover:opacity-90 transition-opacity focus:outline-none focus:ring-2 focus:ring-blue-500"
        on:click={() => lightboxIndex = i}
        aria-label="Open photo {i + 1}"
      >
        <img
          src={imageBase + img}
          alt={img}
          class="w-full h-full object-cover"
          loading="lazy"
        />
      </button>
    {/each}
  </div>
{/if}

<!-- Map + Stats split -->
<div class="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-4 mb-4">
  <!-- Map -->
  <div class="h-[400px] lg:h-[420px] rounded-xl overflow-hidden bg-zinc-800">
    {#if trackUrl}
      <ActivityMap
        {trackUrl}
        {timeseries}
        bbox={detail?.bbox ?? null}
        initialCoords={activity.preview_coords}
        accentColor={color}
        bind:hoveredIdx
      />
    {:else}
      <div class="w-full h-full flex items-center justify-center text-zinc-600 text-sm">
        No GPS track
      </div>
    {/if}
  </div>

  <!-- Stats panel -->
  <div class="grid grid-cols-2 lg:grid-cols-1 gap-px bg-zinc-800 rounded-xl overflow-hidden">
    {#each stats as s}
      <div class="bg-zinc-900 px-4 py-3">
        <p class="text-2xl font-bold text-white">{s.value}</p>
        <p class="text-xs text-zinc-500">{s.label}</p>
      </div>
    {/each}
    {#if detail?.gear}
      <div class="bg-zinc-900 px-4 py-3 col-span-2 lg:col-span-1">
        <p class="text-sm font-medium text-zinc-300">{detail.gear}</p>
        <p class="text-xs text-zinc-500">Gear</p>
      </div>
    {/if}
  </div>
</div>

<!-- Charts -->
{#if error}
  <p class="text-red-400 text-sm mt-4">{error}</p>
{:else if timeseries && timeseries.t.length > 0}
  <div class="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
    <ActivityCharts {timeseries} bind:hoveredIdx {athlete} />
  </div>
{:else if !detail || timeseriesLoading}
  <div class="bg-zinc-900 rounded-xl border border-zinc-800 p-4 h-32 animate-pulse"></div>
{/if}

<!-- Laps -->
{#if detail?.laps?.length}
  <div class="mt-4 bg-zinc-900 rounded-xl border border-zinc-800 overflow-hidden">
    <table class="w-full text-sm">
      <thead class="border-b border-zinc-800">
        <tr class="text-left text-zinc-500 text-xs">
          <th class="px-4 py-2">Lap</th>
          <th class="px-4 py-2">Distance</th>
          <th class="px-4 py-2">Time</th>
          <th class="px-4 py-2">Avg speed</th>
          <th class="px-4 py-2">Avg HR</th>
        </tr>
      </thead>
      <tbody>
        {#each detail.laps as lap}
          <tr class="border-b border-zinc-800/50 hover:bg-zinc-800/50">
            <td class="px-4 py-2 text-zinc-400">#{lap.index + 1}</td>
            <td class="px-4 py-2 text-white">{formatDistance(lap.distance_m)}</td>
            <td class="px-4 py-2 text-white">{formatDuration(lap.duration_s)}</td>
            <td class="px-4 py-2 text-white">{formatSpeed(lap.avg_speed_kmh)}</td>
            <td class="px-4 py-2 text-white">{lap.avg_hr_bpm ? `${lap.avg_hr_bpm} bpm` : '—'}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}
