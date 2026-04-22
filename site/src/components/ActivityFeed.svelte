<script lang="ts">
  import { onMount } from 'svelte';
  import type { ActivitySummary, BASIndex, Sport } from '../lib/types';
  import { formatDistance, formatDuration, formatElevation, formatDate, isUnlisted, sportIcon, sportColor, sportLabel } from '../lib/format';
  import { loadIndexPaged, loadShardActivities, loadCombinedFeed, loadCombinedFeedPage } from '../lib/dataloader';

  /** Render preview_coords as an SVG polyline path string. */
  function trackPath(coords: [number, number][] | null, w: number, h: number): string {
    if (!coords || coords.length < 2) return '';
    const lats = coords.map(c => c[0]);
    const lons = coords.map(c => c[1]);
    const minLat = Math.min(...lats), maxLat = Math.max(...lats);
    const minLon = Math.min(...lons), maxLon = Math.max(...lons);
    const latR = maxLat - minLat || 0.001;
    const lonR = maxLon - minLon || 0.001;
    const pad = 4;
    const scaleX = (w - pad * 2) / lonR;
    const scaleY = (h - pad * 2) / latR;
    const scale = Math.min(scaleX, scaleY);
    const offX = pad + (w - pad * 2 - lonR * scale) / 2;
    const offY = pad + (h - pad * 2 - latR * scale) / 2;
    return coords
      .map(([lat, lon], i) => {
        const x = (lon - minLon) * scale + offX;
        const y = h - ((lat - minLat) * scale + offY); // flip: SVG y↓, lat↑
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ');
  }

  /** Base URL of the site (passed from Astro). */
  export let base: string = '/';
  /** When set, load this index URL instead of the root (for per-user profile pages). */
  export let profileIndexUrl: string = '';
  /** When set, only show activities from this handle. */
  export let filterHandle: string = '';

  const PAGE_SIZE = 60;

  let all: ActivitySummary[] = [];
  let sport: Sport | 'all' = 'all';
  let shown = PAGE_SIZE;
  let loading = true;
  let loadingMore = false;
  let error = '';
  let mounted = false;
  let pendingShards: string[] = [];
  /** Remaining combined-feed pages (multi-user global feed). */
  let feedNextPage = 0;
  let feedTotalPages = 0;
  /** Grand total from feed.json — shows instance-wide count even before all pages are loaded. */
  let totalActivities = 0;
  /** Logged-in handle — resolved async via bincio:me event. */
  let me: string = '';

  // Show private activities only to their owner.
  // On a profile page (filterHandle set): show unlisted if me === filterHandle.
  // On the global feed: show unlisted only for the logged-in user's own activities.
  $: isOwner = filterHandle !== '' && me === filterHandle;
  $: withPrivacy = all.filter(a => {
    if (isUnlisted(a.privacy)) {
      return filterHandle ? isOwner : (me !== '' && (a as any).handle === me);
    }
    return true;
  });
  $: filtered = sport === 'all' ? withPrivacy : withPrivacy.filter(a => a.sport === sport);
  $: visible = filtered.slice(0, shown);
  $: canShowMore = shown < filtered.length;
  $: hasMore = canShowMore || pendingShards.length > 0 || feedNextPage > 0;

  async function loadMore() {
    if (canShowMore) {
      shown += PAGE_SIZE;
      return;
    }
    loadingMore = true;
    try {
      let fresh: ActivitySummary[] = [];
      if (feedNextPage > 0) {
        fresh = await loadCombinedFeedPage(base, feedNextPage);
        feedNextPage = feedNextPage < feedTotalPages ? feedNextPage + 1 : 0;
      } else if (pendingShards.length) {
        const url = pendingShards[0];
        pendingShards = pendingShards.slice(1);
        fresh = await loadShardActivities(url);
      } else {
        return;
      }
      const existing = new Map(all.map(a => [a.id, a]));
      for (const a of fresh) if (!existing.has(a.id)) existing.set(a.id, a);
      all = [...existing.values()].sort((a, b) =>
        (b.started_at ?? '').localeCompare(a.started_at ?? ''),
      );
      shown += PAGE_SIZE;
    } catch {
      // load failed — don't block the user
    } finally {
      loadingMore = false;
    }
  }

  $: if (sport) shown = PAGE_SIZE; // reset pagination on filter change

  $: if (mounted) {
    const params = new URLSearchParams(window.location.search);
    if (sport === 'all') params.delete('sport'); else params.set('sport', sport);
    const qs = params.toString();
    history.replaceState(null, '', qs ? `?${qs}` : window.location.pathname);
  }

  onMount(async () => {
    sport = (new URLSearchParams(window.location.search).get('sport') as Sport | 'all') ?? 'all';
    mounted = true;

    // Resolve the logged-in handle so we can show the owner their private activities.
    if ((window as any).__bincioMe !== undefined) {
      me = (window as any).__bincioMe;
    } else {
      window.addEventListener('bincio:me', (e: Event) => { me = (e as CustomEvent).detail; }, { once: true });
    }

    try {
      const isGlobalFeed = !profileIndexUrl && !filterHandle;
      if (isGlobalFeed) {
        const combined = await loadCombinedFeed(base);
        if (combined) {
          all = combined.activities;
          totalActivities = combined.totalActivities;
          feedTotalPages = combined.remainingPages + 1;
          feedNextPage = combined.remainingPages > 0 ? 2 : 0;
          loading = false;
          return;
        }
      }
      const indexUrl = profileIndexUrl
        ? `${base}data/${profileIndexUrl}`
        : `${base}data/index.json`;
      const { index, pendingShards: pending } = await loadIndexPaged(base, indexUrl);
      pendingShards = pending;
      let activities = index.activities;
      if (filterHandle && !profileIndexUrl) {
        activities = activities.filter(a => (a as any).handle === filterHandle);
      }
      all = activities;
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  });

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
      {#if totalActivities > filtered.length}
        {filtered.length} of {totalActivities} activities
      {:else}
        {filtered.length} {filtered.length === 1 ? 'activity' : 'activities'}
      {/if}
    </span>
  {/if}
</div>

{#if loading}
  <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
    {#each Array(12) as _}
      <div class="h-36 rounded-xl bg-zinc-800 animate-pulse"></div>
    {/each}
  </div>
{:else if error}
  <p class="text-red-400 text-center py-12">Could not load activities: {error}</p>
{:else if filtered.length === 0}
  <p class="text-zinc-500 text-center py-12">No activities found.</p>
{:else}
  <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
    {#each visible as a (a.id)}
      <!-- relative + isolate so the stretched activity link stays below the handle link -->
      <div class="relative rounded-xl bg-zinc-900 border border-zinc-800 p-4 hover:border-zinc-600 hover:bg-zinc-800/80 transition-all group">
        <!-- header -->
        <div class="flex items-start justify-between gap-2 mb-3">
          <div class="flex-1 min-w-0">
            <p class="text-xs text-zinc-500 mb-0.5">
              {formatDate(a.started_at)}{#if a.handle} · <a
                href="{import.meta.env.BASE_URL}u/{a.handle}/"
                class="relative z-10 hover:text-zinc-300 transition-colors"
              >@{a.handle}</a>{/if}
            </p>
            <!-- stretched link covers the whole card; sits below the handle link -->
            <h3 class="font-semibold text-white truncate group-hover:text-[--accent] transition-colors flex items-center gap-1.5">
              {#if isUnlisted(a.privacy)}
                <span class="text-zinc-500 shrink-0" title="Unlisted">🔒</span>
              {/if}
              <a
                href={a.detail_url ? `${import.meta.env.BASE_URL}activity/${a.id}/` : `${import.meta.env.BASE_URL}activity/local/?id=${a.id}`}
                class="before:absolute before:inset-0 before:content-[''] truncate"
              >{a.title}</a>
            </h3>
          </div>
          <span
            class="text-xs font-medium px-2 py-0.5 rounded-full flex-shrink-0"
            style="background:{sportColor(a.sport)}22; color:{sportColor(a.sport)}"
          >
            {sportIcon(a.sport)} {sportLabel(a.sport, a.sub_sport)}
          </span>
        </div>

        <!-- track thumbnail -->
        {#if a.preview_coords}
          <svg viewBox="0 0 120 70" class="w-full mt-2 mb-3 rounded overflow-hidden bg-zinc-800/60" style="height:70px">
            <path
              d={trackPath(a.preview_coords, 120, 70)}
              fill="none"
              stroke={sportColor(a.sport)}
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
              opacity="0.9"
            />
          </svg>
        {/if}

        <!-- stats row -->
        <div class="grid grid-cols-3 gap-2 text-center">
          <div>
            <p class="text-lg font-bold text-white">{formatDistance(a.distance_m)}</p>
            <p class="text-xs text-zinc-500">Distance</p>
          </div>
          <div>
            <p class="text-lg font-bold text-white">{formatDuration(a.moving_time_s ?? a.duration_s)}</p>
            <p class="text-xs text-zinc-500">Moving time</p>
          </div>
          <div>
            <p class="text-lg font-bold text-white">{formatElevation(a.elevation_gain_m)}</p>
            <p class="text-xs text-zinc-500">Elevation</p>
          </div>
        </div>

        <!-- secondary stats -->
        {#if a.avg_speed_kmh || a.avg_hr_bpm}
          <div class="flex gap-4 mt-3 pt-3 border-t border-zinc-800 text-xs text-zinc-400">
            {#if a.avg_speed_kmh}
              <span>⚡ {a.avg_speed_kmh.toFixed(1)} km/h</span>
            {/if}
            {#if a.avg_hr_bpm}
              <span>♥ {a.avg_hr_bpm} bpm</span>
            {/if}
            {#if a.avg_cadence_rpm}
              <span>↻ {a.avg_cadence_rpm} rpm</span>
            {/if}
          </div>
        {/if}
      </div>
    {/each}
  </div>

  {#if hasMore}
    <div class="text-center mt-8">
      <button
        class="px-6 py-2 rounded-full border border-zinc-700 text-zinc-300 hover:border-zinc-500 hover:text-white disabled:opacity-40 transition-colors text-sm"
        disabled={loadingMore}
        on:click={loadMore}
      >
        {#if loadingMore}
          Loading…
        {:else if canShowMore}
          Load more ({filtered.length - shown} remaining)
        {:else if feedNextPage > 0}
          Load more activities
        {:else}
          Load older activities ({pendingShards.length} more {pendingShards.length === 1 ? 'year' : 'years'})
        {/if}
      </button>
    </div>
  {/if}
{/if}
